"""Independent acceptance contracts: no personal schedule is used as training data."""
import base64
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMETABLE_DB_PATH", str(tmp_path / "timetable.db"))
    monkeypatch.setenv("TIMETABLE_IMAGES_DIR", str(tmp_path / "images"))
    return TestClient(server.app)


def test_timetable_is_available_and_empty_on_fresh_store(client):
    response = client.get("/api/timetable")
    assert response.status_code == 200, response.text
    assert response.json()["entries"] == []


def test_image_payload_is_validated_before_model_call(client):
    response = client.post("/api/timetable/extract", json={
        "filename": "schedule.png", "image_base64": base64.b64encode(b"not an image").decode()
    })
    assert response.status_code == 400, response.text


def test_confirmation_cannot_invent_or_bypass_a_missing_draft(client):
    response = client.post("/api/timetable/confirm", json={
        "draft_id": "0" * 32,
        "entries": [{"course": "Linear algebra", "weekday": 1,
                     "start_time": "08:00", "end_time": "09:30", "room": "A101"}]
    })
    assert response.status_code == 404, response.text


def test_export_needs_explicit_date_range(client):
    response = client.get("/api/timetable/export.ics")
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("entry", [
    {"course": "Math", "weekday": 0, "start_time": "08:00", "end_time": "09:00"},
    {"course": "Math", "weekday": 8, "start_time": "08:00", "end_time": "09:00"},
    {"course": "Math", "weekday": 1, "start_time": "25:00", "end_time": "26:00"},
    {"course": "Math", "weekday": 1, "start_time": "09:00", "end_time": "08:00"},
    {"course": "Math", "weekday": 1, "start_time": "08:00", "end_time": "08:00"},
    {"course": "  ", "weekday": 1, "start_time": "08:00", "end_time": "09:00"},
])
def test_invalid_manual_schedule_is_rejected_without_writing(client, entry):
    response = client.post("/api/timetable/manual", json=entry)
    assert response.status_code in (400, 422), response.text
    assert client.get("/api/timetable").json()["entries"] == []


def test_unknown_image_weekday_is_not_invented_as_monday():
    from app.timetable_vision import normalize_weekday
    assert normalize_weekday(None) is None
    assert normalize_weekday("không rõ") is None
    assert normalize_weekday("1") == 1
    assert normalize_weekday("2") == 2
    assert normalize_weekday("Thứ 2") == 1
    assert normalize_weekday("13") is None


def test_empty_confirmation_cannot_erase_an_existing_schedule(client):
    from app import timetable_store
    original = {"course": "Physics", "weekday": 2, "start_time": "10:00", "end_time": "11:00"}
    assert client.post("/api/timetable/manual", json=original).status_code == 200
    timetable_store.save_draft("a" * 32, "b" * 64, "test.png", [], [])
    response = client.post("/api/timetable/confirm", json={"draft_id": "a" * 32, "entries": []})
    assert response.status_code in (400, 422), response.text
    assert client.get("/api/timetable").json()["entries"][0]["course"] == "Physics"


def test_overlapping_manual_schedule_requires_resolution(client):
    first = {"course": "Math", "weekday": 1, "start_time": "08:00", "end_time": "09:30"}
    second = {"course": "Physics", "weekday": 1, "start_time": "09:00", "end_time": "10:00"}
    assert client.post("/api/timetable/manual", json=first).status_code == 200
    response = client.post("/api/timetable/manual", json=second)
    assert response.status_code in (409, 422), response.text
    assert len(client.get("/api/timetable").json()["entries"]) == 1


def fake_vision(monkeypatch, output, done_reason="stop", digest="d" * 64, *, legacy=False):
    from app import timetable_vision as vision
    from app.native_vision import NativeVisionClient
    monkeypatch.setattr(vision, "sys", SimpleNamespace(platform="win32" if legacy else "darwin"))
    def fixture_digest():
        if not digest:
            raise vision.VLMUnavailableError("Fixture has no verified model digest")
        return digest
    monkeypatch.setattr(vision, "get_vlm_digest", fixture_digest)
    monkeypatch.setattr(NativeVisionClient, "available", classmethod(lambda cls, *a, **kw: True))
    def generate(self, *args, **kwargs):
        if done_reason == "length":
            raise ValueError("Fixture output reached token length limit")
        return {"text": json.dumps(output), "finish_reason": done_reason}
    monkeypatch.setattr(NativeVisionClient, "generate_from_image", generate)
    if legacy:
        model_response = SimpleNamespace(status_code=200, text="", json=lambda: {
            "response": json.dumps(output), "done_reason": done_reason, "done": True
        })
        import requests
        def legacy_post(session, url, **kwargs):
            assert session.trust_env is False
            assert kwargs.get("allow_redirects") is False
            if url.endswith("/api/show"):
                return SimpleNamespace(status_code=200, json=lambda: {"details": {"format": "gguf", "family": "qwen"}})
            return model_response
        monkeypatch.setattr(requests.Session, "post", legacy_post)
    monkeypatch.setattr(vision.gpu_coordinator, "acquire_for_inference", lambda **kw: nullcontext())
    monkeypatch.setattr(vision.gpu_coordinator, "check_inference_allowed", lambda: (True, ""))


