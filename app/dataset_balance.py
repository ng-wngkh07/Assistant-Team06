"""Compute actual sample/source/token exposure before a corpus-complete trial."""
import argparse
import hashlib
import html
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from app.text_cleaner import sanitize_for_prompt_context

SUPPORTED_RUNTIME_TASKS = {
    'grounded_qa',
    'insufficient_qa',
    'practice_generate',
    'practice_verify_supported',
    'practice_verify_invalid',
    'exercise_solve',
    'exercise_verify_supported',
    'exercise_verify_invalid',
    'session_summary',
    'auditor_supported',
    'auditor_invalid',
}


def _get_field(row, field, default=None):
    if field in row and row[field] is not None:
        return row[field]
    if 'source_provenance' in row and isinstance(row['source_provenance'], dict):
        if field in row['source_provenance'] and row['source_provenance'][field] is not None:
            return row['source_provenance'][field]
    return default


def _normalize_choices(ch):
    if isinstance(ch, dict):
        return {str(k).strip(): str(v).strip() for k, v in ch.items()}
    if isinstance(ch, list):
        res = {}
        for idx, entry in enumerate(ch):
            if isinstance(entry, dict):
                label = entry.get('label') or chr(65 + idx)
                text = entry.get('text') or entry.get('value', '')
                res[str(label).strip()] = str(text).strip()
            elif isinstance(entry, (str, int, float)):
                res[chr(65 + idx)] = str(entry).strip()
        return res
    return ch


