const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag;
        this.children = [];
        this.listeners = {};
        this.value = '';
        this.textContent = '';
        this.className = '';
        this.hidden = false;
        this.disabled = false;
        this.attributes = {};
        this.classList = { toggle() {} };
    }
    addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
    append(...nodes) { for (const node of nodes) { if (node) { node.parent = this; this.children.push(node); } } }
    appendChild(node) { this.append(node); return node; }
    prepend(node) { node.parent = this; this.children.unshift(node); }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
    showModal() { this.open = true; }
    close() { this.open = false; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
    click() { return Promise.all((this.listeners.click || []).map(handler => handler({ target: this }))); }
    querySelector(selector) {
        if (selector.includes('practice-answer')) return descendants(this).find(node => node.tagName === 'input' && node.name === 'practice-answer' && node.checked) || null;
        if (selector === 'input:checked') return descendants(this).find(node => node.tagName === 'input' && node.checked) || null;
        if (selector === 'button') return descendants(this).find(node => node.tagName === 'button') || null;
        return null;
    }
    querySelectorAll(selector) {
        if (selector === 'button, input') return descendants(this).filter(node => ['button', 'input'].includes(node.tagName));
        return [];
    }
}

function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
function textOf(node) { return [node.textContent, ...node.children.map(textOf)].filter(Boolean).join(' '); }
function findButton(node, label) {
    return descendants(node).find(item => item.tagName === 'button' && item.textContent === label);
}

async function createApp(seed = [], options = {}) {
    const exampleItems = options.exampleItems || [];
    const validationError = options.validationError || false;
    class Storage {
        constructor() { this.data = new Map(seed.length ? [['study-agent-review-v1', JSON.stringify(seed)]] : []); }
        getItem(key) { return this.data.has(key) ? this.data.get(key) : null; }
        setItem(key, value) { this.data.set(key, String(value)); }
        removeItem(key) { this.data.delete(key); }
    }
    const localStorage = new Storage();
    const elements = new Map();
    const events = [];
    const exampleRequests = [];
    const learningRequests = [];
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
            if (!elements.has(id)) elements.set(id, new Element(id.endsWith('dialog') ? 'dialog' : 'div'));
            return elements.get(id);
        },
        createElement(tag) { return new Element(tag); },
    };
    let answerCalls = 0;
    const fetch = async (url, options = {}) => {
        if (url === '/api/learning/topics') return { ok: true, json: async () => ({ topics: [{ id: 'Trí nhớ', label: 'Trí nhớ', document_count: 3 }] }) };
        if (url === '/api/learning/exercises') { learningRequests.push({url, body: JSON.parse(options.body)}); return { ok: true, json: async () => ({items: exampleItems}) }; }
        if (url === '/api/learning/generate') learningRequests.push({url, body: JSON.parse(options.body)});
        if (url === '/api/learning/generate' && validationError) return { ok: false, status: 422, json: async () => ({detail:[{msg:'Value error, Chủ đề không hợp lệ.'}]}) };
        if (url === '/api/documents') return { ok: true, json: async () => ({ documents: [
            { id: 4, filename: 'algebra.pdf', clean_title: 'Đại số tuyến tính', total_pages: 8 },
        ] }) };
        if (url.startsWith('/api/practice/examples?')) {
            exampleRequests.push(url);
            return { ok: true, json: async () => ({ items: exampleItems }) };
        }
        if (url === '/api/practice/generate' || url === '/api/learning/generate') return { ok: true, json: async () => ({
            practice_id: 'session-123456789012345',
            questions: [{
                id: 'q1', question: 'Trong điều kiện A, hiện tượng X có thể xảy ra như thế nào?',
                choices: [{ label: 'A', text: 'Luôn xảy ra' }, { label: 'B', text: 'Có thể xảy ra' },
                    { label: 'C', text: 'Không xảy ra' }, { label: 'D', text: 'Mọi điều kiện' }],
                source: { doc_id: 1, chunk_id: 17, title: 'Tài liệu', page_num: 4, pdf_url: '/api/documents/1/pdf#page=4' },
            }],
        }) };
        if (url === '/api/practice/answer') {
            answerCalls += 1;
            return { ok: true, json: async () => ({
                is_correct: false, correct_answer: 'B', correct_choice: 'Có thể xảy ra',
                explanation: 'Nguồn chỉ cho phép kết luận có thể xảy ra trong điều kiện A.',
                source: { doc_id: 1, chunk_id: 17, title: 'Tài liệu', page_num: 4,
                    snippet: 'Trong điều kiện A, hiện tượng X có thể xảy ra.', pdf_url: '/api/documents/1/pdf#page=4' },
            }) };
        }
        throw new Error(`Unexpected request ${url} ${options.method || 'GET'}`);
    };
    const script = fs.readFileSync(path.join(__dirname, '../static/practice.js'), 'utf8');
    vm.runInNewContext(script, {
        document, fetch, localStorage, URL, URLSearchParams,
        window: { location: { origin: 'http://localhost' }, confirm: () => true },
        CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    });
    ready();
    await Promise.resolve();
    return { document, elements, events, exampleRequests, learningRequests, localStorage, get answerCalls() { return answerCalls; } };
}

