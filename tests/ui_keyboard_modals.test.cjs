const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const INDEX_HTML_PATH = path.join(ROOT, 'static/index.html');
const SPATIAL_JS_PATH = path.join(ROOT, 'static/spatial-layout.js');
const ACCOUNTS_JS_PATH = path.join(ROOT, 'static/accounts.js');
const APP_JS_PATH = path.join(ROOT, 'static/app.js');

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
    attrs[name] = m[2] ?? m[3] ?? m[4] ?? '';
  }
  return attrs;
}

function splitSelectorParts(selector) {
  const parts = [];
  let current = '';
  let inBracket = false;
  for (const ch of String(selector || '')) {
    if (ch === '[') inBracket = true;
    else if (ch === ']') inBracket = false;
    if (ch === ',' && !inBracket) {
      if (current.trim()) parts.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

function matchesSimple(el, token) {
  if (!el || !el.tagName || el.tagName.startsWith('#')) return false;
  let rest = token.trim();
  if (!rest) return false;

  // Strip pseudo-classes used in focusable queries (:not(...), :disabled)
  const negations = [];
  rest = rest.replace(/:not\(([^)]+)\)/g, (_all, inner) => {
    negations.push(inner.trim());
    return '';
  });
  let requireDisabled = false;
  let requireEnabled = false;
  rest = rest.replace(/:disabled/g, () => {
    requireDisabled = true;
    return '';
  }).replace(/:enabled/g, () => {
    requireEnabled = true;
    return '';
  });

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
        if (expr === 'disabled') {
          if (!el.disabled && !el.hasAttribute('disabled')) return false;
        } else if (expr === 'open') {
          if (!el.open && !el.hasAttribute('open')) return false;
        } else if (!el.hasAttribute(expr)) {
          return false;
        }
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

  if (requireDisabled && !el.disabled && !el.hasAttribute('disabled')) return false;
  if (requireEnabled && (el.disabled || el.hasAttribute('disabled'))) return false;
  for (const neg of negations) {
    if (matchesSimple(el, neg)) return false;
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
      if (segments[i] === '>') {
        // Direct child combinator not required in general; handle gracefully
        continue;
      }
      const needDirect = segments[i + 1] === '>';
      cur = cur.parentElement;
      if (!needDirect) {
        while (cur && !matchesSimple(cur, segments[i])) {
          cur = cur.parentElement;
        }
      } else if (cur && !matchesSimple(cur, segments[i])) {
        cur = null;
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

function isCssHiddenNode(node) {
  if (!node || !node.tagName || node.tagName.startsWith('#')) return true;
  if (node.tagName === 'HTML' || node.tagName === 'BODY') return false;
  if (node.hasAttribute('hidden') || node.hidden === true) return true;
  if (node.style && node.style.display === 'none') return true;
  if (node.style && node.style.visibility === 'hidden') return true;

  // Match production CSS visibility rules used by modals and popovers
  if (node.classList.contains('account-popover-menu') && !node.classList.contains('is-open')) {
    return true;
  }
  if (node.classList.contains('account-modal-backdrop') && !node.classList.contains('is-open')) {
    return true;
  }
  if (node.id === 'arym-tour-overlay' && !node.classList.contains('is-active')) {
    return true;
  }
  if (
    node.classList.contains('modal') &&
    node.style?.display !== 'flex' &&
    node.style?.display !== 'block' &&
    !node.classList.contains('active')
  ) {
    return true;
  }
  if (node.tagName === 'DIALOG' && !node.open && !node.hasAttribute('open')) {
    return true;
  }
  return false;
}

function isElementVisible(el) {
  if (!el || !el.isConnected) return false;
  let prev = null;
  let cur = el;
  while (cur && cur.tagName && !cur.tagName.startsWith('#')) {
    if (isCssHiddenNode(cur)) return false;
    if (prev && cur.tagName === 'DETAILS' && !cur.open && !cur.hasAttribute('open')) {
      if (!(prev.tagName === 'SUMMARY' && prev.parentElement === cur)) {
        return false;
      }
    }
    prev = cur;
    cur = cur.parentElement;
  }
  return true;
}

function isNaturallyFocusable(el) {
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toUpperCase();
  if (tag === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() !== 'hidden') return true;
  if (tag === 'BUTTON' || tag === 'SELECT' || tag === 'TEXTAREA' || tag === 'SUMMARY') return true;
  if (tag === 'A' && el.hasAttribute('href')) return true;
  return false;
}

function isFocusableElement(el) {
  if (!isElementVisible(el)) return false;
  if (el.disabled || el.hasAttribute('disabled')) return false;
  if (isNaturallyFocusable(el)) {
    const ti = el.getAttribute('tabindex');
    return ti === null || Number(ti) >= 0 || ti === '-1';
  }
  const ti = el.getAttribute('tabindex');
  return ti !== null && ti !== '';
}

function parseFragmentInto(parentEl, html, ownerDocument) {
  const stripped = String(html || '')
    .replace(/<!DOCTYPE[^>]*>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '');

  const stack = [parentEl];
  const tokenRe = /<(\/?)([a-zA-Z0-9-]+)([^>]*)>|([^<]+)/g;
  let m;
  while ((m = tokenRe.exec(stripped)) !== null) {
    if (m[4]) {
      const txt = m[4];
      if (txt.trim()) {
        const textNode = new DomElement('#text');
        textNode.textContent = txt;
        textNode._ownerDocument = ownerDocument;
        stack[stack.length - 1].appendChild(textNode);
      }
      continue;
    }
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
    el._ownerDocument = ownerDocument;
    const attrs = parseAttributes(rawAttrs.replace(/\/$/, ''));
    for (const [k, v] of Object.entries(attrs)) {
      el.setAttribute(k, v);
    }
    stack[stack.length - 1].appendChild(el);
    if (!selfClosing) stack.push(el);
  }
}

class DomElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.parentElement = null;
    this.listeners = { capture: {}, bubble: {} };
    this.style = {};
    this.value = '';
    this._innerHTML = '';
    this._textContent = '';
    this._classes = new Set();
    this._hidden = false;
    this._disabled = false;
    this._open = false;
    this._ownerDocument = null;
    this.dataset = {};
    this.style = {};
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

  [Symbol.for('nodejs.util.inspect.custom')]() {
    return `<${this.tagName.toLowerCase()}${this.id ? `#${this.id}` : ''}>`;
  }

  set id(val) {
    this.attributes.id = String(val);
    if (this._ownerDocument && this._ownerDocument._byId) {
      this._ownerDocument._byId.set(this.attributes.id, this);
    }
  }

  get className() {
    return [...this._classes].join(' ');
  }

  set className(val) {
    this._classes = new Set(String(val || '').split(/\s+/).filter(Boolean));
  }

  get hidden() {
    return this._hidden || this.hasAttribute('hidden');
  }

  set hidden(val) {
    this._hidden = Boolean(val);
    if (val) this.attributes.hidden = '';
    else delete this.attributes.hidden;
  }

  get disabled() {
    return this._disabled || this.hasAttribute('disabled');
  }

  set disabled(val) {
    this._disabled = Boolean(val);
    if (val) this.attributes.disabled = '';
    else delete this.attributes.disabled;
  }

  get open() {
    return this._open || this.hasAttribute('open');
  }

  set open(val) {
    this._open = Boolean(val);
    if (val) this.attributes.open = '';
    else delete this.attributes.open;
  }

  get isConnected() {
    let cur = this;
    while (cur) {
      if (this._ownerDocument && cur === this._ownerDocument.documentElement) return true;
      cur = cur.parentElement;
    }
    return false;
  }

  get offsetParent() {
    return isElementVisible(this) ? (this.parentElement || this._ownerDocument?.body || {}) : null;
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(val) {
    this._innerHTML = String(val || '');
    this.children = [];
    parseFragmentInto(this, this._innerHTML, this._ownerDocument);
  }

  get textContent() {
    if (this.tagName === '#TEXT') return this._textContent;
    if (this._textContent) return this._textContent;
    return this.children.map((c) => c.textContent).join('');
  }

  set textContent(val) {
    this._textContent = String(val ?? '');
    if (this.tagName !== '#TEXT') this.children = [];
  }

  getClientRects() {
    return isElementVisible(this) ? [{ width: 100, height: 24, top: 0, left: 0 }] : [];
  }

  getBoundingClientRect() {
    return isElementVisible(this)
      ? { width: 100, height: 24, top: 0, left: 0, bottom: 24, right: 100 }
      : { width: 0, height: 0, top: 0, left: 0, bottom: 0, right: 0 };
  }

  scrollIntoView() {}
  setSelectionRange() {}
  select() {}

  appendChild(child) {
    if (!child) return child;
    child.parentElement = this;
    const setOwner = (node) => {
      node._ownerDocument = this._ownerDocument;
      if (node.id && this._ownerDocument && this._ownerDocument._byId) {
        this._ownerDocument._byId.set(node.id, node);
      }
      for (const c of node.children || []) setOwner(c);
    };
    setOwner(child);
    this.children.push(child);
    return child;
  }

  append(...nodes) {
    for (const n of nodes) {
      if (typeof n === 'string') {
        const t = new DomElement('#text');
        t.textContent = n;
        this.appendChild(t);
      } else if (n) {
        this.appendChild(n);
      }
    }
  }

  prepend(...nodes) {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (!n) continue;
      n.parentElement = this;
      n._ownerDocument = this._ownerDocument;
      this.children.unshift(n);
    }
  }

  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }

  insertAdjacentElement(_where, el) {
    return this.appendChild(el);
  }

  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter((c) => c !== this);
      this.parentElement = null;
    }
    const active = this._ownerDocument?.activeElement;
    if (active && this.contains(active) && this._ownerDocument?.body) {
      this._ownerDocument.activeElement = this._ownerDocument.body;
    }
  }

  setAttribute(name, value) {
    const strVal = String(value);
    this.attributes[name] = strVal;
    if (name === 'class') this.className = strVal;
    else if (name === 'id' && this._ownerDocument && this._ownerDocument._byId) {
      this._ownerDocument._byId.set(strVal, this);
    } else if (name === 'hidden') this._hidden = true;
    else if (name === 'disabled') this._disabled = true;
    else if (name === 'open') this._open = true;
    else if (name === 'href') this.href = strVal;
    else if (name === 'type') this.type = strVal;
    else if (name.startsWith('data-')) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_all, ch) => ch.toUpperCase());
      this.dataset[camel] = strVal;
    }
  }

  getAttribute(name) {
    if (name === 'class') return this.className;
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  hasAttribute(name) {
    if (name === 'hidden' && this._hidden) return true;
    if (name === 'disabled' && this._disabled) return true;
    if (name === 'open' && this._open) return true;
    return Object.prototype.hasOwnProperty.call(this.attributes, name);
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'class') this._classes.clear();
    else if (name === 'hidden') this._hidden = false;
    else if (name === 'disabled') this._disabled = false;
    else if (name === 'open') this._open = false;
    else if (name.startsWith('data-')) {
      const camel = name.slice(5).replace(/-([a-z])/g, (_all, ch) => ch.toUpperCase());
      delete this.dataset[camel];
    }
  }

  contains(other) {
    let cur = other;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentElement;
    }
    return false;
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }

  closest(selector) {
    let cur = this;
    while (cur && !cur.tagName.startsWith('#')) {
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

  addEventListener(type, fn, opts) {
    const capture = Boolean(typeof opts === 'boolean' ? opts : opts && opts.capture);
    const bucket = capture ? this.listeners.capture : this.listeners.bubble;
    (bucket[type] ||= []).push(fn);
  }

  removeEventListener(type, fn, opts) {
    const capture = Boolean(typeof opts === 'boolean' ? opts : opts && opts.capture);
    const bucket = capture ? this.listeners.capture : this.listeners.bubble;
    if (!bucket[type]) return;
    bucket[type] = bucket[type].filter((f) => f !== fn);
  }

  focus() {
    if (!this._ownerDocument) return;
    if (!isFocusableElement(this)) return;
    this._ownerDocument.activeElement = this;
  }

  blur() {
    if (this._ownerDocument && this._ownerDocument.activeElement === this) {
      this._ownerDocument.activeElement = this._ownerDocument.body;
    }
    const event = createEvent('blur', this, { bubbles: false, cancelable: false });
    this.dispatchEvent(event);
  }

  click() {
    if (this.disabled) return;
    const event = createEvent('click', this, { bubbles: true, cancelable: true });
    this.dispatchEvent(event);
    if (typeof this.onclick === 'function' && !event._immediateStopped) {
      this.onclick(event);
    }
  }

  dispatchEvent(event) {
    if (!event.target) event.target = this;
    const doc = this._ownerDocument;
    const win = doc?._ownerWindow;
    const pathNodes = [];
    let cur = this;
    while (cur) {
      pathNodes.unshift(cur);
      cur = cur.parentElement;
    }

    const invokeList = (targetObj, fns) => {
      if (!fns) return;
      for (const fn of [...fns]) {
        if (event._immediateStopped) break;
        event.currentTarget = targetObj;
        fn.call(targetObj, event);
      }
    };

    // Capture phase: window -> document -> ancestors -> target
    if (win) invokeList(win, win._listeners.capture[event.type]);
    if (!event._stopped && !event._immediateStopped && doc) {
      invokeList(doc, doc._listeners.capture[event.type]);
    }
    for (let i = 0; i < pathNodes.length; i++) {
      if (event._stopped || event._immediateStopped) break;
      invokeList(pathNodes[i], pathNodes[i].listeners.capture[event.type]);
    }

    // Bubble phase: target -> ancestors -> document -> window
    for (let i = pathNodes.length - 1; i >= 0; i--) {
      if (event._stopped || event._immediateStopped) break;
      if (i < pathNodes.length - 1 && !event.bubbles) break;
      invokeList(pathNodes[i], pathNodes[i].listeners.bubble[event.type]);
    }
    if (event.bubbles && !event._stopped && !event._immediateStopped && doc) {
      invokeList(doc, doc._listeners.bubble[event.type]);
    }
    if (event.bubbles && !event._stopped && !event._immediateStopped && win) {
      invokeList(win, win._listeners.bubble[event.type]);
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
    code: opts.code || '',
    keyCode: opts.keyCode ?? 0,
    which: opts.which ?? opts.keyCode ?? 0,
    shiftKey: Boolean(opts.shiftKey),
    ctrlKey: Boolean(opts.ctrlKey),
    metaKey: Boolean(opts.metaKey),
    altKey: Boolean(opts.altKey),
    isComposing: Boolean(opts.isComposing),
    bubbles: opts.bubbles ?? true,
    cancelable: opts.cancelable ?? true,
    detail: opts.detail,
    defaultPrevented: false,
    _stopped: false,
    _immediateStopped: false,
    stopPropagation() {
      this._stopped = true;
    },
    stopImmediatePropagation() {
      this._stopped = true;
      this._immediateStopped = true;
    },
    preventDefault() {
      if (this.cancelable) this.defaultPrevented = true;
    },
  };
}

function mountStack({
  initialWidth = 1440,
  account = null,
  authRequired = false,
  registrationAllowed = true,
  fetchImpl = null,
  enableReloadSpy = false,
} = {}) {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  const root = new DomElement('document');
  const byId = new Map();
  const stack = [root];

  const stripped = html
    .replace(/<!DOCTYPE[^>]*>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi, '');

  const docListeners = { capture: {}, bubble: {} };
  const winListeners = { capture: {}, bubble: {} };

  const documentObj = {
    _byId: byId,
    _listeners: docListeners,
    readyState: 'loading',
    documentElement: null,
    head: null,
    body: null,
    activeElement: null,
    getElementById(id) {
      const cached = byId.get(id);
      if (cached && cached.isConnected) return cached;
      return this.documentElement?.querySelector(`#${id}`) || cached || null;
    },
    querySelector(sel) {
      return this.documentElement?.querySelector(sel) || null;
    },
    querySelectorAll(sel) {
      return this.documentElement?.querySelectorAll(sel) || [];
    },
    createElement(tag) {
      const el = new DomElement(tag);
      el._ownerDocument = documentObj;
      return el;
    },
    addEventListener(type, fn, opts) {
      const capture = Boolean(typeof opts === 'boolean' ? opts : opts && opts.capture);
      const bucket = capture ? docListeners.capture : docListeners.bubble;
      (bucket[type] ||= []).push(fn);
    },
    removeEventListener(type, fn, opts) {
      const capture = Boolean(typeof opts === 'boolean' ? opts : opts && opts.capture);
      const bucket = capture ? docListeners.capture : docListeners.bubble;
      if (!bucket[type]) return;
      bucket[type] = bucket[type].filter((f) => f !== fn);
    },
    dispatchEvent(event) {
      if (!event.target) event.target = documentObj.body;
      for (const fn of [...(docListeners.capture[event.type] || [])]) {
        if (event._immediateStopped) break;
        event.currentTarget = documentObj;
        fn.call(documentObj, event);
      }
      if (!event._stopped && !event._immediateStopped) {
        for (const fn of [...(docListeners.bubble[event.type] || [])]) {
          if (event._immediateStopped) break;
          event.currentTarget = documentObj;
          fn.call(documentObj, event);
        }
      }
      if (event.bubbles && !event._stopped && !event._immediateStopped) {
        for (const fn of [...(winListeners.bubble[event.type] || [])]) {
          if (event._immediateStopped) break;
          event.currentTarget = windowObj;
          fn.call(windowObj, event);
        }
      }
      return !event.defaultPrevented;
    },
  };

  root._ownerDocument = documentObj;

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
    el._ownerDocument = documentObj;
    const attrs = parseAttributes(rawAttrs.replace(/\/$/, ''));
    for (const [k, v] of Object.entries(attrs)) {
      el.setAttribute(k, v);
    }
    if (el.id) byId.set(el.id, el);
    stack[stack.length - 1].appendChild(el);
    if (!selfClosing) stack.push(el);
  }

  const htmlEl = root.children.find((c) => c.tagName === 'HTML');
  const headEl = htmlEl.querySelector('head') || new DomElement('head');
  const bodyEl = htmlEl.querySelector('body');
  documentObj.documentElement = htmlEl;
  documentObj.head = headEl;
  documentObj.body = bodyEl;
  documentObj.activeElement = bodyEl;

  let reloadCount = 0;
  const fetchCalls = [];
  const storeData = new Map();
  const storage = {
    getItem: (k) => (storeData.has(k) ? storeData.get(k) : null),
    setItem: (k, v) => storeData.set(k, String(v)),
    removeItem: (k) => storeData.delete(k),
  };

  const windowObj = {
    _listeners: winListeners,
    document: documentObj,
    innerWidth: initialWidth,
    location: enableReloadSpy
      ? { origin: 'http://localhost', reload: () => { reloadCount += 1; } }
      : { origin: 'http://localhost' },
    matchMedia: (q) => ({
      matches: q.includes('max-width') ? initialWidth <= 1100 : false,
      media: q,
      addEventListener() {},
      removeEventListener() {},
    }),
    getComputedStyle: (el) => ({
      display: isElementVisible(el) ? (el.style?.display || 'block') : 'none',
      visibility: el.style?.visibility === 'hidden' ? 'hidden' : 'visible',
    }),
    addEventListener(type, fn, opts) {
      const capture = Boolean(typeof opts === 'boolean' ? opts : opts && opts.capture);
      const bucket = capture ? winListeners.capture : winListeners.bubble;
      (bucket[type] ||= []).push(fn);
    },
    removeEventListener(type, fn, opts) {
      const capture = Boolean(typeof opts === 'boolean' ? opts : opts && opts.capture);
      const bucket = capture ? winListeners.capture : winListeners.bubble;
      if (!bucket[type]) return;
      bucket[type] = bucket[type].filter((f) => f !== fn);
    },
    dispatchEvent(event) {
      for (const fn of [...(winListeners.capture[event.type] || [])]) {
        if (event._immediateStopped) break;
        event.currentTarget = windowObj;
        fn.call(windowObj, event);
      }
      if (!event._stopped && !event._immediateStopped) {
        for (const fn of [...(winListeners.bubble[event.type] || [])]) {
          if (event._immediateStopped) break;
          event.currentTarget = windowObj;
          fn.call(windowObj, event);
        }
      }
      return !event.defaultPrevented;
    },
  };
  documentObj._ownerWindow = windowObj;

  const defaultFetch = async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    if (url === '/api/account/status') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          account,
          required: authRequired,
          registration_allowed: registrationAllowed,
          features: [],
        }),
      };
    }
    if (url === '/api/materials/upload') {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          material: {
            doc_id: 42,
            filename: 'lecture.pdf',
            total_pages: 2,
            total_chars: 14,
            src_path: 'src/lecture.pdf',
            pages: [
              { page_num: 1, text: 'Slide 1' },
              { page_num: 2, text: 'Slide 2' },
            ],
          },
        }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({ sessions: [], items: [], models: [], knowledge_base: {}, documents: [] }),
    };
  };

  class MockFileReader {
    readAsDataURL() {
      this.result = 'data:application/pdf;base64,JVBERi0xLjQK';
      if (typeof this.onload === 'function') {
        this.onload({ target: this });
      }
    }
  }

  const sandbox = {
    window: windowObj,
    document: documentObj,
    navigator: { userAgent: 'node-test' },
    CSS: { escape: (s) => String(s).replace(/([^a-zA-Z0-9_-])/g, '\\$1') },
    localStorage: storage,
    sessionStorage: storage,
    fetch: fetchImpl || defaultFetch,
    FileReader: MockFileReader,
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail;
      }
    },
    Event: class Event {
      constructor(type, init = {}) {
        this.type = type;
        this.bubbles = Boolean(init.bubbles);
      }
    },
    URL: { createObjectURL: () => 'blob:mock', revokeObjectURL() {} },
    URLSearchParams,
    TextDecoder,
    TextEncoder,
    confirm: () => true,
    alert: () => {},
    prompt: () => null,
    setTimeout,
    clearTimeout,
    setInterval: () => 1,
    clearInterval: () => {},
    requestAnimationFrame: (cb) => {
      if (typeof cb === 'function') cb(0);
      return 1;
    },
    cancelAnimationFrame: () => {},
    console: { log() {}, warn() {}, error() {} },
  };
  sandbox.globalThis = sandbox;
  sandbox.self = windowObj;
  Object.assign(windowObj, {
    CSS: sandbox.CSS,
    CustomEvent: sandbox.CustomEvent,
    Event: sandbox.Event,
    localStorage: storage,
    sessionStorage: storage,
    fetch: sandbox.fetch,
  });

  vm.createContext(sandbox);
  for (const filePath of [SPATIAL_JS_PATH, APP_JS_PATH, ACCOUNTS_JS_PATH]) {
    const code = fs.readFileSync(filePath, 'utf8');
    vm.runInContext(code, sandbox, { filename: filePath });
  }

  documentObj.readyState = 'complete';
  documentObj.dispatchEvent(createEvent('DOMContentLoaded', documentObj, { bubbles: false }));

  return {
    sandbox,
    document: documentObj,
    window: windowObj,
    fetchCalls,
    getReloadCount: () => reloadCount,
  };
}

