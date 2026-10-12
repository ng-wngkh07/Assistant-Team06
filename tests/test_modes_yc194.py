from app.rag_agent import PsychologyAgent, has_relevant_evidence
from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk


def test_presentation_words_do_not_make_correct_source_irrelevant():
    query = "Hãy giải thích từng bước thật rõ ràng về trí nhớ làm việc"
    assert has_relevant_evidence(query, [make_chunk("Trí nhớ làm việc giữ và xử lý thông tin tạm thời.", semantic_score=0)])


def test_compare_mode_asks_for_second_concept_before_calling_model():
    searcher, model = RecordingSearcher([make_chunk("Trí nhớ làm việc giữ thông tin tạm thời.")]), RecordingModel()
    result = PsychologyAgent(searcher=searcher, ollama=model, translator=NoTranslation()).process_query_sync(
        "Trí nhớ làm việc", embed_model=None, model="test-model", answer_mode="compare")
    assert result["response_kind"] == "clarification"
    assert "hai" in result["answer"].lower() or "thứ hai" in result["answer"].lower()
    assert not model.calls


def test_compare_mode_requires_both_sides_even_with_high_semantic_score():
    searcher = RecordingSearcher([make_chunk("Stack hoạt động theo LIFO, phần tử thêm sau được lấy trước.", semantic_score=0.99)])
    model = RecordingModel()
    result = PsychologyAgent(searcher=searcher, ollama=model, translator=NoTranslation()).process_query_sync(
        "Stack và queue", embed_model=None, model="test-model", answer_mode="compare")
    assert result["response_kind"] == "insufficient_evidence"
    assert result["insufficient_reason"] == "partial_evidence"
    assert not model.calls


def test_compare_balances_two_sides_at_retrieval_boundary():
    class Facets(RecordingSearcher):
        def search_hybrid(self, **kwargs):
            self.calls.append(kwargs)
            if "queue" in kwargs["query"].lower() and "stack" not in kwargs["query"].lower():
                return [make_chunk("Queue theo FIFO, phần tử thêm trước được lấy trước.", doc_id=2, page_num=1)]
            return [make_chunk("Stack theo LIFO, phần tử thêm sau được lấy trước.", doc_id=1, page_num=i) for i in range(1, 13)]
    searcher, model = Facets(), RecordingModel("| Tiêu chí | Stack | queue |\n| --- | --- | --- |\n| Thứ tự lấy | LIFO [S1] | FIFO [S2] |")
    result = PsychologyAgent(searcher=searcher, ollama=model, translator=NoTranslation()).process_query_sync(
        "Stack và queue", embed_model=None, model="test-model", answer_mode="compare", top_k=6, processing_level="standard")
    assert any("queue" in call["query"].lower() and "stack" not in call["query"].lower() for call in searcher.calls)
    prompt = "\n".join(message["content"] for message in model.calls[0]["messages"])
    assert "Queue theo FIFO" in prompt and "Stack theo LIFO" in prompt
    assert result["response_kind"] == "answer"


def test_compare_shared_words_do_not_fabricate_evidence_for_missing_concept():
    searcher=RecordingSearcher([make_chunk('Trí nhớ làm việc giữ và xử lý thông tin tạm thời, dung lượng có giới hạn.',semantic_score=0.99)])
    model=RecordingModel()
    result=PsychologyAgent(searcher=searcher,ollama=model,translator=NoTranslation()).process_query_sync(
        'Trí nhớ làm việc và trí nhớ dài hạn',embed_model=None,model='test-model',answer_mode='compare')
    assert result['response_kind']=='insufficient_evidence'
    assert result['insufficient_reason']=='partial_evidence'
    assert not model.calls


def test_compare_header_without_actual_comparison_rows_is_not_an_answer():
    sources=[make_chunk('Stack dùng LIFO. Queue dùng FIFO.',semantic_score=.99)]
    model=RecordingModel('| Tiêu chí | Stack | Queue |\n| --- | --- | --- |\nĐây là hai cấu trúc dữ liệu. [S1]')
    result=PsychologyAgent(searcher=RecordingSearcher(sources),ollama=model,translator=NoTranslation()).process_query_sync(
        'Stack và queue',embed_model=None,model='test-model',answer_mode='compare')
    assert result['response_kind']=='generation_failed'
    assert not result['citations']
    assert len(model.calls)<=2


def test_legacy_steps_accepts_grounded_prose_without_forcing_steps():
    model=RecordingModel('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn. [S1]')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn.',semantic_score=.99)]),ollama=model,translator=NoTranslation()).process_query_sync(
        'Trí nhớ làm việc',embed_model=None,model='test-model',answer_mode='steps')
    assert result['response_kind']=='answer'
    assert result['citations']
    assert len(model.calls)==1


def test_ordinary_prose_uses_one_call_with_sources_and_output_budget():
    model=RecordingModel('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn. [S1]')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn.',semantic_score=.99)]),ollama=model,translator=NoTranslation()).process_query_sync(
        'Trí nhớ làm việc',embed_model=None,model='test-model',answer_mode='steps',processing_level='standard')
    assert len(model.calls)==1
    call=model.calls[0]
    assert '<document id="S1"' in call['messages'][0]['content']
    assert call['num_predict']==900
    assert len(call['system_prompt'].encode())+sum(len(m['content'].encode()) for m in call['messages'])+call['num_predict'] < call['num_ctx']
    assert result['response_kind']=='answer'
    assert result['citations']


def test_text_source_never_advertises_a_pdf_page_link():
    chunk=make_chunk('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn.',semantic_score=.99)
    chunk['filename']='notes.md'
    result=PsychologyAgent(searcher=RecordingSearcher([chunk]),ollama=RecordingModel('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn. [S1]'),translator=NoTranslation()).process_query_sync(
        'Trí nhớ làm việc',embed_model=None,model='test-model',answer_mode='quick')
    assert result['citations']
    assert result['citations'][0].get('pdf_url') is None
    assert result['citations'][0].get('page_image_url') is None


def test_mode_format_without_valid_citation_is_generation_failed():
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn.',semantic_score=.99)]),ollama=RecordingModel('Trí nhớ làm việc giữ thông tin giới hạn trong thời gian ngắn.'),translator=NoTranslation()).process_query_sync(
        'Trí nhớ làm việc',embed_model=None,model='test-model',answer_mode='quick')
    assert not result['citations']
    assert result['response_kind']=='generation_failed'
