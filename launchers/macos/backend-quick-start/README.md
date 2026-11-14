# macOS Backend Quick-Start Templates

Reusable operator launcher templates for running Study Agent as a local macOS
service behind an authorized reverse proxy or local browser session.

## Setup

1. Copy this folder to an ignored operator runtime location outside the Git
   repository (for example `data/runtime/Backend Quick Start/`).
2. Copy `config.example.json` to `backend.config.json` inside that runtime
   folder and set `"configured": true` once all explicit paths match your local
   installation. Never commit `backend.config.json` or private data to Git.
3. Verify configuration before starting:

```zsh
./Check\ Status.command
```

## Actions

- `Start Backend.command`: Preflights explicit checkout, `.venv`,
  `.train-venv`, source library, `knowledge_base.db`, account store, models,
  and shared GPU lock directory; refuses foreign port conflicts; starts a
  session-detached supervisor managing one bounded `uvicorn` worker.
- `Check Status.command`: Verifies managed supervisor/backend process identity,
  active Git checkout commit, HTTP `/api/health` readiness, model readiness,
  corpus counts, recovery counters, and log locations.
- `Restart Backend.command`: Stops only the identity-verified managed service and
  starts a fresh instance.
- `Stop Backend.command`: Writes a stop intent marker and cleanly stops only the
  identity-verified supervisor and backend child without triggering crash
  restarts or touching unrelated processes.
- `Open App.command`: Opens the configured `public_url` in the default browser
  without guessing external URLs.
- `View Logs.command`: Shows recent lines from `supervisor.log` and
  `backend.log`. Logs rotate automatically at configured size limits.
