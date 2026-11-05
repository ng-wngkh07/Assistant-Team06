import hashlib
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from contextlib import closing
from pathlib import Path
from typing import Dict, Optional
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from fastapi.testclient import TestClient

from app import server
from app.config import DATA_DIR
from app.document_lookup import DocumentLookup
from app.slide_preview import (
    CACHE_DIR,
    convert_pptx_to_pdf_cached,
    find_soffice_executable,
    get_renderer_identity,
    is_soffice_available,
    preflight_pptx_for_preview,
    render_office_page_png,
    write_hardened_profile,
)


def create_valid_synthetic_pptx(
    target_path: Path,
    slide_count: int = 1,
    title_prefix: str = "Slide",
    custom_rels: Optional[Dict[str, str]] = None,
    extra_entries: Optional[Dict[str, bytes]] = None,
    malform_xml: Optional[str] = None,
) -> Path:
    """Create a portable, valid synthetic OOXML PPTX file for testing."""
    target_path.parent.mkdir(parents=True, exist_ok=True)

    content_types_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '  <Default Extension="xml" ContentType="application/xml"/>',
        '  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
    ]
    for i in range(1, slide_count + 1):
        content_types_lines.append(
            f'  <Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
    content_types_lines.append("</Types>")
    content_types_xml = "\n".join(content_types_lines)

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>\n'
        "</Relationships>"
    )

    pres_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">',
        "  <p:sldIdLst>",
    ]
    for i in range(1, slide_count + 1):
        pres_lines.append(f'    <p:sldId id="{255 + i}" r:id="rId{i}"/>')
    pres_lines.extend(["  </p:sldIdLst>",
                       '  <p:sldSz cx="9144000" cy="6858000"/>',
                       '  <p:notesSz cx="6858000" cy="9144000"/>',
                       "</p:presentation>"])
    pres_xml = "\n".join(pres_lines)

    pres_rels_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for i in range(1, slide_count + 1):
        pres_rels_lines.append(
            f'  <Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        )
    pres_rels_lines.append("</Relationships>")
    pres_rels_xml = "\n".join(pres_rels_lines)

    with ZipFile(target_path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("ppt/presentation.xml", pres_xml)
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels_xml)

        for i in range(1, slide_count + 1):
            if malform_xml and i == 1:
                slide_xml = malform_xml
            else:
                slide_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                    '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">\n'
                    "  <p:cSld><p:spTree>\n"
                    "    <p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>\n"
                    "    <p:grpSpPr/>\n"
                    "    <p:sp>\n"
                    "      <p:nvSpPr><p:cNvPr id=\"2\" name=\"Title\"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>\n"
                    "      <p:spPr/>\n"
                    "      <p:txBody><a:bodyPr/><a:p><a:r><a:t>"
                    f"{title_prefix} {i}: Nội dung bài giảng kiểm thử"
                    "</a:t></a:r></a:p></p:txBody>\n"
                    "    </p:sp>\n"
                    "  </p:spTree></p:cSld>\n"
                    "</p:sld>"
                )
            zf.writestr(f"ppt/slides/slide{i}.xml", slide_xml)

            # Slide relationships
            slide_rels_lines = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            ]
            if custom_rels and i == 1:
                for rid, (target, target_mode) in custom_rels.items():
                    mode_attr = f' TargetMode="{target_mode}"' if target_mode else ""
                    slide_rels_lines.append(
                        f'  <Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"{mode_attr}/>'
                    )
            slide_rels_lines.append("</Relationships>")
            zf.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", "\n".join(slide_rels_lines))

        if extra_entries:
            for entry_name, entry_data in extra_entries.items():
                zf.writestr(entry_name, entry_data)

    return target_path


@pytest.fixture(autouse=True)
def isolate_preview_cache(tmp_path, monkeypatch):
    """Ensure every test uses a strictly isolated preview cache and never touches DATA_DIR."""
    isolated = tmp_path / "isolated_office_cache"
    isolated.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.slide_preview.CACHE_DIR", isolated)
    return isolated


