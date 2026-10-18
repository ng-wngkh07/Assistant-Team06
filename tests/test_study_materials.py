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
def isolated_study_sources(tmp_path, monkeypatch):
    """No live corpus/network/model may be touched by this software test module."""
    import app.config as cfg
    import app.server as server
    import app.study_materials as sm
    from app.indexer import KnowledgeIndexer
    from app.document_lookup import DocumentLookup
    from app.ollama_client import OllamaClient
    from app.trained_client import TrainedModelClient
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'synthetic.txt').write_text('Entropy là độ bất định trung bình của một biến ngẫu nhiên rời rạc hữu hạn.', encoding='utf-8')
    db = tmp_path / 'synthetic.db'
    monkeypatch.setattr(cfg, 'DB_PATH', db)
    monkeypatch.setattr(cfg, 'SRC_DIR', src)
    monkeypatch.setattr(sm, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(OllamaClient, 'check_health', lambda self: False)
    monkeypatch.setattr(TrainedModelClient, 'available', classmethod(lambda cls: False))
    indexer = KnowledgeIndexer(db_path=db, src_dir=src)
    indexer.index_all(ocr=False, embed_model=None)
    monkeypatch.setattr(server, 'document_lookup', DocumentLookup(db_path=db, src_dir=src))
    # materials_cache is a snapshot property; replace the actual session store.
    monkeypatch.setattr(study_material_manager, "_materials", {})
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

    # 1. Attachment preview tray inside chat-form & Sample 1 card styles
    assert 'id="chat-attachment-preview-tray"' in html
    assert ".chat-attachment-card" in css
    assert ".attachment-doc-sheet" in css
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
