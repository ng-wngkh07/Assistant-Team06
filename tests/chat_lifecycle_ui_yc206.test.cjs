const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
async function harness(options = {}) {
 class El {
  constructor() {this.listeners={};this.style={};this.value='';this.innerHTML='';this.children=[];const classes=new Set();this.classList={add(x){classes.add(x);},remove(x){classes.delete(x);},toggle(x){classes.has(x)?classes.delete(x):classes.add(x);},contains(x){return classes.has(x);}};}
  addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
  dispatchEvent(e){for(const fn of this.listeners[e.type]||[])fn(e);return true;}
  remove(){} setAttribute(){} appendChild(x){this.children.push(x);} append(...xs){this.children.push(...xs);} insertAdjacentElement(where,el){if(el.id)els.set(el.id,el);} replaceChildren(){} focus(){} querySelector(){return new El();} querySelectorAll(){return [];} contains(){return false;}
 }
 const els = new Map(), events={}, created=[];
 const doc={getElementById(id){if(!els.has(id))els.set(id,new El());return els.get(id);},createElement(){const el=new El();created.push(el);return el;},addEventListener(name,fn){(events[name] ||= []).push(fn);},querySelectorAll(){return [];},querySelector(){return new El();},dispatchEvent(e){for(const fn of events[e.type]||[])fn(e);},body:new El()};
 doc.getElementById('reindex-spinner');doc.getElementById('btn-close-reindex');
 const store={getItem(){return null;},setItem(){},removeItem(){}};
 vm.runInNewContext(fs.readFileSync(process.env.UI_APP_JS || path.join(__dirname,'../static/app.js'),'utf8'),{document:doc,window:{addEventListener(){}},confirm:()=>true,fetch:(url)=>url === '/api/index' && options.index ? options.index() : url === '/api/chat/stream' ? (options.chat ? options.chat() : Promise.reject(new Error('Lỗi thử nghiệm'))) : Promise.resolve({ok:true,json:async()=>({sessions:[],items:[],models:[],knowledge_base:{}})}),localStorage:store,sessionStorage:store,setInterval(){},setTimeout(){},clearTimeout(){},Event:class {constructor(type){this.type=type;}preventDefault(){}},URLSearchParams,TextDecoder,console});
 for(const fn of events.DOMContentLoaded)fn();
 await new Promise(resolve=>setImmediate(resolve));
 let submits=0;els.get('chat-form').addEventListener('submit',()=>submits++);
 return {els,created,input:els.get('user-input'),submit:()=>els.get('chat-form').listeners.submit[0]({preventDefault(){}}),newChat:()=>els.get('btn-new-chat-sidebar').listeners.click[0]({type:'click'}),get submits(){return submits;}};
}

test('New chat resets stale content, session and draft without a runtime exception', async () => {
 const h=await harness();h.input.value='Câu hỏi chưa gửi';h.els.get('messages-container').innerHTML='Phiên cũ';
 assert.doesNotThrow(()=>h.newChat());assert.equal(h.input.value,'');
 assert.notEqual(h.els.get('messages-container').innerHTML,'Phiên cũ');
 assert.equal(h.els.get('session-bar').style.display,'none');
});
test('A failed submitted turn offers recovery without reinserting the old question', async () => {
 const h=await harness();h.input.value='Đạo hàm là gì';await h.submit();assert.equal(h.input.value,'');
 assert.equal(h.els.get('btn-send').disabled,false);
 assert.match(h.els.get('chat-request-status').textContent,/lỗi|thử lại|kiểm tra/i);
});
test('A next draft survives a prior turn failure', async () => {
 let reject;const h=await harness({chat:()=>new Promise((_,r)=>reject=r)});
 h.input.value='Câu trước';const pending=h.submit();h.input.value='Câu tiếp theo';reject(new Error('Lỗi thử nghiệm'));await pending;assert.equal(h.input.value,'Câu tiếp theo');
});
test('Overlong questions remain editable and are not accepted', async () => {
 const h=await harness();h.input.value='a'.repeat(4001);await h.submit();assert.equal(h.input.value.length,4001);
});

test('An indexing failure leaves a closable dialog and permits another attempt', async () => {
 const h=await harness({index:async()=>({ok:false,json:async()=>({detail:'Máy đang bận'})})});
 await h.els.get('btn-reindex').listeners.click[0]();
 assert.match(h.els.get('reindex-msg').textContent,/Máy đang bận/);
 assert.equal(h.els.get('reindex-spinner').style.display,'none');
 assert.equal(h.els.get('btn-close-reindex').disabled,false);
 h.els.get('btn-close-reindex').listeners.click[0]();
 assert.equal(h.els.get('reindex-modal').classList.contains('active'),false);
 assert.equal(h.els.get('btn-reindex').disabled,false);
});


test('An answer keeps the source review warning visible beside its citation', async () => {
 const citation={id:'S1',book_title:'Tài liệu thử',filename:'source.pdf',doc_id:1,page_num:1,snippet:'Nguồn một phần.',page_quality:{needs_review:true}};
 const payload=[{type:'token',content:'Câu trả lời thử.'},{type:'done',response_kind:'answer',citations:[citation]}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('');
 let delivered=false;
 const h=await harness({chat:async()=>({ok:true,body:{getReader:()=>({read:async()=>delivered?{done:true}:(delivered=true,{done:false,value:new TextEncoder().encode(payload)})})}})});
 h.input.value='Một câu hỏi?';await h.submit();
 const warnings=h.created.filter(el=>el.className?.split(' ').includes('source-review-notice'));
 assert.equal(warnings.length,1);
 assert.match(warnings[0].textContent,/chưa.*duyệt|cần.*đối chiếu/i);
});

test('Cited claim text is visible as text and never injected into HTML', async () => {
 const claim='<img src=x onerror=alert(1)> Nội dung người học đối chiếu.';
 const citation={id:'S1',book_title:'Phiếu QA',filename:'source.md',doc_id:901,page_num:1,snippet:'Nguồn gốc.',claims:[claim]};
 const payload=[{type:'token',content:'Câu trả lời.'},{type:'done',response_kind:'answer',citations:[citation]}].map(e=>'data: '+JSON.stringify(e)+'\n\n').join('');
 let delivered=false;
 const h=await harness({chat:async()=>({ok:true,body:{getReader:()=>({read:async()=>delivered?{done:true}:(delivered=true,{done:false,value:new TextEncoder().encode(payload)})})}})});
 h.input.value='Câu hỏi học tập?';await h.submit();
 assert.ok(h.created.some(el=>el.textContent===claim));
 assert.ok(!h.created.some(el=>String(el.innerHTML).includes('onerror=alert')));
});