def image_request(client):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=100)
    page.insert_text((10, 30), "Timetable")
    data = page.get_pixmap().tobytes("png")
    doc.close()
    return client.post("/api/timetable/extract", json={
        "filename": "fixture.png", "image_base64": base64.b64encode(data).decode()
    })


def test_missing_image_fields_stay_unknown_in_draft(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday": None,
               "start_time": None, "end_time": None, "period": "1-3", "room": None}],
               "uncertainties": ["Ngày không rõ"]})
    response = image_request(client)
    assert response.status_code == 200, response.text
    entry = response.json()["entries"][0]
    assert entry["weekday"] is None
    assert entry["start_time"] is None and entry["end_time"] is None
    assert client.get("/api/timetable").json()["entries"] == []


def test_unreadable_course_stays_unknown_without_failing_ocr_corroboration(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": None, "weekday_label": "Thứ 2",
                "start_time": "08:00", "end_time": "09:00"}], "uncertainties": ["Tên môn bị mờ"]})
    response = image_request(client)
    assert response.status_code == 200, response.text
    assert response.json()['entries'][0]['course'] is None
    assert client.get('/api/timetable').json()['entries'] == []


@pytest.mark.parametrize("output", [
    {"entries": "a timetable", "uncertainties": []},
    {"entries": [{"course": "Math"}, "unparsed second course"], "uncertainties": []},
    {"entries": [], "uncertainties": "the picture is blurry"},
])
def test_malformed_output_is_not_silently_shortened(client, monkeypatch, output):
    fake_vision(monkeypatch, output)
    response = image_request(client)
    assert response.status_code == 422, response.text


def test_truncated_model_output_cannot_be_cached_as_complete(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday": 1,
               "start_time": "08:00", "end_time": "09:00"}], "uncertainties": []},
               done_reason="length")
    response = image_request(client)
    assert response.status_code == 422, response.text
    from app import timetable_store
    with timetable_store.get_connection() as conn:
        assert conn.execute("select count(*) from timetable_vlm_cache").fetchone()[0] == 0


def test_missing_digest_is_unavailable_instead_of_hardcoded_provenance(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [], "uncertainties": []}, digest=None)
    response = image_request(client)
    assert response.status_code == 503, response.text


def test_copied_day_label_controls_iso_day(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday_label": "Thứ 4", "weekday": 4,
                "start_time": "08:00", "end_time": "09:00"}], "uncertainties": []})
    response = image_request(client)
    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["weekday"] == 3


def test_unlabelled_model_integer_is_not_trusted_as_day(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday": 2,
                "start_time": "08:00", "end_time": "09:00"}], "uncertainties": []})
    response = image_request(client)
    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["weekday"] is None


def test_draft_provenance_survives_reload(client, monkeypatch):
    from app import timetable_vision as vision
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday_label": "Monday",
                "start_time": "08:00", "end_time": "09:00"}], "uncertainties": []})
    response = image_request(client)
    assert response.status_code == 200, response.text
    result = response.json()
    draft = client.get("/api/timetable/drafts/" + result["draft_id"]).json()
    assert draft["model_digest"] == "d" * 64
    assert draft["prompt_version"] == vision.PROMPT_VERSION
    assert draft["options"]["temperature"] == 0
    assert draft["raw_model_output"]
    assert client.get("/api/timetable/images/" + result["image_hash"]).status_code == 200


@pytest.mark.parametrize("bad_course", [["Math"], {"name": "Math"}, True])
def test_model_course_type_is_checked(client, monkeypatch, bad_course):
    fake_vision(monkeypatch, {"entries": [{"course": bad_course, "weekday_label": "Monday"}],
                            "uncertainties": []})
    response = image_request(client)
    assert response.status_code == 422, response.text

