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
