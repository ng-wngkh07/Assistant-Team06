# Changelog

Changes are grouped by their effect on the product. Unreleased work has not been published as a release.

## Unreleased

### Added
- Planner prompt, JSON schema, and fail-closed adapter gating modules (`app/planner_prompts.py` with delta-only `PLANNER_SYSTEM_PROMPT`, `build_planner_user_prompt`, and `build_planner_response_schema`; `app/planner_adapter.py` validating `planner-9b.json` against pinned base hashes, paired evaluation, and scope/schema locks) paired with `_merge_llm_planner_strategy` deterministic merge/clamping in `app/study_planner.py`.
- Consolidated regression coverage across existing domain modules (`tests/test_practice_adapter.py`, `tests/test_native_mlx_contract.py`, `tests/test_grounded_citations.py`, `tests/test_study_materials.py`, `tests/test_timetable_feature.py`, `tests/test_timetable_registration_hold.py`, `tests/test_audit_regressions.py`) for Q&A/planner fail-closed adapter contracts, shared `<passages>` KV prefixes, focused cloze masking, VLM `OUTPUT_SCHEMA` passthrough, multi-LoRA `lora_banks` hot-swap and missing `prefix_caches` guard, `compare` multi-source citation validation, and public-Git policy checks.

### Changed
- Added opt-in lightweight `GET /api/status?summary=1` polling (`app/server.py`, `app/indexer.py`) that offloads status work from the event loop, coalesces and caches shared library counts keyed on SQLite DB/WAL revision and watcher drift state (`src_watcher_state.json`) without caching user-specific paths or GPU/readiness state, omits document/watcher item lists and infrastructure probes from routine sidebar polls, and updates `static/app.js` to poll only while visible and signed in with single in-flight deduplication, visibility reveal refresh, account-transition stale-response guards, and full `/api/status` fetches with loading/error states when opening the knowledge-base modal.
- Raised the study-material upload limit from 20 MB to 100 MB across backend validation (`app/material_storage.py`), request admission (`MAX_UPLOAD_BODY_BYTES = 140 MiB` JSON/Base64 envelope in `app/request_admission.py`), frontend pre-upload checks (`static/app.js`), operator docs (`README.md`, `docs/usage.md`, `docs/native-host-pilot.md`), and boundary tests.
- Extracted native Q&A prompt constants and contract validators (`MLX_SOURCE_PROMPT`, `MLX_VERDICT_SOURCE_PROMPT`, `qa_system_prompt_matches`) into zero-dependency `app/qa_prompts.py`.
- Aligned `app/flashcard_generator.py` and `app/quiz_generator.py` on shared deterministic `<passages>` prefixes with `context_prefix_marker = "\n<task>\n"`, added `flashcard_response_schema`, `quiz_response_schema`, and `extract_focused_flashcard_context`, scaled topic flashcard retrieval dynamically (`passage_limit` `8..16` in `LearningService.generate_topic_flashcards`, `app/learning.py`), and added `TrainedModelClient.warm_secondary_feature_prefixes` (`app/trained_client.py`) across Practice, Flashcards/Quiz, and Timetable Planner.
- Added resident multi-adapter `lora_banks` / `base_slots` hot-swap caching in `app/mlx_infer.py` and schema-constrained timetable VLM extraction (`OUTPUT_SCHEMA` in `app/timetable_vision.py`, `_get_or_compile_vision_schema` cache in `app/mlx_vision.py`).
- Remove archived historical training/evaluation scripts (`scripts/archive/`), one-off offline curation/evaluation/pending-workspace scripts, unreferenced offline trial/ledger/teacher modules, and unregistered one-off Codex/training test files so the public repository retains active technical product code and test suites (`app/run_ledger.py`, `CHANGELOG.md`).

### Fixed
- Reduced local document quiz and flashcard latency with bounded generation and compact source-cloze review. If a later review times out, return only independently verified items.
- Tightened `_build_qa_response_schema` in `app/rag_agent.py` with per-source `anyOf` line bounds (`source_line_counts`) alongside item `properties` so constrained decoding rejects out-of-range `evidence_lines` per source ID on the first generation pass.
- Enforced multi-source citation coverage for `answer_mode="compare"` in `app/rag_agent.py` so comparisons referencing only a single source across distinct concepts trigger corrective regeneration.
- Fixed `StudyMaterialManager._mask_answer_in_quote` (`app/study_materials.py`) to enforce word/hyphen boundaries instead of masking hyphenated prefix compounds such as `non-mutating algorithms`.
- Guarded `state["prefix_caches"].clear()` in `app/mlx_infer.py` (`_ensure_loaded_model`) when `prefix_caches` is uninitialized; added streaming repetition-cycle guarding (`_has_streaming_repetition_loop`, `loop_guarded`) preserving `presence_penalty=0.0`; and exempted pure single-claim source-omission statements (`_claims_are_pure_abstention`) in `app/rag_agent.py` from `repetitive_output` rejection so base and adapted abstentions resolve cleanly to `insufficient_evidence`.
- Fixed `source_cloze` quiz generation and Practice verification alignment across `app/quiz_generator.py` (`format -> evidence_id -> answer -> distractors` schema/prompt field order), `app/study_materials.py` (`_find_bounded_span_matches` body-preference matching, `_is_negated_superstring`, `any(repair_reasons)` repair triggering, deterministic `quiz_temp=0.0` for MLX `easy`, 3-item review batching with context-size fallback, and `PRACTICE_SYSTEM_PROMPT`), `app/practice.py` (skipping causal/unbound-certainty and non-easy difficulty blocks for verified `easy` `source_cloze` items), and `app/practice_proof.py` (reordering `native_grounded_review_schema` fields to `input_audit -> source_basis -> independent_solution -> supported` and updating `'easy'` criteria for literal `source_cloze` recall).
- Hardened timetable study-planning delta extraction and deterministic fallback parsing in `app/planner_prompts.py` (strict ISO weekday mapping `1..7`, daily minute bounds vs `blocked_day_windows`, `preferred_periods=[]` default, `<= 15`-word summaries) and `app/study_planner.py` (excluding `"tối đa/tối thiểu"` and day-scoped `"chỉ rảnh"` false-positive period matches, parsing `"không xếp học sau"` / earliest bounds, bumping LLM delta extraction to `max_tokens=700`, and overriding overlapped coarse regex windows during `_merge_llm_planner_strategy`).
- Decoupled Q&A prompt constants and matching helpers into zero-dependency `app/qa_prompts.py` so minimal MLX training/inference workers can validate and warm `qa-9b.json` prompts without importing `app/rag_agent.py` / `defusedxml`.
- Updated `app/mlx_infer.py` and `app/trained_client.py` to switch active Q&A LoRA slots in-memory (`0.0s` detach/reattach) while preserving `prefix_caches` (`max_entries=6`), warm `AUDITOR_SYSTEM_PROMPT_COMPACT` symmetrically on the unadapted base path via `extra_base_warmup`, and pre-warm worked-calculation (`MLX_CALCULATION_GUIDANCE`) and verdict (`MLX_VERDICT_SOURCE_PROMPT`) prefixes alongside `MLX_SOURCE_PROMPT`.
- Fixed trailing blank-line counting in `_split_source_lines`, literal code comparison operator decoding in `app/rag_agent.py` (`normalize_evidence_bound_comparisons`), and hyphenated query-acronym abstention handling (`MMPI-2`) in `app/rag_evidence_validator.py`.
- Stripped internal page-review banners (`strip_source_review_banner`) and preserved dotted identifiers (`vec.push_back`, decimals) across focused cloze quizzes and deterministic fallback flashcards (`app/flashcard_generator.py`, `app/study_materials.py`), parsed explicit daily study hour ranges (`chỉ học từ HH:MM đến HH:MM`) and blocked `avoided_weekdays` leakage on partial-window days in `app/study_planner.py`, and normalized APFS NFD/NFC path comparisons in `app/corpus_readiness.py` and `app/corpus_scope.py`.
- Eliminated steady-state 100% idle CPU thrashing in `SrcWatcher` (`app/src_watcher.py`, `app/source_page_reviews.py` `review_dirs_signature`, plus configurable `STUDY_EXTRA_REVIEW_DIRS` and ignored `data/runtime/page_review_dirs.json` loading without hardcoding local audit directories in tracked code) via batched compatible-vector checks, DB stat-bound vector verification caching, scandir review/OCR snapshot caching, and state persistence deduplication; guarded `_is_pure_abstention` in `app/rag_agent.py` against misclassifying mixed refusal-plus-answer claims separated by `;`; and hardened `_has_streaming_repetition_loop` in `app/mlx_infer.py` against paraphrased clause-level decode loops.
- Enforced public-Git technical-only content and test-file hygiene rules across `.agents/rules/study-agent.md`, `CONTRIBUTING.md`, `docs/testing.md`, `docs/repository-maintenance.md`, and `.gitignore`, and extended `scripts/check_history.py` with `validate_public_paths` and `validate_public_doc_diff` guards against committing internal business/training/experiment/evaluation artifacts or one-off ticket-scoped test files.

### Security

- Upgraded `opentelemetry-api` to `>=1.45.2` (`==1.45.2` in `constraints-web-py312.txt`) across dependency manifests (`requirements.txt`, `requirements-dev.txt`, `requirements-windows.txt`) and resolved Bandit security alerts across `app/`, `scripts/`, and modified `tests/` modules (`http(s)` scheme check in `scripts/backend_service.py`, explicit exception handling in `app/mlx_infer.py` and `app/rag_evidence_validator.py`, shared `_run_git` helper in `scripts/check_history.py`, and targeted `# nosec` annotations on audited SQL, subprocess, offline tokenizer, and pytest assertion lines).

