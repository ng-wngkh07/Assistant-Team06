# Product roadmap

The goal is reliable question answering over a learner's permitted materials. Prioritize changes by observed errors and their effect on the learner.

| Priority | Outcome | Acceptance evidence |
| --- | --- | --- |
| P0 | Preserve source scope, formulas, conditions and claim attribution | Regression cases and real-model content review; no fabricated sources |
| P0 | Make upload, extraction, generation and replay failures explicit | API/UI checks with isolated data and failure-path tests |
| P0 | Keep product documentation aligned with the running application | Verified setup commands, working links and accurate capability descriptions |
| P1 | Validate one course end to end | Independent source/page labels, answer and refusal scoring, error analysis and a learner pilot |
| P1 | Improve retrieval and extraction where evaluation shows losses | Before/after comparison on the same holdout, including OCR and formula cases |
| P1 | Make review workflows more useful | Source review states, usable feedback and retained evidence for corrections |
| P2 | Support a shared deployment | Identity, document/session permissions, isolation, queues, monitoring and recovery tests |
| P2 | Improve learning outcomes | Measured task completion time and learning results against a defined baseline |

Core safety and integration checks are implemented incrementally. Passing them does not mark every row complete. Set course-level quality and latency targets after collecting a representative baseline; do not infer them from a demo or a small regression suite.
