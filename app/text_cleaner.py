import re
import unicodedata
from typing import Optional

# TCVN3 (ABC font) to Unicode UTF-8 mapping
TCVN3_MAP = {
    'µ': 'à', '¸': 'á', '¶': 'ả', '·': 'ã', '¹': 'ạ',
    '¨': 'ă', '»': 'ằ', '¾': 'ắ', '¼': 'ẳ', '½': 'ẵ', 'Æ': 'ặ',
    '©': 'â', 'Ç': 'ầ', 'Ê': 'ấ', 'È': 'ẩ', 'É': 'ẫ', 'Ë': 'ậ',
    'Ì': 'è', 'Ð': 'é', 'Í': 'ẻ', 'Î': 'ẽ', 'Ï': 'ẹ',
    'ª': 'ê', 'Ò': 'ề', 'Õ': 'ế', 'Ó': 'ể', 'Ô': 'ễ', 'Ö': 'ệ',
    '×': 'ì', 'Ý': 'í', 'Ø': 'ỉ', 'Ü': 'ĩ', 'Þ': 'ị',
    'ß': 'ò', 'á': 'ó', 'à': 'ỏ', 'ã': 'õ', 'ä': 'ọ',
    '«': 'ô', 'å': 'ồ', 'è': 'ố', 'æ': 'ổ', 'ç': 'ỗ', 'é': 'ộ',
    '¬': 'ơ', 'ê': 'ờ', 'í': 'ớ', 'ë': 'ở', 'ì': 'ỡ', 'î': 'ợ',
    'ï': 'ù', 'ó': 'ú', 'ñ': 'ủ', 'ò': 'ũ', 'ô': 'ụ',
    '­': 'ư', 'õ': 'ừ', 'ø': 'ứ', 'ö': 'ử', '÷': 'ữ', 'ù': 'ự',
    'ú': 'ỳ', 'ý': 'ý', 'û': 'ỷ', 'ü': 'ỹ', 'þ': 'ỵ',
    '®': 'đ', '§': 'Đ'
}

# Vietnamese Unicode distinctive characters (never present in 1-byte legacy TCVN3 ASCII or VNI)
VIET_UNICODE_CHARS = set('đĐơưƠƯ' + ''.join(chr(c) for c in range(0x1EA0, 0x1EFA)))

# TCVN3 distinctive non-ASCII symbols that rarely or never appear in valid text
TCVN3_DISTINCTIVE_CHARS = set('µ¸¶·¹¨»¾¼½Æ©ÇÈÉËÌÐÍÎÏªÒÕÓÔÖ×ØÜÞß«¬­÷®§')

