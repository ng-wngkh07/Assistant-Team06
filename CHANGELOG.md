# Changelog

Changes are grouped by their effect on the product. Unreleased work has not been published as a release.

## Unreleased

### Changed

- Reorganize public documentation in English around product usage, setup, architecture, testing and maintenance. Remove internal allocation and submission records from public guides.
- Preserve the existing attachment and document-viewer behavior while updating the question-answering integration against current main.

### Fixed

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
- Run Codacy Bandit security analysis for Python without requiring remote tool configuration; update the pinned scanner, bound scan duration, fail incomplete analysis and upload SARIF reports with the current CodeQL upload action.

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
-->
