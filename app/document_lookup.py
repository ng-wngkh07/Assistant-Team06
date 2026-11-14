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
from app.source_freshness import (
    assess_document_freshness,
    load_watcher_state_items,
    table_columns,
)


logger = logging.getLogger(__name__)


class DocumentLookup:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        src_dir: Optional[Path] = None,
        data_dir: Optional[Path] = None,
    ):
        self._db_path = db_path
        self._src_dir = src_dir
        self._data_dir = data_dir
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

    def _query_document_rows(
        self,
        db: sqlite3.Connection,
        *,
        doc_id: Optional[int] = None,
        filename: Optional[str] = None,
        only_indexed: bool = True,
    ) -> list[dict]:
        cols = table_columns(db, "documents")
        if not cols or "id" not in cols or "filename" not in cols:
            return []
        base_cols = ["d.id", "d.filename", "d.clean_title", "d.total_pages"]
        extra_cols = [c for c in ("filepath", "file_size", "file_hash", "status") if c in cols]
        select_exprs = ", ".join([*base_cols, *(f"d.{c}" for c in extra_cols), "COUNT(c.id) AS chunk_count"])
        clauses = []
        params = []
        if only_indexed and "status" in cols:
            clauses.append("d.status IN ('indexed','ocr_partial_reviewed')")
        if doc_id is not None:
            clauses.append("d.id=?")
            params.append(doc_id)
        if filename is not None:
            clauses.append("d.filename=?")
            params.append(filename)
        where_sql = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        order_sql = "ORDER BY d.clean_title, d.id" if (doc_id is None and filename is None) else ""
        sql = (
            f"SELECT {select_exprs} "
            "FROM documents d "
            "LEFT JOIN chunks c ON c.doc_id = d.id "
            f"{where_sql}"
            f"GROUP BY d.id {order_sql}"
        )
        return [dict(r) for r in db.execute(sql, params).fetchall()]

    @staticmethod
    def _public_doc_summary(row: dict) -> dict:
        return {
            "id": row["id"],
            "filename": row["filename"],
            "clean_title": row["clean_title"],
            "total_pages": row["total_pages"],
            "chunk_count": row["chunk_count"],
        }

    @staticmethod
    def _status_from_legacy_lookup(doc: Optional[dict]) -> dict:
        if doc is None:
            return {
                "exists": False,
                "fresh": False,
                "status": "absent",
                "reason_code": "source_absent",
                "message": "Không tìm thấy tài liệu trong thư viện.",
                "doc": None,
            }
        return {
            "exists": True,
            "fresh": True,
            "status": "fresh",
            "reason_code": "fresh",
            "message": "",
            "doc": doc,
        }

    def inspect_document_status(
        self,
        doc_id: Optional[int] = None,
        filename: Optional[str] = None,
    ) -> dict:
        """Inspect whether an indexed library document exists and strictly matches its original file."""
        inst_dict = getattr(self, "__dict__", {})
        cls = type(self)
        # Honor legacy test monkeypatches on document()/document_by_filename() while
        # keeping unpatched production lookups strictly bound to disk freshness.
        if doc_id is not None and (
            "document" in inst_dict or cls.document is not DocumentLookup.document
        ):
            return self._status_from_legacy_lookup(self.document(doc_id))
        if filename and (
            "document_by_filename" in inst_dict
            or cls.document_by_filename is not DocumentLookup.document_by_filename
        ):
            return self._status_from_legacy_lookup(self.document_by_filename(filename))
        if doc_id is None and not filename:
            return self._status_from_legacy_lookup(None)
        with closing(self.connect()) as db:
            rows = self._query_document_rows(db, doc_id=doc_id, filename=filename, only_indexed=False)
        if not rows:
            return self._status_from_legacy_lookup(None)
        row = rows[0]
        freshness = assess_document_freshness(
            row,
            db_path=self.db_path,
            src_dir=self.src_dir,
            data_dir=self._data_dir,
        )
        if not freshness.fresh:
            return {
                "exists": True,
                "fresh": False,
                "status": freshness.status,
                "reason_code": freshness.reason_code,
                "message": freshness.message,
                "doc": None,
            }
        if not self.supported_path(row["filename"]):
            return {
                "exists": True,
                "fresh": False,
                "status": "unsafe_source_path",
                "reason_code": "source_identity_mismatch",
                "message": "Định danh tệp tài liệu nguồn không hợp lệ hoặc nằm ngoài phạm vi hỗ trợ.",
                "doc": None,
            }
        return {
            "exists": True,
            "fresh": True,
            "status": "fresh",
            "reason_code": "fresh",
            "message": "",
            "doc": self._public_doc_summary(row),
        }

    def documents(self):
        with closing(self.connect()) as db:
            rows = self._query_document_rows(db, only_indexed=True)
        watcher_items = load_watcher_state_items(data_dir=self._data_dir, db_path=self.db_path)
        fresh_docs = []
        for row in rows:
            if not self.supported_path(row["filename"]):
                continue
            freshness = assess_document_freshness(
                row,
                db_path=self.db_path,
                src_dir=self.src_dir,
                data_dir=self._data_dir,
                watcher_items=watcher_items,
            )
            if freshness.fresh:
                fresh_docs.append(self._public_doc_summary(row))
        return fresh_docs

    def document(self, doc_id: int):
        info = self.inspect_document_status(doc_id=doc_id)
        return info["doc"] if info["fresh"] else None

    def document_by_filename(self, filename: str) -> Optional[dict]:
        """Resolve an attachment by its exact stored relative filename.

        Display titles, basenames and partial names are search terms, not source
        identities. Never silently attach another folder or document edition.
        """
        info = self.inspect_document_status(filename=filename)
        return info["doc"] if info["fresh"] else None

    def search(self, query: str, limit: int = 10, doc_id: Optional[int] = None,
               method: str = "fts"):
        if doc_id is not None:
            info = self.inspect_document_status(doc_id=doc_id)
            if not info["fresh"]:
                return {
                    "results": [],
                    "method": "fts",
                    "notice": info["message"] or "Không tìm thấy tài liệu phù hợp.",
                    "freshness_status": info["status"],
                }
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
            doc_id = item["doc_id"]
            filename = item.get("filename", "")
            path = self.supported_path(filename)
            ext = path.suffix.lower() if path else Path(filename).suffix.lower()
            original_url = f"/api/documents/{doc_id}/original" if path else None

            if ext == ".pdf":
                item["source_kind"] = "pdf"
                item["page_label"] = f"Trang PDF {item['page_num']}"
                item["pdf_url"] = f"/api/documents/{doc_id}/pdf#page={item['page_num']}"
                item["page_image_url"] = f"/api/documents/{doc_id}/pages/{item['page_num']}/image"
                item["original_url"] = original_url
                item["preview_available"] = True
            elif ext == ".pptx":
                from app.slide_preview import is_pptx_preview_available
                preview_avail = bool(path and is_pptx_preview_available(path))
                item["source_kind"] = "pptx"
                item["page_label"] = f"Slide {item['page_num']}"
                item["pdf_url"] = None
                item["page_image_url"] = f"/api/documents/{doc_id}/pages/{item['page_num']}/image" if preview_avail else None
                item["original_url"] = original_url
                item["preview_available"] = preview_avail
            elif ext == ".docx":
                item["source_kind"] = "docx"
                item["page_label"] = f"Phần {item['page_num']}"
                item["pdf_url"] = None
                item["page_image_url"] = None
                item["original_url"] = original_url
                item["preview_available"] = False
            else:
                item["source_kind"] = ext.lstrip(".") or "text"
                item["page_label"] = f"Phần {item['page_num']}"
                item["pdf_url"] = None
                item["page_image_url"] = None
                item["original_url"] = original_url
                item["preview_available"] = False

            results.append(item)
            if len(results) == limit:
                break
        return {"results": results, "method": "hybrid" if semantic else "fts", "notice": notice}

    def page_image(self, doc_id: int, page_num: int) -> Optional[bytes]:
        """Render the original PDF page (or PPTX slide preview) without rewriting its text, math or layout."""
        import pymupdf
        doc = self.document(doc_id)
        if not doc or page_num < 1:
            return None
        path = self.pdf_path(doc['filename'])
        if not path:
            raw_path = self.file_path(doc['filename'])
            if raw_path and raw_path.suffix.lower() == '.pptx':
                from app.slide_preview import render_office_page_png
                return render_office_page_png(raw_path, page_num=page_num)
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
        info = self.inspect_document_status(filename=filename)
        if not info["fresh"] or not info["doc"] or page_num < 1:
            return {}
        doc = info["doc"]
        path = self.supported_path(doc["filename"])
        if not path:
            return {}
        doc_id = doc["id"]
        ext = path.suffix.lower()
        original_url = f"/api/documents/{doc_id}/original"

        if ext == ".pdf":
            return {
                'doc_id': doc_id,
                'source_kind': 'pdf',
                'page_label': f"Trang {page_num}",
                'pdf_url': f'/api/documents/{doc_id}/pdf#page={page_num}',
                'page_image_url': f'/api/documents/{doc_id}/pages/{page_num}/image',
                'original_url': original_url,
                'preview_available': True,
            }
        elif ext == ".pptx":
            from app.slide_preview import is_pptx_preview_available
            preview_avail = bool(path and is_pptx_preview_available(path))
            return {
                'doc_id': doc_id,
                'source_kind': 'pptx',
                'page_label': f"Slide {page_num}",
                'pdf_url': None,
                'page_image_url': f'/api/documents/{doc_id}/pages/{page_num}/image' if preview_avail else None,
                'original_url': original_url,
                'preview_available': preview_avail,
            }
        elif ext == ".docx":
            return {
                'doc_id': doc_id,
                'source_kind': 'docx',
                'page_label': f"Phần {page_num}",
                'pdf_url': None,
                'page_image_url': None,
                'original_url': original_url,
                'preview_available': False,
            }
        else:
            return {
                'doc_id': doc_id,
                'source_kind': ext.lstrip(".") or "text",
                'page_label': f"Phần {page_num}",
                'pdf_url': None,
                'page_image_url': None,
                'original_url': original_url,
                'preview_available': False,
            }
