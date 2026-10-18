import subprocess

from scripts.check_history import compare_to_base, entry_ids, validate


OLD = "### LOG-20261009-qa-first\nFirst change\n"
NEW = OLD + "### LOG-20261010-lookup-fix\nFixed lookup and tested\n"


def test_code_change_without_log_is_rejected():
    assert validate(["app/server.py"], OLD, OLD)


def test_editing_only_old_log_is_not_a_new_entry():
    assert validate(["app/server.py", "LICH_SU_DU_AN.md"], OLD + "text", OLD)


def test_new_log_with_code_change_is_accepted():
    assert validate(["app/server.py", "LICH_SU_DU_AN.md"], NEW, OLD) == []


def test_old_entries_cannot_be_removed_or_ids_duplicated():
    assert validate(["LICH_SU_DU_AN.md"], NEW + OLD, OLD)
    assert validate(["LICH_SU_DU_AN.md"], "### LOG-new\n", OLD)


def test_changelog_migration_keeps_legacy_references_without_public_activity_log():
    migrated = "# Changelog\n\n## Unreleased\n\n- Fix citations.\n\n<!--\nhistory-id: LOG-20261009-qa-first\nhistory-id: YC-229\n-->\n"
    assert validate(["CHANGELOG.md", "LICH_SU_DU_AN.md", "app/server.py"], migrated, OLD) == []
    assert validate(["CHANGELOG.md"], migrated.replace("history-id: LOG-20261009-qa-first\n", ""), OLD)
    assert entry_ids(migrated) == ["LOG-20261009-qa-first", "YC-229"]


def test_base_comparison_detects_uncommitted_code_and_untracked_changelog(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True)
    git("init", "-q")
    (tmp_path / "LICH_SU_DU_AN.md").write_text(OLD)
    (tmp_path / "app.py").write_text("old\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    (tmp_path / "app.py").write_text("new\n")
    changed, current, previous = compare_to_base(tmp_path, "HEAD")
    assert changed == ["app.py"]
    assert validate(changed, current, previous)
    (tmp_path / "CHANGELOG.md").write_text(NEW)
    changed, current, previous = compare_to_base(tmp_path, "HEAD")
    assert "CHANGELOG.md" in changed
    assert validate(changed, current, previous) == []


def test_new_product_entry_needs_no_internal_assignment_identifier():
    old = "# Changelog\n\n## Unreleased\n\n- Existing change.\n<!--\nhistory-id: YC-229\n-->\n"
    new = old.replace("- Existing change.", "- Existing change.\n- Keep attachment scope in follow-up questions.")
    assert validate(["CHANGELOG.md", "app/server.py"], new, old) == []
    metadata_only = old.replace("-->\n", "history-id: YC-230\n-->\n")
    assert validate(["CHANGELOG.md", "app/server.py"], metadata_only, old)
