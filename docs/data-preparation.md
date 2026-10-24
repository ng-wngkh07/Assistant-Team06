# Complete-page data preparation

Preparation accounts for every physical page of every permitted source. Inclusion in the inventory is distinct from approval for training. Private PDFs, transcripts, databases, vectors, preparation manifests and model artifacts remain in ignored local storage. The public synthetic fixtures support reproducible debugging; they do not authorize training on private sources.

## One complete work queue

Freeze source hashes, physical page counts, database identity, review lineage and approved exclusions before processing. Use one resumable queue for the entire inventory, with atomic checkpoints inside the job. Internal checkpoints protect completed work after an interruption; they are not separate acceptance releases.

Reuse existing reviews only when they bind to the same source bytes, physical page and complete reviewed content. Re-extract or re-review changed and unresolved pages. Cache rendered pages and OCR drafts by source hash and page number. Use bounded CPU concurrency for extraction and hashing, and serialize model workloads through the application's shared resource coordinator. Keep an embedding model loaded only while the shared resource lock is held. Before yielding that lock between bounded work units, unload its weights and release device arrays so verification can continue on a 16 GB Mac.

OCR output, matching hashes, agreement between extractors and successful vector generation are diagnostic evidence. They do not replace comparison with the source. Preserve formulas, conditions, code, tables and reading order; record ambiguity instead of filling it in. A clipped source or unreadable passage remains on hold.

## Coverage and source acceptance

Give each physical page a manifest record containing its source hash, page number, review state, evidence location, indexed chunk identifiers and eligibility decision. Check that page numbers have no gaps or duplicates and reconcile page totals against the actual files. Record blank pages and approved exclusions explicitly. Preserve historical holds and exclusions until new evidence supports a reviewed change.

Only complete, source-verified and permitted factual content can enter the positive-answer training view. An incomplete image can separately support a reviewed `insufficient_information` example: retain readable values, label the missing fields and supervise a request for more information. This does not approve an invented transcription or a complete schedule. Record its task and review decision separately from positive content acceptance. Other pages remain visible in the preparation inventory with a specific reason and next action. Do not pad an approved dataset to obtain apparent 100% coverage. Report both inventory coverage and eligible content coverage, with separate counts for pending, partial, excluded and missing-source records.

## Vector integrity

Bind each vector to the exact chunk text, source page, embedding artifact and pooling policy. Record the text and vector SHA-256 hashes, dimensions and full-text status. Verify that all values are finite, vectors have the expected dimensions and normalization, and their text has not changed since embedding. Check every row; one matching row does not establish a database's embedding basis.

Tokenize the complete input before embedding. Reject an over-limit chunk or split it with explicit source bounds; never silently truncate it and label the vector as covering the full text. Do not compare vectors from different models, revisions or pooling policies, even when their dimensions match. Validate related and unrelated retrieval examples against their actual source passages in addition to structural checks.

Prepare and audit an isolated database copy before any serving-data change. Keep SQLite integrity, foreign-key, full-text-search, source-binding and vector audits separate from content review. A complete inventory or an audit with no structural errors does not itself approve the content for training.

## Correcting a damaged source passage

Render the original physical page and verify its PDF and image hashes before changing extracted text. Bind each correction to the exact old chunk digest and the visible source span. A clearly readable displayed formula may resolve a missing symbol in the same passage; record that evidence explicitly. Keep the original PDF and earlier failed evaluation unchanged.

Apply the smallest supported correction in a separate database first. Rebuild vectors for every changed chunk and update their text/model/vector proofs together with full-text search. Compare the affected answers against the rendered source, including conditions and units. A corrected span does not approve the rest of its page or remove existing HOLD decisions.

Any authorized application to serving data needs a recoverable backup, a transaction, rechecked source/chunk identities and post-write retrieval verification. Stop on changed preconditions rather than overwriting newer uploads or edits. Record the exact affected rows and preserve unrelated documents, history, review status and model/adapter selection. Source correction remains separate from training acceptance.

## Training remains a separate decision

The preparation result must state unresolved counts and whether the corpus is complete. Keep `training_allowed=false` until the applicable source, split, leakage, tokenizer, model-memory and quality gates have passed and a run is explicitly authorized. Source preparation does not activate an adapter or change the serving library. See [model selection](model-selection.md) and [question complexity](question-complexity.md).
