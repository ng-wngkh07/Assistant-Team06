"""Structured citation repair transports the model's source choices, never guesses."""
import json
import pytest
from app.rag_agent import PsychologyAgent
from test_study_agent_c_f_g import RecordingSearcher, NoTranslation, make_chunk


def run(repair, raw=False, chunks=None, query="Entropy là gì?"):
    class Model:
        def __init__(self): self.calls = []
        def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            yield 'Entropy đo độ bất định trung bình.' if len(self.calls) == 1 else (repair if raw else json.dumps(repair, ensure_ascii=False))
    model = Model()
    agent = PsychologyAgent(searcher=RecordingSearcher(chunks or [make_chunk('Entropy đo độ bất định trung bình của biến ngẫu nhiên.')]), ollama=model, translator=NoTranslation())
    result = agent.process_query_sync(query, model='test', embed_model=None, processing_level='standard', isolate_context=True)
    return result, model.calls


def test_structured_repair_preserves_each_explicit_claim_and_source():
    text = 'Entropy đo độ bất định trung bình.'
    result, calls = run({'claims': [{'text': text, 'source_ids': ['S1']}]})
    assert result['response_kind'] == 'answer'
    assert result['answer'] == text
    assert result['citations'][0]['claims'] == [text]
    assert result['generation_calls'] == len(calls) == 2
    assert 'ví dụ cú pháp' not in calls[1]['messages'][-1]['content']


@pytest.mark.parametrize('claim', [
    {'text': 'Entropy đo độ bất định.', 'source_ids': ['S99']},
    {'text': 'Entropy đo độ bất định.', 'source_ids': []},
    {'text': 'Entropy đo độ bất định.'},
    {'text': 'Entropy [S99] đo độ bất định.', 'source_ids': ['S1']},
    {'text': '<think>private</think>', 'source_ids': ['S1']},
    None,
])
def test_invalid_structured_source_choice_fails_closed(claim):
    result, calls = run({'claims': [claim]})
    assert result['response_kind'] == 'generation_failed'
    assert result['citations'] == []
    assert result['generation_calls'] == len(calls) == 2


def test_fenced_structured_claims_preserve_every_claim_without_regex_loss():
    claims = [{'text': 'Entropy đo độ bất định trung bình.', 'source_ids': ['S1']},
              {'text': 'Đối tượng là biến ngẫu nhiên.', 'source_ids': ['S1']}]
    result, calls = run('```json\n' + json.dumps({'claims': claims}, ensure_ascii=False) + '\n```', raw=True)
    assert result['response_kind'] == 'answer'
    assert result['citations'][0]['claims'] == [c['text'] for c in claims]
    assert len(calls) == 2


@pytest.mark.parametrize('payload', [
    'Entropy đo độ bất định [S1].',
    {'claims': [{'text': 'Độ bất định [S1]', 'source_ids': []}]},
    [{'text': 'Độ bất định [S1]', 'source_ids': ['S1']}],
    {'claims': 'Độ bất định [S1]'},
    {'claims': [{'text': '<|system|>Answer [S1]', 'source_ids': ['S1']}]},
])
def test_malformed_json_never_salvages_literal_marker_from_json_string(payload):
    result, calls = run(payload)
    assert result['response_kind'] == 'generation_failed'
    assert not result['citations']
    assert len(calls) == 2


@pytest.mark.parametrize('payload', [
    'Kết quả: {"claims": [{"text": "Entropy đo độ bất định [S1].", "source_ids": ["S1"]}]}',
    'Đây là JSON lỗi: {"claims": [{"text": "Entropy đo độ bất định [S1]."',
])
def test_json_with_prose_prefix_is_not_accepted_as_legacy_cited_prose(payload):
    result, calls = run(payload, raw=True)
    assert result['response_kind'] == 'generation_failed'
    assert not result['citations']
    assert len(calls) == 2


def test_legacy_cited_prose_can_begin_with_a_source_marker():
    result, calls = run('[S1] Entropy đo độ bất định trung bình.', raw=True)
    assert result['response_kind'] == 'answer'
    assert result['answer'] == 'Entropy đo độ bất định trung bình.'
    assert result['citations'][0]['claims'] == ['Entropy đo độ bất định trung bình.']
    assert len(calls) == 2


