"""Regression tests for Packet UI-AUDIT-20261110-02 & Packet UI-AUDIT-20261110-03:
1. Account auth contract (/api/account/status, login, register, logout, features GET/POST, id not account_id, min12 pwd, registration_allowed, modal a11y, full page reload on auth/logout, error retention on failed logout).
2. Mobile 355/390px two-row grid dock layout, actual #btn-open-sidebar targeting, username bounds/ellipsis, all 7 controls >=32px, and panel bottom clearance >=100px.
3. Planner weekly lesson card exclusively in canonical step 3, full usable width, table overflow scroll reachability.
4. Onboarding tour inactive state fully hidden/noninteractive (pointer-events none on backdrop and callout when inactive), Escape/finish focus restoration.
5. Coherent asset cache-busting version tags in index.html.
"""

from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_accounts_js_api_contracts_and_a11y():
    accounts_js = (ROOT / "static/accounts.js").read_text(encoding="utf-8")

    # 1. Nonexistent endpoint ban: MUST NOT call /api/accounts/me or /api/accounts/
    assert "/api/accounts/me" not in accounts_js
    assert "/api/accounts/login" not in accounts_js
    assert "/api/accounts/register" not in accounts_js
    assert "/api/accounts/logout" not in accounts_js
    assert "/api/accounts/" not in accounts_js

    # 2. Correct singular /api/account/ endpoints
    assert "/api/account/status" in accounts_js
    assert "/api/account/login" in accounts_js
    assert "/api/account/register" in accounts_js
    assert "/api/account/logout" in accounts_js
    assert "/api/account/features/" in accounts_js
    assert "/guided" in accounts_js

    # 3. Account schema: uses id, not account_id, and no display_name backend contract
    assert "account_id" not in accounts_js
    assert "display_name" not in accounts_js
    assert "currentAccount.id" in accounts_js or "account?.id" in accounts_js or "account.id" in accounts_js

    # 4. Password minimum 12 chars contract
    assert "12" in accounts_js
    assert "password.length < 12" in accounts_js or "length < 12" in accounts_js

    # 5. Respects registration_allowed and required
    assert "registration_allowed" in accounts_js
    assert "required" in accounts_js

    # 6. Preserves UI errors without false logout
    assert "account-auth-error" in accounts_js
    assert "account-popover-error" in accounts_js

    # 7. Reloads on successful auth/logout to prevent cross-scope leakage; does not clear on failed logout
    assert "window.location.reload" in accounts_js
    assert "currentAccount = null" in accounts_js

    # 8. Accessible dialog attributes & keyboard controls
    assert 'role="dialog"' in accounts_js
    assert 'aria-modal="true"' in accounts_js
    assert "Escape" in accounts_js


def test_planner_wizard_owns_step_state_and_weekly_lessons_in_step3():
    wizard_js = (ROOT / "static/planner-wizard.js").read_text(encoding="utf-8")
    spatial_js = (ROOT / "static/spatial-layout.js").read_text(encoding="utf-8")
    spatial_css = (ROOT / "static/spatial-layout.css").read_text(encoding="utf-8")

    # 1. Step 3 definition includes planner-weekly-lessons-card exclusively in canonical step 3
    step1_match = re.search(r'\["Lịch học"[^\]]*\[([^\]]+)\]\]', wizard_js)
    assert step1_match is not None and "planner-weekly-lessons-card" not in step1_match.group(1)

    step2_match = re.search(r'\["Thời gian cá nhân"[^\]]*\[([^\]]+)\]\]', wizard_js)
    assert step2_match is not None and "planner-weekly-lessons-card" not in step2_match.group(1)

    step3_match = re.search(r'\["Kế hoạch & deadline"[^\]]*\[([^\]]+)\]\]', wizard_js)
    assert step3_match is not None
    assert "planner-weekly-lessons-card" in step3_match.group(1)

    step4_match = re.search(r'\["Xuất lịch"[^\]]*\[([^\]]*)\]\]', wizard_js)
    assert step4_match is not None and "planner-weekly-lessons-card" not in step4_match.group(1)

    # 2. No competing wizard click mutation in spatial-layout.js
    assert "planner-wizard-progress" not in spatial_js
    assert "planner-wizard-screen" not in spatial_js

    # 3. Full usable width in spatial-layout.css (no 2-column squeeze on planner wizard screens)
    assert ".timetable-panel:not([hidden])" in spatial_css
    assert ".timetable-panel > .planner-wizard-screen:not([hidden])" in spatial_css
    assert "#planner-weekly-lessons-card {\n  grid-column: 1 / 2" not in spatial_css
    assert ".planner-wizard-screen:not([hidden]) {\n  grid-column: 2 / 3" not in spatial_css

    # 4. Table overflow scroll reachability
    assert "overflow-x: auto" in spatial_css
    assert "-webkit-overflow-scrolling: touch" in spatial_css


