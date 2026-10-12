"""Independent continuation probes; isolated fixtures never train or promote real weights."""
import copy
import hashlib
import json
import runpy
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_ocr_full_transcript_does_not_authorize_extra_text(tmp_path, monkeypatch):
    from test_codex_study_training_contract import _ocr_training_fixture
    td, db = _ocr_training_fixture(tmp_path, monkeypatch, 1, True)
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        doc = conn.execute("SELECT * FROM documents").fetchone()
    assert not td.is_chunk_allowed_in_training(
        doc, 1, "Only page one was reviewed. Invented claim beyond the reviewed transcript.")


def _native_doc(tmp_path, suffix=".pdf"):
    passage = "A real source explains that independent vectors have no nonzero combination equal to zero."
    path = tmp_path / ("native" + suffix)
    if suffix == ".pdf":
        import pymupdf
        pdf = pymupdf.open(); page = pdf.new_page()
        page.insert_textbox((30, 30, 550, 780), passage)
        pdf.save(path); pdf.close()
    else:
        path.write_text(passage)
    return {"filename": path.name, "filepath": str(path), "is_scanned": 0,
            "file_hash": digest(path.read_bytes())}, passage, path


def test_native_full_page_does_not_authorize_extra_text(tmp_path):
    from app.training_data import is_chunk_allowed_in_training
    doc, passage, _ = _native_doc(tmp_path)
    assert not is_chunk_allowed_in_training(doc, 1, passage + " Invented unsupported theorem.", {})


def test_native_current_source_hash_is_required(tmp_path):
    from app.training_data import is_chunk_allowed_in_training
    doc, passage, _ = _native_doc(tmp_path)
    doc["file_hash"] = "0" * 64
    assert not is_chunk_allowed_in_training(doc, 1, passage, {})


def test_native_unknown_source_hash_cannot_authorize_training(tmp_path):
    from app.training_data import is_chunk_allowed_in_training
    doc, passage, _ = _native_doc(tmp_path)
    doc["file_hash"] = ""
    assert not is_chunk_allowed_in_training(doc, 1, passage, {})


def test_genuine_cleaned_native_text_is_retained(tmp_path):
    from app.training_data import is_chunk_allowed_in_training
    from app.text_cleaner import normalize_vietnamese_text
    import pymupdf
    path = tmp_path / "hyphenated.pdf"
    pdf = pymupdf.open(); page = pdf.new_page()
    raw = "A psycho-\nlogy lesson examines learning through conditioning and observable behavior."
    page.insert_textbox((30, 30, 550, 780), raw)
    pdf.save(path); pdf.close()
    doc = {"filename": path.name, "filepath": str(path), "file_hash": digest(path.read_bytes()), "is_scanned": 0}
    assert is_chunk_allowed_in_training(doc, 1, normalize_vietnamese_text(raw), {})


def test_native_docx_units_are_retained(tmp_path):
    import zipfile
    from app.training_data import is_chunk_allowed_in_training
    passage = "A real document explains vector independence using only conditions supported by the source."
    path = tmp_path / "native.docx"
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + passage + '</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, 'w') as zf: zf.writestr('word/document.xml', xml)
    doc = {"filename": path.name, "filepath": str(path), "file_hash": digest(path.read_bytes()), "is_scanned": 0}
    assert is_chunk_allowed_in_training(doc, 1, passage, {})


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_native_text_units_remain_usable_without_ocr(tmp_path, suffix):
    from app.training_data import is_chunk_allowed_in_training
    doc, passage, _ = _native_doc(tmp_path, suffix)
    assert is_chunk_allowed_in_training(doc, 1, passage, {})


def test_missing_original_cannot_be_authorized_by_old_ocr_cache(tmp_path, monkeypatch):
    from test_codex_study_training_contract import _ocr_training_fixture
    td, db = _ocr_training_fixture(tmp_path, monkeypatch, 1, True)
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        doc = conn.execute("SELECT * FROM documents").fetchone()
    (tmp_path / "scanned.pdf").unlink()
    assert not td.is_chunk_allowed_in_training(doc, 1, "Only page one was reviewed")


