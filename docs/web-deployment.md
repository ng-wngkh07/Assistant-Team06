# Web deployment assessment

Study Agent targets a hosted website and retains local installation. The current application has one FastAPI backend, a backend-rendered account-bound HTML entry point and static browser assets. Retrieval, account originals, model execution and persistence run on the backend host. Publishing GitHub code or static assets does not start that backend.

## Current single-host foundation

- Non-loopback API requests require authentication; operators can require it on loopback too. Local account provisioning and session cookies support an invited pilot.
- Account-scoped services separate durable originals, extracted records, histories, planners and feature guide state from the shared corpus and other accounts.
- Shared source originals and compatible precomputed retrieval stay on private host storage. Shared-library users form one authorized audience; this is not arbitrary per-document entitlement management.
- Source and original routes, QA and material flows must enforce current source identity. The opt-in shared-source watcher handles new files separately from account uploads; changed/missing reviewed sources require operator action.
- Model work uses the existing local runtime and resource coordinator. Native macOS MLX, Ollama on Windows and another host/runtime are separate compatibility targets.

These are implementation boundaries, not proof of production acceptance. Read [persistence](service-persistence.md), [configuration](configuration.md) and the [native host runbook](native-host-pilot.md) for current commands and process settings. Both the launching process environment and the `scripts/dev.py` `.env` `ENV_KEYS` allowlist support `STUDY_*` account, scaling, rate-limit, quota, and watcher settings (with process environment variables taking precedence).

## 20-user capacity, security hardening and scaling boundary

- **Per-account isolation and non-blocking metadata**: Each authenticated account receives its own SQLite storage (`history.db`, `uploads.db`, `timetable.db`) and isolated `dialogue.lock` (`account_dialogue_gate`), so session, history, planner, and document lookups across 20+ concurrent users do not wait on global model inference.
- **Bounded FIFO GPU inference admission**: Heavy generation, OCR, and indexing routes wait on a bounded FIFO queue (`STUDY_MAX_INFERENCE_QUEUE=20`, `STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS=25.0`) while limiting each account to one in-flight/queued model request at a time so one user cannot starve others.
- **Transport and anti-abuse controls**: ASGI middleware (`app/request_admission.py`) attaches HTTP security headers (and conditional `Strict-Transport-Security`), enforces body byte caps across all `/api/` mutation endpoints (`POST`, `PUT`, `PATCH`), applies sliding-window HTTP `429` rate limits per session bucket and IP, and enforces per-account upload file/byte quotas.
- **Health and infrastructure probes**: `/api/health` and `/api/status` report sanitized `infrastructure` (disk headroom, read-only KB readability, writable accounts directory, security posture, configured limits) and `admission` queue telemetry; `scripts/backend_service.py` enforces preflight disk checks and flags missing secure-cookie or open-registration settings when `public_url` uses `https://`.
- **Future multi-host expansion path**: Because account services sit behind the `AccountService` boundary (`app/account_services.py`), scaling beyond a single machine to hundreds of users can replace per-account SQLite, in-memory admission/rate-limit stores, and local `originals/` with PostgreSQL, Redis, and object storage behind the same route contracts.

## Hosted topology and zero-cost HTTPS links

Use one canonical HTTPS origin that reaches the backend-rendered entry point, APIs, streaming responses and authorized original-file routes. Keep model workers private. A separate static host must not serve raw `static/index.html` without verified account bootstrap, and a frontend deployment address does not provide model compute or durable backend storage.

Zero-cost HTTPS links without buying a domain or cloud GPU hosting:

- **Cloudflare Quick Tunnel (free, zero domain needed)**: `cloudflared tunnel --url http://127.0.0.1:8000` issues an HTTPS `https://<random>.trycloudflare.com` endpoint proxying directly to loopback port `8000`.
- **Tailscale Funnel / Serve (free `*.ts.net` TLS URL)**: `tailscale funnel 8000` (or `tailscale serve 8000` for private tailnet access) provides a persistent automated HTTPS URL without opening router ports.
- **DuckDNS + Caddy automatic Let's Encrypt TLS**: Free dynamic subdomain paired with Caddy when router ports `80`/`443` can forward directly to the backend host.

See [the native host runbook](native-host-pilot.md#free-https-deployment-options-no-paid-domain-or-hosting) for exact operator commands and required `STUDY_*` flags.

The current native 9B runtime needs the configured local artifacts and compatible hardware. Static-only hosting cannot execute it. A different operating system, container or model runtime needs explicit compatibility and renewed model/content acceptance. Never infer portability or sufficient capacity from a provider name or generic free-tier specification.

Private source/index/model locations and writable account/backup locations must be configured separately from public code and container build contexts. Mounting a persistent volume does not create a backup. Do not copy live SQLite files among replicas; a multi-host design needs coordinated sessions, transactional storage, originals and model jobs.

For a pilot on an existing computer, availability depends on that computer remaining awake, powered and connected. A provider-issued free hostname can address a service when available; it is distinct from owning a custom domain and does not supply 9B compute. No public URL, perpetual free compute, paid service or completed ingress setup is claimed by this repository. Check the selected provider's current terms and technical limits before setup.

## Acceptance before sharing a link

1. Freeze the reviewed code/configuration and verify final file hashes, process identity, cwd, runtime and model identity. Complete automated checks and actual browser/source-content review; keep unsupported or unrun platforms explicit.
2. Reconcile the complete agreed corpus, reviewed passages, original hashes and compatible precomputed retrieval before first account use. Preserve HOLD regions and outstanding source/privacy/model gates. Source rights govern the intended audience; authentication does not establish those rights.
3. Provision invited accounts, close remote registration, set secure cookies and restrict trusted proxy headers to the actual gateway. Verify unauthenticated, cross-account, source-download and large-upload behavior through the final HTTPS origin.
4. Measure concurrent users, GPU queueing, request limits, storage/inference quotas, long work, disconnects and recovery. Synchronous long work or in-memory queues are not durable job handling. Publish measured capacity and remaining limits.
5. Quiesce writers, back up private account and shared assets separately, restore to a new disposable location and verify login, histories, schedules and original hashes. Define retention and account export/deletion procedures before accepting long-lived user data.
6. Verify restart/supervision, monitoring, account recovery/lifecycle, source access and model behavior on the final deployed service. Record evidence for [REL-01 through REL-07](../DEFINITION_OF_DONE.md#production-release-acceptance), including first account use without user-operated indexing or training.

Follow [running/deployment](deployment.md) for the command sequence and rollback boundaries. The optional local knowledge-pack path remains documented in [knowledge packs](knowledge-packs.md). Public Git contains code/guides and safe fixtures; private originals, databases, vectors, weights, credentials and operational evidence stay outside it.
