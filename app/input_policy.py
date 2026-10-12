"""Input validation and security policy for study agent admissions (YC195)."""

import unicodedata
import re
from typing import Optional, List, Dict, Any

# Bidi control characters to reject:
# LRE (\u202a), RLE (\u202b), PDF (\u202c), LRO (\u202d), RLO (\u202e)
# LRI (\u2066), RLI (\u2067), FSI (\u2068), PDI (\u2069), ALM (\u061c)
BIDI_OVERRIDES = frozenset({
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069", "\u061c"
})

ZERO_WIDTH_CHARS = frozenset({
    "\u200b", "\u200c", "\u200d", "\ufeff", "\u2060", "\u180e"
})

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.:]{1,128}$")


def validate_query_text(query: str) -> str:
    """Validate and sanitize user query text strictly before dispatch or model invocation."""
    if not isinstance(query, str):
        raise ValueError("Câu hỏi phải là chuỗi ký tự.")

    # 1. Lone surrogates check
    if any(0xD800 <= ord(c) <= 0xDFFF for c in query):
        raise ValueError("Ký tự Unicode không hợp lệ (lone surrogate).")

    # 2. NUL and C0 controls except LF (\n, 10), CR (\r, 13), Tab (\t, 9)
    for c in query:
        code = ord(c)
        if (code < 32 and code not in (9, 10, 13)) or code == 127:
            raise ValueError("Ký tự điều khiển không được phép.")

    # 3. Bidi overrides / isolate
    if any(c in BIDI_OVERRIDES for c in query):
        raise ValueError("Ký tự điều khiển hướng chữ (bidi override/isolate) không được phép.")

    # 4. Combining character runs > 16
    combining_run = 0
    for c in query:
        cat = unicodedata.category(c)
        if cat in ("Mn", "Mc", "Me"):
            combining_run += 1
            if combining_run > 16:
                raise ValueError("Chuỗi dấu kết hợp (combining run) vượt quá giới hạn 16.")
        else:
            combining_run = 0

    # 5. Normalize NFC and trim whitespace
    query_nfc = unicodedata.normalize("NFC", query)
    trimmed = query_nfc.strip()

    # 6. Length and UTF-8 byte budget
    if len(trimmed) == 0:
        raise ValueError("Câu hỏi không được để trống.")
    if len(trimmed) > 4000:
        raise ValueError("Câu hỏi vượt quá giới hạn 4000 ký tự.")

    try:
        encoded = trimmed.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Lỗi mã hóa Unicode.") from exc

    if len(encoded) > 16000:
        raise ValueError("Câu hỏi vượt quá giới hạn 16000 bytes UTF-8.")

    # 7. Zero-width only check
    non_zw = "".join(c for c in trimmed if c not in ZERO_WIDTH_CHARS and not c.isspace())
    if not non_zw:
        raise ValueError("Câu hỏi chỉ chứa ký tự ẩn/zero-width hoặc khoảng trắng.")

    # 8. Symbols-or-emoji only check:
    # Meaningful text must contain at least one letter (L*) or number (N*)
    has_meaningful_content = any(
        unicodedata.category(c)[0] in ("L", "N")
        for c in trimmed
    )
    if not has_meaningful_content:
        raise ValueError("Câu hỏi chỉ chứa biểu tượng/emoji hoặc ký tự đặc biệt, thiếu nội dung học tập.")

    return trimmed


