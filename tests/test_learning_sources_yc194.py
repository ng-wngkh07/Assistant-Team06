"""Independent source/answer acceptance; same-number answers in other books must not match."""
import importlib
import sqlite3
from pathlib import Path
import pytest
from app.document_lookup import DocumentLookup
from app.indexer import KnowledgeIndexer
from app.practice import PracticeService
from app.searcher import HybridSearcher


@pytest.fixture
def service(tmp_path):
    module = importlib.import_module('app.learning')
    src = tmp_path / 'src'; src.mkdir()
    db = tmp_path / 'kb.db'
    KnowledgeIndexer(db, src)
    for name in ('memory.pdf', 'other.pdf'):
        (src/name).write_bytes(b'%PDF fixture boundary; rendering tested separately')
    with sqlite3.connect(db) as conn:
        for doc_id,name in [(1,'memory.pdf'),(2,'other.pdf')]:
            conn.execute('INSERT INTO documents(id,filename,clean_title,filepath,file_size,file_hash,total_pages,extracted_pages_count,status) VALUES(?,?,?,?,?,?,?,?,?)',
                         (doc_id,name,'Trí nhớ' if doc_id == 1 else 'Cơ học',str(src/name),1,'fixture',6,6,'indexed'))
        rows = [
            (1,1,1,'Trí nhớ','LÝ THUYẾT\nTrí nhớ làm việc giữ và xử lý thông tin tạm thời. Trí nhớ dài hạn lưu thông tin lâu hơn.'),
            (2,1,2,'Trí nhớ','BÀI TẬP\nBài 1. Loại trí nhớ nào giữ thông tin tạm thời?\nBài 2. Nêu một ví dụ về trí nhớ làm việc.'),
            (3,1,3,'Trí nhớ','ĐÁP ÁN\nĐáp án Bài 1. Trí nhớ làm việc.\nĐáp án Bài 9. Không liên quan.'),
            (4,2,4,'Cơ học','ĐÁP ÁN\nĐáp án Bài 2. Gia tốc bằng 3 m/s2.'),
        ]
        for chunk_id,doc_id,page,title,text in rows:
            conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) VALUES(?,?,?,?,?,?,?)',
                         (chunk_id,doc_id,title,'memory.pdf' if doc_id==1 else 'other.pdf',page,0,text))
    lookup=DocumentLookup(db,src)
    searcher=HybridSearcher(db)
    practice=PracticeService(searcher,lookup)
    def no_inference(*args,**kwargs):
        raise AssertionError('Source extraction without solving must not call model')
    practice._chat_complete=no_inference
    return module.LearningService(document_lookup=lookup,practice_service=practice,searcher=searcher), db


def test_extracts_individual_exercise_and_pairs_answer_across_pages_in_same_document(service):
    learning,_=service
    items=learning.exercises(topic='Trí nhớ',limit=10,solve_missing=False,model='test-model')['items']
    assert len(items)==2
    first=next(item for item in items if 'Bài 1' in item['question'])
    assert 'Bài 2' not in first['question']
    assert first['source']['doc_id']==1 and first['source']['page_num']==2
    assert first['answer_status']=='source_document'
    assert first['answer_source']['doc_id']==1 and first['answer_source']['page_num']==3
    assert 'Trí nhớ làm việc' in first['answer'] and 'Bài 9' not in first['answer']
    second=next(item for item in items if 'Bài 2' in item['question'])
    assert second['answer_status']=='missing'
    assert second['answer'] is None and second['answer_source'] is None


def test_unreviewed_document_never_becomes_learning_source(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET status='ocr_partial' WHERE id=1")
    result=learning.exercises(topic='Trí nhớ',limit=10,solve_missing=False,model='test-model')
    assert not result['items']


def test_topic_absent_from_library_does_not_return_unrelated_exercises(service):
    learning,_=service
    assert not learning.exercises(topic='Lỗ đen vũ trụ',limit=10,solve_missing=False,model='test-model')['items']


def test_duplicate_exercise_identifiers_never_silently_receive_one_answer_key(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) VALUES(5,1,?,?,?,?,?)',
                     ('Trí nhớ','memory.pdf',5,0,'CHƯƠNG KHÁC\nBài 1. Nêu một ứng dụng của trí nhớ dài hạn.'))
    result=learning.exercises(topic='Trí nhớ',limit=10,solve_missing=False,model='test-model')['items']
    duplicates=[item for item in result if 'Bài 1' in item['question']]
    assert len(duplicates)==2
    assert all(item['answer_status'] != 'source_document' for item in duplicates)
    assert all(item['answer_source'] is None for item in duplicates)


