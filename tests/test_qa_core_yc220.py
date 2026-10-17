"""User-visible QA contracts, independent of the model's stochastic wording."""
import pytest
from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk
from app.rag_agent import PsychologyAgent


def run(query, chunks=None, error=None, answer='Entropy đo độ bất định. [S1]', **kwargs):
    search = RecordingSearcher(chunks, error)
    model = RecordingModel(answer)
    agent = PsychologyAgent(searcher=search, ollama=model, translator=NoTranslation())
    result = agent.process_query_sync(query, model='test-ollama', embed_model=None,
                                      processing_level='standard', **kwargs)
    return result, search, model


@pytest.mark.parametrize('query', ['???', 'công thức này áp dụng khi nào?', 'định lý này có điều kiện gì?'])
def test_unanchored_question_requests_neutral_clarification(query):
    result, search, model = run(query)
    assert result['response_kind'] == 'clarification'
    assert 'tâm lý' not in result['answer'].casefold()
    assert result['generation_calls'] == 0
    assert not result['citations'] and not search.calls and not model.calls


def test_single_named_technical_term_is_searchable():
    result, search, model = run('Entropy', [make_chunk('Entropy đo độ bất định của biến ngẫu nhiên.')])
    assert search.calls and model.calls
    assert result['response_kind'] == 'answer'


def test_demonstrative_followup_uses_user_topic_but_isolation_does_not():
    history = [{'role':'user', 'content':'Định lý Bayes là gì?'},
               {'role':'assistant', 'content':'Câu trả lời trước không phải nguồn.'}]
    result, search, model = run('công thức này áp dụng khi nào?', chat_history=history)
    assert search.calls and 'Bayes' in search.calls[0]['query']
    result, search, model = run('công thức này áp dụng khi nào?', chat_history=history, isolate_context=True)
    assert result['response_kind'] == 'clarification' and not search.calls


@pytest.mark.parametrize('error,kind', [(None,'insufficient_evidence'), (RuntimeError('offline'),'technical_error')])
def test_no_generation_metadata_matches_actual_calls(error, kind):
    result, search, model = run('Điều kiện áp dụng định lý Bayes là gì?', error=error)
    assert result['response_kind'] == kind
    assert result['generation_calls'] == len(model.calls) == 0
    assert result['insufficient_reason'] == ('technical_error' if error else 'no_results')


@pytest.mark.parametrize('marker',['[S1]','[s1]'])
def test_citation_points_to_visible_claim_without_private_reasoning(marker):
    result, _, _ = run('Entropy là gì?', [make_chunk('Entropy đo độ bất định của biến ngẫu nhiên.')],
        answer='<think>Bí mật suy luận [S1].</think>Entropy đo độ bất định. '+marker)
    assert result['response_kind'] == 'answer'
    claims = result['citations'][0].get('claims', [])
    assert claims and any('Entropy đo độ bất định' in claim for claim in claims)
    assert all('Bí mật' not in claim and '[S1]' not in claim for claim in claims)
    assert '[S1]' not in result['answer']


