import json
import logging
import os
import re
import sqlite3
import struct
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import numpy as np

logger = logging.getLogger(__name__)

from app.config import (
    DB_PATH, DEFAULT_TOP_K, FTS_WEIGHT, SEMANTIC_WEIGHT,
    RRF_K, DEFAULT_EMBED_MODEL
)
from app.text_cleaner import normalize_vietnamese_text, sanitize_for_prompt_context
from app.ollama_client import OllamaClient
from app.indexer import unpack_vector

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


def extract_differential_operands(text: str) -> List[tuple[str, str, int, Optional[str]]]:
    """Extract differential operands: (v_num, v_den, order, coef)."""
    pat = re.compile(
        r"(?:(?<=\s)|(?<=[(\[+\-*/=:<>])|^)([0-9]+|[a-zA-Zα-ωΑ-Ω])?\s*"
        r"d(?:\^?(\d+)|([¹²³⁴⁵⁶⁷⁸⁹]))?\s*([A-Za-z])\s*/\s*"
        r"d(?:\^?(\d+)|([¹²³⁴⁵⁶⁷⁸⁹]))?\s*([A-Za-z])(?:\^?(\d+)|([¹²³⁴⁵⁶⁷⁸⁹]))?(?![\w])"
    )
    sups = {"¹": 1, "²": 2, "³": 3, "⁴": 4, "⁵": 5, "⁶": 6, "⁷": 7, "⁸": 8, "⁹": 9}
    res = []
    for m in pat.finditer(text):
        coef, o1_dig, o1_sup, v_num, o2_dig, o2_sup, v_den, o3_dig, o3_sup = m.groups()
        ord_val = 1
        for val in (o1_dig, o1_sup, o2_dig, o2_sup, o3_dig, o3_sup):
            if not val:
                continue
            if val in sups:
                ord_val = sups[val]
                break
            if val.isascii() and val.isdigit():
                ord_val = int(val)
                break
        tup = (v_num, v_den, ord_val, coef)
        if tup not in res:
            res.append(tup)
    return res


STOPWORDS = {
    "là", "gì", "thế", "nào", "sao", "ra", "như", "được", "có", "của", "trong", "ở", "về",
    "cho", "với", "theo", "và", "hoặc", "hay", "những", "các", "một", "này", "đó", "kia",
    "bởi", "tại", "khi", "để", "thì", "mà", "nếu", "bị", "do", "từ", "hãy", "nêu", "biết",
}


def extract_query_concepts(query: str) -> List[str]:
    """Extract candidate concept phrases from query, handling intent frames, ellipsis, and comparisons."""
    concepts: List[str] = []

    # 1. Number phrases with ellipsis (e.g. 'Hệ thống 1 vs 2', 'Hệ thống 1 và 2', 'Hệ thống 1 - 2')
    m_ellipsis = re.search(r'([^\d\W]+(?:\s+[^\d\W]+)*)\s+(\d+)\s*(?:vs\.?|và|với|hoặc|-)\s*(\d+)', query, re.UNICODE)
    if m_ellipsis:
        base = m_ellipsis.group(1).strip()
        num1 = m_ellipsis.group(2).strip()
        num2 = m_ellipsis.group(3).strip()
        p1 = f"{base} {num1}"
        p2 = f"{base} {num2}"
        if p1 not in concepts:
            concepts.append(p1)
        if p2 not in concepts:
            concepts.append(p2)

    # 2. Standard number phrases (e.g. 'Hệ thống 1', 'Chương 2')
    words = re.findall(r"\w+", query, re.UNICODE)
    for index, word in enumerate(words):
        if not word.isdigit() or index < 2:
            continue
        prefix = words[index - 2:index]
        if all(part.isalpha() for part in prefix):
            phrase = " ".join((*prefix, word))
            if phrase not in concepts:
                concepts.append(phrase)

    # 3. Strip target document scope (e.g. "trong tài liệu...", "theo giáo trình...")
    q = query.strip()
    q = re.split(r"\b(?:trong\s+(?:tài\s+liệu|sách|giáo\s+trình|bài\s+giảng|chương))\b", q, flags=re.I)[0].strip()
    q = re.sub(r"^(?:ở|trong|tại)\s+(?:phần|mục|chương|bài|chủ\s+đề)\s+[^,;:\n]+[,;:\n]\s*", "", q, flags=re.I).strip()

    # 4. Explicit comparison intent: "so sánh A và B", "A và B khác nhau thế nào", "A vs B"
    compare_match = re.search(
        r"^(?:hãy\s+)?(?:so\s+sánh|đối\s+chiếu|phân\s+biệt|giải\s+thích\s+sự\s+khác\s+nhau|sự\s+khác\s+biệt|điểm\s+khác\s+biệt)\s+(?:giữa\s+)?(.*?)(?:\s+(?:khác\s+nhau|khác\s+gì|như\s+thế\s+nào|ra\s+sao)\??$|$)",
        q,
        flags=re.I,
    )
    if compare_match:
        target = compare_match.group(1).strip()
        for sep in [r"\s+và\s+", r"\s+với\s+", r"\s+vs\.?\s+", r"\s+versus\s+"]:
            parts = re.split(sep, target, maxsplit=1, flags=re.I)
            if len(parts) == 2:
                c1, c2 = parts[0].strip(), parts[1].strip()
                c1 = re.sub(r"[?,.]+$", "", c1).strip()
                c2 = re.sub(r"[?,.]+$", "", c2).strip()
                if c1 and c1 not in concepts:
                    concepts.append(c1)
                if c2 and c2 not in concepts:
                    concepts.append(c2)

    # 5. Generic intent stripping for single concept queries
    lead_pat = (
        r"^(?:hãy\s+)?(?:cho\s+biết|giải\s+thích|trình\s+bày|nêu|tìm\s+hiểu(?:\s+về)?|"
        r"định\s+nghĩa(?:\s+(?:về|của))?|khái\s+niệm(?:\s+(?:về|của))?|tính\s+chất(?:\s+(?:về|của))?|"
        r"đặc\s+điểm(?:\s+(?:về|của))?|nguyên\s+lý(?:\s+(?:về|của))?|ý\s+nghĩa(?:\s+(?:về|của))?|"
        r"nội\s+dung(?:\s+(?:về|của))?|quy\s+luật(?:\s+(?:về|của))?|quy\s+tắc(?:\s+(?:về|của))?|"
        r"công\s+thức(?:\s+(?:tính|của))?|cách\s+(?:tính|áp\s+dụng|sử\s+dụng)?|"
        r"lý\s+thuyết(?:\s+(?:về|của))?|học\s+thuyết(?:\s+(?:về|của))?|kiến\s+thức(?:\s+(?:về|của))?|"
        r"cơ\s+sở\s+lý\s+thuyết(?:\s+(?:về|của))?|tổng\s+quan(?:\s+(?:về|của))?)\s+"
    )
    q_clean = re.sub(lead_pat, "", q, flags=re.I).strip()
    # Enumeration requests describe answer shape, not the subject's name.
    q_clean = re.sub(
        r"^(?:(?:hãy|nêu|trình bày)\s+)*(?:\d+|hai|ba|bốn|năm|sáu)\s+"
        r"(?:tính\s+chất|đặc\s+điểm|nguyên\s+tắc|thành\s+phần)"
        r"(?:\s+(?:cơ\s+bản|cốt\s+lõi|chính|quan\s+trọng))?\s+của\s+",
        "", q_clean, flags=re.I,
    )

    trail_pat = (
        r"\s+(?:cho\s+biết|hãy\s+cho\s+biết|xác\s+định)\s+.*$|"
        r"\s+(?:là\s+gì|như\s+thế\s+nào|thế\s+nào|ra\s+sao|là\s+sao|nghĩa\s+là\s+gì|có\s+nghĩa\s+là\s+gì|"
        r"được\s+hiểu\s+như\s+thế\s+nào|được\s+định\s+nghĩa\s+(?:như\s+thế\s+nào|là\s+gì)|"
        r"được\s+tính\s+như\s+thế\s+nào|khác\s+nhau\s+(?:như\s+thế\s+nào|thế\s+nào|ra\s+sao)|"
        r"khác\s+gì(?:\s+nhau)?)[\?\.\!\s]*$"
    )
    q_clean = re.sub(trail_pat, "", q_clean, flags=re.I).strip(" ?.!,:")
    q_clean = re.sub(r"^(?:của|về)\s+", "", q_clean, flags=re.I).strip()

    if q_clean and len(q_clean) >= 2 and not q_clean.lower().startswith("so sánh"):
        if q_clean not in concepts:
            concepts.append(q_clean)

    if re.search(r"lập trình hướng đối tượng|\boop\b", q_clean, re.I):
        for alias in ("lập trình hướng đối tượng", "OOP"):
            if alias not in concepts:
                concepts.append(alias)

    for v_num, v_den, ord_val, coef in extract_differential_operands(query):
        if ord_val == 2:
            d_concept = f"d²{v_num}/d{v_den}²"
        elif ord_val == 3:
            d_concept = f"d³{v_num}/d{v_den}³"
        else:
            d_concept = f"d{v_num}/d{v_den}"
        if d_concept not in concepts:
            concepts.append(d_concept)

    return concepts


