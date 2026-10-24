import os
import sys
import json
import sqlite3
import struct
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from app.config import (
    SRC_DIR, DB_PATH, DEFAULT_EMBED_MODEL,
    CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS, DATA_DIR
)
from app.pdf_extractor import PDFExtractor, PDFExtractionResult
from app.chunker import TextChunker
from app.ollama_client import OllamaClient
from app.source_quality import (
    PIPELINE_VERSION, save_source_quality_sidecar, load_source_quality_sidecar
)

logger = logging.getLogger(__name__)


def pack_vector(vec: List[float]) -> bytes:
    """Pack float list into binary bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> np.ndarray:
    """Unpack binary bytes into numpy float32 array."""
    count = len(blob) // 4
    return np.array(struct.unpack(f"{count}f", blob), dtype=np.float32)


class KnowledgeIndexer:
    def __init__(self, db_path: Path = DB_PATH, src_dir: Path = SRC_DIR):
        self.db_path = db_path
        self.src_dir = src_dir
        self.ollama = OllamaClient()
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        """Create database tables, migration columns, and FTS5 virtual table."""
        with self.get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                clean_title TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                total_pages INTEGER NOT NULL,
                extracted_pages_count INTEGER NOT NULL,
                is_scanned INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT,
                pipeline_version TEXT NOT NULL DEFAULT 'v1',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                book_title TEXT NOT NULL,
                filename TEXT NOT NULL,
                page_num INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(doc_id, page_num);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                book_title,
                text,
                content='chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 0'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, book_title, text) VALUES (new.id, new.book_title, new.text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, book_title, text) VALUES('delete', old.id, old.book_title, old.text);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, book_title, text) VALUES('delete', old.id, old.book_title, old.text);
                INSERT INTO chunks_fts(rowid, book_title, text) VALUES (new.id, new.book_title, new.text);
            END;

            CREATE TABLE IF NOT EXISTS document_source_quality (
                doc_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                pipeline_version TEXT NOT NULL,
                needs_review INTEGER NOT NULL DEFAULT 0,
                quality_warnings TEXT,
                raw_char_count INTEGER NOT NULL DEFAULT 0,
                raw_capture_hash TEXT,
                blocks_count INTEGER NOT NULL DEFAULT 0,
                quality_metadata TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS corpus_vector_provenance (
                chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                text_sha256 TEXT NOT NULL,
                model_digest TEXT NOT NULL,
                vector_sha256 TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                full_text INTEGER NOT NULL,
                embedded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_provenance_digest ON corpus_vector_provenance(model_digest);
            """)

            # Migration: Ensure pipeline_version column exists in documents
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
            if "pipeline_version" not in cols:
                conn.execute("ALTER TABLE documents ADD COLUMN pipeline_version TEXT NOT NULL DEFAULT 'v1'")

            cols_q = [row["name"] for row in conn.execute("PRAGMA table_info(document_source_quality)").fetchall()]
            if "raw_capture_hash" not in cols_q:
                conn.execute("ALTER TABLE document_source_quality ADD COLUMN raw_capture_hash TEXT")

    def get_indexed_files_map(self) -> Dict[str, Dict[str, Any]]:
        """Return dict of {filename: {file_hash, filepath, status, id, pipeline_version, ...}}"""
        with self.get_connection() as conn:
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
            pipe_col = ", pipeline_version" if "pipeline_version" in cols else ""
            cursor = conn.execute(f"SELECT id, filename, filepath, file_hash, status, total_pages, is_scanned{pipe_col} FROM documents")
            return {row["filename"]: dict(row) for row in cursor.fetchall()}

    def backfill_missing_embeddings(
        self,
        embed_model: str = DEFAULT_EMBED_MODEL,
        batch_size: int = 32,
        limit: int = 0,
        progress_callback: Optional[callable] = None
    ) -> int:
        """
        Populate embeddings for all chunks that currently have embedding IS NULL.
        Allows adding vector search capability to already indexed files.
        """
        use_native = False
        native_client = None
        native_embed_models = {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge", "bge-m3"}
        is_native_target = (embed_model in native_embed_models) or (embed_model is None and sys.platform == "darwin")

        if sys.platform == "darwin" and is_native_target:
            from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
            if NativeEmbeddingClient.available():
                use_native = True
                native_client = NativeEmbeddingClient()
                resolved_model = native_client.get_identity()
            else:
                logger.info("Mô hình native MLX embedding chưa khả dụng trên Mac; không thể bổ sung embedding.")
                return 0
        else:
            resolved_model = self.ollama.find_best_embed_model(embed_model) or embed_model
            test_emb = self.ollama.get_embedding("test", model=resolved_model)
            if test_emb is None:
                logger.info(f"Mô hình embedding '{resolved_model}' chưa khả dụng trong Ollama để bổ sung embedding.")
                return 0

        with self.get_connection() as conn:
            if limit > 0:
                missing_chunks = conn.execute(
                    "SELECT id, text FROM chunks WHERE embedding IS NULL ORDER BY id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                missing_chunks = conn.execute(
                    "SELECT id, text FROM chunks WHERE embedding IS NULL ORDER BY id"
                ).fetchall()

        total_missing = len(missing_chunks)
        if total_missing == 0:
            return 0

        logger.info(f"Bắt đầu bổ sung embedding cho {total_missing} đoạn chỉ mục bằng mô hình '{resolved_model}'...")
        count = 0
        import hashlib
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.get_connection() as conn:
            for start in range(0, total_missing, batch_size):
                batch = missing_chunks[start:start + batch_size]
                if use_native and native_client:
                    vectors = native_client.get_batch_embeddings([row["text"] for row in batch])
                else:
                    vectors = self.ollama.get_batch_embeddings(
                        [row["text"] for row in batch], model=resolved_model
                    )
                for row, vec in zip(batch, vectors):
                    if vec:
                        vec_blob = pack_vector(vec)
                        conn.execute(
                            "UPDATE chunks SET embedding = ? WHERE id = ?",
                            (vec_blob, row["id"]),
                        )
                        text_sha = hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
                        vec_sha = hashlib.sha256(vec_blob).hexdigest()
                        conn.execute("""
                            INSERT OR REPLACE INTO corpus_vector_provenance
                            (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (row["id"], text_sha, resolved_model, vec_sha, len(vec), 1, now_iso))
                        count += 1
                conn.commit()
                if progress_callback:
                    done = min(start + len(batch), total_missing)
                    progress_callback(done, total_missing, f"Đã bổ sung {count}/{total_missing} vector embedding...")

        return count

    def index_all(
        self,
        force: bool = False,
        ocr: bool = False,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        progress_callback: Optional[callable] = None,
        force_ocr_files: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        """
        Index all PDFs from SRC_DIR.
        Supports incremental updates (skips unchanged files with matching pipeline version unless force=True).
        Detects obsolete pipeline versions and migrates them safely.
        Automatically backfills vector embeddings if embed_model is available.
        """
        report = {
            "total_files": 0,
            "indexed_files": 0,
            "skipped_files": 0,
            "removed_files": 0,
            "scanned_files": [],
            "error_files": [],
            "total_pages_indexed": 0,
            "total_chunks_created": 0,
            "embeddings_backfilled": 0,
            "embedding_enabled": False,
            "details": []
        }

        if not self.src_dir.exists():
            report["error"] = f"Thư mục nguồn {self.src_dir} không tồn tại."
            return report

        discovery = PDFExtractor.discover_source_files(self.src_dir)
        source_files = discovery["supported"]
        report["total_files"] = len(source_files)
        report["unsupported_files"] = discovery.get("unsupported", [])
        report["symlink_violations"] = discovery.get("symlink_violations", [])

        # Check if embedding model is functional
        resolved_embed_model = None
        can_embed = False
        native_embed_models = {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge", "bge-m3"}
        is_native_target = (embed_model in native_embed_models) or (embed_model is None and sys.platform == "darwin")

        if embed_model:
            if sys.platform == "darwin" and is_native_target:
                from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
                if NativeEmbeddingClient.available():
                    can_embed = True
                    resolved_embed_model = "bge-m3-mlx-fp16"
                    report["embedding_enabled"] = True
                    logger.info(f"Đã kích hoạt mô hình native MLX embedding: {resolved_embed_model}")
                else:
                    can_embed = False
                    report["embedding_enabled"] = False
                    logger.info("Mô hình native MLX embedding chưa khả dụng.")
            else:
                resolved_embed_model = self.ollama.find_best_embed_model(embed_model)
                if resolved_embed_model:
                    test_emb = self.ollama.get_embedding("test", model=resolved_embed_model)
                    if test_emb is not None:
                        can_embed = True
                        report["embedding_enabled"] = True
                        logger.info(f"Đã kích hoạt mô hình embedding Ollama: {resolved_embed_model}")
                    else:
                        logger.info(f"Mô hình embedding '{resolved_embed_model}' chưa khả dụng trong Ollama.")

        indexed_map = self.get_indexed_files_map()
        force_ocr_files = force_ocr_files or set()
        current_keys = {str(p.relative_to(self.src_dir)) for p in source_files}

        obsolete_filenames = set(indexed_map) - current_keys
        if obsolete_filenames:
            with self.get_connection() as conn:
                conn.executemany(
                    "DELETE FROM documents WHERE filename = ?",
                    [(name,) for name in obsolete_filenames],
                )
            report["removed_files"] = len(obsolete_filenames)

        # Pre-load verified human page reviews so they are honored upon reindexing.
        # Strict provenance:
        # 1. Require BOTH canonical relative filename AND current source_hash.
        # 2. Resolve latest verification state (new revoked/pending overrides older verified).
        # 3. Never allow hash-only fallback across different paths.
        latest_page_review_states: Dict[Tuple[str, int], Dict[str, Any]] = {}

        # 1. Load from source_page_reviews
        try:
            from app.source_page_reviews import latest_page_reviews, validate_page_review, reviewed_page_text
            reviews_root = self.src_dir.parent if hasattr(self.src_dir, "parent") else Path.cwd()
            spr = latest_page_reviews(reviews_root)
            for (r_fn, r_page), (r_path, r_data) in spr.items():
                if type(r_page) is not int or r_page < 1 or not r_fn:
                    continue
                norm_fn = str(Path(r_fn))
                order = (r_data.get("reviewed_at") or "", str(r_path))
                prev = latest_page_review_states.get((norm_fn, r_page))
                if prev is None or order > prev["order"]:
                    is_ver = (r_data.get("image_reviewed") is True and r_data.get("completeness") in ("complete", "partial"))
                    txt = r_data.get("transcript") or r_data.get("text") or ""
                    if r_data.get("kind") == "blank":
                        txt = ""
                    s_sha = r_data.get("source_sha256") or r_data.get("source_hash") or ""
                    latest_page_review_states[(norm_fn, r_page)] = {
                        "order": order,
                        "source_hash": s_sha,
                        "is_verified": is_ver,
                        "text": txt,
                        "needs_review": bool(r_data.get("unresolved")),
                        "review": r_data,
                    }
        except Exception as e:
            logger.debug(f"Could not load source_page_reviews: {e}")

        # 2. Load from vision_ocr CACHE_DIR
        from app.vision_ocr import CACHE_DIR
        if CACHE_DIR.exists():
            for cp in CACHE_DIR.glob("*.json"):
                try:
                    entry = json.loads(cp.read_text(encoding="utf-8"))
                    if not isinstance(entry, dict):
                        continue
                    r_fn = entry.get("source_relpath") or entry.get("filename")
                    r_page = entry.get("page_num") or entry.get("page")
                    if not r_fn or type(r_page) is not int or r_page < 1:
                        continue
                    norm_fn = str(Path(r_fn))
                    order = (entry.get("verified_at") or entry.get("reviewed_at") or "", str(cp))
                    prev = latest_page_review_states.get((norm_fn, r_page))
                    if prev is None or order > prev["order"]:
                        is_ver = bool(entry.get("is_verified") and not entry.get("needs_review") and entry.get("text"))
                        s_sha = entry.get("source_hash") or entry.get("source_sha256") or ""
                        latest_page_review_states[(norm_fn, r_page)] = {
                            "order": order,
                            "source_hash": s_sha,
                            "is_verified": is_ver,
                            "text": entry.get("text") or "",
                            "needs_review": bool(entry.get("needs_review")),
                        }
                except Exception:
                    continue

        for idx, file_path in enumerate(source_files, 1):
            rel_path = str(file_path.relative_to(self.src_dir))
            file_hash = PDFExtractor.calculate_file_hash(file_path)
            current_abs_path = str(file_path.resolve())

            if progress_callback:
                progress_callback(idx, len(source_files), rel_path, "Đang xử lý...")

            # Check for incremental skip
            # Stale pipeline version triggers re-indexing to ensure obsolete extraction is not skipped
            if not force and rel_path in indexed_map:
                existing = indexed_map[rel_path]
                pipeline_matched = (existing.get("pipeline_version") == PIPELINE_VERSION)
                if (existing["file_hash"] == file_hash
                    and pipeline_matched
                    and existing["status"] in ("indexed", "scanned_unocred", "ocr_partial_reviewed")
                    and rel_path not in force_ocr_files
                    and not (ocr and existing["status"] == "scanned_unocred")):

                    if existing.get("filepath") != current_abs_path:
                        with self.get_connection() as conn:
                            conn.execute(
                                "UPDATE documents SET filepath = ? WHERE id = ?",
                                (current_abs_path, existing["id"]),
                            )
                        existing["filepath"] = current_abs_path

                    report["skipped_files"] += 1
                    report["details"].append({
                        "filename": rel_path,
                        "status": "skipped",
                        "reason": "File không thay đổi (đã lập chỉ mục, khớp phiên bản pipeline)"
                    })
                    if existing["is_scanned"]:
                        report["scanned_files"].append(rel_path)
                    continue

            # Extract document content
            is_force = (rel_path in force_ocr_files)
            extract_res = PDFExtractor.extract_file(file_path, force_ocr=is_force) if is_force else PDFExtractor.extract_file(file_path)
            if ocr and extract_res.is_scanned and not is_force:
                extract_res = PDFExtractor.extract_file(file_path, ocr=True)

            extract_res.filename = rel_path

            if extract_res.error_message or extract_res.failed_pages:
                err_msg = extract_res.error_message or f"Lỗi trích xuất trang: {extract_res.failed_pages}"
                report["error_files"].append({"filename": rel_path, "error": err_msg})
                # Extraction failures must not erase a previously usable index
                if rel_path not in indexed_map:
                    self._save_document_record(extract_res, status="error", is_scanned=False)
                report["details"].append({
                    "filename": rel_path,
                    "status": "error",
                    "error": err_msg
                })
                continue

            # Chunk pages, honoring human-verified reviews bound strictly to canonical path and source hash
            norm_rel = str(Path(rel_path))
            all_chunks = []
            extracted_pages_count = 0
            for page in extract_res.extracted_pages:
                p_num = page["page_num"]
                rev_state = latest_page_review_states.get((norm_rel, p_num))
                if rev_state and rev_state["source_hash"] == file_hash and rev_state["is_verified"]:
                    review = rev_state.get("review")
                    if review:
                        validate_page_review(review, rel_path, p_num, file_hash)
                        page_text = reviewed_page_text(dict(review, editorial=''))
                        page["needs_review"] = review["completeness"] != "complete"
                    else:
                        page_text = rev_state["text"]
                    page_has_text = len(page_text.strip()) > 0
                else:
                    page_text = page.get("text")
                    page_has_text = page.get("has_text")

                if page_has_text and page_text:
                    extracted_pages_count += 1
                    chunks = TextChunker.chunk_page(
                        book_title=extract_res.clean_title,
                        filename=rel_path,
                        page_num=p_num,
                        page_text=page_text
                    )
                    all_chunks.extend(chunks)

            # If document was flagged scanned but has NO reviewed chunks, record as scanned_unocred
            if extract_res.is_scanned and not all_chunks:
                report["scanned_files"].append(rel_path)
                with self.get_connection() as conn:
                    doc_id = self._save_document_record(extract_res, status="scanned_unocred", is_scanned=True, connection=conn)
                    self._save_quality_record(conn, doc_id, extract_res)
                report["details"].append({
                    "filename": rel_path,
                    "status": "scanned_unocred",
                    "total_pages": extract_res.total_pages,
                    "warning": "Tài liệu dạng scan/ảnh (không có lớp chữ số OCR)"
                })
                continue

            # Prepare embeddings before modifying the database.
            # If embedding fails, an exception is raised and the transaction is never started,
            # retaining the previous index intact.
            prepared_rows, prov_rows = self._prepare_chunk_rows(all_chunks, can_embed, resolved_embed_model)

            # Determine final status for documents with chunks
            doc_status = "indexed"
            if extract_res.is_scanned:
                doc_status = "indexed" if extracted_pages_count == extract_res.total_pages else "ocr_partial_reviewed"

            # Atomic transaction: upsert document, replace chunks, and record source quality.
            # Rolls back completely if any failure occurs during writing.
            with self.get_connection() as conn:
                doc_id = self._save_document_record(
                    extract_res, status=doc_status, is_scanned=extract_res.is_scanned,
                    extracted_pages_count=extracted_pages_count, connection=conn,
                )
                chunks_inserted = self._insert_chunk_rows(conn, doc_id, prepared_rows, prov_rows)
                self._save_quality_record(conn, doc_id, extract_res)

            report["indexed_files"] += 1
            report["total_pages_indexed"] += extracted_pages_count
            report["total_chunks_created"] += chunks_inserted
            report["details"].append({
                "filename": rel_path,
                "clean_title": extract_res.clean_title,
                "status": "indexed",
                "total_pages": extract_res.total_pages,
                "extracted_pages": extracted_pages_count,
                "chunks_count": chunks_inserted,
                "failed_pages": extract_res.failed_pages,
                "ocr_pages": extract_res.ocr_pages,
                "needs_review": extract_res.needs_review,
                "quality_warnings": extract_res.quality_warnings,
            })

        # If embedding is now enabled, backfill any chunks from previously indexed files that have NULL embeddings
        if can_embed and resolved_embed_model:
            backfilled = self.backfill_missing_embeddings(
                embed_model=resolved_embed_model,
                progress_callback=lambda cur, tot, msg: progress_callback(len(source_files), len(source_files), "Embedding Backfill", msg) if progress_callback else None
            )
            report["embeddings_backfilled"] = backfilled

        return report

    def _save_document_record(
        self,
        res: PDFExtractionResult,
        status: str,
        is_scanned: bool,
        extracted_pages_count: int = 0,
        *,
        connection: Optional[sqlite3.Connection] = None
    ) -> int:
        """Upsert document record in DB and delete old chunks if re-indexing. Preserves existing doc_id."""
        if connection is None:
            with self.get_connection() as conn:
                return self._save_document_record(
                    res, status, is_scanned, extracted_pages_count, connection=conn,
                )
        conn = connection

        pipe_ver = getattr(res, "pipeline_version", PIPELINE_VERSION)
        row = conn.execute("SELECT id FROM documents WHERE filename = ?", (res.filename,)).fetchone()
        if row:
            doc_id = row["id"]
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("""
            UPDATE documents SET
                clean_title = ?,
                filepath = ?,
                file_size = ?,
                file_hash = ?,
                total_pages = ?,
                extracted_pages_count = ?,
                is_scanned = ?,
                status = ?,
                error_message = ?,
                pipeline_version = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """, (
                res.clean_title, str(res.filepath), res.file_size_bytes,
                res.file_hash, res.total_pages, extracted_pages_count,
                1 if is_scanned else 0, status, res.error_message, pipe_ver, doc_id
            ))
        else:
            cursor = conn.execute("""
            INSERT INTO documents (
                filename, clean_title, filepath, file_size, file_hash,
                total_pages, extracted_pages_count, is_scanned, status, error_message, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                res.filename, res.clean_title, str(res.filepath), res.file_size_bytes,
                res.file_hash, res.total_pages, extracted_pages_count,
                1 if is_scanned else 0, status, res.error_message, pipe_ver
            ))
            doc_id = cursor.lastrowid
        return doc_id

    def _save_quality_record(self, conn: sqlite3.Connection, doc_id: int, res: PDFExtractionResult):
        """Save source quality and raw block metadata to database and persisted sidecar."""
        import hashlib
        raw_all = "".join(p.get("raw_text", "") for p in res.extracted_pages)
        raw_capture_hash = hashlib.sha256(raw_all.encode("utf-8")).hexdigest()

        warnings_json = json.dumps(res.quality_warnings, ensure_ascii=False)
        blocks_count = sum(len(p.get("blocks", [])) for p in res.extracted_pages)
        meta = {
            "filename": res.filename,
            "file_hash": res.file_hash,
            "pipeline_version": res.pipeline_version,
            "raw_capture_hash": raw_capture_hash,
            "total_pages": res.total_pages,
            "failed_pages": res.failed_pages,
            "needs_review": res.needs_review,
            "quality_warnings": res.quality_warnings,
            "pages_summary": getattr(res, "raw_capture", {}).get("pages_summary", []),
            "pages": res.extracted_pages
        }
        meta_json = json.dumps(meta, ensure_ascii=False)

        conn.execute("""
            INSERT INTO document_source_quality (
                doc_id, filename, file_hash, pipeline_version,
                needs_review, quality_warnings, raw_char_count,
                raw_capture_hash, blocks_count, quality_metadata, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(doc_id) DO UPDATE SET
                filename=excluded.filename,
                file_hash=excluded.file_hash,
                pipeline_version=excluded.pipeline_version,
                needs_review=excluded.needs_review,
                quality_warnings=excluded.quality_warnings,
                raw_char_count=excluded.raw_char_count,
                raw_capture_hash=excluded.raw_capture_hash,
                blocks_count=excluded.blocks_count,
                quality_metadata=excluded.quality_metadata,
                updated_at=CURRENT_TIMESTAMP
        """, (
            doc_id, res.filename, res.file_hash, res.pipeline_version,
            1 if res.needs_review else 0, warnings_json, res.total_chars,
            raw_capture_hash, blocks_count, meta_json
        ))


    def _save_chunks(
        self,
        doc_id: int,
        chunks: List[Dict[str, Any]],
        can_embed: bool = False,
        embed_model: Optional[str] = None
    ) -> int:
        """Insert chunk records and optional embeddings in one transaction."""
        rows, prov_rows = self._prepare_chunk_rows(chunks, can_embed, embed_model)
        with self.get_connection() as conn:
            return self._insert_chunk_rows(conn, doc_id, rows, prov_rows)

    def _prepare_chunk_rows(self, chunks, can_embed, embed_model):
        rows = []
        prov_rows = []
        use_native = False
        native_client = None
        native_embed_models = {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge", "bge-m3"}
        is_native_target = (embed_model in native_embed_models) or (embed_model is None and sys.platform == "darwin")
        if sys.platform == "darwin" and can_embed and is_native_target:
            from app.native_embedding import NativeEmbeddingClient, BGE_NATIVE_IDENTITY
            if NativeEmbeddingClient.available():
                use_native = True
                native_client = NativeEmbeddingClient()
            else:
                raise RuntimeError("Mô hình MLX embedding chưa sẵn sàng trên Mac; không được hạ cấp sang HTTP.")

        import hashlib
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        for start in range(0, len(chunks), 32):
            batch = chunks[start:start + 32]
            if can_embed and embed_model:
                if use_native and native_client:
                    vectors = native_client.get_batch_embeddings([c["text"] for c in batch])
                    model_digest = getattr(native_client, "get_identity", lambda: BGE_NATIVE_IDENTITY)()
                else:
                    vectors = self.ollama.get_batch_embeddings([c["text"] for c in batch], model=embed_model)
                    model_digest = embed_model
            else:
                vectors = [None] * len(batch)
                model_digest = None
            if len(vectors) != len(batch):
                raise ValueError("Embedding count does not match chunk count")
            if can_embed and embed_model and any(not vec for vec in vectors):
                raise RuntimeError("Embedding backend did not return every requested vector")
            for c, vec in zip(batch, vectors):
                row_idx = len(rows)
                blob = pack_vector(vec) if vec else None
                rows.append((c["book_title"], c["filename"], c["page_num"],
                             c["chunk_index"], c["text"], blob))
                if blob and model_digest:
                    t_sha = hashlib.sha256(c["text"].encode("utf-8")).hexdigest()
                    v_sha = hashlib.sha256(blob).hexdigest()
                    prov_rows.append((row_idx, t_sha, model_digest, v_sha, len(vec), 1, now_iso))
        return rows, prov_rows

    @staticmethod
    def _insert_chunk_rows(conn, doc_id, rows, prov_rows=None):
        if isinstance(rows, tuple) and len(rows) == 2 and prov_rows is None:
            rows, prov_rows = rows
        inserted_ids = []
        for row in rows:
            cursor = conn.execute("""
                INSERT INTO chunks (doc_id, book_title, filename, page_num, chunk_index, text, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, *row))
            inserted_ids.append(cursor.lastrowid)

        if prov_rows:
            p_data = []
            for row_idx, t_sha, m_digest, v_sha, dims, full_text, emb_at in prov_rows:
                chunk_id = inserted_ids[row_idx]
                p_data.append((chunk_id, t_sha, m_digest, v_sha, dims, full_text, emb_at))
            conn.executemany("""
                INSERT OR REPLACE INTO corpus_vector_provenance (chunk_id, text_sha256, model_digest, vector_sha256, dimensions, full_text, embedded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, p_data)
        return len(rows)

    def get_status(self) -> Dict[str, Any]:
        """Get summary statistics of the current knowledge base."""
        with self.get_connection() as conn:
            doc_count = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
            scanned_count = conn.execute("SELECT COUNT(*) as c FROM documents WHERE is_scanned = 1").fetchone()["c"]
            chunk_count = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
            embedded_chunk_count = conn.execute("SELECT COUNT(*) as c FROM chunks WHERE embedding IS NOT NULL").fetchone()["c"]
            total_pages = conn.execute("SELECT SUM(extracted_pages_count) as c FROM documents").fetchone()["c"] or 0

            docs = conn.execute("""
                SELECT id, filename, file_hash, clean_title, total_pages, extracted_pages_count, is_scanned, status, error_message, pipeline_version, updated_at
                FROM documents ORDER BY filename ASC
            """).fetchall()

            from app.vision_ocr import CACHE_DIR
            captured, reviewed, latest_reviews = {}, {}, {}
            if CACHE_DIR.exists():
                for cp in CACHE_DIR.glob("*.json"):
                    try:
                        entry = json.loads(cp.read_text(encoding="utf-8"))
                        if not isinstance(entry, dict):
                            continue
                        key = (entry.get("source_relpath"), entry.get("source_hash"))
                        page = entry.get("page_num")
                        if type(page) is not int or page < 1:
                            continue
                        if entry.get("status") == "CAPTURED_PENDING_REVIEW" or entry.get("text"):
                            captured.setdefault(key, set()).add(page)
                        if entry.get("is_verified") or entry.get("review_schema") or entry.get("verified_by"):
                            identity = (key, page)
                            order = (entry.get("verified_at") or "", cp.stat().st_mtime_ns, cp.name)
                            previous = latest_reviews.get(identity)
                            if previous is None or order > previous[0]:
                                latest_reviews[identity] = (order, entry)
                    except (OSError, ValueError, TypeError):
                        continue
            for (key, page), (_, entry) in latest_reviews.items():
                if entry.get("is_verified") and not entry.get("needs_review") and entry.get("text"):
                    reviewed.setdefault(key, set()).add(page)
            documents = []
            for row in docs:
                d = dict(row)
                key = (d["filename"], d["file_hash"])
                c = {p for p in captured.get(key, set()) if p <= d["total_pages"]}
                r = {p for p in reviewed.get(key, set()) if p <= d["total_pages"]}
                d.update(ocr_captured_pages=len(c), ocr_reviewed_pages=len(r),
                         ocr_pending_review_pages=len(c-r))
                documents.append(d)
            return {
                "total_documents": doc_count,
                "scanned_documents": scanned_count,
                "total_indexed_pages": total_pages,
                "total_chunks": chunk_count,
                "embedded_chunks": embedded_chunk_count,
                "ocr_captured_pages": sum(d["ocr_captured_pages"] for d in documents),
                "ocr_reviewed_pages": sum(d["ocr_reviewed_pages"] for d in documents),
                "ocr_pending_review_pages": sum(d["ocr_pending_review_pages"] for d in documents),
                "documents": documents
            }

    def update_page_chunks(self, filename: str, page_num: int, text: str) -> Dict[str, Any]:
        """Update chunks, embeddings, and full-text search for an affected page after human review."""
        if type(page_num) is not int or page_num < 1 or not isinstance(text, str) or not text.strip():
            raise ValueError("Trang hoặc nội dung đã duyệt không hợp lệ")
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id, filename, clean_title, total_pages, is_scanned FROM documents WHERE filename = ?",
                (filename,)
            ).fetchone()
            if not row:
                logger.warning(f"Không tìm thấy tài liệu {filename} trong documents để cập nhật page chunks.")
                return {"status": "not_found", "chunks_updated": 0}

            doc_id = row["id"]
            clean_title = row["clean_title"]
            rel_filename = row["filename"]
            if page_num > row["total_pages"]:
                raise ValueError("Trang được duyệt nằm ngoài tài liệu")

        chunk_records = TextChunker.chunk_page(clean_title, rel_filename, page_num, text)
        with self.get_connection() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ? AND page_num = ?", (doc_id, page_num))
            for chunk in chunk_records:
                conn.execute("""INSERT INTO chunks
                    (doc_id,book_title,filename,page_num,chunk_index,text,embedding)
                    VALUES (?,?,?,?,?,?,NULL)""", (doc_id,clean_title,rel_filename,page_num,
                    chunk["chunk_index"],chunk["text"]))
            pages = conn.execute("SELECT COUNT(DISTINCT page_num) FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()[0]
            state = "indexed" if pages >= row["total_pages"] else ("ocr_partial_reviewed" if row["is_scanned"] else "indexed")
            conn.execute("UPDATE documents SET extracted_pages_count=?,status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (pages,state,doc_id))
        inserted = len(chunk_records)

        logger.info(f"Đã cập nhật {inserted} chunks cho tài liệu '{rel_filename}' trang {page_num} sau khi review OCR.")
        return {
            "status": "success",
            "doc_id": doc_id,
            "filename": rel_filename,
            "page_num": page_num,
            "chunks_updated": inserted,
        }
