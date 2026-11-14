const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP_JS_PATH = path.resolve(__dirname, "..", "static", "app.js");

function extractModelCheckAndStatusSnippet(source) {
  const start = source.indexOf('document.getElementById("btn-model-check")');
  const end = source.indexOf("async function fetchOllamaModels()");
  assert.ok(start !== -1 && end !== -1 && end > start, "Expected model-check and status handlers in static/app.js");
  return source.slice(start, end);
}

test("hosted learner UI error copy omits local launcher commands and Terminal/Finder instructions", async () => {
  const source = fs.readFileSync(APP_JS_PATH, "utf8");
  for (const forbidden of ["Start Study Agent.command", "Start Study Agent.bat", "(Port 8000)"]) {
    assert.equal(source.includes(forbidden), false, `static/app.js still contains hosted-unsafe string: ${forbidden}`);
  }

  const elements = new Map();
  function makeEl(id) {
    const el = {
      id,
      textContent: "",
      className: "",
      disabled: false,
      handlers: {},
      addEventListener(evt, fn) {
        this.handlers[evt] = fn;
      },
    };
    elements.set(id, el);
    return el;
  }

  const btnCheck = makeEl("btn-model-check");
  const modelResult = makeEl("model-check-result");
  const ollamaStatus = makeEl("ollama-status");
  makeEl("kb-status");

  const context = {
    document: {
      getElementById: (id) => elements.get(id) || null,
    },
    ollamaStatus,
    fetch: async () => {
      throw new Error("network down");
    },
    console: { error() {}, warn() {}, log() {} },
    updateSourcesUiForSelection: () => {},
  };

  vm.runInNewContext(extractModelCheckAndStatusSnippet(source), context);

  assert.equal(typeof btnCheck.handlers.click, "function");
  await btnCheck.handlers.click();
  await context.fetchKnowledgeBaseStatus();

  assert.match(modelResult.textContent, /máy chủ|vận hành/i);
  assert.doesNotMatch(modelResult.textContent, /Start Study Agent|\.command|\.bat|Finder|Terminal/i);
  assert.match(ollamaStatus.textContent, /máy chủ|vận hành/i);
  assert.doesNotMatch(ollamaStatus.textContent, /Port 8000|Start Study Agent/i);
});