def is_heading_or_toc_chunk(text: str) -> bool:
    """Detect if chunk is predominantly a table of contents, outline, or heading slide."""
    t = text.strip()
    if not t:
        return True
    tl = t.lower()
    lines = [line.strip() for line in t.split("\n") if line.strip()]
    if not lines:
        return True

    # 1. Detect dot leaders (e.g. "........", "…", "----", spaced ". . . . .") connecting titles to page numbers
    leader_pat = re.compile(r"((?:\.\s*){4,}|…{2,}|_{4,}|-{4,})\s*\d*\s*$")
    dot_leader_lines = sum(1 for line in lines if leader_pat.search(line))
    if dot_leader_lines >= 2:
        # Check if there is actual substantive body text (> 80 chars not containing dot leaders)
        prose_lines = sum(1 for line in lines if len(line) > 80 and not leader_pat.search(line))
        if prose_lines < 2:
            return True

    # 2. Explicit TOC headers without substantive body
    has_toc_header = any(tl.startswith(prefix) for prefix in ("mục lục", "table of contents")) or (
        tl.startswith("nội dung") and any(w in tl for w in ("chương trình", "mục lục", "chi tiết", "bài giảng", "khóa học"))
    )
    if has_toc_header:
        # Preserve if there's actual explanatory body (> 200 chars of non-heading prose)
        substantive = [l for l in lines if len(l) > 60 and not re.match(r"^(\d+[\.\)]|[A-ZĐ\.\-\s]{3,})", l)]
        if not substantive or len(" ".join(substantive)) < 150:
            return True

    # 3. Repeated numbering with no body (e.g. 1. Title, 2. Title)
    numbered_lines = sum(1 for line in lines if re.match(r"^\d+[\.\)]\s*", line))
    if len(lines) >= 3 and numbered_lines >= len(lines) * 0.6:
        has_substantive_math = any(c in t for c in '=><≈≠≤≥∫∑√') or bool(re.search(r'\b(?:d\s*[A-Za-z]\s*/\s*d?\s*[A-Za-z]|f′|lim_)\b', t))
        if not has_substantive_math:
            long_prose = sum(1 for line in lines if len(line) > 80 and not leader_pat.search(line))
            if long_prose == 0:
                return True

    # 4. Short outline slides (few lines, all short headings, total length < 250 chars)
    if len(lines) <= 6 and len(t) < 250:
        section_lines = sum(1 for line in lines if re.match(r"^(\d+(\.\d+)*\.?|[A-ZĐ\d\.\s\-]{3,30}$)", line))
        if section_lines > 0 and section_lines >= len(lines) - 1:
            return True

    return False


