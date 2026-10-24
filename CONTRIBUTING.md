# Contributing

Start with a reproducible problem or a clear user outcome. Check existing issues and pull requests before opening new work. Keep each change small enough to review and preserve work already in progress.

## Set up

Follow the [Windows](README_WINDOWS.md) or [macOS](README_MAC.md) guide. Use a separate demo or test data directory. Keep private documents, databases and model files outside the public change.

Before starting from a clean checkout:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c fix/descriptive-name
```

Save local work before switching or updating branches. Use names such as `fix/attachment-source-scope` or `docs/local-setup`; use `codex/` for branches created by Codex. Names and titles should describe the change. Personal role codes, classroom milestones and internal acceptance assignments do not belong in public titles or documentation.

## Make and verify a change

1. Read the relevant implementation, tests and recent changelog entries.
2. Reproduce the defect or define the intended behavior. For behavior changes, add a test that fails for the actual defect before fixing it.
3. Update the implementation and affected documentation. Do not alter expected results to conceal a failure.
4. Run the checks in [Testing](docs/testing.md). Report the environment, commands, results and checks that were not run.
5. Add a concise entry under **Unreleased** in [CHANGELOG.md](CHANGELOG.md). Describe the user-visible result and any compatibility or data migration impact.
6. Review the complete diff, including new files, for private content and unrelated edits.

Documentation-only changes need a link, command and factual review; tests that do not apply may be marked N/A with a reason. Model behavior changes also need real-model evaluation and a separate content review.

## Pull requests

Use a concrete title, for example `fix: keep attachment scope in follow-up questions`. Explain the problem, resulting behavior, validation and remaining limits. Link the issue when relevant; do not close unrelated or assigned work automatically.

Use the [pull request template](.github/pull_request_template.md). A reviewer should be able to understand and reproduce the result without reading a chat or an internal work log. Keep private evidence and operational handoffs out of the PR.

The repository owner merges after review and required checks. Approval and merge permission are separate controls. Do not bypass protection to work around failing tests. See [repository maintenance](docs/repository-maintenance.md). Automated reviewers use [AGENT.md](AGENT.md); the [project agent rule](.agents/rules/study-agent.md) keeps workspace constraints consistent.

## Source and data safety

- Commit application code, synthetic fixtures, tests and public documentation only.
- Keep `.env`, credentials, personal uploads, conversation history, SQLite databases, embeddings, datasets, model weights and private operational reports out of Git. Never force-add ignored assets.
- Preserve original source files. Keep uncertain extraction or OCR explicitly unresolved.
- Use one writer for each canonical index or model operation, and coordinate inference through the existing resource locks.
- Do not change the serving model, activate an adapter or start training as a side effect of a software fix.

Report suspected credential exposure privately to the repository owner and revoke the affected credential promptly. See [Security](SECURITY.md).

## Publish verified changes

After an important change or defect fix, complete the applicable checks, review the diff for private data, commit to the feature branch, push it to origin, and open or update its pull request. This is standing project authorization; contributors and agents should not wait for another reminder. Keep a failing or incomplete change local with its concrete blocker. Do not publish weights, private sources or operational reports. This rule does not authorize merging main, bypassing branch protection, training or adapter activation.
