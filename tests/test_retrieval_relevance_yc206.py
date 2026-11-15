"""Answer-bearing concepts must enter the candidate pool ahead of unrelated word matches."""
import pytest
from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher, is_heading_or_toc_chunk


def test_spaced_dot_leaders_are_a_table_of_contents():
    source='3. Đạo hàm, vi phân và ứng dụng\n' + '. '*45 + '10\n3.1. Định nghĩa, ý nghĩa và bảng đạo hàm\n' + '. '*45 + '10\n3.2. Đạo hàm cấp cao và công thức Leibniz\n'+'. '*45+'11\n'
    assert is_heading_or_toc_chunk(source)


@pytest.mark.parametrize('concept',['đạo hàm','mệnh đề','điều kiện hóa cổ điển'])
def test_concept_phrase_candidate_survives_generic_word_matches(tmp_path,concept):
    src=tmp_path/'src';src.mkdir()
    words=concept.split()
    unrelated=' '.join(f'{w} là gì trong một ngữ cảnh khác.' for w in words)
    for i in range(65):
        (src/f'distractor-{i}.txt').write_text((unrelated+'\n')*6)
    evidence=f'{concept.capitalize()} được định nghĩa là một khái niệm chuyên môn có điều kiện áp dụng được nêu rõ trong phần văn bản này. Đây là đoạn giải thích trực tiếp, các từ trong tên khái niệm phải đi cùng nhau.'
    (src/'evidence.txt').write_text(evidence)
    idx=KnowledgeIndexer(tmp_path/'kb.db',src);idx.index_all(embed_model=None)
    found=HybridSearcher(tmp_path/'kb.db').search_hybrid(f'{concept} là gì',embed_model=None,top_k=6)
    assert found[0]['filename']=='evidence.txt'
    assert concept in found[0]['text'].casefold()


@pytest.mark.parametrize('concept',['đạo hàm','mệnh đề','điều kiện hóa cổ điển'])
@pytest.mark.parametrize('intent',['{} là gì','lý thuyết {}'])
def test_course_catalog_does_not_outrank_local_explanation(tmp_path,concept,intent):
    src=tmp_path/'src';src.mkdir()
    # Both documents contain the concept and definition cues. The catalogue's
    # cues describe other chapters; it does not explain the requested concept.
    catalog=('Danh sách bài học và tài nguyên.\n'
             'Tuần 1: Định nghĩa và tính chất các khái niệm mở đầu.\n'+
             'Mô tả tài liệu bổ sung và lịch trình học. '*14+'\n'+
             f'Tuần 6: {concept}. Video ôn tập, bài tập và lịch học.\n'+
             'Các phần sau giới thiệu công thức và định nghĩa của chương khác.')
    evidence=(f'3. {concept.capitalize()}\n3.1. Định nghĩa và ý nghĩa\n'
              'Đây là phần giải thích trực tiếp nội dung khái niệm đang được hỏi, '
              'nêu ý nghĩa, điều kiện áp dụng và các thành phần của định nghĩa. '
              'Phần trình bày này cung cấp kiến thức, không chỉ thông báo lịch học.')
    (src/'catalog.txt').write_text(catalog)
    (src/'definition.txt').write_text(evidence)
    idx=KnowledgeIndexer(tmp_path/'kb.db',src);idx.index_all(embed_model=None)
    found=HybridSearcher(tmp_path/'kb.db').search_hybrid(intent.format(concept),embed_model=None,top_k=2)
    assert found[0]['filename']=='definition.txt'


