"""Single-responsibility bridge connecting Weekly Lessons, Topic Classification,
90-minute (1-Subject/Day) Auto-Scheduling, and Exercise PDF Calendar Attachments.

Implements:
1. Automatic topic classification for weekly lessons (`classify_weekly_lesson`).
2. Topic-based worksheet PDF generation with a separate answer key at the end (`create_or_update_worksheet`).
3. Smart timetable auto-scheduler (`auto_schedule_90m_one_subject_per_day`):
   - Inspects all confirmed timetable classes and personal busy slots
   - Finds free windows across the week
   - Allocates 1 hour 30 minutes (90 minutes) per self-study session
   - Enforces at most 1 subject per day (1 môn/ngày) for optimal retention
   - Attaches the topic exercise PDF to each scheduled study session
4. Calendar `.ics` + PDF `.zip` bundle builder (`build_calendar_bundle_zip`) solving
   Google Calendar / Apple Calendar `.ics` binary attachment limitations.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import re
import sqlite3
import uuid
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

from app import timetable_store
from app.db.schemas import timetable_migrations
from app.exercise_pdf import generate_exercise_worksheet_pdf
from app.study_planner import (
    WEEKDAY_NAMES,
    minutes_to_time as _min_to_time,
    subtract_intervals as _subtract_intervals,
    time_to_minutes as _time_to_min,
)
from app.topic_tree import CHILD_TO_PARENT, TOPICS, infer_query_topic_hierarchy, resolve_topic_hierarchy

CURRENT_WORKSHEET_GENERATOR_VERSION = "source_exercise_v2"


def is_valid_worksheet(conn: sqlite3.Connection, worksheet_id: Optional[str]) -> bool:
    """Check if worksheet exists, has non-empty PDF content, and carries safe versioned provenance."""
    if not worksheet_id or not str(worksheet_id).strip():
        return False
    clean_id = str(worksheet_id).strip()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(planner_worksheets)").fetchall()}
    if "generator_version" not in cols:
        return False
    row = conn.execute(
        """
        SELECT worksheet_id, generator_version, pdf_blob
        FROM planner_worksheets
        WHERE worksheet_id = ?
        """,
        (clean_id,),
    ).fetchone()
    if not row:
        return False
    if str(row["generator_version"] or "").strip() != CURRENT_WORKSHEET_GENERATOR_VERSION:
        return False
    if not row["pdf_blob"] or len(bytes(row["pdf_blob"])) == 0:
        return False
    return True


@contextmanager
def _connect(db_path: Optional[Path | str] = None) -> Iterator[sqlite3.Connection]:
    if db_path is None:
        with timetable_store.get_connection() as conn:
            yield conn
        return
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        timetable_migrations.apply(conn)
        yield conn
    finally:
        conn.close()


def classify_weekly_lesson(course: str, lesson_title: str) -> dict[str, str]:
    """Classify a weekly lesson and course into a canonical topic and academic domain."""
    combined = f"{course or ''} {lesson_title or ''}".strip()
    hierarchy = infer_query_topic_hierarchy(combined)
    child_topics = hierarchy.get("child_topics") or []
    parent_labels = hierarchy.get("parent_labels") or []

    if child_topics:
        topic = child_topics[0]
        domain = parent_labels[0] if parent_labels else (CHILD_TO_PARENT.get(topic, {}).get("parent_label") or "Kiến thức đại cương")
        return {"classified_topic": topic, "domain_label": domain}

    for candidate in (lesson_title, course):
        resolved = resolve_topic_hierarchy(candidate)
        if resolved:
            return {
                "classified_topic": str(resolved.get("topic") or lesson_title),
                "domain_label": str(resolved.get("parent_label") or "Kiến thức chuyên ngành"),
            }

    cleaned = re.sub(
        r"^(?:chương|bài|tuần|buổi|phần)\s*\d+\s*[:.\-–—]?\s*",
        "",
        (lesson_title or "").strip(),
        flags=re.IGNORECASE,
    ).strip()
    fallback_topic = cleaned or (lesson_title or "").strip() or (course or "Chủ đề môn học").strip()
    return {
        "classified_topic": fallback_topic[:120],
        "domain_label": (course or "Môn học trên lớp").strip()[:120],
    }


def _synthesize_grounded_questions_for_topic(
    *,
    topic: str,
    course: str,
    lesson_title: str,
    count: int,
    learning_service: Any = None,
    document_lookup: Any = None,
) -> list[dict[str, Any]]:
    """Retrieve actual source-backed learning exercises preserving exact question, answer,
    question source, answer status and answer source. Never fabricate filler questions,
    always-A choices, or invent source quotes.
    """
    target_count = max(1, min(10, int(count or 4)))
    if learning_service is None:
        return []

    try:
        ex_data = learning_service.exercises(topic=topic, limit=target_count, solve_missing=False)
    except Exception:
        return []

    items = ex_data.get("items") or ex_data.get("exercises") or []
    questions: list[dict[str, Any]] = []

    for idx, item in enumerate(items[:target_count], start=1):
        if not isinstance(item, dict):
            continue
        q_text = str(item.get("question") or item.get("text") or "").strip()
        if not q_text:
            continue

        src_info = item.get("source") if isinstance(item.get("source"), dict) else {}
        ans = item.get("answer")
        ans_status = item.get("answer_status")
        ans_source = item.get("answer_source")
        explanation = item.get("explanation") or item.get("solution") or ""
        choices = item.get("choices") or []

        questions.append(
            {
                "id": f"q{idx}",
                "question": q_text,
                "source_context": item.get("source_context") or src_info.get("source_context") or "",
                "choices": choices,
                "answer": ans,
                "answer_status": ans_status,
                "answer_source": ans_source,
                "explanation": explanation,
                "source": src_info,
            }
        )

    return questions


def create_or_update_worksheet(
    db_path: Path | str,
    *,
    course: str,
    lesson_title: str,
    classified_topic: str,
    question_count: int = 4,
    learning_service: Any = None,
    document_lookup: Any = None,
) -> dict[str, Any]:
    """Generate a topic exercise PDF (with separate answer key at the end) and store it in planner_worksheets."""
    questions = _synthesize_grounded_questions_for_topic(
        topic=classified_topic,
        course=course,
        lesson_title=lesson_title,
        count=question_count,
        learning_service=learning_service,
        document_lookup=document_lookup,
    )
    if not questions:
        return {
            "worksheet_id": None,
            "course": (course or "").strip(),
            "topic": (classified_topic or "").strip(),
            "lesson_title": (lesson_title or "").strip(),
            "question_count": 0,
            "pdf_url": None,
            "status": "unavailable",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    pdf_bytes = generate_exercise_worksheet_pdf(
        topic=classified_topic,
        course=course,
        difficulty="medium",
        title=f"Bài tập: {lesson_title or classified_topic}",
        questions=questions,
    )
    worksheet_id = f"ws_{uuid.uuid4().hex[:12]}"
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    with _connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(planner_worksheets)").fetchall()}
        if "generator_version" in cols:
            conn.execute(
                """
                INSERT INTO planner_worksheets (
                    worksheet_id, course, topic, lesson_title, questions_json, pdf_blob, created_at, generator_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worksheet_id,
                    course.strip(),
                    classified_topic.strip(),
                    lesson_title.strip(),
                    json.dumps(questions, ensure_ascii=False),
                    sqlite3.Binary(pdf_bytes),
                    now_iso,
                    CURRENT_WORKSHEET_GENERATOR_VERSION,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO planner_worksheets (
                    worksheet_id, course, topic, lesson_title, questions_json, pdf_blob, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worksheet_id,
                    course.strip(),
                    classified_topic.strip(),
                    lesson_title.strip(),
                    json.dumps(questions, ensure_ascii=False),
                    sqlite3.Binary(pdf_bytes),
                    now_iso,
                ),
            )
        conn.commit()

    return {
        "worksheet_id": worksheet_id,
        "course": course.strip(),
        "topic": classified_topic.strip(),
        "lesson_title": lesson_title.strip(),
        "question_count": len(questions),
        "pdf_url": f"/api/timetable/worksheets/{worksheet_id}.pdf",
        "status": "available",
        "created_at": now_iso,
    }