function flushMicrotasks() {
  return new Promise((resolve) => setTimeout(resolve, 15));
}

function pressKey(target, opts = {}) {
  const ev = createEvent('keydown', target, { bubbles: true, cancelable: true, ...opts });
  target.dispatchEvent(ev);
  return ev;
}

test('UI-AUTH-ENTER: Enter in #account-password or #account-username submits login once without registration or disabling visible button', async () => {
  let resolveFetch;
  const requests = [];
  const h = mountStack({
    fetchImpl: (url, opts) => {
      requests.push({ url, opts });
      if (url === '/api/account/status') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ account: null, required: false, registration_allowed: true, features: [] }),
        });
      }
      if (url === '/api/account/login') {
        return new Promise((resolve) => {
          resolveFetch = resolve;
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
    },
  });
  await flushMicrotasks();

  const trigger = h.document.getElementById('btn-account-trigger');
  trigger.focus();
  trigger.click();

  const userEl = h.document.getElementById('account-username');
  const passEl = h.document.getElementById('account-password');
  const loginBtn = h.document.getElementById('btn-account-login');
  const regBtn = h.document.getElementById('btn-account-register');
  userEl.value = 'alice_dev';
  passEl.value = 'invalid-password-1234';
  passEl.focus();

  const ev1 = pressKey(passEl, { key: 'Enter' });
  await flushMicrotasks();

  const loginRequests = () => requests.filter((r) => r.url === '/api/account/login');
  const registerRequests = () => requests.filter((r) => r.url === '/api/account/register');

  assert.equal(ev1.defaultPrevented, true, 'Enter in password input must preventDefault');
  assert.equal(loginRequests().length, 1, 'Enter in password input must submit login once');
  assert.equal(registerRequests().length, 0, 'Enter must never call register');
  assert.equal(loginBtn.disabled, false, 'Must NOT set disabled=true because CSS hides .account-btn:disabled');
  assert.equal(loginBtn.getAttribute('aria-disabled'), 'true');
  assert.equal(isElementVisible(loginBtn), true, 'Login button must stay visible while in-flight');

  // Repeated Enter and button click while in-flight must NOT duplicate requests
  pressKey(passEl, { key: 'Enter' });
  pressKey(userEl, { key: 'Enter' });
  loginBtn.click();
  regBtn.click();
  await flushMicrotasks();
  assert.equal(loginRequests().length, 1, 'In-flight guard must block duplicate Enter/click submits');
  assert.equal(registerRequests().length, 0, 'In-flight guard must block register while login is pending');

  // Resolve with 401 invalid credentials: error shown in accessible alert, lock released
  resolveFetch({
    ok: false,
    status: 401,
    json: async () => ({ detail: 'Sai tên tài khoản hoặc mật khẩu.' }),
  });
  await flushMicrotasks();

  const errEl = h.document.getElementById('account-auth-error');
  assert.match(errEl.textContent, /Sai tên tài khoản/i);
  assert.equal(errEl.getAttribute('role'), 'alert');
  assert.equal(errEl.getAttribute('aria-live'), 'assertive');
  assert.equal(loginBtn.getAttribute('aria-disabled'), null);
  assert.equal(h.document.getElementById('account-auth-modal').classList.contains('is-open'), true);

  // Submitting from #account-username with Enter also works after error recovery
  userEl.focus();
  const ev2 = pressKey(userEl, { key: 'Enter' });
  await flushMicrotasks();
  assert.equal(ev2.defaultPrevented, true);
  assert.equal(loginRequests().length, 2, 'Enter in username input must submit login once lock releases');

  // Reopen lock/fail second request so we verify background Enter outside auth modal is blocked
  resolveFetch({ ok: false, status: 401, json: async () => ({ detail: 'Sai lần 2.' }) });
  await flushMicrotasks();
  const bgInput = h.document.getElementById('user-input');
  const bgEnter = pressKey(bgInput, { key: 'Enter' });
  await flushMicrotasks();
  assert.equal(bgEnter.defaultPrevented, true, 'Background Enter behind auth modal must be blocked');
  assert.equal(loginRequests().length, 2, 'Blocked background Enter must not trigger auth submit');
  assert.equal(
    h.document.getElementById('account-auth-modal').contains(h.document.activeElement),
    true,
    'Blocked background Enter must pull focus back into auth modal'
  );
});

