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

Operator recovery controller checks (`tests/test_backend_service_controller.py`), hosted UI error-copy tests (`tests/hosted_error_ui.test.cjs`), and 20-user scaling/HTTPS security tests (`tests/test_multiuser_scaling_and_security.py`) run strictly against disposable temporary directories, synthetic SQLite fixtures, and isolated ports without model execution or mutations to private seeds. POSIX supervisor lifecycle subtests in `tests/test_backend_service_controller.py` are skipped on `win32` while template/infrastructure posture checks run across platforms.

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

- **Windows (`Start Study Agent.bat`)**: Checks for `.venv\Scripts\python.exe`, then runs `scripts/dev.py serve --profile local --start-model-service --require-model --open-browser`. The Python launcher reads configured `AGENT_SRC_DIR` / `AGENT_DATA_DIR` first, starts an installed local Ollama daemon if needed, and waits up to 15 seconds at the configured loopback endpoint before checking the configured library and model without implicit pack seeding (explicit `--install-pack` requires both `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256`). It does not download models. If startup fails, the batch wrapper pauses to preserve error output.
- **macOS (`Start Study Agent.command`)**: Checks for `.venv/bin/python` and launches `scripts/dev.py serve --profile local --require-model --open-browser`. It requires that any conflicting process holding port 8000 be stopped prior to launch; it will not arbitrarily terminate foreign processes.

## Documentation and deployment checks

