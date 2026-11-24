"""Shared psychology terminology checks for runtime and training translations."""

import re
import unicodedata


def _fold_bilingual_phrase(text: str) -> str:
    """Fold case, diacritics (including đ->d), hyphens, and whitespace for bilingual matching."""
    folded = unicodedata.normalize("NFC", str(text or "")).casefold().replace("đ", "d")
    stripped = "".join(c for c in unicodedata.normalize("NFKD", folded) if not unicodedata.combining(c))
    stripped = re.sub(r"[-_/]+", " ", stripped)
    stripped = re.sub(r"\bvec\s*to\b", "vector", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


ACADEMIC_BILINGUAL_EQUIVALENTS: tuple[tuple[str, str], ...] = (
    # Math / Calculus / Linear Algebra / Probability / Logic
    ("derivative", "đạo hàm"),
    ("integral", "tích phân"),
    ("limit", "giới hạn"),
    ("calculus", "giải tích"),
    ("linear algebra", "đại số tuyến tính"),
    ("linear transformation", "ánh xạ tuyến tính"),
    ("vector space", "không gian vectơ"),
    ("matrix", "ma trận"),
    ("determinant", "định thức"),
    ("eigenvalue", "trị riêng"),
    ("eigenvector", "vectơ riêng"),
    ("probability", "xác suất"),
    ("conditional probability", "xác suất có điều kiện"),
    ("bayes theorem", "định lý bayes"),
    ("random variable", "biến ngẫu nhiên"),
    ("probability distribution", "phân phối xác suất"),
    ("proposition", "mệnh đề"),
    ("induction", "quy nạp"),
    ("continuity", "liên tục"),
    ("function", "hàm số"),
    ("equation", "phương trình"),
    ("inequality", "bất đẳng thức"),
    ("normal distribution", "phân phối chuẩn"),
    ("variance", "phương sai"),
    ("standard deviation", "độ lệch chuẩn"),
    ("expected value", "kỳ vọng"),
    ("graph theory", "lý thuyết đồ thị"),
    ("predicate logic", "logic vị từ"),
    ("set theory", "lý thuyết tập hợp"),
    # Physics / Sciences
    ("mechanics", "cơ học"),
    ("thermodynamics", "nhiệt động lực học"),
    ("entropy", "entropy"),
    ("optics", "quang học"),
    ("genetics", "di truyền học"),
    ("measurement", "đo lường"),
    ("force", "lực"),
    ("velocity", "vận tốc"),
    ("acceleration", "gia tốc"),
    ("momentum", "động lượng"),
    ("kinetic energy", "động năng"),
    ("potential energy", "thế năng"),
    ("chemical bond", "liên kết hóa học"),
    ("molecule", "phân tử"),
    ("cell", "tế bào"),
    ("natural selection", "chọn lọc tự nhiên"),
    ("astronomy", "thiên văn học"),
    # CS / Programming / OOP / DSA / Concurrency / DB / Networks
    ("object-oriented programming", "lập trình hướng đối tượng"),
    ("oop", "lập trình hướng đối tượng"),
    ("encapsulation", "đóng gói"),
    ("polymorphism", "đa hình"),
    ("inheritance", "kế thừa"),
    ("abstraction", "trừu tượng"),
    ("data structure", "cấu trúc dữ liệu"),
    ("algorithm", "giải thuật"),
    ("linked list", "danh sách liên kết"),
    ("array", "mảng"),
    ("stack", "ngăn xếp"),
    ("queue", "hàng đợi"),
    ("binary tree", "cây nhị phân"),
    ("hash table", "bảng băm"),
    ("pointer", "con trỏ"),
    ("recursion", "đệ quy"),
    ("design pattern", "mẫu thiết kế"),
    ("concurrency", "lập trình đồng thời"),
    ("thread", "luồng"),
    ("deadlock", "bế tắc"),
    ("relational database", "cơ sở dữ liệu quan hệ"),
    ("computer network", "mạng máy tính"),
    ("information theory", "lý thuyết thông tin"),
    ("time complexity", "độ phức tạp thời gian"),
    ("space complexity", "độ phức tạp không gian"),
    ("sorting", "sắp xếp"),
    ("binary search", "tìm kiếm nhị phân"),
    ("class", "lớp"),
    ("object", "đối tượng"),
    ("constructor", "hàm khởi tạo"),
    ("destructor", "hàm hủy"),
    ("mutex", "khóa mutex"),
    ("race condition", "tranh chấp dữ liệu"),
    ("synchronization", "đồng bộ hóa"),
    ("primary key", "khóa chính"),
    ("foreign key", "khóa ngoại"),
    ("normalization", "chuẩn hóa"),
    # Psychology / Economics
    ("working memory", "trí nhớ làm việc"),
    ("short-term memory", "trí nhớ ngắn hạn"),
    ("long-term memory", "trí nhớ dài hạn"),
    ("classical conditioning", "điều kiện hóa cổ điển"),
    ("operant conditioning", "điều kiện hóa thao tác"),
    ("reinforcement", "củng cố"),
    ("cognitive bias", "thiên kiến nhận thức"),
    ("habit", "thói quen"),
    ("personality", "nhân cách"),
    ("unconscious", "vô thức"),
    ("attachment", "gắn bó"),
    ("emotion", "cảm xúc"),
    ("game theory", "lý thuyết trò chơi"),
    ("economics", "kinh tế học"),
    ("memory consolidation", "củng cố trí nhớ"),
    ("attention", "sự chú ý"),
    ("perception", "tri giác"),
    ("decision making", "ra quyết định"),
    ("nash equilibrium", "cân bằng nash"),
    ("opportunity cost", "chi phí cơ hội"),
    ("supply and demand", "cung và cầu"),
    ("inflation", "lạm phát"),
    ("communication", "giao tiếp"),
)


def _build_bilingual_indexes() -> tuple[dict[str, list[str]], tuple[tuple[str, str, str, str], ...]]:
    lookup: dict[str, list[str]] = {}
    entries: list[tuple[str, str, str, str]] = []
    for en, vi in ACADEMIC_BILINGUAL_EQUIVALENTS:
        en_fold = _fold_bilingual_phrase(en)
        vi_fold = _fold_bilingual_phrase(vi)
        if en_fold == vi_fold:
            continue
        for key, target in ((en_fold, vi), (vi_fold, en)):
            bucket = lookup.setdefault(key, [])
            if target not in bucket:
                bucket.append(target)
        entries.append((en, vi, en_fold, vi_fold))
    entries.sort(key=lambda item: max(len(item[2]), len(item[3])), reverse=True)
    return lookup, tuple(entries)


_BILINGUAL_LOOKUP, _SORTED_BILINGUAL_ENTRIES = _build_bilingual_indexes()


def bilingual_equivalents_for(phrase: str) -> list[str]:
    """Return cross-lingual academic equivalents for an English or Vietnamese term/phrase."""
    folded = _fold_bilingual_phrase(phrase)
    if not folded:
        return []
    hits = list(_BILINGUAL_LOOKUP.get(folded, ()))
    if not hits and len(folded) > 4 and folded.endswith("s") and not folded.endswith("ss"):
        hits = list(_BILINGUAL_LOOKUP.get(folded[:-1], ()))
    if not hits:
        for alt in (re.sub(r"\bvecto\b", "vector", folded), re.sub(r"\bvector\b", "vecto", folded)):
            if alt != folded and alt in _BILINGUAL_LOOKUP:
                hits.extend(t for t in _BILINGUAL_LOOKUP[alt] if t not in hits)
    return hits


def _phrase_in_folded_text(folded_phrase: str, folded_text: str, *, allow_plural_s: bool = False) -> bool:
    if not folded_phrase or not folded_text:
        return False
    suffix = r"s?" if allow_plural_s else r""
    return re.search(rf"(?<!\w){re.escape(folded_phrase)}{suffix}(?!\w)", folded_text) is not None


def expand_bilingual_query_terms(text: str, max_terms: int = 6) -> list[str]:
    """Extract cross-lingual equivalents for all academic terms detected in text."""
    folded_text = _fold_bilingual_phrase(text)
    if not folded_text or max_terms <= 0:
        return []
    raw_cf = unicodedata.normalize("NFC", str(text or "")).casefold()
    results: list[str] = []
    seen_folded: set[str] = set()
    for en, vi, en_fold, vi_fold in _SORTED_BILINGUAL_ENTRIES:
        if _phrase_in_folded_text(en_fold, folded_text, allow_plural_s=True):
            if vi_fold not in seen_folded and not _phrase_in_folded_text(vi_fold, folded_text):
                seen_folded.add(vi_fold)
                results.append(vi)
                if len(results) >= max_terms:
                    break
        if _phrase_in_folded_text(vi_fold, folded_text) or (
            re.search(rf"(?<!\w){re.escape(vi)}(?!\w)", raw_cf) is not None
        ):
            if en_fold not in seen_folded and not _phrase_in_folded_text(en_fold, folded_text, allow_plural_s=True):
                seen_folded.add(en_fold)
                results.append(en)
                if len(results) >= max_terms:
                    break
    return results


def translation_guidance(source: str = "") -> str:
    """Select relevant terms; shadowing speech is not Jung's shadow."""
    terms = {
        "shadow": "cái bóng (khía cạnh bị che giấu của nhân cách)",
        "projection": "sự phóng chiếu", "unconscious": "vô thức",
        "working memory": "trí nhớ làm việc", "phonological loop": "vòng lặp âm vị",
        "visuospatial sketchpad": "bảng phác họa thị giác-không gian",
        "central executive": "bộ điều hành trung tâm", "hippocampus": "hồi hải mã",
        "declarative memory": "trí nhớ khai báo", "semantic memory": "trí nhớ ngữ nghĩa",
        "gene": "gen", "allele": "alen", "dominant": "trội", "recessive": "lặn",
        "sexual reproduction": "sinh sản hữu tính",
    }
    value = source.casefold()
    selected = [f"{term} = {meaning}" for term, meaning in terms.items()
                if not source or re.search(r"\b" + re.escape(term) + r"\b", value)]
    if "shadowed" in value or "shadowing" in value:
        selected.append("shadowing speech = nghe và lặp lại lời nói; không phải cái bóng theo Jung")
    if "project information" in value:
        selected.append("project information = truyền/chuyển thông tin đến vùng khác")
    return ("Relevant terminology: " + "; ".join(selected) + ". " if selected else "") + "Preserve proper names and the meaning in context. "


def translation_is_plausible(source: str, translation: str) -> bool:
    value = translation.casefold()
    if len(translation.strip()) < max(30, 0.35 * len(source.strip())):
        return False
    # Runtime cache must enforce the same language boundary as training data.
    if re.search(r"[\u3400-\u9fff]", translation):
        return False
    if len(re.findall(r"[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]", translation)) < max(3, len(translation) // 150):
        return False
    if any(phrase in value for phrase in ("tầm thân", "dự án bóng", "dự án của mình", "dự án mạnh mẽ",
                                           "sktech", "patronize", "hypnotic", "working memory")):
        return False
    if "projection" in source.casefold() and "dự án" in value:
        return False
    if "dominant" in source.casefold() and "bắc cầu" in value:
        return False
    if "sexual reproduction" in source.casefold() and "trải nghiệm sinh" in value:
        return False
    if any(marker in value for marker in ("trả lời:", "câu hỏi:", "[s1]", "[s2]", "<think>")):
        return False
    return True