test('UI-AUTH-IME: IME composition Enter does not submit login until composition ends', async () => {
  const requests = [];
  const h = mountStack({
    fetchImpl: (url, opts) => {
      requests.push({ url, opts });
      if (url === '/api/account/status') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ account: null, required: false, registration_allowed: true, features: [] }),
        });
      }
      return Promise.resolve({ ok: false, status: 401, json: async () => ({ detail: 'bad creds' }) });
    },
  });
  await flushMicrotasks();

  h.document.getElementById('btn-account-trigger').click();
  const userEl = h.document.getElementById('account-username');
  const passEl = h.document.getElementById('account-password');
  userEl.value = 'nguoi_dung_vn';
  passEl.value = 'mat_khau_du_12_ky_tu';
  userEl.focus();

  userEl.dispatchEvent(createEvent('compositionstart', userEl, { bubbles: true }));
  pressKey(userEl, { key: 'Enter' });
  await flushMicrotasks();
  assert.equal(requests.filter((r) => r.url === '/api/account/login').length, 0);

  userEl.dispatchEvent(createEvent('compositionend', userEl, { bubbles: true }));
  pressKey(userEl, { key: 'Enter', isComposing: true });
  pressKey(passEl, { key: 'Enter', keyCode: 229 });
  await flushMicrotasks();
  assert.equal(requests.filter((r) => r.url === '/api/account/login').length, 0);

  // Real Enter after composition ended submits once
  pressKey(userEl, { key: 'Enter' });
  await flushMicrotasks();
  assert.equal(requests.filter((r) => r.url === '/api/account/login').length, 1);
});

