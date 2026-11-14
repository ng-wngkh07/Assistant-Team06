import hashlib
import json
from pathlib import Path

import pytest

import app.practice_adapter as subject
from app.practice import practice_review_schema


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(subject, 'REGISTRY_FILE', tmp_path / 'registry.json')
    monkeypatch.setattr(subject, 'validate_native_model_config', lambda path: {})
    model = tmp_path / 'model'
    model.mkdir()
    hashes = {}
    names = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors.index.json', 'first.safetensors', 'second.safetensors'] + [f'base-{i}.bin' for i in range(7)]
    for name in names:
        path = model / name
        value = json.dumps({'weight_map': {'a': 'first.safetensors', 'b': 'second.safetensors'}}) if name == 'model.safetensors.index.json' else '{}'
        path.write_text(value)
        hashes[path.name] = digest(path)
    adapter = tmp_path / 'adapter'
    adapter.mkdir()
    weights = adapter / 'adapters.safetensors'
    weights.write_bytes(b'clearly synthetic fixture, never model-loaded')
    config = adapter / 'adapter_config.json'
    config.write_text('{}')
    def binding(path):
        return {'path': str(path), 'sha256': digest(path)}
    def write(name, value, lines=False):
        path = tmp_path / name
        path.write_text(''.join(json.dumps(row) + '\n' for row in value) if lines else json.dumps(value))
        return binding(path)
    def bundle(prefix, count, arms):
        suite, raw, reviews = [], [], []
        for i in range(count):
            missing = i >= count - 2 if count == 14 else i == 2
            expected = dict(index=1, supported=not missing, explanation_supported=not (missing or i % 2), unique_answer=not missing, qualifiers_preserved=True, verified_answer='' if missing else 'A')
            case = {'id': str(i), 'source_group': str(i % 3), 'source': {'passage_sha256': str(i)}, 'payload': {'index': 1, 'source': 'Synthetic source.', 'question': 'Synthetic question?', 'choices': dict(A='one', B='two', C='three', D='four'), 'proposed_answer': 'A', 'explanation': 'Synthetic explanation.', 'evidence_quote': 'Synthetic source.'}, 'task': 'missing_required_input' if missing else 'independent_false_explanation' if i % 2 else 'supported', 'expected_core': expected}
            case['compact_messages'] = [{'role': 'system', 'content': subject.SYSTEM_PROMPT}, {'role': 'user', 'content': subject.build_compact_review_prompt(case['payload'])}]
            case['response_schema'] = practice_review_schema(1, None)
            suite.append(case)
            for arm in arms:
                check = dict(expected, **{key: 'Synthetic fixture audit.' for key in subject.AUDIT_FIELDS})
                if count == 14 and (arm == 'base' and i < 2 or arm == 'compact_base' and i == 0):
                    check['supported'] = False
                answer = json.dumps({'checks': [check]})
                raw.append({'id': str(i), 'arm': arm, 'round': 0, 'answer': answer, 'finish_reason': 'stop', 'request_seconds': {'base': 10, 'compact_base': 8, 'adapter': 7}[arm], 'native_worker': {'context_limit': 8192, 'generation_profile': {'presence_penalty': 0}, 'adapter_loaded': arm == 'adapter', 'adapter_sha256': digest(weights) if arm == 'adapter' else None, 'adapter_scope': 'single_question_practice_review' if arm == 'adapter' else None, 'timings': {'worker_seconds': 6}}})
                reviews.append({'id': str(i), 'arm': arm, 'round': 0, 'source_verified': True, 'status': 'PASS', 'rationale': 'Synthetic fixture review, no model-quality claim.', 'answer_sha256': hashlib.sha256(answer.encode()).hexdigest(), 'source_sha256': str(i), 'repetition_loop': False})
        data = {'suite': write(prefix + '-suite.jsonl', suite, True), 'results': write(prefix + '-results.jsonl', raw, True), 'content_review': write(prefix + '-review.json', {'status': 'COMPLETE', 'cases': reviews})}
        if count == 14:
            data['source_review'] = write('sources.json', {'status': 'PASS', 'source_spans': [{'source': c['source']} for c in suite]})
            data['strict_summary'] = write('strict.json', {'decision': 'SYNTHETIC_FIXTURE_NO_MODEL_ACCEPTANCE'})
            data['refusal_contract_review'] = write('contract.json', {'schema': 'practice-refusal-contract-review-v1', 'status': 'REVIEWED_APPLICATION_CONTRACT', 'primary_strict_summary_sha256': data['strict_summary']['sha256'], 'original_strict_decision': 'SYNTHETIC_FIXTURE_NO_MODEL_ACCEPTANCE'})
            data['protocol'] = write('protocol.json', {'frozen_before_any_fresh_output': True, 'activation': False, 'generation': {'temperature': 0, 'seed': 42, 'max_tokens': 1000, 'context': 8192, 'thinking': False, 'prefill': 128, 'presence_penalty': 0, 'schema': 'practice_review_schema(1,None)'}, 'fresh_eval_sha256': data['suite']['sha256'], 'source_review_sha256': data['source_review']['sha256'], 'selected_adapter_sha256': digest(weights), 'adapter_config_sha256': digest(config)})
        return data
    manifest = {'schema': 'study-practice-9b-v1', 'stage': 'application_accepted', 'weights': binding(weights), 'adapter_config': binding(config), 'training_receipt': write('training.json', {'status': 'TRAINED_NOT_ACCEPTED', 'steps': 128, 'base_files_sha256': hashes, 'adapter_sha256': digest(weights)}), 'primary': bundle('primary', 14, ('base', 'compact_base', 'adapter')), 'application': bundle('native', 3, ('base', 'adapter'))}
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    return model, manifest, binding


