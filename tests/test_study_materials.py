"""
Test suite for Study Materials (Upload, Quiz & Flashcards generation).
Covers validation, extraction, grounding, fallback generation, and API endpoints.
"""

import base64
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.server import app
from app.pdf_extractor import PDFExtractor
from app.study_materials import (
    StudyMaterialManager,
    StudyMaterialError,
    study_material_manager
)


@pytest.fixture(autouse=True)
def isolated_study_sources(tmp_path, monkeypatch, native_context_metadata):
    """No live corpus/network/model may be touched by this software test module."""
    import app.config as cfg
    import app.server as server
    import app.study_materials as sm
    from app.indexer import KnowledgeIndexer
    from app.document_lookup import DocumentLookup
    from app.ollama_client import OllamaClient
    from app.trained_client import TrainedModelClient
    from app.native_embedding import NativeEmbeddingClient
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls, *a, **kw: False))
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'synthetic.txt').write_text('Entropy là độ bất định trung bình của một biến ngẫu nhiên rời rạc hữu hạn.', encoding='utf-8')
    db = tmp_path / 'synthetic.db'
    monkeypatch.setattr(cfg, 'DB_PATH', db)
    monkeypatch.setattr(cfg, 'SRC_DIR', src)
    monkeypatch.setattr(cfg, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(sm, 'DATA_DIR', tmp_path / 'data')
    from app.gpu_lock import GPULockManager
    monkeypatch.setattr(sm, 'gpu_coordinator', GPULockManager(
        tmp_path / 'runtime/gpu.lock', tmp_path / 'runtime/gpu_state.json'))
    monkeypatch.setattr(OllamaClient, 'check_health', lambda self: False)
    monkeypatch.setattr(TrainedModelClient, 'available', classmethod(lambda cls: False))
    indexer = KnowledgeIndexer(db_path=db, src_dir=src)
    indexer.index_all(ocr=False, embed_model=None)
    # Follow patched config as the production singleton does, including per-test library changes.
    monkeypatch.setattr(server, 'document_lookup', DocumentLookup())
    # materials_cache is a snapshot property; replace the actual session store.
    monkeypatch.setattr(study_material_manager, "_materials", {})
    monkeypatch.setattr(study_material_manager, "_raw_files", {})
    # Attachment tests mock the Ollama boundary independently of the host's model.
    monkeypatch.setattr(server.agent, "ollama", OllamaClient())
    # API default is captured at import; tests explicitly select their mocked backend.
    original_sync = server.agent.process_query_sync
    original_stream = server.agent.process_query_stream
    def sync_with_test_model(query, **kwargs):
        kwargs["model"] = "test-ollama"
        return original_sync(query, **kwargs)
    def stream_with_test_model(query, **kwargs):
        kwargs["model"] = "test-ollama"
        yield from original_stream(query, **kwargs)
    monkeypatch.setattr(server.agent, "process_query_sync", sync_with_test_model)
    monkeypatch.setattr(server.agent, "process_query_stream", stream_with_test_model)
    from app.history import HistoryStore
    private_history = HistoryStore(tmp_path / "history.db")
    monkeypatch.setattr(server, "_history_store", private_history)
    monkeypatch.setattr(server.summarizer, "history_store", private_history)
    yield


def mock_recognized_image(monkeypatch):
    """A readable image fixture uses OCR output, never file name or image metadata."""
    from app.pdf_extractor import PDFExtractor, PDFExtractionResult
    from pathlib import Path
    text = 'Định lý Pythagoras phát biểu rằng trong tam giác vuông, bình phương cạnh huyền bằng tổng bình phương hai cạnh góc vuông.'
    def extract(path, **kwargs):
        result = PDFExtractionResult(filename=Path(path).name, filepath=Path(path))
        result.total_pages = 1
        result.total_chars = len(text)
        result.extracted_pages = [{'page_num': 1, 'text': text, 'raw_text': text}]
        return result
    monkeypatch.setattr(PDFExtractor, 'extract_file', staticmethod(extract))


@pytest.fixture
def client():
    return TestClient(app)


def test_validation_allowed_extensions():
    manager = StudyMaterialManager()
    
    # Valid text file
    txt_content = b"Hello world study guide"
    manager.validate_file("guide.txt", txt_content)
    
    # Valid markdown
    md_content = b"# Chapter 1\nLinear algebra concepts"
    manager.validate_file("notes.md", md_content)


def test_validation_disallowed_extension():
    manager = StudyMaterialManager()
    with pytest.raises(StudyMaterialError) as exc:
        manager.validate_file("malicious.exe", b"binary content")
    assert "kh\u00f4ng \u0111\u01b0\u1ee3c h\u1ed7 tr\u1ee3" in str(exc.value)  # nosec B101


def test_validation_oversized_file():
    manager = StudyMaterialManager()
    oversized = b"x" * (100 * 1024 * 1024 + 1)
    with pytest.raises(StudyMaterialError) as exc:
        manager.validate_file("big.txt", oversized)
    assert "v\u01b0\u1ee3t qu\u00e1 gi\u1edbi h\u1ea1n cho ph\u00e9p" in str(exc.value)  # nosec B101
    assert "100MB" in str(exc.value)  # nosec B101


def test_deterministic_fallback_quiz_and_flashcards():
    manager = StudyMaterialManager()
    sample_pages = [
        {
            "page_num": 1,
            "text": "Vector l\u00e0 m\u1ed9t \u0111\u1ed1i t\u01b0\u1ee3ng to\u00e1n h\u1ecdc c\u00f3 \u0111\u1ed9 d\u00e0i v\u00e0 h\u01b0\u1edbng. Kh\u00f4ng gian vector l\u00e0 t\u1eadp h\u1ee3p c\u00e1c vector c\u00f9ng hai ph\u00e9p to\u00e1n c\u1ed9ng v\u00e0 nh\u00e2n v\u1edbi v\u00f4 h\u01b0\u1edbng."
        },
        {
            "page_num": 2,
            "text": "Ma tr\u1eadn vu\u00f4ng A \u0111\u01b0\u1ee3c g\u1ecdi l\u00e0 kh\u1ea3 ngh\u1ecbch n\u1ebfu t\u1ed3n t\u1ea1i ma tr\u1eadn B sao cho AB = BA = I."
        }
    ]
    
    quiz = manager.fallback_generate_quiz(sample_pages, count=3)
    assert quiz == []  # Retired: generic distractors must never masquerade as a quiz.  # nosec B101

    flashcards = manager.fallback_generate_flashcards(sample_pages, count=5)
    assert len(flashcards) >= 1  # nosec B101
    for f in flashcards:
        assert "front" in f  # nosec B101
        assert "back" in f  # nosec B101
        assert "page_num" in f  # nosec B101
        assert "evidence_quote" in f  # nosec B101


def test_api_upload_material_text_file(client):
    content = "L\u1ecbch s\u1eed Vi\u1ec7t Nam l\u00e0 qu\u00e1 tr\u00ecnh d\u1ef1ng n\u01b0\u1edbc v\u00e0 gi\u1eef n\u01b0\u1edbc c\u1ee7a d\u00e2n t\u1ed9c Vi\u1ec7t Nam qua h\u00e0ng ng\u00e0n n\u0103m. Chi\u1ebfn th\u1eafng B\u1ea1ch \u0110\u1eb1ng n\u0103m 938 ch\u1ea5m d\u1ee9t ngh\u00ecn n\u0103m B\u1eafc thu\u1ed9c."
    b64 = "data:text/plain;base64," + base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    res = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "lich_su.txt",
        "file_base64": b64
    })
    assert res.status_code == 200  # nosec B101
    data = res.json()
    assert data["status"] == "success"  # nosec B101
    material = data["material"]
    assert material["filename"] == "lich_su.txt"  # nosec B101
    assert material["quiz"] == []  # nosec B101
    assert material["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(material["flashcards"]) >= 1  # nosec B101
    assert material["id"] in study_material_manager.materials_cache  # nosec B101


def test_api_get_material_by_id(client):
    content = "Thu\u1eadt to\u00e1n t\u00ecm ki\u1ebfm nh\u1ecb ph\u00e2n c\u00f3 \u0111\u1ed9 ph\u1ee9c t\u1ea1p O(log n) tr\u00ean m\u1ea3ng \u0111\u00e3 s\u1eafp x\u1ebfp."
    b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    upload_res = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "algorithms.txt",
        "file_base64": b64
    })
    assert upload_res.status_code == 200  # nosec B101
    mat_id = upload_res.json()["material"]["id"]
    
    get_res = client.get(f"/api/materials/{mat_id}")
    assert get_res.status_code == 200  # nosec B101
    assert get_res.json()["id"] == mat_id  # nosec B101
    
    # Not found case
    not_found = client.get("/api/materials/nonexistent_id_12345")
    assert not_found.status_code == 404  # nosec B101


def test_api_list_library_documents(client):
    res = client.get("/api/materials/documents")
    assert res.status_code == 200  # nosec B101
    data = res.json()
    assert "documents" in data  # nosec B101
    assert isinstance(data["documents"], list)  # nosec B101


def test_api_generate_from_library_document(client):
    import sqlite3
    from app.config import DB_PATH

    test_filename = "test_physics_guide.pdf"
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO documents (filename, clean_title, filepath, file_size, file_hash, total_pages, extracted_pages_count, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (test_filename, "Vật lý đại cương", test_filename, 1024, "dummyhash123", 2, 2, "indexed")
        )
        doc_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO chunks (doc_id, book_title, filename, page_num, chunk_index, text) VALUES (?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                "Vật lý đại cương",
                test_filename,
                1,
                0,
                "Định luật I Newton phát biểu rằng nếu một vật không chịu tác dụng của lực nào thì vật giữ nguyên trạng thái đứng yên hoặc chuyển động thẳng đều."
            )
        )
        conn.commit()

    try:
        res = client.post("/api/materials/generate-from-library", json={
        "difficulty": "easy",
            "filename": test_filename
        })
        assert res.status_code == 200  # nosec B101
        data = res.json()
        assert data["status"] == "success"  # nosec B101
        material = data["material"]
        assert material["quiz"] == []  # nosec B101
        assert material["generation"]["quiz"] == "unavailable"  # nosec B101
        assert len(material["flashcards"]) >= 1  # nosec B101
        assert material["flashcards"][0]["page_num"] == 1  # nosec B101
        assert "Newton" in material["flashcards"][0]["evidence_quote"]  # nosec B101
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()


