"""4-Call Qwen3 Thinking Diagnostic for local psychology QA.
Evaluates hypothesis: does reasoning (think: True) with bounded token budget (num_predict <= 4096, num_ctx <= 8192)
resolve token exhaustion and improve discriminative accuracy on Kahneman20068 and Lori28283?
Neutral rubric, serial GPU execution, full telemetry logging.
"""

import hashlib
import json
import logging
import os
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR_PATH = Path(__file__).resolve().parent.parent
if str(BASE_DIR_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_DIR_PATH))

import requests
from app.config import BASE_DIR, OLLAMA_BASE_URL
from app.curate_dataset_v7 import (
    compute_digest,
    compute_review_verdict_digest,
    get_ollama_model_digest,
    resolve_exact_source_span_offsets,
    segment_text_into_sentences,
    validate_and_parse_judge_verdict,
    SYSTEM_PROMPT_TRAIN,
    CALIBRATION_PROTOCOL_VERSION
)
from app.gpu_lock import gpu_coordinator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("qwen3_thinking_diagnostic")

DIAGNOSTIC_PROMPT_TEMPLATE = (
    "Bạn là Thẩm định viên Khoa học khắt khe tuyệt đối (Strict Fact-Checking Judge). "
    "Nhiệm vụ của bạn là kiểm tra xem CÂU TRẢ LỜI CẦN THẨM ĐỊNH có hoàn toàn được chứng minh 100% bởi các câu trong trích đoạn [S1] hay không.\n\n"
    "<context>\n<document id=\"S1\" book=\"{book}\" page=\"{page}\">\n{annotated_source}\n</document>\n</context>\n\n"
    "CÂU HỎI:\n{question}\n\n"
    "CÂU TRẢ LỜI CẦN THẨM ĐỊNH:\n{answer}\n\n"
    "QUY TẮC THẨM ĐỊNH:\n"
    "1. Đối chiếu từng câu, từng mệnh đề trong CÂU TRẢ LỜI với các câu nguồn có đánh mã [S1.1], [S1.2],... trong trích đoạn.\n"
    "2. Kiểm tra xem mọi khẳng định, suy luận, hoặc chi tiết trong câu trả lời có tiền đề hoặc căn cứ trực tiếp trong nguồn hay không. "
    "Nếu có bất kỳ khẳng định nào không được hỗ trợ bởi nguồn (hoặc ngoại suy quá mức ngoài văn bản), hãy ghi nhận vào unsupported_claims.\n"
    "3. Nếu toàn bộ mọi mệnh đề trong câu trả lời đều có căn cứ trực tiếp trong nguồn: đặt supported=true, answer_grounded=true, "
    "unsupported_claims=[], và trích các cụm từ nguyên văn chứng minh vào evidence_spans.\n"
    "4. Nếu có bất kỳ mệnh đề nào không đủ căn cứ: đặt supported=false, answer_grounded=false, và liệt kê các mệnh đề chưa có căn cứ vào unsupported_claims.\n\n"
    "Trả về định dạng JSON duy nhất:\n"
    "{{\n"
    "  \"supported\": true/false,\n"
    "  \"answer_grounded\": true/false,\n"
    "  \"unsupported_claims\": [],\n"
    "  \"evidence_spans\": [\"cụm từ nguyên văn trong nguồn\"],\n"
    "  \"reason\": \"giải thích ngắn gọn\"\n"
    "}}"
)


