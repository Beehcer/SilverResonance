'use strict';
// Integration tests execute the complete app, its registered handlers and real
// state/symbol models. Only layout, drawing and network are replaced; no browser
// or live server is controlled by this test. Visual hit testing is checked in UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const Model = require('./model.js');
const Symbols = require('../../static/workspace-symbols.js');
const Chart = require('../../static/workspace-chart.js');
const NS = 'silverresonance-dual-sample-v1:';
const fixtures = [{symbol:'CZCE.MA701',name:'甲醇701',tick:1}, {symbol:'GFEX.lc2701',name:'碳酸锂2701',tick:20}, {symbol:'SHFE.au2612',name:'沪金2612',tick:0.02}];
const copy = value => JSON.parse(JSON.stringify(value));

class Node {
  constructor(tag = 'div') {
    Object.assign(this,{tagName:tag.toUpperCase(),children:[],dataset:{},attributes:{},style:{},listeners:{},className:'',value:'',hidden:false,validity:{badInput:false}});
    this.classList = {contains:name=>this.className.split(' ').includes(name),add:(...names)=>{this.className=[...new Set([...this.className.split(' ').filter(Boolean),...names])].join(' ');},remove:(...names)=>{this.className=this.className.split(' ').filter(name=>!names.includes(name)).join(' ');},toggle:(name,on)=>{if(on??!this.classList.contains(name))this.classList.add(name);else this.classList.remove(name);}};
  }
  append(...nodes) { for(const node of nodes){if(node.parentElement)node.parentElement.children=node.parentElement.children.filter(item=>item!==node);node.parentElement=this;this.children.push(node);} }
  replaceChildren(...nodes) {this.children.forEach(node=>node.parentElement=null);this.children=[];this.append(...nodes);}
  get childElementCount(){return this.children.length;}
  get firstElementChild(){return this.children[0];}
  get value(){return this._value??'';}
  set value(value){this._value=String(value);}
  setAttribute(key,value){this.attributes[key]=String(value);if(key==='id')this.id=value;if(key==='class')this.className=value;if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;}
  getAttribute(key){return this.attributes[key]??null;}
  matches(selector){return selector.split(',').some(raw=>{const s=raw.trim();if(s[0]==='#')return this.id===s.slice(1);if(s[0]==='.')return this.classList.contains(s.slice(1));const a=/^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/.exec(s);if(a)return this.getAttribute(a[1])!==null&&(a[2]===undefined||this.getAttribute(a[1])===a[2]);return this.tagName===s.toUpperCase();});}
  closest(selector){for(let node=this;node;node=node.parentElement)if(node.matches(selector))return node;return null;}
  querySelectorAll(selector){return this.children.flatMap(node=>[...(node.matches(selector)?[node]:[]),...node.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  addEventListener(type,fn,options){(this.listeners[type]??=[]).push({fn,capture:options===true||options?.capture});}
  cloneNode(deep){const node=new Node(this.tagName);Object.assign(node,{dataset:{...this.dataset},attributes:{...this.attributes},className:this.className,id:this.id,hidden:this.hidden});if(deep)node.append(...this.children.map(child=>child.cloneNode(true)));return node;}
  getBoundingClientRect(){return {left:180,top:this.dataset.paneId==='pane-b'?450:100,width:1000,height:350};}
  setPointerCapture(id){this.captured=id;}
  releasePointerCapture(){this.captured=null;}
  focus(){this.focused=true;}
  click(){emit(this,'click');}
}
function emit(target,type,fields={}) {
  const event={type,target,button:0,pointerId:1,clientX:20,clientY:20,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...fields};
  const chain=[];for(let node=target;node;node=node.parentElement)chain.push(node);
  for(const capture of [true,false])for(const node of capture?[...chain].reverse():chain){for(const handler of node.listeners[type]||[])if(Boolean(handler.capture)===capture)handler.fn(event);if(!capture)node['on'+type]?.(event);if(event.stopped)return event;}
  return event;
}
function parseHtml(html) {
  const doc=new Node('document'),stack=[doc],voidTags=new Set(['meta','link','input','br','hr']);
  for(const token of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)){
    const raw=token[0],tag=token[1].toLowerCase();if(raw[1]==='/'){if(stack.at(-1).tagName===tag.toUpperCase())stack.pop();continue;}
    const node=new Node(tag);for(const attr of raw.matchAll(/([\w-]+)="([^"]*)"/g))node.setAttribute(attr[1],attr[2]);node.hidden=/\shidden(?:\s|>)/.test(raw);stack.at(-1).append(node);
    if(!voidTags.has(tag))stack.push(node);
  }
  const template=doc.querySelector('#pane-template');template.content={firstElementChild:template.firstElementChild};
  doc.createElement=tag=>new Node(tag);return doc;
}
async function harness(storage=new Map()) {
  const document=parseHtml(fs.readFileSync(path.join(__dirname,'index.html'),'utf8')),window=new Node('window'),charts=[],errors=[];
  let now=1000,hit=null,id=0;document.elementFromPoint=()=>hit;
  class ChartStub {constructor(host,period,callbacks){Object.assign(this,{host,period,callbacks});charts.push(this);}setData(market,plan){this.market=market;this.plan=plan;}setPlan(plan){this.plan=plan;}setTool(tool){this.tool=tool;}render(){}setLinkedRange(){} }
  Object.assign(window,{DualWorkspaceModel:Model,WorkspaceSymbols:Symbols,WorkspaceChart:{...Chart,Chart:ChartStub},PlanBoardModel:{makeId:()=>`drawing-${++id}`}});
  const fetch=async url=>({ok:true,json:async()=>url==='/api/symbols'?{symbols:fixtures}:{market:{...fixtures.find(item=>item.symbol===decodeURIComponent(url.split('=')[1])),price:2600,source:'mock',periods:Object.fromEntries(['day','m30','m3'].map(period=>[period,[{close:2600}]]))}}});
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'app.js'),'utf8'),{window,document,fetch,AbortController,performance:{now:()=>now},localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},console:{error:error=>errors.push(error)}});
  const settle=()=>new Promise(resolve=>setImmediate(resolve));await settle();assert.deepEqual(errors,[]);
  const $=selector=>document.querySelector(selector),layout=()=>JSON.parse(storage.get(NS+'layout')),pane=id=>$('#panes').children.find(node=>node.dataset.paneId===id);
  const button=symbol=>$('#symbol-list').querySelectorAll('.dl-symbol-pick').find(node=>node.dataset.symbol===symbol);
  const startDrag=(symbol,target)=>{now+=500;emit(button(symbol),'pointerdown');hit=target;emit(document,'pointermove',{clientX:300,clientY:200});};
  const finish=()=>emit(document,'pointerup',{clientX:300,clientY:200});
  const dragTo=async(symbol,target)=>{startDrag(symbol,target);finish();await settle();};
  const input=(entry,stop)=>{for(const [selector,value] of [['#ticket-entry',entry],['#ticket-stop',stop]]){$(selector).value=String(value);emit($(selector),'input');}emit($('#ticket-stop'),'change');};
  return {document,window,$,layout,pane,button,storage,charts,settle,startDrag,finish,dragTo,input,plan:symbol=>JSON.parse(storage.get(NS+'plan:'+symbol)),advance:()=>now+=500};
}
const [MA,LC,AU]=fixtures.map(item=>item.symbol);