def validate_chat_history(history: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, str]]]:
    """Validate chat history messages: bounded, user/assistant only, length limited."""
    if history is None:
        return None
    if not isinstance(history, list):
        raise ValueError("Lịch sử trò chuyện phải là danh sách.")
    if len(history) > 32:
        raise ValueError("Lịch sử trò chuyện vượt quá giới hạn 32 tin nhắn.")

    total_bytes = 0
    validated_history = []
    for idx, msg in enumerate(history):
        if not isinstance(msg, dict):
            raise ValueError(f"Tin nhắn #{idx+1} trong lịch sử không hợp lệ.")
        role = msg.get("role")
        if not isinstance(role, str):
            raise ValueError(f"Vai trò tin nhắn #{idx+1} phải là chuỗi.")
        if any(0xD800 <= ord(c) <= 0xDFFF for c in role):
            raise ValueError(f"Vai trò tin nhắn #{idx+1} chứa ký tự Unicode không hợp lệ.")
        if role not in ("user", "assistant"):
            raise ValueError("Vai trò không được phép. Chỉ chấp nhận 'user' hoặc 'assistant'.")
        content = msg.get("content")
        if not isinstance(content, str):
            raise ValueError(f"Nội dung tin nhắn #{idx+1} phải là chuỗi ký tự.")

        # Check lone surrogates
        if any(0xD800 <= ord(c) <= 0xDFFF for c in content):
            raise ValueError(f"Nội dung tin nhắn #{idx+1} chứa ký tự Unicode không hợp lệ.")

        # Check control characters
        for c in content:
            code = ord(c)
            if (code < 32 and code not in (9, 10, 13)) or code == 127:
                raise ValueError(f"Nội dung tin nhắn #{idx+1} chứa ký tự điều khiển không được phép.")

        # Check bidi overrides
        if any(c in BIDI_OVERRIDES for c in content):
            raise ValueError(f"Nội dung tin nhắn #{idx+1} chứa ký tự điều khiển hướng chữ không được phép.")

        # Check combining character runs > 16
        combining_run = 0
        for c in content:
            cat = unicodedata.category(c)
            if cat in ("Mn", "Mc", "Me"):
                combining_run += 1
                if combining_run > 16:
                    raise ValueError(f"Nội dung tin nhắn #{idx+1} chứa chuỗi dấu kết hợp vượt quá 16.")
            else:
                combining_run = 0

        if len(content) > 4000:
            raise ValueError(f"Nội dung tin nhắn #{idx+1} vượt quá giới hạn 4000 ký tự.")

        try:
            content_bytes = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"Lỗi mã hóa tin nhắn #{idx+1}.") from exc

        if len(content_bytes) > 16000:
            raise ValueError(f"Nội dung tin nhắn #{idx+1} vượt quá giới hạn 16000 bytes UTF-8.")

        total_bytes += len(content_bytes)
        if total_bytes > 32000:
            raise ValueError("Tổng dung lượng lịch sử trò chuyện vượt quá giới hạn 32000 bytes UTF-8.")

        validated_history.append({"role": role, "content": content})

    return validated_history


def validate_identifier(val: Optional[str], name: str = "id") -> Optional[str]:
    """Validate session_id or request_id to ensure safe UUID-compatible identifier."""
    if val is None:
        return None
    if not isinstance(val, str):
        raise ValueError(f"{name} phải là chuỗi ký tự.")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in val):
        raise ValueError(f"{name} chứa ký tự Unicode không hợp lệ.")
    trimmed = val.strip()
    if not trimmed:
        return None
    if len(trimmed) > 128:
        raise ValueError(f"{name} vượt quá giới hạn 128 ký tự.")
    if not ID_PATTERN.match(trimmed):
        raise ValueError(f"{name} chứa ký tự không hợp lệ.")
    return trimmed


def validate_topic_input(topic: str) -> str:
    """Validate learning topic input."""
    if not isinstance(topic, str):
        raise ValueError("Chủ đề phải là chuỗi ký tự.")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in topic):
        raise ValueError("Ký tự Unicode không hợp lệ trong chủ đề.")
    for c in topic:
        code = ord(c)
        if (code < 32 and code not in (9, 10, 13)) or code == 127:
            raise ValueError("Ký tự điều khiển không được phép trong chủ đề.")
    if any(c in BIDI_OVERRIDES for c in topic):
        raise ValueError("Chủ đề chứa ký tự điều khiển hướng chữ không được phép.")
    combining_run = 0
    for c in topic:
        cat = unicodedata.category(c)
        if cat in ("Mn", "Mc", "Me"):
            combining_run += 1
            if combining_run > 16:
                raise ValueError("Chủ đề chứa chuỗi dấu kết hợp vượt quá 16.")
        else:
            combining_run = 0
    topic_nfc = unicodedata.normalize("NFC", topic).strip()
    if not topic_nfc:
        raise ValueError("Chủ đề không được để trống.")
    if len(topic_nfc) > 120:
        raise ValueError("Chủ đề vượt quá giới hạn 120 ký tự.")
    if not any(unicodedata.category(c)[0] in ("L", "N") for c in topic_nfc):
        raise ValueError("Chủ đề phải chứa chữ cái hoặc chữ số có nghĩa.")
    return topic_nfc
