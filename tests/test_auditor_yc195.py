"""Malformed auditor responses cannot certify factual support."""
import json

import pytest

from app.reasoning_policy import audit_draft_answer
from app.rag_agent import PsychologyAgent
from test_study_agent_c_f_g import RecordingSearcher, RecordingModel, NoTranslation, make_chunk


SOURCE = 'Stack thêm và lấy phần tử ở đỉnh, theo nguyên tắc LIFO.'


@pytest.mark.parametrize('claims', [
    [None],
    [123],
    [{'statement': '', 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'supported'}],
    [{'statement': 'Stack dùng LIFO.', 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'not-a-verdict'}],
    [{'statement': ['Stack'], 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'supported'}],
])
def test_invalid_claim_schema_is_never_verified(claims):
    status, _ = audit_draft_answer(json.dumps({'claims': claims, 'overall_verdict': 'supported'}, ensure_ascii=False), {'S1'}, {'S1': SOURCE})
    assert status != 'verified'


@pytest.mark.parametrize('source_id,quote', [('S99', SOURCE), ('S1', 'Stack dùng FIFO.'), ('S1', '')])
def test_forged_source_or_quote_is_never_verified(source_id, quote):
    status, _ = audit_draft_answer(json.dumps({'claims': [{'statement': 'Stack dùng LIFO.', 'source_id': source_id, 'evidence_quote': quote, 'verdict': 'supported'}], 'overall_verdict': 'supported'}, ensure_ascii=False), {'S1'}, {'S1': SOURCE})
    assert status != 'verified'


def test_supported_claim_with_admitted_evidence_remains_eligible():
    status, claims = audit_draft_answer(json.dumps({'claims': [{'statement': 'Stack dùng LIFO.', 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'supported'}], 'overall_verdict': 'supported'}, ensure_ascii=False), {'S1'}, {'S1': SOURCE})
    assert status == 'verified'
    assert len(claims) == 1


def test_unclosed_json_fence_is_not_a_complete_certificate():
    raw = json.dumps({'claims': [{'statement': 'Stack dùng LIFO.', 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'supported'}], 'overall_verdict': 'supported'}, ensure_ascii=False)
    status, _ = audit_draft_answer('```json\n' + raw, {'S1'}, {'S1': SOURCE})
    assert status != 'verified'


class SequenceModel(RecordingModel):
    def __init__(self, replies):
        super().__init__()
        self.replies = iter(replies)

    def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        self.last_done_reason = "stop"
        yield next(self.replies)


def test_auditor_cannot_certify_by_omitting_a_false_claim_from_draft():
    draft = 'Stack dùng LIFO. [S1] Queue cũng dùng LIFO. [S2]'
    audit = json.dumps({'claims': [{'statement': 'Stack dùng LIFO.', 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'supported'}], 'overall_verdict': 'supported'}, ensure_ascii=False)
    model = SequenceModel([draft, audit])
    agent = PsychologyAgent(searcher=RecordingSearcher([make_chunk(SOURCE, semantic_score=.99), make_chunk('Queue dùng FIFO: phần tử vào trước được lấy trước.', semantic_score=.99)]), ollama=model, translator=NoTranslation())
    result = agent.process_query_sync('So sánh Stack và Queue', embed_model=None, model='test-model', processing_level='careful')
    assert result.get('check_status') != 'verified'
    assert len(model.calls) <= 2


def test_explicitly_unsupported_audit_does_not_return_draft_as_an_answer():
    draft = 'Stack dùng FIFO. [S1]'
    audit = json.dumps({'claims': [{'statement': 'Stack dùng FIFO.', 'source_id': 'S1', 'evidence_quote': SOURCE, 'verdict': 'unsupported'}], 'overall_verdict': 'unsupported'}, ensure_ascii=False)
    model = SequenceModel([draft, audit])
    result = PsychologyAgent(searcher=RecordingSearcher([make_chunk(SOURCE, semantic_score=.99)]), ollama=model, translator=NoTranslation()).process_query_sync('Stack lấy phần tử theo nguyên tắc nào?', embed_model=None, model='test-model', processing_level='careful')
    assert result['response_kind'] != 'answer'
    assert 'FIFO' not in result['answer']
    assert len(model.calls) <= 2
