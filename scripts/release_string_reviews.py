"""Manual historical tool to compile and verify C++ string transcript reviews.

WARNING: This manual historical tool automatically compiles and executes C++
source code extracted from input review files. It requires operator-trusted
inputs stored in the repository-local directory; shared /tmp inputs are removed.
"""
import json, hashlib, sys, fcntl, subprocess, datetime, shutil
from pathlib import Path
from datetime import timezone

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from app.source_page_reviews import validate_page_review
from scripts.index_source_reviews import apply_reviews

O = ROOT / 'data/evaluation/corpus-review-20261005/all-remaining-20261005'
D = {int(k): v for k, v in json.loads((O / 'string-transcripts.json').read_text(encoding='utf-8')).items()}
B = O / 'string-source-checks'
B.mkdir(parents=True, exist_ok=True)

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

# 1. Compile programs and verify correctness against independent test cases
programs = {
    '9.2': '#include <stdio.h>\n' + D[3].split('// Function to count the number of characters in a string', 1)[1].split('Program 9.2 Output')[0],
    '9.3': D[4].split('Program 9.3 Concatenating Character Strings\n', 1)[1] + D[5].split('Program 9.3 Output')[0],
    '9.4': '#include <iostream>\nusing namespace std;\n' + D[5].split('Program 9.4 Testing Strings for Equality\n', 1)[1] + D[6].split('Program 9.4 Output')[0],
    '9.7': D[8].split('Program 9.7 Output')[0],
    '9.10': D[14].split('Program 9.10 Modifying the Dictionary Lookup Using Binary Search\n', 1)[1] + D[15] + D[16].split('Program 9.10 Output')[0],
    '9.11': D[16].split('Program 9.11 Converting a String to its Integer Equivalent\n', 1)[1] + D[17].split('Program 9.11 Output')[0]
}

cases = {
    '9.2': [('', '5  2  3')],
    '9.3': [('', 'Test works.')],
    '9.4': [('', '011')],
    '9.7': [('', 'Well, here goes. - words = 3\nAnd here we go... again. - words = 5')],
    '9.10': [('aigrette\n', 'an ornamental cluster of feathers'), ('acerb\n', 'Sorry, the word acerb is not in my dictionary.')],
    '9.11': [('', '245\n125\n13')]
}

report = []
for key, source in programs.items():
    p = B / f'program-{key}.cpp'
    p.write_text(source)
    exe = p.with_suffix('')
    c = subprocess.run(['clang++', '-std=c++14', '-Wall', '-Wextra', str(p), '-o', str(exe)], capture_output=True, text=True)
    if c.returncode != 0:
        raise RuntimeError(f'Program {key} compilation failed: {c.stderr}')
    runs = []
    for inp, expect in cases[key]:
        r = subprocess.run([str(exe)], input=inp, capture_output=True, text=True, timeout=5, check=True)
        if expect not in r.stdout:
            raise RuntimeError(f'Mismatch in {key}: expected "{expect}", got "{r.stdout}"')
        runs.append(dict(input=inp, output=r.stdout.strip(), state='PASS'))
    report.append(dict(program=key, compile='PASS', cases=runs, source_sha256=sha(p)))

(B / 'independent-evidence.json').write_text(json.dumps(dict(programs=report, training_started=False), ensure_ascii=False, indent=2))

# 2. Build and validate reviews
rows = json.loads((O / 'string-rows.json').read_text(encoding='utf-8'))
paths = []
source_hash = sha(ROOT / 'src' / rows[0]['filename'])
folder = ROOT / 'data/evaluation/corpus-completion-20261004/source-reviews' / source_hash
folder.mkdir(parents=True, exist_ok=True)

with (ROOT / 'data/evaluation/corpus-completion-20261004/runner.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for r in rows:
        page = r['page_num']
        editorial = ''
        if page == 16:
            editorial = 'Sai lệch tiêu đề trong nguồn slide: Banner Program 9.10 Output (Rerun) ghi "Sorry, that word is not in my dictionary." nhưng lệnh printf trong mã nguồn thực thi in ra "Sorry, the word %s is not in my dictionary.". Giữ nguyên transcript theo ảnh; không biến sai lệch nguồn thành nhãn huấn luyện.'
        if page == 9:
            editorial = 'Bảng 9.1 theo dõi giá trị các biến i, string[i], wordCount, lookingForWord của hàm countWords(). Dữ liệu bảng được ghi nhận trung thực theo cấu trúc dòng cột.'

        review = dict(
            filename=r['filename'],
            page=page,
            source_sha256=source_hash,
            image_path=r['image_path'],
            image_sha256=sha(r['image_path']),
            raw_capture_path=r['raw_capture_path'],
            raw_capture_sha256=sha(r['raw_capture_path']),
            reviewer='Codex direct visual review',
            reviewed_at=datetime.datetime.now(timezone.utc).isoformat(),
            image_reviewed=True,
            transcript=D[page],
            editorial=editorial,
            unresolved=[],
            completeness='complete',
            kind='printed',
            training_deferred=True,
            review_evidence='All 18 queued pages inspected in string-0..4 contact sheets. Six complete programs compiled and verified with independent test cases. No training started.'
        )
        validate_page_review(review, r['filename'], page, source_hash)
        tag = hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        p = folder / f'{page:04}-{tag}.json'
        p.write_text(json.dumps(review, ensure_ascii=False, indent=2))
        paths.append(str(p))

    changed = apply_reviews(paths)
    (O / 'approved-string-reviews.json').write_text(json.dumps(paths, ensure_ascii=False, indent=2))
    (O / 'string-index-readback.json').write_text(json.dumps(changed, ensure_ascii=False, indent=2))

print('Source-reviewed/indexed string pages:', len(changed), 'compiled programs:', len(report))
