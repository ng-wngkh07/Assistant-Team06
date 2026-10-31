"""Model-free regressions for hypothetical grounding, multi-step derivation, and fail-closed review gates."""
import json
from pathlib import Path
import pytest

from app.practice import (
    PracticeGenerationError,
    PracticeService,
    build_practice_generation_prompt,
)

BINARY_SEARCH_PASSAGE = (
    "Tìm kiếm nhị phân trong ví dụ dùng mảng tăng dần và đoạn chỉ số đóng [lo,hi]. "
    "Mỗi bước lấy mid=floor((lo+hi)/2); nếu khóa nhỏ hơn a[mid] thì hi=mid-1, "
    "nếu lớn hơn thì lo=mid+1. Dừng không tìm thấy khi lo>hi."
)

POSITIVE_CONTROL_QUESTION = {
    "question": (
        "Cho mảng tăng dần A = [10, 20, 30, 40, 50, 60, 70] và khóa K = 55. "
        "Nếu đoạn tìm kiếm ban đầu là chỉ số đóng [lo=0, hi=6], sau khi thực hiện đúng hai bước "
        "của thuật toán Tìm kiếm nhị phân theo tài liệu, giá trị của biến lo sẽ là bao nhiêu?"
    ),
    "choices": {
        "A": "lo = 0",
        "B": "lo = 3",
        "C": "lo = 4",
        "D": "lo = 6",
    },
    "answer": "C",
    "explanation": (
        "Bước 1: mid = floor((0+6)/2) = 3; A[3] = 40. Vì K = 55 > 40, cập nhật lo = mid + 1 = 4; đoạn thành [4,6]. "
        "Bước 2: mid = floor((4+6)/2) = 5; A[5] = 60. Vì K = 55 < 60, cập nhật hi = mid - 1 = 4; đoạn thành [4,4]. "
        "Giá trị của biến lo sau hai bước là 4."
    ),
    "source_chunk_id": 1,
    "evidence_quote": (
        "Tìm kiếm nhị phân trong ví dụ dùng mảng tăng dần và đoạn chỉ số đóng [lo,hi]. "
        "Mỗi bước lấy mid=floor((lo+hi)/2); nếu khóa nhỏ hơn a[mid] thì hi=mid-1, "
        "nếu lớn hơn thì lo=mid+1. Dừng không tìm thấy khi lo>hi."
    ),
}


class FakeSearcher:
    def __init__(self, passages):
        self.passages = passages

    def source_page_chunks(self, doc_id, page_num, limit=12):
        return [p for p in self.passages if p["doc_id"] == doc_id and p["page_num"] == page_num][:limit]


class FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def chat_complete(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        self.last_done_reason = "stop"
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class FakeDocumentLookup:
    def document(self, doc_id):
        return {"id": doc_id, "filename": "algo.pdf", "clean_title": "Cấu trúc dữ liệu", "total_pages": 10}

    def pdf_path(self, filename):
        return Path("algo.pdf")


def make_passage(chunk_id=1, page=1, text=BINARY_SEARCH_PASSAGE):
    return {
        "chunk_id": chunk_id,
        "doc_id": 1,
        "book_title": "Cấu trúc dữ liệu",
        "filename": "algo.pdf",
        "page_num": page,
        "chunk_index": chunk_id,
        "text": text,
        "safe_text": text,
    }


def make_service(responses, text=BINARY_SEARCH_PASSAGE):
    ollama = FakeOllama(responses)
    searcher = FakeSearcher([make_passage(text=text)])
    service = PracticeService(searcher, FakeDocumentLookup(), ollama=ollama)
    return service, ollama


def test_positive_control_two_step_scenario_accepted():
    """Valid derivation from explicit passage rules + hypothetical input passes all gates."""
    gen_response = {"questions": [POSITIVE_CONTROL_QUESTION]}
    val_response = {
        "checks": [{
            "index": 1,
            "input_audit": "Đề bài cung cấp đầy đủ mảng A, khóa K=55 và đoạn tìm kiếm ban đầu [lo=0, hi=6].",
            "independent_solution": "Bước 1: mid=floor((0+6)/2)=3, A[3]=40 < 55 -> lo=4. Bước 2: mid=floor((4+6)/2)=5, A[5]=60 > 55 -> hi=4. Biến lo kết thúc là 4.",
            "choice_comparison": "Kết quả lo=4 khớp duy nhất với lựa chọn C. Các lựa chọn A, B, D sai.",
            "explanation_audit": "Các bước tính toán, cập nhật lo/hi và kết luận trong lời giải đều chính xác.",
            "supported": True,
            "explanation_supported": True,
            "unique_answer": True,
            "qualifiers_preserved": True,
            "difficulty_matches": True,
            "verified_answer": "C",
        }]
    }
    service, _ = make_service([gen_response, val_response])
    result = service.generate_from_passages(
        [make_passage()],
        count=1,
        difficulty="hard",
        model="test-model",
        enforce_difficulty=True,
    )
    assert result["count"] == 1
    assert result["complete"] is True
    session_questions = service._sessions[result["practice_id"]]["questions"]
    assert session_questions[0]["answer"] == "C"
    assert session_questions[0]["source"]["chunk_id"] == 1


def test_service_rejects_wrong_intermediate_explanation_even_if_key_matches():
    """Reject a false intermediate value even when every other review gate passes."""
    wrong_explanation_q = {
        **POSITIVE_CONTROL_QUESTION,
        "explanation": POSITIVE_CONTROL_QUESTION["explanation"].replace("A[3] = 40", "A[3] = 30"),
    }
    gen_response = {"questions": [wrong_explanation_q]}
    # The final lo=4 and key C remain correct; isolate the explanation gate.
    val_response = {
        "checks": [{
            "index": 1,
            "supported": True,
            "explanation_supported": False,
            "unique_answer": True,
            "qualifiers_preserved": True,
            "difficulty_matches": True,
            "verified_answer": "C",
        }]
    }
    service, _ = make_service([gen_response, val_response])
    with pytest.raises(PracticeGenerationError, match="đạt kiểm tra nguồn"):
        service.generate_from_passages(
            [make_passage()],
            count=1,
            difficulty="hard",
            model="test-model",
            enforce_difficulty=True,
        )


def test_service_rejects_key_mismatch_when_derivation_yields_different_choice():
    """Question proposes key B ('3') but derivation and explanation correctly yield C ('4')."""
    mismatched_key_q = {
        **POSITIVE_CONTROL_QUESTION,
        "answer": "B",  # Model mistakenly claimed B was the answer key
    }
    gen_response = {"questions": [mismatched_key_q]}
    # Independent solver verifies answer is C, not proposed B
    val_response = {
        "checks": [{
            "index": 1,
            "supported": True,
            "explanation_supported": True,
            "unique_answer": True,
            "qualifiers_preserved": True,
            "difficulty_matches": True,
            "verified_answer": "C",
        }]
    }
    service, _ = make_service([gen_response, val_response])
    with pytest.raises(PracticeGenerationError, match="đạt kiểm tra nguồn"):
        service.generate_from_passages(
            [make_passage()],
            count=1,
            difficulty="hard",
            model="test-model",
            enforce_difficulty=True,
        )


def test_service_rejects_unsupported_assumption_or_incomplete_starting_state():
    """Question omits initial lo/hi index bounds needed to solve, or relies on rules outside passage."""
    incomplete_state_q = {
        **POSITIVE_CONTROL_QUESTION,
        "question": (
            "Cho mảng A = [10, 20, 30, 40, 50] và khóa K = 25. "
            "Tìm vị trí của phần tử trong mảng sau bước 1."
        ),
        "explanation": "Giả sử chỉ số bắt đầu từ 1 và lo=1, hi=5...",
        "answer": "A",
    }
    gen_response = {"questions": [incomplete_state_q]}
    val_response = {
        "checks": [{
            "index": 1,
            "supported": False,
            "explanation_supported": False,
            "unique_answer": False,
            "qualifiers_preserved": False,
            "difficulty_matches": False,
            "verified_answer": "",
        }]
    }
    service, _ = make_service([gen_response, val_response])
    with pytest.raises(PracticeGenerationError, match="đạt kiểm tra nguồn"):
        service.generate_from_passages(
            [make_passage()],
            count=1,
            difficulty="hard",
            model="test-model",
            enforce_difficulty=True,
        )


def test_service_rejects_easy_definition_requested_as_hard():
    """A direct definition or single-rule restatement must not pass hard difficulty gate."""
    easy_q = {
        **POSITIVE_CONTROL_QUESTION,
        "question": "Trong tìm kiếm nhị phân, điều kiện dừng khi không tìm thấy phần tử là gì?",
        "choices": {
            "A": "lo > hi",
            "B": "lo == hi",
            "C": "lo < hi",
            "D": "mid == 0",
        },
        "answer": "A",
        "explanation": "Đoạn nguồn nêu trực tiếp: 'Dừng không tìm thấy khi lo>hi.'",
    }
    gen_response = {"questions": [easy_q]}
    val_response = {
        "checks": [{
            "index": 1,
            "supported": True,
            "explanation_supported": True,
            "unique_answer": True,
            "qualifiers_preserved": True,
            "difficulty_matches": False,  # Easy definition does not match 'hard'
            "verified_answer": "A",
        }]
    }
    service, _ = make_service([gen_response, val_response])
    with pytest.raises(PracticeGenerationError, match="đạt kiểm tra nguồn"):
        service.generate_from_passages(
            [make_passage()],
            count=1,
            difficulty="hard",
            model="test-model",
            enforce_difficulty=True,
        )


def test_generation_prompt_allows_hypothetical_inputs_and_defines_hard_multi_step():
    """Prompt contract allows clearly labeled hypothetical inputs while demanding multi-step hard criteria."""
    passages = [make_passage()]
    prompt = build_practice_generation_prompt(passages, count=1, difficulty="hard")
    assert "dữ kiện giả định cụ thể làm đầu vào bài tập" in prompt
    assert "Mọi trạng thái ban đầu, đơn vị tính và quy ước chỉ số" in prompt
    assert "ít nhất hai bước lập luận phụ thuộc nhau" in prompt
    assert "Không suy diễn thêm quy tắc" in prompt


def test_verification_prompt_demands_independent_solving_and_arithmetic_check():
    """Verification prompt contract requires independent solving, step verification, and allows valid inputs."""
    service, ollama = make_service([{"checks": []}])
    internal_q = {
        "question": POSITIVE_CONTROL_QUESTION["question"],
        "choices": [{"label": k, "text": v} for k, v in POSITIVE_CONTROL_QUESTION["choices"].items()],
        "answer": POSITIVE_CONTROL_QUESTION["answer"],
        "explanation": POSITIVE_CONTROL_QUESTION["explanation"],
        "source": {"chunk_id": 1, "snippet": POSITIVE_CONTROL_QUESTION["evidence_quote"]},
    }
    service._verify_questions([internal_q], {1: make_passage()}, "test-model", difficulty="hard")
    assert len(ollama.prompts) == 1
    prompt = ollama.prompts[0]
    assert "tự giải độc lập" in prompt
    assert "mọi bước tính toán, chỉ số trung gian" in prompt
    assert "dữ kiện giả định nêu rõ trong đề là đầu vào bài tập hợp lệ" in prompt
    assert "hard là yêu cầu từ hai bước lập luận phụ thuộc nhau" in prompt
