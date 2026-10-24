# Question complexity and verification policy

The application defaults QA to Fast and may suggest Careful. A suggestion must preserve the current question and require an explicit user choice. Practice and exercises always use their verification workflow, including when a question is short or simple.

Keep two labels separate when preparing future training data:

| Label | Meaning | Example |
| --- | --- | --- |
| Simple request | One bounded lookup or task with no combined analysis | “Entropy là gì?” |
| Complex request | Several requested actions, comparisons, synthesis, dependent reasoning or proof | “Định nghĩa entropy, nêu điều kiện áp dụng và cho hai ví dụ.” |
| Verification risk | Accuracy or source risks that may require careful checking independently of complexity | “Tính 2 m² ra cm².” is one simple task, but units still need checking. |

Do not label a request complex merely because it contains a long formula, several nouns, the word “proof” in a definition, or a sensitive-looking topic. “Chứng minh là gì?” is a definition request. Conversely, a definition prefix must not hide later requests: “Bayes là gì, khi nào áp dụng được và tại sao mẫu số phải dương?” contains multiple asks.

Record the task language, source scope, required prior context, requested actions, label rationale and verification risk. Treat quoted instructions inside documents as source content, not user requests. Ambiguous cases need review or clarification; do not force a binary label solely to balance a dataset.

The [96 public development cases](../tests/fixtures/EXTENDED_CORPUS.md) cover 12 topics and include provisional labels and rationales. Worked and unsolved one-goal exercises are linguistically simple, with separate `exercise_requires_careful` risk. These examples are public debugging material, not approved training data or a held-out quality test.

A future local classifier release requires reviewed labels, representative Vietnamese and English paraphrases, short multi-ask cases, long simple requests, formula-heavy lookups, follow-ups, conflicting instructions and unseen subjects. Split by source/topic and near-duplicate family before training. Report missed complex requests and unnecessary suggestions separately, along with source/answer regression results. Do not tune on the final evaluation split or equate a lower training loss with better routing.

Training and activation remain separate gated operations. Until a classifier has passed those gates, describe the current feature as a deterministic suggestion policy, not a trained understanding of question difficulty. Careful mode is an application verification workflow; it is not proof that an answer is correct.