# VNI Windows font to Unicode mapping (ordered from longest multi-char sequences to single characters)
VNI_MAP = {
    # 4-char sequences (horn + circumflex + tone)
    'öôù': 'ướ', 'öôø': 'ườ', 'öôû': 'ưở', 'öôõ': 'ưỡ', 'öôï': 'ượ',
    'ÖÔÙ': 'ƯỚ', 'ÖÔØ': 'ƯỜ', 'ÖÔÛ': 'ƯỞ', 'ÖÔÕ': 'ƯỠ', 'ÖÔÏ': 'ƯỢ',
    'Öôù': 'Ướ', 'Öôø': 'Ườ', 'Öôû': 'Ưở', 'Öôõ': 'Ưỡ', 'Öôï': 'Ượ',
    # 3-char sequences
    'öô': 'ươ', 'ÖÔ': 'ƯƠ', 'Öô': 'Ươ',
    # Circumflex & breve vowel combinations (lowercase, titlecase, uppercase)
    'aá': 'ấ', 'aà': 'ầ', 'aå': 'ẩ', 'aã': 'ẫ', 'aä': 'ậ',
    'Aá': 'Ấ', 'Aà': 'Ầ', 'Aå': 'Ẩ', 'Aã': 'Ẫ', 'Aä': 'Ậ',
    'AÁ': 'Ấ', 'AÀ': 'Ầ', 'AÅ': 'Ẩ', 'AÃ': 'Ẫ', 'AÄ': 'Ậ',
    'aé': 'ắ', 'aè': 'ằ', 'aú': 'ẳ', 'aü': 'ẵ', 'aë': 'ặ',
    'Aé': 'Ắ', 'Aè': 'Ằ', 'Aú': 'Ẳ', 'Aü': 'Ẵ', 'Aë': 'Ặ',
    'AÉ': 'Ắ', 'AÈ': 'Ằ', 'AÚ': 'Ẳ', 'AÜ': 'Ẵ', 'AË': 'Ặ',
    'eá': 'ế', 'eà': 'ề', 'eå': 'ể', 'eã': 'ễ', 'eä': 'ệ',
    'Eá': 'Ế', 'Eà': 'Ề', 'Eå': 'Ể', 'Eã': 'Ễ', 'Eä': 'Ệ',
    'EÁ': 'Ế', 'EÀ': 'Ề', 'EÅ': 'Ể', 'EÃ': 'Ễ', 'EÄ': 'Ệ',
    'oá': 'ố', 'oà': 'ồ', 'oå': 'ổ', 'oã': 'ỗ', 'oä': 'ộ',
    'Oá': 'Ố', 'Oà': 'Ồ', 'Oå': 'Ổ', 'Oã': 'Ỗ', 'Oä': 'Ộ',
    'OÁ': 'Ố', 'OÀ': 'Ồ', 'OÅ': 'Ổ', 'OÃ': 'Ỗ', 'OÄ': 'Ộ',
    'ôù': 'ớ', 'ôø': 'ờ', 'ôû': 'ở', 'ôõ': 'ỡ', 'ôï': 'ợ',
    'Ôù': 'Ớ', 'Ôø': 'Ờ', 'Ôû': 'Ở', 'Ôõ': 'Ỡ', 'Ôï': 'Ợ',
    'ÔÙ': 'Ớ', 'ÔØ': 'Ờ', 'ÔÛ': 'Ở', 'ÔÕ': 'Ỡ', 'ÔÏ': 'Ợ',
    'öù': 'ứ', 'öø': 'ừ', 'öû': 'ử', 'öõ': 'ữ', 'öï': 'ự',
    'Öù': 'Ứ', 'Öø': 'Ừ', 'Öû': 'Ử', 'Öõ': 'Ữ', 'Öï': 'Ự',
    'ÖÙ': 'Ứ', 'ÖØ': 'Ừ', 'ÖÛ': 'Ử', 'ÖÕ': 'Ữ', 'ÖÏ': 'Ự',
    'aâ': 'â', 'Aâ': 'Â', 'AÂ': 'Â',
    'aê': 'ă', 'Aê': 'Ă', 'AÊ': 'Ă',
    'eâ': 'ê', 'Eâ': 'Ê', 'EÂ': 'Ê',
    'oâ': 'ô', 'Oâ': 'Ô', 'OÂ': 'Ô',
    # Plain vowels + tone marks (lowercase, titlecase, uppercase)
    'aù': 'á', 'aø': 'à', 'aû': 'ả', 'aõ': 'ã', 'aï': 'ạ',
    'Aù': 'Á', 'Aø': 'À', 'Aû': 'Ả', 'Aõ': 'Ã', 'Aï': 'Ạ',
    'AÙ': 'Á', 'AØ': 'À', 'AÛ': 'Ả', 'AÕ': 'Ã', 'AÏ': 'Ạ',
    'eù': 'é', 'eø': 'è', 'eû': 'ẻ', 'eõ': 'ẽ', 'eï': 'ẹ',
    'Eù': 'É', 'Eø': 'È', 'Eû': 'Ẻ', 'Eõ': 'Ẽ', 'Eï': 'Ẹ',
    'EÙ': 'É', 'EØ': 'È', 'EÛ': 'Ẻ', 'EÕ': 'Ẽ', 'EÏ': 'Ẹ',
    'où': 'ó', 'oø': 'ò', 'oû': 'ỏ', 'oõ': 'õ', 'oï': 'ọ',
    'Où': 'Ó', 'Oø': 'Ò', 'Oû': 'Ỏ', 'Oõ': 'Õ', 'Oï': 'Ọ',
    'OÙ': 'Ó', 'OØ': 'Ò', 'OÛ': 'Ỏ', 'OÕ': 'Õ', 'OÏ': 'Ọ',
    'uù': 'ú', 'uø': 'ù', 'uû': 'ủ', 'uõ': 'ũ', 'uï': 'ụ',
    'Uù': 'Ú', 'Uø': 'Ù', 'Uû': 'Ủ', 'Uõ': 'Ũ', 'Uï': 'Ụ',
    'UÙ': 'Ú', 'UØ': 'Ù', 'UÛ': 'Ủ', 'UÕ': 'Ũ', 'UÏ': 'Ụ',
    'yù': 'ý', 'yø': 'ỳ', 'yû': 'ỷ', 'yõ': 'ỹ', 'yï': 'ỵ',
    'Yù': 'Ý', 'Yø': 'Ỳ', 'Yû': 'Ỷ', 'Yõ': 'Ỹ', 'Yï': 'Ỵ',
    'YÙ': 'Ý', 'YØ': 'Ỳ', 'YÛ': 'Ỷ', 'YÕ': 'Ỹ', 'YÏ': 'Ỵ',
    # Single-byte VNI representations (VNI-Times / Windows-1258 style)
    'ñ': 'đ', 'Ñ': 'Đ',
    'ö': 'ư', 'Ö': 'Ư',
    'ô': 'ơ', 'Ô': 'Ơ',
    'æ': 'ỉ', 'Æ': 'Ỉ',
    'ó': 'ĩ', 'Ó': 'Ĩ',
    'ò': 'ị', 'Ò': 'Ị',
    'î': 'ỵ', 'Î': 'Ỵ',
}

