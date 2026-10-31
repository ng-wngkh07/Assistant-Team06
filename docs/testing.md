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

Mocked native answer tests opt in to temporary context metadata and explicit model-client fakes; CI does not need private weights. In-process worker tests restore their socket and offline-environment guards. Native curation requires POSIX locks and is skipped on Windows, where training commands are unsupported. Calendar timezone data is supplied by the pinned `tzdata` package on hosts without a system IANA database.

Optional adapter checks use `ui/requirements.txt` and `tests/test_streamlit_flow.py`; this separate client does not replace the primary web UI.

## CI security workflows

GitHub Actions runs the following automated security workflows:

- **Bandit** (`.github/workflows/codacy.yml`, historical filename): Scans every tracked Python file, including tests, with `bandit[sarif]==1.9.4`. Its official SARIF formatter preserves native LOW/note, MEDIUM/warning and HIGH/error levels; no findings or rules are filtered. Execution failures, incomplete analysis and parse errors fail the workflow. This replaces the legacy Codacy wrapper that classified LOW test assertions as error-level alerts. Findings still require review; a green workflow is not a clean security audit.
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
- **Physical page review release gate**: `app/knowledge_pack.py` and `scripts/build_distribution.py` enforce complete source-bound per-physical-page review coverage (`--page-reviews`) for every declared document during export. The exporter reconciles expected production chunk projections against database passages, rejects out-of-range physical page membership and orphaned database chunks, validates blank page declarations (0 chunks, canonical empty digest), and exports portable review coverage metadata in `manifest.json`. Installer and application distribution builder enforce contiguous canonical 0-based chunk indexing (`0..N-1`), verify passage digests over the installed database sequence (`ORDER BY chunk_index, id`), and reject packs missing portable review coverage, containing incomplete reviews, or presenting mismatched passage digests.
- **Historical C++ review tool**: `scripts/release_string_reviews.py` now reads `string-transcripts.json` and `string-rows.json` from the ignored directory `data/evaluation/corpus-review-20261005/all-remaining-20261005`. Place reviewed inputs there explicitly; there is no shared `/tmp` fallback. This manual tool compiles and executes source from its inputs, so it requires operator-trusted files and is excluded from automated runtime validation.

## Public debugging corpus

The [extended fixture guide](../tests/fixtures/EXTENDED_CORPUS.md) describes 12 synthetic topics, 96 review cases and a 14-course timetable PNG/JSON pair. Portable tests index these sources in temporary databases and check timetable draft/confirmation/export behavior. The case manifest contains content rubrics and simple/complex labels; it is not a precomputed model benchmark or approved training set. Image extraction quality and actual model answers require separate live evaluation and source review.

## Launchers and service startup

Platform launch scripts provide single-action startup for development and manual verification:

- **Windows (`Start Study Agent.bat`)**: Checks for `.venv\Scripts\python.exe`, then runs `scripts/dev.py serve --profile local --start-model-service --require-model --open-browser`. The Python launcher reads the configuration first, starts an installed local Ollama daemon if needed, and waits up to 15 seconds at the configured loopback endpoint before verifying the pack and model. It does not download models. If startup fails, the batch wrapper pauses to preserve error output.
- **macOS (`Start Study Agent.command`)**: Checks for `.venv/bin/python` and launches `scripts/dev.py serve --profile local --require-model --open-browser`. It requires that any conflicting process holding port 8000 be stopped prior to launch; it will not arbitrarily terminate foreign processes.

## Real-model acceptance

### Live application gate

Confirm newly reported release defects through the actual application and its real configured source/data paths. A failure in an isolated harness is a diagnostic lead until the affected application flow reproduces it. Record the exact source, physical page, request and visible result without publishing private data. After completing source review and repairs, freeze the code, configuration and release artifacts for one consolidated final functional acceptance run. Record each required flow as passed, failed or unrun. A failed run requires repair and renewed verification of affected flows; it cannot be relabeled successful to preserve a one-run count.

After automated checks, restart the actual project server when needed and verify its process, working directory and revision/file hashes. Open the served application in a real browser and exercise the changed controls through completion: model readiness, QA, source links, quiz count and difficulty, answers, feedback and visible failure/retry states. Inspect the browser result and the server response/logs together. Record screenshots and request/result evidence in ignored local storage; keep private passages out of Git. Verify generated content separately against the source. API-only requests, mocked inference and isolated fixtures supplement this gate but do not replace it. Record unavailable platforms and unrun flows as pending, not passed.

Before declaring push or demo readiness, record the actual checkout/install path, browser URL, serving process and final source hashes. Exercise fresh-clone startup when clone usability is part of the change. Relevant changes after a successful run require repeating the affected user flows. Do not substitute an old deployment, isolated mock or direct API response for the final served UI.

The health endpoint distinguishes installed artifacts from executable runtime capability. Its import probe does not load weights or certify content. Use **Kiểm tra mô hình** in the web UI for a real inference smoke check, then test an actual question with source review.

For changes to retrieval, prompts, evidence validation or generated study content:

1. Freeze synthetic or permitted holdout sources and questions before the run. Include definitions, formulas, conditions, follow-ups, comparisons, missing evidence and document-injection cases.
2. Record the application revision or file hashes, model/runtime identity, context, decoding settings and source hashes in a private evidence bundle.
3. Use the existing model and shared resource locks. Store test databases and histories separately from serving data.
4. Check response states, generation counts, source membership and request replay.
5. Review answer content against the original passage. Check mathematical conditions, units, attribution, quiz alternatives and whether a flashcard can be answered from its source.
6. Keep failed runs and explain the fix. Mark unrun checks explicitly; never turn structural checks into a semantic PASS.

`scripts/evaluate_qa_core.py` is an opt-in local harness. It requires an isolated output directory and the existing coordinator data directory for locks. Its structural result requires a separate content review.

A small synthetic suite is a software regression gate. Course-level acceptance needs independently reviewed questions and sources, representative learners, a defined scoring rubric and measured failure rates. A CSV comparison of prefilled predictions does not call a model and is not an application accuracy benchmark.
