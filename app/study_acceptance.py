"""Validate the frozen study comparison from its original, file-bound evidence."""
import hashlib
import json
import math
from pathlib import Path

from app.config import SRC_DIR, TRAINING_MODEL_REPO, TRAINING_MODEL_REVISION

SUITE_HASH = '4752827e56b9d3e24bc6ec6b12bc479c5abbc5cacd016a0efafa9de656eb42d7'
BASE_RAW_HASH = '370726de91575058976721e02d59034f9e66904d6751ec6bbbb60724ac3525ea'
BASE_REVIEW_HASH = 'd079104e18add16a75993dd715e352e2f5ec7f1147012bee93fd397d51ef24a8'
NO_SOURCE = {f'codex-holdout-{i}' for i in range(25, 29)}
RANK = dict(zip(('fail', 'partial', 'pass'), (0, 1, 2)))


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def text_hash(value):
    need(isinstance(value, str), 'Expected actual text')
    return hashlib.sha256(value.encode()).hexdigest()


def cases(data):
    raw = data.get('cases')
    if isinstance(raw, list):
        need(len(raw) == 28 and all(isinstance(c, dict) for c in raw), 'Incomplete/invalid case list')
        result = {c.get('id'): c for c in raw}
    elif isinstance(raw, dict):
        result = raw
    else:
        raise ValueError('Missing actual cases')
    need(len(result) == 28 and all(isinstance(k, str) and isinstance(v, dict) for k,v in result.items()),
         'Missing or duplicate cases')
    return result


def identity_files(directory, hashes, minimum, file_hash):
    need(isinstance(hashes, dict) and minimum <= set(hashes), 'Incomplete evaluated file identity')
    for name, expected in hashes.items():
        need(isinstance(name, str) and Path(name).name == name, 'Unsafe identity file name')
        p = directory / name
        need(p.is_file() and file_hash(p) == expected, 'Evaluated file changed: ' + name)


