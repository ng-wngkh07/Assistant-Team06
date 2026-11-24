"""Native model identity, exact template budgets and offline-worker contracts."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess  # nosec B404
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _worker(tmp_path, monkeypatch, *, nested=True, input_count=40, max_tokens=50, context=100, finish_reason="stop", generation_tokens=7, response_schema=None):
    model = tmp_path / 'base'
    model.mkdir()
    config = {'max_position_embeddings': 262144}
    base = {
        'model_type': 'qwen3_5', 'architectures': ['Qwen3_5ForConditionalGeneration'],
        'text_config': {'model_type':'qwen3_5_text','hidden_size':4096,
                        'num_hidden_layers':32,'num_attention_heads':16},
        'quantization': {'bits':4,'group_size':64,'mode':'affine'},
    }
    (base['text_config'] if nested else base).update(config)
    (model / 'config.json').write_text(json.dumps(base))
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
            assert Path(path).resolve() == model.resolve()  # nosec B101
            assert kwargs.get('local_files_only') is True  # nosec B101
            assert kwargs.get('trust_remote_code') is False  # nosec B101
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
    sampling.make_logits_processors = lambda **kw: [kw]
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
    if response_schema is not None:
        request['response_schema'] = response_schema
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
    output = io.StringIO(); monkeypatch.setattr(sys, 'stdout', output)
    return module, calls, output


def test_structured_native_request_installs_schema_constraint_before_generation(tmp_path, monkeypatch):
    schema = {'type': 'object', 'properties': {'checks': {'type': 'array'}}, 'required': ['checks']}
    module, calls, output = _worker(tmp_path, monkeypatch, response_schema=schema)
    observed = []
    constraint = object()
    monkeypatch.setitem(sys.modules, 'mlx_vlm.structured', types.SimpleNamespace(
        build_json_schema_logits_processor=lambda tokenizer, value: observed.append(value) or constraint))
    module.main()
    assert observed == [schema]  # nosec B101
    processors = calls['generation'][0]['logits_processors']
    assert processors[-1] is constraint  # nosec B101
    for p in processors:
        if isinstance(p, dict):
            assert p.get('presence_penalty', 0.0) == 0.0  # nosec B101
    payload = json.loads(output.getvalue())
    assert payload['generation_profile']['presence_penalty'] == 0.0  # nosec B101


def test_nested_qwen35_capacity_is_read_before_local_generation(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch)
    module.main()
    result = json.loads(output.getvalue())
    assert result['context_limit'] == 100  # nosec B101
    assert result['input_tokens'] == 40  # nosec B101
    assert result['answer'] == 'Đã kiểm tra.'  # nosec B101
    assert len(calls['loads']) == 1  # nosec B101
    assert calls['generation'][0]['max_tokens'] == 50  # nosec B101


def test_native_prompt_disables_hidden_thinking_in_the_actual_template(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch, nested=False)
    module.main()
    assert calls['templates'][0].get('enable_thinking') is False  # nosec B101
    assert calls['templates'][0]['add_generation_prompt'] is True  # nosec B101
    assert calls['generation'][0]['prompt'] == 'formatted local prompt'  # nosec B101


def test_native_qa_keeps_deterministic_sampling_and_uses_anti_loop_profile(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch)
    module.main()
    generation = calls['generation'][0]
    assert generation['sampler']['temp'] == 0.0  # nosec B101
    assert generation['logits_processors'][0] == {  # nosec B101
        'presence_penalty': 1.5, 'presence_context_size': 2048,
    }
    assert json.loads(output.getvalue())['done_reason'] == 'stop'  # nosec B101


def test_over_budget_request_never_loads_weights(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch, input_count=60)
    with pytest.raises(ValueError, match='110|token'):
        module.main()
    assert calls['loads'] == []  # nosec B101
    assert calls['generation'] == []  # nosec B101


def test_missing_selected_tokenizer_never_uses_legacy_3b(tmp_path, monkeypatch):
    from app import token_counter
    from app import model_resolver
    legacy = tmp_path / 'data/models/qwen2.5-3b-4bit'
    legacy.mkdir(parents=True); (legacy / 'tokenizer.json').write_text('{}')
    monkeypatch.setattr(token_counter, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(token_counter, 'MLX_MODEL_DIR', tmp_path / 'missing-selected-base')
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path / 'missing-selected-base')
    assert token_counter._find_model_dir('qwen3.5-9b-4bit') is None  # nosec B101


@pytest.mark.parametrize('name', ['unrelated-9b-model', 'qwen3.5-9b-4bit-typo', 'unrelated-3b-model'])
def test_unknown_model_name_cannot_resolve_to_an_installed_base(name):
    from app.model_resolver import resolve_mlx_model_dir
    with pytest.raises(ValueError):
        resolve_mlx_model_dir(name)


@pytest.mark.parametrize('shard_name', ['../outside.safetensors', '/tmp/outside.safetensors', ['not-a-path'], None])  # nosec B108
def test_malformed_or_escaping_shard_map_fails_closed(tmp_path, shard_name):
    from app.trained_client import TrainedModelClient
    model = tmp_path/'model';model.mkdir()
    (model/'config.json').write_text('{}')
    (model/'tokenizer.json').write_text('{}')
    (model/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'layer.weight':shard_name}}))
    assert TrainedModelClient._verify_weights(model) is False  # nosec B101


def test_sharded_model_missing_one_weight_file_is_unavailable(tmp_path):
    from app.trained_client import TrainedModelClient
    model = tmp_path/'model';model.mkdir()
    (model/'config.json').write_text('{}')
    (model/'tokenizer.json').write_text('{}')
    (model/'first.safetensors').write_bytes(b'fixture')
    index={'weight_map':{'a':'first.safetensors','b':'second.safetensors'}}
    (model/'model.safetensors.index.json').write_text(json.dumps(index))
    assert TrainedModelClient._verify_weights(model) is False  # nosec B101


@pytest.mark.skipif(sys.platform != 'darwin', reason='Mac deployment default; Windows keeps its explicit local compatibility path')
def test_mac_default_names_native_9b_without_an_environment_override(tmp_path):
    env = os.environ.copy(); env.pop('DEFAULT_CHAT_MODEL', None); env.pop('TRAINED_MODEL_NAME', None)
    env.pop('MLX_MODEL_DIR', None); env.pop('MLX_9B_MODEL_DIR', None)
    env['AGENT_DATA_DIR'] = str(tmp_path)
    code = 'import json; from app.config import DEFAULT_CHAT_MODEL, MLX_MODEL_DIR; print(json.dumps([DEFAULT_CHAT_MODEL,str(MLX_MODEL_DIR)]))'
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)  # nosec B603
    name, directory = json.loads(result.stdout)
    assert '9b' in name.casefold() and ':' not in name  # nosec B101
    assert Path(directory).name == 'qwen3.5-9b-4bit'  # nosec B101


@pytest.mark.parametrize('reason,count', [('stop', 7), ('length', 50), (None, 7)])
def test_worker_reports_actual_generation_finish_and_usage(tmp_path, monkeypatch, reason, count):
    # Returned text re-encodes to 3 tokens; it cannot establish actual stop or usage.
    module, calls, output = _worker(tmp_path, monkeypatch, finish_reason=reason, generation_tokens=count)
    module.main()
    result = json.loads(output.getvalue())
    assert result['done_reason'] == reason  # nosec B101
    assert result['is_truncated'] is (reason == 'length')  # nosec B101
    assert result['output_tokens'] == count  # nosec B101


def test_mac_selected_alias_never_changes_backend_when_weights_are_missing(monkeypatch):
    from app import model_resolver
    from app.trained_client import TrainedModelClient
    monkeypatch.setattr(model_resolver, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(TrainedModelClient, 'available', staticmethod(lambda *a, **kw: False))
    assert model_resolver.is_mlx_model('qwen3.5-9b-4bit') is True  # nosec B101
    assert model_resolver.is_mlx_model('qwen3.5:9b') is True  # nosec B101


def test_selected_model_override_is_independent_of_isolated_data_directory(tmp_path):
    env = os.environ.copy()
    env.update(AGENT_DATA_DIR=str(tmp_path/'isolated-data'), MLX_MODEL_DIR=str(tmp_path/'reviewed-9b'), DEFAULT_CHAT_MODEL='qwen3.5-9b-4bit')
    code = 'from app.model_resolver import resolve_mlx_model_dir; print(resolve_mlx_model_dir("qwen3.5-9b-4bit"))'
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)  # nosec B603
    assert Path(result.stdout.strip()) == tmp_path/'reviewed-9b'  # nosec B101


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
    assert ''.join(client.chat_stream(messages, system_prompt='Use the supplied sources.')) == 'Đã kiểm tra.'  # nosec B101
    assert requests[0]['messages'] == messages  # nosec B101
    assert client.last_done_reason == 'stop'  # nosec B101
    assert client.last_usage['input_tokens'] == 40  # nosec B101
    assert client.last_usage['output_tokens'] == 7  # nosec B101


@pytest.mark.parametrize('filename', ['index.html', 'app.js', 'timetable.js'])
def test_model_ui_never_hardcodes_an_unselected_legacy_vision_model(filename):
    import re
    text = (ROOT/'static'/filename).read_text()
    assert not re.search(r'Qwen2\.5-VL\s*3B|qwen2\.5vl:3b', text, re.I), 'Image progress text must describe the actual selected local engine'  # nosec B101


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
    assert max(size) <= 1024 and abs(size[0] - size[1]*3) <= 2  # nosec B101
    assert request['prompt'] == 'SOURCE IMAGE IS UNTRUSTED DATA'  # nosec B101
    assert request['max_tokens'] == 2048  # nosec B101
    assert result['finish_reason'] == 'stop'  # nosec B101
    assert hashlib.sha256(raw).hexdigest() == original_hash  # nosec B101
    assert Image.open(io.BytesIO(raw)).size == (3000, 1000)  # nosec B101


@pytest.mark.parametrize('missing',['processor_config.json','preprocessor_config.json'])
def test_native_vision_readiness_requires_image_processor_assets(tmp_path, monkeypatch, missing):
    from app import native_vision
    from app.trained_client import TrainedModelClient
    monkeypatch.setattr(TrainedModelClient,'_verify_weights',staticmethod(lambda path:True))
    for name in ('processor_config.json','preprocessor_config.json'):
        if name!=missing:(tmp_path/name).write_text('{}')
    assert native_vision.NativeVisionClient.available(tmp_path) is False  # nosec B101


def test_native_vision_readiness_requires_its_local_runtime(tmp_path, monkeypatch):
    from app import native_vision
    from app.trained_client import TrainedModelClient
    model = tmp_path/'model'; model.mkdir()
    for name in ('processor_config.json', 'preprocessor_config.json'):
        (model/name).write_text('{}')
    monkeypatch.setattr(native_vision, 'BASE_DIR', tmp_path/'without-runtime')
    monkeypatch.setattr(native_vision, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(TrainedModelClient, '_verify_weights', staticmethod(lambda path: True))
    assert native_vision.NativeVisionClient.available(model) is False  # nosec B101


@pytest.mark.parametrize('changed_file', ['model.safetensors', 'tokenizer.json', 'preprocessor_config.json', 'chat_template.jinja'])
def test_native_calendar_cache_identity_changes_with_actual_artifact_bytes(tmp_path, monkeypatch, changed_file, current_native_config):
    from app import native_vision, model_resolver, timetable_vision
    monkeypatch.setattr(native_vision.sys, 'platform', 'darwin')
    monkeypatch.setattr(native_vision, 'MLX_9B_DIR', tmp_path)
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path)
    for name in ('config.json', 'tokenizer.json', 'processor_config.json', 'preprocessor_config.json', 'model.safetensors', 'chat_template.jinja'):
        (tmp_path / name).write_text('asset-v1')
    (tmp_path/'config.json').write_text(json.dumps(current_native_config))
    monkeypatch.setattr(native_vision.NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    first = timetable_vision.get_vlm_digest()
    path = tmp_path / changed_file
    previous = path.stat()
    path.write_text('asset-v2')
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    second = timetable_vision.get_vlm_digest()
    assert first != second, 'A fixed repository label must not reuse OCR after local assets change'  # nosec B101


def test_native_prefill_bounds_peak_memory_and_reports_phases(tmp_path, monkeypatch):
    module, calls, output = _worker(tmp_path, monkeypatch)
    module.main()
    result = json.loads(output.getvalue())
    assert calls['generation'][0]['prefill_step_size'] == 128  # nosec B101
    timing = result['timings']
    assert timing['load_seconds'] >= 0  # nosec B101
    assert timing['first_token_seconds'] >= 0  # nosec B101
    assert timing['generation_seconds'] >= timing['first_token_seconds']  # nosec B101
    assert timing['prompt_tokens_per_second'] == 20.0  # nosec B101
    assert timing['generation_tokens_per_second'] == 5.0  # nosec B101
    assert timing['peak_memory_gb'] == 5.4  # nosec B101


@pytest.mark.parametrize('mode', ['accepted', 'absent', 'wrong_path', 'ordinary_qa'])
def test_native_worker_requires_acceptance_path_and_exact_scope(tmp_path, monkeypatch, mode):
    from app import practice_adapter
    from app.practice import practice_review_schema
    module, calls, output = _worker(tmp_path, monkeypatch, max_tokens=1000, context=8192)
    adapter = tmp_path / 'adapter'
    adapter.mkdir()
    (adapter / 'adapters.safetensors').write_bytes(b'synthetic fixture, never real model loaded')
    request = json.loads(sys.stdin.getvalue())
    item = dict(index=1, source='Synthetic source.', question='Synthetic question?', choices=dict(A='one', B='two', C='three', D='four'), proposed_answer='A', explanation='Synthetic explanation.', evidence_quote='Synthetic source.')
    request.update(adapter_path=str(adapter), messages=[{'role': 'system', 'content': practice_adapter.SYSTEM_PROMPT}, {'role': 'user', 'content': practice_adapter.build_compact_review_prompt(item)}], response_schema=practice_review_schema(1, None))
    accepted = {'path': str(adapter if mode != 'wrong_path' else tmp_path / 'another'), 'sha256': 'fixture', 'scope': 'single_question_practice_review'}
    approvals = []
    def load(model_path, require_active=True):
        approvals.append(require_active)
        return None if mode == 'absent' else accepted
    monkeypatch.setattr(practice_adapter, 'load_practice_adapter', load)
    if mode == 'ordinary_qa': request['messages'][1]['content'] = 'Ordinary QA'
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
    monkeypatch.setitem(sys.modules, 'mlx_vlm.structured', types.SimpleNamespace(build_json_schema_logits_processor=lambda *args: object()))
    if mode != 'accepted':
        with pytest.raises(ValueError, match='nghiệm thu'): module.main()
        assert calls['loads'] == []  # nosec B101
    else:
        module.main()
        result = json.loads(output.getvalue())
        assert calls['loads'][0][1]['adapter_path'] == str(adapter)  # nosec B101
        assert result['adapter_sha256'] == 'fixture'  # nosec B101
        assert result['adapter_scope'] == 'single_question_practice_review'  # nosec B101
    assert approvals == [False]  # nosec B101


@pytest.mark.parametrize('mode', ['accepted', 'legacy_acceptance', 'altered_prompt'])
def test_native_grounded_adapter_cannot_cross_review_profiles(tmp_path, monkeypatch, mode):
    from app import practice_adapter
    from app.practice_proof import native_grounded_review_prompt, native_grounded_review_schema
    module, calls, output = _worker(tmp_path, monkeypatch, max_tokens=1000, context=8192)
    adapter = tmp_path / 'adapter'
    adapter.mkdir()
    (adapter / 'adapters.safetensors').write_bytes(b'synthetic fixture, never real model loaded')
    value = json.loads(sys.stdin.getvalue())
    item = dict(index=1, source='Synthetic source.', question='Synthetic question?', choices=dict(A='one', B='two', C='three', D='four'), proposed_answer='A', explanation='Synthetic explanation.', evidence_quote='Synthetic source.')
    prompt = native_grounded_review_prompt(item)
    if mode == 'altered_prompt':
        altered = prompt.replace('Use ONLY source as evidence.', 'Use background knowledge as evidence.')
        assert altered != prompt  # nosec B101
        prompt = altered
    value.update(adapter_path=str(adapter), seed=42, temperature=0, messages=[{'role': 'system', 'content': practice_adapter.SYSTEM_PROMPT}, {'role': 'user', 'content': prompt}], response_schema=native_grounded_review_schema(1, None))
    profile = practice_adapter.COMPACT_REVIEW_PROFILE if mode == 'legacy_acceptance' else practice_adapter.GROUNDED_REVIEW_PROFILE
    accepted = dict(path=str(adapter), sha256='fixture', scope='single_question_practice_review', review_profile=profile)
    monkeypatch.setattr(practice_adapter, 'load_practice_adapter', lambda *args, **kwargs: accepted)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(value)))
    monkeypatch.setitem(sys.modules, 'mlx_vlm.structured', types.SimpleNamespace(build_json_schema_logits_processor=lambda *args: object()))
    if mode != 'accepted':
        with pytest.raises(ValueError, match='nghiệm thu'): module.main()
        assert calls['loads'] == []  # nosec B101
    else:
        module.main()
        result = json.loads(output.getvalue())
        assert result['adapter_loaded'] is True  # nosec B101
        assert result['adapter_review_profile'] == practice_adapter.GROUNDED_REVIEW_PROFILE  # nosec B101
        assert calls['loads'][0][1]['adapter_path'] == str(adapter)  # nosec B101


def test_native_vision_worker_applies_response_schema_and_repetition_penalty(tmp_path, monkeypatch):
    """app.mlx_vision installs JSON schema logits processor and repetition penalty into mlx_vlm.generate."""
    model = tmp_path / 'vision_model'
    model.mkdir()
    (model / 'config.json').write_text('{}', encoding='utf-8')
    img_file = tmp_path / 'page.png'
    img_file.write_bytes(b'png-bytes')

    observed = {'generate': [], 'schema': []}
    tok = object()
    processor = types.SimpleNamespace(tokenizer=tok)
    schema_proc = object()

    def load(path, **kwargs):
        return object(), processor

    def generate(mdl, proc, **kwargs):
        observed['generate'].append((proc, kwargs))
        return types.SimpleNamespace(
            text='{"text":"Quote \\"x\\" and \\\\frac{a}{b}"}',
            finish_reason='stop',
            generation_tokens=12,
            prompt_tokens=20,
        )

    vlm_mod = types.ModuleType('mlx_vlm')
    vlm_mod.load = load
    vlm_mod.generate = generate
    prompt_mod = types.ModuleType('mlx_vlm.prompt_utils')
    prompt_mod.apply_chat_template = lambda proc, cfg, text, **kw: f'templated:{text}'
    struct_mod = types.ModuleType('mlx_vlm.structured')
    struct_mod.build_json_schema_logits_processor = (
        lambda active_tok, schema: observed['schema'].append((active_tok, schema)) or schema_proc
    )
    core = types.ModuleType('mlx.core')
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType('mlx')
    mlx.core = core
    for mod_name, mod_obj in [
        ('mlx_vlm', vlm_mod),
        ('mlx_vlm.prompt_utils', prompt_mod),
        ('mlx_vlm.structured', struct_mod),
        ('mlx', mlx),
        ('mlx.core', core),
    ]:
        monkeypatch.setitem(sys.modules, mod_name, mod_obj)

    import socket
    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    spec = importlib.util.spec_from_file_location('_native_vision_worker', ROOT / 'app/mlx_vision.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    schema = {
        'type': 'object',
        'properties': {'text': {'type': 'string'}},
        'required': ['text'],
        'additionalProperties': False,
    }
    req = {
        'model_path': str(model),
        'image_path': str(img_file),
        'prompt': 'OCR page',
        'max_tokens': 2048,
        'temperature': 0.0,
        'response_schema': schema,
        'repetition_penalty': 1.05,
        'repetition_context_size': 64,
    }
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(req)))
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    module.main()

    result = json.loads(out.getvalue())
    assert result['finish_reason'] == 'stop'  # nosec B101
    assert observed['schema'] == [(tok, schema)]  # nosec B101
    gen_proc, gen_kw = observed['generate'][0]
    assert gen_proc is processor  # nosec B101
    assert gen_kw['logits_processors'] == [schema_proc]  # nosec B101
    assert gen_kw['repetition_penalty'] == 1.05  # nosec B101
    assert gen_kw['repetition_context_size'] == 64  # nosec B101


def test_native_ocr_passes_transcript_schema_and_retries_length_up_to_4096(tmp_path, monkeypatch):
    """Dense native OCR pages pass JSON schema + repetition penalty and retry once up to 4096 on length truncation."""
    import hashlib
    from contextlib import nullcontext
    from PIL import Image, ImageDraw
    from app import vision_ocr
    from app.native_vision import NativeVisionClient

    img = Image.new('RGB', (120, 60), 'white')
    ImageDraw.Draw(img).text((8, 8), 'GDP = C + I + G', fill='black')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    raw = buf.getvalue()

    monkeypatch.setattr(vision_ocr, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(vision_ocr, 'CACHE_DIR', tmp_path / 'ocr_cache')
    monkeypatch.setattr(vision_ocr.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    monkeypatch.setattr(NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeVisionClient, 'get_identity', lambda self: 'native-ocr-digest')

    calls = []

    def fake_gen(self, image_bytes, prompt, max_tokens=2048, response_schema=None, repetition_penalty=None, repetition_context_size=None, **kw):
        calls.append({
            'max_tokens': max_tokens,
            'response_schema': response_schema,
            'repetition_penalty': repetition_penalty,
            'repetition_context_size': repetition_context_size,
        })
        if max_tokens <= 2048:
            return {'text': '{"text":"truncated', 'finish_reason': 'length', 'is_truncated': True}
        return {
            'text': json.dumps({'text': 'Quote "GDP" and \\frac{Y}{P}'}),
            'finish_reason': 'stop',
            'is_truncated': False,
        }

    monkeypatch.setattr(NativeVisionClient, 'generate_from_image', fake_gen)
    mgr = vision_ocr.VisionOCRManager(cache_dir=tmp_path / 'ocr_cache')
    rec = mgr.ocr_image_bytes(raw, hashlib.sha256(raw).hexdigest(), 'dense.pdf', 1)

    assert len(calls) == 2  # nosec B101
    assert calls[0]['max_tokens'] == 2048  # nosec B101
    assert calls[1]['max_tokens'] == 4096  # nosec B101
    assert calls[0]['response_schema'] == vision_ocr.TRANSCRIPT_JSON_SCHEMA  # nosec B101
    assert calls[0]['repetition_penalty'] == 1.05  # nosec B101
    assert calls[0]['repetition_context_size'] == 64  # nosec B101
    assert rec['raw_text'] == 'Quote "GDP" and \\frac{Y}{P}'  # nosec B101


def test_native_vision_ocr_retries_4096_when_native_client_raises_truncation_value_error(
    tmp_path, monkeypatch, current_native_config
):
    """Real NativeVisionClient raises ValueError on length=2048; VisionOCRManager must still retry at 4096."""
    import hashlib
    import io
    from contextlib import nullcontext
    from PIL import Image
    from app import native_vision, vision_ocr

    img_buf = io.BytesIO()
    Image.new('RGB', (400, 300), 'white').save(img_buf, 'PNG')
    raw = img_buf.getvalue()
    # Add non-white pixel so blank-page check runs inference
    img = Image.open(io.BytesIO(raw))
    img.putpixel((10, 10), (0, 0, 0))
    out_buf = io.BytesIO()
    img.save(out_buf, 'PNG')
    raw = out_buf.getvalue()

    model_dir = tmp_path / 'vision-model'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps(current_native_config), encoding='utf-8')
    (model_dir / 'processor_config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'preprocessor_config.json').write_text('{}', encoding='utf-8')

    monkeypatch.setattr(vision_ocr, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(vision_ocr, 'CACHE_DIR', tmp_path / 'ocr_cache')
    monkeypatch.setattr(vision_ocr.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    monkeypatch.setattr(native_vision, 'MLX_9B_DIR', model_dir)
    monkeypatch.setattr(native_vision.NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(native_vision.NativeVisionClient, 'get_identity', lambda self: 'native-ocr-digest')
    monkeypatch.setattr(native_vision.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())

    calls = []

    def fake_request_json(payload, **kwargs):
        calls.append(payload.get('max_tokens'))
        if payload.get('max_tokens', 2048) <= 2048:
            return {'text': '{"text":"truncated', 'finish_reason': 'length', 'is_truncated': True}
        return {'text': json.dumps({'text': 'Recovered full dense page'}), 'finish_reason': 'stop', 'is_truncated': False}

    monkeypatch.setattr(native_vision._VISION_STREAM_WORKER, 'request_json', fake_request_json)
    mgr = vision_ocr.VisionOCRManager(cache_dir=tmp_path / 'ocr_cache')
    rec = mgr.ocr_image_bytes(raw, hashlib.sha256(raw).hexdigest(), 'dense_real.pdf', 1)

    assert calls == [2048, 4096]  # nosec B101
    assert rec['raw_text'] == 'Recovered full dense page'  # nosec B101
    assert rec['status'] == 'CAPTURED_PENDING_REVIEW'  # nosec B101


def test_native_vision_status_identity_caches_stat_signature_and_detects_mutation(
    tmp_path, monkeypatch, current_native_config
):
    """NativeVisionClient.get_status_identity reuses stat-bound digest while get_identity keeps byte checks."""
    from app import native_vision
    from app.trained_client import TrainedModelClient

    model_dir = tmp_path / 'vision-model'
    model_dir.mkdir()
    shard1 = model_dir / 'model-00001-of-00002.safetensors'
    shard2 = model_dir / 'model-00002-of-00002.safetensors'
    index_data = {
        'weight_map': {
            'a': shard1.name,
            'b': shard2.name,
        }
    }
    (model_dir / 'config.json').write_text(json.dumps(current_native_config), encoding='utf-8')
    (model_dir / 'model.safetensors.index.json').write_text(json.dumps(index_data), encoding='utf-8')
    (model_dir / 'tokenizer.json').write_text('{}', encoding='utf-8')
    (model_dir / 'processor_config.json').write_text('{}', encoding='utf-8')
    (model_dir / 'preprocessor_config.json').write_text('{}', encoding='utf-8')
    shard1.write_bytes(b'SHARD1_V1_' + b'\x01' * 1024)
    shard2.write_bytes(b'SHARD2_V1_' + b'\x02' * 1024)

    monkeypatch.setattr(TrainedModelClient, '_verify_weights', staticmethod(lambda path: True))
    native_vision.NativeVisionClient.clear_status_identity_cache()
    client = native_vision.NativeVisionClient(model_dir=model_dir)

    shard_reads = 0
    orig_open = Path.open

    def counted_open(self, mode='r', *args, **kwargs):
        nonlocal shard_reads
        handle = orig_open(self, mode, *args, **kwargs)
        if 'rb' in str(mode) and self.resolve() == shard1.resolve():
            shard_reads += 1
        return handle

    monkeypatch.setattr(Path, 'open', counted_open)

    try:
        cold = client.get_status_identity()
        assert cold.startswith('mlx-vision-sha256:')  # nosec B101
        assert shard_reads == 1  # nosec B101

        for _ in range(3):
            assert client.get_status_identity() == cold  # nosec B101
        assert shard_reads == 1  # nosec B101

        # Explicit get_identity() always reads actual bytes.
        assert client.get_identity() == cold  # nosec B101
        assert shard_reads == 2  # nosec B101

        # Replacing shard1 with identical byte length must invalidate stat cache immediately.
        staging = tmp_path / 'shard1.tmp'
        staging.write_bytes(b'SHARD1_V2_' + b'\x03' * 1024)
        staging.replace(shard1)

        updated = client.get_status_identity()
        assert updated != cold  # nosec B101
        assert shard_reads == 3  # nosec B101
        assert client.get_status_identity() == updated  # nosec B101
        assert shard_reads == 3  # nosec B101
    finally:
        native_vision.NativeVisionClient.clear_status_identity_cache()


def test_ocr_image_bytes_reuses_preverified_model_digest_via_status_identity(tmp_path, monkeypatch):
    """Multi-page OCR passes preverified digest to reuse stat-bound verification without extra full-shard hashes."""
    import hashlib
    from contextlib import nullcontext
    from PIL import Image, ImageDraw
    from app import vision_ocr
    from app.native_vision import NativeVisionClient

    img = Image.new('RGB', (100, 50), 'white')
    ImageDraw.Draw(img).text((6, 6), 'Page content', fill='black')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    raw = buf.getvalue()

    monkeypatch.setattr(vision_ocr, 'sys', types.SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(vision_ocr, 'CACHE_DIR', tmp_path / 'ocr_cache')
    monkeypatch.setattr(vision_ocr.gpu_coordinator, 'acquire_for_inference', lambda **kw: nullcontext())
    monkeypatch.setattr(NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))

    full_calls = []
    status_calls = []

    def fake_get_identity(self):
        full_calls.append(1)
        return 'vision-digest-A'

    def fake_status_identity(self):
        status_calls.append(1)
        return 'vision-digest-A'

    monkeypatch.setattr(NativeVisionClient, 'get_identity', fake_get_identity)
    monkeypatch.setattr(NativeVisionClient, 'get_status_identity', fake_status_identity)
    monkeypatch.setattr(
        NativeVisionClient,
        'generate_from_image',
        lambda self, *a, **kw: {'text': '{"text":"Page content"}', 'finish_reason': 'stop', 'is_truncated': False},
    )

    mgr = vision_ocr.VisionOCRManager(cache_dir=tmp_path / 'ocr_cache')
    rec1 = mgr.ocr_image_bytes(
        raw, hashlib.sha256(raw).hexdigest(), 'doc.pdf', 1, preverified_model_digest='vision-digest-A'
    )
    assert rec1['model_digest'] == 'vision-digest-A'  # nosec B101
    assert len(status_calls) == 1  # nosec B101
    assert len(full_calls) == 0  # nosec B101

    # Mismatched preverified digest forces full byte re-verification.
    rec2 = mgr.ocr_image_bytes(
        raw, hashlib.sha256(raw).hexdigest(), 'doc.pdf', 2, preverified_model_digest='stale-digest'
    )
    assert rec2['model_digest'] == 'vision-digest-A'  # nosec B101
    assert len(status_calls) == 2  # nosec B101
    assert len(full_calls) == 1  # nosec B101


def test_mlx_infer_stream_reuses_model_and_deep_clones_hybrid_prefix_cache(tmp_path, monkeypatch, capsys):
    """app/mlx_infer.py --stream keeps model resident, streams token NDJSON, and deep-clones hybrid caches."""
    import runpy
    import socket

    model_dir = tmp_path / 'qwen9b'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(
        json.dumps({
            'model_type': 'qwen3_5',
            'architectures': ['Qwen3_5ForConditionalGeneration'],
            'text_config': {
                'model_type': 'qwen3_5_text',
                'hidden_size': 4096,
                'num_hidden_layers': 32,
                'num_attention_heads': 16,
                'max_position_embeddings': 262144,
            },
            'quantization': {'bits': 4, 'group_size': 64, 'mode': 'affine'},
        }),
        encoding='utf-8',
    )

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    monkeypatch.setenv('HF_HUB_OFFLINE', os.environ.get('HF_HUB_OFFLINE', '0'))
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', os.environ.get('TRANSFORMERS_OFFLINE', '0'))
    monkeypatch.setenv('HF_DATASETS_OFFLINE', os.environ.get('HF_DATASETS_OFFLINE', '0'))

    class FakeArray:
        def __init__(self, data):
            self.data = list(data.data) if isinstance(data, FakeArray) else list(data)

        def __getitem__(self, item):
            if item is None:
                return self
            return self.data[item]

    class FakeLayer:
        def __init__(self, state):
            self.state = state

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            sys_txt = messages[0]['content']
            user_txt = messages[-1]['content']
            return f'<|im_start|>system\n{sys_txt}<|im_end|>\n<|im_start|>user\n{user_txt}<|im_end|>\n<|im_start|>assistant\n'

        def encode(self, text):
            # Deterministic 1-token-per-char encoding (>= 64 tokens for system prefix marker)
            return [ord(ch) for ch in text]

    tokenizer = Tokenizer()

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return tokenizer

    load_calls = []
    prefill_calls = []
    prefill_counts_at_gen = []
    gen_calls = []
    observed_layer1_states = []

    class FakeModel:
        def __call__(self, arr, cache=None):
            prefill_calls.append(len(arr.data))
            # Layer 0 resembles KVCache [k, v]; Layer 1 resembles hybrid ArraysCache [conv_state]
            cache[0].state = [FakeArray([1, 2]), FakeArray([3, 4])]
            cache[1].state = [FakeArray([9, 9])]

    def load(path, **kwargs):
        load_calls.append(path)
        return FakeModel(), tokenizer

    def make_prompt_cache(_model):
        return [FakeLayer([]), FakeLayer([])]

    def stream_generate(model, active_tok, *, prompt=None, prompt_cache=None, **kwargs):
        prefill_counts_at_gen.append(len(prefill_calls))
        gen_calls.append({'prompt': prompt, 'has_cache': prompt_cache is not None})
        observed_layer1_states.append(prompt_cache[1].state[0].data.copy())
        # Simulate in-place mutation during decoding as MLX ArraysCache / KVCache performs
        prompt_cache[1].state[0].data[0] = 999
        prompt_cache[1].state[0] = FakeArray([888])
        yield types.SimpleNamespace(
            text='Trả ', finish_reason=None, prompt_tokens=10, generation_tokens=1,
            prompt_tps=40.0, generation_tps=18.0, peak_memory=5.4,
        )
        yield types.SimpleNamespace(
            text='lời.', finish_reason='stop', prompt_tokens=10, generation_tokens=2,
            prompt_tps=40.0, generation_tps=18.0, peak_memory=5.4,
        )

    lm = types.ModuleType('mlx_lm')
    lm.load = load
    lm.stream_generate = stream_generate
    lm.generate = lambda *a, **k: 'Trả lời.'
    sampling = types.ModuleType('mlx_lm.sample_utils')
    sampling.make_sampler = lambda **kw: kw
    sampling.make_logits_processors = lambda **kw: [kw]
    cache_mod = types.ModuleType('mlx_lm.models.cache')
    cache_mod.make_prompt_cache = make_prompt_cache
    models_mod = types.ModuleType('mlx_lm.models')
    models_mod.cache = cache_mod
    core = types.ModuleType('mlx.core')
    core.array = FakeArray
    core.eval = lambda *a, **kw: None
    core.clear_cache = lambda: None
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType('mlx')
    mlx.core = core
    transformers = types.ModuleType('transformers')
    transformers.AutoTokenizer = AutoTokenizer
    for name, mod in [
        ('mlx_lm', lm),
        ('mlx_lm.sample_utils', sampling),
        ('mlx_lm.models', models_mod),
        ('mlx_lm.models.cache', cache_mod),
        ('mlx', mlx),
        ('mlx.core', core),
        ('transformers', transformers),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    sys_msg = 'SYSTEM PREFIX ' + ('A' * 80)
    reqs = [
        {'model_path': str(model_dir), 'messages': [{'role': 'system', 'content': sys_msg}, {'role': 'user', 'content': 'Q1?'}], 'max_tokens': 16},
        {'model_path': str(model_dir), 'messages': [{'role': 'system', 'content': sys_msg}, {'role': 'user', 'content': 'Q2?'}], 'max_tokens': 16},
    ]
    monkeypatch.setattr(sys, 'argv', ['mlx_infer.py', '--stream'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'.join(json.dumps(r) for r in reqs) + '\n'))

    runpy.run_path(str(ROOT / 'app/mlx_infer.py'), run_name='__main__')
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]
    assert not [evt for evt in lines if 'error' in evt], f'Unexpected error events: {lines}'  # nosec B101
    done_events = [evt for evt in lines if evt.get('done')]
    token_events = [evt for evt in lines if 'token' in evt]

    assert len(load_calls) == 1  # nosec B101
    assert sum(prefill_calls) == done_events[0]['review_cache']['prefix_tokens']  # nosec B101
    assert prefill_counts_at_gen == [len(prefill_calls), len(prefill_calls)]  # Req 2 reused cached prefix without prefill  # nosec B101
    assert len(token_events) == 4  # 2 streamed token chunks per request  # nosec B101
    assert len(done_events) == 2  # nosec B101
    assert done_events[0]['answer'] == 'Trả lời.'  # nosec B101
    assert done_events[0]['review_cache']['reused_tokens'] == 0  # nosec B101
    assert done_events[0]['review_cache']['prefix_tokens'] >= 64  # nosec B101
    assert done_events[1]['timings']['load_seconds'] == 0.0  # nosec B101
    assert done_events[1]['review_cache']['reused_tokens'] == done_events[0]['review_cache']['prefix_tokens']  # nosec B101
    assert isinstance(gen_calls[1]['prompt'], list)  # Suffix tokens only after prompt_cache  # nosec B101
    assert observed_layer1_states == [[9, 9], [9, 9]]  # In-place mutation did not corrupt snapshot  # nosec B101


def test_mlx_vision_stream_reuses_loaded_model_and_schema_processor(tmp_path, monkeypatch, capsys):
    """app/mlx_vision.py --stream keeps model and processor warm across pages."""
    import runpy
    import socket

    model_dir = tmp_path / 'vision9b'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(json.dumps({'model_type': 'qwen3_5'}), encoding='utf-8')
    img_path = tmp_path / 'page.png'
    img_path.write_bytes(b'fake')

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    monkeypatch.setenv('HF_HUB_OFFLINE', os.environ.get('HF_HUB_OFFLINE', '0'))
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', os.environ.get('TRANSFORMERS_OFFLINE', '0'))
    monkeypatch.setenv('HF_HUB_DISABLE_TELEMETRY', os.environ.get('HF_HUB_DISABLE_TELEMETRY', '0'))
    monkeypatch.setenv('HF_DATASETS_OFFLINE', os.environ.get('HF_DATASETS_OFFLINE', '0'))

    load_calls = []
    schema_calls = []
    tok = object()
    processor = types.SimpleNamespace(tokenizer=tok)
    schema_proc = object()

    vlm = types.ModuleType('mlx_vlm')
    vlm.load = lambda path, **kw: load_calls.append(path) or (object(), processor)
    vlm.generate = lambda *a, **kw: types.SimpleNamespace(
        text='{"text":"Page text"}', finish_reason='stop', generation_tokens=4, prompt_tokens=12
    )
    prompt_utils = types.ModuleType('mlx_vlm.prompt_utils')
    prompt_utils.apply_chat_template = lambda *a, **kw: 'vision prompt'
    structured = types.ModuleType('mlx_vlm.structured')
    structured.build_json_schema_logits_processor = lambda t, s: schema_calls.append((t, s)) or schema_proc
    core = types.ModuleType('mlx.core')
    core.clear_cache = lambda: None
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType('mlx')
    mlx.core = core
    for name, mod in [
        ('mlx_vlm', vlm),
        ('mlx_vlm.prompt_utils', prompt_utils),
        ('mlx_vlm.structured', structured),
        ('mlx', mlx),
        ('mlx.core', core),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    schema = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}
    req = {
        'model_path': str(model_dir),
        'image_path': str(img_path),
        'prompt': 'OCR page',
        'response_schema': schema,
    }
    monkeypatch.setattr(sys, 'argv', ['mlx_vision.py', '--stream'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'.join([json.dumps(req), json.dumps(req)]) + '\n'))

    runpy.run_path(str(ROOT / 'app/mlx_vision.py'), run_name='__main__')
    results = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]

    assert len(results) == 2  # nosec B101
    assert len(load_calls) == 1  # nosec B101
    assert len(schema_calls) == 2  # nosec B101
    assert all(r['text'] == '{"text":"Page text"}' and r['finish_reason'] == 'stop' for r in results)  # nosec B101


def test_native_vision_uses_resident_stream_worker_and_evicts_on_cancel(monkeypatch, tmp_path):
    """NativeVisionClient uses ResidentStreamWorker by default and evicts it when cancelled."""
    from contextlib import nullcontext
    from PIL import Image
    from app import native_vision

    img_dir = tmp_path / 'imgs'
    img_dir.mkdir()
    img = Image.new('RGB', (64, 64), 'white')
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    raw = buf.getvalue()

    model_dir = tmp_path / 'vision9b'
    model_dir.mkdir()
    client = native_vision.NativeVisionClient(model_dir=model_dir)

    seen_requests = []
    evict_calls = []
    monkeypatch.setattr(native_vision.NativeVisionClient, 'available', classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(native_vision, '_acquire_inference_lock', lambda **kw: nullcontext())
    monkeypatch.setattr(
        native_vision._VISION_STREAM_WORKER,
        'request_json',
        lambda payload, **kw: seen_requests.append(payload) or {'text': 'OK', 'finish_reason': 'stop', 'is_truncated': False},
    )
    monkeypatch.setattr(
        native_vision._VISION_STREAM_WORKER,
        'evict',
        lambda: evict_calls.append(True),
    )

    out = client.generate_from_image(raw, 'OCR page', max_tokens=256)
    assert out['text'] == 'OK'  # nosec B101
    assert len(seen_requests) == 1  # nosec B101
    assert seen_requests[0]['prompt'] == 'OCR page'  # nosec B101

    native_vision.cancel_active_vision_workers()
    assert len(evict_calls) == 1  # nosec B101


def test_mlx_infer_stream_warmup_prepopulates_prefix_cache_before_first_query(tmp_path, monkeypatch, capsys):
    """op='warmup' pre-loads model and builds system prefix KV caches without running generation."""
    import runpy
    import socket

    model_dir = tmp_path / 'qwen9b'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(
        json.dumps({
            'model_type': 'qwen3_5',
            'architectures': ['Qwen3_5ForConditionalGeneration'],
            'text_config': {
                'model_type': 'qwen3_5_text',
                'hidden_size': 4096,
                'num_hidden_layers': 32,
                'num_attention_heads': 16,
                'max_position_embeddings': 262144,
            },
            'quantization': {'bits': 4, 'group_size': 64, 'mode': 'affine'},
        }),
        encoding='utf-8',
    )

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    monkeypatch.setenv('HF_HUB_OFFLINE', os.environ.get('HF_HUB_OFFLINE', '0'))
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', os.environ.get('TRANSFORMERS_OFFLINE', '0'))
    monkeypatch.setenv('HF_DATASETS_OFFLINE', os.environ.get('HF_DATASETS_OFFLINE', '0'))

    class FakeArray:
        def __init__(self, data):
            self.data = list(data.data) if isinstance(data, FakeArray) else list(data)

        def __getitem__(self, item):
            if item is None:
                return self
            return self.data[item]

    class FakeLayer:
        def __init__(self, state):
            self.state = state

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            sys_txt = messages[0]['content']
            user_txt = messages[-1]['content']
            return f'<|im_start|>system\n{sys_txt}<|im_end|>\n<|im_start|>user\n{user_txt}<|im_end|>\n<|im_start|>assistant\n'

        def encode(self, text):
            return [ord(ch) for ch in text]

    tokenizer = Tokenizer()

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return tokenizer

    load_calls = []
    prefill_calls = []
    gen_calls = []

    class FakeModel:
        def __call__(self, arr, cache=None):
            prefill_calls.append(len(arr.data))
            cache[0].state = [FakeArray([1, 2]), FakeArray([3, 4])]

    lm = types.ModuleType('mlx_lm')
    lm.load = lambda path, **kw: load_calls.append(path) or (FakeModel(), tokenizer)
    lm.generate = lambda *a, **k: 'OK'

    def stream_generate(model, active_tok, *, prompt=None, prompt_cache=None, **kwargs):
        gen_calls.append({'prefill_count': len(prefill_calls), 'has_cache': prompt_cache is not None})
        yield types.SimpleNamespace(
            text='OK.', finish_reason='stop', prompt_tokens=10, generation_tokens=1,
            prompt_tps=40.0, generation_tps=18.0, peak_memory=5.4,
        )

    lm.stream_generate = stream_generate
    sampling = types.ModuleType('mlx_lm.sample_utils')
    sampling.make_sampler = lambda **kw: kw
    sampling.make_logits_processors = lambda **kw: [kw]
    cache_mod = types.ModuleType('mlx_lm.models.cache')
    cache_mod.make_prompt_cache = lambda _m: [FakeLayer([])]
    models_mod = types.ModuleType('mlx_lm.models')
    models_mod.cache = cache_mod
    core = types.ModuleType('mlx.core')
    core.array = FakeArray
    core.eval = lambda *a, **kw: None
    core.clear_cache = lambda: None
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType('mlx')
    mlx.core = core
    transformers = types.ModuleType('transformers')
    transformers.AutoTokenizer = AutoTokenizer
    for name, mod in [
        ('mlx_lm', lm),
        ('mlx_lm.sample_utils', sampling),
        ('mlx_lm.models', models_mod),
        ('mlx_lm.models.cache', cache_mod),
        ('mlx', mlx),
        ('mlx.core', core),
        ('transformers', transformers),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    sys_1 = 'SYSTEM PROMPT ONE ' + ('B' * 80)
    sys_2 = 'SYSTEM PROMPT TWO ' + ('C' * 80)
    reqs = [
        {'op': 'warmup', 'model_path': str(model_dir), 'system_prompts': [sys_1, sys_2, sys_1], 'num_ctx': 8192},
        {'model_path': str(model_dir), 'messages': [{'role': 'system', 'content': sys_1}, {'role': 'user', 'content': 'First question?'}], 'max_tokens': 16},
    ]
    monkeypatch.setattr(sys, 'argv', ['mlx_infer.py', '--stream'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'.join(json.dumps(r) for r in reqs) + '\n'))

    runpy.run_path(str(ROOT / 'app/mlx_infer.py'), run_name='__main__')
    events = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]
    done_events = [evt for evt in events if evt.get('done')]
    assert len(done_events) == 2  # nosec B101

    warmup_evt, query_evt = done_events
    assert warmup_evt['op'] == 'warmup'  # nosec B101
    assert warmup_evt['ok'] is True  # nosec B101
    assert len(warmup_evt['warmed_prefixes']) == 2  # nosec B101
    assert len(load_calls) == 1  # nosec B101
    assert len(gen_calls) == 1  # Warmup never invoked stream_generate  # nosec B101
    assert query_evt['timings']['load_seconds'] == 0.0  # nosec B101
    assert query_evt['review_cache']['reused_tokens'] == warmup_evt['warmed_prefixes'][0]['prefix_tokens']  # nosec B101
    assert gen_calls[0]['prefill_count'] == len(prefill_calls)  # Zero extra prefill during first user query  # nosec B101


def test_mlx_infer_stream_caches_compiled_schema_with_clone_and_supports_prefill_step_size(tmp_path, monkeypatch, capsys):
    """Repeated structured stream requests compile schema once and clone per request; timings record schema compile time."""
    import runpy
    import socket

    model_dir = tmp_path / 'qwen9b'
    model_dir.mkdir()
    (model_dir / 'config.json').write_text(
        json.dumps({
            'model_type': 'qwen3_5',
            'architectures': ['Qwen3_5ForConditionalGeneration'],
            'text_config': {
                'model_type': 'qwen3_5_text',
                'hidden_size': 4096,
                'num_hidden_layers': 32,
                'num_attention_heads': 16,
                'max_position_embeddings': 262144,
            },
            'quantization': {'bits': 4, 'group_size': 64, 'mode': 'affine'},
        }),
        encoding='utf-8',
    )

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    monkeypatch.setenv('HF_HUB_OFFLINE', os.environ.get('HF_HUB_OFFLINE', '0'))
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', os.environ.get('TRANSFORMERS_OFFLINE', '0'))
    monkeypatch.setenv('HF_DATASETS_OFFLINE', os.environ.get('HF_DATASETS_OFFLINE', '0'))
    monkeypatch.setenv('MLX_PREFILL_STEP_SIZE', '256')

    class FakeArray:
        def __init__(self, data):
            self.data = list(data.data) if isinstance(data, FakeArray) else list(data)
        def __getitem__(self, item):
            return self if item is None else self.data[item]

    class FakeLayer:
        def __init__(self, state):
            self.state = state

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return f"<|im_start|>system\n{messages[0]['content']}<|im_end|>\n<|im_start|>user\n{messages[-1]['content']}<|im_end|>\n<|im_start|>assistant\n"
        def encode(self, text):
            return [ord(ch) for ch in text]

    tokenizer = Tokenizer()
    class AutoTokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            return tokenizer

    class FakeSchemaProc:
        def __init__(self, tag):
            self.tag = tag
            self.clones = 0
        def clone(self):
            self.clones += 1
            return FakeSchemaProc(f'{self.tag}-clone-{self.clones}')

    compile_calls = []
    gen_kwargs_seen = []

    lm = types.ModuleType('mlx_lm')
    lm.load = lambda path, **kw: (lambda arr, cache=None: setattr(cache[0], 'state', [FakeArray([1])]), tokenizer)
    lm.generate = lambda *a, **k: '{}'

    def stream_generate(model, active_tok, *, prompt=None, prompt_cache=None, **kwargs):
        gen_kwargs_seen.append(kwargs)
        yield types.SimpleNamespace(
            text='{}', finish_reason='stop', prompt_tokens=10, generation_tokens=1,
            prompt_tps=40.0, generation_tps=18.0, peak_memory=5.4,
        )

    lm.stream_generate = stream_generate
    sampling = types.ModuleType('mlx_lm.sample_utils')
    sampling.make_sampler = lambda **kw: kw
    sampling.make_logits_processors = lambda **kw: [kw]
    cache_mod = types.ModuleType('mlx_lm.models.cache')
    cache_mod.make_prompt_cache = lambda _m: [FakeLayer([])]
    models_mod = types.ModuleType('mlx_lm.models')
    models_mod.cache = cache_mod
    core = types.ModuleType('mlx.core')
    core.array = FakeArray
    core.eval = lambda *a, **kw: None
    core.clear_cache = lambda: None
    core.random = types.SimpleNamespace(seed=lambda value: None)
    mlx = types.ModuleType('mlx')
    mlx.core = core
    transformers = types.ModuleType('transformers')
    transformers.AutoTokenizer = AutoTokenizer
    structured = types.ModuleType('mlx_vlm.structured')
    def build_proc(tok, schema):
        proc = FakeSchemaProc(f'compiled-{len(compile_calls)}')
        compile_calls.append(proc)
        return proc
    structured.build_json_schema_logits_processor = build_proc
    mlx_vlm = types.ModuleType('mlx_vlm')
    mlx_vlm.structured = structured

    for name, mod in [
        ('mlx_lm', lm),
        ('mlx_lm.sample_utils', sampling),
        ('mlx_lm.models', models_mod),
        ('mlx_lm.models.cache', cache_mod),
        ('mlx', mlx),
        ('mlx.core', core),
        ('transformers', transformers),
        ('mlx_vlm', mlx_vlm),
        ('mlx_vlm.structured', structured),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    schema = {'type': 'object', 'properties': {'claims': {'type': 'array'}}, 'required': ['claims']}
    reqs = [
        {'model_path': str(model_dir), 'messages': [{'role': 'system', 'content': 'SYS'}, {'role': 'user', 'content': 'Q1'}], 'max_tokens': 16, 'response_schema': schema},
        {'model_path': str(model_dir), 'messages': [{'role': 'system', 'content': 'SYS'}, {'role': 'user', 'content': 'Q2'}], 'max_tokens': 16, 'response_schema': schema},
    ]
    monkeypatch.setattr(sys, 'argv', ['mlx_infer.py', '--stream'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'.join(json.dumps(r) for r in reqs) + '\n'))

    runpy.run_path(str(ROOT / 'app/mlx_infer.py'), run_name='__main__')
    events = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]
    done_events = [evt for evt in events if evt.get('done')]
    assert len(done_events) == 2  # nosec B101
    assert len(compile_calls) == 1, 'Identical schema should compile once and clone for subsequent requests'  # nosec B101
    assert done_events[0]['timings']['schema_compile_seconds'] >= 0.0  # nosec B101
    assert done_events[1]['timings']['schema_compile_seconds'] >= 0.0  # nosec B101
    assert gen_kwargs_seen[0]['prefill_step_size'] == 256  # nosec B101
    assert gen_kwargs_seen[0]['logits_processors'][-1].tag == 'compiled-0-clone-1'  # nosec B101
    assert gen_kwargs_seen[1]['logits_processors'][-1].tag == 'compiled-0-clone-2'  # nosec B101


def test_mlx_infer_multi_adapter_hot_swap_avoids_base_reload(tmp_path, monkeypatch):
    import socket
    from collections import OrderedDict

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    mx = types.ModuleType('mlx.core')
    mx.random = types.SimpleNamespace(seed=lambda _: None)
    mx.clear_cache = lambda: None
    mlx_pkg = types.ModuleType('mlx')
    mlx_pkg.core = mx
    lm = types.ModuleType('mlx_lm')
    sampler = types.ModuleType('mlx_lm.sample_utils')
    sampler.make_sampler = lambda **k: None
    sampler.make_logits_processors = lambda **k: []
    utils_mod = types.ModuleType('mlx_lm.utils')

    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    adp_a = tmp_path / 'adp_a'
    adp_a.mkdir()
    (adp_a / 'adapters.safetensors').write_bytes(b'a')
    adp_b = tmp_path / 'adp_b'
    adp_b.mkdir()
    (adp_b / 'adapters.safetensors').write_bytes(b'b')

    base_q = object()
    lora_q_a = types.SimpleNamespace(linear=base_q, lora_a=1, lora_b=2)
    lora_q_b = types.SimpleNamespace(linear=base_q, lora_a=3, lora_b=4)

    class FakeModel:
        def __init__(self):
            self.q_proj = base_q

        def named_modules(self):
            return [('q_proj', self.q_proj)]

    model_inst = FakeModel()
    load_calls = []

    def fake_load(path, adapter_path=None):
        load_calls.append(adapter_path)
        if adapter_path == str(adp_a):
            model_inst.q_proj = lora_q_a
        return model_inst, 'tok'

    def fake_load_adapters(m, adapter_path):
        assert m is model_inst and adapter_path == str(adp_b)  # nosec B101
        m.q_proj = lora_q_b
        return m

    lm.load = fake_load
    lm.stream_generate = lambda *a, **k: iter(())
    utils_mod.load_adapters = fake_load_adapters
    for name, mod in [
        ('mlx', mlx_pkg),
        ('mlx.core', mx),
        ('mlx_lm', lm),
        ('mlx_lm.sample_utils', sampler),
        ('mlx_lm.utils', utils_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location('mlx_infer_hotswap_test', ROOT / 'app' / 'mlx_infer.py')
    mlx_infer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mlx_infer)

    state = {
        'cfg': {},
        'tok': {},
        'model_key': None,
        'model': None,
        'loaded_tok': None,
        'lora_bindings': None,
        'base_slots': {},
        'lora_banks': OrderedDict(),
        'prefix_caches': OrderedDict({'keep': 'kv_snap'}),
        'context_prefix_cache': OrderedDict(),
        'schema_processors': OrderedDict(),
    }

    mlx_infer._ensure_loaded_model(model_dir, adp_a, state, adapter_sha256='sha_a')
    assert model_inst.q_proj is lora_q_a and len(load_calls) == 1  # nosec B101

    mlx_infer._ensure_loaded_model(model_dir, adp_b, state, adapter_sha256='sha_b')
    assert model_inst.q_proj is lora_q_b and len(load_calls) == 1  # nosec B101

    mlx_infer._ensure_loaded_model(model_dir, adp_a, state, adapter_sha256='sha_a')
    assert model_inst.q_proj is lora_q_a and len(load_calls) == 1  # nosec B101

    mlx_infer._ensure_loaded_model(model_dir, None, state)
    assert model_inst.q_proj is base_q and len(load_calls) == 1  # nosec B101
    assert state['prefix_caches'].get('keep') == 'kv_snap'  # nosec B101

    sparse_state = {
        'cfg': {},
        'tok': {},
        'model_key': None,
        'model': None,
        'loaded_tok': None,
        'lora_bindings': None,
        'base_slots': {},
        'lora_banks': OrderedDict(),
        'context_prefix_cache': OrderedDict(),
        'schema_processors': OrderedDict(),
    }
    mlx_infer._ensure_loaded_model(model_dir, None, sparse_state)
    assert sparse_state['model'] is model_inst and len(load_calls) == 2  # nosec B101


def test_mlx_infer_detects_streaming_repetition_loop(monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    mx = types.ModuleType('mlx.core')
    mx.random = types.SimpleNamespace(seed=lambda _: None)
    mx.clear_cache = lambda: None
    mlx_pkg = types.ModuleType('mlx')
    mlx_pkg.core = mx
    lm = types.ModuleType('mlx_lm')
    lm.load = lambda *a, **k: (None, None)
    lm.stream_generate = lambda *a, **k: iter(())
    sampler = types.ModuleType('mlx_lm.sample_utils')
    sampler.make_sampler = lambda **k: None
    sampler.make_logits_processors = lambda **k: []
    for name, mod in [
        ('mlx', mlx_pkg),
        ('mlx.core', mx),
        ('mlx_lm', lm),
        ('mlx_lm.sample_utils', sampler),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location('mlx_infer_loop_test', ROOT / 'app' / 'mlx_infer.py')
    mlx_infer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mlx_infer)

    unit = 'rotateLeft gán T1->left=x; rotateRight gán T1->right=x; '
    assert mlx_infer._has_streaming_repetition_loop('{"claims":[{"text":"' + unit * 4) is True  # nosec B101
    assert mlx_infer._has_streaming_repetition_loop('{"claims":[{"text":"rotateLeft gán T1->left=x;","source_ids":["S1"],"evidence_lines":[2,3]}]}') is False  # nosec B101


def test_mlx_infer_detects_paraphrased_clause_loop_and_allows_clean_multi_claim(monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket, 'create_connection', socket.create_connection)
    mx = types.ModuleType('mlx.core')
    mx.random = types.SimpleNamespace(seed=lambda _: None)
    mx.clear_cache = lambda: None
    mlx_pkg = types.ModuleType('mlx')
    mlx_pkg.core = mx
    lm = types.ModuleType('mlx_lm')
    lm.load = lambda *a, **k: (None, None)
    lm.stream_generate = lambda *a, **k: iter(())
    sampler = types.ModuleType('mlx_lm.sample_utils')
    sampler.make_sampler = lambda **k: None
    sampler.make_logits_processors = lambda **k: []
    for name, mod in [
        ('mlx', mlx_pkg),
        ('mlx.core', mx),
        ('mlx_lm', lm),
        ('mlx_lm.sample_utils', sampler),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location('mlx_infer_clause_loop_test', ROOT / 'app' / 'mlx_infer.py')
    mlx_infer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mlx_infer)

    repeated_text = (
        '{"claims":[{"text":"rotateLeft xoay nút x qua nhánh phải (x->right) còn rotateRight xoay qua nhánh trái; '
        'rotateLeft gán T2=T1->left còn rotateRight gán T2=T1->right; '
        'rotateLeft gán x->right=T2 còn rotateRight gán x->left=T2; '
        'rotateLeft xoay qua nhánh phải còn rotateRight xoay qua nhánh trái; '
        'rotateLeft gán T2=T1->left còn rotateRight gán T2=T1->right; '
        'rotateLeft gán x->right=T2 còn rotateRight gán x->left=T2; '
        'rotateLeft gán T2=T1->left còn rotateRight gán T2=T1->right; '
        'rotateLeft trả về T1 còn rotateRight cũng trả về T1."}]}'
    )
    assert mlx_infer._has_streaming_repetition_loop(repeated_text) is True  # nosec B101

    legitimate = (
        '{"claims":['
        '{"text":"rotateLeft đặt AVLNodes* T1=x->right; AVLNodes* T2=T1->left; gán T1->left=x và x->right=T2 rồi trả về T1.","source_ids":["S1"],"evidence_lines":[2,7]},'
        '{"text":"rotateRight đặt AVLNodes* T1=x->left; AVLNodes* T2=T1->right; gán T1->right=x và x->left=T2; x->height dùng max(getHeight(x->left),getHeight(x->right)) với giá trị xấp xỉ 3.14.","source_ids":["S2"],"evidence_lines":[2,7]}'
        ']}'
    )
    assert mlx_infer._has_streaming_repetition_loop(legitimate) is False  # nosec B101

    three_item_review = (
        '{"checks":['
        '{"index":1,"input_audit":"Task asks X; rule given; no input is missing.","explanation_audit":"The explanation accurately follows the source rule."},'
        '{"index":2,"input_audit":"Task asks Y; rule given; no input is missing.","explanation_audit":"The explanation accurately follows the source rule."},'
        '{"index":3,"input_audit":"Task asks Z; rule given; no input is missing.","explanation_audit":"The explanation accurately follows the source rule."}'
        ']}'
    )
    assert mlx_infer._has_streaming_repetition_loop(three_item_review, max_schema_items=3) is False  # nosec B101
    looping_three_item = three_item_review + '"x":"The explanation accurately follows the source rule. The explanation accurately follows the source rule."}'
    assert mlx_infer._has_streaming_repetition_loop(looping_three_item, max_schema_items=3) is True  # nosec B101
