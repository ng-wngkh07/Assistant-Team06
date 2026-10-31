# Automated review guidance

Review changes against the actual repository state, current callers and [Contributing](CONTRIBUTING.md). Report concrete defects with a reproduction scenario and affected file/line. Distinguish existing defects from regressions.

## Code review rules

1. Keep secrets, private documents, databases, embeddings, datasets, weights and operational reports out of Git. Do not force-add ignored files or modify source PDFs.
2. Preserve document/page scope and real claim-to-source attribution. Do not fabricate citations, answers or OCR text. Keep unresolved source states explicit.
3. Update `CHANGELOG.md` with product changes. Preserve historical reference identifiers during documentation migration; never copy prompts or personal activity logs into the changelog.
4. Keep `static/` as the primary UI and `app/` as the single backend. Optional clients do not replace these boundaries. Coordinate local model work through existing resource locks.
5. Tests must isolate source, history and model boundaries. Do not change expected values to hide defects. Model-facing changes need real-model evidence and a separate content review.
6. One writer owns a file scope at a time. Verify release of ownership before a handoff or work on an isolated snapshot.
7. Verify changed user flows through the actual served web UI and configured local model on the target machine. Record the serving process/revision, request settings, visible result and source review. Isolated, mocked or API-only tests cannot replace this gate. If the target platform is unavailable, report that limit and leave its acceptance pending.
8. Before push or demo readiness, verify the actual installation/checkout path, browser URL and serving process against the final files. Exercise fresh-clone startup when clone usability is in scope. Changes after acceptance invalidate affected runtime evidence.
9. Model and data migrations follow the current contract. Retire obsolete execution paths and incompatible derived data when authorized; preserve original sources and valid privacy/source gates. Compare embeddings in isolation before selection, with unchanged chunks, predeclared source-backed queries, full-text coverage, latency and memory. Never mix incompatible vector bases or keep obsolete tests solely to preserve old behavior.
10. Confirm every newly reported release defect in the actual application using the real checkout/install, configured data paths and affected user flow before treating it as a product defect. An isolated harness failure alone is diagnostic evidence. Preserve private user data during reproduction. Once page review and fixes are complete, freeze the candidate and perform one consolidated final functional acceptance run; record every function's actual result. If that run fails, fix the reproduced failure and recheck affected flows; never conceal failure to keep a one-run label.

## Publication after verified work

The project owner authorizes committing and pushing important changes and defect fixes to a feature branch after applicable verification and privacy review. Open or update the pull request without waiting for another reminder. Report the pushed revision and remaining limits. Incomplete or failing changes remain local with their blocker. This is not permission to merge main, change protections, publish private data, train, or activate adapters.

## Production release requirements

These requirements apply to releases intended for end users. Record each requirement's evidence and PASS, FAIL, BLOCKED or NOT_RUN status in the local checkpoint. A release request covers the real learning library and accepted model behavior; synthetic fixtures support testing and cannot substitute for that deliverable.