def add_weekly_lesson(
    db_path: Path | str,
    *,
    course: str,
    lesson_title: str,
    status: str = "upcoming",
    question_count: int = 4,
    learning_service: Any = None,
    document_lookup: Any = None,
) -> dict[str, Any]:
    """Save a weekly lesson and auto-classify its topic.
    Exercises are generated ON DEMAND through LearningService.generate/PracticeService,
    not automatically on save.
    """
    clean_course = (course or "").strip()
    clean_title = (lesson_title or "").strip()
    if not clean_course or not clean_title:
        raise ValueError("Vui lòng nhập đầy đủ tên môn học và tên bài học trong tuần.")

    clean_status = "studied" if status == "studied" else "upcoming"
    q_count = max(1, min(10, int(question_count or 4)))

    classification = classify_weekly_lesson(clean_course, clean_title)
    classified_topic = classification["classified_topic"]
    domain_label = classification["domain_label"]
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    with _connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_lessons)").fetchall()}
        col_names = ["course", "lesson_title", "status", "classified_topic", "domain_label", "question_count", "worksheet_id", "created_at"]
        params = [clean_course, clean_title, clean_status, classified_topic, domain_label, q_count, None, now_iso]
        if "practice_id" in cols:
            col_names.append("practice_id")
            params.append(None)
        if "exercise_status" in cols:
            col_names.append("exercise_status")
            params.append("not_generated")

        placeholders = ", ".join(["?"] * len(col_names))
        cur = conn.execute(
            f"INSERT INTO weekly_lessons ({', '.join(col_names)}) VALUES ({placeholders})",
            params,
        )
        lesson_id = cur.lastrowid
        conn.commit()

    return {
        "id": lesson_id,
        "course": clean_course,
        "lesson_title": clean_title,
        "status": clean_status,
        "classified_topic": classified_topic,
        "domain_label": domain_label,
        "question_count": q_count,
        "worksheet_id": None,
        "practice_id": None,
        "pdf_url": None,
        "exercise_status": "not_generated",
        "worksheet_status": "not_generated",
        "created_at": now_iso,
    }


