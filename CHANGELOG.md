# Changelog

Changes are grouped by their effect on the product. Unreleased work has not been published as a release.

## Unreleased

### Security

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

- Scale backend concurrency for 20+ authenticated users while keeping a clean boundary for future multi-host expansion: isolate per-account `dialogue.lock` files (`account_dialogue_gate`) so session, history, and planner CRUD never block on another account's model work, replace immediate global-lock rejection on authenticated inference/upload/OCR/index routes with a bounded FIFO queue (`acquire_inference`, `STUDY_MAX_INFERENCE_QUEUE=20`, `STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS=25.0`, one active/queued inference request per account), expand `scripts/dev.py` `ENV_KEYS` and `.env.example` for hosted scaling/security settings, and expose sanitized `infrastructure` (disk headroom, read-only KB readability, writable accounts directory, security posture, admission/storage limits) and `admission` telemetry on `/api/health`, `/api/status`, and `scripts/backend_service.py` (with HTTPS/cookie/registration warnings and preflight disk/writability validation).
- Add macOS backend recovery controller (`scripts/backend_service.py`) and reusable operator launcher templates (`launchers/macos/backend-quick-start/`) providing session-detached supervision, strict process-identity verification, foreign-port fail-closed protection, preflight validation of explicit paths without demo/DB seeding, bounded log rotation, bounded crash recovery, and status separation of web, model/QA, and corpus readiness.

### Fixed

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
