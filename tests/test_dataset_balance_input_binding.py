"""Production preflight must bind each task's actual input representation."""
import json
import sys
import types

import pytest

from app.dataset_balance import exact_balance


@pytest.fixture
def tokenizer_boundary(monkeypatch):
    tokenizer = types.SimpleNamespace(from_pretrained=
        lambda *a, **kw: types.SimpleNamespace(encode=lambda text, **kw: list(text)))
    monkeypatch.setitem(sys.modules, 'transformers', types.SimpleNamespace(AutoTokenizer=tokenizer))


def write_candidate(tmp_path, task, user, dialog=None):
    rows = []
    for split in ('train', 'valid'):
        row = {'id': split+'-bound-one', 'split': split, 'domain': 'programming',
               'task': task, 'source_file_sha256': 'a'*64, 'passage': 'SOURCE FIRST\nSOURCE SECOND',
               'reference': 'bound target'}
        if dialog is not None:
            row['dialog'] = dialog
        rows.append(row)
        (tmp_path/f'{split}.jsonl').write_text(json.dumps({'messages': [
            {'role': 'system', 'content': 'frozen system'},
            {'role': 'user', 'content': user},
            {'role': 'assistant', 'content': row['reference']}]} )+'\n')
    (tmp_path/'approved_manifest.jsonl').write_text('\n'.join(json.dumps(row) for row in rows)+'\n')


def test_verifier_source_is_bound_after_json_decoding(tmp_path, tokenizer_boundary):
    user = 'Kiểm định. Dữ liệu cần kiểm định:\n'+json.dumps([
        {'index': 1, 'source': 'SOURCE FIRST\nSOURCE SECOND', 'question': 'a question'}])
    write_candidate(tmp_path, 'practice_verify_invalid', user)
    # Count/source concentration checks remain separate; this check only tests input binding.
    assert exact_balance(tmp_path, tmp_path/'synthetic-model')['source_context_bound'] is True


def test_summary_binds_bounded_dialog_without_inserting_pdf(tmp_path, tokenizer_boundary):
    dialog = ['Q'*299+'\n'+'HIDDEN QUESTION', 'A'*399+'\n'+'HIDDEN ANSWER']
    q, a = (value[:limit].replace('\n', ' ').strip() for value, limit in zip(dialog, (300, 400)))
    user = f'<transcript>\nNgười dùng: {q}\nTrợ lý: {a}\n</transcript>'
    write_candidate(tmp_path, 'session_summary', user, dialog)
    assert exact_balance(tmp_path, tmp_path/'synthetic-model')['source_context_bound'] is True


def test_summary_dialog_may_legitimately_quote_the_source(tmp_path, tokenizer_boundary):
    dialog = ['Nhắc lại câu nguồn.', 'SOURCE FIRST\nSOURCE SECOND']
    user = '<transcript>\nNgười dùng: Nhắc lại câu nguồn.\nTrợ lý: SOURCE FIRST SOURCE SECOND\n</transcript>'
    write_candidate(tmp_path, 'session_summary', user, dialog)
    assert exact_balance(tmp_path, tmp_path/'synthetic-model')['source_context_bound'] is True


def test_summary_rejects_extra_unreviewed_transcript_turn(tmp_path, tokenizer_boundary):
    user = ('<transcript>\nNgười dùng: reviewed question\nTrợ lý: reviewed answer\n'
            'Người dùng: UNREVIEWED EXTRA TURN\n</transcript>')
    write_candidate(tmp_path, 'session_summary', user, ['reviewed question', 'reviewed answer'])
    with pytest.raises(ValueError):
        exact_balance(tmp_path, tmp_path/'synthetic-model')


def test_verifier_cannot_hide_a_second_changed_source(tmp_path, tokenizer_boundary):
    user = 'Dữ liệu cần kiểm định:\n'+json.dumps([
        {'index': 1, 'source': 'SOURCE FIRST\nSOURCE SECOND'},
        {'index': 2, 'source': 'UNREVIEWED OTHER SOURCE'}])
    write_candidate(tmp_path, 'practice_verify_invalid', user)
    with pytest.raises(ValueError):
        exact_balance(tmp_path, tmp_path/'synthetic-model')


@pytest.mark.parametrize('task', ['practice_verify_invalid', 'session_summary'])
def test_mismatched_task_input_is_rejected(tmp_path, tokenizer_boundary, task):
    user = ('Kiểm định. Dữ liệu cần kiểm định:\n'+json.dumps([{'index': 1, 'source': 'CHANGED SOURCE'}])
            if task.startswith('practice_verify_') else '<transcript>CHANGED DIALOG</transcript>')
    write_candidate(tmp_path, task, user, ['reviewed question', 'reviewed answer'])
    with pytest.raises(ValueError):
        exact_balance(tmp_path, tmp_path/'synthetic-model')
