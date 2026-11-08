import sqlite3
import pytest
from fastapi.testclient import TestClient
from app import server
from app import accounts


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDY_ACCOUNTS_DIR', str(tmp_path/'accounts'))
    monkeypatch.setenv('STUDY_ACCOUNTS_REQUIRED','1')
    a=TestClient(server.app,headers={'X-Study-Request':'1'})
    b=TestClient(server.app,headers={'X-Study-Request':'1'})
    yield a,b
    a.close();b.close()


def register(client, username):
    r=client.post('/api/account/register',json={'username':username,'password':'private-example-123'})
    assert r.status_code==200, r.text
    return r.json()['account']


def test_required_auth_sessions_and_logout(clients):
    a,b=clients
    assert a.get('/api/timetable').status_code==401
    register(a,'alpha')
    cookie=a.cookies.get(accounts.COOKIE)
    assert a.get('/api/timetable').status_code==200
    assert b.get('/api/timetable').status_code==401
    assert a.post('/api/account/logout').status_code==200
    a.cookies.set(accounts.COOKIE,cookie)
    assert a.get('/api/timetable').status_code==401


def test_passwords_and_sessions_are_hashed(clients):
    a,_=clients
    register(a,'alpha')
    with accounts.connection() as db:
        row=db.execute('SELECT salt,password_hash FROM accounts').fetchone()
        assert row['password_hash']!='private-example-123' and len(row['salt'])==32
        assert db.execute('SELECT token_hash FROM sessions').fetchone()[0]!=a.cookies.get(accounts.COOKIE)
    assert 'httponly' in a.post('/api/account/login',json={'username':'alpha','password':'private-example-123'}).headers['set-cookie'].lower()


def test_onboarding_shared_across_devices_private_per_account(clients):
    a,b=clients
    register(a,'alpha')
    assert a.get('/api/account/features/planner').json()['first_use'] is True
    assert a.post('/api/account/features/planner/guided').status_code==200
    assert b.post('/api/account/login',json={'username':'alpha','password':'private-example-123'}).status_code==200
    assert b.get('/api/account/features/planner').json()['first_use'] is False
    assert b.get('/api/account/features/qa').json()['first_use'] is True
    b.post('/api/account/logout')
    register(b,'bravo')
    assert b.get('/api/account/features/planner').json()['first_use'] is True


def test_planner_history_and_materials_isolated(clients):
    a,b=clients
    register(a,'alpha');register(b,'bravo')
    entry={'course':'Only Alpha','weekday':1,'start_time':'08:00','end_time':'09:00'}
    r=a.post('/api/timetable/manual',json=entry)
    assert r.status_code==200,r.text
    assert 'Only Alpha' in a.get('/api/timetable').text
    assert 'Only Alpha' not in b.get('/api/timetable').text
    session=a.post('/api/sessions',json={})
    assert session.status_code==200,session.text
    sid=session.json().get('session',session.json())['id']
    assert b.get('/api/sessions/'+sid).status_code==404
    from app.account_services import make_material_manager
    from app.accounts import account_id
    token=account_id.set(a.get('/api/account/status').json()['account']['id'])
    try:
        manager=make_material_manager();manager._materials['only-alpha']={'id':'only-alpha','filename':'private.txt'}
        manager.cache_raw_file('private.txt',b'private alpha content','only-alpha')
        restored=make_material_manager()
        assert restored._raw_files['only-alpha']==b'private alpha content'
    finally:
        account_id.reset(token)
    token=account_id.set(b.get('/api/account/status').json()['account']['id'])
    try:
        assert 'only-alpha' not in make_material_manager()._materials
    finally:
        account_id.reset(token)


def test_wrong_origin_expiry_and_mutation_guard(clients):
    a,_=clients
    register(a,'alpha')
    assert a.post('/api/account/logout',headers={'Origin':'https://other.invalid'}).status_code==403
    assert a.post('/api/index',json={}).status_code==403
    with accounts.connection() as db:db.execute('UPDATE sessions SET expires=0')
    assert a.get('/api/timetable').status_code==401


def test_bad_login_rate_limited(clients):
    a,_=clients
    for _ in range(5):
        assert a.post('/api/account/login',json={'username':'unknown','password':'wrong-example-123'}).status_code==401
    assert a.post('/api/account/login',json={'username':'unknown','password':'wrong-example-123'}).status_code==429


def test_html_storage_scope_and_static_entry_redirect(clients):
    a,b=clients
    alpha=register(a,'alpha');bravo=register(b,'bravo')
    for client,own,other in ((a,alpha,bravo),(b,bravo,alpha)):
        page=client.get('/')
        assert f'name="study-account-scope" content="{own["id"]}"' in page.text
        assert other['id'] not in page.text
        assert page.headers['cache-control']=='no-store'
        assert 'cookie' in page.headers['vary'].lower()
        redirect=client.get('/static/index.html',follow_redirects=False)
        assert redirect.status_code==307 and redirect.headers['location']=='/'


def test_real_upload_extraction_is_durable_and_private(clients):
    import base64
    import fitz
    a,b=clients
    alpha=register(a,'alpha');register(b,'bravo')
    with fitz.open() as document:
        document.new_page().insert_text((72,72),'Only Alpha private study material. This private document explains a complete example for local extraction.')
        raw=document.tobytes()
    response=a.post('/api/materials/upload',json={
        'filename':'private-study.pdf','file_base64':base64.b64encode(raw).decode(),
        'generate_study_set':False,
    })
    assert response.status_code==200,response.text
    material_id=response.json()['material_id']
    assert a.get('/api/materials/'+material_id).status_code==200
    assert b.get('/api/materials/'+material_id).status_code==404
    from app.account_services import make_material_manager
    token=accounts.account_id.set(alpha['id'])
    try:
        restored=make_material_manager()
        assert restored._raw_files[material_id]==raw
        assert material_id in restored._materials
    finally:
        accounts.account_id.reset(token)


def test_local_legacy_remains_and_remote_is_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('TIMETABLE_DB_PATH',str(tmp_path/'legacy.db'))
    monkeypatch.delenv('STUDY_ACCOUNTS_REQUIRED',raising=False)
    with TestClient(server.app) as local:
        assert local.get('/api/timetable').status_code==200
    with TestClient(server.app,client=('198.51.100.5',1234),base_url='https://study.example') as remote:
        assert remote.get('/api/timetable').status_code==401
        assert remote.post('/api/account/register',headers={'Origin':'https://study.example'},json={'username':'alpha','password':'private-example-123'}).status_code==403
