"""Read-only coverage of source-bound vectors, independent of service liveness."""
import hashlib
import sqlite3

from app.vector_rebuild import valid_blob


def index_coverage(database, expected_identity):
    total = database.execute('SELECT count(*) FROM chunks').fetchone()[0]
    compatible = 0
    if expected_identity:
        try:
            rows = database.execute('''SELECT c.text,c.embedding,p.text_sha256,p.vector_sha256
                FROM chunks c JOIN corpus_vector_provenance p ON p.chunk_id=c.id
                WHERE c.embedding IS NOT NULL AND p.model_digest=? AND p.full_text=1 AND p.dimensions=1024''',
                (expected_identity,))
            for text, blob, text_hash, vector_hash in rows:
                if (hashlib.sha256(text.encode()).hexdigest() == text_hash
                        and valid_blob(blob, vector_hash)):
                    compatible += 1
        except sqlite3.Error:
            pass
    return {
        'total_chunks': total,
        # Compatibility key retained for clients; compatible_chunks covers either provider.
        'compatible_native_chunks': compatible,
        'compatible_chunks': compatible,
        'model_identity': expected_identity,
        'coverage_ratio': round(compatible / total, 4) if total else 0.0,
        'status': 'full' if total and compatible == total else ('partial' if compatible else 'keyword_only'),
        'degraded_fallback': compatible < total or not total,
    }
