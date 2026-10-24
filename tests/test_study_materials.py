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
    monkeypatch.setattr(server, 'document_lookup', DocumentLookup(db_path=db, src_dir=src))
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
    assert "kh\u00f4ng \u0111\u01b0\u1ee3c h\u1ed7 tr\u1ee3" in str(exc.value)


def test_validation_oversized_file():
    manager = StudyMaterialManager()
    oversized = b"x" * (21 * 1024 * 1024)
    with pytest.raises(StudyMaterialError) as exc:
        manager.validate_file("big.txt", oversized)
    assert "v\u01b0\u1ee3t qu\u00e1 gi\u1edbi h\u1ea1n cho ph\u00e9p" in str(exc.value)


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
    assert len(quiz) >= 1
    for q in quiz:
        assert "question" in q
        assert "choices" in q and len(q["choices"]) == 4
        assert q["correct_answer"] in ["A", "B", "C", "D"]
        assert "evidence_quote" in q
        assert "page_num" in q
        assert q["page_num"] in [1, 2]

    flashcards = manager.fallback_generate_flashcards(sample_pages, count=5)
    assert len(flashcards) >= 1
    for f in flashcards:
        assert "front" in f
        assert "back" in f
        assert "page_num" in f
        assert "evidence_quote" in f


def test_api_upload_material_text_file(client):
    content = "L\u1ecbch s\u1eed Vi\u1ec7t Nam l\u00e0 qu\u00e1 tr\u00ecnh d\u1ef1ng n\u01b0\u1edbc v\u00e0 gi\u1eef n\u01b0\u1edbc c\u1ee7a d\u00e2n t\u1ed9c Vi\u1ec7t Nam qua h\u00e0ng ng\u00e0n n\u0103m. Chi\u1ebfn th\u1eafng B\u1ea1ch \u0110\u1eb1ng n\u0103m 938 ch\u1ea5m d\u1ee9t ngh\u00ecn n\u0103m B\u1eafc thu\u1ed9c."
    b64 = "data:text/plain;base64," + base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    res = client.post("/api/materials/upload", json={
        "filename": "lich_su.txt",
        "file_base64": b64
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    material = data["material"]
    assert material["filename"] == "lich_su.txt"
    assert len(material["quiz"]) >= 1
    assert len(material["flashcards"]) >= 1
    assert material["id"] in study_material_manager.materials_cache


def test_api_get_material_by_id(client):
    content = "Thu\u1eadt to\u00e1n t\u00ecm ki\u1ebfm nh\u1ecb ph\u00e2n c\u00f3 \u0111\u1ed9 ph\u1ee9c t\u1ea1p O(log n) tr\u00ean m\u1ea3ng \u0111\u00e3 s\u1eafp x\u1ebfp."
    b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    
    upload_res = client.post("/api/materials/upload", json={
        "filename": "algorithms.txt",
        "file_base64": b64
    })
    assert upload_res.status_code == 200
    mat_id = upload_res.json()["material"]["id"]
    
    get_res = client.get(f"/api/materials/{mat_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == mat_id
    
    # Not found case
    not_found = client.get("/api/materials/nonexistent_id_12345")
    assert not_found.status_code == 404


def test_api_list_library_documents(client):
    res = client.get("/api/materials/documents")
    assert res.status_code == 200
    data = res.json()
    assert "documents" in data
    assert isinstance(data["documents"], list)


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
            "filename": test_filename
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        material = data["material"]
        assert len(material["quiz"]) >= 1
        assert len(material["flashcards"]) >= 1
        assert material["quiz"][0]["page_num"] == 1
        assert "Newton" in material["quiz"][0]["evidence_quote"]
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
        "filename": "dinh_ly_pythagoras.png",
        "file_base64": b64_png,
    })
    assert res_png.status_code == 200
    data_png = res_png.json()
    assert data_png["status"] == "success"
    assert data_png["material"]["filename"] == "dinh_ly_pythagoras.png"
    assert len(data_png["material"]["quiz"]) >= 1
    assert len(data_png["material"]["flashcards"]) >= 1

    # 2. JPEG support with explicit recognized text; unreadable images are rejected separately.
    buf_jpg = io.BytesIO()
    img.save(buf_jpg, format="JPEG")
    b64_jpg = "data:image/jpeg;base64," + base64.b64encode(buf_jpg.getvalue()).decode("ascii")

    res_jpg = client.post("/api/materials/upload", json={
        "filename": "so_do_tam_ly_hoc_nhan_thuc.jpg",
        "file_base64": b64_jpg,
    })
    assert res_jpg.status_code == 200
    data_jpg = res_jpg.json()
    assert data_jpg["status"] == "success"
    assert data_jpg["material"]["filename"] == "so_do_tam_ly_hoc_nhan_thuc.jpg"
    assert len(data_jpg["material"]["quiz"]) >= 1
    assert len(data_jpg["material"]["flashcards"]) >= 1


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
        "filename": "bai_giang_tri_nho.png",
        "file_base64": b64_png,
    })
    assert res.status_code == 200
    material = res.json()["material"]
    assert material["total_pages"] == 1
    assert len(material["quiz"]) >= 1
    assert len(material["flashcards"]) >= 1


