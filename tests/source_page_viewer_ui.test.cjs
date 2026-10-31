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
