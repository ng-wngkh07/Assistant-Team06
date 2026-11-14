const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function createHarness(options = {}) {
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

  let generateResolver = null;
  let generateRejecter = null;

  const defaultFetch = (url, opts) => {
    if (url === '/api/materials/documents') {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          documents: [
            {
              filename: 'test-doc.pdf',
              clean_title: 'Tài liệu ôn tập',
              total_pages: 10,
              chunk_count: 5,
            },
          ],
        }),
      });
    }
    if (url === '/api/materials/generate-from-library') {
      return new Promise((resolve, reject) => {
        generateResolver = resolve;
        generateRejecter = reject;
      });
    }
    return Promise.resolve({
      ok: true,
      json: async () => ({ sessions: [], items: [], models: [], knowledge_base: {} }),
    });
  };

  const fetchHandler = options.fetch || defaultFetch;

  const sandbox = {
    document,
    window: {
      addEventListener() {},
      location: { origin: 'http://localhost' },
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
    resolveGeneration: (data, ok = true) => {
      if (generateResolver) {
        generateResolver({
          ok,
          json: async () => data,
        });
      }
    },
    rejectGeneration: (err) => {
      if (generateRejecter) {
        generateRejecter(err);
      }
    },
  };
}

test('05-D: Leave-waiting control and Escape key dismiss modal without claiming model cancellation', async () => {
  const h = createHarness();
  const inputEl = h.elements.get('user-input');
  inputEl.focus();
  assert.equal(h.document.activeElement, inputEl);

  // Open library picker and click generate
  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');
  assert.ok(generateBtn, 'Expected generate button to exist');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  const loadingModal = h.elements.get('study-loading-modal');
  assert.equal(loadingModal.style.display, 'flex');
  assert.equal(loadingModal.classList.contains('active'), true);

  // Click leave-waiting button
  const leaveBtn = h.elements.get('btn-leave-study-loading');
  assert.ok(leaveBtn, 'Leave waiting button must exist');
  leaveBtn.dispatchEvent({ type: 'click' });

  assert.equal(loadingModal.style.display, 'none');
  assert.equal(loadingModal.classList.contains('active'), false);
  // Focus restored
  assert.equal(h.document.activeElement, inputEl);
  // Honest ongoing status - does NOT claim model canceled
  const statusText = h.elements.get('study-tool-status').textContent;
  assert.match(statusText, /Đang tiếp tục xử lý.*trong nền/i);
  assert.doesNotMatch(statusText, /đã hủy|hủy bỏ/i);
});

test('05-D: Escape key dismisses modal and restores focus', async () => {
  const h = createHarness();
  const inputEl = h.elements.get('user-input');
  inputEl.focus();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  const loadingModal = h.elements.get('study-loading-modal');
  assert.equal(loadingModal.style.display, 'flex');

  // Trigger Escape
  h.document.dispatchEvent({ type: 'keydown', key: 'Escape' });

  assert.equal(loadingModal.style.display, 'none');
  assert.equal(loadingModal.classList.contains('active'), false);
  assert.equal(h.document.activeElement, inputEl);
});

test('05-D: Duplicate in-flight generation requests are blocked with alert', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  // First click starts generation
  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Second click while in-flight
  generateBtn.dispatchEvent({ type: 'click' });
  assert.equal(h.alerts.length, 1);
  assert.match(h.alerts[0], /Đang xử lý tạo câu hỏi.*Vui lòng chờ hoàn thành/i);
});