test('UI-AUTH-ESCAPE: Switching via hidden #btn-account-switch restores focus to visible #btn-account-trigger instead of dropping to body, and traps Tab', async () => {
  const h = mountStack({
    account: { id: 1, username: 'studier', role: 'student' },
    registrationAllowed: false,
  });
  await flushMicrotasks();

  const trigger = h.document.getElementById('btn-account-trigger');
  trigger.focus();
  trigger.click();

  const menu = h.document.getElementById('account-popover-menu');
  assert.equal(menu.classList.contains('is-open'), true);
  const switchBtn = h.document.getElementById('btn-account-switch');
  assert.ok(switchBtn);
  switchBtn.focus();
  assert.equal(h.document.activeElement, switchBtn);

  // Clicking switch hides menu (display: none) BEFORE opening auth modal
  switchBtn.click();
  assert.equal(menu.classList.contains('is-open'), false);
  const authModal = h.document.getElementById('account-auth-modal');
  assert.equal(authModal.classList.contains('is-open'), true);

  // Verify Tab/Shift+Tab wrap skips hidden register button
  const userEl = h.document.getElementById('account-username');
  const passEl = h.document.getElementById('account-password');
  const cancelBtn = h.document.getElementById('btn-account-cancel');
  const loginBtn = h.document.getElementById('btn-account-login');
  assert.equal(h.document.activeElement?.id, 'account-username');

  const shiftTabFirst = pressKey(userEl, { key: 'Tab', shiftKey: true });
  assert.equal(shiftTabFirst.defaultPrevented, true);
  assert.equal(h.document.activeElement?.id, 'btn-account-login');

  const tabLast = pressKey(loginBtn, { key: 'Tab' });
  assert.equal(tabLast.defaultPrevented, true);
  assert.equal(h.document.activeElement?.id, 'account-username');

  // Escape closes auth modal and restores focus to connected visible #btn-account-trigger (never body or hidden switchBtn)
  passEl.focus();
  const esc = pressKey(passEl, { key: 'Escape' });
  assert.equal(esc.defaultPrevented, true);
  assert.equal(authModal.classList.contains('is-open'), false);
  assert.equal(h.document.activeElement === h.document.body, false, 'Focus must not drop to body after Escape');
  assert.equal(h.document.activeElement?.id, trigger.id, 'Focus must restore to visible #btn-account-trigger');
  assert.equal(isElementVisible(h.document.activeElement), true);
  assert.ok(cancelBtn);
});