def test_vectorized_semantic_matrix_cache_and_tamper_invalidation(tmp_path, monkeypatch):
    """Vectorized semantic search reuses validated BLAS matrix when DB is unchanged and invalidates on write."""
    import hashlib
    import sqlite3
    import numpy as np
    from app import native_embedding, searcher as searcher_mod
    from app.indexer import pack_vector
    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient

    src = tmp_path / 'src'
    src.mkdir()
    (src / 'doc_a.txt').write_text('Khái niệm A được trình bày chi tiết.', encoding='utf-8')
    (src / 'doc_b.txt').write_text('Khái niệm B là ví dụ khác.', encoding='utf-8')

    db_path = tmp_path / 'kb.db'
    idx = KnowledgeIndexer(db_path, src)
    idx.index_all(embed_model=None)

    vec_a = [1.0] + [0.0] * 1023
    vec_b = [0.0, 1.0] + [0.0] * 1022
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute('SELECT id, doc_id, text FROM chunks ORDER BY id').fetchall()
        for cid, doc_id, text in rows:
            vec = vec_a if 'A' in text else vec_b
            blob = pack_vector(vec)
            conn.execute('UPDATE chunks SET embedding=? WHERE id=?', (blob, cid))
            conn.execute(
                """INSERT OR REPLACE INTO corpus_vector_provenance
                   (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                   VALUES (?, ?, ?, ?, 1024, 1, '2026-11-15T00:00:00Z')""",
                (
                    cid,
                    hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    BGE_NATIVE_IDENTITY,
                    hashlib.sha256(blob).hexdigest(),
                ),
            )
        conn.commit()

    monkeypatch.setattr(searcher_mod.sys, 'platform', 'darwin')
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding', lambda self, q, **kw: list(vec_a))

    hs = HybridSearcher(db_path, src_dir=src, data_dir=tmp_path)
    build_calls = []
    orig_validate = HybridSearcher._validate_semantic_rows

    def tracked_validate(*args, **kwargs):
        build_calls.append(1)
        return orig_validate(*args, **kwargs)

    monkeypatch.setattr(hs, '_validate_semantic_rows', tracked_validate)

    # Cold search builds normalized BLAS matrix once
    res1 = hs.search_semantic('truy vấn A', embed_model='bge-m3-mlx-fp16', limit=5)
    assert len(res1) == 1 and res1[0]['filename'] == 'doc_a.txt'
    assert len(build_calls) == 1

    # Warm repeat searches (global & scoped) hit cached matrix without rebuilding
    res2 = hs.search_semantic('truy vấn A', embed_model='bge-m3-mlx-fp16', limit=5)
    doc_a_id = res1[0]['doc_id']
    res3 = hs.search_semantic('truy vấn A', embed_model='bge-m3-mlx-fp16', limit=5, document_id=doc_a_id)
    assert res2[0]['chunk_id'] == res1[0]['chunk_id']
    assert res3[0]['doc_id'] == doc_a_id
    assert len(build_calls) == 1

    # Mutate SQLite row in-place to simulate tampered text -> next query invalidates cache immediately
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE chunks SET text='tampered text' WHERE doc_id=?", (doc_a_id,))
        conn.commit()

    res_after = hs.search_semantic('truy vấn A', embed_model='bge-m3-mlx-fp16', limit=5)
    assert res_after == []
    assert len(build_calls) == 2


def test_search_hybrid_deduplicates_freshness_pass_within_request_only(tmp_path, monkeypatch):
    """search_hybrid scans freshness once across FTS+semantic yet catches disk deletions on the next call."""
    import hashlib
    import sqlite3
    from app import searcher as searcher_mod, source_freshness as sf
    from app.indexer import pack_vector
    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient

    src = tmp_path / 'src'
    src.mkdir()
    doc1 = src / 'alpha.txt'
    doc2 = src / 'beta.txt'
    doc1.write_text('Định lý Alpha mô tả cân bằng thị trường đầy đủ.', encoding='utf-8')
    doc2.write_text('Định lý Beta mô tả tăng trưởng dài hạn.', encoding='utf-8')

    db_path = tmp_path / 'kb.db'
    KnowledgeIndexer(db_path, src).index_all(embed_model=None)

    vec_alpha = [1.0] + [0.0] * 1023
    with sqlite3.connect(db_path) as conn:
        for cid, text in conn.execute('SELECT id, text FROM chunks'):
            blob = pack_vector(vec_alpha)
            conn.execute('UPDATE chunks SET embedding=? WHERE id=?', (blob, cid))
            conn.execute(
                """INSERT OR REPLACE INTO corpus_vector_provenance
                   (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                   VALUES (?, ?, ?, ?, 1024, 1, '2026-11-15T00:00:00Z')""",
                (
                    cid,
                    hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    BGE_NATIVE_IDENTITY,
                    hashlib.sha256(blob).hexdigest(),
                ),
            )
        conn.commit()

    monkeypatch.setattr(searcher_mod.sys, 'platform', 'darwin')
    monkeypatch.setattr(NativeEmbeddingClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, 'get_embedding', lambda self, q, **kw: list(vec_alpha))

    seen_assess = []
    orig_assess = sf.assess_document_freshness

    def counted_assess(*args, **kwargs):
        seen_assess.append(1)
        return orig_assess(*args, **kwargs)

    monkeypatch.setattr(sf, 'assess_document_freshness', counted_assess)

    hs = HybridSearcher(db_path, src_dir=src, data_dir=tmp_path)
    out1 = hs.search_hybrid('Định lý Alpha', embed_model='bge-m3-mlx-fp16', top_k=4, enable_topic_routing=False)
    assert any(r['filename'] == 'alpha.txt' for r in out1)
    assert len(seen_assess) == 2  # Once per document across BOTH FTS and semantic!

    # Delete alpha.txt from disk -> the next search_hybrid must immediately exclude it
    doc1.unlink()
    out2 = hs.search_hybrid('Định lý Alpha', embed_model='bge-m3-mlx-fp16', top_k=4, enable_topic_routing=False)
    assert all(r['filename'] != 'alpha.txt' for r in out2)


