"""Source-bound, selective, immutable releases; no model weights are loaded.

Bad proposals become durable sample revisions. Previously accepted revisions and
all 35 exact v9 messages/IDs remain available, even when an amendment fails.
Semantic approval must be supplied separately and bound to the exact content.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.sample_ledger import SampleLedger
from app.config import MLX_MODEL_DIR

PARENT_DATA = 'b094b041159f2604db6058401159fcdf20c9f51b45b22a999f4d33e8ba2b34a5'
PARENT_MANIFEST = '84f4fa58462c1160c9dfea7b158d04e7bf43048380e592c8f4d69511bb304b71'


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()


def rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def write_new(path, data):
    with Path(path).open('x', encoding='utf-8') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())


def exact_tokens(messages):
    program = '''import json,sys
from transformers import AutoTokenizer
from pathlib import Path
v=json.load(sys.stdin)
from app.model_resolver import validate_native_model_config
validate_native_model_config(Path(v['model']))
t=AutoTokenizer.from_pretrained(v['model'],local_files_only=True,trust_remote_code=False)
print(json.dumps([len(t.encode(t.apply_chat_template(m,tokenize=False,add_generation_prompt=False),add_special_tokens=False)) for m in v['messages']]))
'''
    p = subprocess.run([str(ROOT/'.train-venv/bin/python'), '-c', program],
        input=json.dumps({'model':str(MLX_MODEL_DIR), 'messages':messages}),
        text=True, capture_output=True, timeout=120, check=True)
    lengths = json.loads(p.stdout)
    if len(lengths) != len(messages) or any(type(n) is not int or n<1 for n in lengths):
        raise ValueError('Invalid exact tokenizer response')
    return lengths


def curate(proposals, release, history, token_counter=exact_tokens, parent=None):
    import pymupdf
    parent = Path(parent) if parent is not None else ROOT/'data/training/v9_multidomain_grounded'
    original_parent = parent.name == 'v9_multidomain_grounded'
    binding = {} if original_parent else json.loads((parent/'approval.json').read_text())
    parent_data = PARENT_DATA if original_parent else binding['dataset_sha256']
    parent_manifest = PARENT_MANIFEST if original_parent else binding['approved_manifest_sha256']
    if sha((parent/'train.jsonl').read_bytes()+(parent/'valid.jsonl').read_bytes()) != parent_data:
        raise ValueError('Accepted parent changed')
    if file_sha(parent/'approved_manifest.jsonl') != parent_manifest:
        raise ValueError('Accepted parent manifest changed')
    parent_review_path = ROOT/'data/evaluation/codex-v9-final-content-review-2026-10-03.json' if original_parent else parent/'content_review.json'
    parent_review = json.loads(parent_review_path.read_text())
    if parent_review.get('reviewer') != 'Codex' or parent_review.get('dataset_sha256') != parent_data or (original_parent and parent_review.get('status') != 'DATA_CONTENT_REVIEW_PASS_PENDING_EXECUTION_GATES') or (not original_parent and (not parent_review.get('content_review_complete') or parent_review.get('manifest_sha256') != parent_manifest)):
        raise ValueError('Original parent content review unavailable')
    parent_checks = {r['id']:r for r in parent_review['source_checks' if original_parent else 'cases']}
    old_manifest = rows(parent/'approved_manifest.jsonl')
    old_rows = {s:rows(parent/(s+'.jsonl')) for s in ('train','valid')}
    system = old_rows['train'][0]['messages'][0]['content']
    suite = json.loads((ROOT/'data/evaluation/codex-study-frozen-holdout-2026-10-03.json').read_text())
    forbidden = {unicodedata.normalize('NFC', x) for x in suite['never_train_source_groups']}
    forbidden.add(unicodedata.normalize('NFC', 'sắp xếp.pdf'))
    forbidden_hashes = {c['source_sha256'] for c in suite['cases']}
    groups = {unicodedata.normalize('NFC',m['source_file']):m['split'] for m in old_manifest}
    ledger = SampleLedger(history)
    requested = {m['id'] for m in old_manifest}
    parent_ids = set(requested)
    hashes = {}; pages = {}; pending = []

    def source_check(m):
        src = ROOT/'src'/m['source_file']
        if not src.resolve().is_relative_to((ROOT/'src').resolve()) or not src.is_file():
            raise ValueError('Source missing/outside src')
        name = unicodedata.normalize('NFC',m['source_file'])
        if src not in hashes: hashes[src] = file_sha(src)
        if name in forbidden or hashes[src] in forbidden_hashes:
            raise ValueError('Frozen/gold whole-file source excluded')
        if m['source_file_sha256'] != hashes[src]: raise ValueError('Current source hash differs')
        if m['split'] not in ('train','valid'): raise ValueError('Invalid split')
        if name in groups and groups[name] != m['split']: raise ValueError('Whole-file split conflict')
        key = (src,m['page'])
        if key not in pages:
            with pymupdf.open(src) as pdf:
                if not 0 < m['page'] <= len(pdf): raise ValueError('Page outside source')
                pages[key] = pdf[m['page']-1].get_text()
        raw = pages[key]
        if m.get('source_type') == 'reviewed_ocr':
            from app.vision_ocr import VisionOCRManager
            entry = VisionOCRManager().get_cached_transcript(m['ocr_cache_key'])
            if not entry or not VisionOCRManager.is_allowed_in_training(entry)[0] or entry.get('needs_review') or entry.get('source_hash') != hashes[src] or entry.get('source_relpath') != m['source_file'] or entry.get('page_num') != m['page']:
                raise ValueError('OCR not verified for exact source/page')
            if file_sha(ROOT/'data/ocr_cache'/(m['ocr_cache_key']+'.json')) != m['ocr_record_sha256']:
                raise ValueError('Approved OCR revision changed')
            image = Path(entry['image_path'])
            if not image.resolve().is_relative_to((ROOT/'data').resolve()) or file_sha(image) != entry.get('image_hash') or entry.get('image_hash') != m['ocr_image_sha256']:
                raise ValueError('OCR image binding differs')
            raw = entry['text']
        i=m['span_start'];j=m['span_end']
        if type(i) is not int or type(j) is not int or not 0<=i<j<=len(raw): raise ValueError('Invalid native span')
        if raw[i:j] != m['passage'] or sha(m['passage']) != m['passage_sha256']:
            raise ValueError('Not an exact native source span')
        groups[name] = m['split']

    for m in old_manifest:
        check = parent_checks[m['id']]
        if not check.get('source_verified') or check['answer_sha256'] != sha(m['reference']) or check['passage_sha256'] != sha(m['passage']):
            raise ValueError('Parent review differs from retained record')
        source_check(m)
        proof = dict(check, reviewer='Codex', grade='pass', reason='Exact approved parent retained', parent_review_sha256=file_sha(parent_review_path))
        ledger.record(m['id'],m,'accepted','Retained exact approved parent content',proof)

    for p in proposals:
        sid = p['id']; requested.add(sid)
        ledger.record(sid,p,'proposed',p.get('revision_reason','New or amended proposal'))
        try:
            if sid in parent_ids: raise ValueError('Parent IDs cannot be amended')
            if p.get('quarantine_reason'): raise ValueError(p['quarantine_reason'])
            source_check(p)
            ledger.record(sid,p,'source_verified','Actual current native source verified')
            review = p.get('review',{})
            required = {'source_file_sha256':p['source_file_sha256'], 'passage_sha256':sha(p['passage']),
                        'query_sha256':sha(p['query']), 'answer_sha256':sha(p['reference'])}
            if review.get('reviewer')!='Codex' or review.get('grade')!='pass' or not review.get('source_verified') or not review.get('reason') or any(review.get(k)!=v for k,v in required.items()):
                raise ValueError('Exact content review missing/different')
            messages = [{'role':'system','content':system},
                        {'role':'user','content':f"Đoạn trích:\n[S1] {p['passage']}\n\nCâu hỏi: {p['query']}"},
                        {'role':'assistant','content':p['reference']}]
            pending.append((p,messages,review))
        except (ValueError,KeyError,TypeError,FileNotFoundError) as e:
            ledger.record(sid,p,'quarantined',str(e))

    lengths = token_counter([messages for p,messages,r in pending]) if pending else []
    for (p,messages,review),n in zip(pending,lengths):
        if n>1024:
            ledger.record(p['id'],p,'quarantined',f'Exact token length {n} exceeds 1024; no truncation')
        else:
            ledger.record(p['id'],p,'accepted','Exact source, semantic review and tokens pass',dict(review,exact_tokens=n))
    accepted = {e['stable_id']:e for e in ledger.accepted() if e['stable_id'] in requested}
    manifest = list(old_manifest); outputs = {s:list(old_rows[s]) for s in old_rows}
    seen = {json.dumps(r,ensure_ascii=False,sort_keys=True) for v in outputs.values() for r in v}
    for sid,e in sorted(accepted.items()):
        if sid in parent_ids: continue
        p=e['payload']; source_check(p)
        m={k:p[k] for k in ('id','split','domain','source_file','source_file_sha256','page','span_start','span_end','passage','passage_sha256','query','reference')}
        m['kind']=p.get('kind','target')
        if p.get('source_type') == 'reviewed_ocr':
            for key in ('source_type','ocr_cache_key','ocr_record_sha256','ocr_image_sha256'):
                m[key] = p[key]
        r={'messages':[{'role':'system','content':system},{'role':'user','content':f"Đoạn trích:\n[S1] {p['passage']}\n\nCâu hỏi: {p['query']}"},{'role':'assistant','content':p['reference']}]}
        signature=json.dumps(r,ensure_ascii=False,sort_keys=True)
        if signature in seen:
            ledger.record(sid,p,'quarantined','Duplicate record; accepted original retained in history'); continue
        seen.add(signature);manifest.append(m);outputs[m['split']].append(r)
    final_tokens=token_counter([r['messages'] for s in outputs for r in outputs[s]])
    if not final_tokens or max(final_tokens)>1024: raise ValueError('Final retained release exceeds budget')
    release=Path(release);release.mkdir(parents=True,exist_ok=False)
    for split,rs in outputs.items():write_new(release/(split+'.jsonl'),''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rs))
    write_new(release/'approved_manifest.jsonl',''.join(json.dumps(m,ensure_ascii=False)+'\n' for m in manifest))
    report={'release_version':release.name,'train_samples':len(outputs['train']),'valid_samples':len(outputs['valid']),
        'source_groups':len({m['source_file'] for m in manifest}),'audit_complete':True,'whole_file_isolation_verified':True,
        'holdout_leaks':0,'cross_book_pairs':0,'parent_retained':len(old_manifest),'max_exact_tokens':max(final_tokens),
        'history_directory':str(Path(history).resolve()),'training_approved':False}
    write_new(release/'summary.json',json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--proposals',type=Path,required=True)
    p.add_argument('--release',type=Path,default=ROOT/'data/training/v10_multidomain_grounded')
    p.add_argument('--history',type=Path,default=ROOT/'data/training/sample_history_v10')
    p.add_argument('--parent',type=Path)
    a=p.parse_args();print(json.dumps(curate(json.loads(a.proposals.read_text()),a.release,a.history,parent=a.parent),ensure_ascii=False))


if __name__=='__main__':main()