def test_api_upload_corrupted_image_rejected(client):
    fake_png = b"\x89PNG\r\n\x1a\ncorrupted_not_a_real_image_stream"
    b64 = "data:image/png;base64," + base64.b64encode(fake_png).decode("ascii")
    res = client.post("/api/materials/upload", json={
        "filename": "broken.png",
        "file_base64": b64,
    })
    assert res.status_code == 422
    assert "bị hỏng" in res.json()["detail"] or "không đúng định dạng" in res.json()["detail"]


def test_chat_attach_button_left_alignment_and_full_width_bottom_toolbar():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    css = (root / "static/style.css").read_text(encoding="utf-8")
    html = (root / "static/index.html").read_text(encoding="utf-8")

    assert "align-items: stretch;" in css
    assert ".chat-attach-wrapper" in css
    assert "margin-right: auto;" in css
    assert 'id="chat-attach-wrapper"' in html
    assert 'id="btn-chat-attach"' in html


def test_attachment_preview_card_modal_structure_and_centered_chat_layout(client):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    css = (root / "static/style.css").read_text(encoding="utf-8")
    html = (root / "static/index.html").read_text(encoding="utf-8")
    js = (root / "static/app.js").read_text(encoding="utf-8")

    # Compact attachment banner retains reader access and removal controls.
    assert 'id="chat-attachment-preview-tray"' in html
    assert ".chat-attachment-card" in css
    assert ".attachment-card-open" in css
    assert ".attachment-doc-sheet" not in css
    assert "buildDocSheetPreviewHtml" not in js
    assert 'class="attachment-card-open"' in js
    assert ".attachment-card-remove" in css
    assert "upsertAttachmentPreviewCard" in js
    assert "removeAttachmentPreviewCard" in js

    # 2. Ensure #ocr-modal is closed BEFORE #library-picker-modal and #study-material-modal
    ocr_idx = html.index('id="ocr-modal"')
    lib_idx = html.index('id="library-picker-modal"')
    study_idx = html.index('id="study-material-modal"')
    ocr_segment = html[ocr_idx:lib_idx]
    assert ocr_segment.count("<div") == ocr_segment.count("</div>"), (
        "#ocr-modal must be completely closed before #library-picker-modal so study modals are not hidden inside #ocr-modal"
    )
    assert lib_idx < study_idx

    # 3. Ensure welcome-card hides on has-chat and both frames share centered 720px max-width without negative translateY
    assert ".messages-container.has-chat .welcome-card" in html
    assert "display: none !important;" in html
    assert "transform: translateY(-16px)" not in html
    assert ".message.study-card-message" in css

    # 4. Ensure /api/materials/documents lists available documents and /api/materials/generate-from-library works
    docs_res = client.get("/api/materials/documents")
    assert docs_res.status_code == 200
    docs = docs_res.json().get("documents", [])
    assert len(docs) >= 1
    first_doc_name = docs[0]["filename"]

    gen_res = client.post("/api/materials/generate-from-library", json={"filename": first_doc_name})
    assert gen_res.status_code == 200
    mat = gen_res.json()["material"]
    assert len(mat["quiz"]) >= 1
    assert len(mat["flashcards"]) >= 1
    assert len(mat.get("pages", [])) >= 1


