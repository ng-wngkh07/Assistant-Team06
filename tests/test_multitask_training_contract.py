"""Independent artifact acceptance; explicitly select a local dataset to audit."""
import json
import os
from pathlib import Path

import pytest

DOMAINS = {'linear_algebra', 'calculus', 'discrete_math', 'programming', 'physics', 'psychology'}
TASKS = {'grounded_qa', 'insufficient_qa', 'practice_generate',
         'practice_verify_supported', 'practice_verify_invalid', 'session_summary'}


@pytest.fixture
def candidate():
    selected = os.environ.get('DATASET_CONTRACT_DIR')
    if not selected:
        pytest.skip('Explicit local dataset required; production corpus is not a CI fixture.')
    path = Path(selected)
    records = [json.loads(s) for s in (path/'approved_manifest.jsonl').read_text().splitlines() if s.strip()]
    return path, records


@pytest.mark.parametrize('split', ['train', 'valid'])
def test_every_subject_has_every_runtime_text_task(candidate, split):
    _, records = candidate
    rows = [r for r in records if r['split'] == split]
    assert {r['domain'] for r in rows} == DOMAINS
    for domain in DOMAINS:
        assert {r.get('task') for r in rows if r['domain'] == domain} == TASKS, domain


def test_training_uses_multiple_content_sources_per_subject(candidate):
    _, records = candidate
    for domain in DOMAINS:
        rows = [r for r in records if r['split'] == 'train' and r['domain'] == domain]
        groups = {r['source_file_sha256'] for r in rows}
        assert len(groups) >= 2, domain
        assert all(sum(r['source_file_sha256'] == group for r in rows) <= len(rows)/2 for group in groups)


def test_manifest_binds_source_input_and_target_in_actual_order(candidate):
    path, records = candidate
    assert len({r['id'] for r in records}) == len(records)
    for split in ('train', 'valid'):
        manifest = [r for r in records if r['split'] == split]
        examples = [json.loads(s) for s in (path/f'{split}.jsonl').read_text().splitlines() if s.strip()]
        assert len(manifest) == len(examples)
        for row, example in zip(manifest, examples):
            messages = example['messages']
            prompt = '\n'.join(m['content'] for m in messages[:-1])
            if row['task'] == 'session_summary':
                # The source span remains provenance, but runtime summarises only dialog.
                dialog = row.get('dialog')
                assert dialog and len(dialog) == 2, row['id']
                q, a = dialog
                bounded_q = q[:300].replace('\n', ' ').strip()
                bounded_a = a[:400].replace('\n', ' ').strip()
                assert f'Người dùng: {bounded_q}\nTrợ lý: {bounded_a}' in prompt, row['id']
                from app.topic_summarizer import SUMMARIZE_SYSTEM_PROMPT
                assert messages[0]['content'] == SUMMARIZE_SYSTEM_PROMPT, row['id']
                assert '<context>' not in messages[1]['content'], row['id']
            elif row['task'].startswith('practice_verify_'):
                # Runtime carries the source inside JSON, where newlines are escaped.
                review_items = json.loads(messages[1]['content'].split('Dữ liệu cần kiểm định:\n', 1)[1])
                assert len(review_items) == 1, row['id']
                assert review_items[0]['source'] == row['passage'][:1400], row['id']
            else:
                assert row['passage'] and row['passage'] in prompt, row['id']
                if row['task'] in {'grounded_qa', 'insufficient_qa'}:
                    assert row['query'] and row['query'] in prompt, row['id']
            assert messages[-1]['role'] == 'assistant' and messages[-1]['content'] == row['reference'], row['id']


def test_structured_feature_labels_obey_runtime_schema(candidate):
    _, records = candidate
    checked = set()
    for row in records:
        task = row.get('task')
        if task not in TASKS - {'grounded_qa', 'insufficient_qa'}:
            continue
        target = json.loads(row['reference'])
        checked.add(task)
        if task == 'practice_generate':
            assert set(target) == {'questions'} and len(target['questions']) == 1
            q = target['questions'][0]
            assert set(q['choices']) == {'A', 'B', 'C', 'D'}
            assert len(set(q['choices'].values())) == 4
            assert q['answer'] in q['choices'] and q['source_chunk_id'] == 1
            assert len(q['evidence_quote']) >= 20 and q['evidence_quote'] in row['passage']
        elif task.startswith('practice_verify_'):
            assert set(target) == {'checks'} and len(target['checks']) == 1
            c = target['checks'][0]
            assert c['index'] == 1
            for key in ('supported', 'explanation_supported', 'unique_answer', 'qualifiers_preserved'):
                assert isinstance(c[key], bool)
            if task == 'practice_verify_supported':
                assert c['verified_answer'] in ('A', 'B', 'C', 'D')
                assert all(c[k] for k in ('supported', 'explanation_supported', 'unique_answer', 'qualifiers_preserved'))
            else:
                assert c['verified_answer'] in ('A', 'B', 'C', 'D', '', None)
                assert not all(c[k] for k in ('supported', 'explanation_supported', 'unique_answer', 'qualifiers_preserved'))
        elif task == 'session_summary':
            assert set(target) == {'title', 'summary'}
            assert 3 <= len(target['title'].split()) <= 7
            assert 0 < len(target['summary']) < 400
    assert checked == TASKS - {'grounded_qa', 'insufficient_qa'}


def test_literal_passage_offsets_bind_to_canonical_chunk(candidate):
    import hashlib
    import sqlite3
    selected_db = os.environ.get('DATASET_SOURCE_DB')
    if not selected_db:
        pytest.skip('Canonical readback requires explicitly selected source database.')
    _, records = candidate
    with sqlite3.connect(Path(selected_db).resolve().as_uri() + '?mode=ro', uri=True) as db:
        defects = []
        for row in records:
            chunk = db.execute('SELECT filename,page_num,text FROM chunks WHERE id=?', (row['chunk_id'],)).fetchone()
            start, end = row['span_start'], row['span_end']
            if (not chunk or chunk[:2] != (row['source_file'], row['page'])
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(chunk[2])
                    or chunk[2][start:end] != row['passage']
                    or hashlib.sha256(row['passage'].encode()).hexdigest() != row['passage_sha256']):
                defects.append(row['id'])
        assert not defects, f'{len(defects)} broken literal source spans: {defects[:8]}'


def test_generation_does_not_teach_single_letter_position_bias(candidate):
    from collections import Counter
    _, records = candidate
    for split in ('train', 'valid'):
        answers = [q['answer'] for row in records if row.get('task') == 'practice_generate' and row['split'] == split
                   for q in json.loads(row['reference'])['questions']]
        assert len(answers) >= 4
        counts = Counter(answers)
        assert set(counts) == {'A', 'B', 'C', 'D'}, (split, counts)
        assert max(counts.values()) <= len(answers)/2, (split, counts)


def test_mcq_distractors_are_not_one_cross_subject_metatext_template(candidate):
    _, records = candidate
    seen = {}
    for row in records:
        if row.get('task') != 'practice_generate':
            continue
        for question in json.loads(row['reference'])['questions']:
            distractors = tuple(sorted(v for key, v in question['choices'].items() if key != question['answer']))
            seen.setdefault(distractors, set()).add(row['domain'])
    shared = [domains for domains in seen.values() if len(domains) >= 3]
    assert not shared, f'Identical distractor template spans unrelated subjects: {shared}'
