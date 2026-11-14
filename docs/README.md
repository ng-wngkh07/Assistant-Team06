# Documentation

These guides describe Study Agent's learner workflows, single-host operations and development contracts. Learners access the hosted website by link. Source-checkout installation is for operators and developers, not a required learner workflow. Deployment instructions are not evidence that a public service is running.

## Learners

| Guide | Use it for |
| --- | --- |
| [Usage](usage.md) | Questions, source access, private attachments, guest uploads and result states |
| [Study planning](study-planning.md) | Commitments, deadlines, proposed sessions and calendar alarms |
| [Timetable workflow](timetable-workflow.md) | Image review, confirmation and export |
| [Context management](context-management.md) | Conversation budget and continuation |
| [Question complexity](question-complexity.md) | Fast/Careful suggestions and verification risk |
| [Citation presentation](ui-sources.md) | What evidence cards and source links mean |

## Installation and operations

| Guide | Use it for |
| --- | --- |
| [macOS setup](../README_MAC.md), [Windows setup](../README_WINDOWS.md) | Operator/developer demo and backend model installation |
| [Deployment](deployment.md) | Start, update, preserve data and verify a hosted or local instance |
| [Configuration](configuration.md) | Profiles, environment variables, accounts and watched sources |
| [Native host pilot](native-host-pilot.md) | Same-origin HTTPS on the native model host |
| [Web deployment assessment](web-deployment.md) | Hosting/runtime boundaries and remaining service acceptance |
| [Service persistence](service-persistence.md) | Account stores, migrations, backup, restore and legacy import |
| [Knowledge packs](knowledge-packs.md) | Optional reviewed precomputed library import for operators |
| [Local performance](local-performance.md) | Measure end-to-end model latency and memory |

## Developers and maintainers

| Guide | Use it for |
| --- | --- |
| [Contributing](../CONTRIBUTING.md) | Reproduction, implementation, validation and feature PRs |
| [Architecture](architecture.md) | Backend/UI, retrieval, accounts, storage and component boundaries |
| [AI interface](ai-interface.md) | Optional generation protocol and error contracts |
| [Testing](testing.md) | Regression, actual UI/model and platform acceptance |
| [Security checks](security-check.md) | Publication checks and reporting boundaries |
| [Repository maintenance](repository-maintenance.md) | Review, owner merge policy and public records |
| [Documentation conventions](documentation.md) | Required same-change documentation updates |
| [Roadmap](roadmap.md) | Outcomes and evidence still required |

## Sources and models

| Guide | Use it for |
| --- | --- |
| [Data preparation](data-preparation.md) | Complete-page review and vector provenance |
| [Data distribution](data-distribution.md) | Hosted access versus optional public packs, rights and privacy |
| [Model selection](model-selection.md) | Pinned artifacts, historical measurements and rollback |
| [RAG and training](rag-and-training.md) | Retrieval, upload/data boundaries and teacher supervision |
| [LoRA architecture](lora-architecture.md) | Required adaptation design and separate training/activation gates |

The running `/docs` page defines HTTP contracts. [AGENT.md](../AGENT.md) and [Definition of done](../DEFINITION_OF_DONE.md) define review/release requirements. Public guides contain no private inventories, operational transcripts or personal work allocation. A guide may describe a required acceptance check without claiming it has passed.