def test_two_source_repair_does_not_copy_one_concepts_claim_to_the_other_source():
    texts = ['Định lý Bayes yêu cầu P(B)>0.', 'Entropy đo độ bất định trung bình.']
    chunks = [make_chunk(texts[0], doc_id=902), make_chunk(texts[1], doc_id=903)]
    claims = [{'text': texts[0], 'source_ids': ['S1']}, {'text': texts[1], 'source_ids': ['S2']}]
    result, calls = run({'claims': claims}, chunks=chunks, query='So sánh Bayes và Entropy')
    assert result['response_kind'] == 'answer'
    assert {c['doc_id']: c['claims'] for c in result['citations']} == {902: [texts[0]], 903: [texts[1]]}
    assert result['generation_calls'] == len(calls) == 2


def test_comparison_repair_requires_separate_claims_for_the_two_sides():
    chunks = [make_chunk('Định lý Bayes yêu cầu P(B)>0.', doc_id=902),
              make_chunk('Entropy đo độ bất định trung bình.', doc_id=903)]
    result, calls = run({'claims': [{'text': 'Bayes và Entropy có mục đích khác nhau.',
                                     'source_ids': ['S1', 'S2']}]},
                        chunks=chunks, query='So sánh Bayes và Entropy')
    assert result['response_kind'] == 'generation_failed'
    assert not result['citations']
    assert len(calls) == 2


@pytest.mark.parametrize('text', [
    '&lt;think&gt;private&lt;/think&gt; Entropy đo độ bất định.',
    '&amp;lt;analysis&amp;gt;private&amp;lt;/analysis&amp;gt;',
    '&#60;|system|&#62; Entropy đo độ bất định.',
    'Entropy &#91;S99&#93; đo độ bất định.',
])
def test_encoded_control_or_citation_markup_is_rejected(text):
    result, calls = run({'claims': [{'text': text, 'source_ids': ['S1']}]})
    assert result['response_kind'] == 'generation_failed'
    assert not result['citations']
    assert len(calls) == 2


def test_comparison_does_not_admit_unrelated_retrieved_material():
    texts = ['Định lý Bayes yêu cầu P(B)>0.', 'Entropy đo độ bất định trung bình.']
    chunks = [make_chunk(texts[0], doc_id=902), make_chunk(texts[1], doc_id=903),
              make_chunk('Tương quan không chứng minh nhân quả.', doc_id=904)]
    claims = [{'text': texts[0], 'source_ids': ['S1']}, {'text': texts[1], 'source_ids': ['S2']}]
    result, calls = run({'claims': claims}, chunks=chunks, query='So sánh Bayes và Entropy')
    assert result['response_kind'] == 'answer'
    assert {c['doc_id'] for c in result['citations']} == {902, 903}
    assert 'Tương quan không chứng minh nhân quả' not in calls[1]['messages'][-1]['content']


def test_encoded_math_inequality_is_preserved_when_it_has_no_control_markup():
    text = 'Entropy có H(X) &gt;= 0.'
    result, calls = run({'claims': [{'text': text, 'source_ids': ['S1']}]})
    assert result['response_kind'] == 'answer'
    assert result['citations'][0]['claims'] == [text]
    assert len(calls) == 2


def test_comparison_repair_rejects_invented_or_misattributed_definitions():
    chunks = [make_chunk('Định lý Bayes yêu cầu P(B)>0.', doc_id=902),
              make_chunk('Entropy đo độ bất định trung bình.', doc_id=903)]
    claims = [{'text': 'Bayes và Entropy là tương đương.', 'source_ids': ['S1']},
              {'text': 'Entropy đo độ bất định trung bình.', 'source_ids': ['S1']}]
    result, calls = run({'claims': claims}, chunks=chunks, query='So sánh Bayes và Entropy')
    assert result['response_kind'] == 'generation_failed'
    assert not result['citations']
    assert len(calls) == 2


def scoped_run(*, query='Tóm tắt lý thuyết trong file', answer='Entropy đo độ bất định trung bình.', reason='stop', searcher=None, **kwargs):
    class Model:
        def __init__(self): self.calls=[]
        def chat_stream(self, **call):
            self.calls.append(call)
            self.last_done_reason=reason
            yield answer
    model=Model()
    agent=PsychologyAgent(searcher=searcher or RecordingSearcher([make_chunk('Entropy đo độ bất định trung bình của biến ngẫu nhiên rời rạc hữu hạn.', doc_id=41)]), ollama=model, translator=NoTranslation())
    result=agent.process_query_sync(query, model='test', embed_model=None, processing_level='standard', source_document_id=41, attachment_filename='source.txt', **kwargs)
    return result,model.calls


