"""Hierarchical Parent–Child Topic Tree shared by QA and Learning/Practice (YC-03).

Organizes the authored learning concepts into a 2-level hierarchy:
- Level 1 (Parent Domain): Broad academic domain (Mathematics & Logic, Natural Sciences,
  Computer Science & Software Engineering, Psychology & Behavioral Science,
  Economics, Decision Theory & Communication).
- Level 2 (Child Topic): Specific authored learning topics and their verified aliases.

Sources establish availability; model output cannot invent or extend topics at runtime.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


def normalized(text: Any) -> str:
    text = unicodedata.normalize("NFC", str(text or "")).casefold()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.replace("đ", "d").replace("_", " ")).strip()


TOPIC_TREE: Tuple[Dict[str, Any], ...] = (
    {
        "parent_id": "math_logic",
        "parent_label": "Toán học và Logic",
        "parent_aliases": ("toán học", "mathematics", "toán đại cương"),
        "children": (
            (
                "Đại số tuyến tính",
                (
                    "đại số tuyến tính",
                    "linear algebra",
                    "ma trận",
                    "định thức",
                    "không gian vectơ",
                    "không gian vector",
                    "ánh xạ tuyến tính",
                ),
            ),
            (
                "Giải tích và vi tích phân",
                (
                    "vi tích phân",
                    "calculus",
                    "đạo hàm",
                    "tích phân",
                    "giới hạn hàm",
                    "liên tục của hàm",
                ),
            ),
            (
                "Logic và toán rời rạc",
                (
                    "toán rời rạc",
                    "discrete mathematics",
                    "logic mệnh đề",
                    "mệnh đề",
                    "đại số boole",
                    "phép đếm",
                    "hệ thức đệ quy",
                    "quan hệ tương đương",
                ),
            ),
            (
                "Xác suất và thống kê",
                (
                    "xác suất",
                    "probability",
                    "bayes",
                    "thống kê",
                    "statistical inference",
                    "trung bình mẫu",
                    "sample mean",
                ),
            ),
        ),
    },
    {
        "parent_id": "natural_sciences",
        "parent_label": "Khoa học tự nhiên và Đo lường",
        "parent_aliases": ("khoa học tự nhiên", "natural sciences", "vật lý đại cương"),
        "children": (
            (
                "Cơ học",
                (
                    "cơ học",
                    "động học",
                    "động lực học",
                    "định luật newton",
                    "cơ học newton",
                    "động lượng",
                    "angular momentum",
                    "kinematics",
                ),
            ),
            (
                "Nhiệt học và nhiệt động lực học",
                (
                    "nhiệt động",
                    "khí lý tưởng",
                    "thermodynamics",
                    "nhiệt lượng",
                    "nhiệt dung",
                    "entropy nhiệt động",
                    "thermal entropy",
                ),
            ),
            (
                "Thiên văn học và quang học",
                (
                    "thiên văn",
                    "thiên văn học",
                    "quang học",
                    "năm ánh sáng",
                    "astronomy",
                    "optics",
                    "light-year",
                ),
            ),
            (
                "Hóa học",
                (
                    "hóa học",
                    "nồng độ mol",
                    "chất tan",
                    "dung dịch",
                    "dung môi",
                    "chemistry",
                    "molarity",
                ),
            ),
            (
                "Sinh học và di truyền học",
                (
                    "sinh học",
                    "di truyền",
                    "di truyền học",
                    "adn",
                    "dna",
                    "nucleotide",
                    "biology",
                    "genetics",
                ),
            ),
            (
                "Đo lường và hệ đơn vị",
                (
                    "đo lường",
                    "hệ đơn vị",
                    "đơn vị đo",
                    "đổi đơn vị",
                    "đổi diện tích",
                    "đổi thể tích",
                    "đổi độ dài",
                    "measurement",
                    "units of measurement",
                    "unit conversion",
                ),
            ),
        ),
    },
    {
        "parent_id": "computer_science",
        "parent_label": "Khoa học máy tính và Kỹ thuật phần mềm",
        "parent_aliases": ("khoa học máy tính", "computer science", "công nghệ thông tin", "kỹ thuật phần mềm"),
        "children": (
            (
                "Lập trình cơ bản",
                (
                    "lập trình cơ bản",
                    "introduction to programming",
                    "vòng lặp",
                    "con trỏ",
                    "c++",
                    "cấu trúc điều khiển",
                ),
            ),
            (
                "Lập trình hướng đối tượng",
                (
                    "lập trình hướng đối tượng",
                    "object oriented",
                    "object-oriented",
                    "oop",
                    "tính đa hình",
                    "tính đóng gói",
                ),
            ),
            (
                "Cấu trúc dữ liệu và giải thuật",
                (
                    "cấu trúc dữ liệu",
                    "data structures",
                    "giải thuật",
                    "thuật toán",
                    "linked list",
                    "binary search tree",
                    "hash table",
                    "tìm kiếm nhị phân",
                    "binary search",
                ),
            ),
            (
                "Mẫu thiết kế phần mềm",
                (
                    "mẫu thiết kế",
                    "design pattern",
                    "abstract factory",
                    "singleton pattern",
                ),
            ),
            (
                "Lập trình đồng thời",
                (
                    "lập trình đồng thời",
                    "concurrency",
                    "multithreading",
                    "race condition",
                    "mutual exclusion",
                ),
            ),
            (
                "Cơ sở dữ liệu quan hệ",
                (
                    "cơ sở dữ liệu",
                    "cơ sở dữ liệu quan hệ",
                    "khóa chính",
                    "khóa ngoại",
                    "bảng quan hệ",
                    "relational database",
                    "primary key",
                    "foreign key",
                ),
            ),
            (
                "Mạng máy tính và truyền dữ liệu",
                (
                    "mạng máy tính",
                    "truyền dữ liệu",
                    "tốc độ bit",
                    "đường truyền",
                    "computer network",
                    "data transmission",
                ),
            ),
            (
                "Lý thuyết thông tin",
                (
                    "lý thuyết thông tin",
                    "entropy shannon",
                    "shannon entropy",
                    "entropy rời rạc",
                    "information theory",
                ),
            ),
        ),
    },
    {
        "parent_id": "economics_decision_comm",
        "parent_label": "Kinh tế, Ra quyết định và Giao tiếp",
        "parent_aliases": ("kinh tế và kỹ năng", "ra quyết định", "decision making"),
        "children": (
            (
                "Kinh tế học",
                (
                    "kinh tế",
                    "kinh tế học",
                    "chi phí cơ hội",
                    "economics",
                    "opportunity cost",
                ),
            ),
            (
                "Lý thuyết trò chơi và ra quyết định",
                (
                    "lý thuyết trò chơi",
                    "game theory",
                    "cân bằng nash",
                    "nash equilibrium",
                    "thiên kiến nhận thức",
                    "cognitive bias",
                ),
            ),
            (
                "Tiếng Anh và kỹ năng giao tiếp",
                (
                    "ielts",
                    "cue card",
                    "english vocabulary",
                    "kỹ năng giao tiếp",
                    "public speaking",
                ),
            ),
        ),
    },
    {
        "parent_id": "psychology_behavioral",
        "parent_label": "Tâm lý học và Khoa học hành vi",
        "parent_aliases": ("tâm lý học", "psychology", "khoa học hành vi"),
        "children": (
            (
                "Trí nhớ và nhận thức",
                (
                    "trí nhớ",
                    "working memory",
                    "long-term memory",
                    "cognitive psychology",
                    "tâm lý học nhận thức",
                ),
            ),
            (
                "Học tập và thói quen",
                (
                    "thói quen",
                    "habit loop",
                    "điều kiện hóa",
                    "classical conditioning",
                    "operant conditioning",
                    "growth mindset",
                ),
            ),
            (
                "Nhân cách và tâm lý học phân tích",
                (
                    "nhân cách",
                    "personality",
                    "carl jung",
                    "c. g. jung",
                    "archetype",
                    "cái bóng",
                ),
            ),
            (
                "Tâm lý học xã hội và gắn bó",
                (
                    "tâm lý học xã hội",
                    "social psychology",
                    "gắn bó",
                    "attachment theory",
                    "dissonance",
                    "ảnh hưởng xã hội",
                ),
            ),
            (
                "Cảm xúc và sức khỏe tâm lý",
                (
                    "cảm xúc",
                    "emotion regulation",
                    "sang chấn",
                    "trauma",
                    "rối loạn tâm lý",
                    "mental health",
                ),
            ),
        ),
    },
)

# Preserve exact flat TOPICS ordering expected by existing callers and tests
TOPICS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Đại số tuyến tính", ("đại số tuyến tính", "linear algebra", "ma trận", "định thức", "không gian vectơ", "không gian vector", "ánh xạ tuyến tính")),
    ("Giải tích và vi tích phân", ("vi tích phân", "calculus", "đạo hàm", "tích phân", "giới hạn hàm", "liên tục của hàm")),
    ("Logic và toán rời rạc", ("toán rời rạc", "discrete mathematics", "logic mệnh đề", "mệnh đề", "đại số boole", "phép đếm", "hệ thức đệ quy", "quan hệ tương đương")),
    ("Xác suất và thống kê", ("xác suất", "probability", "bayes", "thống kê", "statistical inference", "trung bình mẫu", "sample mean")),
    ("Cơ học", ("cơ học", "động học", "động lực học", "định luật newton", "cơ học newton", "động lượng", "angular momentum", "kinematics")),
    ("Nhiệt học và nhiệt động lực học", ("nhiệt động", "khí lý tưởng", "thermodynamics", "nhiệt lượng", "nhiệt dung", "entropy nhiệt động", "thermal entropy")),
    ("Thiên văn học và quang học", ("thiên văn", "thiên văn học", "quang học", "năm ánh sáng", "astronomy", "optics", "light-year")),
    ("Hóa học", ("hóa học", "nồng độ mol", "chất tan", "dung dịch", "dung môi", "chemistry", "molarity")),
    ("Sinh học và di truyền học", ("sinh học", "di truyền", "di truyền học", "adn", "dna", "nucleotide", "biology", "genetics")),
    ("Đo lường và hệ đơn vị", ("đo lường", "hệ đơn vị", "đơn vị đo", "đổi đơn vị", "đổi diện tích", "đổi thể tích", "đổi độ dài", "measurement", "units of measurement", "unit conversion")),
    ("Lập trình cơ bản", ("lập trình cơ bản", "introduction to programming", "vòng lặp", "con trỏ", "c++", "cấu trúc điều khiển")),
    ("Lập trình hướng đối tượng", ("lập trình hướng đối tượng", "object oriented", "object-oriented", "oop", "tính đa hình", "tính đóng gói")),
    ("Cấu trúc dữ liệu và giải thuật", ("cấu trúc dữ liệu", "data structures", "giải thuật", "thuật toán", "linked list", "binary search tree", "hash table", "tìm kiếm nhị phân", "binary search")),
    ("Mẫu thiết kế phần mềm", ("mẫu thiết kế", "design pattern", "abstract factory", "singleton pattern")),
    ("Lập trình đồng thời", ("lập trình đồng thời", "concurrency", "multithreading", "race condition", "mutual exclusion")),
    ("Cơ sở dữ liệu quan hệ", ("cơ sở dữ liệu", "cơ sở dữ liệu quan hệ", "khóa chính", "khóa ngoại", "bảng quan hệ", "relational database", "primary key", "foreign key")),
    ("Mạng máy tính và truyền dữ liệu", ("mạng máy tính", "truyền dữ liệu", "tốc độ bit", "đường truyền", "computer network", "data transmission")),
    ("Lý thuyết thông tin", ("lý thuyết thông tin", "entropy shannon", "shannon entropy", "entropy rời rạc", "information theory")),
    ("Kinh tế học", ("kinh tế", "kinh tế học", "chi phí cơ hội", "economics", "opportunity cost")),
    ("Trí nhớ và nhận thức", ("trí nhớ", "working memory", "long-term memory", "cognitive psychology", "tâm lý học nhận thức")),
    ("Học tập và thói quen", ("thói quen", "habit loop", "điều kiện hóa", "classical conditioning", "operant conditioning", "growth mindset")),
    ("Nhân cách và tâm lý học phân tích", ("nhân cách", "personality", "carl jung", "c. g. jung", "archetype", "cái bóng")),
    ("Tâm lý học xã hội và gắn bó", ("tâm lý học xã hội", "social psychology", "gắn bó", "attachment theory", "dissonance", "ảnh hưởng xã hội")),
    ("Cảm xúc và sức khỏe tâm lý", ("cảm xúc", "emotion regulation", "sang chấn", "trauma", "rối loạn tâm lý", "mental health")),
    ("Lý thuyết trò chơi và ra quyết định", ("lý thuyết trò chơi", "game theory", "cân bằng nash", "nash equilibrium", "thiên kiến nhận thức", "cognitive bias")),
    ("Tiếng Anh và kỹ năng giao tiếp", ("ielts", "cue card", "english vocabulary", "kỹ năng giao tiếp", "public speaking")),
)

CHILD_TO_PARENT: Dict[str, Dict[str, Any]] = {}
for _node in TOPIC_TREE:
    for _child_label, _child_aliases in _node["children"]:
        CHILD_TO_PARENT[_child_label] = {
            "parent_id": _node["parent_id"],
            "parent_label": _node["parent_label"],
            "child_label": _child_label,
            "aliases": _child_aliases,
        }


def resolve_topic(value: Any) -> Optional[Tuple[str, Tuple[str, ...]]]:
    target = normalized(value)
    if not target:
        return None
    for label, aliases in TOPICS:
        if target in {normalized(label), *(normalized(a) for a in aliases)}:
            return label, aliases
    return None


def resolve_topic_hierarchy(value: Any) -> Optional[Dict[str, Any]]:
    """Resolve either a child topic or a parent domain into its hierarchy descriptor."""
    target = normalized(value)
    if not target:
        return None
    child_match = resolve_topic(value)
    if child_match:
        label, aliases = child_match
        parent_info = CHILD_TO_PARENT.get(label, {})
        return {
            "is_parent": False,
            "parent_id": parent_info.get("parent_id", "general"),
            "parent_label": parent_info.get("parent_label", label),
            "topic": label,
            "child_topics": (label,),
            "aliases": aliases,
        }
    for node in TOPIC_TREE:
        parent_names = {
            normalized(node["parent_id"]),
            normalized(node["parent_label"]),
            *(normalized(a) for a in node.get("parent_aliases", ())),
        }
        if target in parent_names:
            combined_aliases: List[str] = []
            child_labels: List[str] = []
            for c_label, c_aliases in node["children"]:
                child_labels.append(c_label)
                combined_aliases.extend(c_aliases)
            return {
                "is_parent": True,
                "parent_id": node["parent_id"],
                "parent_label": node["parent_label"],
                "topic": node["parent_label"],
                "child_topics": tuple(child_labels),
                "aliases": tuple(dict.fromkeys(combined_aliases)),
            }
    return None


def matches(text: Any, aliases: Tuple[str, ...]) -> bool:
    value = normalized(text)
    return any(
        re.search(r"(?<!\w)" + re.escape(normalized(alias)) + r"(?!\w)", value)
        for alias in aliases
    )


PATTERNS: Dict[str, re.Pattern[str]] = {
    label: re.compile("|".join(r"(?<!\w)" + re.escape(normalized(a)) + r"(?!\w)" for a in aliases))
    for label, aliases in TOPICS
}


def infer_query_topic_hierarchy(query: Any) -> Dict[str, Any]:
    """Identify matching child topics and parent domains from a query string."""
    norm_q = normalized(query)
    if not norm_q:
        return {"parent_ids": [], "parent_labels": [], "child_topics": []}
    matched_children: List[str] = []
    matched_parent_ids: List[str] = []
    matched_parent_labels: List[str] = []
    for label, pattern in PATTERNS.items():
        if pattern.search(norm_q):
            matched_children.append(label)
            p_info = CHILD_TO_PARENT.get(label)
            if p_info:
                if p_info["parent_id"] not in matched_parent_ids:
                    matched_parent_ids.append(p_info["parent_id"])
                if p_info["parent_label"] not in matched_parent_labels:
                    matched_parent_labels.append(p_info["parent_label"])
    return {
        "parent_ids": matched_parent_ids,
        "parent_labels": matched_parent_labels,
        "child_topics": matched_children,
    }


def get_topic_tree_catalog(available_counts: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Return the hierarchical parent-child topic tree with optional document counts."""
    counts = available_counts or {}
    tree_out: List[Dict[str, Any]] = []
    for node in TOPIC_TREE:
        children_out = []
        parent_total = 0
        for child_label, child_aliases in node["children"]:
            c_count = int(counts.get(child_label, 0))
            parent_total += c_count
            children_out.append(
                {
                    "topic": child_label,
                    "aliases": list(child_aliases),
                    "document_count": c_count,
                }
            )
        tree_out.append(
            {
                "parent_id": node["parent_id"],
                "parent_label": node["parent_label"],
                "document_count": parent_total,
                "children": children_out,
            }
        )
    return tree_out
