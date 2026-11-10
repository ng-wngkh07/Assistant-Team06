/**
 * ARYM — Account Session & First-Time Tour Binding (`static/accounts.js`)
 *
 * Clean header-pill account trigger (inspired by Open WebUI / LobeChat / Linear):
 * - No unrequested top status bars or extra mode annotation clutter text.
 * - Triggers the button-spotlight onboarding tour (`window.ARYMOnboardingTour`) strictly ONCE
 *   for newly created accounts (`first_use === true`), persisting completion to the server
 *   via `POST /api/account/features/{feature}/guided` so it never repeats on subsequent visits.
 */
(function () {
  'use strict';

  const FEATURE_KEYS = ['qa', 'tools', 'timetable', 'planner'];

  let currentAccount = null;
  let activeFeature = 'qa';
  let authModalEl = null;
  let accountMenuEl = null;
  let tourRunningForAccountId = null;

  async function apiJson(url, options) {
    const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, options || {}));
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
        background: rgba(12, 12, 16, 0.88);
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
      .account-error { color: #fca5a5; min-height: 18px; font-size: 12px; }
      .account-popover-menu {
        position: fixed;
        top: 56px;
        right: 20px;
        z-index: 9400;
        display: none;
        min-width: 210px;
        padding: 10px;
        border-radius: 14px;
        background: rgba(12, 12, 16, 0.92);
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
    authModalEl.innerHTML = `
      <div class="account-card" role="dialog" aria-modal="true" aria-labelledby="account-auth-title">
        <h2 id="account-auth-title">Đăng nhập hoặc tạo tài khoản</h2>
        <p>Lưu lịch sử hỏi đáp, tiến độ ôn tập và thời khóa biểu riêng theo tài khoản của bạn.</p>
        <div class="account-field">
          <label for="account-username">Tên tài khoản</label>
          <input id="account-username" autocomplete="username" placeholder="vd: sinhvien_cntt" />
        </div>
        <div class="account-field">
          <label for="account-display-name">Tên hiển thị (khi tạo mới)</label>
          <input id="account-display-name" autocomplete="name" placeholder="vd: Nguyễn Văn A" />
        </div>
        <div class="account-field">
          <label for="account-password">Mật khẩu</label>
          <input id="account-password" type="password" autocomplete="current-password" placeholder="Ít nhất 6 ký tự" />
        </div>
        <div class="account-error" id="account-auth-error"></div>
        <div class="account-actions">
          <button type="button" class="account-btn" id="btn-account-cancel">Đóng</button>
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
      <button type="button" class="account-btn" id="btn-account-switch">Đổi tài khoản</button>
      <button type="button" class="account-btn" id="btn-account-logout">Đăng xuất</button>
    `;
    document.body.appendChild(accountMenuEl);

    bindEvents();
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
      triggerBtn.textContent = currentAccount.display_name || `@${currentAccount.username}`;
      triggerBtn.setAttribute('data-authenticated', 'true');
      triggerBtn.title = `Tài khoản: @${currentAccount.username}`;
      if (accountMenuEl) {
        const userLabel = accountMenuEl.querySelector('#account-popover-user');
        if (userLabel) {
          userLabel.textContent = `${currentAccount.display_name} (@${currentAccount.username})`;
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

  function openAuthModal() {
    ensureStylesAndModals();
    const errEl = document.getElementById('account-auth-error');
    if (errEl) errEl.textContent = '';
    authModalEl.classList.add('is-open');
    const input = document.getElementById('account-username');
    if (input) input.focus();
  }

  function closeAuthModal() {
    if (authModalEl) authModalEl.classList.remove('is-open');
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
    const username = (document.getElementById('account-username')?.value || '').trim();
    const displayName = (document.getElementById('account-display-name')?.value || '').trim();
    const password = document.getElementById('account-password')?.value || '';
    const errEl = document.getElementById('account-auth-error');
    if (errEl) errEl.textContent = '';
    try {
      const payload = mode === 'register'
        ? { username, password, display_name: displayName || username }
        : { username, password };
      const data = await apiJson(`/api/accounts/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      currentAccount = data.account || null;
      renderAccountHeaderTrigger();
      closeAuthModal();
      window.dispatchEvent(new CustomEvent('study:account-changed', { detail: { account: currentAccount } }));
      await notifyFeatureEnter(activeFeature, true);
    } catch (err) {
      if (errEl) errEl.textContent = err.message || 'Không thực hiện được.';
    }
  }

  async function logout() {
    await apiJson('/api/accounts/logout', { method: 'POST' }).catch(() => {});
    currentAccount = null;
    tourRunningForAccountId = null;
    if (accountMenuEl) accountMenuEl.classList.remove('is-open');
    renderAccountHeaderTrigger();
    window.dispatchEvent(new CustomEvent('study:account-changed', { detail: { account: null } }));
  }

  async function notifyFeatureEnter(feature, forceGuideCheck) {
    activeFeature = FEATURE_KEYS.includes(feature) ? feature : 'qa';
    if (!currentAccount) return null;
    try {
      const data = await apiJson(`/api/account/features/${encodeURIComponent(activeFeature)}`, {
        method: 'POST',
      });
      if (data.first_use && (forceGuideCheck || tourRunningForAccountId !== currentAccount.account_id)) {
        tourRunningForAccountId = currentAccount.account_id;
        // Mark guided immediately on server so refreshing or switching tabs never triggers it a 2nd time
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
    });

    document.getElementById('btn-account-cancel')?.addEventListener('click', closeAuthModal);
    document.getElementById('btn-account-login')?.addEventListener('click', () => submitAuth('login'));
    document.getElementById('btn-account-register')?.addEventListener('click', () => submitAuth('register'));
    document.getElementById('btn-account-switch')?.addEventListener('click', () => {
      if (accountMenuEl) accountMenuEl.classList.remove('is-open');
      openAuthModal();
    });
    document.getElementById('btn-account-logout')?.addEventListener('click', logout);

    document.getElementById('mode-qa')?.addEventListener('click', () => notifyFeatureEnter('qa'));
    document.getElementById('mode-tools')?.addEventListener('click', () => notifyFeatureEnter('tools'));
    document.getElementById('mode-timetable')?.addEventListener('click', () => notifyFeatureEnter('timetable'));
  }

  async function init() {
    ensureStylesAndModals();
    try {
      const data = await apiJson('/api/accounts/me');
      currentAccount = data.account || null;
    } catch (_err) {
      currentAccount = null;
    }
    renderAccountHeaderTrigger();
    if (currentAccount) {
      await notifyFeatureEnter(activeFeature, true);
    }
  }

  window.StudyAccount = {
    getAccount: () => currentAccount,
    openAuthModal,
    notifyFeatureEnter,
    showGuide: () => launchSpotlightTour({ markServerGuided: Boolean(currentAccount) }),
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