@pytest.fixture
def office_lookup(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    db_path = tmp_path / "knowledge.db"

    # 1. Valid PDF file
    (src / "sample.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF"
    )

    # 2. Valid synthetic PPTX file (10 slides)
    create_valid_synthetic_pptx(src / "lecture.pptx", slide_count=10, title_prefix="Lecture Slide")

    # 3. DOCX file
    (src / "syllabus.docx").write_bytes(b"PK\x03\x04fake_docx_binary_content_for_testing")

    # 4. Text file
    (src / "notes.txt").write_text("ghi chu hoc tap", encoding="utf-8")

    # 5. Pending PPTX (valid synthetic PPTX with 2 slides)
    create_valid_synthetic_pptx(src / "pending.pptx", slide_count=2, title_prefix="Pending Slide")

    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY, filename TEXT, clean_title TEXT,
              total_pages INTEGER, status TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, book_title TEXT,
              filename TEXT, page_num INTEGER, chunk_index INTEGER, text TEXT, embedding BLOB);
            CREATE VIRTUAL TABLE chunks_fts USING fts5(text);
        """)
        docs = [
            (1, "sample.pdf", "Sample PDF", 1, "indexed"),
            (2, "lecture.pptx", "Lecture PPTX", 10, "indexed"),
            (3, "syllabus.docx", "Syllabus DOCX", 5, "indexed"),
            (4, "notes.txt", "Notes Text", 1, "indexed"),
            (5, "pending.pptx", "Pending PPTX", 2, "pending"),
        ]
        for did, name, title, pages, status in docs:
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?)", (did, name, title, pages, status))

        # Chunks for searching
        chunks = [
            (1, 1, "sample.pdf", "sample.pdf", 1, 0, "nội dung trang PDF mẫu"),
            (2, 2, "lecture.pptx", "lecture.pptx", 3, 0, "nội dung slide 3 bài giảng PPTX"),
            (3, 3, "syllabus.docx", "syllabus.docx", 2, 0, "nội dung phần 2 đề cương DOCX"),
            (4, 4, "notes.txt", "notes.txt", 1, 0, "nội dung ghi chú"),
        ]
        for cid, did, title, name, page, idx, text in chunks:
            db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,NULL)", (cid, did, title, name, page, idx, text))
            db.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cid, text))

    return DocumentLookup(db_path, src)


@pytest.fixture
def office_client(office_lookup, monkeypatch):
    monkeypatch.setattr(server, "document_lookup", office_lookup)

    class NoHistory:
        def __getattr__(self, name):
            raise AssertionError("Document lookup touched conversation history")

    monkeypatch.setattr(server, "history_store", NoHistory())
    return TestClient(server.app)


def test_office_documents_never_expose_fake_pdf_urls(office_client, office_lookup):
    """Office documents must NOT link to /pdf; only PDF documents provide pdf_url."""
    # 1. Search endpoint
    res = office_client.get("/api/documents/search", params={"q": "bài giảng"})
    assert res.status_code == 200
    results = res.json()["results"]
    assert len(results) == 1
    pptx_item = results[0]
    assert pptx_item["doc_id"] == 2
    assert pptx_item["source_kind"] == "pptx"
    assert pptx_item["pdf_url"] is None
    assert pptx_item["original_url"] == "/api/documents/2/original"
    assert "Slide" in pptx_item["page_label"]

    res_docx = office_client.get("/api/documents/search", params={"q": "đề cương"})
    assert res_docx.status_code == 200
    docx_item = res_docx.json()["results"][0]
    assert docx_item["doc_id"] == 3
    assert docx_item["source_kind"] == "docx"
    assert docx_item["pdf_url"] is None
    assert docx_item["original_url"] == "/api/documents/3/original"
    assert docx_item["page_image_url"] is None
    assert "Phần" in docx_item["page_label"]

    # 2. Direct /pdf endpoint must 404 for Office files
    assert office_client.get("/api/documents/2/pdf").status_code == 404
    assert office_client.get("/api/documents/3/pdf").status_code == 404

    # 3. PDF document maintains pdf_url
    res_pdf = office_client.get("/api/documents/search", params={"q": "mẫu"})
    assert res_pdf.status_code == 200
    pdf_item = res_pdf.json()["results"][0]
    assert pdf_item["source_kind"] == "pdf"
    assert pdf_item["pdf_url"] == "/api/documents/1/pdf#page=1"
    assert pdf_item["original_url"] == "/api/documents/1/original"


def test_source_preview_metadata_identifies_office_semantics(office_client):
    """Source preview endpoint returns rich metadata: kind, label, original_url, no /pdf for Office."""
    # PPTX
    res_pptx = office_client.get("/api/documents/source-preview", params={"filename": "lecture.pptx", "page_num": 3})
    assert res_pptx.status_code == 200
    data_pptx = res_pptx.json()
    assert data_pptx["doc_id"] == 2
    assert data_pptx["source_kind"] == "pptx"
    assert data_pptx["page_label"] == "Slide 3"
    assert data_pptx["pdf_url"] is None
    assert data_pptx["original_url"] == "/api/documents/2/original"

    # DOCX: logical reading section, no forged page image
    res_docx = office_client.get("/api/documents/source-preview", params={"filename": "syllabus.docx", "page_num": 2})
    assert res_docx.status_code == 200
    data_docx = res_docx.json()
    assert data_docx["doc_id"] == 3
    assert data_docx["source_kind"] == "docx"
    assert data_docx["page_label"] == "Phần 2"
    assert data_docx["pdf_url"] is None
    assert data_docx["page_image_url"] is None
    assert data_docx["preview_available"] is False
    assert data_docx["original_url"] == "/api/documents/3/original"

    # PDF: physical page
    res_pdf = office_client.get("/api/documents/source-preview", params={"filename": "sample.pdf", "page_num": 1})
    assert res_pdf.status_code == 200
    data_pdf = res_pdf.json()
    assert data_pdf["doc_id"] == 1
    assert data_pdf["source_kind"] == "pdf"
    assert data_pdf["pdf_url"] == "/api/documents/1/pdf#page=1"
    assert data_pdf["page_image_url"] == "/api/documents/1/pages/1/image"
    assert data_pdf["preview_available"] is True


def test_generic_original_download_endpoint(office_client, office_lookup, tmp_path):
    """Endpoint /api/documents/{id}/original safely downloads original source file with correct MIME."""
    # 1. Download PDF
    res = office_client.get("/api/documents/1/original")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="sample.pdf"' in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF")

    # 2. Download PPTX
    res = office_client.get("/api/documents/2/original")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert 'filename="lecture.pptx"' in res.headers["content-disposition"]
    assert res.content.startswith(b"PK\x03\x04")

    # 3. Download DOCX
    res = office_client.get("/api/documents/3/original")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert 'filename="syllabus.docx"' in res.headers["content-disposition"]

    # 4. Download TXT
    res = office_client.get("/api/documents/4/original")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]

    # 5. Pending document must 404
    assert office_client.get("/api/documents/5/original").status_code == 404

    # 6. Nonexistent id must 404
    assert office_client.get("/api/documents/999/original").status_code == 404

    # 7. Path traversal and unauthorized paths fail closed
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 secret")
    with sqlite3.connect(office_lookup.db_path) as db:
        db.execute("INSERT INTO documents VALUES (10, '../secret.pdf', 'Secret', 1, 'indexed')")
        db.execute("INSERT INTO documents VALUES (11, 'unsupported.bin', 'Binary', 1, 'indexed')")
    (office_lookup.src_dir / "unsupported.bin").write_bytes(b"\x00\x01\x02")

    assert office_client.get("/api/documents/10/original").status_code == 404
    assert office_client.get("/api/documents/11/original").status_code == 404


def test_absent_renderer_returns_none_instead_of_synthetic_html(tmp_path, monkeypatch):
    """When LibreOffice is unavailable, render_office_page_png returns None without synthesizing fake HTML text PNG."""
    from app import slide_preview

    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: None)
    pptx_path = create_valid_synthetic_pptx(tmp_path / "deck.pptx", slide_count=1)

    assert slide_preview.is_soffice_available() is False
    result = slide_preview.render_office_page_png(pptx_path, page_num=1)
    assert result is None


def test_docx_never_synthesizes_fake_page_image(tmp_path):
    """DOCX files use logical sections and must never generate fake page images."""
    fake_docx = tmp_path / "doc.docx"
    fake_docx.write_bytes(b"PK\x03\x04fake_docx")

    assert render_office_page_png(fake_docx, page_num=1) is None


def test_renderer_timeout_and_failure_handled_gracefully(tmp_path, monkeypatch):
    """When conversion command fails or times out, helper returns None without unhandled crash."""
    from app import slide_preview

    dummy_soffice = tmp_path / "soffice"
    dummy_soffice.write_text("#!/bin/sh\nexit 1\n")
    dummy_soffice.chmod(0o755)

    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: dummy_soffice)
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-dummy")

    pptx_path = create_valid_synthetic_pptx(tmp_path / "deck.pptx", slide_count=1)

    # Non-zero exit code
    assert slide_preview.render_office_page_png(pptx_path, page_num=1) is None

    # Timeout
    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=10)

    monkeypatch.setattr(subprocess, "run", timeout_run)
    assert slide_preview.render_office_page_png(pptx_path, page_num=1) is None


def test_preview_cache_keying_and_invalidation(tmp_path, monkeypatch):
    """Cache is keyed by source hash and renderer identity; modifying file invalidates cache."""
    from app import slide_preview

    converted_pdf_bytes = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 100 100]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF"
    )

    pptx_file = create_valid_synthetic_pptx(tmp_path / "test.pptx", slide_count=1, title_prefix="Initial")

    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-v1")

    call_count = 0

    def fake_convert_command(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "test.pdf").write_bytes(converted_pdf_bytes)
        return subprocess.CompletedProcess(cmd, 0, stdout="converted", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_convert_command)

    # First call: converts and caches
    img1 = slide_preview.render_office_page_png(pptx_file, page_num=1)
    assert img1 is not None and img1.startswith(b"\x89PNG")
    assert call_count == 1

    # Second call: uses cache, does not call conversion again
    img2 = slide_preview.render_office_page_png(pptx_file, page_num=1)
    assert img2 == img1
    assert call_count == 1

    # Modify PPTX content: cache must invalidate and reconvert
    create_valid_synthetic_pptx(pptx_file, slide_count=1, title_prefix="Modified v2")
    img3 = slide_preview.render_office_page_png(pptx_file, page_num=1)
    assert img3 is not None
    assert call_count == 2

    # Renderer identity change: cache must invalidate and reconvert
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-v2-upgraded")
    img4 = slide_preview.render_office_page_png(pptx_file, page_num=1)
    assert img4 is not None
    assert call_count == 3


def test_concurrent_render_safe(tmp_path, monkeypatch):
    """Concurrent rendering of the same document handles cache generation without race or corruption."""
    from app import slide_preview

    converted_pdf_bytes = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 100 100]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF"
    )

    pptx_file = create_valid_synthetic_pptx(tmp_path / "concurrent.pptx", slide_count=1)

    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-v1")

    def fake_convert(cmd, **kwargs):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "concurrent.pdf").write_bytes(converted_pdf_bytes)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_convert)

    results = []
    errors = []

    def worker():
        try:
            res = slide_preview.render_office_page_png(pptx_file, page_num=1)
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 8
    assert all(r is not None and r.startswith(b"\x89PNG") for r in results)
    assert all(r == results[0] for r in results)


def test_user_installation_does_not_mutate_home(tmp_path, monkeypatch):
    """Conversion passes -env:UserInstallation and never overrides the global os.environ['HOME']."""
    from app import slide_preview

    original_home = os.environ.get("HOME")
    sample_pdf_bytes = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 100 100]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF"
    )
    pptx_file = create_valid_synthetic_pptx(tmp_path / "env_check.pptx", slide_count=1)

    executed_cmd = []

    def record_run(cmd, **kwargs):
        executed_cmd.append(list(cmd))
        if "--outdir" in cmd:
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            (outdir / "env_check.pdf").write_bytes(sample_pdf_bytes)
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="soffice 1.0", stderr="")

    monkeypatch.setattr(subprocess, "run", record_run)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-v1")

    res = slide_preview.render_office_page_png(pptx_file, page_num=1)
    assert res is not None
    assert os.environ.get("HOME") == original_home
    assert any(any(arg.startswith("-env:UserInstallation=file://") for arg in cmd) for cmd in executed_cmd)


# ---------------------------------------------------------------------------
# Requirement 1: OOXML Security Preflight Tests (External rels, active payloads, DTD/entities)
# ---------------------------------------------------------------------------


def test_preflight_rejects_external_relationships_without_conversion(tmp_path, monkeypatch):
    """External relationship targets (e.g. https://example.invalid/private.png) fail preflight; subprocess is NEVER called."""
    from app import slide_preview

    # Synthetic PPTX targeting external URL
    pptx_path = create_valid_synthetic_pptx(
        tmp_path / "external_rel.pptx",
        slide_count=1,
        custom_rels={"rId99": ("https://example.invalid/private.png", "External")},
    )
    original_bytes = pptx_path.read_bytes()

    # Preflight check fails closed
    is_safe, reason = preflight_pptx_for_preview(pptx_path)
    assert is_safe is False
    assert "external" in reason.lower()

    # Intercept subprocess.run: MUST NOT BE CALLED
    def fail_if_called(*args, **kwargs):
        raise AssertionError("Subprocess conversion was invoked for externally-referencing PPTX!")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))

    # Conversion returns None safely
    assert slide_preview.convert_pptx_to_pdf_cached(pptx_path) is None
    assert slide_preview.render_office_page_png(pptx_path, page_num=1) is None

    # Original file is preserved untouched
    assert pptx_path.read_bytes() == original_bytes


