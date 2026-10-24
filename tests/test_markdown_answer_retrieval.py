"""Adjacent Markdown source answers supplement retrieval without crossing scope."""
import sqlite3
from copy import deepcopy
import pytest
from app.searcher import HybridSearcher


@pytest.fixture
def search(tmp_path):
    db=tmp_path/'library.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE chunks(id INTEGER PRIMARY KEY,doc_id INTEGER,book_title TEXT,filename TEXT,page_num INTEGER,chunk_index INTEGER,text TEXT)')
        conn.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',[
            (1,10,'Public','source.md',5,0,'## Bài tập 1'),
            (2,10,'Public','source.md',6,0,'Tìm 8 trong mảng [2,4,6,8,10].'),
            (3,10,'Public','source.md',7,0,'## Đáp án bài 1'),
            (4,10,'Public','source.md',8,0,'Chỉ số 3; mid đầu là 2, sau là 3.'),
            (5,11,'Other','source.md',8,0,'WRONG_DOCUMENT'),
            (6,10,'Public','other.md',8,0,'WRONG_FILE'),
            (7,10,'Public','source.md',10,0,'NON_ADJACENT'),
        ])
    return HybridSearcher(db)


def test_numbered_answer_is_an_exact_separately_citable_source(search):
    anchor=search.source_page_chunks(10,6)[0];before=deepcopy(anchor)
    result=search.expand_markdown_answers([anchor])
    assert anchor==before and result[0]==before
    assert [c['chunk_id'] for c in result]==[2,4]
    assert result[1]['page_num']==8 and result[1]['doc_id']==10
    assert result[1]['text']=='Chỉ số 3; mid đầu là 2, sau là 3.'
    assert result[1]['citation_label']=='[Public, Đoạn 8]'
    assert result[1]['source_type']=='exercise_answer_context'
    assert 'semantic_score' not in result[1]
    assert search.expand_markdown_answers(result)==result


@pytest.mark.parametrize('change', ['wrong_number','pdf','fake_text','fake_doc','fake_file','fake_page','no_header','new_section'])
def test_expansion_requires_matching_number_format_and_exact_anchor_identity(search,change):
    anchor=search.source_page_chunks(10,6)[0]
    with sqlite3.connect(search.db_path) as db:
        if change=='wrong_number':db.execute("UPDATE chunks SET text='## Đáp án bài 2' WHERE id=3")
        elif change=='pdf':
            db.execute("UPDATE chunks SET filename='source.pdf' WHERE filename='source.md'")
            anchor['filename']='source.pdf'
        elif change=='no_header':db.execute("DELETE FROM chunks WHERE id=1")
        elif change=='new_section':db.execute("UPDATE chunks SET text='## Theory' WHERE id=4")
        elif change=='fake_text':anchor['text']='FORGED'
        elif change=='fake_doc':anchor['doc_id']=11
        elif change=='fake_file':anchor['filename']='other.md'
        elif change=='fake_page':anchor['page_num']=7
    assert search.expand_markdown_answers([anchor])==[anchor]


def test_whole_answer_obeys_count_and_character_budgets(search):
    anchor=search.source_page_chunks(10,6)[0]
    assert search.expand_markdown_answers([anchor],max_chunks=1)==[anchor]
    assert search.expand_markdown_answers([anchor],max_chars=len(anchor['text'])+1)==[anchor]


def test_existing_answer_retains_verified_question_link_without_mutating_anchors(search):
    question=search.source_page_chunks(10,6)[0]
    answer=search.source_page_chunks(10,8)[0]
    anchors=[answer,question]
    before=deepcopy(anchors)
    result=search.expand_markdown_answers(anchors)
    assert anchors==before
    assert [c['chunk_id'] for c in result]==[4,2]
    assert result[0]['exercise_question']==question['text']


def test_new_answer_retains_exact_stored_question_link(search):
    question=search.source_page_chunks(10,6)[0]
    result=search.expand_markdown_answers([question])
    assert result[1]['exercise_question']==question['text']