def test_api_upload_image_png_and_jpeg(client, monkeypatch):
    mock_recognized_image(monkeypatch)
    import io
    from PIL import Image, PngImagePlugin

    # Avoid invoking external PowerShell OCR during fast unit test
    monkeypatch.setattr(StudyMaterialManager, "_run_windows_native_ocr", staticmethod(lambda filepath: ""))

    # 1. PNG with embedded description metadata
    img = Image.new("RGB", (320, 180), color=(24, 28, 40))
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text(
        "Description",
        "Định lý Pythagoras phát biểu rằng trong tam giác vuông, bình phương cạnh huyền bằng tổng bình phương hai cạnh góc vuông."
    )
    buf_png = io.BytesIO()
    img.save(buf_png, format="PNG", pnginfo=png_info)
    b64_png = "data:image/png;base64," + base64.b64encode(buf_png.getvalue()).decode("ascii")

    res_png = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "dinh_ly_pythagoras.png",
        "file_base64": b64_png,
    })
    assert res_png.status_code == 200  # nosec B101
    data_png = res_png.json()
    assert data_png["status"] == "success"  # nosec B101
    assert data_png["material"]["filename"] == "dinh_ly_pythagoras.png"  # nosec B101
    assert data_png["material"]["quiz"] == []  # nosec B101
    assert data_png["material"]["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(data_png["material"]["flashcards"]) >= 1  # nosec B101

    # 2. JPEG support with explicit recognized text; unreadable images are rejected separately.
    buf_jpg = io.BytesIO()
    img.save(buf_jpg, format="JPEG")
    b64_jpg = "data:image/jpeg;base64," + base64.b64encode(buf_jpg.getvalue()).decode("ascii")

    res_jpg = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "so_do_tam_ly_hoc_nhan_thuc.jpg",
        "file_base64": b64_jpg,
    })
    assert res_jpg.status_code == 200  # nosec B101
    data_jpg = res_jpg.json()
    assert data_jpg["status"] == "success"  # nosec B101
    assert data_jpg["material"]["filename"] == "so_do_tam_ly_hoc_nhan_thuc.jpg"  # nosec B101
    assert data_jpg["material"]["quiz"] == []  # nosec B101
    assert data_jpg["material"]["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(data_jpg["material"]["flashcards"]) >= 1  # nosec B101


def test_api_upload_image_with_primary_ocr_mocked(client, monkeypatch):
    import io
    from PIL import Image
    from app.pdf_extractor import PDFExtractor

    img = Image.new("RGB", (200, 100), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64_png = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    ocr_text = (
        "Trí nhớ ngắn hạn là hệ thống lưu giữ thông tin trong thời gian ngắn từ vài giây đến một phút. "
        "Trí nhớ dài hạn là hệ thống lưu trữ kiến thức bền vững qua nhiều năm."
    )
    monkeypatch.setattr(PDFExtractor, "ocr_available", staticmethod(lambda: True))
    monkeypatch.setattr(
        "app.pdf_extractor.VisionOCRManager.ocr_image_bytes",
        lambda self, **kwargs: {"text": ocr_text, "needs_review": True, "is_verified": False},
    )

    res = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "bai_giang_tri_nho.png",
        "file_base64": b64_png,
    })
    assert res.status_code == 200  # nosec B101
    material = res.json()["material"]
    assert material["total_pages"] == 1  # nosec B101
    assert material["quiz"] == []  # nosec B101
    assert material["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(material["flashcards"]) >= 1  # nosec B101


def test_api_upload_corrupted_image_rejected(client):
    fake_png = b"\x89PNG\r\n\x1a\ncorrupted_not_a_real_image_stream"
    b64 = "data:image/png;base64," + base64.b64encode(fake_png).decode("ascii")
    res = client.post("/api/materials/upload", json={
        "filename": "broken.png",
        "file_base64": b64,
    })
    assert res.status_code == 422  # nosec B101
    assert "bị hỏng" in res.json()["detail"] or "không đúng định dạng" in res.json()["detail"]  # nosec B101


def test_chat_attach_button_left_alignment_and_full_width_bottom_toolbar():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    css = (root / "static/style.css").read_text(encoding="utf-8")
    html = (root / "static/index.html").read_text(encoding="utf-8")

    assert "align-items: stretch;" in css  # nosec B101
    assert ".chat-attach-wrapper" in css  # nosec B101
    assert "margin-right: auto;" in css  # nosec B101
    assert 'id="chat-attach-wrapper"' in html  # nosec B101
    assert 'id="btn-chat-attach"' in html  # nosec B101


def test_attachment_preview_card_modal_structure_and_centered_chat_layout(client):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    css = (root / "static/style.css").read_text(encoding="utf-8")
    spatial_css = (root / "static/spatial-layout.css").read_text(encoding="utf-8")
    html = (root / "static/index.html").read_text(encoding="utf-8")
    js = (root / "static/app.js").read_text(encoding="utf-8")

    # Compact attachment banner retains reader access and removal controls.
    assert 'id="chat-attachment-preview-tray"' in html  # nosec B101
    assert ".chat-attachment-card" in css  # nosec B101
    assert ".attachment-card-open" in css  # nosec B101
    assert ".attachment-doc-sheet" not in css  # nosec B101
    assert "buildDocSheetPreviewHtml" not in js  # nosec B101
    assert 'class="attachment-card-open"' in js  # nosec B101
    assert ".attachment-card-remove" in css  # nosec B101
    assert "upsertAttachmentPreviewCard" in js  # nosec B101
    assert "removeAttachmentPreviewCard" in js  # nosec B101

    # 2. Ensure #ocr-modal is closed BEFORE #library-picker-modal and #study-material-modal
    ocr_idx = html.index('id="ocr-modal"')
    lib_idx = html.index('id="library-picker-modal"')
    study_idx = html.index('id="study-material-modal"')
    ocr_segment = html[ocr_idx:lib_idx]
    assert ocr_segment.count("<div") == ocr_segment.count("</div>"), (  # nosec B101
        "#ocr-modal must be completely closed before #library-picker-modal so study modals are not hidden inside #ocr-modal"
    )
    assert lib_idx < study_idx  # nosec B101

    # 3. Ensure welcome-card & bento-hub hide on has-chat and centered chat layout guarantees
    assert (  # nosec B101
        ".messages-container.has-chat .welcome-card" in spatial_css
        or ".messages-container.has-chat .welcome-card" in css
    )
    assert "display: none !important;" in spatial_css or "display: none !important;" in css  # nosec B101
    assert ".messages-container.has-chat .arym-bento-grid" in spatial_css  # nosec B101
    assert ".messages-container > .message:not(.welcome-card)" in spatial_css  # nosec B101
    assert "margin: 0 auto !important;" in spatial_css  # nosec B101
    assert "transform: translateY(-16px)" not in spatial_css and "transform: translateY(-16px)" not in html  # nosec B101
    assert ".message.study-card-message" in css  # nosec B101
    assert "arym-bento-grid" in html  # nosec B101

    # 4. Ensure /api/materials/documents lists available documents and /api/materials/generate-from-library works
    docs_res = client.get("/api/materials/documents")
    assert docs_res.status_code == 200  # nosec B101
    docs = docs_res.json().get("documents", [])
    assert len(docs) >= 1  # nosec B101
    first_doc_name = docs[0]["filename"]

    gen_res = client.post("/api/materials/generate-from-library", json={"filename": first_doc_name, "difficulty":"easy"})
    assert gen_res.status_code == 200  # nosec B101
    mat = gen_res.json()["material"]
    assert mat["quiz"] == []  # nosec B101
    assert mat["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(mat["flashcards"]) >= 1  # nosec B101
    assert len(mat.get("pages", [])) >= 1  # nosec B101


def test_ingest_to_src_without_auto_quiz_and_on_demand_generate(client, tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    import app.config as cfg

    root = Path(__file__).resolve().parents[1]
    html = (root / "static/index.html").read_text(encoding="utf-8")
    js = (root / "static/app.js").read_text(encoding="utf-8")

    # Verify dedicated button & content viewer modal exist
    assert 'id="btn-create-quiz-flashcard"' in html  # nosec B101
    assert 'id="document-content-modal"' in html  # nosec B101
    assert "openDocumentContentViewer" in js  # nosec B101
    assert "generateStudySetForActiveAttachment" in js  # nosec B101

    temp_src = tmp_path / "src"
    temp_db = tmp_path / "knowledge_base.db"
    monkeypatch.setattr(cfg, "SRC_DIR", temp_src)
    monkeypatch.setattr(cfg, "DB_PATH", temp_db)

    doc_text = (
        "Cấu trúc dữ liệu cây nhị phân tìm kiếm (BST) là cấu trúc phân cấp trong đó mỗi nút có tối đa hai nút con. "
        "Nút con bên trái luôn có giá trị nhỏ hơn nút cha và nút con bên phải luôn có giá trị lớn hơn nút cha."
    )
    b64 = base64.b64encode(doc_text.encode("utf-8")).decode("ascii")

    # Step 1: Add file with generate_study_set=False, persist_to_src=True
    res = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "cay_nhi_phan.txt",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    })
    assert res.status_code == 200  # nosec B101
    mat = res.json()["material"]
    assert mat["saved_to_src"] is True  # nosec B101
    assert mat["src_path"] == "src/cay_nhi_phan.txt"  # nosec B101
    assert mat["study_generated"] is False  # nosec B101
    assert mat["quiz"] == []  # nosec B101
    assert mat["flashcards"] == []  # nosec B101
    assert len(mat["pages"]) == 1  # nosec B101
    assert "cây nhị phân" in mat["pages"][0]["text"]  # nosec B101
    assert (temp_src / "cay_nhi_phan.txt").is_file()  # nosec B101

    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT id, filename, status FROM documents WHERE filename = ?", ("cay_nhi_phan.txt",)).fetchone()
        assert row is not None  # nosec B101
        assert row[2] == "indexed"  # nosec B101
        chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (row[0],)).fetchone()[0]
        assert chunks >= 1  # nosec B101

    # Step 2: Click dedicated button to generate Quiz & Flashcard on demand
    gen_res = client.post("/api/materials/generate-from-library", json={"filename": "cay_nhi_phan.txt", "difficulty":"easy"})
    assert gen_res.status_code == 200  # nosec B101
    gen_mat = gen_res.json()["material"]
    assert gen_mat["study_generated"] is True  # nosec B101
    assert gen_mat["quiz"] == []  # nosec B101
    assert gen_mat["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(gen_mat["flashcards"]) >= 1  # nosec B101


def test_upload_webp_study_material(client, monkeypatch):
    mock_recognized_image(monkeypatch)
    import io
    from PIL import Image
    img = Image.new("RGB", (100, 50), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    res = client.post("/api/materials/upload", json={
        "difficulty": "easy",
        "filename": "diagram.webp",
        "file_base64": b64,
        "generate_study_set": True,
        "quiz_count": 2,
        "flashcard_count": 2,
        "persist_to_src": False,
    })
    assert res.status_code == 200  # nosec B101
    mat = res.json()["material"]
    assert mat["filename"] == "diagram.webp"  # nosec B101
    assert mat["quiz"] == []  # nosec B101
    assert mat["generation"]["quiz"] == "unavailable"  # nosec B101
    assert len(mat["flashcards"]) >= 1  # nosec B101


def test_canvas_document_viewer_ui_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "static/index.html").read_text(encoding="utf-8")
    css = (root / "static/style.css").read_text(encoding="utf-8")
    js = (root / "static/app.js").read_text(encoding="utf-8")

    # 1. Canvas Top Bar Controls & Action buttons
    assert 'id="document-content-modal"' in html  # nosec B101
    assert 'class="modal canvas-document-modal"' in html  # nosec B101
    assert 'class="canvas-top-bar"' in html  # nosec B101
    assert 'id="btn-canvas-zoom-out"' in html  # nosec B101
    assert 'id="btn-canvas-zoom-in"' in html  # nosec B101
    assert 'id="canvas-zoom-label"' in html  # nosec B101
    assert 'id="btn-canvas-download"' in html  # nosec B101
    assert 'id="btn-canvas-expand"' in html  # nosec B101
    assert 'id="btn-doc-content-generate"' in html  # nosec B101
    assert 'id="btn-close-doc-content-modal"' in html  # nosec B101

    # 2. Canvas Document Sheet structure
    assert 'id="canvas-doc-sheet"' in html  # nosec B101
    assert 'id="doc-content-pages-list"' in html  # nosec B101
    assert 'class="canvas-doc-flow"' in html  # nosec B101

    # 3. Canvas CSS & User message attachment pill
    assert ".canvas-doc-sheet" in css  # nosec B101
    assert ".canvas-h1" in css  # nosec B101
    assert ".canvas-h2" in css  # nosec B101
    assert ".canvas-callout-box" in css  # nosec B101
    assert ".canvas-page-divider" in css  # nosec B101
    assert ".message-attachment-card" in css  # nosec B101
    assert ".message-attachment-icon" in css  # nosec B101
    assert ".message-attachment-meta" in css  # nosec B101
    assert "gradient(" not in css, "Flat design rule: No gradient in style.css"  # nosec B101

    # 4. Canvas JS methods & Zoom logic
    assert "formatCanvasBlock" in js  # nosec B101
    assert "applyCanvasZoom" in js  # nosec B101
    assert "openDocumentContentViewer" in js  # nosec B101


def test_chat_immediate_query_with_attachment(client, tmp_path, monkeypatch):
    import sqlite3
    import app.config as cfg
    from unittest.mock import patch

    temp_src = tmp_path / "src"
    temp_db = tmp_path / "knowledge_base.db"
    monkeypatch.setattr(cfg, "SRC_DIR", temp_src)
    monkeypatch.setattr(cfg, "DB_PATH", temp_db)

    # 1. Upload Physics document
    doc_text = (
        "CHỦ ĐỀ 1: VẬT LÝ NHIỆT\n"
        "1. Cấu trúc chất & Động học phân tử\n"
        "Mô hình động học phân tử: Chất được cấu tạo từ các hạt nguyên tử phân tử luôn chuyển động hỗn loạn không ngừng. "
        "Nhiệt độ càng cao tốc độ chuyển động trung bình của các hạt càng lớn.\n"
        "2. Nội năng & Định luật 1 Nhiệt động lực học\n"
        "Công thức định luật 1: Delta U = Q + W."
    )
    b64 = base64.b64encode(doc_text.encode("utf-8")).decode("ascii")

    res = client.post("/api/materials/upload", json={
        "filename": "On_Tap_Vat_Ly_12.txt",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    })
    assert res.status_code == 200  # nosec B101

    # 2. Directly ask question or request summary using attachment_filename
    mock_llm_response = [
        "TÓM TẮT LÝ THUYẾT ÔN TẬP VẬT LÝ 12 [S1]\n\n"
        "Dựa trên file On_Tap_Vat_Ly_12 bạn gửi, mình tổng hợp lại kiến thức trọng tâm:\n"
        "Chủ đề 1. Vật lý nhiệt: Cấu trúc chất và mô hình động học phân tử [S1].\n"
        "Công thức: Delta U = Q + W [S1]."
    ]

    from app.ollama_client import OllamaClient

    def fake_chat_stream(self, *args, **kwargs):
        self.last_done_reason = "stop"
        for part in mock_llm_response:
            yield part

    monkeypatch.setattr(OllamaClient, "chat_stream", fake_chat_stream)

    chat_res = client.post("/api/chat", json={
        "query": "Tóm tắt lý thuyết trong file",
        "attachment_filename": "On_Tap_Vat_Ly_12.txt",
        "save_history": False,
    })
    assert chat_res.status_code == 200  # nosec B101
    data = chat_res.json()
    assert len(data.get("citations", [])) >= 1  # nosec B101
    assert "On_Tap_Vat_Ly_12" in data["citations"][0]["filename"]  # nosec B101
    assert "Vật lý nhiệt" in data["answer"] or "VẬT LÝ" in data["answer"]  # nosec B101


def test_extract_docx_hierarchical_structure_and_tables():
    fixture_path = Path(__file__).parent / "fixtures" / "documents" / "On_Tap_Hoa_Hoc_12.docx"
    res = PDFExtractor.extract_docx(fixture_path)
    assert res.error_message is None  # nosec B101
    # Must NOT fragment into 67 single-line pages! Must be 3 cohesive pages.
    assert res.total_pages == 3  # nosec B101
    assert res.total_chars >= 3500  # nosec B101
    assert res.unit_name == "trang"  # nosec B101

    # Check Page 1 has Ester & Lipid & Callout
    p1 = res.extracted_pages[0]
    assert p1["page_num"] == 1  # nosec B101
    assert "ESTER - LIPID - XÀ PHÒNG" in p1["text"]  # nosec B101
    assert any(b["type"] == "callout" for b in p1["blocks"])  # nosec B101

    # Check Page 2 has Carbohydrates & Table
    p2 = res.extracted_pages[1]
    assert p2["page_num"] == 2  # nosec B101
    assert "CARBOHYDRATE" in p2["text"]  # nosec B101
    table_blocks = [b for b in p2["blocks"] if b["type"] == "table"]
    assert len(table_blocks) >= 1  # nosec B101
    assert "Monosaccharide" in table_blocks[0]["text"]  # nosec B101
    assert "Disaccharide" in table_blocks[0]["text"]  # nosec B101

    # Check Page 3 has Polymer & Kim loại
    p3 = res.extracted_pages[2]
    assert p3["page_num"] == 3  # nosec B101
    assert "POLYMER" in p3["text"]  # nosec B101
    assert "KIM LOẠI" in p3["text"]  # nosec B101


def test_extract_docx_edge_cases(tmp_path):
    import zipfile

    # 1. Empty file
    empty_f = tmp_path / "empty.docx"
    empty_f.write_bytes(b"")
    res1 = PDFExtractor.extract_docx(empty_f)
    assert "rỗng" in (res1.error_message or "").lower()  # nosec B101

    # 2. Corrupt non-zip file
    corrupt_f = tmp_path / "corrupt.docx"
    corrupt_f.write_bytes(b"Plain text disguised as docx")
    res2 = PDFExtractor.extract_docx(corrupt_f)
    assert "hợp lệ" in (res2.error_message or "").lower()  # nosec B101

    # 3. Zip file without word/document.xml
    fake_zip = tmp_path / "fake.docx"
    with zipfile.ZipFile(fake_zip, "w") as z:
        z.writestr("notes.txt", "hello")
    res3 = PDFExtractor.extract_docx(fake_zip)
    assert "word/document.xml" in (res3.error_message or "")  # nosec B101


def test_chat_immediate_qa_docx_chemistry_summary_and_specific(client, tmp_path, monkeypatch):
    import app.config as cfg
    from app.ollama_client import OllamaClient

    temp_src = tmp_path / "src"
    temp_db = tmp_path / "knowledge_base.db"
    monkeypatch.setattr(cfg, "SRC_DIR", temp_src)
    monkeypatch.setattr(cfg, "DB_PATH", temp_db)

    # 1. Upload On_Tap_Hoa_Hoc_12.docx
    fixture_path = Path(__file__).parent / "fixtures" / "documents" / "On_Tap_Hoa_Hoc_12.docx"
    raw_bytes = fixture_path.read_bytes()
    b64 = base64.b64encode(raw_bytes).decode("ascii")

    res = client.post("/api/materials/upload", json={
        "filename": "On_Tap_Hoa_Hoc_12.docx",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    })
    assert res.status_code == 200  # nosec B101
    material = res.json()["material"]
    assert material["total_pages"] == 3  # nosec B101

    # 2. Ask summary query: "tóm tắt nội dung có trong file"
    # The model mock must cite only passages admitted under the real budget.
    # The earlier mock assumed S3 was present and relied on automatic source
    # assignment to hide invalid markers when that page was not admitted.
    def fake_chat_stream_summary(self, *args, **kwargs):
        import re
        from html import unescape
        self.last_done_reason = "stop"
        prompt = kwargs["messages"][-1]["content"]
        passages = re.findall(r'<document id="(S\d+)"[^>]*>\s*(.*?)\s*</document>', prompt, re.S)
        assert passages, "The attachment overview must supply real admitted passages"  # nosec B101
        for source_id, text in passages:
            excerpt = " ".join(unescape(text).split()[:30])
            yield f"{excerpt} [{source_id}]\n"

    monkeypatch.setattr(OllamaClient, "chat_stream", fake_chat_stream_summary)

    chat_res = client.post("/api/chat", json={
        "query": "tóm tắt nội dung có trong file",
        "attachment_filename": "On_Tap_Hoa_Hoc_12.docx",
        "save_history": False,
    })
    assert chat_res.status_code == 200  # nosec B101
    data = chat_res.json()
    assert len(data.get("citations", [])) >= 1  # nosec B101
    # Must only cite On_Tap_Hoa_Hoc_12.docx
    for cit in data["citations"]:
        assert "On_Tap_Hoa_Hoc_12" in cit["filename"]  # nosec B101
    assert "Ester" in data["answer"] or "HÓA HỌC" in data["answer"]  # nosec B101


def test_chat_multiturn_followup_preserves_document_context(client, tmp_path, monkeypatch):
    import app.config as cfg
    from app.ollama_client import OllamaClient

    temp_src = tmp_path / "src"
    temp_db = tmp_path / "knowledge_base.db"
    monkeypatch.setattr(cfg, "SRC_DIR", temp_src)
    monkeypatch.setattr(cfg, "DB_PATH", temp_db)

    # 1. Upload On_Tap_Hoa_Hoc_12.docx
    fixture_path = Path(__file__).parent / "fixtures" / "documents" / "On_Tap_Hoa_Hoc_12.docx"
    raw_bytes = fixture_path.read_bytes()
    b64 = base64.b64encode(raw_bytes).decode("ascii")

    upload_res = client.post("/api/materials/upload", json={
        "filename": "On_Tap_Hoa_Hoc_12.docx",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    })
    assert upload_res.status_code == 200  # nosec B101

    # 2. Turn 1 with attachment
    sess_res = client.post("/api/sessions", json={})
    assert sess_res.status_code == 200  # nosec B101
    session_id = sess_res.json()["id"]

    mock_turn1 = ["Ester no đơn chức có công thức CnH2nO2 (n >= 2) [S1]."]
    def fake_stream1(self, *args, **kwargs):
        self.last_done_reason = "stop"
        for p in mock_turn1:
            yield p

    monkeypatch.setattr(OllamaClient, "chat_stream", fake_stream1)

    res1 = client.post("/api/chat", json={
        "query": "Ester là gì?",
        "attachment_filename": "On_Tap_Hoa_Hoc_12.docx",
        "session_id": session_id,
        "save_history": True,
    })
    assert res1.status_code == 200  # nosec B101
    assert len(res1.json().get("citations", [])) >= 1  # nosec B101

    # 3. Turn 2 (Follow-up WITHOUT passing attachment_filename explicitly)
    mock_turn2 = ["Phản ứng xà phòng hóa là thủy phân trong môi trường kiềm tạo muối và alcohol [S1]."]
    def fake_stream2(self, *args, **kwargs):
        self.last_done_reason = "stop"
        for p in mock_turn2:
            yield p

    monkeypatch.setattr(OllamaClient, "chat_stream", fake_stream2)

    res2 = client.post("/api/chat", json={
        "query": "Phản ứng xà phòng hóa diễn ra như thế nào?",
        "session_id": session_id,
        "save_history": True,
    })
    assert res2.status_code == 200  # nosec B101
    data2 = res2.json()
    assert len(data2.get("citations", [])) >= 1  # nosec B101
    assert "On_Tap_Hoa_Hoc_12" in data2["citations"][0]["filename"]  # nosec B101


def test_download_material_raw_binary(client, tmp_path, monkeypatch):
    import app.config as cfg
    temp_src = tmp_path / "src"
    temp_db = tmp_path / "knowledge_base.db"
    monkeypatch.setattr(cfg, "SRC_DIR", temp_src)
    monkeypatch.setattr(cfg, "DB_PATH", temp_db)

    # 1. Upload DOCX fixture
    fixture_path = Path(__file__).parent / "fixtures" / "documents" / "On_Tap_Hoa_Hoc_12.docx"
    raw_bytes = fixture_path.read_bytes()
    b64 = base64.b64encode(raw_bytes).decode("ascii")

    upload_res = client.post("/api/materials/upload", json={
        "filename": "On_Tap_Hoa_Hoc_12.docx",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    })
    assert upload_res.status_code == 200  # nosec B101

    # 2. Download raw file
    dl_res = client.get("/api/materials/download?filename=On_Tap_Hoa_Hoc_12.docx")
    assert dl_res.status_code == 200  # nosec B101
    assert dl_res.content == raw_bytes  # nosec B101
    assert "openxmlformats" in dl_res.headers.get("content-type", "")  # nosec B101
    assert "attachment" in dl_res.headers.get("content-disposition", "")  # nosec B101
    assert "On_Tap_Hoa_Hoc_12.docx" in dl_res.headers.get("content-disposition", "")  # nosec B101

    # 3. Nonexistent file returns 404
    bad_res = client.get("/api/materials/download?filename=ghost_file.docx")
    assert bad_res.status_code == 404  # nosec B101


def test_unreadable_image_api_rejects_before_persisting_or_generating(client, monkeypatch):
    import io
    from pathlib import Path
    from PIL import Image, PngImagePlugin
    from app.pdf_extractor import PDFExtractor, PDFExtractionResult
    def empty_ocr(path, **kwargs):
        return PDFExtractionResult(filename=Path(path).name, filepath=Path(path))
    monkeypatch.setattr(PDFExtractor, 'extract_file', staticmethod(empty_ocr))
    monkeypatch.setattr(StudyMaterialManager, '_run_windows_native_ocr', staticmethod(lambda p: ''))
    def unexpected(*args, **kwargs):
        pytest.fail('Unreadable image must not be saved, indexed or sent to generation')
    monkeypatch.setattr(study_material_manager, 'ingest_to_src_and_index', unexpected)
    monkeypatch.setattr(study_material_manager, 'generate_materials_from_pages', unexpected)
    info = PngImagePlugin.PngInfo()
    info.add_text('Description', 'Entropy là độ bất định trung bình của một biến ngẫu nhiên rời rạc hữu hạn.')
    data = io.BytesIO()
    Image.new('RGB', (100, 60), 'white').save(data, format='PNG', pnginfo=info)
    response = client.post('/api/materials/upload', json={
        'filename': 'chi_co_metadata.png',
        'file_base64': base64.b64encode(data.getvalue()).decode('ascii'),
        'persist_to_src': True,
    })
    assert response.status_code == 422  # nosec B101
    assert not study_material_manager.materials_cache  # nosec B101


def _persist_upload(client, filename, text):
    return client.post('/api/materials/upload', json={
        'filename': filename,
        'file_base64': base64.b64encode(text.encode('utf-8')).decode('ascii'),
        'persist_to_src': True,
        'generate_study_set': False,
    })


def test_upload_with_existing_name_preserves_both_sources_and_document_scope(client):
    import sqlite3
    from app import config as cfg
    original = 'Bản nguồn ban đầu: mã nhận diện là ALPHA-271. Đây là tài liệu học tập cần được giữ nguyên.'
    incoming = 'Bản nguồn mới: mã nhận diện là BETA-392. Nội dung upload phải là một nguồn độc lập.'
    first = _persist_upload(client, 'same-name.txt', original)
    assert first.status_code == 200  # nosec B101
    first_mat = first.json()['material']
    second = _persist_upload(client, 'same-name.txt', incoming)
    assert second.status_code == 200  # nosec B101
    second_mat = second.json()['material']
    assert (cfg.SRC_DIR / 'same-name.txt').read_text(encoding='utf-8') == original  # nosec B101
    assert second_mat['filename'] != first_mat['filename']  # nosec B101
    assert second_mat['doc_id'] != first_mat['doc_id']  # nosec B101
    assert (cfg.SRC_DIR / second_mat['filename']).read_text(encoding='utf-8') == incoming  # nosec B101
    assert second_mat['src_path'] == f"src/{second_mat['filename']}"  # nosec B101
    with sqlite3.connect(cfg.DB_PATH) as db:
        old_text = ' '.join(r[0] for r in db.execute('SELECT text FROM chunks WHERE doc_id=?', (first_mat['doc_id'],)))
        new_text = ' '.join(r[0] for r in db.execute('SELECT text FROM chunks WHERE doc_id=?', (second_mat['doc_id'],)))
    assert 'ALPHA-271' in old_text and 'BETA-392' not in old_text  # nosec B101
    assert 'BETA-392' in new_text and 'ALPHA-271' not in new_text  # nosec B101


@pytest.mark.parametrize('failure_point', ['_prepare_chunk_rows', '_insert_chunk_rows'])
def test_upload_index_failure_is_reported_and_does_not_publish_a_source(client, monkeypatch, failure_point):
    import sqlite3
    from app import config as cfg
    from app.indexer import KnowledgeIndexer
    with sqlite3.connect(cfg.DB_PATH) as db:
        before = db.execute('SELECT id,filename,file_hash FROM documents ORDER BY id').fetchall()
    def fail(*args, **kwargs):
        raise RuntimeError('private-storage-diagnostic-marker')
    monkeypatch.setattr(KnowledgeIndexer, failure_point, fail)
    response = _persist_upload(client, 'failed-upload.txt',
        'Tài liệu tổng hợp có đủ văn bản. Lỗi lưu chỉ mục phải được thông báo rõ ràng cho người dùng.')
    assert response.status_code == 503  # nosec B101
    assert 'private-storage-diagnostic-marker' not in response.text  # nosec B101
    assert not (cfg.SRC_DIR / 'failed-upload.txt').exists()  # nosec B101
    assert study_material_manager.resolve_raw_file(filename='failed-upload.txt') is None  # nosec B101
    assert not study_material_manager.materials_cache  # nosec B101
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT id,filename,file_hash FROM documents ORDER BY id').fetchall() == before  # nosec B101


def test_uploading_identical_file_twice_keeps_one_stable_source(client):
    import sqlite3
    from app import config as cfg
    text = 'Nguồn học tập tổng hợp: mã nhận diện UNIQUE-619. Nội dung này phải tra cứu được sau upload.'
    first = _persist_upload(client, 'repeat.txt', text).json()['material']
    with sqlite3.connect(cfg.DB_PATH) as db:
        before = db.execute('SELECT text FROM chunks WHERE doc_id=? ORDER BY chunk_index', (first['doc_id'],)).fetchall()
    second = _persist_upload(client, 'repeat.txt', text).json()['material']
    assert first['doc_id'] == second['doc_id']  # nosec B101
    assert first['filename'] == second['filename']  # nosec B101
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT COUNT(*) FROM documents WHERE filename=?', (first['filename'],)).fetchone()[0] == 1  # nosec B101
        assert db.execute('SELECT text FROM chunks WHERE doc_id=? ORDER BY chunk_index', (first['doc_id'],)).fetchall() == before  # nosec B101
        assert db.execute('SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?', ('"UNIQUE-619"',)).fetchone()[0] >= 1  # nosec B101


def test_repeated_upload_does_not_replace_existing_reviewed_chunks(client):
    import sqlite3
    from app import config as cfg
    text = 'Nguồn học tập tổng hợp: mã nhận diện RETAIN-852. Giữ nguyên các bản ghi đã được kiểm duyệt.'
    first = _persist_upload(client, 'reviewed.txt', text).json()['material']
    with sqlite3.connect(cfg.DB_PATH) as db:
        db.execute('UPDATE chunks SET text=replace(text,?,?), embedding=? WHERE doc_id=?',
                   ('Nguồn', 'NGUỒN', b'verified-vector-placeholder', first['doc_id']))
        db.execute('UPDATE document_source_quality SET needs_review=1,quality_warnings=? WHERE doc_id=?',
                   ('["HOLD: source uncertainty"]', first['doc_id']))
        before = db.execute('SELECT id,text,embedding FROM chunks WHERE doc_id=? ORDER BY id', (first['doc_id'],)).fetchall()
        quality_before = db.execute('SELECT * FROM document_source_quality WHERE doc_id=?', (first['doc_id'],)).fetchone()
    second = _persist_upload(client, 'reviewed.txt', text)
    assert second.status_code == 200  # nosec B101
    assert second.json()['material']['doc_id'] == first['doc_id']  # nosec B101
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT id,text,embedding FROM chunks WHERE doc_id=? ORDER BY id', (first['doc_id'],)).fetchall() == before  # nosec B101
        assert db.execute('SELECT * FROM document_source_quality WHERE doc_id=?', (first['doc_id'],)).fetchone() == quality_before  # nosec B101


def test_concurrent_uploads_with_same_name_keep_each_source(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from app import config as cfg
    from app.pdf_extractor import PDFExtractionResult
    def ingest(i):
        text = f'Concurrent source marker TOKEN-{i}-913 with enough reference material for durable indexing.'
        result = PDFExtractionResult('concurrent.txt', cfg.SRC_DIR / 'concurrent.txt')
        result.total_pages = 1
        result.total_chars = len(text)
        result.extracted_pages = [{'page_num': 1, 'text': text}]
        info = study_material_manager.ingest_to_src_and_index('concurrent.txt', text.encode(), result)
        return result.filename, info['doc_id'], text
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ingest, range(4)))
    assert len({name for name, _, _ in results}) == 4  # nosec B101
    assert len({doc_id for _, doc_id, _ in results}) == 4  # nosec B101
    with sqlite3.connect(cfg.DB_PATH) as db:
        for name, doc_id, text in results:
            assert (cfg.SRC_DIR / name).read_text() == text  # nosec B101
            chunks = ' '.join(r[0] for r in db.execute('SELECT text FROM chunks WHERE doc_id=?', (doc_id,)))
            assert text in chunks  # nosec B101


