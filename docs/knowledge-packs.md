# Prebuilt local knowledge packs

A publisher can distribute a reviewed document library with its vectors so users can start asking questions without collecting documents, extracting text or generating embeddings. This optional distribution path runs the backend and models on the user's computer. A hosted service instead keeps its prepared library on the backend host; it does not require a public knowledge-pack download. Model weights are installed once, separately from the knowledge pack. No hosted inference service, external model API, or cloud fallback is permitted.

## Supported pack protocols

The application supports two distinct, artifact-verified knowledge pack protocols:

1. **macOS Native BGE Pack**
   - **Protocol**: `bge-cls-l2-v1`
   - **Target Model Identity**: `bge-m3-mlx-fp16@a37eddded9a6a1273a87fb8b0da0d1cdbd98aeec`
   - **Dimensions**: 1024 float32, L2-normalized
   - **Sequence Length**: 2048 tokens
   - **Platform Requirement**: Apple Silicon macOS only. Requires local MLX weights in `data/models/bge-m3-mlx-fp16`. On Windows/Linux, native MLX is marked `NOT_RUN` (unsupported platform).

2. **Ollama Portable Pack**
   - **Protocol**: `bge-m3-ollama-l2-v1`
   - **Target Model Identity**: `ollama-sha256:7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` (or exact publisher-verified local Ollama `bge-m3` model digest)
   - **Dimensions**: 1024 float32, L2-normalized
   - **Sequence Length**: Up to 8192 tokens
   - **Platform Requirement**: Cross-platform (Windows, Linux, macOS). Requires local Ollama running on loopback (default `http://127.0.0.1:11434`) with model `bge-m3`.

Matching model names or vector dimensions alone is insufficient: pooling layers, tokenization, and numerical representations differ across backends. A native pack cannot be loaded under an Ollama backend, and an Ollama pack cannot be loaded under the native MLX client. Both the exporter and installer strictly enforce cross-protocol isolation.

## Publisher preparation

For a full source-library release, first complete the [document publication and coverage review](data-distribution.md). Every original needs a resolved rights and privacy decision; a successful export does not establish those permissions.

Use an isolated library containing only material you are entitled to redistribute. Review source content, extraction, citations and license terms. Finish the current artifact-bound vector build before export. Do not point an export at a training dataset or distribute a copy of the working database.

Create a private JSON allowlist with one entry per approved original document:

```json
[
  {
    "filename": "public-notes.md",
    "sha256": "replace_with_the_reviewed_original_file_sha256",
    "license": "CC0-1.0",
    "redistribution_reviewed": true
  }
]
```

The review flag records an actual publisher decision; it is not an automatic privacy or legal review. Use the correct license for each source and include any required copyright/attribution text in an optional `license_notice` field. Export rejects changed originals, pending source review, missing licenses and vectors with stale text, artifact, dimension, normalization or full-text provenance.

Publisher export strictly requires explicit, complete source-bound per-physical-page review evidence via `--page-reviews` (pointing to a review manifest JSON file):

```bash
python -m app.knowledge_pack export \
  --database data/publisher/knowledge_base.db \
  --sources data/publisher/sources \
  --allowlist data/publisher/redistribution.json \
  --page-reviews data/publisher/page-reviews.json \
  --output data/publisher/knowledge-pack.zip
```

Physical PDF pages and PPTX slides are enumerated directly from original source containers without model or OCR calls. Every single physical page in every declared source must have an explicit, completed review:
- Missing, duplicate, out-of-range, partial completeness, unresolved items, or `HOLD` status reviews immediately fail export.
- Review image and raw capture artifact bindings are validated against on-disk files.
- Each page's expected production chunk projection (`reviewed_page_text` with editorial remarks omitted) is reconciled against ordered database chunks. Altered transcripts, mismatched chunk counts, extra chunks, out-of-range physical pages, or stale passages fail closed even if vectors are recomputed and valid.
- Passage digests bind an unambiguous canonical JSON array serialization of the exact ordered passage list (`_page_passages_digest`), preserving passage boundaries and list ordering.
- Verified blank pages require explicit reviews (`kind: "blank"`), contain 0 chunks, and bind to canonical empty list digest `sha256(b'[]')`.
- Minimal portable review coverage is exported in `manifest.json` under `documents[].page_reviews`, recording `page`, `kind`, `completeness`, `chunks`, and `text_sha256` (the passage digest of the ordered chunk texts). Private review files and local paths are excluded.
- During installation, contiguous canonical 0-based chunk indices (`0..N-1` per physical page) are strictly enforced, and per-page digests are re-verified over the installed database sequence (`ORDER BY chunk_index, id`).

