"""Legacy Windows Ollama compatibility and explicit native MLX routing contracts."""
import json
import requests
import pytest
from contextlib import nullcontext
from types import SimpleNamespace

from app import bilingual
from app.trained_client import TrainedModelClient


@pytest.fixture
def legacy_windows(monkeypatch):
    # Preserve explicit Windows compatibility without authorizing Mac fallback.
    from app import model_resolver
    monkeypatch.setattr(model_resolver, 'sys', SimpleNamespace(platform='win32'))
    # Local imports see an explicit Windows platform without mutating the host's sys module.
    import sys, types, requests
    platform_sys=types.ModuleType('sys'); platform_sys.__dict__.update(sys.__dict__)
    platform_sys.platform='win32'
    import builtins, importlib
    modules={f'app.{name}' for name in ('bilingual','query_normalizer','training_data','training_audit','server')}
    for name in modules:
        module=importlib.import_module(name)
        if hasattr(module,'sys'):monkeypatch.setattr(module,'sys',platform_sys)
    original_import=builtins.__import__
    def scoped_import(name,globals=None,locals=None,fromlist=(),level=0):
        if name=='sys' and globals and globals.get('__name__') in modules:return platform_sys
        return original_import(name,globals,locals,fromlist,level)
    monkeypatch.setattr(builtins,'__import__',scoped_import)
    from app.ollama_client import OllamaClient
    original_post=requests.post
    class LocalMetadata:
        status_code=200
        def json(self):return {'details':{'format':'gguf','family':'qwen'},'remote_host':None,'remote_model':None}
    def session_post(self,url,**kwargs):
        if url.endswith('/api/show'):return LocalMetadata()
        assert requests.post is not original_post, 'Legacy fixture must never reach a real daemon'
        response=requests.post(url,**kwargs)
        if not hasattr(response,'status_code'):response.status_code=200
        return response
    monkeypatch.setattr(requests.Session,'post',session_post)
    monkeypatch.setattr(OllamaClient,'find_best_chat_model',lambda self,preferred=None:preferred)