def check_local_definition_and_catalog(text: str, concepts: List[str]) -> tuple[bool, bool, bool]:
    """
    Distinguish locally attached explanation/definition from remote catalogue cues.
    Returns (locally_attached, has_explanation, is_catalogue).
    """
    t = text.strip()
    if not t:
        return False, False, False
    t_lower = t.lower()
    t_norm = re.sub(r"xạtuyến", "xạ tuyến", t_lower)
    lines = [line.strip() for line in t.split("\n") if line.strip()]

    # 1. Catalogue / Playlist / Syllabus indicators
    cat_header_patterns = [
        r"danh\s+sách\s+(?:bài\s+học|khóa\s+học|video|tài\s+nguyên|mục\s+lục)",
        r"(?:kế\s+hoạch|lịch\s+trình|lịch\s+học|tiến\s+trình)\s+(?:học\s+tập|giảng\s+dạy|chi\s+tiết)?",
        r"(?:video|bài\s+tập)\s+và\s+(?:lịch\s+học|tài\s+liệu)",
        r"tài\s+liệu\s+bổ\s+sung\s+và\s+lịch",
        r"playlist",
        r"slide\s+bài\s+giảng",
    ]
    has_cat_header = any(re.search(pat, t_lower) for pat in cat_header_patterns)
    schedule_lines = sum(
        1 for line in lines
        if re.match(r"^(?:tuần|buổi|tiết|bài|chương|lecture|week|session)\s+\d+[:\.\-]", line.lower())
    )
    is_catalogue = has_cat_header or (schedule_lines >= 2)

    target_concepts = [c.strip().lower() for c in concepts if c and c.strip()] if concepts else []

    def_cues = (
        "định nghĩa", "khái niệm", "được định nghĩa là", "được định nghĩa", "là giới hạn",
        "công thức", "nguyên lý", "ý nghĩa", "bản chất", "tính chất", "đặc điểm", "quy luật",
        "lý thuyết", "học thuyết", "hai điều kiện", "f(u + v)", "f(αu)", "f′(", "lim_",
        "definition", "meaning"
    )
    explanation_cues = (
        "giải thích", "điều kiện áp dụng", "thành phần", "nội dung", "mô tả",
        "được hiểu là", "tức là", "cụ thể", "chứng minh", "áp dụng cho",
        "cung cấp kiến thức", "biểu diễn", "nêu ý nghĩa", "nêu rõ"
    )

    locally_attached = False
    has_explanation = False

    if not target_concepts:
        if any(c in t_lower for c in ("được định nghĩa là", "định nghĩa:", "khái niệm:")):
            locally_attached = True
            has_explanation = any(c in t_lower for c in explanation_cues) or len(t) > 120
        return locally_attached, has_explanation, is_catalogue

    for concept in target_concepts:
        c_words = [w for w in re.findall(r"\w+", concept) if w not in STOPWORDS]
        if not c_words:
            continue

        for idx, line in enumerate(lines):
            line_lower = line.lower()
            has_concept_in_line = (concept in line_lower) or (
                len(c_words) >= 2 and all(w in line_lower for w in c_words)
            )
            if not has_concept_in_line:
                continue

            # Check if this line itself is a schedule entry (e.g. "Tuần 6: đạo hàm...")
            is_schedule_line = bool(re.match(r"^(?:tuần|buổi|tiết|lecture|week)\s+\d+", line_lower))

            # Case A: Same line contains definition cue
            if any(cue in line_lower for cue in def_cues):
                locally_attached = True
                surrounding = " ".join(lines[idx:idx + 3]).lower()
                if any(ec in surrounding for ec in explanation_cues) or len(surrounding) > 100:
                    has_explanation = True
                break

            # Case B: Line is a heading with concept, adjacent line (idx+1 or idx+2) has definition cue
            if not is_schedule_line:
                for next_idx in (idx + 1, idx + 2):
                    if next_idx < len(lines):
                        next_line_lower = lines[next_idx].lower()
                        if re.match(r"^(?:tuần|buổi|tiết|lecture|week)\s+\d+", next_line_lower):
                            break
                        if any(cue in next_line_lower for cue in def_cues):
                            locally_attached = True
                            body_lines = [lines[j] for j in range(next_idx, min(len(lines), next_idx + 4))]
                            body_text = " ".join(body_lines).lower()
                            if any(ec in body_text for ec in explanation_cues) or len(body_text) > 80:
                                has_explanation = True
                            break
            if locally_attached:
                break

            # Case C: Character window in prose (not schedule lines)
            if not is_schedule_line:
                pos = t_lower.find(concept)
                while pos != -1:
                    window_start = max(0, pos - 120)
                    window_end = min(len(t_lower), pos + len(concept) + 160)
                    window = t_lower[window_start:window_end]
                    if not re.search(r"(?:tuần|buổi|tiết)\s+\d+", window):
                        if any(cue in window for cue in def_cues):
                            locally_attached = True
                            if any(ec in window for ec in explanation_cues) or len(t) > 120:
                                has_explanation = True
                            break
                    pos = t_lower.find(concept, pos + 1)
            if locally_attached:
                break

    return locally_attached, has_explanation, is_catalogue


