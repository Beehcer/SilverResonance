'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {test}=require('node:test');
const WorkspaceSymbols=require('./static/workspace-symbols.js');
const WorkspaceShortcuts=require('./static/workspace-shortcuts.js');

class Node extends EventTarget{
  constructor(tag){super();this.tagName=tag.toUpperCase();this.nodeType=1;this.dataset={};this.children=[];this.className='';this.replacements=0;this.value='';this.classes=new Set();this.classList={add:(...names)=>names.forEach(name=>this.classes.add(name)),remove:(...names)=>names.forEach(name=>this.classes.delete(name)),toggle:(name,on)=>{if(on)this.classes.add(name);else this.classes.delete(name);}};}
  append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
  replaceChildren(...nodes){this.replacements++;this.children=[];this.append(...nodes);}
  get childNodes(){return this.children;}
  getBoundingClientRect(){return{top:0,height:30};}
  closest(selector){const tags=selector.split(',').map(value=>value.trim().toUpperCase());for(let node=this;node;node=node.parentElement)if(tags.includes(node.tagName))return node;return null;}
  getAttribute(){return null;}
}
function emit(target,type,fields={}){const event=new Event(type,{cancelable:true});Object.assign(event,fields);target.dispatchEvent(event);return event;}
function transfer(){const values=new Map();return{setData:(key,value)=>values.set(key,value),getData:key=>values.get(key)};}
function harness(){
  const source=fs.readFileSync('static/workspace.js','utf8');
  const start=source.indexOf('  function favorites()'),end=source.indexOf('  async function enter(',start);assert.ok(start>=0&&end>start);
  const nodes=new Map(),storage=new Map([['favorites',['ag']]]),switched=[],state={symbols:['rb','ag','au','rm'],symbol:'rb',market:null,quoteCache:{rb:{price:100,at:1000,source:'live'}},visitedSymbols:new Set(),boards:[]};let now=10000;
  const $=id=>{if(!nodes.has(id))nodes.set(id,new Node(id==='symbol-search'?'input':'div'));return nodes.get(id);};
  const el=(tag,text,className)=>{const node=new Node(tag);node.textContent=text;node.className=className||'';return node;};
  const context={state,$,el,WorkspaceSymbols,marketCache:{ready:()=>false},C:{priceLabel:price=>String(price)},fmtTime:()=>'',storageGet:(key,fallback)=>storage.has(key)?storage.get(key):fallback,storageSet:(key,value)=>storage.set(key,JSON.parse(JSON.stringify(value))),switchSymbol:symbol=>switched.push(symbol),Date:{now:()=>now}};
  vm.createContext(context);vm.runInContext(source.slice(start,end)+'\n globalThis.handlers={renderSymbols,bindSymbolDrag,endSymbolDrag};',context);
  context.handlers.renderSymbols();
  const list=$('symbol-list'),row=symbol=>list.children.find(item=>item.children[0]?.dataset.symbol===symbol),button=symbol=>row(symbol)?.children[0];
  return{...context,handlers:context.handlers,list,row,button,storage,switched,advance:ms=>now+=ms,ids:()=>list.children.map(item=>item.children[0]?.dataset.symbol)};
}

test('actual symbol rows defer quote rerenders throughout a drag and show the latest quote on dragend',()=>{
  const h=harness(),button=h.button('rb'),row=h.row('rb'),initial=h.list.replacements,dataTransfer=transfer();
  assert.equal(button.draggable,true);assert.notEqual(row.children[1].draggable,true);
  emit(button,'dragstart',{dataTransfer});assert.equal(h.state.symbolDrag,'rb');assert.equal(dataTransfer.effectAllowed,'move');assert.equal(dataTransfer.getData('text/plain'),'rb');
  h.state.quoteCache.rb={price:108,at:2000,source:'live'};h.handlers.renderSymbols();h.handlers.renderSymbols();
  assert.equal(h.list.replacements,initial);assert.equal(h.button('rb'),button);assert.equal(button.children[1].textContent,'100');assert.equal(h.state.symbolRenderPending,true);
  emit(button,'dragend');assert.equal(h.state.symbolDrag,null);assert.equal(h.button('rb').children[1].textContent,'108');assert.equal(h.ids()[0],'ag');
});

test('actual drop handlers persist same-group order and suppress drag completion clicks',()=>{
  const h=harness(),source=h.button('rm'),target=h.row('rb'),dataTransfer=transfer();
  emit(source,'dragstart',{dataTransfer});assert.equal(emit(target,'dragover',{clientY:5,dataTransfer}).defaultPrevented,true);assert.equal(dataTransfer.dropEffect,'move');
  const drop=emit(target,'drop',{clientY:5,dataTransfer});assert.equal(drop.defaultPrevented,true);
  assert.deepEqual(h.storage.get('symbol-order'),['ag','rm','rb','au']);assert.deepEqual(h.ids(),['ag','rm','rb','au']);assert.deepEqual(h.storage.get('favorites'),['ag']);
  emit(h.button('rm'),'click');assert.deepEqual(h.switched,[]);
  h.advance(351);emit(h.button('rm'),'click');assert.deepEqual(h.switched,['rm']);
});

test('actual cross-favorite-group drop is rejected without changing membership or group order',()=>{
  const h=harness(),source=h.button('rb'),target=h.row('ag'),before=h.ids();emit(source,'dragstart',{dataTransfer:transfer()});
  assert.equal(emit(target,'dragover',{clientY:5,dataTransfer:transfer()}).defaultPrevented,false);
  // A synthetic drop must also remain harmless even though the browser would not allow it.
  emit(target,'drop',{clientY:5});assert.deepEqual(h.ids(),before);assert.deepEqual(h.storage.get('favorites'),['ag']);
});

test('actual filtered-list drop keeps hidden contracts and quote refresh retains favorite priority',()=>{
  const h=harness();h.state.symbols=[{symbol:'rb',name:'金属 螺纹'},{symbol:'ag',name:'白银'},{symbol:'au',name:'金属 黄金'},{symbol:'rm',name:'菜粕'}];h.$('symbol-search').value='金属';h.handlers.renderSymbols();
  assert.deepEqual(h.ids(),['rb','au']);emit(h.button('au'),'dragstart',{dataTransfer:transfer()});emit(h.row('rb'),'drop',{clientY:5});
  assert.deepEqual(h.storage.get('symbol-order'),['ag','au','rb','rm']);h.$('symbol-search').value='';h.handlers.renderSymbols();assert.deepEqual(h.ids(),['ag','au','rb','rm']);
  emit(h.row('rm').children[1],'click');assert.deepEqual(h.ids().slice(0,2),['ag','rm']);
});

test('selection protection blocks text selection without cancelling native drag handlers or input edits',()=>{
  const h=harness(),document=new EventTarget();WorkspaceShortcuts.install(document);
  const select=target=>{const event=new Event('selectstart',{cancelable:true});Object.defineProperty(event,'composedPath',{value:()=>[target,document]});document.dispatchEvent(event);return event;};
  assert.equal(select(h.button('rb').children[0]).defaultPrevented,true);assert.equal(select(h.$('symbol-search')).defaultPrevented,false);
  const event=emit(h.button('rb'),'dragstart',{dataTransfer:transfer()});assert.equal(event.defaultPrevented,false);assert.equal(h.state.symbolDrag,'rb');emit(h.button('rb'),'dragend');
});