def test_accepted_binding_and_metrics(accepted):
    model, manifest, _ = accepted
    result = subject.load_practice_adapter(model)
    assert result['primary']['source_correct'] == {'base': 12, 'compact_base': 13, 'adapter': 14}
    assert result['primary']['paired_ratio'] == .7
    assert result['application']['paired_ratio'] == .7


def test_qualified_stage_does_not_auto_activate(accepted):
    model, manifest, _ = accepted
    manifest['stage'] = 'native_qualified'
    manifest.pop('application')
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    assert subject.load_practice_adapter(model) is None
    assert subject.load_practice_adapter(model, require_active=False)['stage'] == 'native_qualified'


@pytest.mark.parametrize('file', ['base', 'weights', 'review', 'protocol'])
def test_actual_byte_changes_rejected(accepted, file):
    model, manifest, _ = accepted
    path = model / 'base-0.bin' if file == 'base' else Path(manifest['weights']['path']) if file == 'weights' else Path(manifest['primary']['content_review' if file == 'review' else 'protocol']['path'])
    subject.load_practice_adapter(model)
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError):
        subject.load_practice_adapter(model)


@pytest.mark.parametrize('defect', ['unsafe', 'slow', 'source_tie', 'partial_native', 'duplicate', 'incomplete'])
def test_gates_recomputed_even_when_manifest_rebound(accepted, defect):
    model, manifest, binding = accepted
    group = manifest['application'] if defect == 'partial_native' else manifest['primary']
    if defect in ('source_tie', 'partial_native'):
        path = Path(group['content_review']['path'])
        data = json.loads(path.read_text())
        for row in data['cases']:
            if row['arm'] == 'adapter' and (defect == 'partial_native' or row['id'] in ('0', '1')):
                row['status'] = 'PARTIAL'
        path.write_text(json.dumps(data))
        group['content_review'] = binding(path)
    else:
        path = Path(group['results']['path'])
        data = [json.loads(line) for line in path.read_text().splitlines()]
        if defect == 'duplicate': data[-1] = data[0]
        if defect == 'incomplete': data.pop()
        if defect == 'slow':
            for row in data:
                if row['arm'] == 'adapter': row['request_seconds'] = 15
        if defect == 'unsafe':
            row = next(x for x in data if x['arm'] == 'adapter' and x['id'] == '1')
            check = json.loads(row['answer'])['checks'][0]
            check['explanation_supported'] = True
            row['answer'] = json.dumps({'checks': [check]})
            rp = Path(group['content_review']['path'])
            reviews = json.loads(rp.read_text())
            next(x for x in reviews['cases'] if x['arm'] == 'adapter' and x['id'] == '1')['answer_sha256'] = hashlib.sha256(row['answer'].encode()).hexdigest()
            rp.write_text(json.dumps(reviews))
            group['content_review'] = binding(rp)
        path.write_text(''.join(json.dumps(row) + '\n' for row in data))
        group['results'] = binding(path)
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    with pytest.raises(ValueError): subject.load_practice_adapter(model)


