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
  const statusUrls = [];

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
      if (url === "/api/status?summary=1" || url === "/api/status") {
        statusCalls += 1;
        statusUrls.push(String(url));
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
  assert.deepEqual(statusUrls, ["/api/status?summary=1"]);
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

test("app.js skips hidden polls, refreshes on visibility reveal, caps in-flight polls to one, and preserves explicit refresh", { timeout: 5000 }, async () => {
  const { elements, docEvents, document } = makeFakeDom();
  document.visibilityState = "hidden";
  document.hidden = true;
  const intervalFns = [];
  const statusResolvers = [];
  let summaryCalls = 0;
  let inFlightNow = 0;
  let maxConcurrent = 0;

  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
  const context = {
    document,
    window: {
      addEventListener() {},
      dispatchEvent() {},
      StudyAccount: {
        canAccessProtectedApis: () => true,
        whenReady: async () => ({ account: { id: "u1", username: "student" }, required: true, registration_allowed: false }),
      },
    },
    confirm: () => true,
    fetch: (url) => {
      if (url === "/api/status?summary=1") {
        summaryCalls += 1;
        inFlightNow += 1;
        maxConcurrent = Math.max(maxConcurrent, inFlightNow);
        return new Promise((resolve) => {
          statusResolvers.push((docsCount) => {
            inFlightNow -= 1;
            resolve({
              ok: true,
              status: 200,
              json: async () => ({
                inference_ready: true,
                knowledge_base: {
                  searchable_documents: docsCount,
                  registered_documents: docsCount,
                  total_documents: docsCount,
                  discovered_files: docsCount,
                  total_chunks: 10,
                  embedded_chunks: 10,
                },
                storage: { watcher: { enabled: false } },
              }),
            });
          });
        });
      }
      if (url === "/api/model/check") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ ready: true, message: "OK" }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ items: [], models: [], default_model: "local", default_model_available: true }),
      });
    },
    localStorage: store,
    sessionStorage: store,
    setInterval(fn) {
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
  };

  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), context);
  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));

  // Initial hidden tab must not poll; interval ticks while hidden must also skip.
  assert.equal(summaryCalls, 0);
  await intervalFns[0]();
  await intervalFns[0]();
  assert.equal(summaryCalls, 0, "Hidden tab must not poll /api/status?summary=1");

  // Reveal tab -> triggers one immediate summary poll.
  document.visibilityState = "visible";
  document.hidden = false;
  for (const fn of docEvents.visibilitychange || []) fn({ type: "visibilitychange" });
  await new Promise((r) => setImmediate(r));
  assert.equal(summaryCalls, 1, "Becoming visible must trigger a status refresh");

  // While poll #1 is in flight, interval ticks and visibilitychange must not open a second concurrent poll.
  const p2 = intervalFns[0]();
  const p3 = intervalFns[0]();
  for (const fn of docEvents.visibilitychange || []) fn({ type: "visibilitychange" });
  assert.equal(summaryCalls, 1, "At most one summary poll may be in flight at a time");
  assert.equal(maxConcurrent, 1);

  // Explicit model check while poll #1 is in flight queues one follow-up refresh without concurrent overlap.
  const modelCheckBtn = elements.get("btn-model-check");
  const modelCheckPromise = modelCheckBtn.listeners.click[0]({ preventDefault() {} });
  await new Promise((r) => setImmediate(r));
  assert.equal(summaryCalls, 1, "Explicit refresh must wait for in-flight poll instead of launching a concurrent poll");

  statusResolvers[0](2);
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(summaryCalls, 2, "Queued explicit refresh runs sequentially after in-flight poll completes");
  assert.equal(maxConcurrent, 1);
  assert.equal(elements.get("stat-docs").textContent, 2);
  statusResolvers[1](5);
  await Promise.all([p2, p3, modelCheckPromise]);
  assert.equal(elements.get("stat-docs").textContent, 5);
});

