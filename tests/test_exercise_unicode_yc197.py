"""PDF extraction spacing/case must not hide an actual exercise heading."""
import sqlite3
from pathlib import Path
import pytest
from app.practice import PracticeService

@pytest.mark.parametrize('heading', ['BÀI\u00a0TẬP', 'BÀI TẬP', 'Bài\u00a01.', 'Bài\t1.'])
def test_pdf_unicode_heading_preserves_source_and_locator(tmp_path, heading):
    path=tmp_path/'corpus.db'
    text=heading+'\nCho T(x)=3x. Kiểm tra tính tuyến tính.'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE chunks (id INTEGER,doc_id INTEGER,book_title TEXT,filename TEXT,page_num INTEGER,chunk_index INTEGER,text TEXT)')
        # The matching row must survive more than2000 preceding non-exercise rows.
        db.executemany('INSERT INTO chunks VALUES (?,1,?,?,2,?,?)', [(100+i,'Nguồn thử','course.pdf',i-2001,'Khái niệm ma trận.') for i in range(2001)])
        db.execute('INSERT INTO chunks VALUES (7,1,?, ?,2,0,?)',('Nguồn thử','course.pdf',text))
        db.execute('CREATE TABLE documents (id INTEGER,is_scanned INTEGER,status TEXT)')
        db.execute("INSERT INTO documents VALUES(1,0,'indexed')")
    class Lookup:
        def document(self,doc_id):return {'id':doc_id,'filename':'course.pdf','total_pages':3}
        def pdf_path(self,filename):return Path('course.pdf')
        def connect(self):
            db=sqlite3.connect(path);db.row_factory=sqlite3.Row;return db
    service=PracticeService(searcher=None,document_lookup=Lookup())
    items=service.list_example_exercises(1,1,3)
    assert len(items)==1
    assert items[0]['text']==text
    assert items[0]['page_num']==2 and items[0]['chunk_id']==7
    assert items[0]['pdf_url'].endswith('/1/pdf#page=2')