def request():
    item = {'index': 1, 'source': 'Synthetic source.', 'question': 'Synthetic question?', 'choices': dict(A='one', B='two', C='three', D='four'), 'proposed_answer': 'A', 'explanation': 'Synthetic explanation.', 'evidence_quote': 'Synthetic source.'}
    return {'messages': [{'role': 'system', 'content': subject.SYSTEM_PROMPT}, {'role': 'user', 'content': subject.build_compact_review_prompt(item)}], 'max_tokens': 1000, 'num_ctx': 8192, 'temperature': 0, 'seed': 42, 'response_schema': practice_review_schema(1, None)}


def test_exact_request_scope():
    assert subject.request_matches_scope(request())


@pytest.mark.parametrize('defect', ['ordinary_qa', 'difficulty', 'multi', 'extra_role', 'temperature', 'schema_order'])
def test_unsupported_requests_never_match_adapter_scope(defect):
    value = request()
    if defect == 'ordinary_qa': value['messages'][1]['content'] = 'ordinary question'
    if defect == 'difficulty': value['response_schema'] = practice_review_schema(1, 'medium')
    if defect == 'multi': value['response_schema'] = practice_review_schema(2, None)
    if defect == 'extra_role': value['messages'].append({'role': 'user', 'content': 'more'})
    if defect == 'temperature': value['temperature'] = .1
    if defect == 'schema_order': value['response_schema'] = dict(reversed(list(value['response_schema'].items())))
    assert not subject.request_matches_scope(value)


@pytest.mark.parametrize('case_id,expected_core', [('0', False), ('12', True)])
def test_refusal_qualifier_is_recorded_without_overriding_supported_item_checks(accepted, case_id, expected_core):
    model, manifest, binding = accepted
    evidence = manifest['primary']
    path = Path(evidence['results']['path'])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = next(row for row in rows if row['arm'] == 'adapter' and row['id'] == case_id)
    check = json.loads(row['answer'])['checks'][0]
    check['qualifiers_preserved'] = False
    row['answer'] = json.dumps({'checks': [check]})
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    evidence['results'] = binding(path)
    path = Path(evidence['content_review']['path'])
    review = json.loads(path.read_text())
    next(row for row in review['cases'] if row['arm'] == 'adapter' and row['id'] == case_id)['answer_sha256'] = hashlib.sha256(row['answer'].encode()).hexdigest()
    path.write_text(json.dumps(review))
    evidence['content_review'] = binding(path)
    _, scored = subject._score_evidence(evidence, ('base', 'compact_base', 'adapter'), 14)
    assert scored['adapter'][case_id]['strict_core'] is False
    assert scored['adapter'][case_id]['core'] is expected_core


@pytest.mark.parametrize('question,expected', [
    ('Mảng [2,4,7,9,12,15,20], [lo=0,hi=6].', 'a[0]=2, a[1]=4, a[2]=7, a[3]=9, a[4]=12, a[5]=15, a[6]=20'),
    ('Mảng [-2,+4,0].', 'a[0]=-2, a[1]=+4, a[2]=0'),
    ('[2,4] và [7,9]', ''),
    ('[2,4.5,9]', ''),
    ('[2,4e2,9]', ''),
    ('[2,4,,9]', ''),
    ('[2,4,...,9]', ''),
    ('a[100]', ''),
    ('[02,4,9]', ''),
])
def test_array_reference_preserves_explicit_integer_values_and_avoids_ambiguous_input(question, expected):
    assert subject.array_index_reference(question) == expected