_VNI_PATTERNS = sorted(VNI_MAP.keys(), key=len, reverse=True)
_VNI_REGEX = re.compile('|'.join(re.escape(k) for k in _VNI_PATTERNS))

VNI_COMMON_WORDS_PAT = re.compile(
    r'(?i)\b(laø|vaø|meänh|ñeà|coù|ñuùng|cuûa|khoâng|moät|ngöôøi|ñöôïc|nhöng|toaùn|hoïc|ñònh|nghóa|khaúng|phaûi|bieát|caùc|trong|ñaõ|seõ|ñeán)\b'
)
VNI_DIGRAPH_PAT = re.compile(r'(?i)(?:ñ|öô|[aeouyöô][ùøûõïäâëéèúü]|o[áàảãạâä]|e[áàảãạêë])')


def is_likely_tcvn3(text: str) -> bool:
    """
    Check if the text is encoded in legacy TCVN3 (ABC).
    Crucial: Text containing standard Vietnamese Unicode characters (U+1EA0..U+1EF9, đ, Đ, ư, ơ)
    must NEVER be flagged as TCVN3 to prevent corrupting normal Unicode text.
    """
    if not text:
        return False
    sample = text[:3000]
    u_count = sum(1 for c in sample if c in VIET_UNICODE_CHARS)
    # Multiplication/bullet dots alone are not evidence of a legacy encoding.
    t_count = sum(1 for c in sample if c in TCVN3_DISTINCTIVE_CHARS and c != "·")

    # If the text already has genuine Vietnamese Unicode characters, it is NOT TCVN3
    if u_count > 2:
        return False

    # Genuine TCVN3 text has high frequency of distinctive marks and 0 Vietnamese Unicode characters
    sample_len = max(len(sample.strip()), 1)
    return t_count >= 5 and (t_count / sample_len) > 0.005


def decode_tcvn3(text: str) -> str:
    """Convert TCVN3 encoded string to standard Unicode UTF-8."""
    if not text:
        return ""
    res = [TCVN3_MAP.get(c, c) for c in text]
    return "".join(res)


def is_likely_vni(text: str) -> bool:
    """
    Positive conservative detection of VNI Windows (VNI-Times) legacy encoding.
    Must never trigger on modern Unicode Vietnamese, English code, Spanish, or German.
    """
    if not text:
        return False
    sample = text[:4000]
    u_count = sum(1 for c in sample if c in VIET_UNICODE_CHARS)
    if u_count > 2:
        return False

    # Positive detection: matches known frequent VNI words
    words_matches = len(VNI_COMMON_WORDS_PAT.findall(sample))
    if words_matches >= 2:
        return True

    # Co-occurrence of Vietnamese ñ/ö with multiple VNI tone digraphs
    has_d_or_u = ('ñ' in sample or 'Ñ' in sample or 'ö' in sample or 'Ö' in sample)
    digraphs = len(VNI_DIGRAPH_PAT.findall(sample))
    return has_d_or_u and digraphs >= 4


def decode_vni(text: str) -> str:
    """Convert VNI Windows encoded string to standard Unicode UTF-8."""
    if not text:
        return ""
    return _VNI_REGEX.sub(lambda m: VNI_MAP[m.group(0)], text)