def run_diagnostic():
    model_name = "qwen3:4b"
    model_digest = get_ollama_model_digest(model_name)
    logger.info("Starting Qwen3 thinking diagnostic with model %s (digest: %s)", model_name, model_digest[:16])

    paired_controls_path = BASE_DIR / "data" / "evaluation" / "codex-pilot16-paired-controls-2026-10-02-0800.json"
    with open(paired_controls_path, "r", encoding="utf-8") as f:
        controls_data = json.load(f)

    # Select exactly Kahneman20068 (case 2) and Lori28283 (case 6)
    target_source_ids = {"src-5600-tu-duy--20068", "src-Lori Gottlie-28283"}
    selected_cases = [c for c in controls_data["cases"] if c["source_id"] in target_source_ids]
    # Sort deterministically
    selected_cases.sort(key=lambda c: c["source_id"])

    calls_plan = []
    for c in selected_cases:
        # Negative variant: original candidate
        calls_plan.append({
            "case_id": c["source_id"],
            "book": c["book"],
            "page": c["page"],
            "source_text": c["source_text"],
            "question": c["question"],
            "answer": c["original_candidate"]["answer"],
            "variant": "original_candidate_negative",
            "expected_grounded": False
        })
        # Positive variant: corrected control
        calls_plan.append({
            "case_id": c["source_id"],
            "book": c["book"],
            "page": c["page"],
            "source_text": c["source_text"],
            "question": c["question"],
            "answer": c["corrected_control"]["answer"],
            "variant": "corrected_control_positive",
            "expected_grounded": True
        })

    logger.info("Planned calls: %d", len(calls_plan))
    if len(calls_plan) != 4:
        raise ValueError(f"Expected exactly 4 calls, got {len(calls_plan)}")

    results = []
    pid = os.getpid()

    for idx, plan_item in enumerate(calls_plan, 1):
        logger.info("Executing diagnostic call %d/4: %s (%s)", idx, plan_item["case_id"], plan_item["variant"])

        source_text = plan_item["source_text"]
        question = plan_item["question"]
        answer = plan_item["answer"]
        book = plan_item["book"]
        page = plan_item["page"]

        # Segment source sentences and annotate
        source_sentences = segment_text_into_sentences(source_text, prefix="S1")
        annotated_chunks = []
        for s in source_sentences:
            annotated_chunks.append(f"[{s['id']}] {s['text']}")
        annotated_source = "\n\n".join(annotated_chunks)

        prompt = DIAGNOSTIC_PROMPT_TEMPLATE.format(
            book=book,
            page=page,
            annotated_source=annotated_source,
            question=question,
            answer=answer
        )
        prompt_tpl_hash = hashlib.sha256(DIAGNOSTIC_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

        src_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        q_hash = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
        ans_hash = hashlib.sha256(answer.strip().encode("utf-8")).hexdigest()

        # Build canonical message target hash
        doc_header = f'<document id="S1" book="{book}" page="{page}">\n{source_text}\n</document>'
        synthesized_msg = [
            {"role": "system", "content": SYSTEM_PROMPT_TRAIN},
            {"role": "user", "content": f"<context>\n{doc_header}\n</context>\n\nCâu hỏi: {question}\nHãy trả lời chi tiết bằng tiếng Việt, dẫn mã [S1]."},
            {"role": "assistant", "content": answer}
        ]
        target_hash = compute_digest(synthesized_msg)

        options = {
            "temperature": 0.0,
            "num_ctx": 8192,
            "num_predict": 4096
        }

        call_payload = {
            "model": model_name,
            "stream": False,
            "think": True,
            "format": "json",
            "options": options,
            "messages": [{"role": "user", "content": prompt}]
        }

        t0 = time.time()
        call_error = None
        done_reason = "error"
        prompt_eval_count = 0
        eval_count = 0
        raw_content = ""
        thinking_content = ""

        # Acquire lock and execute
        with gpu_coordinator.acquire_for_inference():
            try:
                resp = requests.post(f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat", json=call_payload, timeout=240)
                if resp.status_code == 200:
                    resp_json = resp.json()
                    done_reason = resp_json.get("done_reason", "stop")
                    prompt_eval_count = resp_json.get("prompt_eval_count", 0)
                    eval_count = resp_json.get("eval_count", 0)
                    msg_obj = resp_json.get("message", {})
                    raw_content = msg_obj.get("content", "").strip()
                    thinking_content = msg_obj.get("thinking", "") or ""
                else:
                    call_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as exc:
                call_error = str(exc)

        elapsed = round(time.time() - t0, 3)
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        max_rss_mb = round(rusage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)

        # Parse verdict fail-closed
        if call_error is not None:
            raw_payload = {
                "supported": False,
                "answer_grounded": False,
                "unsupported_claims": [call_error],
                "reason": f"Lỗi gọi mô hình: {call_error}",
                "evidence_spans": []
            }
        else:
            raw_payload = raw_content

        verdict = validate_and_parse_judge_verdict(
            raw_payload=raw_payload,
            source_text=source_text,
            question=question,
            target_answer=answer,
            judge_model=model_name,
            model_digest=model_digest,
            prompt_template_sha256=prompt_tpl_hash,
            target_sha256=target_hash,
            calibration_version=CALIBRATION_PROTOCOL_VERSION,
            require_vietnamese=True,
            require_citation=False,
            done_reason=done_reason
        )

        results.append({
            "call_index": idx,
            "case_id": plan_item["case_id"],
            "variant": plan_item["variant"],
            "expected_grounded": plan_item["expected_grounded"],
            "source_sha256": src_hash,
            "question_sha256": q_hash,
            "answer_sha256": ans_hash,
            "target_sha256": target_hash,
            "prompt_template_sha256": prompt_tpl_hash,
            "model_name": model_name,
            "model_digest": model_digest,
            "options": options,
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
            "done_reason": done_reason,
            "elapsed_seconds": elapsed,
            "max_rss_mb": max_rss_mb,
            "pid": pid,
            "call_error": call_error,
            "thinking_length_chars": len(thinking_content),
            "thinking_snippet": thinking_content[:500] + ("..." if len(thinking_content) > 500 else ""),
            "raw_response": raw_content,
            "verdict": verdict
        })

        logger.info(
            "Call %d/4 finished in %.2fs (eval_count=%d, done_reason=%s, supported=%s, answer_grounded=%s)",
            idx, elapsed, eval_count, done_reason, verdict["supported"], verdict["answer_grounded"]
        )

    output_payload = {
        "diagnostic_id": "qwen3-thinking-diagnostic-2026-10-02",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "model_digest": model_digest,
        "total_calls": len(results),
        "results": results,
        "summary": {
            "negative_cases": [r for r in results if not r["expected_grounded"]],
            "positive_cases": [r for r in results if r["expected_grounded"]],
        }
    }

    out_file = BASE_DIR / "data" / "evaluation" / "qwen3-thinking-diagnostic-2026-10-02.json"
    temp_file = out_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)
    temp_file.replace(out_file)

    logger.info("Diagnostic completed successfully. Results saved to %s", out_file)
    return output_payload


if __name__ == "__main__":
    run_diagnostic()
