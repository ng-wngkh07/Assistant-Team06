"""Continuous Knowledge Curation & Auditing Pipeline (Dataset v7+).
Uses the configured current local teacher and calibrated local judge,
coordinated through GPU lock. Tracks pending, accepted, rejected, and cached hashes.
Includes judge calibration (positive, numeric error, terminology error, unsupported claims,
string-false parsing, ungrounded extrapolations, and factual word-substitutions).
Enforces fail-closed release gates (>= 2048 train, >= 200 valid, >= 400 groups, 0 holdouts,
0 duplicate targets, 0 duplicate assistant answers, verified DB source & target proof bindings,
and immutable staging).
Provides worker process locking in try/finally, batch-boundary pause, and plan-hash verification.
"""

import argparse
import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple, Optional


from app.config import BASE_DIR, DB_PATH, OLLAMA_BASE_URL
from app.text_cleaner import sanitize_for_prompt_context
from app.training_data import basic_record_quality, detect_language, is_substantive_excerpt
from app.synthetic_curriculum import build_synthetic_curriculum
from app.token_auditor import audit_context_truncation
from app.gpu_lock import gpu_coordinator

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TRAIN = (
    "Bạn là trợ lý tâm lý học. Tổng hợp và liên kết các đoạn được cung cấp, "
    "giải thích có căn cứ bằng tiếng Việt tự nhiên. Chỉ xuất câu trả lời hoàn chỉnh; "
    "không in suy nghĩ nội bộ, mã nguồn hoặc danh sách tài liệu."
)

FROZEN_HOLDOUT_BOOKS = {
    "1241-thien-tai-ben-trai-ke-dien-ben-phai-thuviensach.vn.pdf",
    "C. G. Jung (1958) - The Undiscovered Self.pdf",
    "David G. Myers & C. Nathan DeWall (2015, 2013, 2010, 2007) - Psychology (eleventh edition).pdf",
    "OpenStax - Psychology.pdf",
    "dac_nhan_tam.pdf"
}

FACET_ROTATION = [
    "overview",
    "comparative_analysis",
    "causal_inference",
    "conditional_reasoning"
]

TEACHER_PROMPT_TEMPLATE = (
    "<context>\n<document id=\"S1\" book=\"{book}\" page=\"{page}\">\n{text}\n</document>\n</context>\n\n"
    "Đóng vai trò chuyên gia tâm lý học, dựa CHẶT CHẼ vào đoạn trích [S1] trên:\n"
    "1. Đặt 1 câu hỏi học thuật phân tích sâu nội dung đoạn trích bằng TIẾNG VIỆT (khía cạnh: {facet}).\n"
    "2. Viết câu trả lời hoàn chỉnh với độ dài tự nhiên phù hợp lượng thông tin thật (khoảng 60-250 từ) bằng TIẾNG VIỆT tự nhiên, giải thích có căn cứ và BẮT BUỘC dẫn mã câu trích [S1] bên cạnh các dữ kiện lấy từ sách.\n"
    "3. Trích xuất các luận điểm chính (claims) tương ứng từng câu/mệnh đề trong câu trả lời bằng TIẾNG VIỆT mà câu trả lời khẳng định dựa trên nguồn.\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "- Dù tài liệu gốc [S1] là tiếng Anh hay tiếng Việt, CẢ CÂU HỎI VÀ CÂU TRẢ LỜI ĐỀU PHẢI VIẾT HOÀN TOÀN BẰNG TIẾNG VIỆT CHUẨN XÁC.\n"
    "- Chọn khía cạnh phù hợp với nội dung đoạn trích ({facet}). Nếu nguồn thiếu tiền đề nhân quả hay điều kiện, hãy thích ứng sang khía cạnh mô tả, định nghĩa, tổng quan hoặc nêu rõ tiền đề và giới hạn; tuyệt đối không ép buộc ngoại suy nhân quả.\n"
    "- Suy luận hữu ích phải có tiền đề, giả thiết và giới hạn rõ ràng; không bao giờ trình bày kiến thức ngoài nguồn như là sự thật được khẳng định trong nguồn.\n"
    "- Dịch thuật ngữ chính xác (ví dụ: 'biopsychosocial' là 'sinh-tâm-xã-hội', KHÔNG viết 'sinh-tâm-xã-học'; 'attachment' là 'gắn bó'; 'mindset' là 'tư duy/tâm thế').\n"
    "- Tuyệt đối không tự suy diễn thêm nguyên nhân, quan hệ hay cơ chế tâm lý ngoài nguồn. Nếu nguồn thiếu tiền đề, phải kiềm chế (abstain) hoặc nêu rõ giới hạn.\n\n"
    "Trả về định dạng JSON duy nhất:\n"
    "{{\n"
    "  \"question\": \"...\",\n"
    "  \"answer\": \"...\",\n"
    "  \"claims\": [\"luận điểm 1\", \"luận điểm 2\"]\n"
    "}}"
)


JUDGE_PROMPT_TEMPLATE = (
    "Bạn là Thẩm định viên Khoa học khắt khe tuyệt đối (Strict Fact-Checking Judge). "
    "Nhiệm vụ của bạn là kiểm tra xem CÂU TRẢ LỜI CẦN THẨM ĐỊNH có hoàn toàn được chứng minh 100% bởi trích đoạn [S1] hay không.\n\n"
    "<context>\n<document id=\"S1\" book=\"{book}\" page=\"{page}\">\n{text}\n</document>\n</context>\n\n"
    "CÂU HỎI:\n{question}\n\n"
    "KHÍA CẠNH: {facet}\n\n"
    "CÁC LUẬN ĐIỂM CẦN KIỂM ĐỊNH:\n{claims_json}\n\n"
    "CÂU TRẢ LỜI CẦN THẨM ĐỊNH:\n{answer}\n\n"
    "QUY TẮC THẨM ĐỊNH BẮT BUỘC:\n"
    "1. KIỂM TRA TỪNG CÂU VÀ TỪNG Ý: Bạn phải đối chiếu từng câu, từng mệnh đề trong CÂU TRẢ LỜI CẦN THẨM ĐỊNH với nội dung văn bản [S1].\n"
    "2. TUYỆT ĐỐI KHÔNG CHẤP NHẬN NGOẠI SUY VƯỢT QUÁ NGUỒN: Nếu câu trả lời chứa BẤT KỲ ý nào, nguyên nhân nào, thái độ nào, quan hệ gia đình nào (ví dụ: người chồng không hỗ trợ), hoặc viện dẫn lý thuyết tâm lý học nào KHÔNG CÓ trong [S1], bạn BẮT BUỘC phải đặt supported=false, answer_grounded=false, và liệt kê các ý ngoại suy vào unsupported_claims.\n"
    "3. ĐÚNG NGUYÊN VĂN DỮ KIỆN VÀ HÀNH ĐỘNG: Nếu văn bản gốc [S1] ghi một chi tiết cụ thể (ví dụ: 'bỏ rét') nhưng câu trả lời đổi thành chi tiết khác (ví dụ: 'bỏ đói'), hoặc làm sai lệch số liệu/thuật ngữ, BẮT BUỘC gán supported=false và answer_grounded=false.\n"
    "4. KHÔNG COI LỜI GIỚI THIỆU LÀ KẾT QUẢ KHOA HỌC: Lời nói đầu, lời bạt, lời giới thiệu của dịch giả/người khác không được coi là kết quả nghiên cứu khoa học của tác giả. Nếu câu trả lời gán ghép thành kết quả nghiên cứu, PHẢI gán supported=false.\n"
    "5. VĂN BẢN BỊ CẮT CỤT: Nếu đoạn trích bị cắt lửng giữa chừng (kết thúc cụt từ hoặc cụt câu) mà câu trả lời phỏng đoán nội dung bị thiếu, PHẢI gán supported=false.\n"
    "6. ĐIỀU KIỆN CHẤP THUẬN: Nếu và chỉ nếu 100% mọi câu trong câu trả lời đều có căn cứ trực tiếp trong [S1] và không có bất kỳ suy diễn thêm nào, gán supported=true, answer_grounded=true, unsupported_claims=[], và trích xuất các câu/cụm từ nguyên văn chứng minh vào 'evidence_spans' (sao chép chính xác từng chữ và chữ hoa/thường nguyên gốc trong [S1], không bọc thêm dấu ngoặc vuông [] quanh trích đoạn).\n\n"
    "Trả về định dạng JSON duy nhất:\n"
    "{{\n"
    "  \"supported\": true/false,\n"
    "  \"answer_grounded\": true/false,\n"
    "  \"unsupported_claims\": [],\n"
    "  \"evidence_spans\": [\"cụm từ nguyên văn trong S1\"],\n"
    "  \"reason\": \"giải thích ngắn gọn dưới 50 từ\"\n"
    "}}"
)


RUNTIME_DIR = BASE_DIR / "data" / "runtime"
WORKER_LOCK_FILE = RUNTIME_DIR / "curation_worker.lock"
WORKER_PAUSE_FILE = RUNTIME_DIR / "curation_worker.pause"
PIPELINE_STATE_FILE = RUNTIME_DIR / "continuous_pipeline_state.json"


def compute_digest(obj: Any) -> str:
    """Deterministic SHA256 digest of arbitrary serializable object."""
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


from app.config import DEFAULT_CHAT_MODEL
DEFAULT_CURATION_MODEL = DEFAULT_CHAT_MODEL

def get_ollama_model_digest(model_name: str, default: Optional[str] = None) -> str:
    """Fetch exact model sha256 digest from Ollama tags endpoint.
    Strict fail-closed: if Ollama is unreachable or model is absent and default is not provided,
    raises RuntimeError. Never returns hardcoded digests or sha256 of model name."""
    if sys.platform == "darwin":
        from app.model_resolver import is_mlx_model
        if is_mlx_model(model_name):
            from app.model_resolver import resolve_mlx_model_dir
            from app.trained_client import TrainedModelClient
            root = resolve_mlx_model_dir(model_name)
            if not TrainedModelClient._verify_weights(root):
                raise RuntimeError("Missing or incomplete local MLX artifact")
            files = set(root.glob("*.safetensors"))
            names = ("config.json", "tokenizer.json", "tokenizer_config.json",
                     "special_tokens_map.json", "chat_template.jinja", "model.safetensors.index.json")
            files.update(root / name for name in names)
            digest = hashlib.sha256()
            for path in sorted(files):
                digest.update(path.name.encode("utf-8") + b"\0")
                if path.is_file():
                    with path.open("rb") as stream:
                        digest.update(hashlib.file_digest(stream, "sha256").digest())
                else:
                    digest.update(b"missing")
            return "mlx-sha256:" + digest.hexdigest()
        raise RuntimeError(f"Chỉ cho phép mô hình MLX cục bộ trên macOS, nhận: {model_name}")
    try:
        from app.ollama_client import OllamaClient
        client = OllamaClient()
        client._verify_model_locality(model_name)
        for m in client.list_models():
            if m.get("name") == model_name or m.get("model") == model_name:
                digest = m.get("digest")
                if digest:
                    return digest
    except Exception as e:
        logger.debug("Could not fetch model digest from Ollama: %s", e)
    if default is not None:
        return default
    raise RuntimeError(
        f"CHẶN QUY TRÌNH: Không thể lấy model digest thực từ Ollama cho '{model_name}'. "
        "Máy chủ Ollama chưa phản hồi hoặc mô hình chưa được cài đặt. Không tạo digest giả định."
    )