def test_ingest_to_src_without_auto_quiz_and_on_demand_generate(client, tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    import app.config as cfg

    root = Path(__file__).resolve().parents[1]
    html = (root / "static/index.html").read_text(encoding="utf-8")
    js = (root / "static/app.js").read_text(encoding="utf-8")

    # Verify dedicated button & content viewer modal exist
    assert 'id="btn-create-quiz-flashcard"' in html
    assert 'id="document-content-modal"' in html
    assert "openDocumentContentViewer" in js
    assert "generateStudySetForActiveAttachment" in js

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
        "filename": "cay_nhi_phan.txt",
        "file_base64": b64,
        "generate_study_set": False,
        "persist_to_src": True,
    })
    assert res.status_code == 200
    mat = res.json()["material"]
    assert mat["saved_to_src"] is True
    assert mat["src_path"] == "src/cay_nhi_phan.txt"
    assert mat["study_generated"] is False
    assert mat["quiz"] == []
    assert mat["flashcards"] == []
    assert len(mat["pages"]) == 1
    assert "cây nhị phân" in mat["pages"][0]["text"]
    assert (temp_src / "cay_nhi_phan.txt").is_file()

    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT id, filename, status FROM documents WHERE filename = ?", ("cay_nhi_phan.txt",)).fetchone()
        assert row is not None
        assert row[2] == "indexed"
        chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (row[0],)).fetchone()[0]
        assert chunks >= 1

    # Step 2: Click dedicated button to generate Quiz & Flashcard on demand
    gen_res = client.post("/api/materials/generate-from-library", json={"filename": "cay_nhi_phan.txt"})
    assert gen_res.status_code == 200
    gen_mat = gen_res.json()["material"]
    assert gen_mat["study_generated"] is True
    assert len(gen_mat["quiz"]) >= 1
    assert len(gen_mat["flashcards"]) >= 1


def test_upload_webp_study_material(client, monkeypatch):
    mock_recognized_image(monkeypatch)
    import io
    from PIL import Image
    img = Image.new("RGB", (100, 50), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    res = client.post("/api/materials/upload", json={
        "filename": "diagram.webp",
        "file_base64": b64,
        "generate_study_set": True,
        "quiz_count": 2,
        "flashcard_count": 2,
        "persist_to_src": False,
    })
    assert res.status_code == 200
    mat = res.json()["material"]
    assert mat["filename"] == "diagram.webp"
    assert len(mat["quiz"]) >= 1
    assert len(mat["flashcards"]) >= 1


def test_canvas_document_viewer_ui_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "static/index.html").read_text(encoding="utf-8")
    css = (root / "static/style.css").read_text(encoding="utf-8")
    js = (root / "static/app.js").read_text(encoding="utf-8")

    # 1. Canvas Top Bar Controls & Action buttons
    assert 'id="document-content-modal"' in html
    assert 'class="modal canvas-document-modal"' in html
    assert 'class="canvas-top-bar"' in html
    assert 'id="btn-canvas-zoom-out"' in html
    assert 'id="btn-canvas-zoom-in"' in html
    assert 'id="canvas-zoom-label"' in html
    assert 'id="btn-canvas-download"' in html
    assert 'id="btn-canvas-expand"' in html
    assert 'id="btn-doc-content-generate"' in html
    assert 'id="btn-close-doc-content-modal"' in html

    # 2. Canvas Document Sheet structure
    assert 'id="canvas-doc-sheet"' in html
    assert 'id="doc-content-pages-list"' in html
    assert 'class="canvas-doc-flow"' in html

    # 3. Canvas CSS & User message attachment pill
    assert ".canvas-doc-sheet" in css
    assert ".canvas-h1" in css
    assert ".canvas-h2" in css
    assert ".canvas-callout-box" in css
    assert ".canvas-page-divider" in css
    assert ".message-attachment-card" in css
    assert ".message-attachment-icon" in css
    assert ".message-attachment-meta" in css
    assert "gradient(" not in css, "Flat design rule: No gradient in style.css"

    # 4. Canvas JS methods & Zoom logic
    assert "formatCanvasBlock" in js
    assert "applyCanvasZoom" in js
    assert "openDocumentContentViewer" in js


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
    assert res.status_code == 200

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
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert len(data.get("citations", [])) >= 1
    assert "On_Tap_Vat_Ly_12" in data["citations"][0]["filename"]
    assert "Vật lý nhiệt" in data["answer"] or "VẬT LÝ" in data["answer"]


