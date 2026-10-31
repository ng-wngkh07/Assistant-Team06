from app.bilingual import LocalTranslator
from test_runtime_model_selection import legacy_windows
import hashlib
import json
import sqlite3

from app.training_data import (
    basic_record_quality,
    ask_teacher,
    detect_language,
    format_context,
    is_substantive_excerpt,
    is_vietnamese_translation,
    prepare,
    select_cross_book_pairs,
    write_training_files,
)


def test_teacher_reads_nested_items_and_translates_each_source(monkeypatch, legacy_windows):
    english = "The mind and the body respond to trauma, and the nervous system changes with experience. " * 4
    vietnamese = "Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi theo trải nghiệm. " * 4
    item = {"question": "Hai đoạn cho phép suy luận điều gì về tâm trí và cơ thể?",
            "answer": "Hai đoạn cùng mô tả sự tác động qua lại của tâm trí và cơ thể, nhưng không chứng minh được cơ chế cụ thể. [S1] [S2]"}
    responses = [{"translations": {"items": [item]}}, vietnamese, vietnamese]
    models = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            value = responses.pop(0)
            return {"message": {"content": json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value}}

    def fake_post(*args, **kwargs):
        models.append(kwargs["json"]["model"])
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    pair = {"filename": "book.pdf", "title": "Book", "sources": [
        {"id": "S1", "page": 1, "text": english}, {"id": "S2", "page": 2, "text": english},
    ]}
    result = ask_teacher(pair, model="fixture-teacher:latest", translation_model="fixture-translator:latest")
    assert result["items"] == [item]
    assert result["translations"]["S1"] == vietnamese.strip()
    assert basic_record_quality({"pair": pair, **result})
    assert models == ["fixture-teacher:latest", "fixture-translator:latest", "fixture-translator:latest"]


def test_partial_english_translation_is_rejected():
    source = "The nervous system changes through experience and shapes emotional responses. " * 8
    record = {
        "language": "en",
        "pair": {"sources": [{"id": "S1", "text": source}, {"id": "S2", "text": source}]},
        "translations": {
            "S1": "Hệ thần kinh thay đổi qua trải nghiệm và định hình phản ứng cảm xúc.",
            "S2": "Hệ thần kinh thay đổi qua trải nghiệm và định hình phản ứng cảm xúc.",
        },
    }
    assert not basic_record_quality(record)


def test_translation_and_excerpt_quality_filters():
    vietnamese = "Tâm lý học nghiên cứu cảm xúc, hành vi và suy nghĩ của con người trong đời sống."
    chinese = "为什么类别是有用的，但定义不起作用？通过相似性确定类别，使用原型或实例。"
    headings = "\n".join(["Prototype Approach: Finding Average Case"] * 10)

    assert is_vietnamese_translation(vietnamese)
    assert not is_vietnamese_translation(chinese)
    assert not is_vietnamese_translation(vietnamese + chinese * 2)
    assert not is_substantive_excerpt("Fill in the blanks: ______ and ______ are missing.")
    assert not is_substantive_excerpt(headings)
    assert is_substantive_excerpt(vietnamese)


def test_prepare_retries_invalid_cached_translation(tmp_path, monkeypatch):
    english = "The mind and the body respond to trauma, and the nervous system changes with experience. " * 4
    chinese = "为什么类别是有用的，但定义不起作用？通过相似性确定类别，使用原型或实例。"
    vietnamese = "Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi theo trải nghiệm. " * 4
    pair = {
        "filename": "english-book.pdf", "title": "English book", "pair_index": 0,
        "sources": [
            {"id": "S1", "page": 1, "text": english},
            {"id": "S2", "page": 2, "text": english},
        ],
    }
    item = {"question": "Hai đoạn trình bày mối quan hệ nào?", "answer": "Hai đoạn mô tả mối quan hệ. [S1] [S2]"}
    old = {"key": "old", "pair": pair, "language": "en", "translations": {"S1": chinese, "S2": chinese}, "items": [item]}
    (tmp_path / "manifest.jsonl").write_text(json.dumps(old, ensure_ascii=False) + "\n")
    monkeypatch.setattr("app.training_data.select_pairs", lambda *_args, **_kwargs: [pair])
    monkeypatch.setattr(
        "app.training_data.ask_teacher",
        lambda *_args, **_kwargs: {"language": "en", "translations": {"S1": vietnamese, "S2": vietnamese}, "items": [item]},
    )

    summary = prepare(tmp_path / "unused.db", tmp_path, model="local")

    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == 2
    assert summary["train_examples"] == 3
    assert chinese not in (tmp_path / "train.jsonl").read_text()


