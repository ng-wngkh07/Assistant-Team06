# Security

Study Agent is intended for trusted local use. The current API does not provide authenticated users or per-user authorization. Keep it on a trusted machine and network; do not expose it as a public document service without additional controls.

## Reporting a vulnerability

Report sensitive findings privately to the repository owner through an available private GitHub reporting or contact channel. Do not put credentials, private documents or exploit data in a public issue. For a non-sensitive report, include the affected revision, reproduction steps, impact and a minimal synthetic example.

If a credential is exposed, revoke it at the provider first. Coordinate remediation with the owner; do not rewrite shared Git history on your own.

## Data handling

Keep `.env`, personal uploads, conversations, SQLite files, embeddings, datasets, weights and private reports out of Git. Use synthetic test data. Preserve original documents and unresolved OCR states. Code licensing does not grant permission to redistribute source documents or models.

Public knowledge packs deliberately contain readable originals and source text. Review their rights, privacy and exact coverage before release; vectors do not conceal private content. Follow the [document distribution procedure](docs/data-distribution.md) and reconstruct an allowlisted pack rather than publishing a working database.

A past scan or a passing test suite is not a certification that a later revision is free from vulnerabilities. Review the exact public diff before publication.
