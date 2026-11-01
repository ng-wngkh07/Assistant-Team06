"""Compare held-out analytical answers from the base or local LoRA model."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from mlx_lm import generate, load

from app.config import BASE_DIR, MLX_ADAPTER_DIR, MLX_MODEL_DIR
from app.evaluation_metrics import citation_metrics, plain_answer_metrics


def evaluate(data_dir: Path, use_adapter: bool, max_cases: int = 3,
             adapter_dir: Path | None = None, case_indices: list[int] | None = None,
             output_path: Path | None = None) -> dict:
    dataset_digest = hashlib.sha256(
        (data_dir / "train.jsonl").read_bytes() + (data_dir / "valid.jsonl").read_bytes()
    ).hexdigest()
    approval = json.loads((data_dir / "approval.json").read_text(encoding="utf-8"))
    if approval.get("dataset_sha256") != dataset_digest:
        raise ValueError("Dữ liệu đánh giá không khớp bản đã duyệt")
    summary_path = data_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    plain_answer = summary.get("plain_answer") is True
    selected_adapter = adapter_dir or MLX_ADAPTER_DIR
    if not selected_adapter.is_absolute():
        selected_adapter = BASE_DIR / selected_adapter
    adapter = str(selected_adapter) if use_adapter or adapter_dir else None
    model, tokenizer = load(str(MLX_MODEL_DIR), adapter_path=adapter)
    all_cases = [json.loads(line) for line in (data_dir / "valid.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if case_indices is None:
        selected = [(index, case) for index, case in enumerate(all_cases)
                    if "Câu hỏi:" in case["messages"][-2]["content"]][:max_cases]
    else:
        if len(set(case_indices)) != len(case_indices) or any(index < 0 or index >= len(all_cases) for index in case_indices):
            raise ValueError("Chỉ số câu hỏi đánh giá không hợp lệ")
        selected = [(index, all_cases[index]) for index in case_indices]
        if any("Câu hỏi:" not in case["messages"][-2]["content"] for _, case in selected):
            raise ValueError("Danh sách đánh giá chứa một mẫu dịch thay vì câu hỏi")
    cases = selected
    if not cases:
        raise ValueError("Không có câu hỏi phân tích giữ lại để đánh giá")
    results = []
    for valid_index, case in cases:
        prompt = tokenizer.apply_chat_template(
            case["messages"][:-1], tokenize=False, add_generation_prompt=True
        )
        answer = generate(model, tokenizer, prompt=prompt, max_tokens=700)
        source_ids = set(re.findall(r'<document id="(S\d+)"', case["messages"][-2]["content"]))
        results.append({
            "valid_index": valid_index,
            "dataset_sha256": dataset_digest,
            "question": case["messages"][-2]["content"].split("Câu hỏi:")[-1].split("\n")[0],
            "reference": case["messages"][-1]["content"],
            "answer": answer,
            **(plain_answer_metrics(answer) if plain_answer else citation_metrics(answer, source_ids)),
        })
    output = BASE_DIR / "data" / "evaluation"
    output.mkdir(parents=True, exist_ok=True)
    if hashlib.sha256((data_dir / "train.jsonl").read_bytes() +
                      (data_dir / "valid.jsonl").read_bytes()).hexdigest() != dataset_digest:
        raise RuntimeError("Dữ liệu đánh giá thay đổi trong lúc sinh câu trả lời")
    name = ("trained" if selected_adapter == MLX_ADAPTER_DIR else selected_adapter.name) if adapter else "base"
    target = output_path or output / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "model": name,
        "cases": len(results),
        "dataset_sha256": dataset_digest,
        "answer_format": "plain" if plain_answer else "cited",
        "format_valid": sum(result.get("format_valid", False) for result in results) if plain_answer else None,
        "valid_citations": None if plain_answer else sum(result["citation_valid"] for result in results),
        "valid_citation_tags": None if plain_answer else sum(result["citation_tags_valid"] for result in results),
        "output": str(target),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=BASE_DIR / "data" / "training" / "v1")
    parser.add_argument("--adapter", action="store_true")
    parser.add_argument("--adapter-dir", type=Path, help="Đánh giá một adapter thử nghiệm riêng")
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--case-indices", type=int, nargs="+", help="Chỉ số dòng valid.jsonl, bắt đầu từ 0")
    parser.add_argument("--output", type=Path, help="File JSON kết quả; tránh ghi đè đánh giá cũ")
    args = parser.parse_args()
    print(json.dumps(evaluate(args.data, args.adapter, args.max_cases, args.adapter_dir,
                              args.case_indices, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
