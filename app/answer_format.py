"""Keep private reasoning and source markers out of the user-facing answer."""

import re


_THINK = re.compile(r"<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", re.I | re.S)
_FINAL = re.compile(r"<\s*final\s*>(.*?)<\s*/\s*final\s*>", re.I | re.S)
_SOURCE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", re.I)
_BOOK_SOURCE = re.compile(r"\[[^\[\]\n]{2,160},\s*Trang\s+\d+\]", re.I)
_SOURCE_SECTION = re.compile(
    r"(?is)\n[ \t\r\n]*(?:#+[ \t]+)?(?:nguồn(?: tham khảo)?|trích dẫn|tài liệu tham khảo|sources?|references?)[ \t]*:.*$"
)
_SOURCE_INTRO = re.compile(
    r"(?i)\b(?:theo\s+(?:(?:các\s+)?(?:đoạn(?:\s+trích)?|trích\s+đoạn|nguồn|tài liệu)\s*|(?=\[\s*S\d+))|"
    r"(?:trong|ở|tại)\s+(?:các\s+)?(?:đoạn(?:\s+trích)?|trích\s+đoạn|nguồn|tài liệu)\s*)"
    r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]\s*,?\s*"
)
_MATH_START = re.compile(
    r"^(?:"
    r"[a-z]\s*(?:[\(=+\-*/^<>≠≤≥∈∉±²³⁴⁵⁶⁷⁸⁹]|==|!=|\b(?:in|notin|thuộc)\b)"
    r"|\(?\s*d(?:\^?\d+|[²³⁴⁵⁶⁷⁸⁹]+)?\s*[a-zA-Z]?\s*/\s*d(?:\^?\d+|[²³⁴⁵⁶⁷⁸⁹]+)?\s*[a-zA-Z]?(?:\^?\d+|[²³⁴⁵⁶⁷⁸⁹]+)?"
    r")"
)

# Supported mathematical command maps
_MATH_CMD_MAP = {
    # Greek lowercase
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ϵ", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ",
    "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    # Greek uppercase
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # Sets & logic
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆",
    "supset": "⊃", "supseteq": "⊇", "cap": "∩", "cup": "∪",
    "setminus": "∖", "emptyset": "∅", "forall": "∀", "exists": "∃",
    # Operators & relations
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡",
    "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔",
    "longrightarrow": "→", "longleftarrow": "←",
    # Calculus & functions
    "infty": "∞", "sum": "∑", "prod": "∏", "int": "∫", "partial": "∂",
    "ln": "ln", "log": "log", "exp": "exp", "lim": "lim",
    "sup": "sup", "inf": "inf", "max": "max", "min": "min",
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot",
}

_MATH_SET_MAP = {
    "R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ",
}

_MATH_CAL_MAP = {
    "A": "𝒜", "B": "ℬ", "C": "𝒞", "D": "𝒟", "E": "ℰ", "F": "ℱ",
    "G": "𝒢", "H": "ℋ", "I": "ℐ", "J": "𝒥", "K": "𝒦", "L": "ℒ",
    "M": "ℳ", "N": "𝒩", "O": "𝒪", "P": "𝒫", "Q": "𝒬", "R": "ℛ",
    "S": "𝒮", "T": "𝒯", "U": "𝒰", "V": "𝒱", "W": "𝒲", "X": "𝒳",
    "Y": "𝒴", "Z": "𝒵",
}

_UNSUPPORTED_DANGEROUS_CMDS = {
    "input", "include", "catcode", "def", "let", "edef", "gdef", "xdef",
    "openin", "read", "write", "openout", "closein", "closeout",
    "special", "shipout", "csname", "endcsname", "eval", "exec",
}


def _to_math_bold(char: str) -> str:
    """Map ASCII alphanumeric character to Unicode mathematical bold."""
    if "A" <= char <= "Z":
        return chr(ord(char) - 65 + 0x1D400)
    if "a" <= char <= "z":
        return chr(ord(char) - 97 + 0x1D41A)
    if "0" <= char <= "9":
        return chr(ord(char) - 48 + 0x1D7CE)
    return char


