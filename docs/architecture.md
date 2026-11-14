# Architecture

See the [guide index](README.md) for installation, learner and operations workflows. Runtime assets belong outside public code; `tests/fixtures/` contains safe synthetic inputs and `scripts/` contains development/maintenance tools.

The product has one FastAPI backend in `app/` and one primary web interface in `static/`. `run.py` starts the service; `scripts/dev.py` applies the selected environment profile.

## Question answering

1. Extract documents and preserve source identity, page or section, and raw text.
2. Build SQLite FTS5 and optional embedding indexes.
3. Resolve the question and its document/page scope, including attachment follow-ups.
4. Retrieve passages and pack them within the configured model context.
5. Generate an answer and validate citation identifiers and claim mappings. Careful processing adds an evidence audit.
6. Return an answer, clarification, insufficient-evidence result or explicit generation failure. Save the completed turn and its citations in the active account history or local guest history.

Sources are untrusted input. Instructions inside a document must not become application instructions. A citation must refer to an admitted passage; an accessible link alone does not establish support for a claim.

## Chunking and retrieval context

Newly indexed text uses a hierarchy of paragraph, line, sentence, word and finally character boundaries within the legal chunk window. A simple pipe-row check avoids an internal table line break when another legal line boundary is available. This is a boundary heuristic, not a complete heading, table or code parser. Each chunk remains an exact slice of the stripped page text with character offsets and exact overlap. Defaults remain 1200 characters and 200 characters of overlap.

Hybrid retrieval combines SQLite FTS5/BM25 and embeddings with reciprocal rank fusion and the existing content heuristics. It does not use a cross-encoder reranker. Once the original hits pass relevance and partial-evidence checks, QA can add their immediate previous and next chunks. The database identity and original text of each hit must match, and every neighbor must share its document ID, filename and page or reading section. Neighbor expansion never crosses pages or follows neighbors recursively.

For questions that explicitly name a differential, FTS also probes compact and spaced spellings of that operand. Ranking compares the complete operand, including variable case, derivative order and coefficient, rather than borrowing a different differential or a repeated question. The probe stays within the existing document scope and candidate limit; source quality and heading/catalogue demotion still apply. This is a retrieval heuristic, not symbolic algebra or a guarantee of semantic support.

Original hits retain their order and scores. Whole neighbors are appended as independent, sanitized citation sources with their own source-quality warnings and no inherited retrieval scores. Expansion adds nothing beyond a total of 12 chunks or 12000 source characters; original hits remain intact even when already above those expansion limits. The existing token budget still controls final prompt admission. If expansion fails, QA keeps the original hits. Explicit page retrieval keeps its selected document/page scope and does not expand to neighbors; whole-document overview retrieval is unchanged.

Markdown extraction can store a numbered exercise heading and its body as separate reading sections. Exercise lookup retains the heading provenance and the exact question-body and answer-body sources. For library or document QA, an exact stored exercise-body hit can add the answer two reading sections later only when the intervening answer heading matches the preceding exercise number. This bounded layout rule stays within one document and filename; it does not cross PDF pages or expand an explicitly selected page. It is not a general parser for arbitrarily arranged exercise collections.

For an explicitly selected page, QA can score only the exact selected passages against the question using their existing stored vectors and the configured embedding model. Stored vectors must match the actual artifact identity, exact text and vector hashes, full-text flag, dimensions and normalization. Missing or invalid vectors provide no semantic evidence. If optional embedding identity resolution or inference is unavailable, retrieval keeps its lexical path. No other page supplies a selected-page score. The existing lexical checks and 0.52 semantic relevance threshold still apply; selecting a page alone does not establish that it answers the question. The page-retrieval API remains available to practice callers without requiring embedding generation.

Citation repair rebuilds its context from the already admitted, escaped source blocks and one resolved question. It excludes old conversation history. When space permits, a rejected structured draft is included only as escaped, explicitly untrusted data; the admitted sources and answer reserve take priority over that optional draft. Repair instructions require a direct answer to the question, including a requested calculation or result already present in the evidence. Applicable formula conditions, domains, units and the subject of each mathematical relation must remain supported by their own admitted sources; unrelated neighboring topics must be omitted. A claim may cite the same source as another claim or a different admitted source according to its actual support, rather than the position of a source in a schema example. When passages overlap, repair prefers one passage containing the complete premise and conclusion over a fragment cut off before the conclusion. Repair generation requests readable mathematical notation. Structured repair can normalize an escaped comparison only when its operands and operator match a literal comparison in the cited original source; an exact encoded spelling in the cited source takes precedence and remains unchanged, and unsupported comparisons fail citation validation. Operands retain compound expression context across mathematical operators and whitespace, including a spaced differential such as `d Q/dt`. This is a bounded representation check, not an algebraic proof or a general HTML decoder. Source escaping and decoded-markup rejection remain intact. Existing context limits and citation validation still apply.

