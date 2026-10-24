# Testing and acceptance

Use temporary data or synthetic fixtures for automated tests. Never point the portable suite at a serving knowledge base.

## Regression checks

From the repository root, using the project Python environment:

```bash
python -m pip check
python -m compileall -q app
python -m pytest -c pytest.windows.ini -rs
python scripts/dev.py check --profile local
node --test tests/*.test.cjs
python scripts/check_history.py --base origin/main
git diff --check
```

The Node shell glob works in POSIX shells. In PowerShell, enumerate files explicitly:

```powershell
node --test (Get-ChildItem tests\*.test.cjs).FullName
```

Python 3.12 on Windows and macOS is the CI dependency target. A local run on a different Python version is additional evidence, not a substitute for those CI jobs. Record skipped checks and their reasons. An environment check confirms routes and runtime availability, not answer quality.

Optional adapter checks use `ui/requirements.txt` and `tests/test_streamlit_flow.py`; this separate client does not replace the primary web UI.

## CI security workflows

GitHub Actions runs three automated security workflows:

- **Actionlint** (`.github/workflows/actionlint.yml`): Validates GitHub Actions workflow syntax and ShellCheck rules on pull requests to `main`, pushes to `main`, and manual dispatch. Run locally with `actionlint`.
- **Dependency Review** (`.github/workflows/dependency-review.yml`): Fails the check when a pull request to `main` introduces dependencies with moderate or higher severity vulnerabilities across runtime, development, and unknown scopes. The repository dependency graph must be enabled for this check to run.
- **Gitleaks** (`.github/workflows/gitleaks.yml`): Scans the repository git history on pull requests to `main`, pushes to `main`, and manual dispatch to detect committed secrets. Run locally with `gitleaks git --redact --no-banner`.

These checks do not become required merge checks automatically. The repository owner manages required checks through the branch rules.

## Security hardening and validation

The security regression suite uses synthetic Office files, tokenizer stubs and temporary run records. It verifies these boundaries:

- **Office XML**: DOCX document XML and the PPTX presentation, relationships, slides and notes used by extraction are parsed with `defusedxml.ElementTree` and `forbid_dtd=True`. DTDs and entities are rejected, including UTF-16 input. Any extraction failure discards partial pages and character counts. Ordinary XML escaping, Unicode, slide order and notes remain supported.
- **Tokenizer auditing**: `app/token_auditor.py` loads built-in tokenizers with `trust_remote_code=False` and `local_files_only=True`. Install the required tokenizer files locally; tokenizers requiring custom Python code are rejected.
- **Run ledger**: Duplicate checks block unreadable JSON and invalid required guard fields, even when remediation is supplied. Valid runs stopped before manifest binding may retain a null signature with a `pending_preflight` dataset. Running or successful runs require a signature. Repair invalid records from retained evidence before retrying; the check does not delete history.
- **Acceptance gates**: Dataset preparation, candidate rejudging, source review and diagnostic tools raise explicit exceptions so their checks remain active under `python -O`. Optimization tests execute only isolated guard statements, without running dataset-producing scripts.
- **Physical source pages**: Page validation raises `ValueError` when an existing source cannot be parsed or its physical page count cannot be determined. Form validation without a source file retains its existing behavior.
- **Historical C++ review tool**: `scripts/release_string_reviews.py` now reads `string-transcripts.json` and `string-rows.json` from the ignored directory `data/evaluation/corpus-review-20261005/all-remaining-20261005`. Place reviewed inputs there explicitly; there is no shared `/tmp` fallback. This manual tool compiles and executes source from its inputs, so it requires operator-trusted files and is excluded from automated runtime validation.

## Public debugging corpus

The [extended fixture guide](../tests/fixtures/EXTENDED_CORPUS.md) describes 12 synthetic topics, 96 review cases and a 14-course timetable PNG/JSON pair. Portable tests index these sources in temporary databases and check timetable draft/confirmation/export behavior. The case manifest contains content rubrics and simple/complex labels; it is not a precomputed model benchmark or approved training set. Image extraction quality and actual model answers require separate live evaluation and source review.

## Real-model acceptance

For changes to retrieval, prompts, evidence validation or generated study content:

1. Freeze synthetic or permitted holdout sources and questions before the run. Include definitions, formulas, conditions, follow-ups, comparisons, missing evidence and document-injection cases.
2. Record the application revision or file hashes, model/runtime identity, context, decoding settings and source hashes in a private evidence bundle.
3. Use the existing model and shared resource locks. Store test databases and histories separately from serving data.
4. Check response states, generation counts, source membership and request replay.
5. Review answer content against the original passage. Check mathematical conditions, units, attribution, quiz alternatives and whether a flashcard can be answered from its source.
6. Keep failed runs and explain the fix. Mark unrun checks explicitly; never turn structural checks into a semantic PASS.

`scripts/evaluate_qa_core.py` is an opt-in local harness. It requires an isolated output directory and the existing coordinator data directory for locks. Its structural result requires a separate content review.

A small synthetic suite is a software regression gate. Course-level acceptance needs independently reviewed questions and sources, representative learners, a defined scoring rubric and measured failure rates. A CSV comparison of prefilled predictions does not call a model and is not an application accuracy benchmark.
