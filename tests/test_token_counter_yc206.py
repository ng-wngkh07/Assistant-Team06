"""Tokenizer infrastructure failures must not freeze a request or invent exact budgets."""
import threading
import time
import sys
import shlex
from pathlib import Path
import pytest
from app.token_counter import TokenizerWorkerClient
from app.dialogue import build_context


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX pipe worker; Windows uses conservative budget')
def test_silent_tokenizer_worker_is_bounded_and_reaped(tmp_path):
    executable = tmp_path / 'silent-python'
    executable.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote('import sys,time; sys.stdin.readline(); time.sleep(20)') + '\n')
    executable.chmod(0o755)
    client = TokenizerWorkerClient(tmp_path, executable)
    client.timeout_seconds = 0.15
    outcomes = []
    def run():
        try:
            outcomes.append(client.count([{'role':'user','content':'Câu hỏi'}]))
        except Exception as exc:
            outcomes.append(exc)
    started = time.monotonic()
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=0.6)
    finished_in_time = not worker.is_alive()
    client.close()
    worker.join(timeout=2)
    assert finished_in_time, 'A silent tokenizer worker held the question lane beyond its deadline'
    assert time.monotonic()-started < 1.5
    assert isinstance(outcomes[0], (TimeoutError, RuntimeError))
    assert client._proc is None


def test_counter_failure_uses_honest_conservative_budget():
    def broken_counter(messages):
        raise TimeoutError('tokenizer unavailable')
    messages, info = build_context('Chỉ dùng nguồn.', 'Câu hỏi?', ['<document id="S1">Nguồn rõ ràng.</document>'], None, capacity=4096, output_reserve=640, token_counter=broken_counter)
    assert info['budget_method'] != 'exact_tokenizer'
    assert info['source_ids'] == ['S1']
    assert messages


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX pipe worker; Windows uses conservative budget')
def test_tokenizer_stdin_backpressure_obeys_same_deadline(tmp_path):
    executable = tmp_path / 'nonreader-python'
    executable.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote('import time; time.sleep(20)') + '\n')
    executable.chmod(0o755)
    client = TokenizerWorkerClient(tmp_path, executable)
    client.timeout_seconds = 0.15
    outcomes=[]
    def run():
        try:
            outcomes.append(client.count([{'role':'user','content':'x'*1000000}]))
        except Exception as exc:
            outcomes.append(exc)
    worker=threading.Thread(target=run)
    worker.start();worker.join(timeout=0.6)
    bounded=not worker.is_alive()
    if not bounded and client._proc is not None:
        client._proc.kill()
    worker.join(timeout=2);client.close()
    assert bounded, 'Tokenizer input pipe backpressure blocked the question lane'
    assert isinstance(outcomes[0],TimeoutError)
    assert client._proc is None