@pytest.mark.parametrize('busy_kind, expected_status', [('gpu', 503), ('dialogue', 409)])
def test_upload_preserves_busy_status_during_embedding_admission(client, monkeypatch, busy_kind, expected_status):
    import app.study_materials as legacy_materials
    from types import SimpleNamespace
    monkeypatch.setattr(legacy_materials, 'sys', SimpleNamespace(platform='win32'))
    from app import config as cfg
    from app.ollama_client import OllamaClient
    from app.gpu_lock import GPUBusyError
    from app.dialogue import DialogueBusyError
    def busy(self):
        raise (GPUBusyError if busy_kind == 'gpu' else DialogueBusyError)('Resource busy; retry later.')
    monkeypatch.setattr(OllamaClient, 'check_health', busy)
    response = _persist_upload(client, 'busy-source.txt', 'This source must not bypass resource admission while another model operation owns the resource.')
    assert response.status_code == expected_status  # nosec B101
    assert response.headers['retry-after'] == '2'  # nosec B101
    assert not (cfg.SRC_DIR / 'busy-source.txt').exists()  # nosec B101


def test_upload_does_not_claim_vectors_ready_when_chunk_embedding_fails(client, monkeypatch):
    import app.study_materials as legacy_materials
    from types import SimpleNamespace
    monkeypatch.setattr(legacy_materials, 'sys', SimpleNamespace(platform='win32'))
    from app.ollama_client import OllamaClient
    monkeypatch.setattr(OllamaClient, 'check_health', lambda self: True)
    monkeypatch.setattr(OllamaClient, 'find_best_embed_model', lambda self, model: 'test-embed')
    monkeypatch.setattr(OllamaClient, 'get_embedding', lambda self, text, **kw: [0.1] * 1024 if text == 'test' else None)
    monkeypatch.setattr(OllamaClient, 'get_batch_embeddings', lambda self, texts, **kw: [None] * len(texts))
    response = _persist_upload(client, 'keyword-only.txt', 'Keyword searchable content survives an unavailable vector response. Distinct marker SEARCH-823 must remain in the source.')
    # Either retain honest keyword-only rows or fail closed before publishing.
    assert response.status_code in (200, 503)  # nosec B101
    if response.status_code == 200:
        material = response.json()['material']
        assert material['chunks_indexed'] > 0  # nosec B101
        assert material.get('vectors_ready') is False  # nosec B101
        assert material.get('embedding_status') == 'offline_keyword_only'  # nosec B101


