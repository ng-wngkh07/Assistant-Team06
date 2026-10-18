# Testing and acceptance

Use temporary data or synthetic fixtures for automated tests. Never point the portable suite at a serving knowledge base.

## Regression checks

From the repository root, using the project Python environment:

```bash
python -m pip check
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
