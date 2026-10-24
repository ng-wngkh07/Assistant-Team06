# Architecture

The product has one FastAPI backend in `app/` and one primary web interface in `static/`. `run.py` starts the service; `scripts/dev.py` applies the selected environment profile.

## Question answering

1. Extract documents and preserve source identity, page or section, and raw text.
2. Build SQLite FTS5 and optional embedding indexes.
3. Resolve the question and its document/page scope, including attachment follow-ups.
4. Retrieve passages and pack them within the configured model context.
5. Generate an answer and validate citation identifiers and claim mappings. Careful processing adds an evidence audit.
6. Return an answer, clarification, insufficient-evidence result or explicit generation failure. Save the completed turn and its citations in local history.

Sources are untrusted input. Instructions inside a document must not become application instructions. A citation must refer to an admitted passage; an accessible link alone does not establish support for a claim.

## Chunking and retrieval context

Newly indexed text uses a hierarchy of paragraph, line, sentence, word and finally character boundaries within the legal chunk window. A simple pipe-row check avoids an internal table line break when another legal line boundary is available. This is a boundary heuristic, not a complete heading, table or code parser. Each chunk remains an exact slice of the stripped page text with character offsets and exact overlap. Defaults remain 1200 characters and 200 characters of overlap.

Hybrid retrieval combines SQLite FTS5/BM25 and embeddings with reciprocal rank fusion and the existing content heuristics. It does not use a cross-encoder reranker. Once the original hits pass relevance and partial-evidence checks, QA can add their immediate previous and next chunks. The database identity and original text of each hit must match, and every neighbor must share its document ID, filename and page or reading section. Neighbor expansion never crosses pages or follows neighbors recursively.

For questions that explicitly name a differential, FTS also probes compact and spaced spellings of that operand. Ranking compares the complete operand, including variable case, derivative order and coefficient, rather than borrowing a different differential or a repeated question. The probe stays within the existing document scope and candidate limit; source quality and heading/catalogue demotion still apply. This is a retrieval heuristic, not symbolic algebra or a guarantee of semantic support.

Original hits retain their order and scores. Whole neighbors are appended as independent, sanitized citation sources with their own source-quality warnings and no inherited retrieval scores. Expansion adds nothing beyond a total of 12 chunks or 12000 source characters; original hits remain intact even when already above those expansion limits. The existing token budget still controls final prompt admission. If expansion fails, QA keeps the original hits. Explicit page retrieval keeps its selected document/page scope and does not expand to neighbors; whole-document overview retrieval is unchanged.

Markdown extraction can store a numbered exercise heading and its body as separate reading sections. Exercise lookup retains the heading provenance and the exact question-body and answer-body sources. For library or document QA, an exact stored exercise-body hit can add the answer two reading sections later only when the intervening answer heading matches the preceding exercise number. This bounded layout rule stays within one document and filename; it does not cross PDF pages or expand an explicitly selected page. It is not a general parser for arbitrarily arranged exercise collections.

For an explicitly selected page, QA can score only the exact selected passages against the question using their existing stored vectors and the configured embedding model. Missing, malformed, nonfinite, zero or dimension-mismatched vectors provide no semantic evidence. No other page supplies a score. The existing lexical checks and 0.52 semantic relevance threshold still apply; selecting a page alone does not establish that it answers the question. The page-retrieval API remains available to practice callers without requiring embedding generation.

Citation repair rebuilds its context from the already admitted, escaped source blocks and one resolved question. It excludes old conversation history. When space permits, a rejected structured draft is included only as escaped, explicitly untrusted data; the admitted sources and answer reserve take priority over that optional draft. Repair instructions require a direct answer to the question, including a requested calculation or result already present in the evidence. Applicable formula conditions, domains, units and the subject of each mathematical relation must remain supported by their own admitted sources; unrelated neighboring topics must be omitted. A claim may cite the same source as another claim or a different admitted source according to its actual support, rather than the position of a source in a schema example. When passages overlap, repair prefers one passage containing the complete premise and conclusion over a fragment cut off before the conclusion. Repair generation requests readable mathematical notation. Structured repair can normalize an escaped comparison only when its operands and operator match a literal comparison in the cited original source; an exact encoded spelling in the cited source takes precedence and remains unchanged, and unsupported comparisons fail citation validation. Operands retain compound expression context across mathematical operators and whitespace, including a spaced differential such as `d Q/dt`. This is a bounded representation check, not an algebraic proof or a general HTML decoder. Source escaping and decoded-markup rejection remain intact. Existing context limits and citation validation still apply.

For the pinned 9B model, structured claims select an inclusive range of numbered lines from one admitted source. The backend resolves the exact original slice; line numbers never become evidence text. Invalid ranges, conflicting explicit quotations and missing referenced function definitions are rejected. This bounded dependency check does not establish general semantic entailment, so real-model source review remains required.

