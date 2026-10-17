"""
Test suite for Study Materials (Upload, Quiz & Flashcards generation).
Covers validation, extraction, grounding, fallback generation, and API endpoints.
"""

import base64
import pytest
from fastapi.testclient import TestClient

from app.server import app
from app.study_materials import (
    StudyMaterialManager,
    StudyMaterialError,
    study_material_manager
)


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

    # 2. Pure JPEG diagram image without metadata (exercises synthesized visual study fallback)
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

