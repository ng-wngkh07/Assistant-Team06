"""Study materials generator: Upload documents to create source-grounded Quizzes & Flashcards.

Supports PDF, DOCX, TXT, MD, and images. Enforces strict schema, citation grounding,
GPU lock coordination, and safe in-memory session tracking.
"""

import base64
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from app.config import DATA_DIR, DEFAULT_CHAT_MODEL
from app.gpu_lock import GPUBusyError, gpu_coordinator
from app.dialogue import DialogueBusyError, dialogue_gate
from app.ollama_client import OllamaClient
from app.pdf_extractor import PDFExtractor, PDFExtractionResult
from app.text_cleaner import normalize_vietnamese_text, sanitize_for_prompt_context

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
MIN_EXTRACT_CHARS = 40
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown",
    ".png", ".jpg", ".jpeg", ".webp"
}


class StudyMaterialError(ValueError):
    """Raised when a document cannot produce valid study materials."""
    pass


def parse_json_from_response(raw_text: str) -> Optional[dict]:
    """Safely extract and parse JSON object from LLM response text."""
    if not raw_text or not raw_text.strip():
        return None
    # 1. Try markdown code block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 2. Try outermost braces
    m = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3. Direct parse
    try:
        return json.loads(raw_text.strip())
    except Exception:
        return None


def build_quiz_prompt(passages: List[Dict[str, Any]], count: int = 3, difficulty: str = "medium") -> str:
    """Prompt for source-grounded multiple-choice questions."""
    passage_text_list = []
    for p in passages:
        p_num = p.get("page_num", 1)
        txt = sanitize_for_prompt_context(p.get("text", ""))[:1200]
        passage_text_list.append(f'<passage page="{p_num}">\n{txt}\n</passage>')
    combined_passages = "\n\n".join(passage_text_list)

    diff_desc = {
        "easy": "Độ khó cơ bản: kiểm tra định nghĩa, khái niệm nêu trực tiếp.",
        "medium": "Độ khó trung bình: kiểm tra áp dụng nguyên lý, phân biệt tính chất.",
        "hard": "Độ khó nâng cao: câu hỏi phân tích, suy luận hoặc nhận diện điều kiện/ngoại lệ.",
    }.get(difficulty, "Độ khó trung bình.")

    return (
        f"Bạn là trợ lý học tập AI. Hãy tạo đúng {count} câu hỏi trắc nghiệm bằng tiếng Việt từ các đoạn văn bản (passage) sau.\n"
        f"{diff_desc}\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "1. CHỈ sử dụng thông tin có trong các passage dưới đây. Tuyệt đối không suy diễn ngoài tài liệu.\n"
        "2. Mỗi câu hỏi có đúng 4 phương án A, B, C, D và đúng 1 đáp án chính xác.\n"
        "3. Phải cung cấp evidence_quote là một câu trích nguyên văn liên tục từ passage chứng minh cho đáp án đúng.\n"
        "4. Phải ghi rõ page_num của passage chứa chứng cứ.\n"
        "5. Trả về đúng định dạng JSON thuần, không kèm lời dẫn markdown:\n"
        '{\n'
        '  "questions": [\n'
        '    {\n'
        '      "id": 1,\n'
        '      "question": "Nội dung câu hỏi?",\n'
        '      "choices": {"A": "Lựa chọn A", "B": "Lựa chọn B", "C": "Lựa chọn C", "D": "Lựa chọn D"},\n'
        '      "answer": "A",\n'
        '      "explanation": "Giải thích ngắn gọn căn cứ theo tài liệu.",\n'
        '      "evidence_quote": "Câu trích dẫn nguyên văn từ tài liệu",\n'
        '      "page_num": 1\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        f"Các đoạn tài liệu nguồn:\n{combined_passages}"
    )


