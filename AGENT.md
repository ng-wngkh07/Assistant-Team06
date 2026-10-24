# Automated review guidance

Review changes against the actual repository state, current callers and [Contributing](CONTRIBUTING.md). Report concrete defects with a reproduction scenario and affected file/line. Distinguish existing defects from regressions.

## Code review rules

1. Keep secrets, private documents, databases, embeddings, datasets, weights and operational reports out of Git. Do not force-add ignored files or modify source PDFs.
2. Preserve document/page scope and real claim-to-source attribution. Do not fabricate citations, answers or OCR text. Keep unresolved source states explicit.
3. Update `CHANGELOG.md` with product changes. Preserve historical reference identifiers during documentation migration; never copy prompts or personal activity logs into the changelog.
4. Keep `static/` as the primary UI and `app/` as the single backend. Optional clients do not replace these boundaries. Coordinate local model work through existing resource locks.
5. Tests must isolate source, history and model boundaries. Do not change expected values to hide defects. Model-facing changes need real-model evidence and a separate content review.
6. One writer owns a file scope at a time. Verify release of ownership before a handoff or work on an isolated snapshot.

## Publication after verified work

The project owner authorizes committing and pushing important changes and defect fixes to a feature branch after applicable verification and privacy review. Open or update the pull request without waiting for another reminder. Report the pushed revision and remaining limits. Incomplete or failing changes remain local with their blocker. This is not permission to merge main, change protections, publish private data, train, or activate adapters.

## Required checks

```bash
python -m pytest -c pytest.windows.ini -rs
python scripts/dev.py check --profile local
node --test tests/*.test.cjs
python scripts/check_history.py --base origin/main
```

Record OS, Python, revision, skipped checks and limits. A green suite does not establish corpus completeness or model acceptance. Branch publication requires the applicable verification and privacy review above; training and merging need their own authorization. Do not claim that this file alone activates an external review integration.

## Local model execution only

Inference, embeddings, OCR/vision, dataset generation, evaluation and training must run on local models and local files. External model APIs, cloud teachers and automatic cloud fallback are prohibited. Missing artifacts or unavailable local capability must produce an explicit unavailable/HOLD result. Researching public documentation is separate from product model execution. Upload persistence and retrieval do not authorize fine-tuning or adapter activation; unreviewed sources remain excluded from training.
