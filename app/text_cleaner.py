import re
import unicodedata

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

# Vietnamese Unicode distinctive characters (never present in 1-byte legacy TCVN3 ASCII)
VIET_UNICODE_CHARS = set('đĐơưƠƯ' + ''.join(chr(c) for c in range(0x1EA0, 0x1EFA)))

# TCVN3 distinctive non-ASCII symbols that rarely or never appear in valid text
TCVN3_DISTINCTIVE_CHARS = set('µ¸¶·¹¨»¾¼½Æ©ÇÈÉËÌÐÍÎÏªÒÕÓÔÖ×ØÜÞß«¬­÷®§')

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
    t_count = sum(1 for c in sample if c in TCVN3_DISTINCTIVE_CHARS)
    
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

def normalize_vietnamese_text(text: str) -> str:
    """Clean up extracted PDF text, normalize Unicode NFC, remove null bytes, fix hyphens."""
    if not text:
        return ""
    
    # Auto-detect TCVN3
    if is_likely_tcvn3(text):
        text = decode_tcvn3(text)
    
    # Unicode Normalization Form C
    text = unicodedata.normalize("NFC", text)
    
    # Remove null bytes and non-printable control characters (except newline, tab)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)
    
    # Fix hyphenated line breaks (e.g., "psycho-\nlogy" -> "psychology")
    text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)
    
    # Normalize common glued Vietnamese boundaries from PDF extraction
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
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
    
    # Replace multiple spaces/tabs with single space
    text = re.sub(r'[ \t]{2,}', ' ', text)
    
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