Citation validation rejects exact echoes, standalone unanswered questions and empty cited labels, including a partly complete answer with an empty condition. It permits the existing single repair and fails closed if that repair is still incomplete. Repair uses a neutral claim example, follows the question language and distinguishes the requested exercise from other items within the same passage. Answer cleanup requires explicit source framing before removing a citation introduction, preserving ordinary predicates such as “tồn tại” and the case of leading mathematical identifiers, including differentials. These checks do not replace source/content review or repair damaged source extraction.

Generated answers and repair claims share a bounded, nonexecuting math normalizer. Supported LaTeX fractions, roots, grouped powers and subscripts, number sets and symbols become readable Unicode/plain text. It preserves grouping, identifier case and distinct supported symbol variants. Unsupported or malformed recognized math invalidates the draft and uses the existing single repair; a failed repair returns a generation failure without citations. Ordinary paths and currency remain text. This representation step does not prove an equation, supply missing premises or establish semantic support for a citation.

This bounded previous/next window uses the small-to-big retrieval idea without a persisted parent hierarchy or an additional model call. Existing indexes require no migration for the context window. The splitter affects future indexing only; applying new boundaries to an existing corpus requires a separately reviewed rebuild that preserves source reviews and exclusions. No automatic rebuild, training or adapter activation occurs. Reverting these code changes requires no database migration.

Text quiz validation additionally rejects overlapping truth regions for scalar or probability comparisons to zero, including a change of operand. When a source answer is a bit/byte value, alternatives must use concrete numeric unit values rather than broad categories. These are narrow checks, not a general proof that all distractors are false. Rejected outputs keep the labeled extractive fallback; fallback availability does not establish assessment quality. Source cloze flashcards retain the whole admitted passage and its conditions.

## Model roles

On Mac, the selected text model loads directly from local MLX weights in the isolated Python worker. QA and text helpers must resolve the same model identity, tokenizer and chat template. The operational window is 8192 tokens, independently of advertised capacity. Hidden thinking is disabled; Careful mode is a separate source-verification workflow.

| Role | Runtime boundary |
| --- | --- |
| Grounded QA and text helpers | Direct local Python MLX, pinned 9B base; no silent provider or 3B fallback |
| Retrieval vectors | Separately validated local embedding artifact; never silently mix embedding spaces |
| Image extraction | Local OCR/native vision runtime and source-review gates |
| Fine-tuning | Local MLX on the intended same base, gated data and hardware checks, separately accepted adapter |
| Windows compatibility | Explicit local development runtime; never a remote fallback for the Mac |

Keep the dialogue and GPU coordinator around model work. Preserve subprocess lock inheritance and serialize workloads that cannot fit together. Switching QA needs a backend restart and runtime readback, with no source database replacement. Inference fit does not establish training fit. The training-plan command produces a blocked, read-only proposal and cannot authorize execution. Legacy 3B adapters retain their own base identity and cannot be loaded on 9B.

## Components

| Component | Responsibility |
| --- | --- |
| `app/server.py` | HTTP API, request lifecycle, source scope and history integration |
| `app/rag_agent.py`, `app/dialogue.py` | Retrieval, prompt budget, answer validation and serialized dialogue |
| `app/searcher.py`, `app/indexer.py` | Hybrid retrieval and index maintenance |
| `app/pdf_extractor.py`, `app/document_lookup.py` | Extraction, source access and document lookup |
| `app/study_materials.py` | Upload validation, quizzes, flashcards and source provenance |
| `app/history.py`, `app/summarizer.py` | Sessions, replay and topic summaries |
| `app/practice.py`, `app/learning_tools.py` | Exercises and study workflows |
| `app/timetable_vision.py`, `app/timetable_store.py` | Image recognition, draft confirmation and timetable storage |
| `app/ollama_client.py`, `app/trained_client.py` | Existing local model backends |
| `app/gpu_lock.py`, `app/file_lock.py` | Shared process and inference resource coordination |
| `static/` | Browser UI and document viewer |

## Storage and concurrency

Runtime data is local and excluded from Git: source documents, SQLite databases, embeddings, histories, images, caches and model artifacts. Demo and automated tests use separate data directories. See [data policy](../data/README.md).

One writer owns a canonical index or model operation at a time. Local inference uses the existing dialogue and GPU coordination. These locks serialize work; they do not provide user authentication or multi-tenant isolation.

## API

The running `/docs` page is the authoritative endpoint and payload reference. Core routes cover chat and streaming, sessions, document lookup, material upload/inspection/generation/download, practice and timetables. Do not duplicate business logic in a second backend or infer current contracts from a UI mockup.
