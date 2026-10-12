"""Deployment window stays within verified model capacity; no weight edit."""
import json
import pytest
from app import dialogue

@pytest.mark.parametrize('requested,model_limit,expected', [(16384,32768,16384),(32768,32768,32768),(16384,8192,8192)])
def test_verified_native_window_can_be_selected(tmp_path,monkeypatch,requested,model_limit,expected):
    (tmp_path/'config.json').write_text(json.dumps({'max_position_embeddings':model_limit}))
    monkeypatch.setattr(dialogue,'MLX_MODEL_DIR',tmp_path)
    monkeypatch.setattr(dialogue,'MLX_CONTEXT_TOKENS',requested,raising=False)
    assert dialogue.context_capacity(dialogue.TRAINED_MODEL_NAME)==expected

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
