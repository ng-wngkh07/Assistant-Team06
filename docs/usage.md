# Using Study Agent

## Ask and verify

Choose an indexed document or attach a supported file, then ask a specific question. Open the cited passage and check that it supports the answer, including formulas, units and conditions. Follow-up questions can reuse the session's source. Start a new conversation to clear the current draft and source selection.

The document viewer supports reading, search, zoom and downloading the original file. DOCX, text and Markdown use extracted reading sections; these are not guaranteed to match physical printed pages. PDF references use the source page numbers.

## Processing levels and result states

Automatic processing selects a workflow from the question. Standard processing generates and checks a sourced answer. Careful processing adds a claim-to-evidence audit and may return an unverified result if that audit cannot finish.

An ambiguous question may require clarification. A narrow page scope or missing evidence may require a different source. A model error or interrupted generation should remain an explicit failure, not a fabricated answer. Retrieval scores are ranking signals, not probabilities of correctness.

## Study materials

Upload PDF, DOCX, PPTX, TXT, Markdown, PNG, JPEG or WebP, up to 20 MB. Images must produce readable OCR; names, dimensions and image metadata are not document evidence.

Quizzes include an answer, explanation and source passage. Flashcards use a phrase selected from a passage to create a source cloze: the application hides that phrase while retaining the surrounding conditions. If model output cannot be used, the application labels the extractive fallback. Fallback material is basic reading practice, not a reviewed assessment.

Exact quote validation checks source membership. It does not certify the educational quality of a quiz or its distractors. Review generated materials before using them for assessment. The current browser flow selects a whole document; page scope is also supported by the generation API.

## Exercises and review

Use study tools to find exercises and available answers, create practice questions and revisit uncertain responses. Exercise sources and answer sources are separate. Review progress stored in the browser applies to that browser; it is not a synchronized learner account.

## Timetables

Upload a timetable image, inspect the draft, correct uncertain fields and confirm before saving or exporting. Recognition output is not automatically accepted as a schedule.

## History and privacy

Completed conversations are stored locally. Repeating a completed request with its request identifier can return the saved result without another generation. Do not use personal conversations or uploaded documents as model-training data without a separate, explicit process.
