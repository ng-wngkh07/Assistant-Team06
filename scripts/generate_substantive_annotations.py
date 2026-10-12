"""Generate authoritative 36 substantive annotated contexts for training-yc188-data06-repair.

Produces:
data/training/v18_scoped_multitask_20261011/source_annotations.json
with 36 contexts (24 train, 12 valid) covering all 6 domains and 6 tasks.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.config import BASE_DIR, DB_PATH as CONFIG_DB_PATH

ROOT_DIR = Path(BASE_DIR).resolve()
DB_PATH = CONFIG_DB_PATH
OUTPUT_PATH = ROOT_DIR / "data/training/v18_scoped_multitask_20261011/source_annotations.json"

# In public/release clone, embedded private RAW_CONTEXT_DEFS is emptied.
# Reviewed definitions must be supplied from an explicit local path (env or CLI) or monkeypatched.
RAW_CONTEXT_DEFS: List[Dict[str, Any]] = []


def build_and_verify_annotations(definitions_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    if definitions_path is None:
        definitions_path = (
            os.getenv("ANNOTATION_DEFS_PATH")
            or os.getenv("MULTITASK_ANNOTATION_DEFS")
            or os.getenv("ANNOTATION_DEFINITIONS_PATH")
        )

    if definitions_path:
        defs_file = Path(definitions_path).resolve()
        if not defs_file.is_file():
            raise FileNotFoundError(f"Definitions file not found at: {defs_file}")
        specs = json.loads(defs_file.read_text(encoding="utf-8"))
    else:
        specs = RAW_CONTEXT_DEFS

    if not specs:
        raise ValueError(
            "No substantive annotation definitions provided. "
            "Supply an explicit local definitions path via CLI (--definitions), "
            "ANNOTATION_DEFS_PATH / MULTITASK_ANNOTATION_DEFS env vars, "
            "or set nonempty RAW_CONTEXT_DEFS. Fail-closed: cannot manufacture data."
        )

    conn = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
    c = conn.cursor()

    verified_records: List[Dict[str, Any]] = []
    answer_dist: Dict[str, int] = {}
    invalid_dist: Dict[str, int] = {}

    for idx, spec in enumerate(specs, 1):
        cid = spec["chunk_id"]
        doc_id = spec["doc_id"]
        prefix = spec["search_prefix"]
        quote = spec["evidence_quote"]

        c.execute("SELECT filename, page_num, text FROM chunks WHERE id = ? AND doc_id = ?", (cid, doc_id))
        row = c.fetchone()
        if not row:
            raise ValueError(f"[{idx}] Chunk {cid} with doc_id {doc_id} not found in database")
        filename, page_num, full_text = row

        c.execute("SELECT file_hash FROM documents WHERE id = ?", (doc_id,))
        doc_row = c.fetchone()
        file_hash = doc_row[0] if doc_row else ""

        real_s = full_text.find(prefix)
        if real_s == -1:
            raise ValueError(f"[{idx}] Prefix not found in chunk {cid}:\n{prefix[:100]}")
        real_e = real_s + len(prefix)
        passage = full_text[real_s:real_e]

        if quote not in passage:
            raise ValueError(f"[{idx}] Quote not in passage for context {spec['id_suffix']}")
        if len(quote) < 20:
            raise ValueError(f"[{idx}] Quote shorter than 20 chars ({len(quote)}): {quote}")

        # Strict check: mcq must have independently authored verifier_check
        if "verifier_check" not in spec["mcq"]:
            raise ValueError(f"[{idx}] mcq missing authored verifier_check for {spec['id_suffix']}")
        mcq_check = spec["mcq"]["verifier_check"]
        if not isinstance(mcq_check, dict):
            raise ValueError(f"[{idx}] mcq verifier_check must be a dict for {spec['id_suffix']}")
        for k in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved", "verified_answer"):
            if k not in mcq_check:
                raise ValueError(f"[{idx}] mcq verifier_check missing key {k} for {spec['id_suffix']}")
        for k in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved"):
            if not isinstance(mcq_check[k], bool):
                raise ValueError(f"[{idx}] mcq verifier_check[{k}] must be bool for {spec['id_suffix']}")
        if not all(mcq_check[k] is True for k in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved")):
            raise ValueError(f"[{idx}] mcq verifier_check must have all True booleans for {spec['id_suffix']}")
        if mcq_check["verified_answer"] != spec["mcq"]["answer"]:
            raise ValueError(f"[{idx}] mcq verifier_check verified_answer must match mcq answer for {spec['id_suffix']}")

        # Strict check: invalid_mcq must have independently authored verifier_check
        if "verifier_check" not in spec["invalid_mcq"]:
            raise ValueError(f"[{idx}] invalid_mcq missing authored verifier_check for {spec['id_suffix']}")
        inv_check = spec["invalid_mcq"]["verifier_check"]
        if not isinstance(inv_check, dict):
            raise ValueError(f"[{idx}] invalid_mcq verifier_check must be a dict for {spec['id_suffix']}")
        for k in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved", "verified_answer"):
            if k not in inv_check:
                raise ValueError(f"[{idx}] invalid_mcq verifier_check missing key {k} for {spec['id_suffix']}")
        for k in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved"):
            if not isinstance(inv_check[k], bool):
                raise ValueError(f"[{idx}] invalid_mcq verifier_check[{k}] must be bool for {spec['id_suffix']}")
        if all(inv_check[k] is True for k in ("supported", "explanation_supported", "unique_answer", "qualifiers_preserved")):
            raise ValueError(f"[{idx}] invalid_mcq verifier_check cannot have all True booleans for {spec['id_suffix']}")

        ans = spec["mcq"]["answer"]
        answer_dist[ans] = answer_dist.get(ans, 0) + 1
        inv_ans = spec["invalid_mcq"]["answer"]
        invalid_dist[inv_ans] = invalid_dist.get(inv_ans, 0) + 1

        rec = {
            "id_suffix": spec["id_suffix"],
            "domain": spec["domain"],
            "split": spec["split"],
            "doc_id": doc_id,
            "filename": filename,
            "file_hash": file_hash,
            "page_num": page_num,
            "chunk_id": cid,
            "span_start": real_s,
            "span_end": real_e,
            "passage": passage,
            "evidence_quote": quote,
            "grounded_q": spec["grounded_q"],
            "grounded_a": spec["grounded_a"],
            "insufficient_q": spec["insufficient_q"],
            "insufficient_a": spec["insufficient_a"],
            "mcq": spec["mcq"],
            "invalid_mcq": spec["invalid_mcq"],
            "dialog": spec["dialog"],
            "title": spec["title"],
            "summary": spec["summary"],
        }
        verified_records.append(rec)

    conn.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(verified_records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified and saved {len(verified_records)} substantive contexts to {OUTPUT_PATH}")
    print(f"MCQ Answer distribution: {answer_dist}")
    print(f"Invalid MCQ Answer distribution: {invalid_dist}")
    return verified_records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build and verify substantive annotations from reviewed definitions.")
    parser.add_argument("--definitions", "-d", type=str, default=None, help="Path to reviewed definitions JSON file")
    args = parser.parse_args()
    build_and_verify_annotations(definitions_path=args.definitions)
