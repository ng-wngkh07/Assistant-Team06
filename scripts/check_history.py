"""Require a product changelog update and preserve historical reference IDs."""
import argparse
from pathlib import Path
import re
import subprocess  # nosec B404

ROOT = Path(__file__).resolve().parents[1]


def _run_git(root: Path, *args: str, check: bool = True, errors: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a fixed git CLI subcommand inside `root` without shell interpolation."""
    return subprocess.run(  # nosec B603, B607
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors=errors,
        check=check,
    )
HISTORY = "CHANGELOG.md"
LEGACY_HISTORY = "LICH_SU_DU_AN.md"
PUBLIC_DOC_PATHS = ("docs", "README.md", "CONTRIBUTING.md", "CHANGELOG.md")

_FORBIDDEN_PUBLIC_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal eval directory", re.compile(r"^eval/")),
    ("archived script directory", re.compile(r"^scripts/archive/")),
    ("local packet or scratch directory", re.compile(r"^(?:review_packets|scratch)/")),
    (
        "internal curation/evaluation/training script",
        re.compile(r"^scripts/(?:curate|evaluate|rejudge|prepare_.*training|test_live_).*\.py$"),
    ),
    (
        "ticket/packet/codex-scoped test file sprawl",
        re.compile(r"^tests/test_(?:codex_.*|.*_yc\d+|ui_audit_packet.*)\.py$"),
    ),
    (
        "internal scorecard/comparison/eval/training artifact",
        re.compile(r"(?:scorecard|answer_comparison|model_comparison|eval_report|training_run|account_summary|USER_DATA_SUMMARY)", re.I),
    ),
)

_FORBIDDEN_PUBLIC_DOC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal account summary artifact", re.compile(r"account_summary|USER_DATA_SUMMARY")),
    ("DO_NOT_ACTIVATE verdict", re.compile(r"DO_NOT_ACTIVATE")),
    ("training valid_loss metric", re.compile(r"valid_loss")),
    ("internal swap_delta_mb metric", re.compile(r"swap_delta_mb")),
    ("internal peak_memory_gb metric", re.compile(r"peak_memory_gb")),
    ("internal exact_core metric", re.compile(r"exact_core")),
    ("internal exact_set_match metric", re.compile(r"exact_set_match")),
    ("internal median_ratio_vs_ metric", re.compile(r"median_ratio_vs_")),
    ("private corpus document count", re.compile(r"166-document")),
    ("private corpus chunk count", re.compile(r"59,?688")),
    ("dated internal evaluation folder", re.compile(r"data/evaluation/ktdc-reviews")),
    ("dated internal runtime audit folder", re.compile(r"data/runtime/app-audit-20261024")),
    ("internal protocol probe key", re.compile(r"qa_full_length_backward")),
    ("internal protocol loss key", re.compile(r"qa_loss_equivalence")),
    ("private source excerpt token", re.compile(r"sốthực")),
    ("numbered experiment run label", re.compile(r"\bRun\s+[0-9]+\b")),
)


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
    changed = _run_git(root, "diff", "--name-only", base).stdout.splitlines()
    changed += _run_git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    previous = ""
    for name in (HISTORY, LEGACY_HISTORY):
        old = _run_git(root, "show", f"{base}:{name}", check=False)
        if old.returncode == 0:
            previous = old.stdout
            break
    path = root / HISTORY
    if not path.exists():
        path = root / LEGACY_HISTORY
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    return sorted(set(changed)), current, previous


def validate_public_paths(added_paths: list[str]) -> list[str]:
    """Reject newly added files belonging to internal training, eval, or ticket-sprawl scopes."""
    errors: list[str] = []
    for raw_path in added_paths:
        norm = raw_path.replace("\\", "/").lstrip("./")
        for label, pattern in _FORBIDDEN_PUBLIC_PATH_PATTERNS:
            if pattern.search(norm):
                errors.append(f"Disallowed public path ({label}): {norm}")
                break
    return errors


def validate_public_doc_diff(diff_text: str) -> list[str]:
    """Reject leaked internal training, evaluation, or private-corpus content in public docs."""
    errors: list[str] = []
    current_file = "<unknown>"
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        for label, pattern in _FORBIDDEN_PUBLIC_DOC_PATTERNS:
            if pattern.search(content):
                errors.append(
                    f"Disallowed internal content ({label}) in public doc {current_file}: {content.strip()[:120]}"
                )
                break
    return errors


def collect_public_guard_inputs(root: Path, base: str) -> tuple[list[str], str]:
    """Collect newly added paths and public-doc diff text relative to base."""
    added = _run_git(root, "diff", "--name-only", "--diff-filter=A", base).stdout.splitlines()
    untracked = _run_git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    added_paths = sorted({p for p in (*added, *untracked) if p})

    doc_diff = _run_git(root, "diff", "-U0", base, "--", *PUBLIC_DOC_PATHS, errors="replace").stdout
    extra_blocks: list[str] = []
    for rel in untracked:
        norm = rel.replace("\\", "/")
        if norm.endswith(".md") and (norm.startswith("docs/") or norm in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md")):
            fp = root / rel
            if fp.is_file():
                extra_blocks.append(f"+++ b/{norm}")
                extra_blocks.extend("+" + ln for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines())
    if extra_blocks:
        doc_diff = doc_diff + "\n" + "\n".join(extra_blocks)
    return added_paths, doc_diff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="PR base commit or ref")
    args = parser.parse_args()
    changed, current, previous = compare_to_base(ROOT, args.base)
    errors = validate(changed, current, previous)
    added_paths, doc_diff = collect_public_guard_inputs(ROOT, args.base)
    errors.extend(validate_public_paths(added_paths))
    errors.extend(validate_public_doc_diff(doc_diff))
    if errors:
        print("\n".join(errors))
        return 1
    print("History check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
