"""Regression tests for FIX-SRC-WATCH-03: freshness read/cache/admission races and single cancellable worker path."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import pytest

from app import file_lock as fcntl
from app import native_embedding, source_freshness
from app.gpu_lock import GPULockManager, IndexingCancelledError
from app.indexer import KnowledgeIndexer
from app.native_embedding import BGE_NATIVE_IDENTITY, NativeEmbeddingClient
from app.searcher import HybridSearcher


def _indexed_doc_fixture(tmp_path: Path, text: str = "The old approved original holds seventeen units.") -> dict:
    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    target = src / "source.txt"
    target.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    doc = {
        "id": 1,
        "filename": "source.txt",
        "filepath": str(target),
        "file_hash": digest,
        "status": "indexed",
    }
    return {
        "src": src,
        "data": data,
        "db_path": data / "knowledge_base.db",
        "file": target,
        "hash": digest,
        "doc": doc,
    }


def test_freshness_fails_closed_on_in_place_mutation_after_read_and_does_not_poison_cache(tmp_path, monkeypatch):
    """INJECTED deterministic read/stat race (in-place overwrite after read) must fail closed without caching old hash."""
    source_freshness.clear_freshness_cache()
    fx = _indexed_doc_fixture(tmp_path)
    real_hash = source_freshness._compute_file_sha256
    mutated = False

    def mutate_after_read(path: Path) -> str:
        nonlocal mutated
        digest = real_hash(path)
        if not mutated:
            path.write_text("The changed original holds twenty nine units.", encoding="utf-8")
            mutated = True
        return digest

    monkeypatch.setattr(source_freshness, "_compute_file_sha256", mutate_after_read)
    res = source_freshness.assess_document_freshness(
        fx["doc"],
        src_dir=fx["src"],
        db_path=fx["db_path"],
        watcher_items={},
    )
    assert res.fresh is False
    assert res.status == "needs_update"
    assert res.reason_code in {"source_stale", "source_identity_mismatch"}
    assert hashlib.sha256(fx["file"].read_bytes()).hexdigest() != fx["hash"]

    # Cache must NOT retain stale digest against old or new stat; subsequent check must also fail closed.
    monkeypatch.setattr(source_freshness, "_compute_file_sha256", real_hash)
    with source_freshness._CACHE_LOCK:
        assert fx["hash"] not in source_freshness._FILE_HASH_CACHE.values()
    res_after = source_freshness.assess_document_freshness(
        fx["doc"],
        src_dir=fx["src"],
        db_path=fx["db_path"],
        watcher_items={},
    )
    assert res_after.fresh is False
    assert res_after.status == "needs_update"


def test_freshness_fails_closed_on_atomic_replacement_after_read_and_does_not_poison_cache(tmp_path, monkeypatch):
    """INJECTED deterministic read/stat race (same-length atomic os.replace after read) must fail closed."""
    source_freshness.clear_freshness_cache()
    old_text = "OLD_APPROVED_BYTES_001_SEVENTEEN"
    new_text = "NEW_REPLACED_BYTES_002_TWENTYNIN"
    assert len(old_text.encode("utf-8")) == len(new_text.encode("utf-8"))
    fx = _indexed_doc_fixture(tmp_path, text=old_text)
    real_hash = source_freshness._compute_file_sha256
    replaced = False

    def replace_after_read(path: Path) -> str:
        nonlocal replaced
        digest = real_hash(path)
        if not replaced:
            staging = tmp_path / "staging.txt"
            staging.write_text(new_text, encoding="utf-8")
            os.replace(staging, path)
            replaced = True
        return digest

    monkeypatch.setattr(source_freshness, "_compute_file_sha256", replace_after_read)
    res = source_freshness.assess_document_freshness(
        fx["doc"],
        src_dir=fx["src"],
        db_path=fx["db_path"],
        watcher_items={},
    )
    assert res.fresh is False
    assert res.status == "needs_update"

    monkeypatch.setattr(source_freshness, "_compute_file_sha256", real_hash)
    with source_freshness._CACHE_LOCK:
        assert fx["hash"] not in source_freshness._FILE_HASH_CACHE.values()
    res_after = source_freshness.assess_document_freshness(
        fx["doc"],
        src_dir=fx["src"],
        db_path=fx["db_path"],
        watcher_items={},
    )
    assert res_after.fresh is False
    assert res_after.status == "needs_update"


def test_freshness_direct_stream_read_detects_mid_read_in_place_mutation(tmp_path, monkeypatch):
    """Real _compute_file_sha256 validates opened-file identity before and after reading chunks."""
    source_freshness.clear_freshness_cache()
    big_old = ("A" * 70000) + "_TAIL_OLD"
    fx = _indexed_doc_fixture(tmp_path, text=big_old)

    orig_open = Path.open
    mutated = False

    class _MutatingOnSecondRead:
        def __init__(self, inner, target_path: Path):
            self._inner = inner
            self._target = target_path
            self._reads = 0

        def fileno(self):
            return self._inner.fileno()

        def read(self, size=-1):
            nonlocal mutated
            data = self._inner.read(size)
            self._reads += 1
            if self._reads == 1 and not mutated:
                self._target.write_text(("B" * 70000) + "_TAIL_NEW", encoding="utf-8")
                mutated = True
            return data

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    def wrapped_open(self, mode="r", *args, **kwargs):
        handle = orig_open(self, mode, *args, **kwargs)
        if self == fx["file"] and "rb" in str(mode):
            return _MutatingOnSecondRead(handle, fx["file"])
        return handle

    monkeypatch.setattr(Path, "open", wrapped_open)
    res = source_freshness.assess_document_freshness(
        fx["doc"],
        src_dir=fx["src"],
        db_path=fx["db_path"],
        watcher_items={},
    )
    assert res.fresh is False
    assert res.status == "needs_update"


def test_freshness_fails_closed_on_cache_hit_race_and_preserves_stable_cache_hits(tmp_path, monkeypatch):
    """Cache-hit admission verifies opened-file identity after lookup; unchanged files still avoid re-hashing."""
    source_freshness.clear_freshness_cache()
    fx = _indexed_doc_fixture(tmp_path)

    hash_calls = 0
    real_hash = source_freshness._compute_file_sha256

    def counted_hash(path: Path) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return real_hash(path)

    monkeypatch.setattr(source_freshness, "_compute_file_sha256", counted_hash)

    # Warm pass -> hashes once, stable second pass -> cache hit without re-hashing
    assert source_freshness.assess_document_freshness(
        fx["doc"], src_dir=fx["src"], db_path=fx["db_path"], watcher_items={}
    ).fresh is True
    assert source_freshness.assess_document_freshness(
        fx["doc"], src_dir=fx["src"], db_path=fx["db_path"], watcher_items={}
    ).fresh is True
    assert hash_calls == 1

    # Now mutate file right after cache hit inside _get_cached_file_sha256 before admission completes
    real_cached = source_freshness._get_cached_file_sha256

    def mutate_after_cache_hit(path: Path, *args, **kwargs):
        digest = real_cached(path, *args, **kwargs)
        path.write_text("MUTATED_DURING_CACHE_HIT_ADMISSION", encoding="utf-8")
        return digest

    monkeypatch.setattr(source_freshness, "_get_cached_file_sha256", mutate_after_cache_hit)
    res = source_freshness.assess_document_freshness(
        fx["doc"], src_dir=fx["src"], db_path=fx["db_path"], watcher_items={}
    )
    assert res.fresh is False
    assert res.status == "needs_update"
    assert hash_calls == 1


@pytest.mark.parametrize("action", ["delete", "escape_symlink"])
def test_freshness_fails_closed_on_delete_or_symlink_escape_during_check(tmp_path, monkeypatch, action):
    """Deleting or swapping source for an escaping symlink mid-check fails closed with the right status."""
    source_freshness.clear_freshness_cache()
    fx = _indexed_doc_fixture(tmp_path)
    outside = tmp_path / "outside_secret.txt"
    outside.write_text(fx["file"].read_text(encoding="utf-8"), encoding="utf-8")

    real_hash = source_freshness._compute_file_sha256
    triggered = False

    def swap_after_read(path: Path) -> str:
        nonlocal triggered
        digest = real_hash(path)
        if not triggered:
            triggered = True
            path.unlink()
            if action == "escape_symlink":
                path.symlink_to(outside)
        return digest

    monkeypatch.setattr(source_freshness, "_compute_file_sha256", swap_after_read)
    res = source_freshness.assess_document_freshness(
        fx["doc"], src_dir=fx["src"], db_path=fx["db_path"], watcher_items={}
    )
    assert res.fresh is False
    if action == "delete":
        assert res.status == "missing_source"
        assert res.reason_code == "source_missing"
    else:
        assert res.status == "unsafe_source_path"
        assert res.reason_code == "source_identity_mismatch"


def test_freshness_bounded_under_concurrent_writer_and_candidate_filtering_preserved(tmp_path):
    """Concurrent replacement/in-place updates never spin unboundedly or break candidate_doc_ids filtering."""
    source_freshness.clear_freshness_cache()
    src = tmp_path / "src"
    data = tmp_path / "data"
    src.mkdir()
    data.mkdir()
    db_path = data / "knowledge_base.db"

    (src / "a.txt").write_text("ALPHA_STABLE_CONTENT_111", encoding="utf-8")
    b_path = src / "b.txt"
    b_path.write_text("BETA_ORIGINAL_CONTENT_222", encoding="utf-8")
    (src / "c.txt").write_text("GAMMA_EXCLUDED_CONTENT_333", encoding="utf-8")

    indexer = KnowledgeIndexer(db_path=db_path, src_dir=src)
    indexer.index_all(ocr=False, embed_model=None)
    docs_map = indexer.get_indexed_files_map()
    a_id = docs_map["a.txt"]["id"]
    b_id = docs_map["b.txt"]["id"]

    stop_writer = threading.Event()

    def writer_loop() -> None:
        idx = 0
        while not stop_writer.is_set():
            idx += 1
            if idx % 2 == 0:
                b_path.write_text(f"BETA_INPLACE_{idx}", encoding="utf-8")
            else:
                staging = tmp_path / "b_tmp.txt"
                staging.write_text(f"BETA_REPLACED_{idx}", encoding="utf-8")
                os.replace(staging, b_path)
            time.sleep(0.002)

    t = threading.Thread(target=writer_loop, daemon=True)
    t.start()
    try:
        t0 = time.monotonic()
        for _ in range(25):
            res = source_freshness.assess_document_freshness(
                docs_map["b.txt"],
                src_dir=src,
                db_path=db_path,
                watcher_items={},
            )
            assert res.fresh is False
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0
    finally:
        stop_writer.set()
        t.join(timeout=2.0)

    searcher = HybridSearcher(db_path=db_path, src_dir=src, data_dir=data)
    with searcher._connection() as conn:
        filtered = source_freshness.get_fresh_doc_ids(
            conn,
            db_path=db_path,
            src_dir=src,
            data_dir=data,
            candidate_doc_ids=[a_id, b_id],
        )
    assert filtered == [a_id]


def test_native_embedding_removes_subprocess_run_monkeypatch_detection(tmp_path, monkeypatch):
    """Shipped runtime must use one cancellable worker path without _ORIG_SUBPROCESS_RUN test-detection."""
    assert not hasattr(native_embedding, "_ORIG_SUBPROCESS_RUN")

    client = NativeEmbeddingClient(tmp_path)
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(client, "get_identity", lambda: "fixture-basis")
    monkeypatch.setattr(native_embedding.gpu_coordinator, "acquire_for_inference", lambda **kw: nullcontext())

    def legacy_run_forbidden(*args, **kwargs):
        raise AssertionError("NativeEmbeddingClient must not execute obsolete subprocess.run branch")

    monkeypatch.setattr(native_embedding.subprocess, "run", legacy_run_forbidden)

    cancel_ev = threading.Event()
    worker_py = tmp_path / "disposable_worker.py"
    worker_py.write_text(
        "import json, sys, time\n"
        "_ = sys.stdin.read()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(NativeEmbeddingClient, "_worker_command", classmethod(lambda cls: [sys.executable, str(worker_py)]))

    timer = threading.Timer(0.1, cancel_ev.set)
    timer.start()
    try:
        with pytest.raises(IndexingCancelledError):
            client.get_batch_embeddings(["test"], cancel_event=cancel_ev)
    finally:
        timer.cancel()


def test_native_embedding_disposable_subprocess_cancellation_inherits_locks_and_reaps_non_mlx(tmp_path, monkeypatch):
    """Non-MLX disposable subprocess inherits coordinator locks, cancels cleanly, reaps group, and spares unrelated procs."""
    gpu_dir = tmp_path / "runtime"
    gpu_dir.mkdir()
    gpu_lock = GPULockManager(gpu_dir / "gpu.lock", gpu_dir / "gpu_state.json")

    monkeypatch.setattr(native_embedding, "gpu_coordinator", gpu_lock)
    monkeypatch.setattr(NativeEmbeddingClient, "available", classmethod(lambda cls, *a, **kw: True))
    monkeypatch.setattr(NativeEmbeddingClient, "verify_identity", classmethod(lambda cls, *a, **kw: BGE_NATIVE_IDENTITY))

    ready_file = tmp_path / "worker.ready"
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"

    gc_code = (
        "import os, pathlib, time; "
        f"pathlib.Path({json.dumps(str(grandchild_pid_file))}).write_text(str(os.getpid())); "
        "time.sleep(30)"
    )
    fake_worker = tmp_path / "non_mlx_worker.py"
    fake_worker.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        f"pathlib.Path({json.dumps(str(child_pid_file))}).write_text(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, '-c', {json.dumps(gc_code)}])\n"
        f"pathlib.Path({json.dumps(str(ready_file))}).write_text('ready')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(NativeEmbeddingClient, "_worker_command", classmethod(lambda cls: [sys.executable, str(fake_worker)]))

    # Start an unrelated process outside the worker session group to confirm no unrelated kills
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    cancel_ev = threading.Event()
    client = NativeEmbeddingClient(tmp_path)
    errors = []

    def run_worker():
        try:
            client.get_batch_embeddings(["non-mlx lifetime check"], cancel_event=cancel_ev)
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=run_worker, daemon=True)
    try:
        t.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not (
            ready_file.exists() and child_pid_file.exists() and grandchild_pid_file.exists()
        ):
            time.sleep(0.02)
        assert ready_file.exists()
        assert child_pid_file.exists()
        assert grandchild_pid_file.exists()
        child_pid = int(child_pid_file.read_text().strip())
        grandchild_pid = int(grandchild_pid_file.read_text().strip())

        # While alive, coordinator lock FDs inherited by child must prevent exclusive acquisition
        if os.name != "nt":
            for lp in (gpu_lock.lock_path, gpu_lock.native_lock_path):
                fd = os.open(str(lp), os.O_RDWR)
                try:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)

        t_cancel = time.monotonic()
        cancel_ev.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert (time.monotonic() - t_cancel) < 1.5
        assert len(errors) == 1 and isinstance(errors[0], IndexingCancelledError)

        # Child and grandchild must both be terminated and reaped (no zombies or orphans)
        for pid in (child_pid, grandchild_pid):
            alive = True
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.02)
                except ProcessLookupError:
                    alive = False
                    break
            assert alive is False, f"Owned PID {pid} survived cancellation"

        with native_embedding._ACTIVE_WORKERS_LOCK:
            assert len(native_embedding._ACTIVE_WORKERS) == 0

        # Coordinator locks are released immediately after cancellation
        with gpu_lock.acquire_for_inference(timeout=0.5):
            pass

        # Unrelated process must still be alive
        assert unrelated.poll() is None
    finally:
        cancel_ev.set()
        t.join(timeout=2.0)
        unrelated.terminate()
        unrelated.wait(timeout=2.0)


def test_native_embedding_disposable_subprocess_timeout_escalates_sigkill_and_reaps_non_mlx(tmp_path):
    """Non-MLX disposable subprocess ignoring SIGTERM is SIGKILLed on timeout, reaped, and releases inherited locks."""
    gpu_dir = tmp_path / "runtime"
    gpu_dir.mkdir()
    gpu_lock = GPULockManager(gpu_dir / "gpu.lock", gpu_dir / "gpu_state.json")

    pid_file = tmp_path / "stubborn.pid"
    ready_file = tmp_path / "stubborn.ready"
    stubborn = tmp_path / "stubborn_non_mlx_worker.py"
    stubborn.write_text(
        "import os, pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({json.dumps(str(pid_file))}).write_text(str(os.getpid()))\n"
        f"pathlib.Path({json.dumps(str(ready_file))}).write_text('ready')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    t0 = time.monotonic()
    with gpu_lock.acquire_for_inference(timeout=1.0) as lock_fds:
        pass_fds = tuple(int(fd) for fd in lock_fds) if os.name != "nt" else ()
        with pytest.raises(subprocess.TimeoutExpired):
            NativeEmbeddingClient._run_cancellable_popen(
                [sys.executable, str(stubborn)],
                input_text="{}",
                cwd=tmp_path,
                env=dict(os.environ),
                timeout=0.3,
                pass_fds=pass_fds,
            )
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0

    assert pid_file.exists()
    pid = int(pid_file.read_text().strip())
    alive = True
    for _ in range(30):
        try:
            os.kill(pid, 0)
            time.sleep(0.02)
        except ProcessLookupError:
            alive = False
            break
    assert alive is False

    with native_embedding._ACTIVE_WORKERS_LOCK:
        assert len(native_embedding._ACTIVE_WORKERS) == 0

    with gpu_lock.acquire_for_inference(timeout=0.5):
        pass