def test_subtopic_generation_retrieves_theory_without_requiring_exact_book_title(service):
    learning,_=service
    captured={}
    def generate(**kwargs):
        captured.update(kwargs)
        return {'practice_id':'a'*32,'questions':[]}
    learning.practice_service.generate_from_passages=generate
    learning.generate(topic='trí nhớ làm việc',count=1,difficulty='hard',purpose='review',weak_topics=['trí nhớ làm việc'],model='test-model')
    assert captured['count']==1 and captured['difficulty']=='hard' and captured['purpose']=='review'
    assert any('Trí nhớ làm việc giữ' in passage['text'] for passage in captured['passages'])
    assert all(passage['doc_id']==1 for passage in captured['passages'])


def test_hard_generation_prioritizes_applicable_rules_over_definition_tables(service):
    learning, db = service
    definition = ('Trí nhớ làm việc: khái niệm, định nghĩa, tính chất, cơ chế. '
                  '| Định nghĩa | Cơ chế |\n|---|---|\n'
                  '| Trí nhớ làm việc | Giữ thông tin tạm thời |')
    rules = ('Trí nhớ làm việc giữ thông tin tạm thời. '
             'Nếu hai tác vụ cùng cần một nguồn xử lý thì chúng có thể cản trở nhau. '
             'Chỉ được kết luận có giao thoa khi hai tác vụ cùng cần nguồn xử lý ấy; '
             'không được coi sự chậm lại là bằng chứng chắc chắn của quên.')
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE chunks SET text=? WHERE id=1', (definition,))
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) '
                     'VALUES(5,1,?,?,?,?,?)', ('Trí nhớ','memory.pdf',5,0,rules))
    captured = {}
    learning.practice_service.generate_from_passages = lambda **kw: captured.update(kw) or {}
    learning.generate(topic='Trí nhớ', count=5, difficulty='hard', model='test-model')
    assert captured['passages'][0]['page_num'] == 5
    assert captured['enforce_difficulty'] is True


def test_repeated_course_header_does_not_hide_distinct_source_rules(service):
    learning, db = service
    header = 'Trí nhớ làm việc. Ghi chú môn học, phần lý thuyết. ' * 4
    first = header + 'Nếu thông tin không được ôn lại thì dấu vết có thể suy yếu.'
    second = header + 'Khi hai tác vụ cùng cần nguồn xử lý thì giao thoa có thể xuất hiện.'
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE chunks SET text=? WHERE id=1', (first,))
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) '
                     'VALUES(5,1,?,?,?,?,?)', ('Trí nhớ','memory.pdf',5,0,second))
    captured = {}
    learning.practice_service.generate_from_passages = lambda **kw: captured.update(kw) or {}
    learning.generate(topic='Trí nhớ', count=2, difficulty='medium', model='test-model')
    assert {p['page_num'] for p in captured['passages']} == {1,5}


def test_duplicate_source_answer_keys_are_ambiguous_not_first_key_wins(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) VALUES(5,1,?,?,?,?,?)',
                     ('Trí nhớ','memory.pdf',6,0,'ĐÁP ÁN\nĐáp án Bài 1. Trí nhớ dài hạn.'))
    items=learning.exercises(topic='Trí nhớ',limit=10,solve_missing=False,model='test-model')['items']
    first=next(item for item in items if 'Bài 1' in item['question'])
    assert first['answer_status'] != 'source_document'
    assert first['answer_source'] is None


