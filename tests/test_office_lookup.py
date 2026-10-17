"""Issue #20: Backend office lookup handles partial names without changing other answers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from app.dialogue import HELP_MESSAGE, dialogue_intent
from app.office_lookup import (
    DEFAULT_OFFICES,
    OfficeRecord,
    format_office_reply,
    load_offices,
    lookup_office,
    normalize_office_text,
    search_offices,
)
from app.rag_agent import PsychologyAgent


def test_partial_name_training_finds_training_office():
    """Issue #20 acceptance: 'training' should find 'Training Office'."""
    match = lookup_office("training")
    assert match is not None
    assert match.name == "Training Office"
    assert match["name"] == "Training Office"

    all_matches = search_offices("training")
    assert all_matches
    assert all_matches[0].name == "Training Office"
    assert any(item.name == "International Training Office" for item in all_matches)


def test_exact_and_partial_names_across_positions():
    """Full name, prefix partial, middle partial, and suffix partial names are matched and ranked."""
    sample_offices = [
        OfficeRecord(name="Ho Chi Minh City Office", location="Campus 2"),
        OfficeRecord(name="Student Affairs Office", location="Room A1-102"),
        OfficeRecord(name="Academic Affairs Office", location="Room A1-105"),
        OfficeRecord(name="Training Office", location="Room A1-201"),
    ]

    # Exact full name
    exact = search_offices("Training Office", offices=sample_offices)
    assert [o.name for o in exact] == ["Training Office"]

    # Prefix partial name (example from prompt: 'Ho Chi Minh' -> 'Ho Chi Minh City Office')
    prefix = search_offices("Ho Chi Minh", offices=sample_offices)
    assert len(prefix) == 1
    assert prefix[0].name == "Ho Chi Minh City Office"

    # Middle partial name matching multiple offices
    middle = search_offices("Affairs", offices=sample_offices)
    assert [o.name for o in middle] == ["Student Affairs Office", "Academic Affairs Office"]

    # Substring partial match inside a word
    sub = search_offices("Academ", offices=sample_offices)
    assert [o.name for o in sub] == ["Academic Affairs Office"]


@pytest.mark.parametrize(
    "raw_query,expected_top",
    [
        ("TRAINING", "Training Office"),
        ("  tRaInInG   oFfIcE  ", "Training Office"),
        ("   ho   chi   minh  ", "Ho Chi Minh City Office"),
        ("student---affairs", "Student Affairs Office"),
    ],
)
def test_case_insensitivity_and_extra_whitespace(raw_query: str, expected_top: str):
    match = lookup_office(raw_query)
    assert match is not None
    assert match.name == expected_top


@pytest.mark.parametrize("empty_or_invalid", ["", "   ", "\t\n ", "???", "---", None, 123])
def test_empty_whitespace_or_nonexistent_returns_no_results(empty_or_invalid):
    assert normalize_office_text(empty_or_invalid) == ""
    assert search_offices(empty_or_invalid) == []  # type: ignore[arg-type]
    assert lookup_office(empty_or_invalid) is None  # type: ignore[arg-type]
    assert format_office_reply(empty_or_invalid) is None  # type: ignore[arg-type]


def test_nonexistent_office_name_returns_empty():
    assert search_offices("Quantum Teleportation Office") == []
    assert lookup_office("Quantum Teleportation Office") is None
    assert format_office_reply("Quantum Teleportation Office") is None


def test_multiple_matches_ranking_and_formatting():
    offices = [
        OfficeRecord(name="International Training Office", location="B2-301"),
        OfficeRecord(name="Training Office", location="A1-201"),
    ]
    results = search_offices("training", offices=offices)
    # 'Training Office' must rank ahead of 'International Training Office' because it is a prefix match
    assert [r.name for r in results] == ["Training Office", "International Training Office"]

    reply = format_office_reply("training", offices=offices)
    assert reply is not None
    assert "Found 2 matching offices:" in reply
    assert "Training Office" in reply
    assert "International Training Office" in reply


def test_load_offices_from_csv_and_fallback(tmp_path: Path):
    csv_file = tmp_path / "offices.csv"
    csv_file.write_text(
        "name,location,email,hours,description\n"
        "Training Office,Room 101,training@example.edu,08:00-17:00,Course registration\n"
        "Financial Aid Office,Room 202,aid@example.edu,08:00-16:30,Scholarships\n",
        encoding="utf-8",
    )
    loaded = load_offices(csv_file)
    assert len(loaded) == 2
    assert lookup_office("financial", csv_path=csv_file).name == "Financial Aid Office"  # type: ignore[union-attr]

    missing_csv = tmp_path / "missing.csv"
    fallback = load_offices(missing_csv)
    assert len(fallback) == len(DEFAULT_OFFICES)


def test_no_change_to_other_dialogue_and_rag_answers():
    """Issue #20 acceptance: no change to other answers (help, greetings, study questions with 'training')."""
    assert dialogue_intent("help", []) == (HELP_MESSAGE, "help")
    assert "Chào bạn" in dialogue_intent("Xin chào", [])[0]  # type: ignore[index]

    # Study questions containing 'training' or 'office' must still pass through to RAG unchanged
    study_query = "What is the difference between training error and test error in machine learning?"
    assert dialogue_intent(study_query, []) == (None, study_query)

    agent = PsychologyAgent(
        searcher=SimpleNamespace(search_hybrid=lambda **kwargs: []),
        ollama=SimpleNamespace(chat_stream=lambda **kwargs: iter([])),
        translator=SimpleNamespace(translate=lambda chunks: {}),
    )
    res = agent.process_query_sync(study_query, embed_model=None)
    assert "chưa tìm thấy đủ căn cứ" in res["answer"]
    assert res["citations"] == []
