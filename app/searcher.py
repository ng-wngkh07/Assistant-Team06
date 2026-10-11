import re
import sqlite3
import struct
from pathlib import Path
from typing import List, Dict, Any, Optional
import numpy as np

from app.config import (
    DB_PATH, DEFAULT_TOP_K, FTS_WEIGHT, SEMANTIC_WEIGHT,
    RRF_K, DEFAULT_EMBED_MODEL
)
from app.text_cleaner import normalize_vietnamese_text, sanitize_for_prompt_context
from app.ollama_client import OllamaClient
from app.indexer import unpack_vector

class HybridSearcher:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.ollama = OllamaClient()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

def extract_document_scope_tokens(query: str) -> List[str]:
    """Extract document/chapter identifiers from queries specifying a target document."""
    patterns = [
        r"(?:trong\s+(?:tài\s+liệu|sách|giáo\s+trình|bài\s+giảng|chương))\s+([^\?\.\,\!]+)",
        r"(?:tài\s+liệu|sách|giáo\s+trình|bài\s+giảng|chương)\s+([0-9]+[^\?\.\,\!]*)",
    ]
    for pat in patterns:
        m = re.search(pat, query, re.I)
        if m:
            scope = m.group(1).strip()
            words = [
                w for w in re.findall(r"\w+", scope.lower())
                if w not in ("trong", "tài", "liệu", "sách", "giáo", "trình", "bài", "giảng")
            ]
            if words:
                return words
    return []


def is_heading_or_toc_chunk(text: str) -> bool:
    """Detect if chunk is predominantly a table of contents, outline, or heading slide."""
    t = text.strip()
    if not t:
        return True
    tl = t.lower()
    if tl.startswith("nội dung") or tl.startswith("mục lục") or "table of contents" in tl:
        return True
    lines = [line.strip() for line in t.split("\n") if line.strip()]
    if len(lines) <= 6:
        section_lines = sum(1 for line in lines if re.match(r"^(\d+(\.\d+)*\.?|[A-ZĐ\d\.\s\-]{3,30}$)", line))
        if section_lines >= len(lines) - 1 and len(t) < 250:
            return True
    return False