@pytest.mark.parametrize('review',[
    {'verified':True,'verified_answer':'Trí nhớ dài hạn.','conditions_preserved':True,'solution_supported':True},
    {'verified':True,'verified_answer':'Trí nhớ làm việc.','conditions_preserved':False,'solution_supported':True},
    {'verified':True},
])
def test_model_verified_answer_requires_agreement_and_conditions_not_a_bare_yes(service,review):
    import json
    learning,_=service
    replies=iter([json.dumps({'solution':'Đáp án theo lý thuyết.','final_answer':'Trí nhớ làm việc.'},ensure_ascii=False),json.dumps(review,ensure_ascii=False)])
    learning.practice_service._chat_complete=lambda *args,**kwargs:next(replies)
    answer,status=learning._solve_and_verify_exercise('Loại trí nhớ nào giữ thông tin tạm thời?',
        [{'text':'Trí nhớ làm việc giữ và xử lý thông tin tạm thời.'}],'test-model')
    assert status!='model_verified'


def test_generation_selects_relevant_later_passage_instead_of_first_pages_of_book(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE documents SET total_pages=300 WHERE id=1')
        for i in range(5,215):
            conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) VALUES(?,1,?,?,?,?,?)',
                         (i,'Trí nhớ','memory.pdf',i,0,'Giới thiệu chung: đọc và ghi chép tài liệu.'))
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) VALUES(215,1,?,?,?,?,?)',
                     ('Trí nhớ','memory.pdf',215,0,'Ghi nhớ chủ động: tự nhớ lại nội dung trước khi đối chiếu tài liệu giúp kiểm tra phần chưa nắm.'))
    captured={}
    learning.practice_service.generate_from_passages=lambda **kwargs:captured.update(kwargs) or {'practice_id':'a'*32,'questions':[]}
    learning.generate(topic='ghi nhớ chủ động',count=1,difficulty='medium',purpose='practice',weak_topics=[],model='test-model')
    assert any(passage['page_num']==215 for passage in captured['passages'])
    assert not any(passage['text'].startswith('Giới thiệu chung:') for passage in captured['passages'])


def test_english_problem_and_answer_sections_pair_with_separate_source_pages(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE chunks SET text=? WHERE id=4',('Cơ học\nEXERCISES\nProblem 1. A 2 kg object is acted on by a 6 N net force. Find its acceleration.',))
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) VALUES(5,2,?,?,?,?,?)',
                     ('Cơ học','other.pdf',5,0,'ANSWERS\nAnswer 1. The acceleration is 3 m/s2.'))
    items=learning.exercises(topic='Cơ học',limit=10,solve_missing=False,model='test-model')['items']
    assert len(items)==1
    assert items[0]['question'].startswith('Problem 1.')
    assert items[0]['answer_status']=='source_document'
    assert '3 m/s2' in items[0]['answer']
    assert items[0]['source']['page_num']==4 and items[0]['answer_source']['page_num']==5


def test_theory_question_generation_does_not_silently_use_answer_only_material(service):
    from app.practice import PracticeGenerationError
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('DELETE FROM chunks WHERE id IN (1,2)')
    def forbidden(**kwargs):
        raise AssertionError('Answer keys alone are not theory passages for generation')
    learning.practice_service.generate_from_passages=forbidden
    with pytest.raises(PracticeGenerationError):
        learning.generate(topic='Trí nhớ',count=1,difficulty='medium',purpose='practice',weak_topics=[],model='test-model')


def test_exercise_and_answer_beyond_first_hundred_chunks_are_discovered(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE documents SET total_pages=300,extracted_pages_count=300 WHERE id=1')
        for page in range(4,215):
            conn.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(1,?,?,?,?,?)',
                         ('Trí nhớ','memory.pdf',page,0,'LÝ THUYẾT\nĐoạn nền của tài liệu dài.'))
        for page,text in [(215,'BÀI TẬP\nBài 8. Nhớ một kiến thức đã học lâu trước thuộc loại nào?'),(216,'ĐÁP ÁN\nĐáp án Bài 8. Trí nhớ dài hạn.')]:
            conn.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(1,?,?,?,?,?)',
                         ('Trí nhớ','memory.pdf',page,0,text))
    result=learning.exercises(topic='Trí nhớ',limit=10,solve_missing=False,model='test-model')
    item=next((i for i in result['items'] if 'Bài 8' in i['question']),None)
    assert item is not None,'A late exercise must not be silently omitted'
    assert item['source']['page_num']==215 and item['answer_source']['page_num']==216


