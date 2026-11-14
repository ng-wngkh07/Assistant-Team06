# Single-Host Persistence Architecture & Service Storage

This document details the single-host persistence architecture, database schema migrations, account isolation boundaries, backup/restore procedures, and legacy migration tools.

---

## 1. Architectural Overview

The application adopts a single-host, process-isolated persistence model designed for durability, strict privacy isolation, and operational reliability:

```
data/
├── accounts/                     [0o700 directory permissions]
│   ├── accounts.db               [Global authentication store: credentials, sessions, rate limits]
│   ├── <account_id_32_hex>/      [Per-account private directory, 0o700]
│   │   ├── history.db            [Conversation sessions and turn history]
│   │   ├── timetable.db          [Timetable drafts, confirmed schedules, personal tasks]
│   │   ├── uploads.db            [Upload metadata, extraction records, aliases]
│   │   ├── originals/            [0o700: Immutable content-addressed original files]
│   │   │   └── <sha256>.bin      [0o600: Raw document payloads, deduplicated]
│   │   └── timetable_images/     [0o700: Timetable input photos]
│   └── ...
├── knowledge_base.db             [Read-only for account operations; serialized operator ingestion may update]
└── retrieval_tree.db             [Read-only for queries; explicit derived-index maintenance is separate]
```

### Core Invariants:
1. **Strict Partitioning**: The global authentication database (`accounts.db`) is physically decoupled from individual user datasets. Each authenticated account owns a dedicated subdirectory keyed strictly by a 32-character hexadecimal identity (`/data/accounts/<account_id>/`).
2. **Private Originals Isolation**: User-uploaded documents and schedule images are never indexed into the shared library corpus (`knowledge_base.db`) and are never written to `src/` or training datasets.
3. **Immutable Content-Addressed Storage**: Uploaded files are stored on disk indexed strictly by their SHA-256 digest (`originals/<hash>.bin`), deduplicating identical uploads while maintaining distinct logical references in `uploads.db`.
4. **Knowledge Asset Protection**: All shared course materials, vector databases, and model weights remain strictly read-only during account data operations.

---

## 2. SQLite Configuration & Concurrency Controls

The account persistence layer uses `app.db.open_sqlite` with the following settings. Shared-library read-only readers and other subsystems have their own connection modes; this is not a claim about every SQLite connection in the repository:

* **`PRAGMA journal_mode = WAL;`**
  Write-Ahead Logging permits concurrent, non-blocking readers alongside an active writer.
* **`PRAGMA busy_timeout = 10000;`**
  A 10-second busy timeout bounds lock waiting; startup WAL negotiation retries transient busy/locked errors within that budget. Lock timeouts remain explicit failures, never permission to execute a transaction without its lock.
* **`PRAGMA synchronous = FULL;`**
  Requests SQLite FULL synchronization. Durability still depends on the filesystem and hardware honoring flushes; this does not establish tested power-loss recovery or an atomic snapshot across files.
* **`PRAGMA foreign_keys = ON;`**
  Enforces relational integrity, cascading deletions, and constraint checks across parent-child records.

### Transaction Discipline & Deterministic Closure:
Write operations use `app.db.atomic_transaction(conn)`, which begins transactions with `BEGIN IMMEDIATE`. This acquires the write lock at the start of the transaction, preventing deadlocks between concurrent readers upgrading to writers. If lock acquisition fails (e.g. busy timeout), the `OperationalError` propagates immediately and the block is never executed unlocked.

Connections should be deterministically managed using `app.db.managed_sqlite(path)` or explicit `try ... finally: conn.close()`. Standard library `sqlite3.Connection` context managers commit/rollback transactions on exit but do not close the underlying OS file descriptors; explicit closure prevents connection leaks and file-lock retention.

---

## 3. Versioned Schema Migrations (`app/db/`)

The account authentication, upload, history, practice and timetable schemas use versioned migrations managed by `app.db.MigrationManager`. Shared corpus and sidecar schemas have separate lifecycle contracts; this is not a claim that every repository database uses this manager.

