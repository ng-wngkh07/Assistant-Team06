const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function createDOM() {
  const elements = new Map();
  const docEvents = {};

  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase();
      this.listeners = {};
      this.style = {};
      this._value = '';
      this._innerHTML = '';
      this.textContent = '';
      this.children = [];
      this.attributes = {};
      this.hidden = false;
      this.dataset = {};
      this.parentElement = null;
      this._id = '';
      this.classList = {
        _classes: new Set(),
        add: (...cls) => cls.forEach((c) => this.classList._classes.add(c)),
        remove: (...cls) => cls.forEach((c) => this.classList._classes.delete(c)),
        toggle: (c, force) => {
          if (force === undefined) {
            if (this.classList._classes.has(c)) {
              this.classList._classes.delete(c);
              return false;
            }
            this.classList._classes.add(c);
            return true;
          }
          if (force) this.classList._classes.add(c);
          else this.classList._classes.delete(c);
          return force;
        },
        contains: (c) => this.classList._classes.has(c),
      };
    }
    get value() {
      return this._value;
    }
    set value(v) {
      this._value = String(v);
    }
    get id() {
      return this._id;
    }
    set id(val) {
      this._id = val;
      if (val) elements.set(val, this);
    }
    get innerHTML() {
      return this._innerHTML;
    }
    set innerHTML(html) {
      this._innerHTML = html;
      const idMatches = html.matchAll(/id=["']([^"']+)["']/g);
      for (const match of idMatches) {
        const childId = match[1];
        if (!elements.has(childId)) {
          const childEl = new Element('div');
          childEl.id = childId;
          childEl.ownerDocument = this.ownerDocument;
          this.appendChild(childEl);
        }
      }
    }
    addEventListener(name, fn) {
      (this.listeners[name] ||= []).push(fn);
    }
    removeEventListener(name, fn) {
      if (!this.listeners[name]) return;
      this.listeners[name] = this.listeners[name].filter((f) => f !== fn);
    }
    dispatchEvent(e) {
      for (const fn of this.listeners[e.type] || []) fn(e);
      return true;
    }
    setAttribute(k, v) {
      this.attributes[k] = String(v);
      if (k === 'id') this.id = v;
    }
    removeAttribute(k) {
      delete this.attributes[k];
    }
    getAttribute(k) {
      return this.attributes[k] || null;
    }
    appendChild(x) {
      x.parentElement = this;
      this.children.push(x);
      return x;
    }
    append(...xs) {
      for (const x of xs) {
        if (typeof x === 'string') {
          const t = new Element('#text');
          t.textContent = x;
          this.appendChild(t);
        } else if (x) {
          this.appendChild(x);
        }
      }
    }
    focus() {
      if (this.ownerDocument) this.ownerDocument.activeElement = this;
    }
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    }
    querySelectorAll(sel) {
      const results = [];
      const traverse = (node) => {
        for (const child of node.children) {
          if (sel.startsWith('#') && child.id === sel.slice(1)) results.push(child);
          else if (sel.startsWith('.') && child.classList.contains(sel.slice(1))) results.push(child);
          else if (sel.toLowerCase() === child.tagName.toLowerCase()) results.push(child);
          traverse(child);
        }
      };
      traverse(this);
      return results;
    }
    contains(other) {
      let cur = other;
      while (cur) {
        if (cur === this) return true;
        cur = cur.parentElement;
      }
      return false;
    }
  }

  const document = {
    activeElement: null,
    readyState: 'complete',
    body: new Element('body'),
    head: new Element('head'),
    createElement(tag) {
      const el = new Element(tag);
      el.ownerDocument = document;
      return el;
    },
    getElementById(id) {
      return elements.get(id) || null;
    },
    querySelector(sel) {
      if (sel.startsWith('#')) return this.getElementById(sel.slice(1));
      if (sel.includes('meta[name="study-account-scope"]')) {
        let meta = elements.get('meta-scope');
        if (!meta) {
          meta = this.createElement('meta');
          meta.setAttribute('name', 'study-account-scope');
          meta.setAttribute('content', 'local');
          elements.set('meta-scope', meta);
        }
        return meta;
      }
      return this.body.querySelector(sel);
    },
    querySelectorAll(sel) {
      return this.body.querySelectorAll(sel);
    },
    addEventListener(name, fn) {
      (docEvents[name] ||= []).push(fn);
    },
    removeEventListener(name, fn) {
      if (!docEvents[name]) return;
      docEvents[name] = docEvents[name].filter((f) => f !== fn);
    },
    dispatchEvent(e) {
      for (const fn of docEvents[e.type] || []) fn(e);
      return true;
    },
  };
  document.body.ownerDocument = document;
  document.head.ownerDocument = document;

  return { document, elements, docEvents };
}

