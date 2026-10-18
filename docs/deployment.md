# Running and updating

Study Agent currently targets a trusted local installation. Use the platform setup guide and keep the server bound to `127.0.0.1` unless a separately reviewed deployment provides authentication, source permissions and network controls.

## Start and check

```bash
python scripts/dev.py check --profile local --require-model
python scripts/dev.py serve --profile local
```

Use the Python executable from the project environment. Open `/docs` for the API reference and `/api/health` for the health response. A health response confirms service availability; it does not certify model accuracy or corpus completeness.

## Update an existing installation

Stop your own serving process. Inspect `git status` and preserve uncommitted work before updating. From a clean `main` checkout:

```bash
git fetch origin
git pull --ff-only origin main
```

Install changed dependencies in the matching environment, run the checks in [Testing](testing.md), then start the application again. Confirm the process uses the intended checkout and refresh browser assets. Pulling code does not transfer document databases, embeddings or model weights.

## Backup and recovery

Back up source files, local databases and approved model artifacts separately from Git. Stop writers or use a consistent SQLite backup method; copying a live database file alone can omit pending journal data. Keep artifact versions and source hashes with the backup.

Before a migration, retain a recoverable copy and document the rollback procedure. Do not merge SQLite or vector files across branches. Do not clear locks while an operation owns them.

## Shared deployment requirements

Before offering a shared service, add authenticated identities, per-source and per-session authorization, upload limits, isolation tests, a bounded work queue, observability and a backup/restore test. Measure latency and resource use before setting a service-level target. These are planned requirements, not current capabilities.
