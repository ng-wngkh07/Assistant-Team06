"""Read-only document lookup, independent of chat and conversation history."""

import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import DB_PATH, SRC_DIR, DEFAULT_EMBED_MODEL
from app.gpu_lock import gpu_coordinator, GPUBusyError
from app.indexer import unpack_vector
from app.ollama_client import OllamaClient
from app.searcher import HybridSearcher


logger = logging.getLogger(__name__)


class DocumentLookup:
    def __init__(self, db_path: Optional[Path] = None, src_dir: Optional[Path] = None):
        self._db_path = db_path
        self._src_dir = src_dir
        self.ollama = OllamaClient()

    @property
    def db_path(self) -> Path:
        if self._db_path is not None:
            return self._db_path
        from app.config import DB_PATH as CONFIG_DB_PATH
        return CONFIG_DB_PATH

    @db_path.setter
    def db_path(self, val: Path):
        self._db_path = val

    @property
    def src_dir(self) -> Path:
        if self._src_dir is not None:
            return self._src_dir
        from app.config import SRC_DIR as CONFIG_SRC_DIR
        return CONFIG_SRC_DIR

    @src_dir.setter
    def src_dir(self, val: Path):
        self._src_dir = val

    def connect(self):
        if not self.db_path.exists():
            from app.indexer import KnowledgeIndexer
            KnowledgeIndexer(self.db_path, self.src_dir)
        db = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def file_path(self, filename: str) -> Optional[Path]:
        try:
            root = self.src_dir.resolve()
            path = (self.src_dir / filename).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return None
            return path
        except Exception:
            return None

    SUPPORTED_EXTENSIONS = {
        ".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown",
        ".png", ".jpg", ".jpeg", ".webp"
    }

    def supported_path(self, filename: str) -> Optional[Path]:
        path = self.file_path(filename)
        if not path or path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            return None
        return path

    def pdf_path(self, filename: str) -> Optional[Path]:
        path = self.file_path(filename)
        if not path or path.suffix.lower() != ".pdf":
            return None
        return path

    def documents(self):
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT d.id, d.filename, d.clean_title, d.total_pages, COUNT(c.id) AS chunk_count "
                "FROM documents d "
                "LEFT JOIN chunks c ON c.doc_id = d.id "
                "WHERE d.status IN ('indexed','ocr_partial_reviewed') "
                "GROUP BY d.id "
                "ORDER BY d.clean_title, d.id"
            ).fetchall()
        return [dict(r) for r in rows if self.supported_path(r["filename"])]

    def document(self, doc_id: int):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT d.id, d.filename, d.clean_title, d.total_pages, COUNT(c.id) AS chunk_count "
                "FROM documents d "
                "LEFT JOIN chunks c ON c.doc_id = d.id "
                "WHERE d.id=? AND d.status IN ('indexed','ocr_partial_reviewed') "
                "GROUP BY d.id", (doc_id,)
            ).fetchone()
        if not row or not self.supported_path(row["filename"]):
            return None
        return dict(row)

    def document_by_filename(self, filename: str) -> Optional[dict]:
        """Resolve an attachment by its exact stored relative filename.

        Display titles, basenames and partial names are search terms, not source
        identities. Never silently attach another folder or document edition.
        """
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT d.id, d.filename, d.clean_title, d.total_pages, COUNT(c.id) AS chunk_count "
                "FROM documents d LEFT JOIN chunks c ON c.doc_id=d.id "
                "WHERE d.filename=? AND d.status IN ('indexed','ocr_partial_reviewed') "
                "GROUP BY d.id", (filename,)
            ).fetchone()
        if not row or not self.supported_path(row["filename"]):
            return None
        return dict(row)

    def search(self, query: str, limit: int = 10, doc_id: Optional[int] = None,
               method: str = "fts"):
        available = self.documents()
        ids = [d["id"] for d in available if doc_id is None or d["id"] == doc_id]
        if not ids:
            return {"results": [], "method": "fts", "notice": "Không có tài liệu phù hợp."}
        scope = ",".join("?" for _ in ids)
        fts_query = HybridSearcher._clean_fts_query(query)
        if fts_query:
            fts_query = f"text : ({fts_query})"
        fts = []
        with closing(self.connect()) as db:
            if fts_query:
                fts = [dict(r) for r in db.execute(
                    f"SELECT c.id AS chunk_id, c.doc_id, c.book_title, c.filename, "
                    f"c.page_num, c.chunk_index, c.text, bm25(chunks_fts) AS rank_score "
                    f"FROM chunks_fts JOIN chunks c ON chunks_fts.rowid=c.id "
                    f"WHERE chunks_fts MATCH ? AND c.doc_id IN ({scope}) "
                    f"ORDER BY rank_score, c.id LIMIT ?",
                    [fts_query, *ids, limit * 3],
                )]

        semantic = []
        notice = ""
        if method == "hybrid":
            try:
                installed_digest = None
                try:
                    with closing(self.connect()) as db:
                        row = db.execute("SELECT model_digest FROM corpus_vector_provenance WHERE full_text=1 LIMIT 1").fetchone()
                        if row:
                            installed_digest = row[0]
                except Exception:
                    pass

                use_native = False
                if installed_digest and installed_digest.startswith("ollama-sha256:"):
                    use_native = False
                elif installed_digest and installed_digest.startswith("bge-m3-mlx-fp16@"):
                    if sys.platform != "darwin":
                        raise ValueError("Installed native BGE knowledge pack requires macOS")
                    use_native = True
                elif os.getenv("EMBED_BACKEND") == "ollama":
                    use_native = False
                elif os.getenv("EMBED_BACKEND") in ("native", "mlx"):
                    if sys.platform != "darwin":
                        raise ValueError("Native embedding requires macOS")
                    use_native = True
                elif sys.platform == "darwin":
                    use_native = True
                else:
                    use_native = False

                if use_native:
                    from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
                    native = NativeEmbeddingClient()
                    if not native.available():
                        raise ValueError("Configured native embedding basis is unavailable")
                    expected_identity = BGE_NATIVE_IDENTITY
                    vec = native.get_embedding(query, expected_identity=expected_identity)
                else:
                    if DEFAULT_EMBED_MODEL in {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge"} and sys.platform != "darwin":
                        raise ValueError("Native embedding is unavailable on this platform")
                    expected_identity = self.ollama.embedding_identity(DEFAULT_EMBED_MODEL)
                    if not expected_identity:
                        raise ValueError("Embedding artifact identity is unknown")
                    with gpu_coordinator.acquire_for_inference():
                        vec = self.ollama.get_embedding(query, model=DEFAULT_EMBED_MODEL)
                    if self.ollama.embedding_identity(DEFAULT_EMBED_MODEL) != expected_identity:
                        raise ValueError("Embedding artifact changed during query")

                if vec is None:
                    notice = "Tìm ngữ nghĩa chưa khả dụng; đang hiển thị kết quả từ khóa."
                else:
                    q = np.asarray(vec, dtype=np.float32)
                    norm = np.linalg.norm(q)
                    if not np.isfinite(q).all() or norm == 0:
                        raise ValueError("Invalid embedding")
                    q /= norm
                    import hashlib
                    with closing(self.connect()) as db:
                        sql = f"""
                            SELECT c.id AS chunk_id, c.doc_id, c.book_title, c.filename, c.page_num,
                                   c.chunk_index, c.text, c.embedding,
                                   p.text_sha256, p.model_digest, p.vector_sha256, p.dimensions, p.full_text
                            FROM chunks c JOIN corpus_vector_provenance p ON p.chunk_id=c.id
                            WHERE c.doc_id IN ({scope}) AND c.embedding IS NOT NULL
                              AND p.model_digest=? AND p.full_text=1 AND p.dimensions=?
                        """
                        rows = db.execute(sql, [*ids, expected_identity, len(q)]).fetchall()

                    for row in rows:
                        blob = row["embedding"]
                        if not blob:
                            continue
                        actual_t = hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
                        actual_v = hashlib.sha256(blob).hexdigest()
                        if actual_t != row["text_sha256"] or actual_v != row["vector_sha256"]:
                            continue
                        v = unpack_vector(blob)
                        vn = np.linalg.norm(v)
                        if v.shape == q.shape and vn > 0 and np.isfinite(v).all():
                            if abs(vn - 1.0) > 1e-3:
                                continue
                            score = float(np.dot(q, v / vn))
                            if score >= 0.15:
                                item = {k: row[k] for k in row.keys() if k not in ("embedding", "text_sha256", "model_digest", "vector_sha256", "dimensions", "full_text")}
                                semantic.append((score, item))
                    semantic.sort(key=lambda x: (-x[0], x[1]["chunk_id"]))
                    semantic = [item for _, item in semantic[:limit * 3]]
                    if not semantic:
                        notice = "Chưa có đoạn ngữ nghĩa phù hợp; đang hiển thị kết quả từ khóa."
            except Exception as exc:
                # This entire branch is optional dense retrieval. The lexical
                # result above remains useful when its provider or index fails.
                logger.warning("Library semantic retrieval unavailable: %s", type(exc).__name__)
                notice = "Tìm ngữ nghĩa tạm thời chưa khả dụng; đang hiển thị kết quả từ khóa."

        ranked, scores = {}, {}
        for group in [fts, semantic]:
            for rank, item in enumerate(group, 1):
                cid = item["chunk_id"]
                ranked[cid] = item
                scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
        ordered = sorted(ranked, key=lambda cid: (-scores[cid], cid))
        results, seen = [], set()
        for cid in ordered:
            item = ranked[cid]
            key = " ".join(item["text"].split()).casefold()
            if key in seen:
                continue
            seen.add(key)
            item.pop("rank_score", None)
            item["pdf_url"] = (
                f"/api/documents/{item['doc_id']}/pdf#page={item['page_num']}"
                if self.pdf_path(item.get("filename", ""))
                else None
            )
            item["page_image_url"] = (
                f"/api/documents/{item['doc_id']}/pages/{item['page_num']}/image"
                if self.pdf_path(item.get("filename", "")) else None
            )
            results.append(item)
            if len(results) == limit:
                break
        return {"results": results, "method": "hybrid" if semantic else "fts", "notice": notice}

    def page_image(self, doc_id: int, page_num: int) -> Optional[bytes]:
        """Render the original PDF page without rewriting its text, math or layout."""
        import pymupdf
        doc = self.document(doc_id)
        if not doc or page_num < 1:
            return None
        path = self.pdf_path(doc['filename'])
        if not path:
            return None
        with pymupdf.open(path) as pdf:
            if page_num > len(pdf):
                return None
            page = pdf[page_num - 1]
            width, height = page.rect.width, page.rect.height
            if width <= 0 or height <= 0:
                return None
            scale = min(1400 / width, 2200 / height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            return pix.tobytes('png')

    def source_preview(self, filename: str, page_num: int) -> dict:
        """Resolve persisted citations using database IDs, never user paths or URLs."""
        with closing(self.connect()) as db:
            row = db.execute('SELECT id FROM documents WHERE filename=?', (filename,)).fetchone()
        if not row or not self.pdf_path(filename) or page_num < 1:
            return {}
        doc_id = row['id']
        return {'doc_id': doc_id, 'page_image_url': f'/api/documents/{doc_id}/pages/{page_num}/image',
                'pdf_url': f'/api/documents/{doc_id}/pdf#page={page_num}'}
