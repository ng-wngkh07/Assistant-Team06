# Configuration

The supported launcher is `scripts/dev.py`, also used by the Windows wrappers. It reads supported settings from `.env`; existing process environment variables take precedence. Relative paths are resolved from the repository root. Direct `run.py` execution does not load `.env` automatically.

## Profiles

| Profile | Sources and data | Startup behavior |
| --- | --- | --- |
| `demo` | Public fixtures and `data/team-demo` | Builds an isolated demonstration index |
| `local` | Configured directories | Uses existing local data; synchronize new sources explicitly |

## Settings

See `.env.example` and `app/config.py` for current defaults.

| Setting | Purpose |
| --- | --- |
| `AGENT_SRC_DIR`, `AGENT_DATA_DIR` | Source and runtime data directories |
| `HISTORY_DB_PATH` | Optional history database override |
| `TIMETABLE_DB_PATH`, `TIMETABLE_IMAGES_DIR` | Optional timetable storage overrides |
| `OLLAMA_BASE_URL` | Local Ollama endpoint |
| `DEFAULT_CHAT_MODEL`, `DEFAULT_EMBED_MODEL` | Question answering and embedding model identifiers |
| `OLLAMA_AUX_CHAT_MODEL` | Auxiliary image model |
| `MLX_CONTEXT_TOKENS` | Operational context budget for the MLX backend |
| `HOST`, `PORT` | Listening address and port |

The sample selects an Ollama development backend. An existing MLX installation has separate runtime and model artifacts. Do not change a working provider or context setting merely to reproduce a demo. More context is not evidence of better answers.

Keep `.env`, model registries and private artifact locations local. Environment setup does not authorize training or adapter activation. See [source handling](../corpus/README.md).