def list_weekly_lessons(db_path: Path | str) -> list[dict[str, Any]]:
    from app.practice import is_valid_practice_session
    with _connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(weekly_lessons)").fetchall()}
        p_cols = ""
        if "practice_id" in cols:
            p_cols += ", practice_id"
        if "exercise_status" in cols:
            p_cols += ", exercise_status"
        rows = conn.execute(
            f"""
            SELECT id, course, lesson_title, status, classified_topic,
                   domain_label, question_count, worksheet_id, created_at{p_cols}
            FROM weekly_lessons
            ORDER BY id DESC
            """
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            ws_id = (item.get("worksheet_id") or "").strip() or None
            p_id = (item.get("practice_id") or "").strip() or None
            item["worksheet_id"] = ws_id
            item["practice_id"] = p_id

            if p_id and is_valid_practice_session(p_id, db_path=db_path):
                item["pdf_url"] = f"/api/learning/export-pdf/{p_id}"
                item["exercise_status"] = "available"
                item["worksheet_status"] = "available"
            elif ws_id and is_valid_worksheet(conn, ws_id):
                item["pdf_url"] = f"/api/timetable/worksheets/{ws_id}.pdf"
                item["exercise_status"] = "available"
                item["worksheet_status"] = "available"
            else:
                item["pdf_url"] = None
                curr_status = item.get("exercise_status")
                # Linked legacy worksheet / stale practice failing verification is unavailable, not un-generated.
                item["exercise_status"] = (
                    "unavailable" if (curr_status == "unavailable" or ws_id or p_id) else "not_generated"
                )
                item["worksheet_status"] = item["exercise_status"]
            out.append(item)
    return out


def delete_weekly_lesson(db_path: Path | str, lesson_id: int) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM weekly_lessons WHERE id = ?", (int(lesson_id),))
        conn.commit()
        return cur.rowcount > 0


def get_worksheet_pdf(db_path: Path | str, worksheet_id: str) -> Optional[dict[str, Any]]:
    clean_id = (worksheet_id or "").strip()
    if not clean_id:
        return None
    with _connect(db_path) as conn:
        if not is_valid_worksheet(conn, clean_id):
            return None
        row = conn.execute(
            """
            SELECT worksheet_id, course, topic, lesson_title, pdf_blob, created_at
            FROM planner_worksheets
            WHERE worksheet_id = ?
            """,
            (clean_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "worksheet_id": row["worksheet_id"],
            "course": row["course"],
            "topic": row["topic"],
            "lesson_title": row["lesson_title"],
            "pdf_bytes": bytes(row["pdf_blob"]),
            "created_at": row["created_at"],
        }


def auto_schedule_90m_one_subject_per_day(
    db_path: Optional[Path | str] = None,
    *,
    learning_service: Any = None,
    document_lookup: Any = None,
    session_minutes: int = 90,
    propose_only: bool = False,
) -> dict[str, Any]:
    """Inspect all confirmed timetable classes, personal routines, and weekly lessons;
    propose and save a self-study schedule with at most 1 subject per day using the unified
    proposal/conflict-validation/confirmation workflow in app.study_planner.
    """
    from app.study_planner import confirm_study_plan, generate_study_plan

    duration = int(session_minutes or 90)
    draft = generate_study_plan(
        include_courses=True,
        session_duration=duration,
        db_path=db_path,
        learning_service=learning_service,
        document_lookup=document_lookup,
    )

    unplaced = draft.get("unplaced_courses") or []
    days_without_free_slot = draft.get("days_without_free_slot") or []
    explanation = draft.get("explanation") or draft.get("reasoning_summary") or ""

    if propose_only:
        return {
            "status": "draft",
            "plan_id": draft["plan_id"],
            "session_duration_minutes": duration,
            "max_subjects_per_day": 1,
            "scheduled_count": len(draft.get("sessions") or []),
            "sessions": draft.get("sessions") or [],
            "unplaced_courses": unplaced,
            "insufficient_space_warning": (
                f"Không đủ thời gian cho các môn: {', '.join(unplaced)}" if unplaced else None
            ),
            "days_without_free_slot": days_without_free_slot,
            "explanation": explanation,
        }

    confirmed = confirm_study_plan(draft["plan_id"], db_path=db_path)
    all_sessions = confirmed.get("sessions") or []
    plan_sessions = [
        s for s in all_sessions
        if s.get("plan_id") == draft["plan_id"]
        or (s.get("source") in ("ai_auto_90m", "ai_auto_courses") and not s.get("completed") and not s.get("locked"))
    ]
    if not plan_sessions:
        plan_sessions = all_sessions

    return {
        "status": "scheduled",
        "plan_id": draft["plan_id"],
        "session_duration_minutes": duration,
        "max_subjects_per_day": 1,
        "scheduled_count": len(plan_sessions),
        "sessions": plan_sessions,
        "unplaced_courses": unplaced,
        "insufficient_space_warning": (
            f"Không đủ thời gian cho các môn: {', '.join(unplaced)}" if unplaced else None
        ),
        "days_without_free_slot": days_without_free_slot,
        "explanation": explanation,
    }


def build_calendar_bundle_zip(
    db_path: Path | str,
    *,
    ics_bytes: bytes,
    base_url: str = "http://127.0.0.1:8000",
) -> bytes:
    """Create a .zip bundle containing the .ics file, all attached PDF exercise worksheets,
    and a Vietnamese guide explaining Google Calendar / Apple Calendar attachment compatibility.
    """
    with _connect(db_path) as conn:
        session_cols = {r[1] for r in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
        p_col = ", practice_id" if "practice_id" in session_cols else ""
        sessions = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT id, title, course, weekday, start_time, end_time,
                       classified_topic, worksheet_id{p_col}
                FROM study_sessions
                ORDER BY weekday, start_time
                """
            ).fetchall()
        ]
        worksheets_by_id: dict[str, dict[str, Any]] = {}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(planner_worksheets)").fetchall()}
        if "generator_version" in cols:
            for row in conn.execute(
                """
                SELECT worksheet_id, course, topic, lesson_title, pdf_blob, generator_version
                FROM planner_worksheets
                WHERE generator_version = ?
                """,
                (CURRENT_WORKSHEET_GENERATOR_VERSION,),
            ).fetchall():
                if row["pdf_blob"] and len(bytes(row["pdf_blob"])) > 0:
                    worksheets_by_id[row["worksheet_id"]] = dict(row)

    from app.exercise_pdf import generate_exercise_worksheet_pdf
    from app.practice import get_practice_db_path, is_valid_practice_session
    practice_pdfs_by_id: dict[str, bytes] = {}
    try:
        p_db = get_practice_db_path()
        if p_db.exists():
            with closing(sqlite3.connect(str(p_db))) as pconn:
                pconn.row_factory = sqlite3.Row
                for sess in sessions:
                    pid = (sess.get("practice_id") or "").strip()
                    if pid and pid not in practice_pdfs_by_id and is_valid_practice_session(pid, db_path=p_db):
                        prow = pconn.execute("SELECT questions_json, difficulty FROM practice_sessions WHERE practice_id = ?", (pid,)).fetchone()
                        if prow and prow["questions_json"]:
                            q_list = json.loads(prow["questions_json"])
                            if q_list:
                                pdf_data = generate_exercise_worksheet_pdf(
                                    topic=sess.get("classified_topic") or sess.get("course") or "Bài tập",
                                    course=sess.get("course") or "",
                                    difficulty=prow["difficulty"] or "medium",
                                    questions=q_list,
                                )
                                practice_pdfs_by_id[pid] = pdf_data
    except Exception as exc:
        logger.warning("Error generating practice PDF for calendar bundle: %s", exc)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("thoi-khoa-bieu-arym.ics", ics_bytes)

        manifest_lines = [
            "=========================================================================",
            "ARYM — Adaptive Revision Yields Mastery",
            "GÓI LỊCH HỌC TẬP (.ICS) & BÀI TẬP PDF ĐÍNH KÈM",
            "=========================================================================",
            "",
            "HƯỚNG DẪN SỬ DỤNG LỊCH HỌC VÀ BÀI TẬP:",
            "1. Nhập tệp thoi-khoa-bieu-arym.ics vào Google Calendar hoặc Apple Calendar.",
            "2. Các buổi tự học có bài tập đính kèm sẽ có liên kết tải trực tiếp trong ghi chú.",
            "3. Thư mục `bai-tap-pdf/` chứa các tệp PDF bài tập có sẵn để mở trực tiếp.",
            "",
            "DANH SÁCH CÁC BUỔI TỰ HỌC & TỆP BÀI TẬP:",
        ]

        written_ws: set[str] = set()
        for sess in sessions:
            p_id = (sess.get("practice_id") or "").strip()
            ws_id = (sess.get("worksheet_id") or "").strip()
            day_name = WEEKDAY_NAMES.get(int(sess["weekday"]), f"Thứ {sess['weekday']}")
            course_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (sess.get("course") or "mon").lower()).strip("-") or "mon"
            topic_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (sess.get("classified_topic") or "chude").lower()).strip("-") or "chude"
            if p_id and p_id in practice_pdfs_by_id:
                pdf_rel_path = f"bai-tap-pdf/thu-{sess['weekday']}-{course_slug}-{topic_slug}-{p_id}.pdf"
                manifest_lines.append(
                    f"   - {day_name} ({sess['start_time']} - {sess['end_time']}): "
                    f"{sess['course']} | Chủ đề: {sess.get('classified_topic') or 'Ôn tập'} "
                    f"-> Tệp: {pdf_rel_path} (URL: {base_url}/api/learning/export-pdf/{p_id})"
                )
                if p_id not in written_ws:
                    written_ws.add(p_id)
                    zf.writestr(pdf_rel_path, practice_pdfs_by_id[p_id])
            elif ws_id and ws_id in worksheets_by_id:
                pdf_rel_path = f"bai-tap-pdf/thu-{sess['weekday']}-{course_slug}-{topic_slug}-{ws_id}.pdf"
                manifest_lines.append(
                    f"   - {day_name} ({sess['start_time']} - {sess['end_time']}): "
                    f"{sess['course']} | Chủ đề: {sess.get('classified_topic') or 'Ôn tập'} "
                    f"-> Tệp: {pdf_rel_path} (URL: {base_url}/api/timetable/worksheets/{ws_id}.pdf)"
                )
                if ws_id not in written_ws:
                    written_ws.add(ws_id)
                    zf.writestr(pdf_rel_path, bytes(worksheets_by_id[ws_id]["pdf_blob"]))
            else:
                manifest_lines.append(
                    f"   - {day_name} ({sess['start_time']} - {sess['end_time']}): "
                    f"{sess['course']} | Chủ đề: {sess.get('classified_topic') or 'Ôn tập'} "
                    f"-> Chưa có bài tập PDF (tài liệu nguồn chưa cung cấp bài tập)"
                )

        # Also include any weekly lesson worksheets not yet in study_sessions
        for ws_id, ws_row in worksheets_by_id.items():
            if ws_id not in written_ws:
                written_ws.add(ws_id)
                c_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (ws_row.get("course") or "mon").lower()).strip("-") or "mon"
                t_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (ws_row.get("topic") or "chude").lower()).strip("-") or "chude"
                zf.writestr(f"bai-tap-pdf/{c_slug}-{t_slug}-{ws_id}.pdf", bytes(ws_row["pdf_blob"]))

        zf.writestr("HUONG_DAN_DINH_KEM_LICH.txt", "\n".join(manifest_lines) + "\n")

    return buf.getvalue()