class HybridSearcher:
    def __init__(self, db_path: Optional[Union[Path, str]] = None):
        self._db_path = Path(db_path) if db_path is not None else None
        self.ollama = OllamaClient()

    @property
    def db_path(self) -> Path:
        if self._db_path is not None:
            return self._db_path
        from app.config import DB_PATH as CONFIG_DB_PATH
        return CONFIG_DB_PATH

    @db_path.setter
    def db_path(self, val: Optional[Union[Path, str]]):
        self._db_path = Path(val) if val is not None else None

    def get_connection(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            from app.indexer import KnowledgeIndexer
            KnowledgeIndexer(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def connect(self) -> sqlite3.Connection:
        return self.get_connection()

    @staticmethod
    def _clean_fts_query(query: str) -> str:
        """Sanitize query string for FTS5 syntax, avoiding syntax errors with special chars."""
        cleaned = re.sub(r'["\'\*\^\:\(\)\[\]\{\}\+\-\~]', ' ', query)
        words = [w.strip() for w in cleaned.split() if w.strip()]
        if not words:
            return ""
        meaningful = [w for w in words if w.lower() not in STOPWORDS]
        target_words = meaningful if meaningful else words
        terms = [f'"{w}"*' for w in target_words]
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
        self._attach_page_quality_metadata(result)
        return result

    def score_page_chunks(
        self,
        chunks: List[Dict[str, Any]],
        query: str,
        doc_id: int,
        page_num: int,
        embed_model: Optional[str] = DEFAULT_EMBED_MODEL,
    ) -> None:
        """Score verified chunks on the selected page using stored vectors and embedding model.

        Fails closed on missing/zero/nonfinite/dimension-invalid vectors or embedding errors.
        Never borrows vectors or scores across documents, pages, or files.
        """
        if not chunks or not embed_model or not query:
            return

        use_native = False
        native_embed_models = {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge"}
        if sys.platform != "darwin" and (embed_model in native_embed_models or "mlx" in (embed_model or "")):
            return

        installed_digest = None
        with self.get_connection() as conn:
            try:
                row = conn.execute("SELECT model_digest FROM corpus_vector_provenance WHERE full_text=1 LIMIT 1").fetchone()
                if row:
                    installed_digest = row[0]
            except sqlite3.Error:
                pass

        if installed_digest and installed_digest.startswith("ollama-sha256:"):
            use_native = False
        elif installed_digest and installed_digest.startswith("bge-m3-mlx-fp16@"):
            if sys.platform != "darwin":
                logger.warning("Installed native BGE knowledge pack requires macOS; dense retrieval disabled")
                return
            use_native = True
        elif os.getenv("EMBED_BACKEND") == "ollama":
            use_native = False
        elif os.getenv("EMBED_BACKEND") in ("native", "mlx"):
            if sys.platform != "darwin":
                logger.warning("Native embedding requires macOS; dense retrieval disabled")
                return
            use_native = True
        elif sys.platform == "darwin" and (embed_model is None or embed_model in native_embed_models or "mlx" in str(embed_model)):
            use_native = True
        else:
            use_native = False

        if use_native:
            from app.native_embedding import NativeEmbeddingClient
            if not NativeEmbeddingClient.available():
                return

        from app.native_embedding import BGE_NATIVE_IDENTITY
        try:
            expected_identity = BGE_NATIVE_IDENTITY if use_native else getattr(
                self.ollama, "embedding_identity", lambda model: None)(embed_model)
        except Exception as exc:
            logger.warning("Embedding identity unavailable; dense retrieval disabled: %s", type(exc).__name__)
            return
        if not expected_identity:
            return

        if use_native:
            from app.native_embedding import NativeEmbeddingClient
            try:
                query_vec = NativeEmbeddingClient().get_embedding(query, expected_identity=expected_identity)
            except Exception as exc:
                logger.warning("Selected-page embedding unavailable: %s", type(exc).__name__)
                return
        else:
            embed_client = getattr(self, "ollama", None)
            if embed_client is None or not hasattr(embed_client, "get_embedding"):
                return
            try:
                from app.gpu_lock import gpu_coordinator
                with gpu_coordinator.acquire_for_inference():
                    query_vec = embed_client.get_embedding(query, model=embed_model)
            except Exception:
                return

        if query_vec is None:
            return

        if not use_native:
            try:
                post_identity = getattr(self.ollama, "embedding_identity", lambda model: None)(embed_model)
            except Exception as exc:
                logger.warning("Post-query embedding identity check failed: %s", type(exc).__name__)
                return
            if post_identity != expected_identity:
                return
        try:
            q_arr = np.array(query_vec, dtype=np.float32)
        except Exception:
            return
        if q_arr.ndim != 1 or q_arr.size == 0 or not np.all(np.isfinite(q_arr)):
            return
        q_norm = float(np.linalg.norm(q_arr))
        if q_norm <= 0 or not np.isfinite(q_norm):
            return
        q_arr = q_arr / q_norm

        target_chunks = [
            c for c in chunks
            if c.get("chunk_id") is not None
            and c.get("doc_id") == doc_id
            and c.get("page_num") == page_num
        ]
        if not target_chunks:
            return

        import hashlib
        chunk_ids = [c["chunk_id"] for c in target_chunks]
        id_to_row = {}
        try:
            with self.get_connection() as conn:
                placeholders = ",".join("?" for _ in chunk_ids)
                sql = f"""
                    SELECT c.id, c.doc_id, c.filename, c.page_num, c.chunk_index, c.text, c.embedding,
                           p.text_sha256, p.model_digest, p.vector_sha256, p.dimensions, p.full_text
                    FROM chunks c JOIN corpus_vector_provenance p ON p.chunk_id = c.id
                    WHERE c.id IN ({placeholders}) AND c.doc_id = ? AND c.page_num = ?
                      AND c.embedding IS NOT NULL AND p.model_digest = ? AND p.full_text = 1 AND p.dimensions = ?
                """
                cursor = conn.execute(sql, (*chunk_ids, doc_id, page_num, expected_identity, len(q_arr)))
                for row in cursor.fetchall():
                    cid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
                    id_to_row[cid] = row
        except Exception:
            return

        for c in target_chunks:
            row = id_to_row.get(c["chunk_id"])
            if not row:
                continue
            r_filename = row["filename"] if isinstance(row, sqlite3.Row) else row[2]
            r_chunk_index = row["chunk_index"] if isinstance(row, sqlite3.Row) else row[4]
            r_text = row["text"] if isinstance(row, sqlite3.Row) else row[5]
            blob = row["embedding"] if isinstance(row, sqlite3.Row) else row[6]

            if (
                c.get("filename") != r_filename
                or c.get("chunk_index") != r_chunk_index
                or c.get("text") != r_text
                or not blob
            ):
                continue

            actual_text_sha = hashlib.sha256(r_text.encode("utf-8")).hexdigest()
            actual_vec_sha = hashlib.sha256(blob).hexdigest()
            p_text_sha = row["text_sha256"] if isinstance(row, sqlite3.Row) else row[7]
            p_vec_sha = row["vector_sha256"] if isinstance(row, sqlite3.Row) else row[9]
            if actual_text_sha != p_text_sha or actual_vec_sha != p_vec_sha:
                continue

            try:
                doc_vec = unpack_vector(blob)
            except Exception:
                continue
            if not isinstance(doc_vec, np.ndarray) or doc_vec.shape != q_arr.shape:
                continue
            if not np.all(np.isfinite(doc_vec)):
                continue
            d_norm = float(np.linalg.norm(doc_vec))
            if d_norm <= 0 or not np.isfinite(d_norm):
                continue
            if use_native and abs(d_norm - 1.0) > 1e-3:
                continue
            sim = float(np.dot(q_arr, doc_vec / d_norm))
            if np.isfinite(sim):
                c["semantic_score"] = sim


    def _attach_page_quality_metadata(self, items: List[Dict[str, Any]]) -> None:
        """Attach page quality and validation warnings if corpus_page_quality or document_source_quality exists."""
        if not items:
            return
        try:
            with self.get_connection() as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "corpus_page_quality" in tables:
                    for item in items:
                        fn = item.get("filename")
                        pn = item.get("page_num")
                        if fn and pn is not None:
                            row = conn.execute(
                                "SELECT needs_review, warnings_json, review_path, raw_capture FROM corpus_page_quality WHERE filename=? AND page_num=?",
                                (fn, pn)
                            ).fetchone()
                            if row:
                                item["page_quality"] = {
                                    "needs_review": bool(row["needs_review"]),
                                    "warnings": json.loads(row["warnings_json"]) if row["warnings_json"] else [],
                                    "review_path": row["review_path"],
                                    "raw_capture": bool(row["raw_capture"]),
                                }
                elif "document_source_quality" in tables:
                    for item in items:
                        fn = item.get("filename")
                        if fn:
                            row = conn.execute(
                                "SELECT pages_summary FROM document_source_quality WHERE filename=?",
                                (fn,)
                            ).fetchone()
                            if row and row["pages_summary"]:
                                item["page_quality"] = {
                                    "needs_review": False,
                                    "warnings": [],
                                    "pages_summary": json.loads(row["pages_summary"]),
                                }
        except Exception:
            pass

    def expand_markdown_answers(self, chunks, max_chunks=12, max_chars=12000):
        """Bind an indexed Markdown exercise body to its adjacent numbered answer.

        Reading sections are not PDF pages. Expand only an exact stored anchor
        with the layout: numbered exercise heading, body, matching answer
        heading, answer body. Keep each added row independently citable.
        """
        result = list(chunks)
        size = sum(len(c.get("text", "")) for c in result)
        seen = {c.get("chunk_id", c.get("id")) for c in result}
        positions = {c.get("chunk_id", c.get("id")): i for i, c in enumerate(result)}
        added = []
        with self.get_connection() as conn:
            for anchor in chunks:
                if Path(str(anchor.get("filename", ""))).suffix.casefold() not in {".md", ".markdown"}:
                    continue
                cid = anchor.get("chunk_id", anchor.get("id"))
                row = conn.execute("SELECT id AS chunk_id,doc_id,book_title,filename,page_num,chunk_index,text FROM chunks WHERE id=?", (cid,)).fetchone()
                keys = ("doc_id", "filename", "page_num", "chunk_index", "text")
                if row is None or any(anchor.get(k) != row[k] for k in keys):
                    continue
                page = row["page_num"]
                nearby = conn.execute(
                    "SELECT id AS chunk_id,doc_id,book_title,filename,page_num,chunk_index,text FROM chunks "
                    "WHERE doc_id=? AND filename=? AND page_num BETWEEN ? AND ? ORDER BY page_num,chunk_index,id",
                    (row["doc_id"], row["filename"], page-1, page+2),
                ).fetchall()
                by_section = {}
                for item in nearby:
                    by_section.setdefault(item["page_num"], []).append(item)
                before = by_section.get(page-1, [])
                after = by_section.get(page+1, [])
                if len(before) != 1 or len(after) != 1:
                    continue
                question_heading = re.fullmatch(
                    r"(?i)\s*#{1,6}\s+(?:bài\s+tập|exercises?|problems?)\s+(\d+(?:[.:]\d+)*)[.:]?(?:[ \t]+[^\n]*)?\s*",
                    before[0]["text"],
                )
                answer_heading = re.fullmatch(
                    r"(?i)\s*#{1,6}\s+(?:đáp\s+án|lời\s+giải|answers?|solutions?)\s+"
                    r"(?:(?:bài|câu|exercise|problem)\s+)?(\d+(?:[.:]\d+)*)[.:]?[ \t]*",
                    after[0]["text"],
                )
                if not question_heading or not answer_heading or question_heading[1] != answer_heading[1]:
                    continue
                for item in by_section.get(page+2, []):
                    if (not item["text"].strip() or re.match(r"^\s*#{1,6}\s", item["text"])):
                        continue
                    if item["chunk_id"] in seen:
                        # Annotate only the verified stored answer; preserve
                        # caller-owned records and their citation positions.
                        pos = positions[item["chunk_id"]]
                        result[pos] = dict(result[pos], exercise_question=row["text"])
                        continue
                    if len(result) >= max_chunks or size+len(item["text"]) > max_chars:
                        break
                    candidate = dict(item)
                    candidate.update(safe_text=sanitize_for_prompt_context(item["text"]),
                                     citation_label=f"[{item['book_title']}, Đoạn {item['page_num']}]",
                                     source_type="exercise_answer_context", exercise_question=row["text"])
                    positions[item["chunk_id"]] = len(result)
                    result.append(candidate); added.append(candidate)
                    seen.add(item["chunk_id"]); size += len(item["text"])
        if added:
            self._attach_page_quality_metadata(added)
        return result

    def expand_page_context(
        self,
        chunks: List[Dict[str, Any]],
        max_chunks: int = 12,
        max_chars: int = 12000
    ) -> List[Dict[str, Any]]:
        """
        Bounded same-page neighboring context expansion.
        Keep all input anchors unchanged in their order, never mutate.
        Verify each anchor against DB identity (chunk_id/doc_id/filename/page/chunk_index/text);
        only retrieve immediate +/-1 chunk_index with SAME doc_id, filename AND page_num.
        Append whole distinct neighbor rows as separately citable records with safe_text,
        normal citation_label, quality metadata, source_type neighboring_context.
        Do not fabricate semantic/hybrid scores on neighbors.
        Bounded total count/chars: preserve anchors even if over budget, but then add none.
        Missing/mismatched identities cannot expand.
        """
        if not chunks:
            return []

        result = list(chunks)
        total_chunks = len(result)
        total_chars = sum(len(c.get("text", "")) for c in result)

        if total_chunks >= max_chunks or total_chars >= max_chars:
            return result

        try:
            with self.get_connection() as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "chunks" not in tables:
                    return result

                seen_ids = set()
                seen_keys = set()
                for c in result:
                    cid = c.get("chunk_id") if c.get("chunk_id") is not None else c.get("id")
                    if cid is not None:
                        seen_ids.add(cid)
                    seen_keys.add((c.get("doc_id"), c.get("filename"), c.get("page_num"), c.get("chunk_index")))

                new_neighbors = []
                for c in chunks:
                    cid = c.get("chunk_id") if c.get("chunk_id") is not None else c.get("id")
                    doc_id = c.get("doc_id")
                    filename = c.get("filename")
                    page_num = c.get("page_num") if c.get("page_num") is not None else c.get("page")
                    chunk_index = c.get("chunk_index")
                    text = c.get("text")

                    if any(v is None for v in (cid, doc_id, filename, page_num, chunk_index, text)):
                        continue

                    row = conn.execute(
                        "SELECT id AS chunk_id, doc_id, book_title, filename, page_num, chunk_index, text FROM chunks WHERE id = ?",
                        (cid,)
                    ).fetchone()
                    if not row:
                        continue
                    if not (row["doc_id"] == doc_id and row["filename"] == filename and
                            row["page_num"] == page_num and row["chunk_index"] == chunk_index and
                            row["text"] == text):
                        continue

                    # Verified anchor identity. Query immediate +/-1 chunk_index on same doc, filename, page
                    candidates = conn.execute(
                        """SELECT id AS chunk_id, doc_id, book_title, filename, page_num, chunk_index, text
                           FROM chunks
                           WHERE doc_id = ? AND filename = ? AND page_num = ? AND chunk_index IN (?, ?)
                           ORDER BY chunk_index""",
                        (doc_id, filename, page_num, chunk_index - 1, chunk_index + 1)
                    ).fetchall()

                    for cand_row in candidates:
                        cand = dict(cand_row)
                        cand_id = cand["chunk_id"]
                        cand_key = (cand["doc_id"], cand["filename"], cand["page_num"], cand["chunk_index"])
                        if cand_id in seen_ids or cand_key in seen_keys:
                            continue
                        if not cand["text"].strip():
                            continue
                        cand_len = len(cand["text"])
                        if total_chunks + 1 > max_chunks or total_chars + cand_len > max_chars:
                            continue

                        cand["safe_text"] = sanitize_for_prompt_context(cand["text"])
                        fname_lower = str(cand.get("filename", "")).lower()
                        if fname_lower.endswith((".docx", ".txt", ".md", ".markdown")):
                            unit = "Đoạn"
                        elif fname_lower.endswith((".png", ".jpg", ".jpeg")):
                            unit = "Hình"
                        else:
                            unit = "Trang"
                        cand["citation_label"] = f"[{cand['book_title']}, {unit} {cand['page_num']}]"
                        cand["source_type"] = "neighboring_context"

                        seen_ids.add(cand_id)
                        seen_keys.add(cand_key)
                        total_chunks += 1
                        total_chars += cand_len
                        new_neighbors.append(cand)

                if new_neighbors:
                    self._attach_page_quality_metadata(new_neighbors)
                    result.extend(new_neighbors)
        except Exception:
            return result

        return result

    @staticmethod
    def _number_phrases(query: str) -> str:
        """Keep named concepts such as 'Hệ thống 1' together in FTS, including ellipsis like 'Hệ thống 1 vs 2'."""
        words = re.findall(r"\w+", query, re.UNICODE)
        phrases = []
        m_ellipsis = re.search(r'([^\d\W]+(?:\s+[^\d\W]+)*)\s+(\d+)\s*(?:vs\.?|và|với|hoặc|-)\s*(\d+)', query, re.UNICODE)
        if m_ellipsis:
            base = m_ellipsis.group(1).strip()
            phrases.append(f"{base} {m_ellipsis.group(2).strip()}")
            phrases.append(f"{base} {m_ellipsis.group(3).strip()}")
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
        fts_query = f"text : ({fts_query})"

        q_lower = query.lower()
        is_definition_q = any(k in q_lower for k in (
            "là gì", "định nghĩa", "khái niệm", "tính chất", "điều kiện", "nguyên lý",
            "thế nào", "đặc điểm", "công thức", "lý thuyết", "học thuyết", "bản chất", "ý nghĩa"
        ))
        scope_tokens = extract_document_scope_tokens(query)
        concept_phrases = extract_query_concepts(query)
        diff_operands = extract_differential_operands(query)
        diff_fts_phrases = []
        for v_num, v_den, ord_val, coef in diff_operands:
            if ord_val >= 2:
                diff_fts_phrases.extend([
                    f'\"d {ord_val}{v_num} d{v_den} {ord_val}\"',
                    f'\"d {ord_val}{v_num} d {v_den} {ord_val}\"',
                    f'\"d {ord_val} {v_num} d{v_den} {ord_val}\"',
                    f'\"d {ord_val} {v_num} d {v_den} {ord_val}\"',
                    f'\"{ord_val}{v_num}\"',
                    f'\"d^{ord_val}{v_num} d{v_den}^{ord_val}\"',
                    f'\"d^{ord_val}{v_num} d {v_den}^{ord_val}\"',
                ])
                if ord_val == 2:
                    diff_fts_phrases.extend([
                        f'\"d²{v_num} d{v_den}²\"',
                        f'\"d²{v_num} d {v_den}²\"',
                        f'\"d² {v_num} d{v_den}²\"',
                        f'\"d² {v_num} d {v_den}²\"',
                    ])
                elif ord_val == 3:
                    diff_fts_phrases.extend([
                        f'\"d³{v_num} d{v_den}³\"',
                        f'\"d³{v_num} d {v_den}³\"',
                        f'\"d³ {v_num} d{v_den}³\"',
                        f'\"d³ {v_num} d {v_den}³\"',
                    ])
            else:
                diff_fts_phrases.extend([
                    f'\"d {v_num} d {v_den}\"',
                    f'\"d {v_num} d{v_den}\"',
                    f'\"d {v_num} {v_den}\"',
                    f'\"d{v_num} d {v_den}\"',
                    f'\"d{v_num} d{v_den}\"',
                    f'\"d{v_num} {v_den}\"',
                ])

        fetch_limit = max(limit * 4, 100)
        results = []
        try:
            with self.get_connection() as conn:
                phrase_rows = []

                # 0. Differential operand phrase queries with source whitespace variants
                if diff_fts_phrases:
                    unique_diff = list(dict.fromkeys(diff_fts_phrases))
                    or_diff_query = " OR ".join(unique_diff)
                    try:
                        d_rows = conn.execute("""
                            SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num,
                                   c.chunk_index, c.text, bm25(chunks_fts) as rank_score
                            FROM chunks_fts f JOIN chunks c ON f.rowid = c.id
                            WHERE chunks_fts MATCH ? AND (? IS NULL OR c.doc_id = ?)
                            ORDER BY rank_score ASC LIMIT ?
                        """, (f"text : ({or_diff_query})", document_id, document_id, fetch_limit)).fetchall()
                        phrase_rows.extend(d_rows)
                    except Exception:
                        pass

                # 1. Prioritized exact phrase queries for extracted concepts
                for cp in concept_phrases:
                    clean_cp = re.sub(r'["\'\*\^\:\(\)\[\]\{\}\+\-\~]', ' ', cp).strip()
                    if clean_cp:
                        try:
                            p_rows = conn.execute("""
                                SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num,
                                       c.chunk_index, c.text, bm25(chunks_fts) as rank_score
                                FROM chunks_fts f JOIN chunks c ON f.rowid = c.id
                                WHERE chunks_fts MATCH ? AND (? IS NULL OR c.doc_id = ?)
                                ORDER BY rank_score ASC LIMIT ?
                            """, (f'text : ("{clean_cp}")', document_id, document_id, fetch_limit)).fetchall()
                            phrase_rows.extend(p_rows)
                        except Exception:
                            pass

                # 2. Ordered/conjunctive AND queries for multi-word concepts
                for cp in concept_phrases:
                    clean_cp = re.sub(r'["\'\*\^\:\(\)\[\]\{\}\+\-\~]', ' ', cp).strip()
                    cp_words = [w for w in re.findall(r"\w+", clean_cp.lower()) if w not in STOPWORDS]
                    if len(cp_words) >= 2:
                        and_clause = " AND ".join(f'"{w}"*' for w in cp_words)
                        try:
                            a_rows = conn.execute("""
                                SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num,
                                       c.chunk_index, c.text, bm25(chunks_fts) as rank_score
                                FROM chunks_fts f JOIN chunks c ON f.rowid = c.id
                                WHERE chunks_fts MATCH ? AND (? IS NULL OR c.doc_id = ?)
                                ORDER BY rank_score ASC LIMIT ?
                            """, (f"text : ({and_clause})", document_id, document_id, fetch_limit)).fetchall()
                            phrase_rows.extend(a_rows)
                        except Exception:
                            pass

                # 3. Number phrases
                phrase_query = self._number_phrases(query)
                if phrase_query:
                    try:
                        n_rows = conn.execute("""
                            SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num,
                                   c.chunk_index, c.text, bm25(chunks_fts) as rank_score
                            FROM chunks_fts f JOIN chunks c ON f.rowid = c.id
                            WHERE chunks_fts MATCH ? AND (? IS NULL OR c.doc_id = ?)
                            ORDER BY rank_score ASC LIMIT ?
                        """, (f"text : ({phrase_query})", document_id, document_id, fetch_limit)).fetchall()
                        phrase_rows.extend(n_rows)
                    except Exception:
                        pass

                # 4. Broader OR terms query
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

                    locally_attached, has_explanation, is_cat = check_local_definition_and_catalog(txt, concept_phrases)

                    # Demote TOC chunks across all queries heavily
                    if is_heading_or_toc_chunk(txt):
                        relevance -= 50.0

                    # Demote pure catalogue/playlist/syllabus chunks that do not explain the concept
                    if is_cat and not locally_attached:
                        relevance -= 35.0

                    if diff_operands:
                        txt_no_q = txt.replace(query, "")
                        cand_operands_outside = extract_differential_operands(txt_no_q)
                        matched_operands = [
                            q_op for q_op in diff_operands
                            if any(
                                c_op[0] == q_op[0]
                                and c_op[1] == q_op[1]
                                and c_op[2] == q_op[2]
                                and c_op[3] == q_op[3]
                                for c_op in cand_operands_outside
                            )
                        ]
                        if matched_operands:
                            relevance += 35.0
                            has_math_rel = bool(re.search(
                                r"(?:[><=≠≤≥]|tăng|giảm|dương|âm|đổi\s+dấu|kết\s+luận)",
                                txt_no_q,
                                re.I,
                            ))
                            if has_math_rel:
                                relevance += 15.0
                        else:
                            all_cand = extract_differential_operands(txt)
                            has_mismatching = any(
                                any(
                                    c_op[0].upper() == q_op[0].upper()
                                    and c_op[1].lower() == q_op[1].lower()
                                    and (c_op[0] != q_op[0] or c_op[1] != q_op[1] or c_op[2] != q_op[2] or c_op[3] != q_op[3])
                                    for c_op in all_cand
                                )
                                for q_op in diff_operands
                            )
                            if has_mismatching:
                                relevance -= 20.0
                            elif any(
                                any(
                                    c_op[0] == q_op[0]
                                    and c_op[1] == q_op[1]
                                    and c_op[2] == q_op[2]
                                    and c_op[3] == q_op[3]
                                    for c_op in all_cand
                                )
                                for q_op in diff_operands
                            ):
                                relevance -= 20.0

                    # Concept-tied scoring: prioritize exact concept hits and ordered/conjunctive candidates
                    has_exact_concept = False
                    if concept_phrases:
                        for cp in concept_phrases:
                            if cp.lower() in txt_lower:
                                has_exact_concept = True
                                break

                    if len(concept_phrases) >= 2:
                        matched_concepts = sum(1 for cp in concept_phrases if cp.lower() in txt_lower)
                        if matched_concepts >= 2:
                            relevance += 25.0
                        elif matched_concepts == 1:
                            relevance += 12.0
                    elif has_exact_concept:
                        relevance += 25.0

                    if not has_exact_concept and concept_phrases:
                        for cp in concept_phrases:
                            cp_words = [w for w in re.findall(r"\w+", cp.lower()) if w not in STOPWORDS]
                            if len(cp_words) >= 2 and all(w in txt_lower for w in cp_words):
                                relevance += 10.0
                                break

                    # Tie definition/support directly to locally attached explanation of the requested concept
                    if locally_attached:
                        if is_definition_q:
                            relevance += 25.0
                        else:
                            relevance += 15.0
                        if has_explanation:
                            relevance += 15.0
                    elif has_exact_concept and not is_cat:
                        # Chunk has concept and is body text, but definition cue is not strongly structured
                        if is_definition_q:
                            if any(d in txt_lower for d in ("định nghĩa", "khái niệm", "được định nghĩa là", "là giới hạn", "công thức", "definition")):
                                relevance += 8.0
                            if any(p in txt_norm for p in ("hai điều kiện", "tính chất", "thỏa", "f(u + v)", "f(αu)", "nguyên lý", "đặc điểm")):
                                relevance += 5.0

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
        use_native = False
        native_embed_models = {"bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge"}
        if sys.platform != "darwin" and (embed_model in native_embed_models or "mlx" in (embed_model or "")):
            return []

        installed_digest = None
        with self.get_connection() as conn:
            try:
                row = conn.execute("SELECT model_digest FROM corpus_vector_provenance WHERE full_text=1 LIMIT 1").fetchone()
                if row:
                    installed_digest = row[0]
            except sqlite3.Error:
                pass

        if installed_digest and installed_digest.startswith("ollama-sha256:"):
            use_native = False
        elif installed_digest and installed_digest.startswith("bge-m3-mlx-fp16@"):
            if sys.platform != "darwin":
                logger.warning("Installed native BGE knowledge pack requires macOS; dense retrieval disabled")
                return []
            use_native = True
        elif os.getenv("EMBED_BACKEND") == "ollama":
            use_native = False
        elif os.getenv("EMBED_BACKEND") in ("native", "mlx"):
            if sys.platform != "darwin":
                logger.warning("Native embedding requires macOS; dense retrieval disabled")
                return []
            use_native = True
        elif sys.platform == "darwin" and (embed_model is None or embed_model in native_embed_models or "mlx" in str(embed_model)):
            use_native = True
        else:
            use_native = False

        if use_native:
            from app.native_embedding import NativeEmbeddingClient
            if not NativeEmbeddingClient.available():
                return []

        from app.native_embedding import BGE_NATIVE_IDENTITY
        try:
            expected_identity = BGE_NATIVE_IDENTITY if use_native else getattr(
                self.ollama, "embedding_identity", lambda model: None)(embed_model)
        except Exception as exc:
            logger.warning("Embedding identity unavailable; dense retrieval disabled: %s", type(exc).__name__)
            return []
        if not expected_identity:
            return []

        with self.get_connection() as conn:
            try:
                compatible = conn.execute("""
                    SELECT 1 FROM chunks c JOIN corpus_vector_provenance p ON p.chunk_id=c.id
                    WHERE c.embedding IS NOT NULL AND p.model_digest=? AND p.full_text=1 LIMIT 1
                """, (expected_identity,)).fetchone()
            except sqlite3.Error:
                compatible = None
            if not compatible:
                return []

        try:
            if use_native:
                from app.native_embedding import NativeEmbeddingClient
                query_vec = NativeEmbeddingClient().get_embedding(query, expected_identity=expected_identity)
            else:
                from app.gpu_lock import gpu_coordinator
                with gpu_coordinator.acquire_for_inference():
                    query_vec = self.ollama.get_embedding(query, model=embed_model)
        except Exception as exc:
            logger.warning("Query embedding unavailable; using keyword retrieval: %s", type(exc).__name__)
            return []
        if query_vec is None:
            return []

        if not use_native:
            try:
                post_identity = getattr(self.ollama, "embedding_identity", lambda model: None)(embed_model)
            except Exception as exc:
                logger.warning("Post-query embedding identity check failed: %s", type(exc).__name__)
                return []
            if post_identity != expected_identity:
                return []

        q_arr = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q_arr)
        if q_norm == 0 or not np.all(np.isfinite(q_arr)):
            return []
        q_arr = q_arr / q_norm

        import hashlib
        results = []
        with self.get_connection() as conn:
            sql = """
                SELECT c.id, c.doc_id, c.book_title, c.filename, c.page_num, c.chunk_index, c.text, c.embedding,
                       p.text_sha256, p.model_digest, p.vector_sha256, p.dimensions, p.full_text
                FROM chunks c JOIN corpus_vector_provenance p ON p.chunk_id = c.id
                WHERE c.embedding IS NOT NULL AND p.model_digest = ? AND p.full_text = 1 AND p.dimensions = ?
            """
            params = [expected_identity, len(q_arr)]
            if document_id is not None:
                sql += " AND c.doc_id = ?"
                params.append(document_id)
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            
            scored_rows = []
            for r in rows:
                blob = r["embedding"]
                if not blob:
                    continue

                if r["model_digest"] != expected_identity:
                    continue
                if r["dimensions"] != len(q_arr):
                    continue
                if r["full_text"] != 1:
                    continue
                actual_text_sha = hashlib.sha256(r["text"].encode("utf-8")).hexdigest()
                if r["text_sha256"] != actual_text_sha:
                    continue
                actual_vec_sha = hashlib.sha256(blob).hexdigest()
                if r["vector_sha256"] != actual_vec_sha:
                    continue

                doc_vec = unpack_vector(blob)
                if doc_vec.shape != q_arr.shape:
                    continue
                if not np.all(np.isfinite(doc_vec)):
                    continue
                d_norm = float(np.linalg.norm(doc_vec))
                if d_norm <= 0 or not np.isfinite(d_norm):
                    continue
                if use_native and abs(d_norm - 1.0) > 1e-3:
                    continue
                sim = float(np.dot(q_arr, doc_vec / d_norm))
                scored_rows.append((sim, r))

            scored_rows.sort(key=lambda x: x[0], reverse=True)
            min_sim = 0.50 if use_native else 0.45
            for rank_idx, (sim, r) in enumerate(scored_rows):
                if sim < min_sim:
                    break
                if len(results) >= limit:
                    break
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

        query_concepts = extract_query_concepts(query_clean)
        query_diff_operands = extract_differential_operands(query_clean)

        def _content_relevance(text: str) -> float:
            score = 0.0
            if is_heading_or_toc_chunk(text):
                score -= 0.8

            if query_diff_operands:
                txt_no_q = text.replace(query_clean, "")
                cand_operands = extract_differential_operands(txt_no_q)
                has_match = any(
                    any(
                        c_op[0] == q_op[0]
                        and c_op[1] == q_op[1]
                        and c_op[2] == q_op[2]
                        and c_op[3] == q_op[3]
                        for c_op in cand_operands
                    )
                    for q_op in query_diff_operands
                )
                if has_match:
                    score += 0.5
                else:
                    all_cand = extract_differential_operands(text)
                    if any(
                        any(
                            c_op[0].upper() == q_op[0].upper()
                            and c_op[1].lower() == q_op[1].lower()
                            and (c_op[0] != q_op[0] or c_op[1] != q_op[1] or c_op[2] != q_op[2] or c_op[3] != q_op[3])
                            for c_op in all_cand
                        )
                        for q_op in query_diff_operands
                    ):
                        score -= 0.4
                    elif any(
                        any(
                            c_op[0] == q_op[0]
                            and c_op[1] == q_op[1]
                            and c_op[2] == q_op[2]
                            and c_op[3] == q_op[3]
                            for c_op in all_cand
                        )
                        for q_op in query_diff_operands
                    ):
                        score -= 0.4

            locally_attached, has_explanation, is_cat = check_local_definition_and_catalog(text, query_concepts)
            if locally_attached:
                score += 0.4
                if has_explanation:
                    score += 0.2
            elif is_cat:
                score -= 0.5
            else:
                t_low = text.lower()
                has_concept = any(cp.lower() in t_low for cp in query_concepts) if query_concepts else True
                if has_concept:
                    if any(k in t_low for k in ("định nghĩa", "khái niệm", "được định nghĩa là", "là giới hạn", "công thức", "tính chất", "nguyên lý")):
                        score += 0.15

            if len(text) > 100 and any(p in text for p in ('.', '!', '?', ';', '\n')):
                score += 0.1
            return score

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
                # Semantic-only candidate under partial native indexing: require genuine relevance
                sem_score = item.get("semantic_score", 0.0)
                content_rel = _content_relevance(item["text"])
                if sem_score < 0.52 or content_rel < 0:
                    continue
                chunk_map[cid] = item
            else:
                chunk_map[cid]["semantic_score"] = item["semantic_score"]
            rank = item["semantic_rank"]
            score = SEMANTIC_WEIGHT * (1.0 / (RRF_K + rank))
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + score

        # If only FTS was available, use FTS results with content relevance
        if not semantic_results and fts_results:
            combined = sorted(fts_results, key=lambda x: (x.get("fts_score", 0.0) + _content_relevance(x["text"]) * 10.0), reverse=True)
        else:
            sorted_cids = sorted(
                rrf_scores.keys(),
                key=lambda cid: (rrf_scores[cid] * (1.0 + max(-0.8, _content_relevance(chunk_map[cid]["text"])))),
                reverse=True,
            )
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

        # For a requested attribute, prefer passages that actually describe
        # that attribute. A subject repeated in a page header is insufficient.
        # Apply only when an explicit matching passage exists; synonyms-only
        # retrieval keeps the normal lexical/semantic path.
        attribute_groups = (
            (r'tính\s+chất|properties', r'tính\s+chất|properties'),
            (r'đặc\s+điểm|characteristics', r'đặc\s+điểm|characteristics'),
            (r'thành\s+phần|components', r'thành\s+phần|components'),
            (r'nguyên\s+tắc|principles', r'nguyên\s+tắc|principles'),
        )
        for query_pattern, source_pattern in attribute_groups:
            if re.search(query_pattern,query_clean,re.I):
                explicit = [item for item in combined if re.search(source_pattern,item['text'],re.I)
                            and not is_heading_or_toc_chunk(item['text'])]
                if explicit:
                    combined = explicit
                break

        # For broad queries, allow more chunks per book (up to 4) so multi-chapter concepts are retained
        book_limit = max_per_book if max_per_book is not None else (4 if is_broad else 2)

        diverse = []
        per_book = {}
        for item in combined:
            rel = _content_relevance(item["text"])
            if rel < -0.4:
                continue
            filename = item["filename"]
            if per_book.get(filename, 0) >= book_limit:
                continue
            diverse.append(item)
            per_book[filename] = per_book.get(filename, 0) + 1
            if len(diverse) >= top_k:
                break

        # If top_k not reached, fill with remaining relevant candidates from matching books
        # but NEVER force unrelated filler or negative-relevance items just to reach top_k.
        if len(diverse) < top_k:
            for item in combined:
                if item in diverse:
                    continue
                rel = _content_relevance(item["text"])
                if rel < -0.2:
                    continue
                has_fts = item.get("fts_score") is not None and item.get("fts_score", 0.0) > 0.0
                has_sem = item.get("semantic_score") is not None and item.get("semantic_score", 0.0) >= 0.52
                if not (has_fts or has_sem):
                    continue
                diverse.append(item)
                if len(diverse) >= top_k:
                    break

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

        self._attach_page_quality_metadata(final_results)
        return final_results
