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
