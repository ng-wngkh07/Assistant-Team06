# Public test fixtures

These materials were authored for software tests. They contain no private books, conversations or personal schedules.

After installing the environment described in [README](../../README.md), run `python scripts/dev.py demo` to create a separate full-text index in `data/team-demo`. Model-free search does not establish that answer generation, embeddings or image recognition are available.

- `documents/`: synthetic learning documents, including the public corpus described below.
- `qa/cases.json`: questions and source-check criteria, not an academic quality holdout.
- `qa_core/`: small source-grounding regression fixtures for real-model checks.
- `timetables/demo.png` and `expected.json`: a fictional schedule and its expected extraction.

See [Public corpus](PUBLIC_CORPUS.md) for licensing and boundary cases. Fixture indexing and passing software tests do not authorize corpus promotion or training.

[Extended debugging corpus](EXTENDED_CORPUS.md) adds 12 authored subjects, 96 labeled review cases and a 14-course fictional timetable. Run `python -m pytest -q tests/test_extended_public_samples.py` for integrity, indexing and confirm/export integration checks.
