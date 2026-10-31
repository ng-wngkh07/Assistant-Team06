"""Resumable, source-verified expansion with a frozen book-level holdout."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.config import BASE_DIR, DB_PATH, OLLAMA_BASE_URL, DEFAULT_CHAT_MODEL
from app.model_resolver import reject_retired_model
from app.merge_training import _source_is_current
from app.text_cleaner import sanitize_for_prompt_context
from app.training_audit import check_record
from app.training_data import (ask_teacher, basic_record_quality, detect_language,
                               is_substantive_excerpt, select_pairs, write_training_files)

FOCI = [
    "so sánh hai cách giải thích và điều kiện áp dụng",
    "phân biệt dữ kiện, suy luận và điều chưa thể kết luận",
    "áp dụng hai đoạn vào một tình huống mới, nêu rõ giả định",
    "phân biệt tương quan và nhân quả, tránh khẳng định quá mức",
]
THEMES = [
    ("trí nhớ", "memory", "trí nhớ"),
    ("sang chấn", "trauma", "sang chấn"),
    ("chú ý", "attention", "chú ý"),
    ("quyết định", "decision", "quyết định"),
    ("cảm xúc", "emotion", "cảm xúc"),
    ("nhân cách", "personality", "nhân cách"),
    ("học tập", "learning", "học"),
    ("gắn bó", "attachment", "gắn bó"),
    ("động lực", "motivation", "động lực"),
    ("căng thẳng", "stress", "căng thẳng"),
    ("thói quen", "habit", "thói quen"),
    ("nhận thức", "cognitive", "nhận thức"),
]


def read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_signature(pair):
    return digest([(s.get("filename", pair["filename"]), s["page"], s["text"]) for s in pair["sources"]])


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def book_partition(db, heldout):
    """Exclude every alias of a held-out PDF and choose one copy per hash."""
    docs = list(db.execute("SELECT filename,file_hash FROM documents WHERE status='indexed' ORDER BY filename"))
    heldout_hashes = {r[1] for r in docs if r[0] in heldout}
    aliases = {r[0] for r in docs if r[1] in heldout_hashes}
    seen = set()
    canonical = set()
    for name, file_hash in sorted(docs, key=lambda row: (len(row[0]), row[0])):
        if file_hash not in seen and name not in aliases:
            canonical.add(name)
        seen.add(file_hash)
    return canonical, aliases


def cross_pairs(db, allowed, per_theme=2):
    db.row_factory = sqlite3.Row
    for topic, english, vietnamese in THEMES:
        grouped = []
        for term, language in ((english, "en"), (vietnamese, "vi")):
            by_book = defaultdict(list)
            for row in db.execute("SELECT filename,book_title,page_num,text FROM chunks WHERE page_num>10 AND length(text)>=400 AND lower(text) LIKE ? ORDER BY filename,page_num,chunk_index", ("%" + term + "%",)):
                if row["filename"] not in allowed:
                    continue
                text = sanitize_for_prompt_context(row["text"][:650])
                if detect_language(text) == language and is_substantive_excerpt(text):
                    by_book[row["filename"]].append(row)
            choices = []
            for filename in sorted(by_book):
                rows = by_book[filename]
                for fraction in (0.35, 0.65):
                    choices.append(rows[min(len(rows)-1, int(len(rows)*fraction))])
            grouped.append(choices)
        if not all(grouped):
            continue
        for index in range(per_theme):
            first = grouped[0][index % len(grouped[0])]
            second = next((r for r in grouped[1][index:] + grouped[1][:index] if r["filename"] != first["filename"]), None)
            if second is None:
                continue
            sources = [{"id": f"S{number}", "filename": row["filename"], "book": row["book_title"], "page": row["page_num"], "text": sanitize_for_prompt_context(row["text"][:650])} for number, row in enumerate((first, second), 1)]
            yield {"filename": f"cross:v5:{topic}:{index}", "title": topic, "pair_index": index, "cross_book": True, "sources": sources}


def plan(previous, destination, db_path=DB_PATH, pairs_per_book=4, new_pairs=72):
    if destination.resolve() == previous.resolve():
        raise ValueError("Phải dùng thư mục dữ liệu mới")
    train = previous / "train.jsonl"
    valid = previous / "valid.jsonl"
    old_digest = hashlib.sha256(train.read_bytes()+valid.read_bytes()).hexdigest()
    approval = json.loads((previous/"approval.json").read_text())
    if approval.get("dataset_sha256") != old_digest:
        raise ValueError("Hash dữ liệu gốc không khớp approval")
    summary = json.loads((previous/"summary.json").read_text())
    old = read_records(previous/"approved_manifest.jsonl")
    with sqlite3.connect(db_path) as db:
        allowed, heldout = book_partition(db, set(summary["held_out_books"]))
        if any(not _source_is_current(db, r["pair"]) or not basic_record_quality(r) for r in old):
            raise ValueError("Dữ liệu gốc không còn đạt đối chiếu nguồn/bản dịch")
        counts = Counter(r["pair"]["filename"] for r in old if not r["pair"].get("cross_book"))
        singles = [p for p in select_pairs(db_path, pairs_per_book) if p["filename"] in allowed]
        singles.sort(key=lambda p: (p["pair_index"], counts[p["filename"]], p["filename"]))
        cross = list(cross_pairs(db, allowed))
    seen = {source_signature(r["pair"]) for r in old}
    candidates = []
    # Cover new books first while reserving a substantial cross-book budget.
    single_budget = max(1, new_pairs-len(cross))
    for pair in singles:
        signature = source_signature(pair)
        if signature not in seen and len(candidates) < single_budget:
            seen.add(signature)
            candidates.append(pair)
    for pair in cross:
        signature = source_signature(pair)
        if signature not in seen and len(candidates) < new_pairs:
            seen.add(signature)
            candidates.append(pair)
    destination.mkdir(parents=True, exist_ok=True)
    plan_path = destination / "plan.json"
    settings = {"previous_dataset_sha256": old_digest, "held_out_books": sorted(heldout), "allowed_training_books": sorted(allowed), "pairs_per_book": pairs_per_book, "new_pairs": new_pairs, "pairs": candidates}
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != settings:
            raise ValueError("Cấu hình/chỉ mục thay đổi; chọn bộ dữ liệu mới")
    elif any(destination.iterdir()):
        raise FileExistsError("Thư mục đã có dữ liệu không thuộc quy trình này")
    else:
        write_json(plan_path, settings)
        (destination/"manifest.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in old), encoding="utf-8")
    return settings


def prepare(destination, teacher=DEFAULT_CHAT_MODEL, teacher_fn=None):
    settings = json.loads((destination/"plan.json").read_text())
    records = read_records(destination/"manifest.jsonl")
    seen = {source_signature(r["pair"]) for r in records}
    for index, pair in enumerate(settings["pairs"], 1):
        signature = source_signature(pair)
        if signature in seen:
            continue
        try:
            for attempt in range(2):
                try:
                    generated = (teacher_fn or ask_teacher)(pair, model=teacher, reasoning_focus=FOCI[(index-1)%len(FOCI)])
                    break
                except (requests.RequestException, ValueError, KeyError):
                    if attempt:
                        raise
            record = {"key": "v5:"+signature, "pair": pair, "teacher_model": teacher, "translation_model": teacher, "reasoning_focus": FOCI[(index-1)%len(FOCI)], **generated}
            if not basic_record_quality(record):
                raise ValueError("Nguồn/bản dịch không đạt bộ lọc")
            with (destination/"manifest.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False)+"\n")
            seen.add(signature)
            print(f"prepare {index}/{len(settings['pairs'])}: {pair['filename'][:70]}", flush=True)
        except (requests.RequestException, ValueError, KeyError) as error:
            with (destination/"generation_errors.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps({"signature": signature, "error": str(error)}, ensure_ascii=False)+"\n")
            print(f"prepare rejected {index}: {error}", flush=True)
        write_json(destination/"pipeline_status.json", {"stage": "preparing", "updated_at": datetime.now(timezone.utc).isoformat(), "last_pair": index, "planned_pairs": len(settings["pairs"]), "records": len(read_records(destination/"manifest.jsonl"))})
    return {"records": len(read_records(destination/"manifest.jsonl")), "planned_new_pairs": len(settings["pairs"])}


def audit(destination, primary=DEFAULT_CHAT_MODEL, secondary=None):
    if not secondary or primary == secondary:
        raise ValueError("Cần hai mô hình kiểm tra khác nhau")
    reject_retired_model(primary)
    reject_retired_model(secondary)
    (destination/"approval.json").unlink(missing_ok=True)
    records = read_records(destination/"manifest.jsonl")
    decisions = []
    for model, name in ((primary, "primary"), (secondary, "secondary")):
        path = destination/f"audit_{name}.jsonl"
        reviewed = {entry["key"]: entry for entry in read_records(path) if entry.get("model") == model and entry.get("audit_version") == 1}
        for index, record in enumerate(records, 1):
            key = digest(record)
            if key in reviewed:
                continue
            try:
                result = check_record(record, model)
                entry = {"key": key, "record_key": record["key"], "model": model, "audit_version": 1, **result}
                with path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(entry, ensure_ascii=False)+"\n")
                reviewed[key] = entry
                print(f"audit {name} {index}/{len(records)}: {sum(d['supported'] for d in result['decisions'])}/{len(record['items'])}", flush=True)
            except (requests.RequestException, ValueError, KeyError) as error:
                print(f"audit pending {name} {index}: {error}", flush=True)
        decisions.append(reviewed)
    # A deliberately false answer must be rejected by both judges.
    control = json.loads(json.dumps(records[0]))
    control["items"] = [{"question": "Hai đoạn chứng minh điều gì về khả năng ghi nhớ?", "answer": "Hai đoạn chứng minh mọi người đều có trí nhớ hoàn hảo, không bao giờ quên hay mắc lỗi; hồi hải mã không có vai trò gì trong trí nhớ. [S1] [S2]"}]
    controls = [check_record(control, model) for model in (primary, secondary)]
    calibrated = all(not any(d["supported"] for d in result["decisions"]) for result in controls)
    hard_control = next(r for r in read_records(BASE_DIR/"data"/"training"/"v3"/"approved_manifest.jsonl") if r["key"].startswith("cross:trí nhớ:"))
    hard_results = [check_record(hard_control, model) for model in (primary, secondary)]
    calibrated = calibrated and not all(r["cross_source_link_ok"] and any(d["supported"] for d in r["decisions"]) for r in hard_results)
    positive = next(r for r in records if r["pair"].get("cross_book") and r.get("teacher_model") == "human-reviewed")
    positive_results = [check_record(positive, model) for model in (primary, secondary)]
    calibrated = calibrated and all(r["translation_ok"] and r["cross_source_link_ok"] and any(d["supported"] for d in r["decisions"]) for r in positive_results)
    write_json(destination/"negative_control.json", {"record": control, "models": [primary, secondary], "results": controls, "hard_control": hard_control, "hard_results": hard_results, "positive_control": positive, "positive_results": positive_results, "verifier_calibrated": calibrated})
    approved = []
    with sqlite3.connect(DB_PATH) as db:
        for record in records:
            first, second = (d.get(digest(record)) for d in decisions)
            translations_complete = all(len(record.get("translations", {}).get(s["id"], "").strip()) >= 0.65 * len(s["text"].strip()) for s in record["pair"]["sources"] if detect_language(s["text"]) == "en")
            if not first or not second or not basic_record_quality(record) or not translations_complete or not _source_is_current(db, record["pair"]):
                continue
            if not all(d["translation_ok"] and d["cross_source_link_ok"] for d in (first, second)):
                continue
            items = [item for item, a, b in zip(record["items"], first["decisions"], second["decisions"]) if a["supported"] and b["supported"]]
            if items:
                approved.append({**record, "items": items})
    settings = json.loads((destination/"plan.json").read_text())
    (destination/"approved_manifest.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in approved), encoding="utf-8")
    summary = write_training_files(approved, destination, primary, plain_answer=True, augment_prompts=True, held_out_override=set(settings["held_out_books"]))
    training_books = {s.get("filename", r["pair"]["filename"]) for r in approved for s in r["pair"]["sources"]} - set(settings["held_out_books"])
    complete = all(all(digest(r) in d for r in records) for d in decisions)
    summary.update({"audit_complete": complete, "verifier_calibrated": calibrated, "approved_records": len(approved), "manifest_records": len(records), "verifier_models": [primary, secondary], "train_books": sorted(training_books), "previous_dataset_sha256": settings["previous_dataset_sha256"], "approved_manifest_sha256": hashlib.sha256((destination/"approved_manifest.jsonl").read_bytes()).hexdigest(), "source_verified_against_index": True})
    # Expansion gates require actual approved data, not duplicated questions alone.
    summary["expansion_gate"] = summary["train_examples"] >= 216 and len(training_books) >= 22 and summary["train_cross_book_pairs"] >= 6 and summary["valid_examples"] >= 6
    write_json(destination/"summary.json", summary)
    if complete and calibrated and summary["expansion_gate"]:
        write_json(destination/"approval.json", {"dataset_sha256": hashlib.sha256((destination/"train.jsonl").read_bytes()+(destination/"valid.jsonl").read_bytes()).hexdigest(), "approved_manifest_sha256": summary["approved_manifest_sha256"], "method": "two-model-source-verified-expansion", "verifier_models": [primary, secondary], "audit_complete": True, "source_verified_against_index": True})
    return summary


def run(destination, previous, pairs_per_book=4, new_pairs=72, iterations=240, backend="ollama", skip_prepare=False):
    from app.fine_tune import train
    status_path = destination/"pipeline_status.json"
    destination.mkdir(parents=True, exist_ok=True)
    def status(stage, **kwargs):
        write_json(status_path, {"stage": stage, "updated_at": datetime.now(timezone.utc).isoformat(), **kwargs})
    # plan() must run before the status file makes this a nonempty directory.
    settings = plan(previous, destination, pairs_per_book=pairs_per_book, new_pairs=new_pairs)
    try:
        status("preparing", planned_pairs=len(settings["pairs"]))
        if skip_prepare:
            print("Using saved source-verified candidates; generation can be resumed if audit gates fail.", flush=True)
        elif backend == "mlx":
            tags = requests.get(OLLAMA_BASE_URL.rstrip('/')+"/api/ps", timeout=15).json()
            for model in tags.get("models", []):
                requests.post(OLLAMA_BASE_URL.rstrip('/')+"/api/generate", json={"model": model["name"], "keep_alive": 0}, timeout=60).raise_for_status()
            subprocess.run([str(BASE_DIR/".train-venv"/"bin"/"python"), "-u", "-m", "app.mlx_teacher", "--output", str(destination)], cwd=BASE_DIR, check=True)
        else:
            prepare(destination)
        status("auditing")
        summary = audit(destination)
        if not (destination/"approval.json").exists():
            raise ValueError("Dữ liệu chưa đạt đủ duyệt/độ phủ/liên sách; chưa được huấn luyện")
        status("training", summary=summary)
        tags = requests.get(OLLAMA_BASE_URL.rstrip('/')+"/api/ps", timeout=15).json()
        for model in tags.get("models", []):
            requests.post(OLLAMA_BASE_URL.rstrip('/')+"/api/generate", json={"model": model["name"], "keep_alive": 0}, timeout=60).raise_for_status()
        adapter = BASE_DIR/"data"/"adapters"/f"trial-11-v5-lr3e-5-iters{iterations}"
        train(destination, iterations=iterations, adapter_dir=adapter, learning_rate=0.00003, max_seq_length=2048)
        status("trained_pending_evaluation", adapter=str(adapter), summary=summary)
        return summary
    except Exception as error:
        status("failed", error=str(error))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "prepare", "audit", "run"])
    parser.add_argument("--output", type=Path, default=BASE_DIR/"data"/"training"/"v5")
    parser.add_argument("--previous", type=Path, default=BASE_DIR/"data"/"training"/"v4")
    parser.add_argument("--pairs-per-book", type=int, default=4)
    parser.add_argument("--new-pairs", type=int, default=72)
    parser.add_argument("--iters", type=int, default=240)
    parser.add_argument("--backend", choices=["ollama", "mlx"], default="ollama")
    parser.add_argument("--skip-prepare", action="store_true", help="Duyệt các mẫu đã lưu trước; có thể tiếp tục bổ sung nếu chưa đạt")
    args = parser.parse_args()
    if args.action == "plan":
        result = plan(args.previous, args.output, pairs_per_book=args.pairs_per_book, new_pairs=args.new_pairs)
        result = {**result, "pairs": len(result["pairs"])}
    elif args.action == "prepare":
        result = prepare(args.output)
    elif args.action == "audit":
        result = audit(args.output)
    else:
        result = run(args.output, args.previous, args.pairs_per_book, args.new_pairs, args.iters, args.backend, args.skip_prepare)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
