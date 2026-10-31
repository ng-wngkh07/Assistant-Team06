"""Reviewed source additions and a separate, explicitly synthetic reasoning curriculum."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from app.config import DB_PATH
from app.merge_training import _approved_records, _source_is_current
from app.prepare_v5 import book_partition, digest, read_records, write_json
from app.text_cleaner import sanitize_for_prompt_context
from app.training_audit import check_record
from app.training_data import basic_record_quality, pair_language, write_training_files

ROOT = Path(__file__).resolve().parent.parent
SYSTEM = "Bạn là trợ lý tâm lý học. Trả lời bằng tiếng Việt, giải thích rõ, phân biệt dữ kiện và giới hạn kết luận. Không in suy nghĩ nội bộ."


def curriculum():
    """Formal exercises, not facts attributed to any PDF or clinical records."""
    examples = []
    def add(kind, question, answer, derivation):
        examples.append({"id": f"synthetic-v6-{len(examples)}", "kind": kind,
                         "provenance": "Codex-authored hypothetical exercise; not a PDF excerpt",
                         "derivation": derivation, "messages": [
                             {"role": "system", "content": SYSTEM},
                             {"role": "user", "content": question},
                             {"role": "assistant", "content": answer}]})
    for n, part in [(60,15),(80,24),(120,30),(150,45),(200,50),(240,72),(300,90),(400,100)]:
        percent=part/n*100
        add("proportion", f"Bài tập giả định: trong {n} người tham gia, {part} người chọn hoạt động A. Tỷ lệ chọn A là bao nhiêu? Có thể coi đây là tỷ lệ của mọi người trong dân số không?",
            f"Tỷ lệ trong mẫu là {part}/{n} × 100 = {percent:g}%. Đây là tỷ lệ quan sát ở nhóm tham gia. Chưa thể coi đó là tỷ lệ của toàn bộ dân số vì đề bài không cho biết cách chọn mẫu hoặc mức độ đại diện. Một phép tính đúng trong mẫu không tự bảo đảm khả năng khái quát.",
            {"numerator":part,"denominator":n,"percent":percent})
    # Use non-medical detection tasks; reserve the established benchmark numbers.
    for n, rate, sensitivity, false_rate in [(2000,.1,.8,.1),(4000,.05,.9,.1),(5000,.2,.8,.05),(1000,.2,.9,.1),
                                              (8000,.1,.75,.05),(3000,.2,.7,.1),(6000,.05,.8,.2),(10000,.2,.9,.05)]:
        positive=round(n*rate);true=round(positive*sensitivity);false=round((n-positive)*false_rate)
        posterior=true/(true+false)*100
        add("conditional_probability", f"Bài toán giả định về bộ lọc thư: có {n} thư, {rate*100:g}% là thư rác. Bộ lọc gắn cờ {sensitivity*100:g}% thư rác và nhầm {false_rate*100:g}% thư thường. Trong các thư bị gắn cờ, khoảng bao nhiêu phần trăm thực sự là thư rác? Giải thích bằng số thư.",
            f"Có {positive} thư rác và {n-positive} thư thường. Bộ lọc gắn cờ đúng {true} thư rác và gắn cờ nhầm {false} thư thường. Tổng số thư bị gắn cờ là {true+false}. Do đó tỷ lệ thư rác trong nhóm bị gắn cờ là {true}/({true}+{false}) × 100 ≈ {posterior:.2f}%. Tỷ lệ này khác tỷ lệ phát hiện thư rác vì mẫu số gồm cả những thư thường bị gắn cờ nhầm.",
            {"total":n,"target":positive,"true_flag":true,"false_flag":false,"posterior_percent":posterior})
    for group, x, y, confound in [
        ("nhân viên","uống nhiều cà phê","làm việc muộn","khối lượng công việc"),
        ("người đọc sách","đến thư viện nhiều","đọc nhanh","thói quen đọc từ trước"),
        ("người đi xe","đi xa hơn","mệt hơn","thời gian đi đường"),
        ("người tham gia câu lạc bộ","tham gia thường xuyên","có nhiều bạn","mức độ hướng ngoại từ trước"),
        ("trẻ em","có nhiều sách ở nhà","làm bài tốt hơn","nguồn lực của gia đình"),
        ("người tập nhạc","tập nhiều giờ","nhớ giai điệu tốt hơn","kinh nghiệm âm nhạc trước đó"),
        ("người dùng ứng dụng","mở ứng dụng thường xuyên","hoàn thành nhiều bài","động lực ban đầu"),
        ("người chơi thể thao","tham gia nhiều buổi","gắn kết với đội hơn","mức độ gắn kết có sẵn")]:
        add("causal_limits", f"Khảo sát {group} thấy ai {x} thường {y}. Chỉ từ thông tin này có thể kết luận nguyên nhân không?",
            f"Chưa thể kết luận quan hệ nhân quả từ kết quả khảo sát này. Nó cho thấy hai đặc điểm có liên hệ trong mẫu. {confound.capitalize()} có thể liên quan đến cả hai; đây là giả thuyết cần kiểm tra, không phải điều đã được chứng minh. Cũng cần xem xét chiều tác động ngược và cách chọn mẫu. Thiết kế có kiểm soát hoặc theo dõi theo thời gian có thể giúp kiểm tra tốt hơn, nhưng vẫn cần nêu các giới hạn cụ thể.",
            {"observed":"association only","proposed_confound":confound})
    for a,b,c in [("An","Bình","Chi"),("Dũng","Hà","Lan"),("Mai","Nam","Oanh"),("Phúc","Quân","Trang")]:
        add("explicit_attribution", f"Ghi chú: {a} đã đọc sách Y. {b} nói rõ rằng mình chưa đọc sách Y. Ai chưa đọc? Chỉ dựa vào ghi chú.",
            f"{b} là người chưa đọc sách Y. Ghi chú xác định rõ lời nói của {b}, còn {a} đã đọc. Vì chủ thể được nêu trực tiếp nên không cần suy đoán hoặc từ chối kết luận.", {"known_subject":b})
        add("valid_forward_logic", f"Quy tắc giả định không có ngoại lệ: nếu {a} hoàn thành bài tập thì nhận chứng nhận. {a} đã hoàn thành bài tập. Có kết luận {a} nhận chứng nhận được không?",
            f"Có. Quy tắc nêu nếu hoàn thành bài tập thì nhận chứng nhận, và đề bài xác nhận {a} đã hoàn thành. Vì vậy kết luận {a} nhận chứng nhận được hỗ trợ bởi hai tiền đề. Đây là áp dụng đúng chiều của điều kiện, không phải suy ngược từ kết quả.", {"valid_inference":"modus ponens"})
        add("valid_contrapositive", f"Giả sử nếu {a} hoàn thành bài tập thì nhận chứng nhận, và quy tắc không có ngoại lệ. {a} không nhận chứng nhận. Có thể suy ra điều gì về việc hoàn thành bài tập?",
            f"Theo các giả thiết đã cho, {a} không hoàn thành bài tập. Nếu đã hoàn thành thì phải nhận chứng nhận; việc không nhận chứng nhận loại trừ khả năng hoàn thành trong quy tắc này. Đây là suy luận từ 'nếu P thì Q' và 'không Q' tới 'không P'. Kết luận phụ thuộc giả thiết quy tắc không có ngoại lệ.", {"valid_inference":"modus tollens"})
        add("attribution", f"Một ghi chú viết: '{a} tham gia nhóm đọc sách. Sau đó {b} hỏi {c} về một cuốn sách. Người ấy chưa đọc sách đó.' Có thể khẳng định {a} là người chưa đọc không?",
            f"Không. Ghi chú chỉ nêu rõ {a} tham gia nhóm đọc sách. Đại từ 'người ấy' ở câu cuối chưa có chủ thể được xác định chắc chắn trong thông tin cung cấp; không được tự gán cho {a}. Cần thêm câu trước hoặc lời giải thích về người được nhắc đến để biết ai chưa đọc. Sự xuất hiện của một tên trong đoạn không đủ chứng minh mọi hành động đều thuộc người đó.", {"ambiguous_subject":True})
        add("conditional_logic", f"Quy tắc giả định: nếu {a} hoàn thành bài tập thì được nhận chứng nhận. {a} đã nhận chứng nhận. Có chắc {a} hoàn thành bài tập không?",
            "Chưa chắc. Quy tắc chỉ nói hoàn thành bài tập là điều kiện đủ để nhận chứng nhận, không nói đó là cách duy nhất. Từ 'nếu P thì Q' và việc Q xảy ra không thể suy ra chắc chắn P. Muốn kết luận, cần thêm điều kiện rằng chỉ người hoàn thành bài tập mới được nhận chứng nhận.", {"invalid_inference":"affirming the consequent"})
    for scenario in ["một đồng nghiệp né cuộc họp", "một bạn đọc im lặng khi thảo luận", "một sinh viên bỏ một buổi học", "một người đổi lịch hẹn"]:
        add("unknown_motive", f"Chỉ biết {scenario}. Có thể khẳng định họ đang che giấu điều gì không?",
            "Chưa thể khẳng định động cơ từ một hành vi đơn lẻ. Có nhiều khả năng giải thích, nhưng thông tin hiện có chưa phân biệt được các khả năng ấy. Cần hỏi thêm hoàn cảnh và lời giải thích của chính người đó. Không nên trình bày giả thuyết như một sự thật, hoặc gán nhãn tâm lý chỉ vì hành vi có vẻ khác thường.", {"evidence":"one observed behavior; no motive evidence"})
    terms=[("correlation","tương quan"),("causation","quan hệ nhân quả"),("working memory","trí nhớ làm việc"),
           ("negative reinforcement","củng cố âm tính"),("punishment","trừng phạt"),("dissociation","phân ly"),
           ("random assignment","phân nhóm ngẫu nhiên"),("leading question","câu hỏi dẫn dắt"),
           ("classical conditioning","điều kiện hóa cổ điển"),("operant conditioning","điều kiện hóa thao tác"),
           ("hippocampus","hồi hải mã"),("limbic system","hệ viền")]
    for english, vietnamese in terms:
        add("terminology", f"Trong tâm lý học, '{english}' thường được dịch sang tiếng Việt là gì? Trả lời ngắn gọn.",
            f"'{english}' thường được dịch là '{vietnamese}'.", {"english":english,"vietnamese":vietnamese})
    for text in ["toi lo lang truoc buoi thuyet trinh", "tôi khó tập trung vào cuối ngày", "toi thay met sau mot tuan ban ron", "tôi hồi hộp trước khi gặp người mới"]:
        add("clinical_limit", f"{text}. Điều đó có đủ để xác nhận một rối loạn tâm lý không?",
            "Thông tin này chưa đủ để xác nhận một rối loạn tâm lý. Cần hiểu thời gian kéo dài, mức độ, hoàn cảnh và ảnh hưởng đến sinh hoạt, cùng những yếu tố liên quan khác. Nếu biểu hiện kéo dài hoặc gây khó khăn đáng kể, bạn có thể trao đổi với chuyên gia phù hợp. Không nên tự gán chẩn đoán chỉ từ một biểu hiện hoặc một câu mô tả.", {"diagnosis_supported":False})
    for question,answer in [
        ("Không có đoạn sách đi kèm. Khi trả lời một câu hỏi tâm lý học tổng quát, cần phân biệt những loại thông tin nào?", "Cần phân biệt kiến thức khái quát, giả thuyết giải thích và dữ kiện về trường hợp cụ thể. Có thể giải thích khái niệm chung, nhưng không được nói rằng một đoạn sách đã chứng minh điều gì khi không có đoạn sách để đối chiếu. Nếu thiếu thông tin về một người, cần nêu giới hạn thay vì suy ra động cơ hoặc chẩn đoán."),
        ("Nếu hai sách cùng dùng từ 'trí nhớ', liệu chúng đang nói cùng một loại trí nhớ không?", "Chưa chắc. Cần xem mỗi đoạn định nghĩa và sử dụng thuật ngữ ra sao, chẳng hạn duy trì thông tin tạm thời, nhớ sự kiện hay kỹ năng đã học. Một từ chung không đủ chứng minh hai kết quả có thể ghép thành cùng một kết luận. Phải kiểm tra đối tượng, bối cảnh và phạm vi của từng phát biểu."),
        ("Một câu tiếng Anh bị cắt ở 'may depend on'. Khi dịch, có nên tự thêm yếu tố mà nó phụ thuộc không?", "Không. Có thể dịch phần còn đủ nghĩa và báo rằng câu bị ngắt ở 'có thể phụ thuộc vào…'. Không tự bổ sung yếu tố còn thiếu dù có kiến thức về chủ đề. Phần bị cắt là giới hạn của nguồn, không phải khoảng trống để điền bằng phỏng đoán."),
        ("Một nghiên cứu ở một nhóm nhỏ cho kết quả tốt. Có thể hứa phương pháp ấy hiệu quả cho mọi người không?", "Chưa thể. Kết quả ở nhóm nghiên cứu không tự bảo đảm hiệu quả cho mọi người hoặc mọi hoàn cảnh. Cần xem thiết kế, cách chọn mẫu, độ bất định và khả năng lặp lại. Có thể trình bày kết quả quan sát cùng giới hạn, nhưng không hứa hiệu quả cá nhân khi chưa có căn cứ.")]:
        add("knowledge_scope", question, answer, {"rule":"distinguish general knowledge, source evidence and individual claims"})
    return examples


def prepare(output):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new dataset directory")
    output.mkdir(parents=True,exist_ok=True)
    previous=ROOT/"data/training/v5"
    records, old_hash=_approved_records(previous)
    heldout=set(json.loads((previous/"summary.json").read_text())["held_out_books"])
    definitions=json.loads((ROOT/"data/training/v6_source_additions.json").read_text())
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory=sqlite3.Row
        allowed, aliases=book_partition(db,heldout)
        for definition in definitions:
            rows=[db.execute("SELECT * FROM chunks WHERE id=?",(n,)).fetchone() for n in definition["chunk_ids"]]
            if any(r["filename"] not in allowed for r in rows):
                raise ValueError("Held-out or duplicate content selected")
            sources=[{"id":f"S{i}","filename":r["filename"],"book":r["book_title"],"page":r["page_num"],
                      "chunk_id":r["id"],"text":sanitize_for_prompt_context(r["text"][:650])} for i,r in enumerate(rows,1)]
            pair={"filename":rows[0]["filename"],"title":rows[0]["book_title"],"pair_index":definition["chunk_ids"][0],"sources":sources}
            if len({r["filename"] for r in rows})>1:
                pair.update({"filename":"cross:v6:"+digest(definition["chunk_ids"]),"title":definition["topic"],"cross_book":True})
            record={"key":"v6:"+digest(pair),"pair":pair,"teacher_model":"source-reviewed-by-codex", "language":pair_language(pair),
                    "translations":{f"S{i}":t for i,t in enumerate(definition["translations"],1)},"items":definition["items"]}
            if not _source_is_current(db,pair) or not basic_record_quality(record):
                raise ValueError("Invalid source or translation: "+record["key"])
            records.append(record)
        if any(not _source_is_current(db,r["pair"]) for r in records):
            raise ValueError("Previous sources changed")
    # Fix only the new version's held-out translation labels, never v5 files.
    cases=json.loads((previous/"evaluation_reference_cases.json").read_text())["cases"]
    errata=json.loads((ROOT/"data/evaluation/v5-reference-errata.json").read_text())["entries"]
    changes=[]
    for entry in errata:
        case=cases[int(entry["case_id"].split("-")[1])]
        source=case["messages"][-2]["content"]
        for r in records:
            if r["pair"]["filename"] not in heldout: continue
            for s in r["pair"]["sources"]:
                if s["text"]==source:
                    r["translations"][s["id"]]=entry["corrected_reference"]
                    changes.append({"record_key":r["key"],"source_id":s["id"],"errata":entry["case_id"]})
        case["messages"][-1]["content"]=entry["corrected_reference"]
    if len(changes)!=2: raise ValueError("Errata did not align to exactly two validation labels")
    (output/"manifest.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in records))
    synthetic=curriculum()
    (output/"curriculum_manifest.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in synthetic))
    write_json(output/"evaluation_reference_cases.json",{"cases":cases,"errata_applied":changes})
    write_json(output/"plan.json",{"previous_dataset_sha256":old_hash,"inherited_records":len(records)-len(definitions),
                                 "new_source_records":len(definitions),"synthetic_examples":len(synthetic),
                                 "held_out_books":sorted(aliases),"allowed_training_books":sorted(allowed),"errata":changes})
    return {"records":len(records),"new_source_records":len(definitions),"synthetic":len(synthetic)}


def audit(output, primary=None, secondary=None):
    from app.model_resolver import reject_retired_model
    if not primary or not secondary or primary == secondary:
        raise ValueError("Cần hai mô hình kiểm định đã hiệu chuẩn cho quy trình hiện hành; không tái dùng mô hình cũ.")
    reject_retired_model(primary)
    reject_retired_model(secondary)
    records=read_records(output/"manifest.jsonl")
    plan=json.loads((output/"plan.json").read_text())
    previous=ROOT/"data/training/v5"
    decisions=[]
    for name,model in [("primary",primary),("secondary",secondary)]:
        path=output/f"audit_{name}.jsonl"
        reviewed={e["key"]:e for e in read_records(path)}
        inherited={e["key"]:e for e in read_records(previous/f"audit_{name}.jsonl")}
        for i,r in enumerate(records):
            key=digest(r)
            if key in reviewed: continue
            # Only exact hash-matching decisions can be reused.
            if key in inherited:
                entry={**inherited[key],"inherited_from":"v5"}
            else:
                entry={"key":key,"record_key":r["key"],"model":model,"audit_version":1,**check_record(r,model)}
            with path.open("a") as f:f.write(json.dumps(entry,ensure_ascii=False)+"\n")
            reviewed[key]=entry
            print(f"audit {name} {i+1}/{len(records)}: {sum(d['supported'] for d in entry['decisions'])}/{len(r['items'])}",flush=True)
        decisions.append(reviewed)
    # Independent calibration is rerun, not inherited from a prior process.
    positive=next(r for r in records if r["pair"].get("cross_book") and r.get("teacher_model")=="human-reviewed")
    negative=json.loads(json.dumps(positive));negative["items"]=[{"question":"Hai đoạn có chứng minh trí nhớ hoàn hảo không?","answer":"Mọi người đều có trí nhớ hoàn hảo và không bao giờ quên. [S1] [S2]"}]
    controls=[{"model":model,"positive":check_record(positive,model),"negative":check_record(negative,model)} for model in (primary,secondary)]
    calibrated=all(c["positive"]["translation_ok"] and c["positive"]["cross_source_link_ok"] and any(d["supported"] for d in c["positive"]["decisions"]) and not any(d["supported"] for d in c["negative"]["decisions"]) for c in controls)
    write_json(output/"calibration.json",{"calibrated":calibrated,"controls":controls})
    approved=[]
    with sqlite3.connect(DB_PATH) as db:
        for r in records:
            a,b=[d[digest(r)] for d in decisions]
            if not basic_record_quality(r) or not _source_is_current(db,r["pair"]):continue
            if not all(x["translation_ok"] and x["cross_source_link_ok"] for x in (a,b)):continue
            items=[item for item,x,y in zip(r["items"],a["decisions"],b["decisions"]) if x["supported"] and y["supported"]]
            if items:approved.append({**r,"items":items})
    if not calibrated:raise ValueError("Judges failed calibration")
    (output/"approved_manifest.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in approved))
    summary=write_training_files(approved,output,"source-reviewed-by-codex",plain_answer=True,augment_prompts=True,held_out_override=set(plan["held_out_books"]))
    synthetic=read_records(output/"curriculum_manifest.jsonl")
    # Exact arithmetic checks do not depend on an LLM judge.
    for r in synthetic:
        d=r["derivation"]
        if r["kind"]=="conditional_probability":
            if not abs(d["posterior_percent"]-100*d["true_flag"]/(d["true_flag"]+d["false_flag"]))<1e-8:raise ValueError("Arithmetic failed")
        if r["kind"]=="proportion" and not abs(d["percent"]-100*d["numerator"]/d["denominator"])<1e-8:raise ValueError("Proportion failed")
    with (output/"train.jsonl").open("a") as f:
        for r in synthetic:f.write(json.dumps({"messages":r["messages"]},ensure_ascii=False)+"\n")
    samples=read_records(output/"train.jsonl")
    unique=list({digest(sample):sample for sample in samples}.values())
    (output/"train.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in unique))
    books={s.get("filename",r["pair"]["filename"]) for r in approved for s in r["pair"]["sources"]}-set(plan["held_out_books"])
    summary.update({"train_examples":len(unique),"exact_duplicate_examples_removed":len(samples)-len(unique),"synthetic_examples":len(synthetic),
                    "synthetic_kinds":dict(Counter(r["kind"] for r in synthetic)),"train_books":sorted(books),
                    "manifest_records":len(records),"approved_records":len(approved),"audit_complete":True,
                    "source_verified_against_index":True,"verifier_calibrated":True,"previous_dataset_sha256":plan["previous_dataset_sha256"]})
    write_json(output/"summary.json",summary)
    if len(books)<27 or summary["train_examples"]<=256 or summary["train_cross_book_pairs"]<6:raise ValueError("Expansion did not meet source coverage/cross-book gates")
    # Every artifact that contributes to training is hash-bound.
    write_json(output/"approval.json",{"dataset_sha256":hashlib.sha256((output/"train.jsonl").read_bytes()+(output/"valid.jsonl").read_bytes()).hexdigest(),
                                      "approved_manifest_sha256":hashlib.sha256((output/"approved_manifest.jsonl").read_bytes()).hexdigest(),
                                      "auxiliary_files_sha256":{"curriculum_manifest.jsonl":hashlib.sha256((output/"curriculum_manifest.jsonl").read_bytes()).hexdigest()},
                                      "method":"hash-inherited-two-model-review-plus-new-source-review-and-formal-curriculum",
                                      "synthetic_review":"Codex authored/reviewed; exact arithmetic checked; not independently expert reviewed",
                                      "source_verified_against_index":True,"audit_complete":True})
    return summary


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("stage",choices=["prepare","audit"]);p.add_argument("--output",type=Path,default=ROOT/"data/training/v6")
    a=p.parse_args();print(json.dumps((prepare if a.stage=="prepare" else audit)(a.output),ensure_ascii=False),flush=True)
