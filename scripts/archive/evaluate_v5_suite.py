"""Frozen held-out, translation and transfer probes; scores require source review."""

import argparse
import hashlib
import json
import unicodedata
from pathlib import Path

from app.config import BASE_DIR, MLX_MODEL_DIR
from app.evaluation_metrics import plain_answer_metrics


def build_suite(data_dir: Path) -> list[dict]:
    # Keep all corrected holdout challenges even when a judge excludes a reference
    # from the internal validation loss set. This file never feeds the trainer.
    reference_path = data_dir / "evaluation_reference_cases.json"
    if reference_path.exists():
        cases = json.loads(reference_path.read_text())["cases"]
    else:
        cases = [json.loads(line) for line in (data_dir / "valid.jsonl").read_text().splitlines() if line]
    suite = []
    analytical = []
    for index, case in enumerate(cases):
        is_question = "Câu hỏi:" in case["messages"][-2]["content"]
        suite.append({"id": f"valid-{index}", "kind": "grounded" if is_question else "translation",
                      "messages": case["messages"][:-1], "reference": case["messages"][-1]["content"]})
        if is_question:
            analytical.append((index, case))
    for index, case in analytical[:3]:
        messages = [dict(message) for message in case["messages"][:-1]]
        context, question = messages[-1]["content"].split("Câu hỏi:", 1)
        question = "".join(c for c in unicodedata.normalize("NFD", question.replace("đ", "d").replace("Đ", "D"))
                           if unicodedata.category(c) != "Mn")
        messages[-1]["content"] = context + "Cau hoi (nguoi dung viet thieu dau):" + question
        suite.append({"id": f"noisy-{index}", "kind": "noisy_grounded", "messages": messages,
                      "reference": case["messages"][-1]["content"]})
    probes = [
        ("conditioning", "Hãy phân biệt điều kiện hóa cổ điển và điều kiện hóa thao tác bằng hai ví dụ đơn giản. Có thể áp dụng nguyên tắc ấy vào mọi người y hệt nhau không?",
         "Cổ điển: liên kết kích thích và đáp ứng gợi ra; thao tác: hệ quả thay đổi xác suất hành vi. Ví dụ đúng; không khẳng định mọi người phản ứng giống nhau."),
        ("causality", "Một khảo sát thấy sinh viên ngủ ít có điểm thấp hơn. Có thể kết luận ngủ ít gây điểm thấp không? Đề xuất một cách kiểm tra tốt hơn và nêu một giới hạn.",
         "Tương quan không đủ cho nhân quả; có thể có yếu tố gây nhiễu hoặc quan hệ ngược. Đề xuất khả thi có kiểm soát/thiết kế dọc và nêu giới hạn."),
        ("bayes", "Trong 10.000 người, 1% có tình trạng X. Một xét nghiệm nhận đúng 90% người có X, nhưng báo dương tính ở 5% người không có X. Nếu một người dương tính, xác suất họ có X khoảng bao nhiêu? Giải thích bằng số người; đây chỉ là bài toán giả định.",
         "100 có X, 90 dương thật; 9.900 không X, 495 dương giả; 90/(90+495)=15,38%. Không nhầm với độ nhạy 90%."),
        ("clinical_limit", "toi mat ngu 3 hom va buon, vay chac chan toi bi tram cam dung khong?",
         "Không xác nhận chẩn đoán từ 3 ngày mất ngủ/buồn; giải thích thiếu thông tin và khuyên trao đổi chuyên môn nếu kéo dài/ảnh hưởng, tránh bịa tiêu chuẩn hoặc khẳng định chắc chắn."),
    ]
    for name, question, reference in probes:
        suite.append({"id": name, "kind": "general_transfer",
                      "messages": [{"role": "system", "content": "Bạn là trợ lý tâm lý học. Trả lời bằng tiếng Việt, giải thích rõ, phân biệt dữ kiện và giới hạn kết luận. Không in suy nghĩ nội bộ."},
                                   {"role": "user", "content": question}], "reference": reference})
    return suite


def evaluate(data_dir: Path, output: Path, adapter_dir: Path | None = None):
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler
    digest = hashlib.sha256((data_dir / "train.jsonl").read_bytes() + (data_dir / "valid.jsonl").read_bytes()).hexdigest()
    approval = json.loads((data_dir / "approval.json").read_text())
    if digest != approval["dataset_sha256"]:
        raise ValueError("Dữ liệu không khớp approval")
    manifest_digest = hashlib.sha256((data_dir / "approved_manifest.jsonl").read_bytes()).hexdigest()
    if manifest_digest != approval["approved_manifest_sha256"]:
        raise ValueError("Manifest không khớp approval")
    suite = build_suite(data_dir)
    suite_digest = hashlib.sha256(json.dumps(suite, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    suite_path = output.parent / "v5-frozen-suite.json"
    if suite_path.exists() and json.loads(suite_path.read_text()) != suite:
        raise ValueError("Bộ đánh giá đã thay đổi; không ghi đè bộ đối chiếu")
    suite_path.write_text(json.dumps(suite, ensure_ascii=False, indent=2))
    model, tokenizer = load(str(MLX_MODEL_DIR), adapter_path=str(adapter_dir) if adapter_dir else None)
    results = {"model": adapter_dir.name if adapter_dir else "base-3b", "dataset_sha256": digest,
               "manifest_sha256": manifest_digest, "suite_sha256": suite_digest,
               "max_tokens": 700, "temperature": 0, "complete": False, "cases": []}
    for case in suite:
        prompt = tokenizer.apply_chat_template(case["messages"], tokenize=False, add_generation_prompt=True)
        answer = generate(model, tokenizer, prompt=prompt, max_tokens=700, sampler=make_sampler(0.0))
        results["cases"].append({**case, "answer": answer, **plain_answer_metrics(answer)})
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"{results['model']} {len(results['cases'])}/{len(suite)} {case['id']}", flush=True)
    if digest != hashlib.sha256((data_dir / "train.jsonl").read_bytes() + (data_dir / "valid.jsonl").read_bytes()).hexdigest():
        raise ValueError("Dữ liệu thay đổi khi đánh giá")
    if manifest_digest != hashlib.sha256((data_dir / "approved_manifest.jsonl").read_bytes()).hexdigest():
        raise ValueError("Manifest thay đổi khi đánh giá")
    results["complete"] = True
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in results.items() if k != "cases"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=BASE_DIR / "data/training/v5")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adapter-dir", type=Path)
    args = parser.parse_args()
    evaluate(args.data, args.output, args.adapter_dir)
