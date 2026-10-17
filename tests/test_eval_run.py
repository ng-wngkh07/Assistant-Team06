import subprocess
import sys
from pathlib import Path

import pytest

from eval.run import calculate_accuracy


ROOT_DIR = Path(__file__).resolve().parent.parent
RUN_SCRIPT = ROOT_DIR / "eval" / "run.py"


def test_calculate_accuracy_uses_exact_match_after_trimming(tmp_path):
    cases = tmp_path / "cases.csv"
    cases.write_text(
        "expected,predicted\n"
        "lookup,lookup\n"
        "qa,qa \n"
        "timetable,qa\n",
        encoding="utf-8",
    )

    result = calculate_accuracy(cases)

    assert result == {"correct": 2, "total": 3, "accuracy": 2 / 3}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("input,answer\nx,y\n", "missing required column"),
        ("expected,predicted\n,\n", "empty required value"),
        ("expected,predicted\n", "no benchmark cases"),
        ("expected,predicted\nonly-expected\n", "missing a required value"),
        ("expected,expected,predicted\na,a,a\n", "unique column names"),
        ("expected,predicted\na,a,extra\n", "more values than headers"),
    ],
)
def test_invalid_csv_is_rejected(tmp_path, content, message):
    cases = tmp_path / "cases.csv"
    cases.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        calculate_accuracy(cases)


def test_default_benchmark_runs_from_outside_repository():
    completed = subprocess.run(
        [sys.executable, str(RUN_SCRIPT)],
        cwd=ROOT_DIR.parent,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Accuracy: 3/4 (75.00%)"


def test_cli_reports_invalid_file_without_success_output(tmp_path):
    missing_file = tmp_path / "missing.csv"

    completed = subprocess.run(
        [sys.executable, str(RUN_SCRIPT), str(missing_file)],
        cwd=ROOT_DIR,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "Error:" in completed.stderr
