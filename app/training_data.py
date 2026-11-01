"""Create local, source-grounded bilingual supervision for MLX LoRA training."""

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from html import escape as _html_escape
from pathlib import Path
from typing import Dict, Optional, Set, Tuple


def escape(text: str, entities: Optional[Dict[str, str]] = None) -> str:
    res = _html_escape(text, quote=False)
    for k, v in (entities or {}).items():
        res = res.replace(k, v)
    return res


from app.config import DB_PATH, DEFAULT_CHAT_MODEL, OLLAMA_BASE_URL, TRAINED_MODEL_NAME
from app.model_resolver import is_mlx_model
from app.text_cleaner import sanitize_for_prompt_context
from app.bilingual_terms import translation_guidance, translation_is_plausible
from app.answer_format import clean_answer

SYSTEM = (
    "Bạn là trợ lý học thuật đa lĩnh vực. Hãy phân tích và tổng hợp bằng tiếng Việt "
    "chỉ từ ngữ cảnh được cung cấp. Dẫn mã nguồn [S1], [S2] cho từng ý. "
    "Không chẩn đoán cá nhân, không bịa thông tin hoặc trích dẫn."
)
FORBIDDEN_SOURCES = {
    "CS202_Week6.pdf",
    "Đại số tuyến tính/Chuong 5_Cheo hoa ma tran.pdf",
    "OpenStax - Psychology.pdf",
    "sắp xếp.pdf",
}
NO_INFO = "Tài liệu hiện có trong thư viện không đề cập đủ thông tin để trả lời câu hỏi này."
VI_CHARS = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]")
HAN_CHARS = re.compile(r"[\u3400-\u9fff]")
SOURCE_RE = re.compile(r"\[S([12])\]", re.I)


