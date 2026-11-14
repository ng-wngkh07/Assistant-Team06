const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('Sidebar stats, storage watcher status, and KB modal distinguish searchable vs scanned/captured/HOLD/vector states honestly', async () => {
    const els = new Map();
    const events = {};
    class El {
        constructor(id = '') {
            this.id = id;
            this.listeners = {};
            this.style = {};
            this.dataset = {};
            this.value = '';
            this.textContent = '';
            this.innerHTML = '';
            this.title = '';
            this.disabled = false;
            this.children = [];
            const classes = new Set();
            this.classList = {
                add(x) { classes.add(x); },
                remove(x) { classes.delete(x); },
                toggle(x) { classes.has(x) ? classes.delete(x) : classes.add(x); },
                contains(x) { return classes.has(x); },
            };
        }
        addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
        dispatchEvent(e) { for (const fn of this.listeners[e.type] || []) fn(e); return true; }
        remove() {}
        setAttribute() {}
        getAttribute() { return null; }
        appendChild(x) { this.children.push(x); }
        append(...xs) { this.children.push(...xs); }
        insertAdjacentElement(where, el) { if (el && el.id) els.set(el.id, el); }
        replaceChildren() {}
        focus() {}
        scrollIntoView() {}
        querySelector() { return new El(); }
        querySelectorAll() { return []; }
        contains() { return false; }
    }

    const doc = {
        getElementById(id) {
            if (!els.has(id)) els.set(id, new El(id));
            return els.get(id);
        },
        createElement(tag) { return new El(tag); },
        addEventListener(name, fn) { (events[name] ||= []).push(fn); },
        querySelectorAll() { return []; },
        querySelector() { return new El(); },
        dispatchEvent(e) { for (const fn of events[e.type] || []) fn(e); },
        body: new El('body'),
    };
    doc.getElementById('processing-level').value = 'auto';

    const statusPayload = {
        inference_ready: false,
        service_message: 'Môi trường đang chạy kiểm thử',
        knowledge_base: {
            total_documents: 6,
            registered_documents: 6,
            discovered_files: 6,
            searchable_documents: 1,
            scanned_documents: 5,
            total_indexed_pages: 12,
            total_chunks: 4,
            embedded_chunks: 2,
            documents: [
                {
                    filename: 'digital_ready.pdf',
                    clean_title: 'Digital Ready',
                    total_pages: 12,
                    extracted_pages_count: 12,
                    is_scanned: false,
                    status: 'indexed',
                    searchable: true,
                    embedding_status: 'vectorized',
                },
                {
                    filename: 'scan_hold.pdf',
                    clean_title: 'Scan Hold',
                    total_pages: 100,
                    extracted_pages_count: 0,
                    is_scanned: true,
                    status: 'scanned_unocred',
                    searchable: false,
                    ocr_captured_pages: 2,
                    ocr_reviewed_pages: 0,
                },
            ],
        },
        storage: {
            sources_dir: null,
            watcher: {
                enabled: true,
                running: true,
                auto_ocr: true,
                ocr_runtime_available: true,
                indexed_count: 1,
                captured_pending_review_count: 2,
                hold_count: 1,
                needs_ocr_count: 2,
                vector_pending_count: 1,
                error_count: 0,
                items: [
                    {
                        filename: 'scan_hold.pdf',
                        status: 'hold',
                        ocr_pages_captured: 2,
                        ocr_pages_reviewed: 0,
                        ocr_pages_hold: 1,
                    },
                ],
            },
        },
        watcher: null,
    };
    statusPayload.watcher = statusPayload.storage.watcher;

    const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), {
        document: doc,
        window: { addEventListener() {} },
        confirm: () => true,
        fetch: async (url) => {
            if (url === '/api/status') {
                return { ok: true, json: async () => statusPayload };
            }
            return { ok: true, json: async () => ({ sessions: [], items: [], models: [], default_model: 'local' }) };
        },
        localStorage: store,
        sessionStorage: store,
        setInterval() {},
        setTimeout() {},
        clearTimeout() {},
        Event: class { constructor(t) { this.type = t; } preventDefault() {} },
        URLSearchParams,
        TextDecoder,
        TextEncoder,
        console,
    });

    for (const fn of (events.DOMContentLoaded || [])) await fn();
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setTimeout(r, 10));

    assert.equal(els.get('stat-docs').textContent, 1);
    assert.match(els.get('btn-open-kb').textContent, /1\/6 tài liệu/);

    const watchText = els.get('storage-watch-status').textContent;
    assert.match(watchText, /OCR tự động: Bật/);
    assert.match(watchText, /Đã chụp chờ duyệt: 2/);
    assert.match(watchText, /HOLD: 1/);
    assert.match(watchText, /Chờ vector: 1/);

    const openKbFn = (els.get('btn-open-kb').listeners.click || [])[0];
    assert.ok(openKbFn, 'btn-open-kb click listener should be registered');
    openKbFn();
    const kbHtml = els.get('kb-table-body').innerHTML;
    assert.match(kbHtml, /Vector sẵn sàng/);
    assert.match(kbHtml, /HOLD 1/);
    assert.match(kbHtml, /Duyệt OCR/);
});
