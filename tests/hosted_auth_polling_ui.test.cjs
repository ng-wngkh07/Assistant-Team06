const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP_JS_PATH = path.resolve(__dirname, "..", "static", "app.js");
const ACCOUNTS_JS_PATH = path.resolve(__dirname, "..", "static", "accounts.js");

function makeFakeDom() {
  const elements = new Map();
  const docEvents = {};

  function makeEl(id = "") {
    const el = {
      id,
      textContent: "",
      innerHTML: "",
      value: "",
      hidden: false,
      disabled: false,
      className: "",
      style: {},
      dataset: {},
      children: [],
      attributes: {},
      listeners: {},
      classList: {
        _set: new Set(),
        add(...xs) { xs.forEach((x) => this._set.add(x)); },
        remove(...xs) { xs.forEach((x) => this._set.delete(x)); },
        toggle(x, f) {
          if (f === undefined) {
            if (this._set.has(x)) { this._set.delete(x); return false; }
            this._set.add(x); return true;
          }
          if (f) this._set.add(x); else this._set.delete(x);
          return f;
        },
        contains(x) { return this._set.has(x); },
      },
      addEventListener(evt, fn) {
        this.listeners[evt] = this.listeners[evt] || [];
        this.listeners[evt].push(fn);
      },
      appendChild(child) { this.children.push(child); return child; },
      append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = [...nodes]; },
      setAttribute(k, v) { this.attributes[k] = String(v); },
      getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
      hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k); },
      insertAdjacentElement() {},
      querySelector() { return makeEl(); },
      querySelectorAll() { return []; },
      focus() {},
      closest() { return null; },
      contains() { return false; },
    };
    if (id) elements.set(id, el);
    return el;
  }

  for (const id of [
    "chat-form", "user-input", "messages-container", "btn-send", "btn-new-chat",
    "history-list", "stat-docs", "stat-pages", "stat-chunks", "stat-scanned",
    "ollama-status", "btn-open-kb", "kb-modal", "btn-close-kb", "kb-table-body",
    "btn-reindex", "reindex-modal", "reindex-msg", "reindex-spinner", "btn-close-reindex",
    "btn-model-check", "model-check-result", "btn-account-trigger", "btn-replay-tour",
  ]) {
    makeEl(id);
  }

  const document = {
    readyState: "complete",
    body: makeEl("body"),
    documentElement: makeEl("html"),
    head: makeEl("head"),
    getElementById: (id) => elements.get(id) || makeEl(id),
    createElement: (tag) => makeEl(),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(evt, fn) {
      docEvents[evt] = docEvents[evt] || [];
      docEvents[evt].push(fn);
    },
    dispatchEvent() {},
    contains() { return false; },
  };
  return { elements, docEvents, document, makeEl };
}

test("app.js pauses 5s /api/status polling after 401 and avoids scary HTTP 401 red error", async () => {
  const { elements, docEvents, document } = makeFakeDom();
  const winListeners = {};
  const intervalFns = [];
  let statusCalls = 0;

  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
  const windowObj = {
    addEventListener(evt, fn) {
      winListeners[evt] = winListeners[evt] || [];
      winListeners[evt].push(fn);
    },
    dispatchEvent() {},
  };

  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), {
    document,
    window: windowObj,
    confirm: () => true,
    fetch: async (url) => {
      if (url === "/api/status") {
        statusCalls += 1;
        return { ok: false, status: 401, json: async () => ({ detail: "Vui lòng đăng nhập" }) };
      }
      if (url === "/api/models") {
        return { ok: false, status: 401, json: async () => ({ detail: "Vui lòng đăng nhập" }) };
      }
      if (url.startsWith("/api/sessions")) {
        return { ok: false, status: 401, json: async () => ({ detail: "Vui lòng đăng nhập" }) };
      }
      if (url === "/api/history") {
        throw new Error("Should not fall back to /api/history on 401");
      }
      return { ok: true, status: 200, json: async () => ({ items: [] }) };
    },
    localStorage: store,
    sessionStorage: store,
    setInterval(fn, _ms) {
      intervalFns.push(fn);
      return intervalFns.length;
    },
    clearInterval() {},
    setTimeout() {},
    clearTimeout() {},
    Event: class { constructor(t) { this.type = t; } preventDefault() {} },
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    console: { log() {}, warn() {}, error() {} },
  });

  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(statusCalls, 1, "Initial /api/status should execute once");
  assert.doesNotMatch(elements.get("ollama-status").textContent, /HTTP 401|lỗi/i);
  assert.match(elements.get("ollama-status").textContent, /Đăng nhập/i);

  assert.ok(intervalFns.length >= 1, "Status poll interval should be registered");
  for (let i = 0; i < 5; i += 1) {
    await intervalFns[0]();
  }
  assert.equal(statusCalls, 1, "Status poll must stop hitting /api/status after 401");
});