def normalize_vietnamese_text(text: str, content_type: Optional[str] = None) -> str:
    """
    Clean up extracted document text, normalize Unicode NFC, remove null bytes, fix hyphens.
    Preserves code identifiers (camelCase), indentation, and math expressions.
    """
    if not text:
        return ""

    # Code whitespace, literals and operators are source content, not prose cleanup.
    is_code = content_type == 'code' or bool(re.search(
        r'(?m)^\s*(?:#include\b|class\s+\w+\s*\{|def\s+\w+\(|'
        r'(?:public|private|protected):|(?:int|void|const|char|float)\s+[^\n]*[;{])', text))
    if is_code:
        return unicodedata.normalize('NFC', text).replace('\x00', '').strip()

    # Auto-detect legacy encodings
    if is_likely_tcvn3(text):
        text = decode_tcvn3(text)
    elif is_likely_vni(text):
        text = decode_vni(text)

    # Unicode Normalization Form C
    text = unicodedata.normalize("NFC", text)

    # Remove null bytes and non-printable control characters (except newline, tab)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)

    # Dehyphenation is safe only when the caller explicitly identifies prose.
    # Mixed pages can contain subtraction split over lines.
    if content_type == 'prose':
        text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)

    # Normalize common glued Vietnamese boundaries from PDF extraction (explicit prose only; NO camelCase split)
    text = re.sub(r'(?i)\b(ánh\s*)?(xạ)(tuyến\s*tính)\b', r'\1xạ \3', text)
    text = re.sub(r'(?i)\b(đại\s*)?(số)(tuyến\s*tính)\b', r'\1số \3', text)
    text = re.sub(r'(?i)\b(thể)(được)\b', r'\1 \2', text)
    text = re.sub(r'(?i)\b(cơ\s*)?(sở)(cho|là)\b', r'\1sở \3', text)
    text = re.sub(r'(?i)\b(kết\s*)?(quả)(trả\s*về)\b', r'\1quả \3', text)
    text = re.sub(r'(?i)\b(trả\s*)?(về)(là)\b', r'\1về \3', text)
    text = re.sub(r'(?i)\b(mở)(rộng)\b', r'\1 \2', text)
    text = re.sub(r'(?i)\b(tự)(nhiên)\b', r'\1 \2', text)
    text = re.sub(r'(?i)\b(xạ)(f)\b', r'\1 \2', text)

    # Replace multiple newlines with at most two
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Collapse multiple internal spaces while preserving leading line indentation and quoted string literals
    parts = []
    last_end = 0
    for m in re.finditer(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')', text):
        pre = text[last_end:m.start()]
        pre = re.sub(r'(?<=\S)[ \t]{2,}', ' ', pre)
        parts.append(pre)
        parts.append(m.group(0))
        last_end = m.end()
    post = text[last_end:]
    post = re.sub(r'(?<=\S)[ \t]{2,}', ' ', post)
    parts.append(post)
    text = "".join(parts)

    return text.strip()


def sanitize_for_prompt_context(text: str) -> str:
    """
    Sanitize text before injecting into prompt context to prevent
    indirect prompt injection attacks embedded within documents.
    """
    if not text:
        return ""

    # Neutralize common indirect jailbreak trigger phrases
    jailbreak_patterns = [
        r"(?i)ignore\s+(?:all\s+)?(?:(?:previous|prior|above)\s+)?instructions",
        r"(?i)disregard\s+(?:all\s+)?(?:(?:previous|prior|above)\s+)?instructions",
        r"(?i)system\s*:\s*you\s+are\s+now",
        r"(?i)bỏ\s+qua\s+(?:tất\s+cả\s+)?(?:các\s+)?hướng\s+dẫn(?:\s+trước)?",
        r"(?i)từ\s+bây\s+giờ\s+hãy\s+đóng\s+vai",
    ]
    sanitized = text
    for pat in jailbreak_patterns:
        sanitized = re.sub(pat, "[Bị vô hiệu hóa: Cụm từ chỉ dẫn không an toàn]", sanitized)

    # Neutralize ChatML tokens
    sanitized = re.sub(r"<\|([^|>]+)\|>", r"&lt;|\1|&gt;", sanitized)

    # Broad HTML/XML escaping to disarm all tag injection and breakout attempts
    sanitized = sanitized.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return sanitized


class TextCleaner:
    """Compatibility interface for text cleaning and decoding."""
    clean = staticmethod(normalize_vietnamese_text)
    normalize_vietnamese_text = staticmethod(normalize_vietnamese_text)
    is_likely_tcvn3 = staticmethod(is_likely_tcvn3)
    decode_tcvn3 = staticmethod(decode_tcvn3)
    is_likely_vni = staticmethod(is_likely_vni)
    decode_vni = staticmethod(decode_vni)
    sanitize_for_prompt_context = staticmethod(sanitize_for_prompt_context)