def test_mobile_dock_and_layout_guarantees():
    spatial_css = (ROOT / "static/spatial-layout.css").read_text(encoding="utf-8")
    liquid_css = (ROOT / "static/liquid-glass.css").read_text(encoding="utf-8")
    wizard_css = (ROOT / "static/planner-wizard.css").read_text(encoding="utf-8")

    # 1. Consolidated overrides: No conflicting .chat-header mobile media queries in liquid-glass.css or planner-wizard.css
    assert ".chat-header" not in wizard_css
    assert ".chat-header" not in liquid_css.split("/* Mobile Cascade Overrides")[0] if "/* Mobile Cascade Overrides" in liquid_css else True

    # 2. Real button selector: Targets #btn-open-sidebar, NOT nonexistent #sidebar-toggle
    assert ".chat-header #btn-open-sidebar" in spatial_css
    assert "#sidebar-toggle" not in spatial_css

    # 3. Compact mobile Two-Row Grid structure in spatial-layout.css
    assert "grid-template-columns: 1fr 1fr" in spatial_css
    assert "grid-template-rows: auto auto" in spatial_css
    assert "grid-column: 1 / -1" in spatial_css  # mode-switch spans full width row 2

    # 4. Bounded and ellipsis username on #btn-account-trigger
    assert ".chat-header #btn-account-trigger" in spatial_css
    assert "text-overflow: ellipsis" in spatial_css
    assert "max-width: 95px" in spatial_css
    assert "max-width: 82px" in spatial_css

    # 5. Touch targets usable >= 32px
    assert "height: 32px" in spatial_css
    assert "min-height: 32px" in spatial_css
    assert "min-width: 34px" in spatial_css

    # 6. Bottom clearance >= 100px so dock never overlaps workspace / composer
    assert "padding: 12px 10px 104px 10px !important;" in spatial_css

    # 7. Redundant brand pill hidden on mobile
    assert ".chat-header .header-brand-pill" in spatial_css
    assert "display: none !important;" in spatial_css


def test_onboarding_tour_inactive_state_contract():
    tour_css = (ROOT / "static/onboarding-tour.css").read_text(encoding="utf-8")
    tour_js = (ROOT / "static/onboarding-tour.js").read_text(encoding="utf-8")

    # 1. Overlay when inactive is hidden and pointer-events none
    assert ".arym-tour-overlay {" in tour_css
    assert "pointer-events: none;" in tour_css
    assert "visibility: hidden;" in tour_css
    assert "display: none;" in tour_css

    # 2. Backdrop when inactive is pointer-events none; active has pointer-events auto
    assert ".arym-tour-backdrop {" in tour_css
    assert ".arym-tour-overlay.is-active .arym-tour-backdrop" in tour_css

    # 3. Callout frame when inactive is pointer-events none; active has pointer-events auto
    assert ".arym-tour-overlay.is-active .arym-tour-callout-frame" in tour_css

    # 4. JS handles Escape, hides overlay completely and restores focus
    assert "Escape" in tour_js
    assert "previousActiveElement" in tour_js
    assert "overlayEl.style.display = 'none'" in tour_js
    assert "aria-hidden" in tour_js


def test_asset_versions_consistently_bumped_in_index_html():
    index_html = (ROOT / "static/index.html").read_text(encoding="utf-8")

    # Ensure no stale ?v=1 or ?v=2 on modified assets
    for asset in [
        "/static/liquid-glass.css",
        "/static/onboarding-tour.css",
        "/static/spatial-layout.css",
        "/static/planner-wizard.css",
        "/static/spatial-layout.js",
        "/static/onboarding-tour.js",
        "/static/accounts.js",
        "/static/planner-wizard.js",
    ]:
        pattern = rf'{re.escape(asset)}\?v=([^\s"\'<>]+)'
        match = re.search(pattern, index_html)
        assert match is not None, f"Asset {asset} missing version query in index.html"
        assert match.group(1) not in ("1", "2"), f"Asset {asset} has stale version ?v={match.group(1)}"
        assert match.group(1).startswith("liquid-glass-202611")