test("accounts.js exposes whenReady and prevents unauthenticated initial /api/status & /api/sessions calls", async () => {
  const { elements, docEvents, document } = makeFakeDom();
  const winListeners = {};
  const calledUrls = [];
  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };

  const context = {
    document,
    window: {
      addEventListener(evt, fn) {
        winListeners[evt] = winListeners[evt] || [];
        winListeners[evt].push(fn);
      },
      dispatchEvent(evt) {
        for (const fn of winListeners[evt.type] || []) fn(evt);
      },
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
    confirm: () => true,
    fetch: async (url) => {
      calledUrls.push(String(url));
      if (url === "/api/account/status") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ account: null, required: true, registration_allowed: false }),
        };
      }
      return { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) };
    },
    localStorage: store,
    sessionStorage: store,
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() {},
    clearTimeout() {},
    Event: class { constructor(t) { this.type = t; } preventDefault() {} },
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    console: { log() {}, warn() {}, error() {} },
  };
  context.window.document = document;

  vm.runInNewContext(fs.readFileSync(ACCOUNTS_JS_PATH, "utf8"), context);
  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), context);

  assert.equal(typeof context.window.StudyAccount?.whenReady, "function", "StudyAccount.whenReady must exist");
  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 15));

  assert.deepEqual(calledUrls, ["/api/account/status"], `Unexpected unauthenticated API calls: ${calledUrls.join(", ")}`);
  assert.match(elements.get("ollama-status").textContent, /Đăng nhập/i);
});

test("document-lookup, practice, timetable, and planner-assistant suppress unauthenticated startup calls and refresh on login", async () => {
  const { elements, docEvents, document } = makeFakeDom();
  const winListeners = {};
  const calledUrls = [];
  let signedIn = false;
  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };

  const context = {
    document,
    window: {
      addEventListener(evt, fn) {
        winListeners[evt] = winListeners[evt] || [];
        winListeners[evt].push(fn);
      },
      dispatchEvent(evt) {
        for (const fn of winListeners[evt.type] || []) fn(evt);
      },
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
    confirm: () => true,
    fetch: async (url) => {
      calledUrls.push(String(url));
      if (url === "/api/account/status") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            account: signedIn ? { id: "u1", username: "student" } : null,
            required: true,
            registration_allowed: false,
          }),
        };
      }
      if (!signedIn) {
        return { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) };
      }
      if (url === "/api/documents") {
        return { ok: true, status: 200, json: async () => ({ documents: [{ id: 1, clean_title: "Book A" }] }) };
      }
      if (url === "/api/learning/topics") {
        return { ok: true, status: 200, json: async () => ({ topics: [{ id: "t1", label: "Topic 1" }] }) };
      }
      if (url === "/api/timetable") {
        return { ok: true, status: 200, json: async () => ({ entries: [] }) };
      }
      if (url === "/api/timetable/planner/state") {
        return { ok: true, status: 200, json: async () => ({ known_courses: [], routines: [], tasks: [] }) };
      }
      if (url === "/api/timetable/planner/weekly-lessons") {
        return { ok: true, status: 200, json: async () => ({ lessons: [] }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
    localStorage: store,
    sessionStorage: store,
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() {},
    clearTimeout() {},
    Event: class { constructor(t) { this.type = t; } preventDefault() {} },
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    console: { log() {}, warn() {}, error() {} },
  };
  context.window.document = document;

  for (const fname of [
    "accounts.js",
    "document-lookup.js",
    "practice.js",
    "timetable.js",
    "planner-assistant.js",
  ]) {
    const filePath = path.resolve(__dirname, "..", "static", fname);
    vm.runInNewContext(fs.readFileSync(filePath, "utf8"), context);
  }

  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 20));

  assert.deepEqual(
    calledUrls,
    ["/api/account/status"],
    `Expected only /api/account/status before login, got: ${calledUrls.join(", ")}`
  );
  assert.equal(context.window.StudyAccount.canAccessProtectedApis(), false);

  // Also ensure study:account-changed with account=null does not fire protected fetches
  context.window.dispatchEvent(new context.CustomEvent("study:account-changed", { detail: { account: null, scope: "local" } }));
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 15));
  assert.deepEqual(calledUrls, ["/api/account/status"]);
});

