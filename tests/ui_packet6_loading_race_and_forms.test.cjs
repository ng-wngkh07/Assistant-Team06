const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function createHarness() {
  class MockElement {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase();
      this.listeners = {};
      this.style = {};
      this.value = '';
      this._innerHTML = '';
      this.textContent = '';
      this.children = [];
      this.attributes = {};
      this.hidden = false;
      this.disabled = false;
      const classes = new Set();
      this.classList = {
        add: (...xs) => xs.forEach((x) => classes.add(x)),
        remove: (...xs) => xs.forEach((x) => classes.delete(x)),
        toggle: (x, force) => {
          if (force === undefined) {
            if (classes.has(x)) {
              classes.delete(x);
              return false;
            }
            classes.add(x);
            return true;
          }
          if (force) classes.add(x);
          else classes.delete(x);
          return force;
        },
        contains: (x) => classes.has(x),
      };
    }
    get innerHTML() {
      return this._innerHTML;
    }
    set innerHTML(html) {
      this._innerHTML = html;
      this.children = [];
      const tagMatches = [...html.matchAll(/<(?:button|div|span|td|tr|p|label)[^>]*>/gi)];
      for (const m of tagMatches) {
        const tagStr = m[0];
        const tagNameMatch = tagStr.match(/^<([a-z0-9]+)/i);
        const tag = tagNameMatch ? tagNameMatch[1] : 'div';
        const el = new MockElement(tag);

        const classMatch = tagStr.match(/class="([^"]+)"/i);
        if (classMatch) {
          classMatch[1].split(/\s+/).filter(Boolean).forEach((c) => el.classList.add(c));
        }
        const fnMatch = tagStr.match(/data-filename="([^"]+)"/i);
        if (fnMatch) el.setAttribute('data-filename', fnMatch[1]);

        const idMatch = tagStr.match(/id="([^"]+)"/i);
        if (idMatch) {
          el.id = idMatch[1];
          elements.set(idMatch[1], el);
        }
        this.appendChild(el);
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
      const type = typeof e === 'string' ? e : e.type;
      const eventObj = typeof e === 'string' ? { type } : e;
      for (const fn of this.listeners[type] || []) fn(eventObj);
      return true;
    }
    remove() {
      if (this.parentElement) {
        this.parentElement.children = this.parentElement.children.filter((c) => c !== this);
      }
    }
    setAttribute(k, v) {
      this.attributes[k] = String(v);
      this[k] = v;
    }
    getAttribute(k) {
      return this.attributes[k] ?? this[k] ?? null;
    }
    appendChild(x) {
      x.parentElement = this;
      this.children.push(x);
      return x;
    }
    append(...xs) {
      for (const x of xs) {
        if (typeof x === 'string') {
          const t = new MockElement('#text');
          t.textContent = x;
          this.appendChild(t);
        } else if (x) {
          this.appendChild(x);
        }
      }
    }
    insertAdjacentElement(where, el) {
      if (el.id) elements.set(el.id, el);
      return el;
    }
    replaceChildren(...xs) {
      this.children = [];
      this.append(...xs);
    }
    focus() {
      document.activeElement = this;
    }
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    }
    querySelectorAll(sel) {
      const res = [];
      const walk = (n) => {
        for (const c of n.children) {
          if (sel.startsWith('.') && c.classList.contains(sel.slice(1))) res.push(c);
          else if (sel.startsWith('#') && c.id === sel.slice(1)) res.push(c);
          else if (sel.toLowerCase() === c.tagName.toLowerCase()) res.push(c);
          walk(c);
        }
      };
      walk(this);
      return res;
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

  const elements = new Map();
  const documentEvents = {};
  const alerts = [];

  const document = {
    activeElement: null,
    getElementById(id) {
      if (!elements.has(id)) {
        const el = new MockElement('div');
        el.id = id;
        elements.set(id, el);
      }
      return elements.get(id);
    },
    createElement(tag) {
      return new MockElement(tag);
    },
    addEventListener(name, fn) {
      (documentEvents[name] ||= []).push(fn);
    },
    removeEventListener(name, fn) {
      if (!documentEvents[name]) return;
      documentEvents[name] = documentEvents[name].filter((f) => f !== fn);
    },
    querySelectorAll() {
      return [];
    },
    querySelector(sel) {
      if (sel.startsWith('#')) return this.getElementById(sel.slice(1));
      return null;
    },
    dispatchEvent(e) {
      const type = typeof e === 'string' ? e : e.type;
      const eventObj = typeof e === 'string' ? { type, preventDefault() {} } : e;
      if (!eventObj.preventDefault) eventObj.preventDefault = () => {};
      for (const fn of documentEvents[type] || []) fn(eventObj);
      return true;
    },
    body: new MockElement('body'),
  };

  const store = {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
  };

  const inFlightResolvers = new Map();

  const fetchHandler = (url, opts) => {
    if (url === '/api/materials/documents') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          documents: [
            { filename: 'A.txt', clean_title: 'Tài liệu A', total_pages: 5, chunk_count: 2 },
            { filename: 'B.txt', clean_title: 'Tài liệu B', total_pages: 8, chunk_count: 4 },
          ],
        }),
      });
    }
    if (url === '/api/materials/generate-from-library') {
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      const fn = body.filename || 'default';
      return new Promise((resolve, reject) => {
        inFlightResolvers.set(fn, { resolve, reject });
      });
    }
    return Promise.resolve({
      ok: true,
      json: async () => ({ sessions: [], items: [], models: [], knowledge_base: {} }),
    });
  };

  const openedMaterials = [];

  const sandbox = {
    document,
    window: {
      addEventListener() {},
      location: { origin: 'http://localhost' },
      completedMaterialsRegistry: null,
    },
    CSS: { escape: (s) => s },
    confirm: () => true,
    alert: (msg) => alerts.push(msg),
    fetch: fetchHandler,
    localStorage: store,
    sessionStorage: store,
    setInterval() {},
    setTimeout() {},
    clearTimeout() {},
    Event: class {
      constructor(type) {
        this.type = type;
      }
      preventDefault() {}
    },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    console: {
      log() {},
      warn() {},
      error() {},
    },
  };

  const scriptPath = path.join(__dirname, '../static/app.js');
  const code = fs.readFileSync(scriptPath, 'utf8');
  vm.runInNewContext(code, sandbox);

  // Trigger DOMContentLoaded
  for (const fn of documentEvents.DOMContentLoaded || []) fn();

  return {
    elements,
    document,
    alerts,
    openedMaterials,
    window: sandbox.window,
    resolveGeneration: (filename, data, ok = true) => {
      const res = inFlightResolvers.get(filename);
      if (res) {
        inFlightResolvers.delete(filename);
        res.resolve({
          ok,
          json: async () => data,
        });
      }
    },
    rejectGeneration: (filename, err) => {
      const res = inFlightResolvers.get(filename);
      if (res) {
        inFlightResolvers.delete(filename);
        res.reject(err);
      }
    },
  };
}