def test_hybrid_search_promotes_high_semantic_chunk_covering_all_query_numbers(tmp_path, monkeypatch):
    from app.searcher import HybridSearcher

    hs = HybridSearcher(tmp_path / "unused.db")
    fts_rows = [
        {"chunk_id": 1, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 48, "chunk_index": 0,
         "text": "Lý thuyết trò chơi\n\nHai tên tội phạm đó đều chọn phản bội đối phương dẫn tới mỗi người phải ngồi tù 5 năm giam giữ.\nSự lựa chọn này được coi là kết quả của cân bằng Nash trong thuyết trò chơi hiện đại.\n48",
         "fts_score": 18.0, "fts_rank": 1, "source_type": "fts"},
        {"chunk_id": 2, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 51, "chunk_index": 0,
         "text": "Lý thuyết trò chơi\n\nNếu hai người cùng giữ im lặng và từ chối nhận tội thì mỗi người chỉ cần ngồi tù một năm.\nNhưng không ai chủ động thay đổi chiến lược vì lo ngại rủi ro từ đối phương.\n51",
         "fts_score": 17.5, "fts_rank": 2, "source_type": "fts"},
        {"chunk_id": 3, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 53, "chunk_index": 0,
         "text": "Lý thuyết trò chơi\n\nTình thế tiến thoái lưỡng nan của người tù xảy ra nhiều lần trong đời sống xã hội và kinh tế.\nHai bên đều chịu tác động từ động cơ ích kỷ ban đầu.\n53",
         "fts_score": 17.0, "fts_rank": 3, "source_type": "fts"},
        {"chunk_id": 4, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 46, "chunk_index": 0,
         "text": "Lý thuyết trò chơi\n\nCâu chuyện tình thế tiến thoái lưỡng nan của người tù là bài toán kinh điển kể về hai tên trộm.\nBị cảnh sát bắt giữ và thẩm vấn tách biệt trong hai căn phòng khác nhau.\n46",
         "fts_score": 16.5, "fts_rank": 4, "source_type": "fts"},
    ]
    # Page 47 covers BOTH query numbers (5 và 1) strongly in semantic space but lacked a keyword in FTS top-N;
    # Page footer "5" in filler chunk must NOT falsely match query number 5.
    sem_rows = [
        {**fts_rows[0], "semantic_score": 0.78, "semantic_rank": 1, "source_type": "semantic"},
        {**fts_rows[1], "semantic_score": 0.71, "semantic_rank": 2, "source_type": "semantic"},
        {"chunk_id": 5, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 47, "chunk_index": 0,
         "text": "Lý thuyết trò chơi\n\nCảnh sát đưa ra điều kiện: dù không nhận tội cũng chịu 1 năm tù giam.\nNếu chỉ một bên tố cáo thì được giảm còn hai tháng; nếu hai người đều nhận tội thì mỗi người ngồi tù 5 năm.\n47",
         "semantic_score": 0.64, "semantic_rank": 3, "source_type": "semantic"},
        {**fts_rows[2], "semantic_score": 0.63, "semantic_rank": 4, "source_type": "semantic"},
        {**fts_rows[3], "semantic_score": 0.62, "semantic_rank": 5, "source_type": "semantic"},
        {"chunk_id": 6, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 5, "chunk_index": 0,
         "text": "Lý thuyết trò chơi\n\nTình thế tiến thoái lưỡng nan của người tù là khái niệm tổng quát phân tích xung đột.\nLợi ích cá nhân ngắn hạn thường dẫn tới kết quả kém cho nhóm.\n5",
         "semantic_score": 0.60, "semantic_rank": 6, "source_type": "semantic"},
    ]
    monkeypatch.setattr(hs, "search_fts", lambda *a, **kw: list(fts_rows))
    monkeypatch.setattr(hs, "search_semantic", lambda *a, **kw: list(sem_rows))
    monkeypatch.setattr(hs, "_attach_page_quality_metadata", lambda chunks: None)

    hits = hs.search_hybrid(
        "Tại sao phản bội chịu 5 năm tù thay vì cùng chối tội chỉ chịu 1 năm?",
        top_k=4,
        embed_model="bge-m3-mlx-fp16",
        enable_topic_routing=False,
    )
    pages = [h["page_num"] for h in hits]
    assert 47 in pages, f"Expected page 47 (covering both 1 and 5) in top-4, got pages={pages}"
    assert pages.index(47) < pages.index(53) if 53 in pages else True
    assert 5 not in pages, f"Footer-only page number 5 must not promote filler chunk: {pages}"