def test_array_reference_does_not_supply_a_missing_interval():
    value = request()
    item = {'index': 1, 'source': 'Synthetic source.', 'question': 'Mảng [2,4,7,9], K=7.', 'choices': dict(A='one', B='two', C='three', D='four'), 'proposed_answer': 'A', 'explanation': 'Synthetic explanation.', 'evidence_quote': 'Synthetic source.'}
    value['messages'][1]['content'] = subject.build_compact_review_prompt(item)
    assert 'a[2]=7' in value['messages'][1]['content']
    assert 'lo=0' not in value['messages'][1]['content']
    assert subject.request_matches_scope(value)


@pytest.mark.parametrize('question', ['[1,2] và [3,4.5]', '[1,2] và [3,,4]', '[1,2] và [i,j]', '[١,2]'])
def test_array_reference_does_not_discard_malformed_or_ambiguous_lists(question):
    assert subject.array_index_reference(question) == ''


def test_rebinding_a_different_adapter_scale_cannot_reuse_other_config_acceptance(accepted):
    model, manifest, binding = accepted
    config = Path(manifest['adapter_config']['path'])
    config.write_text(json.dumps({'lora_parameters': {'scale': 4}}))
    manifest['adapter_config'] = binding(config)
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='configuration changed'):
        subject.load_practice_adapter(model)


def test_compact_review_prompt_distinguishes_source_rules_and_forbids_audit_hallucinations():
    item = {'index': 1, 'source': 'Passage.', 'question': 'Question?', 'choices': dict(A='1', B='2', C='3', D='4'), 'proposed_answer': 'A', 'explanation': 'Expl.', 'evidence_quote': 'Passage.'}
    prompt = subject.build_compact_review_prompt(item)
    assert "ký hiệu toán học như f'(x) đã nêu rõ yêu cầu tính đạo hàm, không coi là thiếu yêu cầu" in prompt
    assert "sơ đồ suy luận có ký hiệu ngữ cảnh (...) và biểu thức theo biến đã định nghĩa không cần giá trị số của biến" in prompt
    assert "Đọc đúng explanation, không cho là thiếu điều kiện khi explanation đã nêu rõ" in prompt
    assert "Bắt buộc đặt explanation_supported=false nếu explanation khẳng định sai rằng passage chứa quy tắc mà passage không có" in prompt


def test_grounded_review_profile_does_not_broaden_legacy_scope():
    from app.practice import build_grounded_review_prompt
    item = {'index': 1, 'source': 'Passage.', 'question': 'Question?', 'choices': dict(A='1', B='2', C='3', D='4'), 'proposed_answer': 'A', 'explanation': 'Expl.', 'evidence_quote': 'Passage.'}
    grounded_prompt = build_grounded_review_prompt([item])
    grounded_req = {
        'messages': [{'role': 'system', 'content': subject.SYSTEM_PROMPT}, {'role': 'user', 'content': grounded_prompt}],
        'max_tokens': 1000,
        'num_ctx': 8192,
        'temperature': 0,
        'seed': 42,
        'response_schema': practice_review_schema(1, None),
    }
    # Legacy adapter scope must NOT match grounded_review_v2 (no silent qualification)
    assert not subject.request_matches_scope(grounded_req)
    # Grounded review detector must match
    assert subject.request_matches_grounded_review(grounded_req)