1. **REL-01 — Complete real library.** Reconcile the release inventory against every real learning document in the publisher's source library, including explicitly identified exclusions and unresolved items. Provide originals, faithful passages and compatible precomputed retrieval indexes/vectors for the complete declared scope. Do not silently drop documents, substitute sample knowledge, or describe a partial pack as the full library. Unresolved permission, privacy, extraction or source-quality issues remain release blockers rather than implicit scope reductions. Any narrower release requires an explicit owner decision and accurate coverage documentation.
2. **REL-02 — Usable source verification.** From an actual answer, the recipient can open the cited original, reach the cited page or reading section, inspect the supporting passage and download the original. Preserve conditions, formulas, units and source identity. Test these actions from the installed distribution; publisher-only absolute paths and unavailable local files do not satisfy this requirement. Omitting citations or providing inaccessible links does not resolve missing source delivery.
3. **REL-03 — Ready initial retrieval.** Bundle the originals and precomputed index in a versioned, integrity-checked knowledge pack delivered with the application or through a verified installation step. The end user starts asking questions without manually locating files, indexing, embedding or training the initial library. Check artifact hashes, source coverage and exact embedding compatibility. Document required runtime/model prerequisites accurately; a knowledge pack does not itself provide the model runtime.
4. **REL-04 — Preserve the accepted model.** Identify and verify the exact base artifact/revision, backend, tokenizer, accepted adapter if present, prompts, reasoning settings, context limits and decoding configuration used for acceptance. Deliver or pin those artifacts and settings through a reproducible installation path, subject to their distribution permissions. Confirm the installed runtime identity and perform real-model source/content regression checks. Do not silently substitute models, drop an accepted adapter, shorten context, or change reasoning behavior. Report an unadapted base as unadapted; retrieval access and a model's name are not evidence of fine-tuning on the corpus. Training or activating an adapter still requires separate authorization and gates.
5. **REL-05 — Persistent personal library.** Give every installation a stable writable directory for personal originals and persistent retrieval/history/calendar storage, separate from the publisher's seed pack. The UI identifies the actual location. Uploaded files persist and become available for retrieval through automatic local processing; test retrieval and source access after restart. Updates must preserve personal data. Merely copying a file into a folder is not automatic ingestion unless a verified watcher implements that behavior. Personal uploads do not automatically become public or training data.
6. **REL-06 — Publication and privacy boundaries.** Keep private originals, private extracted passages/vectors, histories, credentials, raw training datasets, checkpoints and local operational evidence out of public Git and release assets. Only distribute source-derived content with recorded redistribution permission and completed privacy/source review. Source attribution, an owner's approval of scope, successful indexing, or moving files to a ZIP, release asset or server does not establish that permission. Reviewed public originals and indexes may be delivered as separate knowledge-pack assets under their own terms; that does not authorize exporting the private working database. An accepted model artifact requires its own distribution and privacy review, separate from permission to publish training records.
7. **REL-07 — Verify the installed product.** Exercise fresh installation, initial startup without corpus rebuilding, real-model QA across the declared subjects, complete source opening/downloading, question count and difficulty, personal upload persistence and restart against the final distribution. Record platform, final code/artifact identities and source/content review. Keep software tests, model quality, corpus completeness, privacy, platform support and actual publication as separate results. A green suite or a successful synthetic demo cannot establish production acceptance. Missing mandatory evidence leaves the release incomplete; report the concrete blocker without lowering these requirements.

The release checklist in [Definition of done](DEFINITION_OF_DONE.md#production-release-acceptance) applies these requirements. Public documentation describes verified behavior and coverage; private inventories and raw review evidence stay in ignored local storage.

### Complete page content review

REL-01 and REL-06 require a page-by-page review of every physical page or slide in every declared original. Maintain a hash-bound coverage ledger that records the document, physical page or slide, extraction revision, reviewed content, evidence and unresolved issues. Reuse existing complete reviews only while their source and evidence bindings remain valid. Review the remaining pages, including pages with no extraction warnings; native extraction, valid vectors and an earlier database status do not establish content completeness.

Compare extracted text with the visible original, including formulas, units, conditions, table relationships, code literals and ordering, captions, diagrams and handwriting. Distinguish faithful transcription from whether a statement in the source is factually correct. Preserve exercise errors as authored exercise content, and keep reviewer explanations separate from source evidence. Record blank pages explicitly. An unreadable, missing, clipped or ambiguous region stays partial/HOLD; never fill it from general knowledge or another document without identifying that alternative source separately.

Apply accepted extraction repairs in an isolated shadow. Recompute only invalidated retrieval data, verify complete page/document coverage and exact source access, and pass the applicable gates before replacing serving data or publishing a pack. Report complete, partial, pending and index-reconciliation counts separately; do not certify the entire corpus from samples or structurally valid review records alone.

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
