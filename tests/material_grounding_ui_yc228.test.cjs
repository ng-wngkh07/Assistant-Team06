const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function ui() {
  const element = () => ({textContent: '', innerHTML: '', style: {}, children: [],
    classList: {add() {}}, appendChild(child) {this.children.push(child);}, querySelector() {return null;}});
  const context = {Date, console, document: {createElement: element},
    currentStudyMaterial: null, currentQuizIndex: 0, quizUserAnswers: {}, currentCardIndex: 0,
    isCardFlipped: false, cardMastery: {}, cardOrder: [], messagesContainer: element()};
  for (const id of ['studyMaterialTitle','studyMaterialSubtitle','quizBadgeCount','cardBadgeCount',
    'sourceDocName','sourcePageCount','sourceDocType','sourceProcessedAt','sourceTextPreview','studyMaterialModal']) context[id]=element();
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  // Execute the product's own function bodies with a small DOM boundary, without startup requests.
  const functions = source.match(/^    (?:async )?function \w+[^\n]*\{[\s\S]*?^    \}/gm);
  vm.runInContext(functions.join('\n'), context);
  context.switchStudyTab = context.renderQuiz = context.renderFlashcards = () => {};
  return context;
}
const fallback = {filename:'<img src=x onerror=boom>.txt',id:'private',total_pages:1,quiz:[],flashcards:[],pages:[],
  generation:{quiz:'extractive_fallback',flashcards:'model'},source_validation:'verbatim_quote',warnings:['<img src=x onerror=boom>']};

test('material modal distinguishes extractive fallback and does not imply semantic verification', () => {
  const ctx=ui();ctx.openStudyMaterial(fallback);
  assert.match(ctx.studyMaterialSubtitle.textContent,/dự phòng|trích xuất trực tiếp|fallback/i);
  assert.match(ctx.studyMaterialSubtitle.textContent,/chưa.*(?:kiểm|xác)|không.*(?:kiểm|xác)/i);
  assert.equal(ctx.studyMaterialModal.style.display,'flex');
});

test('chat study card exposes fallback and escapes untrusted text', () => {
  const ctx=ui();ctx.appendStudyCardToChat(fallback);
  const html=ctx.messagesContainer.children[0].innerHTML;
  assert.match(html,/dự phòng|trích xuất trực tiếp|fallback/i);
  assert.ok(!html.includes('<img src=x onerror=boom>'));
  assert.match(html,/&lt;img/);
});