def test_upload_with_no_searchable_chunks_fails_before_publishing(client, monkeypatch):
    from app import config as cfg
    from app.indexer import KnowledgeIndexer
    monkeypatch.setattr(KnowledgeIndexer, '_prepare_chunk_rows', lambda *args, **kwargs: [])
    response = _persist_upload(client, 'no-searchable-chunks.txt', 'Readable source material must not be advertised as ready when no searchable chunk has been prepared.')
    assert response.status_code == 503  # nosec B101
    assert not (cfg.SRC_DIR / 'no-searchable-chunks.txt').exists()  # nosec B101
    assert not study_material_manager.materials_cache  # nosec B101


def test_upload_acquires_model_resource_coordinator_before_embedding(client, monkeypatch):
    import app.study_materials as legacy_materials
    from types import SimpleNamespace
    monkeypatch.setattr(legacy_materials, 'sys', SimpleNamespace(platform='win32'))
    from contextlib import contextmanager
    import app.study_materials as sm
    from app.ollama_client import OllamaClient
    from app.gpu_lock import GPUBusyError
    calls = []
    @contextmanager
    def busy(*args, **kwargs):
        raise GPUBusyError('Model resource is reserved.')
        yield
    monkeypatch.setattr(sm.gpu_coordinator, 'acquire_for_inference', busy)
    monkeypatch.setattr(OllamaClient, 'check_health', lambda self: True)
    monkeypatch.setattr(OllamaClient, 'find_best_embed_model', lambda self, model: 'test-embed')
    def embedding(self, text, **kwargs):
        calls.append(text)
        return [0.1] * 1024
    monkeypatch.setattr(OllamaClient, 'get_embedding', embedding)
    monkeypatch.setattr(OllamaClient, 'get_batch_embeddings', lambda self, texts, **kw: [[0.1] * 1024 for _ in texts])
    response = _persist_upload(client, 'resource-admission.txt', 'Native text upload must acquire the existing model resource coordinator before any embedding probe or batch inference.')
    assert response.status_code == 503  # nosec B101
    assert not calls  # nosec B101