def test_preflight_rejects_active_payloads_without_conversion(tmp_path, monkeypatch):
    """Active payloads (VBA macro, embedded OLE, ActiveX) fail preflight; conversion is never called."""
    from app import slide_preview

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Subprocess conversion was invoked for active payload PPTX!")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))

    # 1. VBA project binary
    vba_pptx = create_valid_synthetic_pptx(
        tmp_path / "vba_deck.pptx",
        extra_entries={"ppt/vbaProject.bin": b"fake_vba_bytecode"},
    )
    is_safe, reason = preflight_pptx_for_preview(vba_pptx)
    assert is_safe is False
    assert "active payload" in reason.lower()
    assert slide_preview.convert_pptx_to_pdf_cached(vba_pptx) is None

    # 2. Embedded OLE object
    ole_pptx = create_valid_synthetic_pptx(
        tmp_path / "ole_deck.pptx",
        extra_entries={"ppt/embeddings/oleObject1.bin": b"fake_ole_data"},
    )
    is_safe, reason = preflight_pptx_for_preview(ole_pptx)
    assert is_safe is False
    assert slide_preview.convert_pptx_to_pdf_cached(ole_pptx) is None

    # 3. ActiveX control
    activex_pptx = create_valid_synthetic_pptx(
        tmp_path / "activex_deck.pptx",
        extra_entries={"ppt/activeX/activeX1.xml": b"<xml/>"},
    )
    is_safe, reason = preflight_pptx_for_preview(activex_pptx)
    assert is_safe is False
    assert slide_preview.convert_pptx_to_pdf_cached(activex_pptx) is None