function sampleReview(id = 'saved-1') {
    return {
        id, question: 'Câu hỏi cần ôn lại?',
        choices: [{ label: 'A', text: 'Một' }, { label: 'B', text: 'Hai' }, { label: 'C', text: 'Ba' }, { label: 'D', text: 'Bốn' }],
        submitted_answer: 'A', correct_answer: 'B', correct_choice: 'Hai', explanation: 'Giải thích từ nguồn.',
        source: { doc_id: 1, chunk_id: 17, title: 'Tài liệu', page_num: 4, snippet: 'Trích dẫn nguồn.', pdf_url: '/api/documents/1/pdf#page=4' },
    };
}

test('Answers appear only after submission, wrong answers persist locally, and the source page reopens', async () => {
    const app = await createApp();
    app.document.dispatchEvent({ type: 'app-source-selected', detail: { doc_id: 1, page_num: 4, chunk_id: 17, title: 'Tài liệu' } });
    await app.elements.get('btn-practice-current').click();
    const body = app.elements.get('practice-body');
    assert.doesNotMatch(textOf(body), /Đáp án:|Nguồn chỉ cho phép kết luận/);
    const answerInput = descendants(body).find(node => node.tagName === 'input' && node.name === 'practice-answer' && node.value === 'A');
    answerInput.checked = true;
    const answerButton = findButton(body, 'Trả lời');
    await answerButton.click();

    assert.equal(app.answerCalls, 1);
    assert.match(textOf(body), /Đáp án: B/);
    assert.match(textOf(body), /Nguồn chỉ cho phép kết luận/);
    const saved = JSON.parse(app.localStorage.getItem('study-agent-review-v1'));
    assert.equal(saved.length, 1);
    assert.equal(saved[0].submitted_answer, 'A');
    assert.equal(saved[0].submitted_answer_text, 'Luôn xảy ra');
    assert.equal(saved[0].correct_answer, 'B');
    assert.equal(saved[0].source.page_num, 4);

    await findButton(body, 'Xem kết quả').click();
    await findButton(body, 'Mở tab Ôn tập').click();
    const review = app.elements.get('review-list');
    assert.match(textOf(review), /Nguồn chỉ cho phép kết luận/);
    assert.match(textOf(review), /Câu trả lời trước: A\. Luôn xảy ra/);
    const pdf = descendants(review).find(node => node.tagName === 'a');
    assert.equal(pdf.href, 'http://localhost/api/documents/1/pdf#page=4');
    const restored = await createApp(saved);
    const restoredList = restored.elements.get('review-list');
    assert.match(textOf(restoredList), /A\. Luôn xảy ra/);
    assert.match(textOf(restoredList), /Đáp án: B\. Có thể xảy ra/);
    assert.match(textOf(restoredList), /Trong điều kiện A, hiện tượng X có thể xảy ra/);

    await findButton(review, 'Tự kiểm tra lại').click();
    assert.doesNotMatch(textOf(review), /Đáp án: B|Nguồn chỉ cho phép kết luận/);
    const retryAnswer = descendants(review).find(node => node.tagName === 'input' && node.value === 'B');
    retryAnswer.checked = true;
    await findButton(review, 'Kiểm tra').click();
    assert.match(textOf(review), /Đáp án: B\. Có thể xảy ra/);
    await findButton(review, 'Mở nguồn trong Hỏi đáp').click();
    const selected = app.events.filter(event => event.type === 'app-source-selected').at(-1);
    assert.equal(selected.detail.chunk_id, 17);
    assert.equal(selected.detail.page_num, 4);
});