test('a normal pointer release preserves the symbol button until its native click',async()=>{
  const h=await harness(),button=h.button(LC);emit(button,'pointerdown');emit(h.document,'pointermove',{clientX:22,clientY:22});emit(h.document,'pointerup');
  assert.equal(h.button(LC),button);assert.equal(h.$('#drop-preview').hidden,true);
  emit(button,'click');await h.settle();assert.deepEqual(h.layout().panes,[{id:'pane-a',symbol:LC}]);
});

test('actual pointer drag adds below with a guide, replaces a targeted slot, and focuses an already open symbol',async()=>{
  const h=await harness();h.startDrag(LC,h.pane('pane-a'));
  assert.equal(h.$('#drop-preview').dataset.mode,'add');assert.equal(h.$('#drop-preview').hidden,false);assert.equal(h.$('#panes').classList.contains('is-add-preview'),true);
  assert.equal(h.layout().panes.length,1);h.finish();await h.settle();
  assert.deepEqual(h.layout().panes.map(p=>p.symbol),[MA,LC]);assert.equal(h.$('#preview').classList.contains('is-single'),false);assert.equal(h.$('#drop-preview').hidden,true);
  h.startDrag(AU,h.pane('pane-b'));assert.equal(h.$('#drop-preview').dataset.mode,'replace');h.finish();await h.settle();
  assert.deepEqual(h.layout().panes.map(p=>p.symbol),[MA,AU]);await h.dragTo(MA,h.pane('pane-b'));
  assert.equal(h.layout().activeId,'pane-a');assert.deepEqual(h.layout().panes.map(p=>p.symbol),[MA,AU]);
  h.pane('pane-b').querySelector('.close-pane').click();assert.equal(h.layout().panes.length,1);assert.equal(h.pane('pane-b').hidden,true);
  assert.equal(h.$('#preview').classList.contains('is-single'),true);assert.equal(h.pane('pane-a').querySelector('.close-pane').hidden,true);
});