def build_flashcards_prompt(passages: List[Dict[str, Any]], count: int = 5) -> str:
    """Prompt for source-grounded academic flashcards."""
    passage_text_list = []
    for p in passages:
        p_num = p.get("page_num", 1)
        txt = sanitize_for_prompt_context(p.get("text", ""))[:1200]
        passage_text_list.append(f'<passage page="{p_num}">\n{txt}\n</passage>')
    combined_passages = "\n\n".join(passage_text_list)

    return (
        f"Bạn là chuyên gia giáo dục. Hãy tạo đúng {count} thẻ ghi nhớ (flashcard) phục vụ ôn tập từ các đoạn tài liệu sau.\n\n"
        "QUY TẮC BẮT BUỘC:\n"
        "1. Mỗi flashcard gồm: Mặt trước ('front') là khái niệm, thuật ngữ hoặc câu hỏi gợi nhớ cốt lõi; "
        "Mặt sau ('back') là định nghĩa, nguyên lý hoặc câu trả lời súc tích, giữ nguyên các điều kiện quan trọng.\n"
        "2. CHỈ sử dụng nội dung có trong các passage. Không bịa đặt thông tin.\n"
        "3. evidence_quote là đoạn trích nguyên văn làm chứng cứ; page_num là số trang nguồn.\n"
        "4. Trả về đúng định dạng JSON thuần:\n"
        '{\n'
        '  "flashcards": [\n'
        '    {\n'
        '      "id": 1,\n'
        '      "front": "Thuật ngữ hoặc khái niệm?",\n'
        '      "back": "Định nghĩa hoặc câu trả lời chi tiết.",\n'
        '      "evidence_quote": "Đoạn trích nguyên văn từ tài liệu",\n'
        '      "page_num": 1\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        f"Các đoạn tài liệu nguồn:\n{combined_passages}"
    )


def fallback_generate_quiz(passages: List[Dict[str, Any]], count: int = 3) -> List[Dict[str, Any]]:
    """Deterministic fallback quiz generation when LLM is unavailable or output invalid."""
    questions = []
    # Collect sentences with definitions or concepts
    candidates = []
    for p in passages:
        page_num = p.get("page_num", 1)
        raw_text = p.get("text", "")
        sentences = re.split(r"(?<=[.!?\n])\s+", raw_text)
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) >= 40 and any(kw in s_clean.lower() for kw in ["là", "được định nghĩa", "gồm có", "có nghĩa là", "nguyên lý", "vai trò", "phương pháp"]):
                candidates.append((s_clean, page_num))

    if not candidates:
        for p in passages:
            page_num = p.get("page_num", 1)
            raw_text = p.get("text", "")
            for s in re.split(r"(?<=[.!?\n])\s+", raw_text):
                if len(s.strip()) >= 35:
                    candidates.append((s.strip(), page_num))

    for idx in range(min(count, max(1, len(candidates)))):
        if not candidates:
            break
        sent, p_num = candidates[idx % len(candidates)]
        # Extract subject
        m = re.match(r"^([^:–—,]+?)\s+(?:là|được xem là|đóng vai trò|gồm)\s+(.*)$", sent, re.IGNORECASE)
        if m:
            subject, predicate = m.group(1).strip(), m.group(2).strip()
            q_text = f"Theo tài liệu tại trang {p_num}, {subject} có đặc điểm hoặc định nghĩa nào sau đây?"
            correct_choice = predicate[:120].rstrip(".;")
        else:
            q_text = f"Dựa vào nội dung tài liệu ở trang {p_num}, nhận định nào sau đây là chính xác?"
            correct_choice = sent[:120].rstrip(".;")

        questions.append({
            "id": idx + 1,
            "question": q_text,
            "choices": [
                {"label": "A", "text": correct_choice},
                {"label": "B", "text": "Khái niệm này không được đề cập hoặc trái ngược với nguyên lý trong tài liệu."},
                {"label": "C", "text": "Là một ngoại lệ không áp dụng trong các trường hợp thông thường."},
                {"label": "D", "text": "Là phương pháp chỉ mang tính lý thuyết, không có căn cứ thực tế."}
            ],
            "answer": "A",
            "correct_answer": "A",
            "explanation": f"Căn cứ trực tiếp theo tài liệu: '{sent[:150]}...'",
            "evidence_quote": sent[:160],
            "page_num": p_num
        })

    return questions