def compute_review_verdict_digest(
    source_sha256: str,
    target_sha256: str,
    question_sha256: str,
    prompt_template_sha256: str,
    judge_model: str,
    model_digest: str,
    reason_sha256: str,
    supported: bool = True,
    answer_grounded: bool = True,
) -> str:
    """Deterministic verdict digest binding source, target, question, prompt revision, model digest, and reason."""
    payload = {
        "source_sha256": source_sha256,
        "target_sha256": target_sha256,
        "question_sha256": question_sha256,
        "prompt_template_sha256": prompt_template_sha256,
        "judge_model": judge_model,
        "model_digest": model_digest,
        "reason_sha256": reason_sha256,
        "supported": supported,
        "answer_grounded": answer_grounded,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


_SYNTHETIC_REGISTRY: Optional[Dict[str, Dict[str, Any]]] = None

def get_synthetic_registry() -> Dict[str, Dict[str, Any]]:
    """Return cached canonical synthetic curriculum registry mapped by message hash."""
    global _SYNTHETIC_REGISTRY
    if _SYNTHETIC_REGISTRY is None:
        items = build_synthetic_curriculum()
        _SYNTHETIC_REGISTRY = {compute_digest(it["messages"]): it for it in items}
    return _SYNTHETIC_REGISTRY


def parse_supported_verdict(val: Any) -> bool:
    """Strictly parse boolean supported verdict, rejecting string 'false' / 'False' / '0'."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
        return False
    if isinstance(val, (int, float)):
        return bool(val)
    return False


CALIBRATION_PROTOCOL_VERSION = "v7.3_calibrated_bound_provenance"
VI_CHARS = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]")


def truncate_to_sentence_boundary(text: str, max_chars: int = 900) -> str:
    """Truncate text to the last complete sentence within max_chars to avoid dangling words or mid-sentence cuts."""
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    sub = stripped[:max_chars]
    min_len = min(50, max_chars // 2)
    matches = list(re.finditer(r'[\.\!\?]["”\']?(?:\s|\Z)', sub))
    if matches:
        last_match = matches[-1]
        candidate = sub[:last_match.end()].strip()
        if len(candidate) >= min_len:
            return candidate
    last_space = sub.rfind(" ")
    if last_space >= min_len:
        candidate = sub[:last_space].rstrip(" \t\r\n-–—,;:")
        return candidate
    return sub.strip()


def extract_complete_source_text(raw_text: str, max_chars: int = 900) -> str:
    """Extract a complete, sanitized source excerpt bounded to complete sentences.
    Avoids mid-word or mid-sentence slicing that causes artificial truncation rejections."""
    cleaned = sanitize_for_prompt_context(raw_text)
    return truncate_to_sentence_boundary(cleaned, max_chars=max_chars)


def is_truncated_source_text(text: str) -> bool:
    """Detect whether source text was cut off mid-word or ends without terminal punctuation."""
    stripped = text.strip()
    if not stripped:
        return True
    if stripped[-1] in "-–—,":
        return True
    last_token = stripped.split()[-1] if stripped.split() else ""
    if len(last_token) <= 2 and not last_token[-1] in ".!?\"'”»":
        return True
    if stripped[-1] not in ".!?\"'”)»":
        return True
    return False


EDITORIAL_STAFF_PATTERNS = [
    r"\b(?:vice-president|president|editor-in-chief|editorial director|acquisitions editor|supervising developmental editor|developmental editor|copy editor|production editor|proofreader|compositor|permissions researcher|photo researcher|art director|cover designer|marketing manager|project manager|senior editor|managing editor|executive editor|series editor|associate editor)\b",
    r"\b(?:ban biên tập|chịu trách nhiệm xuất bản|biên tập viên|trình bày bìa|sửa bản in|nộp lưu chiểu|nhà xuất bản|chịu trách nhiệm nội dung)\b",
]

PUBLISHER_BOILERPLATE_PATTERNS = [
    r"library of congress cataloging",
    r"cataloging-in-publication",
    r"all rights reserved(?:\.|\s+no part)",
    r"printed in the united states",
    r"stands as the largest and oldest publishing house owned wholly by its employees",
    r"credits and acknowledgments for material borrowed from other sources",
    r"không phần nào trong xuất bản phẩm này được phép sao chép",
]

CONTRIBUTOR_BIO_ROLE_PATTERNS = [
    r"\b(?:is|was)\s+(?:a|an)\s+(?:licensed psychologist|professor emeritus|professor of|associate professor|assistant professor|director of the|visiting scholar)\b",
    r"\bdepartment of [a-z\s]+ at [a-z\s]+ university\b",
    r"\b(?:has published more than \d+ articles|author of more than \d+ books|visiting scholar at the)\b",
]

ACKNOWLEDGEMENTS_PATTERNS = [
    r"^\s*(?:acknowledgements?|lời cảm ơn)\b",
    r"\b(?:putting together this (?:co-edited )?book|we would like to thank our|special thanks to)\b",
]

TOC_LINE_PATTERN = re.compile(
    r"^\s*(?:[0-9]+|[IVXLCDM]+|chương\s+[0-9]+|chapter\s+[0-9]+|part\s+[0-9]+)\s*[\.■\-\–\:\—]\s*.+?\s+[0-9]+\s*$",
    re.IGNORECASE
)


def is_substantive_psychology_source(text: str) -> Tuple[bool, str]:
    """Strict structural and content filter to reject boilerplate, editorial credits,
    cataloging data, publisher history, table of contents, contributor biographies, acknowledgements, and truncated excerpts."""
    if not text or not isinstance(text, str):
        return False, "empty_source"
    clean_text = text.strip()
    if len(clean_text) < 50:
        return False, "source_too_short"
    lower_text = clean_text.lower()
    for pat in ACKNOWLEDGEMENTS_PATTERNS:
        if re.search(pat, lower_text, re.IGNORECASE):
            return False, "acknowledgements_boilerplate"
    if lower_text.startswith("about the authors") or lower_text.startswith("about the contributors") or lower_text.startswith("contributors\n") or lower_text.startswith("author biographies"):
        return False, "contributor_biography"
    bio_hits = sum(1 for pat in CONTRIBUTOR_BIO_ROLE_PATTERNS if re.search(pat, lower_text, re.IGNORECASE))
    if bio_hits >= 2:
        return False, "contributor_biography"
    editorial_matches = []
    for pat in EDITORIAL_STAFF_PATTERNS:
        editorial_matches.extend(re.findall(pat, lower_text, re.IGNORECASE))
    if len(set(editorial_matches)) >= 2:
        return False, "editorial_staff_credits"
    lines = [l.strip() for l in clean_text.splitlines() if l.strip()]
    if lines:
        role_name_lines = sum(
            1 for l in lines
            if re.match(r"^[A-Za-z\s\-\/]+:\s+[A-Za-z\s\.\,]+", l) and
            any(kw in l.lower() for kw in ("editor", "manager", "director", "designer", "researcher", "proofreader", "compositor", "biên tập", "giám đốc"))
        )
        if role_name_lines >= 2 and (role_name_lines / len(lines)) >= 0.2:
            return False, "editorial_staff_credits"
    for pat in PUBLISHER_BOILERPLATE_PATTERNS:
        if re.search(pat, lower_text, re.IGNORECASE):
            if "publishing house" in lower_text or "cataloging" in lower_text or "all rights reserved" in lower_text or "stands as the largest and oldest" in lower_text:
                return False, "publisher_cataloging_or_corporate_boilerplate"
    if lower_text.startswith("brief contents") or lower_text.startswith("table of contents") or lower_text.startswith("mục lục"):
        return False, "table_of_contents"
    toc_lines = sum(1 for l in lines if TOC_LINE_PATTERN.match(l))
    if len(lines) >= 4 and (toc_lines / len(lines)) >= 0.35:
        return False, "table_of_contents"
    if lower_text.startswith("references\n") or lower_text.startswith("tài liệu tham khảo\n") or lower_text.startswith("bibliography\n"):
        return False, "bibliography_or_references"
    if is_truncated_source_text(clean_text):
        return False, "truncated_source_text"
    return True, ""


def resolve_exact_source_span_offsets(source_text: str, candidate_span: str) -> Optional[Tuple[str, int, int]]:
    """Resolve candidate evidence span against original source text.
    Handles OCR line-breaks, whitespace formatting differences, quotation mark variations (curly vs straight),
    and enclosing quotation wrappers without permitting any altered words, dropped terms, number changes, or translations.
    Returns (verbatim_substring_from_source, start_char_offset, end_char_offset) or None."""
    if not source_text or not candidate_span:
        return None
    span_clean = candidate_span.strip()
    if not span_clean:
        return None

    def _match_span(target: str) -> Optional[Tuple[str, int, int]]:
        idx = source_text.find(target)
        if idx != -1:
            end_idx = idx + len(target)
            return source_text[idx:end_idx], idx, end_idx
        tokens = target.split()
        if not tokens:
            return None
        pattern = r"\s+".join(re.escape(t) for t in tokens)
        m = re.search(pattern, source_text)
        if m is not None:
            start_idx = m.start()
            end_idx = m.end()
            verbatim = source_text[start_idx:end_idx]
            if verbatim.split() == tokens:
                return verbatim, start_idx, end_idx

        # Quote-normalized matching (curly vs straight quotes, apostrophes)
        def _norm_q(s: str) -> str:
            return s.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
        norm_target = _norm_q(target)
        norm_source = _norm_q(source_text)
        norm_tokens = norm_target.split()
        if norm_tokens:
            q_pattern = r"\s+".join(re.escape(t) for t in norm_tokens)
            qm = re.search(q_pattern, norm_source)
            if qm is not None:
                start_idx = qm.start()
                end_idx = qm.end()
                verbatim = source_text[start_idx:end_idx]
                if _norm_q(verbatim).split() == norm_tokens:
                    return verbatim, start_idx, end_idx
        return None

    res = _match_span(span_clean)
    if res is not None:
        return res

    # Unwrapping enclosing quotation marks or brackets if added by model
    unwrapped = span_clean.strip("\"'«»“”")
    if (unwrapped.startswith("[") and unwrapped.endswith("]")) or (unwrapped.startswith("(") and unwrapped.endswith(")")):
        unwrapped = unwrapped[1:-1].strip()
    if unwrapped and unwrapped != span_clean:
        res = _match_span(unwrapped)
        if res is not None:
            return res

    return None


find_verbatim_span_offsets = resolve_exact_source_span_offsets


def segment_text_into_sentences(text: str, prefix: str = "S1") -> List[Dict[str, Any]]:
    """Segment text into deterministic, numbered sentences with exact start/end offsets.
    Handles terminal punctuation (. ! ?), ellipsis, quotes, and double newlines,
    while protecting common abbreviations.
    Returns list of dicts: [{'id': f'{prefix}.{idx}', 'text': str, 'start': int, 'end': int}, ...]
    """
    if not text or not text.strip():
        return []
    abbrev_re = re.compile(r'\b(?:PhD|Dr|Mr|Mrs|Ms|Prof|vs|e\.g|i\.e|al|No|vol|pp|sec)\.\s*$', re.IGNORECASE)
    sentences = []
    pos = 0
    length = len(text)
    idx = 1
    while pos < length:
        while pos < length and text[pos].isspace():
            pos += 1
        if pos >= length:
            break
        start = pos
        while pos < length:
            if text[pos] in '.!?':
                while pos + 1 < length and text[pos + 1] in '.!?':
                    pos += 1
                while pos + 1 < length and text[pos + 1] in '\"\'”’»)':
                    pos += 1
                candidate_sub = text[start:pos + 1]
                if not abbrev_re.search(candidate_sub):
                    if pos + 1 >= length or text[pos + 1].isspace():
                        end = pos + 1
                        sentences.append({
                            'id': f'{prefix}.{idx}',
                            'text': text[start:end].strip(),
                            'start': start,
                            'end': end
                        })
                        idx += 1
                        pos = end
                        break
            elif text[pos] == '\n' and pos + 1 < length and text[pos + 1] == '\n':
                end = pos
                raw_s = text[start:end].strip()
                if raw_s:
                    sentences.append({
                        'id': f'{prefix}.{idx}',
                        'text': raw_s,
                        'start': start,
                        'end': end
                    })
                    idx += 1
                pos = end + 2
                break
            pos += 1
        else:
            if start < length:
                remaining = text[start:].strip()
                if remaining:
                    sentences.append({
                        'id': f'{prefix}.{idx}',
                        'text': remaining,
                        'start': start,
                        'end': length
                    })
                    idx += 1
            break
    return sentences


def validate_vietnamese_semantic_text(text: str) -> Tuple[bool, str]:
    """Validate that text is substantive, natural Vietnamese and not untranslated English or garbled."""
    if not text or not text.strip():
        return False, "empty_text"
    vi_chars = re.findall(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]", text)
    if len(vi_chars) < 3 and len(text) > 30:
        return False, "insufficient_vietnamese_diacritics"
    en_stopwords = {"the", "and", "of", "to", "in", "is", "that", "for", "with", "on", "as", "by", "this", "are", "from", "at"}
    words = re.findall(r"[a-zA-Z]+", text.lower())
    en_count = sum(1 for w in words if w in en_stopwords)
    if len(words) > 10 and (en_count / len(words)) > 0.25:
        return False, "excessive_untranslated_english"
    return True, "valid"


def validate_claim_map(
    source_text: str,
    answer: str,
    claim_map: List[Dict[str, Any]],
    source_id: str = "S1"
) -> Dict[str, Any]:
    """Shared fail-closed validator for full-answer claim-to-source coverage.
    Ensures 100% of answer sentences map to discrete claims with verified source sentence IDs,
    verbatim evidence spans, positive relations, and correct modalities.
    """
    violations: List[str] = []
    if not isinstance(claim_map, list) or len(claim_map) == 0:
        return {
            "valid": False,
            "coverage": 0.0,
            "total_answer_sentences": 0,
            "covered_answer_sentences": 0,
            "uncovered_sentence_ids": [],
            "violations": ["empty_or_invalid_claim_map"],
            "claim_map_hash": ""
        }

    ans_ok, ans_reason = validate_vietnamese_semantic_text(answer)
    if not ans_ok:
        violations.append(f"vietnamese_language_violation: {ans_reason}")

    src_sents = segment_text_into_sentences(source_text, prefix=source_id)
    ans_sents = segment_text_into_sentences(answer, prefix="A1")
    src_sent_ids = {s["id"] for s in src_sents}
    ans_sent_ids = {s["id"] for s in ans_sents}

    covered_ans_sent_ids: Set[str] = set()
    claim_ids: Set[str] = set()

    for idx, c in enumerate(claim_map):
        c_id = c.get("claim_id", f"C{idx+1}")
        if c_id in claim_ids:
            violations.append(f"duplicate_claim_id: {c_id}")
        claim_ids.add(c_id)

        c_text = str(c.get("claim_text", "")).strip()
        if not c_text:
            violations.append(f"empty_claim_text: {c_id}")

        ans_s_id = c.get("answer_sentence_id")
        if not ans_s_id or ans_s_id not in ans_sent_ids:
            violations.append(f"unrecognized_answer_sentence_id: {ans_s_id} in {c_id}")
        else:
            covered_ans_sent_ids.add(ans_s_id)

        # Answer offsets
        ans_offsets = c.get("answer_offsets")
        if not isinstance(ans_offsets, list) or len(ans_offsets) != 2:
            violations.append(f"invalid_answer_offsets_shape: {c_id}")
        else:
            s_off, e_off = ans_offsets
            if not (0 <= s_off < e_off <= len(answer)):
                violations.append(f"answer_offsets_out_of_bounds: {c_id} [{s_off}:{e_off}]")

        # Source sentence IDs
        s_src_ids = c.get("source_sentence_ids", [])
        if not isinstance(s_src_ids, list):
            violations.append(f"invalid_source_sentence_ids_type: {c_id}")
        else:
            for s_sid in s_src_ids:
                if s_sid not in src_sent_ids:
                    violations.append(f"unrecognized_source_sentence_id: {s_sid} in {c_id}")

        # Evidence spans
        spans = c.get("evidence_spans", [])
        if not isinstance(spans, list):
            violations.append(f"invalid_evidence_spans_type: {c_id}")
        else:
            for sp in spans:
                sp_str = str(sp).strip()
                if not sp_str:
                    continue
                resolved = resolve_exact_source_span_offsets(source_text, sp_str)
                if resolved is None:
                    violations.append(f"unmatched_evidence_span: {c_id} -> {sp_str[:40]}")

        # Relation & Modality & Support
        rel = str(c.get("relation", "entailment")).lower()
        if rel in ("contradiction", "unsupported"):
            violations.append(f"negative_or_unsupported_relation: {c_id} ({rel})")

        mod = str(c.get("modality", "asserted")).lower()
        c_type = str(c.get("claim_type", "fact")).lower()
        if mod == "uncertain" and c_type == "fact":
            violations.append(f"uncertain_modality_asserted_as_fact: {c_id}")

        if not c.get("is_supported", True):
            violations.append(f"claim_marked_not_supported: {c_id}")

    # Check full answer coverage
    uncovered = ans_sent_ids - covered_ans_sent_ids
    if uncovered:
        violations.append(f"incomplete_answer_coverage: uncovered {sorted(list(uncovered))}")

    coverage = len(covered_ans_sent_ids) / max(1, len(ans_sent_ids))
    is_valid = (len(violations) == 0 and coverage == 1.0)

    claim_map_hash = hashlib.sha256(json.dumps(claim_map, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    return {
        "valid": is_valid,
        "coverage": coverage,
        "total_answer_sentences": len(ans_sents),
        "covered_answer_sentences": len(covered_ans_sent_ids),
        "uncovered_sentence_ids": sorted(list(uncovered)),
        "violations": violations,
        "claim_map_hash": claim_map_hash,
        "source_sentences": src_sents,
        "answer_sentences": ans_sents
    }


def compute_curation_cache_key(
    source_text: str,
    facet: str,
    teacher_model: str,
    judge_model: str,
    teacher_prompt_tpl: str = TEACHER_PROMPT_TEMPLATE,
    judge_prompt_tpl: str = JUDGE_PROMPT_TEMPLATE,
    teacher_model_digest: Optional[str] = None,
    judge_model_digest: Optional[str] = None,
    question: Optional[str] = None,
    target_answer: Optional[str] = None,
    claims: Optional[List[str]] = None,
    calibration_version: Optional[str] = None
) -> str:
    """Deterministic cache key bound to source text, facet, question, target answer,
    model identities & digests, prompt templates, and calibration protocol."""
    t_digest = teacher_model_digest or get_ollama_model_digest(teacher_model)
    j_digest = judge_model_digest or get_ollama_model_digest(judge_model)
    key_payload = {
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "facet": facet,
        "teacher_model": teacher_model,
        "teacher_model_digest": t_digest,
        "judge_model": judge_model,
        "judge_model_digest": j_digest,
        "teacher_prompt_sha256": hashlib.sha256(teacher_prompt_tpl.encode("utf-8")).hexdigest(),
        "judge_prompt_sha256": hashlib.sha256(judge_prompt_tpl.encode("utf-8")).hexdigest(),
    }
    if question is not None:
        key_payload["question_sha256"] = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
    if target_answer is not None:
        key_payload["target_sha256"] = hashlib.sha256(target_answer.strip().encode("utf-8")).hexdigest()
    if claims is not None:
        key_payload["claims_sha256"] = hashlib.sha256(json.dumps(sorted(claims), ensure_ascii=False).encode("utf-8")).hexdigest()
    if calibration_version is not None:
        key_payload["calibration_version"] = calibration_version
    return hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()


def validate_and_parse_judge_verdict(
    raw_payload: Any,
    source_text: str,
    question: str,
    target_answer: str,
    judge_model: str,
    model_digest: str,
    prompt_template_sha256: str,
    target_sha256: Optional[str] = None,
    calibration_version: str = CALIBRATION_PROTOCOL_VERSION,
    require_vietnamese: bool = True,
    require_citation: bool = False,
    done_reason: Optional[str] = None
) -> Dict[str, Any]:
    """Shared fail-closed review validator and parser used across calibration,
    candidate generation, rejudge, and release gates.
    Validates boolean schema, discrete claims, evidence spans against source text,
    language adherence, and computes cryptographic bindings.
    """
    if isinstance(raw_payload, str):
        try:
            data = json.loads(raw_payload)
        except Exception as e:
            data = {
                "supported": False,
                "answer_grounded": False,
                "unsupported_claims": [f"json_parse_error: {e}"],
                "reason": f"Lỗi phân tích cú pháp JSON từ thẩm định viên: {e}",
                "evidence_spans": []
            }
    elif isinstance(raw_payload, dict):
        data = dict(raw_payload)
    else:
        data = {
            "supported": False,
            "answer_grounded": False,
            "unsupported_claims": ["invalid_payload_type"],
            "reason": "Payload thẩm định không hợp lệ",
            "evidence_spans": []
        }

    sup_raw = data.get("supported")
    grd_raw = data.get("answer_grounded", sup_raw)
    sup_bool = parse_supported_verdict(sup_raw)
    grd_bool = parse_supported_verdict(grd_raw)
    is_supported = sup_bool and grd_bool

    d_reason = done_reason or (data.get("done_reason") if isinstance(data, dict) else None)

    raw_unsupported = data.get("unsupported_claims", [])
    if isinstance(raw_unsupported, list):
        unsupported_claims = [str(c) for c in raw_unsupported]
    elif raw_unsupported:
        unsupported_claims = [str(raw_unsupported)]
    else:
        unsupported_claims = []

    if d_reason == "length":
        is_supported = False
        if "generation_truncated_length" not in unsupported_claims:
            unsupported_claims.append("generation_truncated_length")
        data["reason"] = (str(data.get("reason", "")) + " | Phản hồi thẩm định bị cắt do vượt giới hạn token (done_reason=length)").strip(" |")

    # Fail closed if nested raw_response is present and negative
    nested_raw = data.get("raw_response")
    if nested_raw is not None and nested_raw != data:
        if isinstance(nested_raw, str):
            try:
                nested_raw = json.loads(nested_raw)
            except Exception:
                nested_raw = {"supported": False, "reason": "json_parse_error_in_nested_raw"}
        if isinstance(nested_raw, dict):
            raw_n_sup = parse_supported_verdict(nested_raw.get("supported"))
            raw_n_grd = parse_supported_verdict(nested_raw.get("answer_grounded", raw_n_sup))
            if not (raw_n_sup is True and raw_n_grd is True):
                is_supported = False
                unsupported_claims.append("negative_nested_raw_response")
            if nested_raw.get("unsupported_claims"):
                is_supported = False
                unsupported_claims.extend([str(c) for c in nested_raw.get("unsupported_claims")])
            if nested_raw.get("reason") == "judge_call_failed":
                is_supported = False
                unsupported_claims.append("judge_call_failed")

    if is_supported and len(unsupported_claims) > 0:
        is_supported = False

    raw_spans = data.get("evidence_spans", [])
    evidence_spans: List[str] = []
    evidence_span_offsets: List[Dict[str, Any]] = []
    has_span_error = False

    if isinstance(raw_spans, list):
        for s in raw_spans:
            s_str = s.get("text") if isinstance(s, dict) else str(s)
            s_str = s_str.strip()
            if not s_str:
                continue
            resolved = resolve_exact_source_span_offsets(source_text, s_str)
            if resolved is not None:
                verbatim_text, start_off, end_off = resolved
                evidence_spans.append(verbatim_text)
                evidence_span_offsets.append({
                    "text": verbatim_text,
                    "start": start_off,
                    "end": end_off
                })
            else:
                has_span_error = True
                evidence_spans.append(s_str)
                reason = (str(data.get("reason", "")) + f" | Bằng chứng không có trong nguồn: '{s_str[:50]}'").strip(" |")
                unsupported_claims.append(f"fabricated_evidence_span: {s_str[:50]}")

    reason = str(data.get("reason", "")).strip()

    # Pre-check: Substantive psychology source check
    is_sub, sub_reason = is_substantive_psychology_source(source_text)
    if not is_sub:
        is_supported = False
        reason = (reason + f" | Nguồn trích không đủ điều kiện thực chất: {sub_reason}").strip(" |")
        if f"ineligible_source: {sub_reason}" not in unsupported_claims:
            unsupported_claims.append(f"ineligible_source: {sub_reason}")

    # Gate: Evidence spans must exist and appear in source
    if is_supported:
        if len(evidence_spans) == 0:
            is_supported = False
            reason = (reason + " | Thiếu bằng chứng trích đoạn (evidence_spans rỗng).").strip(" |")
            unsupported_claims.append("missing_evidence_spans")
        elif has_span_error:
            is_supported = False

    # Gate: Language constraint / instruction following
    if require_vietnamese and target_answer:
        vi_chars_count = len(VI_CHARS.findall(target_answer))
        words = re.findall(r"[A-Za-z]+", target_answer.lower())
        en_hits = sum(w in {"the", "and", "of", "to", "that", "with", "for", "is", "in", "by", "from", "on"} for w in words)
        if len(target_answer.strip()) >= 30 and (vi_chars_count < 3 and (en_hits >= 2 or detect_language(target_answer) == "en")):
            is_supported = False
            reason = (reason + " | Câu trả lời vi phạm ngôn ngữ yêu cầu (tiếng Anh thay vì tiếng Việt).").strip(" |")
            unsupported_claims.append("language_instruction_violation_english")

    # Gate: Citation requirement [S1]
    if require_citation and target_answer:
        if "[S1]" not in target_answer:
            is_supported = False
            reason = (reason + " | Câu trả lời thiếu dẫn chứng mã tài liệu [S1] theo yêu cầu.").strip(" |")
            unsupported_claims.append("missing_s1_citation")

    if not reason:
        reason = "Đầy đủ căn cứ từ trích đoạn" if is_supported else "Thẩm định viên xác định không đủ căn cứ"

    src_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    q_hash = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
    tgt_hash = target_sha256 or hashlib.sha256(target_answer.strip().encode("utf-8")).hexdigest()
    ans_hash = hashlib.sha256(target_answer.strip().encode("utf-8")).hexdigest()

    verdict_hash = compute_review_verdict_digest(
        source_sha256=src_hash,
        target_sha256=tgt_hash,
        question_sha256=q_hash,
        prompt_template_sha256=prompt_template_sha256,
        judge_model=judge_model,
        model_digest=model_digest,
        reason_sha256=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
        supported=is_supported,
        answer_grounded=is_supported and grd_bool
    )

    return {
        "supported": is_supported,
        "answer_grounded": is_supported and grd_bool,
        "unsupported_claims": unsupported_claims,
        "evidence_spans": evidence_spans,
        "evidence_span_offsets": evidence_span_offsets,
        "reason": reason,
        "judge_model": judge_model,
        "model_digest": model_digest,
        "source_sha256": src_hash,
        "target_sha256": tgt_hash,
        "answer_sha256": ans_hash,
        "question_sha256": q_hash,
        "prompt_template_sha256": prompt_template_sha256,
        "calibration_version": calibration_version,
        "verdict_digest": verdict_hash,
        "raw_response": data
    }


def is_valid_cached_judge_verdict(
    cached_judge: Optional[Dict[str, Any]],
    source_text: str,
    question: str,
    target_answer: str,
    judge_model: str,
    expected_digest: str,
    expected_prompt_hash: str,
    expected_target_hash: Optional[str] = None,
    expected_calibration_version: Optional[str] = None
) -> bool:
    """Validate whether a cached judge verdict strictly satisfies all bindings, calibration, and constraints."""
    if not cached_judge or not isinstance(cached_judge, dict):
        return False
    # Fail closed on non-substantive source text
    is_sub, _ = is_substantive_psychology_source(source_text)
    if not is_sub:
        return False
    if cached_judge.get("judge_model") != judge_model:
        return False
    if not cached_judge.get("model_digest") or cached_judge.get("model_digest") != expected_digest:
        return False
    if not cached_judge.get("prompt_template_sha256") or cached_judge.get("prompt_template_sha256") != expected_prompt_hash:
        return False
    if cached_judge.get("reason") == "judge_call_failed":
        return False
    if not (parse_supported_verdict(cached_judge.get("supported")) is True and
            parse_supported_verdict(cached_judge.get("answer_grounded", True)) is True):
        return False
    if cached_judge.get("unsupported_claims"):
        return False
    spans = cached_judge.get("evidence_spans")
    if not isinstance(spans, list) or len(spans) == 0:
        return False
    for s in spans:
        s_str = s.get("text") if isinstance(s, dict) else str(s)
        s_str = s_str.strip()
        if not s_str or (s_str not in source_text and resolve_exact_source_span_offsets(source_text, s_str) is None):
            return False

    expected_src_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if not cached_judge.get("source_sha256") or cached_judge.get("source_sha256") != expected_src_hash:
        return False

    expected_q_hash = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
    if not cached_judge.get("question_sha256") or cached_judge.get("question_sha256") != expected_q_hash:
        return False

    expected_a_hash = hashlib.sha256(target_answer.strip().encode("utf-8")).hexdigest()
    recorded_tgt_hash = cached_judge.get("target_sha256")
    if not recorded_tgt_hash:
        return False
    valid_targets = {expected_a_hash}
    if expected_target_hash:
        valid_targets.add(expected_target_hash)
    if recorded_tgt_hash not in valid_targets:
        return False

    target_calib = expected_calibration_version or CALIBRATION_PROTOCOL_VERSION
    if not cached_judge.get("calibration_version") or cached_judge.get("calibration_version") != target_calib:
        return False

    # Fail closed on negative nested raw_response in cache
    raw_resp = cached_judge.get("raw_response")
    if raw_resp is not None:
        if isinstance(raw_resp, str):
            try:
                raw_resp = json.loads(raw_resp)
            except Exception:
                return False
        if isinstance(raw_resp, dict):
            raw_sup = parse_supported_verdict(raw_resp.get("supported"))
            raw_grd = parse_supported_verdict(raw_resp.get("answer_grounded", raw_sup))
            if not (raw_sup is True and raw_grd is True):
                return False
            if raw_resp.get("unsupported_claims") or raw_resp.get("reason") == "judge_call_failed":
                return False
            raw_spans = raw_resp.get("evidence_spans")
            if isinstance(raw_spans, list):
                for rs in raw_spans:
                    rs_str = rs.get("text") if isinstance(rs, dict) else str(rs)
                    rs_str = rs_str.strip()
                    if not rs_str or (rs_str not in source_text and resolve_exact_source_span_offsets(source_text, rs_str) is None):
                        return False
        else:
            return False

    return True


def get_book_partition(db: sqlite3.Connection) -> Tuple[Set[str], Set[str]]:
    """Strictly partition canonical training books from holdout books and their hash aliases."""
    cursor = db.cursor()
    docs = list(cursor.execute("SELECT filename, file_hash FROM documents WHERE status='indexed' ORDER BY filename"))
    def _fname(r): return r["filename"] if isinstance(r, (sqlite3.Row, dict)) else r[0]
    def _fhash(r): return r["file_hash"] if isinstance(r, (sqlite3.Row, dict)) else r[1]

    holdout_hashes = {_fhash(r) for r in docs if _fname(r) in FROZEN_HOLDOUT_BOOKS}
    holdout_aliases = {_fname(r) for r in docs if _fhash(r) in holdout_hashes or _fname(r) in FROZEN_HOLDOUT_BOOKS}

    seen_hashes = set()
    canonical_allowed = set()
    for row in sorted(docs, key=lambda r: (len(_fname(r)), _fname(r))):
        fname, fhash = _fname(row), _fhash(row)
        if fhash not in seen_hashes and fname not in holdout_aliases:
            canonical_allowed.add(fname)
        seen_hashes.add(fhash)

    return canonical_allowed, holdout_aliases


def assign_group_partition(filename: str, page_num: int) -> str:
    """Deterministically partition a book-chapter group into train (85%) or valid (15%)."""
    chapter_bucket = page_num // 15
    key = f"{filename}:{chapter_bucket}"
    hash_val = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return "valid" if (hash_val % 100) < 15 else "train"


def get_group_key(filename: str, page_num: int) -> str:
    """Return deterministic book-chapter cluster key."""
    return f"{filename}:{page_num // 15}"


# ---------------------------------------------------------------------------
# Calibration Suite for Judge
# ---------------------------------------------------------------------------
def run_judge_calibration(judge_model: str = DEFAULT_CURATION_MODEL) -> Dict[str, Any]:
    """Verify that the judge model strictly distinguishes supported vs unsupported claims and extrapolations."""
    calibration_cases = [
        {
            "id": "calib_positive_habits",
            "type": "positive_control",
            "book": "5446-suc-manh-cua-thoi-quen",
            "source": "Theo Skinner, điều kiện hóa thao tác là một quá trình học tập trong đó hành vi được củng cố hoặc trừng phạt dựa trên hậu quả của nó.",
            "claims": ["Điều kiện hóa thao tác là quá trình học tập dựa trên hậu quả của hành vi"],
            "answer": "Theo đoạn trích [S1], Skinner định nghĩa điều kiện hóa thao tác là một quá trình học tập, trong đó hành vi của cá nhân được củng cố hoặc trừng phạt dựa trên hậu quả mà hành vi đó mang lại [S1].",
            "question": "Điều kiện hóa thao tác theo Skinner được định nghĩa như thế nào?",
            "expected_supported": True
        },
        {
            "id": "calib_positive_thinking",
            "type": "positive_control",
            "book": "5600-tu-duy-nhanh-va-cham",
            "source": "Hệ thống 1 hoạt động tự động và nhanh chóng, với rất ít hoặc không có nỗ lực và không có sự kiểm soát tự giác. Hệ thống 2 phân bổ sự chú ý đến các hoạt động đòi hỏi nỗ lực trí tuệ.",
            "claims": ["Hệ thống 1 hoạt động tự động và nhanh chóng", "Hệ thống 2 phân bổ sự chú ý đến các hoạt động đòi hỏi nỗ lực trí tuệ"],
            "answer": "Theo đoạn trích [S1], Hệ thống 1 vận hành tự động và nhanh chóng mà không cần nhiều nỗ lực, trong khi Hệ thống 2 phụ trách phân bổ sự chú ý cho các hoạt động trí tuệ phức tạp [S1].",
            "question": "Sự khác biệt cơ bản giữa Hệ thống 1 và Hệ thống 2 là gì?",
            "expected_supported": True
        },
        {
            "id": "calib_psychological_extrapolation",
            "type": "psychological_extrapolation",
            "book": "5446-suc-manh-cua-thoi-quen",
            "source": "Người phụ nữ quyết định tham gia nhóm hỗ trợ cai nghiện cờ bạc sau khi nhận ra cô đã tiêu sạch tiền tiết kiệm gia đình.",
            "claims": ["Người phụ nữ tham gia nhóm cai nghiện cờ bạc", "Người chồng không tiếp tục hỗ trợ tài chính", "Cô lo lắng sâu sắc về tương lai bất định"],
            "answer": "Theo trích đoạn [S1], người phụ nữ quyết định tham gia nhóm hỗ trợ cai nghiện cờ bạc sau khi nhận ra cô đã tiêu sạch tiền tiết kiệm gia đình. Điều này xảy ra do người chồng không tiếp tục hỗ trợ tài chính, khiến cô lo lắng sâu sắc về tương lai bất định theo các lý thuyết tâm lý học hành vi.",
            "question": "Người phụ nữ đã có hành động gì sau khi nhận ra vấn đề tài chính?",
            "expected_supported": False
        },
        {
            "id": "calib_preface_as_fact",
            "type": "preface_as_fact",
            "book": "5600-tu-duy-nhanh-va-cham",
            "source": "LỜI GIỚI THIỆU: Cuốn sách này là một tác phẩm xuất sắc của giáo sư Daniel Kahneman. Tôi rất hân hạnh được giới thiệu bản dịch tiếng Việt này tới bạn đọc trong cả nước. GS. Nguyễn Văn Tuấn.",
            "claims": ["Nghiên cứu của Kahneman chứng minh tác động mạnh mẽ đến bạn đọc cả nước"],
            "answer": "Theo đoạn trích [S1], nghiên cứu tâm lý học của Daniel Kahneman đã chứng minh rằng cuốn sách của ông tác động mạnh mẽ đến bạn đọc cả nước thông qua các thí nghiệm hành vi.",
            "question": "Nghiên cứu của Daniel Kahneman chứng minh điều gì về tâm lý học?",
            "expected_supported": False
        },
        {
            "id": "calib_dangling_truncation",
            "type": "dangling_truncation",
            "book": "5446-suc-manh-cua-thoi-quen",
            "source": "Lề thói được lập trình sẵn – Hãy tò mò, Nói điều không ai nói, Áp d",
            "claims": ["Lề thói được lập trình sẵn giúp nhân viên áp dụng các biện pháp mới"],
            "answer": "Đoạn trích [S1] trình bày các lề thói được lập trình sẵn gồm Hãy tò mò, Nói điều không ai nói và Áp dụng các giải pháp mới.",
            "question": "Các lề thói được lập trình sẵn gồm những gì?",
            "expected_supported": False
        },
        {
            "id": "calib_numeric_error",
            "type": "numeric_error",
            "book": "Tài liệu kiểm chuẩn",
            "source": "Nghiên cứu khảo sát 100 bệnh nhân, trong đó có 20 người xuất hiện triệu chứng lo âu.",
            "claims": ["Có 45% bệnh nhân trong nghiên cứu xuất hiện triệu chứng lo âu"],
            "answer": "Theo nghiên cứu trên 100 bệnh nhân, có 45% người xuất hiện triệu chứng lo âu rõ rệt.",
            "question": "Tỷ lệ bệnh nhân xuất hiện triệu chứng lo âu trong nghiên cứu là bao nhiêu?",
            "expected_supported": False
        },
        {
            "id": "calib_terminology_error",
            "type": "terminology_error",
            "book": "Tài liệu kiểm chuẩn",
            "source": "Củng cố âm tính là việc loại bỏ kích thích khó chịu nhằm làm tăng tần suất xuất hiện của hành vi.",
            "claims": ["Củng cố âm tính đồng nghĩa với hình phạt để làm giảm hành vi"],
            "answer": "Củng cố âm tính là hình thức xử phạt áp dụng để triệt tiêu hành vi không mong muốn.",
            "question": "Củng cố âm tính có ý nghĩa gì trong điều kiện hóa thao tác?",
            "expected_supported": False
        },
        {
            "id": "calib_factual_distortion",
            "type": "factual_distortion",
            "book": "Tài liệu kiểm chuẩn",
            "source": "Những cách thô bạo hơn gồm: lăng mạ ma quỷ, người bệnh bị bỏ rét, kéo căng hoặc dùng roi quất.",
            "claims": ["Các biện pháp thô bạo gồm lăng mạ ma quỷ, bỏ đói hoặc dùng roi quất"],
            "answer": "Những biện pháp thô bạo được áp dụng trong thời Trung cổ gồm lăng mạ ma quỷ, bỏ đói người bệnh hoặc dùng roi quất.",
            "question": "Các biện pháp trị liệu thô bạo thời Trung cổ gồm những gì?",
            "expected_supported": False
        },
        {
            "id": "calib_string_false_test",
            "type": "string_false_parsing",
            "book": "Tài liệu kiểm chuẩn",
            "source": "Đoạn trích mô tả quá trình hình thành trí nhớ ngắn hạn qua vỏ não trước trán.",
            "claims": ["Trí nhớ ngắn hạn được lưu giữ vĩnh viễn ở tủy sống"],
            "answer": "Trí nhớ ngắn hạn nằm vĩnh viễn ở tủy sống và không cần tới não bộ.",
            "question": "Cơ chế trí nhớ ngắn hạn diễn ra ở đâu?",
            "expected_supported": False
        },
        {
            "id": "calib_statistical_vs_personal_belief_distortion",
            "type": "statistical_vs_personal_belief_distortion",
            "book": "5600-tu-duy-nhanh-va-cham",
            "source": "Niềm tin lâu bền của con người thường bắt nguồn từ kinh nghiệm sống cá nhân trực tiếp chứ không hình thành từ những con số thống kê khô khan.",
            "claims": ["Niềm tin lâu bền của con người dựa trên các kết quả thống kê khoa học"],
            "answer": "Theo đoạn trích [S1], niềm tin lâu bền của con người được chứng minh là hình thành chủ yếu dựa trên các kết quả thống kê khoa học và phân tích định lượng.",
            "question": "Niềm tin lâu bền của con người hình thành từ đâu theo đoạn trích?",
            "expected_supported": False
        },
        {
            "id": "calib_sunk_cost_counterfactual_omission",
            "type": "sunk_cost_counterfactual_omission",
            "book": "5600-tu-duy-nhanh-va-cham",
            "source": "Hiện tượng chi phí chìm được minh họa rõ khi một người đã bỏ 100 đô la mua vé xem kịch thì sẽ cố đi xem dù có bão tuyết, trong khi nếu được tặng chiếc vé miễn phí đó, họ sẽ dễ dàng chọn ở nhà.",
            "claims": ["Tư duy phê phán giúp con người nhận thức rõ về mọi quyết định mua sắm"],
            "answer": "Theo đoạn trích [S1], tư duy phê phán là phương pháp rèn luyện nhận thức quan trọng giúp mọi người đưa ra những quyết định mua sắm và thưởng thức nghệ thuật đúng đắn trong đời sống.",
            "question": "Đoạn trích minh họa hiện tượng chi phí chìm qua tình huống vé xem kịch như thế nào?",
            "expected_supported": False
        },
        {
            "id": "calib_language_instruction_violation",
            "type": "language_instruction_violation",
            "book": "Abigail A. Baird - Attachment Theory",
            "source": "Lý thuyết gắn bó của John Bowlby chỉ ra rằng mối liên kết cảm xúc ban đầu giữa trẻ sơ sinh và người chăm sóc có vai trò nền tảng cho sự phát triển tâm lý sau này.",
            "claims": ["Lý thuyết gắn bó chỉ ra mối liên kết cảm xúc ban đầu có vai trò nền tảng"],
            "answer": "According to the excerpt [S1], John Bowlby's attachment theory demonstrates that the early emotional bond between infants and primary caregivers plays a fundamental role in later psychological development [S1].",
            "question": "Lý thuyết gắn bó của Bowlby nhấn mạnh điều gì? Hãy trả lời chi tiết bằng tiếng Việt.",
            "expected_supported": False
        },
        {
            "id": "calib_positive_wrapped_source_newlines",
            "type": "positive_control_wrapped_source",
            "book": "473711314-Tam-lý-học-dị-thường-va-lam-sang-Paul-Bennett-pdf",
            "source": "Chương này giới thiệu những vấn đề của tâm lí học dị thường, trong đó có nhiều vấn\nđề được bàn sâu ở các phần sau. Bắt đầu bằng khái niệm về tâm lí học dị thường và nó liên\nquan như thế nào đến sức khoẻ tâm thần (SKTT).",
            "claims": ["Chương này giới thiệu những vấn đề của tâm lí học dị thường", "Bắt đầu bằng khái niệm về tâm lí học dị thường và nó liên quan như thế nào đến sức khoẻ tâm thần"],
            "answer": "Theo đoạn trích [S1], Chương này giới thiệu những vấn đề của tâm lí học dị thường, trong đó có nhiều vấn đề được bàn sâu ở các phần sau. Bắt đầu bằng khái niệm về tâm lí học dị thường và nó liên quan như thế nào đến sức khoẻ tâm thần [S1].",
            "question": "Chương đầu tiên của sách giới thiệu những nội dung nền tảng nào về tâm lý học dị thường?",
            "expected_supported": True
        },
        {
            "id": "calib_negative_credits_as_reasoning",
            "type": "metadata_credits_as_reasoning_negative_control",
            "book": "Abigail A. Baird & Anjanie McCarthy - Think Psychology",
            "source": "Vice-President, Editorial Director: Gary Bennett\nEditor-in-Chief: Michelle Sartor\nAcquisitions Editor: Matthew Christian\nMarketing Manager: Lisa Gillis\nSupervising Developmental Editor: Maurice Esses\nCopyright © 2014 by Pearson Education, Inc.",
            "claims": ["Quy trình xuất bản sách áp dụng suy luận điều kiện", "Bảo đảm quản lý hiệu quả và trách nhiệm học thuật"],
            "answer": "Đoạn trích [S1] chứng minh rằng quy trình xuất bản sách áp dụng suy luận điều kiện để bảo đảm quản lý hiệu quả và trách nhiệm học thuật thông qua sự phân công giữa Gary Bennett và Michelle Sartor [S1].",
            "question": "Cấu trúc ban biên tập này phản ánh suy luận điều kiện (conditional reasoning) và quản lý chất lượng học thuật như thế nào?",
            "expected_supported": False
        },
        {
            "id": "calib_positive_real_psychology_knowledge",
            "type": "positive_control_real_knowledge",
            "book": "5600-tu-duy-nhanh-va-cham",
            "source": "Hiệu ứng mỏ neo xuất hiện khi người ta cân nhắc một giá trị cụ thể cho một đại lượng chưa biết trước khi ước tính đại lượng đó. Những ước tính vẫn duy trì ở gần con số mà mọi người đã cân nhắc.",
            "claims": ["Hiệu ứng mỏ neo xuất hiện khi người ta cân nhắc một giá trị cụ thể cho một đại lượng chưa biết trước khi ước tính đại lượng đó", "Những ước tính vẫn duy trì ở gần con số mà mọi người đã cân nhắc"],
            "answer": "Theo đoạn trích [S1], Hiệu ứng mỏ neo xuất hiện khi người ta cân nhắc một giá trị cụ thể cho một đại lượng chưa biết trước khi ước tính đại lượng đó. Những ước tính vẫn duy trì ở gần con số mà mọi người đã cân nhắc [S1].",
            "question": "Hiệu ứng mỏ neo xảy ra trong quá trình nhận thức như thế nào theo đoạn trích?",
            "expected_supported": True
        },
        {
            "id": "calib_negative_terminology_distortion_biopsychosocial",
            "type": "terminology_distortion_negative_control",
            "book": "473711314-Tam-lý-học-dị-thường-va-lam-sang-Paul-Bennett-pdf",
            "source": "Chương tiếp theo sẽ khảo sát một số yếu tố liên quan đến sự phát triển các rối loạn SKTT, tập trung vào các khía cạnh: di truyền, sinh học, tâm lí, xã hội và gia đình. Cuối cùng sẽ giới thiệu tiếp cận sinh-tâm-xã hội.",
            "claims": ["Đề xuất tiếp cận sinh-tâm-xã-học", "Khẳng định các rối loạn bắt nguồn hoàn toàn từ định chế xã hội phong kiến"],
            "answer": "Theo đoạn trích [S1], cuốn sách đề xuất tiếp cận sinh-tâm-xã-học, một mô hình lý thuyết mới khẳng định các rối loạn bắt nguồn hoàn toàn từ định chế xã hội phong kiến [S1].",
            "question": "Cách tiếp cận nào được giới thiệu để tích hợp các yếu tố phát triển rối loạn sức khỏe tâm thần?",
            "expected_supported": False
        }
    ]

    results = []
    all_passed = True
    j_digest = get_ollama_model_digest(judge_model)
    prompt_tpl_hash = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

    for case in calibration_cases:
        prompt = JUDGE_PROMPT_TEMPLATE.format(
            book=case.get("book", "Tài liệu kiểm chuẩn"),
            page=1,
            text=case["source"],
            question=case["question"],
            facet="calibration",
            claims_json=json.dumps(case["claims"], ensure_ascii=False),
            answer=case["answer"]
        )
        try:
            if sys.platform == "darwin":
                from app.model_resolver import is_mlx_model
                if not is_mlx_model(judge_model):
                    raise RuntimeError(f"Chỉ hỗ trợ mô hình MLX cục bộ trên macOS, nhận: {judge_model}")
                from app.trained_client import TrainedModelClient
                native_client = TrainedModelClient(model_name=judge_model)
                raw_text = native_client.chat_complete(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=400,
                ).strip()
                done_reason = native_client.last_done_reason
            else:
                from app.ollama_client import OllamaClient
                client = OllamaClient()
                client._verify_model_locality(judge_model)
                with gpu_coordinator.acquire_for_inference():
                    resp = client.chat_sync(
                        messages=[{"role": "user", "content": prompt}],
                        model=judge_model,
                        temperature=0.0,
                        num_predict=400,
                    )
                raw_text = resp.get("message", {}).get("content", "").strip()
                done_reason = resp.get("done_reason")
            verdict = validate_and_parse_judge_verdict(
                raw_payload=raw_text,
                source_text=case["source"],
                question=case["question"],
                target_answer=case["answer"],
                judge_model=judge_model,
                model_digest=j_digest,
                prompt_template_sha256=prompt_tpl_hash,
                calibration_version=CALIBRATION_PROTOCOL_VERSION,
                require_vietnamese=True,
                done_reason=done_reason,
            )
            supported = verdict["supported"]
            match = (supported == case["expected_supported"])
            results.append({
                "id": case["id"],
                "type": case["type"],
                "match": match,
                "supported": supported,
                "expected": case["expected_supported"],
                "evidence_spans": verdict["evidence_spans"],
                "reason": verdict["reason"],
                "verdict_digest": verdict.get("verdict_digest"),
                "full_verdict": verdict
            })
            if not match:
                all_passed = False
        except Exception as exc:
            results.append({"id": case["id"], "type": case["type"], "match": False, "error": str(exc)})
            all_passed = False

    return {
        "judge_model": judge_model,
        "model_digest": j_digest,
        "calibration_protocol": CALIBRATION_PROTOCOL_VERSION,
        "prompt_template_sha256": prompt_tpl_hash,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "all_passed": all_passed,
        "total_cases": len(calibration_cases),
        "passed_cases": sum(1 for c in results if c.get("match")),
        "cases": results
    }


def ensure_judge_calibrated(judge_model: str = DEFAULT_CURATION_MODEL) -> Dict[str, Any]:
    """Fail-closed calibration gate. Raises RuntimeError if judge does not pass calibration."""
    calib = run_judge_calibration(judge_model=judge_model)
    if not calib["all_passed"]:
        failed_cases = [c for c in calib["cases"] if not c.get("match")]
        raise RuntimeError(
            f"CHẶN QUY TRÌNH: Thẩm định viên '{judge_model}' không vượt qua kiểm định chuẩn hóa (calibration gate). "
            f"Các trường hợp thất bại: {json.dumps(failed_cases, ensure_ascii=False)}"
        )
    logger.info("Judge calibration PASSED for %s: 100%% control cases matched.", judge_model)
    calib_out = BASE_DIR / "data" / "evaluation" / "judge-calibration-2026-10-02.json"
    _write_atomic_json(calib_out, calib)
    return calib


# ---------------------------------------------------------------------------
# Real Local Teacher Call
# ---------------------------------------------------------------------------
def call_teacher_model(
    src: Dict[str, Any],
    facet: str = "overview",
    teacher_model: str = DEFAULT_CURATION_MODEL,
    max_retries: int = 3
) -> Optional[Dict[str, Any]]:
    """Generate question, substantive answer, and discrete claims using local teacher model with retry."""
    book = src["book_title"]
    page = src["page_num"]
    text = src["text"]

    prompt = TEACHER_PROMPT_TEMPLATE.format(
        book=book,
        page=page,
        text=text,
        facet=facet
    )

    for attempt in range(max_retries):
        try:
            if sys.platform == "darwin":
                from app.model_resolver import is_mlx_model
                if not is_mlx_model(teacher_model):
                    raise RuntimeError(f"Chỉ hỗ trợ mô hình MLX cục bộ trên macOS, nhận: {teacher_model}")
                from app.trained_client import TrainedModelClient
                native_client = TrainedModelClient(model_name=teacher_model)
                raw_msg = native_client.chat_complete(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=500,
                )
                if native_client.last_done_reason != "stop":
                    raise RuntimeError("Native teacher generation did not finish")
            else:
                from app.ollama_client import OllamaClient
                client = OllamaClient()
                client._verify_model_locality(teacher_model)
                with gpu_coordinator.acquire_for_inference():
                    resp = client.chat_sync(
                        messages=[{"role": "user", "content": prompt}],
                        model=teacher_model,
                        temperature=0.2,
                        num_predict=500,
                    )
                raw_msg = resp.get("message", {}).get("content", "")
            try:
                content = json.loads(raw_msg)
            except Exception as json_err:
                logger.warning("Teacher JSON decode error (attempt %d): %s; raw: %s", attempt + 1, json_err, raw_msg[:100])
                time.sleep(1)
                continue
            q = content.get("question", "").strip()
            a = content.get("answer", "").strip()
            claims = content.get("claims", [])
            if q and a and isinstance(claims, list) and len(claims) > 0:
                # Strict Vietnamese instruction-following and citation check
                vi_q_count = len(VI_CHARS.findall(q))
                vi_a_count = len(VI_CHARS.findall(a))
                is_en_q = (len(q) >= 20 and vi_q_count < 2 and detect_language(q) == "en")
                is_en_a = (len(a) >= 30 and vi_a_count < 3 and detect_language(a) == "en")
                has_s1 = "[S1]" in a
                if is_en_q or is_en_a or not has_s1:
                    logger.warning(
                        "Teacher output language/citation violation (attempt %d) for chunk %s: en_q=%s en_a=%s has_s1=%s",
                        attempt + 1, src.get("chunk_id"), is_en_q, is_en_a, has_s1
                    )
                    time.sleep(1)
                    continue
                return {
                    "question": q,
                    "answer": a,
                    "claims": claims
                }
        except Exception as exc:
            logger.warning("Teacher call attempt %d failed for chunk %s: %s", attempt + 1, src.get("chunk_id"), exc)
            time.sleep(2 ** attempt)

    return None


# ---------------------------------------------------------------------------
# Real Local Judge Call
# ---------------------------------------------------------------------------
def call_judge_model(
    src: Dict[str, Any],
    candidate_item: Dict[str, Any],
    facet: str = "overview",
    judge_model: str = DEFAULT_CURATION_MODEL,
    max_retries: int = 3,
    expected_judge_digest: Optional[str] = None,
    require_citation: bool = False
) -> Dict[str, Any]:
    """Audit claims and answer against source excerpt using local judge model with strict parsing and shared validator."""
    book = src.get("book_title", "")
    page = src.get("page_num", 1)
    text = src.get("text", "")
    claims = candidate_item.get("claims", [])
    answer = candidate_item.get("answer", "")
    question = candidate_item.get("question", "")

    m_digest = expected_judge_digest or get_ollama_model_digest(judge_model)
    prompt_tpl_hash = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

    cand_messages = candidate_item.get("messages")
    if cand_messages:
        target_hash = compute_digest(cand_messages)
    else:
        doc_header = f'<document id="S1" book="{book}" page="{page}">\n{text}\n</document>'
        synthesized_msg = [
            {"role": "system", "content": SYSTEM_PROMPT_TRAIN},
            {"role": "user", "content": f"<context>\n{doc_header}\n</context>\n\nCâu hỏi: {question}\nHãy trả lời chi tiết bằng tiếng Việt, dẫn mã [S1]."},
            {"role": "assistant", "content": answer}
        ]
        target_hash = compute_digest(synthesized_msg)

    # Pre-check: Reject if source text is not substantive or truncated
    is_sub, inelig_reason = is_substantive_psychology_source(text)
    if not is_sub:
        return validate_and_parse_judge_verdict(
            raw_payload={
                "supported": False,
                "answer_grounded": False,
                "unsupported_claims": [f"ineligible_source: {inelig_reason}"],
                "evidence_spans": [],
                "reason": f"Đoạn trích không đủ điều kiện thực chất ({inelig_reason}), không thể dùng làm căn cứ thẩm định."
            },
            source_text=text,
            question=question,
            target_answer=answer,
            judge_model=judge_model,
            model_digest=m_digest,
            prompt_template_sha256=prompt_tpl_hash,
            target_sha256=target_hash,
            require_citation=require_citation
        )

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        book=book,
        page=page,
        text=text,
        question=question,
        facet=facet,
        claims_json=json.dumps(claims, ensure_ascii=False),
        answer=answer
    )

    for attempt in range(max_retries):
        try:
            if sys.platform == "darwin":
                from app.model_resolver import is_mlx_model
                if not is_mlx_model(judge_model):
                    raise RuntimeError(f"Chỉ hỗ trợ mô hình MLX cục bộ trên macOS, nhận: {judge_model}")
                from app.trained_client import TrainedModelClient
                native_client = TrainedModelClient(model_name=judge_model)
                content_str = native_client.chat_complete(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=400,
                ).strip()
                done_reason = native_client.last_done_reason
            else:
                from app.ollama_client import OllamaClient
                client = OllamaClient()
                client._verify_model_locality(judge_model)
                with gpu_coordinator.acquire_for_inference():
                    resp = client.chat_sync(
                        messages=[{"role": "user", "content": prompt}],
                        model=judge_model,
                        temperature=0.0,
                        num_predict=400,
                    )
                content_str = resp.get("message", {}).get("content", "").strip()
                done_reason = resp.get("done_reason")
            return validate_and_parse_judge_verdict(
                raw_payload=content_str,
                source_text=text,
                question=question,
                target_answer=answer,
                judge_model=judge_model,
                model_digest=m_digest,
                prompt_template_sha256=prompt_tpl_hash,
                target_sha256=target_hash,
                calibration_version=CALIBRATION_PROTOCOL_VERSION,
                require_vietnamese=True,
                require_citation=require_citation,
                done_reason=done_reason
            )
        except Exception as exc:
            logger.warning("Judge call attempt %d failed for chunk %s: %s", attempt + 1, src.get("chunk_id"), exc)
            time.sleep(2 ** attempt)

    return {
        "supported": False,
        "answer_grounded": False,
        "unsupported_claims": ["judge_call_failed"],
        "evidence_spans": [],
        "reason": "judge_call_failed",
        "judge_model": judge_model,
        "model_digest": m_digest,
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "target_sha256": target_hash,
        "question_sha256": hashlib.sha256(question.strip().encode("utf-8")).hexdigest(),
        "prompt_template_sha256": prompt_tpl_hash,
        "calibration_version": CALIBRATION_PROTOCOL_VERSION,
        "verdict_digest": "",
        "raw_response": {"reason": "judge_call_failed"}
    }


# ---------------------------------------------------------------------------
# Worker Lock & State Management
# ---------------------------------------------------------------------------
class WorkerLock:
    """Kernel-managed flock to guarantee single-owner execution of curation worker."""
    def __init__(self, lock_path: Path = WORKER_LOCK_FILE):
        self.lock_path = lock_path
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = None

    def acquire(self) -> bool:
        self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            os.close(self.fd)
            self.fd = None
            return False

    def release(self):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("CHẶN KHỞI ĐỘNG: Một worker tạo dữ liệu khác đang chạy. Không chạy trùng tiến trình.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def compute_curation_plan_hash(
    sampled_sources: List[Dict[str, Any]],
    teacher_model: str,
    judge_model: str,
    teacher_prompt: str = TEACHER_PROMPT_TEMPLATE,
    judge_prompt: str = JUDGE_PROMPT_TEMPLATE,
    teacher_model_digest: Optional[str] = None,
    judge_model_digest: Optional[str] = None
) -> str:
    """Deterministic plan hash binding source content hashes, model identities & digests, and prompt revisions."""
    t_digest = teacher_model_digest or get_ollama_model_digest(teacher_model)
    j_digest = judge_model_digest or get_ollama_model_digest(judge_model)
    source_bindings = []
    for s in sampled_sources:
        s_text = s.get("text", "")
        source_bindings.append({
            "id": s.get("id"),
            "chunk_id": s.get("chunk_id"),
            "filename": s.get("filename"),
            "partition": s.get("partition"),
            "content_sha256": hashlib.sha256(s_text.encode("utf-8")).hexdigest()
        })
    payload = {
        "sources": source_bindings,
        "teacher_model": teacher_model,
        "teacher_model_digest": t_digest,
        "judge_model": judge_model,
        "judge_model_digest": j_digest,
        "teacher_prompt_sha256": hashlib.sha256(teacher_prompt.encode("utf-8")).hexdigest(),
        "judge_prompt_sha256": hashlib.sha256(judge_prompt.encode("utf-8")).hexdigest(),
        "protocol": CALIBRATION_PROTOCOL_VERSION
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def get_curation_sampled_sources(
    db: sqlite3.Connection,
    max_sources: Optional[int] = None,
    per_book_cap: int = 300
) -> List[Dict[str, Any]]:
    """Deterministically select balanced chunk sources across allowed canonical books with round-robin interleaving."""
    db.row_factory = sqlite3.Row
    allowed_books, holdout_books = get_book_partition(db)
    cursor = db.execute(
        "SELECT id, filename, book_title, page_num, chunk_index, text FROM chunks "
        "WHERE page_num > 5 AND length(text) >= 350 "
        "ORDER BY filename, page_num, chunk_index"
    )
    by_book = defaultdict(list)
    for row in cursor.fetchall():
        if row["filename"] in allowed_books:
            by_book[row["filename"]].append(dict(row))

    # Build per-book candidate lists
    per_book_sources = defaultdict(list)
    for fname in sorted(by_book.keys()):
        chunks = by_book[fname]
        stride = max(1, len(chunks) // per_book_cap)
        selected = chunks[::stride][:per_book_cap]
        for c in selected:
            safe = extract_complete_source_text(c["text"], max_chars=900)
            if is_substantive_excerpt(safe) and not is_truncated_source_text(safe):
                per_book_sources[fname].append({
                    "id": f"src-{fname[:12]}-{c['id']}",
                    "chunk_id": c["id"],
                    "filename": fname,
                    "book_title": c["book_title"],
                    "page_num": c["page_num"],
                    "text": safe,
                    "language": detect_language(safe),
                    "partition": assign_group_partition(fname, c["page_num"])
                })

    # Interleave (round-robin) across books for balanced coverage
    sampled_sources = []
    max_count = max((len(items) for items in per_book_sources.values()), default=0)
    for i in range(max_count):
        for fname in sorted(per_book_sources.keys()):
            if i < len(per_book_sources[fname]):
                sampled_sources.append(per_book_sources[fname][i])

    if max_sources:
        sampled_sources = sampled_sources[:max_sources]
    return sampled_sources


def update_pipeline_state(
    stage: str,
    status: str,
    processed: int,
    total: int,
    accepted: int,
    rejected: int,
    pid: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
    target_file: Optional[Path] = None
):
    """Atomically record continuous pipeline state to data/runtime/continuous_pipeline_state.json or target_file."""
    dest_file = target_file or PIPELINE_STATE_FILE
    dest_dir = dest_file.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "pipeline_name": "psychology_continuous_curation",
        "stage": stage,
        "status": status,
        "pid": pid,
        "processed_sources": processed,
        "total_sources": total,
        "accepted_records": accepted,
        "rejected_count": rejected,
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        state.update(extra)
    temp_file = dest_dir / f"pipeline_state_{os.getpid()}_{time.time_ns()}.tmp"
    try:
        temp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(dest_file)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Curation Workspace with Teacher & Judge
# ---------------------------------------------------------------------------
def curate_candidates_workspace(
    work_dir: Path,
    batch_size: int = 5,
    max_sources_to_curate: Optional[int] = None,
    teacher_model: str = DEFAULT_CURATION_MODEL,
    judge_model: str = DEFAULT_CURATION_MODEL
) -> Dict[str, Any]:
    """Generate real candidates via Ollama teacher & calibrated judge with checkpointing and flock serialization."""
    work_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = work_dir / "checkpoint.json"
    accepted_file = work_dir / "accepted.jsonl"
    rejected_file = work_dir / "rejected.jsonl"
    pending_file = work_dir / "pending.jsonl"
    cache_file = work_dir / "cache.json"

    # Enforce single worker owner lock via context manager
    with WorkerLock() as worker_lock:
        # Mandatory calibration gate before proceeding
        print(f"=== [GATE 0] Chạy bộ kiểm chuẩn Thẩm định viên (Judge Calibration: {judge_model}) ===")
        ensure_judge_calibrated(judge_model=judge_model)
        print("    [GATE 0] Thẩm định viên đã qua chuẩn hóa.")

        # Load cache safely (fail-closed with backup if corrupted)
        cache = {}
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception as e:
                backup_corrupted = work_dir / f"cache.corrupted.{int(time.time())}.json"
                cache_file.rename(backup_corrupted)
                logger.error("Tệp cache bị hỏng (%s), đã sao lưu sang %s và khởi tạo cache rỗng.", e, backup_corrupted.name)

        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            all_sources = get_curation_sampled_sources(db)
            if checkpoint_file.exists():
                sampled_sources = all_sources
            elif max_sources_to_curate:
                sampled_sources = all_sources[:max_sources_to_curate]
            else:
                sampled_sources = all_sources

        # Compute deterministic plan hash
        plan_digest = compute_curation_plan_hash(
            sampled_sources,
            teacher_model=teacher_model,
            judge_model=judge_model
        )

        # Load checkpoint strictly fail-closed
        checkpoint = {
            "status": "in_progress",
            "stage": "data_preparation_candidates",
            "pid": os.getpid(),
            "plan_hash": plan_digest,
            "total_sources": len(sampled_sources),
            "processed_sources": 0,
            "total_accepted": 0,
            "train_accepted": 0,
            "valid_accepted": 0,
            "rejected_count": 0,
            "pending_count": 0,
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        if checkpoint_file.exists():
            try:
                saved_chk = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            except Exception as e:
                raise RuntimeError(f"CHẶN TIẾP TỤC: Tệp checkpoint bị hỏng hoặc không đọc được JSON: {e}")

            saved_plan = saved_chk.get("plan_hash")
            if saved_plan != plan_digest:
                raise RuntimeError(
                    f"CHẶN TIẾP TỤC: Checkpoint plan_hash không khớp (saved={saved_plan} vs expected={plan_digest}). "
                    "Cần migration/rejudge có chủ đích trước khi tiếp tục chu trình."
                )
            checkpoint.update(saved_chk)
            checkpoint["pid"] = os.getpid()
            checkpoint["stage"] = "data_preparation_candidates"
            checkpoint["status"] = "in_progress"

        processed_index = checkpoint.get("processed_sources", 0)

        # Initialize synthetic curriculum if starting fresh
        seen_hashes: Set[str] = set()
        accepted_records: List[Dict[str, Any]] = []

        if accepted_file.exists():
            for line_idx, line in enumerate(accepted_file.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    try:
                        rec = json.loads(line)
                    except Exception as e:
                        raise RuntimeError(f"CHẶN KHỞI ĐỘNG: Tệp accepted.jsonl bị lỗi cú pháp tại dòng {line_idx}: {e}")
                    h = compute_digest(rec.get("messages", []))
                    seen_hashes.add(h)
                    accepted_records.append(rec)
        else:
            synthetic_curriculum = build_synthetic_curriculum()
            for ex in synthetic_curriculum:
                tgt_hash = compute_digest(ex["messages"])
                q_text = ex["messages"][1]["content"]
                q_hash = hashlib.sha256(q_text.strip().encode("utf-8")).hexdigest()
                derivation_h = compute_digest(ex["derivation"])
                prov_tpl_h = hashlib.sha256(ex["provenance"].encode()).hexdigest()
                facet_reason_h = hashlib.sha256(ex["facet"].encode()).hexdigest()
                curriculum_verdict_h = compute_review_verdict_digest(
                    source_sha256=derivation_h,
                    target_sha256=tgt_hash,
                    question_sha256=q_hash,
                    prompt_template_sha256=prov_tpl_h,
                    judge_model="canonical_derivation_registry",
                    model_digest=derivation_h,
                    reason_sha256=facet_reason_h,
                    supported=True,
                    answer_grounded=True
                )
                item = {
                    "type": "synthetic_curriculum",
                    "kind": ex["kind"],
                    "facet": ex["facet"],
                    "provenance": ex["provenance"],
                    "derivation": ex.get("derivation"),
                    "partition": "train",
                    "messages": ex["messages"],
                    "review_evidence": {
                        "source": "curriculum_grounded",
                        "status": "preverified",
                        "supported": True,
                        "answer_grounded": True,
                        "judge_model": "canonical_derivation_registry",
                        "model_digest": derivation_h,
                        "reason": f"Synthetic curriculum verified: {ex['facet']}",
                        "evidence_spans": [ex["facet"]],
                        "verdict_digest": curriculum_verdict_h,
                        "source_sha256": derivation_h,
                        "target_sha256": tgt_hash,
                        "question_sha256": q_hash,
                        "prompt_template_sha256": prov_tpl_h,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "full_verdict": {
                            "supported": True,
                            "answer_grounded": True,
                            "judge_model": "canonical_derivation_registry",
                            "model_digest": derivation_h,
                            "reason": f"Synthetic curriculum verified: {ex['facet']}",
                            "unsupported_claims": [],
                            "evidence_spans": [ex["facet"]],
                            "source_sha256": derivation_h,
                            "target_sha256": tgt_hash,
                            "question_sha256": q_hash,
                            "prompt_template_sha256": prov_tpl_h,
                            "verdict_digest": curriculum_verdict_h
                        }
                    }
                }
                h = compute_digest(item["messages"])
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    accepted_records.append(item)

            with accepted_file.open("w", encoding="utf-8") as f_acc:
                for r in accepted_records:
                    f_acc.write(json.dumps(r, ensure_ascii=False) + "\n")

        print(f"=== Bắt đầu Chu trình Curation với Teacher ({teacher_model}) & Judge ({judge_model}) ===")
        print(f"    Tổng nguồn: {len(sampled_sources)} | Chỉ số tiếp tục: {processed_index}")
        print(f"    Mẫu đã chấp nhận hiện tại: {len(accepted_records)}")

        # Signal handler for clean exit on SIGTERM/SIGINT
        interrupted = False
        def handle_signal(sig, frame):
            nonlocal interrupted
            interrupted = True
            print(f"\n[Worker] Nhận tín hiệu {sig}. Đang lưu checkpoint và dừng an toàn tại biên batch...")

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        completed_source_ids = {r.get("source_id") for r in accepted_records if r.get("source_id")}
        completed_source_hashes = set()
        for r in accepted_records:
            user_msg = next((m.get("content", "") for m in r.get("messages", []) if m.get("role") == "user"), "")
            doc_match = re.search(r'<document[^>]*>(.*?)</document>', user_msg, re.DOTALL)
            if doc_match:
                completed_source_hashes.add(hashlib.sha256(doc_match.group(1).strip().encode("utf-8")).hexdigest())

        if rejected_file.exists():
            for line in rejected_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        rej_rec = json.loads(line)
                        if rej_rec.get("source_id"):
                            completed_source_ids.add(rej_rec["source_id"])
                        if rej_rec.get("source_text"):
                            completed_source_hashes.add(hashlib.sha256(rej_rec["source_text"].strip().encode("utf-8")).hexdigest())
                    except Exception:
                        pass

        state_target_file = PIPELINE_STATE_FILE if work_dir.resolve() == (BASE_DIR / "data" / "training" / "v7_candidates").resolve() else (work_dir / "continuous_pipeline_state.json")

        # Drain and retry pending queue bounded and idempotent
        if pending_file.exists():
            pending_raw = [json.loads(l) for l in pending_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            if pending_raw:
                print(f"[Worker] Tìm thấy {len(pending_raw)} mục trong pending.jsonl. Tiến hành thẩm định lại (drain pending queue)...")
                remaining_pending = []
                for p_idx, p_item in enumerate(pending_raw):
                    p_src_id = p_item.get("source_id")
                    if p_src_id in completed_source_ids:
                        continue
                    p_src = {
                        "chunk_id": p_item.get("chunk_id"),
                        "book_title": p_item.get("book", "Tài liệu"),
                        "page_num": p_item.get("page", 1),
                        "text": p_item.get("source_text", ""),
                        "filename": p_item.get("book", "")
                    }
                    p_cand = {
                        "question": p_item.get("question", ""),
                        "answer": p_item.get("proposed_answer") or p_item.get("target", ""),
                        "claims": p_item.get("claims", [])
                    }
                    p_judge = call_judge_model(p_src, p_cand, facet="overview", judge_model=judge_model, require_citation=True)
                    if not p_judge or p_judge.get("reason") == "judge_call_failed":
                        remaining_pending.append(p_item)
                        continue

                    is_sup = bool(
                        parse_supported_verdict(p_judge.get("supported")) is True and
                        parse_supported_verdict(p_judge.get("answer_grounded", True)) is True and
                        p_judge.get("reason") != "judge_call_failed" and
                        not p_judge.get("unsupported_claims") and
                        isinstance(p_judge.get("evidence_spans"), list) and
                        len(p_judge.get("evidence_spans")) > 0
                    )
                    if is_sup:
                        cand_msg = [
                            {"role": "system", "content": SYSTEM_PROMPT_TRAIN},
                            {"role": "user", "content": f"<context>\n{p_item.get('context', '')}\n</context>\n\nCâu hỏi: {p_cand['question']}\nHãy trả lời chi tiết bằng tiếng Việt, dẫn mã [S1]."},
                            {"role": "assistant", "content": p_cand["answer"]}
                        ]
                        src_h = hashlib.sha256(p_src["text"].encode("utf-8")).hexdigest()
                        tgt_h = compute_digest(cand_msg)
                        q_h = hashlib.sha256(p_cand["question"].strip().encode("utf-8")).hexdigest()
                        prompt_tpl_h = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
                        reason_s = p_judge.get("reason", "").strip() or "Đầy đủ căn cứ từ trích đoạn"
                        j_dig = get_ollama_model_digest(judge_model)
                        verd_h = compute_review_verdict_digest(
                            source_sha256=src_h,
                            target_sha256=tgt_h,
                            question_sha256=q_h,
                            prompt_template_sha256=prompt_tpl_h,
                            judge_model=judge_model,
                            model_digest=j_dig,
                            reason_sha256=hashlib.sha256(reason_s.encode("utf-8")).hexdigest(),
                            supported=True,
                            answer_grounded=True
                        )
                        cand_entry = {
                            "source_id": p_src_id,
                            "chunk_id": p_item.get("chunk_id"),
                            "book": p_item.get("book"),
                            "page": p_item.get("page"),
                            "partition": assign_group_partition(p_item.get("book", ""), p_item.get("page", 1)),
                            "claims": p_cand["claims"],
                            "messages": cand_msg,
                            "review_evidence": {
                                "judge_model": judge_model,
                                "teacher_model": teacher_model,
                                "model_digest": j_dig,
                                "supported": True,
                                "answer_grounded": True,
                                "reason": reason_s,
                                "evidence_spans": p_judge.get("evidence_spans", []),
                                "verdict_digest": verd_h,
                                "source_sha256": src_h,
                                "target_sha256": tgt_h,
                                "question_sha256": q_h,
                                "prompt_template_sha256": prompt_tpl_h,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "full_verdict": p_judge
                            }
                        }
                        if tgt_h not in seen_hashes:
                            seen_hashes.add(tgt_h)
                            accepted_records.append(cand_entry)
                            with accepted_file.open("a", encoding="utf-8") as f_a:
                                f_a.write(json.dumps(cand_entry, ensure_ascii=False) + "\n")
                        completed_source_ids.add(p_src_id)
                    else:
                        rej_entry = {
                            "source_id": p_src_id,
                            "chunk_id": p_item.get("chunk_id"),
                            "book": p_item.get("book"),
                            "page": p_item.get("page"),
                            "source_text": p_src["text"],
                            "context": p_item.get("context"),
                            "question": p_cand["question"],
                            "claims": p_cand["claims"],
                            "target": p_cand["answer"],
                            "proposed_answer": p_cand["answer"],
                            "reason": p_judge.get("reason", "Thẩm định viên từ chối khi drain pending"),
                            "unsupported_claims": p_judge.get("unsupported_claims", []),
                            "judge_model": judge_model,
                            "full_verdict": p_judge,
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                        }
                        with rejected_file.open("a", encoding="utf-8") as f_r:
                            f_r.write(json.dumps(rej_entry, ensure_ascii=False) + "\n")
                        checkpoint["rejected_count"] = checkpoint.get("rejected_count", 0) + 1
                        completed_source_ids.add(p_src_id)

                tmp_pen = pending_file.parent / f"pending.{int(time.time())}.tmp"
                with tmp_pen.open("w", encoding="utf-8") as f_p:
                    for r_p in remaining_pending:
                        f_p.write(json.dumps(r_p, ensure_ascii=False) + "\n")
                tmp_pen.replace(pending_file)
                checkpoint["pending_count"] = len(remaining_pending)
                _write_atomic_json(checkpoint_file, checkpoint)

        curated_this_session = 0
        for idx in range(len(sampled_sources)):
            if max_sources_to_curate is not None and curated_this_session >= max_sources_to_curate:
                print(f"[Worker] Đã hoàn thành batch {curated_this_session} nguồn theo giới hạn yêu cầu.")
                checkpoint["status"] = "paused"
                checkpoint["pause_reason"] = "batch_limit_reached"
                checkpoint["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                checkpoint["processed_sources"] = len(completed_source_ids)
                _write_atomic_json(checkpoint_file, checkpoint)
                update_pipeline_state(
                    stage="curation", status="paused",
                    processed=len(completed_source_ids), total=len(sampled_sources),
                    accepted=len(accepted_records), rejected=checkpoint.get("rejected_count", 0),
                    pid=os.getpid(),
                    extra={"pause_reason": "batch_limit_reached"},
                    target_file=state_target_file
                )
                break

            pause_file = WORKER_PAUSE_FILE if work_dir == (BASE_DIR / "data" / "training" / "v7_candidates") else (work_dir / "curation_worker.pause")
            if interrupted or pause_file.exists():
                reason = "worker_pause_file" if pause_file.exists() else "signal_interrupted"
                print(f"[Worker] Dừng tại biên nguồn {idx}/{len(sampled_sources)} do yêu cầu: {reason}")
                checkpoint["status"] = "paused"
                checkpoint["pause_reason"] = reason
                checkpoint["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                checkpoint["processed_sources"] = len(completed_source_ids)
                _write_atomic_json(checkpoint_file, checkpoint)
                update_pipeline_state(
                    stage="curation", status="paused",
                    processed=len(completed_source_ids), total=len(sampled_sources),
                    accepted=len(accepted_records), rejected=checkpoint.get("rejected_count", 0),
                    pid=os.getpid(),
                    extra={"pause_reason": reason},
                    target_file=state_target_file
                )
                break

            src = sampled_sources[idx]
            src_text_hash = hashlib.sha256(src["text"].strip().encode("utf-8")).hexdigest()
            if src["id"] in completed_source_ids or src_text_hash in completed_source_hashes:
                checkpoint["processed_sources"] = len(completed_source_ids)
                continue

            curated_this_session += 1

            # Pre-check: reject non-substantive or truncated sources fail-closed before model calls
            is_sub, sub_reason = is_substantive_psychology_source(src["text"])
            if not is_sub:
                reason = f"Nguồn không đủ điều kiện thực chất ({sub_reason})"
                rejected_entry = {
                    "source_id": src["id"],
                    "chunk_id": src.get("chunk_id"),
                    "book": src["book_title"],
                    "page": src["page_num"],
                    "source_text": src["text"],
                    "context": f'<document id="S1" book="{src["book_title"]}" page="{src["page_num"]}">\n{src["text"]}\n</document>',
                    "question": None,
                    "claims": [],
                    "target": None,
                    "proposed_answer": None,
                    "reason": reason,
                    "unsupported_claims": [f"ineligible_source: {sub_reason}"],
                    "judge_model": judge_model,
                    "full_verdict": None,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                with rejected_file.open("a", encoding="utf-8") as f_rej:
                    f_rej.write(json.dumps(rejected_entry, ensure_ascii=False) + "\n")
                checkpoint["rejected_count"] = checkpoint.get("rejected_count", 0) + 1
                completed_source_ids.add(src["id"])
                completed_source_hashes.add(src_text_hash)
                checkpoint["processed_sources"] = len(completed_source_ids)
                _write_atomic_json(checkpoint_file, checkpoint)
                print(f"[{idx+1}/{len(sampled_sources)}] REJECTED ({sub_reason}): {src['id']}", flush=True)
                continue

            facet = FACET_ROTATION[idx % len(FACET_ROTATION)]
            cache_key = compute_curation_cache_key(
                source_text=src["text"],
                facet=facet,
                teacher_model=teacher_model,
                judge_model=judge_model,
                calibration_version=CALIBRATION_PROTOCOL_VERSION
            )

            teacher_res = None
            judge_res = None
            expected_judge_digest = get_ollama_model_digest(judge_model)
            expected_prompt_hash = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

            if cache_key in cache:
                cached = cache[cache_key]
                teacher_res = cached.get("teacher")
                judge_res = cached.get("judge")
                cand_tgt_h = None
                if teacher_res:
                    doc_h = f'<document id="S1" book="{src["book_title"]}" page="{src["page_num"]}">\n{src["text"]}\n</document>'
                    c_msg = [
                        {"role": "system", "content": SYSTEM_PROMPT_TRAIN},
                        {"role": "user", "content": f"<context>\n{doc_h}\n</context>\n\nCâu hỏi: {teacher_res.get('question', '')}\nHãy trả lời chi tiết bằng tiếng Việt, dẫn mã [S1]."},
                        {"role": "assistant", "content": teacher_res.get('answer', '')}
                    ]
                    cand_tgt_h = compute_digest(c_msg)

                is_cached_valid = (
                    teacher_res is not None and
                    judge_res is not None and
                    is_valid_cached_judge_verdict(
                        judge_res,
                        source_text=src["text"],
                        question=teacher_res.get("question", ""),
                        target_answer=teacher_res.get("answer", ""),
                        judge_model=judge_model,
                        expected_digest=expected_judge_digest,
                        expected_prompt_hash=expected_prompt_hash,
                        expected_target_hash=cand_tgt_h,
                        expected_calibration_version=CALIBRATION_PROTOCOL_VERSION
                    )
                )
                if not is_cached_valid:
                    if teacher_res:
                        judge_res = call_judge_model(src, teacher_res, facet=facet, judge_model=judge_model, require_citation=True)
                        cache[cache_key] = {"teacher": teacher_res, "judge": judge_res}
                        _write_atomic_json(cache_file, cache)
                    else:
                        teacher_res = call_teacher_model(src, facet=facet, teacher_model=teacher_model)
                        if teacher_res:
                            judge_res = call_judge_model(src, teacher_res, facet=facet, judge_model=judge_model, require_citation=True)
                            cache[cache_key] = {"teacher": teacher_res, "judge": judge_res}
                            _write_atomic_json(cache_file, cache)
            else:
                teacher_res = call_teacher_model(src, facet=facet, teacher_model=teacher_model)
                if teacher_res:
                    judge_res = call_judge_model(src, teacher_res, facet=facet, judge_model=judge_model, require_citation=True)
                    cache[cache_key] = {"teacher": teacher_res, "judge": judge_res}
                    _write_atomic_json(cache_file, cache)

            doc_header = f'<document id="S1" book="{src["book_title"]}" page="{src["page_num"]}">\n{src["text"]}\n</document>'

            # Reviewer error check: do NOT permanently reject; record as pending for retry
            if teacher_res and (judge_res is None or judge_res.get("reason") == "judge_call_failed"):
                pending_entry = {
                    "source_id": src["id"],
                    "chunk_id": src.get("chunk_id"),
                    "book": src["book_title"],
                    "page": src["page_num"],
                    "source_text": src["text"],
                    "context": doc_header,
                    "question": teacher_res.get("question"),
                    "claims": teacher_res.get("claims"),
                    "target": teacher_res.get("answer"),
                    "proposed_answer": teacher_res.get("answer"),
                    "reason": "judge_call_failed",
                    "unsupported_claims": [],
                    "judge_model": judge_model,
                    "full_verdict": judge_res,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                with pending_file.open("a", encoding="utf-8") as f_pen:
                    f_pen.write(json.dumps(pending_entry, ensure_ascii=False) + "\n")
                checkpoint["pending_count"] = checkpoint.get("pending_count", 0) + 1
                checkpoint["processed_sources"] = idx + 1
                _write_atomic_json(checkpoint_file, checkpoint)
                print(f"[{idx+1}/{len(sampled_sources)}] PENDING {src['id']} -> Reviewer error, queued in pending.jsonl", flush=True)
                continue

            is_supported = bool(
                teacher_res and
                judge_res and
                parse_supported_verdict(judge_res.get("supported")) is True and
                parse_supported_verdict(judge_res.get("answer_grounded", True)) is True and
                judge_res.get("reason") != "judge_call_failed" and
                not judge_res.get("unsupported_claims") and
                isinstance(judge_res.get("evidence_spans"), list) and
                len(judge_res.get("evidence_spans")) > 0
            )
            if is_supported:
                cand_messages = [
                    {"role": "system", "content": SYSTEM_PROMPT_TRAIN},
                    {"role": "user", "content": f"<context>\n{doc_header}\n</context>\n\nCâu hỏi: {teacher_res['question']}\nHãy trả lời chi tiết bằng tiếng Việt, dẫn mã [S1]."},
                    {"role": "assistant", "content": teacher_res["answer"]}
                ]
                src_hash = hashlib.sha256(src["text"].encode("utf-8")).hexdigest()
                tgt_hash = compute_digest(cand_messages)
                q_text = teacher_res['question'].strip()
                q_hash = hashlib.sha256(q_text.encode("utf-8")).hexdigest()
                prompt_tpl_hash = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
                reason_str = judge_res.get("reason", "").strip() or "Đầy đủ căn cứ từ trích đoạn"
                j_digest = get_ollama_model_digest(judge_model)
                verdict_hash = compute_review_verdict_digest(
                    source_sha256=src_hash,
                    target_sha256=tgt_hash,
                    question_sha256=q_hash,
                    prompt_template_sha256=prompt_tpl_hash,
                    judge_model=judge_model,
                    model_digest=j_digest,
                    reason_sha256=hashlib.sha256(reason_str.encode("utf-8")).hexdigest(),
                    supported=True,
                    answer_grounded=True
                )
                cand = {
                    "source_id": src.get("id", f"src-{src.get('chunk_id')}"),
                    "chunk_id": src.get("chunk_id", src.get("id")),
                    "book": src.get("book_title", src.get("filename", "")),
                    "page": src.get("page_num", 1),
                    "partition": src.get("partition", assign_group_partition(src.get("filename", ""), src.get("page_num", 1))),
                    "claims": teacher_res.get("claims", []),
                    "messages": cand_messages,
                    "review_evidence": {
                        "judge_model": judge_model,
                        "teacher_model": teacher_model,
                        "model_digest": j_digest,
                        "supported": True,
                        "answer_grounded": True,
                        "reason": reason_str,
                        "evidence_spans": judge_res.get("evidence_spans", []),
                        "verdict_digest": verdict_hash,
                        "source_sha256": src_hash,
                        "target_sha256": tgt_hash,
                        "question_sha256": q_hash,
                        "prompt_template_sha256": prompt_tpl_hash,
                        "calibration_version": CALIBRATION_PROTOCOL_VERSION,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "full_verdict": judge_res
                    }
                }
                h = compute_digest(cand["messages"])
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    accepted_records.append(cand)
                    with accepted_file.open("a", encoding="utf-8") as f_acc:
                        f_acc.write(json.dumps(cand, ensure_ascii=False) + "\n")
            else:
                reason = judge_res.get("reason", "teacher_failed") if judge_res else "teacher_failed"
                checkpoint["rejected_count"] = checkpoint.get("rejected_count", 0) + 1
                rejected_entry = {
                    "source_id": src["id"],
                    "chunk_id": src.get("chunk_id"),
                    "book": src["book_title"],
                    "page": src["page_num"],
                    "source_text": src["text"],
                    "context": doc_header,
                    "question": teacher_res.get("question") if teacher_res else None,
                    "claims": teacher_res.get("claims") if teacher_res else None,
                    "target": teacher_res.get("answer") if teacher_res else None,
                    "proposed_answer": teacher_res.get("answer") if teacher_res else None,
                    "reason": reason,
                    "unsupported_claims": judge_res.get("unsupported_claims", []) if judge_res else [],
                    "judge_model": judge_model,
                    "full_verdict": judge_res,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                with rejected_file.open("a", encoding="utf-8") as f_rej:
                    f_rej.write(json.dumps(rejected_entry, ensure_ascii=False) + "\n")

            completed_source_ids.add(src["id"])
            completed_source_hashes.add(src_text_hash)
            checkpoint["processed_sources"] = len(completed_source_ids)
            train_count = sum(1 for r in accepted_records if r.get("partition") == "train")
            valid_count = sum(1 for r in accepted_records if r.get("partition") == "valid")
            checkpoint.update({
                "total_accepted": len(accepted_records),
                "train_accepted": train_count,
                "valid_accepted": valid_count,
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            })
            _write_atomic_json(checkpoint_file, checkpoint)
            update_pipeline_state(
                stage="curation", status="in_progress",
                processed=idx + 1, total=len(sampled_sources),
                accepted=len(accepted_records), rejected=checkpoint["rejected_count"],
                pid=os.getpid(),
                target_file=state_target_file
            )
            status_tag = "ACCEPTED" if is_supported else "REJECTED"
            print(f"[{idx+1}/{len(sampled_sources)}] {status_tag} {src['id']} ({src['book_title'][:25]}, P.{src['page_num']}) -> acc={len(accepted_records)} (train={train_count}, val={valid_count}), rej={checkpoint['rejected_count']}", flush=True)

        if checkpoint["processed_sources"] >= len(sampled_sources):
            checkpoint["status"] = "completed"
            _write_atomic_json(checkpoint_file, checkpoint)
            update_pipeline_state(
                stage="curation", status="completed",
                processed=len(sampled_sources), total=len(sampled_sources),
                accepted=len(accepted_records), rejected=checkpoint["rejected_count"],
                pid=os.getpid(),
                target_file=state_target_file
            )

    return checkpoint


def _write_atomic_json(path: Path, data: Any):
    """Write JSON atomically via temporary file and atomic replace."""
    tmp = path.parent / f"{path.name}.{os.getpid()}_{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Strict Release Gates & Immutable Publish
# ---------------------------------------------------------------------------
def publish_v7_dataset(
    work_dir: Path,
    publish_dir: Path,
    min_train: int = 2048,
    min_valid: int = 200,
    min_groups: int = 400
) -> Dict[str, Any]:
    """Publish curated dataset via immutable staging, strictly validating all release gates."""
    # Gate 0: Overwrite Protection
    if publish_dir.exists():
        existing_manifest = publish_dir / "approved_manifest.jsonl"
        existing_approval = publish_dir / "approval.json"
        if existing_manifest.exists() or existing_approval.exists():
            raise FileExistsError(
                f"CHẶN XUẤT BẢN: Thư mục đích {publish_dir} đã chứa bộ dữ liệu đã phê duyệt. "
                "Không được ghi đè bản phát hành bất biến. Hãy sử dụng thư mục phiên bản mới."
            )

    accepted_file = work_dir / "accepted.jsonl"
    if not accepted_file.exists():
        raise FileNotFoundError(f"Missing {accepted_file}. Run curation first.")

    accepted_records = [json.loads(line) for line in accepted_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    train_samples = [r for r in accepted_records if r.get("partition") == "train"]
    valid_samples = [r for r in accepted_records if r.get("partition") == "valid"]

    violations = []

    # Gate 0.5: On-disk Judge Calibration Verification
    calib_file = BASE_DIR / "data" / "evaluation" / "judge-calibration-2026-10-02.json"
    if not calib_file.exists():
        calib_candidates = list((BASE_DIR / "data" / "evaluation").glob("judge-calibration-*.json"))
        if calib_candidates:
            calib_file = max(calib_candidates, key=lambda p: p.stat().st_mtime)
    if not calib_file.exists():
        violations.append("CHẶN XUẤT BẢN: Thiếu artifact kiểm chuẩn hiệu chuẩn (calibration artifact) trên đĩa.")
    else:
        try:
            calib_data = json.loads(calib_file.read_text(encoding="utf-8"))
            if calib_data.get("calibration_protocol") != CALIBRATION_PROTOCOL_VERSION:
                violations.append(f"CHẶN XUẤT BẢN: Calibration artifact trên đĩa có protocol không khớp ({calib_data.get('calibration_protocol')} vs {CALIBRATION_PROTOCOL_VERSION}).")
            if not calib_data.get("all_passed"):
                violations.append("CHẶN XUẤT BẢN: Calibration artifact trên đĩa ghi nhận all_passed=False.")
        except Exception as e:
            violations.append(f"CHẶN XUẤT BẢN: Không thể đọc calibration artifact trên đĩa: {e}")

    # Gate 1: Quantity requirements
    if len(train_samples) < min_train:
        violations.append(f"Chưa đủ mẫu học train: có {len(train_samples)}/{min_train} mẫu.")
    if len(valid_samples) < min_valid:
        violations.append(f"Chưa đủ mẫu kiểm định valid: có {len(valid_samples)}/{min_valid} mẫu.")

    # Gate 2: Book-chapter group diversity & Train-Valid Overlap Protection
    groups_train = {f"{r.get('book')}:{r.get('page', 0) // 15}" for r in train_samples if r.get('book')}
    groups_valid = {f"{r.get('book')}:{r.get('page', 0) // 15}" for r in valid_samples if r.get('book')}
    total_groups = len(groups_train | groups_valid)
    if total_groups < min_groups:
        violations.append(f"Chưa đủ độ đa dạng nhóm chương (group diversity): có {total_groups}/{min_groups} nhóm.")
    overlap_groups = groups_train & groups_valid
    if overlap_groups:
        violations.append(f"CHẶN XUẤT BẢN: Phát hiện rò rỉ nhóm giữa train và valid (source-group overlap): {sorted(list(overlap_groups))}")

    # Connect to database for strict source resolution
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        allowed_books, holdout_aliases = get_book_partition(db)
        docs = list(db.execute("SELECT filename, file_hash FROM documents WHERE status='indexed'"))
        holdout_hashes = {r["file_hash"] for r in docs if r["filename"] in FROZEN_HOLDOUT_BOOKS}

        # Index all database chunks for O(1) content verification
        chunk_lookup = {}
        for r in db.execute(
            "SELECT c.id, c.text, c.filename, c.book_title, c.page_num, d.file_hash "
            "FROM chunks c JOIN documents d ON c.doc_id = d.id"
        ):
            chunk_lookup[r["id"]] = dict(r)

    # Gates 3, 4, 5: Provenance, Tamper, Holdout, and Strict Review Verification
    seen_messages_hashes = set()
    seen_assistant_hashes = set()

    for r in accepted_records:
        rec_type = r.get("type")
        source_id = r.get("source_id", "")
        ev = r.get("review_evidence")

        # Duplicate full message target check
        m_hash = compute_digest(r.get("messages", []))
        if m_hash in seen_messages_hashes:
            violations.append(f"Phát hiện trùng lặp target messages trong tập dữ liệu: {source_id}")
        seen_messages_hashes.add(m_hash)

        # Duplicate assistant answer check (different prompt, identical answer)
        assistant_content = "".join(
            m.get("content", "").strip() for m in r.get("messages", []) if m.get("role") == "assistant"
        ).strip()
        if assistant_content:
            a_hash = hashlib.sha256(assistant_content.encode("utf-8")).hexdigest()
            if a_hash in seen_assistant_hashes:
                violations.append(f"Phát hiện câu trả lời trùng lặp với câu hỏi khác (duplicate assistant answer): {source_id}")
            seen_assistant_hashes.add(a_hash)

        # Strict Boolean Schema Check
        if not ev or not isinstance(ev, dict):
            violations.append(f"CHẶN XUẤT BẢN: Thiếu đối tượng review_evidence hợp lệ cho {source_id}.")
            continue

        sup = ev.get("supported")
        grd = ev.get("answer_grounded")
        if not (isinstance(sup, bool) and sup is True):
            violations.append(f"CHẶN XUẤT BẢN: review_evidence.supported không phải boolean True hợp lệ: {source_id}")
            continue
        if not (isinstance(grd, bool) and grd is True):
            violations.append(f"CHẶN XUẤT BẢN: review_evidence.answer_grounded không phải boolean True hợp lệ: {source_id}")
            continue

        # Strict Synthetic Verification against Canonical Registry
        if rec_type in ("synthetic_curriculum", "synthetic"):
            canonical_reg = get_synthetic_registry()
            canonical_item = canonical_reg.get(m_hash)
            if canonical_item is None:
                violations.append(f"CHẶN XUẤT BẢN: Mẫu gắn nhãn synthetic không tồn tại trong canonical registry: {source_id}")
                continue
            if r.get("derivation") != canonical_item.get("derivation"):
                violations.append(f"CHẶN XUẤT BẢN: Mẫu synthetic có derivation không khớp registry chuẩn: {source_id}")
                continue
            if r.get("kind") != canonical_item.get("kind") or r.get("facet") != canonical_item.get("facet"):
                violations.append(f"CHẶN XUẤT BẢN: Mẫu synthetic có kind/facet không khớp registry chuẩn: {source_id}")
                continue
            continue
        elif rec_type and rec_type not in ("extracted_candidate", "text_extraction"):
            violations.append(f"CHẶN XUẤT BẢN: Loại bản ghi không được hỗ trợ (arbitrary type marker): {rec_type}")
            continue

        # For extracted records: Resolve chunk ID from database
        chunk_id = r.get("chunk_id")
        if chunk_id is None and "-" in source_id:
            try:
                chunk_id = int(source_id.split("-")[-1])
            except ValueError:
                chunk_id = None

        if chunk_id is None or chunk_id not in chunk_lookup:
            violations.append(f"CHẶN XUẤT BẢN: Nguồn {source_id} không tồn tại trong cơ sở dữ liệu (unbound source).")
            continue

        db_chunk = chunk_lookup[chunk_id]

        # Holdout check: check DB filename and file_hash directly!
        if (db_chunk["filename"] in FROZEN_HOLDOUT_BOOKS or
            db_chunk["filename"] in holdout_aliases or
            db_chunk["file_hash"] in holdout_hashes):
            violations.append(f"RÒ RỈ HOLDOUT: {source_id} thuộc sách holdout {db_chunk['filename']} ({db_chunk['file_hash']}).")

        # Database index consistency checks (book, page, partition)
        if r.get("book") and r.get("book") not in (db_chunk["filename"], db_chunk["book_title"]):
            violations.append(f"CHẶN XUẤT BẢN: Tên sách không khớp cơ sở dữ liệu ({r.get('book')} vs {db_chunk['filename']}): {source_id}")
        if r.get("page") is not None and r.get("page") != db_chunk["page_num"]:
            violations.append(f"CHẶN XUẤT BẢN: Số trang không khớp cơ sở dữ liệu ({r.get('page')} vs {db_chunk['page_num']}): {source_id}")
        expected_partition = assign_group_partition(db_chunk["filename"], db_chunk["page_num"])
        if r.get("partition") and r.get("partition") != expected_partition:
            violations.append(f"CHẶN XUẤT BẢN: Phân vùng partition không khớp phân bổ nhóm ({r.get('partition')} vs {expected_partition}): {source_id}")

        db_safe_text = extract_complete_source_text(db_chunk["text"], max_chars=900)
        legacy_db_text = sanitize_for_prompt_context(db_chunk["text"][:750]).strip()
        user_msg = next((m.get("content", "") for m in r.get("messages", []) if m.get("role") == "user"), "")
        doc_match = re.search(r'<document[^>]*>(.*?)</document>', user_msg, re.DOTALL)
        doc_text = doc_match.group(1).strip() if doc_match else ""
        if doc_text != db_safe_text and doc_text != legacy_db_text:
            violations.append(f"CHẶN XUẤT BẢN: Ngữ cảnh trích dẫn không khớp nội dung văn bản gốc trong DB: {source_id}")

        # Source content-hash check
        expected_src_hash = hashlib.sha256(doc_text.encode("utf-8")).hexdigest() if doc_text else hashlib.sha256(db_safe_text.encode("utf-8")).hexdigest()
        recorded_src_hash = ev.get("source_sha256")
        if not recorded_src_hash or recorded_src_hash != expected_src_hash:
            violations.append(f"CHẶN XUẤT BẢN: Source hash không khớp hoặc thiếu review proof cho nguồn {source_id}.")

        # Tampered target check: compare current messages digest to review_evidence target_sha256
        recorded_tgt_hash = ev.get("target_sha256")
        if not recorded_tgt_hash or recorded_tgt_hash != m_hash:
            violations.append(f"CHẶN XUẤT BẢN: Target messages đã bị sửa đổi sau thẩm định (tampered target: {source_id}).")

        # Judge model and prompt template verification
        j_model = ev.get("judge_model", "")
        if not j_model:
            violations.append(f"CHẶN XUẤT BẢN: Thiếu judge_model trong review_evidence cho {source_id}.")
            continue

        expected_prompt_hash = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
        if ev.get("prompt_template_sha256") != expected_prompt_hash:
            violations.append(f"CHẶN XUẤT BẢN: Prompt template hash không khớp phiên bản chuẩn hóa cho {source_id}.")

        reason = ev.get("reason", "").strip()
        if len(reason) < 10:
            violations.append(f"CHẶN XUẤT BẢN: Lý giải thẩm định (reason) không đủ hoặc bị thiếu cho {source_id}.")

        q_match = re.search(r'Câu hỏi:\s*(.*?)(?:\nHãy trả lời|\Z)', user_msg, re.DOTALL)
        q_text = q_match.group(1).strip() if q_match else user_msg.strip()
        q_hash = hashlib.sha256(q_text.encode("utf-8")).hexdigest()

        try:
            expected_m_digest = get_ollama_model_digest(j_model)
        except Exception as e:
            violations.append(f"CHẶN XUẤT BẢN: Không thể lấy model digest cho {j_model} ({e}): {source_id}.")
            continue

        m_digest = ev.get("model_digest")
        if not m_digest or m_digest != expected_m_digest:
            violations.append(f"CHẶN XUẤT BẢN: Model digest không khớp bản phát hành thực cho {source_id}.")

        # Gate: Strict full_verdict verification
        full_verdict = ev.get("full_verdict")
        if not full_verdict or not isinstance(full_verdict, dict):
            violations.append(f"CHẶN XUẤT BẢN: Thiếu hoặc sai định dạng full_verdict trong review_evidence cho {source_id}.")
            continue
        fv_sup = full_verdict.get("supported")
        fv_grd = full_verdict.get("answer_grounded")
        if not (isinstance(fv_sup, bool) and fv_sup is True):
            violations.append(f"CHẶN XUẤT BẢN: full_verdict.supported không phải boolean True hợp lệ cho {source_id}.")
            continue
        if not (isinstance(fv_grd, bool) and fv_grd is True):
            violations.append(f"CHẶN XUẤT BẢN: full_verdict.answer_grounded không phải boolean True hợp lệ cho {source_id}.")
            continue
        fv_model = full_verdict.get("judge_model")
        if not fv_model or fv_model != j_model:
            violations.append(f"CHẶN XUẤT BẢN: judge_model trong full_verdict ({fv_model}) không khớp wrapper ({j_model}) cho {source_id}.")
            continue
        if full_verdict.get("reason") == "judge_call_failed":
            violations.append(f"CHẶN XUẤT BẢN: full_verdict chứa lỗi reviewer thất bại cho {source_id}.")
            continue
        if full_verdict.get("unsupported_claims"):
            violations.append(f"CHẶN XUẤT BẢN: full_verdict chứa unsupported_claims không rỗng cho {source_id}: {full_verdict.get('unsupported_claims')}")
            continue

        # Probe 5 check: raw reason must match bound wrapper reason
        fv_reason = full_verdict.get("reason", "").strip()
        if not fv_reason or fv_reason != reason:
            violations.append(f"CHẶN XUẤT BẢN: Lý giải (reason) trong full_verdict không khớp review_evidence cho {source_id}.")

        # Probe 1 & 2 checks: full_verdict raw bindings must match expected hashes
        fv_src_hash = full_verdict.get("source_sha256")
        if not fv_src_hash or fv_src_hash != expected_src_hash:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict source_sha256 không khớp hoặc bị thiếu cho {source_id}.")

        fv_tgt_hash = full_verdict.get("target_sha256")
        if not fv_tgt_hash or fv_tgt_hash != m_hash:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict target_sha256 không khớp messages digest cho {source_id}.")

        fv_q_hash = full_verdict.get("question_sha256")
        if not fv_q_hash or fv_q_hash != q_hash:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict question_sha256 không khớp câu hỏi candidate cho {source_id}.")

        fv_prompt_hash = full_verdict.get("prompt_template_sha256")
        if not fv_prompt_hash or fv_prompt_hash != expected_prompt_hash:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict prompt_template_sha256 không khớp chuẩn cho {source_id}.")

        # Probe 3 check: raw model digest must match expected
        fv_digest = full_verdict.get("model_digest")
        if not fv_digest or fv_digest != expected_m_digest:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict model_digest ({fv_digest}) không khớp mô hình thực ({expected_m_digest}) cho {source_id}.")

        # Gate: Substantive psychology source check
        is_sub, sub_reason = is_substantive_psychology_source(doc_text)
        if not is_sub:
            violations.append(f"CHẶN XUẤT BẢN: Nguồn không đủ tính thực chất ({sub_reason}) cho {source_id}.")

        # Probe 1 & 4 checks: evidence_spans must exist and appear in source
        fv_spans = full_verdict.get("evidence_spans")
        if not isinstance(fv_spans, list) or len(fv_spans) == 0:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict thiếu evidence_spans hoặc danh sách rỗng cho {source_id}.")
        else:
            for s in fv_spans:
                s_str = s.get("text") if isinstance(s, dict) else str(s)
                s_str = s_str.strip()
                if not s_str:
                    violations.append(f"CHẶN XUẤT BẢN: evidence_spans chứa phần tử rỗng cho {source_id}.")
                elif (s_str not in doc_text and s_str not in db_safe_text and
                      resolve_exact_source_span_offsets(doc_text, s_str) is None and
                      resolve_exact_source_span_offsets(db_safe_text, s_str) is None):
                    violations.append(f"CHẶN XUẤT BẢN: evidence_span '{s_str[:50]}' không tồn tại nguyên văn trong nguồn ({source_id}).")

        # Gate: Strict nested raw_response validation
        raw_resp = full_verdict.get("raw_response")
        if raw_resp is not None:
            if isinstance(raw_resp, str):
                try:
                    raw_resp = json.loads(raw_resp)
                except Exception as e:
                    violations.append(f"CHẶN XUẤT BẢN: full_verdict raw_response lỗi phân tích cú pháp JSON ({e}) cho {source_id}.")
                    raw_resp = None
            if isinstance(raw_resp, dict):
                r_sup = parse_supported_verdict(raw_resp.get("supported"))
                r_grd = parse_supported_verdict(raw_resp.get("answer_grounded", r_sup))
                if not (r_sup is True and r_grd is True):
                    violations.append(f"CHẶN XUẤT BẢN: nested full_verdict.raw_response supported=False trái với envelope positive cho {source_id}.")
                if raw_resp.get("unsupported_claims"):
                    violations.append(f"CHẶN XUẤT BẢN: nested full_verdict.raw_response chứa unsupported_claims không rỗng cho {source_id}: {raw_resp.get('unsupported_claims')}")
                if raw_resp.get("reason") == "judge_call_failed":
                    violations.append(f"CHẶN XUẤT BẢN: nested full_verdict.raw_response chứa lỗi reviewer thất bại cho {source_id}.")
                raw_spans = raw_resp.get("evidence_spans")
                if raw_spans is not None:
                    if not isinstance(raw_spans, list) or len(raw_spans) == 0:
                        violations.append(f"CHẶN XUẤT BẢN: nested full_verdict.raw_response thiếu evidence_spans cho {source_id}.")
                    else:
                        for s in raw_spans:
                            s_str = s.get("text") if isinstance(s, dict) else str(s)
                            s_str = s_str.strip()
                            if not s_str or (s_str not in doc_text and s_str not in db_safe_text and
                                             resolve_exact_source_span_offsets(doc_text, s_str) is None and
                                             resolve_exact_source_span_offsets(db_safe_text, s_str) is None):
                                violations.append(f"CHẶN XUẤT BẢN: nested full_verdict.raw_response evidence_span '{s_str[:50]}' không tồn tại nguyên văn trong nguồn ({source_id}).")
            else:
                violations.append(f"CHẶN XUẤT BẢN: full_verdict raw_response không phải dict hợp lệ cho {source_id}.")

        # Gate: Calibration Protocol Version Verification
        calib_ver = ev.get("calibration_version")
        fv_calib_ver = full_verdict.get("calibration_version")
        if not calib_ver or calib_ver != CALIBRATION_PROTOCOL_VERSION:
            violations.append(f"CHẶN XUẤT BẢN: calibration_version không khớp hoặc bị thiếu ({calib_ver} vs {CALIBRATION_PROTOCOL_VERSION}) cho {source_id}.")
        if not fv_calib_ver or fv_calib_ver != CALIBRATION_PROTOCOL_VERSION:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict calibration_version không khớp hoặc bị thiếu ({fv_calib_ver} vs {CALIBRATION_PROTOCOL_VERSION}) cho {source_id}.")

        # Instruction-following language constraint: reject English answer when Vietnamese requested
        if "tiếng việt" in user_msg.lower():
            vi_chars = len(VI_CHARS.findall(assistant_content))
            words = re.findall(r"[A-Za-z]+", assistant_content.lower())
            en_hits = sum(w in {"the", "and", "of", "to", "that", "with", "for", "is", "in", "by", "from", "on"} for w in words)
            if len(assistant_content.strip()) >= 30 and (vi_chars < 3 and (en_hits >= 2 or detect_language(assistant_content) == "en")):
                violations.append(f"CHẶN XUẤT BẢN: Câu trả lời vi phạm ngôn ngữ yêu cầu (tiếng Anh thay vì tiếng Việt): {source_id}")

        # Instruction-following citation check for book-extracted records
        if rec_type not in ("synthetic_curriculum", "synthetic"):
            if "[S1]" not in assistant_content:
                violations.append(f"CHẶN XUẤT BẢN: Câu trả lời thiếu dẫn chứng mã tài liệu [S1] cho {source_id}.")

        expected_verdict_digest = compute_review_verdict_digest(
            source_sha256=expected_src_hash,
            target_sha256=m_hash,
            question_sha256=q_hash,
            prompt_template_sha256=expected_prompt_hash,
            judge_model=j_model,
            model_digest=expected_m_digest,
            reason_sha256=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            supported=True,
            answer_grounded=True
        )

        if ev.get("verdict_digest") != expected_verdict_digest:
            violations.append(f"CHẶN XUẤT BẢN: Verdict digest không khớp dữ liệu thẩm định bị ràng buộc (fabricated review proof: {source_id}).")
        if full_verdict.get("verdict_digest") and full_verdict.get("verdict_digest") != expected_verdict_digest:
            violations.append(f"CHẶN XUẤT BẢN: full_verdict verdict_digest không khớp expected cho {source_id}.")

    if violations:
        raise ValueError("CHẶN XUẤT BẢN: Cổng nghiệm thu chất lượng dữ liệu chưa đạt:\n- " + "\n- ".join(violations))

    # Stage through isolated staging directory
    staging_dir = publish_dir.parent / f"{publish_dir.name}_staging_{int(time.time())}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    train_path = staging_dir / "train.jsonl"
    valid_path = staging_dir / "valid.jsonl"
    manifest_path = staging_dir / "approved_manifest.jsonl"
    summary_path = staging_dir / "summary.json"
    approval_path = staging_dir / "approval.json"

    train_bytes = "".join(json.dumps({"messages": s["messages"]}, ensure_ascii=False) + "\n" for s in train_samples).encode("utf-8")
    valid_bytes = "".join(json.dumps({"messages": s["messages"]}, ensure_ascii=False) + "\n" for s in valid_samples).encode("utf-8")

    train_path.write_bytes(train_bytes)
    valid_path.write_bytes(valid_bytes)

    # Gate 6: Context token audit
    audit = audit_context_truncation(staging_dir, max_seq_length=1024)
    if not audit.get("acceptable"):
        for p in staging_dir.glob("*"):
            p.unlink()
        staging_dir.rmdir()
        raise ValueError(f"CHẶN XUẤT BẢN: Kiểm định token cắt ngắn thất bại: {audit}")

    # Build manifest binding every sample with hash and evidence
    manifest_entries = []
    for s in accepted_records:
        manifest_entries.append({
            "source_id": s.get("source_id", "synthetic"),
            "book": s.get("book", "synthetic_curriculum"),
            "partition": s.get("partition", "train"),
            "messages_sha256": compute_digest(s.get("messages", [])),
            "review_digest": compute_digest(s.get("review_evidence", {}))
        })
    manifest_bytes = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest_entries).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()

    dataset_digest = hashlib.sha256(train_bytes + valid_bytes).hexdigest()
    summary_data = {
        "dataset_version": publish_dir.name,
        "train_examples": len(train_samples),
        "valid_examples": len(valid_samples),
        "unique_groups": total_groups,
        "dataset_sha256": dataset_digest,
        "manifest_sha256": manifest_digest,
        "token_audit": audit,
        "audit_complete": True,
        "holdout_leakage_clean": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

    approval_data = {
        "dataset_version": publish_dir.name,
        "dataset_sha256": dataset_digest,
        "approved_manifest_sha256": manifest_digest,
        "audit_complete": True,
        "holdout_books_strictly_preserved": sorted(list(FROZEN_HOLDOUT_BOOKS)),
        "codex_verification_pending": True,
        "autoapproved": False,
        "note": "Phát hành cần Codex kiểm định độc lập trước khi mở chat trial MLX mới."
    }
    approval_path.write_text(json.dumps(approval_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Atomic move to publish directory
    staging_dir.rename(publish_dir)
    print(f"=== Đã xuất bản thành công bộ dữ liệu {publish_dir.name} ===")
    print(f"    Train: {len(train_samples)} | Valid: {len(valid_samples)} | Groups: {total_groups}")
    print(f"    Dataset SHA256: {dataset_digest}")
    print(f"    Manifest SHA256: {manifest_digest}")
    return summary_data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Curate Dataset v7 with real local teacher & judge")
    parser.add_argument("--work-dir", type=Path, default=BASE_DIR / "data" / "training" / "v7_candidates")
    parser.add_argument("--publish-dir", type=Path, default=BASE_DIR / "data" / "training" / "v7")
    parser.add_argument("--calibrate", action="store_true", help="Run judge calibration suite")
    parser.add_argument("--publish", action="store_true", help="Attempt gated publish")
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--judge-model", type=str, default=DEFAULT_CURATION_MODEL)
    parser.add_argument("--teacher-model", type=str, default=DEFAULT_CURATION_MODEL)
    args = parser.parse_args()

    if args.calibrate:
        res = run_judge_calibration(judge_model=args.judge_model)
        calib_out = BASE_DIR / "data" / "evaluation" / "judge-calibration-2026-10-02.json"
        calib_out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Calibration result saved to: {calib_out}")
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.publish:
        publish_v7_dataset(args.work_dir, args.publish_dir)
    else:
        curate_candidates_workspace(
            args.work_dir,
            max_sources_to_curate=args.max_sources,
            teacher_model=args.teacher_model,
            judge_model=args.judge_model
        )