For workflow/usage changes, apply the [same-change documentation rule](documentation.md#required-documentation-updates). Review README, CONTRIBUTING, both local platform guides and every affected business guide. Check relative file links and heading anchors, examples, endpoint names, environment loader behavior and the documentation index against source. A link checker cannot certify factual accuracy. Documentation-only edits need content/command review and diff checks; do not invent behavioral tests that repeat prose.

Exercise account isolation, login/logout, permitted original access, upload persistence and disposable backup/restore through the actual configured service. For shared-source ingestion, verify the default-off behavior, automatic new-file indexing without a manual call, duplicate stability, modified/missing-source blocking, review preservation and explicit recovery. Native embedding checks must exercise the real worker's active shutdown/cancellation and prove no owned process survives. Unit tests and deliberate fault injection supplement these live gates and must be labelled accordingly.

The optional `test_src_e2e_06_isolated_real_six_pdfs_reconcile` test in `tests/test_src_real_six_e2e.py` skips cleanly by default in portable runs without personal host paths. To exercise it locally against a private 6-PDF directory via isolated temporary copies across 1,411 physical pages without modifying originals, opt in explicitly:

```sh
STUDY_TEST_REAL_SIX_DIR=/path/to/six-pdfs pytest -c pytest.windows.ini -rs tests/test_src_real_six_e2e.py
```

Treat a skipped portable check as unverified for the real corpus rather than full corpus acceptance.

## Test-file hygiene and cleanup

- **Consolidate into existing domain suites:** Add new regression tests to the existing domain test file under `tests/` (for example `tests/test_practice_adapter.py`, `tests/test_study_materials.py`, `tests/test_native_mlx_contract.py`, `tests/test_grounded_citations.py`, `tests/test_timetable_feature.py`, or `tests/test_audit_regressions.py`) instead of creating a new `test_<ticket>.py` module per change.
- **No tracked one-off scripts:** Never save single-use probe, benchmark, or inspection scripts in `tests/`, `scripts/`, or the repository root. Execute disposable scripts inside an ephemeral system temp directory (`mktemp -d` or `tempfile.TemporaryDirectory()`) and delete them immediately after reading their output.
- **Post-run workspace cleanup:** After completing verification, remove `.pytest_cache/`, stray `__pycache__/`, temporary databases, and superseded multi-gigabyte shadow/trial artifacts while preserving active serving databases, `src/`, `data/models/`, and every active source-page review asset. `python scripts/check_history.py --base origin/main` rejects newly introduced internal evaluation/training/ticket-sprawl file paths.

Use separate disposable source/account/data paths. Check concurrent startup, foreign-port safety and recovery through any published launcher. Do not stop another process or replace a serving database to make a test pass. Distinguish code publication, UI/API behavior, actual native model quality, complete source/privacy review and deployed HTTPS acceptance.

## Real-model acceptance

### Live application gate

Confirm newly reported release defects through the actual application and its real configured source/data paths. A failure in an isolated harness is a diagnostic lead until the affected application flow reproduces it. Record the exact source, physical page, request and visible result without publishing private data. After completing source review and repairs, freeze the code, configuration and release artifacts for one consolidated final functional acceptance run. Record each required flow as passed, failed or unrun. A failed run requires repair and renewed verification of affected flows; it cannot be relabeled successful to preserve a one-run count.

For whole-application acceptance, exercise all three primary flows.

1. **Document QA.** Check the answer, its citations and whether the cited original supports its claims and conditions. Include a case with insufficient evidence.
2. **Review-question generation.** Check the requested count and difficulty. For each generated question, verify the answer and explanation against the cited material. For multiple-choice questions, verify that exactly one choice is correct.
3. **Timetable creation and study planning.** Check recognized days and times against the source, then verify the user-visible plan, confirmation and calendar export where applicable.

Run one flow at a time and one case at a time, with only one acceptance test request in flight. Use the existing resource coordinator and wait for the request and any work it started to finish or be cleaned up before submitting another. For each case, record end-to-end latency, including timeouts, and review the visible output against its expected result and source evidence before moving on. Record an outcome for each case and each flow.

After automated checks, restart the actual project server when needed and verify its process, working directory and revision/file hashes. Open the served application in a real browser and exercise the changed controls through completion: model readiness, QA, source links, quiz count and difficulty, answers, feedback and visible failure/retry states. Inspect the browser result and the server response/logs together. Record screenshots and request/result evidence in ignored local storage; keep private passages out of Git. Verify generated content separately against the source. API-only requests, mocked inference and isolated fixtures supplement this gate but do not replace it. Record unavailable platforms and unrun flows as pending, not passed.

Before declaring push or demo readiness, record the actual checkout/install path, browser URL, serving process and final source hashes. Exercise fresh-clone startup when clone usability is part of the change. Relevant changes after a successful run require repeating the affected user flows. Do not substitute an old deployment, isolated mock or direct API response for the final served UI.

The health endpoint distinguishes installed artifacts from executable runtime capability. Its import probe does not load weights or certify content. Use **Kiểm tra mô hình** in the web UI for a real inference smoke check, then test an actual question with source review.

### Whole-application regression cases

Run this set after each change to application behavior, including a fix scoped to one flow or a change to prompts, retrieval or model configuration. Add a direct regression for the reported defect or new behavior. Add cases for any affected shared paths, such as account isolation, source access, persistence or model resource coordination. Keep the change scoped to the assigned outcome and its dependencies.

Use the served web UI, a disposable test account or store, the configured local models, and the public inputs below or equivalent permitted inputs with independently reviewed expected results. Index the public document fixtures in the test library and prepare the remaining inputs before the run. Run cases in the listed order with one test request in flight. A case can include multiple actions. Wait for each action and its work to settle before continuing. If a prerequisite is unavailable, record the case as unrun with its reason.

| Case | Steps | Expected result |
| --- | --- | --- |
| QA-01 · Grounded answer | Attach `extended_probability.md` from the public document fixtures. Submit the question in `probability-worked` from [extended_cases.json](../tests/fixtures/extended_cases.json), then open the citation in the answer. | The answer gives 0.4, preserves the condition that P(B) is positive, and opens the supporting worked answer in the selected original. |
| QA-02 · Insufficient evidence | In a new chat with the same source attached, submit the question in `probability-missing`. | The response says the material lacks the exam schedule and instructor. It supplies neither invented values nor an unrelated source. |
| REVIEW-01 · Generate and grade | In the library, choose [On_Tap_Hoa_Hoc_12.docx](../tests/fixtures/documents/On_Tap_Hoa_Hoc_12.docx). Request two easy quiz questions and one flashcard using **Tạo Quiz & Thẻ**. Open the quiz, submit answers, and inspect the feedback and the flashcard. | The counts match the request. Each quiz question has one correct choice and an explanation supported by the document. The app grades the submitted choices accordingly, and the flashcard answer is supported by the same source. |
| REVIEW-02 · Unusable input | In the review tool, use **Tải tệp mới lên** to upload a new `insufficient.txt` containing only a period. | The app reports that it cannot read enough material, clears any loading state, and creates no new questions. |
| TIME-01 · Recognize and export | Upload [demo.png](../tests/fixtures/timetables/demo.png). Compare the draft with [expected.json](../tests/fixtures/timetables/expected.json), confirm it, and export an iCalendar file for two full future Monday–Sunday weeks. | The draft has one row, DEMO MATH on Monday from 09:00 to 10:00 in A101. The confirmed view matches the draft. The calendar has one weekly event definition and two occurrences at those local times. |
| TIME-02 · Reject an incomplete draft | With the confirmed schedule from TIME-01, open a new draft from the same image, clear the row's end time, and attempt confirmation. | The app flags the incomplete row and retains the last confirmed timetable. An export from the saved schedule still contains the original event. |
| TIME-03 · Confirm a study plan | Discard the incomplete draft. Add a 30-minute assignment for DEMO MATH due two days from now. Set a 60-minute study window before the deadline, clear of classes, commitments and protected rest time. Generate a plan, review it, confirm it, and reload the app. | The plan allocates 30 minutes to the assignment within the eligible window before its deadline with no overlap with classes or commitments. The accepted sessions remain visible after reload. |

For each case, record the final revision and settings, case ID, input, expected and observed result, and elapsed time per request. Record source review and whether the case passed, failed or was unrun. Save evidence privately. A flow passes only when all of its required cases pass. Whole-application acceptance requires passing results for all three flows and the change-specific cases. After a repair, verify the regression and rerun the set against the final candidate.

### Retrieval and generation changes

For changes to retrieval, prompts, evidence validation or generated study content:

1. Freeze synthetic or permitted holdout sources and questions before the run. Include definitions, formulas, conditions, follow-ups, comparisons, missing evidence and document-injection cases.
2. Record the application revision or file hashes, model/runtime identity, context, decoding settings and source hashes in a private evidence bundle.
3. Use the existing model and shared resource locks. Store test databases and histories separately from serving data.
4. Check response states, generation counts, source membership and request replay.
5. Review answer content against the original passage. Check mathematical conditions, units, attribution, quiz alternatives and whether a flashcard can be answered from its source.
6. Keep failed runs and explain the fix. Mark unrun checks explicitly; never turn structural checks into a semantic PASS.

`scripts/evaluate_qa_core.py` is an opt-in local harness. It requires an isolated output directory and the existing coordinator data directory for locks. Its structural result requires a separate content review.

A small synthetic suite is a software regression gate. Course-level acceptance needs independently reviewed questions and sources, representative learners, a defined scoring rubric and measured failure rates. A CSV comparison of prefilled predictions does not call a model and is not an application accuracy benchmark.