The export reads a consistent database snapshot and writes only the allowlisted originals, portable document fields, chunks, validated vectors, and portable review coverage metadata. It excludes arbitrary tables, local absolute paths, chat history, timetables, credentials and internal review metadata. Installation validates complete page coverage and passage digests before extracting the application schema.

Inspect the complete resulting archive and its source content before publishing it. Vectors are derived from documents and are not a privacy boundary. This format deliberately contains source text and original documents for citations: anyone receiving the pack can read them. Neither private business material nor personal documents belong in a public pack.

Distribute an approved pack as a versioned release asset or another trusted download, with its SHA256 and supported application/embedding versions. Keep generated packs, databases, vectors, training sets and weights out of Git. Public repository source code does not require the private corpus or original training data.

### Build an application bundle

After reviewing and committing the public application code, combine that immutable revision with the approved packs. The builder reads committed files through Git; it does not package the current working directory. Use a new output directory outside the repository:

```bash
python scripts/build_distribution.py \
  --ref YOUR_REVIEWED_COMMIT_SHA \
  --native-pack /path/to/approved-native.zip \
  --native-sha256 TRUSTED_NATIVE_SHA256 \
  --ollama-pack /path/to/approved-ollama.zip \
  --ollama-sha256 TRUSTED_OLLAMA_SHA256 \
  --output /path/to/new-study-agent-bundle
```

Supply either backend or both; include the backend required by the recipient's platform. The output contains application code, checked pack archives, `resources/knowledge-packs.json` and `bundle-metadata.json` for distribution reference. Standard `--profile local` startup ignores `resources/knowledge-packs.json` and never auto-seeds `data/user-library/`; operators install a pack explicitly with `--install-pack`, `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256`. Keep each archive bound to its own embedding protocol. Metadata records the source commit, pack digests and actual document/passage counts.

Review the final bundle before uploading it as a versioned release asset. Python dependencies, local runtimes and model weights are separate prerequisites; this command does not produce a standalone native executable or download models. Approval of all current source documents covers that reviewed snapshot, not future uploads or the publisher's mutable working database.

### Public demonstration library

Both reference archives contain the same twelve synthetic CC0 originals and 144 source passages. Only their vectors and embedding contracts differ. The original texts are also available in [the public fixture collection](../tests/fixtures/EXTENDED_CORPUS.md). They cover algorithms, astronomy, biology, chemistry, databases, economics, information theory, logic, networking, probability, statistics and units. This is a small demonstration library, not a complete course corpus.

| Archive | Backend | Bytes | SHA256 |
| --- | --- | ---: | --- |
| `study-agent-public-native-bge-v6.zip` | Native MLX BGE-M3 | 626537 | `dd91d88df9438b99668e5834fec09320498354cad0e6d2d473100daaeaa7d29e` |
| `study-agent-ollama-public-pack-v1.zip` | Ollama BGE-M3 | 626376 | `f8da594e19660588ce4c76904c2068e0bc86c85bab6fd69d922fc418651f2dd0` |

Obtain archives from the publisher's release assets together with the supported application revision. The specifications above identify prepared artifacts; they do not establish that a release has been published. Do not substitute a same-named file from another download or mix the two vector protocols.

## Explicit operator pack installation

Install the documented local model runtime and exact model assets first:
- macOS native default: MLX base model and BGE weights in `data/models/`.
- Windows / Linux default: Local Ollama with `bge-m3` and `qwen3.5:9b`.

Standard `check --profile local` and `serve --profile local` launches resolve canonical configured `AGENT_SRC_DIR` (default `src/`) and `AGENT_DATA_DIR` (default `data/`), even when `src/` is absent or `resources/knowledge-packs.json` is present. Missing libraries stay honestly unpopulated rather than auto-seeding `data/user-library/`.

### Configure and install a pack explicitly

Obtain the publisher's pack and its SHA256 from a trusted release. Add these settings to your local `.env` (or export them in the process environment):

```dotenv
KNOWLEDGE_PACK_PATH=resources/knowledge-pack.zip
KNOWLEDGE_PACK_SHA256=replace_with_the_trusted_64_character_sha256
```

