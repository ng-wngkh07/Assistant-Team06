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

    const summaryPayload = {
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
            },
        },
    };
    summaryPayload.watcher = summaryPayload.storage.watcher;

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
    let resolveFullModal = null;
    const requestedUrls = [];
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), {
        document: doc,
        window: { addEventListener() {} },
        confirm: () => true,
        fetch: async (url) => {
            requestedUrls.push(String(url));
            if (url === '/api/status?summary=1') {
                return { ok: true, json: async () => summaryPayload };
            }
            if (url === '/api/status') {
                return await new Promise((resolve) => {
                    resolveFullModal = () => resolve({ ok: true, json: async () => statusPayload });
                });
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

    assert.ok(requestedUrls.includes('/api/status?summary=1'), 'Sidebar bootstrap must poll summary status');
    assert.equal(els.get('stat-docs').textContent, 1);
    assert.match(els.get('btn-open-kb').textContent, /1\/6 tài liệu/);

    const watchText = els.get('storage-watch-status').textContent;
    assert.match(watchText, /OCR tự động: Bật/);
    assert.match(watchText, /Đã chụp chờ duyệt: 2/);
    assert.match(watchText, /HOLD: 1/);
    assert.match(watchText, /Chờ vector: 1/);

    const openKbFn = (els.get('btn-open-kb').listeners.click || [])[0];
    assert.ok(openKbFn, 'btn-open-kb click listener should be registered');
    const modalPromise = openKbFn();
    assert.match(els.get('kb-table-body').innerHTML, /Đang tải/);
    assert.ok(resolveFullModal, 'Opening KB modal must fetch full /api/status');
    resolveFullModal();
    await modalPromise;

    const kbHtml = els.get('kb-table-body').innerHTML;
    assert.match(kbHtml, /Vector sẵn sàng/);
    assert.match(kbHtml, /HOLD 1/);
    assert.match(kbHtml, /Duyệt OCR/);
});

test('Summary polls preserve full KB modal details, KB modal reports fetch errors, and OCR chooser uses full /api/status', async () => {
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

    const intervalFns = [];
    let fullShouldFail = false;
    const statusUrls = [];
    const windowObj = { addEventListener() {} };
    const store = { getItem() { return null; }, setItem() {}, removeItem() {} };

    const fullPayload = {
        inference_ready: true,
        knowledge_base: {
            total_documents: 1,
            registered_documents: 1,
            discovered_files: 1,
            searchable_documents: 1,
            scanned_documents: 0,
            total_indexed_pages: 5,
            total_chunks: 3,
            embedded_chunks: 3,
            documents: [
                {
                    filename: 'ocr_candidate.pdf',
                    clean_title: 'OCR Candidate',
                    total_pages: 5,
                    extracted_pages_count: 5,
                    is_scanned: false,
                    status: 'indexed',
                    searchable: true,
                    embedding_status: 'vectorized',
                },
            ],
        },
        storage: {
            watcher: {
                enabled: false,
                items: [{ filename: 'ocr_candidate.pdf', status: 'indexed' }],
            },
        },
    };
    fullPayload.watcher = fullPayload.storage.watcher;

    let deferredSummaryResolve = null;
    let deferNextSummary = false;

    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), {
        document: doc,
        window: windowObj,
        confirm: () => true,
        fetch: async (url) => {
            if (String(url).startsWith('/api/status')) {
                statusUrls.push(String(url));
            }
            if (url === '/api/status?summary=1') {
                if (deferNextSummary) {
                    deferNextSummary = false;
                    return await new Promise((resolve) => {
                        deferredSummaryResolve = () => resolve({
                            ok: true,
                            json: async () => ({
                                inference_ready: true,
                                knowledge_base: {
                                    total_documents: 99,
                                    registered_documents: 99,
                                    discovered_files: 99,
                                    searchable_documents: 99,
                                    scanned_documents: 0,
                                    total_indexed_pages: 99,
                                    total_chunks: 99,
                                    embedded_chunks: 99,
                                },
                                storage: { watcher: { enabled: false } },
                            }),
                        });
                    });
                }
                return {
                    ok: true,
                    json: async () => ({
                        inference_ready: true,
                        knowledge_base: {
                            total_documents: 1,
                            registered_documents: 1,
                            discovered_files: 1,
                            searchable_documents: 1,
                            scanned_documents: 0,
                            total_indexed_pages: 5,
                            total_chunks: 3,
                            embedded_chunks: 3,
                        },
                        storage: { watcher: { enabled: false } },
                    }),
                };
            }
            if (url === '/api/status') {
                if (fullShouldFail) {
                    return { ok: false, status: 500, json: async () => ({ detail: 'Server error' }) };
                }
                return { ok: true, json: async () => fullPayload };
            }
            return { ok: true, json: async () => ({ records: [], sessions: [], items: [], models: [], default_model: 'local' }) };
        },
        localStorage: store,
        sessionStorage: store,
        setInterval(fn) { intervalFns.push(fn); return intervalFns.length; },
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

    // Start an in-flight summary poll, then open KB modal (full) before summary finishes.
    deferNextSummary = true;
    const staleSummaryPromise = intervalFns[0]();
    const openKbFn = (els.get('btn-open-kb').listeners.click || [])[0];
    await openKbFn();

    assert.equal(windowObj.kbData.length, 1);
    assert.equal(windowObj.watcherItems.length, 1);
    assert.match(els.get('kb-table-body').innerHTML, /OCR Candidate/);

    // Resolving the earlier summary poll must NOT overwrite newer full stats or clear kbData/watcherItems.
    deferredSummaryResolve();
    await staleSummaryPromise;
    assert.equal(els.get('stat-docs').textContent, 1);
    assert.equal(windowObj.kbData.length, 1);
    assert.equal(windowObj.watcherItems.length, 1);

    // Even a newer subsequent summary poll without documents/items must NOT wipe out kbData/watcherItems.
    await intervalFns[0]();
    assert.equal(windowObj.kbData.length, 1);
    assert.equal(windowObj.watcherItems.length, 1);

    // KB modal error state on full /api/status failure.
    fullShouldFail = true;
    await openKbFn();
    assert.match(els.get('kb-table-body').innerHTML, /Không thể tải danh sách tài liệu/);

    // OCR chooser must continue requesting full /api/status.
    fullShouldFail = false;
    const beforeOcrIdx = statusUrls.length;
    const openOcrFn = (els.get('btn-open-ocr-review').listeners.click || [])[0];
    assert.ok(openOcrFn, 'btn-open-ocr-review click listener should be registered');
    openOcrFn();
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setTimeout(r, 10));
    assert.ok(statusUrls.slice(beforeOcrIdx).includes('/api/status'), 'OCR chooser must request full /api/status');
    assert.equal(els.get('ocr-run-filename').children.length, 1);
});

