"""Build and audit a candidate answer-only dataset without changing older versions."""

import argparse
import hashlib
import json
from pathlib import Path

import requests

from app.config import DEFAULT_CHAT_MODEL
from app.model_resolver import reject_retired_model
from app.bilingual_terms import translation_is_plausible
from app.training_audit import check_record
from app.training_data import basic_record_quality, write_training_files

ROOT = Path(__file__).resolve().parent.parent
TRAINING = ROOT / "data" / "training"

# These three cross-book answers were revised against both passages. The
# original automatically generated answers contained unsupported assertions.
CROSS_BOOK_ANSWERS = {
    "sang chấn": (
        "Hai đoạn nói gì về biểu hiện cơ thể sau sang chấn và giới hạn của thuốc đối với các biểu hiện ấy?",
        "Đoạn thứ hai mô tả phản ứng hậu sang chấn qua những biểu hiện của cơ thể như tim đập mạnh, "
        "thở nhanh và căng cứng [S2]. Đoạn thứ nhất cho rằng thuốc có thể làm dịu các biểu hiện "
        "sinh lý bị xáo trộn, nhưng không tự dạy kỹ năng tự điều chỉnh lâu dài [S1]. Hai đoạn "
        "cùng nhắc đến khía cạnh sinh lý của sang chấn: một đoạn mô tả biểu hiện, đoạn kia bàn về "
        "khả năng và giới hạn của thuốc. Chúng không cho biết phương pháp điều trị nào phù hợp cho "
        "một người cụ thể."
    ),
    "quyết định": (
        "Hai đoạn nêu những giới hạn nào khi giải thích lựa chọn của con người chỉ bằng giá trị tiền bạc?",
        "Đoạn thứ nhất nêu ví dụ máy đánh bạc: về dài hạn đây là lựa chọn bất lợi về tiền, "
        "nhưng nhiều người vẫn chơi; tác giả dùng ví dụ này để cho thấy con người không luôn tối đa "
        "hóa giá trị tiền mặt [S1]. Đoạn thứ hai nêu sự khác nhau trong thái độ với rủi ro khi "
        "đối diện khả năng được và mất, một điểm mà mô hình lựa chọn được bàn tới đã bỏ qua [S2]. "
        "Hai đoạn xét những bối cảnh khác nhau nhưng cùng cho thấy giá trị tiền bạc đơn lẻ "
        "không mô tả hết cách con người lựa chọn. Chúng không chứng minh vì sao một cá nhân cụ thể "
        "quyết định chơi máy đánh bạc."
    ),
    "chú ý": (
        "Trí nhớ làm việc và trí nhớ liên kết tham gia xử lý thông tin theo những cách khác nhau ra sao?",
        "Đoạn thứ nhất mô tả trí nhớ làm việc duy trì và thao tác thông tin để phục vụ suy nghĩ, "
        "giải quyết vấn đề, chú ý và ngôn ngữ [S1]. Đoạn thứ hai mô tả trí nhớ liên kết có thể "
        "tự động gợi ra lời giải thích nhân quả cho một sự kiện, kể cả khi lời giải thích ấy sai "
        "[S2]. Liên kết hai đoạn cho thấy xử lý thông tin có cả phần thao tác có mục đích và "
        "liên tưởng tự động; không nên coi một lời giải thích xuất hiện nhanh là bằng chứng nó đúng."
    ),
}