@pytest.mark.parametrize("field,value", [
    ("weekday_label", True), ("start_time", ["08:00"]),
    ("room", {"code": "A101"}), ("period", [1, 3]),
])
def test_other_model_fields_have_nullable_string_types(client, monkeypatch, field, value):
    entry = {"course": "Math", "weekday_label": "Monday", "start_time": "08:00", "end_time": "09:00"}
    entry[field] = value
    fake_vision(monkeypatch, {"entries": [entry], "uncertainties": []})
    assert image_request(client).status_code == 422


def test_large_image_is_bounded_for_inference_and_uses_schema(client, monkeypatch):
    import io
    from PIL import Image
    from app import timetable_vision as vision
    fake_vision(monkeypatch, {"entries": [], "uncertainties": []}, legacy=True)
    import requests
    post = requests.Session.post
    seen = []
    def capture(session, url, **kwargs):
        if url.endswith("/api/generate"):
            seen.append(kwargs["json"])
        return post(session, url, **kwargs)
    monkeypatch.setattr(requests.Session, "post", capture)
    buf = io.BytesIO()
    large = Image.new("RGB", (3000, 1000), "white")
    from PIL import ImageDraw
    ImageDraw.Draw(large).text((20, 20), "Timetable", fill="black")
    large.save(buf, "PNG")
    response = client.post("/api/timetable/extract", json={"filename": "large.png", "image_base64": base64.b64encode(buf.getvalue()).decode()})
    assert response.status_code == 200, response.text
    assert isinstance(seen[0]["format"], dict)
    with Image.open(io.BytesIO(base64.b64decode(seen[0]["images"][0]))) as sent:
        assert max(sent.size) <= 1600
    with Image.open(io.BytesIO(client.get("/api/timetable/images/"+response.json()["image_hash"]).content)) as original:
        assert original.size == (3000, 1000)


def test_confirmation_preserves_original_and_reviewed_edits(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Read from image", "weekday_label": "Monday", "start_time": "08:00", "end_time": "09:00"}], "uncertainties": []})
    result = image_request(client).json()
    reviewed = [{"course": "Corrected course", "weekday": 1, "start_time": "08:00", "end_time": "09:00"}]
    response = client.post("/api/timetable/confirm", json={"draft_id":result["draft_id"],"entries":reviewed})
    assert response.status_code == 200, response.text
    stored = client.get("/api/timetable/drafts/"+result["draft_id"]).json()
    assert stored["entries"][0]["course"] == "Read from image"
    assert stored["confirmed_at"]
    assert stored["confirmed_entries"][0]["course"] == "Corrected course"


def test_incomplete_image_reports_missing_fields_and_retains_readable_values(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday_label": None,
                "start_time": None, "end_time": None, "period": "1-3"}], "uncertainties": []})
    result = image_request(client).json()
    assessment = result["data_sufficiency"]
    assert assessment["status"] == "insufficient_information"
    assert assessment["missing_fields"] == [{"row": 1, "fields": ["weekday", "start_time", "end_time"]}]
    assert result["entries"][0]["course"] == "Math"
    assert result["entries"][0]["start_time"] is None
    assert any("gửi thêm ảnh" in warning.lower() for warning in result["uncertainties"])
    restored = client.get("/api/timetable/drafts/" + result["draft_id"]).json()
    assert restored["uncertainties"] == result["uncertainties"]
    cached = image_request(client).json()
    assert cached["cached"] is True
    assert cached["data_sufficiency"] == assessment
    assert cached["uncertainties"] == result["uncertainties"]


@pytest.mark.parametrize("course,start,end,missing", [
    (None, "08:00", "09:00", "course"),
    ("Math...", "08:00", "09:00", "course_full_text"),
    ("Math", "12:00", "12:00", "valid_time_range"),
])
def test_incomplete_or_invalid_rows_cannot_claim_sufficient_information(client, monkeypatch, course, start, end, missing):
    fake_vision(monkeypatch, {"entries": [{"course": course, "weekday_label": "Monday",
                "start_time": start, "end_time": end}], "uncertainties": []})
    response = image_request(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["data_sufficiency"]["status"] == "insufficient_information"
    assert missing in result["data_sufficiency"]["missing_fields"][0]["fields"]
    assert client.get("/api/timetable").json()["entries"] == []


def test_complete_visible_rows_do_not_assert_full_semester_coverage(client, monkeypatch):
    fake_vision(monkeypatch, {"entries": [{"course": "Math", "weekday_label": "Monday",
                "start_time": "08:00", "end_time": "09:00"}], "uncertainties": []})
    result = image_request(client).json()
    assert result["data_sufficiency"]["status"] == "visible_rows_have_required_fields"
    assert result["data_sufficiency"]["scope"] == "visible_image_only"
    assert result["data_sufficiency"]["full_schedule_verified"] is False
