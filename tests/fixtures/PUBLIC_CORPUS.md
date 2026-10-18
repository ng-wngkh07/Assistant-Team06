# Public document fixtures

The manifest `public_corpus_yc194.json` describes 27 authored documents across nine topics: 36 PDF pages and approximately 0.76 MB. With the two earlier samples, this fixture set contains 29 documents. These counts describe the fixture set, not model quality or corpus completeness.

The 27 documents were written for this project, independently of private books and training data, and are published under CC0-1.0. The manifest records topics, formats, page counts, sizes and SHA-256 hashes. This license does not change the licenses of the two earlier samples.

| Topic | Test content |
| --- | --- |
| Linear algebra | Linearity, addition and scalar multiplication |
| Calculus | Derivatives, conditions and rates of change |
| Discrete mathematics | Sets, intersection and logic |
| Object-oriented programming | Encapsulation, inheritance and polymorphism |
| Data structures | Stack/LIFO and queue/FIFO |
| General physics | Velocity and uniform motion |
| Cognitive psychology | Working and long-term memory |
| Game theory | Players, strategies and outcomes |
| Learning habits | Cues, behavior and feedback |

Each topic has three formats:

- `*_theory.pdf`: one page of theory without an exercise.
- `*_worked.pdf`: theory on page 1, Exercise 1 on page 2 and its answer on page 3.
- `*_unsolved.md`: theory and Exercise 2 without a source answer.

## Use and checks

Run `python scripts/dev.py demo`, then `python scripts/dev.py serve --profile demo`. This profile uses isolated fixture and data directories. Do not redirect it to private sources, databases or model artifacts. Models are not included in the fixture set.

Check structure and hashes with `python -m pytest -q tests/test_public_corpus_yc194.py`.

Review these behaviors separately:

1. Index all fixture documents without reading the private corpus.
2. Summarize and explain a topic without changing its conditions. Comparisons need evidence for both concepts; an incomplete request should ask for clarification.
3. Match Exercise 1 to its answer on page 3 of the same document. Do not treat theory as an exercise or attach an unrelated answer.
4. Identify the missing source answer for Exercise 2. Label any model-generated solution and its verification state separately.
5. Hide practice answers until submission, then grade and explain with evidence. Review should work without earlier incorrect answers.
6. Open the correct PDF page. Use sections for Markdown, without fabricated PDF links or unnecessary image previews.
7. Exercise missing sources, incomplete questions, a busy model, invalid JSON and ambiguous multiple-choice answers. Report or reject failures explicitly.

Do not use this set for training or as a model-quality holdout. The manifest marks `training_allowed=false` and `quality_holdout=false`. Passing these checks does not establish accuracy on private course materials, whole-corpus performance or adapter quality.
