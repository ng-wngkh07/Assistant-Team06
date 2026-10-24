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

Public debugging examples live in [`tests/fixtures/EXTENDED_CORPUS.md`](../tests/fixtures/EXTENDED_CORPUS.md): 12 additional authored subjects, 96 cases and a 14-course fictional timetable. The demo launcher indexes them into isolated local storage. Shared examples are source documents and expected cases, not a serving database or approved training dataset.

Follow [complete-page preparation](../docs/data-preparation.md) when reviewing a local corpus. Account for every physical page while retaining pending, partial and excluded content outside the eligible training view. Bind native vectors to exact text and artifact identities before using them for dense retrieval.
