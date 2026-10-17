"""Deterministic office directory lookup supporting exact and partial office names (Issue #20)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Optional, Sequence
import unicodedata

from app.config import DATA_DIR

DEFAULT_OFFICES_CSV = DATA_DIR / "offices.csv"


@dataclass(frozen=True)
class OfficeRecord:
    """Immutable representation of a campus/organization office entry."""

    name: str
    location: str = ""
    email: str = ""
    hours: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "location": self.location,
            "email": self.email,
            "hours": self.hours,
            "description": self.description,
        }

    def __getitem__(self, key: str) -> str:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


DEFAULT_OFFICES: tuple[OfficeRecord, ...] = (
    OfficeRecord(
        name="Training Office",
        location="Room A1-201",
        email="training@university.edu.vn",
        hours="Mon-Fri 08:00-17:00",
        description="Undergraduate course registration and academic scheduling",
    ),
    OfficeRecord(
        name="Student Affairs Office",
        location="Room A1-102",
        email="studentaffairs@university.edu.vn",
        hours="Mon-Fri 08:00-17:00",
        description="Student support services and extracurricular activities",
    ),
    OfficeRecord(
        name="Academic Affairs Office",
        location="Room A1-105",
        email="academic@university.edu.vn",
        hours="Mon-Fri 08:00-17:00",
        description="Curriculum management and academic records",
    ),
    OfficeRecord(
        name="Admissions Office",
        location="Room A1-101",
        email="admissions@university.edu.vn",
        hours="Mon-Fri 08:00-17:00",
        description="Enrollment counseling and application processing",
    ),
    OfficeRecord(
        name="Finance and Accounting Office",
        location="Room A1-108",
        email="finance@university.edu.vn",
        hours="Mon-Fri 08:00-16:30",
        description="Tuition payment and scholarship disbursement",
    ),
    OfficeRecord(
        name="International Training Office",
        location="Room B2-301",
        email="intl.training@university.edu.vn",
        hours="Mon-Fri 08:00-17:00",
        description="Joint international programs and exchange support",
    ),
    OfficeRecord(
        name="Ho Chi Minh City Office",
        location="Campus 2 - Building C1",
        email="hcmc.office@university.edu.vn",
        hours="Mon-Fri 08:00-17:00",
        description="Regional student administration and campus coordination",
    ),
    OfficeRecord(
        name="Library and Information Center",
        location="Building D1 - Floor 2",
        email="library@university.edu.vn",
        hours="Mon-Sat 07:30-20:00",
        description="Study spaces and reference material lookup",
    ),
)


def normalize_office_text(text: Any) -> str:
    """Normalize query or office name for case-insensitive partial matching.

    - Converts to Unicode NFC and casefolds.
    - Replaces punctuation/symbols with spaces.
    - Collapses consecutive whitespace into a single space.
    - Returns empty string for non-str, empty, or whitespace/punctuation-only input.
    """
    if not isinstance(text, str):
        return ""
    norm = unicodedata.normalize("NFC", text).casefold()
    norm = re.sub(r"[^\w\s]", " ", norm)
    norm = re.sub(r"_+", " ", norm)
    return " ".join(norm.split())


def _coerce_office_record(item: OfficeRecord | Mapping[str, Any] | str) -> OfficeRecord:
    if isinstance(item, OfficeRecord):
        return item
    if isinstance(item, str):
        return OfficeRecord(name=item.strip())
    if isinstance(item, Mapping):
        return OfficeRecord(
            name=str(item.get("name", "")).strip(),
            location=str(item.get("location", item.get("room", ""))).strip(),
            email=str(item.get("email", item.get("contact", ""))).strip(),
            hours=str(item.get("hours", "")).strip(),
            description=str(item.get("description", item.get("notes", ""))).strip(),
        )
    raise TypeError(f"Unsupported office record type: {type(item)!r}")


def load_offices(csv_path: Optional[Path | str] = None) -> list[OfficeRecord]:
    """Load office records from CSV if available, otherwise return DEFAULT_OFFICES."""
    target = Path(csv_path) if csv_path is not None else DEFAULT_OFFICES_CSV
    if not target.is_file():
        return list(DEFAULT_OFFICES)

    records: list[OfficeRecord] = []
    with target.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            record = _coerce_office_record(row)
            if normalize_office_text(record.name):
                records.append(record)
    return records if records else list(DEFAULT_OFFICES)


def search_offices(
    query: str,
    offices: Optional[Sequence[OfficeRecord | Mapping[str, Any] | str]] = None,
    *,
    csv_path: Optional[Path | str] = None,
) -> list[OfficeRecord]:
    """Search offices by full or partial name (case-insensitive, whitespace-normalized).

    Ranking priority:
      0 - Exact normalized name match (e.g. 'training office' -> 'Training Office')
      1 - Word-aligned prefix match (e.g. 'training' -> 'Training Office')
      2 - General prefix match (e.g. 'train' -> 'Training Office')
      3 - Word-boundary match anywhere in name (e.g. 'affairs' -> 'Student Affairs Office')
      4 - Substring match anywhere in name (e.g. 'form' -> 'Library and Information Center')
    Within the same priority tier, shorter office names come first, preserving input order on ties.
    """
    q_norm = normalize_office_text(query)
    if not q_norm:
        return []

    source_records = (
        [_coerce_office_record(item) for item in offices]
        if offices is not None
        else load_offices(csv_path)
    )

    word_prefix_pattern = re.compile(rf"^{re.escape(q_norm)}(?:\b|\s|$)")
    word_boundary_pattern = re.compile(rf"\b{re.escape(q_norm)}\b")

    scored: list[tuple[int, int, int, OfficeRecord]] = []
    for idx, record in enumerate(source_records):
        name_norm = normalize_office_text(record.name)
        if not name_norm or q_norm not in name_norm:
            continue

        if q_norm == name_norm:
            tier = 0
        elif word_prefix_pattern.search(name_norm):
            tier = 1
        elif name_norm.startswith(q_norm):
            tier = 2
        elif word_boundary_pattern.search(name_norm):
            tier = 3
        else:
            tier = 4

        scored.append((tier, len(name_norm), idx, record))

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in scored]


def lookup_office(
    query: str,
    offices: Optional[Sequence[OfficeRecord | Mapping[str, Any] | str]] = None,
    *,
    csv_path: Optional[Path | str] = None,
) -> Optional[OfficeRecord]:
    """Return the highest-priority matching office for `query`, or None if no match."""
    matches = search_offices(query, offices=offices, csv_path=csv_path)
    return matches[0] if matches else None


def format_office_reply(
    query: str,
    offices: Optional[Sequence[OfficeRecord | Mapping[str, Any] | str]] = None,
    *,
    csv_path: Optional[Path | str] = None,
) -> Optional[str]:
    """Format a deterministic text response for office lookup results, or None if no match."""
    matches = search_offices(query, offices=offices, csv_path=csv_path)
    if not matches:
        return None

    def _render_single(office: OfficeRecord) -> str:
        parts = [f"**{office.name}**"]
        if office.location:
            parts.append(f"Location: {office.location}")
        if office.email:
            parts.append(f"Email: {office.email}")
        if office.hours:
            parts.append(f"Hours: {office.hours}")
        if office.description:
            parts.append(f"Note: {office.description}")
        return " | ".join(parts)

    if len(matches) == 1 or normalize_office_text(matches[0].name) == normalize_office_text(query):
        return _render_single(matches[0])

    lines = [f"Found {len(matches)} matching offices:"]
    for item in matches:
        lines.append(f"- {_render_single(item)}")
    return "\n".join(lines)