def validate_runtime_input_binding(row: dict, messages: list) -> None:
    version = row.get('runtime_binding_version', 'yc196-v1')
    if version != 'yc196-v1':
        raise ValueError(f"Unsupported runtime_binding_version '{version}' at {row.get('id')}")

    if not messages or not isinstance(messages, list):
        raise ValueError(f"Empty or invalid messages at {row.get('id')}")

    # Final assistant reference check
    if messages[-1].get('role') != 'assistant':
        raise ValueError(f"Last message not assistant at {row.get('id')}")
    if 'reference' in row and row['reference'] is not None:
        if messages[-1].get('content', '').strip() != row['reference'].strip():
            raise ValueError(f"Manifest/reference mismatch at {row.get('id')}")
    elif 'reference_sha256' in row and row['reference_sha256'] is not None:
        ref_hash = hashlib.sha256(messages[-1].get('content', '').encode('utf-8')).hexdigest()
        if ref_hash != row['reference_sha256']:
            raise ValueError(f"Manifest/reference_sha256 mismatch at {row.get('id')}")

    # Canonical sha256 check
    expected_hash = hashlib.sha256(
        json.dumps(messages[:-1], ensure_ascii=False, sort_keys=True).encode('utf-8')
    ).hexdigest()
    if row.get('runtime_input_sha256') != expected_hash:
        raise ValueError(f"Runtime input sha256 mismatch at {row.get('id')}")

    task = row.get('task', '')
    if task not in SUPPORTED_RUNTIME_TASKS:
        raise ValueError(f"Unsupported runtime task '{task}' at {row.get('id')}")

    user_msgs = [m.get('content', '') for m in messages[:-1] if m.get('role') == 'user']
    if len(user_msgs) != 1:
        raise ValueError(f"Expected exactly 1 user message, got {len(user_msgs)} at {row.get('id')}")
    user_msg = user_msgs[0]

    raw_passage = _get_field(row, 'passage')
    if task != 'session_summary' and not raw_passage:
        raise ValueError(f"Missing passage for task '{task}' at {row.get('id')}")

    if task in ('grounded_qa', 'insufficient_qa'):
        if len(re.findall(r'<document\b', user_msg)) != 1:
            raise ValueError(f"Expected exactly 1 <document> tag in {task} at {row.get('id')}")
        m_doc = re.search(r'<document\s+([^>]+)>(.*?)</document>', user_msg, re.DOTALL)
        if not m_doc:
            raise ValueError(f"Invalid <document> structure in {task} at {row.get('id')}")
        attrs = m_doc.group(1)
        id_m = re.search(r'\bid=([\'"])(.*?)\1', attrs)
        if not id_m or id_m.group(2) != 'S1':
            raise ValueError(f"Expected document id='S1' at {row.get('id')}")

        filename = _get_field(row, 'filename')
        if filename:
            book_m = re.search(r'\bbook=([\'"])(.*?)\1', attrs)
            if not book_m or book_m.group(2) not in (
                filename,
                html.escape(filename, quote=True),
                html.escape(filename, quote=False),
                filename.replace('"', '&quot;'),
            ):
                raise ValueError(f"Mismatched book in document header at {row.get('id')}")
        page_num = _get_field(row, 'page_num')
        if page_num is not None:
            page_m = re.search(r'\bpage=([\'"])(.*?)\1', attrs)
            if not page_m or page_m.group(2) != str(page_num):
                raise ValueError(f"Mismatched page in document header at {row.get('id')}")

        body = m_doc.group(2).strip()
        safe_p = sanitize_for_prompt_context(raw_passage)
        expected_body = html.escape(safe_p, quote=False).strip()
        if body != expected_body:
            raise ValueError(f"Document body mismatch at {row.get('id')}")

        query = _get_field(row, 'query')
        if query:
            m_uq = re.search(r'<user_query>\s*(.*?)\s*</user_query>', user_msg, re.DOTALL)
            if not m_uq:
                raise ValueError(f"Missing <user_query> at {row.get('id')}")
            q_body = m_uq.group(1).strip()
            valid_q = {
                query,
                html.escape(query, quote=False),
                query.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if q_body not in valid_q:
                raise ValueError(f"Query mismatch in <user_query> at {row.get('id')}")

    elif task == 'practice_generate':
        if len(re.findall(r'<passage\b', user_msg)) != 1:
            raise ValueError(f"Expected exactly 1 <passage> tag at {row.get('id')}")
        m_p = re.search(r'<passage\s+([^>]+)>(.*?)</passage>', user_msg, re.DOTALL)
        if not m_p:
            raise ValueError(f"Invalid <passage> structure at {row.get('id')}")
        attrs = m_p.group(1)
        chunk_id = _get_field(row, 'chunk_id')
        if chunk_id is not None:
            id_m = re.search(r'\bid=([\'"])(.*?)\1', attrs)
            if not id_m or id_m.group(2) != str(chunk_id):
                raise ValueError(f"Mismatched passage id at {row.get('id')}")
        page_num = _get_field(row, 'page_num')
        if page_num is not None:
            page_m = re.search(r'\bpage=([\'"])(.*?)\1', attrs)
            if not page_m or page_m.group(2) != str(page_num):
                raise ValueError(f"Mismatched passage page at {row.get('id')}")
        body = m_p.group(2).strip()
        expected_body = sanitize_for_prompt_context(raw_passage)[:1400].strip()
        if body != expected_body:
            raise ValueError(f"Passage body mismatch at {row.get('id')}")

    elif task in ('practice_verify_supported', 'practice_verify_invalid'):
        h = 'dữ liệu cần kiểm định'
        if h not in user_msg.lower():
            raise ValueError(f"Missing verifier header at {row.get('id')}")
        idx = user_msg.lower().index(h)
        after = user_msg[idx + len(h):].strip()
        if after.startswith(':'):
            after = after[1:].strip()
        try:
            data = json.loads(after)
        except Exception as e:
            raise ValueError(f"Invalid verifier JSON at {row.get('id')}: {e}")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ValueError(f"Expected list with 1 dict in verifier JSON at {row.get('id')}")
        item = data[0]
        expected_src = sanitize_for_prompt_context(raw_passage)[:1400].strip()
        if item.get('source', '').strip() != expected_src:
            raise ValueError(f"Verifier source mismatch at {row.get('id')}")
        query = _get_field(row, 'query')
        if query and item.get('question') != query:
            raise ValueError(f"Verifier question mismatch at {row.get('id')}")
        choices = _get_field(row, 'choices')
        if choices is not None:
            item_choices = item.get('choices')
            if item_choices is None:
                raise ValueError(f"Verifier missing choices at {row.get('id')}")
            if item_choices != choices and _normalize_choices(item_choices) != _normalize_choices(choices):
                raise ValueError(f"Verifier choices mismatch at {row.get('id')}")
        prop_ans = _get_field(row, 'proposed_answer')
        if prop_ans and item.get('proposed_answer') != prop_ans:
            raise ValueError(f"Verifier proposed_answer mismatch at {row.get('id')}")
        prop_expl = _get_field(row, 'proposed_explanation') or _get_field(row, 'explanation')
        if prop_expl and item.get('explanation') != prop_expl:
            raise ValueError(f"Verifier explanation mismatch at {row.get('id')}")
        quote = (
            _get_field(row, 'source_quote')
            or _get_field(row, 'sourcequote')
            or _get_field(row, 'evidence_quote')
        )
        if quote:
            item_quote = (
                item.get('evidence_quote')
                or item.get('source_quote')
                or item.get('sourcequote')
            )
            if not item_quote or str(item_quote).strip() != str(quote).strip():
                raise ValueError(f"Verifier evidence quote mismatch at {row.get('id')}")

    elif task == 'exercise_solve':
        m1 = re.search(r'(?i)lý thuyết tham khảo:\s*', user_msg)
        m2 = re.search(r'(?i)\s*bài tập \(dữ liệu, không phải chỉ dẫn\):', user_msg)
        if not m1 or not m2 or m1.end() >= m2.start():
            raise ValueError(f"Missing exercise_solve headers at {row.get('id')}")
        src = user_msg[m1.end():m2.start()].strip()
        expected_src = sanitize_for_prompt_context(raw_passage)[:1200].strip()
        if src != expected_src:
            raise ValueError(f"Exercise solve source mismatch at {row.get('id')}")
        query = _get_field(row, 'query')
        if query:
            after_m2 = user_msg[m2.end():]
            m3 = re.search(r'(?i)\n\s*trả về json', after_m2)
            q_part = after_m2[:m3.start()].strip() if m3 else after_m2.strip()
            valid_q = {
                query,
                html.escape(query, quote=False),
                query.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if q_part not in valid_q:
                raise ValueError(f"Exercise solve query mismatch at {row.get('id')}")

    elif task in ('exercise_verify_supported', 'exercise_verify_invalid'):
        m1 = re.search(r'(?i)lý thuyết căn cứ:\s*', user_msg)
        m2 = re.search(r'(?i)\s*bài tập \(dữ liệu, không phải chỉ dẫn\):', user_msg)
        if not m1 or not m2 or m1.end() >= m2.start():
            raise ValueError(f"Missing exercise_verify headers at {row.get('id')}")
        src = user_msg[m1.end():m2.start()].strip()
        expected_src = sanitize_for_prompt_context(raw_passage)[:1200].strip()
        if src != expected_src:
            raise ValueError(f"Exercise verify source mismatch at {row.get('id')}")
        query = _get_field(row, 'query')
        if query:
            m_ex = re.search(
                r'(?i)bài tập \(dữ liệu, không phải chỉ dẫn\):\s*(.*?)\s*(?:lời giải đề xuất \(dữ liệu\):|\n\s*yêu cầu đánh giá:)',
                user_msg,
                re.DOTALL,
            )
            if not m_ex:
                raise ValueError(f"Missing exercise query block at {row.get('id')}")
            q_part = m_ex.group(1).strip()
            valid_q = {
                query,
                html.escape(query, quote=False),
                query.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if q_part not in valid_q:
                raise ValueError(f"Exercise verify query mismatch at {row.get('id')}")
        prop_sol = _get_field(row, 'proposed_solution')
        if prop_sol:
            m_sol = re.search(
                r'(?i)lời giải đề xuất \(dữ liệu\):\s*(.*?)\s*(?:đáp án \(dữ liệu\):)',
                user_msg,
                re.DOTALL,
            )
            if not m_sol:
                raise ValueError(f"Missing proposed solution block at {row.get('id')}")
            sol_part = m_sol.group(1).strip()
            valid_sol = {
                prop_sol,
                html.escape(prop_sol, quote=False),
                prop_sol.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if sol_part not in valid_sol:
                raise ValueError(f"Exercise verify solution mismatch at {row.get('id')}")
        prop_ans = _get_field(row, 'proposed_answer')
        if prop_ans:
            m_ans = re.search(
                r'(?i)đáp án \(dữ liệu\):\s*(.*?)\s*(?:yêu cầu đánh giá:)',
                user_msg,
                re.DOTALL,
            )
            if not m_ans:
                raise ValueError(f"Missing proposed answer block at {row.get('id')}")
            ans_part = m_ans.group(1).strip()
            valid_ans = {
                prop_ans,
                html.escape(prop_ans, quote=False),
                prop_ans.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if ans_part not in valid_ans:
                raise ValueError(f"Exercise verify answer mismatch at {row.get('id')}")

    elif task == 'session_summary':
        dialog = _get_field(row, 'dialog')
        if len(re.findall(r'<transcript\b', user_msg)) != 1 or len(re.findall(r'</transcript>', user_msg)) != 1:
            raise ValueError(f"Summary input missing or duplicate <transcript> tags at {row.get('id')}")
        t_start = user_msg.index('<transcript>') + len('<transcript>')
        t_end = user_msg.index('</transcript>')
        transcript_inner = user_msg[t_start:t_end].strip()
        if dialog is not None:
            if not isinstance(dialog, (list, tuple)) or len(dialog) != 2:
                raise ValueError(f"Summary manifest row missing 2-item dialog at {row.get('id')}")
            q_val = html.escape(str(dialog[0])[:300].replace('\n', ' ').strip(), quote=False)
            a_val = html.escape(str(dialog[1])[:400].replace('\n', ' ').strip(), quote=False)
            if not q_val or not a_val:
                raise ValueError(f"Summary dialog contains empty turn at {row.get('id')}")
            expected_transcript = f"Người dùng: {q_val}\nTrợ lý: {a_val}"
            if transcript_inner != expected_transcript:
                raise ValueError(f"Summary input transcript does not match reviewed dialog at {row.get('id')}")
        else:
            if not (transcript_inner.startswith("Người dùng:") and "Trợ lý:" in transcript_inner):
                raise ValueError(f"Summary input missing dialog speakers at {row.get('id')}")

    elif task in ('auditor_supported', 'auditor_invalid'):
        ctx_blocks = list(re.finditer(r'<context>\s*(.*?)\s*</context>', user_msg, re.DOTALL))
        if len(ctx_blocks) != 1:
            raise ValueError(f"Expected exactly 1 <context> block at {row.get('id')}")
        ctx_content = ctx_blocks[0].group(1).strip()
        if '<document' in ctx_content:
            raise ValueError(f"Unescaped document tag inside context at {row.get('id')}")
        unescaped_ctx = html.unescape(ctx_content)
        if len(re.findall(r'<document\b', unescaped_ctx)) != 1:
            raise ValueError(f"Expected exactly 1 unescaped <document> tag at {row.get('id')}")
        m_doc = re.search(r'<document\s+([^>]+)>(.*?)</document>', unescaped_ctx, re.DOTALL)
        if not m_doc:
            raise ValueError(f"Invalid unescaped <document> structure at {row.get('id')}")
        attrs = m_doc.group(1)
        id_m = re.search(r'\bid=([\'"])(.*?)\1', attrs)
        if not id_m or id_m.group(2) != 'S1':
            raise ValueError(f"Expected auditor doc id='S1' at {row.get('id')}")
        filename = _get_field(row, 'filename')
        if filename:
            book_m = re.search(r'\bbook=([\'"])(.*?)\1', attrs)
            if not book_m or book_m.group(2) not in (
                filename,
                html.escape(filename, quote=True),
                html.escape(filename, quote=False),
                filename.replace('"', '&quot;'),
            ):
                raise ValueError(f"Mismatched auditor book attribute at {row.get('id')}")
        page_num = _get_field(row, 'page_num')
        if page_num is not None:
            page_m = re.search(r'\bpage=([\'"])(.*?)\1', attrs)
            if not page_m or page_m.group(2) != str(page_num):
                raise ValueError(f"Mismatched auditor page attribute at {row.get('id')}")
        body = m_doc.group(2).strip()
        expected_body = sanitize_for_prompt_context(raw_passage)[:900].strip()
        if body != expected_body:
            raise ValueError(f"Auditor document body mismatch at {row.get('id')}")
        query = _get_field(row, 'query')
        if query:
            m_uq = re.search(r'<user_query>\s*(.*?)\s*</user_query>', user_msg, re.DOTALL)
            if not m_uq:
                raise ValueError(f"Missing auditor <user_query> at {row.get('id')}")
            q_body = m_uq.group(1).strip()
            valid_q = {
                query,
                html.escape(query, quote=False),
                query.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if q_body not in valid_q:
                raise ValueError(f"Auditor query mismatch at {row.get('id')}")
        draft = _get_field(row, 'draft')
        if draft:
            m_d = re.search(r'<draft_answer>\s*(.*?)\s*</draft_answer>', user_msg, re.DOTALL)
            if not m_d:
                raise ValueError(f"Missing auditor <draft_answer> at {row.get('id')}")
            d_body = m_d.group(1).strip()
            valid_d = {
                draft,
                html.escape(draft, quote=False),
                draft.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'),
            }
            if d_body not in valid_d:
                raise ValueError(f"Auditor draft mismatch at {row.get('id')}")


DOMAINS = ('linear_algebra', 'calculus', 'discrete_math', 'programming', 'physics', 'psychology')
ALIASES = {'algebra': 'linear_algebra', 'computer_science': 'programming', 'cpp': 'programming',
           'data_structures': 'programming', 'oop': 'programming', 'algorithms': 'programming',
           'study_habits': 'psychology'}


def assess_exposure(records, token_lengths=None):
    failures = []
    counts = {split: Counter() for split in ('train', 'valid')}
    sources = defaultdict(Counter)
    exposure = Counter()
    for row in records:
        domain = ALIASES.get(row['domain'], row['domain'])
        split = row['split']
        if domain not in DOMAINS or split not in counts:
            failures.append('unknown_domain_or_split'); continue
        counts[split][domain] += 1
        if not row.get('source_file_sha256'):
            failures.append('missing_content_source_group'); continue
        if split=='train':
            sources[domain][row['source_file_sha256']] += 1
            if token_lengths is not None:
                exposure[domain] += token_lengths[row['id']]
    for split, dist in counts.items():
        total = sum(dist.values())
        for domain in DOMAINS:
            fraction = dist[domain]/total if total else 0
            # Six equally weighted subjects, with a quarter tolerance for counts.
            if not (0.75/6 <= fraction <= 1.25/6):
                failures.append(f'{split}_unbalanced:{domain}')
    for domain in DOMAINS:
        dist = sources[domain]
        if len(dist)<2 or (dist and max(dist.values())/sum(dist.values()) > 0.5):
            failures.append('source_concentration:'+domain)
    if token_lengths is not None:
        total = sum(exposure.values())
        for domain in DOMAINS:
            fraction = exposure[domain]/total if total else 0
            if not (0.5/6 <= fraction <= 1.5/6):
                failures.append('target_token_exposure:'+domain)
    return {'counts': {k:dict(v) for k,v in counts.items()},
            'distinct_train_source_groups': {k:len(sources[k]) for k in DOMAINS},
            'target_tokens': dict(exposure), 'failures': failures, 'passed': not failures,
            'exact_token_exposure_measured': token_lengths is not None}


def exact_balance(data_dir, model_dir):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
    records = [json.loads(s) for s in (data_dir/'approved_manifest.jsonl').read_text().splitlines()]
    lengths = {}
    for split in ('train','valid'):
        manifest = [r for r in records if r['split']==split]
        examples = [json.loads(s) for s in (data_dir/f'{split}.jsonl').read_text().splitlines()]
        if len(manifest)!=len(examples): raise ValueError('Manifest/example counts differ')
        for row, example in zip(manifest, examples):
            messages = example['messages']
            prompt = '\n'.join(m['content'] for m in messages[:-1] if m['role'] in ('system', 'user'))
            user_msg = '\n'.join(m['content'] for m in messages[:-1] if m['role'] == 'user')
            task = row.get('task', '')

            if 'runtime_binding_version' in row:
                validate_runtime_input_binding(row, messages)
            elif task == 'session_summary':
                dialog = row.get('dialog')
                if not dialog or not isinstance(dialog, (list, tuple)) or len(dialog) != 2:
                    raise ValueError('Summary manifest row missing 2-item dialog at ' + row['id'])
                q_val = str(dialog[0])[:300].replace('\n', ' ').strip()
                a_val = str(dialog[1])[:400].replace('\n', ' ').strip()
                if not q_val or not a_val:
                    raise ValueError('Summary dialog contains empty turn at ' + row['id'])
                if '<transcript>' not in user_msg or '</transcript>' not in user_msg:
                    raise ValueError('Summary input missing <transcript> tags at ' + row['id'])
                t_start = user_msg.index('<transcript>') + len('<transcript>')
                t_end = user_msg.index('</transcript>')
                transcript_inner = user_msg[t_start:t_end].strip()
                expected_transcript = f'Người dùng: {q_val}\nTrợ lý: {a_val}'
                if transcript_inner != expected_transcript:
                    raise ValueError('Summary input transcript does not match reviewed dialog at ' + row['id'])

            elif task.startswith('practice_verify_'):
                lower_user = user_msg.lower()
                header = 'dữ liệu cần kiểm định'
                if header not in lower_user:
                    raise ValueError('Verifier input missing "Dữ liệu cần kiểm định" at ' + row['id'])
                header_idx = lower_user.index(header)
                after_header = user_msg[header_idx + len(header):]
                start_bracket = -1
                for i, ch in enumerate(after_header):
                    if ch in ('[', '{'):
                        start_bracket = i
                        break
                if start_bracket == -1:
                    raise ValueError('Verifier input missing JSON after header at ' + row['id'])
                try:
                    parsed_data = json.loads(after_header[start_bracket:].strip())
                except Exception as e:
                    raise ValueError(f'Verifier input contains invalid JSON at {row["id"]}: {e}')

                if isinstance(parsed_data, list):
                    sources = [item.get('source') for item in parsed_data if isinstance(item, dict) and 'source' in item]
                elif isinstance(parsed_data, dict):
                    if 'source' in parsed_data:
                        sources = [parsed_data['source']]
                    elif 'questions' in parsed_data:
                        sources = [q.get('source') for q in parsed_data['questions'] if isinstance(q, dict) and 'source' in q]
                    elif 'checks' in parsed_data:
                        sources = [c.get('source') for c in parsed_data['checks'] if isinstance(c, dict) and 'source' in c]
                    else:
                        sources = []
                else:
                    sources = []

                expected_source = row.get('passage', '')[:1400]
                if not expected_source or len(sources) != 1 or sources[0] != expected_source:
                    raise ValueError('Verifier input missing exact single decoded source passage at ' + row['id'])

            else:
                if not row.get('passage') or row['passage'] not in prompt:
                    raise ValueError('Training input missing exact source passage at ' + row['id'])

            # Existing source/target gates still apply; bind exposure to the actual answers.
            if messages[-1]['role'] != 'assistant' or messages[-1]['content'].strip() != row['reference'].strip():
                raise ValueError('Manifest/answer order differs at ' + row['id'])
            lengths[row['id']] = len(tokenizer.encode(messages[-1]['content'], add_special_tokens=False))
    report = assess_exposure(records, lengths)
    report['source_context_bound'] = True
    report['manifest_sha256'] = hashlib.sha256((data_dir/'approved_manifest.jsonl').read_bytes()).hexdigest()
    report['dataset_sha256'] = hashlib.sha256((data_dir/'train.jsonl').read_bytes()+(data_dir/'valid.jsonl').read_bytes()).hexdigest()
    return report


def require_balanced_dataset(root, data_dir, model_dir):
    if not (root/'data/runtime/corpus_completion_policy.json').exists(): return None
    proc = subprocess.run([str(root/'.train-venv/bin/python'), '-m', 'app.dataset_balance',
        '--data',str(data_dir),'--model',str(model_dir)], cwd=root, capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError('DATASET_UNBALANCED: không xác minh được tỷ lệ nguồn/token: '+proc.stderr[-400:])
    report = json.loads(proc.stdout)
    if not report['passed'] or not report['exact_token_exposure_measured']:
        raise RuntimeError('DATASET_UNBALANCED: '+json.dumps(report['failures'],ensure_ascii=False))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);parser.add_argument('--model',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(exact_balance(args.data,args.model),ensure_ascii=False))
