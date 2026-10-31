# Retrieval, local distribution and future training

## What the current model does

The application combines the pretrained Qwen3.5 9B model with retrieval-augmented generation (RAG). The selected base has no accepted active fine-tuned adapter. Documents in the library supply answer evidence; adding a document does not itself train model weights.

Retrieval combines SQLite FTS5/BM25 keyword results and compatible dense embeddings through weighted reciprocal-rank fusion (RRF). Both legs have weight 0.5 and the RRF smoothing constant is 60. Ordinary retrieval defaults to six results after considering a larger candidate pool. Broad questions can use ten results, capped at twelve; bounded neighboring context and the final 8192-token prompt budget further affect what reaches the model. There is no learned cross-encoder reranker in this pipeline.

Retrieval `top_k=6`, RRF's constant60 and token-sampling top-k are different settings. Six retrieved passages are not six independent witnesses and do not imply a confidence level. Duplicates, contradictory editions and truncated conditions can occupy the available slots. Increasing K may improve evidence recall while adding irrelevant material and prefill cost. Measure full source coverage, version/duplicate errors, supported answers, refusals and latency on frozen questions before selecting a new value. The actual admitted context matters more than the requested K alone.

## Chunk boundaries and context

The current splitter targets 1200 characters with 200 characters of overlap, preferring paragraph, line, sentence and word boundaries. These are character counts, not token counts. The embedding tokenizer independently checks whether each entire chunk fits its configured limit; silent truncation is rejected.

Small chunks can isolate a useful fact but separate it from a definition, negation, theorem assumption or preceding example. Large chunks preserve more context but combine topics and may reduce ranking specificity or exceed the prompt budget. Neither size alone determines hallucination. For example, retrieving a theorem conclusion without its hypotheses can cause a wrong application even when every retrieved word is copied correctly. Bounded same-page neighboring passages help recover context and retain separate source identities; they still require relevance and citation checks.

Changing boundaries requires a new index and revalidation of source mappings. Replacing an embedding model requires rebuilding all incompatible vectors. Equal dimensions or a matching model name do not make different vector spaces interchangeable.

## Local distribution

Distribute three separately versioned components: application code, permitted model artifacts and a reviewed prebuilt knowledge pack. The pack contains approved originals, source passages and vectors bound to the exact embedding artifact. Its installer copies precomputed data and creates local keyword tables; recipients do not extract documents, run embedding inference or train the model to use that pack. Model installation remains a separate one-time requirement. See [knowledge packs](knowledge-packs.md) for the compatibility and installation contract.

Raw training examples, private databases and personal histories are not required for inference and should remain outside Git and public release assets. A public knowledge pack must contain only material permitted for redistribution. It deliberately includes readable source content for citations. Vectors also must not be treated as anonymized documents: published research demonstrates text recovery from embeddings in the studied settings, without establishing the same recovery rate for this application's BGE artifact. [Embedding inversion research](https://arxiv.org/abs/2310.06816).

Application bundles create a personal working library with a real writable source folder and a copy of the prepared retrieval data. New uploads are saved and processed there; restarting reuses this state rather than reseeding it. Adding a previously unseen document still requires extraction and compatible embeddings, which the upload flow performs locally. This is separate from training model parameters. Retaining an original makes later reviewed curation possible; it does not grant training permission or start a training job.

If a future adapter is accepted, either distribute its small adapter files with the exact compatible base or provide a separately validated merged model artifact. The first form requires both components and a compatible loader; the second is larger and packages the adapted weights together. Both can retain learned response behavior without distributing the original training dataset. Conversion and quantization require fresh quality checks. Neither form automatically eliminates memorization or disclosure risk.

## Answer access without distributing originals

A future operator-hosted RAG service could keep originals, passages, vectors and source mappings on an operator-controlled machine. Clients would send questions and receive permitted answers rather than downloading the library. This would change the current local application architecture: the existing API has no per-user authorization and exposes document listing, previews and original downloads. Hiding a download button or source title would not secure those routes.

Such a service would need authenticated access, authorization before retrieval, protected document routes, response and metadata policies, resource limits and tests for bulk extraction. Internal evidence mappings can remain available for audits even when a response uses a neutral source identifier. Answer content can still reveal sensitive facts or suggest a source's identity; hiding filenames does not guarantee confidentiality. The permitted audience and answer scope still require review. This is an architectural option, not a deployed service or permission to upload private sources.

Training a model and distributing only its weights is another option for offline use. It does not require shipping the original training examples at inference time, but it does not guarantee complete and precise recall of a library. Research on supervised fine-tuning found limitations in acquiring new factual knowledge in the studied closed-book QA setting; its results do not establish a particular accuracy for this application. [Fine-tuning and new factual knowledge](https://aclanthology.org/2024.emnlp-main.444/).

Weights are also not a confidentiality boundary: researchers extracted some training sequences from language models by querying them. This demonstrates a disclosure risk, not that every source can be recovered or that a particular recovery rate applies here. [Training-data extraction](https://arxiv.org/abs/2012.07805). Any proposed private-source adapter would need separate permission, preparation, quality and disclosure evaluation before training or distribution. No such adapter is currently accepted.

For public offline distribution, a reviewed, independently authored knowledge collection can avoid shipping the original files, while retaining explicit source and rights records privately where appropriate. Rewriting, summarizing or generating synthetic examples does not automatically remove sensitive information or establish redistribution permission. Report the resulting collection's actual coverage rather than calling it a complete copy of the original library. See [document distribution](data-distribution.md).

## Teacher-assisted learning

A stronger teacher can generate candidate questions and answer examples from permitted passages and an authored topic/objective/difficulty blueprint. This is a feasible training-data design, not current product behavior or a training authorization. RAFT studies training models to select relevant retrieved documents and cite source evidence despite distractors. Distilling Step-by-Step studies using teacher explanations as additional supervision for smaller task-specific models. Their benchmark results do not establish equivalent gains for this application's Vietnamese questions. [RAFT](https://arxiv.org/abs/2403.10131), [Distilling Step-by-Step](https://arxiv.org/abs/2305.02301).

Split documents and versions before generation; retain a genuinely untouched evaluation set. Review source identity, exact evidence, conditions, one correct alternative, ambiguity, duplication and personal data before admitting examples. Mix accepted original examples with accepted synthetic examples. A student's self-generated answer or a teacher's approval is not sufficient source proof. Compare the unchanged base plus RAG against any proposed adapter plus RAG on the same held-out tasks, including unsupported questions and disclosure checks.

A reviewed offline question/flashcard bank, authored templates and a few carefully selected examples are simpler alternatives before training. They can reduce repeated online generation while preserving source review. Fine-tuning does not add parameters or automatically improve decoded tokens per second; any reduction in prompt size or failed retries must be measured separately from model throughput.

Current execution remains local. Approval to send implementation code and nonsecret diagnostics to an external coding assistant does not authorize uploading private documents for teacher generation, starting training or activating an adapter. Those actions need their own permitted-data scope and acceptance gates.