def test_extract_docx_hierarchical_structure_and_tables():
    fixture_path = Path(__file__).parent / "fixtures" / "documents" / "On_Tap_Hoa_Hoc_12.docx"
    res = PDFExtractor.extract_docx(fixture_path)
    assert res.error_message is None
    # Must NOT fragment into 67 single-line pages! Must be 3 cohesive pages.
    assert res.total_pages == 3
    assert res.total_chars >= 3500
    assert res.unit_name == "trang"

    # Check Page 1 has Ester & Lipid & Callout
    p1 = res.extracted_pages[0]
    assert p1["page_num"] == 1
    assert "ESTER - LIPID - XÀ PHÒNG" in p1["text"]
    assert any(b["type"] == "callout" for b in p1["blocks"])

    # Check Page 2 has Carbohydrates & Table
    p2 = res.extracted_pages[1]
    assert p2["page_num"] == 2
    assert "CARBOHYDRATE" in p2["text"]
    table_blocks = [b for b in p2["blocks"] if b["type"] == "table"]
    assert len(table_blocks) >= 1
    assert "Monosaccharide" in table_blocks[0]["text"]
    assert "Disaccharide" in table_blocks[0]["text"]

    # Check Page 3 has Polymer & Kim loại
    p3 = res.extracted_pages[2]
    assert p3["page_num"] == 3
    assert "POLYMER" in p3["text"]
    assert "KIM LOẠI" in p3["text"]


def test_extract_docx_edge_cases(tmp_path):
    import zipfile

    # 1. Empty file
    empty_f = tmp_path / "empty.docx"
    empty_f.write_bytes(b"")
    res1 = PDFExtractor.extract_docx(empty_f)
    assert "rỗng" in (res1.error_message or "").lower()

    # 2. Corrupt non-zip file
    corrupt_f = tmp_path / "corrupt.docx"
    corrupt_f.write_bytes(b"Plain text disguised as docx")
    res2 = PDFExtractor.extract_docx(corrupt_f)
    assert "hợp lệ" in (res2.error_message or "").lower()

    # 3. Zip file without word/document.xml
    fake_zip = tmp_path / "fake.docx"
    with zipfile.ZipFile(fake_zip, "w") as z:
        z.writestr("notes.txt", "hello")
    res3 = PDFExtractor.extract_docx(fake_zip)
    assert "word/document.xml" in (res3.error_message or "")


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
    assert res.status_code == 200
    material = res.json()["material"]
    assert material["total_pages"] == 3

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
        assert passages, "The attachment overview must supply real admitted passages"
        for source_id, text in passages:
            excerpt = " ".join(unescape(text).split()[:30])
            yield f"{excerpt} [{source_id}]\n"

    monkeypatch.setattr(OllamaClient, "chat_stream", fake_chat_stream_summary)

    chat_res = client.post("/api/chat", json={
        "query": "tóm tắt nội dung có trong file",
        "attachment_filename": "On_Tap_Hoa_Hoc_12.docx",
        "save_history": False,
    })
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert len(data.get("citations", [])) >= 1
    # Must only cite On_Tap_Hoa_Hoc_12.docx
    for cit in data["citations"]:
        assert "On_Tap_Hoa_Hoc_12" in cit["filename"]
    assert "Ester" in data["answer"] or "HÓA HỌC" in data["answer"]


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
    assert upload_res.status_code == 200

    # 2. Turn 1 with attachment
    sess_res = client.post("/api/sessions", json={})
    assert sess_res.status_code == 200
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
    assert res1.status_code == 200
    assert len(res1.json().get("citations", [])) >= 1

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
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2.get("citations", [])) >= 1
    assert "On_Tap_Hoa_Hoc_12" in data2["citations"][0]["filename"]


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
    assert upload_res.status_code == 200

    # 2. Download raw file
    dl_res = client.get("/api/materials/download?filename=On_Tap_Hoa_Hoc_12.docx")
    assert dl_res.status_code == 200
    assert dl_res.content == raw_bytes
    assert "openxmlformats" in dl_res.headers.get("content-type", "")
    assert "attachment" in dl_res.headers.get("content-disposition", "")
    assert "On_Tap_Hoa_Hoc_12.docx" in dl_res.headers.get("content-disposition", "")

    # 3. Nonexistent file returns 404
    bad_res = client.get("/api/materials/download?filename=ghost_file.docx")
    assert bad_res.status_code == 404


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
    assert response.status_code == 422
    assert not study_material_manager.materials_cache


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
    assert first.status_code == 200
    first_mat = first.json()['material']
    second = _persist_upload(client, 'same-name.txt', incoming)
    assert second.status_code == 200
    second_mat = second.json()['material']
    assert (cfg.SRC_DIR / 'same-name.txt').read_text(encoding='utf-8') == original
    assert second_mat['filename'] != first_mat['filename']
    assert second_mat['doc_id'] != first_mat['doc_id']
    assert (cfg.SRC_DIR / second_mat['filename']).read_text(encoding='utf-8') == incoming
    assert second_mat['src_path'] == f"src/{second_mat['filename']}"
    with sqlite3.connect(cfg.DB_PATH) as db:
        old_text = ' '.join(r[0] for r in db.execute('SELECT text FROM chunks WHERE doc_id=?', (first_mat['doc_id'],)))
        new_text = ' '.join(r[0] for r in db.execute('SELECT text FROM chunks WHERE doc_id=?', (second_mat['doc_id'],)))
    assert 'ALPHA-271' in old_text and 'BETA-392' not in old_text
    assert 'BETA-392' in new_text and 'ALPHA-271' not in new_text


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
    assert response.status_code == 503
    assert 'private-storage-diagnostic-marker' not in response.text
    assert not (cfg.SRC_DIR / 'failed-upload.txt').exists()
    assert study_material_manager.resolve_raw_file(filename='failed-upload.txt') is None
    assert not study_material_manager.materials_cache
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT id,filename,file_hash FROM documents ORDER BY id').fetchall() == before


