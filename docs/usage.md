# Using Study Agent

## Choose your workspace

For the hosted application, open the operator-provided HTTPS URL and sign in. Processing and private storage run on that backend host; a remote upload leaves the browser. On a local installation, the backend and its files are on your computer. Local loopback guest use remains available unless the operator requires accounts. Account history/planner/uploads are separate from the guest workspace and other accounts; guest data is not automatically imported.

## Ask and verify

Choose an indexed document or attach a supported file, then ask a specific question. Open the cited passage and check that it supports the answer, including formulas, units and conditions. Follow-up questions can reuse the session's source. Start a new conversation to clear the current draft and source selection.

The document viewer supports reading, search, zoom and downloading the original file. DOCX, text and Markdown use extracted reading sections; these are not guaranteed to match physical printed pages. PDF references use the source page numbers.

PPTX previews show the original slide layout through a local LibreOffice executable or a source-bound static rendition supplied with the local library. If rendering is unavailable or the file cannot be safely converted, download the original presentation instead. DOCX references identify a reading section and offer the original Word file; they do not show a fabricated page image. Registered library originals are available through `/api/documents/{id}/original`; the existing `/pdf` endpoint remains PDF-only.

Rendering can still substitute missing fonts or misdisplay legacy text and embedded equations. Compare important formulas and labels with the original presentation. A matching source hash binds the preview to a file; it does not certify every slide's visual accuracy.

## Processing levels and result states

Fast (`standard`) is the default. Careful processing adds a claim-to-evidence audit and may return an unverified result if that audit cannot finish. Complex-question suggestions let you select careful; they do not automatically send another request. The API still accepts explicit `auto` for compatibility.

An ambiguous question may require clarification. A narrow page scope or missing evidence may require a different source. A model error or interrupted generation should remain an explicit failure, not a fabricated answer. Retrieval scores are ranking signals, not probabilities of correctness.

## Study materials

Upload PDF, DOCX, PPTX, TXT, Markdown, PNG, JPEG or WebP, up to 20 MB. Images can be attached and viewed even when they contain no readable text. Generating source-based study material still requires readable extracted content; names, dimensions and image metadata are not document evidence.

Upload behavior depends on the active workspace:

| Workspace | Original and retrieval storage | Shared library effect |
| --- | --- | --- |
| Signed-in account | Private durable originals and extracted records under `STUDY_ACCOUNTS_DIR/<account_id>/`; retained after restart | No shared `src/` insertion; leave the shared-library save option off |
| Local guest, instant use | Temporary in-memory material/original cache | No durable shared indexing; lost after backend restart |
| Local guest, explicitly save to library | Configured shared source directory and knowledge database | Extracts/indexes the new permitted file and retains it |

For guest library persistence, `/api/materials/upload` and `/api/materials/inspect-library` require `persist_to_src: true`; omitted/null/false selects instant use. For accounts, requesting shared persistence is rejected with guidance, while private originals/records are persisted by the account service independently. The private material ID scopes later retrieval and original downloads. Uploading is not permission to publish or train on that content.

Guest saved-library uploads use exclusive file creation and collision handling; distinct same-named content receives a hash suffix. Identical repeats retain the existing document/chunk identities and reviewed data. Both hosted and local `--profile local` checkouts resolve the configured shared `src` (`AGENT_SRC_DIR`, default `src`) and `data` (`AGENT_DATA_DIR`, default `data`) without auto-seeding `data/user-library/`. Account uploads always use private storage (`STUDY_ACCOUNTS_DIR/<account_id>/`). The actual directory appears only where appropriate for local operators; hosted users do not need host filesystem access.

Files copied directly into the shared directory are detected only when the operator enables [automatic ingestion](configuration.md#automatic-source-ingestion). Otherwise synchronize manually. Modified/missing indexed originals require reviewed re-indexing; stale sources are excluded rather than silently serving mismatched evidence. Account uploads are processed through their private upload flow, not this watcher. Keep consistent backups when moving/updating the backend.

Uploaded sources must produce searchable chunks to be admitted; empty extractions fail closed with a safe HTTP 503 and clean only the newly allocated source attempt without removing existing files. Resource contention during admission preserves retryable busy responses (GPU busy HTTP 503 and dialogue busy HTTP 409 with Retry-After headers) rather than masking them. When study-set generation fails after valid extraction because the local model runtime or configuration is unavailable or busy, `/api/materials/upload` retains the extracted document and raw file and returns an explicit HTTP 503 (or HTTP 409 when dialogue-busy) without exposing host paths or tracebacks, and `/api/materials/generate-from-library` similarly returns a bounded HTTP 503 while invalid or empty inputs remain HTTP 422. Public and authenticated JSON/SSE API responses omit internal host filesystem paths (such as `review_path` and `filepath`) while keeping document titles, page numbers, grounding quotes and API viewer/download links intact. Persisting sources for local RAG retrieval and question answering is strictly distinct from parameter training; uploaded materials receive no automatic training approval. Offline embeddings preserve real keyword-retrievable chunks and report status from actual stored rows rather than claiming vectors are ready from backend health. Automatic OCR on scanned uploads preserves review uncertainty rather than claiming human verification.

Model quizzes ask you to choose a missing phrase in the original source passage. Flashcards hide a source phrase for free recall. Both retain the surrounding context and conditions, in the source language. Quizzes include the answer and exact source evidence after you respond. If model output cannot be used, the application labels the extractive fallback. Fallback material is basic reading practice, not a reviewed assessment.

Exact quote validation checks source membership. It does not certify the educational quality of a quiz or its distractors. Review generated materials before using them for assessment. The current browser flow selects a whole document; page scope is also supported by the generation API.

## Exercises and review

Use study tools to find exercises and available answers, create practice questions and revisit uncertain responses. Exercise sources and answer sources are separate. Review progress stored in the browser applies to that browser; it is not a synchronized learner account.

For topic practice, select an authored subject, a count from 1 to 10, and easy, medium or hard difficulty. The application checks candidates against the selected source and requested level. If it cannot verify the full set, it reports the returned count and an explicit shortage; a partial set is not completion of the requested task. Medium and hard practice do not silently fall back to easy missing-phrase questions. Inspect the question, alternatives, answer explanation and source together.

## Study planning

Follow four screens: confirm the class timetable, add personal commitments and study windows, review an editable plan with dated deadlines, then export an iCalendar file with deadline reminders. Review every recognized subject and time against the image before confirmation. See [study planning](study-planning.md) for the full workflow and notification limits, and [image recognition](timetable-workflow.md) for the public multi-subject fixture.

## History and privacy

Completed conversations are stored on the backend: in the signed-in account store or the local guest history database. Repeating a completed request with its request identifier can return the saved result without another generation. Do not use personal conversations or uploaded documents as model-training data without a separate, explicit process.

## Conversation context

The chat shows the most recent packed context budget and warns when earlier turns are omitted. A new chat can keep your unsent draft. Draft byte size is not an exact token count. See [Local context management](context-management.md) for capacity examples, memory tradeoffs and continuation options.
