const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the real UI event handler with the network and DOM at their boundaries.
async function render(item) {
    class Element {
        constructor(tag) {
            this.tagName = tag;
            this.children = [];
            this.listeners = {};
            this.value = '';
            this.textContent = '';
            this.classList = { toggle() {} };
        }
        addEventListener(name, handler) { this.listeners[name] = handler; }
        append(...nodes) { this.children.push(...nodes); }
        appendChild(node) { this.append(node); }
        replaceChildren(...nodes) { this.children = nodes; }
        setAttribute() {}
        focus() {}
    }
    const elements = new Map();
    const events = [];
    const documentListeners = {};
    let ready;
    const document = {
        addEventListener(name, handler) {
            if (name === 'DOMContentLoaded') ready = handler;
            else (documentListeners[name] ||= []).push(handler);
        },
        dispatchEvent(event) {
            events.push(event);
            for (const handler of documentListeners[event.type] || []) handler(event);
            return true;
        },
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, new Element('div'));
            return elements.get(id);
        },
        createElement(tag) { return new Element(tag); },
    };
    const script = fs.readFileSync(path.join(__dirname, '../static/document-lookup.js'), 'utf8');
    vm.runInNewContext(script, {
        document, URLSearchParams,
        CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
        fetch: async () => ({ ok: true, json: async () => ({ results: [item], method: 'fts' }) }),
    });
    ready();
    document.getElementById('user-input');
    elements.get('document-query').value = 'trí nhớ';
    await elements.get('document-search-form').listeners.submit({ preventDefault() {} });
    const card = elements.get('document-results').children[0];
    assert.ok(card, 'Search must render a result, not swallow a rendering error');
    const descendants = node => [node, ...node.children.flatMap(descendants)];
    return { nodes: descendants(card), events, elements };
}

const source = { chunk_id: 24, doc_id: 2, page_num: 3, filename: 'study_demo.md', text: 'Trí nhớ là…' };

test('Markdown and missing PDF metadata keep text readable without a PDF action', async () => {
    for (const metadata of [{ pdf_url: null, page_image_url: null }, {}]) {
        const { nodes } = await render({ ...source, ...metadata });
        assert.equal(nodes.filter(node => node.tagName === 'a').length, 0);
        assert.equal(nodes.find(node => node.className === 'document-page').textContent, 'Phần 3');
        assert.equal(nodes.find(node => node.tagName === 'details').open, true);
        assert.equal(nodes.find(node => node.className === 'document-passage').textContent, source.text);
        assert.equal(nodes.find(node => node.tagName === 'button').textContent, 'Hỏi về phần này');
    }
});

test('PDF provides the correct page link without downloading preview images', async () => {
    const { nodes } = await render({ ...source, filename: 'book.pdf',
        pdf_url: 'https://untrusted.example/wrong', page_image_url: 'https://untrusted.example/image' });
    const links = nodes.filter(node => node.tagName === 'a');
    assert.ok(links.length >= 1);
    for (const link of links) {
        assert.equal(link.href, '/api/documents/2/pdf#page=3');
        assert.equal(link.rel, 'noopener noreferrer');
    }
    assert.equal(nodes.filter(node => node.tagName === 'img').length, 0);
    assert.equal(nodes.find(node => node.className === 'document-page').textContent, 'Trang PDF 3');
    assert.equal(nodes.find(node => node.tagName === 'button').textContent, 'Hỏi về trang này');
});

test('Image metadata alone creates neither an image nor a broken PDF link', async () => {
    const { nodes } = await render({ ...source, pdf_url: null, page_image_url: '/source-image' });
    assert.equal(nodes.filter(node => node.tagName === 'a').length, 0);
    assert.equal(nodes.filter(node => node.tagName === 'img').length, 0);
});

test('Selecting a page for QA keeps the current question and requires an explicit send', async () => {
    const { nodes, events, elements } = await render({ ...source, pdf_url: null, page_image_url: null });
    const question = elements.get('user-input');
    question.value = 'Quasar là gì?';
    const useInQa = nodes.find(node => node.tagName === 'button' && node.textContent === 'Dùng phần làm phạm vi QA');
    assert.ok(useInQa, 'Each source result should be selectable as a retrieval scope');
    useInQa.listeners.click();
    assert.equal(question.value, 'Quasar là gì?');
    assert.deepEqual(events.map(event => event.type), ['app-source-selected']);
    assert.equal(events[0].detail.doc_id, 2);
    assert.equal(events[0].detail.page_num, 3);
});

test('A selected source can open the practice tab and start practice for that exact chunk', async () => {
    const { nodes, events } = await render({ ...source, pdf_url: null, page_image_url: null });
    const practice = nodes.find(node => node.tagName === 'button' && node.textContent === 'Tạo câu hỏi từ đoạn này');
    assert.ok(practice, 'Each search result should offer source-grounded practice');
    practice.listeners.click();
    assert.deepEqual(events.map(event => event.type), [
        'app-source-selected', 'app-open-tool-tab', 'app-start-practice',
    ]);
    assert.equal(events[0].detail.doc_id, 2);
    assert.equal(events[0].detail.page_num, 3);
    assert.equal(events[0].detail.chunk_id, 24);
    assert.equal(events[0].detail.title, 'study_demo.md');
    assert.equal(events[1].detail, 'practice');
});

test('Source selection retains the real filename for chat attachments and section labels', async () => {
    const {nodes, events} = await render(source);
    const select = nodes.find(node => node.textContent === 'Dùng phần làm phạm vi QA');
    assert.ok(select, 'Non-PDF sources must be described as reading sections');
    select.listeners.click();
    assert.equal(events[0].detail.filename, source.filename);
});
