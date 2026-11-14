/**
 * ARYM — Account Session & First-Time Tour Binding (`static/accounts.js`)
 *
 * Clean header-pill account trigger (inspired by Open WebUI / LobeChat / Linear):
 * - Backend API contract:
 *   - GET /api/account/status
 *   - POST /api/account/login
 *   - POST /api/account/register
 *   - POST /api/account/logout
 *   - GET /api/account/features/{feature}
 *   - POST /api/account/features/{feature}/guided
 * - Account identity uses `account.id`, displaying username.
 * - Minimum password length: 12 characters.
 * - Respects `registration_allowed` and `required` flags from status.
 * - Preserves UI errors without false logout.
 * - Reloads account-scoped state upon login/logout.
 * - Accessible modal dialog with Escape dismissal, focus trapping, and focus restoration.
 */
(function () {
  'use strict';

  const FEATURE_KEYS = ['qa', 'planner', 'practice', 'review', 'examples', 'materials', 'ocr', 'documents'];

  let currentAccount = null;
  let authConfig = { required: false, registration_allowed: true };
  let activeFeature = 'qa';
  let authModalEl = null;
  let accountMenuEl = null;
  let tourRunningForAccountId = null;
  let previousActiveElement = null;
  let isAuthSubmitting = false;
  let isAuthImeComposing = false;
  let authKeyboardBound = false;

  function isOverlayElementOpen(el) {
    if (!el) return false;
    if (el.id === 'practice-dialog' || el.tagName === 'DIALOG') {
      return el.open === true || Boolean(el.hasAttribute && el.hasAttribute('open'));
    }
    if (el.id === 'document-content-modal') {
      return Boolean(el.classList?.contains?.('active')) || el.style?.display === 'flex';
    }
    if (el.id === 'arym-tour-overlay') {
      return Boolean(el.classList?.contains?.('is-active'));
    }
    return false;
  }

  function hasOverlayAboveAuthModal() {
    return (
      isOverlayElementOpen(document.getElementById('document-content-modal')) ||
      isOverlayElementOpen(document.getElementById('arym-tour-overlay')) ||
      isOverlayElementOpen(document.getElementById('practice-dialog'))
    );
  }

  function isElementVisibleFocusable(el) {
    if (!el || typeof el.focus !== 'function') return false;
    if (el === document.body || el === document.documentElement) return false;
    if ('isConnected' in el && !el.isConnected) return false;
    if (el.disabled || el.hasAttribute?.('disabled')) return false;
    if (el.getAttribute?.('tabindex') === '-1') return false;
    if (el.tagName === 'INPUT' && (el.getAttribute?.('type') || '').toLowerCase() === 'hidden') {
      return false;
    }

    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      if (cur.hidden === true || cur.hasAttribute?.('hidden')) return false;
      if (cur.getAttribute?.('aria-hidden') === 'true') return false;
      if (cur.style?.display === 'none' || cur.style?.visibility === 'hidden') return false;
      if (cur.classList?.contains?.('account-popover-menu') && !cur.classList.contains('is-open')) {
        return false;
      }
      if (cur.classList?.contains?.('account-modal-backdrop') && !cur.classList.contains('is-open')) {
        return false;
      }
      if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
        try {
          const cs = window.getComputedStyle(cur);
          if (cs && (cs.display === 'none' || cs.visibility === 'hidden')) return false;
        } catch (_e) {}
      }
      cur = cur.parentElement;
    }

    if (typeof el.getClientRects === 'function' && el.getClientRects().length === 0) {
      return false;
    }
    return true;
  }

  function resolveAuthFallbackFocus() {
    const candidateIds = ['btn-account-trigger', 'btn-replay-tour', 'user-input', 'btn-open-sidebar'];
    for (const id of candidateIds) {
      const el = document.getElementById(id);
      if (el && (!authModalEl || !authModalEl.contains?.(el)) && isElementVisibleFocusable(el)) {
        return el;
      }
    }
    return null;
  }

  function getAuthModalFocusables() {
    if (!authModalEl) return [];
    let candidates = Array.from(
      authModalEl.querySelectorAll('input, button, select, textarea, a[href], [tabindex]')
    );
    if (!candidates.length) {
      candidates = ['account-username', 'account-password', 'btn-account-cancel', 'btn-account-register', 'btn-account-login']
        .map((id) => document.getElementById(id))
        .filter(Boolean);
    }
    return candidates.filter((el) => authModalEl.contains?.(el) !== false && isElementVisibleFocusable(el));
  }

  function setAuthSubmitPending(pending) {
    isAuthSubmitting = Boolean(pending);
    const cardEl = authModalEl ? authModalEl.querySelector('.account-card') : null;
    if (cardEl && typeof cardEl.setAttribute === 'function') {
      if (isAuthSubmitting) cardEl.setAttribute('aria-busy', 'true');
      else cardEl.removeAttribute?.('aria-busy');
    }
    for (const id of ['btn-account-login', 'btn-account-register']) {
      const btn = document.getElementById(id);
      if (!btn || typeof btn.setAttribute !== 'function') continue;
      if (isAuthSubmitting) {
        btn.setAttribute('aria-disabled', 'true');
        btn.setAttribute('aria-busy', 'true');
      } else {
        btn.removeAttribute?.('aria-disabled');
        btn.removeAttribute?.('aria-busy');
      }
    }
  }

  async function apiJson(url, options) {
    const opts = options || {};
    const headers = Object.assign(
      {
        'X-Study-Request': '1',
      },
      opts.headers || {}
    );
    if (opts.body && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(
      url,
      Object.assign({ credentials: 'same-origin' }, opts, { headers })
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return data;
  }

  function ensureStylesAndModals() {
    if (document.getElementById('account-ui-styles')) {
      return;
    }
    const style = document.createElement('style');
    style.id = 'account-ui-styles';
    style.textContent = `
      .account-modal-backdrop {
        position: fixed;
        inset: 0;
        z-index: 9500;
        display: none;
        align-items: center;
        justify-content: center;
        padding: 18px;
        background: rgba(0, 0, 0, 0.72);
        backdrop-filter: blur(18px);
        -webkit-backdrop-filter: blur(18px);
      }
      .account-modal-backdrop.is-open { display: flex; }
      .account-card {
        width: min(420px, 100%);
        border-radius: 20px;
        padding: 24px;
        background: rgba(12, 12, 16, 0.92);
        backdrop-filter: blur(32px) saturate(180%);
        -webkit-backdrop-filter: blur(32px) saturate(180%);
        border: 1px solid rgba(255, 255, 255, 0.18);
        color: #f5f5f7;
        box-shadow:
          0 28px 70px rgba(0, 0, 0, 0.92),
          inset 0 1px 0 rgba(255, 255, 255, 0.28);
      }
      .account-card h2 { margin: 0 0 8px; font-size: 18px; font-weight: 700; color: #ffffff; }
      .account-card p { margin: 0 0 14px; color: #a1a1aa; font-size: 13px; line-height: 1.55; }
      .account-field { display: grid; gap: 6px; margin-bottom: 12px; }
      .account-field label { font-size: 12px; color: #d4d4d8; font-weight: 600; }
      .account-field input {
        width: 100%;
        box-sizing: border-box;
        border-radius: 11px;
        border: 1px solid rgba(255, 255, 255, 0.16);
        background: rgba(255, 255, 255, 0.05);
        color: #ffffff;
        padding: 10px 12px;
        font-size: 13px;
      }
      .account-field input:focus {
        outline: none;
        border-color: rgba(255, 255, 255, 0.42);
        background: rgba(255, 255, 255, 0.08);
      }
      .account-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
      .account-btn {
        border: 1px solid rgba(255, 255, 255, 0.16);
        background: rgba(255, 255, 255, 0.06);
        color: #f5f5f7;
        border-radius: 10px;
        padding: 7px 13px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: background 0.16s ease, border-color 0.16s ease;
      }
      .account-btn:hover {
        background: rgba(255, 255, 255, 0.12);
        border-color: rgba(255, 255, 255, 0.28);
      }
      .account-btn.primary {
        background: #ffffff;
        border-color: #ffffff;
        color: #000000;
      }
      .account-btn.primary:hover {
        background: #e5e5ea;
      }
      .account-btn:disabled,
      .account-btn[hidden] {
        display: none !important;
      }
      .account-error { color: #fca5a5; min-height: 18px; font-size: 12px; margin-top: 4px; }
      .account-popover-menu {
        position: fixed;
        bottom: 74px;
        right: 20px;
        z-index: 9400;
        display: none;
        min-width: 210px;
        padding: 10px;
        border-radius: 14px;
        background: rgba(12, 12, 16, 0.94);
        backdrop-filter: blur(28px) saturate(180%);
        -webkit-backdrop-filter: blur(28px) saturate(180%);
        border: 1px solid rgba(255, 255, 255, 0.18);
        box-shadow: 0 20px 50px rgba(0, 0, 0, 0.85), inset 0 1px 0 rgba(255, 255, 255, 0.25);
      }
      .account-popover-menu.is-open { display: grid; gap: 6px; }
      .account-popover-user {
        padding: 6px 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        font-size: 12px;
        color: #e4e4e7;
        font-weight: 600;
      }
    `;
    document.head.appendChild(style);

    authModalEl = document.createElement('div');
    authModalEl.className = 'account-modal-backdrop';
    authModalEl.id = 'account-auth-modal';
    authModalEl.setAttribute('role', 'presentation');
    authModalEl.innerHTML = `
      <div class="account-card" role="dialog" aria-modal="true" aria-labelledby="account-auth-title" tabindex="-1">
        <h2 id="account-auth-title">Đăng nhập hoặc tạo tài khoản</h2>
        <p>Lưu lịch sử hỏi đáp, tiến độ ôn tập và thời khóa biểu riêng theo tài khoản của bạn.</p>
        <div class="account-field">
          <label for="account-username">Tên tài khoản</label>
          <input id="account-username" autocomplete="username" placeholder="vd: sinhvien_cntt" />
        </div>
        <div class="account-field">
          <label for="account-password">Mật khẩu (tối thiểu 12 ký tự)</label>
          <input id="account-password" type="password" autocomplete="current-password" placeholder="Ít nhất 12 ký tự" />
        </div>
        <div class="account-error" id="account-auth-error" role="alert" aria-live="assertive"></div>
        <div class="account-actions">
          <button type="button" class="account-btn" id="btn-account-cancel" aria-label="Đóng hộp thoại">Đóng</button>
          <button type="button" class="account-btn" id="btn-account-register">Tạo tài khoản</button>
          <button type="button" class="account-btn primary" id="btn-account-login">Đăng nhập</button>
        </div>
      </div>
    `;
    document.body.appendChild(authModalEl);

    accountMenuEl = document.createElement('div');
    accountMenuEl.className = 'account-popover-menu';
    accountMenuEl.id = 'account-popover-menu';
    accountMenuEl.innerHTML = `
      <div class="account-popover-user" id="account-popover-user"></div>
      <div class="account-error" id="account-popover-error" role="alert" style="display:none; color:#fca5a5; font-size:11px; padding:4px 8px;"></div>
      <button type="button" class="account-btn" id="btn-account-switch">Đổi tài khoản</button>
      <button type="button" class="account-btn" id="btn-account-logout">Đăng xuất</button>
    `;
    document.body.appendChild(accountMenuEl);

    bindEvents();
    syncRegistrationButtonState();
  }

  function syncRegistrationButtonState() {
    const regBtn = document.getElementById('btn-account-register');
    if (regBtn) {
      regBtn.hidden = !authConfig.registration_allowed;
    }
  }

  function renderAccountHeaderTrigger() {
    const triggerBtn = document.getElementById('btn-account-trigger');
    const replayBtn = document.getElementById('btn-replay-tour');
    if (replayBtn) {
      replayBtn.hidden = false;
    }
    if (!triggerBtn) {
      return;
    }
    if (currentAccount) {
      const uname = currentAccount.username ? `@${currentAccount.username}` : 'Tài khoản';
      triggerBtn.textContent = uname;
      triggerBtn.setAttribute('data-authenticated', 'true');
      triggerBtn.title = `Tài khoản: ${uname}`;
      if (accountMenuEl) {
        const userLabel = accountMenuEl.querySelector('#account-popover-user');
        if (userLabel) {
          userLabel.textContent = uname;
        }
      }
    } else {
      triggerBtn.textContent = 'Đăng nhập';
      triggerBtn.setAttribute('data-authenticated', 'false');
      triggerBtn.title = 'Đăng nhập hoặc tạo tài khoản';
      if (accountMenuEl) {
        accountMenuEl.classList.remove('is-open');
      }
    }
  }

  function openAuthModal(preferredRestoreEl) {
    ensureStylesAndModals();
    const rawCandidate =
      preferredRestoreEl && typeof preferredRestoreEl.focus === 'function'
        ? preferredRestoreEl
        : document.activeElement;
    if (
      rawCandidate &&
      (!authModalEl || !authModalEl.contains?.(rawCandidate)) &&
      isElementVisibleFocusable(rawCandidate)
    ) {
      previousActiveElement = rawCandidate;
    } else {
      previousActiveElement = resolveAuthFallbackFocus();
    }
    isAuthImeComposing = false;
    const errEl = document.getElementById('account-auth-error');
    if (errEl) errEl.textContent = '';
    syncRegistrationButtonState();
    authModalEl.classList.add('is-open');
    authModalEl.setAttribute('aria-hidden', 'false');
    const input = document.getElementById('account-username');
    if (input) {
      input.value = '';
      input.focus();
    }
    const pwd = document.getElementById('account-password');
    if (pwd) pwd.value = '';
  }

  function closeAuthModal() {
    isAuthImeComposing = false;
    if (authModalEl) {
      authModalEl.classList.remove('is-open');
      authModalEl.setAttribute('aria-hidden', 'true');
    }
    const restoreTarget =
      previousActiveElement &&
      (!authModalEl || !authModalEl.contains?.(previousActiveElement)) &&
      isElementVisibleFocusable(previousActiveElement)
        ? previousActiveElement
        : resolveAuthFallbackFocus();
    previousActiveElement = null;
    if (restoreTarget && typeof restoreTarget.focus === 'function') {
      try {
        restoreTarget.focus();
      } catch (_e) {}
    }
  }

  function reloadAccountScopedState(account) {
    const scope = account?.id ? account.id : 'local';
    const metaEl = document.querySelector('meta[name="study-account-scope"]');
    if (metaEl) {
      metaEl.setAttribute('content', scope);
    }
    window.dispatchEvent(
      new CustomEvent('study:account-changed', {
        detail: { account: account || null, scope },
      })
    );
  }

  async function markAllFeaturesGuidedOnServer() {
    if (!currentAccount) return;
    await Promise.all(
      FEATURE_KEYS.map((key) =>
        apiJson(`/api/account/features/${encodeURIComponent(key)}/guided`, {
          method: 'POST',
        }).catch(() => {})
      )
    );
  }

  function launchSpotlightTour(options) {
    const opts = options || {};
    if (window.ARYMOnboardingTour && typeof window.ARYMOnboardingTour.start === 'function') {
      window.ARYMOnboardingTour.start({
        onComplete: async () => {
          if (opts.markServerGuided) {
            await markAllFeaturesGuidedOnServer();
          }
        },
      });
    }
  }

  async function submitAuth(mode) {
    if (isAuthSubmitting) return;
    const username = (document.getElementById('account-username')?.value || '').trim();
    const password = document.getElementById('account-password')?.value || '';
    const errEl = document.getElementById('account-auth-error');
    if (errEl) errEl.textContent = '';

    if (!username || username.length < 3) {
      if (errEl) errEl.textContent = 'Tên tài khoản phải có ít nhất 3 ký tự (chữ, số, ký tự đặc biệt hợp lệ).';
      return;
    }
    if (!password || password.length < 12) {
      if (errEl) errEl.textContent = 'Mật khẩu phải có ít nhất 12 ký tự.';
      return;
    }
    if (mode === 'register' && !authConfig.registration_allowed) {
      if (errEl) errEl.textContent = 'Đăng ký tài khoản hiện không được mở.';
      return;
    }

    setAuthSubmitPending(true);
    try {
      const payload = { username, password };
      const data = await apiJson(`/api/account/${mode}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      currentAccount = data.account || null;
      renderAccountHeaderTrigger();
      closeAuthModal();
      // Successful auth: full page reload to wipe guest state cleanly without cross-scope leak
      if (typeof window !== 'undefined' && window.location && typeof window.location.reload === 'function') {
        window.location.reload();
      } else {
        reloadAccountScopedState(currentAccount);
        await notifyFeatureEnter(activeFeature, true);
      }
    } catch (err) {
      // Preserve UI errors without false logout: DO NOT clear currentAccount
      if (errEl) errEl.textContent = err.message || 'Không thực hiện được.';
    } finally {
      setAuthSubmitPending(false);
    }
  }

  async function logout() {
    const errorEl = document.getElementById('account-popover-error') || document.getElementById('account-auth-error');
    if (errorEl) {
      errorEl.textContent = '';
      errorEl.style.display = 'none';
    }
    try {
      await apiJson('/api/account/logout', { method: 'POST' });
    } catch (err) {
      // Failed logout: NEVER clear identity or reload! Show readable error and retain state.
      if (errorEl) {
        errorEl.textContent = err.message || 'Đăng xuất không thành công.';
        errorEl.style.display = 'block';
      }
      return;
    }
    // Successful logout:
    currentAccount = null;
    tourRunningForAccountId = null;
    if (accountMenuEl) accountMenuEl.classList.remove('is-open');
    renderAccountHeaderTrigger();
    // Full page reload after successful logout using server guest scope
    if (typeof window !== 'undefined' && window.location && typeof window.location.reload === 'function') {
      window.location.reload();
    } else {
      reloadAccountScopedState(null);
    }
  }

  async function notifyFeatureEnter(feature, forceGuideCheck) {
    const mapped = feature === 'tools' ? 'practice' : feature === 'timetable' ? 'planner' : feature;
    activeFeature = FEATURE_KEYS.includes(mapped) ? mapped : 'qa';
    if (!currentAccount) return null;
    try {
      const data = await apiJson(`/api/account/features/${encodeURIComponent(activeFeature)}`, {
        method: 'GET',
      });
      if (data.first_use && (forceGuideCheck || tourRunningForAccountId !== currentAccount.id)) {
        tourRunningForAccountId = currentAccount.id;
        await markAllFeaturesGuidedOnServer();
        launchSpotlightTour({ markServerGuided: false });
      }
      return data;
    } catch (_err) {
      return null;
    }
  }

  function bindEvents() {
    const triggerBtn = document.getElementById('btn-account-trigger');
    if (triggerBtn && !triggerBtn.dataset.boundAccount) {
      triggerBtn.dataset.boundAccount = 'true';
      triggerBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!currentAccount) {
          openAuthModal();
        } else if (accountMenuEl) {
          accountMenuEl.classList.toggle('is-open');
        }
      });
    }

    const replayBtn = document.getElementById('btn-replay-tour');
    if (replayBtn && !replayBtn.dataset.boundTour) {
      replayBtn.dataset.boundTour = 'true';
      replayBtn.addEventListener('click', () => {
        launchSpotlightTour({ markServerGuided: Boolean(currentAccount) });
      });
    }

    document.addEventListener('click', (e) => {
      if (accountMenuEl && !accountMenuEl.contains(e.target) && e.target?.id !== 'btn-account-trigger') {
        accountMenuEl.classList.remove('is-open');
      }
      if (authModalEl && authModalEl.classList.contains('is-open') && e.target === authModalEl) {
        closeAuthModal();
      }
    });

    // Accessible keyboard handling (Escape dismissal, IME-safe login Enter, and modal focus trap)
    if (!authKeyboardBound) {
      authKeyboardBound = true;
      document.addEventListener(
        'keydown',
        (e) => {
          if (hasOverlayAboveAuthModal()) return;
          const isAuthOpen = Boolean(authModalEl && authModalEl.classList.contains('is-open'));
          const isMenuOpen = Boolean(accountMenuEl && accountMenuEl.classList.contains('is-open'));

          if (e.key === 'Escape') {
            if (isAuthOpen) {
              e.preventDefault?.();
              e.stopPropagation?.();
              e.stopImmediatePropagation?.();
              closeAuthModal();
              return;
            }
            if (isMenuOpen) {
              e.preventDefault?.();
              e.stopPropagation?.();
              e.stopImmediatePropagation?.();
              const wasInsideMenu = Boolean(
                document.activeElement && accountMenuEl.contains?.(document.activeElement)
              );
              accountMenuEl.classList.remove('is-open');
              if (wasInsideMenu) {
                const trigger = document.getElementById('btn-account-trigger');
                if (isElementVisibleFocusable(trigger)) {
                  try {
                    trigger.focus();
                  } catch (_e) {}
                }
              }
              return;
            }
          }

          if (!isAuthOpen) return;

          if (e.key === 'Tab') {
            const focusables = getAuthModalFocusables();
            if (!focusables.length) return;
            const first = focusables[0];
            const last = focusables[focusables.length - 1];
            const active = document.activeElement;
            if (!authModalEl.contains?.(active)) {
              e.preventDefault?.();
              e.stopPropagation?.();
              try {
                (e.shiftKey ? last : first).focus();
              } catch (_e) {}
              return;
            }
            if (e.shiftKey && active === first) {
              e.preventDefault?.();
              e.stopPropagation?.();
              try {
                last.focus();
              } catch (_e) {}
            } else if (!e.shiftKey && active === last) {
              e.preventDefault?.();
              e.stopPropagation?.();
              try {
                first.focus();
              } catch (_e) {}
            }
            return;
          }

          if (e.key === 'Enter') {
            const target = e.target || document.activeElement;
            if (target && !authModalEl.contains?.(target)) {
              e.preventDefault?.();
              e.stopPropagation?.();
              e.stopImmediatePropagation?.();
              const focusables = getAuthModalFocusables();
              const fallbackTarget = document.getElementById('account-username') || focusables[0];
              if (fallbackTarget && typeof fallbackTarget.focus === 'function') {
                try {
                  fallbackTarget.focus();
                } catch (_e) {}
              }
            }
          }
        },
        true
      );
    }

    for (const inputId of ['account-username', 'account-password']) {
      const fieldEl = document.getElementById(inputId);
      if (!fieldEl || fieldEl.dataset?.boundAuthEnter) continue;
      if (fieldEl.dataset) fieldEl.dataset.boundAuthEnter = 'true';
      fieldEl.addEventListener('compositionstart', () => {
        isAuthImeComposing = true;
      });
      fieldEl.addEventListener('compositionend', () => {
        isAuthImeComposing = false;
      });
      fieldEl.addEventListener('blur', () => {
        isAuthImeComposing = false;
      });
      fieldEl.addEventListener('keydown', (e) => {
        if (!authModalEl || !authModalEl.classList.contains('is-open')) return;
        if (hasOverlayAboveAuthModal()) return;
        if (e.isComposing || isAuthImeComposing || e.keyCode === 229 || e.which === 229) return;
        if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault?.();
          e.stopPropagation?.();
          submitAuth('login');
        }
      });
    }

    document.getElementById('btn-account-cancel')?.addEventListener('click', closeAuthModal);
    document.getElementById('btn-account-login')?.addEventListener('click', () => submitAuth('login'));
    document.getElementById('btn-account-register')?.addEventListener('click', () => submitAuth('register'));
    document.getElementById('btn-account-switch')?.addEventListener('click', () => {
      const trigger = document.getElementById('btn-account-trigger');
      if (accountMenuEl) accountMenuEl.classList.remove('is-open');
      openAuthModal(trigger);
    });
    document.getElementById('btn-account-logout')?.addEventListener('click', logout);

    document.getElementById('mode-qa')?.addEventListener('click', () => notifyFeatureEnter('qa'));
    document.getElementById('mode-tools')?.addEventListener('click', () => notifyFeatureEnter('tools'));
    document.getElementById('mode-timetable')?.addEventListener('click', () => notifyFeatureEnter('timetable'));
  }

  async function init() {
    ensureStylesAndModals();
    try {
      const data = await apiJson('/api/account/status', { method: 'GET' });
      currentAccount = data.account || null;
      authConfig.required = Boolean(data.required);
      authConfig.registration_allowed = data.registration_allowed !== false;
    } catch (_err) {
      currentAccount = null;
    }
    syncRegistrationButtonState();
    renderAccountHeaderTrigger();
    if (currentAccount) {
      await notifyFeatureEnter(activeFeature, true);
    }
  }

  window.StudyAccount = {
    getAccount: () => currentAccount,
    getConfig: () => Object.assign({}, authConfig),
    openAuthModal,
    closeAuthModal,
    reloadState: () => reloadAccountScopedState(currentAccount),
    notifyFeatureEnter,
    showGuide: () => launchSpotlightTour({ markServerGuided: Boolean(currentAccount) }),
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
