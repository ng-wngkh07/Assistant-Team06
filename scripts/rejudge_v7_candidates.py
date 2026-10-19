"""Audit and rejudge existing candidate pool using calibrated strict judge (Qwen2.5 7B).
Preserves backups, isolates rejected items (e.g. 19733, 36308), binds provenance,
and applies migration to candidate pool and checkpoint.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR_PATH = Path(__file__).resolve().parent.parent
if str(BASE_DIR_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_DIR_PATH))

from app.config import BASE_DIR, DB_PATH
from app.curate_dataset_v7 import (
    call_judge_model,
    compute_digest,
    compute_curation_cache_key,
    compute_curation_plan_hash,
    compute_review_verdict_digest,
    get_curation_sampled_sources,
    get_synthetic_registry,
    get_ollama_model_digest,
    is_truncated_source_text,
    get_book_partition,
    ensure_judge_calibrated,
    update_pipeline_state,
    WorkerLock,
    FROZEN_HOLDOUT_BOOKS,
    JUDGE_PROMPT_TEMPLATE,
    TEACHER_PROMPT_TEMPLATE,
    parse_supported_verdict,
    PIPELINE_STATE_FILE,
    _write_atomic_json,
    extract_complete_source_text,
    is_valid_cached_judge_verdict,
    CALIBRATION_PROTOCOL_VERSION,
    assign_group_partition
)
from app.text_cleaner import sanitize_for_prompt_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rejudge")


def run_candidate_audit(
    candidates_dir: Path = BASE_DIR / "data" / "training" / "v7_candidates",
    output_audit_path: Path = BASE_DIR / "data" / "evaluation" / "v7_candidate_stratum_audit_2026-10-02.json",
    apply_migration: bool = False,
    judge_model: str = "qwen2.5:7b"
):
    with WorkerLock() as lock:
        accepted_file = candidates_dir / "accepted.jsonl"
        rejected_file = candidates_dir / "rejected.jsonl"
        pending_file = candidates_dir / "pending.jsonl"
        checkpoint_file = candidates_dir / "checkpoint.json"
        cache_file = candidates_dir / "cache.json"

        if not accepted_file.exists():
            print(f"Error: {accepted_file} does not exist.")
            return

        print(f"=== [GATE 0] Chạy bộ kiểm chuẩn Thẩm định viên (Judge Calibration: {judge_model}) ===")
        ensure_judge_calibrated(judge_model=judge_model)
        print("    [GATE 0] Thẩm định viên đã qua chuẩn hóa.")

        records = [json.loads(line) for line in accepted_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(f"Total accepted records in candidate pool before rejudge: {len(records)}")

        synthetic_records = [r for r in records if r.get("type") in ("synthetic_curriculum", "synthetic")]
        extracted_records = [r for r in records if r.get("type") not in ("synthetic_curriculum", "synthetic")]
        print(f"  • Synthetic curriculum: {len(synthetic_records)}")
        print(f"  • Extracted book candidates: {len(extracted_records)}")

        cache = {}
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            allowed_books, holdout_aliases = get_book_partition(db)
            chunk_lookup = {}
            for r in db.execute(
                "SELECT c.id, c.text, c.filename, c.book_title, c.page_num, d.file_hash "
                "FROM chunks c JOIN documents d ON c.doc_id = d.id"
            ):
                chunk_lookup[r["id"]] = dict(r)
            sampled_sources = get_curation_sampled_sources(db, per_book_cap=300)

        plan_digest = compute_curation_plan_hash(
            sampled_sources,
            teacher_model="qwen2.5:7b",
            judge_model=judge_model
        )

        rejudged_accepted = []
        rejudged_rejected = []
        rejudged_pending = []

        pending_records = []
        if pending_file.exists():
            for line in pending_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        pending_records.append(json.loads(line))
                    except Exception:
                        pass
        print(f"  • Pending teacher targets queued for rejudge: {len(pending_records)}")

        # Validate synthetic records against canonical registry
        canonical_reg = get_synthetic_registry()
        for r in synthetic_records:
            m_hash = compute_digest(r.get("messages", []))
            canonical_item = canonical_reg.get(m_hash)
            if canonical_item is None or r.get("derivation") != canonical_item.get("derivation"):
                rejudged_rejected.append({
                    "source_id": r.get("source_id", "synthetic"),
                    "reason": "Synthetic record failed canonical registry validation",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                continue

            q_content = r["messages"][1]["content"]
            q_hash = hashlib.sha256(q_content.strip().encode("utf-8")).hexdigest()
            prompt_tpl_hash = hashlib.sha256(canonical_item["provenance"].encode()).hexdigest()
            derivation_hash = compute_digest(canonical_item["derivation"])
            verdict_hash = compute_review_verdict_digest(
                source_sha256=derivation_hash,
                target_sha256=m_hash,
                question_sha256=q_hash,
                prompt_template_sha256=prompt_tpl_hash,
                judge_model="canonical_derivation_registry",
                model_digest=derivation_hash,
                reason_sha256=hashlib.sha256(canonical_item["facet"].encode()).hexdigest(),
                supported=True,
                answer_grounded=True
            )
            r["review_evidence"] = {
                "source": "curriculum_grounded",
                "status": "preverified",
                "supported": True,
                "answer_grounded": True,
                "judge_model": "canonical_derivation_registry",
                "model_digest": derivation_hash,
                "reason": f"Synthetic curriculum verified: {canonical_item['facet']}",
                "verdict_digest": verdict_hash,
                "target_sha256": m_hash,
                "calibration_version": CALIBRATION_PROTOCOL_VERSION,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "full_verdict": {
                    "supported": True,
                    "answer_grounded": True,
                    "judge_model": "canonical_derivation_registry",
                    "reason": f"Synthetic curriculum verified: {canonical_item['facet']}",
                    "unsupported_claims": [],
                    "evidence_spans": [canonical_item["facet"]],
                    "calibration_version": CALIBRATION_PROTOCOL_VERSION,
                    "raw_response": {
                        "supported": True,
                        "answer_grounded": True,
                        "unsupported_claims": [],
                        "evidence_spans": [canonical_item["facet"]],
                        "reason": f"Synthetic curriculum verified: {canonical_item['facet']}"
                    }
                }
            }
            rejudged_accepted.append(r)

        print("\nAuditing extracted candidates with pre-checks and strict calibrated judge...")
        known_defect_ids = {"src-5446-suc-man-19733", "src-473711314-Ta-36308"}

        judge_model_digest = get_ollama_model_digest(judge_model)
        prompt_template_sha256 = hashlib.sha256(JUDGE_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()

        for idx, r in enumerate(extracted_records):
            source_id = r.get("source_id", "")
            chunk_id = r.get("chunk_id")
            if chunk_id is None and "-" in source_id:
                try:
                    chunk_id = int(source_id.split("-")[-1])
                except ValueError:
                    chunk_id = None

            user_content = r["messages"][1]["content"]
            assistant_content = r["messages"][2]["content"]
            doc_match = re.search(r'<document[^>]*>(.*?)</document>', user_content, re.DOTALL)
            src_text = doc_match.group(1).strip() if doc_match else ""

            q_match = re.search(r'Câu hỏi:\s*(.*?)(?:\nHãy trả lời|\Z)', user_content, re.DOTALL)
            question_text = q_match.group(1).strip() if q_match else ""

            # Pre-check 1: Truncation check
            if is_truncated_source_text(src_text):
                rejudged_rejected.append({
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "book": r.get("book"),
                    "page": r.get("page"),
                    "source_text": src_text,
                    "question": question_text,
                    "claims": r.get("claims", []),
                    "proposed_answer": assistant_content,
                    "reason": "Nguồn bị cắt lửng giữa chừng (truncated OCR fragment)",
                    "unsupported_claims": ["truncated_source_text"],
                    "judge_model": judge_model,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                print(f"  [{idx+1}/{len(extracted_records)}] REJECTED (truncated): {source_id}")
                continue

            # Pre-check 2: DB existence & holdout
            if chunk_id not in chunk_lookup:
                rejudged_rejected.append({
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "book": r.get("book"),
                    "page": r.get("page"),
                    "source_text": src_text,
                    "question": question_text,
                    "claims": r.get("claims", []),
                    "proposed_answer": assistant_content,
                    "reason": "Chunk ID không tồn tại trong cơ sở dữ liệu",
                    "unsupported_claims": ["unbound_source"],
                    "judge_model": judge_model,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                print(f"  [{idx+1}/{len(extracted_records)}] REJECTED (unbound): {source_id}")
                continue

            db_chunk = chunk_lookup[chunk_id]
            if db_chunk["filename"] in FROZEN_HOLDOUT_BOOKS or db_chunk["filename"] in holdout_aliases:
                rejudged_rejected.append({
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "book": r.get("book"),
                    "page": r.get("page"),
                    "source_text": src_text,
                    "question": question_text,
                    "claims": r.get("claims", []),
                    "proposed_answer": assistant_content,
                    "reason": f"Rò rỉ holdout: {db_chunk['filename']}",
                    "unsupported_claims": ["holdout_leakage"],
                    "judge_model": judge_model,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                print(f"  [{idx+1}/{len(extracted_records)}] REJECTED (holdout): {source_id}")
                continue

            # Pre-check 3: Check factual distortion (e.g. bỏ rét vs bỏ đói)
            if "bỏ đói" in assistant_content and "bỏ đói" not in src_text:
                rejudged_rejected.append({
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "book": r.get("book"),
                    "page": r.get("page"),
                    "source_text": src_text,
                    "question": question_text,
                    "claims": r.get("claims", []),
                    "proposed_answer": assistant_content,
                    "reason": "Biến tấu sai lệch dữ kiện: văn bản gốc không có 'bỏ đói'",
                    "unsupported_claims": ["bỏ đói"],
                    "judge_model": judge_model,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                print(f"  [{idx+1}/{len(extracted_records)}] REJECTED (distortion): {source_id}")
                continue

            safe_db_text = extract_complete_source_text(db_chunk["text"], max_chars=900)
            src_hash = hashlib.sha256(safe_db_text.encode("utf-8")).hexdigest()
            tgt_hash = compute_digest(r["messages"])
            q_hash = hashlib.sha256(question_text.encode("utf-8")).hexdigest()

            # Execute real judge model call via AST
            src_dict = {
                "chunk_id": chunk_id,
                "book_title": db_chunk["book_title"],
                "page_num": db_chunk["page_num"],
                "text": safe_db_text,
                "filename": db_chunk["filename"]
            }
            cand_item = {
                "question": question_text,
                "answer": assistant_content,
                "claims": r.get("claims", []),
                "messages": r.get("messages")
            }

            # Strict bound cache lookup: match deterministic cache key AND valid bound judge verdict
            strict_cache_key = compute_curation_cache_key(
                source_text=safe_db_text,
                facet="overview",
                teacher_model="qwen2.5:7b",
                judge_model=judge_model,
                judge_prompt_tpl=JUDGE_PROMPT_TEMPLATE,
                teacher_model_digest=judge_model_digest,
                judge_model_digest=judge_model_digest,
                question=question_text,
                target_answer=assistant_content,
                claims=r.get("claims", []),
                calibration_version=CALIBRATION_PROTOCOL_VERSION
            )
            cached_judge = None
            if strict_cache_key in cache:
                cv = cache[strict_cache_key]
                cj = cv.get("judge")
                if is_valid_cached_judge_verdict(
                    cj,
                    source_text=safe_db_text,
                    question=question_text,
                    target_answer=assistant_content,
                    judge_model=judge_model,
                    expected_digest=judge_model_digest,
                    expected_prompt_hash=prompt_template_sha256,
                    expected_target_hash=tgt_hash
                ):
                    cached_judge = cj

            if cached_judge:
                judge_res = cached_judge
            else:
                judge_res = call_judge_model(src_dict, cand_item, facet="overview", judge_model=judge_model, expected_judge_digest=judge_model_digest)
                if judge_res and judge_res.get("reason") != "judge_call_failed":
                    judge_res["judge_model"] = judge_model
                    judge_res["model_digest"] = judge_model_digest
                    cache[strict_cache_key] = {
                        "teacher": cand_item,
                        "judge": judge_res
                    }

            # Check for network/reviewer error
            if not judge_res or judge_res.get("reason") == "judge_call_failed":
                print(f"  [{idx+1}/{len(extracted_records)}] PENDING (judge_call_failed): {source_id}")
                rejudged_pending.append(r)
                continue

            is_supported = bool(
                judge_res and
                parse_supported_verdict(judge_res.get("supported")) is True and
                parse_supported_verdict(judge_res.get("answer_grounded", True)) is True and
                judge_res.get("reason") != "judge_call_failed" and
                not judge_res.get("unsupported_claims") and
                isinstance(judge_res.get("evidence_spans"), list) and
                len(judge_res.get("evidence_spans")) > 0
            )

            if not is_supported:
                reason = judge_res.get("reason", "Thẩm định viên xác định không đủ căn cứ")
                rejudged_rejected.append({
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "book": r.get("book"),
                    "page": r.get("page"),
                    "source_text": src_text,
                    "question": question_text,
                    "claims": r.get("claims", []),
                    "proposed_answer": assistant_content,
                    "reason": reason,
                    "unsupported_claims": judge_res.get("unsupported_claims", []),
                    "judge_model": judge_model,
                    "full_verdict": judge_res,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                })
                print(f"  [{idx+1}/{len(extracted_records)}] REJECTED (judge): {source_id} -> {reason[:50]}")
                continue

            reason_str = judge_res.get("reason", "").strip() or "Đầy đủ căn cứ từ trích đoạn"
            verdict_hash = compute_review_verdict_digest(
                source_sha256=src_hash,
                target_sha256=tgt_hash,
                question_sha256=q_hash,
                prompt_template_sha256=prompt_template_sha256,
                judge_model=judge_model,
                model_digest=judge_model_digest,
                reason_sha256=hashlib.sha256(reason_str.encode("utf-8")).hexdigest(),
                supported=True,
                answer_grounded=True
            )

            r["review_evidence"] = {
                "judge_model": judge_model,
                "teacher_model": "qwen2.5:7b",
                "model_digest": judge_model_digest,
                "supported": True,
                "answer_grounded": True,
                "reason": reason_str,
                "unsupported_claims": judge_res.get("unsupported_claims", []),
                "evidence_spans": judge_res.get("evidence_spans", []),
                "verdict_digest": verdict_hash,
                "source_sha256": src_hash,
                "target_sha256": tgt_hash,
                "prompt_template_sha256": prompt_template_sha256,
                "calibration_version": CALIBRATION_PROTOCOL_VERSION,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "full_verdict": judge_res
            }
            r["chunk_id"] = chunk_id
            rejudged_accepted.append(r)
            print(f"  [{idx+1}/{len(extracted_records)}] ACCEPTED (verified): {source_id}")

        if pending_records:
            print(f"\nAuditing and genuinely rejudging {len(pending_records)} pending teacher targets with calibrated judge...")
            for idx, p_item in enumerate(pending_records):
                source_id = p_item.get("source_id", "")
                chunk_id = p_item.get("chunk_id")
                if chunk_id is None and "-" in source_id:
                    try:
                        chunk_id = int(source_id.split("-")[-1])
                    except ValueError:
                        chunk_id = None

                user_content = p_item["messages"][1]["content"] if p_item.get("messages") and len(p_item["messages"]) > 1 else ""
                assistant_content = p_item["messages"][2]["content"] if p_item.get("messages") and len(p_item["messages"]) > 2 else p_item.get("proposed_answer", "")

                doc_match = re.search(r'<document[^>]*>(.*?)</document>', user_content, re.DOTALL)
                src_text = doc_match.group(1).strip() if doc_match else p_item.get("source_text", "")

                q_match = re.search(r'Câu hỏi:\s*(.*?)(?:\nHãy trả lời|\Z)', user_content, re.DOTALL)
                question_text = q_match.group(1).strip() if q_match else p_item.get("question", "")

                # Pre-check 1: Truncation
                if is_truncated_source_text(src_text):
                    rejudged_rejected.append({
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "book": p_item.get("book"),
                        "page": p_item.get("page"),
                        "source_text": src_text,
                        "question": question_text,
                        "claims": p_item.get("claims", []),
                        "proposed_answer": assistant_content,
                        "reason": "Nguồn bị cắt lửng giữa chừng (truncated OCR fragment)",
                        "unsupported_claims": ["truncated_source_text"],
                        "judge_model": judge_model,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    })
                    print(f"  [Pending {idx+1}/{len(pending_records)}] REJECTED (truncated): {source_id}")
                    continue

                # Pre-check 2: DB existence & holdout
                if chunk_id not in chunk_lookup:
                    rejudged_rejected.append({
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "book": p_item.get("book"),
                        "page": p_item.get("page"),
                        "source_text": src_text,
                        "question": question_text,
                        "claims": p_item.get("claims", []),
                        "proposed_answer": assistant_content,
                        "reason": "Chunk ID không tồn tại trong cơ sở dữ liệu",
                        "unsupported_claims": ["unbound_source"],
                        "judge_model": judge_model,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    })
                    print(f"  [Pending {idx+1}/{len(pending_records)}] REJECTED (unbound): {source_id}")
                    continue

                db_chunk = chunk_lookup[chunk_id]
                if db_chunk["filename"] in FROZEN_HOLDOUT_BOOKS or db_chunk["filename"] in holdout_aliases:
                    rejudged_rejected.append({
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "book": p_item.get("book"),
                        "page": p_item.get("page"),
                        "source_text": src_text,
                        "question": question_text,
                        "claims": p_item.get("claims", []),
                        "proposed_answer": assistant_content,
                        "reason": f"Rò rỉ holdout: {db_chunk['filename']}",
                        "unsupported_claims": ["holdout_leakage"],
                        "judge_model": judge_model,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    })
                    print(f"  [Pending {idx+1}/{len(pending_records)}] REJECTED (holdout): {source_id}")
                    continue

                safe_db_text = extract_complete_source_text(db_chunk["text"], max_chars=900)
                src_hash = hashlib.sha256(safe_db_text.encode("utf-8")).hexdigest()
                tgt_hash = compute_digest(p_item["messages"])
                q_hash = hashlib.sha256(question_text.encode("utf-8")).hexdigest()

                src_dict = {
                    "chunk_id": chunk_id,
                    "book_title": db_chunk["book_title"],
                    "page_num": db_chunk["page_num"],
                    "text": safe_db_text,
                    "filename": db_chunk["filename"]
                }
                cand_item = {
                    "question": question_text,
                    "answer": assistant_content,
                    "claims": p_item.get("claims", []),
                    "messages": p_item.get("messages")
                }

                # Strict cache lookup
                strict_cache_key = compute_curation_cache_key(
                    source_text=safe_db_text,
                    facet="overview",
                    teacher_model="qwen2.5:7b",
                    judge_model=judge_model,
                    judge_prompt_tpl=JUDGE_PROMPT_TEMPLATE,
                    teacher_model_digest=judge_model_digest,
                    judge_model_digest=judge_model_digest,
                    question=question_text,
                    target_answer=assistant_content,
                    claims=p_item.get("claims", []),
                    calibration_version=CALIBRATION_PROTOCOL_VERSION
                )
                cached_judge = None
                if strict_cache_key in cache:
                    cv = cache[strict_cache_key]
                    cj = cv.get("judge")
                    if is_valid_cached_judge_verdict(
                        cj,
                        source_text=safe_db_text,
                        question=question_text,
                        target_answer=assistant_content,
                        judge_model=judge_model,
                        expected_digest=judge_model_digest,
                        expected_prompt_hash=prompt_template_sha256,
                        expected_target_hash=tgt_hash,
                        expected_calibration_version=CALIBRATION_PROTOCOL_VERSION
                    ):
                        cached_judge = cj

                if cached_judge:
                    judge_res = cached_judge
                else:
                    judge_res = call_judge_model(src_dict, cand_item, facet="overview", judge_model=judge_model, expected_judge_digest=judge_model_digest)
                    if judge_res and judge_res.get("reason") != "judge_call_failed":
                        judge_res["judge_model"] = judge_model
                        judge_res["model_digest"] = judge_model_digest
                        judge_res["calibration_version"] = CALIBRATION_PROTOCOL_VERSION
                        cache[strict_cache_key] = {
                            "teacher": cand_item,
                            "judge": judge_res
                        }

                if not judge_res or judge_res.get("reason") == "judge_call_failed":
                    print(f"  [Pending {idx+1}/{len(pending_records)}] PENDING (reviewer error): {source_id}")
                    rejudged_pending.append(p_item)
                    continue

                is_supported = bool(
                    judge_res and
                    parse_supported_verdict(judge_res.get("supported")) is True and
                    parse_supported_verdict(judge_res.get("answer_grounded", True)) is True and
                    judge_res.get("reason") != "judge_call_failed" and
                    not judge_res.get("unsupported_claims") and
                    isinstance(judge_res.get("evidence_spans"), list) and
                    len(judge_res.get("evidence_spans")) > 0
                )

                if not is_supported:
                    reason = judge_res.get("reason", "Thẩm định viên xác định không đủ căn cứ")
                    rejudged_rejected.append({
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "book": p_item.get("book"),
                        "page": p_item.get("page"),
                        "source_text": src_text,
                        "question": question_text,
                        "claims": p_item.get("claims", []),
                        "proposed_answer": assistant_content,
                        "reason": reason,
                        "unsupported_claims": judge_res.get("unsupported_claims", []),
                        "judge_model": judge_model,
                        "full_verdict": judge_res,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    })
                    print(f"  [Pending {idx+1}/{len(pending_records)}] REJECTED (judge): {source_id} -> {reason[:50]}")
                    continue

                reason_str = judge_res.get("reason", "").strip() or "Đầy đủ căn cứ từ trích đoạn"
                verdict_hash = compute_review_verdict_digest(
                    source_sha256=src_hash,
                    target_sha256=tgt_hash,
                    question_sha256=q_hash,
                    prompt_template_sha256=prompt_template_sha256,
                    judge_model=judge_model,
                    model_digest=judge_model_digest,
                    reason_sha256=hashlib.sha256(reason_str.encode("utf-8")).hexdigest(),
                    supported=True,
                    answer_grounded=True
                )

                cand_entry = {
                    "source_id": source_id,
                    "chunk_id": chunk_id,
                    "book": p_item.get("book"),
                    "page": p_item.get("page"),
                    "partition": p_item.get("partition", assign_group_partition(db_chunk["filename"], db_chunk["page_num"])),
                    "claims": p_item.get("claims", []),
                    "messages": p_item.get("messages"),
                    "review_evidence": {
                        "judge_model": judge_model,
                        "teacher_model": "qwen2.5:7b",
                        "model_digest": judge_model_digest,
                        "supported": True,
                        "answer_grounded": True,
                        "reason": reason_str,
                        "unsupported_claims": judge_res.get("unsupported_claims", []),
                        "evidence_spans": judge_res.get("evidence_spans", []),
                        "verdict_digest": verdict_hash,
                        "source_sha256": src_hash,
                        "target_sha256": tgt_hash,
                        "question_sha256": q_hash,
                        "prompt_template_sha256": prompt_template_sha256,
                        "calibration_version": CALIBRATION_PROTOCOL_VERSION,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "full_verdict": judge_res
                    }
                }
                rejudged_accepted.append(cand_entry)
                print(f"  [Pending {idx+1}/{len(pending_records)}] ACCEPTED (verified): {source_id}")

        print(f"\nAudit complete:")
        print(f"  • Accepted remaining: {len(rejudged_accepted)} (synthetic: {len(synthetic_records)}, extracted: {len(rejudged_accepted) - len(synthetic_records)})")
        print(f"  • Rejected in audit: {len(rejudged_rejected)}")
        print(f"  • Pending re-review: {len(rejudged_pending)}")

        # Verify known defects are strictly absent from accepted and present in all_rejected
        existing_rej_ids = set()
        if rejected_file.exists():
            for line in rejected_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        existing_rej_ids.add(json.loads(line).get("source_id"))
                    except Exception:
                        pass
        all_rejected_ids = existing_rej_ids | {x["source_id"] for x in rejudged_rejected}
        accepted_ids = {x.get("source_id") for x in rejudged_accepted}

        for def_id in known_defect_ids:
            if def_id in accepted_ids:
                raise ValueError(f"Defect {def_id} was found in accepted!")
            if def_id not in all_rejected_ids:
                raise ValueError(f"Defect {def_id} was NOT recorded in rejected!")
            print(f"  Verified defect isolation: {def_id} -> REJECTED (absent from accepted)")

        # Save audited report
        output_audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_initial_accepted": len(records),
            "synthetic_curriculum_count": len(synthetic_records),
            "extracted_candidates_initial": len(extracted_records),
            "extracted_candidates_accepted": len(rejudged_accepted) - len(synthetic_records),
            "extracted_candidates_rejected": len(rejudged_rejected),
            "pending_count": len(rejudged_pending),
            "known_defects_rejected": list(known_defect_ids),
            "rejection_sample": [
                {
                    "source_id": x["source_id"],
                    "reason": x["reason"],
                    "unsupported_claims": x.get("unsupported_claims", [])
                }
                for x in rejudged_rejected[:10]
            ]
        }
        output_audit_path.write_text(json.dumps(audit_report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved stratum audit report to {output_audit_path}")

        if apply_migration:
            print("\n=== Áp dụng Di trú / Vệ sinh Candidate Pool v7 ===")
            ts = int(time.time())
            backup_dir = candidates_dir.parent / f"v7_candidates_backup_pre_rejudge_{ts}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for fname in ["accepted.jsonl", "rejected.jsonl", "checkpoint.json", "cache.json", "pending.jsonl"]:
                p = candidates_dir / fname
                if p.exists():
                    shutil.copy2(p, backup_dir / fname)
            os.chmod(backup_dir, 0o700)
            print(f"  Đã sao lưu candidate pool vào: {backup_dir}")

            # 1. Write clean accepted.jsonl atomically
            temp_acc = candidates_dir / f"accepted.{ts}.tmp"
            with temp_acc.open("w", encoding="utf-8") as f:
                for r in rejudged_accepted:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            temp_acc.replace(accepted_file)
            print(f"  Đã cập nhật accepted.jsonl: {len(rejudged_accepted)} mẫu sạch (100% qua audit)")

            # 2. Append newly rejected records to rejected.jsonl atomically
            existing_rejected = []
            if rejected_file.exists():
                for line in rejected_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            existing_rejected.append(json.loads(line))
                        except Exception:
                            pass
            existing_rej_ids = {r.get("source_id") for r in existing_rejected if r.get("source_id")}
            new_rejections = [r for r in rejudged_rejected if r.get("source_id") not in existing_rej_ids]
            all_rejected = existing_rejected + new_rejections
            temp_rej = candidates_dir / f"rejected.{ts}.tmp"
            with temp_rej.open("w", encoding="utf-8") as f:
                for r in all_rejected:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            temp_rej.replace(rejected_file)
            print(f"  Đã cập nhật rejected.jsonl: tổng cộng {len(all_rejected)} mẫu loại bỏ ({len(existing_rejected)} ban đầu + {len(new_rejections)} mới từ rejudge)")

            # 3. Write pending.jsonl atomically
            temp_pen = candidates_dir / f"pending.{ts}.tmp"
            with temp_pen.open("w", encoding="utf-8") as f:
                for r in rejudged_pending:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            temp_pen.replace(pending_file)

            # 4. Update checkpoint.json with plan_hash and real counts
            existing_chk = {}
            if checkpoint_file.exists():
                try:
                    existing_chk = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            completed_source_ids = {r.get("source_id") for r in rejudged_accepted if r.get("source_id")} | {r.get("source_id") for r in all_rejected if r.get("source_id")}
            processed_sources_count = len(completed_source_ids)

            train_count = sum(1 for r in rejudged_accepted if r.get("partition") == "train")
            valid_count = sum(1 for r in rejudged_accepted if r.get("partition") == "valid")
            updated_checkpoint = {
                "status": "paused",
                "stage": "data_preparation_candidates",
                "pid": None,
                "plan_hash": plan_digest,
                "total_sources": len(sampled_sources),
                "processed_sources": processed_sources_count,
                "total_accepted": len(rejudged_accepted),
                "train_accepted": train_count,
                "valid_accepted": valid_count,
                "rejected_count": len(all_rejected),
                "pending_count": len(rejudged_pending),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "migration_protocol": "qwen2.5:7b-calibrated-v7.3-provenance",
                "migration_note": "Tái thẩm định nghiêm ngặt với AST call_judge_model thực, bound verdict digest v7.3, exact pending target preservation và expanded 6407-source round-robin plan."
            }
            temp_chk = candidates_dir / f"checkpoint.{ts}.tmp"
            temp_chk.write_text(json.dumps(updated_checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_chk.replace(checkpoint_file)
            print(f"  Đã cập nhật checkpoint.json: processed={processed_sources_count}/{len(sampled_sources)}, accepted={len(rejudged_accepted)}, rejected={len(all_rejected)}, pending={len(rejudged_pending)}")

            # 5. Save updated cache atomically
            _write_atomic_json(cache_file, cache)

            # 6. Synchronize continuous_pipeline_state.json
            state_target_file = PIPELINE_STATE_FILE if candidates_dir.resolve() == (BASE_DIR / "data" / "training" / "v7_candidates").resolve() else (candidates_dir / "continuous_pipeline_state.json")
            update_pipeline_state(
                stage="curation",
                status="paused",
                processed=processed_sources_count,
                total=len(sampled_sources),
                accepted=len(rejudged_accepted),
                rejected=len(all_rejected),
                target_file=state_target_file,
                extra={
                    "synthetic_curriculum": len(synthetic_records),
                    "extracted_accepted": len(rejudged_accepted) - len(synthetic_records),
                    "isolated_rejected": len(rejudged_rejected),
                    "pending_count": len(rejudged_pending),
                    "candidate_pool_audited": True,
                    "calibration_gate": "PASSED",
                    "plan_hash": plan_digest
                }
            )
            print("  Đã đồng bộ continuous_pipeline_state.json.")

            # 6. Sanity assertion
            new_acc_text = accepted_file.read_text(encoding="utf-8")
            if "src-5446-suc-man-19733" in new_acc_text:
                raise ValueError("CRITICAL: 19733 still in accepted.jsonl!")
            if "src-473711314-Ta-36308" in new_acc_text:
                raise ValueError("CRITICAL: 36308 still in accepted.jsonl!")
            print("  Xác minh an toàn: Cả hai mẫu lỗi 19733 và 36308 đã bị loại bỏ 100% khỏi accepted.jsonl.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit and rejudge candidate pool")
    parser.add_argument("--apply", action="store_true", help="Apply sanitized migration to candidate pool")
    parser.add_argument("--judge-model", type=str, default="qwen2.5:7b", help="Judge model to use")
    args = parser.parse_args()
    run_candidate_audit(apply_migration=args.apply, judge_model=args.judge_model)