def equal_metrics(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(
            equal_metrics(actual[k], v) for k,v in expected.items())
    if isinstance(expected, float):
        return isinstance(actual, (int,float)) and not isinstance(actual,bool) and math.isclose(actual,expected,abs_tol=1e-12)
    return type(actual) is type(expected) and actual == expected


def assess_dossier(dossier, adapter, base, file_hash):
    need(dossier.get('schema') == 'codex-study-acceptance-v1', 'Unexpected dossier schema')
    need(dossier.get('reviewer') == 'Codex' and dossier.get('reviewed_by_codex') is True, 'Independent review missing')
    need(isinstance(dossier.get('trial_id'),str) and bool(dossier['trial_id']), 'Trial identity missing')
    bindings = dossier.get('bindings')
    required = {'suite','protocol','baseline_raw','baseline_review','candidate_raw','candidate_review','decision'}
    need(isinstance(bindings,dict) and required <= set(bindings), 'Incomplete bound artifacts')
    data = {}; paths = {}
    for key in required:
        b = bindings[key]
        need(isinstance(b,dict), 'Invalid binding')
        p = Path(b.get('path',''))
        need(p.is_absolute() and p.is_file(), 'Binding requires an existing absolute file')
        need(file_hash(p) == b.get('sha256'), 'Artifact bytes changed: '+key)
        value = json.loads(p.read_text())
        need(isinstance(value,dict), 'Artifact must contain structured evidence')
        paths[key] = p; data[key] = value
    for key, pinned in [('suite',SUITE_HASH),('baseline_raw',BASE_RAW_HASH),('baseline_review',BASE_REVIEW_HASH)]:
        need(bindings[key]['sha256'] == pinned, 'Original frozen evidence differs: '+key)
    suite, proto = data['suite'], data['protocol']
    frozen = cases(suite)
    need(proto.get('suite',{}).get('sha256') == SUITE_HASH, 'Protocol refers to another suite')
    acceptance = suite['acceptance']
    need(all(proto.get('acceptance',{}).get(k) == v for k,v in acceptance.items()), 'Frozen threshold weakened/changed')
    need(proto['acceptance'].get('max_retention_drop',0) == 0, 'Retention drop protection weakened')
    need(set(proto.get('no_source_case_ids',[])) == NO_SOURCE, 'Refusal controls changed')
    model = dossier.get('model_identity'); adapter_identity = dossier.get('adapter_identity')
    need(isinstance(model,dict) and isinstance(adapter_identity,dict), 'Evaluated base/adapter identities missing')
    need(model.get('repo') == TRAINING_MODEL_REPO and model.get('revision') == TRAINING_MODEL_REVISION,
         'Base repo/revision differs')
    base = Path(base).resolve(); adapter = Path(adapter).resolve()
    need(Path(adapter_identity.get('path','')).resolve() == adapter, 'Adapter path differs')
    br, cr = data['baseline_raw'], data['candidate_raw']
    bv, cv = data['baseline_review'], data['candidate_review']
    base_hashes = model.get('files_sha256'); adapter_hashes = adapter_identity.get('files_sha256')
    need(base_hashes == bv.get('base_files_sha256') == cv.get('base_files_sha256') == cr.get('base_files_sha256'),
         'Base identity differs across inference/review/dossier')
    need(adapter_hashes == cr.get('adapter_files_sha256'), 'Adapter identity differs from actual inference')
    identity_files(base,base_hashes,{'model.safetensors','config.json','tokenizer.json'},file_hash)
    identity_files(adapter,adapter_hashes,{'adapters.safetensors','adapter_config.json'},file_hash)
    need(br.get('adapter') is None and Path(cr.get('adapter','')).resolve() == adapter, 'Wrong evaluated adapter')
    need(cr.get('baseline_raw_sha256') == BASE_RAW_HASH, 'Candidate is not paired with original baseline')
    raw_maps = []
    for raw in (br,cr):
        need(raw.get('status') == 'COMPLETED', 'Inference did not complete')
        need(raw.get('suite_sha256') == SUITE_HASH and raw.get('decoding') == suite['decoding'], 'Inference protocol changed')
        need(Path(raw.get('model_dir','')).resolve() == base, 'Different inference base path')
        mapped = cases(raw); need(set(mapped)==set(frozen), 'Different inference coverage'); raw_maps.append(mapped)
    reviews = []
    for rev,key in [(bv,'baseline_raw'),(cv,'candidate_raw')]:
        need(rev.get('complete') is True and rev.get('content_review_complete') is True and rev.get('reviewer')=='Codex',
             'Content review incomplete')
        need(rev.get('suite_sha256') == SUITE_HASH and rev.get('raw_output_sha256') == bindings[key]['sha256'],
             'Review is not bound to exact inference')
        mapped = cases(rev); need(set(mapped)==set(frozen), 'Different review coverage'); reviews.append(mapped)
    for cid, original in frozen.items():
        source = SRC_DIR / original['source_group']
        need(source.resolve().is_relative_to(SRC_DIR.resolve()) and source.is_file(), 'Missing original source')
        need(file_hash(source)==original['source_sha256'], 'Original source changed')
        inp = text_hash(json.dumps(original['messages'],ensure_ascii=False,sort_keys=True))
        need(inp == original['input_sha256'], 'Frozen prompt hash differs')
        for index,(raw,review) in enumerate(zip(raw_maps,reviews)):
            actual, reviewed = raw[cid], review[cid]
            for key in ('question','source_group','source_sha256','page','passage','input_sha256'):
                need(actual.get(key)==original[key], cid+': changed frozen '+key)
            # The pinned original baseline predates raw messages storage.
            if index == 1 or 'messages' in actual:
                need(actual.get('messages')==original['messages'],cid+': changed messages')
            answer_hash = text_hash(actual.get('answer'))
            need(actual.get('answer_sha256') == answer_hash, cid+': changed raw output hash')
            need(reviewed.get('grade') in RANK and isinstance(reviewed.get('reason'),str) and reviewed['reason'].strip(),
                 cid+': missing content grade/reason')
            need(reviewed.get('source_verified') is True,cid+': source unreviewed')
            for key,expected in [('input_sha256',inp),('source_sha256',original['source_sha256']),
                                 ('passage_sha256',text_hash(original['passage'])),('answer_sha256',answer_hash)]:
                need(reviewed.get(key)==expected,cid+': review binding differs '+key)
    groups = {kind:[cid for cid,c in frozen.items() if c['kind']==kind] for kind in ('target','retention')}
    need(len(groups['target'])==20 and len(groups['retention'])==8,'Frozen groups changed')
    scores = {}
    for kind,ids in groups.items():
        scores[kind] = {}
        for key,review in zip(('baseline','candidate'),reviews):
            grades = [review[cid]['grade'] for cid in ids]
            scores[kind][key] = {g:grades.count(g) for g in RANK}
            scores[kind][key]['pass_rate'] = grades.count('pass')/len(ids)
    declined = [cid for cid in frozen if RANK[reviews[1][cid]['grade']] < RANK[reviews[0][cid]['grade']]]
    ret = [cid for cid in declined if cid in groups['retention']]
    critical = [cid for cid in declined if frozen[cid].get('critical')]
    no_source = [cid for cid in sorted(NO_SOURCE) if reviews[1][cid]['grade']!='pass']
    gain = scores['target']['candidate']['pass_rate']-scores['target']['baseline']['pass_rate']
    drop = scores['retention']['baseline']['pass_rate']-scores['retention']['candidate']['pass_rate']
    gates = {'target_accuracy':scores['target']['candidate']['pass_rate']>=acceptance['min_target_accuracy'],
             'absolute_gain':gain+1e-12>=acceptance['min_absolute_gain'], 'retention_drop':drop<=0,
             'retention_regressions':len(ret)<=acceptance['max_retention_regressions'],
             'critical_regressions':len(critical)<=acceptance['max_critical_regressions'], 'no_source_all_pass':not no_source}
    expected = {'scores':scores,'absolute_gain':gain,'retention_drop':drop,'retention_regressions':ret,
                'critical_regressions':critical,'no_source_failures':no_source,'gates':gates,
                'failed_gates':[k for k,v in gates.items() if not v]}
    decision = data['decision']
    need(decision.get('schema')=='codex-study-paired-decision-v1' and decision.get('reviewer')=='Codex', 'Decision provenance missing')
    need(all(equal_metrics(decision.get(k),v) for k,v in expected.items()), 'Decision differs from recomputed comparison')
    need(all(gates.values()) and decision.get('status')=='ACCEPTED' and decision.get('promoted') is True,
         'Quality gates do not allow promotion')
    return expected