- Harden HTTP/HTTPS API admission and infrastructure posture in `app/request_admission.py`, `app/account_services.py`, and `app/routes/material_routes.py`: add security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and `Strict-Transport-Security` when `STUDY_SECURE_COOKIES=1` or trusted HTTPS proxy scheme is active), expand early byte-cap enforcement to 256 KiB across all non-upload `/api/` mutation endpoints (`POST`, `PUT`, `PATCH`) and 28 MiB on `/api/timetable/extract`, add a bounded 4096-bucket sliding-window rate limiter returning HTTP `429` + `Retry-After` (keyed by verified-shape session cookie hash + NAT IP ceiling or client IP, defaulting to 300/min API and 30/min heavy inference/upload), and enforce per-account upload storage quotas (`STUDY_ACCOUNT_MAX_FILES=50`, `STUDY_ACCOUNT_MAX_STORAGE_MB=250`) on non-deduplicated uploads.

- Project public and authenticated JSON and SSE API responses and sanitize runtime error details via `app/response_projection.py` across `/api/chat`, `/api/chat/stream`, persisted and pre-existing history and session routes (`/api/history`, `/api/history/{item_id}`, `/api/sessions`, `/api/sessions/search`, `/api/sessions/{session_id}`, `/api/sessions/{session_id}/summarize`), and document, learning, practice, and study-material routes (`app/server.py`, `app/routes/session_routes.py`, `app/routes/document_routes.py`, `app/routes/learning_routes.py`, `app/routes/material_routes.py`), stripping internal host path keys (`review_path`, `filepath`, `raw_capture_path`, `image_path`, `storage_path`, `config_path`, and absolute `src_path`) and local config/traceback paths at the response boundary without mutating stored SQLite history rows while preserving citation identities, grounding quotes and claims, `page_quality` uncertainty flags, relative `src/` references, API preview/download URLs, and internal searcher metadata.

- Add optional local accounts with salted password hashes, revocable sessions, request-origin checks and separate planner, history and upload storage. Remote API access requires authentication; public registration remains disabled by default.

- Bound Office preview conversion with local OOXML validation, isolated macro-disabled renderer profiles and source-content cache keys. Reject external relationships and active payloads before automatic conversion; verify source and PDF digests for explicitly imported static slide renditions.

- Upgrade `pillow` to `>=13.0.0`, `certifi` to `>=2026.10.18`, `filelock` to `>=4.2.1`, and `streamlit` to `1.66.1` across dependency and constraint manifests to address Dependabot advisories.
- Reconstruct validated OCR cache filenames from an explicit hex alphabet and enforce normalized `CACHE_DIR` prefix containment in `app/vision_ocr.py`, sanitize error responses across `app/routes/` and `app/server.py` to prevent exception stack-trace exposure, and eliminate overlapping whitespace quantifiers in `app/answer_format.py` (`_SOURCE_SECTION`) to prevent polynomial ReDoS backtracking.
- Replace `xml.etree.ElementTree` with `defusedxml.ElementTree` (`forbid_dtd=True`) in `app/knowledge_pack.py` and replace `xml.sax.saxutils.escape` with `html.escape` across RAG and training-data modules.

- Report the full Python scan through pinned Bandit and its official SARIF formatter, retaining native severity and failing on incomplete analysis.

- Parse DOCX and PPTX XML documents using `defusedxml` with forbidden DTD and entity expansion to prevent XML injection and external entity attacks; discard partial output when parsing fails.
- Restrict `AutoTokenizer` in token auditing to local files with disabled remote code execution (`trust_remote_code=False`, `local_files_only=True`).
- Reject malformed records, missing signatures, and invalid statuses during duplicate run ledger checks to prevent silent bypass of quality gates.
- Replace runtime assertions across dataset preparation and validation tools with explicit exception gates to ensure enforcement under Python optimization.
- Fail closed during source page validation when physical page counts cannot be established from existing files.
- Move manual historical C++ review script inputs from shared temporary directories to repository-local storage.
- Enforce explicit complete source-bound per-physical-page review coverage (`--page-reviews`) during publisher knowledge-pack export; enumerate physical PDF pages and PPTX slides from original containers, reconcile expected production chunk projections against indexed passages, and fail closed on omitted, duplicate, out-of-range, partial, or HOLD reviews, tampered evidence, or altered indexed passages even when re-embedded vectors are valid.
- Enforce unambiguous canonical JSON serialization for physical page passage digests, validate contiguous canonical chunk indexing (`0..N-1`) over installed database sequence `ORDER BY chunk_index, id`, and reject out-of-range or extraneous physical page chunks before export in knowledge packs.
- Validate portable physical-page review coverage and passage digests during pack installation and application distribution bundling (`scripts/build_distribution.py`), preventing unreviewed or modified packs from entering production.

### Added

- Add cross-process resident MLX worker tracking and immediate training eviction (`app/gpu_lock.py`, `app/stream_worker.py`, `app/native_embedding.py`, `app/trained_client.py`, `tests/test_gpu_coordination.py`): register live `ResidentStreamWorker` (`mlx_infer`, `mlx_vision`) and `NativeEmbeddingClient` (`mlx_embedding`) child PIDs in file-locked `gpu_resident_workers.json`, run a lightweight owner monitor thread that cancels startup warmup (`TrainedModelClient._cancel_startup_warmup_signal`) and evicts resident workers immediately upon `waiting_for_training`/`training` without waiting for the 180s `idle_ttl`, and apply a bounded cross-process termination fallback (`evict_cross_process_resident_workers`) inside `acquire_for_training()` so the web server can remain online while training reclaims Unified Memory.
- Add compact `claim_id`-indexed Careful auditor mode (`AUDITOR_SYSTEM_PROMPT_COMPACT`, `compact=True` in `build_auditor_messages`, exact `claim_count` schema bounds with fail-closed `claim_id` resolution preserving `"statement"` compatibility in `app/reasoning_policy.py` and `app/rag_agent.py`) and extend `TrainedModelClient.warm_prefix_caches` (`app/trained_client.py`) to prewarm `AUDITOR_SYSTEM_PROMPT_COMPACT` plus canonical auditor JSON `response_schema` grammar processors during background MLX startup warmup (`tests/test_generation_latency_optimizations.py`).
- Add 1-slot exact `<context>` prefix KV reuse (`context_prefix_cache` via `context_prefix_marker="\n<user_query>"`) in `app/mlx_infer.py` and `app/rag_agent.py` primary MLX requests (extending warmed system KV snapshots with `mx.clear_cache()` memory cleanup), expand resident `prefix_caches` (`4` LRU) and `schema_processors` (`16` LRU), and raise the `TrainedModelClient` worker default `MLX_PREFILL_STEP_SIZE` to `256` while preserving `128` standalone fallback in `app/mlx_infer.py`.
- Add background MLX prefix KV cache startup warmup (`TrainedModelClient.start_startup_warmup` / `stop_startup_warmup`, configurable via `STUDY_DISABLE_STARTUP_WARMUP=1` in `scripts/dev.py` `ENV_KEYS` and `.env.example`) paired with `app/mlx_infer.py` `"op": "warmup"`, a capped 2-entry LRU prefix cache, and `mx.clear_cache()` cleanup on 16 GB Apple Silicon hosts (`app/mlx_infer.py`, `app/trained_client.py`, `app/server.py`, `tests/test_native_mlx_contract.py`, `tests/test_trained_client.py`).
- Add read-only retrieval tree sidecar health inspection (`inspect_retrieval_tree_health` in `app/retrieval_tree.py`) surfaced through `inspect_infrastructure_health` (`app/runtime_health.py`, `/api/health`, `/api/status`) and `scripts/dev.py check --profile local` (`tests/test_retrieval_tree_production.py`).
- Add offline read-only training and adapter readiness auditor (`scripts/audit_adapter_readiness.py`, `tests/test_adapter_readiness_audit.py`) checking corpus/OCR review completeness, native base model & hardware/protocol preflight, dataset `runtime_files_sha256` drift and scope/token gates, and strict adapter acceptance status without mutating databases or activating unaccepted candidates.
- Add bounded persistent `--stream` native MLX workers (`app/stream_worker.py`, `app/trained_client.py`, `app/mlx_infer.py`, `app/native_vision.py`, `app/mlx_vision.py`, `app/native_embedding.py`) coordinated by `gpu_coordinator` (`app/gpu_lock.py`) with role-scoped mutual eviction (`mlx_infer` vs `mlx_vision`, full eviction for training), idle timeout cleanup, cancellation teardown, and cloned Qwen3.5 hybrid `KVCache`/`ArraysCache` prefix caching.
- Add strictly source-bound Verified Practice Bank reuse in `app/practice.py`, `app/learning.py`, `app/routes/learning_routes.py`, and `app/server.py` (`enable_verified_bank=True`, opt-out via `refresh=true`): reuses verified questions from `practice.db` (preferring unanswered items) only when matching model identity, difficulty policy, exact `chunk_id` text/safe-text SHA-256 digests, page-quality `needs_review` checks, and deterministic citation/choice validators, while issuing fresh session IDs and empty answer maps.
- Add atomic retrieval-tree sidecar replacement (`rebuild_tree` in `app/retrieval_tree.py` and `--replace` in `scripts/build_retrieval_tree.py`) with automatic refresh when indexed sources or embeddings change (`app/indexer.py`, `app/src_watcher.py`) and sidecar staleness invalidation in `TreeSearcher` (`app/searcher.py`).
- Scale backend concurrency for 20+ authenticated users while keeping a clean boundary for future multi-host expansion: isolate per-account `dialogue.lock` files (`account_dialogue_gate`) so session, history, and planner CRUD never block on another account's model work, replace immediate global-lock rejection on authenticated inference/upload/OCR/index routes with a bounded FIFO queue (`acquire_inference`, `STUDY_MAX_INFERENCE_QUEUE=20`, `STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS=25.0`, one active/queued inference request per account), expand `scripts/dev.py` `ENV_KEYS` and `.env.example` for hosted scaling/security settings, and expose sanitized `infrastructure` (disk headroom, read-only KB readability, writable accounts directory, security posture, admission/storage limits) and `admission` telemetry on `/api/health`, `/api/status`, and `scripts/backend_service.py` (with HTTPS/cookie/registration warnings and preflight disk/writability validation).
- Add macOS backend recovery controller (`scripts/backend_service.py`) and reusable operator launcher templates (`launchers/macos/backend-quick-start/`) providing session-detached supervision, strict process-identity verification, foreign-port fail-closed protection, preflight validation of explicit paths without demo/DB seeding, bounded log rotation, bounded crash recovery, and status separation of web, model/QA, and corpus readiness.
- Topic flashcard generation (`LearningService.flashcards` and `POST /api/learning/flashcards`) integrated into the study-material modal (`flashcards` tab) alongside practice exercises, and server-backed multi-page PDF export for stored `material_id` quiz sets via `POST /api/learning/export-pdf`.
- Enrolled-semester course filtering (`confirmed_classes`) for free-time study plan generation with preserved `weekly_lessons` and `practice_id` links across draft editing and confirmation, plus optional `STUDY_PUBLIC_URL` and `STUDY_APPLE_VISION_OCR_BIN` settings.

