"""Reviewed, portable publisher data; installation never extracts or embeds sources.

Only an explicit redistribution allowlist enters a pack. SQLite files, arbitrary
tables, local paths, histories and review metadata are never copied. An installer
builds the known application schema from checked records, never executes SQL from
an archive, and publishes a new isolated directory after complete validation.
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
import zipfile

from app.config import CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS
from app.indexer import KnowledgeIndexer
from app.source_quality import PIPELINE_VERSION
from app.vector_rebuild import valid_blob
from app.chunker import TextChunker
from app.source_page_reviews import validate_page_review, reviewed_page_text

MAX_TOTAL_BYTES = 4 * 1024**3
MAX_MEMBER_BYTES = 1024**3
NATIVE_PROTOCOL = 'bge-cls-l2-v1'
OLLAMA_PROTOCOL = 'bge-m3-ollama-l2-v1'
CONTRACT = {'format': 1, 'chunk_size': CHUNK_SIZE_CHARS,
            'chunk_overlap': CHUNK_OVERLAP_CHARS, 'dimensions': 1024,
            'embedding_protocol': NATIVE_PROTOCOL, 'max_length': 2048}


def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _filename(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or any(ord(c) < 32 for c in name)):
        raise ValueError('Invalid portable source filename')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('Source filename must be a normalized relative path')
    if path.suffix.lower() not in {'.pdf', '.docx', '.pptx', '.txt', '.md'}:
        raise ValueError('Unsupported source type in knowledge pack')
    return name


def _enumerate_physical_pages(source: Path) -> int:
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix == '.pdf':
        import pymupdf
        doc = pymupdf.open(str(source))
        try:
            return len(doc)
        finally:
            doc.close()
    elif suffix == '.pptx':
        import xml.etree.ElementTree as ET
        ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main'}
        with zipfile.ZipFile(source) as z:
            presentation = ET.fromstring(z.read('ppt/presentation.xml'))
            slides = presentation.findall('p:sldIdLst/p:sldId', ns)
            return len(slides)
    elif suffix in ('.md', '.txt'):
        return 1
    else:
        raise ValueError(f"Unsupported page-count semantics for source: {source.name}")


def _page_passages_digest(texts: list[str]) -> str:
    """Canonical JSON serialization preserving exact passage list boundaries and ordering."""
    payload = json.dumps(list(texts), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()



def validate_and_get_contract(identity):
    """Establish content-bound contract for either native MLX or Ollama BGE-M3."""
    if not isinstance(identity, str):
        raise ValueError('Embedding identity must be a string')
    native_prefix = 'bge-m3-mlx-fp16@'
    ollama_prefix = 'ollama-sha256:'
    if identity.startswith(native_prefix):
        hex_part = identity.removeprefix(native_prefix)
        if len(hex_part) != 40 or any(c not in '0123456789abcdef' for c in hex_part):
            raise ValueError('This pack requires an artifact-bound native BGE-M3 identity')
        return {'format': 1, 'chunk_size': CHUNK_SIZE_CHARS,
                'chunk_overlap': CHUNK_OVERLAP_CHARS, 'dimensions': 1024,
                'embedding_protocol': NATIVE_PROTOCOL, 'max_length': 2048}
    elif identity.startswith(ollama_prefix):
        hex_part = identity.removeprefix(ollama_prefix)
        if len(hex_part) != 64 or any(c not in '0123456789abcdef' for c in hex_part):
            raise ValueError('This pack requires an artifact-bound Ollama BGE-M3 identity')
        return {'format': 1, 'chunk_size': CHUNK_SIZE_CHARS,
                'chunk_overlap': CHUNK_OVERLAP_CHARS, 'dimensions': 1024,
                'embedding_protocol': OLLAMA_PROTOCOL, 'max_length': 8192}
    else:
        raise ValueError('Unsupported or unverified embedding identity')


def _identity(value):
    return validate_and_get_contract(value)


def export_pack(database, sources, allowlist, output, *, expected_identity, page_reviews=None):
    """Publisher operation: copy only reviewed sources and matching full vectors."""
    if page_reviews is None:
        raise ValueError('Explicit complete source-bound per-physical-page review evidence is required')
    contract = validate_and_get_contract(expected_identity)
    database, sources, output = Path(database), Path(sources).resolve(), Path(output)
    allowed = json.loads(Path(allowlist).read_text(encoding='utf-8'))
    if not isinstance(allowed, list) or not allowed:
        raise ValueError('A nonempty explicit redistribution allowlist is required')
    if output.exists():
        raise ValueError('Choose a new pack output path')

    raw_reviews = []
    if isinstance(page_reviews, (str, Path)):
        rev_path = Path(page_reviews)
        if not rev_path.is_file():
            raise ValueError(f'Page review file not found: {rev_path}')
        data = json.loads(rev_path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            raw_reviews = data.get('reviews', [])
        elif isinstance(data, list):
            raw_reviews = data
        else:
            raise ValueError('Invalid review evidence manifest structure')
    elif isinstance(page_reviews, dict):
        raw_reviews = page_reviews.get('reviews', list(page_reviews.values()))
    elif isinstance(page_reviews, list):
        raw_reviews = page_reviews
    else:
        raise ValueError('Unsupported page reviews argument type')

    reviews_by_key = {}
    for item in raw_reviews:
        if isinstance(item, (str, Path)):
            item_path = Path(item)
            if not item_path.is_file():
                raise ValueError(f'Review file not found: {item_path}')
            rev = json.loads(item_path.read_text(encoding='utf-8'))
        elif isinstance(item, dict):
            if 'review_path' in item:
                p = Path(item['review_path'])
                if not p.is_file():
                    raise ValueError(f'Referenced review file not found: {p}')
                rev = json.loads(p.read_text(encoding='utf-8'))
            else:
                rev = item
        else:
            raise ValueError('Invalid review entry')

        fn = rev.get('filename')
        pg = rev.get('page')
        if not isinstance(fn, str) or not isinstance(pg, int):
            raise ValueError('Review entry missing valid filename or page number')
        key = (fn, pg)
        if key in reviews_by_key:
            raise ValueError(f'Duplicate page review for {fn}:{pg}')
        reviews_by_key[key] = rev

    documents, selected = [], []
    names = set()
    with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        for entry in allowed:
            name = _filename(entry['filename'])
            if (name.casefold() in names or entry.get('redistribution_reviewed') is not True
                    or not isinstance(entry.get('license'), str) or not entry['license'].strip()):
                raise ValueError('Each unique source needs a license and explicit redistribution review')
            names.add(name.casefold())
            source = (sources / name).resolve()
            if not source.is_relative_to(sources) or not source.is_file():
                raise ValueError('Allowed original source is missing or outside its root')
            digest = sha256_file(source)
            doc = db.execute('SELECT * FROM documents WHERE filename=?', (name,)).fetchone()
            quality = db.execute('SELECT * FROM document_source_quality WHERE doc_id=? AND filename=?', (doc['id'], name)).fetchone() if doc else None
            if (not doc or digest != entry.get('sha256') or digest != doc['file_hash']
                    or doc['status'] != 'indexed' or doc['pipeline_version'] != PIPELINE_VERSION
                    or not quality or quality['pipeline_version'] != PIPELINE_VERSION
                    or quality['file_hash'] != digest or quality['doc_id'] != doc['id']
                    or quality['filename'] != name or quality['needs_review']):
                raise ValueError('Source changed, is unindexed, or still requires content review')

            physical_pages = _enumerate_physical_pages(source)
            if physical_pages != doc['total_pages']:
                raise ValueError(f'Physical page count {physical_pages} does not match indexed total {doc["total_pages"]} for {name}')

            # Check for out-of-range reviews for this document
            for (r_fn, r_pg) in reviews_by_key:
                if r_fn == name and (r_pg < 1 or r_pg > physical_pages):
                    raise ValueError(f'Out-of-range page review for {name}: page {r_pg} (total {physical_pages})')

            # Reject any chunks with invalid/out-of-range physical page numbers (NULL, < 1, > physical_pages)
            out_of_range_chunks = db.execute('''SELECT id, page_num FROM chunks
                WHERE doc_id=? AND (page_num IS NULL OR page_num < 1 OR page_num > ?)''',
                (doc['id'], physical_pages)).fetchall()
            if out_of_range_chunks:
                raise ValueError(f'Document {name} contains {len(out_of_range_chunks)} chunks outside physical page range 1..{physical_pages}')

            total_db_chunks = db.execute('SELECT COUNT(*) FROM chunks WHERE doc_id=?', (doc['id'],)).fetchone()[0]

            doc_page_reviews = []
            doc_validated_chunks = []
            doc_validated_chunk_count = 0
            for page_num in range(1, physical_pages + 1):
                rev_key = (name, page_num)
                if rev_key not in reviews_by_key:
                    raise ValueError(f'Missing complete page review for {name}:{page_num}')
                rev = reviews_by_key[rev_key]
                validate_page_review(rev, name, page_num, digest)
                if rev.get('kind') == 'hold':
                    raise ValueError(f'Review marked as HOLD for {name}:{page_num}')
                if rev.get('completeness') != 'complete':
                    raise ValueError(f'Review not complete for {name}:{page_num}')
                if rev.get('unresolved'):
                    raise ValueError(f'Review has unresolved items for {name}:{page_num}')

                expected_text = reviewed_page_text(dict(rev, editorial=''))
                if rev.get('kind') == 'blank' or not expected_text.strip():
                    expected_chunks = []
                else:
                    expected_chunks = TextChunker.chunk_page(
                        book_title=doc['clean_title'],
                        filename=name,
                        page_num=page_num,
                        page_text=expected_text
                    )

                actual_chunks = db.execute('''SELECT c.*, p.text_sha256, p.model_digest, p.vector_sha256,
                                              p.dimensions, p.full_text FROM chunks c
                                              LEFT JOIN corpus_vector_provenance p ON p.chunk_id=c.id
                                              WHERE c.doc_id=? AND c.page_num=?
                                              ORDER BY c.chunk_index, c.id''', (doc['id'], page_num)).fetchall()

                if len(actual_chunks) != len(expected_chunks):
                    raise ValueError(f'Indexed chunk count {len(actual_chunks)} does not match reviewed expectation {len(expected_chunks)} for {name}:{page_num}')

                for act, exp in zip(actual_chunks, expected_chunks):
                    if act['chunk_index'] != exp['chunk_index']:
                        raise ValueError(f'Chunk index mismatch for {name}:{page_num}')
                    if act['text'] != exp['text']:
                        raise ValueError(f'Indexed text does not match reviewed projection for {name}:{page_num}')
                    text_hash = hashlib.sha256(act['text'].encode()).hexdigest()
                    if (not act['text'].strip() or act['model_digest'] != expected_identity
                            or act['text_sha256'] != text_hash or act['dimensions'] != 1024
                            or act['full_text'] != 1 or not valid_blob(act['embedding'], act['vector_sha256'])):
                        raise ValueError('Incomplete, stale or incompatible vectors cannot enter a pack')
                    doc_validated_chunks.append(act)

                doc_validated_chunk_count += len(expected_chunks)
                p_digest = _page_passages_digest([exp['text'] for exp in expected_chunks])
                doc_page_reviews.append({
                    'page': page_num,
                    'kind': rev.get('kind', 'printed'),
                    'completeness': 'complete',
                    'chunks': len(expected_chunks),
                    'text_sha256': p_digest,
                })

            if total_db_chunks != doc_validated_chunk_count:
                raise ValueError(f'Total database chunks {total_db_chunks} does not match validated physical page chunks {doc_validated_chunk_count} for {name}')

            portable_doc = {
                'id': len(documents) + 1,
                'filename': name,
                'title': doc['clean_title'],
                'sha256': digest,
                'bytes': source.stat().st_size,
                'pages': doc['total_pages'],
                'pipeline_version': doc['pipeline_version'],
                'license': entry['license'],
                'license_notice': entry.get('license_notice', ''),
                'page_reviews': doc_page_reviews,
            }
            documents.append(portable_doc)
            selected.append((doc['id'], source, portable_doc, doc_validated_chunks))

        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.pack-export-', dir=output.parent) as temporary:
            candidate = Path(temporary) / 'pack.zip'
            count = 0
            with zipfile.ZipFile(candidate, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                with archive.open('index.jsonl', 'w') as records:
                    for doc_id, _, portable_doc, doc_chunks in selected:
                        if not doc_chunks:
                            raise ValueError('Every published document must contain indexed chunks')
                        for row in doc_chunks:
                            text_hash = hashlib.sha256(row['text'].encode()).hexdigest()
                            record = {'doc': portable_doc['id'], 'page': row['page_num'],
                                      'index': row['chunk_index'], 'text': row['text'],
                                      'text_sha256': text_hash, 'vector_sha256': row['vector_sha256'],
                                      'vector': base64.b64encode(row['embedding']).decode('ascii')}
                            records.write((json.dumps(record, ensure_ascii=False) + '\n').encode('utf-8'))
                            count += 1
                for _, source, doc, _ in selected:
                    digest = hashlib.sha256()
                    with source.open('rb') as original, archive.open('sources/' + doc['filename'], 'w') as target:
                        while block := original.read(1024**2):
                            digest.update(block); target.write(block)
                    if digest.hexdigest() != doc['sha256']:
                        raise ValueError('Source changed during export')
                manifest = {**contract, 'model_identity': expected_identity,
                            'documents': documents, 'chunks': count}
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            os.replace(candidate, output)
    return {'sha256': sha256_file(output), 'documents': len(documents), 'chunks': count}


def install_pack(archive, destination, *, expected_sha256, expected_identity):
    """Install into a NEW isolated folder; trust a publisher digest obtained separately."""
    contract = validate_and_get_contract(expected_identity)
    archive, destination = Path(archive), Path(destination)
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or sha256_file(archive) != expected_sha256:
        raise ValueError('Knowledge pack SHA256 does not match the trusted publisher digest')
    if destination.exists():
        raise ValueError('Installation destination exists; user data will not be overwritten')
    with zipfile.ZipFile(archive) as pack:
        members = pack.infolist()
        names = [entry.filename for entry in members]
        if (len(names) != len(set(names)) or len(names) > 10002
                or any(entry.file_size > MAX_MEMBER_BYTES for entry in members)
                or sum(entry.file_size for entry in members) > MAX_TOTAL_BYTES):
            raise ValueError('Duplicate entries or oversized knowledge pack')
        if 'manifest.json' not in names or pack.getinfo('manifest.json').file_size > 4 * 1024**2:
            raise ValueError('Missing or oversized knowledge manifest')
        manifest = json.loads(pack.read('manifest.json'))
        if any(manifest.get(k) != v for k, v in contract.items()) or manifest.get('model_identity') != expected_identity:
            raise ValueError('Pack uses a different chunking or embedding contract')
        docs = manifest.get('documents')
        if not isinstance(docs, list) or not docs:
            raise ValueError('Empty knowledge pack')
        expected_files = {'manifest.json', 'index.jsonl'}
        doc_map, casefolded = {}, set()
        for doc in docs:
            name = _filename(doc['filename'])
            if (type(doc['id']) is not int or doc['id'] < 1 or doc['id'] in doc_map
                    or name.casefold() in casefolded or not isinstance(doc.get('license'), str)
                    or not doc['license'].strip() or type(doc['pages']) is not int or doc['pages'] < 1
                    or doc.get('pipeline_version') != PIPELINE_VERSION):
                raise ValueError('Invalid or duplicate source manifest')
            page_reviews = doc.get('page_reviews')
            if not isinstance(page_reviews, list) or len(page_reviews) != doc['pages']:
                raise ValueError('Missing or incomplete physical page review coverage metadata')
            for p_idx, p_rev in enumerate(page_reviews, 1):
                if (not isinstance(p_rev, dict)
                        or p_rev.get('page') != p_idx
                        or p_rev.get('completeness') != 'complete'
                        or not isinstance(p_rev.get('kind'), str) or not p_rev['kind']
                        or not isinstance(p_rev.get('chunks'), int) or p_rev['chunks'] < 0
                        or not isinstance(p_rev.get('text_sha256'), str) or len(p_rev['text_sha256']) != 64):
                    raise ValueError('Invalid physical page review coverage entry')
                if p_rev['kind'] == 'blank' and (p_rev['chunks'] != 0 or p_rev['text_sha256'] != _page_passages_digest([])):
                    raise ValueError('Blank page review must contain zero chunks and empty digest')
            doc_map[doc['id']] = doc; casefolded.add(name.casefold())
            expected_files.add('sources/' + name)
        if set(names) != expected_files:
            raise ValueError('Unlisted, missing or unsafe archive members')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.pack-install-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'ready'; staging.mkdir()
            source_root = staging / 'sources'; source_root.mkdir()
            for doc in docs:
                source = source_root / doc['filename']; source.parent.mkdir(parents=True, exist_ok=True)
                with pack.open('sources/' + doc['filename']) as original, source.open('xb') as target:
                    while block := original.read(1024**2): target.write(block)
                if source.stat().st_size != doc['bytes'] or sha256_file(source) != doc['sha256']:
                    raise ValueError('Original source does not match the reviewed publisher hash')
                if _enumerate_physical_pages(source) != doc['pages']:
                    raise ValueError(f"Installed source physical pages do not match reviewed manifest for {doc['filename']}")
            indexer = KnowledgeIndexer(staging / 'knowledge_base.db', source_root)
            count, doc_counts, seen = 0, {doc_id: 0 for doc_id in doc_map}, set()
            conn = indexer.get_connection()
            try:
                with conn as db:
                    for doc in docs:
                        db.execute('''INSERT INTO documents(id,filename,clean_title,filepath,file_size,file_hash,
                            total_pages,extracted_pages_count,status,pipeline_version) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                            (doc['id'], doc['filename'], doc['title'], doc['filename'], doc['bytes'], doc['sha256'],
                             doc['pages'], doc['pages'], 'indexed', doc['pipeline_version']))
                        db.execute('''INSERT INTO document_source_quality(doc_id,filename,file_hash,pipeline_version,
                            needs_review,quality_warnings) VALUES(?,?,?,?,0,'[]')''',
                            (doc['id'], doc['filename'], doc['sha256'], doc['pipeline_version']))
                    with pack.open('index.jsonl') as records:
                        for line in iter(lambda: records.readline(128 * 1024 + 1), b''):
                            if len(line) > 128 * 1024:
                                raise ValueError('Oversized chunk record')
                            row = json.loads(line)
                            doc = doc_map.get(row['doc']); key = (row['doc'], row['page'], row['index'])
                            blob = base64.b64decode(row['vector'], validate=True)
                            if (not doc or key in seen or type(row['page']) is not int
                                    or not 1 <= row['page'] <= doc['pages'] or type(row['index']) is not int
                                    or row['index'] < 0 or not isinstance(row['text'], str) or not row['text'].strip()
                                    or hashlib.sha256(row['text'].encode()).hexdigest() != row['text_sha256']
                                    or not valid_blob(blob, row['vector_sha256'], dimensions=contract['dimensions'])):
                                raise ValueError('Invalid chunk identity, source binding or vector bytes')
                            cursor = db.execute('''INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text,embedding)
                                VALUES(?,?,?,?,?,?,?)''', (doc['id'], doc['title'], doc['filename'], row['page'], row['index'], row['text'], blob))
                            db.execute('INSERT INTO corpus_vector_provenance VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP)',
                                       (cursor.lastrowid, row['text_sha256'], expected_identity, row['vector_sha256'], contract['dimensions']))
                            count += 1; doc_counts[doc['id']] += 1; seen.add(key)
                    if count != manifest['chunks'] or not count or not all(doc_counts.values()):
                        raise ValueError('Pack does not cover its declared documents/chunks')

                    # Validate physical page review coverage and contiguous indexing against installed DB sequence
                    for doc in docs:
                        total_doc_installed = db.execute('SELECT COUNT(*) FROM chunks WHERE doc_id=?', (doc['id'],)).fetchone()[0]
                        total_doc_expected = sum(p['chunks'] for p in doc['page_reviews'])
                        if total_doc_installed != total_doc_expected:
                            raise ValueError(f"Installed document chunk count {total_doc_installed} does not match reviewed page coverage sum {total_doc_expected} for {doc['filename']}")

                        for p_idx, p_rev in enumerate(doc['page_reviews'], 1):
                            page_rows = db.execute('''
                                SELECT chunk_index, text FROM chunks
                                WHERE doc_id=? AND page_num=?
                                ORDER BY chunk_index, id
                            ''', (doc['id'], p_idx)).fetchall()

                            if len(page_rows) != p_rev['chunks']:
                                raise ValueError(f"Installed chunk count {len(page_rows)} does not match reviewed page coverage {p_rev['chunks']} for {doc['filename']}:{p_idx}")

                            page_indices = [r['chunk_index'] for r in page_rows]
                            if page_indices != list(range(len(page_rows))):
                                raise ValueError(f"Non-canonical, gapped or swapped chunk indices for {doc['filename']}:{p_idx}: {page_indices}")

                            actual_texts = [r['text'] for r in page_rows]
                            if _page_passages_digest(actual_texts) != p_rev['text_sha256']:
                                raise ValueError(f"Installed chunk passage digest does not match reviewed page coverage for {doc['filename']}:{p_idx}")

                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
                        raise ValueError('Installed knowledge integrity check failed')
                    db.execute("INSERT INTO chunks_fts(chunks_fts,rank) VALUES('integrity-check',1)")
            finally:
                conn.close()
            (staging / 'knowledge-pack.json').write_text(json.dumps({
                'sha256': expected_sha256, 'model_identity': expected_identity,
                'embedding_protocol': contract['embedding_protocol'],
                'documents': len(docs), 'chunks': count}), encoding='utf-8')
            os.rename(staging, destination)
    return {'documents': len(docs), 'chunks': count, 'sha256': expected_sha256}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='action', required=True)
    export = subcommands.add_parser('export', help='Publisher: export explicitly reviewed sources and prebuilt vectors')
    export.add_argument('--database', type=Path, required=True)
    export.add_argument('--sources', type=Path, required=True)
    export.add_argument('--allowlist', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--page-reviews', type=Path, default=None,
                        help='Explicit source-bound per-physical-page review evidence')
    export.add_argument('--identity', help='Explicit model identity digest')
    export.add_argument('--backend', choices=('native', 'ollama'), default=None)
    export.add_argument('--model', default=None)
    install = subcommands.add_parser('install', help='Install a trusted prebuilt pack without extracting or embedding documents')
    install.add_argument('--archive', type=Path, required=True)
    install.add_argument('--destination', type=Path, required=True)
    install.add_argument('--sha256', required=True)
    install.add_argument('--identity', help='Explicit model identity digest')
    install.add_argument('--backend', choices=('native', 'ollama'), default=None)
    install.add_argument('--model', default=None)
    args = parser.parse_args()
    try:
        if args.identity:
            identity = args.identity
        elif args.backend == 'ollama' or (args.model and not args.model.startswith('native')):
            from app.ollama_client import OllamaClient
            identity = OllamaClient().embedding_identity(args.model or 'bge-m3')
            if not identity:
                parser.exit(1, f"Could not determine identity for Ollama model '{args.model or 'bge-m3'}'\n")
        elif args.backend == 'native':
            from app.native_embedding import NativeEmbeddingClient
            identity = NativeEmbeddingClient().get_identity()
        else:
            from app.native_embedding import NativeEmbeddingClient
            if NativeEmbeddingClient.available():
                identity = NativeEmbeddingClient().get_identity()
            else:
                from app.ollama_client import OllamaClient
                identity = OllamaClient().embedding_identity(args.model or 'bge-m3')
                if not identity:
                    parser.exit(1, "Could not determine local embedding model identity\n")

        if args.action == 'export':
            result = export_pack(args.database, args.sources, args.allowlist, args.output,
                                 expected_identity=identity, page_reviews=args.page_reviews)
        else:
            result = install_pack(args.archive, args.destination, expected_sha256=args.sha256, expected_identity=identity)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'Knowledge pack failed: {exc}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
