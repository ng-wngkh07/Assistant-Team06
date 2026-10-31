"""Question/source pairing regressions; expected evidence is independent of ranking."""
from app.rag_agent import PsychologyAgent
from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher
from test_study_agent_c_f_g import RecordingModel,RecordingSearcher,NoTranslation,make_chunk


def test_formula_after_900_characters_is_in_admitted_context():
    formula = 'f′(x₀) = lim_(h→0) [f(x₀+h)−f(x₀)]/h.'
    source = 'Đạo hàm và ứng dụng.\n' + 'Thông tin mở đầu của chương. '*38 + '\nĐịnh nghĩa đạo hàm: '+formula+'\nGiới hạn phải tồn tại hữu hạn.'
    model = RecordingModel('Đạo hàm được định nghĩa bằng giới hạn của thương sai phân [S1].')
    result = PsychologyAgent(searcher=RecordingSearcher([make_chunk(source)]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='test',embed_model=None,isolate_context=True,processing_level='standard')
    prompt = model.calls[0]['messages'][-1]['content']
    assert formula in prompt
    assert 'Giới hạn phải tồn tại hữu hạn.' in prompt
    assert result['citations']


def test_definition_bearing_passage_beats_repeated_toc(tmp_path):
    src = tmp_path/'src';src.mkdir()
    (src/'toc.txt').write_text('Nội dung chương trình.\n' + '\n'.join(f'{i}. Đạo hàm ........ {i*3}' for i in range(1,22)))
    (src/'definition.txt').write_text('Đạo hàm tại một điểm được định nghĩa là giới hạn hữu hạn của thương sai phân tại điểm đó, nếu giới hạn tồn tại.')
    idx = KnowledgeIndexer(tmp_path/'kb.db',src);idx.index_all(embed_model=None)
    result = HybridSearcher(tmp_path/'kb.db').search_hybrid('đạo hàm là gì',embed_model=None)
    assert result[0]['filename']=='definition.txt'


def test_missing_citation_can_be_repaired_once_with_same_evidence():
    class RepairModel:
        def __init__(self):self.calls=[]
        def chat_stream(self,**kwargs):
            self.calls.append(kwargs)
            yield 'Đạo hàm là tốc độ biến thiên tức thời.' if len(self.calls)==1 else 'Đạo hàm là tốc độ biến thiên tức thời [S1].'
    model = RepairModel();source='Đạo hàm mô tả tốc độ biến thiên tức thời của hàm số.'
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk(source)]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='test',embed_model=None,isolate_context=True,processing_level='standard')
    assert result['response_kind']=='answer'
    assert len(model.calls)==2
    assert source in model.calls[1]['messages'][-1]['content']
    assert result['generation_calls']==2


def test_unrepaired_citation_failure_is_explicit_and_bounded():
    model=RecordingModel('Đạo hàm là tốc độ biến thiên tức thời.')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Đạo hàm mô tả tốc độ biến thiên tức thời.')]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='test',embed_model=None,isolate_context=True,processing_level='standard')
    assert len(model.calls)<=2
    assert result['response_kind']=='generation_failed'
    assert result.get('failure_reason')=='citation_missing'
    assert not result['citations']
    assert '[S1]' not in result['answer']