test("app.js ignores stale in-flight status responses across account transitions", { timeout: 5000 }, async () => {
  const { elements, docEvents, document } = makeFakeDom();
  const winListeners = {};
  const deferredResponses = [];
  let signedInAccount = { id: "user_a", username: "alice" };
  let activeSummaryCount = 0;
  let peakSummaryCount = 0;
  let abortedSummaryCount = 0;

  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
  const windowObj = {
    addEventListener(evt, fn) {
      winListeners[evt] = winListeners[evt] || [];
      winListeners[evt].push(fn);
    },
    dispatchEvent(evt) {
      for (const fn of winListeners[evt.type] || []) fn(evt);
    },
    StudyAccount: {
      canAccessProtectedApis: () => Boolean(signedInAccount),
      getConfig: () => ({ required: true, registration_allowed: false }),
      getAccount: () => signedInAccount,
      whenReady: async () => ({ account: signedInAccount, required: true, registration_allowed: false }),
    },
  };

  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), {
    document,
    window: windowObj,
    confirm: () => true,
    AbortController,
    fetch: (url, opts) => {
      if (url === "/api/status?summary=1") {
        if (opts?.signal?.aborted) {
          return Promise.reject(opts.signal.reason || new Error("Aborted"));
        }
        activeSummaryCount += 1;
        peakSummaryCount = Math.max(peakSummaryCount, activeSummaryCount);
        return new Promise((resolve, reject) => {
          let settled = false;
          const settle = (cb, value) => {
            if (settled) return;
            settled = true;
            activeSummaryCount -= 1;
            cb(value);
          };
          deferredResponses.push((payload) => settle(resolve, payload));
          opts?.signal?.addEventListener("abort", () => {
            abortedSummaryCount += 1;
            settle(reject, opts.signal.reason || new Error("Aborted"));
          }, { once: true });
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ items: [], models: [], default_model: "local", default_model_available: true }),
      });
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
  });

  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(deferredResponses.length, 1, "Initial Account A poll started");
  assert.equal(activeSummaryCount, 1);

  // Switch to Account B before Account A's slow poll returns: Account A is aborted before Account B polls.
  signedInAccount = { id: "user_b", username: "bob" };
  windowObj.dispatchEvent({
    type: "study:account-changed",
    detail: { account: signedInAccount, scope: "user_b", required: true, authorized: true },
  });
  assert.equal(peakSummaryCount, 1, "Account transition must never exceed one active summary HTTP request");
  assert.equal(abortedSummaryCount, 1, "Previous account summary request must be aborted");
  await new Promise((r) => setImmediate(r));
  assert.equal(deferredResponses.length, 2, "Account B immediately initiates its own summary poll");
  assert.equal(peakSummaryCount, 1);

  // Resolve Account B with 7 docs, and ensure any late Account A call cannot overwrite Account B UI.
  deferredResponses[1]({
    ok: true,
    status: 200,
    json: async () => ({
      inference_ready: true,
      knowledge_base: { searchable_documents: 7, registered_documents: 7, total_documents: 7, total_chunks: 14, embedded_chunks: 14 },
      storage: { watcher: { enabled: false } },
    }),
  });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(elements.get("stat-docs").textContent, 7);

  deferredResponses[0]({
    ok: true,
    status: 200,
    json: async () => ({
      inference_ready: false,
      service_message: "Stale account A error",
      knowledge_base: { searchable_documents: 99, registered_documents: 99, total_documents: 99, total_chunks: 100, embedded_chunks: 0 },
      storage: { watcher: { enabled: false } },
    }),
  });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(elements.get("stat-docs").textContent, 7, "Stale Account A response must not overwrite Account B UI");
});

