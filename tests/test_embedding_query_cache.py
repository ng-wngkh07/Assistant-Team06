"""Repeated search queries reuse validated vectors without caching model identity by stat."""
import json
import threading
from contextlib import nullcontext
import pytest


def _make_fake_popen(handler):
    class _Stdin:
        def __init__(self, on_close):
            self._buf = []
            self._on_close = on_close
            self._closed = False

        def write(self, data):
            self._buf.append(data)
            return len(data)

        def flush(self):
            pass

        def close(self):
            if not self._closed:
                self._closed = True
                self._on_close("".join(self._buf))

    class _OutStream:
        def __init__(self, done, getter):
            self._done = done
            self._getter = getter

        def read(self):
            self._done.wait(timeout=5.0)
            return self._getter()

        def close(self):
            pass

    class _FakePopen:
        def __init__(self, cmd, **kw):
            self.args = cmd
            self.pid = -1
            self.returncode = None
            self._kw = kw
            self._stdout = ""
            self._stderr = ""
            self._done = threading.Event()
            self.stdin = _Stdin(self._finish)
            self.stdout = _OutStream(self._done, lambda: self._stdout)
            self.stderr = _OutStream(self._done, lambda: self._stderr)

        def _finish(self, text):
            if not self._done.is_set():
                rc, out, err = handler(text, **self._kw)
                self._stdout = out
                self._stderr = err
                self.returncode = rc
                self._done.set()

        def poll(self):
            return self.returncode

        def terminate(self):
            if self.returncode is None:
                self.returncode = -15
            self._done.set()

        def kill(self):
            if self.returncode is None:
                self.returncode = -9
            self._done.set()

        def wait(self, timeout=None):
            self._done.wait(timeout=timeout)
            return self.returncode

    return _FakePopen


def setup_client(tmp_path, monkeypatch):
    from app import native_embedding as mod
    client = mod.NativeEmbeddingClient(tmp_path)
    monkeypatch.setattr(mod.NativeEmbeddingClient, 'available', classmethod(lambda cls, *a: True))
    identity = ['artifact-A']
    monkeypatch.setattr(client, 'get_identity', lambda: identity[0])
    monkeypatch.setattr(mod.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    calls = []

    def run_handler(stdin_text, **kw):
        request = json.loads(stdin_text)
        calls.append(request)
        vecs = [[1.0] + [0.0]*1023 for _ in request['texts']]
        return 0, json.dumps({'vectors': vecs, 'model_identity': identity[0]}), ''

    monkeypatch.setattr(mod.subprocess, 'Popen', _make_fake_popen(run_handler))
    return client, identity, calls


def test_repeat_query_across_clients_reuses_validated_vector_and_returns_copy(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    first = client.get_embedding('Entropy là gì?')
    first[0] = 0
    from app.native_embedding import NativeEmbeddingClient
    second_client = NativeEmbeddingClient(tmp_path)
    monkeypatch.setattr(second_client, 'get_identity', lambda: identity[0])
    second = second_client.get_embedding('Entropy là gì?')
    assert second[0] == 1.0
    assert len(calls) == 1


def test_changed_artifact_and_token_limit_never_reuse_old_vector(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    client.get_embedding('Entropy', max_length=512)
    identity[0] = 'artifact-B'
    client.get_embedding('Entropy', max_length=512)
    client.get_embedding('Entropy', max_length=64)
    assert len(calls) == 3


def test_model_identity_is_verified_even_on_cache_hit(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    client.get_embedding('Entropy')
    def corrupt(): raise RuntimeError('Changed model bytes')
    monkeypatch.setattr(client, 'get_identity', corrupt)
    with pytest.raises(RuntimeError, match='Changed model bytes'):
        client.get_embedding('Entropy')
    assert len(calls) == 1


def test_cache_has_a_bound_and_evicts_old_queries(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    monkeypatch.setattr(client, '_query_cache_limit', 2)
    for text in ['A','B','C','A']:
        client.get_embedding(text)
    assert len(calls) == 4
    assert len(client._query_cache) <= 2


def test_artifact_change_during_generation_cannot_cache_a_vector(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    def replaced(texts, **kwargs):
        identity[0]='artifact-B'
        return [[1.0]+[0.0]*1023]
    monkeypatch.setattr(client, 'get_batch_embeddings', replaced)
    with pytest.raises(RuntimeError, match='thay đổi'):
        client.get_embedding('Entropy')


def test_failed_worker_never_populates_query_cache(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    from app import native_embedding as mod
    def fail(stdin_text, **kw):
        calls.append({'input': stdin_text, **kw})
        return 1, '', 'fixture failure'
    monkeypatch.setattr(mod.subprocess, 'Popen', _make_fake_popen(fail))
    for _ in range(2):
        with pytest.raises(RuntimeError, match='fixture failure'):
            client.get_embedding('Entropy')
    assert len(calls) == 2


def test_expected_vector_basis_is_checked_before_cache_hit_or_worker(tmp_path, monkeypatch):
    client, identity, calls = setup_client(tmp_path, monkeypatch)
    assert client.get_embedding('Entropy', expected_identity='artifact-A')
    assert len(calls) == 1
    # Even an already cached vector cannot satisfy a different stored basis.
    assert client.get_embedding('Entropy', expected_identity='artifact-B') is None
    assert client.get_embedding('New question', expected_identity='artifact-B') is None
    assert len(calls) == 1
    identity[0] = 'artifact-B'
    assert client.get_embedding('Entropy', expected_identity='artifact-B')
    assert len(calls) == 2
