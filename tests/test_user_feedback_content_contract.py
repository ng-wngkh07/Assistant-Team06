import hashlib
import io
import sqlite3
import pytest
import pymupdf
from PIL import Image
from fastapi.testclient import TestClient
from app import server
from app.searcher import HybridSearcher
from app.document_lookup import DocumentLookup


@pytest.fixture
def content_library(tmp_path, monkeypatch):
    src = tmp_path / 'src'; src.mkdir()
    db_path = tmp_path / 'knowledge.db'
    with sqlite3.connect(db_path) as db:
        db.executescript('''
        CREATE TABLE documents(id INTEGER PRIMARY KEY, filename TEXT, clean_title TEXT, total_pages INTEGER, status TEXT);
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, doc_id INTEGER, book_title TEXT, filename TEXT, page_num INTEGER, chunk_index INTEGER, text TEXT, embedding BLOB);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(book_title,text);
        ''')
        for did in (1,2):
            name=f'Untitled_{did}.pdf'; pdf=pymupdf.open()
            for page in (1,2):
                p=pdf.new_page(width=300,height=400)
                p.draw_rect(pymupdf.Rect(30,30,270,370),color=None,fill=(1,0,0) if (did,page)==(1,1) else (0,0,1))
            pdf.save(src/name); pdf.close()
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?)',(did,name,name,2,'indexed'))
        rows=[(1,1,'quasar title only','Untitled_1.pdf',1,0,'Nothing about planets in this passage.',None),
              (2,2,'untitled','Untitled_2.pdf',2,0,'quasar ' * 20,None),
              (3,1,'untitled','Untitled_1.pdf',1,1,'quasar ' + 'filler '*100,None)]
        for r in rows:
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',r)
            db.execute('INSERT INTO chunks_fts(rowid,book_title,text) VALUES(?,?,?)',(r[0],r[2],r[6]))
    lookup=DocumentLookup(db_path,src)
    monkeypatch.setattr(server,'document_lookup',lookup)
    return db_path,src,lookup


def test_qa_keyword_retrieval_never_uses_title_only_match(content_library):
    db,_,_=content_library
    rows=HybridSearcher(db).search_fts('quasar')
    assert rows and 1 not in {r['chunk_id'] for r in rows}


def test_bm25_content_rank_preserves_best_match_first(content_library):
    path,_,_=content_library
    with sqlite3.connect(path) as db:
        best=db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'text:quasar' ORDER BY bm25(chunks_fts) LIMIT 1").fetchone()[0]
    assert HybridSearcher(path).search_fts('quasar')[0]['chunk_id']==best


def test_document_lookup_does_not_use_title_only_match(content_library):
    _,_,lookup=content_library
    rows=lookup.search('quasar')['results']
    assert rows and 1 not in {r['chunk_id'] for r in rows}


def test_original_page_image_matches_requested_document_page(content_library):
    _,src,_=content_library
    before=hashlib.sha256((src/'Untitled_1.pdf').read_bytes()).hexdigest()
    with TestClient(server.app) as client:
        red=client.get('/api/documents/1/pages/1/image')
        blue=client.get('/api/documents/1/pages/2/image')
        assert red.status_code==blue.status_code==200
        assert red.headers['content-type'].startswith('image/png')
        a=Image.open(io.BytesIO(red.content)).convert('RGB'); b=Image.open(io.BytesIO(blue.content)).convert('RGB')
        assert a.getpixel((a.width//2,a.height//2))==(255,0,0)
        assert b.getpixel((b.width//2,b.height//2))==(0,0,255)
        assert client.get('/api/documents/1/pages/3/image').status_code==404
        assert client.get('/api/documents/999/pages/1/image').status_code==404
    assert hashlib.sha256((src/'Untitled_1.pdf').read_bytes()).hexdigest()==before


def test_selected_source_page_is_content_context_not_title_guess(content_library):
    from app.rag_agent import PsychologyAgent
    path,_,_=content_library
    class Capture:
        def chat_stream(self, **kwargs):
            self.messages=kwargs['messages']
            yield 'Nội dung được trích từ trang đã chọn [S1].'
    class Translator:
        def translate(self,chunks): return {}
    model=Capture()
    agent=PsychologyAgent(searcher=HybridSearcher(path),ollama=model,translator=Translator())
    result=agent.process_query_sync('Giải thích nội dung trang được chọn.',embed_model=None,source_page=(2,2),model='test-ollama')
    assert result['citations'][0]['doc_id']==2
    assert result['citations'][0]['page_num']==2
    assert result['citations'][0]['id']=='S1'
    prompt='\n'.join(m['content'] for m in model.messages)
    assert 'quasar quasar' in prompt
    assert 'Nothing about planets' not in prompt
    assert result['citations'][0]['pdf_url']=='/api/documents/2/pdf#page=2'
    assert result['citations'][0].get('page_image_url') is None
