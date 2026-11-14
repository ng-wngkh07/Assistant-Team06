"""Request-local native review cache isolation; these are CPU boundary tests."""
from pathlib import Path
import pytest


def test_review_session_is_nested_and_disposed_on_failure():
    from app.trained_client import native_review_session, current_review_cache_dir
    assert current_review_cache_dir() is None
    with pytest.raises(RuntimeError):
        with native_review_session():
            first = current_review_cache_dir()
            assert Path(first).is_dir()
            with native_review_session():
                assert current_review_cache_dir() == first
            raise RuntimeError("injected caller failure")
    assert current_review_cache_dir() is None
    assert not Path(first).exists()
    with native_review_session():
        assert current_review_cache_dir() != first


def test_cached_prefix_excludes_all_question_specific_data():
    from app.mlx_review_cache import review_prefix_length
    prefix = "trusted instruction " * 40
    prompt = prefix + "Dữ liệu cần kiểm định:\nSECRET SOURCE"
    encode = lambda text, **kw: list(text.encode())
    count = review_prefix_length(prompt, encode(prompt), encode)
    assert count > 0 and count % 128 == 0
    assert count <= len(encode(prefix))
    with_refs = prefix + "Đối chiếu chỉ số từ 0 do ứng dụng chép trực tiếp từ question:\nSECRET QUESTION"
    assert review_prefix_length(with_refs, encode(with_refs), encode) == count
    assert review_prefix_length("ordinary QA", encode("ordinary QA"), encode) == 0


def test_review_cache_never_opens_for_marker_only_requests(monkeypatch, tmp_path):
    from app.mlx_review_cache import prepare_review_cache
    before = set(tmp_path.iterdir())
    # A marker alone does not authorize caching arbitrary QA or source text.
    result = prepare_review_cache(None, None, 'private source', {
        'review_cache_dir': str(tmp_path),
        'messages': [{'role': 'system', 'content': 'untrusted'}, {'role': 'user', 'content':
            'Kiểm định độc lập từng câu từ source và question\nDữ liệu cần kiểm định:\n[]'}],
    }, None)
    assert result[0] == 'private source' and result[1] == {}
    assert set(tmp_path.iterdir()) == before


def test_review_session_does_not_cross_independent_contexts():
    from contextvars import Context
    from app.trained_client import native_review_session, current_review_cache_dir
    with native_review_session():
        outer = current_review_cache_dir()
        assert outer
        assert Context().run(current_review_cache_dir) is None


def test_only_review_calls_in_one_session_receive_the_temporary_directory(monkeypatch, native_context_metadata):
    import json
    from contextlib import nullcontext
    from types import SimpleNamespace
    import app.trained_client as module
    from app.practice_proof import native_grounded_review_prompt, native_grounded_review_schema
    from app.practice_adapter import SYSTEM_PROMPT
    monkeypatch.setattr(module.TrainedModelClient, 'available', lambda *a: True)
    monkeypatch.setattr(module.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    requests = []
    def run(*args, **kwargs):
        requests.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'answer': '{}', 'done_reason': 'stop'}))
    monkeypatch.setattr(module.subprocess, 'run', run)
    item = dict(index=1, source='Private source.', question='What?', choices=dict(A='one', B='two', C='three', D='four'), explanation='Private explanation.', evidence_quote='Private source.')
    prompt = native_grounded_review_prompt(item)
    client = module.TrainedModelClient()
    def review():
        client.chat_complete([dict(role='user', content=prompt)], system_prompt=SYSTEM_PROMPT,
                             temperature=0, max_tokens=1000, response_schema=native_grounded_review_schema(1, None))
    review()
    assert 'review_cache_dir' not in requests[-1]
    with module.native_review_session():
        directory = module.current_review_cache_dir()
        review()
        assert requests[-1]['review_cache_dir'] == directory
        client.chat_complete([dict(role='user', content='ordinary QA')])
        assert 'review_cache_dir' not in requests[-1]
    assert not Path(directory).exists()
