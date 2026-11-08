"""Local account sessions and request-scoped private storage."""
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import sqlite3
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, RedirectResponse
from app.config import DATA_DIR

account_id = ContextVar('study_account_id', default=None)
COOKIE = 'study_session'
FEATURES = {'qa', 'practice', 'review', 'examples', 'documents', 'materials', 'ocr', 'planner'}
router = APIRouter(prefix='/api/account')


def required():
    return os.environ.get('STUDY_ACCOUNTS_REQUIRED', '').lower() in {'1', 'true'}


def root_dir():
    return Path(os.environ.get('STUDY_ACCOUNTS_DIR', str(DATA_DIR / 'accounts')))


def private_dir():
    identity = account_id.get()
    if identity is None:
        return None
    if not re.fullmatch('[a-f0-9]{32}', identity):
        raise RuntimeError('Invalid account identity')
    path = root_dir() / identity
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@contextmanager
def connection():
    root = root_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'accounts.db'
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS feature_usage(account_id TEXT NOT NULL, feature TEXT NOT NULL, guided INTEGER NOT NULL DEFAULT 0, last_used REAL NOT NULL, PRIMARY KEY(account_id,feature));
        CREATE TABLE IF NOT EXISTS auth_attempts(identity TEXT NOT NULL, attempted REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS auth_attempt_lookup ON auth_attempts(identity,attempted);
        ''')
        path.chmod(0o600)
        yield db
        db.commit()
    finally:
        db.close()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def session_account(token):
    if not token or len(token) > 128:
        return None
    with connection() as db:
        row = db.execute('SELECT a.id,a.username FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?', (digest(token), time.time())).fetchone()
    return dict(row) if row else None


def loopback(request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = request.client is not None and request.client.host == 'testclient'
    return local and request.url.hostname in {'127.0.0.1', 'localhost', '::1', 'testserver'}


class AccountMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = request.url.path
        if path == '/static/index.html':
            return await RedirectResponse('/', status_code=307)(scope, receive, send)
        is_api = path.startswith('/api/')
        user = session_account(request.cookies.get(COOKIE)) if request.cookies.get(COOKIE) else None
        must_login = required() or not loopback(request)
        public = path in {'/api/account/status', '/api/account/login', '/api/account/register', '/api/health'}
        async def reject(status, detail):
            await JSONResponse({'detail': detail}, status_code=status, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if is_api and must_login and not user and not public:
            return await reject(401, 'Vui lòng đăng nhập để sử dụng tài khoản của bạn.')
        if is_api and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.url.netloc}'
            if origin and origin != expected:
                return await reject(403, 'Nguồn yêu cầu không hợp lệ.')
            if (user or must_login or path.startswith('/api/account/')) and not origin and request.headers.get('x-study-request') != '1':
                return await reject(403, 'Thiếu xác nhận yêu cầu cùng ứng dụng.')
            if user and (path == '/api/index' or path.startswith('/api/ocr/')):
                return await reject(403, 'Thao tác quản trị thư viện chỉ dùng trong chế độ cục bộ.')
        scope.setdefault('state', {})['account'] = user
        token = account_id.set(user['id'] if user else None)
        async def private_send(message):
            if is_api and message['type'] == 'http.response.start':
                headers = [(k,v) for k,v in message.get('headers', []) if k.lower() != b'cache-control']
                message = {**message, 'headers': headers + [(b'cache-control', b'no-store')]}
            await send(message)
        try:
            await self.app(scope, receive, private_send)
        finally:
            account_id.reset(token)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r'^[A-Za-z0-9_.@+\-]+$')
    password: str = Field(min_length=12, max_length=128)


def issue_session(response, identity, request):
    raw = secrets.token_urlsafe(32)
    with connection() as db:
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(raw), identity, time.time() + 30*86400))
    response.set_cookie(COOKIE, raw, max_age=30*86400, httponly=True, samesite='strict', secure=request.url.scheme == 'https' or os.environ.get('STUDY_SECURE_COOKIES') == '1', path='/')


def throttle(request, username):
    key = digest(f'{request.client.host if request.client else "unknown"}|{username.lower()}')
    with connection() as db:
        db.execute('DELETE FROM auth_attempts WHERE attempted<?', (time.time()-900,))
        if db.execute('SELECT COUNT(*) FROM auth_attempts WHERE identity=?', (key,)).fetchone()[0] >= 5:
            raise HTTPException(429, 'Thử quá nhiều lần. Vui lòng chờ 15 phút.')
        db.execute('INSERT INTO auth_attempts VALUES(?,?)', (key,time.time()))


@router.get('/status')
def status(request: Request):
    return {'account': getattr(request.state, 'account', None), 'required': required() or not loopback(request), 'registration_allowed': loopback(request) or os.environ.get('STUDY_ALLOW_REGISTRATION') == '1'}


@router.post('/register')
def register(credentials: Credentials, request: Request, response: Response):
    if not loopback(request) and os.environ.get('STUDY_ALLOW_REGISTRATION') != '1':
        raise HTTPException(403, 'Đăng ký công khai chưa được mở.')
    throttle(request, credentials.username)
    salt = secrets.token_hex(16)
    encoded = password_hash(credentials.password, salt)
    identity = uuid.uuid4().hex
    try:
        with connection() as db:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (identity,credentials.username.lower(),salt,encoded))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Tên tài khoản không khả dụng.')
    issue_session(response,identity,request)
    return {'account': {'id':identity,'username':credentials.username.lower()}}


@router.post('/login')
def login(credentials: Credentials, request: Request, response: Response):
    throttle(request, credentials.username)
    with connection() as db:
        row = db.execute('SELECT * FROM accounts WHERE username=?',(credentials.username.lower(),)).fetchone()
    salt = row['salt'] if row else '0'*32
    encoded = password_hash(credentials.password,salt)
    if not row or not hmac.compare_digest(encoded,row['password_hash']):
        raise HTTPException(401,'Tên tài khoản hoặc mật khẩu chưa đúng.')
    issue_session(response,row['id'],request)
    return {'account': {'id':row['id'],'username':row['username']}}


@router.post('/logout')
def logout(request: Request, response: Response):
    raw = request.cookies.get(COOKIE)
    if raw:
        with connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(digest(raw),))
    response.delete_cookie(COOKIE, path='/', samesite='strict')
    return {'status':'ok'}


@router.get('/features/{feature}')
def feature_status(feature: str):
    if feature not in FEATURES:
        raise HTTPException(404,'Không tìm thấy chức năng.')
    identity = account_id.get()
    if not identity:
        return {'first_use': True, 'account_saved': False}
    with connection() as db:
        row=db.execute('SELECT guided FROM feature_usage WHERE account_id=? AND feature=?',(identity,feature)).fetchone()
        db.execute('INSERT INTO feature_usage VALUES(?,?,0,?) ON CONFLICT(account_id,feature) DO UPDATE SET last_used=excluded.last_used',(identity,feature,time.time()))
    return {'first_use':not row or not row['guided'], 'account_saved': True}


@router.post('/features/{feature}/guided')
def finish_guide(feature: str):
    identity=account_id.get()
    if not identity:
        raise HTTPException(401,'Đăng nhập để lưu hướng dẫn giữa các thiết bị.')
    if feature not in FEATURES:
        raise HTTPException(404,'Không tìm thấy chức năng.')
    with connection() as db:
        db.execute('INSERT INTO feature_usage VALUES(?,?,1,?) ON CONFLICT(account_id,feature) DO UPDATE SET guided=1,last_used=excluded.last_used',(identity,feature,time.time()))
    return {'status':'ok'}
