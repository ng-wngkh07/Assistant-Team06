# Repository maintenance

## Review and merge

Contributors propose changes through pull requests. The repository owner performs merges after review and required checks. Write access to a feature branch does not need to imply permission to update `main`.

For owner-only merging on GitHub, configure a branch ruleset targeting `main` with **Restrict updates**, and grant only the owner a bypass limited to pull requests. Keep required status checks and other quality protections in a separate rule without that bypass. Available controls depend on the repository plan and ownership model. Inspect the current GitHub settings when changing policy; documentation is not proof that a rule is active.

Do not relax protection to get a failing pull request merged. A required review and permission to merge serve different purposes.

## Branch cleanup

Deleting a branch after merge is optional. Delete a merged feature branch when it has no dependent pull request or unfinished work. GitHub can automatically delete merged head branches. The merged commits remain in Git history; deleting a branch does not erase them. Preserve long-lived release branches and branches still in use.

## Public records

Keep commits, tracked files, `docs/`, PR titles/descriptions, and changelog entries focused strictly on technical product behavior, architecture, compatibility, and validation. Never commit or publish internal business documents, model-training runs or protocols, model-experimentation metrics, evaluation scorecards, paired answer comparisons, private corpus counts/excerpts, or personal work allocation and chat logs. Use Git authorship for attribution and keep private evaluation, training, and audit artifacts strictly inside local ignored storage (`data/runtime/`, `data/evaluation/`, `data/training/`). Run `python scripts/check_history.py --base origin/main` before committing or pushing to enforce both changelog and public-content guards.

## GitHub references

- [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Automatic branch deletion](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-the-automatic-deletion-of-branches)
