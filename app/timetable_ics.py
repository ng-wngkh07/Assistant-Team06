"""RFC 5545 iCalendar export for academic timetables.

Generates recurring weekly events within a user-specified semester date range (start_date, end_date)
using the Asia/Ho_Chi_Minh timezone (+07:00).
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Tuple


def _get_vn_timezone():
    try:
        return ZoneInfo("Asia/Ho_Chi_Minh")
    except Exception:
        return timezone(timedelta(hours=7))


def _escape_text(value: str) -> str:
    """Encode RFC 5545 TEXT without allowing new content lines."""
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    return value.replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _fold_line(line: str) -> str:
    """Fold at 75 UTF-8 octets, including the continuation space."""
    parts = []
    current = ''
    size = 0
    for character in line:
        width = len(character.encode('utf-8'))
        if size + width > 75:
            parts.append(current)
            current, size = ' ', 1
        current += character
        size += width
    parts.append(current)
    return '\r\n'.join(parts)


def validate_date_range(start_date_str: str, end_date_str: str) -> Tuple[date, date]:
    """Validate and parse start_date and end_date in YYYY-MM-DD format.

    Raises ValueError if format is invalid or start_date > end_date.
    """
    date_regex = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not date_regex.match(start_date_str) or not date_regex.match(end_date_str):
        raise ValueError("Dates must be formatted as YYYY-MM-DD")

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid calendar date: {e}")

    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")

    return start_date, end_date


def generate_ics(
    entries: List[Dict[str, Any]],
    start_date_str: str,
    end_date_str: str,
) -> str:
    """Generate RFC 5545 .ics text for a list of timetable entries.

    - weekday is 1..7 (1 = Monday / Thứ 2, ..., 7 = Sunday / Chủ nhật)
    - start_time and end_time must be HH:MM
    - Events recur weekly until end_date
    """
    if not entries:
        raise ValueError("Thời khóa biểu hiện chưa có môn học nào để xuất lịch.")

    start_date, end_date = validate_date_range(start_date_str, end_date_str)

    byday_map = {
        1: "MO",
        2: "TU",
        3: "WE",
        4: "TH",
        5: "FR",
        6: "SA",
        7: "SU",
    }

    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Agent Hoc Tap//Timetable Generator v1.0//VI",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Ho_Chi_Minh",
        "X-LIC-LOCATION:Asia/Ho_Chi_Minh",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0700",
        "TZOFFSETTO:+0700",
        "TZNAME:+07",
        "DTSTART:19700101T000000",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    clock_regex = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    events_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Mỗi mục trong danh sách thời khóa biểu phải là một đối tượng từ điển.")

        if "course" not in entry or entry["course"] is None:
            raise ValueError("Tên môn học không hợp lệ hoặc thiếu tên môn")
        course = str(entry["course"]).strip()
        if not course:
            raise ValueError("Tên môn học không được để trống")

        if "weekday" not in entry or entry["weekday"] is None:
            raise ValueError("Thứ trong tuần không được để trống")
        raw_w = entry["weekday"]
        if isinstance(raw_w, bool) or isinstance(raw_w, float):
            raise ValueError("Thứ trong tuần không hợp lệ (không chấp nhận boolean hoặc số thực)")
        try:
            if isinstance(raw_w, str):
                if not raw_w.strip().isdigit():
                    raise ValueError("Thứ trong tuần không hợp lệ")
                raw_weekday = int(raw_w.strip())
            elif isinstance(raw_w, int):
                raw_weekday = raw_w
            else:
                raise ValueError("Thứ trong tuần không hợp lệ")
        except (TypeError, ValueError) as err:
            raise ValueError(f"Thứ trong tuần không hợp lệ: {err}")

        if raw_weekday not in byday_map:
            raise ValueError(f"Thứ trong tuần phải từ 1 đến 7 (nhận được: {raw_weekday})")

        if "start_time" not in entry or "end_time" not in entry:
            raise ValueError("Thiếu giờ bắt đầu hoặc giờ kết thúc")
        st_val = entry.get("start_time")
        et_val = entry.get("end_time")
        if not st_val or not et_val:
            raise ValueError("Giờ bắt đầu và kết thúc không được để trống")
        start_time_str = str(st_val).strip()
        end_time_str = str(et_val).strip()

        if not clock_regex.match(start_time_str):
            raise ValueError(f"Giờ bắt đầu '{start_time_str}' không đúng định dạng 24h HH:MM")
        if not clock_regex.match(end_time_str):
            raise ValueError(f"Giờ kết thúc '{end_time_str}' không đúng định dạng 24h HH:MM")

        sh, sm = [int(x) for x in start_time_str.split(":")]
        eh, em = [int(x) for x in end_time_str.split(":")]
        if (eh * 60 + em) <= (sh * 60 + sm):
            raise ValueError(f"Giờ kết thúc ({end_time_str}) phải sau giờ bắt đầu ({start_time_str})")

        # Python weekday: 0 = Mon, 6 = Sun
        target_py_weekday = raw_weekday - 1
        days_ahead = (target_py_weekday - start_date.weekday()) % 7
        first_event_date = start_date + timedelta(days=days_ahead)

        if first_event_date > end_date:
            continue

        event_uid = f"{uuid.uuid4()}@agent-hoctap.local"
        dtstart = f"{first_event_date.strftime('%Y%m%d')}T{sh:02d}{sm:02d}00"
        dtend = f"{first_event_date.strftime('%Y%m%d')}T{eh:02d}{em:02d}00"
        local_end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=_get_vn_timezone())
        until_str = local_end_dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        room = str(entry.get("room") or "")
        notes = str(entry.get("notes") or "")
        description = f"Môn học: {course}"
        if room:
            description += f"\nPhòng: {room}"
        if notes:
            description += f"\nGhi chú: {notes}"

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{event_uid}",
            f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;TZID=Asia/Ho_Chi_Minh:{dtstart}",
            f"DTEND;TZID=Asia/Ho_Chi_Minh:{dtend}",
            f"RRULE:FREQ=WEEKLY;BYDAY={byday_map[raw_weekday]};UNTIL={until_str}",
            f"SUMMARY:{_escape_text(course)}",
            f"LOCATION:{_escape_text(room)}",
            f"DESCRIPTION:{_escape_text(description)}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ])
        events_count += 1

    if events_count == 0:
        raise ValueError("Không có môn học nào trong khoảng thời gian đã chọn để xuất lịch.")

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"
