"""Deployment window stays within verified model capacity; no weight edit."""
import json
import sys
import pytest
from app import dialogue

@pytest.fixture(autouse=True)
def isolated_model_resolver(tmp_path, monkeypatch):
    from app import model_resolver
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path)

@pytest.mark.skipif(sys.platform != "darwin", reason="Native Mac MLX deployment")
@pytest.mark.parametrize('requested,model_limit,expected', [(16384,32768,16384),(32768,32768,32768),(16384,8192,8192)])
def test_verified_native_window_can_be_selected(tmp_path,monkeypatch,requested,model_limit,expected):
    (tmp_path/'config.json').write_text(json.dumps({'max_position_embeddings':model_limit}))
    monkeypatch.setattr(dialogue,'MLX_MODEL_DIR',tmp_path)
    monkeypatch.setattr(dialogue,'MLX_CONTEXT_TOKENS',requested,raising=False)
    assert dialogue.context_capacity(dialogue.TRAINED_MODEL_NAME)==expected  # nosec B101

@pytest.mark.skipif(sys.platform != "darwin", reason="Native Mac MLX deployment")
def test_unknown_model_capacity_fails_closed(tmp_path,monkeypatch):
    (tmp_path/'config.json').write_text('{}')
    monkeypatch.setattr(dialogue,'MLX_MODEL_DIR',tmp_path)
    with pytest.raises(dialogue.ContextLimitError):
        dialogue.context_capacity(dialogue.TRAINED_MODEL_NAME)

def test_numbered_comparison_keeps_distinct_concept_identifiers():
    from app.rag_agent import extract_compare_concepts, PsychologyAgent
    from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk
    query='Hệ thống 1 và Hệ thống 2 khác nhau thế nào?'
    assert extract_compare_concepts(query)==('Hệ thống 1','Hệ thống 2')  # nosec B101
    model=RecordingModel()
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Hệ thống 1 hoạt động nhanh và tự động.',semantic_score=0)]),ollama=model,translator=NoTranslation()).process_query_sync(query,embed_model=None,model='test',processing_level='standard')
    assert result['response_kind']=='insufficient_evidence'  # nosec B101
    assert result['insufficient_reason']=='partial_evidence'  # nosec B101
    assert result['search_scope']=={'kind':'library'}  # nosec B101
    assert not model.calls  # nosec B101


def test_conversational_compare_query_and_vector_orthography_variants():
    from app.rag_agent import extract_compare_concepts, PsychologyAgent
    from app.rag_evidence_validator import concept_has_evidence, detect_partial_evidence
    from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk

    query = "So sánh ngắn gọn sự khác nhau giữa không gian vector và không gian con trong tài liệu."
    concepts = extract_compare_concepts(query)
    assert concepts == ("không gian vector", "không gian con")  # nosec B101

    c1_chunk = make_chunk("Định nghĩa. Cho V là một không gian vectơ trên R thỏa 8 tiên đề.", doc_id=63, page_num=3)
    c2_chunk = make_chunk("Định nghĩa. Cho W là một tập con khác rỗng của V gọi là không gian con của V.", doc_id=63, page_num=46)
    assert concept_has_evidence(concepts[0], [c1_chunk]) is True  # nosec B101
    assert concept_has_evidence(concepts[1], [c2_chunk]) is True  # nosec B101
    assert detect_partial_evidence(query, [c1_chunk, c2_chunk]) is False  # nosec B101

    model = RecordingModel(
        answer=(
            "| Tiêu chí | Không gian vectơ | Không gian con |\n"
            "|---|---|---|\n"
            "| Định nghĩa | Cho V là một không gian vectơ trên R thỏa 8 tiên đề [S1]. | Cho W là một tập con khác rỗng của V gọi là không gian con của V [S2]. |"
        )
    )
    result = PsychologyAgent(
        searcher=RecordingSearcher([c1_chunk, c2_chunk]),
        ollama=model,
        translator=NoTranslation(),
    ).process_query_sync(query, embed_model=None, model="test", processing_level="standard")
    assert result["response_kind"] != "insufficient_evidence"  # nosec B101
    assert len(result["citations"]) >= 2  # nosec B101

    # Structured 9B JSON claims with verbatim evidence_quote must not fail just because text summarizes
    json_model = RecordingModel(
        answer=__import__("json").dumps(
            {
                "claims": [
                    {
                        "text": "Không gian vectơ V cần thỏa mãn 8 tiên đề về phép cộng và nhân vô hướng.",
                        "source_ids": ["S1"],
                        "evidence_quote": "Cho V là một không gian vectơ trên R thỏa 8 tiên đề.",
                    },
                    {
                        "text": "Không gian con W là tập con khác rỗng của không gian vectơ V.",
                        "source_ids": ["S2"],
                        "evidence_quote": "Cho W là một tập con khác rỗng của V gọi là không gian con của V.",
                    },
                ]
            },
            ensure_ascii=False,
        )
    )
    json_res = PsychologyAgent(
        searcher=RecordingSearcher([c1_chunk, c2_chunk]),
        ollama=json_model,
        translator=NoTranslation(),
    ).process_query_sync(query, embed_model=None, model="test", processing_level="standard")
    assert json_res["response_kind"] == "answer"  # nosec B101
    assert len(json_res["citations"]) == 2  # nosec B101