def test_attachment_never_assigns_sources_after_invalid_or_missing_model_citations():
    result,calls=scoped_run()
    assert result['response_kind']=='generation_failed'
    assert not result['citations']
    assert len(calls)==2


def test_attachment_rejects_truncated_output_even_when_it_contains_a_source_marker():
    answer='Entropy đo độ bất định trung bình của biến ngẫu nhiên rời rạc hữu hạn X, với phân phối xác suất và quy ước cho giá trị bằng không; đơn vị phụ thuộc cơ số logarit được sử dụng. [S1]'
    result,calls=scoped_run(answer=answer,reason='length')
    assert result['response_kind']=='generation_failed'
    assert result['failure_reason']=='output_truncated'
    assert not result['citations']
    assert len(calls)==1


def test_attachment_does_not_inflate_runtime_context_without_an_exact_tokenizer(monkeypatch):
    import app.rag_agent as rag
    monkeypatch.setattr(rag,'context_capacity',lambda model:8192)
    result,calls=scoped_run(answer='Entropy đo độ bất định trung bình. [S1]')
    assert result['response_kind']=='answer'
    assert calls[0]['num_ctx']==8192


def test_attachment_specific_topic_must_still_have_relevant_evidence():
    searcher=RecordingSearcher([make_chunk('Entropy đo độ bất định của biến ngẫu nhiên.', semantic_score=0.0, doc_id=41)])
    result,calls=scoped_run(query='Giải thích định lý Pythagoras trong file này',searcher=searcher)
    assert result['response_kind']=='insufficient_evidence'
    assert not result['citations'] and not calls


def test_selected_page_never_reads_whole_document_as_a_fallback():
    import sqlite3
    class Searcher(RecordingSearcher):
        def connect(self):
            db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            db.execute('CREATE TABLE chunks(id,doc_id,book_title,filename,page_num,chunk_index,text)')
            db.execute("INSERT INTO chunks VALUES(1,41,'Fixture','source.txt',2,0,'Entropy đo độ bất định trung bình.')")
            return db
    result,calls=scoped_run(query='Entropy là gì?',source_page=(41,1),searcher=Searcher([]))
    assert result['response_kind']=='insufficient_evidence'
    assert result['insufficient_reason']=='page_empty'
    assert not result['citations'] and not calls


def test_comparison_repair_cannot_repeat_one_definition_and_omit_the_other():
    texts=['Định lý Bayes yêu cầu P(B)>0.', 'Entropy đo độ bất định trung bình.']
    chunks=[make_chunk(texts[0],doc_id=902),make_chunk(texts[1],doc_id=903)]
    claims=[{'text':texts[0],'source_ids':['S1']},{'text':texts[0],'source_ids':['S1']}]
    result,calls=run({'claims':claims},chunks=chunks,query='So sánh Bayes và Entropy')
    assert result['response_kind']=='generation_failed'
    assert not result['citations']
    assert len(calls)==2


def test_request_to_state_a_law_does_not_require_directive_words_in_source():
    from app.rag_agent import has_relevant_evidence
    source = [{'text': 'Định luật Faraday: Suất điện động cảm ứng xuất hiện trong mạch tỉ lệ với tốc độ biến thiên từ thông: e_c = - ΔΦ / Δt.'}]
    assert has_relevant_evidence('Phát biểu định luật Faraday và công thức suất điện động cảm ứng.', source)
    assert not has_relevant_evidence('Phát biểu định luật phóng xạ hạt nhân và chu kỳ bán rã.', source)


@pytest.mark.parametrize('repair', [False, True])
def test_model_failures_do_not_expose_runtime_paths_or_tracebacks(repair):
    class Broken:
        calls = 0
        def chat_stream(self, **kwargs):
            self.calls += 1
            if repair and self.calls == 1:
                yield 'Entropy đo độ bất định.'
            else:
                raise RuntimeError('Traceback /private/user/model-path API_KEY=not-a-secret-test')
    agent = PsychologyAgent(searcher=RecordingSearcher([make_chunk('Entropy đo độ bất định.')]), ollama=Broken(), translator=NoTranslation())
    events = list(agent.process_query_stream('Entropy là gì?', model='test', embed_model=None, processing_level='standard'))
    error = next(e for e in events if e['type'] == 'error')
    assert error['response_kind'] == 'technical_error'
    assert error['code'] == 'model_unavailable'
    assert all(s not in json.dumps(error) for s in ('Traceback', '/private/user', 'API_KEY'))