test('Delayed full modal response and stale errors keep newer sidebar, GPU banner, and coverage state', { timeout: 5000 }, async () => {
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
            this.className = '';
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

    const intervalFns = [];
    const windowObj = { addEventListener() {} };
    const store = { getItem() { return null; }, setItem() {}, removeItem() {} };
    const deferredFull = [];
    const deferredSummary = [];
    let deferSummary = false;
    let summaryPayload = {
        inference_ready: true,
        gpu: { busy: false },
        knowledge_base: {
            total_documents: 1,
            registered_documents: 1,
            discovered_files: 1,
            searchable_documents: 1,
            scanned_documents: 0,
            total_indexed_pages: 5,
            total_chunks: 10,
            embedded_chunks: 1,
        },
        storage: { watcher: { enabled: true, running: true, hold_count: 0 } },
    };

    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), {
        document: doc,
        window: windowObj,
        confirm: () => true,
        fetch: (url) => {
            if (url === '/api/status?summary=1') {
                if (deferSummary) {
                    deferSummary = false;
                    return new Promise((resolve, reject) => {
                        deferredSummary.push({ resolve, reject });
                    });
                }
                return Promise.resolve({ ok: true, status: 200, json: async () => summaryPayload });
            }
            if (url === '/api/status') {
                return new Promise((resolve, reject) => {
                    deferredFull.push({ resolve, reject });
                });
            }
            if (url === '/api/model/check') {
                return Promise.resolve({ ok: true, status: 200, json: async () => ({ ready: true, message: 'OK' }) });
            }
            return Promise.resolve({ ok: true, status: 200, json: async () => ({ sessions: [], items: [], models: [], default_model: 'local' }) });
        },
        localStorage: store,
        sessionStorage: store,
        setInterval(fn) { intervalFns.push(fn); return intervalFns.length; },
        setTimeout() {},
        clearTimeout() {},
        Event: class { constructor(t) { this.type = t; } preventDefault() {} },
        URLSearchParams,
        TextDecoder,
        TextEncoder,
        console: { log() {}, warn() {}, error() {} },
    });

    for (const fn of (events.DOMContentLoaded || [])) await fn();
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(els.get('stat-docs').textContent, 1);

    // 1. Start full modal request, then update sidebar/GPU/coverage via a newer summary poll.
    const openKbFn = (els.get('btn-open-kb').listeners.click || [])[0];
    const modalPromise1 = openKbFn();
    assert.equal(deferredFull.length, 1);

    summaryPayload = {
        inference_ready: false,
        gpu: { busy: true, message: 'Đang huấn luyện phiên mới' },
        knowledge_base: {
            total_documents: 3,
            registered_documents: 3,
            discovered_files: 3,
            searchable_documents: 2,
            scanned_documents: 1,
            total_indexed_pages: 20,
            total_chunks: 12,
            embedded_chunks: 9,
        },
        storage: { watcher: { enabled: true, running: true, hold_count: 4 } },
    };
    await intervalFns[0]();
    assert.equal(els.get('stat-docs').textContent, 2);
    assert.match(els.get('btn-open-kb').textContent, /2\/3 tài liệu/);
    assert.equal(els.get('stat-chunks').textContent, 12);
    assert.equal(els.get('training-banner').style.display, 'flex');
    assert.equal(els.get('training-banner-text').textContent, 'Đang huấn luyện phiên mới');
    assert.match(els.get('ollama-status').textContent, /GPU bận/);
    assert.match(els.get('storage-watch-status').textContent, /HOLD: 4/);

    // Completing the older full request must render modal details while preserving newer sidebar/GPU/coverage state.
    deferredFull[0].resolve({
        ok: true,
        status: 200,
        json: async () => ({
            inference_ready: true,
            gpu: { busy: false },
            knowledge_base: {
                total_documents: 1,
                registered_documents: 1,
                discovered_files: 1,
                searchable_documents: 1,
                scanned_documents: 0,
                total_indexed_pages: 5,
                total_chunks: 10,
                embedded_chunks: 1,
                documents: [{
                    filename: 'old_modal_doc.pdf',
                    clean_title: 'Old Modal Doc',
                    total_pages: 4,
                    extracted_pages_count: 4,
                    is_scanned: false,
                    status: 'indexed',
                    searchable: true,
                    embedding_status: 'vectorized',
                }],
            },
            storage: {
                watcher: {
                    enabled: true,
                    running: true,
                    hold_count: 0,
                    items: [{ filename: 'old_modal_doc.pdf', status: 'indexed' }],
                },
            },
        }),
    });
    await modalPromise1;
    assert.match(els.get('kb-table-body').innerHTML, /Old Modal Doc/);
    assert.equal(windowObj.kbData[0].filename, 'old_modal_doc.pdf');
    assert.equal(windowObj.watcherItems[0].filename, 'old_modal_doc.pdf');
    assert.equal(els.get('stat-docs').textContent, 2);
    assert.match(els.get('btn-open-kb').textContent, /2\/3 tài liệu/);
    assert.equal(els.get('stat-chunks').textContent, 12);
    assert.equal(els.get('training-banner').style.display, 'flex');
    assert.equal(els.get('training-banner-text').textContent, 'Đang huấn luyện phiên mới');
    assert.match(els.get('ollama-status').textContent, /GPU bận/);
    assert.match(els.get('storage-watch-status').textContent, /HOLD: 4/);

    // 2. A delayed full request failing with HTTP 500 or network error must not overwrite newer sidebar/GPU state.
    const modalPromise2 = openKbFn();
    assert.equal(deferredFull.length, 2);
    summaryPayload = {
        inference_ready: true,
        gpu: { busy: false },
        knowledge_base: {
            total_documents: 5,
            registered_documents: 5,
            discovered_files: 5,
            searchable_documents: 5,
            scanned_documents: 0,
            total_indexed_pages: 30,
            total_chunks: 20,
            embedded_chunks: 20,
        },
        storage: { watcher: { enabled: false } },
    };
    await intervalFns[0]();
    assert.equal(els.get('stat-docs').textContent, 5);
    assert.equal(els.get('training-banner').style.display, 'none');
    assert.match(els.get('ollama-status').textContent, /sẵn sàng/);

    deferredFull[1].resolve({ ok: false, status: 500, json: async () => ({ detail: 'Modal failed' }) });
    await modalPromise2;
    assert.match(els.get('kb-table-body').innerHTML, /Không thể tải danh sách tài liệu/);
    assert.equal(els.get('stat-docs').textContent, 5);
    assert.match(els.get('ollama-status').textContent, /sẵn sàng/);

    const modalPromise3 = openKbFn();
    assert.equal(deferredFull.length, 3);
    deferredFull[2].reject(new Error('Modal transport dropped'));
    await modalPromise3;
    assert.match(els.get('kb-table-body').innerHTML, /Không thể tải danh sách tài liệu/);
    assert.equal(els.get('stat-docs').textContent, 5);
    assert.match(els.get('ollama-status').textContent, /sẵn sàng/);

    // 3. Stale summary HTTP/network failure after a newer full request must not overwrite newer sidebar state.
    deferSummary = true;
    const staleFailedSummary = intervalFns[0]();
    const modalPromise4 = openKbFn();
    assert.equal(deferredFull.length, 4);
    deferredFull[3].resolve({
        ok: true,
        status: 200,
        json: async () => ({
            inference_ready: true,
            gpu: { busy: false },
            knowledge_base: {
                total_documents: 6,
                registered_documents: 6,
                discovered_files: 6,
                searchable_documents: 6,
                scanned_documents: 0,
                total_indexed_pages: 36,
                total_chunks: 24,
                embedded_chunks: 24,
                documents: [{ filename: 'newer_full.pdf', clean_title: 'Newer Full', total_pages: 6, extracted_pages_count: 6, is_scanned: false, status: 'indexed', searchable: true, embedding_status: 'vectorized' }],
            },
            storage: { watcher: { enabled: false, items: [] } },
        }),
    });
    await modalPromise4;
    assert.equal(els.get('stat-docs').textContent, 6);

    deferredSummary[0].reject(new Error('Stale summary error'));
    await staleFailedSummary;
    assert.equal(els.get('stat-docs').textContent, 6);
    assert.match(els.get('ollama-status').textContent, /sẵn sàng/);
});
