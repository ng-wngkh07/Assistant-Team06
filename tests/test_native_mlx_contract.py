"""Native model identity, exact template budgets and offline-worker contracts."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _worker(tmp_path, monkeypatch, *, nested=True, input_count=40, max_tokens=50, context=100, finish_reason="stop", generation_tokens=7):
    model = tmp_path / 'base'
    model.mkdir()
    config = {'max_position_embeddings': 262144}
    (model / 'config.json').write_text(json.dumps({'text_config': config} if nested else config))
    calls = {'loads': [], 'templates': [], 'generation': []}

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            calls['templates'].append(kwargs)
            return 'formatted local prompt'
        def encode(self, text):
            return list(range(input_count if text == 'formatted local prompt' else 3))

    tokenizer = Tokenizer()
    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            assert Path(path).resolve() == model.resolve()
            assert kwargs.get('local_files_only') is True
            assert kwargs.get('trust_remote_code') is False
            return tokenizer

    def load(path, **kwargs):
        calls['loads'].append((path, kwargs))
        return object(), tokenizer
    def generate(model, active_tokenizer, **kwargs):
        calls['generation'].append(kwargs)
        return 'Đã kiểm tra.'

    def stream_generate(model, active_tokenizer, **kwargs):
        calls['generation'].append(kwargs)
        yield types.SimpleNamespace(text='Đã kiểm tra.', finish_reason=finish_reason,
            prompt_tokens=input_count, generation_tokens=generation_tokens,
            prompt_tps=20.0, generation_tps=5.0, peak_memory=5.4)

    lm = types.ModuleType('mlx_lm'); lm.load = load; lm.generate = generate; lm.stream_generate = stream_generate
    sampling = types.ModuleType('mlx_lm.sample_utils'); sampling.make_sampler = lambda **kw: kw
    core = types.ModuleType('mlx.core'); core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType('mlx'); mlx.core = core
    transformers = types.ModuleType('transformers'); transformers.AutoTokenizer = AutoTokenizer
    for name, module in [('mlx_lm', lm), ('mlx_lm.sample_utils', sampling), ('mlx', mlx), ('mlx.core', core), ('transformers', transformers)]:
        monkeypatch.setitem(sys.modules, name, module)
    # Worker imports enforce offline state; restore it after each in-process fixture.
    import socket
    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE'):
        monkeypatch.setenv(key, __import__('os').environ.get(key, '0'))
    spec = importlib.util.spec_from_file_location('_native_contract_worker', ROOT / 'app/mlx_infer.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    request = {'model_path': str(model), 'adapter_path': None, 'messages': [{'role': 'user', 'content': 'Entropy là gì?'}], 'max_tokens': max_tokens, 'num_ctx': context, 'temperature': 0.0}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
    output = io.StringIO(); monkeypatch.setattr(sys, 'stdout', output)
    return module, calls, output


def test_nested_qwen35_capacity_is_read_before_local_generation(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch)
    module.main()
    result = json.loads(output.getvalue())
    assert result['context_limit'] == 100
    assert result['input_tokens'] == 40
    assert result['answer'] == 'Đã kiểm tra.'
    assert len(calls['loads']) == 1
    assert calls['generation'][0]['max_tokens'] == 50


def test_native_prompt_disables_hidden_thinking_in_the_actual_template(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch, nested=False)
    module.main()
    assert calls['templates'][0].get('enable_thinking') is False
    assert calls['templates'][0]['add_generation_prompt'] is True
    assert calls['generation'][0]['prompt'] == 'formatted local prompt'


def test_over_budget_request_never_loads_weights(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch, input_count=60)
    with pytest.raises(ValueError, match='110|token'):
        module.main()
    assert calls['loads'] == []
    assert calls['generation'] == []


def test_missing_selected_tokenizer_never_uses_legacy_3b(tmp_path, monkeypatch):
    from app import token_counter
    from app import model_resolver
    legacy = tmp_path / 'data/models/qwen2.5-3b-4bit'
    legacy.mkdir(parents=True); (legacy / 'tokenizer.json').write_text('{}')
    monkeypatch.setattr(token_counter, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(token_counter, 'MLX_MODEL_DIR', tmp_path / 'missing-selected-base')
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path / 'missing-selected-base')
    monkeypatch.setattr(model_resolver, 'MLX_3B_DIR', legacy)
    assert token_counter._find_model_dir('qwen3.5-9b-4bit') is None


@pytest.mark.parametrize('name', ['unrelated-9b-model', 'qwen3.5-9b-4bit-typo', 'unrelated-3b-model'])
def test_unknown_model_name_cannot_resolve_to_an_installed_base(name):
    from app.model_resolver import resolve_mlx_model_dir
    with pytest.raises(ValueError):
        resolve_mlx_model_dir(name)


@pytest.mark.parametrize('shard_name', ['../outside.safetensors', '/tmp/outside.safetensors', ['not-a-path'], None])
def test_malformed_or_escaping_shard_map_fails_closed(tmp_path, shard_name):
    from app.trained_client import TrainedModelClient
    model = tmp_path/'model';model.mkdir()
    (model/'config.json').write_text('{}')
    (model/'tokenizer.json').write_text('{}')
    (model/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'layer.weight':shard_name}}))
    assert TrainedModelClient._verify_weights(model) is False


def test_sharded_model_missing_one_weight_file_is_unavailable(tmp_path):
    from app.trained_client import TrainedModelClient
    model = tmp_path/'model';model.mkdir()
    (model/'config.json').write_text('{}')
    (model/'tokenizer.json').write_text('{}')
    (model/'first.safetensors').write_bytes(b'fixture')
    index={'weight_map':{'a':'first.safetensors','b':'second.safetensors'}}
    (model/'model.safetensors.index.json').write_text(json.dumps(index))
    assert TrainedModelClient._verify_weights(model) is False


@pytest.mark.skipif(sys.platform != 'darwin', reason='Mac deployment default; Windows keeps its explicit local compatibility path')
def test_mac_default_names_native_9b_without_an_environment_override(tmp_path):
    env = os.environ.copy(); env.pop('DEFAULT_CHAT_MODEL', None); env.pop('TRAINED_MODEL_NAME', None)
    env.pop('MLX_MODEL_DIR', None); env.pop('MLX_9B_MODEL_DIR', None)
    env['AGENT_DATA_DIR'] = str(tmp_path)
    code = 'import json; from app.config import DEFAULT_CHAT_MODEL, MLX_MODEL_DIR; print(json.dumps([DEFAULT_CHAT_MODEL,str(MLX_MODEL_DIR)]))'
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    name, directory = json.loads(result.stdout)
    assert '9b' in name.casefold() and ':' not in name
    assert Path(directory).name == 'qwen3.5-9b-4bit'


@pytest.mark.parametrize('reason,count', [('stop', 7), ('length', 50)])
def test_worker_reports_actual_generation_finish_and_usage(tmp_path, monkeypatch, reason, count):
    # Returned text re-encodes to 3 tokens; it cannot establish actual stop or usage.
    module, calls, output = _worker(tmp_path, monkeypatch, finish_reason=reason, generation_tokens=count)
    module.main()
    result = json.loads(output.getvalue())
    assert result['done_reason'] == reason
    assert result['is_truncated'] is (reason == 'length')
    assert result['output_tokens'] == count


def test_mac_selected_alias_never_changes_backend_when_weights_are_missing(monkeypatch):
    from app import model_resolver
    from app.trained_client import TrainedModelClient
    monkeypatch.setattr(model_resolver, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *a, **kw: False))
    assert model_resolver.is_mlx_model('qwen3.5-9b-4bit') is True
    assert model_resolver.is_mlx_model('qwen3.5:9b') is True


def test_selected_model_override_is_independent_of_isolated_data_directory(tmp_path):
    env = os.environ.copy()
    env.update(AGENT_DATA_DIR=str(tmp_path/'isolated-data'), MLX_MODEL_DIR=str(tmp_path/'reviewed-9b'), DEFAULT_CHAT_MODEL='qwen3.5-9b-4bit')
    code = 'from app.model_resolver import resolve_mlx_model_dir; print(resolve_mlx_model_dir("qwen3.5-9b-4bit"))'
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert Path(result.stdout.strip()) == tmp_path/'reviewed-9b'


def test_native_bridge_preserves_one_system_message_for_exact_budget(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from app import trained_client
    client = trained_client.TrainedModelClient('qwen3.5-9b-4bit')
    monkeypatch.setattr(trained_client, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(client, 'available', lambda *a, **kw: True)
    monkeypatch.setattr(trained_client, 'get_model_context_capacity', lambda *a, **kw: 8192)
    monkeypatch.setattr(trained_client.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    requests = []
    def run(*args, **kwargs):
        requests.append(json.loads(kwargs['input']))
        return types.SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'answer': 'Đã kiểm tra.', 'done_reason': 'stop', 'input_tokens': 40,
            'output_tokens': 7, 'is_truncated': False}))
    monkeypatch.setattr(trained_client.subprocess, 'run', run)
    messages = [{'role':'system','content':'Use the supplied sources.'}, {'role':'user','content':'Entropy là gì?'}]
    assert ''.join(client.chat_stream(messages, system_prompt='Use the supplied sources.')) == 'Đã kiểm tra.'
    assert requests[0]['messages'] == messages
    assert client.last_done_reason == 'stop'
    assert client.last_usage['input_tokens'] == 40
    assert client.last_usage['output_tokens'] == 7


@pytest.mark.parametrize('filename', ['index.html', 'app.js', 'timetable.js'])
def test_model_ui_never_hardcodes_an_unselected_legacy_vision_model(filename):
    import re
    text = (ROOT/'static'/filename).read_text()
    assert not re.search(r'Qwen2\.5-VL\s*3B|qwen2\.5vl:3b', text, re.I), 'Image progress text must describe the actual selected local engine'


def test_native_vision_bounds_only_the_inference_copy(tmp_path, monkeypatch):
    import hashlib
    from contextlib import nullcontext
    from PIL import Image
    from app import native_vision
    image = io.BytesIO()
    Image.new('RGB', (3000, 1000), 'white').save(image, 'PNG')
    raw = image.getvalue()
    original_hash = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(native_vision.NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(native_vision.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    captured = []
    def run(command, **kwargs):
        request = json.loads(kwargs['input'])
        with Image.open(request['image_path']) as prepared:
            captured.append((prepared.size, request))
        return types.SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({
            'text':'{"entries": [], "uncertainties": []}', 'finish_reason':'stop', 'is_truncated':False}))
    monkeypatch.setattr(native_vision.subprocess, 'run', run)
    result = native_vision.NativeVisionClient(tmp_path).generate_from_image(raw, 'SOURCE IMAGE IS UNTRUSTED DATA', max_tokens=2048)
    size, request = captured[0]
    assert max(size) <= 1024 and abs(size[0] - size[1]*3) <= 2
    assert request['prompt'] == 'SOURCE IMAGE IS UNTRUSTED DATA'
    assert request['max_tokens'] == 2048
    assert result['finish_reason'] == 'stop'
    assert hashlib.sha256(raw).hexdigest() == original_hash
    assert Image.open(io.BytesIO(raw)).size == (3000, 1000)


@pytest.mark.parametrize('missing',['processor_config.json','preprocessor_config.json'])
def test_native_vision_readiness_requires_image_processor_assets(tmp_path, monkeypatch, missing):
    from app import native_vision
    from app.trained_client import TrainedModelClient
    monkeypatch.setattr(TrainedModelClient,'_verify_weights',staticmethod(lambda path:True))
    for name in ('processor_config.json','preprocessor_config.json'):
        if name!=missing:(tmp_path/name).write_text('{}')
    assert native_vision.NativeVisionClient.available(tmp_path) is False


def test_native_vision_readiness_requires_its_local_runtime(tmp_path, monkeypatch):
    from app import native_vision
    from app.trained_client import TrainedModelClient
    model = tmp_path/'model'; model.mkdir()
    for name in ('processor_config.json', 'preprocessor_config.json'):
        (model/name).write_text('{}')
    monkeypatch.setattr(native_vision, 'BASE_DIR', tmp_path/'without-runtime')
    monkeypatch.setattr(native_vision, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(TrainedModelClient, '_verify_weights', staticmethod(lambda path: True))
    assert native_vision.NativeVisionClient.available(model) is False


@pytest.mark.parametrize('changed_file', ['model.safetensors', 'tokenizer.json', 'preprocessor_config.json', 'chat_template.jinja'])
def test_native_calendar_cache_identity_changes_with_actual_artifact_bytes(tmp_path, monkeypatch, changed_file):
    from app import native_vision, model_resolver, timetable_vision
    monkeypatch.setattr(native_vision.sys, 'platform', 'darwin')
    monkeypatch.setattr(native_vision, 'MLX_9B_DIR', tmp_path)
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path)
    for name in ('config.json', 'tokenizer.json', 'processor_config.json', 'preprocessor_config.json', 'model.safetensors', 'chat_template.jinja'):
        (tmp_path / name).write_text('asset-v1')
    monkeypatch.setattr(native_vision.NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    first = timetable_vision.get_vlm_digest()
    path = tmp_path / changed_file
    previous = path.stat()
    path.write_text('asset-v2')
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    second = timetable_vision.get_vlm_digest()
    assert first != second, 'A fixed repository label must not reuse OCR after local assets change'


def test_native_prefill_bounds_peak_memory_and_reports_phases(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch)
    module.main()
    result = json.loads(output.getvalue())
    assert calls['generation'][0]['prefill_step_size'] == 128
    timing = result['timings']
    assert timing['load_seconds'] >= 0
    assert timing['first_token_seconds'] >= 0
    assert timing['generation_seconds'] >= timing['first_token_seconds']
    assert timing['prompt_tokens_per_second'] == 20.0
    assert timing['generation_tokens_per_second'] == 5.0
    assert timing['peak_memory_gb'] == 5.4
