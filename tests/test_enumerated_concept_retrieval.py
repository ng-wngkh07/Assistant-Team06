from app.searcher import extract_query_concepts


def test_requested_number_and_generic_properties_do_not_obscure_named_concept():
    concepts = extract_query_concepts('Bốn tính chất cơ bản của lập trình hướng đối tượng là gì?')
    assert 'lập trình hướng đối tượng' in concepts
    assert any(c.casefold() == 'oop' for c in concepts)


def test_requested_properties_do_not_fill_context_with_subject_headers(tmp_path,monkeypatch):
    from app.searcher import HybridSearcher
    service=HybridSearcher(tmp_path/'unused.db')
    texts=['Circuit notes\nBa tính chất của bộ lọc là ổn định, tuyến tính và bất biến theo thời gian.',
           'Circuit notes\nLịch nộp bài và yêu cầu trình bày báo cáo thực hành.']
    rows=[{'chunk_id':i,'filename':'notes.md','book_title':'Circuits','page_num':i,
           'chunk_index':0,'text':text,'fts_rank':i,'fts_score':1/i} for i,text in enumerate(texts,1)]
    monkeypatch.setattr(service,'search_fts',lambda *a,**kw:rows)
    results=service.search_hybrid('Ba tính chất của bộ lọc là gì?',embed_model=None)
    assert [r['chunk_id'] for r in results] == [1]


def test_high_similarity_cannot_invent_an_unseen_specific_record():
    from app.rag_agent import has_relevant_evidence
    chunks=[{'text':'Student enrollment data describes courses and assessment methods.','semantic_score':0.99}]
    query='Điểm thi chính thức của sinh viên EXAMPLE-NO-RECORD-12345678 là bao nhiêu?'
    assert has_relevant_evidence(query,chunks) is False
    chunks[0]['text'] += ' EXAMPLE-NO-RECORD-12345678 scored 8.'
    assert has_relevant_evidence(query,chunks) is True
