const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const INDEX_HTML_PATH = path.join(ROOT, 'static/index.html');
const SPATIAL_JS_PATH = path.join(ROOT, 'static/spatial-layout.js');
const LAYOUT_JS_PATH = path.join(ROOT, 'static/layout.js');
const DOC_LOOKUP_JS_PATH = path.join(ROOT, 'static/document-lookup.js');
const SPATIAL_CSS_PATH = path.join(ROOT, 'static/spatial-layout.css');
const STYLE_CSS_PATH = path.join(ROOT, 'static/style.css');

const VOID_TAGS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

function parseAttributes(attrText) {
  const attrs = {};
  const attrRe = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = attrRe.exec(attrText)) !== null) {
    const name = m[1];
    const value = m[2] ?? m[3] ?? m[4] ?? '';
    attrs[name] = value;
  }
  return attrs;
}

function splitSelectorParts(selector) {
  return selector.split(',').map((s) => s.trim()).filter(Boolean);
}

function matchesSimple(el, token) {
  if (!el || !el.tagName) return false;
  let rest = token.trim();
  if (!rest) return false;

  const tagMatch = rest.match(/^[a-zA-Z0-9_-]+/);
  if (tagMatch) {
    if (el.tagName !== tagMatch[0].toUpperCase()) return false;
    rest = rest.slice(tagMatch[0].length);
  }

  while (rest.length > 0) {
    if (rest.startsWith('#')) {
      const m = rest.slice(1).match(/^[a-zA-Z0-9_-]+/);
      if (!m || el.id !== m[0]) return false;
      rest = rest.slice(1 + m[0].length);
    } else if (rest.startsWith('.')) {
      const m = rest.slice(1).match(/^[a-zA-Z0-9_-]+/);
      if (!m || !el.classList.contains(m[0])) return false;
      rest = rest.slice(1 + m[0].length);
    } else if (rest.startsWith('[')) {
      const end = rest.indexOf(']');
      if (end === -1) return false;
      const expr = rest.slice(1, end).trim();
      const eq = expr.indexOf('=');
      if (eq === -1) {
        if (!el.hasAttribute(expr)) return false;
      } else {
        const key = expr.slice(0, eq).trim();
        const expected = expr.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
        if (el.getAttribute(key) !== expected) return false;
      }
      rest = rest.slice(end + 1);
    } else {
      return false;
    }
  }
  return true;
}

function matchesSelector(el, selector) {
  for (const part of splitSelectorParts(selector)) {
    const segments = part.trim().split(/\s+/);
    let cur = el;
    let matched = matchesSimple(cur, segments[segments.length - 1]);
    if (!matched) continue;
    for (let i = segments.length - 2; i >= 0; i--) {
      cur = cur.parentElement;
      while (cur && !matchesSimple(cur, segments[i])) {
        cur = cur.parentElement;
      }
      if (!cur) {
        matched = false;
        break;
      }
    }
    if (matched) return true;
  }
  return false;
}

class DomElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.parentElement = null;
    this.listeners = {};
    this.style = {};
    this.value = '';
    this._classes = new Set();
    this._focused = false;
    this.classList = {
      add: (...xs) => xs.filter(Boolean).forEach((x) => this._classes.add(x)),
      remove: (...xs) => xs.filter(Boolean).forEach((x) => this._classes.delete(x)),
      toggle: (x, force) => {
        if (force === undefined) {
          if (this._classes.has(x)) {
            this._classes.delete(x);
            return false;
          }
          this._classes.add(x);
          return true;
        }
        if (force) this._classes.add(x);
        else this._classes.delete(x);
        return Boolean(force);
      },
      contains: (x) => this._classes.has(x),
      toArray: () => [...this._classes],
    };
  }

  get id() {
    return this.attributes.id || '';
  }

  set id(val) {
    this.attributes.id = String(val);
  }

  get className() {
    return [...this._classes].join(' ');
  }

  get hidden() {
    return this.hasAttribute('hidden');
  }

  set hidden(val) {
    if (val) this.setAttribute('hidden', '');
    else this.removeAttribute('hidden');
  }

  set className(val) {
    this._classes = new Set(String(val || '').split(/\s+/).filter(Boolean));
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) {
    const strVal = String(value);
    this.attributes[name] = strVal;
    if (name === 'class') this.className = strVal;
  }

  getAttribute(name) {
    if (name === 'class') return this.className;
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name);
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'class') this._classes.clear();
  }

  contains(other) {
    let cur = other;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentElement;
    }
    return false;
  }

  closest(selector) {
    let cur = this;
    while (cur) {
      if (matchesSelector(cur, selector)) return cur;
      cur = cur.parentElement;
    }
    return null;
  }

  querySelectorAll(selector) {
    const found = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (matchesSelector(child, selector)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }

  removeEventListener(type, fn) {
    if (!this.listeners[type]) return;
    this.listeners[type] = this.listeners[type].filter((f) => f !== fn);
  }

  focus() {
    if (this._ownerDocument) this._ownerDocument.activeElement = this;
  }

  blur() {
    if (this._ownerDocument && this._ownerDocument.activeElement === this) {
      this._ownerDocument.activeElement = this._ownerDocument.body;
    }
  }

  click() {
    const event = createEvent('click', this, { bubbles: true, cancelable: true });
    this.dispatchEvent(event);
  }

  dispatchEvent(event) {
    let target = this;
    if (!event.target) event.target = target;
    let cur = this;
    while (cur) {
      event.currentTarget = cur;
      for (const fn of cur.listeners[event.type] || []) {
        fn.call(cur, event);
      }
      if (event._stopped || !event.bubbles) break;
      cur = cur.parentElement || (cur === this._ownerDocument?.documentElement ? this._ownerDocument : null);
    }
    if (!event._stopped && event.bubbles && this._ownerDocument && cur !== this._ownerDocument) {
      event.currentTarget = this._ownerDocument;
      for (const fn of this._ownerDocument.listeners[event.type] || []) {
        fn.call(this._ownerDocument, event);
      }
    }
    return !event.defaultPrevented;
  }
}

