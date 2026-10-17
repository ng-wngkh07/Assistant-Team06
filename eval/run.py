"""Score precomputed benchmark predictions from a CSV file."""

import argparse
import csv
import sys
from pathlib import Path


DEFAULT_CASES_PATH = Path(__file__).resolve().parent / "cases.csv"
REQUIRED_COLUMNS = {"expected", "predicted"}


def calculate_accuracy(cases_path: Path) -> dict[str, int | float]:
    """Return exact-match accuracy for non-empty expected/predicted CSV values."""
    with cases_path.open(encoding="utf-8-sig", newline="") as cases_file:
        reader = csv.DictReader(cases_file)
        fieldnames = reader.fieldnames
        if not fieldnames or len(fieldnames) != len(set(fieldnames)):
            raise ValueError("CSV must have a header row with unique column names")
        missing_columns = REQUIRED_COLUMNS - set(fieldnames)
        if missing_columns:
            raise ValueError(
                "CSV is missing required column(s): "
                + ", ".join(sorted(missing_columns))
            )

        total = 0
        correct = 0
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"CSV row {row_number} has more values than headers")
            expected = row["expected"]
            predicted = row["predicted"]
            if expected is None or predicted is None:
                raise ValueError(f"CSV row {row_number} is missing a required value")
            expected = expected.strip()
            predicted = predicted.strip()
            if not expected or not predicted:
                raise ValueError(f"CSV row {row_number} has an empty required value")

            total += 1
            correct += expected == predicted

    if total == 0:
        raise ValueError("CSV contains no benchmark cases")

    return {
        "correct": correct,
        "total": total,
        "accuracy": correct / total,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print exact-match accuracy for predictions in a benchmark CSV."
    )
    parser.add_argument(
        "cases",
        nargs="?",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"CSV file to score (default: {DEFAULT_CASES_PATH})",
    )
    args = parser.parse_args(argv)

    try:
        result = calculate_accuracy(args.cases)
    except (OSError, csv.Error, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    percentage = result["accuracy"] * 100
    print(
        f"Accuracy: {result['correct']}/{result['total']} "
        f"({percentage:.2f}%)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
