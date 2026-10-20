"""Build pending workspace (data/training/v7_pending_review/pending_drafts.jsonl)
containing 16 diverse new source-grounded drafts + 6 corrected substantive controls.
Every item is fully validated with validate_claim_map (100% coverage, verbatim spans, valid sentence IDs).
Provenance: authoring_route = 'antigravity_agent_source_review', status = 'draft_pending_codex_review'.
"""

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR_PATH = Path(__file__).resolve().parent.parent
if str(BASE_DIR_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_DIR_PATH))

from app.config import BASE_DIR
from app.curate_dataset_v7 import (
    compute_digest,
    resolve_exact_source_span_offsets,
    segment_text_into_sentences,
    validate_claim_map,
    SYSTEM_PROMPT_TRAIN
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_pending_workspace")


def build_item(
    source_id: str,
    chunk_id: int,
    book: str,
    book_title: str,
    page: int,
    domain: str,
    partition: str,
    source_text: str,
    question: str,
    answer: str,
    claims_spec: List[Dict[str, Any]],
    is_control: bool = False
) -> Dict[str, Any]:
    """Build a validated pending draft item."""
    src_sents = segment_text_into_sentences(source_text, prefix="S1")
    ans_sents = segment_text_into_sentences(answer, prefix="A1")

    # Match each claim with exact answer sentence offsets and exact source evidence spans
    ans_sent_map = {s["id"]: s for s in ans_sents}
    claim_map = []

    for idx, c in enumerate(claims_spec, 1):
        ans_id = c["answer_sentence_id"]
        if ans_id not in ans_sent_map:
            raise ValueError(f"Unknown answer_sentence_id {ans_id} in {source_id}")
        ans_s = ans_sent_map[ans_id]

        # Verify evidence spans against source text
        ev_spans = c.get("evidence_spans", [])
        ev_span_offsets = []
        for sp in ev_spans:
            resolved = resolve_exact_source_span_offsets(source_text, sp)
            if resolved is None:
                raise ValueError(f"Failed to resolve evidence span '{sp}' in {source_id}")
            verbatim, s_off, e_off = resolved
            ev_span_offsets.append({
                "text": verbatim,
                "start": s_off,
                "end": e_off
            })

        claim_map.append({
            "claim_id": f"C{idx}",
            "claim_text": c["claim_text"],
            "answer_sentence_id": ans_id,
            "answer_offsets": [ans_s["start"], ans_s["end"]],
            "source_id": "S1",
            "source_sentence_ids": c.get("source_sentence_ids", []),
            "evidence_spans": ev_spans,
            "evidence_span_offsets": ev_span_offsets,
            "relation": c.get("relation", "entailment"),
            "modality": c.get("modality", "asserted"),
            "claim_type": c.get("claim_type", "fact"),
            "is_supported": True
        })

    # Validate claim map using shared fail-closed validator
    val_res = validate_claim_map(source_text, answer, claim_map, source_id="S1")
    if not val_res["valid"]:
        raise ValueError(f"Validation failed for {source_id}: {val_res['violations']}")

    src_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    q_hash = hashlib.sha256(question.strip().encode("utf-8")).hexdigest()
    ans_hash = hashlib.sha256(answer.strip().encode("utf-8")).hexdigest()

    # Build canonical message target
    doc_header = f'<document id="S1" book="{book}" page="{page}">\n{source_text}\n</document>'
    synthesized_msg = [
        {"role": "system", "content": SYSTEM_PROMPT_TRAIN},
        {"role": "user", "content": f"<context>\n{doc_header}\n</context>\n\nCâu hỏi: {question}\nHãy trả lời chi tiết bằng tiếng Việt, dẫn mã [S1]."},
        {"role": "assistant", "content": answer}
    ]
    target_hash = compute_digest(synthesized_msg)

    return {
        "source_id": source_id,
        "chunk_id": chunk_id,
        "book": book,
        "book_title": book_title,
        "page": page,
        "source_group": f"{book}:{page}",
        "partition": partition,
        "domain": domain,
        "is_control": is_control,
        "source_text": source_text,
        "source_sha256": src_hash,
        "source_sentences": src_sents,
        "question": question,
        "question_sha256": q_hash,
        "answer": answer,
        "answer_sha256": ans_hash,
        "answer_sentences": ans_sents,
        "claim_map": claim_map,
        "claim_map_hash": val_res["claim_map_hash"],
        "full_answer_coverage": val_res["coverage"],
        "messages": synthesized_msg,
        "target_sha256": target_hash,
        "authoring_route": "antigravity_agent_source_review",
        "provenance": "antigravity_agent_source_review",
        "status": "draft_pending_codex_review"
    }


def build_all_records() -> List[Dict[str, Any]]:
    # Load 16 verified sources
    sources_path = BASE_DIR / "data" / "training" / "v7_pending_review" / "verified_16_sources.json"
    with open(sources_path, "r", encoding="utf-8") as f:
        src_16 = json.load(f)

    # Load paired controls
    controls_path = BASE_DIR / "data" / "evaluation" / "codex-pilot16-paired-controls-2026-10-02-0800.json"
    with open(controls_path, "r", encoding="utf-8") as f:
        controls_data = json.load(f)

    all_records = []

    # -----------------------------------------------------------------------
    # 6 Corrected Controls (Cases 0, 1, 2, 3, 5, 6)
    # -----------------------------------------------------------------------
    # Case 0: Ariely Hospital pain
    c0 = controls_data["cases"][0]
    all_records.append(build_item(
        source_id=c0["source_id"],
        chunk_id=33318,
        book=c0["book"],
        book_title=c0["book"],
        page=c0["page"],
        domain="cognition",
        partition="train",
        source_text=c0["source_text"],
        question=c0["question"],
        answer=c0["corrected_control"]["answer"],
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Tác giả tự hỏi về các lựa chọn cá nhân như lý do yêu một người hay thích leo núi hơn học lịch sử",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["sao tôi lại đem lòng yêu cô gái\nnày chứ không phải một cô gái khác", "tại sao tôi\nthích leo núi mà không phải là học lịch sử"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Tác giả trải qua đau đớn trong thời gian nằm viện và có thời gian suy nghĩ giữa các đợt điều trị",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Trong suốt thời gian nằm viện, tôi đã trải qua nhiều cơn đau đớn.", "Khoảng thời gian giữa những lần điều trị và phẫu thuật giúp tôi suy nghĩ về\nđiều này."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Cơn đau xảy ra chủ yếu khi ngâm dung dịch khử trùng, tháo băng và chất khử trùng gây đau dữ dội khi da không lành lặn",
                "source_sentence_ids": ["S1.4", "S1.5", "S1.6"],
                "evidence_spans": ["khi\ntôi được ngâm vào dung dịch khử trùng, tháo băng và lấy tế bào đã chết.", "chất khử trùng khiến tôi vô cùng đau đớn."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.4",
                "claim_text": "Đoạn trích không trực tiếp xác định thái độ bất mãn hay phản kháng tâm lý",
                "source_sentence_ids": ["S1.1", "S1.2", "S1.3"],
                "evidence_spans": ["tại sao những hành động hàng ngày\ncủa tôi chỉ để thuận tiện cho các bác sỹ chứ không phải cho tôi"],
                "relation": "limitation", "modality": "asserted", "claim_type": "limitation"
            }
        ],
        is_control=True
    ))

    # Case 1: Duhigg Kufa riots
    c1 = controls_data["cases"][1]
    all_records.append(build_item(
        source_id=c1["source_id"],
        chunk_id=19437,
        book=c1["book"],
        book_title=c1["book"],
        page=c1["page"],
        domain="learning",
        partition="train",
        source_text=c1["source_text"],
        question=c1["question"],
        answer=c1["corrected_control"]["answer"],
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Thiếu tá phân tích thấy bạo lực mở đầu từ đám đông tụ tập ở trung tâm mua sắm và có người bán thức ăn dạo",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Bạo lực thường mở đầu từ một đám đông người Iraq tụ tập ở\ntrung tâm mua sắm", "Những\nngười bán thức ăn dạo cũng như người xem sẽ xuất hiện."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Bạo lực bùng nổ khi có người ném đá hoặc ném chai",
                "source_sentence_ids": ["S1.4"],
                "evidence_spans": ["Rồi có ai đó ném đá hay ném một cái\nchai và ồn ào sẽ bũng nổ quá sức tưởng tượng."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Thiếu tá yêu cầu thị trưởng giữ người bán thức ăn dạo ngoài trung tâm mua sắm",
                "source_sentence_ids": ["S1.5"],
                "evidence_spans": ["có thể giữ những\nngười bán thức ăn dạo ngoài trung tâm mua sắm được không?"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.4",
                "claim_text": "Đoạn trích kể tiếp có đám đông nhỏ vài tuần sau nhưng chưa nêu diễn biến tiếp theo hoặc kết quả của đề nghị",
                "source_sentence_ids": ["S1.7"],
                "evidence_spans": ["Vài tuần sau đó, có một đám đông nhỏ tập trung gần Masjid al-Kufa, hay nhà thờ Great\nMosque of Kufa."],
                "relation": "limitation", "modality": "asserted", "claim_type": "limitation"
            }
        ],
        is_control=True
    ))

    # Case 2: Kahneman Medical analogy
    c2 = controls_data["cases"][2]
    all_records.append(build_item(
        source_id=c2["source_id"],
        chunk_id=20068,
        book=c2["book"],
        book_title=c2["book"],
        page=c2["page"],
        domain="cognition",
        partition="valid",
        source_text=c2["source_text"],
        question=c2["question"],
        answer=c2["corrected_control"]["answer"],
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Tập trung vào sai lệch không nhằm bôi nhọ nhận thức, giống nghiên cứu bệnh trong y học nhằm mang lại sức khỏe tốt hơn",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["tập trung vào sự sai lệch không nhằm mục đích bôi nhọ\nnhận thức của loài người, giống như các nghiên cứu y học, sự quan tâm\nnghiên cứu các căn bệnh không nằm ngoài mục đích mang lại sức khỏe tốt\nhơn cho loài người."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Phần lớn phán đoán đúng nhưng con người vẫn tự tin khi sai và người khác dễ nhận ra sai lầm hơn bản thân",
                "source_sentence_ids": ["S1.3", "S1.5"],
                "evidence_spans": ["Thông thường hầu hết chúng ta đều khỏe mạnh và hầu\nhết những phán đoán và hành động của chúng ta đều đúng đắn.", "chúng ta vẫn tự tin ngay\ncả khi mắc sai lầm và thường những người khác dễ phát hiện ra sai lầm đó\nhơn là bản thân chúng ta."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ],
        is_control=True
    ))

    # Case 3: Dweck Human qualities
    c3 = controls_data["cases"][3]
    all_records.append(build_item(
        source_id=c3["source_id"],
        chunk_id=39672,
        book=c3["book"],
        book_title=c3["book"],
        page=c3["page"],
        domain="personality",
        partition="train",
        source_text=c3["source_text"],
        question=c3["question"],
        answer=c3["corrected_control"]["answer"],
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Con người từ lâu đã suy nghĩ và hành động khác nhau, từ đó đặt câu hỏi vì sao có người thông minh hay đạo đức hơn",
                "source_sentence_ids": ["S1.6", "S1.7"],
                "evidence_spans": ["people have thought differently, acted differently, and\nfared differently from each other.", "why some people are smarter or more moral—\nand whether there was something that made them permanently different."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Tác giả đặt cạnh nhau niềm tin phẩm chất có thể phát triển và niềm tin phẩm chất là đặc điểm cố định ăn sâu",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Whether human qualities are things that can be cultivated or things that are\ncarved in stone is an old issue.", "thinking that your intelligence or personality is\nsomething you can develop, as opposed to something that is a fixed, deep-seated\ntrait?"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Hậu quả của hai niềm tin này đối với mỗi người là trọng tâm được nêu",
                "source_sentence_ids": ["S1.3"],
                "evidence_spans": ["What are the consequences of thinking that your intelligence or personality is\nsomething you can develop"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.4",
                "claim_text": "Đoạn trích chưa kết luận đặc điểm cố định là đặc điểm đã được quyết định từ khi sinh ra",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["fixed, deep-seated\ntrait"],
                "relation": "limitation", "modality": "asserted", "claim_type": "limitation"
            }
        ],
        is_control=True
    ))

    # Case 5: Blumenfeld HPI
    c5 = controls_data["cases"][5]
    all_records.append(build_item(
        source_id=c5["source_id"],
        chunk_id=25331,
        book=c5["book"],
        book_title=c5["book"],
        page=c5["page"],
        domain="research_methods",
        partition="train",
        source_text=c5["source_text"],
        question=c5["question"],
        answer=c5["corrected_control"]["answer"],
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "HPI cần bao gồm các yếu tố nguy cơ hoặc nguyên nhân khả dĩ, mô tả triệu chứng theo thời gian và sự chăm sóc đã nhận",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["It should include possible risk factors or other \ncauses of the current illness as well as a detailed chronological description \nof all symptoms and prior care obtained for this problem."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Đó là bệnh sử đầy đủ của vấn đề bệnh lý hiện tại đưa bệnh nhân đến khám",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["This is the complete history of the current medical problem that brought the \npatient to medical attention."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Thông tin âm tính liên quan giúp loại trừ chẩn đoán thay thế và quan trọng như thông tin dương tính",
                "source_sentence_ids": ["S1.3"],
                "evidence_spans": ["Pertinent negative information (symptoms or problems that are not present) helps exclude \nalternative diagnoses and is as important as pertinent positive information."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.4",
                "claim_text": "Bệnh lý liên quan có thể nêu ở HPI nhưng vấn đề không trực tiếp liên quan thường thuộc tiền sử bệnh trước đây",
                "source_sentence_ids": ["S1.4"],
                "evidence_spans": ["those that are \nnot directly relevant to the present illness are usually covered instead in the \nsection on past medical history"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ],
        is_control=True
    ))

    # Case 6: Lori Gottlieb Reeling from breakup
    c6 = controls_data["cases"][6]
    all_records.append(build_item(
        source_id=c6["source_id"],
        chunk_id=28283,
        book=c6["book"],
        book_title=c6["book"],
        page=c6["page"],
        domain="clinical",
        partition="valid",
        source_text=c6["source_text"],
        question=c6["question"],
        answer=c6["corrected_control"]["answer"],
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Tác giả đang chao đảo sau khi người đàn ông cô dự định kết hôn bất ngờ chia tay tối hôm trước",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["the man I thought I was going to marry unexpectedly called it quits."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Cô cố gắng tập trung vào bệnh nhân, chỉ cho phép mình khóc trong giờ nghỉ mười phút và lau mascara",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Today I’m trying to focus on my patients (allowing myself to cry only\nin the ten-minute breaks between sessions, carefully wiping away\nmy running mascara before the next person arrives)."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Tác giả đối phó với nỗi đau bằng cách che giấu, tương tự như cô nghi ngờ John đang làm",
                "source_sentence_ids": ["S1.3"],
                "evidence_spans": ["I’m dealing with my pain the way I suspect John has been dealing\nwith his: by covering it up."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.4",
                "claim_text": "Là nhà trị liệu cô biết nỗi đau gắn với mất mát và thay đổi luôn đi kèm mất mát",
                "source_sentence_ids": ["S1.4", "S1.5", "S1.6"],
                "evidence_spans": ["pain is tied to loss.", "change and loss travel together."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.5",
                "claim_text": "Đoạn trích không kết luận cô hoàn toàn mất tập trung hay phán xét cách đối phó này là không lành mạnh",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Today I’m trying to focus on my patients", "by covering it up."],
                "relation": "limitation", "modality": "asserted", "claim_type": "limitation"
            }
        ],
        is_control=True
    ))

    logger.info("Successfully processed 6 corrected controls.")

    # -----------------------------------------------------------------------
    # 16 Diverse New Drafts
    # -----------------------------------------------------------------------
    # 1. Kahneman Pupil Dilation Add-1 (src_16[0])
    s1 = src_16[0]
    all_records.append(build_item(
        source_id=s1["source_id"],
        chunk_id=s1["chunk_id"],
        book=s1["book"],
        book_title=s1["book_title"],
        page=s1["page"],
        domain=s1["domain"],
        partition=s1["partition"],
        source_text=s1["source_text"],
        question="Theo đoạn trích [S1], mức độ nỗ lực của trí óc khi thực hiện nhiệm vụ Cộng 1 có mối liên hệ như thế nào với độ mở của đồng tử và khả năng phát hiện mục tiêu thị giác?",
        answer="Trong nhiệm vụ Cộng 1, người tham gia hầu như không bỏ qua chữ K ở giai đoạn đầu hoặc gần kết thúc, nhưng đa số bỏ qua mục tiêu ở khoảng giữa khi trí óc đang nỗ lực cao nhất dù mắt mở to nhìn chằm chằm. Tỷ lệ thất bại trong phát hiện chữ cái tạo nên sơ đồ hình chữ V ngược tương đồng với độ mở của đồng tử [S1]. Đoạn trích kết luận độ mở đồng tử phản ánh mức độ nỗ lực của chủ thể và là chỉ dấu giúp tìm hiểu cơ chế hoạt động của trí não.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Người tham gia không bỏ qua chữ K ở đầu hoặc cuối nhiệm vụ Cộng 1 nhưng bỏ qua ở giữa nhiệm vụ khi nỗ lực cao nhất",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["Người quan sát hầu\nnhư không bao giờ bỏ qua chữ K nào được trình chiếu ở đầu hoặc khi gần\nkết thúc nhiệm vụ Cộng 1, nhưng hầu hết bọn họ bỏ qua mục tiêu vào\nkhoảng giữa của nhiệm vụ khi trí óc họ đang nỗ lực ở mức cao nhất, mặc dù\ncon mắt của họ mở to hết mức, nhìn chằm chằm vào những bức hình."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Thất bại phát hiện chữ cái tạo sơ đồ chữ V ngược tương tự độ mở đồng tử",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Thất\nbại trong việc phát hiện đối tượng chữ cái cũng vẽ ra sơ đồ hình chữ V\nngược giống với độ mở của đồng tử."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Độ mở đồng tử phù hợp với mức độ nỗ lực và giúp hiểu cách hoạt động của trí não",
                "source_sentence_ids": ["S1.3"],
                "evidence_spans": ["Độ mở của\nđồng tử phù hợp với mức độ nỗ lực của chủ thể và chúng ta có thể tiếp tục đi\ntheo cách này để hiểu cách hoạt động của trí não."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 2. Ariely Decoy Effect (src_16[1])
    s2 = src_16[1]
    all_records.append(build_item(
        source_id=s2["source_id"],
        chunk_id=s2["chunk_id"],
        book=s2["book"],
        book_title=s2["book_title"],
        page=s2["page"],
        domain=s2["domain"],
        partition=s2["partition"],
        source_text=s2["source_text"],
        question="Theo đoạn trích [S1], tác giả đưa ra số liệu thực nghiệm nào về quyết định đăng ký tạp chí của sinh viên và đặt ra câu hỏi nghiên cứu gì về vai trò của lựa chọn làm nền?",
        answer="Trong thực nghiệm được nêu, có 16 sinh viên chọn tạp chí điện tử giá 59$, 0 sinh viên chọn tạp chí in giá 125$, và 84 sinh viên chọn gói cả tạp chí in và điện tử cùng giá 125$. Sinh viên nhận thấy lợi thế rõ rệt của gói kết hợp so với gói chỉ đặt báo in [S1]. Tác giả đặt câu hỏi liệu sự hiện diện của lựa chọn chỉ đặt báo in (gọi là lựa chọn làm nền) có chi phối quyết định hay không, và đề xuất thử nghiệm giả định khi loại bỏ lựa chọn làm nền này.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Số liệu đăng ký: 16 người chọn điện tử 59$, 0 người chọn in 125$, và 84 người chọn cả hai gói cùng giá 125$",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["• Đặt tạp chí điện tử giá 59$ -16 sinh viên\n• Đặt tạp chí in giá 125$ - 0 sinh viên\n• Đặt tạp chí in và tạp chí điện tử giá 125$ - 84 sinh viên"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Sinh viên thấy lợi thế của gói cả hai so với lựa chọn chỉ đặt báo in",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Họ thấy được lợi thế của lựa chọn đặt cả tạp chí in và tạp\nchí điện tử so với lựa chọn chỉ đặt tạp chí in."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Tác giả đặt câu hỏi liệu lựa chọn làm nền có chi phối quyết định và giả định tình huống bỏ lựa chọn làm nền",
                "source_sentence_ids": ["S1.3", "S1.4"],
                "evidence_spans": ["Nhưng liệu họ có bị chi phối\nbởi sự có mặt của lựa chọn chỉ đặt tạp chí in (từ đây tôi sẽ gọi lựa chọn này\nlà lựa chọn làm nền) hay không?", "Giả sử tôi bỏ lựa chọn làm nền và sắp xếp\nlại như sau"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 3. Goldstein Neuropsychology Dissociations (src_16[2])
    s3 = src_16[2]
    all_records.append(build_item(
        source_id=s3["source_id"],
        chunk_id=s3["chunk_id"],
        book=s3["book"],
        book_title=s3["book_title"],
        page=s3["page"],
        domain=s3["domain"],
        partition=s3["partition"],
        source_text=s3["source_text"],
        question="Theo đoạn trích [S1], tâm lý học thần kinh (neuropsychology) được định nghĩa như thế nào và phương pháp phân ly (dissociations) đóng vai trò gì trong việc xác định sự khu trú chức năng?",
        answer="Tâm lý học thần kinh là ngành nghiên cứu hành vi của con người bị tổn thương não bộ, thường bắt nguồn từ tai nạn hoặc đột quỵ làm gián đoạn nguồn cấp máu [S1]. Nguyên lý nền tảng là các chức năng nhận thức sẽ suy giảm theo những cách thức đặc thù khi các vùng não cụ thể bị tổn thương. Phương pháp chủ đạo của ngành là xác định sự phân ly (dissociations), tức các tình huống trong đó một chức năng bị mất đi trong khi một chức năng khác vẫn được duy trì.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Tâm lý học thần kinh nghiên cứu người bị tổn thương não do tai nạn hoặc đột quỵ gián đoạn máu nuôi",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Brain damage is generally caused by accidents or by a stroke in \nwhich the blood supply to an area of the brain is disrupted.", "The study of the behavior of \nhumans with brain damage is called neuropsychology."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Chức năng nhận thức suy thoái theo các cách đặc thù khi những vùng não cụ thể bị tổn thương",
                "source_sentence_ids": ["S1.4"],
                "evidence_spans": ["cognitive functioning breaks down in speciﬁ c ways when speciﬁ c areas of the brain are damaged"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Phương pháp cốt lõi là xác định phân ly - tình huống một chức năng vắng mặt trong khi chức năng khác vẫn hiện diện",
                "source_sentence_ids": ["S1.5"],
                "evidence_spans": ["The major \nmethod of neuropsychology is the determination of dissociations—situations in which one \nfunction is absent while another function is present."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 4. Duhigg Dopamine Habit Loop (src_16[3])
    s4 = src_16[3]
    all_records.append(build_item(
        source_id=s4["source_id"],
        chunk_id=s4["chunk_id"],
        book=s4["book"],
        book_title=s4["book_title"],
        page=s4["page"],
        domain=s4["domain"],
        partition=s4["partition"],
        source_text=s4["source_text"],
        question="Theo đoạn trích [S1], phản ứng của những con khỉ trước sự xao lãng khác nhau như thế nào giữa giai đoạn chưa hình thành thói quen và sau khi thói quen mong đợi phần thưởng đã được thiết lập?",
        answer="Đối với những con khỉ chưa phát triển thói quen bền vững, các yếu tố xao lãng như mở cửa phòng hoặc để sẵn thức ăn ở góc tỏ ra có hiệu quả; con khỉ rời ghế và không học được sự thèm muốn nước quả [S1]. Ngược lại, một khi thói quen đã được củng cố và não bộ đã hình thành sự mong đợi phần thưởng, các tác nhân gây xao lãng hoàn toàn không còn tác động nào đến hành vi của chúng.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Với khỉ chưa hình thành thói quen bền vững, xao lãng có hiệu quả khiến khỉ rời ghế và không học cách thèm nước quả",
                "source_sentence_ids": ["S1.6", "S1.7", "S1.8"],
                "evidence_spans": ["Đối với những con khỉ không phát triển một thói quen bền vững, sự xao lãng có hiệu quả.", "Nó\ntrượt khỏi cái ghế, ra khỏi phòng và không bao giờ nhìn lại.", "Nó không học để thèm muốn nước\nquả."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Khi thói quen hình thành và não mong đợi phần thưởng, xao lãng không còn ảnh hưởng",
                "source_sentence_ids": ["S1.9"],
                "evidence_spans": ["một khi con khỉ đã hình thành thói quen – một khi não nó mong đợi phần\nthưởng – việc xao lãng không có ảnh hưởng gì."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 5. Duhigg Self-Control Teaching (src_16[4])
    s5 = src_16[4]
    all_records.append(build_item(
        source_id=s5["source_id"],
        chunk_id=s5["chunk_id"],
        book=s5["book"],
        book_title=s5["book_title"],
        page=s5["page"],
        domain=s5["domain"],
        partition=s5["partition"],
        source_text=s5["source_text"],
        question="Theo đoạn trích [S1], các nhà nghiên cứu đã phát hiện ra phương pháp nào giúp trẻ tự kiểm soát và lý thuyết về nghị lực đã phát triển ra sao vào những năm 1980?",
        answer="Các nhà nghiên cứu nhận thấy việc hướng dẫn trẻ các bí quyết đơn giản, như vẽ tranh để tự làm xao lãng hoặc tưởng tượng có khung bao quanh viên kẹo để coi nó như bức ảnh, giúp trẻ học cách tự kiểm soát [S1]. Vào thập niên 1980, giới khoa học chấp nhận lý thuyết coi nghị lực là một kỹ năng có thể học và dạy được như học toán. Tuy nhiên, nguồn tài trợ cho hướng dẫn này rất khan hiếm và chủ đề này từng không thịnh hành khiến nhiều nhà nghiên cứu chuyển hướng.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Dạy trẻ bí quyết đơn giản như vẽ tranh hoặc tưởng tượng khung quanh kẹo dẻo giúp trẻ học tự kiểm soát",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["dạy chúng những bí quyết\nđơn giản – như vẽ một bức tranh để tự làm xao lãng mình, hay tưởng tượng có một cái khung\nxung quanh kẹo dẻo để chúng giống một bức ảnh hơn và ít giống sự cám dỗ hơn – giúp chúng\nhọc cách tự kiểm soát."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Những năm 1980 lý thuyết chấp nhận nghị lực là kỹ năng có thể học được như làm toán",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Vào những năm 1980, một lý thuyết hình thành được chấp nhận rộng\nrãi: Nghị lực là một kỹ năng có thể học được, một điều có thể được dạy giống như cách những\nđứa trẻ học làm toán và nói “cảm ơn.”"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Nguồn tài trợ khan hiếm và chủ đề nghị lực không thịnh hành khiến nhiều nhà khoa học chuyển sang lĩnh vực khác",
                "source_sentence_ids": ["S1.3", "S1.4", "S1.5"],
                "evidence_spans": ["nguồn tài trợ cho những hướng dẫn đó rất hiếm.", "Chủ đề nghị lực không thịnh hành.", "Nhiều nhà khoa học của Stanford chuyển sang các lĩnh vực\nnghiên cứu khác."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 6. Duhigg Consumer Data Modeling (src_16[5])
    s6 = src_16[5]
    all_records.append(build_item(
        source_id=s6["source_id"],
        chunk_id=s6["chunk_id"],
        book=s6["book"],
        book_title=s6["book_title"],
        page=s6["page"],
        domain=s6["domain"],
        partition=s6["partition"],
        source_text=s6["source_text"],
        question="Theo đoạn trích [S1], phòng phân tích dữ liệu tại Target đã sử dụng các thuật toán máy tính như thế nào để dự báo nhu cầu và gửi ưu đãi định kỳ cho khách hàng?",
        answer="Vào năm 2002, phòng phân tích tại Target đã xây dựng các chương trình máy tính để nhận diện những hộ gia đình có con nhỏ, qua đó gửi danh sách quảng cáo xe đạp vào tháng 11, phiếu mua dụng cụ học tập vào tháng 9 và đồ chơi hồ bơi vào tháng 6 [S1]. Hệ thống cũng phát hiện những người mua đồ bơi vào tháng 4 để tiếp thị phiếu mua kem chống nắng vào tháng 7 và sách giảm cân vào tháng 12.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Target năm 2002 xây dựng chương trình máy tính xác định hộ có con nhỏ để gửi ưu đãi định kỳ theo tháng",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Vào lúc Pole gia nhập Target năm 2002, phòng phân tích vừa xây dựng các chương trình máy\ntính để xác định các hộ gia đình có con nhỏ", "gửi cho các bậc cha\nmẹ danh sách xe đạp và xe hai bánh cho trẻ con trông thật hoàn hảo dưới cây thông Giáng\nsinh, cũng như phiếu mua hàng dụng cụ học tập vào tháng Chín và quảng cáo đồ chơi trong hồ\nbơi vào tháng Sáu."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Máy tính tìm người mua đồ bơi tháng 4 để gửi phiếu kem chống nắng tháng 7 và sách giảm cân tháng 12",
                "source_sentence_ids": ["S1.3"],
                "evidence_spans": ["Các máy tính tìm kiếm những người mua sắm mua đồ bơi vào tháng Tư và\ngửi cho họ phiếu mua hàng kem chống nắng vào tháng Bảy và sách giảm cân vào tháng Mười\nhai."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 7. Attached Romantic Affiliation (src_16[6])
    s7 = src_16[6]
    all_records.append(build_item(
        source_id=s7["source_id"],
        chunk_id=s7["chunk_id"],
        book=s7["book"],
        book_title=s7["book_title"],
        page=s7["page"],
        domain=s7["domain"],
        partition=s7["partition"],
        source_text=s7["source_text"],
        question="Theo đoạn trích [S1], lý thuyết gắn bó (attachment theory) khác biệt với các can thiệp mối quan hệ truyền thống như thế nào và có phạm vi ứng dụng ra sao?",
        answer="Khác với các biện pháp can thiệp quan hệ thông thường vốn chỉ tập trung vào người độc thân hoặc các cặp đôi đã gắn kết, lý thuyết gắn bó là một khung lý thuyết bao quát về liên kết tình cảm lãng mạn áp dụng cho mọi giai đoạn đời sống tình cảm [S1]. Các tác giả đã xây dựng phương pháp Gắn bó Người lớn Ứng dụng (Applied Adult Attachment), cung cấp các chỉ dẫn cụ thể cho người đang hẹn hò, người mới bắt đầu quan hệ, người gắn bó lâu năm, cũng như người trải qua chia tay hay mất mát người thân.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Lý thuyết gắn bó là lý thuyết bao quát về liên kết lãng mạn áp dụng cho mọi giai đoạn, khác với can thiệp chỉ nhắm vào người độc thân hoặc cặp đôi",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["unlike other relationship interventions that mostly \nfocus either on singles or existing couples, attachment theory is an \noverarching theory of romantic affiliation that allows for the development of useful applications for people in all stages of their \nromantic life."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Phương pháp Applied Adult Attachment có ứng dụng cụ thể từ hẹn hò, quan hệ lâu năm tới chia tay hoặc mất người thân",
                "source_sentence_ids": ["S1.1", "S1.3"],
                "evidence_spans": ["We created what we call Applied Adult Attachment, a methodology that is relevant for a variety of relationship situations.", "There are specific applications for people who are \ndating, those in early stages of relationships, and those who are in \nlong-term ones, for people going through a breakup or those who \nare grieving the loss of a loved one."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 8. Attached Physiological Unit (src_16[7])
    s8 = src_16[7]
    all_records.append(build_item(
        source_id=s8["source_id"],
        chunk_id=s8["chunk_id"],
        book=s8["book"],
        book_title=s8["book_title"],
        page=s8["page"],
        domain=s8["domain"],
        partition=s8["partition"],
        source_text=s8["source_text"],
        question="Theo đoạn trích [S1], khi hai người lựa chọn gắn bó với nhau, tính phụ thuộc sinh học và sự liên kết sinh lý giữa họ diễn ra như thế nào?",
        answer="Khi một người đã lựa chọn một đối tượng gắn bó đặc biệt, sự phụ thuộc lẫn nhau luôn tất yếu xuất hiện bất kể mức độ độc lập hay ý chí có ý thức của họ [S1]. Đoạn trích chỉ ra rằng một mối quan hệ không có cảm giác dễ bị tổn thương hay sợ mất mát là trái với bản chất sinh học con người. Qua tiến hóa, việc hai người trở thành một đơn vị sinh lý thống nhất mang lại lợi thế sinh tồn, khiến trạng thái cảm xúc của một bên trực tiếp làm xao động người còn lại.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Khi chọn đối tượng đặc biệt, sự phụ thuộc luôn xuất hiện bất chấp ý chí có ý thức hay mức độ độc lập",
                "source_sentence_ids": ["S1.2", "S1.3", "S1.4", "S1.5"],
                "evidence_spans": ["once we choose someone special, powerful and often \nuncontrollable forces come into play.", "regardless of how independent we are and despite our conscious wills.", "Once we choose a partner, there is no question about \nwhether dependency exists or not.", "It always does."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Chung sống thanh tao không có tổn thương và sợ mất mát là không phù hợp với sinh học",
                "source_sentence_ids": ["S1.6"],
                "evidence_spans": ["An elegant coexistence that does not include uncomfortable feelings of vulnerability and fear of loss sounds good but is not our biology."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Tiến hóa chứng minh cặp đôi trở thành một đơn vị sinh lý tạo lợi thế sinh tồn, khiến phản ứng của người này làm người kia bất an",
                "source_sentence_ids": ["S1.7"],
                "evidence_spans": ["What \nproved through evolution to have a strong survival advantage is a \nhuman couple becoming one physiological unit", "which means \nthat if she’s reacting, then I’m reacting, or if he’s upset, that also \nmakes me unsettled."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 9. Feist Freud Censor & Rooms (src_16[8])
    s9 = src_16[8]
    all_records.append(build_item(
        source_id=s9["source_id"],
        chunk_id=s9["chunk_id"],
        book=s9["book"],
        book_title=s9["book_title"],
        page=s9["page"],
        domain=s9["domain"],
        partition=s9["partition"],
        source_text=s9["source_text"],
        question="Theo đoạn trích [S1], Freud đã sử dụng ẩn dụ về sảnh vào, phòng tiếp khách và người gác cổng như thế nào để mô tả mối quan hệ giữa vô thức, tiền ý thức và ý thức?",
        answer="Trong ẩn dụ của Freud, sảnh đón khách đại diện cho các hình ảnh vô thức, trong khi căn phòng tiếp khách nhỏ đại diện cho tiền ý thức với các ý niệm tiền ý thức [S1]. Vị khách quan trọng ngồi phía sau bức bình phong ở cuối phòng đại diện cho con mắt của ý thức. Người gác cổng canh giữ ngưỡng cửa giữa hai căn phòng đóng vai trò như cơ chế kiểm duyệt sơ cấp, ngăn cản các hình ảnh vô thức bước vào tiền ý thức và đẩy lùi những ý niệm tiền ý thức trở lại vô thức.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Sảnh đón khách đại diện cho hình ảnh vô thức còn phòng tiếp khách đại diện cho tiền ý thức",
                "source_sentence_ids": ["S1.3", "S1.4"],
                "evidence_spans": ["The people in the entrance hall represent unconscious \nimages.", "The small reception room is the preconscious and its inhabitants represent \npreconscious ideas."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Vị khách quan trọng ở cuối phòng đại diện cho con mắt ý thức",
                "source_sentence_ids": ["S1.5"],
                "evidence_spans": ["the important guest who, of course, represents the eye of con­\nsciousness."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Người gác cổng giữa hai phòng là cơ chế kiểm duyệt sơ cấp ngăn vô thức lên tiền ý thức và đẩy lùi ý niệm",
                "source_sentence_ids": ["S1.6"],
                "evidence_spans": ["The doorkeeper who guards the threshold between the two rooms is \nthe primary censor that prevents unconscious images from becoming preconscious \nand renders preconscious images unconscious by throwing them back."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 10. Feist Self-Actualization (src_16[9])
    s10 = src_16[9]
    all_records.append(build_item(
        source_id=s10["source_id"],
        chunk_id=s10["chunk_id"],
        book=s10["book"],
        book_title=s10["book_title"],
        page=s10["page"],
        domain=s10["domain"],
        partition=s10["partition"],
        source_text=s10["source_text"],
        question="Theo đoạn trích [S1], những đặc điểm tâm lý nổi bật nào thể hiện sự chấp nhận bản thân, người khác và sự bất định ở những người tự hiện thực hóa bản thân?",
        answer="Những người tự hiện thực hóa bản thân sẵn sàng đón nhận sự nghi ngờ, tính bất định và những nẻo đường chưa ai khai phá, khiến họ rất thích hợp với vai trò triết gia, nhà thám hiểm hoặc nhà khoa học [S1]. Họ có khả năng chấp nhận bản thân như vốn có, không có thái độ phòng vệ, giả tạo hay cảm giác tội lỗi tự hủy hoại, và không bị đè nặng bởi sự lo âu hay hổ thẹn quá mức. Tương tự, họ chấp nhận người khác mà không có nhu cầu mang tính cưỡng chế phải giáo huấn, thông tri hay cải biến người khác.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Người tự hiện thực hóa đón nhận nghi ngờ và bất định, phù hợp làm triết gia, nhà thám hiểm hay khoa học",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["They welcome doubt, uncertainty, indefiniteness, and uncharted paths, a \nquality that makes self-actualizing people particularly well suited to be philosophers, \nexplorers, or scientists."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Họ chấp nhận bản thân, thiếu tính phòng thủ, không giả tạo hay mặc cảm tội lỗi tự hủy hoại và không quá lo âu",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Self-actualizing people can accept themselves the way they are.", "They lack defensive­\nness, phoniness, and self-defeating guilt", "are not burdened \nby undue anxiety or shame."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Họ chấp nhận người khác và không có nhu cầu cưỡng chế phải chỉ dẫn hay cải biến người khác",
                "source_sentence_ids": ["S1.4"],
                "evidence_spans": ["they accept others and have no com­\npulsive need to instruct, inform, or convert."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 11. Dweck Mindset Learning (src_16[10])
    s11 = src_16[10]
    all_records.append(build_item(
        source_id=s11["source_id"],
        chunk_id=s11["chunk_id"],
        book=s11["book"],
        book_title=s11["book_title"],
        page=s11["page"],
        domain=s11["domain"],
        partition=s11["partition"],
        source_text=s11["source_text"],
        question="Theo đoạn trích [S1], sự khác biệt giữa người có tư duy cố định và người có tư duy phát triển được thể hiện như thế nào trong việc tự đánh giá năng lực của bản thân?",
        answer="Đối với người có định hướng học hỏi, thông tin chính xác về năng lực hiện tại là điều kiện thiết yếu để tiến bộ [S1]. Trái lại, ở những người có tư duy cố định, việc coi mọi kết quả là tin tốt hay tin xấu về các phẩm chất quý giá của mình tất yếu dẫn tới sự bóp méo nhận thức; một số kết quả bị phóng đại trong khi số khác bị gạt đi khiến họ không còn hiểu đúng về chính mình. Đoạn trích viện dẫn kết luận của Howard Gardner rằng các cá nhân kiệt xuất có tài năng đặc biệt trong việc nhận diện điểm mạnh và điểm yếu, và những người có tư duy phát triển dường như sở hữu năng lực này.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Người hướng tới học hỏi cần thông tin chính xác về năng lực hiện tại để học tập hiệu quả",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["if\nyou’re oriented toward learning, as they are, you need accurate information\nabout your current abilities in order to learn effectively."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Ở người tư duy cố định sự bóp méo xuất hiện khiến kết quả bị phóng đại hoặc thoái thác và không hiểu rõ bản thân",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["if everything\nis either good news or bad news about your precious traits—as it is with fixedmindset people—distortion almost inevitably enters the picture.", "Some outcomes\nare magnified, others are explained away, and before you know it you don’t\nknow yourself at all."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Gardner kết luận người kiệt xuất có tài nhận diện điểm mạnh điểm yếu và người tư duy phát triển dường như có tài năng đó",
                "source_sentence_ids": ["S1.4", "S1.5"],
                "evidence_spans": ["exceptional individuals have “a special talent for identifying their own strengths\nand weaknesses.”", "those with the growth mindset seem to\nhave that talent."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 12. Bennett 4 Clinical Paradigms (src_16[11])
    s12 = src_16[11]
    all_records.append(build_item(
        source_id=s12["source_id"],
        chunk_id=s12["chunk_id"],
        book=s12["book"],
        book_title=s12["book_title"],
        page=s12["page"],
        domain=s12["domain"],
        partition=s12["partition"],
        source_text=s12["source_text"],
        question="Theo đoạn trích [S1], bốn trường phái liệu pháp tâm lý chính kể từ cuối thế kỷ 19 giải thích nguyên nhân gây ra các vấn đề sức khỏe tâm thần như thế nào?",
        answer="Kể từ cuối thế kỷ 19, bốn trường phái liệu pháp tâm lý chính đưa ra các cách giải thích khác nhau về nguyên nhân bệnh lý tâm thần [S1]. Phân tâm học xem chấn thương tâm lý tuổi thơ và vô thức là nguồn gốc vấn đề ở tuổi trưởng thành; trường phái hành vi cho rằng tâm bệnh bắt nguồn từ các quá trình điều kiện hóa (conditioning). Trường phái nhận thức hoặc hành vi nhận thức quy định nguyên nhân là do những nhận thức lệch lạc và không thích hợp, trong khi tâm lý học nhân văn coi tâm bệnh là hệ quả của việc cá nhân đi lệch khỏi con đường tự thể hiện (self-actualization).",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Từ cuối thế kỷ 19 có 4 trường phái liệu pháp giải thích khác nhau về nguyên nhân vấn đề sức khỏe tâm thần",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Kể từ cuối thế ky 19, có 4 trường phái liệu pháp tâm lí chính, mỗi trường phái lại có\nmột cách giải thích khác nhau về nguyên nhân của các vấn dé SKTT:"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Phân tâm học quy về chấn thương tuổi thơ và vô thức, còn trường phái hành vi quy về điều kiện hóa",
                "source_sentence_ids": ["S1.3", "S1.4"],
                "evidence_spans": ["Phân tâm học coi những chấn thương tâm lí từ tuổi thơ và vô thức là nguyên\nnhân gây ra các vấn đề tâm lí ở tuổi trưởng thành.", "Trường phái hành vi thì cho rằng tâm bệnh xuất phát từ các quá trình điều\nkiện hoá (conditioning)."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Nhận thức quy về nhận thức sai lệch, còn nhân văn quy về việc lệch khỏi con đường tự thể hiện",
                "source_sentence_ids": ["S1.5", "S1.6"],
                "evidence_spans": ["Trường phái nhận thức hay hành vi nhận thức giả định rằng yếu tố quy định\ntâm bệnh là những nhận thức không thích hợp và lệch lạc của con người.", "Tâm lí học nhân văn xem tâm bệnh là hậu quả của việc cá nhân đi lệch khỏi\ncon đường tự thể hiện (self-actualization)."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 13. Van der Kolk Trauma & Action (src_16[12])
    s13 = src_16[12]
    all_records.append(build_item(
        source_id=s13["source_id"],
        chunk_id=s13["chunk_id"],
        book=s13["book"],
        book_title=s13["book_title"],
        page=s13["page"],
        domain=s13["domain"],
        partition=s13["partition"],
        source_text=s13["source_text"],
        question="Theo đoạn trích [S1], quan sát của Pierre Janet về phản ứng của người sống sót sau sang chấn là gì và khả năng hành động tự vệ có ý nghĩa ra sao đối với vết thương tâm lý?",
        answer="Từ năm 1889, nhà tâm lý học Pierre Janet đã chỉ ra rằng những người sống sót sau sang chấn thường có xu hướng tiếp tục hành động, hoặc tiếp tục những nỗ lực hành động vô ích đã khởi phát tại thời điểm biến cố xảy ra [S1]. Khả năng có thể cử động và hành động để tự bảo vệ bản thân là yếu tố then chốt quyết định liệu trải nghiệm kinh hoàng có để lại vết sẹo tâm lý lâu dài hay không. Đoạn trích nhấn mạnh khoa học thần kinh ngày càng chứng minh não bộ là một mạng lưới rộng lớn của các cấu phần liên kết được tổ chức nhằm giúp con người sinh tồn và phát triển.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Từ 1889 Pierre Janet chỉ ra người sống sót sau sang chấn có xu hướng tiếp tục hành động hoặc nỗ lực hành động vô ích khởi phát lúc xảy ra biến cố",
                "source_sentence_ids": ["S1.1"],
                "evidence_spans": ["Since at least 1889, when the French\npsychologist Pierre Janet published the first scientific account of traumatic stress,3 it has been\nrecognized that trauma survivors are prone to “continue the action, or rather the (futile) attempt at\naction, which began when the thing happened.”"],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Khả năng hành động bảo vệ bản thân quyết định trải nghiệm có để lại vết sẹo lâu dài hay không",
                "source_sentence_ids": ["S1.2"],
                "evidence_spans": ["Being able to move and do something to protect\noneself is a critical factor in determining whether or not a horrible experience will leave long-lasting\nscars."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Não bộ là mạng lưới rộng lớn các bộ phận liên kết nhằm giúp sinh tồn và phát triển",
                "source_sentence_ids": ["S1.4"],
                "evidence_spans": ["it is a vast network of interconnected parts\norganized to help us survive and flourish."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 14. Viktor Frankl Meaning in Suffering (src_16[13])
    s14 = src_16[13]
    all_records.append(build_item(
        source_id=s14["source_id"],
        chunk_id=s14["chunk_id"],
        book=s14["book"],
        book_title=s14["book_title"],
        page=s14["page"],
        domain=s14["domain"],
        partition=s14["partition"],
        source_text=s14["source_text"],
        question="Theo đoạn trích [S1], trong hoàn cảnh lao động khổ sai khắc nghiệt, tác giả đã trải nghiệm sự giác ngộ tâm lý nào về bản chất của tình yêu và ý nghĩa nội tâm?",
        answer="Trong điều kiện lao động khổ sai giá lạnh tại chiến hào nơi mọi người im lặng và suy nghĩ dường như tê cóng, hình ảnh người vợ luôn hiện diện trong tâm trí tác giả [S1]. Dù không biết vợ mình có còn sống hay không, tác giả nhận ra một chân lý sâu sắc rằng tình yêu không chỉ gắn liền với sự hiện hữu thể xác của một con người. Tình yêu tìm thấy ý nghĩa sâu sắc nhất trong tâm trí và chính nội tâm con người, bất kể đối tượng được yêu có thực sự còn tồn tại trên đời hay không.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Trong hào lao động mặt đất đông cứng và mọi người im lặng tê cóng, hình ảnh người vợ không phai mờ trong tâm trí",
                "source_sentence_ids": ["S1.1", "S1.2", "S1.3", "S1.4"],
                "evidence_spans": ["quay lại vị trí làm việc trong hào của mình.", "Mọi người đều\nim lặng, dường như mọi suy nghĩ của họ đều bị tê cóng.", "Hình ảnh về người vợ thân yêu chưa lúc nào phai mờ trong tâm trí tôi."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Dù không biết nàng còn sống hay không, tác giả hiểu rằng tình yêu không chỉ gắn với thể xác",
                "source_sentence_ids": ["S1.5", "S1.6"],
                "evidence_spans": ["Tôi thậm chí không biết nàng có còn sống hay\nkhông.", "Tình yêu không\nchỉ gắn liền với sự hiện hữu của thể xác."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Tình yêu tìm thấy ý nghĩa sâu sắc nhất trong nội tâm cho dù người ấy có còn tồn tại hay không",
                "source_sentence_ids": ["S1.7", "S1.8"],
                "evidence_spans": ["Tình yêu tìm thấy ý nghĩa sâu sắc\nnhất trong tâm trí, trong chính nội tâm của con người.", "Cho dù người ấy có\nthực sự tồn tại, có còn sống hay không cũng không quan trọng."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 15. Phelps Mind Brain & Behavior (src_16[14])
    s15 = src_16[14]
    all_records.append(build_item(
        source_id=s15["source_id"],
        chunk_id=s15["chunk_id"],
        book=s15["book"],
        book_title=s15["book_title"],
        page=s15["page"],
        domain=s15["domain"],
        partition=s15["partition"],
        source_text=s15["source_text"],
        question="Theo đoạn trích [S1], khoa học tâm lý phân biệt và liên kết các khái niệm 'tâm trí' (mind), 'hoạt động thần kinh' và 'hành vi' (behavior) như thế nào?",
        answer="Tâm trí (mind) dùng để chỉ hoạt động tinh thần, bao gồm ký ức, suy nghĩ, cảm xúc và các trải nghiệm tri giác nảy sinh khi con người tương tác với thế giới [S1]. Hoạt động tinh thần này là kết quả trực tiếp của các quá trình sinh hóa diễn ra bên trong não bộ. Trong khi đó, hành vi (behavior) mô tả toàn bộ các hành động có thể quan sát được của con người hoặc động vật, từ những hành vi phổ quát ở mọi loài như ăn uống cho đến các hành động phức tạp chỉ có ở người như tranh luận triết học hay phẫu thuật.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Tâm trí chỉ hoạt động tinh thần gồm ký ức, suy nghĩ, cảm xúc và tri giác khi tương tác thế giới",
                "source_sentence_ids": ["S1.2", "S1.3"],
                "evidence_spans": ["Mind refers to mental activity.", "The mind includes the memories, thoughts, feelings, and\nperceptual experiences (sights, smells, tastes, sounds, and touches) we have while\ninteracting with the world."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Hoạt động tinh thần bắt nguồn từ các quá trình sinh hóa trong não",
                "source_sentence_ids": ["S1.4"],
                "evidence_spans": ["Mental activity results from biochemical processes within\nthe brain."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.3",
                "claim_text": "Hành vi là toàn bộ hành động quan sát được, từ ăn uống ở mọi loài đến hành động độc nhất ở người như tranh luận triết học",
                "source_sentence_ids": ["S1.5", "S1.6", "S1.7", "S1.8"],
                "evidence_spans": ["Behavior describes the totality of observable human (or animal) actions.", "These actions range from the subtle to the complex.", "Some occur exclusively in humans,\nsuch as debating philosophy or performing surgery.", "Others occur in all animals, such as\neating and drinking."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    # 16. Blumenfeld Nervous System Levels (src_16[15])
    s16 = src_16[15]
    all_records.append(build_item(
        source_id=s16["source_id"],
        chunk_id=s16["chunk_id"],
        book=s16["book"],
        book_title=s16["book_title"],
        page=s16["page"],
        domain=s16["domain"],
        partition=s16["partition"],
        source_text=s16["source_text"],
        question="Theo đoạn trích [S1], hệ thần kinh thực hiện việc xử lý thông tin với những đặc tính mạng lưới nào và cấu trúc của nó được mô tả ở những cấp độ nào?",
        answer="Hệ thần kinh là hệ thống phức tạp và tinh vi nhất trong cơ thể, với các mạng lưới liên kết thực hiện việc xử lý thông tin đồng thời mang tính cục bộ và phân tán, tuần tự và song song, thứ bậc và toàn thể [S1]. Cấu trúc của hệ thần kinh được mô tả trên nhiều cấp độ: từ các phân khu não đại thể, các con đường dẫn truyền kết nối và nhóm tế bào, đến các tế bào não riêng lẻ, và cuối cùng là các thụ thể, chất dẫn truyền thần kinh cùng các phân tử phát tín hiệu.",
        claims_spec=[
            {
                "answer_sentence_id": "A1.1",
                "claim_text": "Hệ thần kinh thực hiện xử lý thông tin đồng thời cục bộ và phân tán, tuần tự và song song, thứ bậc và toàn thể",
                "source_sentence_ids": ["S1.1", "S1.2"],
                "evidence_spans": ["The nervous system is perhaps the most beautiful, elegant, and \ncomplex system in the body.", "Its interconnected networks perform \nprocessing that is simultaneously local and distributed, serial and \nparallel, hierarchical and global."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            },
            {
                "answer_sentence_id": "A1.2",
                "claim_text": "Cấu trúc hệ thần kinh được mô tả ở nhiều cấp độ từ phân khu đại thể, đường dẫn truyền, tế bào đơn lẻ đến thụ thể và chất dẫn truyền thần kinh",
                "source_sentence_ids": ["S1.3"],
                "evidence_spans": ["structures of the nervous system can be described on multiple levels: in terms of macroscopic brain divisions; connecting pathways and cell groupings; \nindividual brain cells; and, ultimately, receptors, neurotransmitters, \nand other signaling molecules."],
                "relation": "entailment", "modality": "asserted", "claim_type": "fact"
            }
        ]
    ))

    logger.info("Successfully built and validated all 16 new drafts. Total records: %d", len(all_records))
    return all_records


def main():
    records = build_all_records()
    if len(records) != 22:
        raise ValueError(f"Expected 22 records (16 drafts + 6 controls), got {len(records)}")

    out_dir = BASE_DIR / "data" / "training" / "v7_pending_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "pending_drafts.jsonl"

    with open(out_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    logger.info("Successfully wrote %d validated records to %s", len(records), out_file)

    # Summary by domain and partition
    by_domain = {}
    by_partition = {}
    for r in records:
        by_domain[r["domain"]] = by_domain.get(r["domain"], 0) + 1
        by_partition[r["partition"]] = by_partition.get(r["partition"], 0) + 1

    print("Summary:")
    print("  Total records:", len(records))
    print("  Controls:", sum(1 for r in records if r["is_control"]))
    print("  New drafts:", sum(1 for r in records if not r["is_control"]))
    print("  By domain:", by_domain)
    print("  By partition:", by_partition)


if __name__ == "__main__":
    main()
