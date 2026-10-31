# Required RAG and LoRA architecture

## Product requirement and current implementation

The required application architecture combines retrieval-augmented generation (RAG) with a separately trained and accepted LoRA adapter on the selected base model. LoRA is a required part of the complete product, particularly for source-grounded review-question and answer generation. A larger local teacher model supplies candidate training examples; source and content review determines which examples may train the student. This teacher-assisted preparation is an offline development activity, separate from interactive inference.

The current macOS implementation uses the pinned local MLX Qwen3.5 9B 4-bit base with RAG and no active accepted adapter. It is an interim implementation and an experimental control, not fulfillment of the complete RAG-and-LoRA requirement. Descriptions elsewhere of an unadapted base or future fine-tuning report implementation status; they do not make LoRA optional in the required product architecture. An unsuccessful adapter trial leaves the architecture requirement incomplete rather than permitting the unadapted control to be relabeled as the final architecture.

This requirement does not start a training job, accept historical datasets for a different base, or activate an adapter. The exact data, training protocol and candidate must satisfy the gates below. Existing source, privacy, local-only execution and production acceptance requirements continue to apply.

## Responsibilities

| Component | Required responsibility |
|---|---|
| Reviewed originals and retrieval data | Supply permitted evidence with accessible document, page or reading-section mappings. |
| RAG | Retrieve and admit relevant source passages within the actual context budget. |
| Larger local teacher | Propose questions, correct answers, explanations and rejection controls from approved passages and an authored subject/objective/difficulty blueprint. |
| Source and content review | Check actual source support, conditions, arithmetic, uniqueness, difficulty, privacy and suitability for training; retain rejected and unresolved records. |
| LoRA-adapted student | Learn the required source-grounded response and question-generation behavior on the same exact base artifact used for evaluation and serving. |
| Application validators and independent acceptance | Enforce source identity, schema, answer/explanation consistency and rejection behavior, including when the adapted model is confident. |

An adapter may also support text QA, explicit insufficient-evidence responses, exercise solutions, verification and session summaries. Each affected task needs retention testing. Improvement of image recognition or timetable OCR is a separate task; a text adapter does not establish vision acceptance.

Teacher generation is not source proof. A larger model may still produce an incorrect key, omit an initial state or unit, invent a rule, or label an easy definition as hard. Student generation and student self-review also cannot replace independent source/content assessment.

## Required offline preparation

1. Pin the student artifact/revision, tokenizer, backend, chat template, reasoning setting and application prompt/schema versions. Preserve originals and existing review/HOLD decisions.
2. Split by document family, edition, source section and exercise/template ancestry before teacher generation. Keep a final evaluation set untouched by teaching, prompt selection and hyperparameter selection. Historical development/validation cases remain development material.
3. Select and pin a larger local teacher using source-reviewed pilot examples and measured local resource feasibility. Teacher size is a selection constraint, not evidence of correctness. No private document or question is sent to a hosted teacher under the current local-only contract.
4. Generate candidate questions and targets from permitted source material and the blueprint. Cover difficulty, subject, language, question count, answer position, multiple admitted passages and insufficient-evidence cases. Retain teacher identity and all generation settings.
5. Independently review every admitted target. Bind it to original/source/passage hashes and record conditions, evidence, answer, derivation, distractor validity and rejection reasons. Do not promote a record merely because JSON parses, a quote occurs in a passage, or a teacher approves it.
6. Rebuild inputs with current application prompt producers. Use request-local passage handles rather than persistent database chunk IDs. Recount the complete serialized input and target with the selected 9B tokenizer and explicit non-thinking settings. Verify assistant-only loss boundaries.

## Sequence length and hardware

Training sequence length is selected from measured data coverage and actual training performance on the target hardware. It is not inherited from an old adapter, a library default, the inference context limit or the base model's advertised maximum context.

Measure full serialized input-plus-target token distributions by task, difficulty, language and passage count. Then, under separately authorized isolated training checks, measure forward/backward peak memory, optimizer-state allocation, finite gradients, swap growth, step time and useful supervised tokens per second at increasing lengths. Keep the base, LoRA target modules/rank, effective batch and software version fixed during that comparison.

Select the shortest measured feasible length that preserves the reviewed workload. Handle genuinely longer examples explicitly through a measured longer bucket or faithful task restructuring that preserves source evidence and targets. Never silently truncate, remove conditions or discard difficult examples to make a shorter setting appear sufficient. Recount regenerated teacher targets before making the selection final.

## Comparison and acceptance

Compare the same exact base plus RAG against that base plus the candidate LoRA and the same RAG. Freeze the library/index snapshot, admitted context, prompts, decoding, reasoning, context/output budgets and application validators. Use a fixed-context comparison to isolate adaptation, followed by an end-to-end application comparison to measure actual retrieval and user-visible output.

Question-generation evaluation must measure source-supported correctness, unique answer, key/choice/explanation agreement, preserved conditions and units, exact source mappings, requested difficulty, verified count fulfillment and rejection of unsupported or ambiguous cases. Report raw candidate quality and independently accepted output yield separately, so filtering every question cannot masquerade as high quality.

Include correct controls as well as wrong-key, wrong-intermediate-step, missing-initial-state, wrong-unit, duplicate-correct-choice, unsupported-explanation, injection and source-handle controls. Do not use the adapted model as the only judge of its own questions. Retention checks cover other supported text features, privacy, unsupported questions and source access. Report latency, generation throughput and local memory independently of quality.

Publish or activate an adapter only after its exact weights and base identity are bound to the accepted comparison and source review. Update and verify the native loading and acceptance path before activation: the present runtime deliberately rejects adapters and historical acceptance evidence is not a new 9B protocol. Conversion or another backend requires its own verified compatibility and content checks.

See [model selection](model-selection.md), [retrieval and training](rag-and-training.md), [testing](testing.md) and the [production definition of done](../DEFINITION_OF_DONE.md) for existing operational boundaries. Meeting software checks alone does not satisfy this model-architecture requirement.
