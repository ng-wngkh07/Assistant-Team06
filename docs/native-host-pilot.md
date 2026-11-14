# Native single-host web pilot

This runbook prepares the existing native model host for a same-origin website. It does not establish release acceptance or activate Internet access. Complete the source/privacy/model gates and record local UI, account isolation, restart/recovery and measured load acceptance before enabling ingress. A public URL is a separate operator action.

## Data flow and storage

A browser sends an authenticated HTTPS upload to the backend. The backend writes an immutable original under `STUDY_ACCOUNTS_DIR/<account_id>/originals/<sha256>.bin` and saves account-private metadata, extracted pages and retrieval chunks in that account's `uploads.db`. The client never needs filesystem access to the host. When the backend runs on your Mac, these files are on your Mac. If it later moves to a persistent remote server, they are on that server.

An upload is not copied into the shared `src/` folder. Private retrieval lets answers use the uploaded source without changing model weights. Shared-library admission and training are separate reviewed operations requiring source-use rights, purpose-specific consent, privacy review and quality acceptance. Uploading alone authorizes neither. The original, extraction, index and model weights have different lifecycles.

The upload API performs extraction and private indexing; it is not a verified durable asynchronous ingestion queue. Files manually copied into the shared source directory are detected only when `STUDY_SRC_AUTO_WATCH=1` is exported before startup. New settled sources are indexed incrementally; changed/missing reviewed originals require operator review and re-indexing. This is separate from account-private uploads and never triggers retraining. See [configuration](configuration.md#automatic-source-ingestion). A later ingestion worker should record account, original hash, extraction/index revision, pending/running/ready/needs-review/failed status and retry history, reconcile incomplete work after restart, and retain unreadable pages as unresolved. It must not auto-publish originals or admit them into a training pool.

## Prepare one native backend

Use the reviewed application checkout, its Python environment and the accepted native model runtime. Keep `src`, databases, account stores, model weights and backups outside public Git and container build contexts. Do not recreate the shared index during ordinary startup. The configured library must match the reviewed source inventory and retrieval model.

Create a private operator shell environment file outside the checkout and source it before launch, or configure those keys in a private ignored `.env` loaded by `scripts/dev.py` (process environment variables always take precedence). Replace example paths with existing persistent directories; do not copy sample data over the real library:

```sh
export AGENT_SRC_DIR='/absolute/private/library/src'
export AGENT_DATA_DIR='/absolute/private/library/data'
export STUDY_ACCOUNTS_DIR='/absolute/private/accounts'
export STUDY_GPU_RUNTIME_DIR='/absolute/private/runtime/gpu'
export MLX_MODEL_DIR='/absolute/private/models/qwen3.5-9b-4bit'
export MLX_BGE_MODEL_DIR='/absolute/private/models/bge-m3-mlx-fp16'
export DEFAULT_CHAT_MODEL='qwen3.5-9b-4bit'
export STUDY_ACCOUNTS_REQUIRED=1
export STUDY_ALLOW_REGISTRATION=0
export STUDY_SECURE_COOKIES=1
export FORWARDED_ALLOW_IPS='127.0.0.1,::1'
export STUDY_MAX_INFERENCE_QUEUE=20
export STUDY_INFERENCE_QUEUE_TIMEOUT_SECONDS=25.0
export STUDY_RATE_LIMIT_ENABLED=1
export STUDY_API_RATE_LIMIT_PER_MINUTE=300
export STUDY_INFERENCE_RATE_LIMIT_PER_MINUTE=30
export STUDY_ACCOUNT_MAX_FILES=50
export STUDY_ACCOUNT_MAX_STORAGE_MB=250
export STUDY_MIN_FREE_DISK_MB=512
```

Provision invited accounts locally before public ingress, using the existing account UI; do not leave remote registration enabled for convenience. Local HTTP provisioning needs a separate loopback-only startup before enforcing secure HTTPS cookies. Stop that instance before the HTTPS candidate starts. Secure-cookie mode must be active for the actual hosted origin.

After environment loading and local readiness checks, the intended upstream command from the application checkout is:

```sh
.venv/bin/python -m uvicorn app.server:app \
  --host 127.0.0.1 --port 8000 --workers 1 \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 \
  --timeout-keep-alive 15
```

On macOS, operators can alternatively drive that exact loopback worker command through `scripts/backend_service.py` (`start`, `status`, `restart`, `stop`, `open`, `logs`) and a copied folder from `launchers/macos/backend-quick-start/` backed by a private `backend.config.json` outside Git. Keep the machine awake and connected; the controller provides session-detached supervision, strict process-identity stop/restart checks, foreign-port refusal, disk-space and account-directory preflight checks, HTTPS/cookie security warnings, bounded log rotation, and bounded crash backoff without altering foreign listeners.

One worker preserves the current single-process model/resource coordination assumptions while supporting ~20 concurrent authenticated users through per-account SQLite/lock isolation (`account_dialogue_gate`), a bounded FIFO GPU inference queue (`acquire_inference`), sliding-window rate limiting, and per-account upload storage quotas. Keep port 8000 bound to loopback; expose only the reviewed HTTPS gateway. Do not use wildcard forwarded-header trust. The native `.train-venv` runtime and model artifacts must pass the application's runtime and real-inference checks in the installed checkout; a successful Python import alone is insufficient. [Uvicorn settings](https://uvicorn.dev/settings/), [deployment](https://uvicorn.dev/deployment/).

## Free HTTPS deployment options (no paid domain or hosting)

Operators can publish the loopback backend (`http://127.0.0.1:8000`) over HTTPS without paying for cloud GPUs or purchasing a custom domain:

1. **Cloudflare Quick Tunnel (free temporary `*.trycloudflare.com` HTTPS URL, zero domain required)**:
   Install `cloudflared` (for example `brew install cloudflared`), start the backend with `STUDY_ACCOUNTS_REQUIRED=1`, `STUDY_ALLOW_REGISTRATION=0`, `STUDY_SECURE_COOKIES=1`, and `FORWARDED_ALLOW_IPS=127.0.0.1,::1`, then run:

   ```sh
   cloudflared tunnel --url http://127.0.0.1:8000
   ```

   Share only the generated `https://<subdomain>.trycloudflare.com` URL with invited account holders; stop the tunnel to close external ingress immediately. Note that Quick Tunnel URLs change on each tunnel restart and remain subject to Cloudflare service terms.
2. **Tailscale Funnel / Serve (free `*.ts.net` TLS URL, no router port forwarding)**:
   Run `tailscale funnel 8000` for an authenticated public HTTPS link on your free `*.ts.net` hostname (or `tailscale serve 8000` to restrict HTTPS access exclusively to devices on your tailnet). Keep `STUDY_SECURE_COOKIES=1` and `STUDY_ACCOUNTS_REQUIRED=1` active.
3. **Free Dynamic DNS (DuckDNS) + Caddy automatic Let's Encrypt TLS**:
   When router ports `80` and `443` can forward directly to the backend host, register a free `<name>.duckdns.org` subdomain pointed at your public IP and run Caddy with the minimal configuration below (replacing the example DuckDNS hostname with your own):

   ```caddyfile
   your-study-host.duckdns.org {
       request_body {
           max_size 28MiB
       }
       reverse_proxy 127.0.0.1:8000
   }
   ```

Replace the domain only after deciding the real address and verifying source/access permissions. Validate the configuration with the installed Caddy version before running it. The request-body cap is a gateway ceiling, not an account storage quota; account upload limits and abuse budgets still need acceptance. The upload API uses Base64 JSON, so encoded requests are larger than raw files. Verify both limits with the actual UI. Caddy returns413 when the body limit is exceeded. [Request-body limits](https://caddyserver.com/docs/caddyfile/directives/request_body).

Caddy keeps the original Host and provides forwarded headers. Normal `text/event-stream` responses are flushed immediately, but the application's complete streaming/disconnect behavior must still be tested through the gateway. Keep authentication cookies, API calls, static files and original-source links on the same HTTPS origin. Do not expose the account directory, shared database, model directory or `src/` through a file server. [Reverse proxy behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

The default public HTTPS setup needs correct public DNS, reachable ports80/443 and persistent writable certificate storage. Router forwarding, firewalls, dynamic addresses or carrier-grade NAT may require a different ingress arrangement. No domain purchase, router change, tunnel or public service is created by this runbook. [Automatic HTTPS prerequisites](https://caddyserver.com/docs/automatic-https).

## Verify before sharing a link

- Unauthenticated access to history, calendars, uploads and shared originals is denied. Two invited accounts cannot access each other's records or guessed file/practice identifiers.
- Actual uploads, source citations/downloads, private QA and generated practice work through HTTPS and survive a backend restart. Failed/unreadable uploads show truthful status. No private file is added to shared retrieval or training.
- Automatic calendar proposals respect actual busy intervals, rest, deadlines and existing sessions. Proposals do not save until confirmed; stale drafts reject without partial changes. Calendar exercises reuse the saved practice and review flow.
- Measure authenticated HTTP/database capacity separately from simultaneous model requests. Record hardware, request mix, sample size, latency, failures and busy behavior. Concurrent HTTP requests are not a supported-user count.
- Stop writes for a cross-file recovery snapshot; use `scripts/backup_service.py` to back up the private account store and restore into a new unused directory. Verify restored login, history, calendar and original hashes without replacing the serving store. Store an independent private backup with an operator-defined retention policy.
- Verify source permissions and full-library/model gates, HTTPS cookies, oversized requests, account lifecycle/recovery, storage/inference quotas, durable long-job handling and operational monitoring. Remaining failures keep broad public access closed.

A foreground development process is not an uptime service. A long-running host additionally needs supervised startup/restart, controlled updates, an awake connected machine, secure storage and monitored backups. Test that installation independently before claiming availability. A single-host SQLite pilot may remain viable after measured contention and recovery checks; multiple hosts require shared transactional/object storage and coordinated model work rather than copying live SQLite files between replicas.