test("app.js serializes non-aborting account transitions, discards stale responses, and preserves new account explicit refresh", { timeout: 5000 }, async () => {
  const { elements, docEvents, document } = makeFakeDom();
  const winListeners = {};
  const deferredSummary = [];
  let signedInAccount = { id: "user_a", username: "alice" };
  let activeSummaryCount = 0;
  let peakSummaryCount = 0;

  const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
  const windowObj = {
    addEventListener(evt, fn) {
      winListeners[evt] = winListeners[evt] || [];
      winListeners[evt].push(fn);
    },
    dispatchEvent(evt) {
      for (const fn of winListeners[evt.type] || []) fn(evt);
    },
    StudyAccount: {
      canAccessProtectedApis: () => Boolean(signedInAccount),
      getConfig: () => ({ required: true, registration_allowed: false }),
      getAccount: () => signedInAccount,
      whenReady: async () => ({ account: signedInAccount, required: true, registration_allowed: false }),
    },
  };

  // Run without AbortController so the old HTTP flight finishes normally after account transition.
  vm.runInNewContext(fs.readFileSync(APP_JS_PATH, "utf8"), {
    document,
    window: windowObj,
    confirm: () => true,
    fetch: (url) => {
      if (url === "/api/status?summary=1") {
        activeSummaryCount += 1;
        peakSummaryCount = Math.max(peakSummaryCount, activeSummaryCount);
        return new Promise((resolve) => {
          deferredSummary.push((payload) => {
            activeSummaryCount -= 1;
            resolve(payload);
          });
        });
      }
      if (url === "/api/model/check") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ ready: true, message: "OK" }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ items: [], models: [], default_model: "local", default_model_available: true }),
      });
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
  });

  for (const fn of docEvents.DOMContentLoaded || []) await fn();
  await new Promise((r) => setImmediate(r));
  assert.equal(deferredSummary.length, 1);

  // Switch to Account B while Account A is still active: must NOT open a concurrent 2nd HTTP request.
  signedInAccount = { id: "user_b", username: "bob" };
  windowObj.dispatchEvent({
    type: "study:account-changed",
    detail: { account: signedInAccount, scope: "user_b", required: true, authorized: true },
  });
  await new Promise((r) => setImmediate(r));
  assert.equal(peakSummaryCount, 1, "Must keep at most one active summary HTTP request across account switch");
  assert.equal(deferredSummary.length, 1, "Account B waits for Account A completion when transport cannot abort");

  // Trigger an explicit refresh (model check) for Account B BEFORE Account A finishes.
  const modelCheckPromise = elements.get("btn-model-check").listeners.click[0]({ preventDefault() {} });
  await new Promise((r) => setImmediate(r));

  // Complete Account A with stale 99 docs: must be discarded and its finally must NOT clear Account B's queued refresh.
  deferredSummary[0]({
    ok: true,
    status: 200,
    json: async () => ({
      inference_ready: false,
      service_message: "Stale account A error",
      knowledge_base: { searchable_documents: 99, registered_documents: 99, total_documents: 99, total_chunks: 100, embedded_chunks: 0 },
      storage: { watcher: { enabled: false } },
    }),
  });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));

  assert.notEqual(elements.get("stat-docs").textContent, 99, "Stale Account A payload must be ignored");
  assert.doesNotMatch(elements.get("ollama-status").textContent, /Stale account A error/);
  assert.equal(deferredSummary.length, 2, "Account B queued explicit refresh must run after Account A completes");
  assert.equal(peakSummaryCount, 1);

  // While Account B's first poll is in flight, queue another explicit refresh and verify it also completes cleanly.
  const secondModelCheckPromise = elements.get("btn-model-check").listeners.click[0]({ preventDefault() {} });
  await new Promise((r) => setImmediate(r));

  deferredSummary[1]({
    ok: true,
    status: 200,
    json: async () => ({
      inference_ready: true,
      knowledge_base: { searchable_documents: 7, registered_documents: 7, total_documents: 7, total_chunks: 14, embedded_chunks: 14 },
      storage: { watcher: { enabled: false } },
    }),
  });
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(elements.get("stat-docs").textContent, 7);
  assert.equal(deferredSummary.length, 3, "Queued explicit refresh during Account B poll must still execute");

  deferredSummary[2]({
    ok: true,
    status: 200,
    json: async () => ({
      inference_ready: true,
      knowledge_base: { searchable_documents: 8, registered_documents: 8, total_documents: 8, total_chunks: 16, embedded_chunks: 16 },
      storage: { watcher: { enabled: false } },
    }),
  });
  await Promise.all([modelCheckPromise, secondModelCheckPromise]);
  assert.equal(elements.get("stat-docs").textContent, 8);
});
