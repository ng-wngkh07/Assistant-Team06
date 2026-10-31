# Distributing documents safely

A complete local source library and a public release are different inventories. Account for every original in the local library, then publish only the exact files whose redistribution rights and privacy review are complete. Keep unresolved files visible in the private inventory with a reason. Do not report a public library as complete while required files remain pending.

## Review each original

Record the relative filename, original SHA256, size, rights holder, license or written permission, permission evidence, privacy decision and publication decision in an ignored local manifest. Permission must cover the intended audience and distribution method. Owning a downloaded copy, being able to read it, or having indexed it does not establish permission to redistribute it. The repository's MIT license covers code; it does not relicense books, slides, images or model weights.

For original project-authored material, record the owner's release decision. For third-party material, retain the applicable license, attribution and notices. Store private correspondence used as permission evidence locally; publish only notices that are required and safe to disclose. Leave missing or unclear permission pending rather than assigning a license automatically.

Review the original bytes as well as extracted passages for personal details, annotations, embedded attachments and document metadata. A secret scanner can find some credentials; it cannot establish redistribution rights or replace a content review. If a redacted edition is needed, preserve the private original, review the new edition and bind approval and rebuilt vectors to its new hash. Approval of the original does not automatically approve a changed edition.

## Verify the complete extracted content

Full-library acceptance requires the [complete page content review](../AGENT.md#complete-page-content-review) for every physical page or slide, including pages without extraction warnings. Keep a private coverage ledger bound to source hashes, extraction revisions, reviewed passages and original-page evidence. Report complete reviews, partial/HOLD regions, pages awaiting review and accepted passages awaiting index reconciliation separately. Valid vectors and successful text extraction do not prove that the content is complete.

Compare formulas, signs, indices, units, conditions, table relationships, code, captions, diagrams and handwriting with the visible original. Preserve authored errors as source content and record any reviewer explanation separately. A faithful transcription does not certify that every statement in a book is factually correct. Missing or unreadable source regions remain explicit; a separately identified alternative source must never become an invented quotation from the damaged original.

Stage accepted repairs in an isolated database, reconcile the resulting passages and regenerate invalidated vectors before the serving-data and release checks. Preserve original files and personal data. A record marked complete describes only its reviewed page; it cannot approve unreviewed pages or authorize training.

## Separate the deliverables

| Deliverable | Contents | Distribution |
| --- | --- | --- |
| Application repository | Code, installation and usage guides, safe public fixtures and required notices | Git |
| Reviewed knowledge pack | Approved originals, source passages, citation information and matching vectors | A versioned release asset or trusted download, with SHA256 and supported application revision |
| Private working storage | Unapproved originals, permission correspondence, working databases, histories, schedules, credentials and local settings | Remains local |
| Private training storage | Training examples and splits, prompts and responses, evaluations containing private source text, checkpoints and private adapters | Remains local unless a separate release is explicitly approved |

Do not copy the working database into a release. Use the [allowlisted knowledge-pack exporter](knowledge-packs.md#publisher-preparation), which reconstructs the portable document and chunk records and validates their vectors. It does not export arbitrary database tables. The exporter requires a source hash, license, explicit redistribution review, and complete source-bound per-physical-page review evidence (`--page-reviews`) for every selected original. Those fields record a publisher's actual review; setting a boolean does not perform that review.

Vectors, extracted text, titles and original files all belong within the same publication decision. Embeddings are not a confidentiality boundary. Removing training tables does not make private source passages safe to publish. A ZIP file, `.gitignore`, Git LFS or an unlisted download link does not restrict access once content is public.

## Reconcile and verify a release

1. Freeze the approved originals and application revision. Recheck source hashes, extraction quality, unresolved holds and vector compatibility. Indexing, source acceptance, redistribution permission and training approval remain separate decisions.
2. Export into a new path using the reviewed allowlist and verified physical-page review manifest (`--page-reviews`). The exporter reconciles expected production chunks against reviewed page transcripts, checks blank page declarations, and records portable review coverage in the pack. Inspect every archive member, source notice and portable record. Check for unrelated documents, local paths, credentials, private metadata, history and training artifacts.
3. Reconcile the private inventory against the exported manifest. Record each included, excluded and pending original. If the requirement is the entire source library, every original must have a resolved decision and every required public original must be present. An unindexed or held file cannot be silently omitted and counted as delivered.
4. Install the exact archive into an isolated fresh checkout. Verify original downloads and citations, model answers, study tools, restart persistence and the absence of an embedding rebuild. Test each advertised runtime with its matching pack protocol.
5. Publish the reviewed asset with its digest, required notices and supported application revision. Keep permission evidence and private inventory details out of public issues, PR bodies, logs and screenshots.

Anyone receiving a public pack can read and copy its contents. If a source is restricted to named recipients, use a separate access-controlled distribution channel after permission is established. The current Study Agent API has no per-user authorization and must not be exposed as a private multi-user document service. Encryption can protect a transfer, but does not stop an authorized recipient from copying a decrypted document.

Approval of an entire source directory applies to a frozen inventory of exact files and hashes. It does not approve files added later, including documents uploaded by recipients. Include every approved original and its compatible retrieval data in the release asset; the original directory does not need to enter Git history. An application bundle initializes a separate personal working library from that public seed. Never rebuild a public release by copying a recipient's working library or exporting all newly discovered files without a new publication review.

When permission is unavailable, document the source's official acquisition link and let each user import their own permitted copy locally. Do not include the original, extracted text or its vectors in the public pack. Any resulting gap must be stated in the release coverage.
