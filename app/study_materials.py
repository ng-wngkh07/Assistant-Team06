"""Study materials generator: Upload documents to create source-grounded Quizzes & Flashcards.

Supports PDF, DOCX, TXT, MD, and images. Enforces strict schema, citation grounding,
GPU lock coordination, and safe in-memory session tracking.
"""

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import DATA_DIR, DEFAULT_CHAT_MODEL
from app.gpu_lock import GPUBusyError, gpu_coordinator
from app.dialogue import DialogueBusyError, dialogue_gate
from app.ollama_client import OllamaClient
from app.pdf_extractor import PDFExtractor, PDFExtractionResult
from app.text_cleaner import normalize_vietnamese_text, sanitize_for_prompt_context

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
MIN_EXTRACT_CHARS = 40

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".png", ".jpg", ".jpeg"
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
        elif ext in (".png",) and not file_bytes.startswith(b"\x89PNG"):
            raise StudyMaterialError("Tệp PNG không đúng định dạng.")
        elif ext in (".jpg", ".jpeg") and not file_bytes.startswith(b"\xff\xd8"):
            raise StudyMaterialError("Tệp JPEG không đúng định dạng.")

        return ext

    def extract_document(self, filename: str, file_bytes: bytes) -> PDFExtractionResult:
        """Extract text content from uploaded file bytes safely via PDFExtractor."""
        ext = self.validate_file(filename, file_bytes)

        # Write to secure temp file
        upload_dir = DATA_DIR / "runtime" / "user_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        temp_file = upload_dir / f"upload_{uuid.uuid4().hex[:12]}{ext}"

        try:
            temp_file.write_bytes(file_bytes)
            result = PDFExtractor.extract_file(temp_file)
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
        record = {
            "id": material_id,
            "material_id": material_id,
            "filename": extracted_result.filename,
            "clean_title": extracted_result.clean_title,
            "total_pages": extracted_result.total_pages,
            "total_chars": extracted_result.total_chars,
            "quiz": norm_quiz,
            "flashcards": norm_flashcards,
            "difficulty": difficulty,
        }

        with self._lock:
            self._materials[material_id] = record

        return record

    @property
    def materials_cache(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._materials)

    def fallback_generate_quiz(self, passages: List[Dict[str, Any]], count: int = 3) -> List[Dict[str, Any]]:
        return fallback_generate_quiz(passages, count=count)

    def fallback_generate_flashcards(self, passages: List[Dict[str, Any]], count: int = 5) -> List[Dict[str, Any]]:
        return fallback_generate_flashcards(passages, count=count)

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
        """Generate quiz and flashcards from an existing indexed library document."""
        import sqlite3
        from contextlib import closing
        from app.config import DB_PATH

        with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if doc_id is not None:
                doc = db.execute("SELECT id, filename, clean_title, total_pages FROM documents WHERE id = ?", (doc_id,)).fetchone()
            elif filename:
                doc = db.execute("SELECT id, filename, clean_title, total_pages FROM documents WHERE filename = ? OR clean_title = ?", (filename, filename)).fetchone()
            else:
                raise StudyMaterialError("Cần cung cấp doc_id hoặc filename tài liệu.")

            if not doc:
                raise StudyMaterialError(f"Không tìm thấy tài liệu '{doc_id or filename}' trong thư viện.")

            resolved_doc_id = doc["id"]
            p_to = page_to or doc["total_pages"]
            rows = db.execute(
                "SELECT page_num, text FROM chunks WHERE doc_id = ? AND page_num BETWEEN ? AND ? ORDER BY chunk_index",
                (resolved_doc_id, page_from, p_to)
            ).fetchall()

        if not rows:
            raise StudyMaterialError(f"Không có nội dung văn bản trong khoảng trang {page_from} - {p_to}.")

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

    def get_material(self, material_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._materials.get(material_id)


study_material_manager = StudyMaterialManager()