def test_mlx_inference_applies_requested_sampler_and_seed(monkeypatch, tmp_path, capsys):
    request = {"model_path": str(tmp_path), "messages": [{"role": "user", "content": "Hello"}],
               "temperature": 0.17, "seed": 37, "max_tokens": 19}
    (tmp_path / "config.json").write_text(json.dumps({"max_position_embeddings": 32768}))
    seen = {}
    sampler = object()
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs): return "prompt"
        def encode(self, text): return [1]
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *a, **k: Tokenizer())))
    def generate(model, tokenizer, **kwargs):
        seen["generate"] = kwargs
        return "answer"
    def make_sampler(*args, **kwargs):
        seen["sampler"] = (args, kwargs)
        return sampler
    monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(
        generate=generate, load=lambda *a, **k: (object(), Tokenizer())))
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", types.SimpleNamespace(make_sampler=make_sampler))
    core = types.SimpleNamespace(random=types.SimpleNamespace(seed=lambda value: seen.update(seed=value)))
    monkeypatch.setitem(sys.modules, "mlx", types.SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    runpy.run_path(str(ROOT / "app/mlx_infer.py"), run_name="__main__")
    assert seen.get("seed") == 37
    assert seen["generate"].get("sampler") is sampler
    assert seen["generate"]["max_tokens"] == 19
    args, kwargs = seen["sampler"]
    assert kwargs.get("temp", args[0] if args else None) == 0.17


def test_default_query_normalization_uses_shared_base(monkeypatch):
    from app import query_normalizer as qn
    from app.trained_client import TrainedModelClient
    calls = []
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls: True))
    monkeypatch.setattr(TrainedModelClient, "chat_complete", lambda self, *a, **k:
                        calls.append((a, k)) or '{"normalized_query":"ánh xạ tuyến tính là gì?"}')
    monkeypatch.setattr(qn.requests, "post", lambda *a, **k: pytest.fail("Default MLX query sent to Ollama"))
    assert qn.normalize_query_with_model("anh xa tuyen tinh la gi?") == "ánh xạ tuyến tính là gì?"
    assert len(calls) == 1


def test_default_training_teacher_uses_shared_base(monkeypatch):
    from app import training_data as td
    from app.trained_client import TrainedModelClient
    calls = []
    answer = "Nguồn thứ nhất trình bày điều kiện của vectơ độc lập [S1]. Nguồn thứ hai giải thích rằng kết luận cần dựa trên các điều kiện đã cho [S2]."
    response = json.dumps({"items": [{"question": "Hai nguồn này giải thích điều kiện như thế nào?", "answer": answer}]}, ensure_ascii=False)
    monkeypatch.setattr(TrainedModelClient, "available", classmethod(lambda cls: True))
    monkeypatch.setattr(TrainedModelClient, "chat_stream", lambda self, *a, **k: calls.append((a, k)) or iter([response]))
    monkeypatch.setattr(td.requests, "post", lambda *a, **k: pytest.fail("Default teacher sent to a second text base"))
    pair = {"filename": "sample.pdf", "book_title": "sample", "sources": [
        {"id": "S1", "book_title": "sample", "filename": "sample.pdf", "page_num": 1,
         "text": "Các vectơ độc lập phải thỏa mãn điều kiện rõ ràng từ nguồn thứ nhất."},
        {"id": "S2", "book_title": "sample", "filename": "sample.pdf", "page_num": 2,
         "text": "Kết luận phải được nêu theo các điều kiện cụ thể có trong nguồn thứ hai."}]}
    result = td.ask_teacher(pair)
    assert result["items"][0]["answer"] == answer
    assert len(calls) == 1