test('05-D: Late completion after dismissal updates status without stealing focus or reopening modal', async () => {
  const h = createHarness();
  const inputEl = h.elements.get('user-input');
  inputEl.focus();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  const loadingModal = h.elements.get('study-loading-modal');
  const studyModal = h.elements.get('study-material-modal');

  // User dismisses modal
  h.elements.get('btn-leave-study-loading').dispatchEvent({ type: 'click' });
  assert.equal(loadingModal.style.display, 'none');
  assert.notEqual(studyModal.style.display, 'flex');

  // Move focus to inputEl
  inputEl.focus();
  assert.equal(h.document.activeElement, inputEl);

  // Late completion resolves
  h.resolveGeneration({
    material: {
      filename: 'test-doc.pdf',
      quiz: [
        {
          id: 'q1',
          question: 'Câu hỏi test?',
          choices: [{ label: 'A', text: '1' }],
          correct_answer: 'A',
        },
      ],
      flashcards: [{ front: 'F', back: 'B' }],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  // Modal must NOT reopen, must NOT steal focus
  assert.equal(loadingModal.style.display, 'none');
  assert.notEqual(studyModal.style.display, 'flex');
  assert.equal(h.document.activeElement, inputEl);

  // Status bar has open result button
  const statusHtml = h.elements.get('study-tool-status').innerHTML;
  assert.match(statusHtml, /Đã tạo xong 1 câu trắc nghiệm/i);
  assert.match(statusHtml, /Mở xem kết quả/i);

  // Clicking open result button opens the material modal
  const openBtn = h.elements.get('btn-open-completed-test-doc_pdf');
  assert.ok(openBtn, 'Button to open completed result should exist');
  openBtn.dispatchEvent({ type: 'click' });

  assert.equal(studyModal.style.display, 'flex');
  assert.equal(studyModal.classList.contains('active'), true);
});

test('05-D: Direct completion when user stays waiting automatically opens study modal', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  const loadingModal = h.elements.get('study-loading-modal');
  const studyModal = h.elements.get('study-material-modal');
  assert.equal(loadingModal.style.display, 'flex');

  // User stays waiting, generation completes
  h.resolveGeneration({
    material: {
      filename: 'test-doc.pdf',
      quiz: [
        {
          id: 'q1',
          question: 'Câu hỏi test?',
          choices: [{ label: 'A', text: '1' }],
          correct_answer: 'A',
        },
      ],
      flashcards: [{ front: 'F', back: 'B' }],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(loadingModal.style.display, 'none');
  assert.equal(studyModal.style.display, 'flex');
  assert.equal(studyModal.classList.contains('active'), true);
});

test('05-E: Unverified quiz grading renders truthful match/difference and unverified score copy', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Resolve with unverified material
  h.resolveGeneration({
    material: {
      filename: 'unverified-test.pdf',
      answers_verified: false,
      quiz: [
        {
          id: 'q1',
          question: 'Khái niệm nào sau đây đúng?',
          choices: [
            { label: 'A', text: 'Phương án A' },
            { label: 'B', text: 'Phương án B' },
          ],
          correct_answer: 'A',
          page_num: 2,
          evidence_quote: 'Bằng chứng trích dẫn A',
        },
      ],
      flashcards: [],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  // Verify subtitle states unverified notice
  const subtitle = h.elements.get('study-material-subtitle').textContent;
  assert.match(subtitle, /Chưa xác minh đáp án đúng \(không xác minh ngữ nghĩa\)/i);

  // Find choices in container
  const choicesContainer = h.elements.get('quiz-choices-container');
  const choiceBtns = choicesContainer.querySelectorAll('button');
  assert.equal(choiceBtns.length, 2);

  // Click choice A (matches proposed answer)
  choiceBtns[0].dispatchEvent({ type: 'click' });

  // After answering, renderQuizQuestion re-renders the buttons with updated classes
  const updatedChoiceBtns = choicesContainer.querySelectorAll('button');
  assert.equal(updatedChoiceBtns[0].classList.contains('proposed-match'), true);
  assert.equal(updatedChoiceBtns[0].classList.contains('correct'), false);

  // Feedback title and icon
  const feedbackTitle = h.elements.get('quiz-feedback-title').textContent;
  const feedbackIcon = h.elements.get('quiz-feedback-icon').textContent;
  assert.equal(feedbackIcon, '📋');
  assert.match(feedbackTitle, /Khớp với đáp án đề xuất \(A\) — Chưa xác minh ngữ nghĩa/i);
  assert.doesNotMatch(feedbackTitle, /Chính xác!/);

  // Score text: must state unverified proposed match
  const scoreText = h.elements.get('quiz-score-text').textContent;
  assert.match(scoreText, /Khớp đáp án đề xuất: 1 \/ 1 \(Chưa xác minh · Tổng 1 câu\)/i);
  assert.doesNotMatch(scoreText, /^Điểm:/);

  // Citation note
  const citationQuote = h.elements.get('quiz-citation-quote').textContent;
  assert.match(citationQuote, /không khẳng định xác minh đáp án/i);
});

test('05-E: Unverified quiz grading renders proposed-diff and truthful copy on differing answer', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  h.resolveGeneration({
    material: {
      filename: 'unverified-diff-test.pdf',
      answers_verified: false,
      quiz: [
        {
          id: 'q1',
          question: 'Khái niệm nào sau đây đúng?',
          choices: [
            { label: 'A', text: 'Phương án A' },
            { label: 'B', text: 'Phương án B' },
          ],
          correct_answer: 'A',
          page_num: 2,
        },
      ],
      flashcards: [],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  const choicesContainer = h.elements.get('quiz-choices-container');
  const choiceBtns = choicesContainer.querySelectorAll('button');

  // Click choice B (differs from proposed answer A)
  choiceBtns[1].dispatchEvent({ type: 'click' });

  const updatedChoiceBtns = choicesContainer.querySelectorAll('button');
  assert.equal(updatedChoiceBtns[1].classList.contains('proposed-diff'), true);
  assert.equal(updatedChoiceBtns[1].classList.contains('incorrect'), false);

  const feedbackTitle = h.elements.get('quiz-feedback-title').textContent;
  const feedbackIcon = h.elements.get('quiz-feedback-icon').textContent;
  assert.equal(feedbackIcon, 'ℹ️');
  assert.match(feedbackTitle, /Khác với đáp án đề xuất \(Đáp án đề xuất: A\) — Chưa xác minh ngữ nghĩa/i);
  assert.doesNotMatch(feedbackTitle, /Chưa đúng!/);

  const scoreText = h.elements.get('quiz-score-text').textContent;
  assert.match(scoreText, /Khớp đáp án đề xuất: 0 \/ 1 \(Chưa xác minh · Tổng 1 câu\)/i);
});

test('05-D: Generation failure permits repeat attempt after terminal state', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Reject generation with error
  h.rejectGeneration(new Error('Máy chủ gián đoạn'));
  await new Promise((r) => setTimeout(r, 20));

  // Loading modal is closed
  const loadingModal = h.elements.get('study-loading-modal');
  assert.equal(loadingModal.style.display, 'none');

  // Status informs error
  const statusText = h.elements.get('study-tool-status').textContent;
  assert.match(statusText, /Lỗi tạo bài học.*Máy chủ gián đoạn/i);

  // Repeat generation is now allowed (not blocked as duplicate)
  h.alerts.length = 0;
  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Alert was not triggered
  assert.equal(h.alerts.length, 0);
  assert.equal(loadingModal.style.display, 'flex');
});

test('05-E: Verified quiz grading renders authoritative score and accuracy copy', async () => {
  const h = createHarness();

  await h.elements.get('btn-study-tool-library').dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));
  const generateBtn = h.elements.get('library-picker-table-body').querySelector('.btn-generate-from-doc');

  generateBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setImmediate(r));

  // Resolve with verified material
  h.resolveGeneration({
    material: {
      filename: 'verified-test.pdf',
      answers_verified: true,
      quiz: [
        {
          id: 'q1',
          question: 'Định lý X phát biểu thế nào?',
          choices: [
            { label: 'A', text: 'Đáp án A' },
            { label: 'B', text: 'Đáp án B' },
          ],
          correct_answer: 'A',
          page_num: 1,
          evidence_quote: 'Trích dẫn nguồn chính xác',
        },
      ],
      flashcards: [],
    },
  });
  await new Promise((r) => setTimeout(r, 20));

  // Subtitle states verified notice
  const subtitle = h.elements.get('study-material-subtitle').textContent;
  assert.match(subtitle, /Đáp án đã đối chiếu nguồn/i);

  const choicesContainer = h.elements.get('quiz-choices-container');
  const choiceBtns = choicesContainer.querySelectorAll('button');
  assert.equal(choiceBtns.length, 2);

  // Click choice A (correct)
  choiceBtns[0].dispatchEvent({ type: 'click' });

  // After answering, renderQuizQuestion re-renders the buttons with updated classes
  const updatedChoiceBtns = choicesContainer.querySelectorAll('button');
  assert.equal(updatedChoiceBtns[0].classList.contains('correct'), true);
  assert.equal(updatedChoiceBtns[0].classList.contains('proposed-match'), false);

  // Feedback title and icon
  const feedbackTitle = h.elements.get('quiz-feedback-title').textContent;
  const feedbackIcon = h.elements.get('quiz-feedback-icon').textContent;
  assert.equal(feedbackIcon, '✅');
  assert.equal(feedbackTitle, 'Chính xác!');

  // Score text: authoritative Điểm
  const scoreText = h.elements.get('quiz-score-text').textContent;
  assert.match(scoreText, /Điểm: 1 \/ 1 \(Tổng 1 câu\)/i);
});
