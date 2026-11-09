# Hosted web deployment assessment

The intended delivery can be a private web service. GitHub need not contain an installer, source documents, retrieval database or model weights. Keep code and deployment configuration separate from private runtime assets. This assessment does not authorize or claim a public deployment.

## Feasibility and cost

Vercel now supports Docker container functions in beta on all plans. Container functions still follow Function limits and scale down when idle; Docker packaging does not provide a persistent server or free model hardware. Hobby is intended for personal non-commercial use and can pause a limited resource after its allowance is exhausted. These policies can change. [Container guide](https://vercel.com/kb/guide/docker), [Hobby plan](https://vercel.com/docs/plans/hobby).

The documented Hobby Function budget is 2 GB / 1 vCPU, with a maximum request duration of 300 seconds and a 4.5 MB request-body limit. The current 9B model's weights alone exceed that memory budget. Existing timetable image uploads can also exceed the request limit. Packaging the whole application as a Function is therefore unsuitable for the current workload. These are deployment constraints, not a benchmark on Vercel. [Function limits](https://vercel.com/docs/functions/limitations).

A deployment receives a vercel.app address; owning a custom domain is a separate matter. A frontend address cannot replace backend compute, storage or uptime. [Domains](https://vercel.com/docs/domains/working-with-domains).

For a small private pilot, the practical starting point is the existing machine serving the backend, model and private storage, behind authenticated HTTPS access. A static frontend may be hosted separately, but a same-origin HTTPS proxy simplifies cookies, file access and streaming. This has electricity, connectivity and maintenance costs, and stops when the machine sleeps or disconnects. Free hosting with indefinite availability and sufficient 9B compute is not established.

The current HTML entry point is rendered by the backend to bind browser storage to the authenticated account. Serving the raw `static/index.html` directly from Vercel would bypass that binding. Proxy the application entry point as well as its APIs, or implement and verify an account bootstrap before loading the frontend. Keep model requests and large uploads off memory-limited Functions; verify streaming, cookies and original-file access through the selected proxy. No Dockerfile, Vercel deployment or Linux runtime acceptance is supplied by this change.

Docker can package a Linux-compatible backend on a suitable persistent host. Keep private seed assets mounted read-only and personal data on a separate backed-up volume. Never bake sources, databases, credentials or weights into a public image. Volumes provide persistence, not backups or access control by themselves. The present native runtime is verified on macOS/Metal; a Linux CPU/CUDA deployment needs a compatible pinned runtime and renewed model acceptance. MLX now documents Linux CPU/CUDA support, so its name alone does not establish portability of this application's pinned build. [Docker volumes](https://docs.docker.com/engine/storage/volumes/), [MLX](https://github.com/ml-explore/mlx).

## Recommended rollout

The recommendation is a bounded, single-host pilot first. Keep the current pinned MLX model native on the existing Mac and serve the application through one authenticated HTTPS origin. Keep the model worker private. Mount or configure corpus assets separately from writable accounts and backups. A Linux Docker container cannot be assumed to inherit the Mac host's Metal device. The current runtime has not been accepted inside a Linux container. MLX documents separate macOS, Linux CPU and CUDA installations; selecting one changes the execution environment and requires a fresh compatibility and model-quality check. [MLX installation](https://ml-explore.github.io/mlx/build/html/install.html).

| Option | Practical use | Cost and limitations |
| --- | --- | --- |
| Existing Mac, native backend/model, authenticated HTTPS gateway | Initial invite-only pilot; preserves the currently verified model runtime | Reuses hardware; electricity, Internet, backups and maintenance remain. Availability depends on the machine staying awake and connected. Public ingress is a separate setup and acceptance step. |
| Vercel frontend/address with an external backend | Optional presentation layer after proxy and account-bootstrap acceptance | A `vercel.app` deployment address is included, but backend hardware and durable storage are still needed. Hobby is personal/non-commercial and has quotas. |
| Persistent Linux host with Docker, private object storage and eventually PostgreSQL | Later public service or multiple backend replicas | Requires a compatible model runtime, measured capacity, persistent volumes, backup operations and a hosting budget. No perpetual free 9B capacity is established. |

Vercel external rewrites have a documented 120-second proxy timeout. Current AI and OCR operations can exceed that time, so a simple rewrite is not a complete hosting solution. Long work should use an authenticated job API with short status requests and durable results, or a separately verified streaming path and suitable gateway. Neither mechanism is supplied merely by a deployment configuration. Verify cookies, original-file authorization, account-specific HTML bootstrap, disconnect behavior and large uploads through the actual selected gateway before exposing it. The 4.5 MB Function body limit applies to Functions; do not assume every external rewrite has the same body limit. [Proxy limits](https://vercel.com/docs/limits#proxied-request-timeout), [Function payload limits](https://vercel.com/docs/functions/limitations#request-body-size).

Use the following release sequence; these are launch acceptance criteria, not claims that deployment has already occurred:

1. Complete database migration/isolation and backup/restore verification on disposable copies. Keep source databases and originals intact. Restore into a new directory and prove the restored application's accounts, history, schedules and originals work.
2. Select an explicit rights-approved shared library. Authentication protects access; it does not grant permission to redistribute PDFs, previews or retrieved passages. Keep personal uploads private and outside the shared corpus and training pipeline.
3. Run an invite-only same-origin HTTPS pilot with registration closed by default, secure cookies, trusted proxy settings, request/upload limits and per-account inference budgets. Exercise concurrent users, queued GPU work, restart and failed requests on the actual host. Publish a measured capacity limit rather than deriving one from generic SQLite throughput examples.
4. Exercise an operator recovery drill and independent private backup destination. A persistent volume is not a backup. Set retention and account export/deletion procedures before accepting other people's long-lived data.
5. Open broader access only after account recovery/lifecycle, abuse controls, monitoring, durable long-job handling and source/model release gates pass. Move transactional user data to PostgreSQL and originals to private authorized object storage when multiple hosts or measured write contention justify it; do not copy live SQLite files between replicas.

Free tiers are a pilot cost option, not a long-term service guarantee. As checked against official documentation on 2026-11-09, Hobby can pause resources after allowances are exhausted and restricts use to personal non-commercial projects. Custom domain ownership and renewals are separate from the included deployment address. No paid account, public tunnel, domain purchase or production deployment has been created by this work. [Hobby terms and quotas](https://vercel.com/docs/plans/hobby), [Domain behavior](https://vercel.com/docs/domains/working-with-domains).

## Database and service readiness

Versioned migrations, per-account private originals and operator backup/restore tools are described in [service persistence](service-persistence.md). These are single-host building blocks, not public-service acceptance.

The existing knowledge database and retrieval sidecar are a shared learning corpus, not an account database. The original planner and chat history were single-workspace stores. The account layer now adds request-scoped planner/history/upload storage and durable per-feature guide state without replacing the shared corpus. Passwords are salted with scrypt; session tokens are stored as hashes in a separate database and sent using HttpOnly SameSite cookies. Sessions last up to 30 days and logout revokes the current session. This is a bounded local/private pilot implementation, not public-service certification.

Configuration:

- `STUDY_ACCOUNTS_DIR`: durable private account storage, outside public/static paths and Git.
- `STUDY_ACCOUNTS_REQUIRED=1`: require authentication even on loopback.
- `STUDY_ALLOW_REGISTRATION=1`: explicitly enable registration from non-loopback clients. Default registration is local only.
- `STUDY_SECURE_COOKIES=1`: require HTTPS cookies when serving behind a correctly configured trusted proxy. Use HTTPS and a single canonical origin; do not trust arbitrary forwarded headers.

Unaccounted loopback use retains the existing personal workspace. Non-loopback API access requires login. Account users cannot run shared-library indexing or OCR administration. Personal original uploads persist privately; account uploads are not added to the shared retrieval index. Account-specific study review items stored in the browser are namespaced; they are not yet synchronized between devices.

Before public service, complete access and source-rights review, upload size/storage quotas, account recovery and lifecycle, backup/restore drills, per-account inference budgets, durable background ingestion, concurrency/load acceptance, operational monitoring and Linux/container runtime acceptance. Distributed replicas also require shared session/storage coordination and a model queue; copying local SQLite files among ephemeral replicas is unsuitable. A small single-host service may retain SQLite after measured concurrency and recovery checks; a multi-host service should move transactional user data to a managed shared database and private objects to durable authorized storage.

Private hosting reduces accidental publication on GitHub. It does not establish permission to ingest, train on, show or let users download a source. Authorized source access, original-page citations, data minimization and the existing model/corpus acceptance gates remain necessary. Hosting and redistribution decisions must follow the actual permissions for each source.