### Fixed

- `app/rag_query_analyzer.py`, `app/rag_evidence_validator.py`, `app/rag_agent.py`, `tests/test_context_expansion_yc196.py`: Avoid false `partial_evidence` refusals and unnecessary Call 2 numeric repair retries on verified RAG answers by treating `"phân biệt"` comparisons appropriately (`broad_indicators`), stripping parenthetical glosses, pitfall/purpose tails, compound prefixes, and expanded suffixes in `extract_compare_concepts` via `_strip_parenthetical_gloss` while returning `None` on 3+ item comma-separated lists, normalizing `véctơ`/`véc tơ` in `_normalize_loanword_terms_cf`, recognizing mathematical `khác không` / math zero phrases in `_WRITTEN_QUOTE_NUMBER_RE` without false-matching prose `"tài liệu khác không..."`, extracting Unicode/ASCII subscript and superscript numbers (`extract_quote_sub_sup_numbers` inside `extract_quote_supported_numbers`), and checking validated `evidence_quote` text alongside `item["text"]` when verifying `compare_concepts` during `_parse_structured_repair`.
- `app/study_planner.py`, `app/lexical_search.py`, `static/style.css`, `static/spatial-layout.css`, `static/index.html`: Expose `practice_id` and `exercise_status` in `get_combined_weekly_schedule`, keep attached session `notes` synchronized via `_sync_practice_notes` in `update_session_practice` and `_enrich_session` without stale `(Chưa có bài tập PDF đính kèm)` notices, exclude bare 1-2 line mixed-case divider slides (`"L-System"`, `"Tổng quan Fractal"`, blank-page placeholders) lacking punctuation or explanatory verbs in `is_heading_or_toc_chunk`, stack `#session-bar` above `#messages-container` (`z-index: 45`) and wrap mobile `.session-bar` rows cleanly, prevent flashcard front-face vertical overflow (`min-height: 0` on `.card-face-body` and responsive `.flashcard-viewport`), keep mobile `.chat-header` icon-button pointer events on the button element, left-align truncated dock account labels (`#btn-account-trigger`), and bump static CSS cache-busting query versions.
- `app/timetable_table.py`, `app/timetable_vision.py`, `static/timetable.js`, `static/planner-assistant.js`: Classify course-registration result tables (`KẾT QUẢ ĐĂNG KÝ HỌC PHẦN`) as `incomplete_registration` rather than scheduled entries; normalize Cyrillic OCR lookalikes in classroom codes (`А3-303` -> `A3-303`) and `g` clock separators (`12g45` -> `12:45`); corroborate literal `"Tiết: X-Y"` ranges and `"Giờ bắt đầu"` values inside unique grid cells in `grid_room_evidence` (`VERSION` `registered-table-literal-v5-registration-hold`, `PROMPT_VERSION` `v7-timetable-literal-cells`); clear invalid `end_time <= start_time` ranges into editable uncertainties; refresh live timetable draft conflict warnings after period-map bulk updates (`btnApplyPeriodMap`); and require a populated `practice_id` before rendering `"Bài tập"` launcher buttons.
- `app/routes/timetable_routes.py`: Unwrap `"Tự học <course>: <lesson>"` prefixes and generic `"Ôn tập <course>"` subtitles in `POST /api/timetable/planner/sessions/{session_id}/exercise/generate` so planner-generated study sessions resolve to enrolled `classified_topic` or `course` names instead of failing with HTTP `422`.
- `app/lexical_search.py`, `app/searcher.py`, `app/rag_evidence_validator.py`, `app/rag_agent.py`: Strip punctuation and single-character wildcard tokens in `clean_fts_query` while filtering Vietnamese question wrapper stopwords; normalize `Đ`/`đ` before NFD filename scope matching and require token boundaries; run targeted FTS identifier subqueries (`IFS`, `L-System`, etc.) and document-level/diversity slot allocation in `HybridSearcher`; exclude slide outline chunks prefixed with `"Trang N\nNội dung\n..."` without prose body in `is_heading_or_toc_chunk`; strip conversational upload/comparison filler phrases in `weak_keyword_evidence` while guarding `semantic_score=None` in `has_relevant_evidence`; verify explicit uppercase/hyphenated query technical terms in positive claims (`find_ungrounded_technical_terms`, skipping abstentions) and prevent salvaging unrelated Call 1 claims lacking those query identifiers; and strip bogus citations / reclassify pure-abstention responses as `insufficient_evidence` instead of returning `response_kind="answer"`.
- `app/learning.py`, `app/study_materials.py`, `app/account_services.py`, `app/server.py`: Exclude plain-text back-matter `Glossary`/`Index`/`References` blocks in `_is_theory_chunk`; resolve multi-book flashcard attribution by passage quote instead of bare `page_num`; allow PDF soft-wrap whitespace matching (`_quote_matches_chunk`, `_mask_answer_in_quote`) for quiz and flashcard cloze verification; persist `generate_from_library_document` records via `PrivateMaterials.persist_material_record`; expose `material_id` in `list_available_documents` backed by `list_materials()`; and serve `/favicon.ico` from `static/logo.jpg` (`200`/`204`) to eliminate startup 404 console noise.
- `app/rag_query_analyzer.py`, `app/rag_evidence_validator.py`, `app/answer_modes.py`, `app/rag_agent.py`: Strip conversational compare modifiers (`"ngắn gọn"`, `"sự khác nhau giữa"`) and trailing document-scope suffixes (`"trong tài liệu"`, `"theo giáo trình"`) in `extract_compare_concepts` / `detect_partial_evidence`, normalize `vectơ`/`vecto`/`vector` loanword orthography in `concept_has_evidence` and `validate_compare_mode`, and skip redundant `text in source_text` rejection on structured compare claims whose `evidence_quote` / line span is already verbatim-verified.
- `app/practice.py`: Keep a full `240.0s` verification budget for 2-step MLX proof auditing (`is_mlx_model`) so slow primary question generation does not time out follow-up `candidates[:3] + [3:]` verification.
- `app/timetable_table.py`, `app/timetable_vision.py`: Reject class-section `LHP` codes mistaken for classrooms in timetable VLM output, extend literal OCR grid anchors (`[A-Z]{2,8}\d{4,10}[A-Z]{0,3}`) to corroborate stacked room codes (`read_grid_cell_evidence` / `grid_room_evidence`), bump `PROMPT_VERSION` to `v6-timetable-literal-cells`, and update `static/index.html` stylesheet cache busting.
- `app/accounts.py`, `static/accounts.js`, `static/app.js`: Clear per-username and IP login throttling on successful sign-in without counting valid logins as failures, set `SameSite=Lax` on session/logout cookies, format FastAPI `422` validation errors in `apiJson`, update auth modal/trigger titles when registration is disabled, and guard protected chat, model-check, and upload actions until authenticated.
- `app/retrieval_tree.py`, `app/document_lookup.py`, `app/rag_agent.py`: NFC-normalize `retrieval_tree.db` fingerprints and match NFC/NFD filename variants across macOS paths; prefer verified chunks when `source_page` is unspecified instead of failing on partial unverified visual chunks; allow verified primary-source citations for concept comparisons, retry repetitive drafts when verified citations exist, and salvage verified primary claims before returning no-source fallbacks.
- `app/exercise_pdf.py`, `app/learning.py`, `app/practice.py`, `app/study_materials.py`, `app/routes/material_routes.py`, `static/app.js`, `static/practice.js`, `static/style.css`: Paginate PDF exercise sheets and answer keys across pages with `fitz.Story` while keeping cards intact; normalize `vecto`/`vector` orthography and dotted workbook item extraction without treating `R_2[t]` polynomial spaces as missing figures; sample large slide decks evenly during study-material generation; keep verification timeout headroom in `PracticeService`; send `persist_to_src: false` on private-account uploads and forward `material_id`; and prevent top-clipping inside scrolled flashcard faces.
- `app/timetable_table.py`, `app/timetable_ics.py`, `app/study_planner.py`, `app/routes/timetable_routes.py`, `static/timetable.js`: Resolve macOS `apple_vision_ocr` across runtime paths and `STUDY_APPLE_VISION_OCR_BIN`; export Apple/Google Calendar-compatible `.ics` events with deadline `DTEND`, absolute HTTP/HTTPS `URL`/`ATTACH` links, omitted empty `LOCATION`, and Monday `week_start` fallback; propagate generated `practice_id` onto matching `study_sessions`; and replace synthetic `"Tự chọn"` draft rows with real enrolled courses.
- Pin `/api/chat/stream` SSE execution (`app/server.py`) to a single `contextvars`-preserving worker thread with periodic `: keepalive` heartbeats so AnyIO thread-pool hops cannot self-deadlock `DialogueGate` / `GPULockManager` thread-local re-entrancy across primary generation, citation repair, and Careful-mode auditing; stop converting `GPUBusyError`, `DialogueBusyError`, and `IndexingCancelledError` into generic model-startup failure messages in `app/rag_agent.py`; and add foreground-priority preemption + cooldown (`STUDY_BACKGROUND_GPU_COOLDOWN_SECONDS=120` in `app/gpu_lock.py`, `app/src_watcher.py`, `scripts/dev.py`, `.env.example`, `docs/configuration.md`) so background OCR/vectorization yields to interactive user requests without burning OCR retry budgets or evicting resident `mlx_infer` prefix caches (`tests/test_live_concurrency_and_priority.py`).
- Eliminate `/api/health` startup timeouts and CPU/lock contention by keeping startup vector-coverage inspection read-only without spawning background rebuild work during health checks (`app/indexer.py`, `app/vector_rebuild.py`, `app/server.py`, `scripts/backend_service.py`), and gate unauthenticated browser startup polling/fetches across `static/accounts.js` (`canAccessProtectedApis`, `runWhenAuthorized`, logout teardown guard), `static/app.js` (also skipping legacy `/api/history` fallback on `401`), `static/document-lookup.js`, `static/practice.js`, `static/timetable.js`, and `static/planner-assistant.js` so protected routes are skipped cleanly before login and during logout (`tests/test_health_performance_and_401.py`, `tests/hosted_auth_polling_ui.test.cjs`).
- Fix `VisionOCRManager.ocr_image_bytes` (`app/vision_ocr.py`) catching `NativeVisionClient.generate_from_image` truncation `ValueError` at `max_tokens=2048` so the `max_tokens=4096` retry actually executes on dense pages (`tests/test_native_mlx_contract.py`).