def test_failed_concurrent_repeat_cannot_delete_successful_upload(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import sqlite3
    from app import config as cfg
    from app.indexer import KnowledgeIndexer
    from app.pdf_extractor import PDFExtractionResult
    local = threading.local()
    preparing_first = threading.Event()
    second_finished = threading.Event()
    original_prepare = KnowledgeIndexer._prepare_chunk_rows
    text = 'Concurrent repeat SOURCE-613 must remain available when another upload attempt fails while this one succeeds.'
    def prepare(self, *args, **kwargs):
        if getattr(local, 'must_fail', False):
            preparing_first.set()
            second_finished.wait(timeout=0.75)
            raise RuntimeError('simulated preparation failure')
        return original_prepare(self, *args, **kwargs)
    monkeypatch.setattr(KnowledgeIndexer, '_prepare_chunk_rows', prepare)
    def ingest(must_fail):
        local.must_fail = must_fail
        result = PDFExtractionResult('concurrent-failure.txt', cfg.SRC_DIR / 'concurrent-failure.txt')
        result.total_pages = 1
        result.total_chars = len(text)
        result.extracted_pages = [{'page_num': 1, 'text': text}]
        try:
            return StudyMaterialManager().ingest_to_src_and_index('concurrent-failure.txt', text.encode(), result)
        except RuntimeError as exc:
            if not must_fail:
                raise
            return exc
        finally:
            if not must_fail:
                second_finished.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(ingest, True)
        assert preparing_first.wait(timeout=3)  # nosec B101
        succeeded = pool.submit(ingest, False)
        first_result, second_result = failed.result(timeout=5), succeeded.result(timeout=5)
    assert isinstance(first_result, RuntimeError)  # nosec B101
    assert (cfg.SRC_DIR / 'concurrent-failure.txt').read_text() == text  # nosec B101
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT text FROM chunks WHERE doc_id=?', (second_result['doc_id'],)).fetchone()[0] == text  # nosec B101


@pytest.mark.parametrize('generate_study_set', [False, True])
def test_successful_session_only_upload_keeps_original_download(client, generate_study_set):
    from app import config as cfg
    raw = b'Session-only source MEMORY-418 must retain its original download after successful extraction without durable indexing.'
    response = client.post('/api/materials/upload', json={
        "difficulty": "easy",
        'filename': 'session-only-source.txt',
        'file_base64': base64.b64encode(raw).decode(),
        'persist_to_src': False,
        'generate_study_set': generate_study_set,
    })
    assert response.status_code == 200  # nosec B101
    assert not (cfg.SRC_DIR / 'session-only-source.txt').exists()  # nosec B101
    download = client.get('/api/materials/download', params={'material_id': response.json()['material']['id']})
    assert download.status_code == 200  # nosec B101
    assert download.content == raw  # nosec B101


@pytest.mark.parametrize("correct,distractors", [
    ("R>0", ["R<0", "R=0", "R≠0"]),
    ("R>0", ["R<0", "R=0", "R>=0"]),
    ("R>0", ["U>0", "I>0", "V>0"]),
    ("P(B)>0", ["P(B)<0", "P(B)=0", "P(B)!=0"]),
])
def test_source_cloze_rejects_overlapping_zero_conditions(correct, distractors):
    from app.study_materials import _zero_condition_distractors_are_disjoint
    assert not _zero_condition_distractors_are_disjoint(correct, distractors)  # nosec B101


@pytest.mark.parametrize("correct,distractors", [
    ("R>0", ["R<0", "R=0", "R<=0"]),
    ("R=0", ["R<0", "R>0", "R!=0"]),
    ("P(B)>0", ["P(B)<0", "P(B)=0", "P(B)<=0"]),
    ("one bit", ["zero bits", "two bits", "three bits"]),
])
def test_source_cloze_keeps_nonoverlapping_zero_conditions_and_text(correct, distractors):
    from app.study_materials import _zero_condition_distractors_are_disjoint
    assert _zero_condition_distractors_are_disjoint(correct, distractors)  # nosec B101



def test_quiz_rejects_weaker_nonzero_option_next_to_positive_condition():
    import json
    from app.pdf_extractor import PDFExtractionResult
    class Client:
        last_done_reason = "stop"
        def check_health(self): return True
        def chat_complete(self, **kwargs):
            return json.dumps({"questions": [{"format": "source_cloze", "answer": "R>0", "distractors": ["R<0", "R=0", "R≠0"], "evidence_id": "Q1"}], "flashcards": []})
    text = "Định luật Ôm: I = U/R, với R>0. Điện trở được đo bằng ôm."
    source = PDFExtractionResult(filename="synthetic.txt", filepath=Path("synthetic.txt"))
    source.extracted_pages = [{"page_num": 1, "text": text}]
    source.total_chars = len(text); source.total_pages = 1
    result = StudyMaterialManager(Client()).generate_materials_from_pages(source, model="test-ollama", quiz_count=1, difficulty="easy")
    assert result["generation"]["quiz"] == "unavailable"  # nosec B101



@pytest.mark.parametrize("answer,distractors", [
    ("one bit", ["zero entropy", "two outcomes", "non-negative"]),
    ("bit", ["unit", "byte", "dimension"]),
    ("bit", ["measurement", "byte", "quantity"]),
])
def test_information_unit_quiz_distractors_must_be_concrete_unit_values(answer, distractors):
    from app.study_materials import _information_unit_distractors_match
    assert not _information_unit_distractors_match(answer, distractors)  # nosec B101


def test_information_unit_quiz_allows_concrete_alternatives():
    from app.study_materials import _information_unit_distractors_match
    assert _information_unit_distractors_match("one bit", ["zero bits", "two bits", "three bits"])  # nosec B101


@pytest.mark.skipif(__import__('sys').platform != 'darwin', reason='Mac direct MLX upload integration')
def test_uploaded_material_gets_native_vectors_without_any_daemon_probe(client, monkeypatch):
    import sqlite3
    import hashlib
    from app.config import DB_PATH
    from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
    from app.ollama_client import OllamaClient
    legacy_calls, native_calls = [], []
    def forbidden(*a, **kw):
        legacy_calls.append((a, kw))
        raise AssertionError('Native Mac material upload must never probe a model daemon')
    def embed(self, texts, **kwargs):
        native_calls.extend(texts)
        return [[1.0] + [0.0]*1023 for _ in texts]
    monkeypatch.setattr(OllamaClient, 'check_health', forbidden)
    monkeypatch.setattr(OllamaClient, 'get_embedding', forbidden)
    monkeypatch.setattr(OllamaClient, 'get_batch_embeddings', forbidden)
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, 'get_batch_embeddings', embed)
    monkeypatch.setattr(NativeEmbeddingClient, 'get_identity', lambda self: BGE_NATIVE_IDENTITY)
    source = 'Entropy Shannon đo độ bất định của biến ngẫu nhiên rời rạc hữu hạn.'
    response = client.post('/api/materials/upload', json={
        "difficulty": "easy",
        'persist_to_src': True,
        'filename': 'native-entropy.txt',
        'file_base64': base64.b64encode(source.encode()).decode(),
    })
    assert response.status_code == 200, response.text  # nosec B101
    assert legacy_calls == []  # nosec B101
    assert native_calls  # nosec B101
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute('''SELECT c.text,c.embedding,p.text_sha256,p.vector_sha256,p.model_digest,p.dimensions,p.full_text
            FROM chunks c JOIN documents d ON d.id=c.doc_id
            LEFT JOIN corpus_vector_provenance p ON p.chunk_id=c.id WHERE d.filename=?''', ('native-entropy.txt',)).fetchall()
    assert rows  # nosec B101
    for text, blob, text_sha, vector_sha, identity, dimensions, full_text in rows:
        assert text_sha == hashlib.sha256(text.encode()).hexdigest()  # nosec B101
        assert vector_sha == hashlib.sha256(blob).hexdigest()  # nosec B101
        assert identity == BGE_NATIVE_IDENTITY and dimensions == 1024 and full_text == 1  # nosec B101


@pytest.mark.parametrize('busy_kind, expected_status', [('gpu', 503), ('dialogue', 409)])
def test_native_upload_preserves_busy_admission_before_vector_inference(client, monkeypatch, busy_kind, expected_status):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app import config as cfg, study_materials as sm
    from app.native_embedding import NativeEmbeddingClient
    from app.gpu_lock import GPUBusyError
    from app.dialogue import DialogueBusyError
    monkeypatch.setattr(sm, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls, *a, **kw: True))
    calls = []
    def embed(self, texts, **kwargs):
        calls.append(texts)
        raise AssertionError('Admission must reject before any model work')
    monkeypatch.setattr(NativeEmbeddingClient, 'get_batch_embeddings', embed)
    @contextmanager
    def busy(*args, **kwargs):
        raise (GPUBusyError if busy_kind == 'gpu' else DialogueBusyError)('Resource busy; retry later.')
        yield
    monkeypatch.setattr(sm.gpu_coordinator, 'acquire_for_inference', busy)
    response = _persist_upload(client, 'native-busy-source.txt', 'Native upload must preserve resource admission and cannot bypass an active model operation.')
    assert response.status_code == expected_status  # nosec B101
    assert response.headers['retry-after'] == '2'  # nosec B101
    assert not (cfg.SRC_DIR / 'native-busy-source.txt').exists()  # nosec B101
    assert calls == []  # nosec B101


def test_resolve_safe_source_file_nested_and_traversal(tmp_path):
    from app.study_materials import resolve_safe_source_file
    nested_dir = tmp_path / "src" / "cslt" / "tieng Viet"
    nested_dir.mkdir(parents=True)
    sample_file = nested_dir / "lecture_2D_2.pptx"
    sample_file.write_bytes(b"PK\x03\x04test_presentation")

    search_dirs = [tmp_path / "src"]

    # Nested path as stored in documents
    found1 = resolve_safe_source_file("cslt/tieng Viet/lecture_2D_2.pptx", search_dirs=search_dirs)
    assert found1 == sample_file  # nosec B101

    # Path with leading src/
    found2 = resolve_safe_source_file("src/cslt/tieng Viet/lecture_2D_2.pptx", search_dirs=search_dirs)
    assert found2 == sample_file  # nosec B101

    # Just basename
    found3 = resolve_safe_source_file("lecture_2D_2.pptx", search_dirs=search_dirs)
    assert found3 == sample_file  # nosec B101

    # Path traversal attack must be rejected
    assert resolve_safe_source_file("../../../secret.txt", search_dirs=search_dirs) is None  # nosec B101
    assert resolve_safe_source_file("cslt/../../etc/passwd", search_dirs=search_dirs) is None  # nosec B101


def test_merge_chunk_texts_provenance_and_repeats():
    from app.study_materials import StudyMaterialManager
    manager = StudyMaterialManager()

    # 1. With exact char_start / char_end provenance
    chunks_with_prov = [
        {"text": "Đoạn văn mở đầu khái niệm A.", "char_start": 0, "char_end": 28},
        {"text": "khái niệm A. Phần tiếp nối giải thích chi tiết.", "char_start": 16, "char_end": 62},
    ]
    merged_prov = manager._merge_chunk_texts(chunks_with_prov)
    assert merged_prov == "Đoạn văn mở đầu khái niệm A. Phần tiếp nối giải thích chi tiết."  # nosec B101

    # 2. Without provenance: genuine repeats must NOT be stripped merely because edges match
    chunks_without_prov = [
        "Ví dụ 1: Tính toán giá trị entropy trong điều kiện A.",
        "Ví dụ 1: Tính toán giá trị entropy trong điều kiện B.",
    ]
    merged_repeats = manager._merge_chunk_texts(chunks_without_prov)
    assert "điều kiện A" in merged_repeats  # nosec B101
    assert "điều kiện B" in merged_repeats  # nosec B101
    assert merged_repeats.count("Ví dụ 1:") == 2  # nosec B101


def test_difficulty_gate_hard_rejects_easy_cloze_and_fallback():
    from app.pdf_extractor import PDFExtractionResult
    from app.study_materials import StudyMaterialManager, StudyMaterialError
    class OfflineClient:
        def check_health(self): return False
    manager = StudyMaterialManager(OfflineClient())
    res = PDFExtractionResult(filename="entropy.txt", filepath=Path("entropy.txt"))
    res.extracted_pages = [{"page_num": 1, "text": "Entropy là độ bất định trung bình của một biến ngẫu nhiên."}]
    res.total_pages = 1
    res.total_chars = len(res.extracted_pages[0]["text"])

    # Fallback fill-in questions are strictly 'easy'; requesting 'hard' must reject them
    with pytest.raises(StudyMaterialError, match="Không có câu hỏi đạt kiểm định nguồn và độ khó 'khó'"):
        manager.generate_materials_from_pages(res, quiz_count=3, difficulty="hard")


def test_difficulty_gate_partial_count_with_warning(monkeypatch):
    """When only 1 hard question passes verification for count=3, honest partial set is returned."""
    import json
    import app.token_counter as token_counter
    monkeypatch.setattr(token_counter, 'get_exact_token_counter',
                        lambda: lambda messages: sum(len(m['content']) for m in messages))
    from app.pdf_extractor import PDFExtractionResult
    from app.study_materials import StudyMaterialManager

    # Authored two-rule case: override changes the thermal sensor only.
    rules = ("Máy chỉ chạy khi cửa bảo vệ đóng và khóa an toàn đã cài. "
             "Nút ghi đè chỉ bỏ qua cảm biến nhiệt, không thay điều kiện cửa hoặc khóa.")
    hard_q = {
        "question":"Phân tích tình huống cửa đã đóng, khóa chưa cài và nút ghi đè đã bật: máy có được chạy không?",
        "choices":{"A":"Không được chạy do thiếu khóa an toàn", "B":"Được chạy vì ghi đè bỏ qua khóa",
                   "C":"Được chạy vì cửa đã đóng", "D":"Mở cửa thì được chạy"},
        "answer":"A", "explanation":"Thiếu khóa nên chưa đủ điều kiện chạy. Ghi đè chỉ bỏ qua cảm biến nhiệt.",
        "evidence_quote":rules, "page_num":1, "difficulty":"hard",
    }
    easy_q = {
        "question": "Entropy là gì theo định nghĩa của tài liệu?",
        "choices": {"A": "Độ bất định", "B": "Nhiệt độ", "C": "Khối lượng", "D": "Năng lượng"},
        "answer": "A",
        "evidence_quote": "Entropy là độ bất định trung bình của một biến ngẫu nhiên.",
        "page_num": 1,
        "difficulty": "easy",
    }

    class MockModelClient:
        last_done_reason = "stop"
        def check_health(self): return True
        def chat_complete(self, *args, **kwargs):
            self.last_done_reason = "stop"
            prompt = kwargs['messages'][0]['content']
            if 'Kiểm định độc lập từng câu' in prompt:
                item = json.JSONDecoder().raw_decode(prompt.split('Dữ liệu cần kiểm định:\n',1)[1])[0][0]
                is_match = (item['question'] == hard_q['question'])
                return json.dumps({'checks':[{'index':1,
                    'input_audit': 'Đề bài nêu rõ đầy đủ các điều kiện cửa, khóa và ghi đè.' if is_match else 'Câu hỏi khái niệm không có dữ kiện bài tập tình huống.',
                    'independent_solution': 'Suy luận độc lập từ hai quy tắc nguồn: thiếu khóa nên máy không được chạy.' if is_match else 'Nhận diện khái niệm theo một câu định nghĩa.',
                    'choice_comparison': 'Lựa chọn A là đáp án duy nhất phù hợp.' if is_match else 'Lựa chọn A là định nghĩa trực tiếp.',
                    'explanation_audit': 'Giải thích phân tích đúng cả hai điều kiện theo nguồn.' if is_match else 'Giải thích chỉ nhắc lại định nghĩa.',
                    'supported':True,'explanation_supported':True,
                    'unique_answer':True,'qualifiers_preserved':True,'verified_answer':'A',
                    'difficulty_matches':is_match}]})
            return json.dumps({
                "questions": [hard_q, easy_q],
                "flashcards": [{"front": "Khái niệm", "back": "Định nghĩa", "evidence_quote": "Entropy là độ bất định trung bình của một biến ngẫu nhiên.", "page_num": 1}]
            })

    res = PDFExtractionResult(filename="test.txt", filepath=Path("test.txt"))
    text = (
        "Entropy là độ bất định trung bình của một biến ngẫu nhiên. "
        + rules
    )
    res.extracted_pages = [{"page_num": 1, "text": text}]
    res.total_pages = 1
    res.total_chars = len(text)

    manager = StudyMaterialManager(MockModelClient())
    record = manager.generate_materials_from_pages(res, model="test-ollama", quiz_count=3, difficulty="hard")
    assert record["requested_quiz_count"] == 3  # nosec B101
    assert record["actual_quiz_count"] == 1  # nosec B101
    assert record["complete"] is False  # nosec B101
    assert record["difficulty"] == "hard"  # nosec B101
    assert "1/3 câu" in record["warning"]  # nosec B101
    assert record["quiz"][0]["difficulty"] == "hard"  # nosec B101