def test_language_detection_and_bilingual_context():
    english = "The mind and body respond to trauma, and the response of the nervous system changes with experience."
    vietnamese = "Tâm lý học nghiên cứu cảm xúc, hành vi và suy nghĩ của con người trong đời sống."
    assert detect_language(english) == "en"
    assert detect_language(vietnamese) == "vi"
    pair = {
        "title": "Sách thử nghiệm",
        "sources": [
            {"id": "S1", "page": 2, "text": english},
            {"id": "S2", "page": 3, "text": english},
        ]
    }
    context = format_context(pair, {"S1": "Tâm trí và cơ thể phản ứng với sang chấn."})
    assert 'id="S1" book="Sách thử nghiệm" page="2"' in context
    assert "Bản dịch tiếng Việt: Tâm trí" in context
    assert 'id="S2" book="Sách thử nghiệm" page="3"' in context


def test_local_translation_is_cached(tmp_path, monkeypatch, legacy_windows):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": '{"0":"Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi theo trải nghiệm."}'}}

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    translator = LocalTranslator(cache_path=tmp_path / "translations.db", model="fixture-translator:latest")
    chunk = {
        "safe_text": "The mind and body respond to trauma, and the response of the nervous system changes with experience and the environment."
    }
    first = translator.translate([chunk])
    second = translator.translate([chunk])
    assert first == second
    assert "sang chấn" in first[0]
    assert len(calls) == 1


def test_runtime_does_not_reuse_legacy_translation_cache(tmp_path, monkeypatch, legacy_windows):
    source = "The mind and body respond to trauma, and the nervous system changes with experience and the environment."
    translator = LocalTranslator(cache_path=tmp_path / "translations.db", model="fixture-translator:latest")
    legacy_key = hashlib.sha256((translator.model + "\0" + source).encode()).hexdigest()
    with sqlite3.connect(translator.cache_path) as conn:
        conn.execute("INSERT INTO translations VALUES (?, ?)", (legacy_key, "Bản dịch cũ thiếu ý nhưng có đủ chiều dài để vượt qua bộ lọc ngôn ngữ."))
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": json.dumps({"0": "Tâm trí và cơ thể phản ứng với sang chấn, còn hệ thần kinh thay đổi qua trải nghiệm và môi trường."}, ensure_ascii=False)}}

    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("requests.post", post)
    result = translator.translate([{"safe_text": source}])
    assert len(calls) == 1
    assert "môi trường" in result[0]


def test_translation_batches_keep_results_when_one_batch_fails(tmp_path, monkeypatch, legacy_windows):
    calls = []

    class Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": self.content}}

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            return Response('{"0":"Bản dịch thứ nhất về quá trình nhận thức và trải nghiệm. Tâm trí và cơ thể phản ứng với sang chấn, hệ thần kinh thay đổi theo trải nghiệm.","1":"Bản dịch thứ hai về quá trình nhận thức và trải nghiệm. Tâm trí và cơ thể phản ứng với sang chấn, hệ thần kinh thay đổi theo trải nghiệm."}')
        return Response("{truncated")

    monkeypatch.setattr("requests.post", fake_post)
    translator = LocalTranslator(cache_path=tmp_path / "translations.db", model="fixture-translator:latest")
    chunks = [{"safe_text": f"The mind and body respond to trauma and the nervous system changes with experience number {index}. " * 2} for index in range(3)]
    result = translator.translate(chunks)
    assert set(result) == {0, 1}
    assert len(calls) == 2