def test_preflight_rejects_dtd_and_entities_without_conversion(tmp_path, monkeypatch):
    """Disallowed DTD / entity XML triggers defusedxml rejection; conversion is never called."""
    from app import slide_preview

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Subprocess conversion was invoked for DTD/entity PPTX!")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))

    # DTD injection in slide XML
    dtd_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE p:sld [ <!ENTITY xxe "evil_payload"> ]>\n'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>&xxe;</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>'
        '</p:sld>'
    )
    dtd_pptx = create_valid_synthetic_pptx(tmp_path / "dtd_deck.pptx", malform_xml=dtd_xml)
    is_safe, reason = preflight_pptx_for_preview(dtd_pptx)
    assert is_safe is False
    assert "disallowed or malformed xml" in reason.lower()
    assert slide_preview.convert_pptx_to_pdf_cached(dtd_pptx) is None


def test_preflight_rejects_malformed_xml_without_conversion(tmp_path, monkeypatch):
    """Malformed XML syntax fails preflight closed without reaching conversion."""
    from app import slide_preview

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Subprocess conversion was invoked for malformed XML PPTX!")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))

    malformed_pptx = create_valid_synthetic_pptx(tmp_path / "broken.pptx", malform_xml="<broken><unclosed_tag>")
    is_safe, reason = preflight_pptx_for_preview(malformed_pptx)
    assert is_safe is False
    assert slide_preview.convert_pptx_to_pdf_cached(malformed_pptx) is None


