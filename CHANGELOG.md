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

### Added

- Show the last packed context budget, output reserve and omitted-history warning, with an explicit new-chat action that preserves the next draft. Label live draft byte size separately from token usage.
- Report missing timetable fields, clipped course names and invalid time ranges in editable drafts, including cached results. Keep insufficient-information learning examples separate from positive schedule labels and training approval.
- Scan Python, JavaScript, GitHub Actions and the standalone Apple Vision Swift helper with CodeQL; compile the helper explicitly on macOS.
- Add sample university timetable and course-registration screenshots in `calendar_src/` as references for timetable import.
- Provide a read-only Qwen3.5-9B training-plan draft with model configuration checks and separated base, dataset and adapter paths.
- Add a public, synthetic 12-topic corpus with 96 review cases and a 14-course image/JSON timetable fixture for isolated debugging; these fixtures are not approved training data or an independent model-quality holdout.

### Changed

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
- Select the pinned local MLX Qwen3.5 9B 4-bit base on Apple Silicon with an 8192-token operational budget. Keep legacy 3B identity and adapter compatibility separate; model installation does not authorize training or adapter activation.
- Prefer paragraph, line, sentence and word boundaries when indexing new text, retaining exact source slices and configured overlap. After accepted hybrid retrieval, add bounded adjacent chunks from the same document, filename and page as independent citation sources; preserve existing indexes and evidence gates.
- Consume canonical material filenames after upload in the client interface for preview, active attachment scoping, QA, downloads, and generation.

### Fixed

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