- Harden QA numeric provenance repair, method-prompt routing, and Careful auditor budgeting (`app/rag_evidence_validator.py`, `app/rag_agent.py`, `app/reasoning_policy.py`):
  - Extract shared helper `_analyze_numeric_mismatch` and extend `format_numeric_mismatch_feedback` (with `source_meta` sibling-page disambiguation) plus `format_numeric_ban_hint` in `app/rag_evidence_validator.py`, and append end-of-prompt same-page warnings and banned numeric hints in `app/rag_agent.py` so citation repair targets the matching same-page passage without repeating rejected numbers.
  - Exclude procedural/method queries from `MLX_VERDICT_SOURCE_PROMPT` routing in `native_source_prompt`, prefer missing query numbers when selecting a second anchor page in focused Fast mode, strip standalone step headers (`Bước <n>` / `Step <n>`) in `extract_draft_claims`, omit redundant full `sources` text in `build_auditor_messages` when `evidence_catalog` covers >= 98% of compact characters (< 30 entries), scope the Careful auditor payload to cited sources, and scale `auditor_num_predict` via `min(1800, max(1000, auditor_claim_count * 160 + 400))` (capped by available context headroom) to avoid multi-clause JSON truncation.
- Fix end-to-end QA routing, evidence validation, Metal OOM peer eviction, and structured-generation overhead (`app/dialogue.py`, `app/topic_tree.py`, `app/rag_query_analyzer.py`, `app/rag_evidence_validator.py`, `app/rag_agent.py`, `app/native_embedding.py`, `app/stream_worker.py`, `app/mlx_infer.py`, `scripts/dev.py`, `.env.example`, `docs/configuration.md`, `docs/local-performance.md`): avoid intercepting self-contained multi-clause queries that contain trailing anaphoric demonstratives in `dialogue_intent`, route Prisoner's Dilemma terminology to `Lý thuyết trò chơi và ra quyết định` without bare prisoner false positives on Viktor Frankl passages, stop flagging focused multi-part questions containing `và`/`giữa` as `is_broad_query`, allow unambiguous written Vietnamese/English numbers in cited quotes (`extract_quote_written_numbers`) to substantiate digit claims cleanly, trim duplicate instruction prose from 9B user prompts, register `NativeEmbeddingClient` under `mlx_embedding` and evict peer MLX roles before pre-token Metal OOM retries in `ResidentStreamWorker`, cache compiled `response_schema` logits processors across persistent `--stream` MLX requests via `.clone()`, and expose bounded `MLX_PREFILL_STEP_SIZE` tuning (`64..1024`, default `128`).
- Bound focused `Fast` (`standard`) RAG context expansion and packing (`app/rag_agent.py`, `tests/test_structured_context_yc235.py`) by scoping neighbor expansion to top-3 seed chunks while preserving trailing hits and packing prioritized tiers (`~3,800` chars / up to 5 chunks, keeping at least 3 chunks and unmodified `L1|` passage lines), guard `app/server.py` lifespan warmup/eviction hooks when tests substitute minimal `TrainedModelClient` doubles, and hold `NativeEmbeddingClient._stream_lock` through worker termination and active-set cleanup (`app/native_embedding.py`, `tests/test_src_watch_03_freshness_and_worker.py` using `is_pid_alive` safely across POSIX and Windows).
- Include `practice.db` in per-account backup/restore (`scripts/backup_service.py`) and legacy migration (`scripts/migrate_legacy_data.py::migrate_legacy_practice` with conflict detection and FK checks), ensure PyMuPDF document handles close deterministically on `/api/ocr/page-image` (`app/routes/ocr_routes.py`), and apply `sanitize_error_detail` across timetable and planner error responses (`app/routes/timetable_routes.py`).
- Optimize local retrieval and generation latency without altering source-grounding gates: add stat-bound LRU SHA-256 caching (`app/active_registry.py`), preverified embedding/OCR identities across indexing batches (`app/indexer.py`, `app/src_watcher.py`, `app/vision_ocr.py`, `app/mlx_embedding.py`), single-pass request-scoped source freshness checks (`app/source_freshness.py`), generation-bound float32 semantic matrix search (`app/searcher.py`), bounded LRU message token counting and context caching (`app/token_counter.py`, `app/dialogue.py`), and grammar-constrained JSON `response_schema` decoding with compact bounded repair (`app/rag_agent.py`, `app/reasoning_policy.py`, `app/vision_ocr.py`).

- Restrict default source-file resolution in `app/material_storage.py::resolve_safe_source_file`, `app/study_materials.py::inspect_library_document`, and `app/learning.py` exercise extraction strictly to configured `SRC_DIR` (`src`) without falling back to `docs/`, `tests/fixtures/documents/`, or `Path.cwd() / "src"` (`tests/test_hosted_source_paths.py`).
- Preserve the user question inside `build_auditor_messages` and enforce directional/spatial sequence endpoint consistency (`check_positional_sequence_contradiction` in `app/reasoning_policy.py` and `app/rag_agent.py`), triggering citation repair and failing closed rather than accepting inverted front/rear endpoint answers in careful grounding mode.
- Add explicit `STUDY_GPU_RUNTIME_DIR` environment override for shared GPU lock coordination across processes (`app/gpu_lock.py`, defaulting to `DATA_DIR/runtime`) while keeping source watcher state, accounts, and chat histories in their separate durable locations.
- Add an opt-in backend-managed operator `src/` filesystem watcher (`STUDY_SRC_AUTO_WATCH=1`, disabled by default) with per-corpus writer serialization, debounced stability checks, safe single-file incremental indexing, persistent state under private `AGENT_DATA_DIR/runtime/`, non-destructive `needs_update`/`missing_source` flagging for altered or removed sources, bounded error backoff, `gpu_coordinator` busy deferral, and sanitized watcher status on `/api/storage`, `/api/status`, and library UI (`app/src_watcher.py`, `app/indexer.py`, `app/server.py`, `app/config.py`, `static/index.html`, `static/app.js`).
- Build bounded derivative practice proposals from complete cited worked solutions, retaining independent native review and the shared request deadline. Reject polynomial-equivalent and near-identical article-variant choices before publication.

- Add account-scoped durable SQLite persistence for practice sessions (`practice_sessions` table in `practice.db`), allowing generated exercises, answer submissions, citations, and progress to survive server restarts, cache eviction, and window reloads without regeneration (`app/practice.py`, `app/db/schemas.py`, `app/routes/learning_routes.py`).
- Enforce awake-bounds, max daily study minutes, and task completion validation under transaction locks during study plan confirmation, rejecting stale or conflicting proposals with HTTP 409 while preserving confirmed historical sessions (`app/study_planner.py`, `app/routes/timetable_routes.py`).
- Integrate calendar session and weekly lesson exercise actions with the existing practice bridge, unify planning proposal entry under a single primary action that respects configured session durations, clean up verbose labels across calendar, OCR, and library dialogs, and package persisted practice PDF worksheets inside calendar bundle zip exports (`static/index.html`, `static/planner-assistant.js`, `static/timetable.js`, `app/planner_topic_bridge.py`).

