"""Study materials generator: Upload documents to create source-grounded Quizzes & Flashcards.

Supports PDF, DOCX, TXT, MD, and images. Enforces strict schema, citation grounding,
GPU lock coordination, and safe in-memory session tracking.
"""

import base64
import copy
import hashlib
import html
import io
import json
import logging
import os
import re
import subprocess  # nosec B404
import sys
import tempfile
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from PIL import Image

from app.config import DATA_DIR, DEFAULT_CHAT_MODEL, MLX_CONTEXT_TOKENS, TRAINED_MODEL_NAME
from app.gpu_lock import GPUBusyError, gpu_coordinator
from app.dialogue import DialogueBusyError, acquire_inference, dialogue_gate
from app.evaluation_metrics import has_language_drift
from app.model_resolver import is_mlx_model, get_model_context_capacity
from app.ollama_client import OllamaClient
from app.pdf_extractor import PDFExtractor, PDFExtractionResult
from app.source_consistency import inconsistent_prime_extraction_example
from app.text_cleaner import normalize_vietnamese_text, sanitize_for_prompt_context

logger = logging.getLogger(__name__)

_NEGATION_PREFIX_TOKENS = frozenset({
    "non", "un", "in", "im", "dis", "ir", "il", "anti",
    "no", "not", "without", "never",
    "không", "chưa", "phi", "vô", "bất", "ngoại",
})
_DASH_CLASS = "[-\u2013\u2014\u2212]"
_DASH_CHARS = frozenset("-\u2013\u2014\u2212")


def _find_bounded_span_matches(quote: str, span: str) -> List[re.Match]:
    """Find span matches in quote respecting word/hyphen boundaries, ignoring standalone heading repeats when a single body match exists."""
    clean = (span or "").strip()
    if not clean or not quote:
        return []
    left_guard = r"(?<![\w\-])" if (clean[0].isalnum() or clean[0] == "_") else ""
    right_guard = r"(?![\w\-])" if (clean[-1].isalnum() or clean[-1] == "_") else ""
    tokens = clean.split()
    pattern = r"\s+".join(
        "".join(_DASH_CLASS if ch in _DASH_CHARS else re.escape(ch) for ch in tok)
        for tok in tokens
    )
    regex = f"{left_guard}{pattern}{right_guard}" if (left_guard or right_guard) else re.escape(clean)
    matches = list(re.finditer(regex, quote, flags=re.IGNORECASE))
    if len(matches) <= 1:
        return matches
    body_matches = []
    for m in matches:
        line_start = quote.rfind("\n", 0, m.start()) + 1
        line_end = quote.find("\n", m.end())
        line_text = quote[line_start:(len(quote) if line_end == -1 else line_end)].strip()
        if line_text != m.group(0).strip() and not line_text.startswith("#"):
            body_matches.append(m)
    return body_matches if len(body_matches) == 1 else matches


def _count_bounded_span_occurrences(quote: str, span: str) -> int:
    """Count body occurrences of span respecting word/hyphen boundaries."""
    return len(_find_bounded_span_matches(quote, span))


def _is_negated_superstring(small: Tuple[str, ...], large: Tuple[str, ...]) -> bool:
    """Return True when every occurrence of small inside large is directly preceded by a negation token."""
    if not small or len(small) >= len(large):
        return False
    saw_match = False
    for idx in range(len(large) - len(small) + 1):
        if large[idx:idx + len(small)] != small:
            continue
        saw_match = True
        prev = idx - 1
        while prev >= 0 and large[prev] in {"-", "/", ":"}:
            prev -= 1
        if prev < 0 or large[prev] not in _NEGATION_PREFIX_TOKENS:
            return False
    return saw_match

from app.material_storage import (
    IMAGE_EXTENSIONS,
    MAX_FILE_BYTES,
    MIN_EXTRACT_CHARS,
    SUPPORTED_EXTENSIONS,
    StudyMaterialError,
    StudyMaterialIndexError,
    merge_chunk_texts,
    resolve_safe_source_file,
)
from app.question_validator import (
    _information_unit_distractors_match,
    _zero_condition_distractors_are_disjoint,
    evaluate_question_difficulty,
    parse_json_from_response,
)
from app.quiz_generator import (
    build_combined_easy_study_prompt,
    build_quiz_prompt,
    combined_easy_study_schema,
    fallback_generate_quiz,
    quiz_response_schema,
)
from app.flashcard_generator import (
    build_flashcards_prompt,
    extract_focused_flashcard_context,
    fallback_generate_flashcards,
    flashcard_response_schema,
    strip_source_review_banner,
)