def test_compare_extraction_glosses_three_item_lists_and_bilingual_translation():
    from decimal import Decimal
    import json
    from app.rag_query_analyzer import extract_compare_concepts, is_broad_query
    from app.rag_evidence_validator import (
        concept_has_evidence,
        extract_quote_supported_numbers,
        extract_quote_written_numbers,
    )
    from app.rag_agent import PsychologyAgent
    from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk

    assert extract_compare_concepts(  # nosec B101
        "Hãy trình bày và phân biệt sensation (bottom-up processing) với perception (top-down processing) là gì?"
    ) == ("sensation", "perception")
    assert extract_compare_concepts(  # nosec B101
        "Phân biệt tập sinh và cơ sở của không gian vector (số chiều, độc lập tuyến tính) để tránh ngộ nhận thừa vectơ?"
    ) == ("tập sinh", "cơ sở của không gian vector")
    assert extract_compare_concepts(  # nosec B101
        "Phân biệt ma trận khả nghịch (det != 0) với ma trận chéo hóa được (diagonalizable) để tránh đánh đồng hai tính chất?"
    ) == ("ma trận khả nghịch", "ma trận chéo hóa được")
    assert extract_compare_concepts(  # nosec B101
        "Khái niệm phylogenetic endowment của Freud và collective unconscious của Carl Jung giống và khác nhau ở điểm cốt lõi nào?"
    ) == ("phylogenetic endowment của Freud", "collective unconscious của Carl Jung")
    assert extract_compare_concepts(  # nosec B101
        "Phân biệt điều kiện cần chuỗi hội tụ (lim a_n = 0) với điều kiện đủ và bẫy Ratio Test ra L = 1?"
    ) == ("điều kiện cần chuỗi hội tụ", "điều kiện đủ")

    three_item_q = "Hãy phân biệt independent variable, dependent variable và extraneous variables trong experimental psychology?"
    assert extract_compare_concepts(three_item_q) is None  # nosec B101
    assert is_broad_query(three_item_q) is True  # nosec B101

    # véc tơ / véctơ normalization
    assert concept_has_evidence("không gian véc tơ", [make_chunk("Cho V là không gian vector trên R.")]) is True  # nosec B101
    assert concept_has_evidence("tập sinh của không gian vector", [make_chunk("S là tập sinh của không gian véctơ V.")]) is True  # nosec B101

    # Written zero phrases and subscript/superscript quote numbers prevent false numeric mismatch retries
    assert Decimal(0) in extract_quote_written_numbers("Ma trận vuông A khả nghịch khi định thức khác không.")  # nosec B101
    assert extract_quote_written_numbers("tài liệu khác không nêu rõ chi tiết nhưng không chỉ vậy") == set()  # nosec B101
    q_nums, _ = extract_quote_supported_numbers("Cho u₁, u₂ ∈ ℝ³ và phần tử tiến về không.")
    assert {Decimal(0), Decimal(1), Decimal(2), Decimal(3)}.issubset(q_nums)  # nosec B101

    # Bilingual JSON compare answer: Vietnamese claim text backed by English verbatim quote passes Call 1 without repair
    c1 = make_chunk("Sensation is bottom-up sensory detection from receptors.", doc_id=10, page_num=110)
    c2 = make_chunk("Perception is top-down interpretation of sensory input.", doc_id=10, page_num=112)
    payload = json.dumps(
        {
            "claims": [
                {
                    "text": "Cảm giác là quá trình tiếp nhận tín hiệu từ cơ quan thụ cảm.",
                    "source_ids": ["S1"],
                    "evidence_quote": "Sensation is bottom-up sensory detection from receptors.",
                },
                {
                    "text": "Tri giác là quá trình diễn giải thông tin cảm giác.",
                    "source_ids": ["S2"],
                    "evidence_quote": "Perception is top-down interpretation of sensory input.",
                },
            ]
        },
        ensure_ascii=False,
    )
    model = RecordingModel(answer=payload)
    res = PsychologyAgent(
        searcher=RecordingSearcher([c1, c2]),
        ollama=model,
        translator=NoTranslation(),
    ).process_query_sync(
        "Hãy trình bày và phân biệt sensation (bottom-up processing) với perception (top-down processing) là gì?",
        embed_model=None,
        model="test",
        processing_level="standard",
    )
    assert res["response_kind"] == "answer"  # nosec B101
    assert len(model.calls) == 1  # nosec B101
    assert len(res["citations"]) == 2  # nosec B101