def test_late_duplicate_key_cannot_make_early_answer_look_authoritative(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE documents SET total_pages=300,extracted_pages_count=300 WHERE id=1')
        for page in range(4,215):
            conn.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(1,?,?,?,?,?)',
                         ('Trí nhớ','memory.pdf',page,0,'LÝ THUYẾT\nĐoạn nền của tài liệu dài.'))
        conn.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(1,?,?,?,?,?)',
                     ('Trí nhớ','memory.pdf',215,0,'ĐÁP ÁN\nĐáp án Bài 1. Trí nhớ dài hạn.'))
    result=learning.exercises(topic='Trí nhớ',limit=10,solve_missing=False,model='test-model')
    item=next(i for i in result['items'] if 'Bài 1' in i['question'])
    assert item['answer_status']!='source_document' and item['answer_source'] is None


def test_goal_discovery_normalizes_pdf_spacing_and_vietnamese_uppercase(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET clean_title='Tâm lý học nhận thức' WHERE id=1")
        rows=conn.execute('SELECT id,text FROM chunks WHERE doc_id=1').fetchall()
        for chunk_id,text in rows:
            conn.execute('UPDATE chunks SET text=? WHERE id=?',(text.upper().replace(' ','\xa0'),chunk_id))
    matches=learning._matching_documents('trí nhớ làm việc')
    assert [d['id'] for d in matches]==[1], 'PDF Unicode spacing and Vietnamese casing cannot hide a relevant document'


def test_late_unicode_pdf_theory_is_used_for_generation_and_solver(service):
    learning,db=service
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE documents SET clean_title='Tâm lý học' WHERE id=1")
        conn.execute("UPDATE chunks SET text='Giới thiệu không liên quan.' WHERE doc_id=1")
        for page in range(4,215):
            conn.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(1,?,?,?,?,?)',
                         ('Tâm lý học','memory.pdf',page,0,'Giới thiệu không liên quan.'))
        conn.execute('INSERT INTO chunks(doc_id,book_title,filename,page_num,chunk_index,text) VALUES(1,?,?,?,?,?)',
                     ('Tâm lý học','memory.pdf',215,0,'LÝ THUYẾT\nTRÍ NHỚ: tự nhớ lại nội dung trước khi đối chiếu tài liệu.'.upper().replace(' ','\xa0')))
    captured={}
    learning.practice_service.generate_from_passages=lambda **kwargs:captured.update(kwargs) or {'practice_id':'a'*32,'questions':[]}
    learning.generate(topic='trí nhớ',count=1,difficulty='medium',purpose='practice',weak_topics=[],model='test-model')
    assert [p['page_num'] for p in captured['passages']]==[215]
    theory=learning._get_relevant_theory_chunks(1,'Trí nhớ',max_chunks=4)
    assert any(p['page_num']==215 for p in theory)
    assert not any(p['text'].startswith('Giới thiệu') for p in theory)


@pytest.mark.parametrize('advice', [
    'Trí nhớ – ghi chú môn học\n14. Lượt 1 lấy điểm chắc: trả lời câu lý thuyết trước.\n'
    '15. Nếu kẹt quá 5 phút, chuyển sang câu khác.\n16. Chừa ít nhất 10 phút để kiểm tra bài.\n'
    '17. Phải ghi rõ số câu; không được bỏ ý nhỏ.',
    'Trí nhớ – course notes\nExam strategy\nAllocate ten minutes to checking answers. '
    'If stuck, skip to the next question. You must include question numbers.'
])
def test_exam_time_advice_is_not_curriculum_evidence_for_topic_questions(service, advice):
    learning, db = service
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO chunks(id,doc_id,book_title,filename,page_num,chunk_index,text) '
                     'VALUES(5,1,?,?,?,?,?)', ('Trí nhớ','memory.pdf',5,0,advice))
    captured = {}
    learning.practice_service.generate_from_passages = lambda **kw: captured.update(kw) or {}
    learning.generate(topic='Trí nhớ', count=3, difficulty='hard', model='test-model')
    assert [p['page_num'] for p in captured['passages']] == [1]
