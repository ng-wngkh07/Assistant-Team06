"""The last model boundary must know the application context budget."""
import json
import pytest
from types import SimpleNamespace

from app import trained_client


def test_mlx_bridge_forwards_request_context_limit(monkeypatch):
    observed = []
    monkeypatch.setattr(trained_client.TrainedModelClient, 'available', lambda self: True)
    monkeypatch.setattr(trained_client.active_registry, 'get_active_adapter_dir', lambda: None)

    def completed(*args, **kwargs):
        observed.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'answer': 'Có căn cứ.', 'is_truncated': False}), stderr='')
    monkeypatch.setattr(trained_client.subprocess, 'run', completed)
    client = trained_client.TrainedModelClient()
    assert client.chat_complete(messages=[{'role': 'user', 'content': 'Câu hỏi'}], num_ctx=8192, max_tokens=900) == 'Có căn cứ.'
    assert observed[0].get('num_ctx') == 8192


@pytest.mark.parametrize("model_capacity,input_size,num_ctx,max_tokens", [(128,120,8192,20), (32768,9000,None,20), (None,8,8192,20), (0,8,8192,20), ("unknown",8,8192,20), (32768,8,8192,0), (32768,8,8192,-20), (32768,8,0,20), (32768,8,-20,20), (32768,8,8192,20)])
def test_overflow_is_rejected_before_loading_model_weights(tmp_path, monkeypatch, model_capacity, input_size, num_ctx, max_tokens):
    """Exercise JSON boundary with a local tokenizer stub; GPU is never touched."""
    import importlib
    import io
    import sys
    from types import ModuleType
    import pytest
    config = {'max_position_embeddings': model_capacity}
    (tmp_path / 'config.json').write_text(json.dumps(config))
    calls = []
    class Tokenizer:
        model_max_length = 131072  # Must not override the smaller model configuration.
        def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True, **kwargs):
            prompt = 'x' * input_size
            return [1] * len(prompt) if tokenize else prompt
        def encode(self, text, **kwargs):
            return [1] * len(text)
    class Factory:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            if (model_capacity,input_size,num_ctx,max_tokens)==(32768,8,8192,20):
                raise OSError('Tokenizer cannot be verified')
            return Tokenizer()
    tl = ModuleType('transformers'); tl.AutoTokenizer = Factory
    lm = ModuleType('mlx_lm')
    lm.load = lambda *args, **kwargs: calls.append('load') or (object(), Tokenizer())
    lm.generate = lambda *args, **kwargs: calls.append('generate') or 'result'
    sampler = ModuleType('mlx_lm.sample_utils'); sampler.make_sampler = lambda **kwargs: None
    mx = ModuleType('mlx.core'); mx.random = SimpleNamespace(seed=lambda _: None)
    package = ModuleType('mlx'); package.core = mx
    for name, value in [('transformers', tl), ('mlx_lm', lm), ('mlx_lm.sample_utils', sampler), ('mlx', package), ('mlx.core', mx)]:
        monkeypatch.setitem(sys.modules, name, value)
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('yc195_infer_under_test', Path(trained_client.__file__).with_name('mlx_infer.py'))
    infer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(infer)
    request = {'model_path': str(tmp_path), 'messages': [{'role': 'user', 'content': 'valid'}], 'max_tokens': max_tokens}
    if num_ctx is not None:
        request['num_ctx'] = num_ctx
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(request)))
    with pytest.raises((ValueError, RuntimeError)):
        infer.main()
    assert calls == []
