"""Create the reviewed v2 dataset without changing the trained v1 adapter."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from app.config import BASE_DIR
from app.training_data import basic_record_quality, write_training_files


SOURCE_SHA256 = "a3fa556435e50b26ea88e0c75248ceb4a024eda462658626630aad559d80f177"

# These suffixes identify exact v1 records. Reasons are retained in curation.json.
EXCLUDED = {
    "9aa8e062d7e2ad9f": "S2 mixes two clinical cases; translation contains 'Tasted' and a wrong nerve term.",
    "07431c28bf584296": "Promotional review text rather than a substantive psychology passage.",
    "92ab2c80ee6b18ea": "Young adulthood was mistranslated as middle adulthood in question and translation.",
    "26d8db9dff4378db": "Translation mixes languages and malformed personality terms.",
    "4ccbd3dd022966fe": "ACE answer asserts an unsupported causal link and the source has malformed OCR.",
    "a321c5741dc71f97": "Ischemic brain infarct was translated and taught as myocardial infarction.",
    "a4bc8c3ccf65ad6e": "Answer confuses smell with taste and attributes S2 facts to S1.",
    "ac142c4820e836a7": "Answer reverses S1 and S2; passages are instructions and glossary fragments.",
    "3c75c3f5e29bbf4e": "Promotional review text rather than a substantive psychology passage.",
    "67de1da935a73934": "PTSD answer invents a reason for spending differences and misreads a Vietnam veteran as a country comparison.",
    "e30d8ccd572b6b5b": "Placebo answer contradicts S1, which says sham surgery produced similar pain relief.",
    "b55db1a1fc2226fd": "OCR joins a clinical story to a memory table; the answer implies an unsupported causal connection.",
    "10feefa949617066": "Golf answer contradicts S1: the fairness objection disappears if everyone can use a cart.",
    "2cc80e25fbe9bec7": "Autism answer attributes an S2 qualification to S1 and overstates an unproven opioid theory.",
    "84938b76c7cebb51": "Wendell answer misattributes his father's proposal and invents a counseling conclusion.",
    "e6701943e08297aa": "Jung translation retains French and English fragments and distorts clinical language.",
    "43799fd9f0d5ed0b": "Cancer study translation changes 83 participants to 38 and leaves untranslated text.",
    "43edb073bbbac650": "Jung translation leaves key terms untranslated in both passages and answer.",
    "ce43f159d35c9e05": "Trauma translation leaves an English clause, and paired passages do not support the answer's synthesis.",
    "8fdd9226ff696432": "The model wrote the answer in English and misattributes basic emotions to S1.",
    "0f3ea299fbb95f32": "Only 31 Vietnamese characters translate a 650-character English S2 passage.",
    "7a1dffdf40820d5d": "Only 115 Vietnamese characters translate a 650-character English S1 passage.",
    "0c1774d428b10082": "Only 164 Vietnamese characters translate a 650-character English S2 passage.",
}


def build(source: Path, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"Dữ liệu đích đã tồn tại: {destination}")
    source_path = source / "approved_manifest.jsonl"
    raw = source_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Nguồn v1 đã thay đổi; cần kiểm tra lại bản ghi và các loại trừ")
    source_approval = json.loads((source / "approval.json").read_text(encoding="utf-8"))
    if source_approval.get("dataset_sha256") != hashlib.sha256(
        (source / "train.jsonl").read_bytes() + (source / "valid.jsonl").read_bytes()
    ).hexdigest():
        raise ValueError("Bộ v1 không còn khớp approval.json")

    records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    kept, removed = [], []
    for original in records:
        record = copy.deepcopy(original)
        suffix = record["key"].rsplit(":", 1)[-1]
        if suffix in EXCLUDED:
            removed.append({"key": record["key"], "reason": EXCLUDED[suffix]})
            continue
        if suffix == "54fcacd1ff812d00":
            record["translations"]["S2"] = (
                "Wearing là một nhạc sĩ và chỉ huy dàn hợp xướng được kính trọng ở Anh. "
                "Khi ở độ tuổi 40, ông mắc viêm não do virus, làm tổn hại những phần của "
                "thùy thái dương quan trọng cho việc hình thành ký ức mới. Vì tổn thương "
                "não, ông chỉ sống trong một đến hai phút gần nhất: nhớ điều vừa xảy ra "
                "rồi quên những điều khác. Khi gặp lại một người vừa rời phòng ba phút "
                "trước, ông phản ứng như chưa từng gặp người ấy. Vì không thể hình thành "
                "ký ức mới, ông liên tục cảm thấy như mình vừa có ý thức lần đầu."
            )
        if suffix == "31ef061948403573":
            for item in record["items"]:
                item["answer"] = item["answer"].replace(
                    "khu vực sktech visuospatial", "bảng phác họa thị giác không gian"
                )
        if suffix == "c765e12f72e55478":
            record["items"][0]["answer"] = (
                "Trong thí nghiệm, nhóm có ba hạn nộp cố định đạt kết quả tốt nhất, "
                "nhóm không có hạn nộp kém nhất, còn nhóm tự chọn ba hạn nộp nằm giữa "
                "[S1]. Đoạn sau cho rằng người nhận ra xu hướng trì hoãn của mình "
                "có thể tìm cách vượt qua; các cam kết trước giúp tránh cám dỗ khi "
                "theo đuổi mục tiêu dài hạn [S2]. Hai đoạn không chứng minh rằng chỉ "
                "nhận thức về trì hoãn là đủ để cải thiện điểm số."
            )
            record["items"][1]["answer"] = (
                "Trong thí nghiệm này, nhóm có ba hạn nộp cố định đạt kết quả "
                "tốt nhất; nhóm không có hạn nộp kém nhất; nhóm tự chọn hạn nộp "
                "có kết quả ở giữa [S1]. Đoạn sau lưu ý rằng tự đặt hạn nộp không "
                "bảo đảm chọn được hạn tốt nhất và cam kết trước có thể giúp người "
                "hay trì hoãn tránh cám dỗ [S2]. Kết quả chỉ nói về các điều kiện "
                "được nghiên cứu, không chứng minh mọi giới hạn tự do đều có ích."
            )
        if suffix == "8af94b6c03470eeb":
            record["items"][0]["answer"] = (
                "Tác giả mô tả qEEG là tương đối rẻ và có thể cầm tay so với MRI "
                "chức năng; dữ liệu qEEG có thể được so sánh với nhiều mẫu khác "
                "[S1]. Ở đoạn sau, tác giả dùng qEEG để quan sát các mô hình sóng "
                "não ở một số người có lịch sử stress do sang chấn và bàn về việc "
                "giải thích khó khăn về tập trung hoặc cảm xúc [S2]. Hai đoạn "
                "không đủ để kết luận qEEG tự nó chẩn đoán được một cá nhân hay "
                "thay thế MRI trong mọi trường hợp."
            )
        if suffix == "6075ad73e749a49e":
            record["items"][0]["answer"] = (
                "Người Berinmo dùng một từ cho nhiều thẻ màu mà người Anh gọi "
                "bằng các tên màu khác nhau; đây là khác biệt về cách đặt tên và "
                "phân loại [S1]. Đoạn sau giải thích nhận thức theo loại: với ví "
                "dụ của người nói tiếng Anh, hai thẻ cùng loại A và B khó phân biệt "
                "hơn hai thẻ khác loại B và C [S2]. Phần nguồn được cung cấp chưa "
                "cho thấy kết quả đầy đủ của nhóm Berinmo, nên chưa thể kết luận "
                "họ nhìn thấy màu vật lý khác người Anh."
            )
        if not basic_record_quality(record):
            raise ValueError(f"Bản ghi còn lại không đạt kiểm tra cơ bản: {record['key']}")
        source_text = " ".join(s["text"] for s in record["pair"]["sources"])
        source_numbers = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?", source_text))
        for item in record["items"]:
            answer = re.sub(r"\[S[12]\]", "", item["answer"])
            answer_numbers = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?%?", answer))
            if answer_numbers - source_numbers:
                raise ValueError(f"Số trong đáp án không thấy ở nguồn: {record['key']}")
        kept.append(record)

    if {r["key"].rsplit(":", 1)[-1] for r in removed} != set(EXCLUDED):
        raise ValueError("Danh sách loại trừ không khớp nguồn v1")
    destination.mkdir(parents=True)
    (destination / "approved_manifest.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8"
    )
    teachers = sorted({str(record.get("teacher_model", "unknown")) for record in kept})
    summary = write_training_files(kept, destination, ", ".join(teachers) or "unknown")
    summary.update({
        "upstream_audit_complete": True,
        "upstream_approved_records": len(records),
        "curated_records": len(kept),
        "excluded_records": len(removed),
        "source_manifest_sha256": SOURCE_SHA256,
    })
    (destination / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    digest = hashlib.sha256(
        (destination / "train.jsonl").read_bytes() + (destination / "valid.jsonl").read_bytes()
    ).hexdigest()
    (destination / "approval.json").write_text(
        json.dumps({"dataset_sha256": digest, "method": "curated-from-approved-v1",
                    "source_manifest_sha256": SOURCE_SHA256, "curated_records": len(kept)},
                   ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (destination / "curation.json").write_text(
        json.dumps({"excluded": removed, "corrections": [
            {"key_suffix": "54fcacd1ff812d00", "reason": "Corrected the S2 Vietnamese translation against the English source."},
            {"key_suffix": "31ef061948403573", "reason": "Corrected a malformed technical term in the answer."},
            {"key_suffix": "c765e12f72e55478", "reason": "Rewrote two validation answers to separate observed deadlines from unsupported mechanisms."},
            {"key_suffix": "8af94b6c03470eeb", "reason": "Rewrote a validation answer to bound qEEG claims to the source."},
            {"key_suffix": "6075ad73e749a49e", "reason": "Rewrote a validation answer after checking categorical perception direction and missing Berinmo results."},
        ]}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Create curated training data v2")
    parser.add_argument("--source", type=Path, default=BASE_DIR / "data/training/v1")
    parser.add_argument("--destination", type=Path, default=BASE_DIR / "data/training/v2")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
