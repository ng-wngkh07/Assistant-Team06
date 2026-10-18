# Local application data

This directory holds runtime state. Keep private content out of Git. The documentation and any explicitly permitted synthetic fixture are the only public exceptions; an exception for one path does not permit publishing the rest of this directory.

| Data | Purpose | Implementation |
| --- | --- | --- |
| Knowledge index | Documents, extracted chunks, FTS and embeddings | `app/indexer.py` |
| Conversation history | Sessions, turns, citations and request replay | `app/history.py` |
| Timetables and images | Recognition drafts and confirmed schedules | `app/timetable_store.py` |
| Translation cache | Locally generated translations | `app/bilingual.py` |
| OCR cache | Source-bound recognition output | `app/vision_ocr.py` |
| Runtime locks | Coordination of model and indexing operations | `app/gpu_lock.py`, `app/dialogue.py` |
| Demo data | An isolated index built from public test fixtures | `scripts/dev.py` |

Paths are configurable; see [configuration](../docs/configuration.md). Use one writer for the canonical index. Do not merge databases between branches or use serving data as a test fixture. Back up persistent state consistently before migration.
