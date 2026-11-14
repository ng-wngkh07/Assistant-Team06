"""Personal AI Study Schedule Assistant (Bộ xếp lịch lập lịch học tập cá nhân).

Combines confirmed OCR class timetables, weekly personal routines (fixed, protected rest,
flexible activities, and study windows), personal daily limits, and task deadlines to
generate, explain, validate, and reschedule a personalized weekly study plan.

All data is stored strictly in the local SQLite timetable database (TIMETABLE_DB_PATH)
and never enters the RAG index or training datasets.
"""

import json
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app import timetable_store

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _ceil_vn_now(now: Optional[datetime] = None) -> datetime:
    """Return current Asia/Ho_Chi_Minh time rounded up to the next minute boundary.

    Uses the module-global ``datetime.now(VN_TZ)`` when ``now`` is omitted so
    tests overriding ``study_planner.datetime`` or passing ``now`` stay deterministic.
    """
    current = now if now is not None else datetime.now(VN_TZ)
    local_now = current.replace(tzinfo=VN_TZ) if current.tzinfo is None else current.astimezone(VN_TZ)
    if local_now.second or local_now.microsecond:
        return local_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return local_now.replace(second=0, microsecond=0)

TIME_REGEX = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")

WEEKDAY_NAMES: Dict[int, str] = {
    1: "Thứ 2",
    2: "Thứ 3",
    3: "Thứ 4",
    4: "Thứ 5",
    5: "Thứ 6",
    6: "Thứ 7",
    7: "Chủ nhật",
}

ACTIVITY_TYPES: Dict[str, str] = {
    "work": "Đi làm",
    "extra_class": "Học thêm",
    "gym": "Tập gym hoặc tập thể dục",
    "sport": "Chơi thể thao",
    "personal": "Đi chơi hoặc hoạt động cá nhân",
    "rest": "Nghỉ ngơi, ăn uống",
    "study_window": "Khung giờ có thể học",
    "other": "Hoạt động khác",
}

FLEXIBILITY_TYPES: Dict[str, str] = {
    "fixed": "Cố định (Không tự động thay đổi)",
    "protected": "Cần giữ (Bảo vệ thời gian nghỉ ngơi)",
    "flexible": "Có thể đổi giờ (Cần hỏi ý kiến trước khi đổi)",
    "study_window": "Có thể học (Khung giờ cho phép xếp lịch tự học)",
}

PRIORITY_RANK: Dict[str, int] = {
    "high": 0,
    "medium": 1,
    "low": 2,
}

PRIORITY_LABELS: Dict[str, str] = {
    "high": "Cao",
    "medium": "Trung bình",
    "low": "Thấp",
}