### Tracking Schema:
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
```

### Migration Lifecycle:
1. **Atomic Execution**: Each migration step executes inside a `BEGIN IMMEDIATE` transaction. If an error occurs, the transaction rolls back cleanly, leaving the database at its previous valid version.
2. **Unversioned Database Bootstrapping**: Existing unversioned databases (e.g. legacy `accounts.db` or `history.db`) are automatically detected and upgraded incrementally without dropping or rewriting existing data rows.
3. **Orphan Audit & Foreign Key Migration**:
   * Prior to establishing foreign key constraints in `accounts.db`, an audit query purges orphaned sessions and feature usage records where `account_id NOT IN (SELECT id FROM accounts)`.
   * Tables are upgraded to enforce `FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE`.
4. **Index Optimization**: Indices are created for frequent query paths (`idx_sessions_account_id`, `idx_sessions_expires`, `idx_study_sessions_plan_id`, `idx_files_hash`).

---

## 4. Account Storage & Content-Addressed Originals

### Directory Containment & Security:
* Account identities are validated against the strict regex `^[a-f0-9]{32}$`.
* All paths are checked using `app.db.validate_path_containment(path, base_dir)`. Any attempt to access paths outside the account directory via relative segments (`../`) or symlinks is immediately rejected with a `ValueError`.
* Symlinks under the configured account root are strictly rejected for account directories and their children (including databases, originals, and image files), even when the symlink target is within the root directory. This prevents Account A from linking to Account B's directory or files.
* On POSIX, directories request `0o700` (`rwx------`) and files request `0o600` (`rw-------`). Windows mode bits do not verify ACL isolation; operators must configure and verify the host account and storage ACLs separately.

### Content-Addressed Originals & Metadata Aliasing:
* **Two-Phase Write**:
  1. Incoming file bytes are written to a temporary file (`originals/.tmp_<uuid>`) using exclusive creation (`O_CREAT | O_EXCL`) and flushed to disk with `os.fsync`.
  2. The temporary file is published using an exclusive hard link at `originals/<sha256>.bin`; a concurrent existing object is verified, never overwritten.
  3. The parent directory is flushed with `os.fsync` on POSIX systems to ensure durable directory metadata.
  4. The database record is committed in an immediate transaction (`BEGIN IMMEDIATE`). Only after successful transaction commit are in-memory caches and aliases updated. If the database transaction fails, in-memory caches are untouched or rolled back, ensuring consistency between in-memory mappings and fresh manager instances.
* **Lazy Loading (`LazyRawFilesMapping`)**:
  `PrivateMaterials._raw_files` does not load BLOBs into memory on initialization. Instead, it exposes a dictionary-like mapping that reads file bytes on-demand from `originals/<sha256>.bin` and verifies the current original hash before returning data, including cached reads. The byte cache has a 4 MiB total budget per retained account manager and a 50-entry ceiling; larger documents are read on demand without being retained in that cache.
* **Delta Updates**:
  Uploads only insert/update the specific record in `records` and `files` tables, eliminating full catalog rewrites on every upload.

---

## 5. Online Backup & Safe Restore Service (`scripts/backup_service.py`)

The backup service uses SQLite online backups for each database and verifies restoration. It copies only the documented account stores, originals and timetable images. Stop application writes externally for a cross-file recovery snapshot; live per-file backups do not establish global consistency.

### Backup Command:
```bash
python scripts/backup_service.py backup --accounts-dir data/accounts --out backups/backup-20261109.tar.gz
```

* **Live SQLite Online Backup**: Databases are backed up using SQLite's online backup API (`conn.backup()`) over read-only source connections (`file:...mode=ro`), supporting live operations with SQLite-managed read locking. This is not a lock-free operation.
* **Integrity Auditing**: Every database copy is verified using `PRAGMA integrity_check` and `PRAGMA foreign_key_check` before packaging; backup fails immediately on any integrity or foreign key violation.
* **Reference Auditing**: Referenced content-addressed original files are validated to exist on disk and match their expected SHA-256 digests; missing or corrupt originals immediately abort backup.
* **Strict Source Safety**: Rejects symlinks, hard links, and special files (FIFOs, sockets, devices) in backup sources.
* **Manifest Generation**: Generates `manifest.json` detailing relative paths, SHA-256 digests, byte sizes, and database health.
* **Consistency Caveat & Quiescence Notice**: Online backups guarantee internal point-in-time consistency for each individual database file. Passing `--quiesced` records an operator assertion that external host writes were stopped; the backup tool cannot independently verify or enforce caller quiescence assertions. Cross-database point-in-time consistency across separate SQLite files is not guaranteed by single-file WAL transactions.
* **Security Notice**: SHA-256 checksums in the manifest detect accidental corruption or tampering; they do not provide cryptographic authentication or signatures against an active adversary capable of rewriting both archive payload files and `manifest.json`.

### Safe Restore Command:
```bash
python scripts/backup_service.py restore --archive backups/backup-20261109.tar.gz --destination data/restored_accounts
```

### Safety & Verification Gates during Restore:
1. **Safe Destination Enforcement**: Restore will **never** overwrite an existing, active directory. The target directory must be a clean path that does not yet exist; existing directories (even empty ones) are strictly rejected to prevent destination race conditions or accidental data loss.
2. **Atomic Publication**: Files are staged and verified before a same-filesystem, non-overwriting directory publication (macOS `renamex_np`, Linux `renameat2`, or Windows non-overwriting rename). Unsupported platforms fail closed. An existing destination created concurrently is preserved. If any check fails, no partial restore result is published.
3. **Manifest Containment & Exact Inventory**: Rejects any archive containing paths with `..`, absolute path references, duplicate entries, or untracked archive files not declared in `manifest.json`.
4. **Cryptographic Checksum Verification**: Every extracted file's SHA-256 checksum is compared against `manifest.json`. Any mismatch immediately halts restoration.
5. **Database Integrity & Foreign Keys**: Opens all restored databases and confirms `PRAGMA integrity_check == 'ok'` and zero foreign key violations.
6. **Reference Validation**: Confirms that every `file_hash` and `file_objects` record referenced in `uploads.db` exists in `originals/` with matching checksums and sizes.

---

## 6. Opt-In Legacy Data Migration (`scripts/migrate_legacy_data.py`)

To migrate legacy unpartitioned search history and timetable data from earlier single-user configurations into a multi-account setup:

```bash
python scripts/migrate_legacy_data.py --source-dir data/ --accounts-dir data/accounts --target-account <account_id_32_hex>
```

### Safety Guarantees:
1. **Explicit Target Account Required**: The target account ID must be explicitly specified and must exist in `accounts.db`. Implicit migration to `"guest"` or `"anonymous"` is strictly blocked.
2. **Non-Destructive Source Copy**: Source databases are accessed in read-only URI mode (`file:...mode=ro`). SHA-256 digests of source files are verified before and after migration to ensure zero byte alteration.
3. **Protected Knowledge Asset Guard**: Explicitly blocks any migration involving `knowledge_base.db`, vectors, or model weights.
4. **Collision Pre-Check & Fail-Closed Behavior**: Scans for conflicting records before writing. If target contains conflicting records under the same primary key, migration halts immediately (`fail closed`) before modifying target data. Idempotent exact repeats (identical records) are preserved.
5. **Complete Planner & History Coverage**: Migrates all tables and fields across `history.db` and `timetable.db` (including drafts, confirmation events, confirmed items, VLM cache, routines, preferences, tasks, plans, and study sessions).
6. **Cross-Database Atomicity Boundary**: `history.db` and `timetable.db` are migrated in separate atomic transactions. True cross-database atomicity across multiple SQLite files is not supported by single-file WAL transactions and is reported honestly in the result.

---

## 7. Operational Scope & Service Boundaries

The persistence architecture is strictly single-host bounded. It does not claim general public production readiness, guaranteed power-loss recovery across separate files, multi-host replication, or verified global snapshot consistency across disparate SQLite databases. Account operations do not modify shared course materials, vector sidecars, model weights or shared originals. Authorized operator indexing, source-watcher admission and derived-index maintenance are separate serialized operations and may update the shared index; they must preserve source/review gates. Database migrations, backups, and data imports operate exclusively within local, host-isolated accounts storage.

## Acceptance boundary

These changes support a bounded single-host account pilot. They do not provide distributed transactions, replication, production capacity figures, account recovery, retention/export/deletion policy, per-account resource quotas, or a hosted-service acceptance. See [deployment assessment](web-deployment.md) for the staged rollout and remaining release gates. Backup checksums are integrity evidence, not signatures or authorization.
