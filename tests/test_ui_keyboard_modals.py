"""Contract checks for account modal and document viewer keyboard accessibility."""

from html.parser import HTMLParser
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import server
from app.history import HistoryStore


ROOT = Path(__file__).resolve().parents[1]


class _ElementAttrParser(HTMLParser):
    def __init__(self, target_id: str) -> None:
        super().__init__()
        self.target_id = target_id
        self.found_attrs: dict[str, str | None] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if attr_map.get("id") == self.target_id:
            self.found_attrs = attr_map


def test_document_content_modal_served_with_keyboard_dialog_attributes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "history_store", HistoryStore(tmp_path / "history.db"))
    client = TestClient(server.app)

    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    parser = _ElementAttrParser("document-content-modal")
    parser.feed(html)
    attrs = parser.found_attrs
    assert attrs is not None, "#document-content-modal must exist in served HTML"
    assert attrs.get("role") == "dialog"
    assert attrs.get("aria-modal") == "true"
    assert attrs.get("aria-labelledby") == "doc-content-modal-title"
    assert attrs.get("aria-hidden") == "true"
    assert attrs.get("tabindex") == "-1"
    assert "canvas-document-modal" in (attrs.get("class") or "").split()

    accounts_tag = re.search(r'<script\s+src="/static/accounts\.js\?v=([^"]+)"', html)
    app_tag = re.search(r'<script\s+src="/static/app\.js\?v=([^"]+)"', html)
    assert accounts_tag and accounts_tag.group(1).startswith("liquid-glass-202611")
    assert app_tag and app_tag.group(1).startswith("liquid-glass-202611")

    accounts_res = client.get(f"/static/accounts.js?v={accounts_tag.group(1)}")
    assert accounts_res.status_code == 200
    assert "/api/account/${mode}" in accounts_res.text
    assert 'id="account-auth-error" role="alert" aria-live="assertive"' in accounts_res.text
    # CSS rule `.account-btn:disabled` sets `display: none !important`; submit guard must not disable submit buttons.
    assert "btn-account-login').disabled = true" not in accounts_res.text
    assert 'btn-account-login").disabled = true' not in accounts_res.text

    app_res = client.get(f"/static/app.js?v={app_tag.group(1)}")
    assert app_res.status_code == 200
    assert "/api/documents/${docId}/original" in app_res.text
    assert "/api/documents/${documentId}/pdf#page=${pageNumber}" in app_res.text
    assert "/api/documents/${documentId}/pages/${pageNumber}/image" in app_res.text
