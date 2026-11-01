"""History & Conversation Session API Routes (Single Responsibility Module)."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.dialogue import DialogueBusyError

router = APIRouter()


class SessionCreateRequest(BaseModel):
    title: Optional[str] = None


class SessionRenameRequest(BaseModel):
    title: str


class SessionSummarizeRequest(BaseModel):
    pass


@router.get("/api/history")
async def list_history():
    from app import server as _srv
    return {"items": _srv.history_store.list()}


@router.get("/api/history/{item_id}")
async def get_history(item_id: int):
    from app import server as _srv
    item = _srv.history_store.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt tra cứu")
    return item


@router.delete("/api/history/{item_id}")
async def delete_history(item_id: int):
    from app import server as _srv
    if not await _srv.locked_history_mutation(_srv.history_store.delete, item_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy lượt tra cứu")
    return {"deleted": True}


@router.delete("/api/history")
async def clear_history():
    from app import server as _srv
    await _srv.locked_history_mutation(_srv.history_store.clear)
    return {"deleted": True}


@router.get("/api/sessions")
async def list_sessions(limit: int = 50, offset: int = 0, include_legacy: bool = True):
    from app import server as _srv
    return _srv.history_store.list_sessions(limit=limit, offset=offset, include_legacy=include_legacy)


@router.post("/api/sessions")
async def create_session(req: Optional[SessionCreateRequest] = None):
    from app import server as _srv
    title = req.title if req else None
    return _srv.history_store.create_session(title=title)


@router.get("/api/sessions/search")
async def search_sessions(q: str = "", limit: int = 20, offset: int = 0):
    from app import server as _srv
    return _srv.history_store.search_sessions(query=q, limit=limit, offset=offset)


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    from app import server as _srv
    sess = _srv.history_store.get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
    turns = sess.get("turns", [])
    sess["recent_question"] = turns[-1]["question"] if turns else None
    sess["recent_source"] = turns[-1]["citations"][0] if turns and turns[-1].get("citations") else None
    return sess


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, req: SessionRenameRequest):
    from app import server as _srv
    if not req.title or not req.title.strip():
        raise HTTPException(status_code=400, detail="Tiêu đề không được để trống")
    ok = _srv.history_store.rename_session(session_id, req.title.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
    return {"renamed": True, "title": req.title.strip()[:120]}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    from app import server as _srv
    ok = await _srv.locked_history_mutation(_srv.history_store.delete_session, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên trò chuyện")
    return {"deleted": True, "session_id": session_id}


@router.delete("/api/sessions")
async def clear_all_sessions():
    from app import server as _srv
    await _srv.locked_history_mutation(_srv.history_store.clear)
    return {"deleted": True}


@router.post("/api/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, req: Optional[SessionSummarizeRequest] = None):
    from app import server as _srv
    model = _srv.DEFAULT_CHAT_MODEL

    def run_summary():
        with _srv.dialogue_gate.acquire():
            return _srv.summarizer.summarize_session_sync(session_id=session_id, model=model)

    try:
        result = await run_in_threadpool(run_summary)
    except DialogueBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Retry-After": "2"})
    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message", "Lỗi tóm tắt"))
    return result