def _extract_braced_arg(text: str, pos: int) -> tuple[bool, str, int]:
    """Given pos pointing to '{', extract content until matching '}'.
    Returns (ok, content, next_pos).
    """
    if pos >= len(text) or text[pos] != "{":
        return False, "", pos
    depth = 0
    start = pos + 1
    for i in range(pos, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return True, text[start:i], i + 1
    return False, "", pos


def _normalize_math_block(expr: str, depth: int = 0) -> tuple[bool, str]:
    """Normalize a pure math expression (inside delimiters or standalone)."""
    if depth > 40:
        return False, ""

    # 1. Protect literal braces \{ and \}
    s = expr.replace(r"\{", "\x01").replace(r"\}", "\x02")

    # 2. Check brace balance
    if s.count("{") != s.count("}"):
        return False, ""

    # 3. Check dangerous / unsupported commands
    for m in re.finditer(r"\\([a-zA-Z]+)", s):
        if m.group(1) in _UNSUPPORTED_DANGEROUS_CMDS:
            return False, ""

    # 4. Check for indexed root: \sqrt[...]
    if re.search(r"\\sqrt\s*\[", s):
        return False, ""

    # 5. Process \mathcal
    while True:
        m = re.search(r"\\mathcal(?![a-zA-Z])", s)
        if not m:
            break
        idx = m.start()
        pos = m.end()
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos < len(s) and s[pos] == "{":
            ok, arg, next_pos = _extract_braced_arg(s, pos)
            if not ok:
                return False, ""
            clean_arg = arg.strip()
            if not clean_arg or clean_arg not in _MATH_CAL_MAP:
                return False, ""
            s = s[:idx] + _MATH_CAL_MAP[clean_arg] + s[next_pos:]
        elif pos < len(s) and pos > m.end() and s[pos].isalpha():
            letter = s[pos]
            if letter not in _MATH_CAL_MAP:
                return False, ""
            s = s[:idx] + _MATH_CAL_MAP[letter] + s[pos + 1:]
        else:
            return False, ""

    # 6. Process \frac innermost first
    loop_count = 0
    while r"\frac" in s:
        loop_count += 1
        if loop_count > 50:
            return False, ""
        frac_indices = [m.start() for m in re.finditer(r"\\frac\b", s)]
        replaced = False
        for idx in reversed(frac_indices):
            pos = idx + len(r"\frac")
            while pos < len(s) and s[pos].isspace():
                pos += 1
            ok1, num, pos1 = _extract_braced_arg(s, pos)
            if not ok1 or not num.strip():
                return False, ""
            while pos1 < len(s) and s[pos1].isspace():
                pos1 += 1
            ok2, den, pos2 = _extract_braced_arg(s, pos1)
            if not ok2 or not den.strip():
                return False, ""

            if r"\frac" in num or r"\frac" in den:
                continue

            ok_n, norm_num = _normalize_math_block(num, depth + 1)
            if not ok_n:
                return False, ""
            ok_d, norm_den = _normalize_math_block(den, depth + 1)
            if not ok_d:
                return False, ""

            replacement = f"({norm_num})/({norm_den})"
            s = s[:idx] + replacement + s[pos2:]
            replaced = True
            break
        if not replaced:
            return False, ""

    # 7. Process \sqrt innermost first
    loop_count = 0
    while r"\sqrt" in s:
        loop_count += 1
        if loop_count > 50:
            return False, ""
        sqrt_indices = [m.start() for m in re.finditer(r"\\sqrt\b", s)]
        replaced = False
        for idx in reversed(sqrt_indices):
            pos = idx + len(r"\sqrt")
            while pos < len(s) and s[pos].isspace():
                pos += 1
            if pos < len(s) and s[pos] == "[":
                return False, ""
            if pos < len(s) and s[pos] == "{":
                ok, arg, pos1 = _extract_braced_arg(s, pos)
                if not ok or not arg.strip():
                    return False, ""
                if r"\sqrt" in arg:
                    continue
                ok_a, norm_arg = _normalize_math_block(arg, depth + 1)
                if not ok_a:
                    return False, ""
                replacement = f"√({norm_arg})"
                s = s[:idx] + replacement + s[pos1:]
                replaced = True
                break
            else:
                m = re.match(r"^\s*([a-zA-Z0-9])", s[pos:])
                if m:
                    replacement = f"√({m.group(1)})"
                    s = s[:idx] + replacement + s[pos + m.end():]
                    replaced = True
                    break
                else:
                    return False, ""
        if not replaced:
            return False, ""

    # 8. Process exponents ^{...} and subscripts _{...}
    loop_count = 0
    while "^{" in s:
        loop_count += 1
        if loop_count > 50:
            return False, ""
        idx = s.rfind("^{")
        ok, arg, next_pos = _extract_braced_arg(s, idx + 1)
        if not ok or not arg.strip():
            return False, ""
        ok_a, norm_arg = _normalize_math_block(arg, depth + 1)
        if not ok_a:
            return False, ""
        s = s[:idx] + f"^({norm_arg})" + s[next_pos:]

    loop_count = 0
    while "_{" in s:
        loop_count += 1
        if loop_count > 50:
            return False, ""
        idx = s.rfind("_{")
        ok, arg, next_pos = _extract_braced_arg(s, idx + 1)
        if not ok or not arg.strip():
            return False, ""
        ok_a, norm_arg = _normalize_math_block(arg, depth + 1)
        if not ok_a:
            return False, ""
        s = s[:idx] + f"_({norm_arg})" + s[next_pos:]

    # 9. Number sets \mathbb
    while True:
        m = re.search(r"\\mathbb(?![a-zA-Z])", s)
        if not m:
            break
        idx = m.start()
        pos = m.end()
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos < len(s) and s[pos] == "{":
            ok, arg, next_pos = _extract_braced_arg(s, pos)
            if not ok:
                return False, ""
            clean_arg = arg.strip()
            if not clean_arg or clean_arg not in _MATH_SET_MAP:
                return False, ""
            s = s[:idx] + _MATH_SET_MAP[clean_arg] + s[next_pos:]
        elif pos < len(s) and pos > m.end() and s[pos].isalpha():
            token = s[pos]
            if token not in _MATH_SET_MAP:
                return False, ""
            s = s[:idx] + _MATH_SET_MAP[token] + s[pos + 1:]
        else:
            return False, ""

    # 10. Bold formatting \mathbf
    while True:
        m = re.search(r"\\mathbf(?![a-zA-Z])", s)
        if not m:
            break
        idx = m.start()
        pos = m.end()
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos < len(s) and s[pos] == "{":
            ok, arg, next_pos = _extract_braced_arg(s, pos)
            if not ok:
                return False, ""
            clean = arg.strip()
            if not clean:
                return False, ""
            if len(clean) == 1 and clean.isascii() and clean.isalnum():
                replacement = _to_math_bold(clean)
            else:
                # Bold can identify a different mathematical object. Refuse
                # unsupported compound/non-ASCII forms rather than flattening.
                return False, ""
            s = s[:idx] + replacement + s[next_pos:]
        elif pos < len(s) and pos > m.end() and s[pos].isascii() and s[pos].isalnum():
            replacement = _to_math_bold(s[pos])
            s = s[:idx] + replacement + s[pos + 1:]
        else:
            return False, ""

    # 11. Formatting / text commands
    for cmd in ["text", "mathrm", "mathit", "mathsf", "mathtt", "operatorname"]:
        pattern = re.compile(rf"\\{cmd}(?![a-zA-Z])")
        while True:
            m = pattern.search(s)
            if not m:
                break
            idx = m.start()
            pos = m.end()
            while pos < len(s) and s[pos].isspace():
                pos += 1
            if pos < len(s) and s[pos] == "{":
                ok, arg, next_pos = _extract_braced_arg(s, pos)
                if not ok:
                    return False, ""
                ok_a, norm_arg = _normalize_math_block(arg, depth + 1)
                if not ok_a:
                    return False, ""
                if any(op in norm_arg for op in ("+", "-", "±", "∓", "*", "/", "·", "×", "=")) and not (norm_arg.startswith("(") and norm_arg.endswith(")")):
                    replacement = f"({norm_arg})"
                else:
                    replacement = norm_arg
                s = s[:idx] + replacement + s[next_pos:]
            elif pos < len(s) and pos > m.end() and s[pos].isalnum():
                s = s[:idx] + s[pos] + s[pos + 1:]
            else:
                break

    # 11. Sizing modifiers & limits
    s = re.sub(r"\\left\s*([(\[{|.])", lambda m: "" if m.group(1) == "." else m.group(1), s)
    s = re.sub(r"\\right\s*([)\]}|.])", lambda m: "" if m.group(1) == "." else m.group(1), s)
    s = re.sub(r"\\(?:limits|nolimits)\b", "", s)

    # 12. Spacing commands
    s = re.sub(r"\\[,;:!]", " ", s)
    s = re.sub(r"\\(?:quad|qquad)\b", " ", s)

    # 13. Supported symbol / Greek commands (consuming TeX command-delimiting space)
    def _replace_cmd(match: re.Match) -> str:
        name = match.group(1)
        if name in _MATH_CMD_MAP:
            return _MATH_CMD_MAP[name]
        return match.group(0)

    # In TeX, space immediately following command word is delimiter before next token
    s = re.sub(r"\\([a-zA-Z]+)(?:[ \t]+(?=[a-zA-Z0-9]))?", _replace_cmd, s)

    # 14. Check if any unsupported commands remain
    if re.search(r"\\[a-zA-Z]+", s):
        return False, ""

    # 15. Convert bare unescaped grouping braces {...} to (...)
    loop_count = 0
    while "{" in s:
        loop_count += 1
        if loop_count > 50:
            return False, ""
        idx = s.find("{")
        ok, arg, next_pos = _extract_braced_arg(s, idx)
        if not ok:
            return False, ""
        ok_a, norm_arg = _normalize_math_block(arg, depth + 1)
        if not ok_a:
            return False, ""
        s = s[:idx] + f"({norm_arg})" + s[next_pos:]

    # 16. Restore literal braces
    s = s.replace("\x01", "{").replace("\x02", "}")

    return True, s.strip()


def normalize_math_notation(text: str) -> tuple[bool, str]:
    """Normalize a small supported subset of LaTeX to readable plain Unicode math.

    Preserves grouping, identifier case, coefficients/order, numerator/denominator,
    minus and conditions. Returns (False, original_text) on unsupported commands
    or malformed math. Ordinary prose and paths remain unchanged.
    """
    if not text or not isinstance(text, str):
        return True, text

    # Dangerous / unsupported commands check
    for m in re.finditer(r"\\([a-zA-Z]+)", text):
        if m.group(1) in _UNSUPPORTED_DANGEROUS_CMDS:
            return False, text

    # Delimiter scan
    tokens = list(re.finditer(r"\\\(|\\\)|\\\[|\\\]|\$\$", text))
    if tokens:
        delim_stack = []
        spans = []
        for m in tokens:
            tok = m.group(0)
            if tok in (r"\(", r"\["):
                if delim_stack:
                    return False, text
                delim_stack.append((tok, m.start(), m.end()))
            elif tok == "$$":
                if delim_stack and delim_stack[-1][0] == "$$":
                    open_tok, open_start, open_end = delim_stack.pop()
                    spans.append((open_start, m.end(), open_end, m.start()))
                elif not delim_stack:
                    delim_stack.append((tok, m.start(), m.end()))
                else:
                    return False, text
            else:
                if not delim_stack:
                    return False, text
                open_tok, open_start, open_end = delim_stack.pop()
                if (open_tok == r"\(" and tok != r"\)") or (open_tok == r"\[" and tok != r"\]"):
                    return False, text
                spans.append((open_start, m.end(), open_end, m.start()))
        if delim_stack:
            return False, text

        # Replace spans from right to left
        result = text
        for full_start, full_end, inner_start, inner_end in reversed(spans):
            inner_text = text[inner_start:inner_end]
            ok, norm_math = _normalize_math_block(inner_text)
            if not ok:
                return False, text
            result = result[:full_start] + norm_math + result[full_end:]
        return True, result

    # Check for single $ delimiters containing math: $...$
    # Must not be preceded or followed by digits (currency $5, $2)
    dollar_matches = list(re.finditer(r"(?<![\w\$\\])\$(?!\s|\d)(.*?)(?<!\s|\$)\$", text))
    if dollar_matches:
        has_math_dollar = any(
            "\\" in m.group(1) or any(c in m.group(1) for c in "=<>+*/^≠≤≥∈")
            for m in dollar_matches
        )
        if has_math_dollar:
            result = text
            for m in reversed(dollar_matches):
                inner_text = m.group(1)
                ok, norm_math = _normalize_math_block(inner_text)
                if not ok:
                    return False, text
                result = result[:m.start()] + norm_math + result[m.end():]
            return True, result

    # No delimiters present. Check if text contains recognized math
    has_math = (
        r"\frac" in text
        or r"\sqrt" in text
        or "^{" in text
        or "_{" in text
        or bool(re.search(r"\\(?:mathbb|mathbf|mathcal)\b", text))
        or any(re.search(rf"\\{cmd}\b", text) for cmd in ["alpha", "beta", "gamma", "delta", "in", "to", "lim", "ln"])
    )
    if not has_math:
        return True, text

    ok, norm = _normalize_math_block(text)
    if not ok:
        return False, text
    return True, norm


def _is_math_start(text: str) -> bool:
    """Determine whether text begins with a case-sensitive math identifier or differential form."""
    if not text:
        return False
    return bool(_MATH_START.match(text.lstrip()))


def _drop_source_intro(match: re.Match) -> str:
    """Remove a source intro; re-capitalize the sentence it started (not math identifiers)."""
    before = match.string[:match.start()].rstrip()
    rest = match.string[match.end():]
    if (not before or before[-1] in ".!?\n") and rest.lstrip()[:1].islower() and not _is_math_start(rest):
        return "\x00"  # marker: capitalize next char
    return ""


def clean_answer(text: str) -> str:
    text = _THINK.sub("", text)
    final = _FINAL.search(text)
    if final:
        text = final.group(1)
    text = re.sub(r"(?is)^.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", text)
    text = re.sub(r"(?im)^\s*(?:#{1,6}\s*)?(?:câu trả lời(?: cuối cùng)?|final answer)\s*:\s*", "", text)
    text = re.sub(r"(?im)^\s*(?:[-*]\s*)?(?:nêu dữ kiện có nguồn|giải thích suy luận(?: rút ra từ các dữ kiện)?|mức độ chắc chắn|giới hạn(?: hoặc điểm chưa có chứng cứ)?|tóm lại)\s*:\s*", "", text)
    text = _SOURCE_SECTION.sub("", text)
    # Remove parentheses only while their source marker is still identifiable.
    # An actual function call f() or applicability condition must survive.
    text = re.sub(r"\(\s*(?:(?:từ|theo)\s+)?" + _SOURCE.pattern + r"\s*\)", "", text, flags=re.I)
    text = re.sub(r"(?i)\btừ\s+(?=" + _SOURCE.pattern + r")", "", text)
    text = _SOURCE_INTRO.sub(_drop_source_intro, text)
    text = re.sub(r"\x00(.)", lambda m: m.group(1).upper(), text)
    text = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*(?:các\s+)?(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]\s*\.?\s*", "", text)
    text = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*\.?\s*", "", text)
    text = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", text)
    text = _SOURCE.sub("", text)
    text = _BOOK_SOURCE.sub("", text)
    text = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*(?=[.!?;]|$)", "", text)
    text = re.sub(r"\bTầm Thân\b", "cái bóng", text, flags=re.I)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    ok_m, norm_m = normalize_math_notation(text)
    if ok_m:
        text = norm_m
    if text and _is_math_start(text):
        return text
    return text[:1].upper() + text[1:] if text else ""


def _clean_claim_text(text: str) -> str:
    claim = _SOURCE_INTRO.sub("", text)
    claim = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*(?:các\s+)?(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]\s*\.?\s*", "", claim)
    claim = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", claim)
    claim = _SOURCE.sub("", claim)
    claim = _BOOK_SOURCE.sub("", claim)
    claim = re.sub(r"(?i)(?:các\s+)?(?:thông tin|nội dung|dữ liệu)\s+(?:này\s+)?(?:được\s+)?(?:trích dẫn|lấy|thu thập)\s+từ\s*\.?\s*", "", claim)
    claim = re.sub(r"(?i)(?:,\s*)?(?:như\s+)?(?:được\s+)?(?:mô tả|trình bày|nêu|ghi|chỉ ra|đề cập)\s+(?:trong|ở|tại)\s*(?:đoạn|nguồn|tài liệu)?\s*(?=[.!?;]|$)", "", claim)
    claim = re.sub(r"^\s*(?:[-*•]|\d+[.)]|[a-zA-Z][.)])\s+", "", claim)
    claim = re.sub(r"(?i)^\s*(?:theo|trong|ở|tại|căn cứ vào|dựa vào)\s+(?:các\s+)?(?:đoạn|nguồn|tài liệu)\s*(?:,\s*|$)", "", claim)
    claim = re.sub(r"(?i)^\s*(?:theo|trong|ở|tại|căn cứ vào|dựa vào)\s*$", "", claim)
    claim = re.sub(r"\s+([,.;:!?])", r"\1", claim)
    claim = re.sub(r"[ \t]{2,}", " ", claim)
    claim = claim.strip(" \t\r\n*•;,")
    ok_m, norm_m = normalize_math_notation(claim)
    if ok_m:
        claim = norm_m
    return claim


def _is_substantive(text: str) -> bool:
    cleaned = _clean_claim_text(text)
    if not cleaned:
        return False
    if re.fullmatch(r"^(?:[-*•]|\d+[.)]|[a-zA-Z][.)])$", cleaned):
        return False
    if re.fullmatch(r"^(?:theo|trong|ở|tại|căn cứ vào|dựa vào|nguồn|đoạn|tài liệu|chỉ dẫn trong nguồn)$", cleaned, re.I):
        return False
    words = re.findall(r"\w+", cleaned, re.UNICODE)
    meaningful = [w for w in words if w.casefold() not in {"trong", "đoạn", "theo", "nguồn", "ở", "tại", "tài", "liệu", "là", "và", "của"}]
    has_math = any(c in "=<>+*/^√∑∫π≠≤≥()" for c in cleaned)
    return bool(meaningful or has_math)


def extract_citation_claims(raw_text: str, valid_source_ids: set[str] | list[str] | None = None) -> dict[str, list[str]]:
    """Extract model-attributed statement claims near each visible [S#] marker.

    Claims are derived ONLY from the visible final answer (ignoring private
    reasoning/think/analysis tags and source/reference sections).
    Grouped citations like [S1, S2] map the claim to all specified valid IDs.
    Returns a dict mapping source_id (e.g. 'S1') to a list of clean claim strings.
    """
    if not raw_text or not isinstance(raw_text, str):
        return {}

    # 1. Isolate visible text: remove private reasoning and trailing references
    text = _THINK.sub("", raw_text)
    final = _FINAL.search(text)
    if final:
        text = final.group(1)
    text = re.sub(r"(?is)^.*?<\s*/\s*(?:think|analysis|reasoning)\s*>", "", text)
    text = _SOURCE_SECTION.sub("", text)
    text = re.sub(r"(?im)^\s*(?:#{1,6}\s*)?(?:câu trả lời(?: cuối cùng)?|final answer)\s*:\s*", "", text)

    val_set = set(valid_source_ids) if valid_source_ids is not None else None
    claims_by_id: dict[str, list[str]] = {}

    # 2. Split into paragraphs, sentences, and sub-clauses
    # A marker on its own line refers backwards, never to the next answer.
    # Keep this bounded to the immediately preceding paragraph/line so a
    # detached marker cannot assign unrelated earlier claims to a source.
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _SOURCE.sub("", line).strip() and _SOURCE.search(line):
            previous = index - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            if previous >= 0:
                lines[previous] = f"{lines[previous].rstrip()} {line.strip()}"
            lines[index] = ""
    paragraphs = re.split(r"\n+", "\n".join(lines))
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        sentences = re.split(r"(?<=\.)\s+(?!\[\s*s\d)|[!?]+\s+(?!\[\s*s\d)", p, flags=re.I)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            sub_clauses = re.split(
                r"(?<=\])\s*(?:;\s*|,\s*(?=[^\[\]]*\[\s*s\d+)|(?:,\s*)?(?:và|hoặc|nhưng|còn|đồng thời|trong khi)\s+)",
                sent,
                flags=re.I,
            )
            for clause in sub_clauses:
                clause_str = clause.strip()
                if not clause_str:
                    continue
                matches = list(_SOURCE.finditer(clause_str))
                if not matches:
                    continue
                for idx, m in enumerate(matches):
                    matched_ids = []
                    for num in re.findall(r"\d+", m.group(0)):
                        s_id = f"S{int(num)}"
                        if val_set is None or s_id in val_set:
                            if s_id not in matched_ids:
                                matched_ids.append(s_id)
                    if not matched_ids:
                        continue

                    prev_end = matches[idx - 1].end() if idx > 0 else 0
                    next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(clause_str)
                    before_text = clause_str[prev_end : m.start()]
                    after_text = clause_str[m.end() : next_start]

                    if (
                        len(matches) == 1
                        and _is_substantive(after_text)
                        and not after_text.rstrip(" \t\r\n.").endswith(":")
                    ):
                        chosen = clause_str
                    elif _is_substantive(before_text):
                        chosen = before_text
                    elif _is_substantive(after_text):
                        chosen = after_text
                    else:
                        chosen = clause_str

                    claim = _clean_claim_text(chosen)
                    if not claim or not _is_substantive(claim):
                        continue
                    for s_id in matched_ids:
                        claims_by_id.setdefault(s_id, [])
                        if claim not in claims_by_id[s_id]:
                            claims_by_id[s_id].append(claim)

    return claims_by_id
