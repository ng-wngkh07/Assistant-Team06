const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function harness() {
 class El {
  constructor() {this.listeners={};this.style={};this.value='';this.innerHTML='';this.children=[];this.classList={add(){},remove(){},toggle(){},contains(){return false;}};}
  addEventListener(name,fn){(this.listeners[name] ||= []).push(fn);}
  dispatchEvent(e){for(const fn of this.listeners[e.type]||[])fn(e);return true;}
  setAttribute(){} appendChild(x){this.children.push(x);} append(...xs){this.children.push(...xs);} insertAdjacentElement(){} replaceChildren(){} focus(){} querySelector(){return new El();} querySelectorAll(){return [];} contains(){return false;}
 }
 const els = new Map(), events={};
 const doc={getElementById(id){if(!els.has(id))els.set(id,new El());return els.get(id);},createElement(){return new El();},addEventListener(name,fn){(events[name] ||= []).push(fn);},querySelectorAll(){return [];},querySelector(){return new El();},dispatchEvent(e){for(const fn of events[e.type]||[])fn(e);},body:new El()};
 const store={getItem(){return null;},setItem(){},removeItem(){}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8'),{document:doc,window:{addEventListener(){}},fetch:()=>new Promise(()=>{}),localStorage:store,sessionStorage:store,setInterval(){},setTimeout(){},clearTimeout(){},Event:class {constructor(type){this.type=type;}preventDefault(){}},URLSearchParams,TextDecoder,console});
 for(const fn of events.DOMContentLoaded)fn();
 let submits=0;els.get('chat-form').addEventListener('submit',()=>submits++);
 return {input:els.get('user-input'),get submits(){return submits;}};
}
test('Enter while IME is composing does not submit or clear the final word',()=>{
 const h=harness();h.input.value='Trí nhớ';
 h.input.dispatchEvent({type:'compositionstart'});
 let prevented=false;
 h.input.dispatchEvent({type:'keydown',key:'Enter',shiftKey:false,isComposing:true,keyCode:229,preventDefault(){prevented=true;}});
 assert.equal(h.submits,0);assert.equal(h.input.value,'Trí nhớ');assert.equal(prevented,false);
});
test('IME state guards Enter even when the browser omits isComposing',()=>{
 const h=harness();h.input.dispatchEvent({type:'compositionstart'});
 h.input.dispatchEvent({type:'keydown',key:'Enter',shiftKey:false,isComposing:false,preventDefault(){}});
 assert.equal(h.submits,0);
});
test('Regular Enter submits once; Shift+Enter remains a newline',()=>{
 const h=harness();h.input.dispatchEvent({type:'compositionend'});
 h.input.dispatchEvent({type:'keydown',key:'Enter',shiftKey:true,isComposing:false,preventDefault(){throw Error('newline prevented');}});
 assert.equal(h.submits,0);
 h.input.dispatchEvent({type:'keydown',key:'Enter',shiftKey:false,isComposing:false,preventDefault(){}});
 assert.equal(h.submits,1);
});