class HybridSearcher:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.ollama = OllamaClient()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _clean_fts_query(query: str) -> str:
        """Sanitize query string for FTS5 syntax, avoiding syntax errors with special chars."""
        cleaned = re.sub(r'["\'\*\^\:\(\)\[\]\{\}\+\-\~]', ' ', query)
        words = [w.strip() for w in cleaned.split() if w.strip()]
        if not words:
            return ""
        # Match any word with prefix matching (e.g., word1* OR word2*)
        terms = [f'"{w}"*' for w in words]
        # Glueword support for common Vietnamese PDF tokens
        w_lower = [w.lower() for w in words]
        if "xạ" in w_lower and "tuyến" in w_lower:
            terms.extend(['"xạtuyến"*', '"sốtuyến"*'])
        return " OR ".join(terms)

    def source_page_chunks(self, doc_id: int, page_num: int, limit: int = 12) -> List[Dict[str, Any]]:
        """Use the explicitly selected page; document titles are only labels."""
        with self.get_connection() as conn:
            rows=conn.execute('''SELECT id AS chunk_id,doc_id,book_title,filename,page_num,chunk_index,text
                FROM chunks WHERE doc_id=? AND page_num=? ORDER BY chunk_index,id LIMIT ?''',
                (doc_id,page_num,limit)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            if not item['text'].strip(): continue
            item['safe_text']=sanitize_for_prompt_context(item['text'])
            item['source_type']='selected_page'
            result.append(item)
        return result

    @staticmethod
    def _number_phrases(query: str) -> str:
        """Keep named concepts such as 'Hệ thống 1' together in FTS."""
        words = re.findall(r"\w+", query, re.UNICODE)
        phrases = []
        for index, word in enumerate(words):
            if not word.isdigit() or index < 2:
                continue
            prefix = words[index - 2:index]
            if all(part.isalpha() for part in prefix):
                phrase = " ".join((*prefix, word))
                if phrase not in phrases:
                    phrases.append(phrase)
        return " OR ".join('"' + phrase + '"' for phrase in phrases)

    def search_fts(self, query: str, limit: int = 20, document_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Perform Full-Text BM25 Search using SQLite FTS5 with heading demotion and document scope."""
        fts_query = self._clean_fts_query(query)
        if not fts_query:
            return []
        # Titles identify documents; only passage content is evidence for QA.
        fts_query = f"text : ({fts_query})"

        q_lower = query.lower()
        is_definition_q = any(k in q_lower for k in ("là gì", "định nghĩa", "khái niệm", "tính chất", "điều kiện", "nguyên lý", "thế nào", "đặc điểm"))
        scope_tokens = extract_document_scope_tokens(query)

        fetch_limit = max(limit * 3, 40)
        results = []
        try:
            with self.get_connection() as conn:
                phrase_query = self._number_phrases(query)
                if phrase_query:
                    phrase_query = f"text : ({phrase_query})"
                    phrase_rows = conn.execute("""
                        SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num,
                               c.chunk_index, c.text, bm25(chunks_fts) as rank_score
                        FROM chunks_fts f JOIN chunks c ON f.rowid = c.id
                    WHERE chunks_fts MATCH ? AND (? IS NULL OR c.doc_id = ?)
                    ORDER BY rank_score ASC LIMIT ?
                    """, (phrase_query, document_id, document_id, fetch_limit)).fetchall()
                else:
                    phrase_rows = []
                cursor = conn.execute("""
                    SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num, c.chunk_index, c.text,
                           bm25(chunks_fts) as rank_score
                    FROM chunks_fts f
                    JOIN chunks c ON f.rowid = c.id
                    WHERE chunks_fts MATCH ? AND (? IS NULL OR c.doc_id = ?)
                    ORDER BY rank_score ASC
                    LIMIT ?
                """, (fts_query, document_id, document_id, fetch_limit))
                
                rows = phrase_rows + cursor.fetchall()
                seen = set()
                scored_candidates = []
                for r in rows:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                    raw_score = abs(float(r["rank_score"])) if r["rank_score"] else 0.0
                    # SQLite BM25 is negative: greater magnitude is a better match.
                    relevance = 10.0 * raw_score / (1.0 + raw_score)

                    # Prioritize named document scope using unaccented matching
                    if scope_tokens:
                        import unicodedata
                        fn_ascii = unicodedata.normalize("NFD", r["filename"]).encode("ascii", "ignore").decode().lower()
                        scope_ascii = [unicodedata.normalize("NFD", tok).encode("ascii", "ignore").decode().lower() for tok in scope_tokens]
                        matches = sum(1 for tok in scope_ascii if tok in fn_ascii)
                        if matches > 0:
                            relevance += (matches / len(scope_ascii)) * 30.0

                    txt = r["text"]
                    txt_lower = txt.lower()
                    txt_norm = re.sub(r"xạtuyến", "xạ tuyến", txt_lower)

                    if is_definition_q:
                        if is_heading_or_toc_chunk(txt):
                            relevance -= 25.0
                        if "định nghĩa" in txt_lower or "definition" in txt_lower:
                            relevance += 8.0
                        if any(p in txt_norm for p in ("hai điều kiện", "tính chất", "thỏa", "f(u + v)", "f(αu)", "nguyên lý")):
                            relevance += 6.0

                    scored_candidates.append((relevance, r))

                scored_candidates.sort(key=lambda x: x[0], reverse=True)
                for rank_idx, (rel, r) in enumerate(scored_candidates[:limit]):
                    results.append({
                        "chunk_id": r["id"],
                        "doc_id": r["doc_id"],
                        "book_title": r["book_title"],
                        "filename": r["filename"],
                        "page_num": r["page_num"],
                        "chunk_index": r["chunk_index"],
                        "text": r["text"],
                        "fts_rank": rank_idx + 1,
                        "fts_score": round(rel, 4),
                        "source_type": "fts"
                    })
        except Exception:
            # Fallback to LIKE query if FTS syntax fails
            try:
                with self.get_connection() as conn:
                    words = [w for w in query.split() if len(w) > 2]
                    if words:
                        conditions = " OR ".join(["c.text LIKE ?" for _ in words])
                        params = [f"%{w}%" for w in words]
                        if document_id is not None:
                            conditions = f"({conditions}) AND c.doc_id = ?"
                            params.append(document_id)
                        params.append(limit)
                        cursor = conn.execute(f"""
                            SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num, c.chunk_index, c.text
                            FROM chunks c
                            WHERE {conditions}
                            LIMIT ?
                        """, params)
                        for rank_idx, r in enumerate(cursor.fetchall()):
                            results.append({
                                "chunk_id": r["id"],
                                "doc_id": r["doc_id"],
                                "book_title": r["book_title"],
                                "filename": r["filename"],
                                "page_num": r["page_num"],
                                "chunk_index": r["chunk_index"],
                                "text": r["text"],
                                "fts_rank": rank_idx + 1,
                                "fts_score": 1.0 / (rank_idx + 1),
                                "source_type": "like"
                            })
            except Exception:
                pass

        return results

    def search_semantic(
        self,
        query: str,
        embed_model: str = DEFAULT_EMBED_MODEL,
        limit: int = 20,
        document_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Perform semantic vector search using cosine similarity over embeddings."""
        from app.gpu_lock import gpu_coordinator
        with gpu_coordinator.acquire_for_inference():
            query_vec = self.ollama.get_embedding(query, model=embed_model)
        if query_vec is None:
            return []

        q_arr = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q_arr)
        if q_norm == 0:
            return []
        q_arr = q_arr / q_norm

        results = []
        with self.get_connection() as conn:
            if document_id is None:
                cursor = conn.execute("SELECT id, doc_id, book_title, filename, page_num, chunk_index, text, embedding FROM chunks WHERE embedding IS NOT NULL")
            else:
                cursor = conn.execute("SELECT id, doc_id, book_title, filename, page_num, chunk_index, text, embedding FROM chunks WHERE embedding IS NOT NULL AND doc_id = ?", (document_id,))
            rows = cursor.fetchall()
            
            scored_rows = []
            for r in rows:
                blob = r["embedding"]
                if not blob:
                    continue
                doc_vec = unpack_vector(blob)
                if doc_vec.shape != q_arr.shape:
                    continue
                d_norm = np.linalg.norm(doc_vec)
                if d_norm > 0:
                    sim = float(np.dot(q_arr, doc_vec / d_norm))
                    scored_rows.append((sim, r))

            scored_rows.sort(key=lambda x: x[0], reverse=True)
            for rank_idx, (sim, r) in enumerate(scored_rows[:limit]):
                results.append({
                    "chunk_id": r["id"],
                    "doc_id": r["doc_id"],
                    "book_title": r["book_title"],
                    "filename": r["filename"],
                    "page_num": r["page_num"],
                    "chunk_index": r["chunk_index"],
                    "text": r["text"],
                    "semantic_rank": rank_idx + 1,
                    "semantic_score": sim,
                    "source_type": "semantic"
                })

        return results

    def search_hybrid(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
        is_broad: bool = False,
        max_per_book: Optional[int] = None,
        document_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute Hybrid Search combining FTS5 and Vector Search via Reciprocal Rank Fusion (RRF).
        Supports relaxed per-book limits for broad queries to ensure important chapters are retained.
        """
        query_clean = normalize_vietnamese_text(query)
        if not query_clean:
            return []

        fts_results = self.search_fts(query_clean, limit=top_k * 3, document_id=document_id)
        semantic_results = []
        if embed_model:
            semantic_results = self.search_semantic(query_clean, embed_model=embed_model, limit=top_k * 3, document_id=document_id)

        # Merge using Reciprocal Rank Fusion (RRF)
        # RRF_score(d) = sum( weight / (k + rank) )
        chunk_map = {}
        rrf_scores = {}

        for item in fts_results:
            cid = item["chunk_id"]
            chunk_map[cid] = item
            rank = item["fts_rank"]
            score = FTS_WEIGHT * (1.0 / (RRF_K + rank))
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + score

        for item in semantic_results:
            cid = item["chunk_id"]
            if cid not in chunk_map:
                chunk_map[cid] = item
            else:
                chunk_map[cid]["semantic_score"] = item["semantic_score"]
            rank = item["semantic_rank"]
            score = SEMANTIC_WEIGHT * (1.0 / (RRF_K + rank))
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + score

        # If only FTS was available, use FTS results
        if not semantic_results and fts_results:
            combined = sorted(fts_results, key=lambda x: x["fts_rank"])
        else:
            sorted_cids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
            combined = []
            for cid in sorted_cids:
                item = chunk_map[cid].copy()
                item["hybrid_score"] = round(rrf_scores[cid], 5)
                combined.append(item)

        # Identical PDF copies must not consume separate evidence slots.
        unique = []
        seen_texts = set()
        for item in combined:
            text_key = " ".join(item["text"].split()).casefold()
            if text_key in seen_texts:
                continue
            seen_texts.add(text_key)
            unique.append(item)
        combined = unique

        # For broad queries, allow more chunks per book (up to 4) so multi-chapter concepts are retained
        book_limit = max_per_book if max_per_book is not None else (4 if is_broad else 2)

        diverse = []
        per_book = {}
        for item in combined:
            filename = item["filename"]
            if per_book.get(filename, 0) >= book_limit:
                continue
            diverse.append(item)
            per_book[filename] = per_book.get(filename, 0) + 1
            if len(diverse) >= top_k:
                break
        for item in combined:
            if len(diverse) >= top_k:
                break
            if item not in diverse:
                diverse.append(item)

        final_results = []
        seen_passages = set()
        for item in diverse:
            # Deduplicate similar passages
            key = (item["filename"], item["page_num"], item["chunk_index"])
            if key in seen_passages:
                continue
            seen_passages.add(key)
            
            # Sanitize content for safe prompt context
            item["safe_text"] = sanitize_for_prompt_context(item["text"])
            fname_lower = str(item.get("filename", "")).lower()
            if fname_lower.endswith((".docx", ".txt", ".md", ".markdown")):
                unit = "Đoạn"
            elif fname_lower.endswith((".png", ".jpg", ".jpeg")):
                unit = "Hình"
            else:
                unit = "Trang"
            item["citation_label"] = f"[{item['book_title']}, {unit} {item['page_num']}]"
            final_results.append(item)
            if len(final_results) >= top_k:
                break

        return final_results