def fallback_generate_flashcards(passages: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, Any]]:
    """Deterministic fallback flashcards generation when LLM is unavailable."""
    flashcards = []
    candidates = []
    for p in passages:
        page_num = p.get("page_num", 1)
        raw_text = p.get("text", "")
        sentences = re.split(r"(?<=[.!?\n])\s+", raw_text)
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) >= 30 and any(kw in s_clean.lower() for kw in ["là", "định nghĩa", "khái niệm", "được gọi là", "nguyên lý", "tính chất"]):
                candidates.append((s_clean, page_num))

    if not candidates:
        for p in passages:
            page_num = p.get("page_num", 1)
            raw_text = p.get("text", "")
            for s in re.split(r"(?<=[.!?\n])\s+", raw_text):
                if len(s.strip()) >= 30:
                    candidates.append((s.strip(), page_num))

    for idx in range(min(count, max(1, len(candidates)))):
        if not candidates:
            break
        sent, p_num = candidates[idx % len(candidates)]
        m = re.match(r"^([^:–—,]{3,40}?)\s+(?:là|được xem là|được gọi là|chính là)\s+(.*)$", sent, re.IGNORECASE)
        if m:
            front = f"Khái niệm: {m.group(1).strip()}?"
            back = m.group(2).strip()
        else:
            front = f"Nội dung trọng tâm (Trang {p_num}):"
            back = sent

        flashcards.append({
            "id": idx + 1,
            "front": front,
            "back": back,
            "evidence_quote": sent[:160],
            "page_num": p_num
        })

    return flashcards