test('Individual and full review-list deletion update browser-local progress', async () => {
    const app = await createApp([sampleReview(), sampleReview('saved-2')]);
    const review = app.elements.get('review-list');
    assert.equal(descendants(review).filter(node => node.tagName === 'article').length, 2);
    await findButton(review, 'Xóa mục').click();
    assert.equal(JSON.parse(app.localStorage.getItem('study-agent-review-v1')).length, 1);
    await app.elements.get('btn-clear-review').click();
    assert.equal(app.localStorage.getItem('study-agent-review-v1'), null);
    assert.match(textOf(review), /Chưa có câu nào cần ôn tập/);
});

test('Example exercise filters preserve source text and reopen the cited document page', async () => {
    const original = 'Bài tập 2.3 — Tính đạo hàm của hàm số f(x) = x².\nGiữ nguyên ký hiệu.';
    const app = await createApp([], { exampleItems: [{
        doc_id: 4, chunk_id: 44, title: 'Đại số tuyến tính', filename: 'algebra.pdf', page_num: 3,
        text: original, origin: 'source_document', pdf_url: '/api/documents/4/pdf#page=3',
        needs_source_check: true, source_warning: 'OCR hoặc ký hiệu có thể chưa rõ; hãy đối chiếu trang gốc.',
    }] });
    await new Promise(resolve => setImmediate(resolve));

    app.elements.get('practice-examples-document').value = '4';
    app.elements.get('practice-examples-page-from').value = '2';
    app.elements.get('practice-examples-page-to').value = '4';
    app.elements.get('practice-examples-topic').value = 'đạo hàm';
    const form = app.elements.get('practice-examples-form');
    await form.listeners.submit[0]({ preventDefault() {} });

    assert.equal(app.exampleRequests.length, 1);
    const request = new URL(app.exampleRequests[0], 'http://localhost');
    assert.equal(request.pathname, '/api/practice/examples');
    assert.equal(request.searchParams.get('document_id'), '4');
    assert.equal(request.searchParams.get('page_from'), '2');
    assert.equal(request.searchParams.get('page_to'), '4');
    assert.equal(request.searchParams.get('topic'), 'đạo hàm');

    const results = app.elements.get('practice-examples-results');
    assert.equal(results.children.length, 1);
    assert.match(textOf(results), /Bài tập trích từ nguồn · trang 3/);
    assert.match(textOf(results), /OCR hoặc ký hiệu có thể chưa rõ/);
    assert.equal(results.children[0].children.find(node => node.className === 'document-passage').textContent, original);
    const pdf = descendants(results).find(node => node.tagName === 'a');
    assert.equal(pdf.href, 'http://localhost/api/documents/4/pdf#page=3');

    await findButton(results, 'Chọn trang này trong Hỏi đáp').click();
    const selected = app.events.find(event => event.type === 'app-source-selected');
    assert.deepEqual(JSON.parse(JSON.stringify(selected.detail)),
        { doc_id: 4, page_num: 3, chunk_id: 44, title: 'Đại số tuyến tính' });
});