def test_preflight_rejects_unsafe_zip_inputs(tmp_path):
    """Unsafe zip inputs (Zip Slip, not a zip, empty) fail closed."""
    # 1. Non-zip file
    non_zip = tmp_path / "fake.pptx"
    non_zip.write_bytes(b"Not a zip at all")
    is_safe, reason = preflight_pptx_for_preview(non_zip)
    assert is_safe is False

    # 2. Empty file
    empty = tmp_path / "empty.pptx"
    empty.write_bytes(b"")
    is_safe, reason = preflight_pptx_for_preview(empty)
    assert is_safe is False

    # 3. Path traversal in zip entry (Zip Slip)
    slip_pptx = tmp_path / "slip.pptx"
    with ZipFile(slip_pptx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/presentation.xml", "<p:presentation/>")
        zf.writestr("../../etc/passwd", "evil")
    is_safe, reason = preflight_pptx_for_preview(slip_pptx)
    assert is_safe is False
    assert "path traversal" in reason.lower()


# ---------------------------------------------------------------------------
# Requirement 1: Hardened Profile Configuration Tests
# ---------------------------------------------------------------------------


def test_hardened_profile_configuration(tmp_path, monkeypatch):
    """Use recognized profile settings, not unrelated UNO load properties."""
    from app import slide_preview

    user_dir = tmp_path / "user"
    write_hardened_profile(user_dir)

    xcu_path = user_dir / "registrymodifications.xcu"
    assert xcu_path.is_file()
    xcu_text = xcu_path.read_text(encoding="utf-8")

    # Macro execution disabled
    assert 'oor:name="DisableMacrosExecution"' in xcu_text
    assert "<value>true</value>" in xcu_text

    assert 'oor:name="MacroExecutionMode"' not in xcu_text
    assert 'oor:name="UpdateDocMode"' not in xcu_text

    # Security level = 3 (Very High)
    assert 'oor:name="MacroSecurityLevel"' in xcu_text
    assert '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>' in xcu_text

    # Check that conversion command does NOT pass unsupported CLI flags
    recorded_cmd = []

    def mock_run(cmd, **kwargs):
        recorded_cmd.append(list(cmd))
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "valid.pdf").write_bytes(b"%PDF-1.4 minimal")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-v1")

    valid_pptx = create_valid_synthetic_pptx(tmp_path / "valid.pptx", slide_count=1)
    slide_preview.convert_pptx_to_pdf_cached(valid_pptx)

    assert len(recorded_cmd) == 1
    cmd_args = recorded_cmd[0]
    # Verify no bogus unsupported flags
    for arg in cmd_args:
        assert not arg.startswith("--macro-execution-mode")
        assert not arg.startswith("--update-doc-mode")