def test_generate_from_library_read_only_no_index_mutation(tmp_path, monkeypatch):
    import sqlite3
    import app.config as cfg
    from app.study_materials import StudyMaterialManager
    manager = StudyMaterialManager()

    doc_file = cfg.SRC_DIR / "sample_guide.txt"
    doc_file.write_text("Hướng dẫn học tập: Cấu trúc dữ liệu và giải thuật trong lập trình.", encoding="utf-8")

    with sqlite3.connect(cfg.DB_PATH) as db:
        initial_docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        initial_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    record = manager.generate_from_library_document(filename="sample_guide.txt", quiz_count=2, difficulty="easy")
    assert record["filename"] == "sample_guide.txt"  # nosec B101

    with sqlite3.connect(cfg.DB_PATH) as db:
        final_docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        final_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    # Database must remain untouched (safe read-only physical page extraction)
    assert initial_docs == final_docs  # nosec B101
    assert initial_chunks == final_chunks  # nosec B101


def test_api_generate_from_library_carries_controls(client, monkeypatch):
    seen = []
    def capture(extracted, **kwargs):
        seen.append(kwargs)
        return {'id':'transport-fixture','filename':extracted.filename,'quiz':[],'flashcards':[]}
    monkeypatch.setattr(study_material_manager,'generate_materials_from_pages',capture)
    response = client.post("/api/materials/generate-from-library", json={
        "filename":"synthetic.txt", "quiz_count":5, "flashcard_count":7, "difficulty":"medium"})
    assert response.status_code == 200, response.text  # nosec B101
    assert len(seen) == 1  # nosec B101
    assert seen[0]['quiz_count'] == 5  # nosec B101
    assert seen[0]['flashcard_count'] == 7  # nosec B101
    assert seen[0]['difficulty'] == 'medium'  # nosec B101


def test_large_103_page_deck_caps_prompt_prefill_and_prioritizes_substantive_pages():
    from app.study_materials import StudyMaterialManager, PDFExtractionResult

    seen_prompts = []

    class CaptureClient:
        def check_health(self):
            return True

        def chat_complete(self, **kwargs):
            self.last_done_reason = "stop"
            seen_prompts.append(kwargs["messages"][0]["content"])
            return '{"questions": [], "flashcards": []}'

    res = PDFExtractionResult(filename="Chuong_1_103.pdf", filepath=Path("Chuong_1_103.pdf"))
    res.clean_title = "Chuong 1 Ma tran"
    res.total_pages = 103
    res.extracted_pages = [
        {"page_num": 1, "text": "ĐẠI HỌC KHOA HỌC TỰ NHIÊN"},
        {"page_num": 2, "text": "Nội dung: 1.1 Ma trận; 1.2 Định thức."},
    ] + [
        {
            "page_num": p,
            "text": f"Định nghĩa trang {p}: Ma trận vuông cấp n có định thức xác định và quy tắc nhân ma trận hợp lệ.",
        }
        for p in range(3, 104)
    ]
    res.total_chars = sum(len(p["text"]) for p in res.extracted_pages)

    mgr = StudyMaterialManager(CaptureClient())
    mgr.generate_materials_from_pages(res, model="test-ollama", quiz_count=3, flashcard_count=5, difficulty="easy")
    assert seen_prompts  # nosec B101
    # Must not pack all 103 pages into a huge prompt prefill for a 3-question / 5-card set
    doc_blocks_count = seen_prompts[0].count("Trang ")
    assert doc_blocks_count <= 24  # nosec B101


