"""Candidate plans must never imply execution or inherit an incompatible adapter."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.training_profiles import build_training_plan


def model_config():
    return {
        'model_type': 'qwen3_5',
        'quantization': {'bits': 4, 'group_size': 64, 'mode': 'affine'},
        'text_config': {
            'model_type': 'qwen3_5_text', 'hidden_size': 4096,
            'num_hidden_layers': 32, 'num_attention_heads': 16,
            'num_key_value_heads': 4, 'head_dim': 256,
            'intermediate_size': 12288, 'full_attention_interval': 4,
            'attn_output_gate': True,
            'layer_types': ['linear_attention'] * 3 + ['full_attention'],
        },
    }


@pytest.fixture
def paths(tmp_path):
    model, data, adapter = (tmp_path / name for name in ('base', 'data', 'adapter'))
    model.mkdir()
    data.mkdir()
    cfg = model_config()
    cfg['text_config']['layer_types'] *= 8
    (model / 'config.json').write_text(json.dumps(cfg))
    return model, data, adapter


def test_missing_assets_still_produce_explicit_nonexecuting_draft(tmp_path):
    model, data, adapter = (tmp_path / name for name in ('base', 'data', 'adapter'))
    before = list(tmp_path.iterdir())
    plan = build_training_plan(model, data, adapter)
    assert plan['status'] == 'draft'
    assert plan['execution_ready'] is False
    assert plan['training_authorized'] is False
    assert plan['activation'] is False
    assert len(plan['blockers']) >= 5
    assert list(tmp_path.iterdir()) == before
    json.dumps(plan)


def test_config_identity_is_recorded_but_never_claims_weight_identity(paths):
    model, data, adapter = paths
    before = {str(p): p.read_bytes() for p in model.iterdir()}
    plan = build_training_plan(model, data, adapter)
    assert plan['base']['repo'] == 'mlx-community/Qwen3.5-9B-4bit'
    assert plan['base']['revision'] == '8b2b98c00a6b4d291155e4890773ca8f769aee53'
    assert plan['base']['config_sha256'] == hashlib.sha256((model / 'config.json').read_bytes()).hexdigest()
    assert plan['base']['model_dir'] == str(model.resolve())
    assert plan['execution_ready'] is False
    assert not adapter.exists()
    assert before == {str(p): p.read_bytes() for p in model.iterdir()}


def test_plan_is_architecture_specific_and_does_not_resume_legacy_adapter(paths):
    plan = build_training_plan(*paths)
    cfg = plan['mlx_config']
    assert cfg['train'] is False
    assert cfg['resume_adapter_file'] is None
    assert cfg['batch_size'] * cfg['grad_accumulation_steps'] == 4
    assert cfg['iters'] // cfg['grad_accumulation_steps'] == 100
    assert cfg['max_seq_length'] == 1024
    assert cfg['mask_prompt'] is True and cfg['grad_checkpoint'] is True
    assert cfg['fine_tune_type'] == 'lora'
    assert cfg['lora_parameters']['rank'] == 8
    assert cfg['lora_parameters']['scale'] == 2.0
    assert set(cfg['lora_parameters']['keys']) == {
        'self_attn.q_proj', 'self_attn.v_proj',
        'linear_attn.in_proj_qkv', 'linear_attn.out_proj',
    }
    assert cfg['adapter_path'] == str(paths[2].resolve())


@pytest.mark.parametrize('section,key,value', [
    ('top', 'model_type', 'qwen2'),
    ('text_config', 'model_type', 'qwen2'),
    ('text_config', 'num_attention_heads', 32),
    ('text_config', 'num_key_value_heads', 8),
    ('text_config', 'head_dim', 128),
    ('text_config', 'attn_output_gate', False),
    ('text_config', 'layer_types', ['full_attention'] * 32),
    ('quantization', 'bits', 8),
    ('quantization', 'group_size', 128),
])
def test_incompatible_architecture_cannot_be_relabelled_as_candidate(paths, section, key, value):
    model, data, adapter = paths
    path = model / 'config.json'
    config = json.loads(path.read_text())
    target = config if section == 'top' else config[section]
    target[key] = value
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        build_training_plan(model, data, adapter)
    assert not adapter.exists()


def test_malformed_config_fails_closed(paths):
    (paths[0] / 'config.json').write_text('{bad')
    with pytest.raises(ValueError):
        build_training_plan(*paths)


@pytest.mark.parametrize('output', ['model', 'data', 'inside_model', 'inside_data', 'parent'])
def test_adapter_cannot_overwrite_inputs_or_their_parent(paths, output):
    model, data, adapter = paths
    target = {'model': model, 'data': data, 'inside_model': model / 'new',
              'inside_data': data / 'new', 'parent': model.parent}[output]
    with pytest.raises(ValueError):
        build_training_plan(model, data, target)


def test_symlink_cannot_bypass_output_isolation(paths):
    model, data, adapter = paths
    adapter.symlink_to(model, target_is_directory=True)
    with pytest.raises(ValueError):
        build_training_plan(model, data, adapter / 'new')


def test_existing_adapter_cannot_be_silently_reused(paths):
    model, data, adapter = paths
    adapter.mkdir()
    (adapter / 'adapters.safetensors').write_bytes(b'existing')
    with pytest.raises(ValueError):
        build_training_plan(model, data, adapter)
    assert (adapter / 'adapters.safetensors').read_bytes() == b'existing'


def test_unknown_profile_fails_instead_of_falling_back(paths):
    with pytest.raises(ValueError):
        build_training_plan(*paths, profile='gemma4-or-legacy')


def test_cli_requires_explicit_paths_and_never_starts_training(paths):
    root = Path(__file__).resolve().parents[1]
    missing = subprocess.run([sys.executable, 'run.py', 'train-plan'], cwd=root,
                             capture_output=True, text=True)
    assert missing.returncode != 0
    model, data, adapter = paths
    done = subprocess.run([sys.executable, 'run.py', 'train-plan', '--model-dir', str(model),
                           '--data', str(data), '--adapter-dir', str(adapter)], cwd=root,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result['execution_ready'] is False and result['training_authorized'] is False
    assert not adapter.exists()