For the pinned 9B model, structured claims select an inclusive range of numbered lines from one admitted source. The backend resolves the exact original slice; line numbers never become evidence text. Invalid ranges, conflicting explicit quotations and missing referenced function definitions are rejected. This bounded dependency check does not establish general semantic entailment, so real-model source review remains required.

Citation validation rejects exact echoes, standalone unanswered questions and empty cited labels, including a partly complete answer with an empty condition. It permits the existing single repair and fails closed if that repair is still incomplete. Repair uses a neutral claim example, follows the question language and distinguishes the requested exercise from other items within the same passage. Answer cleanup requires explicit source framing before removing a citation introduction, preserving ordinary predicates such as “tồn tại” and the case of leading mathematical identifiers, including differentials. These checks do not replace source/content review or repair damaged source extraction.

Generated answers and repair claims share a bounded, nonexecuting math normalizer. Supported LaTeX fractions, roots, grouped powers and subscripts, number sets and symbols become readable Unicode/plain text. It preserves grouping, identifier case and distinct supported symbol variants. Unsupported or malformed recognized math invalidates the draft and uses the existing single repair; a failed repair returns a generation failure without citations. Ordinary paths and currency remain text. This representation step does not prove an equation, supply missing premises or establish semantic support for a citation.

This bounded previous/next window uses the small-to-big retrieval idea without a persisted parent hierarchy or an additional model call. Existing indexes require no migration for the context window. The splitter affects future indexing only; applying new boundaries to an existing corpus requires a separately reviewed rebuild that preserves source reviews and exclusions. No automatic rebuild, training or adapter activation occurs. Reverting these code changes requires no database migration.

Text quiz validation additionally rejects overlapping truth regions for scalar or probability comparisons to zero, including a change of operand. When a source answer is a bit/byte value, alternatives must use concrete numeric unit values rather than broad categories. These are narrow checks, not a general proof that all distractors are false. Medium and hard assessments require a separate full-passage check of source support, explanation, unique answer, preserved conditions and requested difficulty. They fail explicitly if no candidate passes; an easier extractive question cannot silently replace the requested difficulty. Easy source cloze flashcards retain the whole admitted passage and its conditions. Partial sets expose their actual count and a warning.

Practice topics come from an authored concept catalogue and its explicit aliases, rather than model-generated chapter headings. Passage deduplication compares the complete normalized body, preserving different pages with the same course header. Hard-question source ranking favors explicit conditions and rules; ranking does not certify assessment difficulty. The generation schema binds each passage handle to a finite set of exact, contiguous excerpts from the displayed source, preventing invented quotations. This verifies quotation membership only. Review uses that exact excerpt as rule evidence; surrounding original text can reveal omitted conditions or contradictions but cannot supply missing rules. Answer, explanation, uniqueness, conditions and requested difficulty still require separate verification. Hard prompts require applying at least two explicit source rules to a concrete situation; a prompt instruction alone does not establish that the generated assessment satisfies this requirement.

Native practice reviews up to three candidates together under one shared request deadline, preserving a separate source witness, solution, choice comparison and explanation audit for each index. For a four-candidate proposal, it reviews the first three together and reviews the reserve only when fewer than three distinct questions pass. A reserve timeout can return only the already verified subset with an explicit incomplete count. Complete worked examples or statements can guide candidate generation without certifying source sufficiency. A bounded polynomial checker removes proven equivalent distractors before model review; unfamiliar expressions remain subject to source review. Conservative admission checks exclude unsupported certainty and causal questions without explicit causal wording in the cited excerpt. These checks can exclude valid implicit phrasing and do not prove semantic entailment. Statement-based proposals avoid causal questions. For a native three-question statement request, generation uses one batch with at most one reserve candidate; full review stops after three distinct accepted answers. Generation and review retain the same 180-second total deadline. Causal candidates that pass admission still require all four independent content audits. The admission policy does not rewrite model verdicts or certify general causal reasoning.

For native reviews of up to three candidates, the reviewer must quote a `source_basis` inside the exact cited excerpt and enumerate `matching_choices`. Admission requires one matching letter consistent with the independently verified key, in addition to the existing four audits and verdicts. Context cannot supply a missing source basis. This binding check does not establish semantic entailment; source and content review remains necessary. Trusted instruction prefill can be reused only inside the current native review request, with model, adapter and prefix bindings. Question text, source passages and generated answers are not cached across requests.

