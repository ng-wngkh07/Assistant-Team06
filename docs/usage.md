# Using Study Agent

## Ask and verify

Choose an indexed document or attach a supported file, then ask a specific question. Open the cited passage and check that it supports the answer, including formulas, units and conditions. Follow-up questions can reuse the session's source. Start a new conversation to clear the current draft and source selection.

The document viewer supports reading, search, zoom and downloading the original file. DOCX, text and Markdown use extracted reading sections; these are not guaranteed to match physical printed pages. PDF references use the source page numbers.

## Processing levels and result states

Fast (`standard`) is the default. Careful processing adds a claim-to-evidence audit and may return an unverified result if that audit cannot finish. Complex-question suggestions let you select careful; they do not automatically send another request. The API still accepts explicit `auto` for compatibility.

An ambiguous question may require clarification. A narrow page scope or missing evidence may require a different source. A model error or interrupted generation should remain an explicit failure, not a fabricated answer. Retrieval scores are ranking signals, not probabilities of correctness.

## Study materials

Upload PDF, DOCX, PPTX, TXT, Markdown, PNG, JPEG or WebP, up to 20 MB. Images can be attached and viewed even when they contain no readable text. Generating source-based study material still requires readable extracted content; names, dimensions and image metadata are not document evidence.

Uploaded materials persist in the configured source directory and are indexed into the local knowledge base using cross-process allocation locks and atomic exclusive creation. Application bundles use `data/user-library/src/`; source checkouts default to `src/` unless configured otherwise. The library area displays the actual directory. It is created automatically and retained after restart. Upload through the application to save and process a new document; copying a file directly into the folder does not automatically make it searchable. Keep a backup of the complete personal library when moving or updating the application.

Same-name files with different bytes receive a content-hash suffix to avoid overwriting existing documents and to preserve separate document and chunk scopes. Identical repeat uploads remain idempotent, returning existing document identities and counts without re-extraction or chunk replacement, preserving human-reviewed chunks, vector embeddings and provenance. The interface consumes canonical filenames for active attachment scoping, QA, previews, original file downloads, and generation. When source persistence is explicitly opted out, session-only uploads retain original binary downloads in memory without writing originals or indexing durable chunks. Local uploads never modify the publisher cache or authorize public redistribution, central collection or model training.

Uploaded sources must produce searchable chunks to be admitted; empty extractions fail closed with a safe HTTP 503 and clean only the newly allocated source attempt without removing existing files. Resource contention during admission preserves retryable busy responses (GPU busy HTTP 503 and dialogue busy HTTP 409 with Retry-After headers) rather than masking them. Persisting sources for local RAG retrieval and question answering is strictly distinct from parameter training; uploaded materials receive no automatic training approval. Offline embeddings preserve real keyword-retrievable chunks and report status from actual stored rows rather than claiming vectors are ready from backend health. Automatic OCR on scanned uploads preserves review uncertainty rather than claiming human verification.

Model quizzes ask you to choose a missing phrase in the original source passage. Flashcards hide a source phrase for free recall. Both retain the surrounding context and conditions, in the source language. Quizzes include the answer and exact source evidence after you respond. If model output cannot be used, the application labels the extractive fallback. Fallback material is basic reading practice, not a reviewed assessment.

Exact quote validation checks source membership. It does not certify the educational quality of a quiz or its distractors. Review generated materials before using them for assessment. The current browser flow selects a whole document; page scope is also supported by the generation API.

## Exercises and review

Use study tools to find exercises and available answers, create practice questions and revisit uncertain responses. Exercise sources and answer sources are separate. Review progress stored in the browser applies to that browser; it is not a synchronized learner account.

For topic practice, select an authored subject, a count from 1 to 10, and easy, medium or hard difficulty. The application checks candidates against the selected source and requested level. If it cannot verify the full set, it reports the returned count and an explicit shortage; a partial set is not completion of the requested task. Medium and hard practice do not silently fall back to easy missing-phrase questions. Inspect the question, alternatives, answer explanation and source together.

## Timetables

Upload a timetable image, compare every subject and time with the original, correct uncertain fields, choose whether to replace the existing schedule, and confirm before exporting. The export uses confirmed rows. Review the semester range and resulting calendar contents. See the [recognition and calendar workflow](timetable-workflow.md) for the full procedure and public multi-subject fixture.

## History and privacy

Completed conversations are stored locally. Repeating a completed request with its request identifier can return the saved result without another generation. Do not use personal conversations or uploaded documents as model-training data without a separate, explicit process.

## Conversation context

The chat shows the most recent packed context budget and warns when earlier turns are omitted. A new chat can keep your unsent draft. Draft byte size is not an exact token count. See [Local context management](context-management.md) for capacity examples, memory tradeoffs and continuation options.