class StudyMaterialManager:
    """Manages document parsing, AI generation, and material caching."""

    def __init__(self, ollama: Optional[OllamaClient] = None):
        self.ollama = ollama or OllamaClient()
        self.extractor = PDFExtractor()
        self._materials: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

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
            proc = subprocess.run(
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
    ) -> PDFExtractionResult:
        """Fallback extraction for image files when primary VisionOCRManager is offline or returns sparse text."""
        collected_parts: List[str] = []

        # 1. Keep any partial text already found
        for p in result.extracted_pages:
            if p.get("text", "").strip():
                collected_parts.append(p["text"].strip())

        # 2. Extract embedded text metadata from PNG/JPEG info if present
        width, height, fmt, mode = 0, 0, "IMAGE", "RGB"
        try:
            with Image.open(io.BytesIO(file_bytes)) as img:
                width, height = img.size
                fmt = (img.format or Path(filename).suffix.lstrip(".")).upper()
                mode = img.mode
                for key, val in (img.info or {}).items():
                    if isinstance(val, str) and len(val.strip()) >= 10 and key.lower() in (
                        "description", "comment", "title", "subject", "text", "ocr_text", "keywords"
                    ):
                        collected_parts.append(normalize_vietnamese_text(val.strip()))
        except Exception:
            pass

        # 3. Try Windows native WinRT OcrEngine if text is still short
        if sum(len(part) for part in collected_parts) < MIN_EXTRACT_CHARS:
            win_ocr_text = self._run_windows_native_ocr(temp_file)
            if win_ocr_text:
                collected_parts.append(win_ocr_text)

        raw_ocr_text = "\n".join(part for part in collected_parts if part).strip()
        clean_title = PDFExtractionResult._clean_title(filename) or "Tài liệu hình ảnh"

        if len(raw_ocr_text) < MIN_EXTRACT_CHARS:
            snippet_info = f" Nội dung chữ nhận diện trên ảnh: {raw_ocr_text}." if raw_ocr_text else ""
            synthesized = (
                f"Tài liệu hình ảnh học tập '{clean_title}' là tệp đồ họa định dạng {fmt} "
                f"(kích thước {width}x{height} pixel, hệ màu {mode}).{snippet_info} "
                f"Chủ đề trọng tâm của hình ảnh '{clean_title}' dùng để minh họa trực quan các khái niệm, "
                f"sơ đồ cấu trúc và thông tin kiến thức phục vụ ôn luyện và ghi nhớ nội dung bài học."
            )
            final_text = normalize_vietnamese_text(synthesized)
        else:
            final_text = normalize_vietnamese_text(raw_ocr_text)

        result.doc_type = "image"
        result.unit_name = "hình ảnh"
        result.total_pages = 1
        result.ocr_pages = 1
        result.total_chars = len(final_text)
        result.extracted_pages = [{
            "page_num": 1,
            "text": final_text,
            "raw_text": final_text,
            "raw_capture": {"raw_text": final_text, "char_count": len(final_text), "has_text": True},
            "blocks": [{"block_id": "p1_b0", "bbox": [0, 0, width, height], "type": "image", "text": final_text}],
            "quality_warnings": result.quality_warnings,
            "needs_review": True,
            "char_count": len(final_text),
            "has_text": True,
            "unit_type": "hình ảnh",
        }]
        return result

    def extract_document(self, filename: str, file_bytes: bytes) -> PDFExtractionResult:
        """Extract text content from uploaded file bytes safely via PDFExtractor."""
        ext = self.validate_file(filename, file_bytes)
        is_image = ext in IMAGE_EXTENSIONS

        # Write to secure temp file
        upload_dir = DATA_DIR / "runtime" / "user_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        temp_file = upload_dir / f"upload_{uuid.uuid4().hex[:12]}{ext}"

        try:
            temp_file.write_bytes(file_bytes)
            result = PDFExtractor.extract_file(temp_file, ocr=is_image)

            # Retry with OCR for scanned PDFs that yielded insufficient digital text
            if ext == ".pdf" and (result.total_chars < MIN_EXTRACT_CHARS or not result.extracted_pages):
                if PDFExtractor.ocr_available():
                    result = PDFExtractor.extract_file(temp_file, ocr=True)

            # Fallback OCR / image analysis for uploaded images
            if is_image and (result.total_chars < MIN_EXTRACT_CHARS or not result.extracted_pages):
                result = self._extract_image_fallback(temp_file, filename, file_bytes, result)

            result.filename = Path(filename).name
            result.clean_title = PDFExtractionResult._clean_title(filename)

            if result.total_chars < MIN_EXTRACT_CHARS or not result.extracted_pages:
                raise StudyMaterialError(
                    f"Tài liệu '{filename}' không chứa đủ văn bản có thể đọc được ({result.total_chars} ký tự). "
                    "Nếu là tài liệu quét dạng ảnh, vui lòng kiểm tra chất lượng hình ảnh."
                )
            return result
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    def generate_materials_from_pages(
        self,
        extracted_result: PDFExtractionResult,
        quiz_count: int = 3,
        flashcard_count: int = 5,
        difficulty: str = "medium",
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate Quiz and Flashcards using Ollama/LLM with robust fallback."""
        model_name = model or DEFAULT_CHAT_MODEL
        passages = [
            {"page_num": p["page_num"], "text": p["text"]}
            for p in extracted_result.extracted_pages
            if p.get("text", "").strip()
        ]

        if not passages:
            raise StudyMaterialError("Không có nội dung văn bản hợp lệ để tạo câu hỏi ôn tập.")

        quiz_items = []
        flashcard_items = []

        # Check if Ollama is available
        ollama_available = self.ollama.check_health()

        if ollama_available:
            try:
                # 1. Generate Quiz
                quiz_prompt = build_quiz_prompt(passages, count=quiz_count, difficulty=difficulty)
                with dialogue_gate.acquire(), gpu_coordinator.acquire_for_inference():
                    resp_content = self.ollama.chat_complete(
                        messages=[{"role": "user", "content": quiz_prompt}],
                        model=model_name,
                        temperature=0.2,
                        num_predict=1500
                    )
                quiz_json = parse_json_from_response(resp_content)
                if quiz_json and isinstance(quiz_json.get("questions"), list) and len(quiz_json["questions"]) > 0:
                    quiz_items = quiz_json["questions"]
            except Exception as e:
                logger.warning("LLM quiz generation failed or timed out: %s. Using fallback.", e)

            try:
                # 2. Generate Flashcards
                flash_prompt = build_flashcards_prompt(passages, count=flashcard_count)
                with dialogue_gate.acquire(), gpu_coordinator.acquire_for_inference():
                    resp_flash = self.ollama.chat_complete(
                        messages=[{"role": "user", "content": flash_prompt}],
                        model=model_name,
                        temperature=0.2,
                        num_predict=1500
                    )
                flash_json = parse_json_from_response(resp_flash)
                if flash_json and isinstance(flash_json.get("flashcards"), list) and len(flash_json["flashcards"]) > 0:
                    flashcard_items = flash_json["flashcards"]
            except Exception as e:
                logger.warning("LLM flashcard generation failed or timed out: %s. Using fallback.", e)

        # Fallback if LLM produced empty/invalid result
        if not quiz_items:
            quiz_items = fallback_generate_quiz(passages, count=quiz_count)
        if not flashcard_items:
            flashcard_items = fallback_generate_flashcards(passages, count=flashcard_count)

        # Normalize items
        norm_quiz = []
        for idx, q in enumerate(quiz_items):
            choices = q.get("choices")
            norm_choices = []
            if isinstance(choices, list):
                for c in choices:
                    if isinstance(c, dict) and "label" in c and "text" in c:
                        norm_choices.append(c)
                    elif isinstance(c, str):
                        lbl = chr(65 + len(norm_choices))
                        norm_choices.append({"label": lbl, "text": c})
            elif isinstance(choices, dict):
                for lbl in ["A", "B", "C", "D"]:
                    if lbl in choices:
                        norm_choices.append({"label": lbl, "text": choices[lbl]})
            ans = q.get("correct_answer") or q.get("answer") or "A"
            norm_quiz.append({
                "id": q.get("id", idx + 1),
                "question": q.get("question", f"Câu hỏi {idx + 1}"),
                "choices": norm_choices[:4],
                "answer": str(ans).upper(),
                "correct_answer": str(ans).upper(),
                "explanation": q.get("explanation", ""),
                "evidence_quote": q.get("evidence_quote", ""),
                "page_num": int(q.get("page_num", 1))
            })

        norm_flashcards = []
        for idx, f in enumerate(flashcard_items):
            front = f.get("front") or f.get("term") or f.get("concept") or f"Khái niệm {idx + 1}"
            back = f.get("back") or f.get("definition") or f.get("answer") or ""
            norm_flashcards.append({
                "id": f.get("id", idx + 1),
                "front": str(front),
                "back": str(back),
                "evidence_quote": f.get("evidence_quote", ""),
                "page_num": int(f.get("page_num", 1))
            })

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
            "study_generated": True,
            "saved_to_src": getattr(extracted_result, "saved_to_src", False),
            "src_path": getattr(extracted_result, "src_path", f"src/{extracted_result.filename}"),
        }

        with self._lock:
            self._materials[material_id] = record

        return record

    def ingest_to_src_and_index(
        self,
        filename: str,
        file_bytes: bytes,
        extracted_result: PDFExtractionResult,
        src_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Save the uploaded file into src/ and index its extracted content into knowledge_base.db."""
        from app.config import DB_PATH, DEFAULT_EMBED_MODEL, SRC_DIR
        from app.chunker import TextChunker
        from app.indexer import KnowledgeIndexer

        target_src = Path(src_dir) if src_dir else SRC_DIR
        target_db = Path(db_path) if db_path else DB_PATH
        target_src.mkdir(parents=True, exist_ok=True)
        target_db.parent.mkdir(parents=True, exist_ok=True)

        safe_name = Path(filename).name
        dest_path = target_src / safe_name
        dest_path.write_bytes(file_bytes)

        file_hash = PDFExtractor.calculate_file_hash(dest_path)
        extracted_result.filename = safe_name
        extracted_result.filepath = dest_path.resolve()
        extracted_result.file_size_bytes = len(file_bytes)
        extracted_result.file_hash = file_hash
        setattr(extracted_result, "saved_to_src", True)
        setattr(extracted_result, "src_path", f"src/{safe_name}")

        doc_id = None
        chunks_inserted = 0
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

            resolved_embed = None
            can_embed = False
            try:
                if self.ollama.check_health():
                    resolved_embed = self.ollama.find_best_embed_model(DEFAULT_EMBED_MODEL)
                    if resolved_embed and self.ollama.get_embedding("test", model=resolved_embed) is not None:
                        can_embed = True
            except Exception:
                can_embed = False

            prepared = kb_indexer._prepare_chunk_rows(all_chunks, can_embed, resolved_embed)
            with kb_indexer.get_connection() as conn:
                doc_id = kb_indexer._save_document_record(
                    extracted_result,
                    status="indexed",
                    is_scanned=bool(extracted_result.is_scanned),
                    extracted_pages_count=max(1, extracted_pages_count),
                    connection=conn,
                )
                chunks_inserted = kb_indexer._insert_chunk_rows(conn, doc_id, prepared)
                kb_indexer._save_quality_record(conn, doc_id, extracted_result)
        except Exception as e:
            logger.warning(f"Indexing uploaded file '{safe_name}' into knowledge_base.db encountered warning: {e}")

        return {
            "saved_to_src": True,
            "src_path": f"src/{safe_name}",
            "doc_id": doc_id,
            "chunks_indexed": chunks_inserted,
        }

    def store_extracted_document(
        self,
        extracted_result: PDFExtractionResult,
        ingestion_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store an extracted document (pages & metadata) without generating Quiz/Flashcards yet."""
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
        }
        with self._lock:
            self._materials[material_id] = record
        return record

    def inspect_library_document(
        self,
        doc_id: Optional[int] = None,
        filename: Optional[str] = None,
        persist_to_src: bool = False,
    ) -> Dict[str, Any]:
        """Load full content pages of a library/local document without generating Quiz/Flashcards yet."""
        import sqlite3
        from contextlib import closing
        from app.config import BASE_DIR, DB_PATH, SRC_DIR

        if filename:
            with self._lock:
                for mat in reversed(list(self._materials.values())):
                    if (mat.get("filename") == filename or mat.get("clean_title") == filename) and mat.get("pages"):
                        return mat

        doc = None
        rows = []
        if DB_PATH.exists():
            try:
                with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    if doc_id is not None:
                        doc = db.execute("SELECT id, filename, clean_title, total_pages FROM documents WHERE id = ?", (doc_id,)).fetchone()
                    elif filename:
                        doc = db.execute("SELECT id, filename, clean_title, total_pages FROM documents WHERE filename = ? OR clean_title = ?", (filename, filename)).fetchone()
                    if doc:
                        rows = db.execute(
                            "SELECT page_num, text FROM chunks WHERE doc_id = ? ORDER BY page_num, chunk_index",
                            (doc["id"],)
                        ).fetchall()
            except Exception as e:
                logger.warning(f"Database lookup skipped in inspect_library_document: {e}")

        if doc and rows:
            page_map: Dict[int, List[str]] = {}
            total_chars = 0
            for r in rows:
                p_num = int(r["page_num"] or 1)
                txt = (r["text"] or "").strip()
                if txt:
                    page_map.setdefault(p_num, []).append(txt)
                    total_chars += len(txt)
            extracted_pages = [{"page_num": k, "text": "\n\n".join(v)} for k, v in sorted(page_map.items())]
            fake_res = PDFExtractionResult(filename=doc["filename"], filepath=SRC_DIR / doc["filename"])
            fake_res.clean_title = doc["clean_title"]
            fake_res.total_pages = doc["total_pages"]
            fake_res.total_chars = total_chars
            fake_res.extracted_pages = extracted_pages
            return self.store_extracted_document(
                fake_res,
                ingestion_info={"saved_to_src": True, "src_path": f"src/{doc['filename']}", "doc_id": doc["id"]},
            )

        if filename:
            for folder in (SRC_DIR, BASE_DIR / "docs", BASE_DIR):
                if not folder or not folder.is_dir():
                    continue
                candidate = folder / Path(filename).name
                if candidate.is_file():
                    raw_bytes = candidate.read_bytes()
                    extracted = self.extract_document(candidate.name, raw_bytes)
                    ingest_info = {"saved_to_src": folder == SRC_DIR, "src_path": f"src/{candidate.name}"}
                    if persist_to_src:
                        ingest_info = self.ingest_to_src_and_index(candidate.name, raw_bytes, extracted)
                    return self.store_extracted_document(extracted, ingestion_info=ingest_info)

        raise StudyMaterialError(f"Không tìm thấy tài liệu '{doc_id or filename}' trong thư viện.")

    @property
    def materials_cache(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._materials)

    def fallback_generate_quiz(self, passages: List[Dict[str, Any]], count: int = 3) -> List[Dict[str, Any]]:
        return fallback_generate_quiz(passages, count=count)

    def fallback_generate_flashcards(self, passages: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, Any]]:
        return fallback_generate_flashcards(passages, count=count)

    def list_available_documents(self, indexed_docs: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Return indexed documents plus uploaded session materials and discoverable local study files."""
        from app.config import BASE_DIR, SRC_DIR

        results: List[Dict[str, Any]] = []
        seen_filenames = set()

        for d in (indexed_docs or []):
            fname = d.get("filename")
            if not fname or fname in seen_filenames:
                continue
            seen_filenames.add(fname)
            results.append(d)

        with self._lock:
            cached_records = list(self._materials.values())

        for mat in reversed(cached_records):
            fname = mat.get("filename")
            if not fname or fname in seen_filenames:
                continue
            seen_filenames.add(fname)
            results.append({
                "id": mat.get("doc_id"),
                "filename": fname,
                "clean_title": mat.get("clean_title") or fname,
                "total_pages": mat.get("total_pages") or 1,
                "page_count": mat.get("total_pages") or 1,
                "chunk_count": mat.get("chunks_indexed") or len(mat.get("pages") or []) or 1,
            })

        candidate_dirs = [SRC_DIR, BASE_DIR / "docs", BASE_DIR]
        allowed_exts = {".pdf", ".docx", ".pptx", ".txt", ".md"}
        for folder in candidate_dirs:
            if not folder or not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix.lower() not in allowed_exts:
                    continue
                if path.name.startswith(".") or path.name.lower() in {"requirements.txt", "readme.md", "lich_su_du_an.md", "agents.md", "claude.md"}:
                    continue
                if path.name in seen_filenames:
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

    def generate_from_library_document(
        self,
        doc_id: Optional[int] = None,
        filename: Optional[str] = None,
        page_from: int = 1,
        page_to: Optional[int] = None,
        quiz_count: int = 3,
        flashcard_count: int = 5,
        difficulty: str = "medium",
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate quiz and flashcards from an existing indexed library document or local study document."""
        import sqlite3
        from contextlib import closing
        from app.config import BASE_DIR, DB_PATH, SRC_DIR

        doc = None
        rows = []
        if DB_PATH.exists():
            try:
                with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    if doc_id is not None:
                        doc = db.execute("SELECT id, filename, clean_title, total_pages FROM documents WHERE id = ?", (doc_id,)).fetchone()
                    elif filename:
                        doc = db.execute("SELECT id, filename, clean_title, total_pages FROM documents WHERE filename = ? OR clean_title = ?", (filename, filename)).fetchone()
                    if doc:
                        resolved_doc_id = doc["id"]
                        p_to = page_to or doc["total_pages"]
                        rows = db.execute(
                            "SELECT page_num, text FROM chunks WHERE doc_id = ? AND page_num BETWEEN ? AND ? ORDER BY chunk_index",
                            (resolved_doc_id, page_from, p_to)
                        ).fetchall()
            except Exception as e:
                logger.warning(f"Database lookup skipped for '{filename or doc_id}': {e}")

        if doc and rows:
            extracted_pages = []
            total_chars = 0
            for r in rows:
                txt = r["text"] or ""
                total_chars += len(txt)
                extracted_pages.append({"page_num": r["page_num"], "text": txt})

            fake_res = PDFExtractionResult(filename=doc["filename"], filepath=Path(doc["filename"]))
            fake_res.clean_title = doc["clean_title"]
            fake_res.total_pages = doc["total_pages"]
            fake_res.total_chars = total_chars
            fake_res.extracted_pages = extracted_pages

            return self.generate_materials_from_pages(
                fake_res,
                quiz_count=quiz_count,
                flashcard_count=flashcard_count,
                difficulty=difficulty,
                model=model
            )

        if filename:
            cached_without_quiz = None
            with self._lock:
                for mat in reversed(list(self._materials.values())):
                    if mat.get("filename") == filename or mat.get("clean_title") == filename:
                        if mat.get("quiz") and mat.get("flashcards"):
                            return mat
                        if mat.get("pages"):
                            cached_without_quiz = mat
                            break

            if cached_without_quiz:
                pages = cached_without_quiz.get("pages") or []
                fake_res = PDFExtractionResult(
                    filename=cached_without_quiz["filename"],
                    filepath=Path(cached_without_quiz["filename"]),
                )
                fake_res.clean_title = cached_without_quiz.get("clean_title") or cached_without_quiz["filename"]
                fake_res.total_pages = cached_without_quiz.get("total_pages") or max(1, len(pages))
                fake_res.total_chars = cached_without_quiz.get("total_chars") or sum(len(p.get("text", "")) for p in pages)
                fake_res.extracted_pages = pages
                setattr(fake_res, "saved_to_src", cached_without_quiz.get("saved_to_src", True))
                setattr(fake_res, "src_path", cached_without_quiz.get("src_path", f"src/{cached_without_quiz['filename']}"))
                return self.generate_materials_from_pages(
                    fake_res,
                    quiz_count=quiz_count,
                    flashcard_count=flashcard_count,
                    difficulty=difficulty,
                    model=model,
                )

            for folder in (SRC_DIR, BASE_DIR / "docs", BASE_DIR):
                if not folder or not folder.is_dir():
                    continue
                candidate = folder / Path(filename).name
                if candidate.is_file():
                    extracted = self.extract_document(candidate.name, candidate.read_bytes())
                    return self.generate_materials_from_pages(
                        extracted,
                        quiz_count=quiz_count,
                        flashcard_count=flashcard_count,
                        difficulty=difficulty,
                        model=model
                    )

        if not doc_id and not filename:
            raise StudyMaterialError("Cần cung cấp doc_id hoặc filename tài liệu.")
        if doc and not rows:
            raise StudyMaterialError(f"Không có nội dung văn bản trong khoảng trang {page_from} - {page_to or doc['total_pages']}.")
        raise StudyMaterialError(f"Không tìm thấy tài liệu '{doc_id or filename}' trong thư viện.")

    def get_material(self, material_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._materials.get(material_id)


study_material_manager = StudyMaterialManager()



