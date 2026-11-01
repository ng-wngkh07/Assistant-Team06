# Changelog

Changes are grouped by their effect on the product. Unreleased work has not been published as a release.

## Unreleased

### Security

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

- Add a hierarchical two-level parent-child topic tree (`app/topic_tree.py`) shared between QA and topic-based learning (`app/learning.py`), mapping course subtopics under parent academic domains.
- Render `.pptx` slides and `.docx` pages as visual PNG previews via `app/slide_preview.py` for `/api/documents/{document_id}/pages/{page_num}/image` and `/api/ocr/page-image`.
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
