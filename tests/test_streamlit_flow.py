"""Real Streamlit widget/state regression tests; HTTP boundaries are isolated."""
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
from unittest.mock import patch
from types import SimpleNamespace
from copy import deepcopy
import requests

APP = str(Path(__file__).resolve().parents[1] / 'ui' / 'app.py')

def button(at,text):
    return next(b for b in at.button if text in b.label)

def healthy(*a,**k): return SimpleNamespace(status_code=200)

def test_retry():
    calls=[]
    def post(*a,**kwargs):
        calls.append(deepcopy(kwargs['json']))
        if len(calls)==1: raise requests.Timeout('PRIVATE_DETAIL')
        return SimpleNamespace(status_code=200,json=lambda:{'answer':'Trợ giúp đã hoàn tất.','citations':[],'session_id':'session-real'})
    with patch('requests.get',healthy),patch('requests.post',post):
        at=AppTest.from_file(APP).run()
        at.selectbox[0].set_value('compare').run()
        at.selectbox[1].set_value('careful').run()
        at.chat_input[0].set_value('help').run()
        assert not at.exception
        assert at.session_state['pending_request']['query']=='help'
        assert len(at.session_state['messages'])==2
        assert at.chat_input[0].disabled
        assert calls[0]['answer_mode']=='compare' and calls[0]['processing_level']=='careful'
        assert calls[0]['request_id']
        assert not at.session_state['session_id']
        at.selectbox[0].set_value('quick').run()
        at.selectbox[1].set_value('auto').run()
        assert len(calls)==1
        button(at,'Thử lại').click().run()
        assert not at.exception
        assert len(calls)==2 and calls[0]==calls[1], 'Retry must reuse original ID, history, mode, level and session'
        assert at.session_state['pending_request'] is None
        assert at.session_state['last_error'] is None
        assert at.session_state['session_id']=='session-real'
        assert len(at.session_state['messages'])==3
        button(at,'Cuộc trò chuyện mới').click().run()
        assert not at.exception
        assert len(at.session_state['messages'])==1
        assert at.session_state['session_id'] is None
        assert at.session_state['pending_request'] is None
        assert len(calls)==2


def test_cancel():
    with patch('requests.get',healthy),patch('requests.post',side_effect=requests.Timeout('offline')):
        at=AppTest.from_file(APP).run()
        previous = [
            {'role':'user','content':'Câu hỏi đã hoàn tất'},
            {'role':'assistant','content':'Câu trả lời đã hoàn tất','citations':[]},
        ]
        at.session_state['messages'] = deepcopy(previous)
        at.session_state['session_id'] = 'existing-session'
        at.run()
        at.chat_input[0].set_value('Câu hỏi bị hủy').run()
        assert not at.exception and len(at.session_state['messages'])==3
        button(at,'Hủy yêu cầu').click().run()
        assert not at.exception
        assert at.session_state['pending_request'] is None
        assert at.session_state['last_error'] is None
        assert not at.chat_input[0].disabled
        assert at.session_state['messages'] == previous, 'Preserve completed turns and remove only canceled pending query'
        assert at.session_state['session_id'] == 'existing-session'



@pytest.mark.parametrize("history", [
    [{"role":"user" if i % 2 == 0 else "assistant","content":f"Lượt {i}"} for i in range(40)],
    [{"role":"assistant","content":"a" * 5000}],
    [{"role":"assistant","content":"ệ" * 4000} for _ in range(10)],
])
def test_long_conversation_sends_only_backend_history_limit(history):
    calls=[]
    def post(*args,**kwargs):
        calls.append(deepcopy(kwargs['json']))
        return SimpleNamespace(status_code=200,json=lambda:{
            'answer':'Trợ giúp.', 'citations':[], 'session_id':'existing-session'})
    with patch('requests.get',healthy),patch('requests.post',post):
        at=AppTest.from_file(APP).run()
        previous=deepcopy(history)
        at.session_state['messages']=deepcopy(previous)
        at.session_state['session_id']='existing-session'
        at.run()
        at.chat_input[0].set_value('help').run()
        assert not at.exception
        assert len(calls)==1
        sent=calls[0]['chat_history']
        assert len(sent)<=32, 'Backend permits at most 32 client messages'
        assert all(len(m['content'])<=4000 for m in sent)
        assert sum(len(m['content'].encode('utf-8')) for m in sent)<=32000
        assert at.session_state['messages'][:len(previous)]==previous, 'Keep earlier turns visible while limiting request context'
        assert len(at.session_state['messages'])==len(previous)+2