def test_topic_flashcards_and_modal_quiz_pdf_export_endpoints(client, tmp_path, monkeypatch):
    import sqlite3
    import fitz
    import app.config as cfg
    from app import server as _srv
    from app.indexer import KnowledgeIndexer
    from app.study_materials import StudyMaterialManager

    src = tmp_path / "topic_flash_src"
    src.mkdir(exist_ok=True)
    db = tmp_path / "topic_flash_kb.db"
    KnowledgeIndexer(db, src)
    (src / "DaiSo.pdf").write_bytes(b"%PDF-1.4\n")
    theory = (
        "Ánh xạ tuyến tính f từ không gian vectơ V vào W bảo toàn phép cộng vectơ "
        "và phép nhân vô hướng với mọi vectơ u, v trong V."
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO documents(id,filename,clean_title,filepath,file_size,file_hash,total_pages,extracted_pages_count,status) VALUES(?,?,?,?,?,?,?,?,?)",
            (1, "DaiSo.pdf", "Đại số tuyến tính", str(src / "DaiSo.pdf"), 100, "h1", 1, 1, "indexed"),
        )
        conn.execute(
            "INSERT INTO chunks(doc_id,filename,book_title,page_num,chunk_index,text) VALUES(?,?,?,?,?,?)",
            (1, "DaiSo.pdf", "Đại số tuyến tính", 5, 0, theory),
        )
    monkeypatch.setattr(cfg, "SRC_DIR", src)
    monkeypatch.setattr(cfg, "DB_PATH", db)
    from app.document_lookup import DocumentLookup
    monkeypatch.setattr(_srv.learning_service, "document_lookup", DocumentLookup(db, src))

    class MockFlashClient:
        last_done_reason = "stop"

        def check_health(self):
            return True

        def chat_complete(self, **kwargs):
            self.last_done_reason = "stop"
            return json.dumps(
                {
                    "flashcards": [
                        {
                            "front": "Ánh xạ tuyến tính bảo toàn phép toán nào?",
                            "back": "bảo toàn phép cộng vectơ",
                            "evidence_quote": theory,
                            "page_num": 5,
                        }
                    ]
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(_srv, "study_material_manager", StudyMaterialManager(MockFlashClient()))
    res_fc = client.post("/api/learning/flashcards", json={"topic": "Ánh xạ tuyến tính", "count": 5})
    assert res_fc.status_code == 200, res_fc.text  # nosec B101
    fc_data = res_fc.json()
    assert fc_data["topic"] == "Ánh xạ tuyến tính"  # nosec B101
    assert len(fc_data["flashcards"]) >= 1  # nosec B101
    assert fc_data["flashcards"][0]["back"]  # nosec B101

    # Export server-stored modal quiz questions directly via material_id
    saved_mgr = _srv.study_material_manager
    saved_mat = {
        "id": "modal-mat-1",
        "filename": "BaiGiang.pdf",
        "clean_title": "Tài liệu tải lên: BaiGiang.pdf",
        "quiz": [
            {
                "id": "q1",
                "question": "Ánh xạ tuyến tính bảo toàn phép toán nào?",
                "choices": [
                    {"label": "A", "text": "Phép cộng và phép nhân vô hướng"},
                    {"label": "B", "text": "Phép chia vô hướng"},
                ],
                "correct_answer": "A",
                "explanation": "Theo định nghĩa trang 5.",
                "page_num": 5,
                "evidence_quote": theory,
            }
        ],
    }
    saved_mgr._materials["modal-mat-1"] = saved_mat
    res_pdf = client.post(
        "/api/learning/export-pdf",
        json={
            "topic": "Tài liệu tải lên: BaiGiang.pdf",
            "difficulty": "medium",
            "material_id": "modal-mat-1",
        },
    )
    assert res_pdf.status_code == 200, res_pdf.text  # nosec B101
    assert res_pdf.headers["content-type"].startswith("application/pdf")  # nosec B101
    doc = fitz.open(stream=res_pdf.content, filetype="pdf")
    try:
        full_text = "\n".join(p.get_text("text") for p in doc)
        assert "Ánh xạ tuyến tính bảo toàn phép toán nào?" in full_text  # nosec B101
        assert "Phép cộng và phép nhân vô hướng" in full_text  # nosec B101
    finally:
        doc.close()


def test_upload_study_set_generate_and_pdf_export_e2e(client, monkeypatch):
    import json
    from app import server as _srv
    from app.study_materials import study_material_manager

    raw_text = (
        "Ma trận vuông A được gọi là khả nghịch nếu tồn tại ma trận vuông B sao cho AB = BA = I.\n"
        "Định thức của ma trận khả nghịch luôn khác 0 theo định lý cơ bản."
    )
    b64 = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")

    # 1. Instant upload in Study Tool (persist_to_src=False)
    up_res = client.post(
        "/api/materials/upload",
        json={
            "filename": "upload_ephemeral_note.txt",
            "file_base64": b64,
            "generate_study_set": False,
            "persist_to_src": False,
        },
    )
    assert up_res.status_code == 200, up_res.text  # nosec B101
    mat_id = up_res.json()["material"]["id"]

    class MockStudyClient:
        last_done_reason = "stop"

        def check_health(self):
            return True

        def chat_complete(self, **kwargs):
            self.last_done_reason = "stop"
            prompt = kwargs["messages"][0]["content"]
            if "Kiểm định độc lập từng câu" in prompt:
                return json.dumps(
                    {
                        "checks": [
                            {
                                "index": 1,
                                "source_basis": "Ma trận vuông A được gọi là khả nghịch nếu tồn tại ma trận vuông B sao cho AB = BA = I.",
                                "matching_choices": "A",
                                "input_audit": "Đầy đủ dữ kiện theo định nghĩa trang 1.",
                                "independent_solution": "Tra cứu định nghĩa khả nghịch: tồn tại B sao cho AB = BA = I.",
                                "choice_comparison": "Khớp với đáp án A.",
                                "explanation_audit": "Giải thích đúng theo định nghĩa trang 1.",
                                "supported": True,
                                "explanation_supported": True,
                                "unique_answer": True,
                                "qualifiers_preserved": True,
                                "difficulty_matches": True,
                                "verified_answer": "A",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "flashcards" in prompt.lower():
                return json.dumps(
                    {
                        "flashcards": [
                            {
                                "front": "Khi nào ma trận vuông A khả nghịch?",
                                "back": "khả nghịch",
                                "evidence_quote": "Ma trận vuông A được gọi là khả nghịch nếu tồn tại ma trận vuông B sao cho AB = BA = I.",
                                "page_num": 1,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "questions": [
                        {
                            "question": "Khi nào ma trận vuông A khả nghịch?",
                            "choices": {
                                "A": "Khi tồn tại B sao cho AB = BA = I",
                                "B": "Khi det(A) = 0",
                                "C": "Khi A là ma trận không",
                                "D": "Khi A có các hàng giống nhau",
                            },
                            "answer": "A",
                            "explanation": "Theo định nghĩa trang 1.",
                            "evidence_quote": "Ma trận vuông A được gọi là khả nghịch nếu tồn tại ma trận vuông B sao cho AB = BA = I.",
                            "page_num": 1,
                        }
                    ]
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(_srv, "DEFAULT_CHAT_MODEL", "test-ollama")
    monkeypatch.setattr(_srv.study_material_manager, "ollama", MockStudyClient())
    monkeypatch.setattr(study_material_manager, "ollama", MockStudyClient())
    gen_res = client.post(
        "/api/materials/generate-from-library",
        json={
            "material_id": mat_id,
            "filename": "upload_ephemeral_note.txt",
            "quiz_count": 1,
            "flashcard_count": 1,
            "difficulty": "easy",
        },
    )
    assert gen_res.status_code == 200, gen_res.text  # nosec B101
    mat = gen_res.json()["material"]
    assert len(mat["quiz"]) >= 1  # nosec B101
    assert len(mat["flashcards"]) >= 1  # nosec B101
    pdf_res = client.post(
        "/api/learning/export-pdf",
        json={
            "topic": mat["filename"],
            "difficulty": mat["difficulty"],
            "material_id": mat["id"],
        },
    )
    assert pdf_res.status_code == 200  # nosec B101
    assert pdf_res.content.startswith(b"%PDF-")  # nosec B101

    # 2. Scanned (<40 char text) PDF upload for study set must return clear 422 instead of fake quiz
    import fitz as _fitz
    scan_doc = _fitz.open()
    p = scan_doc.new_page()
    p.insert_text((72, 72), "Hi")
    scan_bytes = scan_doc.tobytes()
    scan_doc.close()
    scan_b64 = base64.b64encode(scan_bytes).decode("ascii")
    scan_res = client.post(
        "/api/materials/upload",
        json={
            "filename": "scanned_only.pdf",
            "file_base64": scan_b64,
            "generate_study_set": True,
            "persist_to_src": False,
        },
    )
    assert scan_res.status_code == 422  # nosec B101
    assert any(kw in scan_res.json()["detail"].lower() for kw in ("hình ảnh", "văn bản", "scan", "ocr", "đọc"))  # nosec B101


def test_flashcards_tolerate_pdf_soft_linewrap_inside_evidence_quote(monkeypatch):
    manager = StudyMaterialManager()
    monkeypatch.setattr(manager.ollama, "check_health", lambda: False)
    page_text = "Tính đóng \ngói (Encapsulation) che giấu dữ liệu nội bộ của đối tượng."

    class SoftWrapClient:
        last_done_reason = "stop"

        def check_health(self):
            return True

        def chat_complete(self, **kwargs):
            import json as _json
            self.last_done_reason = "stop"
            prompt = kwargs["messages"][-1]["content"]
            if "flashcards" in prompt.lower():
                return _json.dumps(
                    {
                        "flashcards": [
                            {
                                "front": "Tính chất nào che giấu dữ liệu nội bộ?",
                                "back": "đóng gói",
                                "evidence_quote": page_text,
                                "page_num": 1,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return _json.dumps({"questions": []})

    from app.pdf_extractor import PDFExtractionResult
    monkeypatch.setattr(manager, "ollama", SoftWrapClient())
    extracted = PDFExtractionResult(filename="oop.pdf", filepath=Path("oop.pdf"))
    extracted.clean_title = "OOP"
    extracted.total_pages = 1
    extracted.total_chars = len(page_text)
    extracted.extracted_pages = [{"page_num": 1, "text": page_text}]
    record = manager.generate_materials_from_pages(extracted, quiz_count=0, flashcard_count=1, model="test-ollama")
    assert record["generation"]["flashcards"] == "model"  # nosec B101
    assert len(record["flashcards"]) == 1  # nosec B101
    assert "[…]" in record["flashcards"][0]["front"]  # nosec B101
    assert record["flashcards"][0]["back"] == "đóng gói"  # nosec B101


def test_account_private_generate_from_library_persists_across_restart_and_lists_material_id(tmp_path, monkeypatch):
    import json as _json
    from app.accounts import account_id
    from app.account_services import make_material_manager
    from app.pdf_extractor import PDFExtractionResult
    from app import server as _srv

    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))
    tok = account_id.set("cc" * 16)
    try:
        mgr1 = make_material_manager()

        class MockStudyModel:
            last_done_reason = "stop"

            def check_health(self):
                return True

            def chat_complete(self, **kwargs):
                self.last_done_reason = "stop"
                prompt = kwargs["messages"][0]["content"]
                if "Kiểm định độc lập từng câu" in prompt:
                    return _json.dumps({
                        "checks": [{
                            "index": 1,
                            "source_basis": "Write-Back chỉ cập nhật cache và bật bit Dirty.",
                            "matching_choices": "A",
                            "input_audit": "Đầy đủ dữ kiện.",
                            "independent_solution": "Write-Back chỉ cập nhật cache.",
                            "choice_comparison": "Khớp A.",
                            "explanation_audit": "Chuẩn.",
                            "supported": True,
                            "explanation_supported": True,
                            "unique_answer": True,
                            "qualifiers_preserved": True,
                            "difficulty_matches": True,
                            "verified_answer": "A",
                        }]
                    }, ensure_ascii=False)
                if "flashcards" in prompt.lower():
                    return _json.dumps({
                        "flashcards": [{
                            "front": "Write-Back cập nhật đâu?",
                            "back": "bit Dirty",
                            "evidence_quote": "Write-Back chỉ cập nhật cache và bật bit Dirty.",
                            "page_num": 1,
                        }]
                    }, ensure_ascii=False)
                return _json.dumps({
                    "questions": [{
                        "question": "Write-Back thực hiện gì khi ghi dữ liệu?",
                        "choices": {"A": "Chỉ cập nhật cache và bật bit Dirty", "B": "Ghi ngay RAM", "C": "Xóa cache", "D": "Khóa bus"},
                        "answer": "A",
                        "explanation": "Theo tài liệu trang 1.",
                        "evidence_quote": "Write-Back chỉ cập nhật cache và bật bit Dirty.",
                        "page_num": 1,
                    }]
                }, ensure_ascii=False)

        monkeypatch.setattr(_srv, "DEFAULT_CHAT_MODEL", "test-ollama")
        raw_bytes = "Write-Back chỉ cập nhật cache và bật bit Dirty.".encode("utf-8")
        ext = mgr1.extract_document("private_notes.txt", raw_bytes)
        ext._raw_bytes = raw_bytes
        rec1 = mgr1.store_extracted_document(ext)

        monkeypatch.setattr(mgr1, "ollama", MockStudyModel())
        rec2 = mgr1.generate_from_library_document(
            material_id=rec1["id"], quiz_count=1, flashcard_count=1, difficulty="easy", model="test-ollama"
        )
        assert rec2["id"] != rec1["id"]  # nosec B101
        assert len(rec2["flashcards"]) == 1  # nosec B101

        # Simulate backend restart: brand-new PrivateMaterials manager must load persisted rec2 and expose material_id
        mgr2 = make_material_manager()
        loaded = mgr2.get_material(rec2["id"])
        assert loaded is not None  # nosec B101
        assert len(loaded["flashcards"]) == 1  # nosec B101
        assert len(loaded["quiz"]) == 1  # nosec B101
        docs = mgr2.list_available_documents([])
        priv = next(d for d in docs if d["filename"] == "private_notes.txt")
        assert priv["material_id"] in (rec1["id"], rec2["id"])  # nosec B101
    finally:
        account_id.reset(tok)


def test_flashcard_and_quiz_share_passage_prefix_and_focused_context():
    from app.flashcard_generator import (
        build_flashcards_prompt,
        extract_focused_flashcard_context,
        flashcard_response_schema,
    )
    from app.quiz_generator import build_quiz_prompt, quiz_response_schema

    passages = [
        {"page_num": 2, "text": "Câu mở đầu ngữ cảnh dài. " * 25 + "Bộ nhớ đệm KV giúp tái sử dụng tiền tố nhanh hơn. Câu kết bổ sung điều kiện."},
    ]
    quiz_prompt = build_quiz_prompt(passages, count=3, difficulty="medium")
    flash_prompt = build_flashcards_prompt(passages, count=6)
    marker = "\n<task>\n"
    assert marker in quiz_prompt and marker in flash_prompt  # nosec B101
    assert quiz_prompt.split(marker, 1)[0] == flash_prompt.split(marker, 1)[0]  # nosec B101
    assert "flashcards" in flash_prompt.lower()  # nosec B101
    assert flashcard_response_schema(["Q1"])["additionalProperties"] is False  # nosec B101
    assert quiz_response_schema("hard", ["Q1"])["additionalProperties"] is False  # nosec B101

    long_masked = ("A " * 150) + "Điều kiện X cần […], áp dụng khi n > 0. " + ("B " * 150)
    focused = extract_focused_flashcard_context(long_masked, max_chars=240)
    assert "[…]" in focused  # nosec B101
    assert len(focused) <= 250  # nosec B101
    code_masked = (
        "[NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH; KÝ HIỆU CHUẨN HÓA]\n"
        + ("Header context line. " * 25)
        + "\nvecIntegers.push_back(50);\nvecIntegers.[…] ();\nreturn 0;"
    )
    code_focused = extract_focused_flashcard_context(code_masked, max_chars=240)
    assert "NỘI DUNG NGUỒN" not in code_focused  # nosec B101
    assert "vecIntegers.push_back(50);" in code_focused  # nosec B101
    assert "vecIntegers.[…] ();" in code_focused  # nosec B101
    assert StudyMaterialManager._mask_answer_in_quote("Mã chuẩn UTF–8 được dùng.", "UTF-8") == "Mã chuẩn […] được dùng."  # nosec B101


def test_flashcard_mask_respects_hyphen_and_word_boundaries():
    quote = "• non-mutating algorithms\n• mutating algorithms"
    masked = StudyMaterialManager._mask_answer_in_quote(quote, "mutating algorithms")
    assert masked == "• non-mutating algorithms\n• […]"  # nosec B101


def test_easy_cloze_quiz_supports_negated_contrasts_bounded_spans_and_practice_system_prompt(monkeypatch, tmp_path):
    import json
    import app.model_resolver as model_resolver
    import app.token_counter as tkc
    from app.pdf_extractor import PDFExtractionResult
    from app.practice_adapter import SYSTEM_PROMPT as PRACTICE_SYSTEM_PROMPT
    from app.practice_proof import native_grounded_review_schema
    import app.trained_client as tc

    (tmp_path / "config.json").write_text('{"max_position_embeddings":8192}', encoding="utf-8")
    monkeypatch.setattr(model_resolver.sys, "platform", "darwin")
    monkeypatch.setattr(model_resolver, "MLX_9B_DIR", tmp_path)
    monkeypatch.setattr(tkc, "get_exact_token_counter", lambda *args: lambda messages: 200)

    pages = [
        {"page_num": 21, "text": "To use STL algorithms include #include <algorithm>.\nThis is precisely why STL supplies these algorithms.\n• non-mutating algorithms\n• mutating algorithms"},
        {"page_num": 22, "text": "Non-Mutating Algorithms\nAlgorithms that change neither order nor contents are called non-mutating algorithms."},
        {"page_num": 23, "text": "Mutating Algorithms\nMutating algorithms change the contents or order of a sequence."},
    ]
    extracted = PDFExtractionResult(filename="stl.pdf", filepath=Path("stl.pdf"))
    extracted.extracted_pages = pages
    extracted.total_pages = 3
    extracted.total_chars = sum(len(p["text"]) for p in pages)

    recorded_reviews = []

    class FakeMLXStudyClient:
        last_done_reason = "stop"

        def __init__(self, *a, **k):
            pass

        @classmethod
        def available(cls, *a, **k):
            return True

        def chat_complete(self, messages, system_prompt="", **kwargs):
            user_text = messages[0]["content"]
            if "Kiểm định độc lập từng câu" in user_text:
                recorded_reviews.append(system_prompt)
                items_in = json.JSONDecoder().raw_decode(user_text.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
                checks_out = []
                for item in items_in:
                    ans = next(k for k, v in item["choices"].items() if v.casefold() in item["source"].casefold() and "[…]" in item["question"] and k in {"A", "B", "C", "D"} and (
                        ("non-mutating" in item["source"].casefold() and v == "non-mutating algorithms")
                        or ("mutating algorithms change" in item["source"].casefold() and v == "mutating algorithms")
                        or ("#include <algorithm>" in item["source"].casefold() and v == "algorithm")
                    ))
                    checks_out.append({
                        "index": item["index"],
                        "input_audit": "Passage states concept directly.",
                        "source_basis": item["source"],
                        "independent_solution": "Directly stated in source passage.",
                        "supported": True,
                        "qualifiers_preserved": True,
                        "choice_comparison": f"{ans} matches source.",
                        "matching_choices": ans,
                        "unique_answer": True,
                        "verified_answer": ans,
                        "explanation_audit": "Matches source directly.",
                        "explanation_supported": True,
                        "difficulty_matches": False,
                    })
                return json.dumps({"checks": checks_out})
            return json.dumps({
                "questions": [
                    {"format": "source_cloze", "answer": "non-mutating algorithms", "distractors": ["mutating algorithms", "STL Containers", "STL Iterators"], "evidence_id": "Q2"},
                    {"format": "source_cloze", "answer": "mutating algorithms", "distractors": ["non-mutating algorithms", "STL Containers", "STL Algorithms"], "evidence_id": "Q3"},
                    {"format": "source_cloze", "answer": "algorithm", "distractors": ["vector", "iterator", "container"], "evidence_id": "Q1"},
                ]
            })

    monkeypatch.setattr(tc, "TrainedModelClient", FakeMLXStudyClient)
    res = StudyMaterialManager().generate_materials_from_pages(
        extracted, quiz_count=3, flashcard_count=0, difficulty="easy", model="qwen3.5-9b-4bit"
    )
    assert len(res["quiz"]) == 3  # nosec B101
    assert recorded_reviews and all(sp_val == PRACTICE_SYSTEM_PROMPT for sp_val in recorded_reviews)  # nosec B101
    props = list(native_grounded_review_schema(1, None)["properties"]["checks"]["items"]["properties"].keys())
    assert props.index("independent_solution") < props.index("supported")  # nosec B101


def test_source_cloze_quiz_strips_source_review_banner(monkeypatch, tmp_path):
    import json
    import app.model_resolver as model_resolver
    import app.token_counter as tkc
    from app.pdf_extractor import PDFExtractionResult
    import app.trained_client as tc

    (tmp_path / "config.json").write_text('{"max_position_embeddings":8192}', encoding="utf-8")
    monkeypatch.setattr(model_resolver.sys, "platform", "darwin")
    monkeypatch.setattr(model_resolver, "MLX_9B_DIR", tmp_path)
    monkeypatch.setattr(tkc, "get_exact_token_counter", lambda *args: lambda messages: 200)

    pages = [
        {
            "page_num": 1,
            "text": "[NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH; KÝ HIỆU CHUẨN HÓA]\nKhái niệm vector trong không gian là đại lượng có hướng và độ lớn.",
        }
    ]
    extracted = PDFExtractionResult(filename="doc.pdf", filepath=Path("doc.pdf"))
    extracted.extracted_pages = pages
    extracted.total_pages = 1
    extracted.total_chars = sum(len(p["text"]) for p in pages)

    class FakeQuizClient:
        last_done_reason = "stop"

        def __init__(self, *a, **kw):
            pass

        @classmethod
        def available(cls, *a, **kw):
            return True

        def chat_complete(self, messages, **kwargs):
            user_text = messages[0]["content"]
            if "Kiểm định độc lập từng câu" in user_text:
                items_in = json.JSONDecoder().raw_decode(user_text.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
                ans = next(k for k, v in items_in[0]["choices"].items() if v == "đại lượng có hướng và độ lớn")
                return json.dumps(
                    {
                        "checks": [
                            {
                                "index": items_in[0]["index"],
                                "input_audit": "Passage states vector definition directly.",
                                "source_basis": items_in[0]["source"],
                                "independent_solution": "Vector definition stated directly.",
                                "supported": True,
                                "qualifiers_preserved": True,
                                "choice_comparison": f"{ans} matches source.",
                                "matching_choices": ans,
                                "unique_answer": True,
                                "verified_answer": ans,
                                "explanation_audit": "Matches source directly.",
                                "explanation_supported": True,
                                "difficulty_matches": True,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "questions": [
                        {
                            "format": "source_cloze",
                            "evidence_id": "Q1",
                            "answer": "đại lượng có hướng và độ lớn",
                            "distractors": ["vô hướng", "ma trận vuông", "hàm liên tục"],
                        }
                    ]
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(tc, "TrainedModelClient", FakeQuizClient)
    res = StudyMaterialManager().generate_materials_from_pages(
        extracted, quiz_count=1, flashcard_count=0, difficulty="easy", model="qwen3.5-9b-4bit"
    )
    assert len(res["quiz"]) == 1  # nosec B101
    q_text = res["quiz"][0]["question"]
    assert "NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH" not in q_text  # nosec B101
    assert "[…]" in q_text  # nosec B101


def test_fallback_flashcards_strips_banner_and_fills_up_to_count():
    from app.flashcard_generator import fallback_generate_flashcards

    cards = fallback_generate_flashcards(
        [
            {
                "page_num": 1,
                "text": "[NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH; KÝ HIỆU CHUẨN HÓA]\nKhái niệm vector là đại lượng có hướng và độ lớn xác định. Tích vô hướng cho biết góc giữa hai vector trong không gian.",
            },
            {
                "page_num": 2,
                "text": "Chuẩn hóa vector đưa độ dài vector về bằng 1 để tiện tính toán. Vector đơn vị có độ lớn luôn bằng 1 trên mọi trục.",
            },
        ],
        count=4,
    )
    assert len(cards) == 4  # nosec B101
    assert len({c["back"] for c in cards}) == 4  # nosec B101
    assert all("NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH" not in c["back"] for c in cards)  # nosec B101


@pytest.mark.parametrize("mode", ["timeout_503", "partial_200", "unavailable_hard_422", "unsupported_422"])
def test_api_material_review_timeout_503_partial_200_and_422_controls(client, monkeypatch, mode):
    import json as _json
    from app import server as _srv
    import app.token_counter as tkc

    if mode == "unsupported_422":
        bad_b64 = base64.b64encode(b".").decode("ascii")
        res = client.post("/api/materials/upload", json={
            "filename": "insufficient.txt",
            "file_base64": bad_b64,
            "quiz_count": 2,
            "flashcard_count": 1,
            "difficulty": "easy",
        })
        assert res.status_code == 422  # nosec B101
        return

    monkeypatch.setattr(
        tkc,
        "get_exact_token_counter",
        lambda *args: lambda messages: sum(len(m["content"]) // 3 for m in messages),
    )

    doc_text = (
        "Write-Back chỉ cập nhật cache và bật bit Dirty trước khi trả về. "
        "Write-Through ghi đồng thời vào cả cache và bộ nhớ chính RAM. "
        "Hit ratio là tỷ lệ truy cập tìm thấy dữ liệu trong bộ nhớ đệm. "
        "Miss penalty là thời gian xử lý bổ sung khi không tìm thấy dữ liệu trong cache. "
        "Giao thức MESI giữ tính nhất quán dữ liệu giữa các bộ nhớ đệm riêng."
    )
    b64 = base64.b64encode(doc_text.encode("utf-8")).decode("ascii")

    class RouteClient:
        last_done_reason = "stop"

        def check_health(self):
            return True

        def chat_complete(self, messages, **kwargs):
            prompt = messages[0]["content"]
            if "Kiểm định độc lập từng câu" in prompt:
                assert "Kiểm định độc lập từng câu từ source và question" in prompt  # nosec B101
                assert "điền khuyết từ source" not in prompt  # nosec B101
                items = _json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
                if mode == "timeout_503":
                    raise TimeoutError("Hết thời gian chờ phản hồi từ worker cục bộ.")
                if mode == "partial_200":
                    if "Write-Through" in items[0]["choices"].values():
                        raise TimeoutError("Hết thời gian chờ phản hồi từ worker cục bộ.")
                    ans = next(k for k, v in items[0]["choices"].items() if v == "bit Dirty")
                    return _json.dumps({
                        "checks": [{
                            "index": 1,
                            "input_audit": "Đề bài hỏi khái niệm trực tiếp trong đoạn nguồn.",
                            "independent_solution": "Write-Back chỉ cập nhật cache và bật bit Dirty.",
                            "choice_comparison": f"{ans} khớp chính xác; các lựa chọn khác sai.",
                            "explanation_audit": "Giải thích trích nguyên văn trang 1 từ đoạn nguồn.",
                            "supported": True,
                            "explanation_supported": True,
                            "qualifiers_preserved": True,
                            "unique_answer": True,
                            "difficulty_matches": True,
                            "verified_answer": ans,
                        }]
                    }, ensure_ascii=False)
                return _json.dumps({
                    "checks": [{
                        "index": 1,
                        "input_audit": "Đề bài chỉ hỏi lại khái niệm đơn lẻ.",
                        "independent_solution": "Định nghĩa trực tiếp.",
                        "choice_comparison": "Khớp A.",
                        "explanation_audit": "Không đủ hai bước suy luận.",
                        "supported": True,
                        "explanation_supported": True,
                        "unique_answer": True,
                        "qualifiers_preserved": True,
                        "difficulty_matches": False,
                        "verified_answer": "A",
                    }]
                }, ensure_ascii=False)
            if "flashcards" in prompt.lower():
                return _json.dumps({"flashcards": [{"answer": "Giao thức MESI", "evidence_id": "Q1"}]}, ensure_ascii=False)
            if mode == "unavailable_hard_422":
                return _json.dumps({
                    "questions": [{
                        "question": "Write-Back làm gì khi ghi dữ liệu?",
                        "choices": {"A": "Chỉ cập nhật cache và bật bit Dirty", "B": "Ghi ngay RAM", "C": "Xóa cache", "D": "Khóa bus"},
                        "answer": "A",
                        "explanation": "Theo đoạn nguồn trang 1.",
                        "evidence_id": "Q1",
                        "difficulty": "hard",
                    }]
                }, ensure_ascii=False)
            return _json.dumps({
                "questions": [
                    {"format": "source_cloze", "evidence_id": "Q1", "answer": "bit Dirty", "distractors": ["bit Valid", "bit Lock", "bit Parity"]},
                    {"format": "source_cloze", "evidence_id": "Q1", "answer": "Write-Through", "distractors": ["Write-Around", "Write-Once", "Write-None"]},
                ]
            }, ensure_ascii=False)

    monkeypatch.setattr(_srv, "DEFAULT_CHAT_MODEL", "test-ollama")
    monkeypatch.setattr(_srv.study_material_manager, "ollama", RouteClient())
    monkeypatch.setattr(study_material_manager, "ollama", RouteClient())

    res = client.post("/api/materials/upload", json={
        "filename": "cache_notes.txt",
        "file_base64": b64,
        "quiz_count": 2,
        "flashcard_count": 1,
        "difficulty": "hard" if mode == "unavailable_hard_422" else "easy",
    })
    if mode == "timeout_503":
        assert res.status_code == 503  # nosec B101
        assert "kiểm định câu hỏi" in res.json()["detail"]  # nosec B101
    elif mode == "partial_200":
        assert res.status_code == 200, res.text  # nosec B101
        mat = res.json()["material"]
        assert mat["actual_quiz_count"] == 1  # nosec B101
        assert mat["requested_quiz_count"] == 2  # nosec B101
        assert mat["complete"] is False  # nosec B101
        assert "quá thời gian" in (mat.get("warning") or "")  # nosec B101
    else:
        assert res.status_code == 422  # nosec B101
        assert "khó" in res.json()["detail"]  # nosec B101


def test_private_upload_easy_cloze_parity_with_compact_review_and_timeout_503(tmp_path, monkeypatch):
    import json as _json
    import app.model_resolver as model_resolver
    import app.token_counter as tkc
    import app.trained_client as tc
    from app.accounts import account_id
    from app.account_services import make_material_manager

    mlx_dir = tmp_path / "mlx"
    mlx_dir.mkdir()
    (mlx_dir / "config.json").write_text('{"max_position_embeddings":8192}', encoding="utf-8")
    monkeypatch.setattr(model_resolver.sys, "platform", "darwin")
    monkeypatch.setattr(model_resolver, "MLX_9B_DIR", mlx_dir)
    monkeypatch.setattr(
        tkc,
        "get_exact_token_counter",
        lambda *args: lambda messages: sum(len(m["content"]) // 3 for m in messages),
    )

    monkeypatch.setenv("STUDY_ACCOUNTS_DIR", str(tmp_path / "accounts"))
    tok = account_id.set("dd" * 16)
    try:
        mgr = make_material_manager()
        raw_text = (
            "Write-Back chỉ cập nhật cache và bật bit Dirty trước khi trả về. "
            "Giao thức MESI duy trì nhất quán giữa các bộ nhớ đệm."
        )
        raw_bytes = raw_text.encode("utf-8")
        ext = mgr.extract_document("private_cloze.txt", raw_bytes)
        ext._raw_bytes = raw_bytes
        rec = mgr.store_extracted_document(ext)

        def _make_mlx_client(timeout_review: bool):
            class PrivateClozeClient:
                last_done_reason = "stop"

                def __init__(self, *args, **kwargs):
                    pass

                @classmethod
                def available(cls, *args, **kwargs):
                    return True

                def chat_complete(self, messages, **kwargs):
                    prompt = messages[0]["content"]
                    if "Kiểm định độc lập từng câu điền khuyết" in prompt:
                        if timeout_review:
                            raise TimeoutError("Hết thời gian chờ phản hồi từ worker cục bộ.")
                        items = _json.JSONDecoder().raw_decode(prompt.split("Dữ liệu cần kiểm định:\n", 1)[1])[0]
                        ans = next(k for k, v in items[0]["choices"].items() if v == "bit Dirty")
                        return _json.dumps({
                            "checks": [{
                                "index": 1,
                                "source_basis": "Write-Back chỉ cập nhật cache và bật bit Dirty trước khi trả về.",
                                "choice_comparison": f"{ans} khớp chính xác; các lựa chọn khác sai.",
                                "matching_choices": ans,
                                "supported": True,
                                "qualifiers_preserved": True,
                                "unique_answer": True,
                                "verified_answer": ans,
                            }]
                        }, ensure_ascii=False)
                    return _json.dumps({
                        "questions": [{
                            "format": "source_cloze",
                            "evidence_id": "Q1",
                            "answer": "bit Dirty",
                            "distractors": ["bit Valid", "bit Lock", "bit Parity"],
                        }],
                        "flashcards": [{"answer": "Giao thức MESI", "evidence_id": "Q1"}],
                    }, ensure_ascii=False)

            return PrivateClozeClient

        monkeypatch.setattr(tc, "TrainedModelClient", _make_mlx_client(timeout_review=False))
        out = mgr.generate_from_library_document(
            material_id=rec["id"], quiz_count=1, flashcard_count=1, difficulty="easy", model="qwen3.5-9b-4bit"
        )
        assert out["actual_quiz_count"] == 1  # nosec B101
        assert out["actual_flashcard_count"] == 1  # nosec B101
        assert out["quiz"][0]["explanation"] == f"Căn cứ trực tiếp theo tài liệu trang 1: {raw_text}"  # nosec B101
        reloaded = make_material_manager().get_material(out["id"])
        assert reloaded is not None and reloaded["actual_quiz_count"] == 1  # nosec B101

        monkeypatch.setattr(tc, "TrainedModelClient", _make_mlx_client(timeout_review=True))
        with pytest.raises(StudyMaterialError, match="Không hoàn thành được bước kiểm định câu hỏi"):
            mgr.generate_from_library_document(
                material_id=rec["id"], quiz_count=1, flashcard_count=1, difficulty="easy", model="qwen3.5-9b-4bit"
            )
    finally:
        account_id.reset(tok)