- Redesign the web interface around a pure black monochrome palette with Apple Liquid Glass refraction and specular edge lighting (`static/liquid-glass.css`, `static/spatial-layout.css`, `static/spatial-layout.js`, `static/index.html`), restructure the workspace into a fixed bottom floating Liquid Glass launcher dock (`bottom-dock`), slide-over glass drawer, Bento spatial hub, and two-column split studios, update branding to `ARYM` (`Adaptive Revision Yields Mastery`), and replace the top account status banner with a dock account pill and a per-account one-time button-spotlight onboarding tour (`static/onboarding-tour.js`, `static/onboarding-tour.css`, `static/accounts.js`).
- Add PDF export for topic-based practice questions and extracted exercises with a separate answer key and source citations placed on the final page(s) (`app/exercise_pdf.py`, `app/practice.py`, `app/routes/learning_routes.py`, `static/practice-pdf.js`).
- Add weekly lesson topic classification, automatic 90-minute (`1 subject/day`) self-study session allocation in free timetable slots with attached PDF worksheets, and a `.zip` calendar bundle export (`app/planner_topic_bridge.py`, `app/timetable_ics.py`, `app/routes/timetable_routes.py`, `static/planner-assistant.js`).

- Add versioned migrations and private content-addressed upload storage for the single-host account service, with operator backup/restore and explicit legacy-account import tools. These do not establish public-service or multi-host readiness.
- Document a staged native-backend website pilot, authenticated source/persistence boundaries and the compatibility checks required before changing the model host or runtime.

- Rename timetable import to study planning and split it into four guided screens: class import, personal time, editable plans and dated deadlines, and calendar export. Save first-use guidance per account and feature, with manual replay.
- Export dated study sessions and deadline alarms in iCalendar files, with stable account-scoped event identifiers. Keep weekly class recurrence and reject undated legacy deadlines instead of guessing dates. Calendar applications control notification delivery.
- Document private website deployment, current database limitations and the distinction between private hosting and permission to use source materials.

- Use concise source-grounded verification for single-question local 9B practice, including an explicit missing-input gate and literal integer-array index references. Expose the selected review profile and actual adapter diagnostics separately; compatible adapters require artifact, content and performance acceptance before activation.
- Let users select one, two or three questions when practising from a specific source excerpt; keep three as the default.
- Add persistent hierarchical retrieval tree sidecar database (`app/retrieval_tree.py`) and maintenance builder CLI (`scripts/build_retrieval_tree.py`) providing candidate chunk filtering over authored taxonomies without modifying the original database.
- Add source-bound sidecar fingerprint validation and shared membership reader (`read_source_bound_tree_memberships`) for QA retrieval and `LearningService` cataloging, ensuring safe fallback to global search upon source mutations or WAL commits.
- Expand timetable OCR into a Personal AI Study Schedule Assistant (`app/study_planner.py`, `app/timetable_store.py`, `app/routes/timetable_routes.py`, `static/timetable.js`) supporting weekly personal routines (`fixed`, `protected`, `flexible`, `study_window`), personal study limits, deadline-driven task splitting, constraint-verified study session scheduling with capacity warnings/suggestions, and unified master schedule management with completion/lock/reschedule controls.
- Add a hierarchical two-level parent-child topic tree (`app/topic_tree.py`) shared between QA and topic-based learning (`app/learning.py`), mapping course subtopics under parent academic domains.
- Provide original-layout PPTX slide previews using a local LibreOffice renderer or explicitly imported, source-bound static PDF renditions. Preserve DOCX logical reading sections and original downloads without fabricating physical page images.
- Add Tesseract OCR (`UB-Mannheim.TesseractOCR`) to the Windows setup manifest (`requirements-windows-tools.json`), installer script (`scripts/windows_setup.ps1`), and Windows documentation (`README_WINDOWS.md`), with automatic Windows installation path detection in `app/timetable_table.py`.
- Retain partial timetable rows (`partial_entries`) during incomplete course-registration screenshot extraction and highlight missing required schedule fields (`.draft-field-missing`) in the timetable draft UI so users can complete missing weekday, time, or room values directly.
- Require source-bound content review and a coverage ledger for every physical page or slide before full-corpus acceptance, including pages without extraction warnings. Preserve unreadable regions and verify repaired passages and affected retrieval data separately.
- Define production acceptance for the complete agreed real library, usable original citations, precomputed retrieval, reproducible model settings and persistent personal uploads. Track these separately from synthetic test coverage and private training data.
- Build application distributions from a committed source revision and explicitly approved knowledge packs, with a bundled descriptor and a separate persistent personal library for uploads, retrieval, history and calendar state. Display the actual local source directory and its privacy policy in the library interface.
- Document source-by-source redistribution and privacy review, full-library coverage reconciliation and separate delivery of public knowledge packs and private training storage.
- Add an allowlisted publisher knowledge-pack exporter and checked installation into a separate local library. Include approved originals, source chunks and compatible vectors without copying private database tables or running user-side document extraction, embedding or training. Provide separate verified BGE-M3 pack formats for native macOS and Ollama; retain each runtime's exact embedding identity and normalization protocol.
- Provide a user-operated model check endpoint (`POST /api/model/check`) and sidebar control button that runs genuine local inference on the configured model to confirm runtime execution readiness without changing models.
- Add native macOS (`Start Study Agent.command`) and Windows (`Start Study Agent.bat`) one-click launch scripts with environment checks and automatic browser opening.
- Show the last packed context budget, output reserve and omitted-history warning, with an explicit new-chat action that preserves the next draft. Label live draft byte size separately from token usage.
- Report missing timetable fields, clipped course names and invalid time ranges in editable drafts, including cached results. Keep insufficient-information learning examples separate from positive schedule labels and training approval.
- Scan Python, JavaScript, GitHub Actions and the standalone Apple Vision Swift helper with CodeQL; compile the helper explicitly on macOS.
- Add sample university timetable and course-registration screenshots in `calendar_src/` as references for timetable import.

### Changed

- Require each application behavior change to pass its direct regression and sequential live cases for document QA, review-question generation, and timetable creation and study planning. Record latency and review the output for each case.
- Use configured shared `src/` and runtime `data/` paths without implicitly creating or selecting `data/user-library/`. Report missing or empty libraries honestly; reviewed knowledge-pack imports require explicit operator configuration, and existing personal data is preserved.

- Reconcile the README, contribution rules and business guides with hosted account storage, opt-in shared-source ingestion and operator/developer backend setup. Workflow/usage changes must update both entry guides and affected documentation in the same change, with specific reasons for unaffected files.

- Require source-bound evidence witnesses and consistent unique-choice checks for small native practice reviews and their adapter qualification. Separate source sufficiency and answer decisions from explanation grading. Review native candidates in groups of up to three and use a separately reviewed reserve only when needed. Reuse trusted instruction prefill within each review request while retaining source/content checks and the shared deadline.

- Optimize `NativeEmbeddingClient.get_embedding` by reusing verified post-generation batch model identity, eliminating redundant parent full-artifact byte hashing on cache misses while preserving byte verification on cache hits and fail-closed integrity checks.
- Wire `HybridSearcher.search_hybrid` to query an adjacent `retrieval_tree.db` sidecar by default before falling back to legacy topic routing, preserving explicit document scopes and global fallbacks.
- Route recognized QA topics through the shared parent-child taxonomy before hybrid retrieval, retaining explicit document scope and global fallback when heuristic evidence checks reject the candidate search. Routing diagnostics are JSON-safe and request-specific.

- Split monolithic backend files into focused single-responsibility modules: `app/lexical_search.py` and `app/semantic_search.py` (from `app/searcher.py`); `app/material_storage.py`, `app/question_validator.py`, `app/quiz_generator.py`, and `app/flashcard_generator.py` (from `app/study_materials.py`); `app/rag_query_analyzer.py`, `app/rag_evidence_validator.py`, and `app/rag_citation_repair.py` (from `app/rag_agent.py`); and domain route modules under `app/routes/` (from `app/server.py`), while preserving backward-compatible re-exports.
- Move legacy `v4`–`v6` model evaluation and preparation scripts from `app/` to `scripts/archive/` and remove unused `static/style.light-backup.css`.
- Default `resolve_processing_level` in `app/reasoning_policy.py` to `"standard"` unless `"careful"` is explicitly requested, and use in-memory extraction (`persist_to_src: false`) when uploading files directly from the Flashcard/Quiz study modal so generation does not block on corpus indexing.
- Unify timetable vision extraction timeout across macOS and Windows via `VISION_TIMEOUT_SECONDS` (360 seconds default).
- Warn when a supported source comparison shows a function declaration and definition with value versus reference parameters, without assuming whether the functions are intentional overloads or the complete program will link.

- Display original library PDF pages in the document viewer, with extracted text labeled separately because it can omit formulas or illustrations.
- Broaden easy-difficulty study material generation prompts in `app/study_materials.py` to allow key concept definitions and terms alongside numerical expressions, and eliminate double-counted chat overhead in conservative token estimation so single-question review fits within model capacity.
- Reject vector rebuilding from obsolete extraction versions or missing/mismatched source-quality records before creating a stage or running the model. Source refreshes update physical paths and retain human acceptance only when native text is unchanged or a valid source-bound review supplies the replacement.
- Replace arbitrary first-page heading topic extraction with an authored concept taxonomy in `app/topic_catalog.py`, grouping chapters under stable concepts and using FTS5-accelerated discovery with candidate text scoping, Unicode NFC/NFD alias coverage, literal phrase verification, and cached membership. Expand coverage for public originals with broad concepts in astronomy/optics, biology/genetics, chemistry, relational databases, economics, information theory, networking, and measurement/units, qualify thermal entropy to prevent misclassification of Shannon entropy, and add precise aliases for binary search and sample mean.
- Increase the native MLX 9B answer generation token budget to 1600 tokens for standard mode and 2000 tokens for careful mode to provide headroom for structured answers with citations, noting that completion remains bounded by model throughput and context constraints.
- Configure the native MLX model worker subprocess timeout with a 600s default to accommodate 4000-token prompt prefill and full generation on Apple Silicon 9B models.
- Exclude table-of-contents, index, and reference passages from learning theory chunks to avoid generating questions from non-instructional boilerplate.
- Install IANA timezone data for calendar recurrence on Windows and isolate mocked native tests from host model assets and worker network guards.