CROSS_BOOK_TRANSLATIONS = {
    "sang chấn": (
        "Một báo cáo nội bộ tháng 6 năm 2010 của Trung tâm Kinh tế Dược thuộc Bộ Quốc phòng Hoa Kỳ "
        "ghi nhận 213.972 người, tức 20% trong số 1,1 triệu quân nhân tại ngũ được khảo sát, "
        "đang dùng một loại thuốc tác động lên tâm thần, như thuốc chống trầm cảm, thuốc chống loạn thần "
        "hoặc thuốc an thần gây ngủ. Theo tác giả, thuốc không thể chữa khỏi sang chấn; chúng có thể "
        "làm dịu các biểu hiện sinh lý bị xáo trộn. Thuốc cũng không tự dạy kỹ năng tự điều chỉnh "
        "bền vững. Thuốc có thể giúp kiểm soát cảm xúc và hành vi, nhưng có những đánh đổi."
    ),
    "quyết định": (
        "Xét về tiền bạc, người ta có thể biết chi phí chơi và mức thưởng để nhận ra rằng máy đánh bạc "
        "gây thua lỗ về lâu dài. Vì thế, nếu chỉ xét lợi ích tiền mặt, chơi máy đánh bạc là bất lợi. "
        "Tuy nhiên, con người không nhất thiết hành động để tối đa hóa số tiền nhận được. Dù nhiều "
        "người biết sòng bạc thắng về lâu dài, sòng bạc vẫn thu hút rất đông khách. Quan sát ấy, "
        "cùng với kết quả nhiều thí nghiệm, khiến các nhà tâm lý học đặt câu hỏi về cách tiếp cận "
        "chỉ dựa trên giá trị tiền bạc khi giải thích quyết định."
    ),
    "chú ý": (
        "Trí nhớ ngắn hạn và trí nhớ làm việc. Trí nhớ làm việc không chỉ liên quan đến cách trí nhớ vận hành, "
        "mà còn đến cách thông tin được xử lý để phục vụ nhận thức, giải quyết vấn đề, suy nghĩ, chú ý "
        "và ngôn ngữ. Theo mô hình được nêu, ba thành phần tham gia thao tác thông tin là vòng lặp âm vị, "
        "bảng phác họa thị giác-không gian và bộ điều hành trung tâm. Vòng lặp âm vị giữ thông tin "
        "bằng lời nói và âm thanh, chẳng hạn khi cố nhớ một số điện thoại hoặc tên của một người."
    ),
}


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _source_key(record: dict) -> str:
    pair = record["pair"]
    data = [(s.get("filename", pair["filename"]), s["page"], s["text"]) for s in pair["sources"]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _held_out(records: list[dict]) -> set[str]:
    cross_files = {s.get("filename") for r in records if r["pair"].get("cross_book") for s in r["pair"]["sources"]}
    free = sorted({r["pair"]["filename"] for r in records if not r["pair"].get("cross_book")} - cross_files)
    english = [r["pair"]["filename"] for r in records if r.get("language") == "en" and r["pair"]["filename"] in free]
    chosen = set(english[:1])
    chosen.update(free[::max(1, len(free) // 4)][:4])
    return chosen


def prepare(output_dir: Path = TRAINING / "v4_candidate") -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Thư mục đã có dữ liệu: {output_dir}")
    records = []
    rejected = []
    seen = set()
    sources = [
        (TRAINING / "v2" / "approved_manifest.jsonl", False),
        (TRAINING / "v3_curated" / "approved_manifest.jsonl", False),
        (TRAINING / "v3" / "approved_manifest.jsonl", True),
    ]
    for path, cross_only in sources:
        for source_record in _read(path):
            record = json.loads(json.dumps(source_record, ensure_ascii=False))
            pair = record["pair"]
            if cross_only:
                if not pair.get("cross_book") or pair["title"] not in CROSS_BOOK_ANSWERS:
                    continue
                if pair["title"] == "chú ý" and not record["key"].startswith("cross:chú ý:E. Bruce Goldstein"):
                    continue
                question, answer = CROSS_BOOK_ANSWERS[pair["title"]]
                record["items"] = [{"question": question, "answer": answer}]
                record["teacher_model"] = "human-reviewed"
                record["translations"] = {"S1": CROSS_BOOK_TRANSLATIONS[pair["title"]]}
                if pair["title"] == "chú ý":
                    pair["sources"][1]["text"] = pair["sources"][1]["text"].replace(
                        "https://thuviensach.vn https://thuviensach.vn ", "")
            elif pair.get("cross_book"):
                continue
            key = _source_key(record)
            if key in seen:
                continue
            if not basic_record_quality(record):
                rejected.append({"key": record["key"], "reason": "basic_quality_or_translation"})
                continue
            if any(not translation_is_plausible(s["text"], record.get("translations", {}).get(s["id"], ""))
                   for s in pair["sources"] if s["id"] in record.get("translations", {})):
                rejected.append({"key": record["key"], "reason": "translation_terms"})
                continue
            seen.add(key)
            records.append(record)
    output_dir.mkdir(parents=True)
    (output_dir / "manifest.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8"
    )
    (output_dir / "selection.json").write_text(json.dumps({"rejected": rejected, "record_count": len(records),
        "source_datasets": [str(path) for path, _ in sources]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"records": len(records), "rejected": len(rejected), "output": str(output_dir)}


def audit(output_dir: Path = TRAINING / "v4_candidate", model: str = DEFAULT_CHAT_MODEL,
          second_model: str | None = None) -> dict:
    if not second_model or second_model == model:
        raise ValueError("Chưa cấu hình mô hình kiểm định độc lập cho quy trình này; không tái dùng kiểm định mô hình cũ.")
    reject_retired_model(model)
    reject_retired_model(second_model)
    records = _read(output_dir / "manifest.jsonl")
    audit_path = output_dir / "audit.jsonl"
    reviewed = {entry["key"]: entry for entry in _read(audit_path)} if audit_path.exists() else {}
    for index, record in enumerate(records, 1):
        key = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in reviewed:
            continue
        try:
            result = check_record(record, model=model)
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f"[{index}/{len(records)}] Chưa kiểm tra được: {error}", flush=True)
            continue
        entry = {"key": key, "record_key": record["key"], **result}
        with audit_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        reviewed[key] = entry
        print(f"[{index}/{len(records)}] Đã kiểm tra {record['pair']['filename'][:42]}", flush=True)
    secondary_path = output_dir / "audit_secondary.jsonl"
    secondary = {entry["key"]: entry for entry in _read(secondary_path)} if secondary_path.exists() else {}
    for index, record in enumerate(records, 1):
        key = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in secondary:
            continue
        try:
            result = check_record(record, model=second_model)
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f"[{index}/{len(records)}] Lượt hai chưa kiểm tra được: {error}", flush=True)
            continue
        entry = {"key": key, "record_key": record["key"], **result}
        with secondary_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        secondary[key] = entry
        print(f"[{index}/{len(records)}] Lượt hai {record['pair']['filename'][:42]}", flush=True)
    approved = []
    for record in records:
        key = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        result = reviewed.get(key)
        second = secondary.get(key)
        if not result or not second or not result["translation_ok"] or not second["translation_ok"] or not result.get("cross_source_link_ok", True) or not second.get("cross_source_link_ok", True):
            continue
        items = [item for item, first_decision, second_decision in zip(record["items"], result["decisions"], second["decisions"])
                 if first_decision["supported"] and second_decision["supported"]]
        if items:
            approved.append({**record, "items": items})
    (output_dir / "approved_manifest.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in approved), encoding="utf-8"
    )
    heldout = _held_out(approved)
    summary = write_training_files(approved, output_dir, model, plain_answer=True,
                                   augment_prompts=True, held_out_override=heldout)
    # A known bad pair links fading flashbulb memories to a trauma passage
    # that contains no claim about memories fading. If the judge accepts it,
    # its approvals cannot authorize a new fine-tune.
    bad_control = next((record for record in _read(TRAINING / "v3" / "approved_manifest.jsonl")
                        if record["key"].startswith("cross:trí nhớ:")), None)
    verifier_calibrated = False
    record_keys = {hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest() for record in records}
    if bad_control is not None and record_keys <= reviewed.keys() and record_keys <= secondary.keys():
        try:
            control_result = check_record(bad_control, model=model)
            second_control = check_record(bad_control, model=second_model)
            verifier_calibrated = not all(
                result["cross_source_link_ok"] and any(decision["supported"] for decision in result["decisions"])
                for result in (control_result, second_control)
            )
            (output_dir / "negative_control.json").write_text(
                json.dumps({"record_key": bad_control["key"], "primary": control_result,
                            "secondary": second_control,
                            "verifier_calibrated": verifier_calibrated}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError):
            pass
    summary.update({"audit_complete": record_keys <= reviewed.keys() and record_keys <= secondary.keys(),
                    "reviewed_records": len(record_keys & reviewed.keys()),
                    "secondary_reviewed_records": len(record_keys & secondary.keys()), "secondary_verifier_model": second_model,
                    "verifier_calibrated": verifier_calibrated,
                    "manifest_records": len(records), "approved_records": len(approved)})
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    approval_path = output_dir / "approval.json"
    if summary["audit_complete"] and verifier_calibrated and summary["train_cross_book_pairs"] >= 2 and summary["valid_examples"] >= 2:
        digest = hashlib.sha256((output_dir / "train.jsonl").read_bytes() + (output_dir / "valid.jsonl").read_bytes()).hexdigest()
        approval_path.write_text(json.dumps({"dataset_sha256": digest, "verifier_model": model,
            "approved_records": len(approved), "training_cross_book_pairs": summary["train_cross_book_pairs"]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        approval_path.unlink(missing_ok=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "audit"])
    parser.add_argument("--output", type=Path, default=TRAINING / "v4_candidate")
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output) if args.action == "prepare" else audit(args.output, args.model), ensure_ascii=False))


if __name__ == "__main__":
    main()
