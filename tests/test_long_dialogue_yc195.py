"""Bounded original-turn recall without treating history as current evidence."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app import server
from app.history import HistoryStore
from app.dialogue import DialogueGate, build_context

@pytest.fixture
def conversation(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path/'history.db')
    sid = store.create_session()['id']
    early_question = 'Trí nhớ làm việc lưu và xử lý thông tin như thế nào?'
    store.add_turn(sid, early_question, 'Thông tin ngắn hạn [S99].', [], 'test')
    other = store.create_session()['id']
    store.add_turn(other, early_question, 'PRIVATE OTHER SESSION MARKER', [], 'test')
    for i in range(20):
        store.add_turn(sid, f'Ánh xạ tuyến tính, ví dụ thứ {i} là gì?', f'Ví dụ tuyến tính {i}.', [], 'test')
    seen=[]
    def respond(**kwargs):
        seen.append(kwargs['chat_history'])
        return {'answer':'Thông tin bám nguồn hiện tại.', 'citations':[], 'response_kind':'answer'}
    def stream(**kwargs):
        seen.append(kwargs['chat_history'])
        yield {'type':'token','content':'Thông tin bám nguồn hiện tại.'}
        yield {'type':'done','citations':[], 'response_kind':'answer'}
    monkeypatch.setattr(server,'history_store',store)
    monkeypatch.setattr(server,'dialogue_gate',DialogueGate(tmp_path/'gate.lock'))
    monkeypatch.setattr(server,'gpu_coordinator',SimpleNamespace(check_inference_allowed=lambda:(True,''),acquire_for_inference=nullcontext))
    monkeypatch.setattr(server.agent,'process_query_sync',respond)
    monkeypatch.setattr(server.agent,'process_query_stream',stream)
    return TestClient(server.app),store,sid,early_question,seen

@pytest.mark.parametrize('route',['/api/chat','/api/chat/stream'])
def test_explicit_return_to_an_old_topic_recalls_original_same_session_turn(conversation,route):
    client,store,sid,early_question,seen=conversation
    response=client.post(route,json={'query':'Quay lại trí nhớ làm việc: vì sao lượng thông tin có giới hạn?', 'session_id':sid,'save_history':False})
    assert response.status_code==200
    contents=[m['content'] for m in seen[-1]]
    assert early_question in contents
    assert 'Ánh xạ tuyến tính, ví dụ thứ 19 là gì?' in contents
    assert len(contents)<=16
    assert all('PRIVATE OTHER SESSION MARKER' not in c for c in contents)
    messages,meta=build_context('Chỉ dùng nguồn hiện tại.', 'Câu hỏi hiện tại', ['<document id="S1">Nguồn mới</document>'],seen[-1])
    assert '[S99]' not in messages[0]['content']
    assert meta['source_ids']==['S1']
    assert meta['input_estimate']+meta['output_reserve']<=meta['context_capacity']

@pytest.mark.parametrize('route',['/api/chat','/api/chat/stream'])
def test_isolated_turn_does_not_recall_old_topics_or_client_history(conversation,route):
    client,store,sid,early_question,seen=conversation
    response=client.post(route,json={'query':'Quay lại trí nhớ làm việc', 'session_id':sid,'save_history':False,'isolate_context':True,'chat_history':[{'role':'user','content':'FAKE CLIENT MEMORY'}]})
    assert response.status_code==200
    assert seen[-1]==[]


def test_ambiguous_followup_keeps_recent_context_without_guessing_old_topic(conversation):
    client,store,sid,early_question,seen=conversation
    response=client.post('/api/chat',json={'query':'Cho ví dụ', 'session_id':sid,'save_history':False})
    assert response.status_code==200
    assert early_question not in [m['content'] for m in seen[-1]]
    assert seen[-1][-2]['content']=='Ánh xạ tuyến tính, ví dụ thứ 19 là gì?'
