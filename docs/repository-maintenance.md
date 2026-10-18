# Repository maintenance

## Review and merge

Contributors propose changes through pull requests. The repository owner performs merges after review and required checks. Write access to a feature branch does not need to imply permission to update `main`.

For owner-only merging on GitHub, configure a branch ruleset targeting `main` with **Restrict updates**, and grant only the owner a bypass limited to pull requests. Keep required status checks and other quality protections in a separate rule without that bypass. Available controls depend on the repository plan and ownership model. Inspect the current GitHub settings when changing policy; documentation is not proof that a rule is active.

Do not relax protection to get a failing pull request merged. A required review and permission to merge serve different purposes.

## Branch cleanup

Deleting a branch after merge is optional. Delete a merged feature branch when it has no dependent pull request or unfinished work. GitHub can automatically delete merged head branches. The merged commits remain in Git history; deleting a branch does not erase them. Preserve long-lived release branches and branches still in use.

## Public records

Keep PR titles and changelog entries focused on behavior, compatibility and validation. Do not include internal staffing, personal work allocation or operational chat logs. Use Git authorship for attribution. Keep private artifacts and sensitive incident details in controlled storage.

## GitHub references

- [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Automatic branch deletion](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-the-automatic-deletion-of-branches)
