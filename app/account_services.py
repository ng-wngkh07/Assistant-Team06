"""Account-scoped services, without process-wide environment changes."""
from collections import OrderedDict
from threading import RLock
import json
import sqlite3
from app.accounts import account_id, private_dir


class AccountService:
    def __init__(self, local, factory):
        object.__setattr__(self, '_local', local)
        object.__setattr__(self, '_factory', factory)
        object.__setattr__(self, '_instances', OrderedDict())
        object.__setattr__(self, '_lock', RLock())

    def current(self):
        identity = account_id.get()
        if identity is None:
            return self._local
        key = str(private_dir())
        with self._lock:
            if key not in self._instances:
                self._instances[key] = self._factory()
            self._instances.move_to_end(key)
            while len(self._instances) > 64:
                self._instances.popitem(last=False)
            return self._instances[key]

    def __getattr__(self, name):
        return getattr(self.current(), name)

    def __setattr__(self, name, value):
        setattr(self.current(), name, value)


def make_material_manager():
    from app.study_materials import StudyMaterialManager, StudyMaterialError

    class PrivateMaterials(StudyMaterialManager):
        def __init__(self):
            super().__init__()
            self.path = private_dir() / 'uploads.db'
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, content TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS files(key TEXT PRIMARY KEY, content BLOB NOT NULL)')
                self._materials.update({r[0]:json.loads(r[1]) for r in db.execute('SELECT id,content FROM records')})
                self._raw_files.update(dict(db.execute('SELECT key,content FROM files')))
            self.path.chmod(0o600)

        def connect(self):
            return sqlite3.connect(self.path, timeout=10)

        def cache_raw_file(self, filename, file_bytes, material_id=None):
            super().cache_raw_file(filename, file_bytes, material_id)
            with self._lock, self.connect() as db:
                for key in (filename, material_id):
                    if key and file_bytes:
                        db.execute('INSERT OR REPLACE INTO files VALUES(?,?)',(key,file_bytes))
                for key,record in self._materials.items():
                    db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(key,json.dumps(record,ensure_ascii=False)))

        def ingest_to_src_and_index(self, *args, **kwargs):
            raise StudyMaterialError('Tệp tài khoản được lưu riêng. Chưa hỗ trợ đưa tệp riêng vào chỉ mục thư viện dùng chung; hãy bỏ tùy chọn thêm vào thư viện.')

    return PrivateMaterials()
