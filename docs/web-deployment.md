# Hosted web deployment assessment

The intended delivery can be a private web service. GitHub need not contain an installer, source documents, retrieval database or model weights. Keep code and deployment configuration separate from private runtime assets. This assessment does not authorize or claim a public deployment.

## Feasibility and cost

Vercel now supports Docker container functions in beta on all plans. Container functions still follow Function limits and scale down when idle; Docker packaging does not provide a persistent server or free model hardware. Hobby is intended for personal non-commercial use and can pause a limited resource after its allowance is exhausted. These policies can change. [Container guide](https://vercel.com/kb/guide/docker), [Hobby plan](https://vercel.com/docs/plans/hobby).

The documented Hobby Function budget is 2 GB / 1 vCPU, with a maximum request duration of 300 seconds and a 4.5 MB request-body limit. The current 9B model's weights alone exceed that memory budget. Existing timetable image uploads can also exceed the request limit. Packaging the whole application as a Function is therefore unsuitable for the current workload. These are deployment constraints, not a benchmark on Vercel. [Function limits](https://vercel.com/docs/functions/limitations).

A deployment receives a vercel.app address; owning a custom domain is a separate matter. A frontend address cannot replace backend compute, storage or uptime. [Domains](https://vercel.com/docs/domains/working-with-domains).

For a small private pilot, the practical starting point is the existing machine serving the backend, model and private storage, behind authenticated HTTPS access. A static frontend may be hosted separately, but a same-origin HTTPS proxy simplifies cookies, file access and streaming. This has electricity, connectivity and maintenance costs, and stops when the machine sleeps or disconnects. Free hosting with indefinite availability and sufficient 9B compute is not established.

The current HTML entry point is rendered by the backend to bind browser storage to the authenticated account. Serving the raw `static/index.html` directly from Vercel would bypass that binding. Proxy the application entry point as well as its APIs, or implement and verify an account bootstrap before loading the frontend. Keep model requests and large uploads off memory-limited Functions; verify streaming, cookies and original-file access through the selected proxy. No Dockerfile, Vercel deployment or Linux runtime acceptance is supplied by this change.

Docker can package a Linux-compatible backend on a suitable persistent host. Keep private seed assets mounted read-only and personal data on a separate backed-up volume. Never bake sources, databases, credentials or weights into a public image. Volumes provide persistence, not backups or access control by themselves. The present native runtime is verified on macOS/Metal; a Linux CPU/CUDA deployment needs a compatible pinned runtime and renewed model acceptance. MLX now documents Linux CPU/CUDA support, so its name alone does not establish portability of this application's pinned build. [Docker volumes](https://docs.docker.com/engine/storage/volumes/), [MLX](https://github.com/ml-explore/mlx).

## Database and service readiness

The existing knowledge database and retrieval sidecar are a shared learning corpus, not an account database. The original planner and chat history were single-workspace stores. The account layer now adds request-scoped planner/history/upload storage and durable per-feature guide state without replacing the shared corpus. Passwords are salted with scrypt; session tokens are stored as hashes in a separate database and sent using HttpOnly SameSite cookies. Sessions last up to 30 days and logout revokes the current session. This is a bounded local/private pilot implementation, not public-service certification.

Configuration:

- `STUDY_ACCOUNTS_DIR`: durable private account storage, outside public/static paths and Git.
- `STUDY_ACCOUNTS_REQUIRED=1`: require authentication even on loopback.
- `STUDY_ALLOW_REGISTRATION=1`: explicitly enable registration from non-loopback clients. Default registration is local only.
- `STUDY_SECURE_COOKIES=1`: require HTTPS cookies when serving behind a correctly configured trusted proxy. Use HTTPS and a single canonical origin; do not trust arbitrary forwarded headers.

Unaccounted loopback use retains the existing personal workspace. Non-loopback API access requires login. Account users cannot run shared-library indexing or OCR administration. Personal original uploads persist privately; account uploads are not added to the shared retrieval index. Account-specific study review items stored in the browser are namespaced; they are not yet synchronized between devices.

Before public service, complete access and source-rights review, upload size/storage quotas, account recovery and lifecycle, backup/restore drills, per-account inference budgets, durable background ingestion, concurrency/load acceptance, operational monitoring and Linux/container runtime acceptance. Distributed replicas also require shared session/storage coordination and a model queue; copying local SQLite files among ephemeral replicas is unsuitable. A small single-host service may retain SQLite after measured concurrency and recovery checks; a multi-host service should move transactional user data to a managed shared database and private objects to durable authorized storage.

Private hosting reduces accidental publication on GitHub. It does not establish permission to ingest, train on, show or let users download a source. Authorized source access, original-page citations, data minimization and the existing model/corpus acceptance gates remain necessary. Hosting and redistribution decisions must follow the actual permissions for each source.