def test_hybrid_search_promotes_sem_full_numeric_coverage_over_dual_partial_hits(tmp_path, monkeypatch):
    from app.searcher import HybridSearcher

    hs = HybridSearcher(tmp_path / "unused.db")
    base_kw = "Hai tên tội phạm phản bội trong thế tiến thoái lưỡng nan của một người tù"
    # Five dual-hit (FTS+SEM) chunks with zero/partial numeric match beat 0.010 boost when
    # the true full-numeric page (p.47) sits deeper in semantic-only ranks (e.g., rank 13).
    fts_rows = [
        {"chunk_id": 1, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 48, "chunk_index": 0,
         "text": f"{base_kw} nên mỗi người phải ngồi tù 5 năm giam giữ và cân bằng Nash.",
         "fts_score": 20.0, "fts_rank": 1, "source_type": "fts"},
        {"chunk_id": 2, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 51, "chunk_index": 0,
         "text": f"{base_kw} nếu im lặng thì mỗi người chỉ chịu một năm tù giam.",
         "fts_score": 19.0, "fts_rank": 2, "source_type": "fts"},
        {"chunk_id": 3, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 46, "chunk_index": 0,
         "text": f"{base_kw} khi hai người bị nhốt thẩm vấn riêng tại đồn cảnh sát.",
         "fts_score": 18.0, "fts_rank": 3, "source_type": "fts"},
        {"chunk_id": 4, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 53, "chunk_index": 0,
         "text": f"{base_kw} dễ lặp lại nhiều lần khiến đôi bên phải trả giá.",
         "fts_score": 17.0, "fts_rank": 4, "source_type": "fts"},
        {"chunk_id": 5, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 143, "chunk_index": 0,
         "text": f"{base_kw} cũng thường được liên hệ trong tâm lý học tình cảm.",
         "fts_score": 16.0, "fts_rank": 5, "source_type": "fts"},
    ]
    sem_rows = [
        {**fts_rows[0], "semantic_score": 0.83, "semantic_rank": 1, "source_type": "semantic"},
        {**fts_rows[1], "semantic_score": 0.71, "semantic_rank": 2, "source_type": "semantic"},
        {**fts_rows[2], "semantic_score": 0.68, "semantic_rank": 3, "source_type": "semantic"},
        {**fts_rows[3], "semantic_score": 0.67, "semantic_rank": 4, "source_type": "semantic"},
        {**fts_rows[4], "semantic_score": 0.63, "semantic_rank": 5, "source_type": "semantic"},
        # Ranks 6..12 filler chunks without query numbers; target p.47 sits at semantic_rank=13
        *[
            {"chunk_id": 100 + i, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 70 + i, "chunk_index": 0,
             "text": f"{base_kw} mô tả thêm các biến thể thông thường của tình huống.",
             "semantic_score": 0.629 - i * 0.001, "semantic_rank": 6 + i, "source_type": "semantic"}
            for i in range(7)
        ],
        {"chunk_id": 47, "doc_id": 22, "book_title": "Game", "filename": "game.pdf", "page_num": 47, "chunk_index": 0,
         "text": "Dù anh không nhận tội cũng án 1 năm tù giam; nếu tố cáo đồng bọn thì giảm còn 2 tháng và người kia 10 năm; cùng nhận tội thì mỗi người 5 năm.",
         "semantic_score": 0.625, "semantic_rank": 13, "source_type": "semantic"},
    ]
    monkeypatch.setattr(hs, "search_fts", lambda *a, **kw: list(fts_rows))
    monkeypatch.setattr(hs, "search_semantic", lambda *a, **kw: list(sem_rows))
    monkeypatch.setattr(hs, "_attach_page_quality_metadata", lambda chunks: None)

    hits = hs.search_hybrid(
        "Tại sao hai tên tội phạm đều chọn phản bội chịu 5 năm tù thay vì cùng chối tội chịu 1 năm?",
        top_k=4,
        embed_model="bge-m3-mlx-fp16",
        enable_topic_routing=False,
    )
    pages = [h["page_num"] for h in hits]
    assert 47 in pages, f"Expected full-numeric semantic hit p.47 in top-4, got pages={pages}"


def test_prefixed_ocr_table_of_contents_is_recognized_as_toc():
    from app.lexical_search import is_heading_or_toc_chunk

    toc = (
        "[NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH; KÝ HIỆU CHUẨN HÓA]\n"
        "MỤC LỤC\n\n"
        "01 Chân tướng lý thuyết trò chơi 9\n"
        "02 Tình thế tiến thoái lưỡng nan của người tù 45\n"
        "05 Trò chơi tâm lý trong cuộc sống 163"
    )
    assert is_heading_or_toc_chunk(toc) is True