# ---------------------------------------------------------------------------
# Requirement 2: UserInstallation URI with Path.as_uri() on Spaces & Unicode
# ---------------------------------------------------------------------------


def test_user_installation_uri_with_spaces_and_unicode(tmp_path, monkeypatch):
    """UserInstallation URI uses Path.as_uri() to handle spaces and Unicode cleanly without manual file:// prefix."""
    from app import slide_preview

    # Temporary directory with spaces and Vietnamese Unicode characters
    unicode_dir = tmp_path / "thư mục kiểm thử với khoảng trắng"
    unicode_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(unicode_dir))

    pptx_path = create_valid_synthetic_pptx(unicode_dir / "bài giảng.pptx", slide_count=1)

    executed_cmd = []

    def mock_run(cmd, **kwargs):
        executed_cmd.append(list(cmd))
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "bài giảng.pdf").write_bytes(b"%PDF-1.4 minimal")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(slide_preview, "find_soffice_executable", lambda: Path("/bin/true"))
    monkeypatch.setattr(slide_preview, "get_renderer_identity", lambda p: "soffice-v1")

    slide_preview.convert_pptx_to_pdf_cached(pptx_path)

    assert len(executed_cmd) == 1
    cmd = executed_cmd[0]
    user_install_arg = next(arg for arg in cmd if arg.startswith("-env:UserInstallation="))
    uri_val = user_install_arg.split("=", 1)[1]

    # Must be valid RFC 8089 file URI starting with file:///
    assert uri_val.startswith("file:///")
    # Must NOT contain raw unencoded spaces
    assert " " not in uri_val
    # Spaces must be encoded as %20
    assert "%20" in uri_val
    # Vietnamese Unicode characters must be percent-encoded
    assert "%" in uri_val