def test_uploading_identical_file_twice_keeps_one_stable_source(client):
    import sqlite3
    from app import config as cfg
    text = 'Nguồn học tập tổng hợp: mã nhận diện UNIQUE-619. Nội dung này phải tra cứu được sau upload.'
    first = _persist_upload(client, 'repeat.txt', text).json()['material']
    with sqlite3.connect(cfg.DB_PATH) as db:
        before = db.execute('SELECT text FROM chunks WHERE doc_id=? ORDER BY chunk_index', (first['doc_id'],)).fetchall()
    second = _persist_upload(client, 'repeat.txt', text).json()['material']
    assert first['doc_id'] == second['doc_id']
    assert first['filename'] == second['filename']
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT COUNT(*) FROM documents WHERE filename=?', (first['filename'],)).fetchone()[0] == 1
        assert db.execute('SELECT text FROM chunks WHERE doc_id=? ORDER BY chunk_index', (first['doc_id'],)).fetchall() == before
        assert db.execute('SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?', ('"UNIQUE-619"',)).fetchone()[0] >= 1


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
    assert second.status_code == 200
    assert second.json()['material']['doc_id'] == first['doc_id']
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT id,text,embedding FROM chunks WHERE doc_id=? ORDER BY id', (first['doc_id'],)).fetchall() == before
        assert db.execute('SELECT * FROM document_source_quality WHERE doc_id=?', (first['doc_id'],)).fetchone() == quality_before


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
    assert len({name for name, _, _ in results}) == 4
    assert len({doc_id for _, doc_id, _ in results}) == 4
    with sqlite3.connect(cfg.DB_PATH) as db:
        for name, doc_id, text in results:
            assert (cfg.SRC_DIR / name).read_text() == text
            chunks = ' '.join(r[0] for r in db.execute('SELECT text FROM chunks WHERE doc_id=?', (doc_id,)))
            assert text in chunks


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
    assert response.status_code == expected_status
    assert response.headers['retry-after'] == '2'
    assert not (cfg.SRC_DIR / 'busy-source.txt').exists()


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
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        material = response.json()['material']
        assert material['chunks_indexed'] > 0
        assert material.get('vectors_ready') is False
        assert material.get('embedding_status') == 'offline_keyword_only'


def test_upload_with_no_searchable_chunks_fails_before_publishing(client, monkeypatch):
    from app import config as cfg
    from app.indexer import KnowledgeIndexer
    monkeypatch.setattr(KnowledgeIndexer, '_prepare_chunk_rows', lambda *args, **kwargs: [])
    response = _persist_upload(client, 'no-searchable-chunks.txt', 'Readable source material must not be advertised as ready when no searchable chunk has been prepared.')
    assert response.status_code == 503
    assert not (cfg.SRC_DIR / 'no-searchable-chunks.txt').exists()
    assert not study_material_manager.materials_cache


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
    assert response.status_code == 503
    assert not calls


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
        assert preparing_first.wait(timeout=3)
        succeeded = pool.submit(ingest, False)
        first_result, second_result = failed.result(timeout=5), succeeded.result(timeout=5)
    assert isinstance(first_result, RuntimeError)
    assert (cfg.SRC_DIR / 'concurrent-failure.txt').read_text() == text
    with sqlite3.connect(cfg.DB_PATH) as db:
        assert db.execute('SELECT text FROM chunks WHERE doc_id=?', (second_result['doc_id'],)).fetchone()[0] == text


