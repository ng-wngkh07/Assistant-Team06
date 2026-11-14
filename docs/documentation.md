# Documentation conventions

Write public project documentation in English. Use direct sentences, concrete behavior and verifiable claims. Explain what a reader needs to use, deploy, maintain or contribute to Study Agent.

- README: purpose, current capabilities, a short start path, limits and links.
- Setup guides: prerequisites, runnable commands, configuration and troubleshooting.
- Architecture: component boundaries, data flow and the source of current API contracts.
- Contributing and templates: reproduction, intended outcome, validation and review.
- Changelog: product changes and compatibility notes; no personal activity diary.

Keep internal work allocation, classroom submissions, tool transcripts and private operational evidence outside public documentation. Public examples must be clearly synthetic. Preserve legal attribution and original source material.

## Required documentation updates

Any change affecting installation, configuration, application use, APIs/user flows, storage/architecture, deployment/recovery or the contribution/testing process must inspect and update **README.md and CONTRIBUTING.md**, together with the relevant `docs/` guides, platform instructions and examples, in the same change. If either entry file is unaffected, give a specific reason in the pull request; do not add cosmetic edits. Missing required documentation leaves the change incomplete.

Keep hosted and local instructions consistent with actual code and validated commands. Distinguish implemented behavior, required acceptance and verified deployment. Keep downloading/running locally supported. Changes to structure must also update the documentation index and affected paths/links. Public guides are the product and operations manual; private checkpoints are not public documentation.

## Changelog format

Add changes under **Unreleased**, grouped as Added, Changed, Fixed, Removed or Security where relevant. State the effect on users and any required migration. Do not claim a version has shipped before release. Historical reference identifiers are retained as hidden compatibility metadata; they are not current roles or assignments.

## Project agent rule

The project rule is [`.agents/rules/study-agent.md`](../.agents/rules/study-agent.md). It uses YAML frontmatter with `trigger: always_on` and a concise description. Keep its constraints aligned with the contribution and testing guides. A valid file and syntax check establish configuration on disk; verify loading in the intended Antigravity workspace before claiming a live agent followed it.

## References consulted

- [GitHub: About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
- [GitHub: Contributing guidelines](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors)
- [GitHub: Pull request templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository)
- [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
- [GitHub Community: README feedback](https://github.com/orgs/community/discussions/157947)

The community discussion is supplementary advice. The structure here follows the needs and verified behavior of this repository.

- [Antigravity: Rules](https://www.antigravity.google/docs/rules/)
