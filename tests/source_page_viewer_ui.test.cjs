const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function viewer() {
  const context = {
    docContentPagesList: {innerHTML: '', querySelector() { return null; }},
    canvasSearchCount: {hidden: true, textContent: ''},
  };
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  const functions = source.match(/^    (?:async )?function \w+[^\n]*\{[\s\S]*?^    \}/gm);
  vm.runInContext(functions.join('\n'), context);
  return context;
}

test('library PDF viewer displays original pages even where extraction is empty', () => {
  const ui = viewer();
  ui.renderDocumentContentPages({filename: 'example.pdf', doc_id: 5, total_pages: 2,
    pages: [{page_num: 1, text: 'A formula f(x)'}]});
  assert.match(ui.docContentPagesList.innerHTML, /src="\/api\/documents\/5\/pages\/1\/image"/);
  assert.match(ui.docContentPagesList.innerHTML, /src="\/api\/documents\/5\/pages\/2\/image"/);
  assert.match(ui.docContentPagesList.innerHTML, /<details class="canvas-extracted-text">/);
  assert.match(ui.docContentPagesList.innerHTML, /có thể thiếu công thức hoặc hình ảnh/);
});

test('searching by page number counts the page and keeps extracted text collapsed', () => {
  const ui = viewer();
  ui.renderDocumentContentPages({filename: 'example.pdf', doc_id: 5, total_pages: 2,
    pages: [{page_num: 2, text: 'A formula f(x)'}]}, 'Trang 2');
  assert.equal(ui.canvasSearchCount.textContent, '1 kết quả');
  assert.match(ui.docContentPagesList.innerHTML, /pages\/2\/image/);
  assert.match(ui.docContentPagesList.innerHTML, /<details class="canvas-extracted-text">/);
  assert.doesNotMatch(ui.docContentPagesList.innerHTML, /pages\/1\/image/);
});

test('searching extracted content opens the matching preview while preserving the original', () => {
  const ui = viewer();
  ui.renderDocumentContentPages({filename: 'example.pdf', doc_id: 5, total_pages: 1,
    pages: [{page_num: 1, text: 'A formula f(x)'}]}, 'f(x)');
  assert.equal(ui.canvasSearchCount.textContent, '1 kết quả');
  assert.match(ui.docContentPagesList.innerHTML, /pages\/1\/image/);
  assert.match(ui.docContentPagesList.innerHTML, /<details class="canvas-extracted-text" open>/);
  assert.match(ui.docContentPagesList.innerHTML, /<mark class="canvas-mark">f\(x\)<\/mark>/i);
});

test('library PPTX presentation viewer displays slide layout with original download and image preview without PDF routes', () => {
  const ui = viewer();
  ui.renderDocumentContentPages({filename: 'lecture.pptx', doc_id: 155, total_pages: 2,
    pages: [{page_num: 1, text: 'Slide 1 Introduction'}, {page_num: 2, text: 'Slide 2 Algorithms'}]});
  assert.match(ui.docContentPagesList.innerHTML, /Slide 1/);
  assert.match(ui.docContentPagesList.innerHTML, /Slide 2/);
  assert.match(ui.docContentPagesList.innerHTML, /href="\/api\/documents\/155\/original"/);
  assert.match(ui.docContentPagesList.innerHTML, /src="\/api\/documents\/155\/pages\/1\/image"/);
  assert.match(ui.docContentPagesList.innerHTML, /canvas-render-unavailable/);
  assert.doesNotMatch(ui.docContentPagesList.innerHTML, /\/api\/documents\/155\/pdf/);
});

test('library DOCX document viewer displays logical reading sections and original download without forging page images', () => {
  const ui = viewer();
  ui.renderDocumentContentPages({filename: 'notes.docx', doc_id: 162, total_pages: 2,
    pages: [{page_num: 1, text: 'Phần 1: Khái niệm'}, {page_num: 2, text: 'Phần 2: Ứng dụng'}]});
  assert.match(ui.docContentPagesList.innerHTML, /Phần 1 \(phân đoạn đọc\)/);
  assert.match(ui.docContentPagesList.innerHTML, /href="\/api\/documents\/162\/original"/);
  assert.doesNotMatch(ui.docContentPagesList.innerHTML, /canvas-original-page-image/);
  assert.doesNotMatch(ui.docContentPagesList.innerHTML, /\/api\/documents\/162\/pdf/);
});