@pytest.mark.parametrize('generate_study_set', [False, True])
def test_successful_session_only_upload_keeps_original_download(client, generate_study_set):
    from app import config as cfg
    raw = b'Session-only source MEMORY-418 must retain its original download after successful extraction without durable indexing.'
    response = client.post('/api/materials/upload', json={
        'filename': 'session-only-source.txt',
        'file_base64': base64.b64encode(raw).decode(),
        'persist_to_src': False,
        'generate_study_set': generate_study_set,
    })
    assert response.status_code == 200
    assert not (cfg.SRC_DIR / 'session-only-source.txt').exists()
    download = client.get('/api/materials/download', params={'material_id': response.json()['material']['id']})
    assert download.status_code == 200
    assert download.content == raw


@pytest.mark.parametrize("correct,distractors", [
    ("R>0", ["R<0", "R=0", "R≠0"]),
    ("R>0", ["R<0", "R=0", "R>=0"]),
    ("R>0", ["U>0", "I>0", "V>0"]),
    ("P(B)>0", ["P(B)<0", "P(B)=0", "P(B)!=0"]),
])
def test_source_cloze_rejects_overlapping_zero_conditions(correct, distractors):
    from app.study_materials import _zero_condition_distractors_are_disjoint
    assert not _zero_condition_distractors_are_disjoint(correct, distractors)


@pytest.mark.parametrize("correct,distractors", [
    ("R>0", ["R<0", "R=0", "R<=0"]),
    ("R=0", ["R<0", "R>0", "R!=0"]),
    ("P(B)>0", ["P(B)<0", "P(B)=0", "P(B)<=0"]),
    ("one bit", ["zero bits", "two bits", "three bits"]),
])
def test_source_cloze_keeps_nonoverlapping_zero_conditions_and_text(correct, distractors):
    from app.study_materials import _zero_condition_distractors_are_disjoint
    assert _zero_condition_distractors_are_disjoint(correct, distractors)



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
    result = StudyMaterialManager(Client()).generate_materials_from_pages(source, model="test-ollama", quiz_count=1)
    assert result["generation"]["quiz"] == "extractive_fallback"



@pytest.mark.parametrize("answer,distractors", [
    ("one bit", ["zero entropy", "two outcomes", "non-negative"]),
    ("bit", ["unit", "byte", "dimension"]),
    ("bit", ["measurement", "byte", "quantity"]),
])
def test_information_unit_quiz_distractors_must_be_concrete_unit_values(answer, distractors):
    from app.study_materials import _information_unit_distractors_match
    assert not _information_unit_distractors_match(answer, distractors)


def test_information_unit_quiz_allows_concrete_alternatives():
    from app.study_materials import _information_unit_distractors_match
    assert _information_unit_distractors_match("one bit", ["zero bits", "two bits", "three bits"])


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
        'filename': 'native-entropy.txt',
        'file_base64': base64.b64encode(source.encode()).decode(),
    })
    assert response.status_code == 200, response.text
    assert legacy_calls == []
    assert native_calls
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute('''SELECT c.text,c.embedding,p.text_sha256,p.vector_sha256,p.model_digest,p.dimensions,p.full_text
            FROM chunks c JOIN documents d ON d.id=c.doc_id
            LEFT JOIN corpus_vector_provenance p ON p.chunk_id=c.id WHERE d.filename=?''', ('native-entropy.txt',)).fetchall()
    assert rows
    for text, blob, text_sha, vector_sha, identity, dimensions, full_text in rows:
        assert text_sha == hashlib.sha256(text.encode()).hexdigest()
        assert vector_sha == hashlib.sha256(blob).hexdigest()
        assert identity == BGE_NATIVE_IDENTITY and dimensions == 1024 and full_text == 1


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
    assert response.status_code == expected_status
    assert response.headers['retry-after'] == '2'
    assert not (cfg.SRC_DIR / 'native-busy-source.txt').exists()
    assert calls == []