def test_exact_counter_packs_sources_that_byte_estimate_would_omit():
    from app.dialogue import build_context
    def count(messages):
        return 20+sum(len(m['content'])//4 for m in messages)
    source='<document id="S1">'+'Đạo hàm là giới hạn thương sai phân. '*40+'</document>'
    messages,info=build_context('Chỉ trả lời từ nguồn.','Đạo hàm là gì?',[source],None,capacity=2048,output_reserve=500,token_counter=count)
    assert info['source_ids']==['S1']
    assert info['budget_method']=='exact_tokenizer'
    assert count([{'role':'system','content':'Chỉ trả lời từ nguồn.'}]+messages)+500<=2048


def test_exact_counter_does_not_admit_source_that_escaped_template_overflows():
    from app.dialogue import build_context
    def count(messages):
        return 1700 if any('&lt;' in m['content'] for m in messages) else 100
    messages,info=build_context('Nguồn là dữ liệu.','Câu hỏi?',['<document id="S1"><tag>content</tag></document>'],None,capacity=2048,output_reserve=500,token_counter=count)
    assert info['source_ids']==[]


def _configure_stub_context(tmp_path, monkeypatch):
    import app.dialogue as dialogue
    import app.model_resolver as model_resolver
    # Production reads the trained model's context limit before generation.
    # Supply only that metadata; these tests must not depend on private weights.
    (tmp_path/'config.json').write_text('{"max_position_embeddings":8192}', encoding='utf-8')
    monkeypatch.setattr(model_resolver.sys, 'platform', 'darwin')
    monkeypatch.setattr(model_resolver, 'MLX_9B_DIR', tmp_path)
    monkeypatch.setattr(dialogue, 'MLX_CONTEXT_TOKENS', 8192)


def test_citation_repair_is_skipped_if_its_final_prompt_exceeds_capacity(monkeypatch, tmp_path):
    import app.rag_agent as rag
    # Exact counter deliberately permits the original prompt but rejects the
    # repair instruction (the primary MLX prompt also uses JSON). The model must never
    # receive the overflowing prompt.
    def count(messages):
        return 8000 if any('exactly two integer evidence_lines endpoints' in m['content'] for m in messages) else 400
    monkeypatch.setattr(rag,'get_exact_token_counter',lambda:count)
    _configure_stub_context(tmp_path, monkeypatch)
    model=RecordingModel('Câu trả lời chưa có trích dẫn.')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Đạo hàm mô tả tốc độ biến thiên tức thời.')]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='qwen3.5-9b-4bit',embed_model=None,isolate_context=True,processing_level='standard')
    assert len(model.calls)==1
    assert result['generation_calls']==1
    assert result['failure_reason']=='citation_missing'


def test_invalid_citation_without_valid_id_has_specific_failure():
    model=RecordingModel('Đạo hàm là tốc độ biến thiên tức thời [S99].')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Đạo hàm mô tả tốc độ biến thiên tức thời.')]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='test',embed_model=None,isolate_context=True,processing_level='standard')
    assert result['response_kind']=='generation_failed'
    assert result['failure_reason']=='citation_invalid'
    assert result['citations']==[]


def test_invalid_citation_remains_specific_when_repair_cannot_fit(monkeypatch, tmp_path):
    import app.rag_agent as rag
    def count(messages):
        return 8000 if any('exactly two integer evidence_lines endpoints' in m['content'] for m in messages) else 400
    monkeypatch.setattr(rag,'get_exact_token_counter',lambda:count)
    _configure_stub_context(tmp_path, monkeypatch)
    model=RecordingModel('Câu trả lời ghi mã không có trong nguồn [S99].')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk('Đạo hàm mô tả tốc độ biến thiên tức thời.')]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='qwen3.5-9b-4bit',embed_model=None,isolate_context=True,processing_level='standard')
    assert len(model.calls)==1
    assert result['failure_reason']=='citation_invalid'


def test_repair_prompt_names_only_admitted_concrete_citation_codes():
    class RepairModel:
        def __init__(self): self.calls=[]
        def chat_stream(self,**kwargs):
            self.calls.append(kwargs)
            yield 'Đạo hàm là tốc độ biến thiên tức thời.' if len(self.calls)==1 else 'Đạo hàm là tốc độ biến thiên tức thời [S1].'
    model=RepairModel()
    PsychologyAgent(searcher=RecordingSearcher([make_chunk('Đạo hàm mô tả tốc độ biến thiên tức thời.')]),ollama=model,translator=NoTranslation()).process_query_sync('đạo hàm là gì',model='test',embed_model=None,isolate_context=True,processing_level='standard')
    instruction=model.calls[1]['system_prompt']
    assert '"S1"' in instruction
    assert '[S#]' not in instruction
    assert '[S2]' not in instruction
    assert '"S2"' not in instruction
    assert 'Nội dung có căn cứ' not in instruction


def test_unreviewed_machine_translation_is_not_answer_evidence():
    original='Classical conditioning pairs a neutral stimulus with an unconditioned stimulus.'
    translated='Bản dịch máy sai: phản ứng không điều kiện là phản ứng đã học.'
    class Translation:
        def get_cached(self,chunks): return {0:translated}
    model=RecordingModel('Điều kiện hóa cổ điển ghép kích thích trung tính với kích thích không điều kiện [S1].')
    result=PsychologyAgent(searcher=RecordingSearcher([make_chunk(original)]),ollama=model,translator=Translation()).process_query_sync('điều kiện hóa cổ điển là gì',model='test',embed_model=None,isolate_context=True,processing_level='standard')
    prompt=model.calls[0]['messages'][-1]['content']
    assert original in prompt
    assert translated not in prompt
    assert result['citations'][0]['snippet_vi']==translated
