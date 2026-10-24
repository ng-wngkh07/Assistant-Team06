# Extended public debugging corpus

This extension contains **12 authored Markdown documents, 96 labeled review cases, and a fictional 14-course timetable**. The existing fixture corpus remains available. All new content is synthetic, published under CC0-1.0, and contains no copied private books, personal timetable, chat history, weights or vectors.

`extended_cases.json` records file hashes, byte counts, topic, question, simple/complex review label and rationale, separate verification risk, suggested processing level, expected outcome and a content-review rubric. These labels are development examples. They do not claim a trained classifier or a quality holdout, and `training_allowed=false` is explicit.

Complexity labels are provisional. Review linguistic complexity separately from verification risk before using them as classifier supervision: a single-step calculation can be a simple request while still requiring careful exercise verification. The public cases support this review; they are not an approved training set or a classifier acceptance score.

| Topic | Important boundary |
| --- | --- |
| Bayesian probability | Positive denominator and same probability model |
| Discrete information entropy | Finite discrete distribution, base-2 logarithm and bits |
| Statistics | Nonempty sample; no causal inference from a mean |
| Chemistry | Solution volume rather than solvent volume; mol/L |
| Biology | Double-stranded DNA; no strand-wide assumptions for arbitrary single strands |
| Networking | Bytes versus bits; ideal transfer without overhead |
| Relational databases | Primary-key uniqueness does not transfer to every foreign key |
| Algorithms | Sorted input, closed search interval and zero-based indices |
| Propositional logic | Material implication does not establish causation |
| Economics | Best excluded alternative rather than the sum of alternatives |
| Astronomy | Vacuum model and light-year as distance |
| Unit conversion | Squared area factor rather than a linear length factor |

Each document contains theory, a worked problem, a second problem without a source answer, and a clearly labeled untrusted instruction. Each topic has eight review cases: definition, conditions, worked example, generated-solution boundary, follow-up, injection, missing information and wrong-source scope. Review the relevant reading section; Markdown section numbers are not physical printed pages.

## Reproduce a problem

1. Install the environment in the repository README. Run `python scripts/dev.py demo` and `python scripts/dev.py serve --profile demo`. This indexes public documents into `data/team-demo`, separate from the local library.
2. Select an `extended_*.md` document. Copy the matching question from `extended_cases.json`, preserving its source selection. For a follow-up, use the recorded prior turns first.
3. Record the case ID, commit, OS, model revision and artifact hashes, selected mode, actual answer and cited section. Compare against `review_reference` and `rubric`; a retrieved quote alone is not a semantic PASS.
4. For missing source answers, distinguish an available source answer from a model-proposed solution and its verifier status. For negative cases, verify that another document is not silently substituted.
5. Report the reproduction using the issue template. Keep personal uploads and raw local logs out of the issue and Git history.

Validate fixture integrity and real model-free indexing with:

```bash
python -m pytest -q tests/test_extended_public_samples.py
```

This command uses temporary sources and databases. It does not call a language model. Running `eval/run.py` with stored predictions also does not measure live model accuracy. Real-model checks need separate output artifacts and a source/content review as described in [Testing](../../docs/testing.md).

## Multisubject timetable

`timetables/multisubject.json` and its matching public `multisubject.png` describe two distinct fictional courses on each weekday (ISO Monday=1 through Sunday=7), at 08:15–09:45 and 13:30–15:00. The semester range is 5–18 October 2026. Fourteen weekly event definitions produce 28 total occurrences in that two-week range.

The integration test saves a draft, confirms user-edited course names, then checks every saved subject, room, date, time and UTF-8 line boundary in the export. No personal schedule is read or changed. For the actual image-recognition flow, upload `multisubject.png` and compare every row against `multisubject.json`. The older fictional `timetables/demo.png` and `expected.json` remain a smaller case. `registered_positions.json` supplies authored OCR positions for long and wrapped course-name regression tests; it is not live OCR output. Recognition accuracy remains separate from JSON/store/export tests.

The corpus is for debugging and review. Do not silently add it to training data or report its successful cases as course-wide accuracy.

See [complexity labeling guidance](../../docs/question-complexity.md) before using these examples to design a future training release.