# ---------------------------------------------------------------------------
# Requirement 3: Portable Real-Renderer Optional Check (No Private Dependencies)
# ---------------------------------------------------------------------------


def test_real_libreoffice_acceptance_if_available(tmp_path):
    """Portable optional real acceptance check with actual LibreOffice if discoverable in environment."""
    from app import slide_preview

    soffice = slide_preview.find_soffice_executable()
    if not soffice:
        # Check explicit environment override for local testing
        override = os.getenv("SOFFICE_PATH") or os.getenv("TEST_REAL_SOFFICE_PATH")
        if override and Path(override).is_file():
            soffice = Path(override)

    if not soffice:
        pytest.skip("No real LibreOffice binary found in environment for live acceptance")

    # Optional local source file override via env var, or portable synthetic PPTX fixture
    real_env_source = os.getenv("TEST_REAL_PPTX_PATH")
    if real_env_source and Path(real_env_source).is_file():
        lecture_path = Path(real_env_source)
    else:
        lecture_path = create_valid_synthetic_pptx(tmp_path / "portable_lecture.pptx", slide_count=2)

    # 1. Slide 1 rendering
    png_1 = slide_preview.render_office_page_png(lecture_path, page_num=1)
    assert png_1 is not None, "Installed LibreOffice must render the portable PPTX fixture"
    assert png_1.startswith(b"\x89PNG\r\n\x1a\n")

    # 2. Slide 2 rendering
    png_2 = slide_preview.render_office_page_png(lecture_path, page_num=2)
    assert png_2 is not None
    assert png_2.startswith(b"\x89PNG\r\n\x1a\n")

    # 3. Slide 3 out of bounds -> None
    assert slide_preview.render_office_page_png(lecture_path, page_num=3) is None

    # 4. Slide 0 / negative -> None
    assert slide_preview.render_office_page_png(lecture_path, page_num=0) is None
    assert slide_preview.render_office_page_png(lecture_path, page_num=-1) is None