test("app.js does not fall back to /api/history when /api/sessions returns 401 and accounts.js blocks protected polling during logout", async () => {
  const { elements, docEvents, document, makeEl } = makeFakeDom();
  makeEl("btn-account-logout");
  makeEl("btn-account-switch");
  makeEl("btn-account-cancel");
  makeEl("btn-account-login");
  makeEl("btn-account-register");
  const origGetById = document.getElementById;
  document.getElementById = (id) => (id === "account-ui-styles" ? null : origGetById(id));
  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
  let sessionReqCount = 0;
  let logoutStarted = false;
  const calledUrls = [];

  const winListeners = {};
  const context = {
    document,
    console: { log() {}, warn() {}, error() {} },
    alert() {},
    confirm: () => true,
    Event: class { constructor(t) { this.type = t; } preventDefault() {} },
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    localStorage: store,
    sessionStorage: store,
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() {},
    clearTimeout() {},
    window: {
      location: { reload() {} },
      addEventListener(type, fn) {
        winListeners[type] = winListeners[type] || [];
        winListeners[type].push(fn);
      },
      dispatchEvent(evt) {
        for (const fn of winListeners[evt.type] || []) fn(evt);
      },
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
    fetch: async (url) => {
      const u = String(url);
      calledUrls.push(u);
      if (u === "/api/account/status") {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            account: { id: "u1", username: "student" },
            required: true,
            registration_allowed: false,
          }),
        };
      }
      if (u === "/api/account/logout") {
        logoutStarted = true;
        await new Promise((r) => setTimeout(r, 15));
        return { ok: true, status: 200, json: async () => ({ status: "ok" }) };
      }
      if (u === "/api/sessions?limit=1") {
        return { ok: true, status: 200, json: async () => ({ items: [] }) };
      }
      if (u.startsWith("/api/sessions?limit=50")) {
        sessionReqCount += 1;
        return { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) };
      }
      if (u === "/api/history") {
        return { ok: false, status: 401, json: async () => ({ detail: "Unauthorized" }) };
      }
      return { ok: true, status: 200, json: async () => ({ documents: [], models: [], entries: [], lessons: [], known_courses: [], routines: [], tasks: [] }) };
    },
  };
  context.window.document = document;

  vm.runInNewContext(fs.readFileSync(ACCOUNTS_JS_PATH, "utf8"), context);
  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), context);

  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 25));

  assert.equal(sessionReqCount, 1, "Initial loadSessions ran once");
  assert.ok(!calledUrls.includes("/api/history"), `Unexpected fallback to /api/history after 401: ${calledUrls.join(", ")}`);

  const logoutBtn = elements.get("btn-account-logout");
  for (const fn of logoutBtn?.listeners?.click || []) fn({ preventDefault() {} });
  assert.equal(logoutStarted, true);
  assert.equal(context.window.StudyAccount.canAccessProtectedApis(), false, "canAccessProtectedApis must return false immediately once logout begins");
  await new Promise((r) => setTimeout(r, 25));
});

test("app.js guards /api/model/check and /api/chat/stream when unauthenticated without clearing draft", async () => {
  const { elements, docEvents, document } = makeFakeDom();
  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
  const calledUrls = [];
  let modalOpened = 0;

  const context = {
    document,
    console: { log() {}, warn() {}, error() {} },
    alert() {},
    confirm: () => true,
    Event: class { constructor(t) { this.type = t; } preventDefault() {} },
    CustomEvent: class { constructor(t, init) { this.type = t; this.detail = init?.detail; } },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    localStorage: store,
    sessionStorage: store,
    setInterval() { return 1; },
    clearInterval() {},
    setTimeout() {},
    clearTimeout() {},
    window: {
      location: { reload() {} },
      addEventListener() {},
      dispatchEvent() {},
      matchMedia: () => ({ matches: false, addEventListener() {} }),
      StudyAccount: {
        canAccessProtectedApis: () => false,
        whenReady: async () => ({ account: null, required: true, registration_allowed: false }),
        openAuthModal: () => { modalOpened += 1; },
      },
    },
    fetch: async (url) => {
      calledUrls.push(String(url));
      return { ok: true, status: 200, json: async () => ({ models: [] }) };
    },
  };
  context.window.document = document;
  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), context);

  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));

  for (const fn of elements.get("btn-model-check").listeners.click || []) await fn({ preventDefault() {} });
  assert.ok(!calledUrls.includes("/api/model/check"), `Unauthenticated #btn-model-check must not POST: ${calledUrls}`);
  assert.ok(modalOpened >= 1);

  elements.get("user-input").value = "Câu hỏi chưa đăng nhập";
  for (const fn of elements.get("chat-form").listeners.submit || []) await fn({ preventDefault() {} });
  assert.ok(!calledUrls.includes("/api/chat/stream"), `Unauthenticated submit must not POST: ${calledUrls}`);
  assert.equal(elements.get("user-input").value, "Câu hỏi chưa đăng nhập");
  assert.ok(modalOpened >= 2);
});