For the default three-question native practice request, complete numbered worked derivative examples can supply bounded proposals directly. This route recognizes only the supported constant, power-rule and product-rule solution forms, preserves the cited mathematical steps and constructs four polynomial-distinct choices. Every proposal still passes the native source reviewer within the unchanged shared 180-second deadline. Unrecognized examples retain model generation; this optimization is not a general raw-model generation improvement. Conservative admission also excludes near-identical choices differing only by a leading English or Vietnamese article; this is an ambiguity filter, not a general semantic equivalence proof.

The `grounded_review_v2` native profile defaults to the base model. It cannot inherit adapter qualification from `compact_review_v1`. A versioned grounded manifest must independently pass the existing 14-case, three-source-family and three-case native application gates, including source/content and paired latency checks. Grounded acceptance additionally requires all six verdict fields, including missing-input qualifier flags, and strictly better source/content accuracy than the current unadapted review profile. The native worker accepts only the exact evaluated singleton prompt, schema and decoding scope; legacy and grounded adapters cannot cross profiles. Grounded qualification requires the current exact-source and matching-choice witnesses, including consistent empty matches for missing-input refusals; legacy-shaped outputs cannot qualify. Native review orders source sufficiency, independent conditions and whole-choice verdicts before explanation grading, retaining all four audits and every acceptance flag. Models API metadata reports the actual qualified practice adapter separately from general QA. No grounded adapter is currently accepted. Training completion, valid JSON and successful software tests do not establish content acceptance or authorize adapter activation.

## Model roles

On Mac, the selected text model loads directly from local MLX weights in the isolated Python worker. QA and text helpers must resolve the same model identity, tokenizer and chat template. The operational window is 8192 tokens, independently of advertised capacity. Hidden thinking is disabled; Careful mode is a separate source-verification workflow.

| Role | Runtime boundary |
| --- | --- |
| Grounded QA and text helpers | Direct local Python MLX, pinned 9B base; no silent provider or 3B fallback |
| Retrieval vectors | Separately validated local embedding artifact; never silently mix embedding spaces |
| Image extraction | Local OCR/native vision runtime and source-review gates |
| Fine-tuning | Local MLX on the intended same base, gated data and hardware checks, separately accepted adapter |
| Windows compatibility | Explicit local development runtime; never a remote fallback for the Mac |

Keep the dialogue and GPU coordinator around model work. Preserve subprocess lock inheritance and serialize workloads that cannot fit together. Switching QA needs a backend restart and runtime readback, with no source database replacement. Inference fit does not establish training fit. The current 9B base is unadapted; retired adapters are not discovered or activated. The training-plan command produces a blocked, read-only proposal. A compatible training protocol, reviewed data and explicit authorization are required before training or accepting an adapter.

## Components

| Component | Responsibility |
| --- | --- |
| `app/server.py` | HTTP API, account-bound entry page, request lifecycle, source scope and history integration |
| `app/request_admission.py`, `app/runtime_health.py` | Early ASGI security headers/HSTS, sliding-window HTTP 429 rate limiting, mutation body caps, MLX runtime probe and disk/DB/posture infrastructure checks |
| `app/accounts.py`, `app/account_services.py`, `app/db/` | Authentication/session middleware, request-scoped private originals, per-account upload storage quotas, histories, planners and account migrations |
| `app/src_watcher.py`, `app/source_freshness.py` | Opt-in incremental shared-source admission, freshness checks and shutdown lifecycle |
| `app/native_embedding.py` | Cancellable native embedding subprocess and owned-process cleanup |
| `app/rag_agent.py`, `app/dialogue.py` | Retrieval, prompt budget, answer validation and serialized dialogue |
| `app/searcher.py`, `app/indexer.py` | Hybrid retrieval and index maintenance |
| `app/pdf_extractor.py`, `app/document_lookup.py` | Extraction, source access and document lookup |
| `app/study_materials.py` | Upload validation, quizzes, flashcards and source provenance |
| `app/history.py`, `app/summarizer.py` | Sessions, replay and topic summaries |
| `app/practice.py`, `app/learning.py`, `app/topic_catalog.py` | Reviewed exercises, study workflows and authored concepts |
| `app/vector_integrity.py`, `app/vector_rebuild.py` | Exact vector provenance, complete staging and atomic activation |
| `app/knowledge_pack.py` | Allowlisted publisher export and verified portable installation |
| `app/timetable_vision.py`, `app/timetable_store.py` | Image recognition, draft confirmation and timetable storage |
| `app/ollama_client.py`, `app/trained_client.py` | Existing local model backends |
| `app/gpu_lock.py`, `app/file_lock.py` | Shared process and inference resource coordination |
| `static/` | Browser UI and document viewer |

## Storage and concurrency

Runtime data lives on the backend host and is excluded from Git: source documents, account stores, SQLite databases, embeddings, histories, images, caches and model artifacts. The hosted backend runs on the operator's machine; local source-checkout execution is an operator/developer workflow. Learners use their browser without maintaining these runtime files. Demo and automated tests use separate data directories. See [data policy](../data/README.md).

