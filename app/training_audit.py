"""Verify synthetic examples against local source passages before fine-tuning."""

import argparse
import hashlib
import json
from pathlib import Path


from app.config import OLLAMA_BASE_URL, DEFAULT_CHAT_MODEL, TRAINED_MODEL_NAME
from app.model_resolver import is_mlx_model
from app.training_data import (
    basic_record_quality,
    format_context,
    write_training_files,
)


def check_record(record: dict, model: str = DEFAULT_CHAT_MODEL) -> dict:
    answers = [
        {"question": item["question"], "answer": item["answer"]}
        for item in record["items"]
    ]
    prompt = (
        "Kiểm tra nghiêm ngặt tính có căn cứ của từng câu trả lời bên dưới. "
        "Đánh dấu supported=false nếu có bất kỳ khẳng định nào không được các đoạn nguồn "
        "hỗ trợ rõ ràng, gán nhầm kết quả nghiên cứu, hoặc trích dẫn nguồn sai. "
        "Cho phép kết luận suy luận nếu từng tiền đề có nguồn và bước nối hợp lý; bác bỏ "
        "kết luận khẳng định quá mức hoặc thiếu mắt xích chứng cứ. "
        "Sự hợp lý chung theo kiến thức ngoài nguồn không đủ. Với sách tiếng Anh, cũng "
        "kiểm tra bản dịch có giữ đúng ý gốc hay không. "
        "Nếu hai đoạn đến từ hai sách khác nhau, chỉ duyệt khi câu hỏi thực sự cần thông tin "
        "từ CẢ HAI đoạn và câu trả lời nêu quan hệ hợp lý giữa chúng; bác bỏ phép nối chỉ dựa "
        "trên một từ chung hoặc suy diễn nhân quả không có căn cứ. "
        f"Trả JSON gồm translation_ok và approved. approved phải có đúng {len(answers)} "
        "giá trị boolean, mỗi giá trị ứng với một câu trả lời theo đúng thứ tự. "
        "Trả thêm cross_source_link_ok là boolean.\n\n"
        + format_context(record["pair"], record.get("translations"))
        + "\n\nCÁC CÂU TRẢ LỜI:\n"
        + json.dumps(answers, ensure_ascii=False)
    )
    import sys
    if sys.platform == "darwin":
        if not is_mlx_model(model):
            raise RuntimeError(f"Mô hình {model} không được hỗ trợ trên macOS (chỉ hỗ trợ MLX)")
        from app.trained_client import TrainedModelClient
        client = TrainedModelClient(model_name=model)
        if not client.available():
            raise RuntimeError(f"Mô hình {model} chưa sẵn sàng cho audit")
        stream = client.chat_stream([{"role": "user", "content": prompt}], max_tokens=250)
        content_text = "".join(stream).strip()
    elif is_mlx_model(model):
        from app.trained_client import TrainedModelClient
        client = TrainedModelClient(model_name=model)
        if not client.available():
            raise RuntimeError(f"Mô hình {model} chưa sẵn sàng cho audit")
        stream = client.chat_stream([{"role": "user", "content": prompt}], max_tokens=250)
        content_text = "".join(stream).strip()
    else:
        from app.ollama_client import OllamaClient
        client = OllamaClient()
        client._verify_model_locality(model)
        resp = client.chat_sync(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=0,
            num_predict=250,
            num_ctx=8192,
        )
        content_text = resp.get("message", {}).get("content", "")
    import re
    m = re.search(r"\{.*\}", content_text, re.DOTALL)
    if not m:
        raise ValueError(f"Không nhận được JSON hợp lệ từ audit model: {content_text}")
    answer = json.loads(m.group(0))
    decisions = answer.get("decisions", [])
    if isinstance(answer.get("approved"), list):
        decisions = [{"supported": value} for value in answer["approved"]]
    if len(decisions) != len(record["items"]):
        raise ValueError(f"Số quyết định kiểm tra không khớp số câu trả lời: {answer!r}")
    translation_ok = answer.get("translation_ok") is True if record["language"] in ("en", "mixed") else True
    cross_source_link_ok = answer.get("cross_source_link_ok") is True if record["pair"].get("cross_book") else True
    return {
        "translation_ok": translation_ok is True,
        "cross_source_link_ok": cross_source_link_ok is True,
        "decisions": [
            {"supported": item.get("supported") is True, "reason": str(item.get("reason", ""))[:400]}
            for item in decisions
        ],
    }


def audit(data_dir: Path, model: str = DEFAULT_CHAT_MODEL) -> dict:
    previous_summary = json.loads((data_dir / "summary.json").read_text(encoding="utf-8")) if (data_dir / "summary.json").exists() else {}
    manifest = [json.loads(line) for line in (data_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    audit_path = data_dir / "audit.jsonl"
    existing = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line] if audit_path.exists() else []
    by_key = {entry["key"]: entry for entry in existing}
    for number, record in enumerate(manifest, 1):
        key = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in by_key:
            continue
        try:
            result = check_record(record, model=model)
            entry = {"key": key, "record_key": record["key"], **result}
            with audit_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            by_key[key] = entry
            approved = sum(x["supported"] for x in result["decisions"])
            print(f'[{number}/{len(manifest)}] {record["pair"]["filename"][:36]}: {approved}/{len(record["items"])} đạt', flush=True)
        except (Exception, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f'[{number}/{len(manifest)}] Chưa kiểm tra được {record["pair"]["filename"][:36]}: {error}', flush=True)
    approved_records = []
    rejected_basic_quality_records = 0
    for record in manifest:
        key = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        result = by_key.get(key)
        if not result or not result["translation_ok"] or not result.get("cross_source_link_ok", True):
            continue
        if not basic_record_quality(record):
            rejected_basic_quality_records += 1
            continue
        kept = [item for item, decision in zip(record["items"], result["decisions"]) if decision["supported"]]
        if kept or record["language"] in ("en", "mixed"):
            approved_records.append({**record, "items": kept})
    approved_path = data_dir / "approved_manifest.jsonl"
    approved_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in approved_records),
        encoding="utf-8",
    )
    summary = write_training_files(approved_records, data_dir, previous_summary.get("teacher_model", "unknown"))
    summary["approved_analysis_examples"] = sum(len(record["items"]) for record in approved_records)
    summary["rejected_basic_quality_records"] = rejected_basic_quality_records
    reviewed_records = sum(
        hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest() in by_key
        for record in manifest
    )
    summary["reviewed_records"] = reviewed_records
    summary["manifest_records"] = len(manifest)
    summary["audit_complete"] = reviewed_records == len(manifest)
    summary["verifier_model"] = model
    (data_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = hashlib.sha256((data_dir / "train.jsonl").read_bytes() + (data_dir / "valid.jsonl").read_bytes()).hexdigest()
    approval_path = data_dir / "approval.json"
    if summary["audit_complete"]:
        approval_path.write_text(
            json.dumps({"dataset_sha256": digest, "verifier_model": model, "approved_analysis_examples": summary["approved_analysis_examples"], "manifest_records": len(manifest)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        approval_path.unlink(missing_ok=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Kiểm tra câu trả lời mẫu bằng mô hình cục bộ")
    parser.add_argument("--data", type=Path, default=Path("data/training/v1"))
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    args = parser.parse_args()
    print(json.dumps(audit(args.data, args.model), ensure_ascii=False))


if __name__ == "__main__":
    main()