def test_default_ollama_translator_uses_selected_backend_and_bounded_context(tmp_path, monkeypatch, legacy_windows):
    english = 'The mind and body respond to trauma, and the nervous system changes with experience. ' * 4
    vietnamese = 'Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi theo trải nghiệm. ' * 4
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'message': {'content': json.dumps({'0': vietnamese}, ensure_ascii=False)}}

    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response()

    monkeypatch.setattr(bilingual, 'DEFAULT_CHAT_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(bilingual.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    translator = bilingual.LocalTranslator(tmp_path / 'translations.db')
    result = translator.translate([{'safe_text': english}])
    assert result == {0: vietnamese}
    assert len(calls) == 1
    assert calls[0]['model'] == 'qwen3.5:9b'
    assert calls[0]['think'] is False
    assert calls[0]['options']['num_ctx'] == 8192


def test_selected_default_query_normalizer_uses_ollama(tmp_path, monkeypatch, legacy_windows):
    from app import query_normalizer as qn
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'message': {'content': '{"normalized_query":"ánh xạ tuyến tính là gì?"}'}}
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(qn, 'DEFAULT_CHAT_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(requests, 'post', post)
    assert qn.normalize_query_with_model('anh xa tuyen tinh la gi?', model='qwen3.5:9b') == 'ánh xạ tuyến tính là gì?'
    assert calls[0]['model'] == 'qwen3.5:9b'
    assert calls[0]['options']['num_ctx'] == 8192


def test_selected_default_source_translation_never_uses_another_model(monkeypatch, legacy_windows):
    from app import training_data as td
    calls = []
    translated = 'Tâm trí và cơ thể phản ứng với sang chấn.'
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message': {'content': translated}}
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(td, 'DEFAULT_CHAT_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(requests, 'post', post)
    assert td.translate_excerpt('The mind and body respond to trauma.', model='qwen3.5:9b') == translated
    assert calls[0]['model'] == 'qwen3.5:9b'
    assert calls[0]['options']['num_ctx'] == 8192


def test_selected_default_source_audit_never_misreports_provider(monkeypatch, legacy_windows):
    from app import training_audit as ta
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'message': {'content': '{"translation_ok":true,"cross_source_link_ok":true,"approved":[true]}'}}
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(ta, 'DEFAULT_CHAT_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(requests, 'post', post)
    record = {'language':'vi', 'pair':{'title':'Book','sources':[{'id':'S1','page':1,'text':'Điều kiện của định lý được nêu trong tài liệu.'}]}, 'items':[{'question':'Điều kiện là gì?','answer':'Điều kiện theo nguồn [S1].'}]}
    assert ta.check_record(record, model='qwen3.5:9b')['decisions'] == [{'supported':True,'reason':''}]
    assert calls[0]['model'] == 'qwen3.5:9b'
    assert calls[0]['options']['num_ctx'] == 8192


def test_model_status_describes_actual_ollama_default_not_mlx_adapter(monkeypatch, legacy_windows):
    import asyncio
    from app import server
    from app.active_registry import active_registry
    monkeypatch.setattr(server, 'DEFAULT_CHAT_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(server.ollama, 'check_health', lambda: True)
    monkeypatch.setattr(server.ollama, 'list_models', lambda: [{'name':'qwen3.5:9b'}])
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(active_registry, 'get_active_info', lambda: {'mode':'lora','active_adapter':{'id':'legacy-adapter'}})
    result = asyncio.run(server.get_models())
    assert result['default_model'] == 'qwen3.5:9b'
    assert result['default_model_backend'] == 'ollama'
    assert result['default_model_available'] is True
    assert result['active_adapter'] is None
    assert result['model_mode'] == 'unadapted_base'
    assert 'qwen3.5:9b' in result['model_description']
    assert 'Qwen2.5 3B' not in result['model_description']


def test_selected_default_teacher_uses_named_model_without_training(monkeypatch, legacy_windows):
    from app import training_data as td
    calls = []
    item = {'question':'Hai nguồn đặt ra điều kiện gì?', 'answer':'Nguồn thứ nhất nêu các điều kiện cần đối với ánh xạ [S1]. Nguồn thứ hai giải thích rằng kết luận chỉ áp dụng khi thỏa mãn những điều kiện đã cho [S2].'}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message':{'content':json.dumps({'items':[item]},ensure_ascii=False)}}
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(td, 'DEFAULT_CHAT_MODEL', 'qwen3.5:9b')
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: False))
    monkeypatch.setattr(requests, 'post', post)
    pair = {'title':'Book','sources':[{'id':'S1','page':1,'text':'Các điều kiện đối với ánh xạ được nêu rõ trong tài liệu.'},{'id':'S2','page':2,'text':'Kết luận chỉ áp dụng khi những điều kiện đó được thỏa mãn.'}]}
    assert td.ask_teacher(pair,model='qwen3.5:9b',translation_model='qwen3.5:9b')['items'] == [item]
    assert calls[0]['model'] == 'qwen3.5:9b'
    assert calls[0]['options']['num_ctx'] == 8192


@pytest.mark.parametrize('alias', ['qwen3.5-9b-4bit', 'mlx-9b'])
def test_current_mlx_alias_stays_on_mlx_translation(tmp_path, monkeypatch, alias):
    source = 'The mind and body respond to trauma, and the nervous system changes with experience. ' * 4
    translated = 'Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi theo trải nghiệm. ' * 4
    monkeypatch.setattr(bilingual, 'DEFAULT_CHAT_MODEL', alias)
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *args, **kwargs: True))
    monkeypatch.setattr(TrainedModelClient, 'chat_complete', lambda *args, **kwargs: json.dumps({'0': translated}, ensure_ascii=False))
    monkeypatch.setattr(bilingual.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    def unexpected_ollama(*args, **kwargs):
        raise AssertionError('An existing MLX alias must not be sent to Ollama')
    monkeypatch.setattr(requests, 'post', unexpected_ollama)
    assert bilingual.LocalTranslator(tmp_path / 'translations.db').translate([{'safe_text': source}]) == {0: translated}
