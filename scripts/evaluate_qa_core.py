"""Opt-in real-model QA evidence. Never treats keyword overlap as semantic PASS.

Run from an isolated checkout with model/runtime links and an explicit output
folder. All DBs are synthetic. The production coordinator directory supplies locks
only; no application DB, source or model is written. Semantic review is separate.
"""
import argparse
import hashlib
import json
import sqlite3
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=ROOT/'tests/fixtures/qa_core/acceptance.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--coordinator-data', type=Path, required=True)
    parser.add_argument('--cases', nargs='*')
    args = parser.parse_args()
    output = args.output.resolve()
    coord = args.coordinator_data.resolve()
    if output == coord or coord in output.parents or output == ROOT/'data':
        parser.error('Output must be outside the production data directory')
    output.mkdir(parents=True, exist_ok=True)
    from app.config import DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL, MLX_MODEL_DIR, MLX_CONTEXT_TOKENS
    from app.dialogue import DialogueGate
    from app.gpu_lock import GPULockManager
    from app.ollama_client import OllamaClient
    from app.bilingual import LocalTranslator
    from app.searcher import HybridSearcher
    from app.rag_agent import PsychologyAgent
    from app.trained_client import TrainedModelClient
    from app.active_registry import active_registry

    class RecordedRealClient(TrainedModelClient):
        def __init__(self):
            super().__init__()
            self.calls = []
        def chat_stream(self, **kwargs):
            started = time.monotonic()
            kwargs['seed'] = 220
            raw = ''.join(super().chat_stream(**kwargs))
            self.calls.append({'raw':raw, 'seconds':time.monotonic()-started,
                               'done_reason':self.last_done_reason,
                               'num_ctx':kwargs.get('num_ctx'), 'max_tokens':kwargs.get('num_predict')})
            yield raw

    fixture_bytes = args.fixture.read_bytes()
    fixture = json.loads(fixture_bytes)
    selected = [c for c in fixture['cases'] if not args.cases or c['id'] in args.cases]
    if not selected or (args.cases and set(args.cases)-{c['id'] for c in selected}):
        parser.error('Select at least one existing case')
    active_adapter = active_registry.get_active_adapter_dir()
    if active_adapter is not None:
        raise RuntimeError('This fixed baseline suite requires the existing unadapted base; do not change the registry')
    report = {'started':datetime.now(timezone.utc).isoformat(),
              'fixture_sha256':hashlib.sha256(fixture_bytes).hexdigest(),
              'model':DEFAULT_CHAT_MODEL, 'embedding_model':DEFAULT_EMBED_MODEL,
              'adapter':None, 'context':MLX_CONTEXT_TOKENS,
              'backend':'MLX subprocess via existing TrainedModelClient',
              'temperature':0, 'seed':220,
              'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('app/rag_agent.py','app/dialogue.py','app/answer_format.py','app/reasoning_policy.py','static/app.js','ui/app.py','scripts/evaluate_qa_core.py')},
              'model_config_sha256':hashlib.sha256((MLX_MODEL_DIR/'config.json').read_bytes()).hexdigest(),
              'tokenizer_sha256':hashlib.sha256((MLX_MODEL_DIR/'tokenizer.json').read_bytes()).hexdigest(),
              'weights_sha256':hashlib.file_digest((MLX_MODEL_DIR/'model.safetensors').open('rb'),'sha256').hexdigest(),
              'sources':fixture['sources'], 'semantic_review':'NOT_RUN', 'results':[]}
    def save():
        tmp=output/'report.tmp'; tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)); tmp.replace(output/'report.json')
    locks = GPULockManager(lock_path=coord/'runtime/gpu.lock', state_path=coord/'runtime/gpu_state.json')
    with DialogueGate(coord/'runtime/dialogue.lock').acquire(), locks.acquire_for_inference():
        if not TrainedModelClient.available():
            raise RuntimeError('Existing MLX runtime/model unavailable')
        path=output/'synthetic.db'
        if path.exists():
            raise FileExistsError('Use a fresh output folder; previous evidence is immutable')
        ollama=OllamaClient()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE chunks (id INTEGER PRIMARY KEY,doc_id INTEGER,book_title TEXT,filename TEXT,page_num INTEGER,chunk_index INTEGER,text TEXT,embedding BLOB)')
            db.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text,content='chunks',content_rowid='id')")
            for i,source in enumerate(fixture['sources'],1):
                vector=ollama.get_embedding(source['text'],model=DEFAULT_EMBED_MODEL)
                if not vector:
                    raise RuntimeError('Real embedding missing; do not silently fall back')
                db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',(i,source['doc_id'],source['title'],f"qa-{source['doc_id']}.md",source['page'],0,source['text'],struct.pack(f'{len(vector)}f',*vector)))
                db.execute('INSERT INTO chunks_fts(rowid,text) VALUES (?,?)',(i,source['text']))
        model=RecordedRealClient()
        agent=PsychologyAgent(searcher=HybridSearcher(path),ollama=model,translator=LocalTranslator(output/'translations.db'))
        save()
        for case in selected:
            before=len(model.calls); started=time.monotonic()
            params={key:case[key] for key in ('source_document_id','source_page','chat_history','processing_level') if key in case}
            params.setdefault('processing_level','standard')
            try:
                result=agent.process_query_sync(case['query'],model=DEFAULT_CHAT_MODEL,embed_model=DEFAULT_EMBED_MODEL,temperature=0,**params)
                calls=model.calls[before:]
                checks={'response_kind':result['response_kind']==case['expected_kind'],
                        'generation_count':result['generation_calls']==len(calls),
                        'citation_documents':all(c.get('doc_id') in case['gold_docs'] for c in result['citations'])}
                report['results'].append({'id':case['id'],'query':case['query'],'expected':case,
                    'seconds':time.monotonic()-started,'checks':checks,'result':result,'real_calls':calls})
            except Exception as exc:
                report['results'].append({'id':case['id'],'error':str(exc),'seconds':time.monotonic()-started})
            save()
            print(case['id'],report['results'][-1].get('checks',report['results'][-1].get('error')),flush=True)
    report['finished']=datetime.now(timezone.utc).isoformat(); save()
    # Structural checks never substitute a semantic acceptance decision.
    return 0 if all(r.get('checks') and all(r['checks'].values()) for r in report['results']) else 1

if __name__=='__main__':
    raise SystemExit(main())
