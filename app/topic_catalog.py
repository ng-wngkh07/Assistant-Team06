"""Authored learning concepts. Sources establish availability, never invent labels.

Keep aliases specific enough to identify a concept in a passage. Changes to this
catalog require source/content review; model output cannot extend it at runtime.
"""
import re
import unicodedata


def normalized(text) -> str:
    text = unicodedata.normalize('NFC', str(text or '')).casefold()
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text.replace('đ', 'd').replace('_', ' ')).strip()


TOPICS = (
    ('Đại số tuyến tính', ('đại số tuyến tính', 'linear algebra', 'ma trận', 'định thức', 'không gian vectơ', 'không gian vector', 'ánh xạ tuyến tính')),
    ('Giải tích và vi tích phân', ('vi tích phân', 'calculus', 'đạo hàm', 'tích phân', 'giới hạn hàm', 'liên tục của hàm')),
    ('Logic và toán rời rạc', ('toán rời rạc', 'discrete mathematics', 'logic mệnh đề', 'mệnh đề', 'đại số boole', 'phép đếm', 'hệ thức đệ quy', 'quan hệ tương đương')),
    ('Xác suất và thống kê', ('xác suất', 'probability', 'bayes', 'thống kê', 'statistical inference', 'trung bình mẫu', 'sample mean')),
    ('Cơ học', ('cơ học', 'động học', 'động lực học', 'định luật newton', 'cơ học newton', 'động lượng', 'angular momentum', 'kinematics')),
    ('Nhiệt học và nhiệt động lực học', ('nhiệt động', 'khí lý tưởng', 'thermodynamics', 'nhiệt lượng', 'nhiệt dung', 'entropy nhiệt động', 'thermal entropy')),
    ('Thiên văn học và quang học', ('thiên văn', 'thiên văn học', 'quang học', 'năm ánh sáng', 'astronomy', 'optics', 'light-year')),
    ('Hóa học', ('hóa học', 'nồng độ mol', 'chất tan', 'dung dịch', 'dung môi', 'chemistry', 'molarity')),
    ('Sinh học và di truyền học', ('sinh học', 'di truyền', 'di truyền học', 'adn', 'dna', 'nucleotide', 'biology', 'genetics')),
    ('Đo lường và hệ đơn vị', ('đo lường', 'hệ đơn vị', 'đơn vị đo', 'đổi đơn vị', 'đổi diện tích', 'đổi thể tích', 'đổi độ dài', 'measurement', 'units of measurement', 'unit conversion')),
    ('Lập trình cơ bản', ('lập trình cơ bản', 'introduction to programming', 'vòng lặp', 'con trỏ', 'c++', 'cấu trúc điều khiển')),
    ('Lập trình hướng đối tượng', ('lập trình hướng đối tượng', 'object oriented', 'object-oriented', 'oop', 'tính đa hình', 'tính đóng gói')),
    ('Cấu trúc dữ liệu và giải thuật', ('cấu trúc dữ liệu', 'data structures', 'giải thuật', 'thuật toán', 'linked list', 'binary search tree', 'hash table', 'tìm kiếm nhị phân', 'binary search')),
    ('Mẫu thiết kế phần mềm', ('mẫu thiết kế', 'design pattern', 'abstract factory', 'singleton pattern')),
    ('Lập trình đồng thời', ('lập trình đồng thời', 'concurrency', 'multithreading', 'race condition', 'mutual exclusion')),
    ('Cơ sở dữ liệu quan hệ', ('cơ sở dữ liệu', 'cơ sở dữ liệu quan hệ', 'khóa chính', 'khóa ngoại', 'bảng quan hệ', 'relational database', 'primary key', 'foreign key')),
    ('Mạng máy tính và truyền dữ liệu', ('mạng máy tính', 'truyền dữ liệu', 'tốc độ bit', 'đường truyền', 'computer network', 'data transmission')),
    ('Lý thuyết thông tin', ('lý thuyết thông tin', 'entropy shannon', 'shannon entropy', 'entropy rời rạc', 'information theory')),
    ('Kinh tế học', ('kinh tế', 'kinh tế học', 'chi phí cơ hội', 'economics', 'opportunity cost')),
    ('Trí nhớ và nhận thức', ('trí nhớ', 'working memory', 'long-term memory', 'cognitive psychology', 'tâm lý học nhận thức')),
    ('Học tập và thói quen', ('thói quen', 'habit loop', 'điều kiện hóa', 'classical conditioning', 'operant conditioning', 'growth mindset')),
    ('Nhân cách và tâm lý học phân tích', ('nhân cách', 'personality', 'carl jung', 'c. g. jung', 'archetype', 'cái bóng')),
    ('Tâm lý học xã hội và gắn bó', ('tâm lý học xã hội', 'social psychology', 'gắn bó', 'attachment theory', 'dissonance', 'ảnh hưởng xã hội')),
    ('Cảm xúc và sức khỏe tâm lý', ('cảm xúc', 'emotion regulation', 'sang chấn', 'trauma', 'rối loạn tâm lý', 'mental health')),
    ('Lý thuyết trò chơi và ra quyết định', ('lý thuyết trò chơi', 'game theory', 'cân bằng nash', 'nash equilibrium', 'thiên kiến nhận thức', 'cognitive bias')),
    ('Tiếng Anh và kỹ năng giao tiếp', ('ielts', 'cue card', 'english vocabulary', 'kỹ năng giao tiếp', 'public speaking')),
)


def resolve_topic(value):
    target = normalized(value)
    for label, aliases in TOPICS:
        if target in {normalized(label), *(normalized(a) for a in aliases)}:
            return label, aliases
    return None


def matches(text, aliases):
    value = normalized(text)
    return any(re.search(r'(?<!\w)' + re.escape(normalized(alias)) + r'(?!\w)', value) for alias in aliases)

PATTERNS = {label: re.compile("|".join(r"(?<!\w)" + re.escape(normalized(a)) + r"(?!\w)" for a in aliases)) for label, aliases in TOPICS}