- Bound native text prefill to 128 tokens per step to reduce transient memory, and report model load, prefill and generation diagnostics without dropping prompt tokens.
- Reuse up to 128 validated query embeddings in process memory, keyed by model directory, freshly verified artifact content, token limit and query digest. Revalidate stored chunk provenance on every retrieval.
- In Fast mode, reuse cached citation-preview translations without waiting for optional translation generation. Keep original source text as the evidence for answers.

- Update application branding to ARYM AI across UI navigation, headers, welcome cards, and accessibility attributes.
- Support standard DD/MM/YYYY date input with keyboard entry and visual calendar picker for semester timetable export.
- Reorganize public documentation in English around product usage, setup, architecture, testing and maintenance. Remove internal allocation and submission records from public guides.
- Replace large attachment previews in the chat composer with compact, keyboard-accessible file banners. Keep explicit document opening and removal, reduce composer height and preserve independent message scrolling.
- Add GitHub Actions security workflows for actionlint syntax linting, Dependency Review and Gitleaks secret detection, with workflow instructions in testing documentation.
- Default QA to Fast, suggest Careful for complex requests without resubmitting the question, and preserve explicit mode choices. Practice keeps its fixed verification workflow.
- Select the pinned local MLX Qwen3.5 9B 4-bit base on Apple Silicon with an 8192-token operational budget. Reject retired model aliases and remove incompatible adapter execution paths; model installation does not authorize training or adapter activation.
- Prefer paragraph, line, sentence and word boundaries when indexing new text, retaining exact source slices and configured overlap. After accepted hybrid retrieval, add bounded adjacent chunks from the same document, filename and page as independent citation sources; preserve existing indexes and evidence gates.
- Consume canonical material filenames after upload in the client interface for preview, active attachment scoping, QA, downloads, and generation.

### Fixed

- Update runtime and browser check error messages (`app/runtime_health.py::runtime_failure_message`, `static/app.js`) so hosted learners are asked to retry or contact the service operator rather than instructed to open `Start Study Agent.command`, `Start Study Agent.bat`, Finder, or Terminal, while keeping error causes distinct.
- Project `is_scanned` when present in `documents` table schemas in `app/source_freshness.py::fetch_document_row` so `StudyMaterialManager.inspect_library_document` and `/api/materials/inspect-library` read accepted indexed scanned-document chunks from SQLite without requiring a separate disposable OCR cache, while retaining legacy absent-column compatibility and disk/hash/watcher/quality freshness refusals (`app/source_freshness.py`, `tests/test_src_watch_02_freshness.py`).
- Reuse `NativeEmbeddingClient.get_status_identity()` across both installed native provenance and default-native branches in `_configured_index_coverage` (`app/server.py`) so unchanged warm `/api/health` polls validate coverage against the stat- and metadata-bound verified BGE-M3 artifact identity without repeatedly hashing the 1.15 GB `model.safetensors` weights, while preserving full-byte verification for query and embedding admission, fail-closed invalidation on file/path changes, missing/unverified/custom model non-readiness, and Ollama coverage routing (`app/server.py`, `tests/test_api_readiness_01.py`).
- Restore keyboard accessibility and focus isolation across account authentication (`static/accounts.js`) and document content viewer (`static/app.js`, `static/index.html`) dialogs: submit login (never registration) once on non-composing `Enter` in `#account-username` or `#account-password` with in-flight duplicate-submit protection (`aria-disabled` / `aria-busy` without triggering `.account-btn:disabled` hiding), add `aria-hidden` / `tabindex="-1"` and initial focus inside `#document-content-modal`, trap `Tab` / `Shift+Tab` inside visible modal controls while blocking background `Enter`, close only the topmost modal on `Escape` without leaking to underlying menus or drawers, and restore focus to a connected visible opener or safe fallback control instead of hidden menu items or `document.body`.
- Enforce a current-week `Asia/Ho_Chi_Minh` ceiling-minute cutoff across `generate_study_plan` (deadlines, weekly lesson courses, and `reschedule_only`) and `confirm_study_plan` / `verify_schedule_constraints`, reject newly proposed or user-edited past draft sessions with `HTTP 409 Conflict` while deriving `completed`/`locked`/`manual` session preservation strictly from stored SQLite records, report expired historical or window-exhausted weeks explicitly via `unscheduled_tasks`, `unplaced_courses`, `warnings`, and `explanation`, and forward explicit `db_path` across `update_preferences`, `ai_notes_override`, and planner CRUD helpers (`app/study_planner.py`, `tests/test_timetable_feature.py`).
- Anchor `_diagnose_unscheduled_task` deadline expiration checks to the selected planning week's Monday date in `Asia/Ho_Chi_Minh` instead of weekday-only tuples, handle weekday-only deadlines and cutoff `0` / `8` / `1440`-minute bounds safely, and avoid emitting contradictory unconfigured-window hints or counter-productive `add_study_window` suggestions when an auto-daytime future schedule hits daily study caps (`app/study_planner.py`, `tests/test_timetable_feature.py`).
- Validate stored corpus vector readiness (`KnowledgeIndexer`, `SrcWatcher`, and post-review chunk updates) against the verified active retrieval embedding identity rather than trusting the first `corpus_vector_provenance` digest or an absent native model directory, failing closed when the native artifact or active identity is unavailable, reusing stat- and metadata-bound verified status identity across unchanged repeated status polls while keeping full byte hashing at query and embedding verification time, and refusing silent mixed/retired/unprovenanced vector migration without deleting existing vectors (`app/indexer.py`, `app/native_embedding.py`, `app/src_watcher.py`).
- Replace the hardcoded personal host path in `tests/test_src_real_six_e2e.py` with the explicit `STUDY_TEST_REAL_SIX_DIR` opt-in environment variable and clean portable skip behavior (`tests/test_src_real_six_e2e.py`, `docs/testing.md`).
- Map degraded or missing local model runtime and configuration failures (`ContextLimitError`, missing weights/tokenizer/config errors) on `/api/materials/upload` and `/api/materials/generate-from-library` to bounded `HTTP 503` responses without host paths or tracebacks (`app/routes/material_routes.py`, `app/response_projection.py`), while retaining valid post-extraction upload records and raw files (guest or private account storage) when study-set generation cannot run and keeping invalid-input / unsupported-difficulty rejections on `HTTP 422`.

- Enforce shared disk-state freshness admission (`app/source_freshness.py`) across document lookup, source preview/download, hybrid/tree retrieval, chat scoping, evidence validation, practice, learning, and study-material generation so stale, modified (including same-length overwrites), missing, debounced, or path-unsafe `src/` originals are excluded or return `HTTP 409 Conflict` / non-leaky notices while preserving database rows and keeping private `material_id` uploads isolated (`app/document_lookup.py`, `app/searcher.py`, `app/retrieval_tree.py`, `app/routes/document_routes.py`, `app/routes/learning_routes.py`, `app/server.py`, `app/rag_evidence_validator.py`, `app/rag_agent.py`, `app/practice.py`, `app/learning.py`, `app/study_materials.py`).
- Make `SrcWatcher` shutdown truthful (`running=True`, `stopping=True`, `mode="stopping"` while the worker thread is alive, refusing concurrent `start()` writers until exit) and add cooperative cancellation across extraction, `corpus_writer_lock`, `gpu_coordinator`, and owned `mlx_embedding` POSIX session groups with bounded lifespan logging (`app/src_watcher.py`, `app/indexer.py`, `app/gpu_lock.py`, `app/native_embedding.py`, `app/pdf_extractor.py`, `app/server.py`).
- Clarify in deployment documentation (`docs/web-deployment.md`) that `app.config` still runs `DATA_DIR.mkdir(parents=True, exist_ok=True)` on import while `app.src_watcher` and `app.source_freshness` background lifecycles remain import-safe.
- Close stat-hash-cache admission races in shared source freshness verification (`app/source_freshness.py`) by verifying opened-file identity and path containment before and after hashing, cache lookup, and admission, failing closed without poisoning the digest cache; remove `subprocess.run` monkeypatch branching from `app/native_embedding.py` so native embedding invocations use only the cancellable `subprocess.Popen` worker path.

- Resolve fallback source PDF paths via configured `SRC_DIR` with path-containment validation in `LearningService` (`app/learning.py`), removing a hardcoded local filesystem path, and isolate mock tokenizer assertions in `tests/test_material_review_contract.py` when model assets are absent.
- Bind practice explanations to the exact cited rule, keep surrounding text for restriction checks, and verify small native batches one question at a time under the shared deadline. Reject proven duplicate polynomial answers and unverified causal or certainty claims; report the grounded review profile separately from legacy adapter qualification.
- Require separate source, content, latency and native application qualification for grounded practice adapters, with exact request scope and actual profile diagnostics. Legacy acceptance cannot enable a grounded adapter; missing-input qualifier failures remain failures.