test('accounts.js: initializes via /api/account/status with X-Study-Request and renders trigger', async () => {
  const { document, elements } = createDOM();
  const fetchCalls = [];

  const mockFetch = async (url, opts) => {
    fetchCalls.push({ url, opts });
    if (url === '/api/account/status') {
      return {
        ok: true,
        json: async () => ({
          account: null,
          required: false,
          registration_allowed: true,
        }),
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const windowObj = {
    addEventListener: () => {},
    dispatchEvent: () => {},
    CustomEvent: class {
      constructor(t, d) {
        this.type = t;
        this.detail = d?.detail;
      }
    },
  };

  const trigger = document.createElement('button');
  trigger.id = 'btn-account-trigger';

  const script = fs.readFileSync(path.join(__dirname, '../static/accounts.js'), 'utf8');
  vm.runInNewContext(script, {
    document,
    window: windowObj,
    fetch: mockFetch,
    console,
    Promise,
    Object,
    Event: class { constructor(t) { this.type = t; } },
    CustomEvent: windowObj.CustomEvent,
  });

  await new Promise((r) => setTimeout(r, 10));

  assert.equal(fetchCalls.length >= 1, true);
  assert.equal(fetchCalls[0].url, '/api/account/status');
  assert.equal(fetchCalls[0].opts.headers['X-Study-Request'], '1');
  assert.equal(trigger.textContent, 'Đăng nhập');
  assert.equal(trigger.getAttribute('data-authenticated'), 'false');
});

test('accounts.js: enforces password min length 12 before network and handles auth login', async () => {
  const { document, elements } = createDOM();
  const fetchCalls = [];
  const dispatchedEvents = [];

  const mockFetch = async (url, opts) => {
    fetchCalls.push({ url, opts });
    if (url === '/api/account/status') {
      return {
        ok: true,
        json: async () => ({ account: null, required: false, registration_allowed: true }),
      };
    }
    if (url === '/api/account/login') {
      return {
        ok: true,
        json: async () => ({
          account: { id: 'test_account_uuid', username: 'student123' },
        }),
      };
    }
    if (url.startsWith('/api/account/features/')) {
      return { ok: true, json: async () => ({ first_use: false }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const windowObj = {
    addEventListener: () => {},
    dispatchEvent: (ev) => dispatchedEvents.push(ev),
    CustomEvent: class {
      constructor(t, d) {
        this.type = t;
        this.detail = d?.detail;
      }
    },
  };

  const trigger = document.createElement('button');
  trigger.id = 'btn-account-trigger';

  const script = fs.readFileSync(path.join(__dirname, '../static/accounts.js'), 'utf8');
  vm.runInNewContext(script, {
    document,
    window: windowObj,
    fetch: mockFetch,
    console,
    Promise,
    Object,
    Event: class { constructor(t) { this.type = t; } },
    CustomEvent: windowObj.CustomEvent,
  });

  await new Promise((r) => setTimeout(r, 10));

  windowObj.StudyAccount.openAuthModal();
  const userInput = elements.get('account-username');
  const pwdInput = elements.get('account-password');
  const errEl = elements.get('account-auth-error');

  // Short password (< 12)
  userInput.value = 'student123';
  pwdInput.value = 'shortpwd';
  elements.get('btn-account-login').dispatchEvent({ type: 'click' });

  assert.equal(errEl.textContent.includes('12 ký tự'), true);
  assert.equal(fetchCalls.some((c) => c.url === '/api/account/login'), false);

  // Correct password (>= 12)
  pwdInput.value = 'valid-password-12345';
  elements.get('btn-account-login').dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(fetchCalls.some((c) => c.url === '/api/account/login'), true);
  assert.equal(trigger.textContent, '@student123');
  assert.equal(trigger.getAttribute('data-authenticated'), 'true');

  const changeEvt = dispatchedEvents.find((e) => e.type === 'study:account-changed');
  assert.equal(Boolean(changeEvt), true);
  assert.equal(changeEvt.detail.account.id, 'test_account_uuid');
  assert.equal(changeEvt.detail.account.username, 'student123');
});

test('accounts.js: Escape key dismisses open auth modal', async () => {
  const { document, elements } = createDOM();

  const mockFetch = async () => ({
    ok: true,
    json: async () => ({ account: null, required: false, registration_allowed: true }),
  });

  const windowObj = {
    addEventListener: () => {},
    dispatchEvent: () => {},
    CustomEvent: class { constructor(t, d) { this.type = t; this.detail = d?.detail; } },
  };

  const script = fs.readFileSync(path.join(__dirname, '../static/accounts.js'), 'utf8');
  vm.runInNewContext(script, {
    document,
    window: windowObj,
    fetch: mockFetch,
    console,
    Promise,
    Object,
    Event: class { constructor(t) { this.type = t; } },
    CustomEvent: windowObj.CustomEvent,
  });

  await new Promise((r) => setTimeout(r, 10));

  windowObj.StudyAccount.openAuthModal();
  const modal = elements.get('account-auth-modal');
  assert.equal(modal.classList.contains('is-open'), true);

  document.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });
  assert.equal(modal.classList.contains('is-open'), false);
});

test('accounts.js: injected logout failure retains state, displays readable error, and does NOT reload', async () => {
  const { document, elements } = createDOM();
  let reloadCalls = 0;

  const mockFetch = async (url) => {
    if (url === '/api/account/status') {
      return {
        ok: true,
        json: async () => ({
          account: { id: 'qa_user_uuid_456', username: 'qa_learner_2026' },
          required: false,
          registration_allowed: true,
        }),
      };
    }
    if (url === '/api/account/logout') {
      // Injected simulated failure on logout
      const err = new Error('Simulated network failure on logout');
      err.status = 500;
      throw err;
    }
    if (url.startsWith('/api/account/features/')) {
      return { ok: true, json: async () => ({ first_use: false }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const windowObj = {
    addEventListener: () => {},
    dispatchEvent: () => {},
    location: {
      reload: () => {
        reloadCalls++;
      },
    },
    CustomEvent: class { constructor(t, d) { this.type = t; this.detail = d?.detail; } },
  };

  const trigger = document.createElement('button');
  trigger.id = 'btn-account-trigger';

  const script = fs.readFileSync(path.join(__dirname, '../static/accounts.js'), 'utf8');
  vm.runInNewContext(script, {
    document,
    window: windowObj,
    fetch: mockFetch,
    console,
    Promise,
    Object,
    Event: class { constructor(t) { this.type = t; } },
    CustomEvent: windowObj.CustomEvent,
  });

  await new Promise((r) => setTimeout(r, 15));

  // Initial state should be authenticated
  assert.equal(trigger.textContent, '@qa_learner_2026');
  assert.equal(trigger.getAttribute('data-authenticated'), 'true');
  assert.equal(windowObj.StudyAccount.getAccount()?.username, 'qa_learner_2026');

  // Trigger logout with simulated network failure
  const logoutBtn = elements.get('btn-account-logout');
  assert.equal(Boolean(logoutBtn), true);
  logoutBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 15));

  // Assert NO false identity clear and NO false reload
  assert.equal(reloadCalls, 0, 'Must NOT reload on failed logout');
  assert.equal(windowObj.StudyAccount.getAccount()?.username, 'qa_learner_2026', 'Identity must remain intact');
  assert.equal(trigger.textContent, '@qa_learner_2026', 'Trigger button must retain username');
  assert.equal(trigger.getAttribute('data-authenticated'), 'true');

  // Assert readable error displayed
  const popoverErr = elements.get('account-popover-error');
  assert.equal(popoverErr.textContent.includes('Simulated network failure on logout'), true);
  assert.equal(popoverErr.style.display, 'block');
});

test('accounts.js: successful login and logout trigger full page reload to isolate scope', async () => {
  const { document, elements } = createDOM();
  let reloadCalls = 0;

  let currentServerSession = null;

  const mockFetch = async (url) => {
    if (url === '/api/account/status') {
      return {
        ok: true,
        json: async () => ({ account: currentServerSession, required: false, registration_allowed: true }),
      };
    }
    if (url === '/api/account/login') {
      currentServerSession = { id: 'succ_uuid_789', username: 'successful_student' };
      return {
        ok: true,
        json: async () => ({ account: currentServerSession }),
      };
    }
    if (url === '/api/account/logout') {
      currentServerSession = null;
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (url.startsWith('/api/account/features/')) {
      return { ok: true, json: async () => ({ first_use: false }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const windowObj = {
    addEventListener: () => {},
    dispatchEvent: () => {},
    location: {
      reload: () => {
        reloadCalls++;
      },
    },
    CustomEvent: class { constructor(t, d) { this.type = t; this.detail = d?.detail; } },
  };

  const trigger = document.createElement('button');
  trigger.id = 'btn-account-trigger';

  const script = fs.readFileSync(path.join(__dirname, '../static/accounts.js'), 'utf8');
  vm.runInNewContext(script, {
    document,
    window: windowObj,
    fetch: mockFetch,
    console,
    Promise,
    Object,
    Event: class { constructor(t) { this.type = t; } },
    CustomEvent: windowObj.CustomEvent,
  });

  await new Promise((r) => setTimeout(r, 15));

  // Perform successful login
  windowObj.StudyAccount.openAuthModal();
  elements.get('account-username').value = 'successful_student';
  elements.get('account-password').value = 'strong-password-12345';
  elements.get('btn-account-login').dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 15));

  assert.equal(reloadCalls, 1, 'Successful login must trigger page reload');

  // Perform successful logout
  elements.get('btn-account-logout').dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 15));

  assert.equal(reloadCalls, 2, 'Successful logout must trigger page reload');
});

test('onboarding-tour.js: inactive tour is hidden/noninteractive; finish and Escape release and restore focus', async () => {
  const { document, elements } = createDOM();

  // Create mock target elements for tour steps
  const sidebarBtn = document.createElement('button');
  sidebarBtn.id = 'btn-open-sidebar';
  sidebarBtn.getBoundingClientRect = () => ({ top: 10, left: 10, width: 40, height: 32 });
  document.body.appendChild(sidebarBtn);

  const newChatBtn = document.createElement('button');
  newChatBtn.id = 'btn-clear-chat';
  newChatBtn.getBoundingClientRect = () => ({ top: 10, left: 60, width: 40, height: 32 });
  document.body.appendChild(newChatBtn);

  const tourReplayBtn = document.createElement('button');
  tourReplayBtn.id = 'btn-replay-tour';
  tourReplayBtn.getBoundingClientRect = () => ({ top: 10, left: 110, width: 40, height: 32 });
  document.body.appendChild(tourReplayBtn);

  const windowObj = {
    innerWidth: 1024,
    innerHeight: 768,
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  const script = fs.readFileSync(path.join(__dirname, '../static/onboarding-tour.js'), 'utf8');
  vm.runInNewContext(script, {
    document,
    window: windowObj,
    console,
    Promise,
    Object,
    Event: class { constructor(t) { this.type = t; } },
  });

  // Focus the tour replay button before starting
  tourReplayBtn.focus();
  assert.equal(document.activeElement, tourReplayBtn);

  // Start tour
  const started = windowObj.ARYMOnboardingTour.start();
  assert.equal(started, true);

  const overlay = elements.get('arym-tour-overlay');
  assert.equal(overlay.classList.contains('is-active'), true);
  assert.equal(overlay.style.display, '');
  assert.equal(overlay.getAttribute('aria-hidden'), null);

  // Press Escape to dismiss tour
  document.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {} });

  // Inactive tour must be fully hidden and noninteractive
  assert.equal(overlay.classList.contains('is-active'), false);
  assert.equal(overlay.style.display, 'none');
  assert.equal(overlay.getAttribute('aria-hidden'), 'true');

  // Focus must be restored to tour replay button
  assert.equal(document.activeElement, tourReplayBtn);

  // Replay tour works
  const replayed = windowObj.ARYMOnboardingTour.start();
  assert.equal(replayed, true);
  assert.equal(overlay.classList.contains('is-active'), true);
  assert.equal(overlay.style.display, '');

  // Skip / close button also dismisses cleanly
  elements.get('arym-tour-skip').dispatchEvent({ type: 'click' });
  assert.equal(overlay.classList.contains('is-active'), false);
  assert.equal(overlay.style.display, 'none');
  assert.equal(overlay.getAttribute('aria-hidden'), 'true');
});
