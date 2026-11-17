import pytest
import sqlite3
from pathlib import Path
from app.config import DB_PATH, SRC_DIR
from app.searcher import HybridSearcher
from app.indexer import KnowledgeIndexer

def test_database_is_populated():
    indexer = KnowledgeIndexer()
    status = indexer.get_status()
    assert status["total_documents"] >= 33
    assert status["total_indexed_pages"] > 5000
    assert status["total_chunks"] > 10000

def test_fts5_search():
    searcher = HybridSearcher()
    # Search for a well-known concept in psychology
    results = searcher.search_fts("Hệ thống 1 và Hệ thống 2", limit=5)
    assert len(results) > 0
    first = results[0]
    assert "text" in first
    assert "page_num" in first
    assert "book_title" in first
    assert first["page_num"] > 0


def test_numbered_concepts_stay_together_in_retrieval():
    searcher = HybridSearcher()
    question = "Hệ thống 1 và Hệ thống 2 khác nhau thế nào?"
    results = searcher.search_hybrid(question, top_k=8, embed_model=None)
    assert any("tu-duy-nhanh-va-cham" in item["filename"] for item in results[:3])
    assert any("Hệ thống 1" in item["text"] and "Hệ thống 2" in item["text"] for item in results)

def test_hybrid_search_results_structure():
    searcher = HybridSearcher()
    results = searcher.search_hybrid("sang chấn tâm lý ảnh hưởng cơ thể", top_k=4, embed_model=None)
    assert len(results) > 0
    assert len(results) <= 4
    for r in results:
        assert "citation_label" in r
        assert "Trang" in r["citation_label"]
        assert "safe_text" in r


def test_duplicate_pdf_aliases_do_not_displace_distinct_evidence(tmp_path, monkeypatch):
    searcher = HybridSearcher(tmp_path / "unused.db")
    rows = [{"chunk_id": i, "filename": name, "book_title": name,
             "page_num": 1, "chunk_index": 0, "text": text, "fts_rank": i}
            for i, name, text in [
                (1, "book.pdf", "Trí nhớ lưu giữ thông tin trong cuộc sống."),
                (2, "book (1).pdf", "Trí nhớ  lưu giữ thông tin trong cuộc sống."),
                (3, "other.pdf", "Khả năng nhớ lại chịu ảnh hưởng của bối cảnh học tập."),
            ]]
    monkeypatch.setattr(searcher, "search_fts", lambda *_args, **_kwargs: rows)
    result = searcher.search_hybrid("trí nhớ", top_k=2, embed_model=None)
    assert [r["chunk_id"] for r in result] == [1, 3]


def test_slide_outline_prefixed_with_trang_number_is_filtered_as_toc():
    from app.lexical_search import is_heading_or_toc_chunk

    slide_toc = "Trang 2\nNội dung\nTổng quan Fractal\nIFS\nL-System\nTập Maldelbrot"
    slide_real = (
        "Trang 57\nHệ L-System\n"
        "Hệ L-System được đề xuất năm 1968 để mô hình hóa cấu trúc phân nhánh của thực vật thông qua tập luật viết lại song song."
    )
    assert is_heading_or_toc_chunk(slide_toc) is True
    assert is_heading_or_toc_chunk(slide_real) is False


def test_clean_fts_query_strips_punctuation_single_char_wildcards_and_balances_multi_identifier_slots(tmp_path, monkeypatch):
    from app.lexical_search import clean_fts_query

    q = "Trong bài giảng Đồ họa Fractal, IFS và L-System dùng để làm gì?"
    fts_q = clean_fts_query(q)
    assert '"L"*' not in fts_q
    assert '"gì?"' not in fts_q
    assert '"Fractal,"' not in fts_q
    assert '"IFS"' in fts_q

    searcher = HybridSearcher(tmp_path / "unused.db")
    rows = [
        {"chunk_id": 1, "doc_id": 155, "filename": "slides.pptx", "book_title": "Slides", "page_num": 36, "chunk_index": 0, "text": "Hệ hàm lặp IFS tạo tập hút fractal.", "fts_rank": 1, "fts_score": 47.2},
        {"chunk_id": 2, "doc_id": 155, "filename": "slides.pptx", "book_title": "Slides", "page_num": 37, "chunk_index": 0, "text": "Hệ hàm lặp IFS affine gồm các ánh xạ co.", "fts_rank": 2, "fts_score": 47.1},
        {"chunk_id": 3, "doc_id": 155, "filename": "slides.pptx", "book_title": "Slides", "page_num": 57, "chunk_index": 0, "text": "Hệ L-System gồm tiên đề và luật sinh để vẽ cấu trúc cây.", "fts_rank": 3, "fts_score": 46.9},
    ]
    monkeypatch.setattr(searcher, "search_fts", lambda *_args, **_kwargs: rows)
    result = searcher.search_hybrid(q, top_k=2, embed_model=None)
    assert [r["page_num"] for r in result] == [36, 57]