@pytest.mark.parametrize('streaming',[False,True])
def test_claims_survive_api_history_and_idempotent_replay(tmp_path,monkeypatch,streaming):
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app import server
    from app.history import HistoryStore
    from app.dialogue import DialogueGate
    search=RecordingSearcher([make_chunk('Entropy đo độ bất định.')])
    model=RecordingModel('Entropy đo độ bất định. [S1]')
    agent=PsychologyAgent(searcher=search,ollama=model,translator=NoTranslation())
    monkeypatch.setattr(server,'agent',agent)
    monkeypatch.setattr(server,'history_store',HistoryStore(tmp_path/'history.db'))
    monkeypatch.setattr(server,'dialogue_gate',DialogueGate(tmp_path/'dialogue.lock'))
    monkeypatch.setattr(server,'gpu_coordinator',SimpleNamespace(check_inference_allowed=lambda:(True,''),acquire_for_inference=nullcontext))
    monkeypatch.setattr(server,'DEFAULT_CHAT_MODEL','test-ollama')
    monkeypatch.setattr(server,'DEFAULT_EMBED_MODEL',None)
    route='/api/chat/stream' if streaming else '/api/chat'
    def parse(response):
        assert response.status_code==200,response.text
        if not streaming:return response.json()
        return next(json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ') and json.loads(line[6:]).get('type')=='done')
    payload={'query':'Entropy là gì?','request_id':'qa-core-yc220-replay','processing_level':'standard'}
    with TestClient(server.app) as client:
        first=parse(client.post(route,json=payload))
        assert first['citations'][0]['claims']
        payload['session_id']=first['session_id']
        calls=len(model.calls)
        replay=parse(client.post(route,json=payload))
        assert replay['cached_replay'] and replay['citations']==first['citations']
        assert len(model.calls)==calls
        turns=client.get('/api/sessions/'+first['session_id']).json()['turns']
        assert len(turns)==1 and turns[0]['citations']==first['citations']


def test_claim_mapping_keeps_nearby_sentences_and_grouped_sources_separate():
    chunks=[make_chunk('Entropy đo độ bất định.',doc_id=41),
            make_chunk('Entropy của biến chắc chắn bằng 0.',doc_id=42)]
    result,_,_=run('Entropy là gì?',chunks,
        answer='Entropy đo độ bất định [S1]. Entropy của biến chắc chắn bằng 0 [S2].\nVí dụ này dùng hai nguồn [S1, S2].')
    citations={c['id']:c for c in result['citations']}
    assert any('độ bất định' in x for x in citations['S1']['claims'])
    assert not any('biến chắc chắn' in x for x in citations['S1']['claims'])
    assert any('biến chắc chắn' in x for x in citations['S2']['claims'])
    assert not any('độ bất định' in x for x in citations['S2']['claims'])
    assert all(any('hai nguồn' in x for x in c['claims']) for c in citations.values())


def test_clarification_keeps_selected_document_scope():
    result,search,model=run('???',source_document_id=41)
    assert result['search_scope']=={'kind':'document','document_id':41}
    assert result['response_kind']=='clarification'
    assert result['generation_calls']==0 and not search.calls


def test_selected_page_anchors_demonstrative_request():
    result,search,model=run('công thức này có nghĩa gì?',
        [make_chunk('Công thức entropy đo độ bất định.',page_num=12)],source_page=(41,12))
    assert search.calls and model.calls
    assert result['response_kind']=='answer'
    assert result['search_scope']=={'kind':'page','document_id':41,'page_num':12}


@pytest.mark.parametrize('connector',['; ', ' và '])
def test_different_cited_clauses_do_not_borrow_each_others_source(connector):
    result,_,_=run('Entropy có ý nghĩa gì?',
        [make_chunk('Entropy đo độ bất định.',doc_id=41),make_chunk('Log cơ số hai cho đơn vị bit.',doc_id=42)],
        answer='Entropy đo độ bất định [S1]'+connector+'log cơ số hai cho đơn vị bit [S2].')
    by_id={c['id']:c for c in result['citations']}
    assert any('bất định' in x for x in by_id['S1']['claims'])
    assert not any('đơn vị bit' in x for x in by_id['S1']['claims'])
    assert any('đơn vị bit' in x for x in by_id['S2']['claims'])
    assert not any('bất định' in x for x in by_id['S2']['claims'])


def test_empty_meaning_is_not_a_page_summary_request():
    result,search,model=run('???',[make_chunk('Trang nguồn có nội dung.')],source_page=(41,12))
    assert result['response_kind']=='clarification'
    assert result['generation_calls']==0 and not search.calls and not model.calls


def test_named_question_is_not_replaced_by_old_history_topic():
    result,search,model=run('Công thức Entropy này có ý nghĩa gì?',
        [make_chunk('Công thức entropy đo độ bất định.')],
        chat_history=[{'role':'user','content':'Định lý Bayes là gì?'}])
    assert search.calls and 'Bayes' not in search.calls[0]['query']
    assert result['response_kind']=='answer'


@pytest.mark.parametrize('raw',['[S1]','... [S1]'])
def test_source_marker_without_an_answer_is_not_a_success(raw):
    result,_,_=run('Entropy là gì?',[make_chunk('Entropy đo độ bất định.')],answer=raw)
    assert result['response_kind']=='generation_failed'
    assert not result['citations']
    assert result['failure_reason']


def test_failed_generation_is_still_counted_as_a_model_call():
    result,_,model=run('Entropy là gì?',[make_chunk('Entropy đo độ bất định.')],answer='❌ Mô hình lỗi')
    assert result['response_kind']=='technical_error'
    assert result['generation_calls']==len(model.calls)==1
    assert not result['citations']


def test_failed_auditor_attempt_is_counted_without_certifying_answer():
    class FailingAuditor(RecordingModel):
        def chat_stream(self,**kwargs):
            self.calls.append(kwargs)
            if len(self.calls)>1:
                raise RuntimeError('auditor offline')
            self.last_done_reason='stop'
            yield 'Entropy đo độ bất định. [S1]'
    model=FailingAuditor()
    agent=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Entropy đo độ bất định.')]),ollama=model,translator=NoTranslation())
    result=agent.process_query_sync('Entropy là gì?',model='test-ollama',embed_model=None,processing_level='careful')
    assert result['response_kind']=='generation_failed' and result['check_status']=='unverified'
    assert result['generation_calls']==len(model.calls)==2
    assert not result['citations']


@pytest.mark.parametrize('prefixes',[('1. ','2. '),('- ','- ')])
def test_source_marker_after_list_label_attaches_to_following_formula(prefixes):
    result,_,_=run('Ánh xạ tuyến tính cần điều kiện gì?',
        [make_chunk('f(u+v)=f(u)+f(v) với mọi u,v.',doc_id=41),
         make_chunk('f(αu)=αf(u) với mọi α và u.',doc_id=42)],
        answer=prefixes[0]+'[S1] f(u+v)=f(u)+f(v) với mọi u,v.\n'+
               prefixes[1]+'[S2] f(αu)=αf(u) với mọi α và u.')
    by_id={c['id']:c['claims'] for c in result['citations']}
    assert any('f(u+v)' in c for c in by_id['S1'])
    assert all('f(αu)' not in c for c in by_id['S1'])
    assert any('f(αu)' in c for c in by_id['S2'])
    assert all('f(u+v)' not in c for c in by_id['S2'])
    assert not any(c.strip() in ('1.','2.','-') for claims in by_id.values() for c in claims)


@pytest.mark.parametrize('intro',['Trong đoạn [S1], ','Theo nguồn [S1], '])
def test_source_intro_attaches_to_the_substantive_statement(intro):
    result,_,_=run('Entropy là gì?',[make_chunk('Entropy đo độ bất định.')],
                   answer='Đây là một định nghĩa. '+intro+'entropy đo độ bất định.')
    claims=result['citations'][0]['claims']
    assert any('entropy đo độ bất định' in c.casefold() for c in claims)
    assert not any(c.casefold() in ('trong đoạn','theo nguồn') for c in claims)


@pytest.mark.parametrize('fail_at',[1,2])
def test_inference_exception_is_typed_and_preserves_attempt_count(fail_at):
    class BrokenModel(RecordingModel):
        def chat_stream(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == fail_at:
                raise RuntimeError('local inference subprocess failed')
            self.last_done_reason = 'stop'
            yield 'Entropy đo độ bất định.'  # Missing marker triggers the one repair.
    model=BrokenModel('unused')
    agent=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Entropy đo độ bất định.')]),ollama=model,translator=NoTranslation())
    result=agent.process_query_sync('Entropy là gì?',model='test-ollama',embed_model=None,processing_level='standard')
    assert result['response_kind']=='technical_error'
    assert result['error'] and result['citations']==[]
    assert result['generation_calls']==len(model.calls)==fail_at


@pytest.mark.parametrize('claim',['f(u+v)=f(u)+f(v).','a = 7.'])
def test_answer_cleanup_preserves_case_sensitive_math_identifiers(claim):
    from app.answer_format import clean_answer, extract_citation_claims
    assert clean_answer(claim+' [S1]')==claim
    assert extract_citation_claims(claim+' [S1]',{'S1'})=={'S1':[claim]}


# YC-222 BUG-02: regression from real-model output (entropy_short, fix03 report).
def test_claim_keeps_leading_trong_when_it_is_not_a_source_reference():
    from app.answer_format import extract_citation_claims
    raw = ('Entropy là độ bất định trung bình. Trong trường hợp đặc biệt, nếu một giá trị '
           'có xác suất 1 thì entropy bằng 0. [S1]')
    claims = extract_citation_claims(raw, {'S1'})['S1']
    assert any(c.startswith('Trong trường hợp đặc biệt') for c in claims), claims


def test_clean_answer_capitalizes_sentence_after_removed_source_intro():
    from app.answer_format import clean_answer
    out = clean_answer('Theo tài liệu, entropy đo độ bất định. Trong đoạn [S1], có ghi rõ rằng đơn vị là bit.')
    assert '. có' not in out and '. Có ghi rõ rằng đơn vị là bit.' in out, out


def test_draft_claims_drop_source_intro_with_marker():
    from app.reasoning_policy import extract_draft_claims
    claims = extract_draft_claims('Theo nguồn [S1], ví dụ P(A|B)=0,4.')
    assert claims and not any('Theo nguồn' in c or ' ,' in c for c in claims), claims
    assert any('P(A|B)=0,4' in c for c in claims)
