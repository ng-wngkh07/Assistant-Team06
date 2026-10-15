"""Shared source-bound visual reviews for handwritten and scanned documents."""
import hashlib
import json
from pathlib import Path


FORBIDDEN_C0_CODEPOINTS = frozenset(c for c in range(32) if c not in (9, 10, 13))


def _check_no_forbidden_control(text: str, field_name: str, filename: str, page: int) -> None:
    if not isinstance(text, str):
        return
    for idx, ch in enumerate(text):
        code = ord(ch)
        if code in FORBIDDEN_C0_CODEPOINTS:
            raise ValueError(
                f"Forbidden control character U+{code:04X} ({repr(ch)}) in '{field_name}' at index {idx}: {filename}:{page}"
            )


def latest_page_reviews(root: Path) -> dict:
    result = {}
    folders = (
        root/'data/evaluation/handwriting-complete-20261004/reviews',
        root/'data/evaluation/corpus-completion-20261004/source-reviews',
        root/'data/evaluation/rag-repair-yc205/reviews',
    )
    for folder in folders:
        for path in folder.glob('*/*.json'):
            if path.name.endswith('-raw.json'):
                continue
            review = json.loads(path.read_text())
            key = (review['filename'], review['page'])
            previous = result.get(key)
            if previous is None or review['reviewed_at'] > previous[1]['reviewed_at']:
                result[key] = (path, review)
    return result


def validate_page_review(review: dict, filename: str, page: int, source_sha256: str) -> None:
    visual_only = review.get('kind') == 'visual'
    if visual_only and (review.get('visible_text_status') != 'none'
                        or review.get('transcript', '').strip()
                        or not review.get('visual_description', '').strip()):
        raise ValueError(f'Invalid visual-only review: {filename}:{page}')
    if (review['filename'] != filename or review['page'] != page
        or review['source_sha256'] != source_sha256 or review.get('image_reviewed') is not True
        or review['completeness'] not in ('complete', 'partial')
        or (review['kind'] not in ('blank', 'visual') and not review['transcript'].strip())
        or (review['completeness'] == 'complete' and review.get('unresolved'))):
        raise ValueError(f'Invalid review identity or status: {filename}:{page}')
    # Review text is curated; raw captures remain untouched for source fidelity.
    for field in ('transcript', 'visual_description', 'editorial'):
        value = review.get(field)
        if isinstance(value, str) and value:
            _check_no_forbidden_control(value, field, filename, page)
    unresolved = review.get('unresolved')
    if isinstance(unresolved, list):
        for idx, item in enumerate(unresolved):
            if isinstance(item, str) and item:
                _check_no_forbidden_control(item, f'unresolved[{idx}]', filename, page)
    image, raw_path = Path(review['image_path']), Path(review['raw_capture_path'])
    if (hashlib.sha256(image.read_bytes()).hexdigest() != review['image_sha256']
        or hashlib.sha256(raw_path.read_bytes()).hexdigest() != review['raw_capture_sha256']):
        raise ValueError(f'Changed review evidence: {filename}:{page}')
    raw = json.loads(raw_path.read_text())
    if (raw['source_relpath'] != filename or raw['page_num'] != page
        or raw['source_hash'] != source_sha256 or raw['image_hash'] != review['image_sha256']):
        raise ValueError(f'Invalid raw capture binding: {filename}:{page}')


def reviewed_page_text(review: dict) -> str:
    if review['kind'] == 'blank':
        return ''
    if review['kind'] == 'visual':
        text = ('[MÔ TẢ HÌNH ĐÃ ĐỐI CHIẾU; KHÔNG PHẢI NGUYÊN VĂN; '
                'CẦN XEM ẢNH NGUỒN]\n' + review['visual_description'])
    else:
        text = '[NỘI DUNG NGUỒN ĐÃ ĐỐI CHIẾU ẢNH; KÝ HIỆU CHUẨN HÓA]\n' + review['transcript']
    if review.get('editorial'):
        if 'Codex' in review.get('reviewer', ''):
            text += '\n\n[HIỆU ĐÍNH / GIẢI THÍCH CỦA CODEX; KHÔNG PHẢI NGUYÊN VĂN VIẾT TAY]\n' + review['editorial']
        else:
            text += '\n\n[HIỆU ĐÍNH / GIẢI THÍCH; KHÔNG PHẢI NGUYÊN VĂN]\n' + review['editorial']
    if review.get('unresolved'):
        text += '\n\n[PHẦN CHƯA XÁC ĐỊNH]\n' + '\n'.join(review['unresolved'])
    return text


def reviewed_page_state(review: dict) -> str:
    if review['completeness'] != 'complete':
        return 'visual_review_partial'
    return {'blank': 'blank_verified', 'visual': 'visual_verified'}.get(review['kind'], 'text_verified')
