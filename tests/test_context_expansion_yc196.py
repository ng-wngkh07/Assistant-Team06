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
    assert dialogue.context_capacity(dialogue.TRAINED_MODEL_NAME)==expected

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
    assert extract_compare_concepts(query)==('Hệ thống 1','Hệ thống 2')
    model=RecordingModel()
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Hệ thống 1 hoạt động nhanh và tự động.',semantic_score=0)]),ollama=model,translator=NoTranslation()).process_query_sync(query,embed_model=None,model='test',processing_level='standard')
    assert result['response_kind']=='insufficient_evidence'
    assert result['insufficient_reason']=='partial_evidence'
    assert result['search_scope']=={'kind':'library'}
    assert not model.calls


def test_conversational_compare_query_and_vector_orthography_variants():
    from app.rag_agent import extract_compare_concepts, PsychologyAgent
    from app.rag_evidence_validator import concept_has_evidence, detect_partial_evidence
    from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk

    query = "So sánh ngắn gọn sự khác nhau giữa không gian vector và không gian con trong tài liệu."
    concepts = extract_compare_concepts(query)
    assert concepts == ("không gian vector", "không gian con")

    c1_chunk = make_chunk("Định nghĩa. Cho V là một không gian vectơ trên R thỏa 8 tiên đề.", doc_id=63, page_num=3)
    c2_chunk = make_chunk("Định nghĩa. Cho W là một tập con khác rỗng của V gọi là không gian con của V.", doc_id=63, page_num=46)
    assert concept_has_evidence(concepts[0], [c1_chunk]) is True
    assert concept_has_evidence(concepts[1], [c2_chunk]) is True
    assert detect_partial_evidence(query, [c1_chunk, c2_chunk]) is False

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
    assert result["response_kind"] != "insufficient_evidence"
    assert len(result["citations"]) >= 2

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
    assert json_res["response_kind"] == "answer"
    assert len(json_res["citations"]) == 2
