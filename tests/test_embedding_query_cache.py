"""Repeated search queries reuse validated vectors without caching model identity by stat."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest


def setup_client(tmp_path, monkeypatch):
    from app import native_embedding as mod
    client = mod.NativeEmbeddingClient(tmp_path)
    monkeypatch.setattr(mod.NativeEmbeddingClient, 'available', classmethod(lambda cls, *a: True))
    identity = ['artifact-A']
    monkeypatch.setattr(client, 'get_identity', lambda: identity[0])
    monkeypatch.setattr(mod.gpu_coordinator, 'acquire_for_inference', lambda: nullcontext())
    calls = []
    def run(*args, **kw):
        request = json.loads(kw['input']); calls.append(request)
        vecs = [[1.0] + [0.0]*1023 for _ in request['texts']]
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'vectors':vecs,'model_identity':identity[0]}))
    monkeypatch.setattr(mod.subprocess, 'run', run)
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
    def fail(*a, **kw):
        calls.append(kw)
        return SimpleNamespace(returncode=1,stderr='fixture failure',stdout='')
    monkeypatch.setattr(mod.subprocess,'run',fail)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='fixture failure'):
            client.get_embedding('Entropy')
    assert len(calls)==2


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