A publisher may distribute a separately reviewed [knowledge pack](knowledge-packs.md) containing permitted source originals and precomputed compatible vectors. Explicit operator installation (`scripts/dev.py --install-pack --profile local` with `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256`) verifies the publisher digest and exact embedding identity, constructs a known schema and publishes a library into `data/knowledge-packs/<sha256>/` atomically without modifying shared `src/` or seeding `data/user-library/`. It never runs document extraction, embedding or training on the recipient's device. Standard `check`/`serve` in `local` mode ignores `resources/knowledge-packs.json`. Native macOS packs and other backend packs require separate compatibility verification. Packaging support does not imply that a pack has passed release acceptance.

The shared corpus uses `AGENT_SRC_DIR` and `AGENT_DATA_DIR`; optional topic routing uses an adjacent retrieval sidecar. Accounts default to `AGENT_DATA_DIR/accounts` or explicit `STUDY_ACCOUNTS_DIR`, with separate durable originals, records, history and planners per account. Authentication/session state and account operations are separate from shared-library maintenance. Account uploads cannot insert into shared `src/`. Loopback guests retain temporary attachments or explicitly saved shared-library uploads. See [persistence](service-persistence.md).

The [source watcher](configuration.md#automatic-source-ingestion) is opt-in and admits settled new supported shared files under the corpus writer lock. Changed/missing indexed sources retain their rows and require operator review/re-indexing; freshness admission blocks their stale retrieval and source routes. Account-private ingestion follows the upload service instead. Indexing is not training or content approval. Sidecar taxonomy maintenance remains explicit; a changed corpus falls back to global retrieval until rebuild.

One writer owns a canonical index or model operation at a time. Session, history, planner, and upload operations use per-account SQLite databases and per-account `dialogue.lock` isolation (`account_dialogue_gate`), so ~20+ authenticated users do not block on one another during metadata requests while model generation/OCR/indexing enter a bounded FIFO queue (`acquire_inference`, 1 in-flight request per account). Keeping SQLite/filesystem access behind `AccountService`, `HistoryStore`, and `LearningService` allows replacing single-host stores with shared PostgreSQL/Redis/object storage when scaling to multiple replicas later.

## API

The running `/docs` page is the authoritative endpoint and payload reference. Core routes cover chat and streaming, sessions, document lookup, material upload/inspection/generation/download, practice and timetables. Do not duplicate business logic in a second backend or infer current contracts from a UI mockup.

Practice generation and source review pass an explicit JSON schema through the native worker or Ollama format parameter. The native worker uses the structured decoder provided by the installed MLX-VLM/llguidance runtime. A valid JSON structure is not evidence of factual correctness; passage, answer uniqueness, qualifiers and difficulty checks remain separate. Missing finish metadata is an incomplete result.

## Shared topic routing

QA retrieval and practice use the authored hierarchy in `app/topic_tree.py`. The query router derives document candidates from indexed passages without changing the database schema or rebuilding vectors. Recognized topic queries try those candidates first; unrecognized queries, empty candidate sets and inadequate scoped evidence use global retrieval. Explicit document or page selection remains authoritative. Existing ranking, source-quality warnings and citation checks still apply. Topic matching and evidence adequacy are heuristics, not semantic guarantees or a measured latency improvement. Request diagnostics report the attempted scope and fallback as JSON-safe metadata.

## Sidecar retrieval tree and retrieval operation

The persistent hierarchical retrieval tree is stored in an adjacent sidecar SQLite database (`retrieval_tree.db`), leaving the active source database (`knowledge_base.db`) completely unchanged. The sidecar index is built via `scripts/build_retrieval_tree.py --source-db PATH --output PATH`.

At query time, passage text, vectors, and provenance rows are read directly from the original source database in read-only mode (`mode=ro`). The sidecar is used exclusively for persistent taxonomy, document, and chunk topic memberships.

On every query, the sidecar reader verifies the canonical filesystem and SQLite header fingerprint of the active source database (resolved path, inode, file size, modification time, header change counter, and WAL status). Any modification to the source database—such as new user uploads, document deletions, or committed WAL transactions—or a missing/mismatched database binding immediately triggers a safe fallback to global retrieval. This ensures that new uploads are never hidden by an outdated index.

Maintenance limitation: The retrieval tree is a derived index, not a source corpus replacement or training approval. When the active library changes, fallback to standard retrieval remains correct until an operator runs a maintenance rebuild via `scripts/build_retrieval_tree.py`. There is no automatic rebuild or background scheduler.

Additionally, embedding query processing reuses the verified post-generation batch model identity to eliminate redundant full-artifact byte hashing on cache misses, while preserving strict actual-byte verification on every cache hit and fail-closed security.
