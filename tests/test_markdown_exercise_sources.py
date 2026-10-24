"""Numbered Markdown exercises retain body provenance across indexed sections."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.learning import LearningService


@pytest.mark.parametrize('split', [True, False])
@pytest.mark.parametrize('head,answer', [('Bài tập 1','Đáp án bài 1'), ('Exercise 1','Answer 1'), ('Problem 1','Solution 1')])
def test_numbered_markdown_headers_pair_only_their_own_content(split, head, answer):
    texts = [f'## {head}\nTính 2+3.', f'## {answer}\n5.', '## Lý thuyết\nKhông phải đáp án.']
    if split:
        texts = [part for text in texts for part in text.split('\n')]
    chunks = [dict(id=i+1, page_num=i+1, text=text) for i,text in enumerate(texts)]
    service = LearningService(SimpleNamespace(pdf_path=lambda _:None), SimpleNamespace())
    exercises, answers, counts = service._extract_exercises_and_answers(iter(chunks), {'id':17,'filename':'public.md'})
    assert len(exercises)==1 and exercises[0]['id']=='1'
    assert 'Tính 2+3.' in exercises[0]['question']
    assert exercises[0]['source']['page_num']==(2 if split else 1)
    assert exercises[0]['source']['snippet']=='Tính 2+3.'
    assert exercises[0]['source']['heading_source']['page_num']==1
    assert answers['1']['answer']=='5.' and counts['1']==1
    assert answers['1']['source']['page_num']==(4 if split else 2)
    assert 'Không phải' not in str(answers)


def test_empty_numbered_heading_does_not_consume_next_unrelated_section():
    service=LearningService(SimpleNamespace(pdf_path=lambda _:None),SimpleNamespace())
    chunks=[{'id':i,'page_num':i,'text':text} for i,text in enumerate([
        '## Bài tập 1','## Lý thuyết','Nội dung không phải bài tập.'],1)]
    assert service._extract_exercises_and_answers(chunks,{'id':1,'filename':'a.md'})[0]==[]


def test_all_public_markdown_topics_have_source_exercise_and_missing_answer(tmp_path):
    import shutil
    from app.document_lookup import DocumentLookup
    from app.indexer import KnowledgeIndexer
    sources=tmp_path/'sources';sources.mkdir()
    for path in (Path(__file__).parent/'fixtures/documents').glob('extended_*.md'):
        shutil.copy2(path,sources/path.name)
    db=tmp_path/'library.db'
    KnowledgeIndexer(db,sources).index_all(ocr=False,embed_model=None)
    lookup=DocumentLookup(db,sources)
    service=LearningService(lookup,SimpleNamespace())
    docs=lookup.documents()
    assert len(docs)==12
    for doc in docs:
        topic=service._extract_document_topic(doc)
        result=service.exercises(topic,solve_missing=False)
        assert len(result['items'])==2,(topic,result)
        first,second=result['items']
        assert first['answer_status']=='source_document'
        assert first['source']['page_num']==6 and first['answer_source']['page_num']==8
        assert first['source']['doc_id']==first['answer_source']['doc_id']==doc['id']
        assert second['answer_status']=='missing' and second['answer'] is None
        assert second['source']['page_num']==10
        assert '999' not in str(result['items'])