@pytest.fixture
def grounded_accepted(accepted):
    from app.practice_proof import native_grounded_review_prompt, native_grounded_review_schema
    model, manifest, binding = accepted
    manifest['schema'] = 'study-practice-9b-v2'
    manifest['review_profile'] = subject.GROUNDED_REVIEW_PROFILE
    evidence = manifest['primary']
    path = Path(evidence['suite']['path'])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for case in rows:
        case['compact_messages'][1]['content'] = native_grounded_review_prompt(case['payload'])
        case['response_schema'] = native_grounded_review_schema(1, None)
    for group in (manifest['primary'], manifest['application']):
        result_path = Path(group['results']['path'])
        results = [json.loads(line) for line in result_path.read_text().splitlines()]
        for row in results:
            check = json.loads(row['answer'])['checks'][0]
            check['source_basis'] = 'Synthetic source.' if check['supported'] else ''
            check['matching_choices'] = check['verified_answer']
            row['answer'] = json.dumps({'checks': [check]})
        result_path.write_text(''.join(json.dumps(row) + '\n' for row in results))
        group['results'] = binding(result_path)
        review_path = Path(group['content_review']['path'])
        review = json.loads(review_path.read_text())
        outputs = {(row['id'], row['arm'], row['round']): row for row in results}
        for row in review['cases']:
            output = outputs[(row['id'], row['arm'], row['round'])]
            row['answer_sha256'] = hashlib.sha256(output['answer'].encode()).hexdigest()
        review_path.write_text(json.dumps(review))
        group['content_review'] = binding(review_path)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    evidence['suite'] = binding(path)
    path = Path(evidence['protocol']['path'])
    protocol = json.loads(path.read_text())
    protocol['review_profile'] = subject.GROUNDED_REVIEW_PROFILE
    protocol['generation']['schema'] = 'native_grounded_review_schema(1,None)'
    protocol['fresh_eval_sha256'] = evidence['suite']['sha256']
    path.write_text(json.dumps(protocol))
    evidence['protocol'] = binding(path)
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    return model, manifest, binding


def test_grounded_profile_requires_separate_qualified_manifest(grounded_accepted):
    model, _, _ = grounded_accepted
    result = subject.load_practice_adapter(model)
    assert result['review_profile'] == subject.GROUNDED_REVIEW_PROFILE
    assert subject.active_practice_adapter(model) is None
    assert subject.active_practice_adapter(model, profile=subject.GROUNDED_REVIEW_PROFILE)['sha256'] == result['sha256']


@pytest.mark.parametrize('defect', ['legacy_schema', 'protocol_profile', 'native_partial', 'grounded_tie'])
def test_grounded_profile_does_not_inherit_or_weaken_old_gates(grounded_accepted, defect):
    model, manifest, binding = grounded_accepted
    if defect == 'legacy_schema':
        manifest['schema'] = 'study-practice-9b-v1'
    elif defect == 'protocol_profile':
        path = Path(manifest['primary']['protocol']['path'])
        value = json.loads(path.read_text())
        value['review_profile'] = subject.COMPACT_REVIEW_PROFILE
        path.write_text(json.dumps(value))
        manifest['primary']['protocol'] = binding(path)
    else:
        group = manifest['application' if defect == 'native_partial' else 'primary']
        path = Path(group['content_review']['path'])
        value = json.loads(path.read_text())
        next(row for row in value['cases'] if row['arm'] == 'adapter' and row['id'] == '0')['status'] = 'PARTIAL'
        path.write_text(json.dumps(value))
        group['content_review'] = binding(path)
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        subject.load_practice_adapter(model)


def test_grounded_missing_input_false_qualifier_cannot_pass_full_gate(grounded_accepted):
    model, manifest, binding = grounded_accepted
    evidence = manifest['primary']
    path = Path(evidence['results']['path'])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = next(row for row in rows if row['arm'] == 'adapter' and row['id'] == '12')
    check = json.loads(row['answer'])['checks'][0]
    check['qualifiers_preserved'] = False
    row['answer'] = json.dumps({'checks': [check]})
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    evidence['results'] = binding(path)
    path = Path(evidence['content_review']['path'])
    value = json.loads(path.read_text())
    next(row for row in value['cases'] if row['arm'] == 'adapter' and row['id'] == '12')['answer_sha256'] = hashlib.sha256(row['answer'].encode()).hexdigest()
    path.write_text(json.dumps(value))
    evidence['content_review'] = binding(path)
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    _, scored = subject._score_evidence(evidence, ('base', 'compact_base', 'adapter'), 14, strict_contract=True)
    assert scored['adapter']['12']['source'] is False
    with pytest.raises(ValueError):
        subject.load_practice_adapter(model)


