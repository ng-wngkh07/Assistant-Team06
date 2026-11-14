# Running, deploying and updating

Study Agent supports a local installation and a hosted website backed by one persistent native model host. The code repository does not contain a deployed URL, private library, database or weights. Publishing to GitHub or static hosting does not execute the Python backend or native model worker.

## Local startup

Follow [macOS](../README_MAC.md) or [Windows](../README_WINDOWS.md) setup. Run these commands with the project Python executable:

```bash
python scripts/dev.py check --profile local --require-model
python scripts/dev.py serve --profile local
```

Use `--profile demo` for isolated public keyword-search fixtures (`tests/fixtures/documents` and `data/team-demo`) without model installation. Standard `--profile local` startup resolves configured `AGENT_SRC_DIR` (`src`) and `AGENT_DATA_DIR` (`data`) without auto-seeding `data/user-library/` or reading `resources/knowledge-packs.json`. Keep the local service on `127.0.0.1`. The supplied platform launchers (`Start Study Agent.command` / `scripts/dev.py serve`) are foreground/manual launchers; for detached macOS supervision with preflight validation, foreign-port refusal, bounded logs, and bounded crash recovery, use `scripts/backend_service.py` and the reusable templates in [launchers/macos/backend-quick-start/](../launchers/macos/backend-quick-start/README.md) with an explicit private JSON config outside Git.

## Hosted single-host deployment

1. Prepare the reviewed code, native model runtime and approved server-side originals/precomputed retrieval. Set stable absolute paths to private storage outside public Git/build contexts. Do not replace an existing library or account store with demo data.
2. Export operator settings in the process environment or private `.env` as described in [configuration](configuration.md). Require accounts (`STUDY_ACCOUNTS_REQUIRED=1`), provision invited accounts through a loopback-only session, then close remote registration (`STUDY_ALLOW_REGISTRATION=0`). Account uploads, history and schedules use durable isolated storage, bounded by per-account quotas (`STUDY_ACCOUNT_MAX_FILES=50`, `STUDY_ACCOUNT_MAX_STORAGE_MB=250`); ordinary account use cannot administer shared indexing.
3. Run one backend worker on loopback (`127.0.0.1:8000`) behind a reviewed same-origin HTTPS gateway — including zero-cost options such as Cloudflare Quick Tunnel (`*.trycloudflare.com`), Tailscale Funnel (`*.ts.net`), or DuckDNS + Caddy Let's Encrypt TLS. Serve `/`, static assets, APIs and authorized original links through that origin; trust only loopback proxy headers (`FORWARDED_ALLOW_IPS=127.0.0.1,::1`) and enable `STUDY_SECURE_COOKIES=1` for HTTPS (`Strict-Transport-Security`, CSRF Origin checks, sliding-window HTTP `429` rate limits, and mutation body caps protect the origin).
4. Monitor infrastructure health via `/api/health`, `/api/status`, and `scripts/backend_service.py status --json` (disk headroom, read-only KB readability, writable accounts directory, active FIFO GPU admission queue depth, and HTTPS/cookie security posture warnings). Verify login and two-account isolation, source opening/downloads, actual native QA and practice, upload/restart persistence, busy/error behavior and gateway streaming.
5. Complete [REL-01 through REL-07](../AGENT.md#production-release-requirements) and record the final deployment identity and remaining limits before sharing the service. Authentication does not grant source rights or prove model accuracy. Scaling beyond one host later replaces per-account SQLite, in-memory admission/rate-limit stores, and local `originals/` behind `AccountService` without changing frontend API routes.

The [native host runbook](native-host-pilot.md) gives the environment and upstream command. The [hosting assessment](web-deployment.md) distinguishes the implemented account foundation from remaining public-service work. A provider-issued hostname does not supply model compute or durable storage. The native host must remain awake and connected. No paid domain, public ingress or hosting installation is performed by following a documentation link.

## Update and rollback

Stop your own writer/model processes and take a recoverable backup. Inspect `git status`; preserve local work before changing revisions. From a clean owner-approved `main` installation:

```bash
git fetch origin
git pull --ff-only origin main
```

A feature candidate should use its explicitly reviewed revision instead. Install changed dependencies in the correct web/native environments, review migrations and run [testing](testing.md). Restart and verify the serving PID, checkout/cwd, Git SHA or dirty-file hashes, model identity, browser assets and actual user flows. A healthy old process can serve old code after files change.

If rollback is required, stop the candidate and select the previous accepted code/configuration. Preserve the failed evidence and private state. Schema/data rollback needs its own compatible recovery procedure; Git rollback does not undo database migrations. Never merge SQLite/vector files between branches or activate an old adapter incidentally.

If upgrading or rolling back an environment that previously used legacy `data/user-library/`, never delete, move or merge that directory automatically: stop the server, inventory `data/user-library/` (`src/`, SQLite databases, `seed-receipt.json`), take an offline backup copy outside the repository before archiving, and keep hosted shared `src/` strictly separate from per-account storage (`STUDY_ACCOUNTS_DIR`).

## Backup and recovery

[Service persistence](service-persistence.md) documents account backup and restore into a new unused destination. Quiesce writers for cross-file consistency. Back up shared originals, retrieval databases, approved artifacts and configuration separately: the account backup tool does not capture the complete service. Copying a live SQLite file alone can omit journal data.

Prove recovery on disposable restored storage, including login, histories, calendar and original hashes. Keep an independent private backup and retention policy. Do not clear another operation's locks or stop a foreign process occupying the configured port. Missing monitoring, account recovery, quotas, supervised recovery or platform acceptance remain explicit operational gaps.