test('Escape, pointer cancellation, loss of focus, and an outside drop preserve the current layout',async()=>{
  for(const ending of ['Escape','pointercancel','blur','outside']){
    const h=await harness(),before=h.layout();h.startDrag(LC,ending==='outside'?null:h.pane('pane-a'));
    if(ending==='Escape')emit(h.document,'keydown',{key:'Escape'});else if(ending==='blur')emit(h.window,'blur');else if(ending==='outside')h.finish();else emit(h.document,ending);
    h.finish();assert.deepEqual(h.layout(),before);assert.equal(h.$('#drop-preview').hidden,true);assert.equal(h.$('#drag-ghost').hidden,true);assert.equal(h.$('#panes').classList.contains('is-add-preview'),false);
  }
});

test('plans and drawings stay with their symbol across replacement, closing and a fresh app instance',async()=>{
  const h=await harness();h.input(2600,2580);const maChart=h.charts.find(chart=>chart.market?.symbol===MA&&chart.period==='m30');
  maChart.callbacks.add({kind:'horizontal',name:'甲醇支撑',points:[{price:2580,time:1}]});
  await h.dragTo(LC,h.pane('pane-a'));h.input(80000,79800);
  const ma=copy(h.plan(MA)),lc=copy(h.plan(LC));assert.equal(ma.direction,'long');assert.equal(lc.objects.length,0);
  await h.dragTo(AU,h.pane('pane-b'));h.input(600,610);assert.deepEqual(h.plan(MA),ma);assert.deepEqual(h.plan(LC),lc);
  await h.dragTo(LC,h.pane('pane-b'));assert.equal(Number(h.$('#ticket-entry').value),80000);assert.equal(h.$('#layer-list').children[0].textContent,'暂无标记');
  h.pane('pane-b').querySelector('.close-pane').click();assert.equal(Number(h.$('#ticket-entry').value),2600);assert.equal(h.$('#layer-list').children[0].children[0].textContent,'甲醇支撑');
  const reload=await harness(h.storage);assert.equal(Number(reload.$('#ticket-entry').value),2600);await reload.dragTo(LC,reload.pane('pane-a'));
  assert.equal(Number(reload.$('#ticket-entry').value),80000);assert.deepEqual(reload.plan(MA),ma);assert.deepEqual(reload.plan(LC),lc);
});

test('invalid unsaved prices block a drag; Escape restores the saved plan and switching remains possible',async()=>{
  const h=await harness();h.input(2600,2580);h.$('#ticket-entry').value='-1';emit(h.$('#ticket-entry'),'input');h.startDrag(LC,h.pane('pane-a'));h.finish();
  assert.equal(h.layout().panes.length,1);assert.equal(h.$('#drop-preview').hidden,true);assert.equal(h.plan(MA).planned_entry_price,2600);
  emit(h.document,'keydown',{key:'Escape'});assert.equal(Number(h.$('#ticket-entry').value),2600);await h.dragTo(LC,h.pane('pane-a'));assert.equal(h.layout().panes.length,2);
});
