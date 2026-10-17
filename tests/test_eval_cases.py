import csv
from pathlib import Path


CASES_PATH = Path(__file__).resolve().parent.parent / "eval" / "cases.csv"
EXPECTED_COLUMNS = ["question", "expected", "category"]
ALLOWED_CATEGORIES = {"normal", "edge", "out-of-scope"}


def test_benchmark_cases_have_expected_schema_and_coverage():
    with CASES_PATH.open(encoding="utf-8-sig", newline="") as cases_file:
        reader = csv.DictReader(cases_file)
        assert reader.fieldnames == EXPECTED_COLUMNS
        cases = list(reader)

    assert len(cases) == 15
    assert all(
        case["question"].strip() and case["expected"].strip()
        for case in cases
    )
    assert all(case["category"] in ALLOWED_CATEGORIES for case in cases)
    assert {case["category"] for case in cases} == ALLOWED_CATEGORIES
    assert sum(case["category"] == "edge" for case in cases) >= 3
    assert len({case["question"] for case in cases}) == len(cases)