test('UX04-RACE: deferred A / dismiss / B-fail / A-complete preserves no late popup and reliable reopen', async () => {
  const h = createHarness();
  const inputEl = h.elements.get('user-input');
  inputEl.focus();
  assert.equal(h.document.activeElement, inputEl);

  // 1. Open library picker to generate A.txt
  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const tableBody = h.elements.get('library-picker-table-body');
  const generateBtns = tableBody.querySelectorAll('.btn-generate-from-doc');
  assert.equal(generateBtns.length, 2, 'Should have 2 docs in library');

  // Click generate for A.txt
  generateBtns[0].dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  const loadingModal = h.elements.get('study-loading-modal');
  const studyModal = h.elements.get('study-material-modal');
  assert.equal(loadingModal.style.display, 'flex');
  assert.equal(loadingModal.classList.contains('active'), true);

  // 2. Dismiss waiting for A.txt
  h.elements.get('btn-leave-study-loading').dispatchEvent({ type: 'click' });
  assert.equal(loadingModal.style.display, 'none');
  assert.equal(loadingModal.classList.contains('active'), false);
  assert.equal(h.document.activeElement, inputEl);

  // 3. Start generating B.txt
  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtns2 = h.elements.get('library-picker-table-body').querySelectorAll('.btn-generate-from-doc');
  generateBtns2[1].dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Modal is now showing for B.txt
  assert.equal(loadingModal.style.display, 'flex');
  assert.equal(loadingModal.classList.contains('active'), true);

  // 4. B.txt fails (busy/error)
  h.rejectGeneration('B.txt', new Error('Hệ thống đang bận (503)'));
  await new Promise((r) => setTimeout(r, 20));

  // B's modal is dismissed, alert was shown, status updated
  assert.equal(loadingModal.style.display, 'none');
  assert.equal(loadingModal.classList.contains('active'), false);
  assert.equal(h.alerts.length, 1);
  assert.match(h.alerts[0], /Hệ thống đang bận/i);
  assert.match(h.elements.get('study-tool-status').textContent, /Lỗi tạo bài học cho "B.txt"/i);

  // Focus set to inputEl (not stolen)
  inputEl.focus();
  assert.equal(h.document.activeElement, inputEl);

  // 5. A.txt resolves successfully late
  h.resolveGeneration('A.txt', {
    material: {
      filename: 'A.txt',
      quiz: [{ id: 'qa1', question: 'Q1?', choices: [{ label: 'A', text: '1' }], correct_answer: 'A' }],
      flashcards: [{ front: 'FA', back: 'BA' }],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  // MUST NOT trigger late popup for A.txt
  assert.equal(studyModal.style.display, undefined);
  assert.equal(studyModal.classList.contains('active'), false);
  // Focus MUST NOT be stolen
  assert.equal(h.document.activeElement, inputEl);

  // A.txt must be stored in completedMaterialsRegistry
  const reg = h.window.completedMaterialsRegistry;
  assert.ok(reg && reg.has('A.txt'), 'A.txt should be in completedMaterialsRegistry');

  // Status must contain button to reopen A.txt
  const statusHtml = h.elements.get('study-tool-status').innerHTML;
  assert.match(statusHtml, /Đã tạo xong 1 câu trắc nghiệm/i);
  assert.match(statusHtml, /A\.txt/i);

  const openBtnA = h.elements.get('btn-open-completed-A_txt');
  assert.ok(openBtnA, 'Button to reopen A.txt should exist in status');

  // User manually reopens A.txt
  openBtnA.dispatchEvent({ type: 'click' });
  assert.equal(studyModal.style.display, 'flex');
  assert.equal(studyModal.classList.contains('active'), true);
  assert.equal(h.elements.get('study-material-title').textContent, '📖 Bộ Ôn Luyện: A.txt');
});

test('UX04-RACE: A-dismissed while B-pending: A-complete does NOT hide B modal or steal focus', async () => {
  const h = createHarness();
  const inputEl = h.elements.get('user-input');
  inputEl.focus();

  // 1. Generate A.txt
  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  h.elements.get('library-picker-table-body').querySelectorAll('.btn-generate-from-doc')[0].dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  const loadingModal = h.elements.get('study-loading-modal');
  const studyModal = h.elements.get('study-material-modal');

  // 2. Dismiss waiting for A.txt
  h.elements.get('btn-leave-study-loading').dispatchEvent({ type: 'click' });
  assert.equal(loadingModal.style.display, 'none');

  // 3. Generate B.txt (user explicitly waits in loading modal)
  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  h.elements.get('library-picker-table-body').querySelectorAll('.btn-generate-from-doc')[1].dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  assert.equal(loadingModal.style.display, 'flex');
  assert.equal(loadingModal.classList.contains('active'), true);

  // 4. While B is still pending, A completes!
  h.resolveGeneration('A.txt', {
    material: {
      filename: 'A.txt',
      quiz: [{ id: 'qa1', question: 'Q1?', choices: [{ label: 'A', text: '1' }], correct_answer: 'A' }],
      flashcards: [{ front: 'FA', back: 'BA' }],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  // B's modal MUST REMAIN VISIBLE! A must NOT hide B's modal or open A's study modal!
  assert.equal(loadingModal.style.display, 'flex', "B's loading modal must remain open");
  assert.equal(loadingModal.classList.contains('active'), true);
  assert.equal(studyModal.style.display, undefined, 'A study modal must not open');

  // 5. B completes
  h.resolveGeneration('B.txt', {
    material: {
      filename: 'B.txt',
      quiz: [{ id: 'qb1', question: 'QB1?', choices: [{ label: 'B', text: '2' }], correct_answer: 'B' }],
      flashcards: [{ front: 'FB', back: 'BB' }],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  // Because user was explicitly waiting for B, B's modal is now hidden and B's study modal is opened!
  assert.equal(loadingModal.style.display, 'none');
  assert.equal(studyModal.style.display, 'flex');
  assert.equal(h.elements.get('study-material-title').textContent, '📖 Bộ Ôn Luyện: B.txt');

  // Both A and B are in completedMaterialsRegistry
  const reg = h.window.completedMaterialsRegistry;
  assert.ok(reg.has('A.txt'));
  assert.ok(reg.has('B.txt'));
});

test('UX04-RACE: same-file in-flight dedup, cleanup on failure and successful retry', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelectorAll('.btn-generate-from-doc')[0];

  // First click starts generation for A.txt
  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Duplicate click while in-flight blocked with alert
  generateBtn.dispatchEvent({ type: 'click' });
  assert.equal(h.alerts.length, 1);
  assert.match(h.alerts[0], /Đang xử lý tạo câu hỏi.*Vui lòng chờ hoàn thành/i);

  // A fails
  h.rejectGeneration('A.txt', new Error('Mạng bị ngắt kết nối'));
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(h.elements.get('study-loading-modal').style.display, 'none');

  // Retry after failure succeeds
  h.alerts.length = 0;
  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // No duplicate alert on retry
  assert.equal(h.alerts.length, 0);
  assert.equal(h.elements.get('study-loading-modal').style.display, 'flex');

  // Resolve retry
  h.resolveGeneration('A.txt', {
    material: {
      filename: 'A.txt',
      quiz: [{ id: 'q1', question: 'Q?', choices: [{ label: 'A', text: '1' }], correct_answer: 'A' }],
      flashcards: [],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(h.elements.get('study-material-modal').style.display, 'flex');
});

test('UX06: Responsive learning form CSS contract satisfies mobile stacking and column basis', () => {
  const cssPath = path.join(__dirname, '../static/spatial-layout.css');
  const css = fs.readFileSync(cssPath, 'utf8');

  // Verify mobile media query has column direction and flex: 0 0 auto !important for form-group
  assert.match(css, /@media\s*\(max-width:\s*640px\)/);
  const mediaIdx = css.indexOf('@media (max-width: 640px)');
  const mobileSection = css.slice(mediaIdx);

  assert.match(mobileSection, /\.learning-tools-panel\s+\.form-row\s*\{[^}]*flex-direction:\s*column\s*!important/);
  assert.match(mobileSection, /\.learning-tools-panel\s+\.form-group[^{]*\{[^}]*flex:\s*0\s+0\s+auto\s*!important/);
  assert.match(mobileSection, /\.learning-tools-panel\s+\.form-group\.form-actions\s*\{[^}]*flex-direction:\s*column\s*!important/);
  assert.match(mobileSection, /\.learning-tools-panel\s+\.form-group\.form-actions\s+button[^{]*\{[^}]*width:\s*100%\s*!important/);

  // Verify index.html does not retain non-wrapping inline style on form-actions
  const htmlPath = path.join(__dirname, '../static/index.html');
  const html = fs.readFileSync(htmlPath, 'utf8');
  assert.doesNotMatch(html, /class="form-group form-actions"\s+style="display:\s*flex;\s*flex-direction:\s*row/);
});