def _static_pdf(path, pages):
    import pymupdf
    with pymupdf.open() as document:
        for index in range(pages):
            page = document.new_page(width=720, height=540)
            page.insert_text((72, 72), f"Original static slide {index + 1}")
        document.save(path)
    return path


def test_precomputed_preview_preserves_ole_source_without_converter(tmp_path, monkeypatch):
    from app import slide_preview as previews
    source = create_valid_synthetic_pptx(tmp_path / "embedded.pptx", slide_count=2,
        extra_entries={"ppt/embeddings/oleObject1.bin": b"opaque embedded object"})
    original = source.read_bytes()
    pdf = _static_pdf(tmp_path / "original.pdf", 2)
    assert not previews.preflight_pptx_for_preview(source)[0]
    monkeypatch.setattr(previews, "find_soffice_executable", lambda: None)
    def no_subprocess(*args, **kwargs):
        pytest.fail("Static preview must not execute Office")
    monkeypatch.setattr(previews.subprocess, "run", no_subprocess)
    cached = previews.register_precomputed_pptx_preview(source, pdf, renderer_id="Synthetic fixture renderer")
    assert previews.convert_pptx_to_pdf_cached(source) == cached
    assert previews.is_pptx_preview_available(source)
    assert previews.render_office_page_png(source, 2).startswith(b"\x89PNG")
    assert previews.render_office_page_png(source, 3) is None
    assert source.read_bytes() == original
    source.write_bytes(original + b"changed")
    assert previews.precomputed_pptx_preview(source) is None


@pytest.mark.parametrize("mutation", ["digest", "path", "pages", "format", "json", "pdf", "symlink"])
def test_precomputed_preview_rejects_tampered_assets(tmp_path, monkeypatch, mutation):
    import json
    from app import slide_preview as previews
    source = create_valid_synthetic_pptx(tmp_path / "source.pptx")
    cached = previews.register_precomputed_pptx_preview(source, _static_pdf(tmp_path / "original.pdf", 1), renderer_id="Test renderer")
    receipt = previews.CACHE_DIR / (hashlib.sha256(source.read_bytes()).hexdigest() + ".json")
    record = json.loads(receipt.read_text())
    if mutation == "digest": record["pdf_sha256"] = "0" * 64
    elif mutation == "path": record["pdf_sha256"] = "../outside.pdf"
    elif mutation == "pages": record["page_count"] = 2
    elif mutation == "format": record["format"] = "unknown"
    elif mutation == "json": receipt.write_text("{invalid")
    elif mutation == "pdf": cached.write_bytes(b"invalid PDF")
    elif mutation == "symlink":
        outside = tmp_path / "outside.pdf"
        outside.write_bytes(cached.read_bytes())
        cached.unlink()
        cached.symlink_to(outside)
    if mutation not in {"json", "pdf", "symlink"}:
        receipt.write_text(json.dumps(record))
    assert previews.precomputed_pptx_preview(source) is None


@pytest.mark.parametrize("bad_pdf", ["wrong_pages", "encrypted", "invalid"])
def test_static_import_rejects_invalid_pdf(tmp_path, bad_pdf):
    import pymupdf
    from app import slide_preview as previews
    source = create_valid_synthetic_pptx(tmp_path / "source.pptx")
    pdf = tmp_path / "input.pdf"
    if bad_pdf == "invalid": pdf.write_bytes(b"not a PDF")
    elif bad_pdf == "wrong_pages": _static_pdf(pdf, 2)
    else:
        with pymupdf.open() as document:
            document.new_page()
            document.save(pdf, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader")
    with pytest.raises(Exception):
        previews.register_precomputed_pptx_preview(source, pdf, renderer_id="Test renderer")
    assert not list(previews.CACHE_DIR.iterdir())
