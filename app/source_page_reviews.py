"""Shared source-bound visual reviews for handwritten and scanned documents."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import unicodedata


FORBIDDEN_C0_CODEPOINTS = frozenset(c for c in range(32) if c not in (9, 10, 13))
REVIEW_RELATIVE_FOLDERS: tuple[str, ...] = (
    'data/evaluation/handwriting-complete-20261004/reviews',
    'data/evaluation/corpus-completion-20261004/source-reviews',
    'data/evaluation/rag-repair-yc205/reviews',
)
_LOCAL_REVIEW_DIRS_CONFIG = 'data/runtime/page_review_dirs.json'
_EXTRA_REVIEW_DIRS_ENV = 'STUDY_EXTRA_REVIEW_DIRS'
_IGNORED_REVIEW_SUFFIXES: tuple[str, ...] = ('-raw.json', '-raw-dup.json', '-source-links.json')


def _check_no_forbidden_control(text: str, field_name: str, filename: str, page: int) -> None:
    if not isinstance(text, str):
        return
    for idx, ch in enumerate(text):
        code = ord(ch)
        if code in FORBIDDEN_C0_CODEPOINTS:
            raise ValueError(
                f"Forbidden control character U+{code:04X} ({repr(ch)}) in '{field_name}' at index {idx}: {filename}:{page}"
            )


def _parse_reviewed_at(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _resolve_review_folders(root: Path) -> tuple[tuple[str, Path], ...]:
    """Resolve default and operator-configured local page-review directories."""
    raw_entries: list[str] = list(REVIEW_RELATIVE_FOLDERS)
    cfg_path = root / _LOCAL_REVIEW_DIRS_CONFIG
    if cfg_path.is_file():
        try:
            loaded = json.loads(cfg_path.read_text(encoding='utf-8'))
            extra = loaded.get('review_dirs') if isinstance(loaded, dict) else loaded
            if isinstance(extra, list):
                for item in extra:
                    if isinstance(item, str) and item.strip():
                        raw_entries.append(item.strip())
        except (OSError, ValueError, TypeError):
            pass
    env_dirs = os.environ.get(_EXTRA_REVIEW_DIRS_ENV, '')
    if env_dirs:
        for item in env_dirs.split(os.pathsep):
            cleaned = item.strip()
            if cleaned:
                raw_entries.append(cleaned)

    resolved: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for item in raw_entries:
        p = Path(item)
        folder = p if p.is_absolute() else (root / p)
        try:
            norm_key = unicodedata.normalize('NFC', str(folder.resolve()))
        except OSError:
            norm_key = unicodedata.normalize('NFC', os.path.abspath(str(folder)))
        if norm_key in seen:
            continue
        seen.add(norm_key)
        resolved.append((item, folder))
    return tuple(resolved)


def review_dirs_signature(root: Path) -> tuple:
    """Return a fast os.scandir metadata signature across all page-review folders."""
    sig = []
    for label, folder in _resolve_review_folders(root):
        if not folder.is_dir():
            sig.append((label, 0, 0, 0, 0))
            continue
        subdir_count = 0
        file_count = 0
        total_bytes = 0
        try:
            max_mtime_ns = int(folder.stat().st_mtime_ns)
            with os.scandir(folder) as top_entries:
                for sub in top_entries:
                    if not sub.is_dir(follow_symlinks=False):
                        continue
                    subdir_count += 1
                    try:
                        sub_st = sub.stat(follow_symlinks=False)
                        if sub_st.st_mtime_ns > max_mtime_ns:
                            max_mtime_ns = int(sub_st.st_mtime_ns)
                        with os.scandir(sub.path) as files:
                            for entry in files:
                                name = entry.name
                                if not name.endswith('.json') or name.endswith(_IGNORED_REVIEW_SUFFIXES):
                                    continue
                                if not entry.is_file(follow_symlinks=False):
                                    continue
                                fst = entry.stat(follow_symlinks=False)
                                file_count += 1
                                total_bytes += int(fst.st_size)
                                if fst.st_mtime_ns > max_mtime_ns:
                                    max_mtime_ns = int(fst.st_mtime_ns)
                    except OSError:
                        continue
        except OSError:
            max_mtime_ns = 0
        sig.append((label, subdir_count, file_count, max_mtime_ns, total_bytes))
    return tuple(sig)


def latest_page_reviews(root: Path) -> dict:
    result = {}
    for _, folder in _resolve_review_folders(root):
        if not folder.is_dir():
            continue
        for path in folder.glob('*/*.json'):
            if path.name.endswith(_IGNORED_REVIEW_SUFFIXES):
                continue
            review = json.loads(path.read_text())
            if not isinstance(review, dict) or 'filename' not in review or 'page' not in review:
                continue
            key = (review['filename'], review['page'])
            previous = result.get(key)
            if previous is None or _parse_reviewed_at(review['reviewed_at']) > _parse_reviewed_at(previous[1]['reviewed_at']):
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
