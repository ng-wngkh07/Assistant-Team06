"""Publisher-only resumable vector migration; extracted text and source IDs stay intact.

Build into a separate SQLite staging file. Only a complete, validated staging
index can be applied, in one transaction, after backing up the serving database.
This command never extracts documents, trains a model, or changes source reviews.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import time

import numpy as np

from app.config import BASE_DIR, DB_PATH
from app.gpu_lock import gpu_coordinator
from app.indexer import pack_vector, unpack_vector
from app.native_embedding import NativeEmbeddingClient
from app.source_quality import PIPELINE_VERSION


def validate_extraction(source):
    """Do not spend model work encoding obsolete or unbound derived text."""
    try:
        invalid = source.execute('''
            SELECT count(*) FROM documents d
            LEFT JOIN document_source_quality q ON q.doc_id=d.id
            WHERE d.pipeline_version IS NULL OR d.pipeline_version != ?
               OR q.doc_id IS NULL OR q.pipeline_version IS NULL OR q.pipeline_version != ?
               OR q.filename != d.filename OR q.file_hash != d.file_hash
               OR q.filename IS NULL OR q.file_hash IS NULL
        ''', (PIPELINE_VERSION, PIPELINE_VERSION)).fetchone()[0]
    except sqlite3.OperationalError as exc:
        raise ValueError('Current source extraction metadata is required before vector rebuilding') from exc
    if invalid:
        raise ValueError(f'{invalid} documents have obsolete or unbound extraction; rebuild source text first')


def nonvector_fingerprint(db):
    """Hash every ordinary table except replaceable vectors and FTS mirrors."""
    digest = hashlib.sha256()
    for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name.startswith(('sqlite_', 'chunks_fts')) or name in {
            'corpus_vector_provenance', 'corpus_embedding_cache',
        }:
            continue
        quote = lambda s: '"' + s.replace('"', '""') + '"'
        cols = [r[1] for r in db.execute('PRAGMA table_info(' + quote(name) + ')')]
        if name == 'chunks':
            cols = [c for c in cols if c != 'embedding']
        fields = ','.join(map(quote, cols))
        digest.update(json.dumps([name, cols]).encode())
        for row in db.execute('SELECT ' + fields + ' FROM ' + quote(name) + ' ORDER BY ' + fields):
            values = [hashlib.sha256(v).hexdigest() if isinstance(v, bytes) else v for v in row]
            digest.update(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode())
            digest.update(b'\n')
    return digest.hexdigest()


def valid_blob(blob, vector_hash, dimensions=1024):
    if not blob or len(blob) != dimensions * 4 or hashlib.sha256(blob).hexdigest() != vector_hash:
        return False
    vector = unpack_vector(blob)
    return bool(np.all(np.isfinite(vector)) and abs(float(np.linalg.norm(vector)) - 1.0) <= 1e-3)


def prepare_stage(database, stage, identity):
    if Path(database).resolve() == Path(stage).resolve():
        raise ValueError('Staging must differ from the serving database')
    with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        source.execute('BEGIN')
        validate_extraction(source)
        return _prepare_validated_stage(database, stage, identity, source)


def protocol_for_identity(identity: str) -> tuple[str, str]:
    if isinstance(identity, str) and identity.startswith('ollama-sha256:'):
        return 'bge-m3-ollama-l2-v1', '8192'
    return 'bge-cls-l2-v1', '2048'


def _prepare_validated_stage(database, stage, identity, source):
    stage.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(stage)) as out:
        with out:
            fingerprint = nonvector_fingerprint(source)
            out.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS texts(hash TEXT PRIMARY KEY,text TEXT NOT NULL,
                    vector BLOB,vector_sha256 TEXT);
                CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,text_hash TEXT NOT NULL);
            ''')
            existing = dict(out.execute('SELECT key,value FROM meta'))
            protocol, max_length = protocol_for_identity(identity)
            expected = {'database': str(database.resolve()), 'nonvector_sha256': fingerprint,
                        'model_identity': identity, 'max_length': max_length, 'protocol': protocol}
            if existing:
                if existing != expected:
                    raise ValueError('Source or embedding identity changed; use a new staging file')
            else:
                for chunk_id, text in source.execute('SELECT id,text FROM chunks ORDER BY id'):
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError(f'Empty source chunk {chunk_id}; cannot claim a full index')
                    text_hash = hashlib.sha256(text.encode()).hexdigest()
                    out.execute('INSERT OR IGNORE INTO texts(hash,text) VALUES(?,?)', (text_hash, text))
                    out.execute('INSERT INTO chunks VALUES(?,?)', (chunk_id, text_hash))
                out.executemany('INSERT INTO meta VALUES(?,?)', expected.items())
            return expected


def read_worker(process, timeout=180):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError('Native embedding batch exceeded its deadline')
    line = process.stdout.readline()
    if not line:
        raise RuntimeError('Native embedding worker exited before returning a result; inspect the local worker log')
    return json.loads(line)


def build_stage_ollama(database, stage, *, model='bge-m3', batch_size=16, limit=0):
    if not 1 <= batch_size <= 32 or limit < 0:
        raise ValueError('batch_size must be 1–32 and limit must be nonnegative')
    with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        validate_extraction(source)
    from app.ollama_client import OllamaClient
    client = OllamaClient()
    if not client.check_health():
        raise RuntimeError('Local Ollama server is not accessible')
    identity = client.embedding_identity(model)
    if not identity:
        raise RuntimeError(f'Could not verify artifact digest for Ollama model {model}')
    prepare_stage(database, stage, identity)

    started = time.monotonic()
    count = 0
    with contextlib.closing(sqlite3.connect(stage)) as out:
        missing = out.execute('SELECT hash,text FROM texts WHERE vector IS NULL ORDER BY length(text),hash').fetchall()
        if limit:
            missing = missing[:limit]
        if not missing:
            return {'completed_this_run': 0, 'remaining': 0}

        with gpu_coordinator.acquire_for_inference():
            for start in range(0, len(missing), batch_size):
                batch = missing[start:start + batch_size]
                texts = [row[1] for row in batch]
                vectors = client.get_batch_embeddings(texts, model=model)
                if client.embedding_identity(model) != identity or len(vectors) != len(batch):
                    raise RuntimeError('Ollama returned an incomplete or incompatible batch')
                for (text_hash, _), vector in zip(batch, vectors):
                    if not vector or len(vector) != 1024:
                        raise ValueError('Ollama returned invalid vector dimension')
                    arr = np.array(vector, dtype=np.float32)
                    if not np.all(np.isfinite(arr)):
                        raise ValueError('Ollama vector contains NaN or Inf')
                    norm = float(np.linalg.norm(arr))
                    if norm <= 0:
                        raise ValueError('Ollama vector has zero norm')
                    arr = arr / norm
                    blob = pack_vector(arr.tolist())
                    vector_hash = hashlib.sha256(blob).hexdigest()
                    if not valid_blob(blob, vector_hash):
                        raise ValueError('Ollama vector failed valid_blob check')
                    out.execute('UPDATE texts SET vector=?,vector_sha256=? WHERE hash=?', (blob, vector_hash, text_hash))
                out.commit()
                count += len(batch)
                print(json.dumps({'built': count, 'scheduled': len(missing), 'seconds': round(time.monotonic()-started, 2)}), flush=True)
            if client.embedding_identity(model) != identity:
                raise RuntimeError('Embedding artifact changed during the rebuild; staging cannot be applied')
            client.unload_resident_models()
        remaining = out.execute('SELECT count(*) FROM texts WHERE vector IS NULL').fetchone()[0]
    return {'completed_this_run': count, 'remaining': remaining, 'seconds': round(time.monotonic()-started, 2)}


def build_stage(database, stage, *, batch_size=16, limit=0, backend='native', model='bge-m3'):
    if backend == 'ollama':
        return build_stage_ollama(database, stage, model=model, batch_size=batch_size, limit=limit)
    if sys.platform != 'darwin':
        raise RuntimeError('This pinned MLX pack builder requires macOS; Ollama vectors need a separate artifact-bound pack')
    if not 1 <= batch_size <= 32 or limit < 0:
        raise ValueError('batch_size must be 1–32 and limit must be nonnegative')
    with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        validate_extraction(source)
    client = NativeEmbeddingClient()
    if not client.available():
        raise RuntimeError('The pinned local embedding runtime is unavailable')
    identity = client.get_identity()
    prepare_stage(database, stage, identity)
    started = time.monotonic()
    count = 0
    with contextlib.closing(sqlite3.connect(stage)) as out:
        missing = out.execute('SELECT hash,text FROM texts WHERE vector IS NULL ORDER BY length(text),hash').fetchall()
        if limit:
            missing = missing[:limit]
        if not missing:
            return {'completed_this_run': 0, 'remaining': 0}
        log_path = stage.with_suffix('.worker.log')
        with gpu_coordinator.acquire_for_inference() as fds, log_path.open('ab') as error_log:
            process = subprocess.Popen(
                [str(BASE_DIR / '.train-venv/bin/python'), '-u', '-m', 'app.mlx_embedding', '--stream'],
                cwd=BASE_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log,
                text=True, bufsize=1, pass_fds=tuple(fds),
            )
            try:
                process.stdin.write(json.dumps({'model_path': str(client.model_dir), 'max_length': 2048}) + '\n')
                process.stdin.flush()
                if read_worker(process).get('model_identity') != identity:
                    raise RuntimeError('Embedding worker artifact identity differs')
                for start in range(0, len(missing), batch_size):
                    batch = missing[start:start + batch_size]
                    process.stdin.write(json.dumps({'texts': [row[1] for row in batch]}, ensure_ascii=False) + '\n')
                    process.stdin.flush()
                    result = read_worker(process)
                    vectors = result.get('vectors', [])
                    if result.get('model_identity') != identity or len(vectors) != len(batch):
                        raise RuntimeError('Embedding worker returned an incomplete or incompatible batch')
                    for (text_hash, _), vector in zip(batch, vectors):
                        blob = pack_vector(vector)
                        vector_hash = hashlib.sha256(blob).hexdigest()
                        if not valid_blob(blob, vector_hash):
                            raise ValueError('Embedding worker returned a malformed vector')
                        out.execute('UPDATE texts SET vector=?,vector_sha256=? WHERE hash=?', (blob, vector_hash, text_hash))
                    out.commit()
                    count += len(batch)
                    print(json.dumps({'built': count, 'scheduled': len(missing), 'seconds': round(time.monotonic()-started, 2)}), flush=True)
                if client.get_identity() != identity:
                    raise RuntimeError('Embedding artifact changed during the rebuild; staging cannot be applied')
            finally:
                process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        remaining = out.execute('SELECT count(*) FROM texts WHERE vector IS NULL').fetchone()[0]
    return {'completed_this_run': count, 'remaining': remaining, 'seconds': round(time.monotonic()-started, 2)}


def apply_stage(database, stage, backup, *, expected_identity):
    paths = [Path(p).resolve() for p in (database, stage, backup)]
    if len(set(paths)) != 3 or backup.exists():
        raise ValueError('Database, stage and new backup must be distinct; backup must not exist')
    with contextlib.closing(sqlite3.connect(stage.resolve().as_uri() + '?mode=ro', uri=True)) as staged:
        staged.execute('BEGIN')
        meta = dict(staged.execute('SELECT key,value FROM meta'))
        if meta.get('model_identity') != expected_identity or meta.get('database') != str(database.resolve()):
            raise ValueError('Staging is bound to a different database or embedding artifact')
        rows = staged.execute('''SELECT c.id,t.text,t.hash,t.vector,t.vector_sha256
            FROM chunks c JOIN texts t ON t.hash=c.text_hash ORDER BY c.id''').fetchall()
        if not rows or any(hashlib.sha256(text.encode()).hexdigest() != text_hash or not valid_blob(blob, vh)
                           for _, text, text_hash, blob, vh in rows):
            raise ValueError('Staging is incomplete or contains changed source/vector bytes')
        backup.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidentally overwriting a previous recovery copy.
        with backup.open('xb'):
            pass
        with contextlib.closing(sqlite3.connect(database)) as serving:
            with contextlib.closing(sqlite3.connect(backup)) as recovery:
                serving.backup(recovery)
            with serving:
                serving.execute('BEGIN IMMEDIATE')
                if nonvector_fingerprint(serving) != meta.get('nonvector_sha256'):
                    raise ValueError('Serving content changed after staging; migration cancelled')
                if serving.execute('SELECT count(*) FROM chunks').fetchone()[0] != len(rows):
                    raise ValueError('Staging does not cover every source chunk')
                now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                for chunk_id, text, text_hash, blob, vector_hash in rows:
                    changed = serving.execute('UPDATE chunks SET embedding=? WHERE id=? AND text=?', (blob, chunk_id, text))
                    if changed.rowcount != 1:
                        raise ValueError('Chunk identity changed; migration cancelled')
                    serving.execute('''INSERT OR REPLACE INTO corpus_vector_provenance
                        (chunk_id,text_sha256,model_digest,vector_sha256,dimensions,full_text,embedded_at)
                        VALUES(?,?,?,?,1024,1,?)''', (chunk_id,text_hash,expected_identity,vector_hash,now))
                if nonvector_fingerprint(serving) != meta['nonvector_sha256']:
                    raise ValueError('Nonvector preservation check failed; transaction rolled back')
    return {'applied_chunks': len(rows), 'nonvector_sha256': meta['nonvector_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'apply'))
    parser.add_argument('--database', type=Path, default=DB_PATH)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--limit', type=int, default=0, help='Build this many unique chunks, then release the GPU; zero builds all')
    parser.add_argument('--backend', choices=('native', 'ollama'), default='native')
    parser.add_argument('--model', default='bge-m3')
    parser.add_argument('--identity', default=None)
    args = parser.parse_args()
    if args.action == 'build':
        result = build_stage(args.database, args.stage, batch_size=args.batch_size, limit=args.limit,
                             backend=args.backend, model=args.model)
    else:
        if not args.backup:
            parser.error('apply requires a new --backup path')
        if args.identity:
            expected_identity = args.identity
        elif args.backend == 'ollama':
            from app.ollama_client import OllamaClient
            expected_identity = OllamaClient().embedding_identity(args.model)
            if not expected_identity:
                parser.error(f'Could not resolve Ollama identity for model {args.model}')
        else:
            expected_identity = NativeEmbeddingClient().get_identity()
        result = apply_stage(args.database, args.stage, args.backup,
                             expected_identity=expected_identity)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