def time_to_minutes(t_str: str) -> int:
    """Convert HH:MM to minutes from 00:00 (0..1439)."""
    s = str(t_str or "").strip()
    if not TIME_REGEX.match(s):
        raise ValueError(f"Giờ không hợp lệ ({t_str}). Định dạng phải là HH:MM (00:00 - 23:59)")
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time(minutes: int) -> str:
    """Convert minutes from 00:00 back to HH:MM."""
    m = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{m // 60:02d}:{m % 60:02d}"


def merge_intervals(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping or adjacent [start, end) intervals."""
    valid = [(int(s), int(e)) for s, e in intervals if int(e) > int(s)]
    if not valid:
        return []
    valid.sort(key=lambda x: (x[0], x[1]))
    merged = [valid[0]]
    for s, e in valid[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged


def subtract_intervals(
    base_intervals: List[Tuple[int, int]],
    blocked_intervals: List[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Subtract blocked_intervals from base_intervals, returning free [start, end) segments."""
    free = merge_intervals(base_intervals)
    blocked = merge_intervals(blocked_intervals)
    if not free or not blocked:
        return free

    result: List[Tuple[int, int]] = []
    for s, e in free:
        cur_segments = [(s, e)]
        for bs, be in blocked:
            next_segments: List[Tuple[int, int]] = []
            for cs, ce in cur_segments:
                if be <= cs or bs >= ce:
                    next_segments.append((cs, ce))
                else:
                    if cs < bs:
                        next_segments.append((cs, bs))
                    if ce > be:
                        next_segments.append((be, ce))
            cur_segments = next_segments
            if not cur_segments:
                break
        result.extend(cur_segments)
    return [seg for seg in result if seg[1] > seg[0]]


# ==============================================================================
# 1. Personal Routines (Lịch sinh hoạt hằng tuần, giờ nghỉ & khung giờ có thể học)
# ==============================================================================


def validate_routine_data(data: Dict[str, Any]) -> Dict[str, Any]:
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Tên hoạt động không được để trống")

    try:
        weekday = int(data.get("weekday"))
    except (TypeError, ValueError):
        raise ValueError("Thứ trong tuần không hợp lệ (1 = Thứ 2 .. 7 = Chủ nhật)")
    if weekday < 1 or weekday > 7:
        raise ValueError("Thứ trong tuần phải từ 1 đến 7")

    start_time = str(data.get("start_time") or "").strip()
    end_time = str(data.get("end_time") or "").strip()
    s_min = time_to_minutes(start_time)
    e_min = time_to_minutes(end_time)
    if e_min <= s_min:
        raise ValueError(f"Giờ kết thúc ({end_time}) phải lớn hơn giờ bắt đầu ({start_time})")

    activity_type = str(data.get("activity_type") or "other").strip()
    if activity_type not in ACTIVITY_TYPES:
        raise ValueError(f"Loại hoạt động không hợp lệ: {activity_type}")

    flexibility = str(data.get("flexibility") or "").strip()
    if not flexibility:
        if activity_type == "study_window":
            flexibility = "study_window"
        elif activity_type == "rest":
            flexibility = "protected"
        else:
            flexibility = "fixed"

    if flexibility not in FLEXIBILITY_TYPES:
        raise ValueError(f"Cách xếp lịch không hợp lệ: {flexibility}")

    # Synchronize study_window type and flexibility if one of them is study_window
    if activity_type == "study_window" and flexibility != "study_window":
        flexibility = "study_window"
    elif flexibility == "study_window" and activity_type != "study_window":
        activity_type = "study_window"

    is_recurring = 1 if data.get("is_recurring", True) else 0
    note = str(data.get("note") or "").strip()

    return {
        "weekday": weekday,
        "start_time": start_time,
        "end_time": end_time,
        "title": title,
        "activity_type": activity_type,
        "flexibility": flexibility,
        "is_recurring": is_recurring,
        "note": note,
    }


def _enrich_routine(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row_dict)
    d["weekday_label"] = WEEKDAY_NAMES.get(d["weekday"], f"Thứ {d['weekday']}")
    d["activity_type_label"] = ACTIVITY_TYPES.get(d["activity_type"], d["activity_type"])
    d["flexibility_label"] = FLEXIBILITY_TYPES.get(d["flexibility"], d["flexibility"])
    d["is_recurring"] = bool(d.get("is_recurring", 1))
    return d


def get_routines(db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        rows = conn.execute("""
            SELECT id, weekday, start_time, end_time, title, activity_type, flexibility, is_recurring, note, created_at
            FROM personal_routines
            ORDER BY weekday ASC, start_time ASC, id ASC
        """).fetchall()
        return [_enrich_routine(dict(r)) for r in rows]


def check_routine_conflicts_with_classes(
    weekday: int,
    start_time: str,
    end_time: str,
    flexibility: str,
    title: str,
    exclude_routine_id: Optional[int] = None,
    db_path: Optional[Path | str] = None,
) -> List[str]:
    """Return warning messages if a non-study-window routine overlaps with confirmed classes or fixed routines."""
    if flexibility == "study_window":
        return []
    warnings: List[str] = []
    classes = timetable_store.get_confirmed_entries(db_path=db_path)
    day_label = WEEKDAY_NAMES.get(weekday, f"Thứ {weekday}")
    for c in classes:
        if c["weekday"] == weekday and start_time < c["end_time"] and c["start_time"] < end_time:
            warnings.append(
                f"Hoạt động '{title}' ({day_label} {start_time}-{end_time}) trùng giờ với lớp học cố định "
                f"'{c['course']}' ({c['start_time']}-{c['end_time']})."
            )
    routines = get_routines(db_path=db_path)
    for r in routines:
        if exclude_routine_id is not None and r["id"] == exclude_routine_id:
            continue
        if r["flexibility"] == "study_window":
            continue
        if r["weekday"] == weekday and start_time < r["end_time"] and r["start_time"] < end_time:
            warnings.append(
                f"Hoạt động '{title}' ({day_label} {start_time}-{end_time}) trùng giờ với hoạt động cá nhân "
                f"'{r['title']}' ({r['start_time']}-{r['end_time']})."
            )
    return warnings


def add_routine(data: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    clean = validate_routine_data(data)
    now_iso = datetime.now(timezone.utc).isoformat()
    with timetable_store.get_connection(db_path=db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO personal_routines
            (weekday, start_time, end_time, title, activity_type, flexibility, is_recurring, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean["weekday"],
                clean["start_time"],
                clean["end_time"],
                clean["title"],
                clean["activity_type"],
                clean["flexibility"],
                clean["is_recurring"],
                clean["note"],
                now_iso,
            ),
        )
        new_id = cursor.lastrowid or 0
    warnings = check_routine_conflicts_with_classes(
        clean["weekday"],
        clean["start_time"],
        clean["end_time"],
        clean["flexibility"],
        clean["title"],
        new_id,
        db_path=db_path,
    )
    return {
        "routine": _enrich_routine({**clean, "id": new_id, "created_at": now_iso}),
        "warnings": warnings,
    }


def update_routine(routine_id: int, data: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    clean = validate_routine_data(data)
    with timetable_store.get_connection(db_path=db_path) as conn:
        row = conn.execute("SELECT id, created_at FROM personal_routines WHERE id = ?", (routine_id,)).fetchone()
        if not row:
            raise KeyError("Không tìm thấy mục lịch sinh hoạt")
        conn.execute(
            """
            UPDATE personal_routines
            SET weekday = ?, start_time = ?, end_time = ?, title = ?, activity_type = ?, flexibility = ?, is_recurring = ?, note = ?
            WHERE id = ?
            """,
            (
                clean["weekday"],
                clean["start_time"],
                clean["end_time"],
                clean["title"],
                clean["activity_type"],
                clean["flexibility"],
                clean["is_recurring"],
                clean["note"],
                routine_id,
            ),
        )
        created_at = row["created_at"]
    warnings = check_routine_conflicts_with_classes(
        clean["weekday"],
        clean["start_time"],
        clean["end_time"],
        clean["flexibility"],
        clean["title"],
        routine_id,
        db_path=db_path,
    )
    return {
        "routine": _enrich_routine({**clean, "id": routine_id, "created_at": created_at}),
        "warnings": warnings,
    }


def delete_routine(routine_id: int, db_path: Optional[Path | str] = None) -> bool:
    with timetable_store.get_connection(db_path=db_path) as conn:
        cursor = conn.execute("DELETE FROM personal_routines WHERE id = ?", (routine_id,))
        return cursor.rowcount > 0


def replace_routines_bulk(items: List[Dict[str, Any]], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Replace the entire weekly personal routine table with validated rows."""
    validated = [validate_routine_data(item) for item in items]
    now_iso = datetime.now(timezone.utc).isoformat()
    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM personal_routines")
        for clean in validated:
            conn.execute(
                """
                INSERT INTO personal_routines
                (weekday, start_time, end_time, title, activity_type, flexibility, is_recurring, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean["weekday"],
                    clean["start_time"],
                    clean["end_time"],
                    clean["title"],
                    clean["activity_type"],
                    clean["flexibility"],
                    clean["is_recurring"],
                    clean["note"],
                    now_iso,
                ),
            )
    saved = get_routines(db_path=db_path)
    all_warnings: List[str] = []
    for r in saved:
        w = check_routine_conflicts_with_classes(
            r["weekday"], r["start_time"], r["end_time"], r["flexibility"], r["title"], r["id"], db_path=db_path
        )
        for msg in w:
            if msg not in all_warnings:
                all_warnings.append(msg)
    return {"routines": saved, "warnings": all_warnings}


# ==============================================================================
# 2. Study Preferences & Natural-Language AI Preference Understanding
# ==============================================================================


DEFAULT_PREFERENCES: Dict[str, Any] = {
    "earliest_study_time": "07:00",
    "latest_study_time": "22:30",
    "max_daily_study_minutes": 240,
    "preferred_session_minutes": 60,
    "min_session_minutes": 30,
    "buffer_before_deadline_hours": 12,
    "ai_notes": "",
    "week_start_date": None,
}


def get_preferences(db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        row = conn.execute("SELECT * FROM study_preferences WHERE id = 1").fetchone()
        if not row:
            return dict(DEFAULT_PREFERENCES)
        data = dict(row)
        data.pop("id", None)
        return data


def update_preferences(payload: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    current = get_preferences(db_path=db_path)
    earliest = str(payload.get("earliest_study_time") or current["earliest_study_time"]).strip()
    latest = str(payload.get("latest_study_time") or current["latest_study_time"]).strip()
    e_min = time_to_minutes(earliest)
    l_min = time_to_minutes(latest)
    if l_min <= e_min:
        raise ValueError("Giờ kết thúc học muộn nhất phải lớn hơn giờ bắt đầu học sớm nhất")

    try:
        max_daily = int(payload.get("max_daily_study_minutes", current["max_daily_study_minutes"]))
    except (TypeError, ValueError):
        raise ValueError("Giới hạn số phút học tối đa mỗi ngày không hợp lệ")
    if max_daily < 30 or max_daily > 16 * 60:
        raise ValueError("Số phút học tối đa mỗi ngày phải từ 30 đến 960 phút (0.5 - 16 giờ)")

    try:
        pref_sess = int(payload.get("preferred_session_minutes", current["preferred_session_minutes"]))
    except (TypeError, ValueError):
        raise ValueError("Thời lượng mỗi phiên học không hợp lệ")
    if pref_sess < 15 or pref_sess > 240:
        raise ValueError("Thời lượng mỗi phiên học phải từ 15 đến 240 phút")

    try:
        min_sess = int(payload.get("min_session_minutes", current.get("min_session_minutes", 30)))
    except (TypeError, ValueError):
        min_sess = 30
    min_sess = max(15, min(pref_sess, min_sess))

    try:
        buffer_hours = int(payload.get("buffer_before_deadline_hours", current.get("buffer_before_deadline_hours", 12)))
    except (TypeError, ValueError):
        buffer_hours = 12
    buffer_hours = max(0, min(72, buffer_hours))

    ai_notes = str(payload.get("ai_notes", current.get("ai_notes", "")) or "").strip()
    week_start_date = payload.get("week_start_date", current.get("week_start_date"))
    if week_start_date is not None:
        week_start_date = str(week_start_date).strip() or None
        if week_start_date and not DATE_REGEX.match(week_start_date):
            raise ValueError("Ngày bắt đầu tuần phải có định dạng YYYY-MM-DD")

    now_iso = datetime.now(timezone.utc).isoformat()
    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute(
            """
            INSERT INTO study_preferences
            (id, earliest_study_time, latest_study_time, max_daily_study_minutes,
             preferred_session_minutes, min_session_minutes, buffer_before_deadline_hours,
             ai_notes, week_start_date, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                earliest_study_time = excluded.earliest_study_time,
                latest_study_time = excluded.latest_study_time,
                max_daily_study_minutes = excluded.max_daily_study_minutes,
                preferred_session_minutes = excluded.preferred_session_minutes,
                min_session_minutes = excluded.min_session_minutes,
                buffer_before_deadline_hours = excluded.buffer_before_deadline_hours,
                ai_notes = excluded.ai_notes,
                week_start_date = excluded.week_start_date,
                updated_at = excluded.updated_at
            """,
            (
                earliest,
                latest,
                max_daily,
                pref_sess,
                min_sess,
                buffer_hours,
                ai_notes,
                week_start_date,
                now_iso,
            ),
        )
    return get_preferences(db_path=db_path)


def parse_ai_preference_notes(ai_notes: str, base_prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Parse natural-language Vietnamese study preferences from the user's note."""
    text = (ai_notes or "").strip()
    lower = text.lower()
    insights: List[str] = []

    earliest_min = time_to_minutes(base_prefs.get("earliest_study_time", "07:00"))
    latest_min = time_to_minutes(base_prefs.get("latest_study_time", "22:30"))
    max_daily_min = int(base_prefs.get("max_daily_study_minutes", 240))
    pref_session_min = int(base_prefs.get("preferred_session_minutes", 60))

    preferred_periods: List[str] = []  # "morning", "afternoon", "evening"
    avoided_weekdays: List[int] = []

    if any(k in lower for k in ("buổi sáng", "sáng sớm", "ưu tiên sáng", "học sáng")):
        preferred_periods.append("morning")
        insights.append("Ưu tiên xếp các phiên tự học vào khung giờ buổi sáng (trước 12:00).")
    if any(k in lower for k in ("buổi chiều", "ưu tiên chiều", "học chiều")):
        preferred_periods.append("afternoon")
        insights.append("Ưu tiên xếp các phiên tự học vào khung giờ buổi chiều (13:00 - 18:00).")
    if any(k in lower for k in ("buổi tối", "ưu tiên tối", "học tối", "ban đêm")):
        preferred_periods.append("evening")
        insights.append("Ưu tiên xếp các phiên tự học vào khung giờ buổi tối (sau 18:00).")

    # Detect "không học sau HH:MM" or "kết thúc trước HH:MM"
    m_after = re.search(r"(?:không học sau|trước|kết thúc trước)\s*(\d{1,2})(?:[:h](\d{2}))?\s*(?:h|giờ)?", lower)
    if m_after:
        hh = int(m_after.group(1))
        mm = int(m_after.group(2) or 0)
        if 12 <= hh <= 23:
            candidate_latest = hh * 60 + mm
            if candidate_latest > earliest_min and candidate_latest < latest_min:
                latest_min = candidate_latest
                insights.append(f"Giới hạn giờ kết thúc học trong ngày không quá {minutes_to_time(latest_min)} theo yêu cầu.")

    # Detect "tối đa X giờ/tiếng mỗi ngày"
    m_max_h = re.search(r"tối đa\s*(\d+(?:[.,]\d+)?)\s*(?:giờ|tiếng|h)", lower)
    if m_max_h:
        hours_val = float(m_max_h.group(1).replace(",", "."))
        if 0.5 <= hours_val <= 14:
            max_daily_min = int(round(hours_val * 60))
            insights.append(f"Áp dụng trần thời lượng tự học tối đa {hours_val:g} giờ/ngày ({max_daily_min} phút).")

    # Detect "mỗi phiên X phút"
    m_sess = re.search(r"(?:mỗi phiên|phiên học|chia thành)\s*(\d{2,3})\s*phút", lower)
    if m_sess:
        val = int(m_sess.group(1))
        if 20 <= val <= 180:
            pref_session_min = val
            insights.append(f"Chia các bài tập thành từng phiên khoảng {pref_session_min} phút.")

    # Detect avoided days like "nghỉ chủ nhật" or "không học chủ nhật" or "tránh thứ 7"
    day_patterns = [
        (r"(?:không học|nghỉ|tránh)\s*(?:vào\s*)?(?:chủ nhật|cn)", 7, "Chủ nhật"),
        (r"(?:không học|nghỉ|tránh)\s*(?:vào\s*)?(?:thứ 7|thứ bảy|t7)", 6, "Thứ 7"),
        (r"(?:không học|nghỉ|tránh)\s*(?:vào\s*)?(?:thứ 6|thứ sáu|t6)", 5, "Thứ 6"),
    ]
    for pat, w_num, w_label in day_patterns:
        if re.search(pat, lower):
            avoided_weekdays.append(w_num)
            insights.append(f"Hạn chế xếp lịch tự học vào {w_label} theo mong muốn cá nhân.")

    return {
        "earliest_min": earliest_min,
        "latest_min": latest_min,
        "max_daily_min": max_daily_min,
        "pref_session_min": pref_session_min,
        "preferred_periods": preferred_periods,
        "avoided_weekdays": avoided_weekdays,
        "insights": insights,
    }


# ==============================================================================
# 3. Study Tasks & Deadlines (Nhập bài tập và deadline)
# ==============================================================================


def _parse_task_deadline(data: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Tuple[int, str, Optional[str]]:
    """Normalize deadline inputs (deadline ISO string, or deadline_date + deadline_time, or deadline_weekday + deadline_time)."""
    raw_iso = data.get("deadline")
    deadline_date = data.get("deadline_date", existing.get("deadline_date") if existing else None)
    deadline_time = data.get("deadline_time", existing.get("deadline_time") if existing else "23:59")
    deadline_weekday = data.get("deadline_weekday", existing.get("deadline_weekday") if existing else None)

    if raw_iso and isinstance(raw_iso, str) and raw_iso.strip():
        s = raw_iso.strip().replace(" ", "T")
        if "T" in s:
            d_part, t_part = s.split("T", 1)
            deadline_date = d_part.strip()
            deadline_time = t_part.strip()[:5]
        elif DATE_REGEX.match(s):
            deadline_date = s

    if deadline_date is not None:
        deadline_date = str(deadline_date).strip() or None
    if deadline_date:
        if not DATE_REGEX.match(deadline_date):
            raise ValueError("Ngày hạn nộp (deadline_date) phải có định dạng YYYY-MM-DD")
        try:
            dt_obj = datetime.strptime(deadline_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValueError(f"Ngày hạn nộp không hợp lệ: {e}")
        # ISO weekday: Monday=1 .. Sunday=7
        deadline_weekday = dt_obj.isoweekday()

    if deadline_weekday is None or deadline_weekday == "":
        raise ValueError("Vui lòng chọn ngày/thứ hạn nộp (deadline)")
    try:
        w = int(deadline_weekday)
    except (TypeError, ValueError):
        raise ValueError("Thứ hạn nộp không hợp lệ")
    if w < 1 or w > 7:
        raise ValueError("Thứ hạn nộp phải từ 1 (Thứ 2) đến 7 (Chủ nhật)")

    t_str = str(deadline_time or "23:59").strip()
    time_to_minutes(t_str)  # validates HH:MM
    return w, t_str, deadline_date


def _enrich_task(row_dict: Dict[str, Any], session_stats: Optional[Dict[int, Dict[str, int]]] = None) -> Dict[str, Any]:
    d = dict(row_dict)
    w = d["deadline_weekday"]
    d["deadline_weekday_label"] = WEEKDAY_NAMES.get(w, f"Thứ {w}")
    d["priority_label"] = PRIORITY_LABELS.get(d["priority"], "Trung bình")
    d["completed"] = bool(d.get("completed", 0))
    d["splittable"] = bool(d.get("splittable", 1))
    est = d.get("estimated_minutes")
    d["missing_estimate"] = (est is None or int(est) <= 0)
    if d.get("deadline_date"):
        d["deadline_display"] = f"{d['deadline_weekday_label']} ({d['deadline_date']}) {d['deadline_time']}"
    else:
        d["deadline_display"] = f"{d['deadline_weekday_label']} - {d['deadline_time']}"

    stats = (session_stats or {}).get(d["id"], {"scheduled": 0, "completed": 0})
    d["scheduled_minutes"] = stats["scheduled"]
    d["completed_session_minutes"] = stats["completed"]
    return d


def get_tasks(db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        rows = conn.execute("""
            SELECT id, title, course, deadline_weekday, deadline_time, deadline_date,
                   estimated_minutes, priority, completed, completed_minutes, splittable, notes, created_at
            FROM study_tasks
            ORDER BY completed ASC,
                     COALESCE(deadline_date, '9999-99-99') ASC,
                     deadline_weekday ASC,
                     deadline_time ASC,
                     id ASC
        """).fetchall()
        sess_rows = conn.execute("""
            SELECT task_id, duration_minutes, completed
            FROM study_sessions
            WHERE task_id IS NOT NULL
        """).fetchall()

    stats: Dict[int, Dict[str, int]] = {}
    for sr in sess_rows:
        tid = sr["task_id"]
        if tid not in stats:
            stats[tid] = {"scheduled": 0, "completed": 0}
        dur = int(sr["duration_minutes"] or 0)
        stats[tid]["scheduled"] += dur
        if sr["completed"]:
            stats[tid]["completed"] += dur

    return [_enrich_task(dict(r), stats) for r in rows]


def add_task(data: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("Tên bài tập hoặc công việc không được để trống")
    course = str(data.get("course") or "").strip()
    if not course:
        raise ValueError("Tên môn học không được để trống")

    w, t_str, d_date = _parse_task_deadline(data)

    raw_est = data.get("estimated_minutes")
    estimated_minutes: Optional[int] = None
    if raw_est is not None and str(raw_est).strip() != "":
        try:
            estimated_minutes = int(raw_est)
        except (TypeError, ValueError):
            raise ValueError("Thời gian ước tính phải là số nguyên (phút)")
        if estimated_minutes < 0 or estimated_minutes > 10000:
            raise ValueError("Thời gian ước tính phải từ 1 đến 10000 phút")
        if estimated_minutes == 0:
            estimated_minutes = None

    priority = str(data.get("priority") or "medium").strip().lower()
    if priority not in PRIORITY_RANK:
        raise ValueError("Mức độ ưu tiên phải là 'high', 'medium' hoặc 'low'")

    completed = 1 if data.get("completed", False) else 0
    splittable = 1 if data.get("splittable", True) else 0
    notes = str(data.get("notes") or "").strip()
    now_iso = datetime.now(timezone.utc).isoformat()

    with timetable_store.get_connection(db_path=db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO study_tasks
            (title, course, deadline_weekday, deadline_time, deadline_date,
             estimated_minutes, priority, completed, completed_minutes, splittable, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (title, course, w, t_str, d_date, estimated_minutes, priority, completed, splittable, notes, now_iso),
        )
        task_id = cursor.lastrowid or 0

    tasks = get_tasks(db_path=db_path)
    created = next((t for t in tasks if t["id"] == task_id), None)
    return created or {}


def update_task(task_id: int, data: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        existing_row = conn.execute("SELECT * FROM study_tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing_row:
            raise KeyError("Không tìm thấy bài tập")
        existing = dict(existing_row)

    title = str(data.get("title", existing["title"]) or "").strip()
    if not title:
        raise ValueError("Tên bài tập hoặc công việc không được để trống")
    course = str(data.get("course", existing["course"]) or "").strip()
    if not course:
        raise ValueError("Tên môn học không được để trống")

    w, t_str, d_date = _parse_task_deadline(data, existing=existing)

    if "estimated_minutes" in data:
        raw_est = data.get("estimated_minutes")
        if raw_est is None or str(raw_est).strip() == "":
            estimated_minutes = None
        else:
            try:
                estimated_minutes = int(raw_est)
            except (TypeError, ValueError):
                raise ValueError("Thời gian ước tính phải là số nguyên (phút)")
            if estimated_minutes < 0 or estimated_minutes > 10000:
                raise ValueError("Thời gian ước tính phải từ 1 đến 10000 phút")
            if estimated_minutes == 0:
                estimated_minutes = None
    else:
        estimated_minutes = existing["estimated_minutes"]

    priority = str(data.get("priority", existing["priority"]) or "medium").strip().lower()
    if priority not in PRIORITY_RANK:
        raise ValueError("Mức độ ưu tiên phải là 'high', 'medium' hoặc 'low'")

    completed = 1 if data.get("completed", bool(existing["completed"])) else 0
    splittable = 1 if data.get("splittable", bool(existing["splittable"])) else 0
    notes = str(data.get("notes", existing["notes"]) or "").strip()

    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute(
            """
            UPDATE study_tasks
            SET title = ?, course = ?, deadline_weekday = ?, deadline_time = ?, deadline_date = ?,
                estimated_minutes = ?, priority = ?, completed = ?, splittable = ?, notes = ?
            WHERE id = ?
            """,
            (title, course, w, t_str, d_date, estimated_minutes, priority, completed, splittable, notes, task_id),
        )
        if completed:
            conn.execute("UPDATE study_sessions SET completed = 1 WHERE task_id = ?", (task_id,))

    tasks = get_tasks(db_path=db_path)
    updated = next((t for t in tasks if t["id"] == task_id), None)
    return updated or {}


def delete_task(task_id: int, db_path: Optional[Path | str] = None) -> bool:
    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute("DELETE FROM study_sessions WHERE task_id = ? AND completed = 0 AND locked = 0", (task_id,))
        cursor = conn.execute("DELETE FROM study_tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0


# ==============================================================================
# 4. Study Sessions & Drafts Storage
# ==============================================================================


def _enrich_session(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row_dict)
    w = int(d.get("weekday") or 1)
    d["weekday_label"] = WEEKDAY_NAMES.get(w, f"Thứ {w}")
    d["weekday_name"] = d["weekday_label"]
    d["priority_label"] = PRIORITY_LABELS.get(d.get("priority") or "medium", "Trung bình")
    d["completed"] = bool(d.get("completed", 0))
    d["locked"] = bool(d.get("locked", 0))
    src = d.get("source") or "ai_assistant"
    if src in ("ai_auto_90m", "ai_auto_courses"):
        d["source_label"] = "Đề xuất thông minh"
    elif src == "ai_assistant":
        d["source_label"] = "Bộ xếp lịch đề xuất"
    else:
        d["source_label"] = "Người dùng tự xếp"
    ws_id = (d.get("worksheet_id") or "").strip()
    p_id = (d.get("practice_id") or "").strip()
    d["classified_topic"] = (d.get("classified_topic") or "").strip()
    d["worksheet_id"] = ws_id or None
    d["practice_id"] = p_id or None

    ex_status = (d.get("exercise_status") or "").strip()
    if not ex_status:
        if p_id or ws_id:
            ex_status = "available"
        else:
            ex_status = "not_generated"
    d["exercise_status"] = ex_status

    if p_id:
        d["pdf_url"] = f"/api/learning/export-pdf/{p_id}"
    elif ws_id:
        d["pdf_url"] = f"/api/timetable/worksheets/{ws_id}.pdf"
    else:
        d["pdf_url"] = None
    return d


def get_study_sessions(db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
        extra_fields = [c for c in ("classified_topic", "worksheet_id", "practice_id", "exercise_status", "weekly_lesson_id") if c in cols]
        extra_cols = (", " + ", ".join(extra_fields)) if extra_fields else ""
        rows = conn.execute(f"""
            SELECT id, plan_id, task_id, title, course, weekday, start_time, end_time,
                   duration_minutes, priority, source, completed, locked,
                   session_index, total_sessions, notes, created_at{extra_cols}
            FROM study_sessions
            ORDER BY weekday ASC, start_time ASC, id ASC
        """).fetchall()
        from app.planner_topic_bridge import is_valid_worksheet

        results = []
        for r in rows:
            sess_dict = _enrich_session(dict(r))
            ws_id = sess_dict.get("worksheet_id")
            if ws_id and not is_valid_worksheet(conn, ws_id):
                if not sess_dict.get("pdf_url") or sess_dict.get("pdf_url", "").startswith("/api/timetable/worksheets/"):
                    sess_dict["pdf_url"] = None
                if sess_dict.get("notes"):
                    sess_dict["notes"] = re.sub(
                        r"Bài tập PDF đính kèm(?:\s*\([^)]*\))?:\s*(?:https?://[^\s|]+|/api/timetable/worksheets/[^\s|]+)",
                        "(Chưa có bài tập PDF đính kèm)",
                        sess_dict["notes"],
                        flags=re.IGNORECASE,
                    )
            results.append(sess_dict)
        return results


def get_session(session_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
        extra_fields = [c for c in ("classified_topic", "worksheet_id", "practice_id", "exercise_status", "weekly_lesson_id") if c in cols]
        extra_cols = (", " + ", ".join(extra_fields)) if extra_fields else ""
        row = conn.execute(
            f"""
            SELECT id, plan_id, task_id, title, course, weekday, start_time, end_time,
                   duration_minutes, priority, source, completed, locked,
                   session_index, total_sessions, notes, created_at{extra_cols}
            FROM study_sessions
            WHERE id = ?
            """,
            (int(session_id),),
        ).fetchone()
        if not row:
            return None
        return _enrich_session(dict(row))


def update_session_practice(
    session_id: int,
    practice_id: str,
    exercise_status: str = "available",
    db_path: Optional[Path | str] = None,
) -> bool:
    with timetable_store.get_connection(db_path=db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
        updates = []
        params = []
        if "practice_id" in cols:
            updates.append("practice_id = ?")
            params.append(str(practice_id or "").strip())
        if "exercise_status" in cols:
            updates.append("exercise_status = ?")
            params.append(str(exercise_status or "").strip())
        if not updates:
            return False
        params.append(int(session_id))
        cur = conn.execute(f"UPDATE study_sessions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        return cur.rowcount > 0


def get_latest_active_plan_draft(db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        row = conn.execute("""
            SELECT * FROM study_plan_drafts
            WHERE status = 'draft'
            ORDER BY created_at DESC
            LIMIT 1
        """).fetchone()
        if not row:
            return None
        return _deserialize_plan_draft(dict(row))


def get_plan_draft(plan_id: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with timetable_store.get_connection(db_path=db_path) as conn:
        row = conn.execute("SELECT * FROM study_plan_drafts WHERE plan_id = ?", (plan_id,)).fetchone()
        if not row:
            return None
        return _deserialize_plan_draft(dict(row))


def _deserialize_plan_draft(data: Dict[str, Any]) -> Dict[str, Any]:
    sessions = json.loads(data.get("sessions_json") or "[]")
    preserved = json.loads(data.get("preserved_sessions_json") or "[]")
    raw_unscheduled = json.loads(data.get("unscheduled_json") or "[]")
    unscheduled_tasks = []
    unplaced_courses = []
    if isinstance(raw_unscheduled, dict):
        unscheduled_tasks = raw_unscheduled.get("tasks") or []
        unplaced_courses = raw_unscheduled.get("courses") or []
    elif isinstance(raw_unscheduled, list):
        for item in raw_unscheduled:
            if isinstance(item, dict) and item.get("course") and not item.get("task_id"):
                unplaced_courses.append(str(item["course"]))
            else:
                unscheduled_tasks.append(item)
    return {
        "plan_id": data["plan_id"],
        "created_at": data["created_at"],
        "status": data["status"],
        "sessions": [_enrich_session(s) for s in sessions],
        "preserved_sessions": [_enrich_session(s) for s in preserved],
        "unscheduled_tasks": unscheduled_tasks,
        "unplaced_courses": unplaced_courses,
        "warnings": json.loads(data.get("warnings_json") or "[]"),
        "suggestions": json.loads(data.get("suggestions_json") or "[]"),
        "explanation": data.get("explanation") or "",
    }


def discard_plan_draft(plan_id: str, db_path: Optional[Path | str] = None) -> bool:
    with timetable_store.get_connection(db_path=db_path) as conn:
        cursor = conn.execute(
            "UPDATE study_plan_drafts SET status = 'discarded' WHERE plan_id = ?",
            (plan_id,),
        )
        return cursor.rowcount > 0


# ==============================================================================
# 5. Core Scheduling Engine & Constraint Verifier
# ==============================================================================


def verify_schedule_constraints(
    sessions: List[Dict[str, Any]],
    confirmed_classes: List[Dict[str, Any]],
    routines: List[Dict[str, Any]],
    tasks_by_id: Dict[int, Dict[str, Any]],
    require_inside_study_window: bool = False,
    max_daily_minutes: Optional[int] = None,
    week_monday: Optional[date] = None,
    earliest_study_time: Optional[str] = None,
    latest_study_time: Optional[str] = None,
    enforce_awake_bounds: bool = False,
    now: Optional[datetime] = None,
) -> List[str]:
    """Deterministically verify hard scheduling constraints. Returns list of violation messages."""
    violations: List[str] = []
    ceil_now = _ceil_vn_now(now) if week_monday is not None else None

    # 1. Check each session's time format & range, plus overlap with classes and blocking routines
    for idx, s in enumerate(sessions):
        w = int(s["weekday"])
        day_label = WEEKDAY_NAMES.get(w, f"Thứ {w}")
        st = str(s["start_time"]).strip()
        et = str(s["end_time"]).strip()
        s_min = time_to_minutes(st)
        e_min = time_to_minutes(et)
        if e_min <= s_min:
            violations.append(f"Phiên học '{s['title']}' ({day_label}) có giờ kết thúc ({et}) không sau giờ bắt đầu ({st}).")
            continue

        # Check against confirmed classes
        for c in confirmed_classes:
            if c["weekday"] == w and st < c["end_time"] and c["start_time"] < et:
                violations.append(
                    f"Phiên học '{s['title']}' ({day_label} {st}-{et}) trùng giờ với lớp học cố định "
                    f"'{c['course']}' ({c['start_time']}-{c['end_time']})."
                )

        # Check against fixed, protected (rest), and unapproved flexible routines (handling cross-midnight)
        for r in routines:
            if r.get("flexibility") in ("fixed", "protected", "flexible") or r.get("activity_type") in ("sleep", "rest", "work", "class", "commute", "sport", "meal"):
                r_w = int(r["weekday"])
                r_s = time_to_minutes(r["start_time"])
                r_e = time_to_minutes(r["end_time"])
                collides = False
                if r_s < r_e:
                    if r_w == w and st < r["end_time"] and r["start_time"] < et:
                        collides = True
                else:
                    if r_w == w and s_min < 1440 and r_s < e_min:
                        collides = True
                    next_w = (r_w % 7) + 1
                    if next_w == w and s_min < r_e and 0 < e_min:
                        collides = True
                if collides:
                    kind = (
                        "giờ nghỉ ngơi được bảo vệ"
                        if r.get("flexibility") == "protected" or r.get("activity_type") == "rest"
                        else f"hoạt động cá nhân ({FLEXIBILITY_TYPES.get(r.get('flexibility'), r.get('flexibility'))})"
                    )
                    violations.append(
                        f"Phiên học '{s['title']}' ({day_label} {st}-{et}) xâm phạm {kind} "
                        f"'{r['title']}' ({r['start_time']}-{r['end_time']})."
                    )

        # Historical preserved records remain exempt from shifts in awake bounds / deadlines / current-time cutoff,
        # but ALL new reviewed proposals must validate awake/night/window/deadline/cutoff/task existence regardless of client flags.
        is_preserved = bool(s.get("_is_preserved", False))
        if not is_preserved:
            if week_monday is not None and ceil_now is not None:
                session_date = week_monday + timedelta(days=w - 1)
                session_start_dt = datetime.combine(
                    session_date,
                    time(s_min // 60, s_min % 60),
                    tzinfo=VN_TZ,
                )
                if session_start_dt < ceil_now:
                    violations.append(
                        f"Phiên học '{s['title']}' ({day_label} {st}-{et}, ngày {session_date.isoformat()}) "
                        f"bắt đầu trước thời điểm hiện tại ({ceil_now.strftime('%Y-%m-%d %H:%M')}) — không thể xếp phiên học mới vào quá khứ."
                    )

            if enforce_awake_bounds or earliest_study_time or latest_study_time:
                if earliest_study_time:
                    early_min = time_to_minutes(earliest_study_time)
                    if s_min < early_min:
                        violations.append(
                            f"Phiên học '{s['title']}' ({day_label} {st}-{et}) bắt đầu trước khung giờ học sớm nhất đã thiết lập ({earliest_study_time})."
                        )
                if latest_study_time:
                    late_min = time_to_minutes(latest_study_time)
                    if e_min > late_min:
                        violations.append(
                            f"Phiên học '{s['title']}' ({day_label} {st}-{et}) kết thúc sau khung giờ học muộn nhất đã thiết lập ({latest_study_time})."
                        )

            # Night protection
            if s_min < 6 * 60 or e_min > 23 * 60:
                violations.append(
                    f"Phiên học '{s['title']}' ({day_label} {st}-{et}) nằm trong khung giờ đêm không được phép."
                )

            # Check inside study_window if required (for AI-generated sessions)
            if require_inside_study_window:
                day_windows = [
                    (time_to_minutes(r["start_time"]), time_to_minutes(r["end_time"]))
                    for r in routines
                    if int(r["weekday"]) == w and (r.get("flexibility") == "study_window" or r.get("activity_type") == "study_window")
                ]
                if day_windows:
                    merged_win = merge_intervals(day_windows)
                    inside = any(ws <= s_min and e_min <= we for ws, we in merged_win)
                    if not inside:
                        violations.append(
                            f"Phiên học '{s['title']}' ({day_label} {st}-{et}) nằm ngoài khung giờ có thể học đã khai báo."
                        )
                else:
                    violations.append(
                        f"Phiên học '{s['title']}' ({day_label} {st}-{et}) nằm ngoài khung giờ có thể học đã khai báo."
                    )

            # Check task existence, deadline & completion status
            tid = s.get("task_id")
            if tid is not None and str(tid).strip() != "":
                try:
                    tid_int = int(tid)
                except (ValueError, TypeError):
                    tid_int = None
                if tid_int not in tasks_by_id:
                    violations.append(
                        f"Bài tập liên kết (ID {tid}) của phiên học '{s['title']}' không còn tồn tại hoặc đã bị xóa."
                    )
                else:
                    t = tasks_by_id[tid_int]
                    if t.get("completed"):
                        violations.append(
                            f"Bài tập '{t.get('title')}' liên kết với phiên học '{s['title']}' đã được đánh dấu hoàn thành."
                        )
                    else:
                        cutoff_w, cutoff_min = _compute_task_week_cutoff(t, week_monday)
                        if (w, e_min) > (cutoff_w, cutoff_min):
                            violations.append(
                                f"Phiên học '{s['title']}' ({day_label} {st}-{et}) kết thúc sau hạn nộp "
                                f"({t.get('deadline_display') or t.get('deadline_time')})."
                            )

    # 2. Check session-to-session overlaps
    for i in range(len(sessions)):
        for j in range(i + 1, len(sessions)):
            s1, s2 = sessions[i], sessions[j]
            if int(s1["weekday"]) == int(s2["weekday"]):
                if s1["start_time"] < s2["end_time"] and s2["start_time"] < s1["end_time"]:
                    day_label = WEEKDAY_NAMES.get(int(s1["weekday"]), f"Thứ {s1['weekday']}")
                    violations.append(
                        f"Hai phiên tự học '{s1['title']}' ({s1['start_time']}-{s1['end_time']}) và "
                        f"'{s2['title']}' ({s2['start_time']}-{s2['end_time']}) bị trùng giờ vào {day_label}."
                    )

    # 3. Check daily max study minutes
    if max_daily_minutes is not None:
        by_day: Dict[int, int] = {}
        for s in sessions:
            w = int(s["weekday"])
            dur = time_to_minutes(s["end_time"]) - time_to_minutes(s["start_time"])
            by_day[w] = by_day.get(w, 0) + dur
        for w, total_m in by_day.items():
            if total_m > max_daily_minutes:
                day_label = WEEKDAY_NAMES.get(w, f"Thứ {w}")
                violations.append(
                    f"Tổng thời gian tự học vào {day_label} ({total_m} phút) vượt quá giới hạn tối đa "
                    f"mỗi ngày ({max_daily_minutes} phút)."
                )

    return violations


def _infer_week_monday(prefs: Dict[str, Any], tasks: List[Dict[str, Any]]) -> Optional[date]:
    """Determine the Monday date of the planning week if dates are used."""
    if prefs.get("week_start_date"):
        try:
            d = datetime.strptime(prefs["week_start_date"], "%Y-%m-%d").date()
            return d - timedelta(days=d.isoweekday() - 1)
        except ValueError:
            pass
    dated_tasks = [t["deadline_date"] for t in tasks if not t.get("completed") and t.get("deadline_date")]
    if dated_tasks:
        earliest_str = min(dated_tasks)
        try:
            d = datetime.strptime(earliest_str, "%Y-%m-%d").date()
            return d - timedelta(days=d.isoweekday() - 1)
        except ValueError:
            pass
    return None


def _compute_task_week_cutoff(task: Dict[str, Any], week_monday: Optional[date]) -> Tuple[int, int]:
    """Return (cutoff_weekday 0..8, cutoff_minutes 0..1440) for a task in the planning week."""
    t_min = time_to_minutes(task.get("deadline_time") or "23:59")
    d_date_str = task.get("deadline_date")
    if d_date_str and week_monday is not None:
        try:
            d_date = datetime.strptime(d_date_str, "%Y-%m-%d").date()
            week_sunday = week_monday + timedelta(days=6)
            if d_date < week_monday:
                return (0, 0)
            if d_date > week_sunday:
                return (7, 24 * 60)
            return (d_date.isoweekday(), t_min)
        except ValueError:
            pass
    return (int(task["deadline_weekday"]), t_min)


def _slot_preference_bonus(start_min: int, end_min: int, preferred_periods: List[str]) -> int:
    """Score bonus for matching time-of-day preferences from AI notes."""
    if not preferred_periods:
        return 0
    mid = (start_min + end_min) // 2
    bonus = 0
    if "morning" in preferred_periods and mid < 12 * 60:
        bonus += 40
    if "afternoon" in preferred_periods and 12 * 60 <= mid < 18 * 60:
        bonus += 40
    if "evening" in preferred_periods and mid >= 18 * 60:
        bonus += 40
    return bonus


def generate_study_plan(
    reschedule_only: bool = False,
    ai_notes_override: Optional[str] = None,
    include_courses: bool = False,
    course_items: Optional[List[Dict[str, Any]]] = None,
    session_duration: Optional[int] = None,
    db_path: Optional[Path | str] = None,
    learning_service: Any = None,
    document_lookup: Any = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Generate a draft personal study schedule combining classes, routines, study windows, deadlines, and courses."""
    confirmed_classes = timetable_store.get_confirmed_entries(db_path=db_path)
    routines = get_routines(db_path=db_path)
    prefs = get_preferences(db_path=db_path)
    if ai_notes_override is not None:
        prefs = update_preferences({**prefs, "ai_notes": ai_notes_override}, db_path=db_path)

    parsed_ai = parse_ai_preference_notes(prefs.get("ai_notes", ""), prefs)
    earliest_min = parsed_ai["earliest_min"]
    latest_min = parsed_ai["latest_min"]
    max_daily_min = parsed_ai["max_daily_min"]
    pref_session_min = parsed_ai["pref_session_min"]
    min_session_min = min(pref_session_min, int(prefs.get("min_session_minutes", 30)))
    buffer_hours = int(prefs.get("buffer_before_deadline_hours", 12))
    buffer_minutes = buffer_hours * 60

    all_tasks = get_tasks(db_path=db_path)
    tasks_by_id = {t["id"]: t for t in all_tasks}
    week_monday = _infer_week_monday(prefs, all_tasks)
    ceil_now = _ceil_vn_now(now) if week_monday is not None else None
    now_date = ceil_now.date() if ceil_now is not None else None
    now_min_of_day = (ceil_now.hour * 60 + ceil_now.minute) if ceil_now is not None else 0
    week_sunday = (week_monday + timedelta(days=6)) if week_monday is not None else None
    is_historical_week = bool(week_sunday is not None and now_date is not None and week_sunday < now_date)

    existing_sessions = get_study_sessions(db_path=db_path)
    # Keep completed sessions, user-locked sessions, and manual sessions intact!
    preserved_sessions: List[Dict[str, Any]] = [
        dict(s) for s in existing_sessions
        if s.get("completed") or s.get("locked") or s.get("source") in ("manual", "user_manual")
    ]

    warnings: List[str] = []
    suggestions: List[Dict[str, Any]] = []
    if is_historical_week and week_monday is not None and week_sunday is not None and ceil_now is not None:
        warnings.append(
            f"Tuần lập lịch đã chọn ({week_monday.isoformat()} đến {week_sunday.isoformat()}) "
            f"đã qua so với thời điểm hiện tại ({ceil_now.strftime('%Y-%m-%d %H:%M')}); "
            "không thể xếp các phiên tự học mới vào quá khứ."
        )

    # Check tasks with missing estimated_minutes
    active_tasks = [t for t in all_tasks if not t.get("completed")]
    missing_estimate_tasks: List[Dict[str, Any]] = []
    schedulable_tasks: List[Dict[str, Any]] = []

    for t in active_tasks:
        if t.get("missing_estimate"):
            missing_estimate_tasks.append({
                "task_id": t["id"],
                "title": t["title"],
                "course": t["course"],
                "deadline_display": t["deadline_display"],
                "priority": t["priority"],
            })
            warnings.append(
                f"Bài tập '{t['title']}' ({t['course']}) chưa có thời gian ước tính hoàn thành. "
                f"Vui lòng nhập thời lượng dự kiến (phút) trước khi xếp lịch cho công việc này."
            )
        else:
            schedulable_tasks.append(t)

    free_slots_by_day: Dict[int, List[Tuple[int, int]]] = {}
    blocked_by_day: Dict[int, List[Tuple[int, int]]] = {}
    past_cutoff_by_day: Dict[int, int] = {w: 0 for w in range(1, 8)}
    daily_used_minutes: Dict[int, int] = {w: 0 for w in range(1, 8)}

    for s in preserved_sessions:
        w = int(s["weekday"])
        dur = time_to_minutes(s["end_time"]) - time_to_minutes(s["start_time"])
        daily_used_minutes[w] = daily_used_minutes.get(w, 0) + dur

    total_study_windows = [
        r for r in routines
        if (r.get("flexibility") == "study_window" or r.get("activity_type") == "study_window")
        and time_to_minutes(r["end_time"]) > time_to_minutes(r["start_time"])
    ]
    has_any_study_windows = len(total_study_windows) > 0

    for w in range(1, 8):
        if week_monday is not None and now_date is not None:
            day_date = week_monday + timedelta(days=w - 1)
            if day_date < now_date:
                past_cutoff_by_day[w] = 1440
            elif day_date == now_date:
                past_cutoff_by_day[w] = now_min_of_day

        blocked: List[Tuple[int, int]] = []
        for c in confirmed_classes:
            if int(c["weekday"]) == w:
                blocked.append((time_to_minutes(c["start_time"]), time_to_minutes(c["end_time"])))
        for r in routines:
            if r.get("flexibility") in ("fixed", "protected", "flexible") or r.get("activity_type") in ("sleep", "rest", "work", "class", "commute", "sport", "meal"):
                r_w = int(r["weekday"])
                r_s = time_to_minutes(r["start_time"])
                r_e = time_to_minutes(r["end_time"])
                if r_s < r_e:
                    if r_w == w:
                        blocked.append((r_s, r_e))
                else:
                    if r_w == w:
                        blocked.append((r_s, 1440))
                    next_w = (r_w % 7) + 1
                    if next_w == w:
                        blocked.append((0, r_e))
        for s in preserved_sessions:
            if int(s["weekday"]) == w:
                blocked.append((time_to_minutes(s["start_time"]), time_to_minutes(s["end_time"])))
        blocked_by_day[w] = merge_intervals(blocked)

        day_cutoff = past_cutoff_by_day[w]
        if day_cutoff >= 1440:
            free_slots_by_day[w] = []
            continue

        day_windows = [
            (max(earliest_min, time_to_minutes(r["start_time"])), min(latest_min, time_to_minutes(r["end_time"])))
            for r in total_study_windows
            if int(r["weekday"]) == w and min(latest_min, time_to_minutes(r["end_time"])) > max(earliest_min, time_to_minutes(r["start_time"]))
        ]
        day_windows = [(s, e) for s, e in day_windows if e > s]
        if day_windows:
            raw_windows = merge_intervals(day_windows)
        elif not has_any_study_windows:
            raw_windows = [(earliest_min, latest_min)]
        else:
            raw_windows = []

        effective_blocked = blocked_by_day[w] + ([(0, day_cutoff)] if day_cutoff > 0 else [])
        free_slots_by_day[w] = subtract_intervals(raw_windows, effective_blocked)

    proposed_sessions: List[Dict[str, Any]] = []
    unscheduled_tasks: List[Dict[str, Any]] = []
    unplaced_courses: List[str] = []
    days_without_free_slot: List[str] = []

    # Optional course/weekly-lesson scheduling (unified core)
    if include_courses or course_items is not None:
        ordered_courses: List[str] = []
        lessons_by_course: Dict[str, List[Dict[str, Any]]] = {}

        if course_items is not None:
            for item in course_items:
                c = (item.get("course") or "").strip()
                if c and c not in ordered_courses:
                    ordered_courses.append(c)
                lessons_by_course.setdefault(c, []).append(item)
        else:
            with timetable_store.get_connection(db_path=db_path) as conn:
                lesson_rows = conn.execute(
                    "SELECT * FROM weekly_lessons ORDER BY id ASC"
                ).fetchall()
                for r in lesson_rows:
                    item = dict(r)
                    c = (item.get("course") or "").strip()
                    if c and c not in ordered_courses:
                        ordered_courses.append(c)
                    lessons_by_course.setdefault(c, []).append(item)

            for cls in confirmed_classes:
                c = (cls.get("course") or "").strip()
                if c and c not in ordered_courses:
                    ordered_courses.append(c)

            for tsk in all_tasks:
                c = (tsk.get("course") or "").strip()
                if c and c not in ordered_courses:
                    ordered_courses.append(c)

        if not ordered_courses:
            raise ValueError(
                "Chưa có môn học nào trong Thời khóa biểu hoặc danh sách Bài học trong tuần. "
                "Hãy lưu ít nhất 1 môn học hoặc nhập bài học tuần trước khi tự động phân bổ."
            )

        duration = int(session_duration or pref_session_min or 60)
        if duration < 15 or duration > 240:
            duration = 60

        preferred_anchors = (8 * 60 + 30, 14 * 60, 19 * 60 + 30, earliest_min)
        assigned_days: set[int] = set()

        retained_courses_by_day: Dict[int, set[str]] = {}
        for s in preserved_sessions:
            w = int(s["weekday"])
            c_name = (s.get("course") or "").strip()
            if c_name:
                retained_courses_by_day.setdefault(w, set()).add(c_name)

        for w in range(1, 8):
            slots = [
                (s, e) for s, e in free_slots_by_day.get(w, [])
                if (e - s) >= duration and (max_daily_min - daily_used_minutes.get(w, 0)) >= duration
            ]
            retained_c = retained_courses_by_day.get(w, set())
            if not slots or len(retained_c) > 1:
                days_without_free_slot.append(WEEKDAY_NAMES.get(w, f"Thứ {w}"))

        lesson_cursor: Dict[str, int] = {c: 0 for c in ordered_courses}

        def _get_course_meta(course_name: str) -> Tuple[str, str, Optional[str], Optional[str], Optional[int]]:
            c_lessons = lessons_by_course.get(course_name) or []
            if c_lessons:
                idx = lesson_cursor.get(course_name, 0) % len(c_lessons)
                les = c_lessons[idx]
                lesson_cursor[course_name] = idx + 1
                l_title = les.get("lesson_title") or f"Ôn tập {course_name}"
                c_topic = les.get("classified_topic") or course_name
                ws_id = (les.get("worksheet_id") or "").strip() or None
                p_id = (les.get("practice_id") or "").strip() or None
                l_id = les.get("id")
            else:
                from app.topic_tree import resolve_topic_hierarchy
                resolved = resolve_topic_hierarchy(course_name)
                c_topic = str(resolved.get("topic") if resolved else course_name)
                l_title = f"Ôn tập {course_name}"
                ws_id = None
                p_id = None
                l_id = None

            return l_title, c_topic, ws_id, p_id, l_id

        # Step 1: Place courses on days that already have that course retained
        for course in ordered_courses:
            for w in range(1, 8):
                if w in assigned_days:
                    continue
                retained_c = retained_courses_by_day.get(w, set())
                if course in retained_c and len(retained_c) == 1:
                    slots = [
                        (s, e) for s, e in free_slots_by_day.get(w, [])
                        if (e - s) >= duration and (max_daily_min - daily_used_minutes.get(w, 0)) >= duration
                    ]
                    if not slots:
                        continue
                    assigned_days.add(w)
                    chosen_start = None
                    for anchor in preferred_anchors:
                        for s, e in slots:
                            if s <= anchor and anchor + duration <= e:
                                chosen_start = anchor
                                break
                        if chosen_start is not None:
                            break
                    if chosen_start is None:
                        chosen_start = slots[0][0]
                    chosen_end = chosen_start + duration

                    l_title, c_topic, ws_id, p_id, l_id = _get_course_meta(course)
                    session_title = f"Tự học {course}: {l_title}"
                    proposed_sessions.append({
                        "id": None,
                        "task_id": None,
                        "weekly_lesson_id": l_id,
                        "title": session_title,
                        "course": course,
                        "weekday": w,
                        "start_time": minutes_to_time(chosen_start),
                        "end_time": minutes_to_time(chosen_end),
                        "duration_minutes": duration,
                        "priority": "high",
                        "source": "ai_auto_90m",
                        "completed": False,
                        "locked": False,
                        "session_index": 1,
                        "total_sessions": 1,
                        "classified_topic": c_topic,
                        "worksheet_id": ws_id,
                        "practice_id": p_id,
                        "exercise_status": "available" if (p_id or ws_id) else "not_generated",
                        "notes": f"Đề xuất tự học {course} | Khung giờ rảnh {minutes_to_time(chosen_start)}-{minutes_to_time(chosen_end)} | Chủ đề: {c_topic}" + (f" | Bài tập đính kèm: /api/timetable/worksheets/{ws_id}.pdf" if ws_id else (f" | Bài tập đính kèm: /api/learning/export-pdf/{p_id}" if p_id else " (Chưa có bài tập PDF đính kèm)")),
                    })
                    daily_used_minutes[w] = daily_used_minutes.get(w, 0) + duration
                    for s_idx, (s, e) in enumerate(free_slots_by_day[w]):
                        if s <= chosen_start and chosen_end <= e:
                            del free_slots_by_day[w][s_idx]
                            if chosen_end < e:
                                free_slots_by_day[w].insert(s_idx, (chosen_end, e))
                            if s < chosen_start:
                                free_slots_by_day[w].insert(s_idx, (s, chosen_start))
                            break
                    break

        # Step 2: Place remaining courses on open days (days with NO retained courses)
        courses_to_place = [c for c in ordered_courses if not any(s["course"] == c for s in proposed_sessions)]
        for course in courses_to_place:
            placed = False
            for w in range(1, 8):
                if w in assigned_days:
                    continue
                retained_c = retained_courses_by_day.get(w, set())
                if retained_c:
                    continue
                slots = [
                    (s, e) for s, e in free_slots_by_day.get(w, [])
                    if (e - s) >= duration and (max_daily_min - daily_used_minutes.get(w, 0)) >= duration
                ]
                if not slots:
                    continue
                assigned_days.add(w)
                chosen_start = None
                for anchor in preferred_anchors:
                    for s, e in slots:
                        if s <= anchor and anchor + duration <= e:
                            chosen_start = anchor
                            break
                    if chosen_start is not None:
                        break
                if chosen_start is None:
                    chosen_start = slots[0][0]
                chosen_end = chosen_start + duration

                l_title, c_topic, ws_id, p_id, l_id = _get_course_meta(course)
                session_title = f"Tự học {course}: {l_title}"
                proposed_sessions.append({
                    "id": None,
                    "task_id": None,
                    "weekly_lesson_id": l_id,
                    "title": session_title,
                    "course": course,
                    "weekday": w,
                    "start_time": minutes_to_time(chosen_start),
                    "end_time": minutes_to_time(chosen_end),
                    "duration_minutes": duration,
                    "priority": "high",
                    "source": "ai_auto_90m",
                    "completed": False,
                    "locked": False,
                    "session_index": 1,
                    "total_sessions": 1,
                    "classified_topic": c_topic,
                    "worksheet_id": ws_id,
                    "practice_id": p_id,
                    "exercise_status": "available" if (p_id or ws_id) else "not_generated",
                    "notes": f"Đề xuất tự học {course} | Khung giờ rảnh {minutes_to_time(chosen_start)}-{minutes_to_time(chosen_end)} | Chủ đề: {c_topic}" + (f" | Bài tập đính kèm: /api/timetable/worksheets/{ws_id}.pdf" if ws_id else (f" | Bài tập đính kèm: /api/learning/export-pdf/{p_id}" if p_id else " (Chưa có bài tập PDF đính kèm)")),
                })
                daily_used_minutes[w] = daily_used_minutes.get(w, 0) + duration
                for s_idx, (s, e) in enumerate(free_slots_by_day[w]):
                    if s <= chosen_start and chosen_end <= e:
                        del free_slots_by_day[w][s_idx]
                        if chosen_end < e:
                            free_slots_by_day[w].insert(s_idx, (chosen_end, e))
                        if s < chosen_start:
                            free_slots_by_day[w].insert(s_idx, (s, chosen_start))
                        break
                placed = True
                break

            if not placed:
                unplaced_courses.append(course)

        # Step 3: If days still remain open and all courses placed, rotate through remaining open days
        if not unplaced_courses:
            remaining_days = [
                d for d in range(1, 8)
                if d not in assigned_days and not retained_courses_by_day.get(d)
            ]
            for idx_d, w in enumerate(remaining_days):
                slots = [
                    (s, e) for s, e in free_slots_by_day.get(w, [])
                    if (e - s) >= duration and (max_daily_min - daily_used_minutes.get(w, 0)) >= duration
                ]
                if not slots:
                    continue
                assigned_days.add(w)
                course = ordered_courses[idx_d % len(ordered_courses)]
                chosen_start = None
                for anchor in preferred_anchors:
                    for s, e in slots:
                        if s <= anchor and anchor + duration <= e:
                            chosen_start = anchor
                            break
                    if chosen_start is not None:
                        break
                if chosen_start is None:
                    chosen_start = slots[0][0]
                chosen_end = chosen_start + duration

                l_title, c_topic, ws_id, p_id, l_id = _get_course_meta(course)
                session_title = f"Tự học {course}: {l_title}"
                proposed_sessions.append({
                    "id": None,
                    "task_id": None,
                    "weekly_lesson_id": l_id,
                    "title": session_title,
                    "course": course,
                    "weekday": w,
                    "start_time": minutes_to_time(chosen_start),
                    "end_time": minutes_to_time(chosen_end),
                    "duration_minutes": duration,
                    "priority": "high",
                    "source": "ai_auto_90m",
                    "completed": False,
                    "locked": False,
                    "session_index": 1,
                    "total_sessions": 1,
                    "classified_topic": c_topic,
                    "worksheet_id": ws_id,
                    "practice_id": p_id,
                    "exercise_status": "available" if (p_id or ws_id) else "not_generated",
                    "notes": f"Đề xuất tự học {course} | Khung giờ rảnh {minutes_to_time(chosen_start)}-{minutes_to_time(chosen_end)} | Chủ đề: {c_topic}" + (f" | Bài tập đính kèm: /api/timetable/worksheets/{ws_id}.pdf" if ws_id else (f" | Bài tập đính kèm: /api/learning/export-pdf/{p_id}" if p_id else " (Chưa có bài tập PDF đính kèm)")),
                })
                daily_used_minutes[w] = daily_used_minutes.get(w, 0) + duration
                for s_idx, (s, e) in enumerate(free_slots_by_day[w]):
                    if s <= chosen_start and chosen_end <= e:
                        del free_slots_by_day[w][s_idx]
                        if chosen_end < e:
                            free_slots_by_day[w].insert(s_idx, (chosen_end, e))
                        if s < chosen_start:
                            free_slots_by_day[w].insert(s_idx, (s, chosen_start))
                        break

    # Sort schedulable tasks by deadline urgency + priority
    def _task_sort_key(t: Dict[str, Any]) -> Tuple[Any, ...]:
        cw, cm = _compute_task_week_cutoff(t, week_monday)
        week_min = cw * 1440 + cm
        p_rank = PRIORITY_RANK.get(t["priority"], 1)
        return (week_min, p_rank, t["id"])

    schedulable_tasks.sort(key=_task_sort_key)

    # Calculate already preserved minutes per task
    preserved_by_task: Dict[int, int] = {}
    for s in preserved_sessions:
        tid = s.get("task_id")
        if tid:
            dur = time_to_minutes(s["end_time"]) - time_to_minutes(s["start_time"])
            preserved_by_task[tid] = preserved_by_task.get(tid, 0) + dur

    for task in schedulable_tasks:
        tid = task["id"]
        est_min = int(task["estimated_minutes"] or 0)
        already_min = preserved_by_task.get(tid, 0)
        needed_min = max(0, est_min - already_min)
        if needed_min == 0:
            continue

        cutoff_w, cutoff_min = _compute_task_week_cutoff(task, week_monday)
        cutoff_week_min = (cutoff_w - 1) * 1440 + cutoff_min if cutoff_w >= 1 else -1
        buffer_week_min = max(0, cutoff_week_min - buffer_minutes)
        splittable = bool(task.get("splittable", True))

        # Two passes: Pass 1 respects buffer before deadline; Pass 2 uses up to exact deadline
        for pass_idx, pass_limit_week_min in enumerate((buffer_week_min, cutoff_week_min)):
            if needed_min <= 0 or cutoff_week_min <= 0:
                break

            # Keep placing sessions in the best available slot until needed_min == 0 or no valid slot remains
            safety_counter = 0
            while needed_min > 0 and safety_counter < 100:
                safety_counter += 1
                best_candidate: Optional[Tuple[float, int, int, int, int, int, int, int]] = None
                # Candidate tuple: (score, -w, -alloc_s, w, s_idx, alloc_s, alloc_end, alloc_dur)

                for w in range(1, 8):
                    if w > cutoff_w:
                        continue
                    day_rem_limit = max_daily_min - daily_used_minutes[w]
                    if day_rem_limit <= 0:
                        continue

                    slots = free_slots_by_day.get(w, [])
                    for s_idx, (slot_s, slot_e) in enumerate(slots):
                        day_offset = (w - 1) * 1440
                        max_end_in_pass = pass_limit_week_min - day_offset
                        if w == cutoff_w:
                            max_end_in_pass = min(max_end_in_pass, cutoff_min)
                        eff_end = min(slot_e, max_end_in_pass)
                        avail = eff_end - slot_s
                        if avail <= 0:
                            continue

                        max_placeable = min(avail, day_rem_limit)
                        if not splittable:
                            if max_placeable < needed_min:
                                continue
                            alloc_dur = needed_min
                        else:
                            effective_min_chunk = min(min_session_min, needed_min)
                            if max_placeable < effective_min_chunk:
                                if pass_idx == 0 or max_placeable < 15:
                                    continue
                            target_chunk = min(needed_min, pref_session_min)
                            if needed_min > target_chunk and (needed_min - target_chunk) < min_session_min:
                                if max_placeable >= needed_min and needed_min <= pref_session_min + min_session_min:
                                    target_chunk = needed_min
                                else:
                                    target_chunk = max(min_session_min, (needed_min + 1) // 2)
                            alloc_dur = min(max_placeable, target_chunk)

                        if alloc_dur <= 0:
                            continue

                        alloc_s = slot_s
                        alloc_e = slot_s + alloc_dur

                        avoid_penalty = -200 if w in parsed_ai["avoided_weekdays"] else 0
                        load_score = (max_daily_min - daily_used_minutes[w]) * 0.4
                        earliness_score = (8 - w) * 15
                        tod_bonus = _slot_preference_bonus(alloc_s, alloc_e, parsed_ai["preferred_periods"])
                        same_task_today = sum(
                            1 for ps in proposed_sessions if ps.get("task_id") == tid and ps["weekday"] == w
                        )
                        spread_penalty = -35 * same_task_today

                        total_score = avoid_penalty + load_score + earliness_score + tod_bonus + spread_penalty
                        cand = (total_score, -w, -alloc_s, w, s_idx, alloc_s, alloc_e, alloc_dur)
                        if best_candidate is None or cand > best_candidate:
                            best_candidate = cand

                if best_candidate is None:
                    break

                _, _, _, chosen_w, chosen_s_idx, alloc_s, alloc_e, alloc_dur = best_candidate
                proposed_sessions.append({
                    "id": None,
                    "task_id": tid,
                    "title": f"Tự học: {task['title']}",
                    "course": task["course"],
                    "weekday": chosen_w,
                    "start_time": minutes_to_time(alloc_s),
                    "end_time": minutes_to_time(alloc_e),
                    "duration_minutes": alloc_dur,
                    "priority": task["priority"],
                    "source": "ai_assistant",
                    "completed": False,
                    "locked": False,
                    "session_index": 1,
                    "total_sessions": 1,
                    "classified_topic": task.get("classified_topic") or task["course"],
                    "worksheet_id": None,
                    "practice_id": None,
                    "exercise_status": "not_generated",
                    "notes": f"Hạn nộp: {task['deadline_display']}",
                })

                needed_min -= alloc_dur
                daily_used_minutes[chosen_w] += alloc_dur
                old_s, old_e = free_slots_by_day[chosen_w][chosen_s_idx]
                del free_slots_by_day[chosen_w][chosen_s_idx]
                if alloc_e < old_e:
                    free_slots_by_day[chosen_w].insert(chosen_s_idx, (alloc_e, old_e))

        if needed_min > 0:
            scheduled_for_task = est_min - needed_min
            reason, task_suggestions = _diagnose_unscheduled_task(
                task=task,
                missing_minutes=needed_min,
                cutoff_w=cutoff_w,
                cutoff_min=cutoff_min,
                free_slots_by_day=free_slots_by_day,
                blocked_by_day=blocked_by_day,
                daily_used_minutes=daily_used_minutes,
                max_daily_min=max_daily_min,
                earliest_min=earliest_min,
                latest_min=latest_min,
                routines=routines,
                week_monday=week_monday,
                ceil_now=ceil_now,
                past_cutoff_by_day=past_cutoff_by_day,
            )
            unscheduled_tasks.append({
                "task_id": tid,
                "title": task["title"],
                "course": task["course"],
                "priority": task["priority"],
                "priority_label": task["priority_label"],
                "deadline_weekday": task["deadline_weekday"],
                "deadline_time": task["deadline_time"],
                "deadline_date": task.get("deadline_date"),
                "deadline_display": task["deadline_display"],
                "estimated_minutes": est_min,
                "scheduled_minutes": scheduled_for_task,
                "missing_minutes": needed_min,
                "reason": reason,
            })
            warnings.append(
                f"Không đủ chỗ xếp lịch cho '{task['title']}' ({task['course']}, hạn {task['deadline_display']}): "
                f"đã xếp {scheduled_for_task}/{est_min} phút, còn thiếu {needed_min} phút. Lý do: {reason}"
            )
            for sug in task_suggestions:
                if not any(existing_s.get("key") == sug.get("key") for existing_s in suggestions):
                    suggestions.append(sug)

    # Number sessions per task chronologically
    all_combined_sessions = preserved_sessions + proposed_sessions
    by_task_list: Dict[int, List[Dict[str, Any]]] = {}
    for s in all_combined_sessions:
        tid = s.get("task_id")
        if tid:
            by_task_list.setdefault(tid, []).append(s)

    for tid, s_list in by_task_list.items():
        s_list.sort(key=lambda x: (int(x["weekday"]), x["start_time"]))
        total_cnt = len(s_list)
        t_obj = tasks_by_id.get(tid)
        base_title = t_obj["title"] if t_obj else s_list[0]["title"].replace("Tự học: ", "")
        for idx, s in enumerate(s_list, start=1):
            s["session_index"] = idx
            s["total_sessions"] = total_cnt
            if not s.get("locked") and not s.get("completed"):
                if total_cnt > 1:
                    s["title"] = f"Tự học: {base_title} (Phiên {idx}/{total_cnt})"
                else:
                    s["title"] = f"Tự học: {base_title}"

    if unplaced_courses:
        warnings.append(
            f"Không đủ khung giờ tự học hợp lệ cho các môn sau: {', '.join(unplaced_courses)}."
        )

    for p in preserved_sessions:
        p["_is_preserved"] = True
    for p in proposed_sessions:
        p["_is_preserved"] = False

    proposed_sessions.sort(key=lambda x: (int(x["weekday"]), x["start_time"]))

    # Hard constraint verification on the generated plan
    violations = verify_schedule_constraints(
        sessions=preserved_sessions + proposed_sessions,
        confirmed_classes=confirmed_classes,
        routines=routines,
        tasks_by_id=tasks_by_id,
        require_inside_study_window=has_any_study_windows,
        max_daily_minutes=max_daily_min,
        week_monday=week_monday,
        now=now,
    )
    if violations:
        warnings.extend(violations)

    explanation = _build_ai_explanation(
        confirmed_classes=confirmed_classes,
        routines=routines,
        preserved_sessions=preserved_sessions,
        proposed_sessions=proposed_sessions,
        unscheduled_tasks=unscheduled_tasks,
        missing_estimate_tasks=missing_estimate_tasks,
        parsed_ai=parsed_ai,
        prefs=prefs,
        reschedule_only=reschedule_only,
        unplaced_courses=unplaced_courses,
        is_historical_week=is_historical_week,
        week_monday=week_monday,
        ceil_now=ceil_now,
    )

    plan_id = uuid.uuid4().hex
    created_at_iso = datetime.now(timezone.utc).isoformat()
    unscheduled_payload = {
        "tasks": unscheduled_tasks,
        "courses": unplaced_courses,
    }

    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute("UPDATE study_plan_drafts SET status = 'discarded' WHERE status = 'draft'")
        conn.execute(
            """
            INSERT INTO study_plan_drafts
            (plan_id, created_at, status, sessions_json, unscheduled_json, warnings_json,
             suggestions_json, explanation, preserved_sessions_json)
            VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                created_at_iso,
                json.dumps(proposed_sessions, ensure_ascii=False),
                json.dumps(unscheduled_payload, ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(suggestions, ensure_ascii=False),
                explanation,
                json.dumps(preserved_sessions, ensure_ascii=False),
            ),
        )

    draft = get_plan_draft(plan_id, db_path=db_path) or {}
    draft["missing_estimate_tasks"] = missing_estimate_tasks
    draft["constraint_violations"] = violations
    draft["unplaced_courses"] = unplaced_courses
    draft["days_without_free_slot"] = days_without_free_slot
    return draft


def _diagnose_unscheduled_task(
    task: Dict[str, Any],
    missing_minutes: int,
    cutoff_w: int,
    cutoff_min: int,
    free_slots_by_day: Dict[int, List[Tuple[int, int]]],
    blocked_by_day: Dict[int, List[Tuple[int, int]]],
    daily_used_minutes: Dict[int, int],
    max_daily_min: int,
    earliest_min: int,
    latest_min: int,
    routines: List[Dict[str, Any]],
    week_monday: Optional[date] = None,
    ceil_now: Optional[datetime] = None,
    past_cutoff_by_day: Optional[Dict[int, int]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Identify why a task could not be fully scheduled and propose actionable user options."""
    suggestions: List[Dict[str, Any]] = []
    reasons: List[str] = []
    cutoffs = past_cutoff_by_day or {}

    local_ceil: Optional[datetime] = None
    if ceil_now is not None:
        local_ceil = ceil_now.replace(tzinfo=VN_TZ) if ceil_now.tzinfo is None else ceil_now.astimezone(VN_TZ)

    if week_monday is not None and local_ceil is not None:
        week_sunday = week_monday + timedelta(days=6)
        if week_sunday < local_ceil.date():
            return (
                f"Tuần lập lịch đã chọn ({week_monday.isoformat()} đến {week_sunday.isoformat()}) "
                f"đã qua so với thời điểm hiện tại ({local_ceil.strftime('%Y-%m-%d %H:%M')}); "
                "không còn khung giờ hợp lệ trong tuần này.",
                [],
            )

    if cutoff_w <= 0:
        return (
            "Hạn nộp của bài tập nằm trước tuần lập lịch hiện tại.",
            [{
                "key": f"extend_deadline_{task['id']}",
                "type": "update_deadline",
                "title": f"Cập nhật lại hạn nộp cho '{task['title']}'",
                "description": "Hạn nộp đã qua so với tuần hiện tại; hãy cập nhật ngày hạn nộp mới hoặc đánh dấu hoàn thành.",
                "task_id": task["id"],
            }],
        )

    if week_monday is not None and local_ceil is not None:
        week_start_dt = datetime.combine(week_monday, time.min, tzinfo=VN_TZ)
        deadline_dt = week_start_dt + timedelta(days=cutoff_w - 1, minutes=cutoff_min)
        if deadline_dt <= local_ceil:
            return (
                f"Thời điểm hiện tại ({local_ceil.strftime('%Y-%m-%d %H:%M')}) đã qua hạn nộp "
                f"của bài tập ({task['deadline_display']}); không thể xếp phiên học mới vào quá khứ.",
                [{
                    "key": f"extend_deadline_{task['id']}",
                    "type": "update_deadline",
                    "title": f"Cập nhật lại hạn nộp cho '{task['title']}'",
                    "description": "Hạn nộp đã qua so với thời điểm hiện tại; hãy cập nhật ngày hạn nộp mới hoặc đánh dấu hoàn thành.",
                    "task_id": task["id"],
                }],
            )

    # 1. Check if study windows before deadline still had time but hit max_daily_study_minutes
    unused_window_due_to_cap = 0
    capped_days: List[str] = []
    for w in range(1, min(7, cutoff_w) + 1):
        rem_win = 0
        for s, e in free_slots_by_day.get(w, []):
            eff_e = min(e, cutoff_min) if w == cutoff_w else e
            if eff_e > s:
                rem_win += eff_e - s
        if rem_win > 0 and daily_used_minutes.get(w, 0) >= max_daily_min:
            unused_window_due_to_cap += rem_win
            capped_days.append(WEEKDAY_NAMES[w])

    if unused_window_due_to_cap > 0:
        reasons.append(
            f"chạm giới hạn học tối đa mỗi ngày ({max_daily_min} phút/ngày) vào {', '.join(capped_days)} "
            f"dù khung giờ có thể học vẫn còn trống {unused_window_due_to_cap} phút"
        )
        needed_increase = min(180, ((missing_minutes + 29) // 30) * 30)
        new_max = max_daily_min + needed_increase
        suggestions.append({
            "key": f"increase_daily_max_{new_max}",
            "type": "increase_daily_limit",
            "title": f"Tăng giới hạn học tối đa/ngày lên {new_max} phút ({new_max / 60:.1f} giờ)",
            "description": (
                f"Khung giờ có thể học vào {', '.join(capped_days)} vẫn còn {unused_window_due_to_cap} phút trống. "
                f"Tăng giới hạn ngày thêm {needed_increase} phút sẽ giúp xếp thêm các phiên còn thiếu."
            ),
            "new_max_daily_minutes": new_max,
        })

    # 2. Check if there are future flexible routines ("Có thể đổi giờ") before the deadline
    flexible_candidates = []
    for r in routines:
        if r["flexibility"] == "flexible" and r["weekday"] <= cutoff_w:
            day_cutoff = cutoffs.get(int(r["weekday"]), 0)
            r_end = time_to_minutes(r["end_time"])
            r_start = max(time_to_minutes(r["start_time"]), day_cutoff)
            eff_end = min(r_end, cutoff_min) if r["weekday"] == cutoff_w else r_end
            dur = eff_end - r_start
            if dur >= 30:
                flexible_candidates.append((r, dur))

    if flexible_candidates:
        r_names = [f"'{r['title']}' ({r['weekday_label']} {r['start_time']}-{r['end_time']})" for r, _ in flexible_candidates]
        reasons.append(f"có hoạt động cá nhân trạng thái 'Có thể đổi giờ' ({', '.join(r_names)}) nằm trước deadline")
        for r, dur in flexible_candidates:
            suggestions.append({
                "key": f"convert_flexible_{r['id']}",
                "type": "convert_flexible_to_study_window",
                "title": f"Đổi giờ hoạt động '{r['title']}' ({r['weekday_label']} {r['start_time']}-{r['end_time']}) thành khung giờ học",
                "description": (
                    f"Hoạt động '{r['title']}' được bạn đánh dấu là 'Có thể đổi giờ'. "
                    f"Nếu bạn đồng ý dời hoạt động này sang lúc khác, bạn sẽ có thêm {dur} phút học trước hạn nộp."
                ),
                "routine_id": r["id"],
                "weekday": r["weekday"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
            })

    # 3. Check unscheduled future free daytime intervals (outside declared study_windows but not blocked by class/fixed/rest)
    has_study_windows = any(
        (r.get("flexibility") == "study_window" or r.get("activity_type") == "study_window")
        and time_to_minutes(r["end_time"]) > time_to_minutes(r["start_time"])
        for r in routines
    )
    potential_new_windows: List[Tuple[int, int, int]] = []
    total_remaining_daytime_open = 0
    for w in range(1, min(7, cutoff_w) + 1):
        day_cap_start = max(earliest_min, cutoffs.get(w, 0))
        day_cap_end = min(latest_min, cutoff_min) if w == cutoff_w else latest_min
        if day_cap_end <= day_cap_start:
            continue
        day_open = subtract_intervals([(day_cap_start, day_cap_end)], blocked_by_day.get(w, []))
        total_remaining_daytime_open += sum(ge - gs for gs, ge in day_open)
        if has_study_windows:
            existing_win = [
                (time_to_minutes(r["start_time"]), time_to_minutes(r["end_time"]))
                for r in routines
                if r["weekday"] == w and (r.get("flexibility") == "study_window" or r.get("activity_type") == "study_window")
            ]
            # Subtract both blocked intervals and already declared study windows from [day_cap_start, day_cap_end]
            open_gaps = subtract_intervals([(day_cap_start, day_cap_end)], blocked_by_day.get(w, []) + existing_win)
            for gs, ge in open_gaps:
                if ge - gs >= 45:
                    potential_new_windows.append((w, gs, ge))

    if not reasons:
        if week_monday is not None and local_ceil is not None and total_remaining_daytime_open < missing_minutes:
            reasons.append(
                f"thời gian còn lại từ thời điểm hiện tại ({local_ceil.strftime('%Y-%m-%d %H:%M')}) "
                f"đến hạn nộp ({task['deadline_display']}) không còn đủ {missing_minutes} phút trong khung giờ hợp lệ"
            )
        elif has_study_windows:
            reasons.append(
                f"tổng thời gian trống trong các 'Khung giờ có thể học' trước hạn ({task['deadline_display']}) đã hết"
            )
        else:
            reasons.append(
                f"không còn khoảng thời gian phù hợp trong khung giờ rảnh ban ngày trước hạn ({task['deadline_display']})"
            )

    if has_study_windows:
        for w, gs, ge in potential_new_windows[:3]:
            win_end = min(ge, gs + max(60, min(180, missing_minutes)))
            day_label = WEEKDAY_NAMES[w]
            st_str = minutes_to_time(gs)
            et_str = minutes_to_time(win_end)
            suggestions.append({
                "key": f"add_window_{w}_{st_str}_{et_str}",
                "type": "add_study_window",
                "title": f"Mở thêm khung giờ có thể học: {day_label} ({st_str} - {et_str})",
                "description": (
                    f"Khoảng {day_label} từ {st_str} đến {et_str} ({win_end - gs} phút) hiện đang trống, "
                    f"không trùng lịch học trên trường hay giờ nghỉ ngơi và nằm trước hạn nộp."
                ),
                "weekday": w,
                "start_time": st_str,
                "end_time": et_str,
            })

    reason_text = "; ".join(reasons) if reasons else "không còn đủ khung giờ có thể học trước hạn nộp"
    return reason_text, suggestions


def _build_ai_explanation(
    confirmed_classes: List[Dict[str, Any]],
    routines: List[Dict[str, Any]],
    preserved_sessions: List[Dict[str, Any]],
    proposed_sessions: List[Dict[str, Any]],
    unscheduled_tasks: List[Dict[str, Any]],
    missing_estimate_tasks: List[Dict[str, Any]],
    parsed_ai: Dict[str, Any],
    prefs: Dict[str, Any],
    reschedule_only: bool,
    unplaced_courses: Optional[List[str]] = None,
    is_historical_week: bool = False,
    week_monday: Optional[date] = None,
    ceil_now: Optional[datetime] = None,
) -> str:
    """Create a clear, structured Vietnamese explanation of the generated study plan."""
    fixed_routines = [r for r in routines if r.get("flexibility") == "fixed"]
    protected_routines = [r for r in routines if r.get("flexibility") == "protected"]
    flexible_routines = [r for r in routines if r.get("flexibility") == "flexible"]
    study_windows = [r for r in routines if r.get("flexibility") == "study_window"]

    total_proposed_min = sum(int(s["duration_minutes"]) for s in proposed_sessions)
    total_preserved_min = sum(
        time_to_minutes(s["end_time"]) - time_to_minutes(s["start_time"]) for s in preserved_sessions
    )

    parts: List[str] = []
    mode_str = "sắp xếp lại các phần việc chưa hoàn thành" if reschedule_only else "tạo kế hoạch học tập cá nhân mới"
    if study_windows:
        parts.append(
            f"Bộ xếp lịch đã {mode_str} dựa trên {len(confirmed_classes)} buổi học cố định trên trường, "
            f"{len(fixed_routines)} hoạt động cá nhân cố định, {len(protected_routines)} khung giờ nghỉ ngơi được bảo vệ "
            f"và {len(study_windows)} khung giờ cho phép tự học."
        )
    else:
        parts.append(
            f"Bộ xếp lịch đã {mode_str} dựa trên {len(confirmed_classes)} buổi học cố định trên trường, "
            f"{len(fixed_routines)} hoạt động cá nhân cố định, {len(protected_routines)} khung giờ nghỉ ngơi được bảo vệ "
            f"và tự động tính khung giờ rảnh ban ngày ({minutes_to_time(parsed_ai['earliest_min'])} - {minutes_to_time(parsed_ai['latest_min'])})."
        )

    if preserved_sessions:
        parts.append(
            f"Đã giữ nguyên {len(preserved_sessions)} phiên tự học ({total_preserved_min} phút) đã hoàn thành hoặc được bạn khóa cố định."
        )

    if proposed_sessions:
        parts.append(
            f"Đề xuất {len(proposed_sessions)} phiên tự học mới (tổng cộng {total_proposed_min} phút ≈ {total_proposed_min / 60:.1f} giờ), "
            f"ưu tiên các bài tập có hạn nộp gần nhất và độ ưu tiên cao, đồng thời cố gắng chừa khoảng dự phòng "
            f"trước deadline và giới hạn không quá {parsed_ai['max_daily_min']} phút tự học/ngày."
        )
    elif is_historical_week and week_monday is not None:
        week_sunday = week_monday + timedelta(days=6)
        now_str = ceil_now.strftime("%Y-%m-%d %H:%M") if ceil_now is not None else "hiện tại"
        parts.append(
            f"⚠️ Tuần lập lịch đã chọn ({week_monday.isoformat()} đến {week_sunday.isoformat()}) "
            f"đã qua so với thời điểm {now_str}; không thể xếp các phiên tự học mới vào quá khứ."
        )
    elif not unscheduled_tasks and not missing_estimate_tasks and not unplaced_courses:
        parts.append("Hiện không có bài tập nào cần xếp thêm phiên học mới.")

    if flexible_routines:
        parts.append(
            f"Các hoạt động 'Có thể đổi giờ' ({len(flexible_routines)} mục) được giữ nguyên vị trí và không bị tự ý thay đổi khi chưa có sự đồng ý của bạn."
        )

    if parsed_ai["insights"]:
        parts.append("Áp dụng yêu cầu cá nhân của bạn: " + " ".join(parsed_ai["insights"]))

    if missing_estimate_tasks:
        names = ", ".join(f"'{t['title']}'" for t in missing_estimate_tasks)
        parts.append(
            f"⚠️ Có {len(missing_estimate_tasks)} công việc ({names}) chưa có thời lượng ước tính nên tạm thời chưa thể xếp lịch. "
            f"Vui lòng bổ sung số phút dự kiến cho các công việc này."
        )

    if unplaced_courses:
        parts.append(
            f"⚠️ Cảnh báo: Không đủ khung giờ tự học hợp lệ cho các môn sau: {', '.join(unplaced_courses)}."
        )

    if unscheduled_tasks:
        total_missing = sum(int(u["missing_minutes"]) for u in unscheduled_tasks)
        parts.append(
            f"⚠️ Lưu ý quan trọng: Còn {len(unscheduled_tasks)} bài tập chưa xếp đủ thời gian (thiếu tổng cộng {total_missing} phút). "
            f"Xem chi tiết cảnh báo và các lựa chọn gợi ý bên dưới để quyết định điều chỉnh."
        )
    elif proposed_sessions and not missing_estimate_tasks and not unplaced_courses:
        parts.append(
            "✅ Toàn bộ khối lượng bài tập đã được xếp đủ vào các khung giờ có thể học trước hạn nộp mà không vi phạm lịch cố định hay giờ nghỉ ngơi. "
            "Hãy kiểm tra bản nháp bên dưới và bấm 'Xác nhận & Lưu lịch học tập'."
        )

    return " ".join(parts)


# ==============================================================================
# 6. Confirming Draft Plan & Managing Confirmed Study Sessions
# ==============================================================================


def confirm_study_plan(
    plan_id: str,
    reviewed_sessions: Optional[List[Dict[str, Any]]] = None,
    db_path: Optional[Path | str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Validate and save the user-reviewed study plan draft into study_sessions."""
    draft = get_plan_draft(plan_id, db_path=db_path)
    if not draft:
        raise KeyError("Không tìm thấy bản nháp kế hoạch học tập")
    if str(draft.get("status") or "") != "draft":
        raise ValueError("Bản nháp kế hoạch học tập không còn hợp lệ (đã được xác nhận hoặc hủy trước đó).")

    sessions_to_save = reviewed_sessions if reviewed_sessions is not None else draft["sessions"]
    normalized_new: List[Dict[str, Any]] = []
    allowed_draft_sources = ("ai_assistant", "ai_auto_90m", "ai_auto_courses")

    for idx, s in enumerate(sessions_to_save):
        title = str(s.get("title") or "").strip()
        course = str(s.get("course") or "").strip()
        if not title or not course:
            raise ValueError(f"Dòng {idx + 1}: Tên phiên học và môn học không được để trống")
        w = int(s.get("weekday") or 0)
        if w < 1 or w > 7:
            raise ValueError(f"Dòng {idx + 1} ({title}): Thứ trong tuần phải từ 1 đến 7")
        st = str(s.get("start_time") or "").strip()
        et = str(s.get("end_time") or "").strip()
        s_min = time_to_minutes(st)
        e_min = time_to_minutes(et)
        if e_min <= s_min:
            raise ValueError(f"Dòng {idx + 1} ({title}): Giờ kết thúc ({et}) phải sau giờ bắt đầu ({st})")

        raw_source = str(s.get("source") or "ai_assistant").strip()
        safe_source = raw_source if raw_source in allowed_draft_sources else "ai_assistant"

        normalized_new.append({
            "task_id": s.get("task_id"),
            "weekly_lesson_id": s.get("weekly_lesson_id"),
            "title": title,
            "course": course,
            "weekday": w,
            "start_time": st,
            "end_time": et,
            "duration_minutes": e_min - s_min,
            "priority": str(s.get("priority") or "medium"),
            "source": safe_source,
            "completed": 0,
            "locked": 1 if s.get("locked") else 0,
            "session_index": int(s.get("session_index") or 1),
            "total_sessions": int(s.get("total_sessions") or 1),
            "notes": str(s.get("notes") or ""),
            "classified_topic": str(s.get("classified_topic") or ""),
            "worksheet_id": str(s.get("worksheet_id") or ""),
            "practice_id": str(s.get("practice_id") or ""),
            "exercise_status": str(s.get("exercise_status") or ""),
            "_is_preserved": False,
        })

    created_at_iso = datetime.now(timezone.utc).isoformat()
    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        d_row = conn.execute("SELECT status FROM study_plan_drafts WHERE plan_id = ?", (plan_id,)).fetchone()
        if not d_row or d_row[0] != "draft":
            raise ValueError("Bản nháp kế hoạch học tập không còn hợp lệ (đã được xác nhận hoặc hủy trước đó).")

        curr_classes = [dict(r) for r in conn.execute("SELECT * FROM timetable_confirmed").fetchall()]
        curr_routines = [dict(r) for r in conn.execute("SELECT * FROM personal_routines").fetchall()]
        curr_tasks = [dict(r) for r in conn.execute("SELECT * FROM study_tasks").fetchall()]
        curr_tasks_by_id = {t["id"]: t for t in curr_tasks}
        curr_preserved = [
            dict(r) for r in conn.execute(
                "SELECT * FROM study_sessions WHERE completed = 1 OR locked = 1 OR source IN ('manual', 'user_manual')"
            ).fetchall()
        ]
        for p in curr_preserved:
            p["_is_preserved"] = True

        pref_row = conn.execute("SELECT * FROM study_preferences WHERE id = 1").fetchone()
        curr_prefs = dict(pref_row) if pref_row else {}
        curr_week_monday = _infer_week_monday(curr_prefs, curr_tasks)
        has_study_windows = any(
            (r.get("flexibility") == "study_window" or r.get("activity_type") == "study_window")
            and time_to_minutes(r["end_time"]) > time_to_minutes(r["start_time"])
            for r in curr_routines
        )

        earliest = curr_prefs.get("earliest_study_time") or "07:00"
        latest = curr_prefs.get("latest_study_time") or "22:30"
        max_daily = curr_prefs.get("max_daily_study_minutes")

        # Revalidate hard constraints: awake bounds, daily budget, current-time cutoff, no overlap with classes, fixed routines, protected rest, or preserved sessions
        violations = verify_schedule_constraints(
            sessions=curr_preserved + normalized_new,
            confirmed_classes=curr_classes,
            routines=curr_routines,
            tasks_by_id=curr_tasks_by_id,
            require_inside_study_window=has_study_windows,
            max_daily_minutes=max_daily,
            week_monday=curr_week_monday,
            earliest_study_time=earliest,
            latest_study_time=latest,
            enforce_awake_bounds=True,
            now=now,
        )
        if violations:
            raise ValueError(
                "Lịch hoặc thiết lập học tập đã thay đổi so với thời điểm đề xuất (phát hiện xung đột hoặc vi phạm giới hạn): "
                + " | ".join(violations)
                + ". Vui lòng tạo lại đề xuất lịch mới."
            )

        # Delete only unlocked, uncompleted, non-manual sessions
        conn.execute(
            "DELETE FROM study_sessions WHERE completed = 0 AND locked = 0 AND source NOT IN ('manual', 'user_manual')"
        )

        cols = {row[1] for row in conn.execute("PRAGMA table_info(study_sessions)").fetchall()}
        col_names = [
            "plan_id", "task_id", "title", "course", "weekday", "start_time", "end_time",
            "duration_minutes", "priority", "source", "completed", "locked",
            "session_index", "total_sessions", "notes", "created_at"
        ]
        val_placeholders = ["?"] * len(col_names)
        if "classified_topic" in cols:
            col_names.append("classified_topic")
            val_placeholders.append("?")
        if "worksheet_id" in cols:
            col_names.append("worksheet_id")
            val_placeholders.append("?")
        if "practice_id" in cols:
            col_names.append("practice_id")
            val_placeholders.append("?")
        if "exercise_status" in cols:
            col_names.append("exercise_status")
            val_placeholders.append("?")
        if "weekly_lesson_id" in cols:
            col_names.append("weekly_lesson_id")
            val_placeholders.append("?")

        insert_sql = f"INSERT INTO study_sessions ({', '.join(col_names)}) VALUES ({', '.join(val_placeholders)})"
        for item in normalized_new:
            params = [
                plan_id,
                item["task_id"],
                item["title"],
                item["course"],
                item["weekday"],
                item["start_time"],
                item["end_time"],
                item["duration_minutes"],
                item["priority"],
                item["source"],
                item["completed"],
                item["locked"],
                item["session_index"],
                item["total_sessions"],
                item["notes"],
                created_at_iso,
            ]
            if "classified_topic" in cols:
                params.append(item.get("classified_topic") or "")
            if "worksheet_id" in cols:
                params.append(item.get("worksheet_id") or "")
            if "practice_id" in cols:
                params.append(item.get("practice_id") or "")
            if "exercise_status" in cols:
                params.append(item.get("exercise_status") or "")
            if "weekly_lesson_id" in cols:
                params.append(item.get("weekly_lesson_id"))
            conn.execute(insert_sql, params)

        conn.execute(
            "UPDATE study_plan_drafts SET status = 'confirmed' WHERE plan_id = ?",
            (plan_id,),
        )

    return {
        "status": "ok",
        "plan_id": plan_id,
        "saved_count": len(normalized_new),
        "sessions": get_study_sessions(db_path=db_path),
    }


def add_manual_study_session(data: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Allow the user to manually add a study session and lock it by default."""
    title = str(data.get("title") or "").strip()
    course = str(data.get("course") or "").strip()
    if not title or not course:
        raise ValueError("Tên phiên học và môn học không được để trống")
    w = int(data.get("weekday") or 0)
    if w < 1 or w > 7:
        raise ValueError("Thứ trong tuần phải từ 1 đến 7")
    st = str(data.get("start_time") or "").strip()
    et = str(data.get("end_time") or "").strip()
    s_min = time_to_minutes(st)
    e_min = time_to_minutes(et)
    if e_min <= s_min:
        raise ValueError("Giờ kết thúc phải lớn hơn giờ bắt đầu")

    confirmed_classes = timetable_store.get_confirmed_entries(db_path=db_path)
    routines = get_routines(db_path=db_path)
    existing_sessions = get_study_sessions(db_path=db_path)
    tasks_by_id = {t["id"]: t for t in get_tasks(db_path=db_path)}

    candidate = {
        "task_id": data.get("task_id"),
        "title": title,
        "course": course,
        "weekday": w,
        "start_time": st,
        "end_time": et,
        "duration_minutes": e_min - s_min,
        "priority": str(data.get("priority") or "medium"),
        "source": "user_manual",
        "completed": 1 if data.get("completed") else 0,
        "locked": 1 if data.get("locked", True) else 0,
        "notes": str(data.get("notes") or ""),
    }

    violations = verify_schedule_constraints(
        sessions=existing_sessions + [candidate],
        confirmed_classes=confirmed_classes,
        routines=routines,
        tasks_by_id=tasks_by_id,
        require_inside_study_window=False,
    )
    if violations:
        raise ValueError(" | ".join(violations))

    now_iso = datetime.now(timezone.utc).isoformat()
    with timetable_store.get_connection(db_path=db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO study_sessions
            (plan_id, task_id, title, course, weekday, start_time, end_time,
             duration_minutes, priority, source, completed, locked,
             session_index, total_sessions, notes, created_at)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'user_manual', ?, ?, 1, 1, ?, ?)
            """,
            (
                candidate["task_id"],
                candidate["title"],
                candidate["course"],
                candidate["weekday"],
                candidate["start_time"],
                candidate["end_time"],
                candidate["duration_minutes"],
                candidate["priority"],
                candidate["completed"],
                candidate["locked"],
                candidate["notes"],
                now_iso,
            ),
        )
        sid = cursor.lastrowid or 0

    sessions = get_study_sessions(db_path=db_path)
    return next((s for s in sessions if s["id"] == sid), {})


def update_study_session(session_id: int, data: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Update a study session (mark completed, lock/unlock, or edit time/title/course)."""
    with timetable_store.get_connection(db_path=db_path) as conn:
        row = conn.execute("SELECT * FROM study_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            raise KeyError("Không tìm thấy phiên học")
        existing = dict(row)

    title = str(data.get("title", existing["title"]) or "").strip()
    course = str(data.get("course", existing["course"]) or "").strip()
    if not title or not course:
        raise ValueError("Tên phiên học và môn học không được để trống")

    w = int(data.get("weekday", existing["weekday"]))
    if w < 1 or w > 7:
        raise ValueError("Thứ trong tuần phải từ 1 đến 7")
    st = str(data.get("start_time", existing["start_time"])).strip()
    et = str(data.get("end_time", existing["end_time"])).strip()
    s_min = time_to_minutes(st)
    e_min = time_to_minutes(et)
    if e_min <= s_min:
        raise ValueError("Giờ kết thúc phải lớn hơn giờ bắt đầu")

    time_changed = (w != existing["weekday"] or st != existing["start_time"] or et != existing["end_time"])
    completed = 1 if data.get("completed", bool(existing["completed"])) else 0
    if "locked" in data:
        locked = 1 if data.get("locked") else 0
    else:
        # Automatically lock if user manually edited the session's day/time
        locked = 1 if (time_changed or existing["locked"]) else 0

    notes = str(data.get("notes", existing["notes"]) or "").strip()

    other_sessions = [s for s in get_study_sessions(db_path=db_path) if s["id"] != session_id]
    candidate = {
        **existing,
        "title": title,
        "course": course,
        "weekday": w,
        "start_time": st,
        "end_time": et,
        "duration_minutes": e_min - s_min,
        "completed": completed,
        "locked": locked,
        "notes": notes,
    }

    if time_changed:
        confirmed_classes = timetable_store.get_confirmed_entries(db_path=db_path)
        routines = get_routines(db_path=db_path)
        tasks_by_id = {t["id"]: t for t in get_tasks(db_path=db_path)}
        violations = verify_schedule_constraints(
            sessions=other_sessions + [candidate],
            confirmed_classes=confirmed_classes,
            routines=routines,
            tasks_by_id=tasks_by_id,
            require_inside_study_window=False,
        )
        if violations:
            raise ValueError(" | ".join(violations))

    with timetable_store.get_connection(db_path=db_path) as conn:
        conn.execute(
            """
            UPDATE study_sessions
            SET title = ?, course = ?, weekday = ?, start_time = ?, end_time = ?,
                duration_minutes = ?, completed = ?, locked = ?, notes = ?
            WHERE id = ?
            """,
            (title, course, w, st, et, e_min - s_min, completed, locked, notes, session_id),
        )
        # If linked to a task, check if all sessions for that task are now completed
        tid = existing.get("task_id")
        if tid:
            task_row = conn.execute(
                "SELECT estimated_minutes FROM study_tasks WHERE id = ?", (tid,)
            ).fetchone()
            sess_rows = conn.execute(
                "SELECT duration_minutes, completed FROM study_sessions WHERE task_id = ?", (tid,)
            ).fetchall()
            done_mins = sum(int(r["duration_minutes"]) for r in sess_rows if r["completed"])
            conn.execute(
                "UPDATE study_tasks SET completed_minutes = ? WHERE id = ?",
                (done_mins, tid),
            )
            if task_row and task_row["estimated_minutes"] and done_mins >= int(task_row["estimated_minutes"]):
                if all(r["completed"] for r in sess_rows):
                    conn.execute("UPDATE study_tasks SET completed = 1 WHERE id = ?", (tid,))

    sessions = get_study_sessions(db_path=db_path)
    return next((s for s in sessions if s["id"] == session_id), {})


def delete_study_session(session_id: int, db_path: Optional[Path | str] = None) -> bool:
    with timetable_store.get_connection(db_path=db_path) as conn:
        cursor = conn.execute("DELETE FROM study_sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0


def apply_planner_suggestion(payload: Dict[str, Any], db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Apply a user-approved scheduling suggestion (e.g., add study window, increase daily limit, or convert flexible routine)."""
    sug_type = str(payload.get("type") or "").strip()
    if sug_type == "increase_daily_limit":
        new_max = int(payload.get("new_max_daily_minutes") or 300)
        prefs = update_preferences({"max_daily_study_minutes": new_max}, db_path=db_path)
        return {"status": "ok", "applied": sug_type, "preferences": prefs}

    if sug_type == "add_study_window":
        w = int(payload["weekday"])
        st = str(payload["start_time"]).strip()
        et = str(payload["end_time"]).strip()
        res = add_routine({
            "weekday": w,
            "start_time": st,
            "end_time": et,
            "title": payload.get("title") or "Khung giờ tự học bổ sung",
            "activity_type": "study_window",
            "flexibility": "study_window",
        }, db_path=db_path)
        return {"status": "ok", "applied": sug_type, "routine": res["routine"]}

    if sug_type == "convert_flexible_to_study_window":
        rid = int(payload["routine_id"])
        existing = next((r for r in get_routines(db_path=db_path) if r["id"] == rid), None)
        if not existing:
            raise KeyError("Không tìm thấy hoạt động cá nhân để đổi giờ")
        res = update_routine(rid, {
            **existing,
            "title": f"Khung giờ tự học (Đổi từ: {existing['title']})",
            "activity_type": "study_window",
            "flexibility": "study_window",
        }, db_path=db_path)
        return {"status": "ok", "applied": sug_type, "routine": res["routine"]}

    raise ValueError(f"Loại gợi ý không được hỗ trợ: {sug_type}")


# ==============================================================================
# 7. Unified Combined Weekly & Daily Schedule View
# ==============================================================================


def get_combined_weekly_schedule(db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Build unified weekly schedule combining OCR classes, personal routines, rest, and study sessions."""
    classes = timetable_store.get_confirmed_entries(db_path=db_path)
    routines = get_routines(db_path=db_path)
    study_sessions = get_study_sessions(db_path=db_path)
    active_draft = get_latest_active_plan_draft(db_path=db_path)

    items: List[Dict[str, Any]] = []
    study_windows: List[Dict[str, Any]] = []

    for c in classes:
        w = int(c["weekday"])
        dur = time_to_minutes(c["end_time"]) - time_to_minutes(c["start_time"])
        items.append({
            "uid": f"class_{c['id']}",
            "item_id": c["id"],
            "category": "class",
            "category_label": "Lớp học trên trường",
            "source_badge": "OCR / Đã xác nhận",
            "weekday": w,
            "weekday_label": WEEKDAY_NAMES.get(w, f"Thứ {w}"),
            "start_time": c["start_time"],
            "end_time": c["end_time"],
            "duration_minutes": dur,
            "title": c["course"],
            "course": c["course"],
            "room": c.get("room"),
            "flexibility": "fixed",
            "flexibility_label": "Cố định",
            "completed": False,
            "locked": True,
            "editable": True,
        })

    for r in routines:
        w = int(r["weekday"])
        dur = time_to_minutes(r["end_time"]) - time_to_minutes(r["start_time"])
        if r["flexibility"] == "study_window":
            study_windows.append({
                "uid": f"window_{r['id']}",
                "item_id": r["id"],
                "weekday": w,
                "weekday_label": WEEKDAY_NAMES.get(w, f"Thứ {w}"),
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "duration_minutes": dur,
                "title": r["title"],
            })
            continue

        is_rest = r["activity_type"] == "rest" or r["flexibility"] == "protected"
        items.append({
            "uid": f"routine_{r['id']}",
            "item_id": r["id"],
            "category": "rest" if is_rest else "routine",
            "category_label": "Nghỉ ngơi / Ăn uống" if is_rest else r["activity_type_label"],
            "source_badge": "Lịch cá nhân",
            "weekday": w,
            "weekday_label": WEEKDAY_NAMES.get(w, f"Thứ {w}"),
            "start_time": r["start_time"],
            "end_time": r["end_time"],
            "duration_minutes": dur,
            "title": r["title"],
            "course": None,
            "room": None,
            "activity_type": r["activity_type"],
            "flexibility": r["flexibility"],
            "flexibility_label": r["flexibility_label"],
            "completed": False,
            "locked": r["flexibility"] in ("fixed", "protected"),
            "editable": True,
        })

    for s in study_sessions:
        w = int(s["weekday"])
        items.append({
            "uid": f"session_{s['id']}",
            "item_id": s["id"],
            "task_id": s.get("task_id"),
            "category": "study_session",
            "category_label": "Phiên tự học",
            "source_badge": s.get("source_label") or ("Bộ xếp lịch đề xuất" if s.get("source") == "ai_assistant" else "Người dùng tự xếp"),
            "weekday": w,
            "weekday_label": WEEKDAY_NAMES.get(w, f"Thứ {w}"),
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "duration_minutes": s["duration_minutes"],
            "title": s["title"],
            "course": s["course"],
            "classified_topic": s.get("classified_topic") or "",
            "worksheet_id": s.get("worksheet_id") or "",
            "pdf_url": s.get("pdf_url"),
            "priority": s.get("priority", "medium"),
            "priority_label": s.get("priority_label", "Trung bình"),
            "flexibility": "locked" if s.get("locked") else "scheduled",
            "flexibility_label": "Đã khóa giờ" if s.get("locked") else "Đã lên lịch",
            "completed": bool(s.get("completed")),
            "locked": bool(s.get("locked")),
            "editable": True,
            "notes": s.get("notes") or "",
        })

    items.sort(key=lambda x: (int(x["weekday"]), x["start_time"], x["end_time"]))
    study_windows.sort(key=lambda x: (int(x["weekday"]), x["start_time"]))

    # Summary statistics
    class_minutes = sum(i["duration_minutes"] for i in items if i["category"] == "class")
    routine_minutes = sum(i["duration_minutes"] for i in items if i["category"] in ("routine", "rest"))
    study_minutes = sum(i["duration_minutes"] for i in items if i["category"] == "study_session")
    completed_study_minutes = sum(
        i["duration_minutes"] for i in items if i["category"] == "study_session" and i["completed"]
    )
    window_minutes = sum(w["duration_minutes"] for w in study_windows)

    return {
        "items": items,
        "study_windows": study_windows,
        "active_draft": active_draft,
        "summary": {
            "class_count": len(classes),
            "class_minutes": class_minutes,
            "routine_count": len(routines) - len(study_windows),
            "routine_minutes": routine_minutes,
            "study_window_count": len(study_windows),
            "study_window_minutes": window_minutes,
            "study_session_count": len(study_sessions),
            "study_minutes": study_minutes,
            "completed_study_minutes": completed_study_minutes,
        },
    }


def get_full_planner_state(db_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Return complete state for the Personal Study Schedule Assistant UI."""
    confirmed_classes = timetable_store.get_confirmed_entries(db_path=db_path)
    routines = get_routines(db_path=db_path)
    preferences = get_preferences(db_path=db_path)
    tasks = get_tasks(db_path=db_path)
    sessions = get_study_sessions(db_path=db_path)
    combined = get_combined_weekly_schedule(db_path=db_path)
    courses = sorted({c["course"] for c in confirmed_classes if c.get("course")} | {t["course"] for t in tasks if t.get("course")})
    return {
        "confirmed_classes": confirmed_classes,
        "routines": routines,
        "preferences": preferences,
        "tasks": tasks,
        "study_sessions": sessions,
        "active_draft": combined["active_draft"],
        "combined_schedule": combined["items"],
        "study_windows": combined["study_windows"],
        "summary": combined["summary"],
        "known_courses": courses,
    }