- Resolve explicit filename-only source scope references before session follow-up inheritance in `requested_source_scope` (`app/server.py`), and preserve `unavailable` status for weekly lessons linked to missing or invalid worksheets while keeping newly added un-generated lessons as `not_generated` (`app/planner_topic_bridge.py`).
- Support `qwen3_5` nested `text_config`, `attn_output_gate` projection geometry, hybrid `linear_attn` and `self_attn` per-layer validation, `language_model.model.layers.` adapter keys, and sharded `model.safetensors.index.json` base weight hashing in LoRA layout, acceptance, and ledger checks (`app/model_resolver.py`, `app/adapter_integrity.py`, `app/active_registry.py`, `app/study_acceptance.py`, `app/run_ledger.py`).
- Enforce strict constraint validation (awake bounds, nighttime protection, daily study limits, task existence and completion) on all newly reviewed study plan sessions during confirmation regardless of client-supplied flags, preventing deleted-task reference saves and flag-based constraint evasion while preserving confirmed historical sessions (`app/study_planner.py`, `app/routes/timetable_routes.py`).
- Eliminate duplicate exercise sets on weekly lessons by disabling automatic legacy worksheet generation on save; generate exercises on demand via the unified practice service, share durable session identity across weekly lessons, planned sessions, interactive practice, PDF, and calendar bundles, and preserve specific lesson topic titles over broad classified fallbacks (`app/planner_topic_bridge.py`, `app/routes/timetable_routes.py`, `app/db/schemas.py`).
- Ensure durable practice session persistence to SQLite prior to memory cache publication and return truthful retryable errors on storage failures, restore answered quiz progress and review states in the practice modal without exposing server-side answer keys for unanswered questions, maintain cache bounds on database reloads, and fail closed when practice storage is missing or unreadable (`app/practice.py`, `static/practice.js`).
- Clean up contradictory mandatory-window copy in the planner wizard, remove fixed 90-minute references in weekly helper prose, ensure single visible primary proposal and reschedule buttons in the timetable UI, and shorten feature and action labels across the interface (`static/index.html`, `static/planner-wizard.js`, `static/onboarding-tour.js`, `app/planner_topic_bridge.py`).

- Clarify constant-derivative and schematic-inference review and distinguish question assumptions from source claims in generated practice explanations. This improves bounded regression cases; independent source-quality failures remain and the new 9B adapter stays inactive.

- Treat omitted or null `persist_to_src` as instant use in material uploads and library inspection. API clients that require durable library indexing must send `persist_to_src: true` explicitly.
- Use the shared vision timeout (360 seconds by default) for document OCR while retaining explicit caller overrides.

- Decline document answers, practice questions, and library study-material generation when a selected page still needs review for potentially missing formulas or diagrams, and direct learners to the original page or another source instead of presenting an uncertain transcription as fact.
- Warn in document answers and decline library study-material generation when a supported two-file prime-number example has an output list that does not match its input.
- Set selected knowledge-pack paths before importing application configuration, start an explicitly requested Ollama service before pack verification, and select query/index embeddings from the installed corpus identity.

- Recover knowledge-pack installation after failed artifact reads by validating receipt presence rather than directory existence, safely cleaning up only wholly empty directory trees while preserving nonempty databases and user data.
- Reject standalone Markdown headings and maintain per-document heading hierarchy stacks to exclude child sections of exercise bodies, answer keys, references, and untrusted injection instructions from learning theory evidence, and add narrow unit-conversion aliases for area and volume conversion to reach substantive theory passages.
- Bind generated practice quotations to exact excerpts from the selected passage. Preserve distinct passages with repeated page headers and prioritize explicit source rules for hard-question candidates; source, answer and difficulty review remain required.
- Keep stale retired model settings unavailable in health, status and model discovery even if those old weights are still installed.
- Constrain practice question and review output to an explicit JSON schema on native MLX and Ollama, preventing missing braces and misplaced answer fields. Source/difficulty verification still rejects unsupported questions. Missing native completion metadata no longer counts as a successful stop; the supported runtime requires streaming finish metadata.
- Retire obsolete 3B/7B training authorization and memory-estimate code. Current diagnostics inspect the nested Qwen3.5 architecture, while training remains blocked pending a separately reviewed 9B protocol. Preserve exact token, source and paired retention review gates; absent model identity is reported as unknown rather than labeled with an old repository/revision.
- Health coverage checks the embedding artifact actually configured on disk in a worker thread, so changed weights cannot leave stale vectors labeled healthy or block the web event loop during validation.
- Fall back to keyword retrieval when the optional embedding provider cannot establish its actual artifact identity or execute a query embedding, rather than failing the entire question-answering request.
- Require medium/hard study questions to pass independent source, answer and difficulty checks. Report insufficient verified output explicitly instead of silently substituting easy cloze questions under a harder label.
- Expose truthful configured-index vector coverage (`coverage_ratio`, `compatible_native_chunks`, `status`) and degraded keyword fallback status in `/api/health` separately from installed embedding artifact readiness.
- Gate hybrid retrieval and semantic candidate similarity under partial native indexing to drop nearest-neighbor noise from unrelated topics while preserving valid synonyms and diverse document selections.
- Elevate substantive authored concept explanations, definitions, property tables, and code syntax in learning quiz source selection while filtering out covers, conventions, and glossary noise, and deduplicating identical source passages.
- Scope learning exercise discovery to the queried concept or its local instructional section within broader textbooks, eliminating unrelated elementary programming exercises from broad reference manuals.
- Prefer direct read-only original PDF page extraction in document inspection, merging chunk overlap conservatively on fallback without trigger-reindexing side effects, and report truthful chunk counts in document lookup.
- Detect database and WAL file modifications via filesystem and header change-counter fingerprints to reliably invalidate catalog topic caching on same-length text updates across separate connections.
- Fix Windows batch startup script control flow by replacing in-block goto labels with subroutines and respecting custom Ollama daemon URL configurations.
- Honor requested question count (1 to 10) and difficulty levels ("easy", "medium", "hard") in the learning UI and backend, retrying truncated batches boundedly with smaller batches while preserving previously verified questions, and displaying the explicit partial count and selected difficulty inside the modal dialog.
- Allocate practice generation time by the requested number of batches while retaining a 180-second ceiling per inference call. Require concrete source-rule scenarios for hard questions and prohibit shortened, noncontiguous evidence quotes.
- Use request-local passage handles for practice generation and resolve them to exact database sources on the server. Reject unknown handles; keep training-example prompt/target preparation on the same contract, requiring regeneration of older preparation outputs before any future training.
- Strip leading Vietnamese enumeration phrasing (e.g. "Bốn tính chất cơ bản của...") and add OOP aliases in query concept extraction so hybrid retrieval targets the actual subject rather than generic counting words.
- Distinguish installed model artifacts from executable runtime capability (such as GPU Metal access) in `/api/health`, preventing misleading missing-model messages when operating system restrictions deny Metal access.
- Differentiate database read failures from model inference or startup errors, reporting actionable retrieval-specific guidance ("Không thể truy xuất cơ sở dữ liệu học tập...") while classifying database read, truncated generation, and runtime execution errors in runtime health.
- Exclude numbered answer labels from factual audits while preserving numeric claims. Prefer compact evidence identifiers that resolve to exact source passages, retaining full claim coverage and fail-closed verification.
- Request intermediate equalities for explicitly requested worked calculations, and clarify the two-endpoint source-line format during bounded citation repair.
- Reject a single requested limit replaced by a neighboring quotient in the same source. Keep formula-transcription repair separate from extra line-format guidance, preserving the requested function and its condition.
- Check the native query embedding's actual model identity against the stored-vector basis before using a cached vector or starting its worker. A changed model falls back to lexical retrieval rather than mixing vector spaces.
- Retain complete worked-solution instructions when citation repair omits a malformed draft, including source conditions and explanations for the requested parts. Use a focused source-translation instruction for explicit native yes/no and true/false questions in both generation attempts, while retaining the calculation and theorem instructions for other requests.
- Clear the draft-size indicator immediately after sending, including deterministic replies that do not emit a context-budget event.

- Preserve exact-exercise solution requirements in native source-line repairs. Explain malformed or invalid line ranges and unsupported copied input values without repeating the rejected draft; retain all evidence checks and the two-call limit.