test('An empty exercise search explains that the selected source has no matching exercises', async () => {
    const app = await createApp();
    await new Promise(resolve => setImmediate(resolve));
    app.elements.get('practice-examples-document').value = '4';
    app.elements.get('practice-examples-page-from').value = '1';
    app.elements.get('practice-examples-page-to').value = '8';
    await app.elements.get('practice-examples-form').listeners.submit[0]({ preventDefault() {} });
    assert.equal(app.elements.get('practice-examples-results').children.length, 0);
    assert.match(app.elements.get('practice-examples-status').textContent, /Không tìm thấy đoạn có dấu hiệu bài tập/);
});


test('Practice starts from a topic without selecting a document or page', async () => {
    const app = await createApp();
    app.elements.get('learning-topic').value = 'Trí nhớ';
    app.elements.get('learning-count').value = '1';
    app.elements.get('learning-difficulty').value = 'medium';
    await app.elements.get('btn-learning-generate').click();
    assert.equal(app.learningRequests.length, 1);
    const body = app.learningRequests[0].body;
    assert.equal(body.topic, 'Trí nhớ');
    assert.equal(body.purpose, 'practice');
    assert.equal(body.count, 1);
    assert.equal(body.difficulty, 'medium');
    assert.equal(body.source_document_id, undefined);
    assert.match(textOf(app.elements.get('practice-body')), /điều kiện A/);
    assert.doesNotMatch(textOf(app.elements.get('practice-body')), /Nguồn chỉ cho phép/);
});

test('Review can generate new questions when no previous mistakes exist', async () => {
    const app = await createApp();
    app.elements.get('review-topic').value = 'Trí nhớ';
    await app.elements.get('btn-review-generate').click();
    assert.equal(app.learningRequests.length, 1);
    assert.equal(app.learningRequests[0].body.purpose, 'review');
    assert.equal(app.learningRequests[0].body.topic, 'Trí nhớ');
});

test('Automatic exercise search returns individual questions and separate source answers', async () => {
    const app = await createApp([], {exampleItems: [{question:'Bài 1: Tính 2+3.', answer:'5', answer_status:'source_document',
        source:{doc_id:1, page_num:2, filename:'math.pdf', title:'Toán'}, answer_source:{doc_id:1,page_num:3,filename:'math.pdf',title:'Toán'}}]});
    app.elements.get('exercise-topic').value = 'Phép cộng';
    await app.elements.get('btn-learning-exercises').click();
    assert.equal(app.learningRequests.length, 1);
    assert.equal(app.learningRequests[0].url, '/api/learning/exercises');
    assert.equal(app.learningRequests[0].body.topic, 'Phép cộng');
    assert.equal(app.learningRequests[0].body.solve_missing, false);
    const text = textOf(app.elements.get('learning-exercise-results'));
    assert.match(text, /Tính 2\+3/);
    assert.match(text, /Chỉ đáp án/);
    assert.doesNotMatch(text, /Đáp án từ tài liệu:/);
    await findButton(app.elements.get('learning-exercise-results'), 'Chỉ đáp án').click();
    const links = descendants(app.elements.get('learning-exercise-results')).filter(node => node.tagName === 'a');
    assert.ok(links.some(node => node.href === '/api/documents/1/pdf#page=2'));
    assert.ok(links.some(node => node.href === '/api/documents/1/pdf#page=3'));
});

test('Review passes the explicit topic of saved mistakes without sending answer text as a topic', async () => {
    const saved = { ...sampleReview(), topic: 'Trí nhớ' };
    const app = await createApp([saved]);
    app.elements.get('review-topic').value = 'Trí nhớ';
    await app.elements.get('btn-review-generate').click();
    const body = app.learningRequests[0].body;
    assert.deepEqual(JSON.parse(JSON.stringify(body.weak_topics)), ['Trí nhớ']);
    assert.ok(!body.weak_topics.includes(saved.question));
    assert.ok(!body.weak_topics.includes(saved.explanation));
});

