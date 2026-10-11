"""Independent failures must block inference on changed model/evaluation bytes."""
import hashlib
import json
from pathlib import Path

import pytest
from scripts import evaluate_exact_base as evaluator


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def base_bundle(tmp_path):
    model = tmp_path/'model'
    model.mkdir()
    for name in ('model.safetensors', 'config.json', 'tokenizer.json'):
        (model/name).write_bytes(b'original '+name.encode())
    identity = tmp_path/'identity.json'
    identity.write_text(json.dumps({'repo': 'mlx-community/Qwen2.5-3B-Instruct-4bit',
                                   'revision': 'a'*40,
                                   'files_sha256': {p.name: sha(p) for p in model.iterdir()}}))
    return model, identity


@pytest.mark.parametrize('name', ['model.safetensors', 'tokenizer.json'])
def test_changed_base_or_tokenizer_blocks_inference(base_bundle, name):
    model, identity = base_bundle
    (model/name).write_bytes(b'changed after identity pinned')
    with pytest.raises(ValueError):
        evaluator.validate_base_identity(model, identity)


def test_unpinned_extra_model_file_blocks_inference(base_bundle):
    model, identity = base_bundle
    (model/'generation_config.json').write_text('{"eos_token_id": 0}')
    with pytest.raises(ValueError):
        evaluator.validate_base_identity(model, identity)


def test_changed_frozen_suite_cannot_reuse_content_approval(tmp_path):
    suite = tmp_path/'suite.json'
    suite.write_text('{"cases":[{"id":"independent-original"}]}')
    approval = tmp_path/'approval.json'
    approval.write_text(json.dumps({'decision': 'PASS', 'independent_review_complete': True,
                                    'suite_sha256': sha(suite)}))
    suite.write_text('{"cases":[{"id":"changed-after-review"}]}')
    with pytest.raises(ValueError):
        evaluator.validate_evaluation_approval(suite, approval)


def test_matching_suite_without_independent_review_blocks_inference(tmp_path):
    suite = tmp_path/'suite.json'
    suite.write_text('{"cases":[{"id":"not-reviewed"}]}')
    approval = tmp_path/'approval.json'
    approval.write_text(json.dumps({'decision': 'PASS', 'independent_review_complete': False,
                                    'suite_sha256': sha(suite)}))
    with pytest.raises(ValueError):
        evaluator.validate_evaluation_approval(suite, approval)


def test_unchanged_complete_base_identity_passes(base_bundle):
    model, identity = base_bundle
    evaluator.validate_base_identity(model, identity)


def test_unchanged_reviewed_suite_passes(tmp_path):
    suite = tmp_path/'suite.json'
    suite.write_text('{"cases":[{"id":"reviewed-original"}]}')
    approval = tmp_path/'approval.json'
    approval.write_text(json.dumps({'decision': 'PASS', 'independent_review_complete': True,
                                    'suite_sha256': sha(suite)}))
    evaluator.validate_evaluation_approval(suite, approval)


def test_empty_directory_is_not_a_pinned_model(tmp_path):
    model = tmp_path/'empty-model'
    model.mkdir()
    identity = tmp_path/'identity.json'
    identity.write_text(json.dumps({'repo': 'mlx-community/Qwen2.5-3B-Instruct-4bit',
                                   'revision': 'a'*40, 'files_sha256': {}}))
    with pytest.raises(ValueError):
        evaluator.validate_base_identity(model, identity)


@pytest.mark.parametrize('gate', ['validate_evaluation_approval', 'validate_base_identity'])
def test_execution_reaches_identity_and_content_gates_before_resource_use(monkeypatch, gate):
    """An environment switch alone must not authorize an unreviewed inference run."""
    monkeypatch.setenv('CODEX_EXACT_BASE_EXECUTION_AUTHORIZED', '1')
    monkeypatch.setattr(evaluator, 'validate_evaluation_approval', lambda *a, **kw: {})
    monkeypatch.setattr(evaluator, 'validate_base_identity', lambda *a, **kw: {})

    def reject_unreviewed(*args, **kwargs):
        raise ValueError('independent review or identity rejected')

    def prohibit_resource_use(*args, **kwargs):
        raise AssertionError('resource preflight reached before required gate')

    monkeypatch.setattr(evaluator, gate, reject_unreviewed)
    monkeypatch.setattr(evaluator, 'get_hardware_memory_profile', prohibit_resource_use)
    with pytest.raises(ValueError, match='independent review or identity rejected'):
        evaluator.run_exact_base_evaluation()