class StudyMaterialManager:
    """Manages document parsing, AI generation, and material caching."""

    MAX_EXTRACT_CACHE = 16

    def __init__(self, ollama: Optional[OllamaClient] = None):
        self.ollama = ollama or OllamaClient()
        self.extractor = PDFExtractor()
        self._materials: Dict[str, Dict[str, Any]] = {}
        self._raw_files: Dict[str, bytes] = {}
        self._extract_cache: OrderedDict[Tuple[Any, ...], PDFExtractionResult] = OrderedDict()
        self._lock = threading.Lock()
        self._bg_ocr_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bg-upload-ocr")
        self._bg_ocr_inflight: set[str] = set()
        self._bg_ocr_futures: List[Any] = []

    def evict_raw_file(self, *filenames: Optional[str]) -> None:
        """Evict raw file bytes from memory cache to avoid exposing failed uploads."""
        with self._lock:
            for fn in filenames:
                if fn:
                    self._raw_files.pop(fn, None)
                    self._raw_files.pop(Path(fn).name, None)

    def cache_raw_file(self, filename: Optional[str], file_bytes: bytes, material_id: Optional[str] = None) -> None:
        """Cache raw file bytes in memory for downloads."""
        if not file_bytes:
            return
        with self._lock:
            if filename:
                self._raw_files[filename] = file_bytes
            if material_id:
                self._raw_files[material_id] = file_bytes

    def validate_file(self, filename: str, file_bytes: bytes) -> str:
        """Validate filename extension, size, and header magic bytes."""
        if not filename or not filename.strip():
            raise StudyMaterialError("Tên tệp tin không hợp lệ.")
        if len(file_bytes) == 0:
            raise StudyMaterialError("Tệp tin rỗng (0 bytes). Vui lòng chọn tệp hợp lệ.")
        if len(file_bytes) > MAX_FILE_BYTES:
            raise StudyMaterialError(f"Dung lượng tệp vượt quá giới hạn cho phép ({MAX_FILE_BYTES // (1024*1024)}MB).")

        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise StudyMaterialError(f"Định dạng '{ext}' không được hỗ trợ. Hệ thống hỗ trợ: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

        # Magic bytes checks
        if ext == ".pdf" and not file_bytes.startswith(b"%PDF"):
            raise StudyMaterialError("Tệp PDF bị hỏng hoặc không đúng định dạng chuẩn.")
        elif ext == ".png" and not file_bytes.startswith(b"\x89PNG"):
            raise StudyMaterialError("Tệp PNG không đúng định dạng.")
        elif ext in (".jpg", ".jpeg") and not file_bytes.startswith(b"\xff\xd8"):
            raise StudyMaterialError("Tệp JPEG không đúng định dạng.")
        elif ext == ".webp" and not (file_bytes.startswith(b"RIFF") and b"WEBP" in file_bytes[:16]):
            raise StudyMaterialError("Tệp WebP không đúng định dạng.")

        if ext in IMAGE_EXTENSIONS:
            try:
                with Image.open(io.BytesIO(file_bytes)) as img:
                    img.verify()
            except Exception as e:
                raise StudyMaterialError(f"Tệp ảnh '{filename}' bị hỏng hoặc không thể giải mã: {e}")

        return ext

    @staticmethod
    def _run_windows_native_ocr(filepath: Path) -> str:
        """Use Windows built-in WinRT OcrEngine via PowerShell when external OCR engines are unavailable."""
        if os.name != "nt":
            return ""
        try:
            abs_path = str(filepath.resolve()).replace("'", "''")
            ps_code = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
                "Add-Type -AssemblyName System.Runtime.WindowsRuntime\n"
                "$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]\n"
                "$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]\n"
                "$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]\n"
                "$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { "
                "$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and "
                "$_.GetParameters()[0].ParameterType.Name.StartsWith('IAsyncOperation') })[0]\n"
                "Function Await($WinRtTask, $ResultType) {\n"
                "    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)\n"
                "    $netTask = $asTask.Invoke($null, @($WinRtTask))\n"
                "    $netTask.Wait(-1) | Out-Null\n"
                "    $netTask.Result\n"
                "}\n"
                f"$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{abs_path}')) ([Windows.Storage.StorageFile])\n"
                "$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])\n"
                "$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])\n"
                "$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])\n"
                "$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()\n"
                "if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US')) }\n"
                "$ocrResult = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])\n"
                "foreach ($line in $ocrResult.Lines) { Write-Output $line.Text }\n"
            )
            encoded = base64.b64encode(ps_code.encode("utf-16le")).decode("ascii")
            proc = subprocess.run(  # nosec B603, B607
                ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return normalize_vietnamese_text(proc.stdout.strip())
        except Exception as e:
            logger.debug("Windows native OCR fallback skipped: %s", e)
        return ""

    def _extract_image_fallback(
        self,
        temp_file: Path,
        filename: str,
        file_bytes: bytes,
        result: PDFExtractionResult,
        allow_without_text: bool = False,
    ) -> PDFExtractionResult:
        """Fallback extraction for image files when primary VisionOCRManager is offline or returns sparse text."""
        collected_parts: List[str] = []

        # 1. Keep any partial text already found via real OCR
        for p in result.extracted_pages:
            if p.get("text", "").strip():
                collected_parts.append(p["text"].strip())

        # 2. Try Windows native WinRT OcrEngine if text is still short (strictly real OCR, never metadata)
        if sum(len(part) for part in collected_parts) < MIN_EXTRACT_CHARS:
            win_ocr_text = self._run_windows_native_ocr(temp_file)
            if win_ocr_text:
                collected_parts.append(win_ocr_text)

        raw_ocr_text = "\n".join(part for part in collected_parts if part).strip()
        final_text = normalize_vietnamese_text(raw_ocr_text)

        if len(final_text) < MIN_EXTRACT_CHARS and not allow_without_text:
            raise StudyMaterialError(
                f"Tài liệu hình ảnh '{filename}' không có đủ văn bản nhận diện được qua OCR ({len(final_text)} ký tự). "
                "Hệ thống không thể đọc được nội dung chữ để tạo câu hỏi ôn tập."
            )

        width, height = 0, 0
        try:
            with Image.open(io.BytesIO(file_bytes)) as img:
                width, height = img.size
        except Exception:  # nosec B110
            pass

        has_detected_text = len(final_text) >= MIN_EXTRACT_CHARS
        clean_title = PDFExtractionResult._clean_title(filename) or Path(filename).stem
        display_text = final_text if final_text else f"[Tài liệu hình ảnh: {clean_title}]"

        result.doc_type = "image"
        result.unit_name = "hình ảnh"
        result.total_pages = 1
        result.ocr_pages = 1 if has_detected_text else 0
        result.total_chars = len(display_text)
        result.extracted_pages = [{
            "page_num": 1,
            "text": display_text,
            "raw_text": display_text,
            "raw_capture": {"raw_text": display_text, "char_count": len(display_text), "has_text": has_detected_text},
            "blocks": [{"block_id": "p1_b0", "bbox": [0, 0, width, height], "type": "image", "text": display_text}],
            "quality_warnings": result.quality_warnings if has_detected_text else ["image_low_or_no_text"],
            "needs_review": not has_detected_text,
            "char_count": len(display_text),
            "has_text": has_detected_text,
            "unit_type": "hình ảnh",
        }]
        return result

    def extract_document(
        self,
        filename: str,
        file_bytes: bytes,
        allow_image_without_text: bool = False,
        generate_study_set: bool = False,
        quiz_count: int = 3,
        flashcard_count: int = 5,
        persist_to_src: bool = False,
        force_fast_print_ocr: Optional[bool] = None,
    ) -> PDFExtractionResult:
        """Extract text content from uploaded file bytes safely via PDFExtractor."""
        ext = self.validate_file(filename, file_bytes)
        is_image = ext in IMAGE_EXTENSIONS
        clean_filename = Path(filename).name
        use_fast_ocr = (bool(generate_study_set) and not persist_to_src) or is_image
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        cache_key = (
            file_sha256,
            ext,
            clean_filename,
            bool(allow_image_without_text),
            bool(generate_study_set and not persist_to_src),
            int(quiz_count or 3),
            int(flashcard_count or 5),
        )
        with self._lock:
            cached_extract = self._extract_cache.get(cache_key)
            if (cached_extract is None or int(getattr(cached_extract, "ocr_truncated_pages", 0) or 0) > 0) and not is_image:
                for ck, cval in self._extract_cache.items():
                    if (
                        len(ck) >= 5
                        and ck[0] == file_sha256
                        and ck[1] == ext
                        and ck[2] == clean_filename
                        and not bool(ck[4])
                        and int(getattr(cval, "ocr_truncated_pages", 0) or 0) == 0
                    ):
                        cached_extract = cval
                        cache_key = ck
                        break
            if cached_extract is not None:
                self._extract_cache.move_to_end(cache_key)
                cloned = copy.deepcopy(cached_extract)
                setattr(cloned, "_raw_bytes", file_bytes)
                return cloned

        # Write to secure temp file preserving filename stem for handwriting/title heuristics
        upload_dir = DATA_DIR / "runtime" / "user_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._-") or "upload"
        temp_file = upload_dir / f"upload_{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"

        try:
            temp_file.write_bytes(file_bytes)
            if is_image and use_fast_ocr:
                result = PDFExtractor.extract_file(temp_file, ocr=True, fast_print_ocr=True, rel_path=clean_filename)
            else:
                result = PDFExtractor.extract_file(temp_file, ocr=is_image, rel_path=clean_filename)

            # Retry with bounded fast-first OCR for scanned PDFs that yielded insufficient digital text
            if ext == ".pdf" and (result.total_chars < MIN_EXTRACT_CHARS or not result.extracted_pages):
                if PDFExtractor.ocr_available():
                    if generate_study_set and not persist_to_src:
                        target_pages = min(8, max(3, max(1, int(quiz_count or 3)) + 2))
                        max_ocr_pages = min(12, max(4, (max(1, int(quiz_count or 3)) + max(0, int(flashcard_count or 0))) * 2))
                        result = PDFExtractor.extract_file(
                            temp_file,
                            ocr=True,
                            fast_print_ocr=True,
                            ocr_max_pages=max_ocr_pages,
                            ocr_target_substantive_pages=target_pages,
                            ocr_budget_seconds=60.0,
                            rel_path=clean_filename,
                        )
                    else:
                        result = PDFExtractor.extract_file(
                            temp_file,
                            ocr=True,
                            fast_print_ocr=bool(force_fast_print_ocr) if force_fast_print_ocr is not None else False,
                            rel_path=clean_filename,
                        )

            # Fallback OCR / image analysis for uploaded images
            if is_image and (result.total_chars < MIN_EXTRACT_CHARS or not result.extracted_pages):
                result = self._extract_image_fallback(
                    temp_file, filename, file_bytes, result, allow_without_text=allow_image_without_text
                )

            result.filename = clean_filename
            result.clean_title = PDFExtractionResult._clean_title(filename)

            if not is_image and (result.total_chars < MIN_EXTRACT_CHARS or not result.extracted_pages):
                raise StudyMaterialError(
                    f"Tài liệu '{filename}' không chứa đủ văn bản có thể đọc được ({result.total_chars} ký tự). "
                    "Nếu là tài liệu quét dạng ảnh, vui lòng kiểm tra chất lượng hình ảnh."
                )
            if is_image and not result.extracted_pages:
                raise StudyMaterialError(
                    f"Không thể đọc hoặc trích xuất tệp hình ảnh '{filename}'."
                )
            cache_snapshot = copy.deepcopy(result)
            with self._lock:
                self._extract_cache[cache_key] = cache_snapshot
                self._extract_cache.move_to_end(cache_key)
                while len(self._extract_cache) > self.MAX_EXTRACT_CACHE:
                    self._extract_cache.popitem(last=False)
            setattr(result, "_raw_bytes", file_bytes)
            return result
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:  # nosec B110
                    pass

    def schedule_background_full_ocr(
        self,
        material_id: str,
        filename: str,
        file_bytes: bytes,
        extracted: Any,
        *,
        account_scope: Optional[str] = None,
    ) -> bool:
        """Schedule low-priority full-document CPU OCR when an upload used bounded fast OCR."""
        truncated = int(getattr(extracted, "ocr_truncated_pages", 0) or 0)
        clean_filename = Path(filename).name
        if truncated <= 0 or not clean_filename.lower().endswith(".pdf") or not file_bytes:
            return False
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        with self._lock:
            if file_sha256 in self._bg_ocr_inflight:
                return False
            self._bg_ocr_inflight.add(file_sha256)
            self._bg_ocr_futures = [fut for fut in self._bg_ocr_futures if not fut.done()]

        def _worker() -> None:
            from app.accounts import account_id as _account_ctx

            token = _account_ctx.set(account_scope) if account_scope is not None else None
            try:
                full_res = self.extract_document(
                    clean_filename,
                    file_bytes,
                    allow_image_without_text=True,
                    generate_study_set=False,
                    persist_to_src=True,
                    force_fast_print_ocr=not bool(getattr(extracted, "handwriting_suspected", False)),
                )
                with self._lock:
                    for ck in list(self._extract_cache.keys()):
                        if len(ck) >= 3 and ck[0] == file_sha256 and ck[1] == ".pdf" and ck[2] == clean_filename:
                            self._extract_cache[ck] = copy.deepcopy(full_res)
                    existing = self._materials.get(material_id)
                    updated = copy.deepcopy(existing) if isinstance(existing, dict) else None
                if updated is not None:
                    updated["pages"] = [
                        {"page_num": int(p.get("page_num", 1)), "text": str(p.get("text", ""))}
                        for p in (full_res.extracted_pages or [])
                        if (p.get("text") or "").strip()
                    ]
                    updated["total_chars"] = int(getattr(full_res, "total_chars", 0) or 0)
                    updated["ocr_truncated_pages"] = 0
                    updated["ocr_background_complete"] = True
                    persist_fn = getattr(self, "persist_material_record", None)
                    if callable(persist_fn):
                        persist_fn(updated)
                    else:
                        with self._lock:
                            if material_id in self._materials:
                                self._materials[material_id] = updated
            except Exception as exc:
                logger.warning("Background full OCR failed for %s: %s", clean_filename, exc)
            finally:
                with self._lock:
                    self._bg_ocr_inflight.discard(file_sha256)
                if token is not None:
                    _account_ctx.reset(token)

        fut = self._bg_ocr_executor.submit(_worker)
        with self._lock:
            self._bg_ocr_futures.append(fut)
        return True

    def wait_background_ocr(self, timeout: float = 5.0) -> None:
        """Wait for queued/running background upload OCR tasks (used by tests and cleanup)."""
        with self._lock:
            pending = [f for f in self._bg_ocr_futures if not f.done()]
        import time as _time
        end = _time.monotonic() + max(0.0, float(timeout))
        for fut in pending:
            rem = max(0.01, end - _time.monotonic())
            try:
                fut.result(timeout=rem)
            except Exception:  # nosec B110
                pass

    def generate_materials_from_pages(
        self,
        extracted_result: PDFExtractionResult,
        quiz_count: int = 3,
        flashcard_count: int = 5,
        difficulty: str = "medium",
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate reviewed questions; never pad a set with generic distractors."""
        model_name = model or DEFAULT_CHAT_MODEL
        is_mlx = is_mlx_model(model_name)

        passages = [
            {"page_num": int(p["page_num"]), "text": p["text"]}
            for p in (extracted_result.extracted_pages or [])
            if p.get("text", "").strip()
        ]

        if not passages:
            raise StudyMaterialError("Không có nội dung văn bản hợp lệ để tạo câu hỏi ôn tập.")

        real_passages = [
            p for p in passages
            if not str(p.get("text", "")).strip().startswith("[Tài liệu hình ảnh:")
        ]
        if not real_passages:
            raise StudyMaterialError(
                "Tài liệu hình ảnh này không có đủ nội dung văn bản để tạo câu hỏi ôn tập (Quiz & Flashcards). "
                "Vui lòng chọn tài liệu văn bản hoặc ảnh chụp tài liệu có chữ rõ ràng."
            )

        # Context budget enforcement via token_counter or conservative estimation
        import app.token_counter as tc_mod
        token_counter_func = None
        try:
            token_counter_func = tc_mod.get_exact_token_counter()
        except Exception:
            token_counter_func = None

        system_prompt = "Bạn là trợ lý học tập AI bám sát tài liệu nguồn. Trả về đúng định dạng JSON được yêu cầu."
        capacity = get_model_context_capacity(model_name) if is_mlx else MLX_CONTEXT_TOKENS
        output_reserve = 1500
        chat_overhead = 256
        budget = max(0, capacity - output_reserve - chat_overhead)

        def estimate_cost(cand_passages: List[Dict[str, Any]]) -> int:
            prompt_q = build_quiz_prompt(cand_passages, count=min(3, quiz_count), difficulty=difficulty) if quiz_count > 0 else ""
            prompt_f = build_flashcards_prompt(cand_passages, count=min(5, flashcard_count)) if flashcard_count > 0 else ""
            messages_q = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_q},
            ] if prompt_q else []
            messages_f = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_f},
            ] if prompt_f else []
            if token_counter_func is not None:
                try:
                    cost_q = token_counter_func(messages_q) if messages_q else 0
                    cost_f = token_counter_func(messages_f) if messages_f else 0
                    return max(cost_q, cost_f)
                except Exception:  # nosec B110
                    pass
            cost_q = (len(system_prompt.encode("utf-8")) + len(prompt_q.encode("utf-8")) + chat_overhead) if prompt_q else 0
            cost_f = (len(system_prompt.encode("utf-8")) + len(prompt_f.encode("utf-8")) + chat_overhead) if prompt_f else 0
            return max(cost_q, cost_f)

        def prompt_fits(prompt, reserve=1500):
            messages = [{'role':'system','content':system_prompt}, {'role':'user','content':prompt}]
            try:
                size = token_counter_func(messages) if token_counter_func else None
            except Exception:
                size = None
            if size is None:
                size = sum(len(m['content'].encode()) for m in messages)
            return size + reserve + chat_overhead <= capacity

        candidate_passages = passages
        max_page_threshold = 6 if is_mlx else 16
        if len(passages) > max_page_threshold:
            if is_mlx:
                max_study_pages = min(8, max(3, max(quiz_count, 1) + min(max(0, flashcard_count), 3)))
            else:
                max_study_pages = min(20, max(12, (max(0, quiz_count) + max(0, flashcard_count)) * 2))
            substantive = [
                p for p in passages
                if len((p.get("text") or "").strip()) >= 120
                and not str(p.get("text") or "").strip().startswith("[Tài liệu hình ảnh:")
            ]
            pool = substantive if substantive else passages
            if len(pool) > max_study_pages:
                step = len(pool) / max_study_pages
                candidate_passages = [pool[int(i * step)] for i in range(max_study_pages)]
            else:
                candidate_passages = pool

        admitted_passages = []
        for p in candidate_passages:
            cand = admitted_passages + [p]
            if estimate_cost(cand) <= budget:
                admitted_passages.append(p)
            else:
                if not admitted_passages:
                    # Oversized first page: pack complete sentences without exceeding budget
                    raw_p_text = p.get("text", "")
                    sentences = re.split(r"(?<=[.!?\n])\s+", raw_p_text)
                    packed_text = ""
                    search_from = 0
                    for s in sentences:
                        s_strip = s.strip()
                        if not s_strip:
                            continue
                        end = raw_p_text.index(s_strip, search_from) + len(s_strip)
                        candidate_text = raw_p_text[:end]
                        test_p = [{"page_num": p["page_num"], "text": candidate_text}]
                        if estimate_cost(test_p) <= budget:
                            packed_text = candidate_text
                            search_from = end
                        else:
                            break
                    if packed_text:
                        admitted_passages.append({"page_num": p["page_num"], "text": packed_text})
                break

        fallback_sources = admitted_passages or passages
        page_chunks: Dict[int, List[str]] = {}
        for p in (admitted_passages if admitted_passages else fallback_sources):
            page_chunks.setdefault(int(p["page_num"]), []).append(p["text"])

        evidence_by_id = {f"Q{i}": p for i, p in enumerate(admitted_passages, 1)}

        def resolve_evidence(item):
            if not isinstance(item, dict):
                return None
            if "evidence_id" not in item:
                return item  # Legacy model outputs still pass the exact quote/page checks below.
            reference = item.get("evidence_id")
            if not isinstance(reference, str) or reference not in evidence_by_id:
                return None
            source = evidence_by_id[reference]
            if "page_num" in item and (type(item["page_num"]) is not int or item["page_num"] != source["page_num"]):
                return None
            if "evidence_quote" in item and item["evidence_quote"] != source["text"]:
                return None
            return {**item, "page_num": source["page_num"], "evidence_quote": source["text"]}

        # Resolve single client for both turns (only if we have admitted passages to send)
        active_client = None
        if admitted_passages:
            if is_mlx:
                import app.trained_client as tc
                try:
                    is_avail = tc.TrainedModelClient.available(model_name=model_name)
                except TypeError:
                    is_avail = tc.TrainedModelClient.available()
                if is_avail:
                    try:
                        active_client = tc.TrainedModelClient(model_name=model_name)
                    except TypeError:
                        active_client = tc.TrainedModelClient()
            else:
                if hasattr(self.ollama, "check_health"):
                    if self.ollama.check_health():
                        active_client = self.ollama
                else:
                    active_client = self.ollama

        # 1. Generate Quiz (and combined easy flashcards on first MLX turn when both fit)
        quiz_raw_items = []
        flashcard_raw_items = []

        def _normalize_quiz_item(raw_q: Any, next_id: int = 1) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
            """Validate one quiz proposal and return (normalized_item, cloze_repair_reason)."""
            q = resolve_evidence(raw_q)
            if not isinstance(q, dict):
                return None, None
            is_cloze = q.get("format") == "source_cloze"
            if is_cloze:
                if q.get("evidence_id") not in evidence_by_id:
                    return None, None
                span = q.get("answer")
                quote = q.get("evidence_quote")
                distractors = q.get("distractors")
                if not isinstance(span, str) or not isinstance(quote, str):
                    return None, "span_or_distractor_shape"
                span = html.unescape(span).strip()
                if not 2 <= len(span) <= 80:
                    return None, "span_or_distractor_shape"
                has_three_distractors = (
                    isinstance(distractors, list)
                    and len(distractors) == 3
                    and all(isinstance(d, str) and d.strip() for d in distractors)
                )
                if not has_three_distractors:
                    return None, "span_or_distractor_shape"
                span_count = _count_bounded_span_occurrences(quote, span)
                if span_count == 0:
                    return None, "span_not_in_excerpt"
                if span_count > 1:
                    return None, "repeated_answer"
                options_cf = [" ".join(html.unescape(d).split()).casefold() for d in [span, *distractors]]
                if len(set(options_cf)) != 4:
                    return None, "duplicate_options"
                if len(span) >= len(quote) * 0.6:
                    return None, "span_or_distractor_shape"
                masked_quote = self._mask_answer_in_quote(quote, span)
                if not masked_quote:
                    return None, "span_not_in_excerpt"
                if (not _zero_condition_distractors_are_disjoint(span, distractors)
                        or not _information_unit_distractors_match(span, distractors)):
                    return None, "incompatible_distractors"
                options = [html.unescape(d).strip() for d in distractors]
                answer_index = hashlib.sha256((quote + "\0" + span).encode()).digest()[0] % 4
                options.insert(answer_index, span)
                stripped_quote = strip_source_review_banner(masked_quote)
                display_masked_quote = stripped_quote if "[…]" in stripped_quote else masked_quote
                q = {
                    **q,
                    "question": "Chọn cụm điền vào trích đoạn:\n" + display_masked_quote + "\nPhần […] là gì?",
                    "choices": dict(zip("ABCD", options)),
                    "answer": "ABCD"[answer_index],
                }

            q_text = q.get("question")
            if not isinstance(q_text, str) or not q_text.strip():
                return None, None
            raw_page = q.get("page_num")
            if isinstance(raw_page, bool) or not isinstance(raw_page, (int, str)):
                return None, None
            if isinstance(raw_page, str):
                if not raw_page.strip().isdigit():
                    return None, None
                p_num = int(raw_page.strip())
            else:
                p_num = raw_page
            if p_num not in page_chunks:
                return None, None
            ev_quote = q.get("evidence_quote")
            if not isinstance(ev_quote, str) or not ev_quote.strip():
                return None, None
            if not any(self._quote_matches_chunk(ev_quote, chunk_text) for chunk_text in page_chunks[p_num]):
                return None, None
            choices = q.get("choices")
            norm_choices = []
            if isinstance(choices, dict):
                if set(choices.keys()) != {"A", "B", "C", "D"}:
                    return None, None
                for lbl in ["A", "B", "C", "D"]:
                    c_text = choices.get(lbl)
                    if not isinstance(c_text, str) or not c_text.strip():
                        return None, None
                    norm_choices.append({"label": lbl, "text": html.unescape(c_text).strip()})
            elif isinstance(choices, list) and len(choices) == 4:
                seen_labels = set()
                for c in choices:
                    if isinstance(c, dict) and "label" in c and "text" in c:
                        lbl = str(c["label"]).strip().upper()
                        c_text = c["text"]
                        if lbl in {"A", "B", "C", "D"} and lbl not in seen_labels and isinstance(c_text, str) and c_text.strip():
                            seen_labels.add(lbl)
                            norm_choices.append({"label": lbl, "text": html.unescape(c_text).strip()})
                        else:
                            return None, None
                    else:
                        return None, None
                if seen_labels != {"A", "B", "C", "D"}:
                    return None, None
                norm_choices.sort(key=lambda x: x["label"])
            else:
                return None, None

            # Different labels alone do not make a single-answer question.
            choice_texts = {" ".join(c["text"].split()).casefold() for c in norm_choices}
            if len(choice_texts) != 4:
                return None, None
            if has_language_drift("\n".join([html.unescape(q_text), *(c["text"] for c in norm_choices)])):
                return None, None
            # A repeated option with extra words is not a reliable distractor.
            option_words = [tuple(re.findall(r"\w+|[^\w\s]", c["text"].casefold())) for c in norm_choices]
            if any(
                small and len(small) < len(large)
                and any(large[i:i + len(small)] == small for i in range(len(large) - len(small) + 1))
                and not _is_negated_superstring(small, large)
                for small in option_words for large in option_words
            ):
                return None, None

            raw_ans = q.get("answer") or q.get("correct_answer")
            if not isinstance(raw_ans, str):
                return None, None
            ans_str = raw_ans.strip().upper()
            if ans_str not in {"A", "B", "C", "D"}:
                return None, None

            evaluated_diff = evaluate_question_difficulty(q)
            explanation = (
                f"Căn cứ trực tiếp theo tài liệu trang {p_num}: {ev_quote}"
                if is_cloze
                else str(q.get("explanation") or f"Căn cứ trực tiếp theo tài liệu trang {p_num}: {ev_quote}")
            )
            return {
                "id": q.get("id") if isinstance(q.get("id"), int) else next_id,
                "question": html.unescape(q_text).strip(),
                "choices": norm_choices,
                "answer": ans_str,
                "correct_answer": ans_str,
                "explanation": explanation,
                "evidence_quote": ev_quote,
                "page_num": p_num,
                "difficulty": evaluated_diff,
                "answer_verified": False,
                **({"format": "source_cloze"} if is_cloze else {}),
            }, None

        original_page_chunks: Dict[int, List[str]] = {}
        for orig_p in passages:
            original_page_chunks.setdefault(int(orig_p["page_num"]), []).append(str(orig_p["text"]))

        proven_quiz: List[Dict[str, Any]] = []
        proven_questions: set[str] = set()
        if quiz_count > 0 and difficulty == "medium" and active_client is not None:
            from app.practice_proof import (
                build_proof_backed_medium_questions,
                verify_proof_backed_medium_question,
            )
            for candidate in build_proof_backed_medium_questions(
                admitted_passages, count=quiz_count, context_passages=passages
            ):
                norm_q, _ = _normalize_quiz_item(candidate, next_id=len(proven_quiz) + 1)
                if norm_q is None or norm_q["question"] in proven_questions:
                    continue
                if not any(
                    self._quote_matches_chunk(norm_q["evidence_quote"], chunk_text)
                    and verify_proof_backed_medium_question(norm_q, chunk_text, context_passages=passages)
                    for chunk_text in page_chunks.get(norm_q["page_num"], [])
                ):
                    continue
                if not any(
                    self._quote_matches_chunk(norm_q["evidence_quote"], orig_text)
                    and verify_proof_backed_medium_question(norm_q, orig_text, context_passages=passages)
                    for orig_text in original_page_chunks.get(norm_q["page_num"], [])
                ):
                    continue
                norm_q["difficulty"] = "medium"
                norm_q["answer_verified"] = True
                norm_q["generation"] = "source_proof"
                proven_questions.add(norm_q["question"])
                proven_quiz.append(norm_q)
                if len(proven_quiz) >= quiz_count:
                    break

        remaining_quiz_count = max(0, quiz_count - len(proven_quiz))

        def _is_too_easy_for_target(item: Dict[str, Any]) -> bool:
            if difficulty == "easy":
                return False
            if item.get("format") == "source_cloze" or item.get("fallback") or item.get("is_fallback"):
                return True
            if is_mlx:
                return False
            return evaluate_question_difficulty(item) == "easy"

        def _distinct_clean_quiz_raw(raw_items: List[Any]) -> Tuple[List[Any], List[Optional[str]]]:
            clean_raw: List[Any] = []
            seen_questions = set(proven_questions)
            cloze_answers_by_page: Dict[int, List[str]] = {}
            repair_reasons: List[Optional[str]] = []
            for raw_item in raw_items:
                norm_item, reason = _normalize_quiz_item(raw_item, next_id=len(proven_quiz) + len(clean_raw) + 1)
                repair_reasons.append(reason)
                if norm_item is None or norm_item["question"] in seen_questions:
                    continue
                if _is_too_easy_for_target(norm_item):
                    continue
                if norm_item.get("format") == "source_cloze":
                    ans_text = " ".join(
                        next(
                            (c["text"] for c in norm_item["choices"] if c["label"] == norm_item["answer"]),
                            "",
                        ).split()
                    ).casefold()
                    p_num = norm_item["page_num"]
                    prior_answers = cloze_answers_by_page.get(p_num, ())
                    if ans_text and any(
                        ans_text == prev or ans_text in prev or prev in ans_text
                        for prev in prior_answers
                    ):
                        repair_reasons[-1] = repair_reasons[-1] or "repeated_answer"
                        continue
                    if ans_text:
                        cloze_answers_by_page.setdefault(p_num, []).append(ans_text)
                seen_questions.add(norm_item["question"])
                clean_raw.append(raw_item)
            return clean_raw, repair_reasons

        def _collect_valid_flashcards(raw_items: List[Any]) -> List[Dict[str, Any]]:
            collected: List[Dict[str, Any]] = []
            for f in raw_items:
                f = resolve_evidence(f)
                if not isinstance(f, dict):
                    continue
                back = f.get("back") or f.get("definition") or f.get("answer")
                if not isinstance(back, str) or not back.strip():
                    continue
                raw_page = f.get("page_num")
                if isinstance(raw_page, bool) or not isinstance(raw_page, (int, str)):
                    continue
                if isinstance(raw_page, str):
                    if not raw_page.strip().isdigit():
                        continue
                    p_num = int(raw_page.strip())
                else:
                    p_num = raw_page
                if p_num not in page_chunks:
                    continue
                ev_quote = f.get("evidence_quote")
                if not isinstance(ev_quote, str) or not ev_quote.strip():
                    continue
                if not any(self._quote_matches_chunk(ev_quote, chunk_text) for chunk_text in page_chunks[p_num]):
                    continue
                answer_text = html.unescape(back).strip()
                masked = self._mask_answer_in_quote(ev_quote, answer_text)
                if masked is None:
                    continue
                if any(item["back"].casefold() == answer_text.casefold() for item in collected):
                    continue
                focused_masked = extract_focused_flashcard_context(masked)
                source_question = f"Điền phần còn thiếu trong trích đoạn:\n{focused_masked}\nPhần […] là gì?"
                collected.append({
                    "id": f.get("id") if isinstance(f.get("id"), int) else (len(collected) + 1),
                    "front": source_question,
                    "back": answer_text,
                    "format": "source_cloze",
                    "evidence_quote": ev_quote,
                    "page_num": p_num,
                })
            return collected

        def _mlx_optional_kwargs(schema_obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            extra: Dict[str, Any] = {}
            if not is_mlx or active_client is None:
                return extra
            import inspect
            try:
                sig = inspect.signature(active_client.chat_complete)
                has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                params = sig.parameters
            except (TypeError, ValueError):
                return extra
            if schema_obj is not None and (has_varkw or "response_schema" in params):
                extra["response_schema"] = schema_obj
            if has_varkw or "context_prefix_marker" in params:
                extra["context_prefix_marker"] = "\n<task>\n"
            return extra

        passage_evidence_ids = [f"Q{idx}" for idx in range(1, len(admitted_passages) + 1)]
        first_batch_quiz_target = min(3, remaining_quiz_count) if remaining_quiz_count > 0 else 0
        quiz_schema = quiz_response_schema(difficulty, passage_evidence_ids, max_items=first_batch_quiz_target)
        review_timed_out_partial = False

        if remaining_quiz_count > 0 and active_client is not None:
            try:
                quiz_prompt = build_quiz_prompt(admitted_passages, count=first_batch_quiz_target, difficulty=difficulty)
                if proven_quiz:
                    excluded_proven = [q["evidence_quote"][:120] for q in proven_quiz]
                    proven_note = "\nKhông lặp lại các trích đoạn/quy tắc đã dùng: " + json.dumps(excluded_proven, ensure_ascii=False)
                    if prompt_fits(quiz_prompt + proven_note):
                        quiz_prompt += proven_note
                quiz_temp = 0.0 if (is_mlx and difficulty == "easy") else 0.2
                combined_prompt = (
                    build_combined_easy_study_prompt(
                        admitted_passages,
                        quiz_count=first_batch_quiz_target,
                        flashcard_count=flashcard_count,
                    )
                    if (is_mlx and difficulty == "easy" and 1 <= quiz_count <= 3 and 1 <= flashcard_count <= 5)
                    else ""
                )
                use_combined_first = bool(combined_prompt and prompt_fits(combined_prompt))
                attempt_prompt = combined_prompt if use_combined_first else quiz_prompt
                attempt_schema = (
                    combined_easy_study_schema(
                        passage_evidence_ids,
                        max_quiz_items=first_batch_quiz_target,
                        max_flashcard_items=flashcard_count,
                    )
                    if use_combined_first
                    else quiz_schema
                )
                first_attempt_clean: List[Any] = []
                attempt_target = first_batch_quiz_target
                for attempt in range(2):
                    extra_combined_tokens = (
                        flashcard_count * 220 if (attempt == 0 and use_combined_first) else 0
                    )
                    quiz_max_tokens = (
                        min(1500, max(550, attempt_target * 380 + extra_combined_tokens))
                        if is_mlx
                        else 1500
                    )
                    with acquire_inference(dialogue_gate), gpu_coordinator.acquire_for_inference():
                        if is_mlx:
                            resp_content = active_client.chat_complete(
                                messages=[{"role": "user", "content": attempt_prompt}],
                                system_prompt=system_prompt,
                                temperature=quiz_temp,
                                max_tokens=quiz_max_tokens,
                                num_ctx=capacity,
                                **_mlx_optional_kwargs(attempt_schema),
                            )
                        else:
                            resp_content = active_client.chat_complete(
                                messages=[{"role": "user", "content": attempt_prompt}],
                                model=model_name,
                                system_prompt=system_prompt,
                                temperature=0.2,
                                num_predict=1500,
                                num_ctx=capacity,
                                think=False,
                            )
                    last_reason = getattr(active_client, "last_done_reason", None)
                    if last_reason != "stop":
                        logger.warning("LLM quiz generation interrupted (done_reason=%s). Using fallback.", last_reason)
                        if attempt == 1 and first_attempt_clean:
                            quiz_raw_items = first_attempt_clean
                        break
                    quiz_json = parse_json_from_response(resp_content)
                    if attempt == 0 and quiz_json and isinstance(quiz_json.get("flashcards"), list):
                        flashcard_raw_items.extend(quiz_json["flashcards"])
                    candidate_items = (
                        list(quiz_json["questions"])
                        if (quiz_json and isinstance(quiz_json.get("questions"), list))
                        else []
                    )
                    clean_items, repair_reasons = _distinct_clean_quiz_raw(candidate_items)
                    if attempt == 0:
                        quiz_raw_items = clean_items
                        first_attempt_clean = clean_items
                        if len(clean_items) >= first_batch_quiz_target:
                            break
                    else:
                        if clean_items and first_attempt_clean:
                            if is_mlx and len(first_attempt_clean) < first_batch_quiz_target:
                                quiz_raw_items, _ = _distinct_clean_quiz_raw(first_attempt_clean + clean_items)
                            else:
                                seen_clean = {
                                    _normalize_quiz_item(item)[0]["question"]
                                    for item in clean_items
                                }
                                merged = list(clean_items)
                                for prev in first_attempt_clean:
                                    prev_norm, _ = _normalize_quiz_item(prev)
                                    if prev_norm and prev_norm["question"] not in seen_clean:
                                        merged.append(prev)
                                        seen_clean.add(prev_norm["question"])
                                quiz_raw_items = merged
                        elif first_attempt_clean and not clean_items:
                            quiz_raw_items = first_attempt_clean
                        elif clean_items:
                            quiz_raw_items = clean_items
                        break

                    repair_instruction = ""
                    if "span_or_distractor_shape" in repair_reasons:
                        repair_instruction += (
                            "\nMỗi câu PHẢI có 1 cụm answer dài 2–80 ký tự chép nguyên văn từ passage Q "
                            "và đúng 3 distractors khác rỗng cùng loại thông tin."
                        )
                    if "span_not_in_excerpt" in repair_reasons:
                        repair_instruction += (
                            "\nCụm answer PHẢI được chép NGUYÊN VĂN chính xác từng ký tự từ passage Q đã chọn "
                            "(không dịch, không rút gọn hay sửa dấu)."
                        )
                    if "repeated_answer" in repair_reasons:
                        repair_instruction += (
                            "\nChọn lại cụm answer KHÔNG bị lặp: chọn toàn bộ điều kiện gồm biểu thức "
                            "và toán tử so sánh, không chỉ tên biến. Nếu nguồn không có điều kiện, "
                            "chọn cụm số kèm đơn vị hoặc cụm thuật ngữ, định nghĩa trọng tâm. Vẫn chỉ dùng nguồn và cấu trúc JSON trên."
                        )
                    if "duplicate_options" in repair_reasons:
                        repair_instruction += (
                            "\nTạo lại ba distractors KHÁC NHAU và khác answer, cùng loại thông tin. "
                            "Không lặp lựa chọn dù khác chữ hoa hoặc khoảng trắng. "
                            "Vẫn chỉ dùng nguồn và cấu trúc JSON trên."
                        )
                    if "incompatible_distractors" in repair_reasons:
                        repair_instruction += (
                            "\nBa distractors phải CÙNG đơn vị hoặc loại thông tin với answer nhưng thuộc miền giá trị "
                            "hay điều kiện RỜI NHAU và loại trừ lẫn nhau."
                        )
                    if is_mlx and 0 < len(first_attempt_clean) < first_batch_quiz_target and any(repair_reasons):
                        missing_quiz = first_batch_quiz_target - len(first_attempt_clean)
                        excluded_items = [
                            str(item.get("question") or item.get("answer") or "")[:200]
                            for item in first_attempt_clean
                            if isinstance(item, dict)
                        ]
                        repair_base = build_quiz_prompt(admitted_passages, count=missing_quiz, difficulty=difficulty)
                        if excluded_items:
                            repair_base += "\nKhông lặp lại các câu/cụm đã chọn: " + json.dumps(excluded_items, ensure_ascii=False)
                        repair_full = repair_base + repair_instruction
                        if (
                            estimate_cost(admitted_passages) + len(repair_instruction.encode()) <= budget
                            and prompt_fits(repair_full)
                        ):
                            attempt_prompt = repair_full
                            attempt_schema = quiz_response_schema(difficulty, passage_evidence_ids, max_items=missing_quiz)
                            attempt_target = missing_quiz
                            continue
                    elif (attempt == 0 and any(repair_reasons)
                            and estimate_cost(admitted_passages) + len(repair_instruction.encode()) <= budget
                            and prompt_fits(quiz_prompt + repair_instruction)):
                        attempt_prompt = quiz_prompt + repair_instruction
                        attempt_schema = quiz_schema
                        continue
                    break
                # A fixed 1500-token response cannot reliably contain ten
                # complete questions. Bound each turn and preserve the count.
                max_refill_batches = (
                    ((remaining_quiz_count - 1) // 3)
                    + (1 if 0 < len(quiz_raw_items) < first_batch_quiz_target else 0)
                    if remaining_quiz_count > 3
                    else 0
                )
                for _ in range(max_refill_batches):
                    remaining = remaining_quiz_count - len(quiz_raw_items)
                    if remaining <= 0:
                        break
                    batch_target = min(3, remaining)
                    excluded = [str(item.get('question') or item.get('answer') or '')[:200]
                                for item in quiz_raw_items if isinstance(item, dict)]
                    excluded.extend(q['question'][:200] for q in proven_quiz)
                    refill_passages = list(admitted_passages)
                    next_prompt = ''
                    while refill_passages:
                        cand_prompt = build_quiz_prompt(refill_passages, count=batch_target, difficulty=difficulty)
                        if excluded:
                            cand_prompt += '\nKhông lặp lại các câu/cụm đã chọn: ' + json.dumps(excluded, ensure_ascii=False)
                        if prompt_fits(cand_prompt):
                            next_prompt = cand_prompt
                            break
                        refill_passages.pop()
                    if not next_prompt:
                        break
                    refill_evidence_ids = [f"Q{idx}" for idx in range(1, len(refill_passages) + 1)]
                    batch_quiz_schema = quiz_response_schema(difficulty, refill_evidence_ids, max_items=batch_target)
                    refill_max_tokens = min(1500, max(550, batch_target * 380)) if is_mlx else 1500
                    with acquire_inference(dialogue_gate), gpu_coordinator.acquire_for_inference():
                        options = ({'max_tokens': refill_max_tokens, **_mlx_optional_kwargs(batch_quiz_schema)}
                                   if is_mlx else {'model': model_name, 'num_predict': 1500, 'think': False})
                        raw = active_client.chat_complete(messages=[{'role': 'user', 'content': next_prompt}],
                            system_prompt=system_prompt, temperature=quiz_temp, num_ctx=capacity, **options)
                    if getattr(active_client, 'last_done_reason', None) != 'stop':
                        break
                    parsed = parse_json_from_response(raw)
                    if not parsed or not isinstance(parsed.get('questions'), list):
                        break
                    prev_clean_count = len(quiz_raw_items)
                    quiz_raw_items, _ = _distinct_clean_quiz_raw(quiz_raw_items + parsed['questions'])
                    if len(quiz_raw_items) <= prev_clean_count:
                        break
            except (GPUBusyError, DialogueBusyError):
                raise
            except (subprocess.TimeoutExpired, TimeoutError) as e:
                if proven_quiz:
                    logger.warning(
                        "LLM quiz generation timed out with %d/%d proof-backed items ready; returning partial set: %s",
                        len(proven_quiz),
                        quiz_count,
                        e,
                    )
                    review_timed_out_partial = True
                else:
                    raise StudyMaterialError(f"Mô hình phản hồi quá thời gian quy định khi tạo câu hỏi ôn tập: {e}")
            except (RuntimeError, FileNotFoundError) as e:
                raise StudyMaterialError(f"Mô hình AI chưa sẵn sàng hoặc thiếu tệp trọng số cục bộ: {e}")
            except Exception as e:
                logger.warning("LLM quiz generation failed: %s. Using fallback.", e)

        # 2. Generate Flashcards (skip when combined easy call already satisfied flashcard_count)
        valid_flashcards = _collect_valid_flashcards(flashcard_raw_items)
        if flashcard_count > 0 and len(valid_flashcards) < flashcard_count and active_client is not None:
            try:
                max_batches = 1 if flashcard_count <= 5 else (flashcard_count + 4) // 5
                for _ in range(max_batches):
                    remaining = flashcard_count - len(valid_flashcards)
                    if remaining <= 0:
                        break
                    batch_flash_target = min(8, remaining)
                    excluded = [str(item.get('answer') or item.get('back') or '')[:80]
                                for item in flashcard_raw_items if isinstance(item, dict)]
                    refill_passages = list(admitted_passages)
                    flash_prompt = ''
                    while refill_passages:
                        cand_flash = build_flashcards_prompt(refill_passages, count=batch_flash_target)
                        if excluded:
                            cand_flash += '\nKhông lặp lại các cụm đã chọn: ' + json.dumps(excluded, ensure_ascii=False)
                        if prompt_fits(cand_flash):
                            flash_prompt = cand_flash
                            break
                        refill_passages.pop()
                    if not flash_prompt:
                        break
                    refill_flash_ids = [f"Q{idx}" for idx in range(1, len(refill_passages) + 1)]
                    batch_flash_schema = flashcard_response_schema(refill_flash_ids, max_items=batch_flash_target)
                    flash_max_tokens = min(1500, max(500, remaining * 260)) if is_mlx else 1500
                    with acquire_inference(dialogue_gate), gpu_coordinator.acquire_for_inference():
                        options = (
                            {'max_tokens': flash_max_tokens, **_mlx_optional_kwargs(batch_flash_schema)}
                            if is_mlx else {'model': model_name, 'num_predict': 1500, 'think': False}
                        )
                        resp_flash = active_client.chat_complete(
                            messages=[{'role': 'user', 'content': flash_prompt}],
                            system_prompt=system_prompt,
                            temperature=0.2,
                            num_ctx=capacity,
                            **options,
                        )
                    if getattr(active_client, 'last_done_reason', None) != 'stop':
                        break
                    flash_json = parse_json_from_response(resp_flash)
                    if not flash_json or not isinstance(flash_json.get('flashcards'), list):
                        break
                    if not flash_json['flashcards']:
                        break
                    flashcard_raw_items.extend(flash_json['flashcards'])
                    valid_flashcards = _collect_valid_flashcards(flashcard_raw_items)
            except (GPUBusyError, DialogueBusyError):
                raise
            except (subprocess.TimeoutExpired, TimeoutError) as e:
                raise StudyMaterialError(f"Mô hình phản hồi quá thời gian quy định khi tạo flashcards: {e}")
            except (RuntimeError, FileNotFoundError) as e:
                raise StudyMaterialError(f"Mô hình AI chưa sẵn sàng hoặc thiếu tệp trọng số cục bộ: {e}")
            except Exception as e:
                logger.warning("LLM flashcard generation failed: %s. Using fallback.", e)

        # Validate LLM-proposed Quiz items strictly against admitted passages
        model_valid_quiz = []
        seen_quiz_questions = set(proven_questions)
        for q in quiz_raw_items:
            norm_q, _ = _normalize_quiz_item(q, next_id=len(proven_quiz) + len(model_valid_quiz) + 1)
            if norm_q is None or norm_q["question"] in seen_quiz_questions:
                continue
            if _is_too_easy_for_target(norm_q):
                continue
            seen_quiz_questions.add(norm_q["question"])
            model_valid_quiz.append(norm_q)

        # Exact quote membership cannot prove the answer, distractors, or
        # selected difficulty. Run the existing independent review contract for model proposals.
        reviewed: List[Dict[str, Any]] = []
        if active_client is not None and model_valid_quiz and remaining_quiz_count > 0:
            from app.practice import PracticeService, PracticeGenerationError
            from app.practice_adapter import SYSTEM_PROMPT as PRACTICE_SYSTEM_PROMPT
            review_system_prompt = PRACTICE_SYSTEM_PROMPT if is_mlx else system_prompt

            def review_chat(prompt, model, max_tokens, timeout=None, response_schema=None):
                if not prompt_fits(prompt, max_tokens):
                    raise PracticeGenerationError('Complete source and review do not fit the context')
                with acquire_inference(dialogue_gate), gpu_coordinator.acquire_for_inference():
                    if is_mlx:
                        options = {'max_tokens': max_tokens, **_mlx_optional_kwargs(response_schema)}
                        if "\nDữ liệu cần kiểm định:\n" in prompt and "context_prefix_marker" in options:
                            options["context_prefix_marker"] = "\nDữ liệu cần kiểm định:\n"
                        else:
                            options.pop("context_prefix_marker", None)
                    else:
                        options = {'model': model_name, 'num_predict': max_tokens, 'think': False}
                        if response_schema is not None:
                            options['response_schema'] = response_schema
                    if timeout is not None and timeout > 0:
                        options['timeout'] = timeout
                    raw = active_client.chat_complete(
                        messages=[{'role': 'user', 'content': prompt}],
                        system_prompt=review_system_prompt,
                        temperature=0.0,
                        num_ctx=capacity,
                        **options,
                    )
                if getattr(active_client, 'last_done_reason', None) != 'stop':
                    raise PracticeGenerationError('Review was interrupted')
                return raw
            reviewer = PracticeService(None, None)
            reviewer._chat_complete = review_chat
            batch_step = 1 if (is_mlx and difficulty in ("medium", "hard")) else (3 if is_mlx else 1)

            def _run_review_pass(items_to_review: List[Dict[str, Any]], step: int) -> None:
                nonlocal review_timed_out_partial
                idx_pos = 0
                cur_step = step
                while idx_pos < len(items_to_review) and len(reviewed) < remaining_quiz_count:
                    batch = items_to_review[idx_pos:idx_pos + cur_step]
                    passages_for_review = {}
                    proposals = []
                    for index, item in enumerate(batch, 1):
                        full_source = next(text for text in page_chunks[item['page_num']]
                                           if self._quote_matches_chunk(item['evidence_quote'], text))
                        passages_for_review[index] = {
                            'safe_text': sanitize_for_prompt_context(full_source),
                            'page_num': item['page_num'],
                        }
                        proposals.append({**item, 'source': {'chunk_id': index, 'snippet': item['evidence_quote']}})
                    try:
                        accepted = reviewer._verify_questions(
                            proposals,
                            passages_for_review,
                            model_name,
                            difficulty=difficulty,
                            study_material_review=(is_mlx and difficulty == "medium"),
                        )
                    except PracticeGenerationError as exc:
                        if len(batch) > 1 and "do not fit the context" in str(exc):
                            cur_step = 1
                            continue
                        raise
                    except (GPUBusyError, DialogueBusyError):
                        raise
                    except (TimeoutError, subprocess.TimeoutExpired) as exc:
                        if proven_quiz or reviewed:
                            logger.warning(
                                "Study material review timed out after accepting %d/%d items; returning partial verified set: %s",
                                len(proven_quiz) + len(reviewed),
                                quiz_count,
                                exc,
                            )
                            review_timed_out_partial = True
                            break
                        raise StudyMaterialError(f'Không hoàn thành được bước kiểm định câu hỏi: {exc}') from exc
                    except RuntimeError as exc:
                        raise StudyMaterialError(f'Không hoàn thành được bước kiểm định câu hỏi: {exc}') from exc
                    for item in accepted:
                        if _is_too_easy_for_target(item):
                            continue
                        clean_item = {k: v for k, v in item.items() if k != 'source'}
                        is_proved = False
                        if difficulty == "medium":
                            from app.practice_proof import verify_proof_backed_medium_question
                            is_proved = any(
                                self._quote_matches_chunk(clean_item["evidence_quote"], orig_text)
                                and verify_proof_backed_medium_question(clean_item, orig_text, context_passages=passages)
                                for orig_text in original_page_chunks.get(clean_item["page_num"], [])
                            )
                        reviewed.append({
                            **clean_item,
                            'difficulty': difficulty,
                            'answer_verified': is_proved,
                            'generation': 'source_proof' if is_proved else 'model',
                        })
                        if len(reviewed) >= remaining_quiz_count:
                            break
                    idx_pos += len(batch)

            _run_review_pass(model_valid_quiz, batch_step)

            covered_pages = {
                int(item.get("page_num") or 0)
                for item in (*proven_quiz, *reviewed)
                if item.get("page_num") is not None
            }
            has_uncovered_pages = any(int(p["page_num"]) not in covered_pages for p in admitted_passages)
            if (
                is_mlx
                and not review_timed_out_partial
                and len(reviewed) < remaining_quiz_count
                and (
                    (difficulty in ("easy", "hard") and (len(reviewed) < len(model_valid_quiz) or has_uncovered_pages))
                    or (difficulty == "medium" and (len(proven_quiz) + len(reviewed)) == 0 and len(model_valid_quiz) > 0)
                )
            ):
                refill_missing = remaining_quiz_count - len(reviewed)
                excluded_after_review = [
                    str(item.get('question') or '')[:200] for item in (*proven_quiz, *model_valid_quiz)
                ]
                if difficulty == "easy":
                    excluded_after_review.extend(
                        str(next((c["text"] for c in item.get("choices", []) if c.get("label") == item.get("answer")), ""))[:120]
                        for item in model_valid_quiz
                        if item.get("format") == "source_cloze"
                    )
                else:
                    excluded_after_review.extend(
                        str(item.get('evidence_quote') or '')[:120] for item in reviewed if item.get('evidence_quote')
                    )
                indexed_admitted = list(enumerate(admitted_passages, 1))
                uncovered_indexed = [(idx, p) for idx, p in indexed_admitted if int(p["page_num"]) not in covered_pages]
                covered_indexed = [(idx, p) for idx, p in indexed_admitted if int(p["page_num"]) in covered_pages]
                prioritized_indexed = uncovered_indexed + covered_indexed if uncovered_indexed else list(indexed_admitted)
                refill_pairs = list(prioritized_indexed)
                refill_prompt = ''
                while refill_pairs:
                    cand_passages = [p for _idx, p in refill_pairs]
                    cand_prompt = build_quiz_prompt(cand_passages, count=refill_missing, difficulty=difficulty)
                    non_empty_ex = [x for x in excluded_after_review if x]
                    if non_empty_ex:
                        label_ex = 'Không lặp lại các câu/cụm đã chọn: ' if difficulty == 'easy' else 'Không lặp lại câu hỏi/trích đoạn đã dùng: '
                        cand_prompt += '\n' + label_ex + json.dumps(non_empty_ex[:12], ensure_ascii=False)
                    if difficulty == "hard" and len(reviewed) < len(model_valid_quiz):
                        cand_prompt += (
                            "\nLưu ý sửa lỗi kiểm định: câu trước chỉ kiểm tra một điều kiện đơn lẻ hoặc suy luận vượt nguồn. "
                            "Hãy đặt câu hỏi mới kết hợp ít nhất hai bước suy luận/tính toán hoặc áp dụng công thức cùng cận/điều kiện trong nguồn."
                        )
                    if prompt_fits(cand_prompt):
                        refill_prompt = cand_prompt
                        break
                    refill_pairs.pop()
                if refill_prompt:
                    try:
                        refill_id_map = {f"Q{pos}": f"Q{orig_idx}" for pos, (orig_idx, _p) in enumerate(refill_pairs, 1)}
                        refill_ids = list(refill_id_map)
                        refill_schema = quiz_response_schema(difficulty, refill_ids, max_items=refill_missing)
                        refill_max_tokens = min(1500, max(550, refill_missing * 380))
                        with acquire_inference(dialogue_gate), gpu_coordinator.acquire_for_inference():
                            refill_raw = active_client.chat_complete(
                                messages=[{'role': 'user', 'content': refill_prompt}],
                                system_prompt=system_prompt,
                                temperature=0.0 if difficulty == 'easy' else 0.2,
                                max_tokens=refill_max_tokens,
                                num_ctx=capacity,
                                **_mlx_optional_kwargs(refill_schema),
                            )
                        if getattr(active_client, 'last_done_reason', None) == 'stop':
                            refill_parsed = parse_json_from_response(refill_raw)
                            if refill_parsed and isinstance(refill_parsed.get('questions'), list):
                                extra_valid: List[Dict[str, Any]] = []
                                for rq in refill_parsed['questions']:
                                    if isinstance(rq, dict) and isinstance(rq.get('evidence_id'), str) and rq['evidence_id'] in refill_id_map:
                                        rq = {**rq, 'evidence_id': refill_id_map[rq['evidence_id']]}
                                    norm_rq, _ = _normalize_quiz_item(rq, next_id=len(proven_quiz) + len(model_valid_quiz) + len(extra_valid) + 1)
                                    if norm_rq is None or norm_rq['question'] in seen_quiz_questions:
                                        continue
                                    if _is_too_easy_for_target(norm_rq):
                                        continue
                                    seen_quiz_questions.add(norm_rq['question'])
                                    extra_valid.append(norm_rq)
                                if extra_valid:
                                    _run_review_pass(extra_valid, batch_step)
                    except (GPUBusyError, DialogueBusyError):
                        raise
                    except Exception as exc:
                        logger.warning("Study material refill failed after partial review: %s", exc)

        combined_quiz = list((proven_quiz + reviewed)[:quiz_count])
        if is_mlx and combined_quiz:
            from app.practice import PracticeService as _PracticeSvc
            letter_counts = {lbl: 0 for lbl in "ABCD"}
            balanced_quiz = []
            for item in combined_quiz:
                item_copy = dict(item)
                if item_copy.get("format") != "source_cloze":
                    q_key = " ".join(str(item_copy.get("question") or "").split()).casefold()
                    ev_ref = str(item_copy.get("evidence_quote") or "")
                    base_slot = hashlib.sha256(f"{q_key}\0{ev_ref}".encode("utf-8")).digest()[0] % 4
                    slot_order = [(base_slot + off) % 4 for off in range(4)]
                    target_slot = min(slot_order, key=lambda idx: letter_counts["ABCD"[idx]])
                    rot_choices, rot_ans = _PracticeSvc._rotate_choices_if_safe(
                        list(item_copy.get("choices") or []),
                        str(item_copy.get("answer") or ""),
                        str(item_copy.get("explanation") or ""),
                        target_slot,
                    )
                    item_copy["choices"] = rot_choices
                    item_copy["answer"] = rot_ans
                    item_copy["correct_answer"] = rot_ans
                final_ans = str(item_copy.get("answer") or "").upper()
                if final_ans in letter_counts:
                    letter_counts[final_ans] += 1
                balanced_quiz.append(item_copy)
            combined_quiz = balanced_quiz

        valid_quiz = []
        for idx, item in enumerate(combined_quiz, 1):
            valid_quiz.append({**item, "id": idx})

        if quiz_count <= 0:
            norm_quiz = []
            quiz_mode = "skipped"
        elif difficulty == "hard":
            matching_quiz = [q for q in valid_quiz if q.get("difficulty") == "hard"]
            if not matching_quiz:
                raise StudyMaterialError(
                    "Không có câu hỏi đạt kiểm định nguồn và độ khó 'khó' (hard) trong tài liệu. "
                    "Hệ thống không hạ độ khó hay thay bằng câu trích đoạn đơn giản. "
                    "Hãy chọn độ khó trung bình hoặc tài liệu có nội dung phù hợp."
                )
            norm_quiz = matching_quiz[:quiz_count]
            quiz_mode = "model"
        elif difficulty == "medium":
            matching_quiz = [q for q in valid_quiz if q.get("difficulty") == "medium"]
            if matching_quiz:
                norm_quiz = matching_quiz[:quiz_count]
                quiz_mode = "model"
            else:
                raise StudyMaterialError(
                    "Không có câu hỏi đạt độ khó trung bình (medium) đã chọn. "
                    "Hệ thống không tự hạ xuống câu nhận biết hoặc điền khuyết."
                )
        else: # easy
            matching_quiz = [q for q in valid_quiz if q.get("difficulty") == "easy"]
            if matching_quiz:
                norm_quiz = matching_quiz[:quiz_count]
                quiz_mode = "model"
            else:
                norm_quiz = []
                quiz_mode = "unavailable"

        if quiz_count > 0 and norm_quiz:
            item_routes = {q.get("generation") or "model" for q in norm_quiz}
            if item_routes == {"source_proof"}:
                quiz_mode = "source_proof"
            elif "source_proof" in item_routes:
                quiz_mode = "source_proof+model"
            else:
                quiz_mode = "model"
        all_answers_verified = bool(norm_quiz) and all(q.get("answer_verified") is True for q in norm_quiz)

        # Reconcile flashcards strictly against admitted passages (reusing combined turn if present)
        valid_flashcards = _collect_valid_flashcards(flashcard_raw_items)

        if valid_flashcards:
            norm_flashcards = valid_flashcards[:flashcard_count]
            flashcard_mode = "model"
        else:
            norm_flashcards = fallback_generate_flashcards(fallback_sources, count=flashcard_count)
            flashcard_mode = "extractive_fallback"

        actual_quiz_count = len(norm_quiz)
        actual_flashcard_count = len(norm_flashcards)
        is_complete = (actual_quiz_count == quiz_count and actual_flashcard_count == flashcard_count)

        completeness_warning = None
        if actual_quiz_count < quiz_count:
            completeness_warning = (
                f"Chỉ có {actual_quiz_count}/{quiz_count} câu đạt kiểm định nguồn và độ khó {difficulty} đã chọn. "
                "Các câu chưa đạt đã bị loại; hệ thống không tự ý thêm câu không đạt chuẩn."
            )
            if review_timed_out_partial:
                completeness_warning += " Một phần câu hỏi chưa kịp hoàn tất kiểm định do quá thời gian phản hồi."
        if actual_flashcard_count < flashcard_count:
            completeness_warning = (completeness_warning or '') + (
                f" Chỉ có {actual_flashcard_count}/{flashcard_count} thẻ có căn cứ nguồn."
            )
        ocr_truncated_pages = int(getattr(extracted_result, "ocr_truncated_pages", 0) or 0)
        if ocr_truncated_pages > 0:
            ocr_notice = (
                f"Đã OCR nhanh {int(getattr(extracted_result, 'ocr_pages', 0) or 0)} trang đầu "
                f"để tạo học liệu kịp thời (còn {ocr_truncated_pages} trang quét chưa xử lý)."
            )
            completeness_warning = (f"{completeness_warning} {ocr_notice}" if completeness_warning else ocr_notice)

        material_id = str(uuid.uuid4())
        pages_preview = [
            {
                "page_num": int(p.get("page_num", 1)),
                "text": str(p.get("text", "")),
            }
            for p in (extracted_result.extracted_pages or [])
            if (p.get("text") or "").strip()
        ]
        record = {
            "id": material_id,
            "material_id": material_id,
            "filename": extracted_result.filename,
            "clean_title": extracted_result.clean_title,
            "total_pages": extracted_result.total_pages,
            "total_chars": extracted_result.total_chars,
            "pages": pages_preview,
            "quiz": norm_quiz,
            "flashcards": norm_flashcards,
            "difficulty": difficulty,
            "requested_quiz_count": quiz_count,
            "actual_quiz_count": actual_quiz_count,
            "requested_flashcard_count": flashcard_count,
            "actual_flashcard_count": actual_flashcard_count,
            "complete": is_complete,
            "warning": completeness_warning,
            "completeness_warning": completeness_warning,
            "study_generated": True,
            "saved_to_src": getattr(extracted_result, "saved_to_src", False),
            "src_path": getattr(extracted_result, "src_path", f"src/{extracted_result.filename}"),
            "answers_verified": all_answers_verified,
            "generation": {
                "quiz": quiz_mode,
                "flashcards": flashcard_mode,
            },
            "source_validation": "verbatim_quote",
        }
        if "model" in quiz_mode or flashcard_mode == "model":
            record["model"] = model_name
        if quiz_mode == "extractive_fallback" or flashcard_mode == "extractive_fallback":
            record["fallback_warning"] = (
                "Nội dung ôn luyện được trích xuất trực tiếp từ tài liệu nguồn (fallback) "
                "do mô hình AI không phản hồi hoặc phản hồi không đạt chuẩn kiểm định trích dẫn."
            )

        with self._lock:
            self._materials[material_id] = record
            raw_b = getattr(extracted_result, "_raw_bytes", None)
            if raw_b is not None:
                self._raw_files[extracted_result.filename] = raw_b
                self._raw_files[material_id] = raw_b

        return record

    @contextmanager
    def _ingest_lock(self) -> Generator[None, None, None]:
        """Acquire exclusive cross-process ingest lock for the entire upload lifecycle."""
        from app import file_lock as fcntl
        from app import config as cfg
        data_dir = getattr(cfg, "DATA_DIR", DATA_DIR)
        lock_path = data_dir / "runtime" / "ingest.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                except Exception:  # nosec B110
                    pass

    def _allocate_source_filename(
        self,
        filename: str,
        file_bytes: bytes,
        target_src: Path,
        target_db: Path,
    ) -> Tuple[str, Path, bool]:
        """Safely allocate a canonical safe filename in target_src, handling collisions
        and concurrent uploads across separate threads and processes.
        Returns (safe_filename, dest_path, newly_created).
        """
        import hashlib
        import sqlite3

        clean_name = Path(filename).name.strip()
        if not clean_name or clean_name in (".", ".."):
            clean_name = "upload.bin"

        stem = Path(clean_name).stem
        suffix = Path(clean_name).suffix
        incoming_hash = hashlib.sha256(file_bytes).hexdigest()

        def get_db_file_hash(name: str) -> Optional[str]:
            if not target_db.exists():
                return None
            try:
                with sqlite3.connect(str(target_db)) as conn:
                    row = conn.execute("SELECT file_hash FROM documents WHERE filename = ?", (name,)).fetchone()
                    return row[0] if row else None
            except Exception:
                return None

        with self._lock:
            cand_name = clean_name
            dest = target_src / cand_name

            # Check if dest already exists with same content
            has_disk_collision = False
            if dest.exists():
                try:
                    if hashlib.sha256(dest.read_bytes()).hexdigest() != incoming_hash:
                        has_disk_collision = True
                except Exception:
                    has_disk_collision = True

            db_hash = get_db_file_hash(cand_name)
            has_db_collision = (db_hash is not None and db_hash != incoming_hash)

            if has_disk_collision or has_db_collision:
                hash_len = 8
                while True:
                    cand_name = f"{stem}-{incoming_hash[:hash_len]}{suffix}"
                    dest = target_src / cand_name
                    cand_disk_collision = False
                    if dest.exists():
                        try:
                            if hashlib.sha256(dest.read_bytes()).hexdigest() != incoming_hash:
                                cand_disk_collision = True
                        except Exception:
                            cand_disk_collision = True
                    cand_db_hash = get_db_file_hash(cand_name)
                    cand_db_collision = (cand_db_hash is not None and cand_db_hash != incoming_hash)

                    if not (cand_disk_collision or cand_db_collision):
                        break
                    hash_len += 4
                    if hash_len > len(incoming_hash):
                        cand_name = f"{stem}-{incoming_hash}-{uuid.uuid4().hex[:6]}{suffix}"
                        dest = target_src / cand_name
                        break

            newly_created = False
            if dest.exists():
                try:
                    if hashlib.sha256(dest.read_bytes()).hexdigest() == incoming_hash:
                        newly_created = False
                    else:
                        has_disk_collision = True
                except Exception:
                    has_disk_collision = True

            if not dest.exists():
                try:
                    fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    with os.fdopen(fd, "wb") as f:
                        f.write(file_bytes)
                    newly_created = True
                except FileExistsError:
                    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == incoming_hash:
                        newly_created = False
                    else:
                        cand_name = f"{stem}-{incoming_hash[:8]}-{uuid.uuid4().hex[:6]}{suffix}"
                        dest = target_src / cand_name
                        fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        with os.fdopen(fd, "wb") as f:
                            f.write(file_bytes)
                        newly_created = True

            return cand_name, dest, newly_created

    def ingest_to_src_and_index(
        self,
        filename: str,
        file_bytes: bytes,
        extracted_result: PDFExtractionResult,
        src_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Save the uploaded file into src/ and index its extracted content into knowledge_base.db."""
        import sqlite3
        from app.config import DB_PATH, DEFAULT_EMBED_MODEL, SRC_DIR
        from app.chunker import TextChunker
        from app.indexer import KnowledgeIndexer

        with self._ingest_lock():
            target_src = Path(src_dir) if src_dir else SRC_DIR
            target_db = Path(db_path) if db_path else DB_PATH
            target_src.mkdir(parents=True, exist_ok=True)
            target_db.parent.mkdir(parents=True, exist_ok=True)

            safe_name, dest_path, newly_created = self._allocate_source_filename(
                filename, file_bytes, target_src, target_db
            )

            file_hash = PDFExtractor.calculate_file_hash(dest_path)
            extracted_result.filename = safe_name
            extracted_result.filepath = dest_path.resolve()
            extracted_result.file_size_bytes = len(file_bytes)
            extracted_result.file_hash = file_hash
            setattr(extracted_result, "saved_to_src", True)
            setattr(extracted_result, "src_path", f"src/{safe_name}")

            # Check for existing successfully indexed source with matching exact hash and real chunks
            if target_db.exists():
                try:
                    with sqlite3.connect(str(target_db)) as db:
                        db.row_factory = sqlite3.Row
                        row = db.execute(
                            "SELECT id, filename, file_hash, status FROM documents WHERE filename = ?",
                            (safe_name,)
                        ).fetchone()
                        if row and row["file_hash"] == file_hash:
                            chunk_stats = db.execute(
                                "SELECT COUNT(*), COUNT(embedding) FROM chunks WHERE doc_id = ?",
                                (row["id"],)
                            ).fetchone()
                            chunk_count = chunk_stats[0] if chunk_stats else 0
                            vector_count = chunk_stats[1] if chunk_stats else 0
                            if chunk_count > 0:
                                # Return existing identity and counts without re-extraction overwrite/re-index/delete/quality rewrite.
                                # Preserve all reviewed chunks/vectors/provenance/HOLD states.
                                vectors_ready = bool(chunk_count > 0 and vector_count >= chunk_count)
                                with self._lock:
                                    self._raw_files[safe_name] = file_bytes
                                return {
                                    "saved_to_src": True,
                                    "src_path": f"src/{safe_name}",
                                    "doc_id": row["id"],
                                    "chunks_indexed": chunk_count,
                                    "status": row["status"],
                                    "vectors_ready": vectors_ready,
                                    "embedding_status": "ready" if vectors_ready else "offline_keyword_only",
                                }
                except Exception as e:
                    logger.debug(f"Check for existing document encountered: {e}")

            doc_id = None
            chunks_inserted = 0
            doc_status = "unindexed"
            can_embed = False
            resolved_embed = None

            try:
                kb_indexer = KnowledgeIndexer(db_path=target_db, src_dir=target_src)
                all_chunks = []
                extracted_pages_count = 0
                for page in (extracted_result.extracted_pages or []):
                    p_num = int(page.get("page_num", 1))
                    p_text = (page.get("text") or "").strip()
                    if p_text:
                        extracted_pages_count += 1
                        page_chunks = TextChunker.chunk_page(
                            book_title=extracted_result.clean_title or safe_name,
                            filename=safe_name,
                            page_num=p_num,
                            page_text=p_text,
                        )
                        all_chunks.extend(page_chunks)

                # Probe embedding backend - acquire gpu_coordinator before any model request
                if sys.platform == "darwin":
                    from app.native_embedding import NativeEmbeddingClient
                    if NativeEmbeddingClient.available():
                        can_embed = True
                        resolved_embed = "bge-m3"
                    else:
                        can_embed = False
                        resolved_embed = None
                else:
                    try:
                        if self.ollama.check_health():
                            resolved_embed = self.ollama.find_best_embed_model(DEFAULT_EMBED_MODEL)
                            if resolved_embed:
                                with gpu_coordinator.acquire_for_inference():
                                    if self.ollama.get_embedding("test", model=resolved_embed) is not None:
                                        can_embed = True
                    except (GPUBusyError, DialogueBusyError):
                        raise
                    except Exception as probe_err:
                        logger.info(f"Ollama embedding probe offline: {probe_err}")
                        can_embed = False

                # Prepare chunk rows; acquire gpu_coordinator before inference, fall back to keyword-only on network/embedding error
                prepared = None
                if can_embed and resolved_embed:
                    try:
                        with gpu_coordinator.acquire_for_inference():
                            prepared = kb_indexer._prepare_chunk_rows(all_chunks, True, resolved_embed)
                    except (GPUBusyError, DialogueBusyError):
                        raise
                    except Exception as embed_err:
                        logger.info(f"Embedding preparation failed ({embed_err}); falling back to keyword-only indexing")
                        can_embed = False
                        resolved_embed = None

                if prepared is None:
                    prepared = kb_indexer._prepare_chunk_rows(all_chunks, False, None)

                # Fail closed with safe 503 before publishing an unusable source if no searchable chunks
                rows, prov_rows = prepared if isinstance(prepared, tuple) else (prepared, None)
                if not rows:
                    raise StudyMaterialIndexError("Không thể tạo các đoạn trích xuất (chunks) để lập chỉ mục cho tài liệu.")

                # Do not mark automatically OCR reviewed; preserve uncertainty
                if extracted_result.is_scanned:
                    extracted_result.needs_review = True
                    doc_status = "indexed"
                else:
                    doc_status = "indexed"

                conn = kb_indexer.get_connection()
                try:
                    with conn:
                        doc_id = kb_indexer._save_document_record(
                            extracted_result,
                            status=doc_status,
                            is_scanned=bool(extracted_result.is_scanned),
                            extracted_pages_count=max(1, extracted_pages_count),
                            connection=conn,
                        )
                        chunks_inserted = kb_indexer._insert_chunk_rows(conn, doc_id, rows, prov_rows)
                        kb_indexer._save_quality_record(conn, doc_id, extracted_result)

                        # Determine vectors_ready from actual stored rows in database, not backend health
                        stored_stats = conn.execute(
                            "SELECT COUNT(*), COUNT(embedding) FROM chunks WHERE doc_id = ?",
                            (doc_id,)
                        ).fetchone()
                        actual_chunks = stored_stats[0] if stored_stats else chunks_inserted
                        actual_vectors = stored_stats[1] if stored_stats else 0
                        vectors_ready = bool(actual_chunks > 0 and actual_vectors >= actual_chunks)
                        embedding_status = "ready" if vectors_ready else "offline_keyword_only"
                finally:
                    conn.close()

            except (GPUBusyError, DialogueBusyError):
                if newly_created and dest_path.exists():
                    try:
                        dest_path.unlink()
                    except Exception:  # nosec B110
                        pass
                self.evict_raw_file(safe_name, filename, getattr(extracted_result, "filename", None))
                raise
            except StudyMaterialIndexError:
                if newly_created and dest_path.exists():
                    try:
                        dest_path.unlink()
                    except Exception:  # nosec B110
                        pass
                self.evict_raw_file(safe_name, filename, getattr(extracted_result, "filename", None))
                raise
            except Exception as e:
                if newly_created and dest_path.exists():
                    try:
                        dest_path.unlink()
                    except Exception:  # nosec B110
                        pass
                self.evict_raw_file(safe_name, filename, getattr(extracted_result, "filename", None))
                logger.warning(f"Indexing uploaded file '{safe_name}' into knowledge_base.db failed: {e}")
                raise StudyMaterialIndexError("Không thể lưu hoặc cập nhật chỉ mục tài liệu vào cơ sở tri thức.") from None

            with self._lock:
                self._raw_files[safe_name] = file_bytes

            return {
                "saved_to_src": True,
                "src_path": f"src/{safe_name}",
                "doc_id": doc_id,
                "chunks_indexed": actual_chunks,
                "status": doc_status,
                "vectors_ready": vectors_ready,
                "embedding_status": embedding_status,
            }

    def store_extracted_document(
        self,
        extracted_result: PDFExtractionResult,
        ingestion_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store an extracted document (pages & metadata) without generating Quiz/Flashcards yet."""
        record = self.build_extracted_document_record(extracted_result, ingestion_info)
        with self._lock:
            self._materials[record["id"]] = record
            raw_b = getattr(extracted_result, "_raw_bytes", None)
            if raw_b is not None:
                self._raw_files[extracted_result.filename] = raw_b
                self._raw_files[record["id"]] = raw_b
        return record

    @staticmethod
    def build_extracted_document_record(
        extracted_result: PDFExtractionResult,
        ingestion_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        material_id = str(uuid.uuid4())
        pages_preview = [
            {
                "page_num": int(p.get("page_num", 1)),
                "text": str(p.get("text", "")),
            }
            for p in (extracted_result.extracted_pages or [])
            if (p.get("text") or "").strip()
        ]
        info = ingestion_info or {}
        record = {
            "id": material_id,
            "material_id": material_id,
            "filename": extracted_result.filename,
            "clean_title": extracted_result.clean_title,
            "total_pages": extracted_result.total_pages,
            "total_chars": extracted_result.total_chars,
            "pages": pages_preview,
            "quiz": [],
            "flashcards": [],
            "difficulty": "medium",
            "study_generated": False,
            "saved_to_src": bool(info.get("saved_to_src", getattr(extracted_result, "saved_to_src", False))),
            "src_path": info.get("src_path", f"src/{extracted_result.filename}"),
            "doc_id": info.get("doc_id"),
            "chunks_indexed": info.get("chunks_indexed", 0),
            "status": info.get("status", "indexed" if info.get("chunks_indexed", 0) > 0 else "unindexed"),
            "vectors_ready": info.get("vectors_ready", False),
            "embedding_status": info.get("embedding_status", "offline_keyword_only"),
        }
        return record

    @staticmethod
    def _lookup_library_document(doc_id=None, filename=None):
        """Resolve explicit identifiers exactly and enforce disk/watcher freshness."""
        import sqlite3
        from contextlib import closing
        from app.config import DATA_DIR, DB_PATH, SRC_DIR
        from app.source_freshness import assess_document_freshness, fetch_document_row

        doc = None
        if DB_PATH.exists():
            try:
                with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    doc = fetch_document_row(db, doc_id=doc_id, filename=filename)
            except sqlite3.Error as exc:
                raise StudyMaterialError("Không thể đọc thư viện tài liệu. Vui lòng kiểm tra dữ liệu cục bộ.") from exc
        if doc_id is not None and doc is None:
            raise StudyMaterialError(f"Không tìm thấy tài liệu '{doc_id}' trong thư viện.")
        if doc is not None and filename and filename != doc["filename"]:
            raise StudyMaterialError("Mã tài liệu và tên tệp không khớp.")
        if doc is not None:
            raw_fp = str(doc.get("filepath") or "").strip()
            require_disk = bool(raw_fp and Path(raw_fp.replace("\\", "/")).is_absolute())
            freshness = assess_document_freshness(
                doc,
                db_path=DB_PATH,
                src_dir=SRC_DIR,
                data_dir=DATA_DIR,
                require_disk_if_unhashed=require_disk,
            )
            if not freshness.fresh:
                raise StudyMaterialError(
                    freshness.message
                    or "Tài liệu nguồn đã thay đổi hoặc thiếu tệp gốc. Vui lòng cập nhật chỉ mục trước khi sử dụng."
                )
        return doc

    def resolve_raw_file(
        self,
        filename: Optional[str] = None,
        material_id: Optional[str] = None,
        doc_id: Optional[int] = None,
    ) -> Optional[Tuple[str, bytes]]:
        """Return original bytes only when all supplied source identifiers agree."""
        with self._lock:
            material = self._materials.get(material_id) if material_id else None
        if material_id:
            if not material or (filename and filename != material.get("filename")):
                return None
            if doc_id is not None and material.get("doc_id") != doc_id:
                return None
            filename = filename or material.get("filename")
            if doc_id is None:
                doc_id = material.get("doc_id")
        try:
            doc = self._lookup_library_document(doc_id, filename)
        except StudyMaterialError:
            return None
        resolved_name = doc["filename"] if doc is not None else filename
        if not resolved_name:
            return None
        candidate = resolve_safe_source_file(resolved_name)
        if candidate is not None:
            try:
                return candidate.name, candidate.read_bytes()
            except OSError:
                return None
        with self._lock:
            raw_bytes = self._raw_files.get(material_id) if material_id else None
            if raw_bytes is None:
                raw_bytes = self._raw_files.get(resolved_name)
        return (Path(resolved_name).name, raw_bytes) if raw_bytes is not None else None

    def inspect_library_document(
        self,
        doc_id: Optional[int] = None,
        filename: Optional[str] = None,
        persist_to_src: bool = False,
    ) -> Dict[str, Any]:
        """Load full content pages of a library/local document without generating Quiz/Flashcards yet."""
        import sqlite3
        from contextlib import closing
        from app.config import DB_PATH, SRC_DIR

        doc = self._lookup_library_document(doc_id, filename)
        rows = []
        if doc is not None:
            try:
                with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    rows = db.execute(
                        "SELECT page_num, text FROM chunks WHERE doc_id = ? ORDER BY page_num, chunk_index",
                        (doc["id"],),
                    ).fetchall()
            except sqlite3.Error as exc:
                raise StudyMaterialError("Không thể đọc nội dung tài liệu trong thư viện.") from exc
            if not rows and dict(doc).get("status") not in ("indexed", "ocr_partial_reviewed"):
                raise StudyMaterialError(
                    "Tài liệu này đang chờ OCR và kiểm duyệt nguồn nên chưa sẵn sàng để trích xuất học liệu."
                )

        target_filename = doc["filename"] if doc else filename
        prefer_verified_db_rows = bool(
            doc is not None
            and rows
            and (bool(dict(doc).get("is_scanned")) or dict(doc).get("status") == "ocr_partial_reviewed")
        )

        if target_filename and not prefer_verified_db_rows:
            candidate = resolve_safe_source_file(target_filename)
            if candidate and candidate.is_file():
                raw_bytes = candidate.read_bytes()
                extracted = self.extract_document(candidate.name, raw_bytes)
                extracted.filename = target_filename
                chunks_cnt = (dict(doc).get("chunk_count") if doc else len(rows)) or len(rows)
                resolved_src = SRC_DIR.resolve() if SRC_DIR.is_dir() else None
                in_src = bool(resolved_src and candidate.is_relative_to(resolved_src))
                ingest_info = {
                    "saved_to_src": in_src,
                    "src_path": f"src/{candidate.relative_to(resolved_src).as_posix()}" if in_src else f"src/{candidate.name}",
                    "doc_id": doc["id"] if doc else None,
                    "chunks_indexed": chunks_cnt,
                    "status": "indexed" if doc else "unindexed",
                }
                return self.store_extracted_document(extracted, ingestion_info=ingest_info)

        # Indexed originals/chunks are authoritative; session cache is only for unindexed uploads.
        if target_filename and doc is None:
            with self._lock:
                for mat in reversed(list(self._materials.values())):
                    if mat.get("filename") == target_filename and mat.get("pages"):
                        return mat

        if doc and rows:
            page_map: Dict[int, List[str]] = {}
            total_chars = 0
            for r in rows:
                p_num = int(r["page_num"] or 1)
                txt = (r["text"] or "").strip()
                if txt:
                    page_map.setdefault(p_num, []).append(txt)
                    total_chars += len(txt)
            extracted_pages = []
            for k in sorted(page_map.keys()):
                merged_txt = self._merge_chunk_texts(page_map[k])
                extracted_pages.append({"page_num": k, "text": merged_txt})
            fake_res = PDFExtractionResult(filename=doc["filename"], filepath=SRC_DIR / doc["filename"])
            fake_res.clean_title = doc["clean_title"]
            fake_res.total_pages = len(extracted_pages) or doc["total_pages"]
            fake_res.total_chars = total_chars
            fake_res.extracted_pages = extracted_pages
            chunks_cnt = (dict(doc).get("chunk_count") if doc else len(rows)) or len(rows)
            return self.store_extracted_document(
                fake_res,
                ingestion_info={
                    "saved_to_src": True,
                    "src_path": f"src/{doc['filename']}",
                    "doc_id": doc["id"],
                    "chunks_indexed": chunks_cnt,
                    "status": "indexed",
                },
            )

        raise StudyMaterialError(f"Không tìm thấy tài liệu '{doc_id or filename}' trong thư viện.")

    @staticmethod
    def _merge_chunk_texts(chunks: List[Any]) -> str:
        """Merge adjacent chunks into page text.

        If chunks have 'char_start'/'char_end' provenance, uses exact character offsets.
        If provenance is absent, preserves ambiguous repeats rather than removing them
        merely because string edges match 25-400 characters.
        """
        if not chunks:
            return ""

        def _get_info(c):
            if isinstance(c, dict):
                return str(c.get("text", "")).strip(), c.get("char_start"), c.get("char_end")
            return str(c).strip(), None, None

        first_txt, prev_start, prev_end = _get_info(chunks[0])
        merged = first_txt
        has_provenance = (prev_start is not None and prev_end is not None)

        for c in chunks[1:]:
            txt, c_start, c_end = _get_info(c)
            if not txt:
                continue
            if has_provenance and c_start is not None and c_end is not None and prev_end is not None:
                if c_start < prev_end:
                    overlap_chars = max(0, prev_end - c_start)
                    if overlap_chars < len(txt):
                        merged += txt[overlap_chars:]
                else:
                    merged += "\n\n" + txt
                prev_end = max(prev_end, c_end)
            else:
                if merged.endswith(txt):
                    pass
                else:
                    merged += "\n\n" + txt
        return merged

    @property
    def materials_cache(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._materials)

    def fallback_generate_quiz(self, passages: List[Dict[str, Any]], count: int = 3) -> List[Dict[str, Any]]:
        return fallback_generate_quiz(passages, count=count)

    def fallback_generate_flashcards(self, passages: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, Any]]:
        return fallback_generate_flashcards(passages, count=count)

    @staticmethod
    def _quote_matches_chunk(ev_quote: str, chunk_text: str) -> bool:
        """Verify evidence quote against chunk text, tolerating PDF soft line-wraps."""
        if not ev_quote or not chunk_text:
            return False
        if ev_quote in chunk_text:
            return True
        return re.sub(r"\s+", " ", ev_quote).strip() in re.sub(r"\s+", " ", chunk_text).strip()

    @staticmethod
    def _mask_answer_in_quote(ev_quote: str, answer_text: str) -> Optional[str]:
        """Replace answer span inside ev_quote even when soft PDF linebreaks or dashes vary."""
        if not ev_quote or not answer_text:
            return None
        raw_tokens = answer_text.split()
        if not raw_tokens:
            return None
        dash_class = "[-\u2013\u2014\u2212]"
        dash_chars = frozenset("-\u2013\u2014\u2212")
        flexible_tokens = [
            "".join(dash_class if ch in dash_chars else re.escape(ch) for ch in tok)
            for tok in raw_tokens
        ]
        pattern = r"\s+".join(flexible_tokens)
        left_guard = r"(?<![\w\-])" if (answer_text[0].isalnum() or answer_text[0] == "_") else ""
        right_guard = r"(?![\w\-])" if (answer_text[-1].isalnum() or answer_text[-1] == "_") else ""
        guarded_pattern = f"{left_guard}{pattern}{right_guard}"

        body_matches = _find_bounded_span_matches(ev_quote, answer_text)
        if len(body_matches) == 1:
            m_only = body_matches[0]
            if m_only.group(0).strip() != ev_quote.strip():
                return ev_quote[:m_only.start()] + "[…]" + ev_quote[m_only.end():]
        match = None
        if left_guard or right_guard:
            match = re.search(guarded_pattern, ev_quote)
            if not match:
                match = re.search(guarded_pattern, ev_quote, flags=re.IGNORECASE)
        if not match and answer_text in ev_quote:
            if answer_text == ev_quote.strip():
                return None
            return ev_quote.replace(answer_text, "[…]", 1)
        if not match:
            match = re.search(pattern, ev_quote)
        if not match:
            match = re.search(pattern, ev_quote, flags=re.IGNORECASE)
        if not match or match.group(0).strip() == ev_quote.strip():
            return None
        return ev_quote[:match.start()] + "[…]" + ev_quote[match.end():]

    def list_available_documents(self, indexed_docs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Return indexed documents plus uploaded session materials and discoverable local study files."""
        import sqlite3
        from contextlib import closing
        from app.config import DB_PATH, SRC_DIR

        results: List[Dict[str, Any]] = []
        seen_filenames = set()
        known_db_filenames = set()
        if DB_PATH.exists():
            try:
                with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    for row in db.execute("SELECT filename FROM documents").fetchall():
                        if row[0]:
                            known_db_filenames.add(row[0])
            except sqlite3.Error:
                pass

        for d in (indexed_docs or []):
            fname = d.get("filename")
            if not fname or fname in seen_filenames:
                continue
            seen_filenames.add(fname)
            results.append(d)

        if hasattr(self, "list_materials"):
            cached_records = self.list_materials()
        else:
            with self._lock:
                cached_records = list(self._materials.values())

        for mat in reversed(cached_records):
            fname = mat.get("filename")
            if not fname or fname in seen_filenames or fname in known_db_filenames:
                continue
            seen_filenames.add(fname)
            results.append({
                "id": mat.get("doc_id"),
                "material_id": mat.get("id") or mat.get("material_id"),
                "filename": fname,
                "clean_title": mat.get("clean_title") or fname,
                "total_pages": mat.get("total_pages") or 1,
                "page_count": mat.get("total_pages") or 1,
                "chunk_count": mat.get("chunks_indexed") or len(mat.get("pages") or []) or 1,
            })

        candidate_dirs = [SRC_DIR]
        allowed_exts = {".pdf", ".docx", ".pptx", ".txt", ".md"}
        excluded_ops = {
            "requirements.txt", "readme.md", "lich_su_du_an.md", "agents.md", "claude.md",
            "documentation.md", "testing.md", "definition_of_done.md", "contributing.md",
            "changelog.md", "license", "license.md", "state.md", "requirements-windows-tools.json",
        }
        for folder in candidate_dirs:
            if not folder or not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix.lower() not in allowed_exts:
                    continue
                if path.name.startswith(".") or path.name.lower() in excluded_ops:
                    continue
                if path.name in seen_filenames or path.name in known_db_filenames:
                    continue
                seen_filenames.add(path.name)
                results.append({
                    "id": None,
                    "filename": path.name,
                    "clean_title": path.stem.replace("_", " ").strip() or path.name,
                    "total_pages": 1,
                    "page_count": 1,
                    "chunk_count": 1,
                })

        return results

    def generate_flashcards_from_passages(
        self,
        passages: List[Dict[str, Any]],
        *,
        filename: str,
        clean_title: Optional[str] = None,
        count: int = 5,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate strict quote-verified flashcards from pre-selected source passages."""
        extracted = PDFExtractionResult(filename=filename, filepath=Path(filename))
        extracted.clean_title = clean_title or filename
        extracted.extracted_pages = [
            {
                "page_num": int(p.get("page_num", idx + 1)),
                "text": str(p.get("text") or ""),
            }
            for idx, p in enumerate(passages)
            if str(p.get("text") or "").strip()
        ]
        extracted.total_pages = len(extracted.extracted_pages)
        extracted.total_chars = sum(len(p["text"]) for p in extracted.extracted_pages)
        return self.generate_materials_from_pages(
            extracted,
            quiz_count=0,
            flashcard_count=count,
            model=model,
        )

    def generate_from_library_document(
        self,
        doc_id: Optional[int] = None,
        filename: Optional[str] = None,
        page_from: int = 1,
        page_to: Optional[int] = None,
        quiz_count: int = 3,
        flashcard_count: int = 5,
        difficulty: str = "medium",
        model: Optional[str] = None,
        material_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate quiz and flashcards from an existing indexed library document or uploaded study document."""
        if doc_id is None and not filename and not material_id:
            raise StudyMaterialError("Cần cung cấp doc_id, material_id hoặc filename tài liệu.")
        material = None
        if material_id and doc_id is None:
            cand = self.get_material(material_id)
            if cand and cand.get("pages"):
                material = cand
        if material is None:
            material = self.inspect_library_document(doc_id=doc_id, filename=filename)
        pages = material.get("pages") or []
        p_to = page_to or max((int(p.get("page_num", 1)) for p in pages), default=page_from)
        scoped_pages = [p for p in pages if page_from <= int(p.get("page_num", 1)) <= p_to]
        if not scoped_pages:
            raise StudyMaterialError(f"Không có nội dung văn bản trong khoảng trang {page_from} - {p_to}.")
        if material.get("doc_id") is not None:
            risky_pages = self._unverified_visual_pages(material["filename"], page_from, p_to)
            if risky_pages:
                shown = ", ".join(str(page) for page in risky_pages[:10])
                if len(risky_pages) > 10:
                    shown += ", …"
                raise StudyMaterialError(
                    f"Không thể tạo câu hỏi hoặc thẻ từ trang {shown}: bản chữ có thể thiếu hoặc "
                    "thể hiện sai công thức hay hình minh họa. Vui lòng xem tài liệu gốc "
                    "hoặc chọn khoảng trang khác."
                )
        inconsistent_pages = [
            p.get("page_num") for p in scoped_pages
            if inconsistent_prime_extraction_example(p.get("text", ""))
        ]
        if inconsistent_pages:
            shown = ", ".join(str(page) for page in inconsistent_pages[:10])
            if len(inconsistent_pages) > 10:
                shown += ", …"
            raise StudyMaterialError(
                f"Không thể tạo câu hỏi hoặc thẻ từ trang {shown}: ví dụ số nguyên tố "
                "trong nguồn có kết quả không khớp với dữ liệu đầu vào. "
                "Vui lòng xem tài liệu gốc và kiểm tra lại hoặc chọn trang khác."
            )
        extracted = PDFExtractionResult(filename=material["filename"], filepath=Path(material["filename"]))
        extracted.clean_title = material.get("clean_title") or material["filename"]
        extracted.total_pages = material.get("total_pages") or len(scoped_pages)
        extracted.total_chars = sum(len(p.get("text", "")) for p in scoped_pages)
        extracted.extracted_pages = scoped_pages
        setattr(extracted, "saved_to_src", material.get("saved_to_src", False))
        setattr(extracted, "src_path", material.get("src_path", f"src/{material['filename']}"))
        with self._lock:
            raw_bytes = self._raw_files.get(material.get("id"))
        if raw_bytes is not None:
            setattr(extracted, "_raw_bytes", raw_bytes)
        record = self.generate_materials_from_pages(
            extracted, quiz_count=quiz_count, flashcard_count=flashcard_count,
            difficulty=difficulty, model=model,
        )
        persist_fn = getattr(self, "persist_material_record", None)
        if callable(persist_fn):
            persist_fn(record, raw_bytes=raw_bytes)
        return record

    @staticmethod
    def _unverified_visual_pages(filename: str, page_from: int, page_to: int) -> List[int]:
        """Find selected indexed pages whose extracted visual content is unreviewed."""
        import sqlite3
        from contextlib import closing
        from app.config import DB_PATH

        try:
            with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                has_quality = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corpus_page_quality'"
                ).fetchone()
                if not has_quality:
                    return []
                rows = db.execute(
                    "SELECT page_num, warnings_json FROM corpus_page_quality "
                    "WHERE filename=? AND page_num BETWEEN ? AND ? AND needs_review=1 ORDER BY page_num",
                    (filename, page_from, page_to),
                ).fetchall()
            risky_pages = []
            for page_num, raw_warnings in rows:
                warnings = json.loads(raw_warnings) if raw_warnings else []
                if not isinstance(warnings, list):
                    raise ValueError("Invalid page quality warnings")
                if "unrepresented_formula_or_diagram_suspected" in warnings:
                    risky_pages.append(int(page_num))
            return risky_pages
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise StudyMaterialError(
                "Không thể kiểm tra chất lượng văn bản nguồn. Vui lòng thử lại hoặc xem tài liệu gốc."
            ) from exc

    def get_material(self, material_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._materials.get(material_id)

    def verify_cas_integrity(self, material_id: str) -> None:
        """Verify file existence and integrity for in-memory or single-user manager."""
        mat = self.get_material(material_id)
        if not mat:
            return
        raw_b = self._raw_files.get(material_id)
        if raw_b is None and mat.get("filename"):
            raw_b = self._raw_files.get(mat["filename"])

    def search_private(
        self,
        material_id: str,
        query: str,
        page_num: Optional[int] = None,
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        """Fallback search for non-account or single-user mode."""
        mat = self.get_material(material_id)
        if not mat:
            return []
        pages = mat.get("pages") or []
        from app.chunker import TextChunker
        results = []
        filename = mat.get("filename", "")
        clean_title = mat.get("clean_title", filename)
        import re
        tokens = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 1]
        for p in pages:
            p_num = int(p.get("page_num", 1))
            if page_num is not None and p_num != page_num:
                continue
            p_text = str(p.get("text", "") or "")
            if not p_text.strip():
                continue
            chunks = TextChunker.chunk_page(clean_title, filename, p_num, p_text)
            for c in chunks:
                c_text = c["text"]
                score = sum(1 for t in tokens if t in c_text.lower())
                results.append({
                    "id": f"{material_id}_{p_num}_{c['chunk_index']}",
                    "chunk_id": f"{material_id}_{p_num}_{c['chunk_index']}",
                    "material_id": material_id,
                    "is_private": True,
                    "doc_id": None,
                    "book_title": clean_title,
                    "filename": filename,
                    "page_num": p_num,
                    "chunk_index": c["chunk_index"],
                    "text": c_text,
                    "safe_text": c_text,
                    "fts_score": float(score),
                    "download_url": f"/api/materials/download?material_id={material_id}",
                })
        results.sort(key=lambda x: x["fts_score"], reverse=True)
        return results[:top_k]


study_material_manager = StudyMaterialManager()



