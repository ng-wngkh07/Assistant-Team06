# Definition of done

A change is ready for review when:

- The reported defect or intended user outcome is clear and reproducible.
- The implementation preserves source scope, provenance, API contracts and unrelated behavior.
- Relevant automated checks pass; skipped or unrun checks have explicit reasons.
- Model-facing changes have real-model evidence and a separate source/content review.
- Changed user flows work in the actual served web application on the target machine, with the serving process/revision and visible results recorded. Mocked tests and direct API calls alone do not satisfy this gate; unavailable target platforms remain explicitly pending.
- Before push or demo readiness, evidence identifies the real checkout/install path and browser URL, matches the final serving files, and covers fresh-clone startup when required. Later relevant edits require renewed runtime verification.
- The UI handles loading, failure, retry and completion consistently for the changed workflow.
- Documentation and the changelog describe the resulting behavior and limits.
- The diff contains no credentials, private documents, databases, model artifacts or internal work logs.
- Compatibility, migration and rollback needs are documented where applicable.
- Review findings are resolved and required checks apply to the revision being merged.
- Newly reported release defects are reproduced in the actual application and real configured paths. After source review and fixes, one consolidated final acceptance run covers the frozen candidate; a failed run remains failed, with affected flows rechecked after repair.

The owner merges through the repository's protected workflow. A green test suite alone does not establish model accuracy, corpus readiness or permission to activate a model.

## Production release acceptance

An end-user release also satisfies every [production release requirement](AGENT.md#production-release-requirements). Keep the requirement IDs in the local evidence checkpoint so later work cannot replace the requested outcome with a demo.

- **REL-01:** The real source inventory is reconciled with the delivered originals, passages and compatible retrieval data. Missing or unresolved documents are explicit; full-library acceptance requires the complete agreed real corpus, not synthetic fixtures.
- **REL-02:** Answers from the installed application lead to the correct original, page or section and supporting passage; original downloads match the delivered files. Source access works on the recipient's machine.
- **REL-03:** Fresh installation uses the integrity-checked precomputed knowledge pack and answers without user-operated initial indexing, embedding or training. Required runtime/model prerequisites are documented and verified separately.
- **REL-04:** Installed model, adapter state, tokenizer, backend, prompts, reasoning, context and decoding match the accepted configuration. Real-model content checks pass, and claims about training match the actual artifacts and runtime state.
- **REL-05:** Personal uploads survive restart in a documented writable library, remain retrievable with accessible originals and are preserved during updates. Uploading alone grants no publication or training approval.
- **REL-06:** Source redistribution, privacy and source-quality reviews cover the delivered assets. Public code, permitted knowledge packs, accepted model artifacts and private training/user data have explicit separate boundaries; private databases and derived private content are excluded from every public output.
- **REL-07:** Final distribution evidence covers the actual UI/model, source access, initial startup, personal storage and restart on each claimed platform. Software, model, corpus and release statuses are reported separately. Unrun or failing mandatory checks remain pending, and a smaller sample release cannot be substituted without an explicit change of scope.

Full-corpus acceptance also requires a [complete page content review](AGENT.md#complete-page-content-review): every physical page or slide has a source-bound content decision, including pages without extraction warnings. The coverage ledger identifies valid complete reviews, partial/HOLD regions, remaining pages and reconciliation with the actual indexed passages. Text, formulas, units, tables, code, diagrams and handwriting are checked against the original; unresolved source defects remain explicit. Extraction repairs and invalidated vectors are verified in isolation before any authorized serving update. Neither sampling nor review-schema validation establishes that all page contents were checked.