test('UI-PDF-MODAL: Document content modal sets ARIA, moves opening focus inside, traps Tab/Shift+Tab, preserves original URLs, blocks background Enter, and Escape closes topmost only restoring opener focus', async () => {
  const h = mountStack({
    initialWidth: 900,
    account: { id: 1, username: 'studier', role: 'student' },
  });
  await flushMicrotasks();

  const docModal = h.document.getElementById('document-content-modal');
  assert.equal(docModal.getAttribute('role'), 'dialog');
  assert.equal(docModal.getAttribute('aria-modal'), 'true');
  assert.equal(docModal.getAttribute('tabindex'), '-1');
  assert.equal(docModal.getAttribute('aria-hidden'), 'true');

  // Open sidebar drawer behind the future viewer modal to verify Escape topmost isolation
  const openSidebarBtn = h.document.getElementById('btn-open-sidebar');
  if (openSidebarBtn) openSidebarBtn.click();
  const hadDrawerOpen = h.document.body.classList.contains('drawer-open');

  // Attach a PDF via #material-file-input to create a real .attachment-card-open trigger
  const fileInput = h.document.getElementById('material-file-input');
  assert.ok(fileInput, '#material-file-input must exist in static/index.html');
  fileInput.files = [{ name: 'lecture.pdf', size: 1024, type: 'application/pdf' }];
  fileInput.dispatchEvent(createEvent('change', fileInput, { bubbles: true }));
  await flushMicrotasks();

  const openCardBtn = h.document.querySelector('#chat-attachment-preview-tray .attachment-card-open');
  assert.ok(openCardBtn, 'Uploading attachment must render .attachment-card-open button');

  // Have underlying #account-auth-modal and #account-popover-menu states behind the viewer to verify Escape topmost isolation
  const authModal = h.document.getElementById('account-auth-modal');
  const menuEl = h.document.getElementById('account-popover-menu');
  authModal.classList.add('is-open');
  menuEl.classList.add('is-open');

  openCardBtn.focus();
  assert.equal(h.document.activeElement === openCardBtn, true);

  // Open Document Content Viewer from attachment card button
  openCardBtn.click();
  menuEl.classList.add('is-open');

  assert.equal(docModal.style.display, 'flex');
  assert.equal(docModal.classList.contains('active'), true);
  assert.equal(docModal.getAttribute('aria-hidden'), 'false');
  assert.equal(
    docModal.contains(h.document.activeElement),
    true,
    'Opening document viewer must move focus inside #document-content-modal'
  );

  // Verify authenticated source links remain intact
  const srcBadge = h.document.getElementById('doc-content-src-badge');
  const origLink = srcBadge.querySelector('a');
  assert.ok(origLink);
  assert.equal(origLink.getAttribute('href'), '/api/documents/42/original');
  const htmlSnapshot = h.document.getElementById('doc-content-pages-list').innerHTML;
  assert.match(htmlSnapshot, /\/api\/documents\/42\/pages\/1\/image/);
  assert.match(htmlSnapshot, /\/api\/documents\/42\/pdf#page=1/);

  // Focusables trap: Tab on last wraps to first; Shift+Tab on first wraps to last
  const focusables = docModal
    .querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex]')
    .filter((el) => isElementVisible(el) && !el.disabled && el.getAttribute('tabindex') !== '-1');
  assert.ok(focusables.length >= 3, 'Document viewer modal should expose multiple visible focusable controls');
  const first = focusables[0];
  const last = focusables[focusables.length - 1];

  first.focus();
  const st = pressKey(first, { key: 'Tab', shiftKey: true });
  assert.equal(st.defaultPrevented, true);
  assert.equal(h.document.activeElement === last, true, 'Shift+Tab at first focusable must wrap to last');

  const tb = pressKey(last, { key: 'Tab' });
  assert.equal(tb.defaultPrevented, true);
  assert.equal(h.document.activeElement === first, true, 'Tab at last focusable must wrap to first');

  // Enter inside modal search input must NOT be blocked by background-Enter guard
  const searchInput = h.document.getElementById('doc-content-search-input');
  searchInput.focus();
  const insideEnter = pressKey(searchInput, { key: 'Enter' });
  assert.equal(insideEnter.defaultPrevented, false, 'Enter inside viewer modal should remain allowed');

  // Background Enter guard while viewer is open: background #user-input Enter must be blocked
  const bgInput = h.document.getElementById('user-input');
  const bgEnter = pressKey(bgInput, { key: 'Enter' });
  assert.equal(bgEnter.defaultPrevented, true, 'Enter targeting behind open modal must be prevented');
  assert.equal(docModal.contains(h.document.activeElement), true, 'Blocked background Enter pulls focus back inside modal');

  // Escape closes ONLY #document-content-modal, leaves underlying popover/drawer untouched, and restores opener focus
  const escEv = pressKey(h.document.activeElement, { key: 'Escape' });
  assert.equal(escEv.defaultPrevented, true);
  assert.equal(docModal.style.display, 'none');
  assert.equal(docModal.classList.contains('active'), false);
  assert.equal(docModal.getAttribute('aria-hidden'), 'true');
  assert.equal(
    h.document.getElementById('account-popover-menu').classList.contains('is-open'),
    true,
    'Escape on topmost document viewer must not close underlying popover menu'
  );
  assert.equal(
    authModal.classList.contains('is-open'),
    true,
    'Escape on topmost document viewer must not close underlying account auth modal'
  );
  if (hadDrawerOpen) {
    assert.equal(
      h.document.body.classList.contains('drawer-open'),
      true,
      'Escape on topmost document viewer must not close underlying sidebar drawer'
    );
  }
  assert.equal(h.document.activeElement === openCardBtn, true, 'Closing viewer must restore focus to opener card button');

  // Subsequent Escapes unwind strictly one layer at a time: authModal -> menuEl -> drawer
  pressKey(h.document.activeElement, { key: 'Escape' });
  assert.equal(authModal.classList.contains('is-open'), false, 'Second Escape closes account auth modal');
  assert.equal(menuEl.classList.contains('is-open'), true, 'Second Escape must not close popover menu');
  pressKey(h.document.activeElement, { key: 'Escape' });
  assert.equal(menuEl.classList.contains('is-open'), false, 'Third Escape closes popover menu');
  if (hadDrawerOpen) {
    assert.equal(h.document.body.classList.contains('drawer-open'), true, 'Third Escape must not close drawer');
    pressKey(h.document.activeElement, { key: 'Escape' });
    assert.equal(h.document.body.classList.contains('drawer-open'), false, 'Fourth Escape closes drawer');
  }

  // If a child native <dialog id="practice-dialog" open> is open on top, Escape must not close #document-content-modal underneath
  openCardBtn.click();
  assert.equal(docModal.classList.contains('active'), true);
  const practiceDialog = h.document.getElementById('practice-dialog');
  practiceDialog.setAttribute('open', '');
  practiceDialog.open = true;
  const escUnderDialog = pressKey(practiceDialog, { key: 'Escape' });
  assert.equal(docModal.classList.contains('active'), true, 'Viewer must not steal Escape from open <dialog>');
  assert.equal(escUnderDialog.defaultPrevented, false);

  // Close practice-dialog, remove opener card from DOM, verify safe visible fallback on Escape
  practiceDialog.removeAttribute('open');
  practiceDialog.open = false;
  openCardBtn.remove();
  first.focus();
  pressKey(first, { key: 'Escape' });
  assert.equal(docModal.classList.contains('active'), false);
  assert.equal(h.document.activeElement === h.document.body, false, 'Fallback focus must land on a visible UI control, not body');
  assert.equal(isElementVisible(h.document.activeElement), true);
});