- Keep each structured QA claim bound to one source and its own evidence range; rejected multi-source claims receive explicit repair guidance without repeating the invalid draft.
- Resolve structured 9B evidence selections against exact admitted source lines, rejecting invalid ranges and omitted referenced function premises while retaining the single-repair limit.
- Limit optional development reload scanning to backend source files so model artifacts, datasets and runtime evidence are not repeatedly traversed. Static UI changes need a browser refresh; launcher/configuration changes need a server restart.
- Keep unreadable course names nullable during weekday corroboration. Distinguish registration windows and unscheduled registration forms from class meetings; normalize literal mobile weekday labels and corroborate unique course-code grid columns with local OCR.
- Preserve numbered Markdown exercises when headings and bodies occupy separate reading sections, with separate question and answer provenance; keep unanswered exercises explicit and render heading markers as readable titles.
- Include a matching adjacent numbered Markdown answer as a separately citable reading section after validating the stored exercise identity; retain document and explicit-page scope.
- Preserve decimal, scientific and signed values during claim cleanup. Reject unsupported bare measured values in answer repairs without claiming general arithmetic validation.
- Keep citation repairs focused on the requested result and its source conditions rather than repeating unrelated retrieved exercises.
- Keep private reasoning removed when switching exercise answer views, and remove citation-only parentheses without changing mathematical calls or conditions.
- Bind native image caches to actual weight, tokenizer and processor content so same-size file changes invalidate cached recognition.
- Use the selected native runtime in macOS command-line embedding checks and status, with the selected QA base as the preparation/audit default; avoid legacy model-service probes and false adapter-readiness labels.
- Reject a source/question quotient restated with an erased named function, including citation-repair output. Preserve explicit uncertainty when formula validation fails.
- Restore native curation parsing and calibration, bind model provenance to actual weight/tokenizer bytes, and avoid nested model admission locks. Preparation and calibration remain separate from training authorization.
- Allow uploading images without readable text as chat attachments and canvas viewer documents without triggering OCR character threshold errors.
- Prevent exporting empty iCalendar (.ics) files when no confirmed timetable events exist, validating semester date boundaries and recurring events.
- Reject incomplete timetable export rows, preserve confirmed subject identities and user edits, and express the final semester recurrence boundary in UTC. Make replacement of the existing schedule an explicit confirmation choice.
- Reject quiz distractors that overlap a correct zero comparison, switch its quantity or substitute a generic category for a bit/byte value. Retain one bounded repair for repeated one-character spans and the labeled fallback for ambiguous generated questions.
- Careful QA excludes standalone discourse labels from factual audits while retaining inline conditions and short mathematical claims.
- Preserve the selected local model identity across translation, query normalization and source preparation/audit calls. Separate native Mac and legacy Ollama identities and invalidate translation caches when the backend changes.
- Normalize supported LaTeX fractions, roots, grouped powers, sets and symbols into readable math while preserving identifier case, grouping and source attribution. Unsupported or malformed math requires bounded repair or an explicit refusal; retain unsupported compound vector notation for validation.
- Require complete opening premises in definitions and theorems. Preserve mathematical predicates and exercise-specific conditions; reject question echoes and empty cited labels. Prefer a complete source passage over overlapping fragments during citation repair.
- Retrieve compact and spaced differentials using their complete operands, preserve source-bound escaped comparisons, and validate selected-page vectors without changing relevance thresholds or borrowing another page.
- Forward explicit Ollama reasoning and seed controls for diagnostics, keeping private reasoning out of final answers and recording final usage counts.
- Rebuild citation-repair context from admitted source blocks and one resolved question, excluding old history. Preserve source-backed applicability conditions and assign claims by actual support rather than source position. Normalize escaped comparison symbols only against the cited original expression, preserving literal encoded source content and rejecting unsupported comparisons.
- Resolve approved legacy evidence images after moving the project using the same project name and evaluation path, while retaining source and image hashes, immutable approvals, and traversal and symlink checks.
- Retain original binary download capability for session-only uploads without durable indexing, for both extraction-only and study-generation paths.
- Preserve reviewed text chunks, vector embeddings, provenance and document identity on repeat identical uploads without destructive re-extraction or chunk replacement.
- Propagate resource admission busy exceptions (`GPUBusyError` HTTP 503 and `DialogueBusyError` HTTP 409 with `Retry-After`) during upload embedding admission instead of masking them behind offline fallback.
- Protect source allocation across separate processes using portable cross-process locking and atomic exclusive file creation; roll back failed indexing attempts without leaking raw cache entries or unlinking preexisting sources.
- Fail closed with HTTP 503 before publishing an upload when zero searchable chunks are prepared, accurately report vector readiness from stored rows, and preserve review uncertainty on scanned uploads without claiming automatic OCR verification.
- Preserve original bytes, document records, and chunk scopes on upload filename collision by allocating canonical filenames with content-hash suffixes, keeping identical repeats idempotent.
- Return HTTP 503 with safe non-leaking diagnostics and clean newly created uploaded files on indexing or database failure without altering preexisting sources.
- Distinguish durable uploaded RAG source persistence from parameter training without automatic training approval, and accurately report offline keyword-retrievable chunks when vector embeddings are unavailable.
- Build model quizzes as source clozes so the original context and conditions remain visible. Reject repeated or out-of-source answer spans, duplicate questions and ambiguous overlapping options; retain the labeled fallback when generation fails.
- Regenerate citation repairs from admitted sources using JSON-safe formula notation, without copying a failed draft.
- Extract image transcripts through a strict OCR response schema and reject uniform images as empty content. Keep unverified OCR outside training.
- Reject quiz language drift and options that repeat another option with extra words. Show source evidence as the explanation.
- Preserve multiplication dots in Unicode formulas instead of misclassifying them as legacy Vietnamese encoding.
- Show a recoverable model error without exposing runtime tracebacks or local paths in chat.
- Retain filenames and reading-section labels when selecting non-PDF sources for QA. Recognize requests to state a law without requiring the instruction words in the source.
- Reject missing or invalid citations, truncated answers and incomplete comparison repairs instead of assigning sources automatically.
- Keep attachment questions within the selected document and page, retain configured context limits and require evidence for named topics.
- Reject unreadable image uploads without turning metadata into study content. Validate generated material against the exact source and page.
- Preserve conditions in flashcards by hiding a source phrase in its original passage. Label model generation and extractive fallback separately.
- Check local working changes during changelog validation and preserve references when migrating historical records.
- Run Codacy Bandit security analysis for Python without requiring remote tool configuration; pin the CLI image and fixed UTF-8 reader for SARIF generation, bound scan duration, fail incomplete analysis and upload SARIF reports with the current CodeQL upload action.

## 2026-10-18

### Added

- Ask questions about an uploaded attachment and keep its source for follow-up questions.
- Read documents in a continuous viewer with search, zoom and original-file downloads.

### Fixed

- Preserve DOCX headings, tables and reading sections instead of treating each paragraph as a page.
- Use standard-library XML extraction for DOCX compatibility across supported environments.
- Support WebP extraction in material uploads.

## 2026-10-17

### Added

- Generate quizzes and flashcards from uploaded or indexed learning materials.
- Expose study tools from the topic and attachment workflows.

### Fixed

- Align chat controls, material layouts, model readiness and local setup checks after integration.

## 2026-10-09 to 2026-10-16

### Added

- Windows and macOS launchers, isolated public demo fixtures and portable checks.
- Document/page-scoped QA, conversation history, practice and review workflows.
- Processing levels, source-linked answers and explicit uncertainty states.

### Fixed

- Reject invalid citations and harden extraction, context boundaries, Unicode handling and UI request recovery.
- Preserve unsent drafts during failed requests and reset source selection for new conversations.
- Separate private source, model and runtime artifacts from public code.

<!-- Historical reference IDs are retained for compatibility with earlier change records.
Their original dates and full records remain in Git history. This metadata is not a
public allocation table or a claim that historical validation applies to current code.
history-id: YC-167
history-id: YC-168
history-id: YC-169
history-id: YC-170
history-id: YC-171
history-id: YC-172
history-id: YC-173
history-id: YC-174
history-id: YC-175
history-id: YC-176
history-id: YC-177
history-id: YC-178
history-id: YC-179
history-id: YC-180
history-id: YC-181
history-id: YC-182
history-id: YC-183
history-id: YC-184
history-id: YC-185
history-id: LOG-20261010-ui-pr6-fix-codex
history-id: YC-186
history-id: YC-187
history-id: YC-188
history-id: YC-189
history-id: YC-190
history-id: LOG-20261011-evaluation-execution05-codex
history-id: LOG-20261011-update-agent-ui-qa
history-id: YC-191
history-id: YC-192
history-id: YC-193
history-id: YC-194
history-id: LOG-20261011-shared-public-corpus-tv6
history-id: YC-195
history-id: YC-196
history-id: YC-197
history-id: YC-198
history-id: YC-199
history-id: YC-200
history-id: YC-201
history-id: YC-202
history-id: YC-203
history-id: YC-204
history-id: YC-205
history-id: LOG-20261012-ui-pr13-tv5
history-id: LOG-20261012-ui-pr14-tv5
history-id: YC-206
history-id: YC-207
history-id: YC-208
history-id: LOG-20261014-pr15-merge-audit-tv6
history-id: YC-209
history-id: YC-210
history-id: YC-211
history-id: YC-212
history-id: YC-213
history-id: YC-214
history-id: YC-215
history-id: LOG-20261016-security-issue28-tv4
history-id: LOG-20261016-security-issue29-tv4
history-id: LOG-20261016-ui-issue19-tv5
history-id: LOG-20261016-ui-issue18-tv5
history-id: YC-217
history-id: LOG-20261016-data-issue23-tv4
history-id: LOG-20261016-backend-issue21-tv2
history-id: YC-218
history-id: LOG-20261017-qa-studymaterials-tv2
history-id: LOG-20261017-qa-attachlayout-imageupload-tv2
history-id: LOG-20261017-qa-relocate-study-tools-stretch-ui-tv2
history-id: LOG-20261017-qa-prominent-quiz-flashcard-tool-tv2
history-id: LOG-20261017-qa-integrate-quiz-flashcard-with-topic-tv2
history-id: LOG-20261017-qa-remove-loading-offline-badge-tv2
history-id: LOG-20261017-backend-issue20-tv3
history-id: LOG-20261017-ai-issue24-tv4
history-id: YC-219
history-id: YC-220
history-id: YC-221
history-id: YC-222
history-id: YC-223
history-id: YC-224
history-id: YC-225
history-id: LOG-20261017-eval-cases-issue26
history-id: LOG-20261017-eval-issue27-codex
history-id: YC-226
history-id: YC-227
history-id: LOG-20261018-chat-attachment-canvas-viewer
history-id: LOG-20261018-docx-extraction-grounded-qa
history-id: LOG-20261018-canvas-attachment-fix-and-download
history-id: LOG-20261018-docx-pure-xml-ci-fix
history-id: YC-228
history-id: YC-229
history-id: YC-230
history-id: YC-234
-->
