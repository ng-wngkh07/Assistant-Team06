import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_three_learning_tabs_share_one_tools_navigation_item():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    nav = re.search(r'<nav class="mode-switch"[^>]*>(.*?)</nav>', html, re.S).group(1)
    assert 'id="mode-tools"' in nav
    assert 'id="mode-documents"' not in nav
    assert nav.count('id="mode-tools"') == 1

    tools = re.search(
        r'<section id="learning-tools-panel".*?</section>\s*</section>\s*<section id="timetable-panel"',
        html,
        re.S,
    ).group(0)
    for tab in ("practice", "review", "examples"):
        assert f'id="btn-tool-tab-{tab}"' in tools
        assert f'id="tool-tab-{tab}"' in tools
    assert re.findall(r'role="tab"[^>]*>(.*?)</button>', tools) == [
        "Tạo bài tập",
        'Ôn tập <span id="review-count">0</span>',
        "Trích xuất bài tập",
    ]
    assert 'id="tool-tab-practice"' in tools
    assert 'id="tool-tab-review"' in tools
    assert 'id="tool-tab-examples"' in tools
    assert "Câu hỏi do AI tạo" in tools
    assert "nội dung không được AI viết lại" in tools
    assert "localStorage" in tools or "trình duyệt này" in tools
    assert "Xóa toàn bộ tiến độ" in tools


def test_learning_tools_have_theme_rules_and_no_decorative_images_or_gradients():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8").lower()
    assert "prefers-color-scheme: dark" in css
    assert "background-image" not in css
    assert "gradient(" not in css


class _PageStructure:
    @staticmethod
    def read(html):
        from html.parser import HTMLParser

        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []
                self.stack = []

            def handle_starttag(self, tag, attrs):
                node = {"tag": tag, "attrs": dict(attrs), "parent": self.stack[-1] if self.stack else None}
                self.elements.append(node)
                if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                    self.stack.append(node)

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index]["tag"] == tag:
                        del self.stack[index:]
                        break

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                self.handle_endtag(tag)

        parser = Parser()
        parser.feed(html)
        return parser.elements


def test_mode_panels_are_siblings_and_controls_have_unique_ids():
    elements = _PageStructure.read((ROOT / "static" / "index.html").read_text(encoding="utf-8"))
    ids = [node["attrs"]["id"] for node in elements if "id" in node["attrs"]]
    assert len(ids) == len(set(ids)), "Duplicate IDs can bind navigation to the wrong element"
    for panel_id in ("qa-panel", "learning-tools-panel", "timetable-panel"):
        panel = next(node for node in elements if node["attrs"].get("id") == panel_id)
        assert panel["parent"]["tag"] == "main", f"{panel_id} must not be nested in a hidden panel"
    assert sum(node["tag"] == "nav" and node["attrs"].get("class") == "mode-switch" for node in elements) == 1


def test_page_assets_are_loaded_once_and_both_themes_use_flat_colors():
    elements = _PageStructure.read((ROOT / "static" / "index.html").read_text(encoding="utf-8"))
    assert sum(node["tag"] == "title" for node in elements) == 1
    stylesheets = [node["attrs"]["href"].split("?", 1)[0] for node in elements if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"]
    assert len(stylesheets) == len(set(stylesheets)), "Duplicate CSS loads can override the chosen theme"
    assert {"/static/style.css", "/static/theme.css"}.issubset(stylesheets)
    theme_css = (ROOT / "static" / "theme.css").read_text(encoding="utf-8").lower()
    assert "gradient(" not in theme_css, "The light theme follows the same flat-color design contract"


def test_light_theme_defines_learning_tool_surface():
    theme_css = (ROOT / "static" / "theme.css").read_text(encoding="utf-8")
    light_palette = re.search(r'html\[data-theme="light"\]\s*\{([^}]+)\}', theme_css).group(1)
    assert "--bg-subtle:" in light_palette, "Learning-tool tabs must not inherit the OS dark surface in light mode"


def test_default_palette_has_no_conflicting_theme_values():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    default_palette = re.search(r':root\s*\{([^}]+)\}', css).group(1)
    names = re.findall(r'(--[a-z-]+)\s*:', default_palette)
    assert len(names) == len(set(names)), "A merged light palette must not override the default dark theme"
