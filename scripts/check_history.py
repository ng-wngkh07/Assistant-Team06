"""Require a product changelog update and preserve historical reference IDs."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HISTORY = "CHANGELOG.md"
LEGACY_HISTORY = "LICH_SU_DU_AN.md"


def entry_ids(content: str) -> list[str]:
    return re.findall(
        r'^(?:### |history-id: )((?:YC-\d+)|(?:LOG-[A-Za-z0-9_-]+))\b', content, re.M)


def validate(changed: list[str], current: str, previous: str) -> list[str]:
    errors = []
    ids = entry_ids(current)
    old_ids = entry_ids(previous)
    if not ids or len(ids) != len(set(ids)):
        errors.append("History entries must have unique YC-/LOG- identifiers.")
    if not set(old_ids).issubset(ids):
        errors.append("Do not delete or renumber old shared history entries.")
    history_changed = any(name in changed for name in (HISTORY, LEGACY_HISTORY))
    substantive = [name for name in changed if name not in (HISTORY, LEGACY_HISTORY)]
    if re.match(r"^# Changelog\s*$", current.splitlines()[0] if current else ""):
        def changes(text: str) -> set[str]:
            visible = re.sub(r"<!--.*?-->", "", text, flags=re.S)
            return set(re.findall(r"^- (\S.*)$", visible, re.M))
        new_entry = bool(changes(current) - changes(previous))
    else:
        # Accept a historical-format branch while it migrates to CHANGELOG.md.
        new_entry = bool(set(ids) - set(old_ids))
    if substantive and (not history_changed or not new_entry):
        errors.append("Project changes require a product entry in CHANGELOG.md.")
    return errors


def compare_to_base(root: Path, base: str) -> tuple[list[str], str, str]:
    # Include staged and working-tree changes; base...HEAD misses local edits.
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", base], cwd=root,
        text=True, encoding="utf-8").splitlines()
    changed += subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=root,
        text=True, encoding="utf-8").splitlines()
    previous = ""
    for name in (HISTORY, LEGACY_HISTORY):
        old = subprocess.run(["git", "show", f"{base}:{name}"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8")
        if old.returncode == 0:
            previous = old.stdout
            break
    path = root / HISTORY
    if not path.exists():
        path = root / LEGACY_HISTORY
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    return sorted(set(changed)), current, previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="PR base commit or ref")
    args = parser.parse_args()
    changed, current, previous = compare_to_base(ROOT, args.base)
    errors = validate(changed, current, previous)
    if errors:
        print("\n".join(errors))
        return 1
    print("History check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
