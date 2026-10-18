# Definition of done

A change is ready for review when:

- The reported defect or intended user outcome is clear and reproducible.
- The implementation preserves source scope, provenance, API contracts and unrelated behavior.
- Relevant automated checks pass; skipped or unrun checks have explicit reasons.
- Model-facing changes have real-model evidence and a separate source/content review.
- The UI handles loading, failure, retry and completion consistently for the changed workflow.
- Documentation and the changelog describe the resulting behavior and limits.
- The diff contains no credentials, private documents, databases, model artifacts or internal work logs.
- Compatibility, migration and rollback needs are documented where applicable.
- Review findings are resolved and required checks apply to the revision being merged.

The owner merges through the repository's protected workflow. A green test suite alone does not establish model accuracy, corpus readiness or permission to activate a model.