def detect_language(text: str) -> str:
    """Classify an excerpt conservatively; OCR noise may still be ambiguous."""
    words = re.findall(r"[A-Za-z]+", text.lower())
    english_hits = sum(w in {"the", "and", "of", "to", "that", "with", "for", "is", "in"} for w in words)
    vietnamese_chars = len(VI_CHARS.findall(text))
    if english_hits >= 8 and vietnamese_chars <= len(words) * 0.2:
        return "en"
    if vietnamese_chars >= max(3, len(text) // 180):
        return "vi"
    return "en" if english_hits >= 8 else "vi"


def is_vietnamese_translation(text: str) -> bool:
    """Reject short or mostly non-Vietnamese translations from the teacher."""
    if len(text.strip()) <= 30:
        return False
    if len(HAN_CHARS.findall(text)) > max(3, len(text) // 20):
        return False
    return len(VI_CHARS.findall(text)) >= max(3, len(text) // 150)


def is_substantive_excerpt(text: str) -> bool:
    """Exclude obvious exercises and lists of chapter headings."""
    if len(re.findall(r"_{3,}", text)) >= 2:
        return False
    if text.count("\n") >= 8 and text.count(".") == 0 and len(text.split()) < 100:
        return False
    return True


def pair_language(pair: dict) -> str:
    english_count = sum(detect_language(source["text"]) == "en" for source in pair["sources"])
    return "en" if english_count == len(pair["sources"]) else ("mixed" if english_count else "vi")


def basic_record_quality(record: dict) -> bool:
    """Check source and translation quality without relying on another model."""
    sources = record["pair"]["sources"]
    if any(not is_substantive_excerpt(source["text"]) for source in sources):
        return False
    if record["language"] not in ("en", "mixed"):
        return True
    english_sources = [source for source in sources if detect_language(source["text"]) == "en"]
    translations = record.get("translations", {})
    return bool(english_sources) and all(
        is_vietnamese_translation(str(translations.get(source["id"], "")))
        and translation_is_plausible(source["text"], str(translations.get(source["id"], "")))
        and len(str(translations[source["id"]]).strip()) >= 0.45 * len(source["text"].strip())
        for source in english_sources
    )


def held_out_book_names(records: list[dict]) -> set[str]:
    """Choose validation books consistently before generating cross-book pairs."""
    single_book_records = [record for record in records if not record["pair"].get("cross_book")]
    held_out = {
        record["pair"]["filename"] for record in single_book_records
        if int(hashlib.sha256(record["pair"]["filename"].encode()).hexdigest(), 16) % 10 == 0
        or (record.get("language") == "en" and int(hashlib.sha256(record["pair"]["filename"].encode()).hexdigest(), 16) % 7 == 0)
    }
    if not held_out and len({record["pair"]["filename"] for record in single_book_records}) > 1:
        held_out.add(single_book_records[-1]["pair"]["filename"])
    return held_out


def is_source_forbidden(filename: str) -> bool:
    fn_norm = unicodedata.normalize("NFC", filename)
    for forbidden in FORBIDDEN_SOURCES:
        forb_norm = unicodedata.normalize("NFC", forbidden)
        if forb_norm == fn_norm or Path(forb_norm).name == Path(fn_norm).name:
            return True
        if forb_norm in fn_norm or fn_norm.endswith(forb_norm):
            return True
    return False


def is_chunk_allowed_in_training(
    doc: sqlite3.Row,
    page_num: int,
    chunk_text: str,
    ocr_entries_by_page: Optional[Dict[int, dict]] = None,
    excluded_pages: Optional[Set[Tuple[str, int]]] = None,
) -> bool:
    """Strict producer training gate:
    - Never allow holdout or forbidden sources.
    - If page is genuine native (PDF page contains native text >= 30 chars), allow without OCR.
    - If page is image-only/scanned, require verified OCR transcript matching chunk text.
    """
    if is_source_forbidden(doc["filename"]):
        return False

    is_doc_scanned = bool(doc["is_scanned"]) if "is_scanned" in doc.keys() else False
    file_hash = doc["file_hash"] if "file_hash" in doc.keys() else ""

    src_path = None
    if "filepath" in doc.keys() and doc["filepath"]:
        p = Path(doc["filepath"])
        if p.is_file():
            src_path = p
    if not src_path:
        from app.config import SRC_DIR
        candidate = SRC_DIR / doc["filename"]
        if candidate.is_file():
            src_path = candidate

    if not src_path or not src_path.is_file():
        return False

    if not isinstance(file_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", file_hash) or not chunk_text.strip():
        return False
    from app.pdf_extractor import PDFExtractor
    current_hash = PDFExtractor.calculate_file_hash(src_path)
    if current_hash != file_hash:
        return False

    # Block any chunk from an excluded page from entering training supervision
    try:
        fn = doc["filename"] if ("filename" in doc.keys() if hasattr(doc, "keys") else hasattr(doc, "__getitem__")) else getattr(doc, "filename", "")
        if excluded_pages is not None:
            if (fn, page_num) in excluded_pages:
                return False
        else:
            from app.corpus_scope import is_page_excluded
            if is_page_excluded(fn, page_num):
                return False
    except Exception:
        # Fail closed on corrupt scope policy, invalid manifest, or broken exclusion validation
        return False

    # Determine if this specific page is genuine native (has actual text layer)
    is_page_native = False
    if not is_doc_scanned:
        from app.text_cleaner import normalize_vietnamese_text
        ext = src_path.suffix.lower()
        if ext == ".pdf":
            try:
                import pymupdf
                with pymupdf.open(src_path) as pdf:
                    if 0 <= page_num - 1 < len(pdf):
                        page = pdf[page_num - 1]
                        native_text = page.get_text().strip()
                        if len(native_text) >= 30:
                            norm_c = re.sub(r"\s+", " ", chunk_text.strip())
                            norm_n = re.sub(r"\s+", " ", native_text)
                            norm_clean_n = re.sub(r"\s+", " ", normalize_vietnamese_text(native_text))
                            if norm_c == norm_n or norm_c in norm_n or norm_c == norm_clean_n or norm_c in norm_clean_n:
                                is_page_native = True
            except Exception:
                pass
        elif ext in (".docx", ".pptx", ".txt", ".md", ".markdown"):
            try:
                res = PDFExtractor.extract_file(src_path, ocr=False)
                for item in res.extracted_pages:
                    if item.get("page_num") == page_num:
                        native_text = item.get("text", "").strip()
                        if len(native_text) >= 30:
                            norm_c = re.sub(r"\s+", " ", chunk_text.strip())
                            norm_n = re.sub(r"\s+", " ", native_text)
                            norm_clean_n = re.sub(r"\s+", " ", normalize_vietnamese_text(native_text))
                            if norm_c == norm_n or norm_c in norm_n or norm_c == norm_clean_n or norm_c in norm_clean_n:
                                is_page_native = True
                        break
            except Exception:
                pass

    if is_page_native:
        return True

    # Scanned/image-only page requires verified OCR transcript matching chunk text
    if ocr_entries_by_page is None:
        from app.vision_ocr import CACHE_DIR
        ocr_entries_by_page = {}
        for cp in CACHE_DIR.glob("*.json"):
            try:
                entry = json.loads(cp.read_text(encoding="utf-8"))
                if entry.get("source_hash") == file_hash:
                    p = entry.get("page_num")
                    if p is not None:
                        ocr_entries_by_page.setdefault(int(p), []).append(entry)
            except Exception:
                continue

    from app.vision_ocr import VisionOCRManager
    entries = ocr_entries_by_page.get(page_num, [])
    if isinstance(entries, dict):
        entries = [entries]  # Retain callers that supply one explicit record.
    norm_c = re.sub(r"\s+", " ", chunk_text.strip())
    for p_entry in entries:
        if not isinstance(p_entry, dict) or p_entry.get("source_hash") != file_hash or p_entry.get("page_num") != page_num:
            continue
        if not VisionOCRManager.is_allowed_in_training(p_entry)[0]:
            continue
        norm_o = re.sub(r"\s+", " ", p_entry.get("text", "").strip())
        if norm_o and (norm_c == norm_o or norm_c in norm_o):
            return True
    return False


def select_pairs(db_path: Path, pairs_per_book: int = 1):
    """Sample adjacent, substantive passages across each book's middle pages."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    doc_cols = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    id_col = "id" if "id" in doc_cols else "rowid AS id"
    title_col = "clean_title" if "clean_title" in doc_cols else "filename AS clean_title"
    scanned_col = "is_scanned" if "is_scanned" in doc_cols else "0 AS is_scanned"
    hash_col = "file_hash" if "file_hash" in doc_cols else "'' AS file_hash"
    path_col = "filepath" if "filepath" in doc_cols else "'' AS filepath"
    docs = conn.execute(f"SELECT {id_col}, filename, {title_col}, {scanned_col}, {hash_col}, {path_col} FROM documents WHERE status IN ('indexed','ocr_partial_reviewed') ORDER BY filename").fetchall()
    for doc in docs:
        if is_source_forbidden(doc["filename"]):
            continue

        # Strict producer training gate: OCR transcripts must be verified per page before entering training
        from app.vision_ocr import CACHE_DIR
        doc_ocr_entries = {}
        for cp in CACHE_DIR.glob("*.json"):
            try:
                entry = json.loads(cp.read_text(encoding="utf-8"))
                # Match strictly on exact source_hash == file_hash
                if entry.get("source_hash") == doc["file_hash"]:
                    p_num = entry.get("page_num")
                    if p_num is not None:
                        doc_ocr_entries.setdefault(int(p_num), []).append(entry)
            except Exception:
                continue

        if doc["is_scanned"]:
            from app.vision_ocr import VisionOCRManager
            if not any(VisionOCRManager.is_allowed_in_training(e)[0] for entries in doc_ocr_entries.values() for e in entries):
                continue

        rows = conn.execute(
            "SELECT page_num, chunk_index, text FROM chunks WHERE doc_id=? AND length(text)>=320 ORDER BY page_num, chunk_index",
            (doc["id"],),
        ).fetchall()
        if len(rows) < 4:
            continue
        for sample_index in range(pairs_per_book):
            fraction = (sample_index + 1) / (pairs_per_book + 1)
            index = min(len(rows) - 2, max(1, round(fraction * (len(rows) - 2))))
            selected = rows[index:index + 2]
            if selected[1]["page_num"] - selected[0]["page_num"] > 2:
                continue
            if not all(is_chunk_allowed_in_training(doc, int(r["page_num"]), r["text"], doc_ocr_entries) for r in selected):
                continue
            if any(not is_substantive_excerpt(row["text"]) for row in selected):
                continue
            if any(len(re.findall(r"[−∙Ñ¤]", row["text"])) > 2 for row in selected):
                continue
            if any(re.search(r"\b(references|bibliography|tài liệu tham khảo)\b", row["text"], re.I) for row in selected):
                continue
            yield {
                "filename": doc["filename"],
                "title": doc["clean_title"],
                "pair_index": sample_index,
                "sources": [
                    {
                        "id": f"S{number}",
                        "page": row["page_num"],
                        "text": sanitize_for_prompt_context(row["text"][:650]),
                    }
                    for number, row in enumerate(selected, 1)
                ],
            }
    conn.close()


def select_cross_book_pairs(db_path: Path, exclude_files: set[str] | None = None):
    """Pair an English and a Vietnamese passage about the same topic."""
    exclude_files = exclude_files or set()
    themes = [
        ("sang chấn", "trauma", "sang chấn", "The Body Keeps", "sang_chan"),
        ("trí nhớ", "memory", "ký ức", "E. Bruce Goldstein", "sang_chan"),
        ("thói quen", "habit", "thói quen", "E. Bruce Goldstein", "5446-"),
        ("cảm xúc", "emotion", "cảm xúc", "Gregory J. Feist", "phi_ly_tri"),
        ("nhân cách", "personality", "nhân cách", "Gregory J. Feist", "Toàn Thư"),
        ("quyết định", "decision", "quyết định", "E. Bruce Goldstein", "5600-"),
        ("chú ý", "attention", "chú ý", "E. Bruce Goldstein", "5600-"),
        ("gắn bó", "attachment", "gắn bó", "Sandi Mann", "sang_chan"),
    ]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    doc_cols = {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    id_col = "id" if "id" in doc_cols else "rowid AS id"
    title_col = "clean_title" if "clean_title" in doc_cols else "filename AS clean_title"
    scanned_col = "is_scanned" if "is_scanned" in doc_cols else "0 AS is_scanned"
    hash_col = "file_hash" if "file_hash" in doc_cols else "'' AS file_hash"
    path_col = "filepath" if "filepath" in doc_cols else "'' AS filepath"
    doc_map = {row["filename"]: row for row in conn.execute(f"SELECT {id_col}, filename, {title_col}, {scanned_col}, {hash_col}, {path_col} FROM documents").fetchall()}
    document_languages = {}
    for doc in conn.execute("SELECT filename FROM documents WHERE status='indexed'"):
        samples = conn.execute(
            "SELECT text FROM chunks WHERE filename=? AND page_num>10 ORDER BY id LIMIT 20",
            (doc["filename"],),
        ).fetchall()
        english_votes = sum(detect_language(row["text"][:650]) == "en" for row in samples)
        document_languages[doc["filename"]] = "en" if samples and english_votes >= len(samples) * 0.6 else "vi"
    for topic, english_term, vietnamese_term, english_preferred, vietnamese_preferred in themes:
        sources = []
        for term, language, preferred in ((english_term, "en", english_preferred), (vietnamese_term, "vi", vietnamese_preferred)):
            rows = conn.execute(
                "SELECT filename, book_title, page_num, text FROM chunks "
                "WHERE length(text)>=400 AND page_num>10 AND lower(text) LIKE ? ORDER BY filename,page_num",
                (f"%{term}%",),
            ).fetchall()
            grouped = {}
            for row in rows:
                if row["filename"] in exclude_files or is_source_forbidden(row["filename"]):
                    continue
                doc = doc_map.get(row["filename"])
                if doc and not is_chunk_allowed_in_training(doc, int(row["page_num"]), row["text"]):
                    continue
                if not is_substantive_excerpt(row["text"]):
                    continue
                if detect_language(row["text"][:650]) != language:
                    continue
                if document_languages.get(row["filename"]) != language:
                    continue
                if len(re.findall(r"[−∙Ñ¤]", row["text"])) > 2:
                    continue
                grouped.setdefault(row["filename"], []).append(row)
            if not grouped:
                break
            filenames = sorted(
                grouped,
                key=lambda name: (preferred.casefold() not in name.casefold(), -len(grouped[name]), name),
            )
            filename = filenames[0]
            options = grouped[filename]
            row = options[len(options) // 2]
            sources.append({
                "id": f"S{len(sources) + 1}",
                "book": row["book_title"],
                "filename": row["filename"],
                "page": row["page_num"],
                "text": sanitize_for_prompt_context(row["text"][:650]),
            })
        if len(sources) == 2 and sources[0]["filename"] != sources[1]["filename"]:
            yield {
                "filename": f'cross:{topic}:{sources[0]["filename"]}:{sources[1]["filename"]}',
                "title": topic,
                "pair_index": 0,
                "cross_book": True,
                "sources": sources,
            }
    conn.close()


def format_context(pair: dict, translations: dict | None = None) -> str:
    parts = []
    for source in pair["sources"]:
        source_id = source["id"]
        translated = (translations or {}).get(source_id, "")
        addition = f"\nBản dịch tiếng Việt: {translated}" if translated else ""
        book = escape(source.get("book", source.get("book_title", pair.get("title") or pair.get("book_title", ""))), {'"': "&quot;"})
        page_val = source.get("page", source.get("page_num", 1))
        parts.append(
            f'<document id="{source_id}" book="{book}" page="{page_val}">\n'
            f'{source["text"]}{addition}\n</document>'
        )
    return "<context>\n" + "\n".join(parts) + "\n</context>"


def translate_excerpt(text: str, model: str = DEFAULT_CHAT_MODEL, *, retry: bool = False) -> str:
    """Translate through the shared text base, or an explicitly chosen Ollama model."""
    messages = [{"role": "system", "content":
        "Translate into Vietnamese faithfully. Preserve subjects, conditions, quantities and uncertainty. "
        "Do not summarize, invent facts, or complete cut-off sentences. Return only the translation."},
        {"role": "user", "content":
         ("The previous translation was incomplete or in the wrong language. " if retry else "")
         + "Translate ALL into Vietnamese. " + translation_guidance(text) + "\n\n" + text}]
    import sys
    if sys.platform == "darwin":
        if not is_mlx_model(model):
            raise RuntimeError(f"Mô hình {model} không được hỗ trợ trên macOS (chỉ hỗ trợ MLX)")
        from app.trained_client import TrainedModelClient
        client = TrainedModelClient(model_name=model)
        if not client.available():
            raise RuntimeError(f"Mô hình {model} dùng chung chưa sẵn sàng cho dịch văn bản")
        content = client.chat_complete(messages, temperature=0, seed=0, max_tokens=1100).strip()
    elif is_mlx_model(model):
        from app.trained_client import TrainedModelClient
        client = TrainedModelClient(model_name=model)
        if not client.available():
            raise RuntimeError(f"Mô hình {model} dùng chung chưa sẵn sàng cho dịch văn bản")
        content = client.chat_complete(messages, temperature=0, seed=0, max_tokens=1100).strip()
    else:
        from app.ollama_client import OllamaClient
        client = OllamaClient()
        client._verify_model_locality(model)
        resp = client.chat_sync(
            messages=messages,
            model=model,
            temperature=0,
            num_predict=1100,
            num_ctx=8192,
        )
        content = resp.get("message", {}).get("content", "")
        if not isinstance(content, str):
            raise ValueError("Mô hình không trả bản dịch dạng chuỗi")
        content = content.strip()
    if content.startswith("{"):
        data = json.loads(content)
        if not isinstance(data.get("translation"), str):
            raise ValueError("Mô hình không trả bản dịch dạng chuỗi")
        return data["translation"].strip()
    return content


def ask_teacher(pair: dict, model: str = DEFAULT_CHAT_MODEL, translation_model: str = DEFAULT_CHAT_MODEL,
                reasoning_focus: str = "") -> dict:
    english_ids = [source["id"] for source in pair["sources"] if detect_language(source["text"]) == "en"]
    language = pair_language(pair)
    prompt = (
        f"{format_context(pair)}\n\n"
        "Tạo đúng một câu hỏi suy luận từ cả S1 và S2, một câu trả lời 70-150 từ bằng tiếng Việt. "
        "Giải thích kết luận có căn cứ, giới hạn, dẫn cả [S1] [S2]. "
        "Câu trả lời phải có cả mã [S1] và [S2] gắn cạnh các ý tương ứng. "
        'Chỉ trả JSON {"items":[{"question":"...","answer":"..."}]}.'
    )
    if reasoning_focus:
        prompt += "\nTrọng tâm câu hỏi: " + reasoning_focus + (
            ". Chỉ nêu kết luận có đủ tiền đề trong hai đoạn; nếu thiếu thì nói rõ giới hạn. "
            "Không bịa nghiên cứu, cơ chế, số liệu hoặc tên tác giả."
        )
    from app.trained_client import TrainedModelClient
    import sys
    if sys.platform == "darwin":
        if not is_mlx_model(model):
            raise RuntimeError(f"Mô hình {model} không được hỗ trợ trên macOS (chỉ hỗ trợ MLX)")
        if not TrainedModelClient.available(model_name=model):
            raise RuntimeError(f"Mô hình {model} dùng chung chưa sẵn sàng")
        client = TrainedModelClient(model_name=model)
        stream = client.chat_stream([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ], max_tokens=900)
        resp_text = "".join(stream).strip()
        m = re.search(r"\{.*\}", resp_text, re.DOTALL)
        if not m:
            raise ValueError(f"Không nhận được JSON hợp lệ từ teacher model: {resp_text}")
        data = json.loads(m.group(0))
    elif is_mlx_model(model):
        if not TrainedModelClient.available(model_name=model):
            raise RuntimeError(f"Mô hình {model} dùng chung chưa sẵn sàng")
        client = TrainedModelClient(model_name=model)
        stream = client.chat_stream([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ], max_tokens=900)
        resp_text = "".join(stream).strip()
        m = re.search(r"\{.*\}", resp_text, re.DOTALL)
        if not m:
            raise ValueError(f"Không nhận được JSON hợp lệ từ teacher model: {resp_text}")
        data = json.loads(m.group(0))
    else:
        from app.ollama_client import OllamaClient
        client = OllamaClient()
        client._verify_model_locality(model)
        resp = client.chat_sync(
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            model=model,
            temperature=0.2,
            num_predict=900,
            num_ctx=8192,
        )
        content_text = resp.get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", content_text, re.DOTALL)
        if not m:
            raise ValueError(f"Không nhận được JSON hợp lệ từ teacher model: {content_text}")
        data = json.loads(m.group(0))
    nested = data.get("translations", {})
    if not isinstance(nested, dict):
        nested = {}
    items = data.get("items")
    if not isinstance(items, list):
        items = nested.get("items", [])
    if not isinstance(items, list):
        items = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("answer"), str):
            item["answer"] = re.sub(
                r"\[\s*S1\s*[,;]\s*S2\s*\]", "[S1] [S2]", item["answer"], flags=re.I
            )
    valid = [
        item for item in items
        if isinstance(item, dict)
        and len(str(item.get("question", ""))) >= 15
        and len(str(item.get("answer", ""))) >= 80
        and set(SOURCE_RE.findall(item["answer"])) == {"1", "2"}
    ]
    if not valid:
        raise ValueError("Không có ví dụ phân tích được gắn cả hai mã nguồn")
    translations = {}
    for source in pair["sources"]:
        if source["id"] in english_ids:
            translations[source["id"]] = translate_excerpt(source["text"], translation_model)
            if not is_vietnamese_translation(translations[source["id"]]) or len(translations[source["id"]].strip()) < 0.45 * len(source["text"].strip()):
                translations[source["id"]] = translate_excerpt(source["text"], translation_model, retry=True)
    if english_ids and not all(
        is_vietnamese_translation(str(translations.get(source_id, "")))
        and len(str(translations[source_id]).strip()) >= 0.45 * len(next(s["text"] for s in pair["sources"] if s["id"] == source_id).strip())
        for source_id in english_ids
    ):
        failures = {source_id: translations.get(source_id, "") for source_id in english_ids}
        raise ValueError("Bản dịch tiếng Việt thiếu hoặc sai ngôn ngữ: " + json.dumps(failures, ensure_ascii=False))
    return {"language": language, "translations": translations, "items": valid[:1]}


def _rough_question_variant(question: str) -> str:
    """A meaning-preserving noisy query, added to training only."""
    text = unicodedata.normalize("NFD", question.casefold())
    text = "".join(character for character in text if unicodedata.category(character) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[?!.:,;]", " ", text)).strip()


def write_training_files(records: list[dict], output_dir: Path, model: str,
                         *, plain_answer: bool = False, augment_prompts: bool = False,
                         held_out_override: set[str] | None = None) -> dict:
    """Build MLX chat JSONL from audited manifest records."""
    output_dir.mkdir(parents=True, exist_ok=True)
    train = []
    valid = []
    held_out_books = held_out_override if held_out_override is not None else held_out_book_names(records)
    skipped_cross_book_pairs = 0
    for record in records:
        pair = record["pair"]
        if pair.get("cross_book") and any(
            source.get("filename") in held_out_books for source in pair["sources"]
        ):
            skipped_cross_book_pairs += 1
            continue
        context = format_context(pair, record.get("translations"))
        held_out = pair["filename"] in held_out_books
        bucket = valid if held_out else train
        if record.get("language") in ("en", "mixed"):
            for source in pair["sources"]:
                if detect_language(source["text"]) != "en":
                    continue
                translation = record.get("translations", {}).get(source["id"], "")
                if len(translation) > 30:
                    bucket.append({"messages": [
                        {"role": "system", "content": "Dịch chính xác đoạn tiếng Anh về tâm lý học sang tiếng Việt, không thêm ý."},
                        {"role": "user", "content": source["text"]},
                        {"role": "assistant", "content": translation},
                    ]})
        for item in record["items"]:
            system = SYSTEM if not plain_answer else (
                "Bạn là trợ lý tâm lý học. Tổng hợp và liên kết các đoạn được cung cấp, "
                "giải thích có căn cứ bằng tiếng Việt tự nhiên. Chỉ xuất câu trả lời hoàn chỉnh; "
                "không in suy nghĩ nội bộ, mã nguồn hoặc danh sách tài liệu."
            )
            answer = clean_answer(item["answer"]) if plain_answer else item["answer"]
            sample = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f'{context}\n\nCâu hỏi: {item["question"]}\nHãy trả lời phân tích bằng tiếng Việt.' if plain_answer else f'{context}\n\nCâu hỏi: {item["question"]}\nHãy trả lời phân tích bằng tiếng Việt, dẫn mã nguồn.'},
                    {"role": "assistant", "content": answer},
                ]
            }
            bucket.append(sample)
            if augment_prompts and bucket is train:
                variant = _rough_question_variant(item["question"])
                if variant and variant != item["question"].casefold():
                    noisy = {"messages": [dict(message) for message in sample["messages"]]}
                    noisy["messages"][1]["content"] = f"{context}\n\nCâu hỏi người dùng (có thể thiếu dấu và dấu câu): {variant}\nHãy hiểu ý định và trả lời phân tích bằng tiếng Việt."
                    bucket.append(noisy)
    for name, samples in (("train", train), ("valid", valid)):
        with (output_dir / f"{name}.jsonl").open("w", encoding="utf-8") as file:
            for sample in samples:
                file.write(json.dumps(sample, ensure_ascii=False) + "\n")
    summary = {
        "books": len({r["pair"]["filename"] for r in records if not r["pair"].get("cross_book")}),
        "english_books": len({r["pair"]["filename"] for r in records if r["language"] == "en" and not r["pair"].get("cross_book")}),
        "cross_book_pairs": sum(bool(r["pair"].get("cross_book")) for r in records),
        "skipped_cross_book_pairs_for_holdout": skipped_cross_book_pairs,
        "train_cross_book_pairs": sum(bool(r["pair"].get("cross_book")) for r in records) - skipped_cross_book_pairs,
        "held_out_books": sorted(held_out_books),
        "train_examples": len(train),
        "valid_examples": len(valid),
        "teacher_model": model,
        "teacher_models_in_manifest": sorted({r.get("teacher_model", model) for r in records}),
        "plain_answer": plain_answer,
        "augmented_prompt_examples": augment_prompts,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def prepare(db_path: Path, output_dir: Path, pairs_per_book: int = 1, limit_books: int = 0, model: str = DEFAULT_CHAT_MODEL, only_files: list[str] | None = None, cross_book: bool = False):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.jsonl"
    existing = []
    if manifest_path.exists():
        existing = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line]
    reusable_records = [entry for entry in existing if basic_record_quality(entry)]
    seen = {entry["key"] for entry in reusable_records}
    def source_signature(pair: dict) -> str:
        material = [
            (source.get("filename", pair["filename"]), source["page"], source["text"])
            for source in pair["sources"]
        ]
        return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    seen_sources = {source_signature(entry["pair"]) for entry in reusable_records}
    selected = list(select_pairs(db_path, pairs_per_book=pairs_per_book))
    if only_files:
        selected = [pair for pair in selected if any(fragment.casefold() in pair["filename"].casefold() for fragment in only_files)]
    if limit_books:
        allowed = set(list(dict.fromkeys(pair["filename"] for pair in selected))[:limit_books])
        selected = [pair for pair in selected if pair["filename"] in allowed]
    if cross_book:
        planned_records = existing + [{"pair": pair, "language": pair_language(pair)} for pair in selected]
        held_out_books = held_out_book_names(planned_records)
        selected.extend(select_cross_book_pairs(db_path, exclude_files=held_out_books))
    for number, pair in enumerate(selected, 1):
        digest = hashlib.sha256(json.dumps(pair, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        key = f'{pair["filename"]}:{pair["pair_index"]}:{digest}'
        source_key = source_signature(pair)
        if key in seen or source_key in seen_sources:
            continue
        try:
            for attempt in range(2):
                try:
                    generated = ask_teacher(pair, model=model)
                    break
                except (Exception, ValueError, KeyError, json.JSONDecodeError):
                    if attempt:
                        raise
            record = {"key": key, "pair": pair, "teacher_model": model, "translation_model": DEFAULT_CHAT_MODEL, **generated}
            with manifest_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.append(record)
            seen.add(key)
            seen_sources.add(source_key)
            print(f'[{number}/{len(selected)}] {pair["filename"][:42]}: {len(generated["items"])} ví dụ ({generated["language"]})', flush=True)
        except (Exception, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f'[{number}/{len(selected)}] Bỏ qua {pair["filename"][:42]}: {error}', flush=True)

    return write_training_files([record for record in existing if basic_record_quality(record)], output_dir, model)


def main():
    parser = argparse.ArgumentParser(description="Tạo bộ ví dụ song ngữ cục bộ từ thư viện PDF")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=Path("data/training/v1"))
    parser.add_argument("--pairs-per-book", type=int, default=1)
    parser.add_argument("--limit-books", type=int, default=0)
    parser.add_argument("--teacher-model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--only-file", action="append", default=[], help="Chỉ tạo thêm ví dụ từ tên file khớp chuỗi này; có thể lặp lại")
    parser.add_argument("--cross-book", action="store_true", help="Tạo thêm ví dụ tổng hợp giữa sách tiếng Anh và tiếng Việt")
    args = parser.parse_args()
    print(json.dumps(prepare(args.db, args.output, args.pairs_per_book, args.limit_books, args.teacher_model, args.only_file, args.cross_book), ensure_ascii=False))


if __name__ == "__main__":
    main()
