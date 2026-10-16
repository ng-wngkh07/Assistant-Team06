"""Public UI adapter boundary: real payloads and fail-closed error handling."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
import requests
from ui import app as ui
from app.server import ChatRequest

def reply(status=200, data=None, error=None):
    def decode():
        if error: raise error
        return data
    return SimpleNamespace(status_code=status, json=decode, text="PRIVATE_BACKEND_DETAIL")

def test_default_payload_matches_backend_schema(monkeypatch):
    seen = {}
    def post(url, **kwargs):
        seen.update(kwargs["json"])
        return reply(data={"answer":"Có nguồn [S1].", "citations":[], "session_id":"session-real"})
    monkeypatch.setattr(ui.requests,"post",post)
    result = ui.call_chat_api("Câu hỏi")
    assert ChatRequest.model_validate(seen).answer_mode != "standard"
    assert result["answer"] == "Có nguồn [S1]."

@pytest.mark.parametrize("status", [409,422,500,503])
def test_http_errors_are_not_answers_and_do_not_expose_body(monkeypatch,status):
    monkeypatch.setattr(ui.requests,"post",lambda *a,**k: reply(status))
    result = ui.call_chat_api("Câu hỏi", session_id="old-session")
    assert result.get("error") or result.get("ok") is False
    assert not result.get("citations")
    assert "PRIVATE_BACKEND_DETAIL" not in str(result)
    assert result.get("session_id", "old-session") in (None,"old-session")

def test_network_failure_cannot_invent_sources_or_session(monkeypatch):
    def fail(*a,**k): raise requests.Timeout("PRIVATE_NETWORK_DETAIL")
    monkeypatch.setattr(ui.requests,"post",fail)
    result = ui.call_chat_api("Câu hỏi")
    assert result.get("error") or result.get("ok") is False
    assert not result.get("citations") and not result.get("session_id")
    assert "PRIVATE_NETWORK_DETAIL" not in str(result)

@pytest.mark.parametrize("data", [None, [], {}, {"answer":42,"citations":[]}, {"answer":"Đáp", "citations":{}}, {"answer":"Đáp", "citations":[None]}])
def test_malformed_success_fails_closed(monkeypatch,data):
    monkeypatch.setattr(ui.requests,"post",lambda *a,**k: reply(data=data))
    result = ui.call_chat_api("Câu hỏi")
    assert result.get("error") or result.get("ok") is False
    assert not result.get("citations")

def test_invalid_json_fails_closed(monkeypatch):
    monkeypatch.setattr(ui.requests,"post",lambda *a,**k: reply(error=ValueError("PRIVATE_JSON_DETAIL")))
    result = ui.call_chat_api("Câu hỏi")
    assert result.get("error") or result.get("ok") is False
    assert "PRIVATE_JSON_DETAIL" not in str(result)

class Display:
    def __init__(self): self.lines=[]
    def expander(self,*a,**kw): return nullcontext()
    def __getattr__(self,name):
        return lambda text,*a,**kw: self.lines.append(str(text))

@pytest.mark.parametrize("url", ["https://attacker.example/x", "//attacker.example/x", "javascript:alert(1)", "/api/documents/../secret/pdf", "/api/documents/1/pdf#page=1) [x](https://attacker.example)"])
def test_citation_cannot_link_to_untrusted_url(monkeypatch,url):
    display=Display()
    monkeypatch.setattr(ui,"st",display)
    ui.render_citations([{"id":"S1","book_title":"Nguồn","page_num":1,"score":0.95,"pdf_url":url}])
    assert not any("](" in line for line in display.lines)
    assert not any("95%" in line for line in display.lines)

def test_citation_handles_nulls_and_opens_real_page(monkeypatch):
    display=Display()
    monkeypatch.setattr(ui,"st",display)
    ui.render_citations([{"id":"S1","book_title":None,"page_num":3,"snippet":None,"score":None,"pdf_url":"/api/documents/7/pdf#page=3","page_quality":{"status":"needs_review","warnings":["Kiểm tra nguồn"]}}])
    assert any(f"{ui.BACKEND_URL.rstrip(chr(47))}/api/documents/7/pdf#page=3" in line for line in display.lines)

@pytest.mark.parametrize('flags', [
    {'response_kind':'technical_error'},
    {'response_kind':'generation_failed'},
    {'error':'PRIVATE_BACKEND_DETAIL'},
    {'is_truncated':True},
])
def test_http_200_failed_turn_is_not_treated_as_success(monkeypatch,flags):
    data={'answer':'Lượt này chưa hoàn tất.', 'citations':[], **flags}
    monkeypatch.setattr(ui.requests,'post',lambda *a,**k: reply(data=data))
    result=ui.call_chat_api('Câu hỏi')
    assert result.get('error') or result.get('ok') is False
    assert 'PRIVATE_BACKEND_DETAIL' not in str(result)


def test_real_page_quality_warning_is_visible(monkeypatch):
    display=Display()
    monkeypatch.setattr(ui,'st',display)
    ui.render_citations([{'id':'S1','page_num':3,'snippet':'Nguồn',
        'page_quality':{'needs_review':True,'warnings':['unrepresented_formula_or_diagram_suspected']}}])
    assert any('unrepresented_formula_or_diagram_suspected' in line for line in display.lines)


@pytest.mark.parametrize("url", ["/api/documents/0/pdf#page=3", "/api/documents/7/pdf#page=0"])
def test_zero_document_or_page_cannot_be_opened(monkeypatch,url):
    display=Display()
    monkeypatch.setattr(ui,"st",display)
    ui.render_citations([{"id":"S1","page_num":3,"pdf_url":url}])
    assert not any("](" in line for line in display.lines)

@pytest.mark.parametrize("answer", ["", "  "])
def test_empty_answer_is_not_success(monkeypatch,answer):
    monkeypatch.setattr(ui.requests,"post",lambda *a,**k: reply(data={"answer":answer,"citations":[]}))
    result=ui.call_chat_api("Câu hỏi")
    assert result.get("error") or result.get("ok") is False


def test_citation_untrusted_text_is_not_rendered_as_markdown_link(monkeypatch):
    calls=[]
    class TextDisplay(Display):
        def markdown(self,text,*a,**kw): calls.append(str(text))
        def caption(self,text,*a,**kw): calls.append(str(text))
    monkeypatch.setattr(ui,"st",TextDisplay())
    ui.render_citations([{"id":"S1","book_title":"[click](https://attacker.example/title)","page_num":3,"snippet":"[click](https://attacker.example/snippet)"}])
    assert not any("[click](https://attacker.example" in line for line in calls)


def test_source_text_is_preserved_as_plain_text(monkeypatch):
    raw=' \n  [nhãn](https://attacker.example) <https://attacker.example> https://attacker.example\n '
    texts=[]
    markdown=[]
    class PlainDisplay(Display):
        def text(self,value,*a,**kw): texts.append(str(value))
        def markdown(self,value,*a,**kw): markdown.append(str(value))
        def caption(self,value,*a,**kw): markdown.append(str(value))
        def warning(self,value,*a,**kw): markdown.append(str(value))
    monkeypatch.setattr(ui,'st',PlainDisplay())
    ui.render_citations([{'id':'S1','page_num':3,'book_title':raw,'snippet':raw,'page_quality':{'needs_review':True,'warnings':[raw]}}])
    assert sum(raw in text for text in texts) >= 3, 'Preserve title, snippet and warning as plain text'
    assert all(raw not in text for text in markdown), 'Source text must not create autolinks'


def test_pdf_action_label_cannot_inject_link_from_page_metadata(monkeypatch):
    markdown=[]
    class LinkDisplay(Display):
        def markdown(self,value,*a,**kw): markdown.append(str(value))
    monkeypatch.setattr(ui,'st',LinkDisplay())
    ui.render_citations([{'id':'S1','page_num':'](https://attacker.example) [x',
        'pdf_url':'/api/documents/7/pdf#page=3'}])
    assert all('https://attacker.example' not in value for value in markdown)
    assert any('/api/documents/7/pdf#page=3' in value for value in markdown)
