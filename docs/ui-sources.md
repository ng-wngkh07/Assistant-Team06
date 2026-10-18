# Citation presentation

Display citation identifiers in the same order and with the same source mapping returned by the backend. Show document title, page or reading section, the actual passage and any available source-quality warning. Let the learner open the original document when a valid local source link is available.

For chat, consume `citations` from `/api/chat` or the completed streaming response. Refer to the current API schema and `app/rag_agent.py` for optional fields. Do not invent missing OCR confidence or review states.

Retrieval score is a ranking value, not confidence in answer correctness. An exact quote proves membership in a source, not that every generated claim follows from it. Preserve unverified and unresolved states.

Treat source text as untrusted content. Escape rendered text and only use validated local document links. Never construct links from arbitrary absolute paths or external URL schemes supplied by a source.

For non-PDF documents, call extracted divisions sections rather than claiming they are original printed pages. An empty citation list must not produce a simulated source card.