function createEvent(type, target = null, opts = {}) {
  return {
    type,
    target,
    currentTarget: target,
    key: opts.key || '',
    bubbles: opts.bubbles ?? true,
    cancelable: opts.cancelable ?? true,
    defaultPrevented: false,
    _stopped: false,
    stopPropagation() {
      this._stopped = true;
    },
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
}

function parseIndexHtmlDom() {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  const root = new DomElement('document');
  const byId = new Map();
  const stack = [root];

  const stripped = html
    .replace(/<!DOCTYPE[^>]*>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '');

  const tagRe = /<(\/?)([a-zA-Z0-9-]+)([^>]*)>/g;
  let m;
  while ((m = tagRe.exec(stripped)) !== null) {
    const isClose = m[1] === '/';
    const tag = m[2].toLowerCase();
    const rawAttrs = m[3] || '';
    const selfClosing = rawAttrs.trim().endsWith('/') || VOID_TAGS.has(tag);

    if (isClose) {
      for (let i = stack.length - 1; i >= 1; i--) {
        if (stack[i].tagName === tag.toUpperCase()) {
          stack.length = i;
          break;
        }
      }
      continue;
    }

    const el = new DomElement(tag);
    const attrs = parseAttributes(rawAttrs.replace(/\/$/, ''));
    for (const [k, v] of Object.entries(attrs)) {
      el.setAttribute(k, v);
    }
    if (el.id) byId.set(el.id, el);
    stack[stack.length - 1].appendChild(el);
    if (!selfClosing) stack.push(el);
  }

  return { html, root, byId };
}

function mountRealApp({ initialWidth = 1440, initialStorage = {}, includeSpatial = true } = {}) {
  const { html, root, byId } = parseIndexHtmlDom();
  const htmlEl = root.children.find((c) => c.tagName === 'HTML');
  const bodyEl = htmlEl.querySelector('body');
  if (!htmlEl || !bodyEl) throw new Error('Expected html and body in static/index.html');
  if (!includeSpatial) {
    htmlEl.removeAttribute('data-spatial-layout');
  }

  const documentListeners = {};
  const documentObj = {
    documentElement: htmlEl,
    body: bodyEl,
    readyState: 'loading',
    activeElement: bodyEl,
    listeners: documentListeners,
    getElementById(id) {
      return byId.get(id) || htmlEl.querySelector(`#${id}`);
    },
    querySelector(selector) {
      return htmlEl.querySelector(selector);
    },
    querySelectorAll(selector) {
      return htmlEl.querySelectorAll(selector);
    },
    createElement(tag) {
      const el = new DomElement(tag);
      el._ownerDocument = documentObj;
      return el;
    },
    addEventListener(type, fn) {
      (documentListeners[type] ||= []).push(fn);
    },
    dispatchEvent(event) {
      event.currentTarget = documentObj;
      for (const fn of documentListeners[event.type] || []) fn.call(documentObj, event);
      return !event.defaultPrevented;
    },
  };

  // Attach ownerDocument to every parsed element so focus/blur/document bubbling works faithfully.
  const bindDoc = (node) => {
    node._ownerDocument = documentObj;
    for (const child of node.children) bindDoc(child);
  };
  bindDoc(htmlEl);

  // Add realistic dynamic session item into real #history-list container for click tests.
  const historyList = byId.get('history-list');
  const sessionRow = documentObj.createElement('div');
  sessionRow.className = 'session-item';
  const sessionInfo = documentObj.createElement('div');
  sessionInfo.className = 'session-info';
  sessionRow.appendChild(sessionInfo);
  historyList.appendChild(sessionRow);

  let viewportWidth = initialWidth;
  const mediaListeners = new Set();
  const storageMap = new Map(Object.entries(initialStorage));
  const localStorage = {
    getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
    setItem: (k, v) => storageMap.set(String(k), String(v)),
    removeItem: (k) => storageMap.delete(k),
    clear: () => storageMap.clear(),
  };

  const matchMedia = (query) => {
    const evalQuery = () => (query.includes('max-width: 900px') ? viewportWidth <= 900 : false);
    let lastMatch = evalQuery();
    const mql = {
      media: query,
      get matches() {
        return evalQuery();
      },
      addEventListener(type, fn) {
        if (type === 'change') mediaListeners.add({ fn, mql, getLast: () => lastMatch, setLast: (v) => { lastMatch = v; } });
      },
      addListener(fn) {
        mediaListeners.add({ fn, mql, getLast: () => lastMatch, setLast: (v) => { lastMatch = v; } });
      },
    };
    return mql;
  };

  class FakeMutationObserver {
    observe() {}
    disconnect() {}
  }

  const windowObj = {
    document: documentObj,
    localStorage,
    matchMedia,
    innerWidth: initialWidth,
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    dispatchEvent() {
      return true;
    },
    addEventListener() {},
  };

  const context = {
    window: windowObj,
    document: documentObj,
    localStorage,
    MutationObserver: FakeMutationObserver,
    CustomEvent: windowObj.CustomEvent,
    console,
    setTimeout,
    clearTimeout,
  };
  windowObj.window = windowObj;

  if (includeSpatial) {
    vm.runInNewContext(fs.readFileSync(SPATIAL_JS_PATH, 'utf8'), context, { filename: 'spatial-layout.js' });
  }
  vm.runInNewContext(fs.readFileSync(LAYOUT_JS_PATH, 'utf8'), context, { filename: 'layout.js' });
  vm.runInNewContext(fs.readFileSync(DOC_LOOKUP_JS_PATH, 'utf8'), context, { filename: 'document-lookup.js' });

  documentObj.readyState = 'complete';
  documentObj.dispatchEvent(createEvent('DOMContentLoaded', documentObj, { bubbles: false }));

  const resizeTo = (nextWidth) => {
    viewportWidth = nextWidth;
    windowObj.innerWidth = nextWidth;
    for (const entry of mediaListeners) {
      const nowMatches = entry.mql.matches;
      if (nowMatches !== entry.getLast()) {
        entry.setLast(nowMatches);
        entry.fn({ matches: nowMatches, media: entry.mql.media });
      }
    }
  };

  const pressEscape = () => {
    documentObj.dispatchEvent(createEvent('keydown', documentObj, { key: 'Escape', bubbles: false }));
  };

  return {
    html,
    window: windowObj,
    document: documentObj,
    localStorage,
    sidebar: byId.get('sidebar'),
    backdrop: byId.get('sidebar-backdrop'),
    openBtn: byId.get('btn-open-sidebar'),
    collapseBtn: byId.get('btn-collapse-sidebar'),
    bentoOpenBtn: documentObj.querySelector('[data-bento-action="open-drawer"]'),
    newChatSidebarBtn: byId.get('btn-new-chat-sidebar'),
    timetableSidebarBtn: byId.get('btn-open-timetable'),
    modeQaBtn: byId.get('mode-qa'),
    modeToolsBtn: byId.get('mode-tools'),
    modeTimetableBtn: byId.get('mode-timetable'),
    qaPanel: byId.get('qa-panel'),
    toolsPanel: byId.get('learning-tools-panel'),
    timetablePanel: byId.get('timetable-panel'),
    outsideTarget: htmlEl.querySelector('.arym-bento-grid'),
    sessionInfo,
    resizeTo,
    pressEscape,
  };
}

function assertCoherentState(env, expectedOpen, label) {
  const { sidebar, backdrop, openBtn, document } = env;
  const body = document.body;
  assert.equal(sidebar.classList.contains('drawer-open'), expectedOpen, `${label}: #sidebar.drawer-open`);
  assert.equal(sidebar.classList.contains('open'), expectedOpen, `${label}: #sidebar.open`);
  assert.equal(body.classList.contains('sidebar-open'), expectedOpen, `${label}: body.sidebar-open`);
  assert.equal(body.classList.contains('sidebar-collapsed'), false, `${label}: body must not retain legacy sidebar-collapsed`);
  assert.equal(backdrop.classList.contains('open'), expectedOpen, `${label}: #sidebar-backdrop.open`);
  assert.equal(openBtn.getAttribute('aria-expanded'), expectedOpen ? 'true' : 'false', `${label}: #btn-open-sidebar aria-expanded`);
  assert.equal(sidebar.getAttribute('aria-hidden'), expectedOpen ? 'false' : 'true', `${label}: #sidebar aria-hidden`);
}

test('SID-04 HTML script order and CSS layer hierarchy prevent backdrop/collapse conflicts', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  const spatialPos = html.indexOf('/static/spatial-layout.js');
  const layoutPos = html.indexOf('/static/layout.js');
  assert.ok(spatialPos !== -1 && layoutPos !== -1, 'Expected both spatial-layout.js and layout.js in index.html');
  assert.ok(spatialPos < layoutPos, 'spatial-layout.js must execute before layout.js');

  const spatialCss = fs.readFileSync(SPATIAL_CSS_PATH, 'utf8');
  const styleCss = fs.readFileSync(STYLE_CSS_PATH, 'utf8');
  assert.ok(styleCss.includes('.sidebar-backdrop'), 'baseline style.css backdrop rule present');

  const sidebarZ = Number(spatialCss.match(/\.sidebar\s*\{[\s\S]*?z-index:\s*(\d+)\s*!important/)?.[1]);
  const headerZ = Number(spatialCss.match(/\.chat-header\s*\{[\s\S]*?z-index:\s*(\d+)\s*!important/)?.[1]);
  const backdropZ = Number(spatialCss.match(/\.sidebar-backdrop\s*\{[\s\S]*?z-index:\s*(\d+)\s*!important/)?.[1]);

  assert.equal(sidebarZ, 60, 'sidebar drawer z-index must be 60');
  assert.equal(headerZ, 55, 'floating dock z-index must be 55');
  assert.equal(backdropZ, 50, 'backdrop z-index must sit at 50 below dock (55) and drawer (60)');
  assert.match(spatialCss, /\.sidebar\s*\{[\s\S]*?margin-left:\s*0\s*!important/);
  assert.match(spatialCss, /body\.sidebar-collapsed\s+\.sidebar\s*\{[\s\S]*?margin-left:\s*0\s*!important/);
  assert.match(spatialCss, /\.sidebar-backdrop\s*\{[\s\S]*?display:\s*block\s*!important/);
  assert.match(spatialCss, /\.sidebar-backdrop\s*\{[\s\S]*?pointer-events:\s*none\s*!important/);
  assert.match(spatialCss, /body\.sidebar-open\s+\.sidebar-backdrop[\s\S]*?pointer-events:\s*auto\s*!important/);
  assert.match(spatialCss, /@media\s*\(max-width:\s*640px\)\s*\{[\s\S]*?\.sidebar\s*\{[\s\S]*?bottom:\s*112px\s*!important/);
});

test('SID-01 & SID-03: Desktop single controller, repeated toggles, no duplicate handlers, persisted state', () => {
  const env = mountRealApp({ initialWidth: 1440, includeSpatial: true });

  assert.equal((env.openBtn.listeners.click || []).length, 1, 'Expected single click handler on #btn-open-sidebar');
  assert.equal((env.collapseBtn.listeners.click || []).length, 1, 'Expected single click handler on #btn-collapse-sidebar');
  assert.equal((env.backdrop.listeners.click || []).length, 1, 'Expected single click handler on #sidebar-backdrop');
  assertCoherentState(env, false, 'desktop initial');

  // Repeated dock toggles
  env.openBtn.click();
  assertCoherentState(env, true, 'dock click 1 (open)');
  assert.equal(env.localStorage.getItem('sidebar_collapsed'), '0');

  env.openBtn.click();
  assertCoherentState(env, false, 'dock click 2 (close)');
  assert.equal(env.localStorage.getItem('sidebar_collapsed'), '1');

  env.openBtn.click();
  assertCoherentState(env, true, 'dock click 3 (reopen)');

  // Close button, then Bento open must not be trapped by body.sidebar-collapsed
  env.collapseBtn.click();
  assertCoherentState(env, false, 'close button');
  assert.equal(env.localStorage.getItem('sidebar_collapsed'), '1');

  env.bentoOpenBtn.click();
  assertCoherentState(env, true, 'bento open-drawer after close');
  assert.equal(env.localStorage.getItem('sidebar_collapsed'), '0');

  // Escape closes and dock reopens on 1st click (no inverted state)
  env.pressEscape();
  assertCoherentState(env, false, 'escape close');
  env.openBtn.click();
  assertCoherentState(env, true, 'dock reopen after escape');

  // Backdrop click closes and dock reopens on 1st click
  env.backdrop.click();
  assertCoherentState(env, false, 'backdrop click close');
  env.openBtn.click();
  assertCoherentState(env, true, 'dock reopen after backdrop');

  // Outside click on Bento area closes
  env.outsideTarget.click();
  assertCoherentState(env, false, 'outside click close');

  // Reload when sidebar_collapsed='1' starts closed and still opens cleanly via Bento
  const reloaded = mountRealApp({
    initialWidth: 1440,
    initialStorage: { sidebar_collapsed: '1' },
    includeSpatial: true,
  });
  assertCoherentState(reloaded, false, 'reloaded collapsed=1');
  reloaded.bentoOpenBtn.click();
  assertCoherentState(reloaded, true, 'bento open after reload collapsed=1');
});

test('SID-02 & SID-03: Mobile close, backdrop, Escape, mode switches, session actions, and 900px breakpoint sync', () => {
  const env = mountRealApp({ initialWidth: 390, includeSpatial: true });
  assertCoherentState(env, false, 'mobile initial');

  // Open via dock, close via close button
  env.openBtn.click();
  assertCoherentState(env, true, 'mobile dock open');
  env.collapseBtn.click();
  assertCoherentState(env, false, 'mobile close button');

  // Open via dock, close via backdrop, reopen via dock (no inverted backdrop!)
  env.openBtn.click();
  assertCoherentState(env, true, 'mobile reopen before backdrop');
  env.backdrop.click();
  assertCoherentState(env, false, 'mobile backdrop close');
  env.openBtn.click();
  assertCoherentState(env, true, 'mobile dock reopen after backdrop');

  // Session click closes mobile drawer
  env.sessionInfo.click();
  assertCoherentState(env, false, 'mobile session click');

  // New chat button inside sidebar closes drawer
  env.openBtn.click();
  env.newChatSidebarBtn.click();
  assertCoherentState(env, false, 'mobile new chat sidebar');

  // Bento open + mode switch on dock closes mobile drawer while switching mode panel
  env.bentoOpenBtn.click();
  assertCoherentState(env, true, 'mobile bento open before mode');
  env.modeToolsBtn.click();
  assertCoherentState(env, false, 'mobile mode tools auto-close');
  assert.ok(env.modeToolsBtn.classList.contains('active'), 'mode-tools active preserved');
  assert.equal(env.toolsPanel.hidden, false, 'tools panel shown');
  assert.equal(env.qaPanel.hidden, true, 'qa panel hidden');

  // Open on desktop, cross 900px down to 390px, then close with Escape
  const bpEnv = mountRealApp({ initialWidth: 1440, includeSpatial: true });
  bpEnv.openBtn.click();
  assertCoherentState(bpEnv, true, 'bp open at 1440');
  bpEnv.resizeTo(840);
  assertCoherentState(bpEnv, true, 'bp crossed down to 840');
  bpEnv.resizeTo(390);
  assertCoherentState(bpEnv, true, 'bp at 390');
  bpEnv.pressEscape();
  assertCoherentState(bpEnv, false, 'bp closed via escape at 390');

  // Open on mobile 390px, cross 900px up to 960px and 1440px, then close via button
  bpEnv.openBtn.click();
  assertCoherentState(bpEnv, true, 'bp reopened at 390');
  bpEnv.resizeTo(960);
  assertCoherentState(bpEnv, true, 'bp crossed up to 960');
  bpEnv.resizeTo(1440);
  assertCoherentState(bpEnv, true, 'bp at 1440');
  bpEnv.collapseBtn.click();
  assertCoherentState(bpEnv, false, 'bp closed via collapseBtn at 1440');
});

test('Legacy layout.js fallback still synchronizes mobile classes cleanly when bottom-dock is absent', () => {
  const legacyEnv = mountRealApp({ initialWidth: 390, includeSpatial: false });
  assert.equal(legacyEnv.document.body.classList.contains('sidebar-open'), false);
  legacyEnv.openBtn.click();
  assert.equal(legacyEnv.document.body.classList.contains('sidebar-open'), true);
  assert.equal(legacyEnv.sidebar.classList.contains('drawer-open'), true);
  legacyEnv.backdrop.click();
  assert.equal(legacyEnv.document.body.classList.contains('sidebar-open'), false);
  assert.equal(legacyEnv.sidebar.classList.contains('drawer-open'), false);
});