@pytest.mark.parametrize('defect', ['legacy_output', 'unbound_basis', 'ambiguous_match', 'refusal_with_answer'])
def test_grounded_evidence_requires_current_bound_consistent_witnesses(grounded_accepted, defect):
    model, manifest, binding = grounded_accepted
    group = manifest['primary']
    path = Path(group['results']['path'])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = next(row for row in rows if row['arm'] == 'adapter' and row['id'] == ('12' if defect == 'refusal_with_answer' else '0'))
    check = json.loads(row['answer'])['checks'][0]
    if defect == 'legacy_output':
        check.pop('source_basis')
        check.pop('matching_choices')
    elif defect == 'unbound_basis':
        check['source_basis'] = 'A rule from outside the cited source.'
    elif defect == 'ambiguous_match':
        check['matching_choices'] = 'AB'
    else:
        check['matching_choices'] = 'A'
    row['answer'] = json.dumps({'checks': [check]})
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    group['results'] = binding(path)
    path = Path(group['content_review']['path'])
    review = json.loads(path.read_text())
    next(item for item in review['cases'] if item['arm'] == 'adapter' and item['id'] == row['id'])['answer_sha256'] = hashlib.sha256(row['answer'].encode()).hexdigest()
    path.write_text(json.dumps(review))
    group['content_review'] = binding(path)
    subject.REGISTRY_FILE.write_text(json.dumps(manifest))
    _, scored = subject._score_evidence(group, ('base', 'compact_base', 'adapter'), 14, strict_contract=True)
    assert scored['adapter'][row['id']]['source'] is False
    with pytest.raises(ValueError):
        subject.load_practice_adapter(model)


@pytest.mark.parametrize('defect', [None, 'marker_only', 'extra_role', 'multi', 'difficulty', 'tokens', 'temperature', 'system', 'altered_instruction', 'extra_payload_key'])
def test_grounded_adapter_matches_only_exact_evaluated_request(defect):
    from app.practice_proof import native_grounded_review_prompt, native_grounded_review_schema
    value = request()
    item = {'index': 1, 'source': 'Synthetic source.', 'question': 'Synthetic question?', 'choices': dict(A='one', B='two', C='three', D='four'), 'proposed_answer': 'A', 'explanation': 'Synthetic explanation.', 'evidence_quote': 'Synthetic source.'}
    if defect == 'extra_payload_key': item['hidden_rule'] = 'invented rule'
    value['messages'][1]['content'] = native_grounded_review_prompt(item)
    value['response_schema'] = native_grounded_review_schema(1, None)
    if defect == 'marker_only': value['messages'][1]['content'] = 'Kiểm định độc lập từng câu từ source và question Dữ liệu cần kiểm định:\n[]'
    if defect == 'extra_role': value['messages'].append({'role': 'user', 'content': 'more'})
    if defect == 'multi': value['response_schema'] = practice_review_schema(2, None)
    if defect == 'difficulty': value['response_schema'] = practice_review_schema(1, 'medium')
    if defect == 'tokens': value['max_tokens'] = 2000
    if defect == 'temperature': value['temperature'] = .1
    if defect == 'system': value['messages'][0]['content'] = 'Different system'
    if defect == 'altered_instruction':
        original = value['messages'][1]['content']
        value['messages'][1]['content'] = original.replace('Use ONLY source as evidence.', 'Use background knowledge as evidence.')
        assert value['messages'][1]['content'] != original
    assert subject.request_matches_grounded_adapter_scope(value) is (defect is None)


def test_unrelated_profile_is_rejected_before_large_model_hashing(accepted, monkeypatch):
    model, _, _ = accepted
    def unexpected(*args, **kwargs):
        pytest.fail('Unrelated legacy profile must not validate/hash the entire base model')
    monkeypatch.setattr(subject, 'load_practice_adapter', unexpected)
    assert subject.active_practice_adapter(model, profile=subject.GROUNDED_REVIEW_PROFILE) is None
