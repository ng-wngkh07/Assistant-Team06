"""Practice & Topic-Based Learning API Routes (Single Responsibility Module)."""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.dialogue import DialogueBusyError
from app.gpu_lock import GPUBusyError
from app.input_policy import validate_topic_input
from app.practice import PracticeGenerationError, PracticeSessionNotFound
from app.runtime_health import runtime_failure_message

logger = logging.getLogger("psychology_agent")

router = APIRouter()


class PracticeGenerateRequest(BaseModel):
    source_document_id: int = Field(ge=1)
    source_page_num: int = Field(ge=1)
    source_chunk_id: Optional[int] = Field(default=None, ge=1)
    count: int = Field(default=3, ge=1, le=3)
    processing_level: Optional[str] = "careful"


class PracticeAnswerRequest(BaseModel):
    practice_id: str = Field(min_length=16, max_length=64)
    question_id: str = Field(min_length=2, max_length=16)
    answer: Optional[Literal["A", "B", "C", "D"]] = None


class LearningGenerateRequest(BaseModel):
    topic: str
    count: int = Field(default=3, ge=1, le=10, strict=True)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    purpose: Literal["practice", "review"] = "practice"
    weak_topics: Optional[list[str]] = None
    model: Optional[str] = None
    processing_level: Optional[str] = "careful"

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        return validate_topic_input(v)

    @field_validator("weak_topics")
    @classmethod
    def validate_weak_topics(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is not None:
            if len(v) > 20:
                raise ValueError("Danh sách chủ đề còn yếu tối đa 20 mục.")
            return [validate_topic_input(item) for item in v]
        return v


class LearningExercisesRequest(BaseModel):
    topic: str
    limit: int = Field(default=10, ge=1, le=20)
    solve_missing: bool = False
    model: Optional[str] = None
    processing_level: Optional[str] = "careful"

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        return validate_topic_input(v)


@router.post("/api/practice/generate")
async def generate_practice(req: PracticeGenerateRequest):
    from app import server as _srv
    document = await run_in_threadpool(_srv.document_lookup.document, req.source_document_id)
    if not document or req.source_page_num > document["total_pages"]:
        raise HTTPException(status_code=404, detail="Không tìm thấy trang nguồn đã chọn.")

    def run_generation():
        with _srv.dialogue_gate.acquire(), _srv.gpu_coordinator.acquire_for_inference():
            return _srv.practice_service.generate(
                doc_id=req.source_document_id,
                page_num=req.source_page_num,
                count=req.count,
                model=_srv.DEFAULT_CHAT_MODEL,
                source_chunk_id=req.source_chunk_id,
            )

    try:
        return await run_in_threadpool(run_generation)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except PracticeGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Không tạo được bộ câu hỏi luyện tập")
        raise HTTPException(status_code=502, detail=runtime_failure_message(exc)) from exc


@router.get("/api/practice/examples")
async def list_practice_examples(
    document_id: int = Query(..., ge=1),
    page_from: int = Query(1, ge=1),
    page_to: Optional[int] = Query(None, ge=1),
    topic: str = Query("", max_length=120),
    limit: int = Query(20, ge=1, le=50),
):
    from app import server as _srv
    document = await run_in_threadpool(_srv.document_lookup.document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu.")
    final_page = page_to or document["total_pages"]
    if page_from > final_page or final_page > document["total_pages"]:
        raise HTTPException(status_code=422, detail="Khoảng trang không hợp lệ với tài liệu đã chọn.")
    try:
        items = await run_in_threadpool(
            _srv.practice_service.list_example_exercises,
            document_id,
            page_from,
            final_page,
            topic.strip(),
            limit,
        )
    except PracticeGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "items": items,
        "origin": "source_document",
        "document": document,
        "page_from": page_from,
        "page_to": final_page,
    }


@router.post("/api/practice/answer")
async def answer_practice(req: PracticeAnswerRequest):
    from app import server as _srv
    try:
        return _srv.practice_service.answer(req.practice_id, req.question_id, req.answer)
    except PracticeSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/api/learning/topics")
async def get_learning_topics():
    from app import server as _srv
    return await run_in_threadpool(_srv.learning_service.topics)


@router.post("/api/learning/generate")
async def generate_learning(req: LearningGenerateRequest):
    from app import server as _srv

    def run_generation():
        with _srv.dialogue_gate.acquire(), _srv.gpu_coordinator.acquire_for_inference():
            return _srv.learning_service.generate(
                topic=req.topic,
                count=req.count,
                difficulty=req.difficulty,
                purpose=req.purpose,
                weak_topics=req.weak_topics,
                model=_srv.DEFAULT_CHAT_MODEL,
            )

    try:
        return await run_in_threadpool(run_generation)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except PracticeGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Không tạo được bộ câu hỏi theo chủ đề")
        raise HTTPException(status_code=502, detail=runtime_failure_message(exc)) from exc


@router.post("/api/learning/exercises")
async def list_learning_exercises(req: LearningExercisesRequest):
    from app import server as _srv

    def run_exercises():
        if req.solve_missing:
            with _srv.dialogue_gate.acquire(), _srv.gpu_coordinator.acquire_for_inference():
                return _srv.learning_service.exercises(
                    topic=req.topic,
                    limit=req.limit,
                    solve_missing=req.solve_missing,
                    model=_srv.DEFAULT_CHAT_MODEL,
                )
        return _srv.learning_service.exercises(
            topic=req.topic,
            limit=req.limit,
            solve_missing=req.solve_missing,
            model=_srv.DEFAULT_CHAT_MODEL,
        )

    try:
        return await run_in_threadpool(run_exercises)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    except GPUBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"})
    except PracticeGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Không trích xuất được bài tập theo chủ đề")
        raise HTTPException(status_code=502, detail="Không trích xuất được bài tập lúc này. Hãy thử lại sau.") from exc
