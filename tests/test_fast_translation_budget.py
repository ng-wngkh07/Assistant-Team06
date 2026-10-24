"""Fast QA must not wait for optional citation-preview translations."""
import hashlib
import sqlite3

from app.bilingual import CACHE_VERSION, LocalTranslator
from app.rag_agent import PsychologyAgent
from test_study_agent_c_f_g import RecordingSearcher, make_chunk


def test_fast_reads_cached_translations_without_generating_them():
    source = 'Classical conditioning pairs a neutral stimulus with an unconditioned stimulus.'
    calls = []

    class Translator:
        def get_cached(self, chunks):
            calls.append('cache')
            return {}

        def translate(self, chunks):
            calls.append('generate')
            return {}

    class Model:
        def chat_stream(self, **kwargs):
            assert source in kwargs['messages'][-1]['content']
            yield 'Điều kiện hóa cổ điển ghép kích thích trung tính với kích thích không điều kiện [S1].'

    result = PsychologyAgent(
        searcher=RecordingSearcher([make_chunk(source)]),
        ollama=Model(), translator=Translator(),
    ).process_query_sync('Điều kiện hóa cổ điển là gì?', model='test',
                         embed_model=None, processing_level='standard')
    assert result['response_kind'] == 'answer'
    assert calls == ['cache']
    assert result['citations'][0]['snippet'] == source
    assert result['citations'][0]['snippet_vi'] == ''


def test_cache_only_translation_preserves_content_model_and_quality_binding(tmp_path, monkeypatch):
    source = 'The mind and body respond to trauma, and the nervous system changes with experience and the environment.'
    translated = 'Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi qua trải nghiệm và môi trường.'
    translator = LocalTranslator(tmp_path / 'translations.db', model='local-A')
    monkeypatch.setattr(translator, 'translate', lambda *_: (_ for _ in ()).throw(AssertionError('No generation')))
    chunks = [{'safe_text': source}]
    assert translator.get_cached(chunks) == {}
    key = hashlib.sha256((CACHE_VERSION + '\0local-A\0' + source).encode()).hexdigest()
    with sqlite3.connect(translator.cache_path) as db:
        db.execute('INSERT INTO translations VALUES (?,?)', (key, translated))
    assert translator.get_cached(chunks) == {0: translated}
    assert translator.get_cached([{'safe_text': source + ' Changed.'}]) == {}
    assert LocalTranslator(translator.cache_path, model='local-B').get_cached(chunks) == {}
    with sqlite3.connect(translator.cache_path) as db:
        db.execute('UPDATE translations SET text_vi=?', ('太短',))
    assert translator.get_cached(chunks) == {}