def test_default_summary_uses_shared_base_and_records_truncation(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from app.history import HistoryStore
    from app.topic_summarizer import TopicSummarizer
    from app.trained_client import TrainedModelClient
    from app import gpu_lock
    store = HistoryStore(tmp_path / 'history.db')
    sid = store.create_session()['id']
    store.add_turn(sid, 'Ánh xạ tuyến tính có điều kiện gì?', 'Bảo toàn phép cộng và nhân vô hướng.', [], 'local')
    summarizer = TopicSummarizer(store)
    monkeypatch.setattr(TrainedModelClient, 'available', classmethod(lambda cls: True))
    def answer(self, *a, **k):
        self.last_done_reason = 'length'
        return json.dumps({'title':'Ánh xạ tuyến tính', 'summary':'Bảo toàn phép cộng và phép nhân vô hướng.'})
    monkeypatch.setattr(TrainedModelClient, 'chat_complete', answer)
    monkeypatch.setattr(summarizer.ollama, 'check_health', lambda: pytest.fail('Default summary consulted Ollama'))
    monkeypatch.setattr(gpu_lock.gpu_coordinator, 'check_inference_allowed', lambda: (True,''))
    monkeypatch.setattr(gpu_lock.gpu_coordinator, 'acquire_for_inference', lambda **k: nullcontext())
    result = summarizer.summarize_session_sync(sid)
    assert result['status'] != 'completed'
    assert store.get_session(sid)['summary_status'] == 'pending'


def _bound_registry_fixture(tmp_path):
    """Structurally paired hypothetical fixture; never used as product acceptance."""
    from app import active_registry as ar
    ev = ROOT / "data/evaluation"
    load = lambda p: json.loads(p.read_text())
    suite_path = ev / "codex-study-frozen-holdout-2026-10-03.json"
    br_path = ev / "codex-study-base3b-raw-2026-10-03.json"
    bv_path = ev / "codex-study-base3b-content-review-2026-10-03.json"
    suite, br, bv = load(suite_path), load(br_path), load(bv_path)
    protocol = load(ROOT / "data/training/v9_multidomain_grounded/evaluation_protocol.json")
    adapter = tmp_path / "hypothetical-adapter"; adapter.mkdir()
    (adapter / "adapters.safetensors").write_bytes(b"unit-fixture-weights")
    (adapter / "adapter_config.json").write_text('{}')
    ah = {p.name: digest(p.read_bytes()) for p in adapter.iterdir()}
    cr = copy.deepcopy(br)
    cr.update(adapter=str(adapter), baseline_raw_sha256=digest(br_path.read_bytes()),
              base_files_sha256=bv["base_files_sha256"], adapter_files_sha256=ah)
    frozen = {c["id"]: c for c in suite["cases"]}
    for case in cr["cases"]: case["messages"] = frozen[case["id"]]["messages"]
    cv = copy.deepcopy(bv)
    for reviewed in cv["cases"].values(): reviewed.update(grade="pass", reason="Hypothetical unit-fixture grade")
    scores = {}
    for kind in ("target", "retention"):
        ids = [c["id"] for c in suite["cases"] if c["kind"] == kind]
        grades = [bv["cases"][cid]["grade"] for cid in ids]
        scores[kind] = {"baseline": {g: grades.count(g) for g in ("pass", "partial", "fail")},
                        "candidate": {"pass": len(ids), "partial": 0, "fail": 0, "pass_rate": 1.0}}
        scores[kind]["baseline"]["pass_rate"] = grades.count("pass") / len(ids)
    decision = {"schema": "codex-study-paired-decision-v1", "status": "ACCEPTED", "promoted": True,
                "reviewer": "Codex", "scores": scores, "absolute_gain": 0.35, "retention_drop": -0.625,
                "retention_regressions": [], "critical_regressions": [], "no_source_failures": [],
                "gates": {k: True for k in ("target_accuracy", "absolute_gain", "retention_drop",
                          "retention_regressions", "critical_regressions", "no_source_all_pass")}, "failed_gates": [],
                "limits": "Frozen 28-case bounded assessment; not general capability certification."}
    paths = {"suite": suite_path, "baseline_raw": br_path, "baseline_review": bv_path}
    payloads = {"protocol": protocol, "candidate_raw": cr, "candidate_review": cv, "decision": decision}
    def persist():
        for key, data in payloads.items():
            paths[key] = tmp_path / (key + ".json")
            if key == "candidate_review":
                data["raw_output_sha256"] = digest(paths["candidate_raw"].read_bytes())
                data["raw_output_path"] = str(paths["candidate_raw"])
            paths[key].write_text(json.dumps(data, ensure_ascii=False))
        dossier = {"schema": "codex-study-acceptance-v1", "trial_id": "hypothetical-unit-contract",
                   "reviewer": "Codex", "reviewed_by_codex": True,
                   "bindings": {k: {"path": str(p), "sha256": digest(p.read_bytes())} for k, p in paths.items()},
                   "model_identity": {"repo": bv["base_repo"], "revision": bv["base_revision"],
                                      "files_sha256": bv["base_files_sha256"]},
                   "adapter_identity": {"path": str(adapter), "files_sha256": ah}}
        return dossier
    return ar, adapter, payloads, persist


def test_canonical_positive_control_agrees_with_independent_oracle(tmp_path):
    ar, adapter, data, persist = _bound_registry_fixture(tmp_path)
    dossier = persist()
    paths = {k: Path(v["path"]) for k,v in dossier["bindings"].items()}
    # Independent arithmetic and input checks live with this test. A temporary
    # script outside the repository must not determine whether it can run.
    load = lambda key: json.loads(paths[key].read_text())
    suite, baseline, candidate = load("suite"), load("baseline_review"), load("candidate_review")
    frozen = {c["id"]: c for c in suite["cases"]}
    raw = load("candidate_raw")
    assert len(frozen) == len(raw["cases"]) == len(candidate["cases"]) == 28
    assert set(frozen) == {c["id"] for c in raw["cases"]} == set(candidate["cases"])
    assert candidate["raw_output_sha256"] == digest(paths["candidate_raw"].read_bytes())
    assert raw["baseline_raw_sha256"] == digest(paths["baseline_raw"].read_bytes())
    for case in raw["cases"]:
        assert case["messages"] == frozen[case["id"]]["messages"]
    rates = {}
    for kind in ("target", "retention"):
        ids = [cid for cid, c in frozen.items() if c["kind"] == kind]
        assert ids
        rates[kind] = tuple(sum(review["cases"][cid]["grade"] == "pass" for cid in ids) / len(ids)
                            for review in (baseline, candidate))
    assert rates["target"][1] == 1.0
    assert rates["target"][1] - rates["target"][0] == pytest.approx(0.35)
    assert rates["retention"][1] == 1.0
    assert rates["retention"][0] - rates["retention"][1] == pytest.approx(-0.625)
    assert all(c["grade"] == "pass" for c in candidate["cases"].values())
    # This is a hypothetical registry contract control, never model acceptance.
    assert ar._is_valid_evidence_payload(dossier, tmp_path, adapter, ROOT / "data/models/qwen2.5-3b-4bit")


@pytest.mark.parametrize("attack", ["question", "decoding", "missing_status", "review_passage", "threshold", "decision"])
def test_registry_rejects_canonical_files_with_changed_contract(tmp_path, attack):
    ar, adapter, data, persist = _bound_registry_fixture(tmp_path)
    cid = data["candidate_raw"]["cases"][0]["id"]
    if attack == "question": data["candidate_raw"]["cases"][0]["question"] = "A different prompt"
    if attack == "decoding": data["candidate_raw"]["decoding"]["max_tokens"] = 999
    if attack == "missing_status": data["candidate_raw"].pop("status")
    if attack == "review_passage": data["candidate_review"]["cases"][cid]["passage_sha256"] = "0" * 64
    if attack == "threshold": data["protocol"]["acceptance"]["min_target_accuracy"] = 0.0
    if attack == "decision": data["decision"]["scores"]["target"]["candidate"]["pass_rate"] = 0.0
    dossier = persist()
    assert not ar._is_valid_evidence_payload(dossier, tmp_path, adapter, ROOT / "data/models/qwen2.5-3b-4bit")


def test_registry_requires_evaluated_model_and_adapter_identities(tmp_path):
    ar, adapter, data, persist = _bound_registry_fixture(tmp_path)
    dossier = persist(); dossier.pop("model_identity"); dossier.pop("adapter_identity")
    assert not ar._is_valid_evidence_payload(dossier, tmp_path, adapter, ROOT / "data/models/qwen2.5-3b-4bit")