Create `resources/` in the checkout and put the downloaded archive there. Use its actual filename in `KNOWLEDGE_PACK_PATH`. Verify the downloaded file before installing:

```bash
# macOS: replace the filename with your selected archive.
shasum -a 256 resources/study-agent-public-native-bge-v6.zip
```

```powershell
# Windows: compare Hash with the complete trusted digest above.
Get-FileHash .\resources\study-agent-ollama-public-pack-v1.zip -Algorithm SHA256
```

For example, the native reference archive uses `KNOWLEDGE_PACK_PATH=resources/study-agent-public-native-bge-v6.zip` and `KNOWLEDGE_PACK_SHA256=dd91d88df9438b99668e5834fec09320498354cad0e6d2d473100daaeaa7d29e`. Windows uses the Ollama filename and digest from the table. Keep both archives and `.env` outside Git.

If using an Ollama pack on macOS, or selecting non-default backends, configure:
```dotenv
EMBED_BACKEND=ollama
LLM_PROVIDER=ollama
DEFAULT_CHAT_MODEL=qwen3.5:9b
```

Run explicit operator installation (never implicit on `check` or default `serve`):

```bash
python scripts/dev.py --install-pack --profile local --require-model
```

If Ollama is not already running, pass `--start-model-service` so the launcher starts the local daemon before pack verification. Running `--install-pack` without both `KNOWLEDGE_PACK_PATH` and `KNOWLEDGE_PACK_SHA256` fails explicitly without seeding anything.

When both explicit pack variables are supplied, `scripts/dev.py --install-pack --profile local` validates the download digest and embedding identity, installs originals and passages/vectors into an isolated directory under `data/knowledge-packs/<sha256>/`, and builds local keyword-search (SQLite FTS5) tables. **It does not run document extraction, OCR, model training or embedding inference**. Keeping both pack variables set selects that verified pack directory on subsequent `check`/`serve` launches. Unset both variables to return to canonical `AGENT_SRC_DIR` and `AGENT_DATA_DIR`, or configure explicit paths into a verified pack directory. Unused `resources/knowledge-packs.json` descriptors are ignored.

A new pack digest selects a separate installation under `data/knowledge-packs/<sha256>/`; the installer never overwrites an existing library, account store, history or timetable. A mismatched model, malformed archive, changed hash, unreviewed pack, or incomplete index fails closed with an actionable message. Leave both pack settings unset unless performing an explicit operator `--install-pack`.

### Safe manual migration from legacy `data/user-library/`

Automatic `data/user-library/` seeding and routing are retired. If an older checkout still contains `data/user-library/`, the launcher never deletes, moves or merges it automatically:

1. Stop all backend processes completely.
2. Inventory the contents of `data/user-library/` (`src/`, `knowledge_base.db`, `history.db`, `timetable.db`, `seed-receipt.json`) and copy a full offline backup outside the repository.
3. Archive that backup safely before configuring canonical `AGENT_SRC_DIR` / `AGENT_DATA_DIR` paths or pointing explicit overrides (`HISTORY_DB_PATH`, `TIMETABLE_DB_PATH`, `TIMETABLE_IMAGES_DIR`) to verified compatible copies. Never copy unreviewed uploads into hosted shared `src/` or mix private account stores with shared fixtures.

Adding personal documents requires local extraction and compatible embeddings, but does not require retraining the language model. Keep those additions private. The current application uses the base model with RAG and has no approved active fine-tuned adapter. Training data is not needed to use a reviewed model or a prebuilt knowledge pack.

## Verification Boundaries

- **Mac-host Ollama Acceptance**: Verification of the public Ollama pack on a macOS host proves schema validity, protocol compatibility, tamper resistance, and end-to-end question answering under local Ollama.
- **Physical Windows Boundary**: While the Ollama protocol is cross-platform, physical Windows machine acceptance with native PowerShell setup remains separately tracked (`NOT_RUN` on macOS test runners). No test runner monkeypatch substitutes for physical hardware execution.

Before calling a distribution ready, verify the exact supported source revision and downloaded pack on a fresh checkout at its actual path and browser URL. Check model answers against their sources, quiz and flashcard content, and restart persistence. Archive integrity, a model smoke check, or green automated tests alone do not establish product readiness. No public release is established by this guide.