test('Text exercise sources show their passage without offering a nonexistent PDF', async () => {
    const app = await createApp([], { exampleItems: [{ question: 'Bài 2. Nêu ví dụ.',
        source: { doc_id: 28, page_num: 6, filename: 'memory.md', title: 'Trí nhớ', pdf_url: null, snippet: 'Bài 2. Nêu ví dụ.' },
        answer: null, answer_status: 'missing', answer_source: null }] });
    app.elements.get('exercise-topic').value = 'Trí nhớ';
    await app.elements.get('btn-learning-exercises').click();
    const results = app.elements.get('learning-exercise-results');
    assert.equal(descendants(results).filter(n => n.tagName === 'a' && /\/pdf/.test(n.href || '')).length, 0);
    assert.match(textOf(results), /đoạn|phần/i);
});

test('A failed AI solution is explicitly unverified instead of presented as an absent source answer', async () => {
    const app = await createApp([], { exampleItems: [{ question: 'Bài 2. Nêu ví dụ.',
        source: { doc_id: 28, page_num: 6, filename: 'memory.md', pdf_url: null },
        answer: null, answer_status: 'unverified', answer_source: null }] });
    app.elements.get('exercise-topic').value = 'Trí nhớ';
    app.elements.get('exercise-solve-missing').checked = true;
    await app.elements.get('btn-learning-exercises').click();
    assert.match(textOf(app.elements.get('learning-exercise-results')), /chưa.*(xác minh|kiểm chứng)/i);
});

test('Exercise answers and answer snippets stay hidden until the learner selects a view', async () => {
    const app = await createApp([], {exampleItems: [{question:'Bài 1: Tính 2+3.', answer:'5', answer_status:'source_document',
        source:{doc_id:1,page_num:2,filename:'math.pdf'}, answer_source:{doc_id:1,page_num:3,filename:'math.md',snippet:'Kết quả cần giấu: 5'}}]});
    app.elements.get('exercise-topic').value = 'Phép cộng';
    await app.elements.get('btn-learning-exercises').click();
    const body = app.elements.get('learning-exercise-results');
    assert.doesNotMatch(textOf(body), /Kết quả cần giấu|Đáp án từ tài liệu/);
    await findButton(body,'Giải chi tiết').click();
    assert.match(textOf(body), /Chưa có lời giải chi tiết/);
    await findButton(body,'Chỉ đáp án').click();
    assert.match(textOf(body), /Đáp án từ tài liệu:  5/);
    assert.match(textOf(body), /Kết quả cần giấu/);
});


test('Learning validation errors display a readable message instead of an object', async () => {
    const app = await createApp([], {validationError:true});
    app.elements.get('learning-topic').value = '???';
    app.elements.get('learning-count').value = '1';
    app.elements.get('learning-difficulty').value = 'medium';
    await app.elements.get('btn-learning-generate').click();
    const message=textOf(app.elements.get('practice-body'));
    assert.match(message,/Chủ đề không hợp lệ/);
    assert.doesNotMatch(message,/\[object Object\]/);
});

test('Saving a new review question refreshes an already visible review list when the practice dialog closes', async () => {
    const app = await createApp();
    app.elements.get('review-topic').value = 'Trí nhớ';
    await app.elements.get('btn-review-generate').click();
    await findButton(app.elements.get('practice-body'), 'Chưa chắc').click();
    await app.elements.get('btn-close-practice').click();
    assert.match(textOf(app.elements.get('review-list')), /Trong điều kiện A/);
    assert.doesNotMatch(textOf(app.elements.get('review-list')), /Chưa có câu nào/);
});