def test_cross_book_examples_do_not_leak_held_out_books(tmp_path):
    names = [f"book-{index}.pdf" for index in range(100)]
    held_out = next(name for name in names if int(hashlib.sha256(name.encode()).hexdigest(), 16) % 10 == 0)
    training = next(name for name in names if int(hashlib.sha256(name.encode()).hexdigest(), 16) % 10 != 0)
    training_two = next(name for name in names if name != training and int(hashlib.sha256(name.encode()).hexdigest(), 16) % 10 != 0)

    def source(name, source_id):
        return {"id": source_id, "filename": name, "page": 12, "text": f"Đoạn sách {name} bàn về trí nhớ."}

    def record(name, sources, cross=False):
        return {
            "pair": {"filename": name, "title": name, "cross_book": cross, "sources": sources},
            "language": "vi", "translations": {},
            "items": [{"question": "Trí nhớ thay đổi ra sao?", "answer": "Các nguồn trình bày trí nhớ. [S1] [S2]"}],
        }

    records = [
        record(training, [source(training, "S1"), source(training, "S2")]),
        record(training_two, [source(training_two, "S1"), source(training_two, "S2")]),
        record(held_out, [source(held_out, "S1"), source(held_out, "S2")]),
        record("cross", [source(training, "S1"), source(held_out, "S2")], cross=True),
        record("cross-safe", [source(training, "S1"), source(training_two, "S2")], cross=True),
    ]
    summary = write_training_files(records, tmp_path, "local-teacher")
    train = (tmp_path / "train.jsonl").read_text()
    valid = (tmp_path / "valid.jsonl").read_text()
    assert summary["skipped_cross_book_pairs_for_holdout"] == 1
    assert summary["train_cross_book_pairs"] == 1
    assert held_out not in train
    assert training not in valid
    assert len(train.splitlines()) == 3
    assert len(valid.splitlines()) == 1
    assert json.loads(valid.splitlines()[0])["messages"][-1]["content"]


def test_cross_book_selection_can_exclude_validation_books(tmp_path, monkeypatch):
    db = tmp_path / "books.db"
    held_out = "The Body Keeps the Score.pdf"
    training_en = "Other English Psychology.pdf"
    training_vi = "Sach tam ly tieng Viet.pdf"
    english_text = ("The mind and the body respond to trauma, and the response of the nervous system changes with experience. " * 8)
    vietnamese_text = ("Sang chấn tâm lý ảnh hưởng đến cơ thể và cảm xúc của con người theo nhiều cách khác nhau. " * 8)
    import pymupdf
    from html import escape
    from app import config, training_data
    from app import vision_ocr
    monkeypatch.setattr(config, "SRC_DIR", tmp_path)
    monkeypatch.setattr(training_data, "SRC_DIR", tmp_path, raising=False)
    cache = tmp_path / "ocr-cache"; cache.mkdir()
    monkeypatch.setattr(vision_ocr, "CACHE_DIR", cache)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE documents (filename TEXT, status TEXT, filepath TEXT, file_hash TEXT, is_scanned INTEGER)")
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, filename TEXT, book_title TEXT, page_num INTEGER, text TEXT)")
        for name, content in ((held_out, english_text), (training_en, english_text), (training_vi, vietnamese_text)):
            p = tmp_path / name
            pdf = pymupdf.open()
            for _ in range(12):
                pdf.new_page()
            pdf[11].insert_htmlbox((30, 30, 550, 760), "<p>" + escape(content) + "</p>",
                                   css="p {font-size: 10px;}")
            native = pdf[11].get_text()
            assert "".join(native.split()) == "".join(content.split()), "fixture preserves actual native source text"
            pdf.save(p); pdf.close()
            conn.execute("INSERT INTO documents VALUES (?, 'indexed', ?, ?, 0)",
                         (name, str(p), hashlib.sha256(p.read_bytes()).hexdigest()))
            conn.execute("INSERT INTO chunks (filename, book_title, page_num, text) VALUES (?, ?, 12, ?)", (name, name, content))

    original = list(select_cross_book_pairs(db))
    safe = list(select_cross_book_pairs(db, exclude_files={held_out}))

    assert original[0]["sources"][0]["filename"] == held_out
    assert safe[0]["sources"][0]["filename"] == training_en
    assert all(source["filename"] != held_out for pair in safe for source in pair["sources"])
