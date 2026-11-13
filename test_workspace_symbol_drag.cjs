"use strict";
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {test}=require('node:test');
const WorkspaceSymbols=require('./static/workspace-symbols.js');
const WorkspaceShortcuts=require('./static/workspace-shortcuts.js');

class Node extends EventTarget{
  constructor(tag){super();this.tagName=tag.toUpperCase();this.nodeType=1;this.dataset={};this.children=[];this.className='';this.replacements=0;this.value='';this.attributes=new Map();this.classes=new Set();this.classList={add:(...names)=>names.forEach(name=>this.classes.add(name)),remove:(...names)=>names.forEach(name=>this.classes.delete(name)),toggle:(name,on)=>{if(on)this.classes.add(name);else this.classes.delete(name);}};}
  append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
  replaceChildren(...nodes){this.replacements++;this.children=[];this.append(...nodes);}
  get childNodes(){return this.children;}
  getBoundingClientRect(){return{top:0,height:30};}
  closest(selector){const tags=selector.split(',').map(value=>value.trim().toUpperCase());for(let node=this;node;node=node.parentElement)if(tags.includes(node.tagName))return node;return null;}
  getAttribute(name){return this.attributes.get(name)??null;}
  setAttribute(name,value){this.attributes.set(name,String(value));}
  querySelectorAll(selector){const found=[];const visit=node=>{if(selector==='[data-symbol-group]'&&node.dataset.symbolGroup||selector==='[data-symbol]'&&node.dataset.symbol||selector==='.quote-row'&&node.className.split(' ').includes('quote-row'))found.push(node);node.children.forEach(visit);};this.children.forEach(visit);return found;}
  focus(){this.focused=true;}
}
function emit(target,type,fields={}){const event=new Event(type,{cancelable:true});Object.assign(event,fields);target.dispatchEvent(event);return event;}
function transfer(){const values=new Map();return{setData:(key,value)=>values.set(key,value),getData:key=>values.get(key)};}
function harness(options={}){
  const source=fs.readFileSync('static/workspace.js','utf8');
  const start=source.indexOf('  function favorites()'),end=source.indexOf('  async function enter(',start);assert.ok(start>=0&&end>start);
  const nodes=new Map(),storage=options.storage||new Map([['favorites',['ag']]]),switched=[],state={session:{user:{id:options.userId||'alice'}},symbols:options.symbols||['rb','ag','au','rm'],symbol:'rb',market:null,quoteCache:{rb:{price:100,at:1000,source:'live'}},visitedSymbols:new Set(),boards:[],viewOnly:Boolean(options.viewOnly)};let now=10000;
  if(options.groups)storage.set('symbol-groups:'+state.session.user.id,options.groups);
  const $=id=>{if(!nodes.has(id))nodes.set(id,new Node(id==='symbol-search'?'input':'div'));return nodes.get(id);};
  const el=(tag,text,className)=>{const node=new Node(tag);node.textContent=text;node.className=className||'';return node;};
  const tabs=WorkspaceSymbols.GROUPS.map(group=>{const tab=new Node('button');tab.dataset.symbolGroup=group.id;return tab;});
  const document={querySelectorAll:selector=>selector==='[data-symbol-group]'?tabs:[]};
  const context={state,$,el,document,WorkspaceSymbols,marketCache:{ready:()=>false},C:{priceLabel:price=>String(price)},fmtTime:()=>'',storageGet:(key,fallback)=>storage.has(key)?storage.get(key):fallback,storageSet:(key,value)=>{storage.set(key,JSON.parse(JSON.stringify(value)));return true;},switchSymbol:symbol=>switched.push(symbol),Date:{now:()=>now}};
  vm.createContext(context);vm.runInContext(source.slice(start,end)+'\n globalThis.handlers={renderSymbols,bindSymbolDrag,endSymbolDrag,bindSymbolGroup,chooseSymbolGroup,symbolGroups,toggleFavorite,toggleDefaultSymbol};',context);
  tabs.forEach(context.handlers.bindSymbolGroup);context.handlers.renderSymbols();
  const list=$('symbol-list'),row=symbol=>list.children.find(item=>item.children[0]?.dataset.symbol===symbol),button=symbol=>row(symbol)?.children[0];
  return{...context,handlers:context.handlers,list,row,button,storage,switched,tab:id=>tabs.find(tab=>tab.dataset.symbolGroup===id),groups:()=>storage.get('symbol-groups:'+state.session.user.id),advance:ms=>now+=ms,ids:()=>list.children.map(item=>item.children[0]?.dataset.symbol).filter(Boolean)};
}

test('actual symbol rows defer quote rerenders throughout a drag; cancelled drag preserves both orders',()=>{
  const h=harness(),button=h.button('rb'),row=h.row('rb'),initial=h.list.replacements,before=h.groups(),dataTransfer=transfer();
  assert.equal(button.draggable,true);assert.notEqual(row.children[1].draggable,true);
  emit(button,'dragstart',{dataTransfer});assert.equal(h.state.symbolDrag,'rb');assert.equal(dataTransfer.effectAllowed,'copyMove');assert.equal(dataTransfer.getData('text/plain'),'rb');
  h.state.quoteCache.rb={price:108,at:2000,source:'live'};h.handlers.renderSymbols();h.handlers.renderSymbols();
  assert.equal(h.list.replacements,initial);assert.equal(h.button('rb'),button);assert.equal(button.children[1].textContent,'100');assert.equal(h.state.symbolRenderPending,true);
  emit(button,'dragend');assert.equal(h.state.symbolDrag,null);assert.equal(h.button('rb').children[1].textContent,'108');assert.deepEqual(h.groups(),before);
});

test('actual row drop crosses old favorite boundary, persists default order and suppresses completion click',()=>{
  const h=harness(),source=h.button('rm'),target=h.row('ag'),dataTransfer=transfer();
  emit(source,'dragstart',{dataTransfer});assert.equal(emit(target,'dragover',{clientY:5,dataTransfer}).defaultPrevented,true);assert.equal(dataTransfer.dropEffect,'move');
  assert.equal(emit(target,'drop',{clientY:5,dataTransfer}).defaultPrevented,true);
  assert.deepEqual(h.groups().default,['rm','ag','rb','au']);assert.deepEqual(h.ids(),['rm','ag','rb','au']);assert.deepEqual(h.groups().favorites,['ag']);
  emit(h.button('rm'),'click');assert.deepEqual(h.switched,[]);h.advance(351);emit(h.button('rm'),'click');assert.deepEqual(h.switched,['rm']);
});

test('dropping from sector onto a custom tab adds once without switching charts or removing source membership',()=>{
  const h=harness();h.handlers.chooseSymbolGroup('agriculture');assert.deepEqual(h.ids(),['rm']);const before=h.groups().default,dataTransfer=transfer();
  emit(h.button('rm'),'dragstart',{dataTransfer});assert.equal(emit(h.tab('favorites'),'dragover',{dataTransfer}).defaultPrevented,true);assert.equal(dataTransfer.dropEffect,'copy');
  emit(h.tab('favorites'),'drop',{dataTransfer});assert.deepEqual(h.groups().favorites,['ag','rm']);assert.deepEqual(h.groups().default,before);assert.equal(h.state.symbolGroup,'agriculture');assert.deepEqual(h.switched,[]);
  emit(h.button('rm'),'dragstart',{dataTransfer});emit(h.tab('favorites'),'drop',{dataTransfer});assert.deepEqual(h.groups().favorites,['ag','rm']);
});

test('favorite-only member can be dragged to empty default; custom groups remain independent',()=>{
  const h=harness({groups:{version:1,default:[],favorites:['ag']}});h.handlers.chooseSymbolGroup('favorites');const dataTransfer=transfer();
  emit(h.button('ag'),'dragstart',{dataTransfer});emit(h.tab('default'),'drop',{dataTransfer});assert.deepEqual(h.groups(),{version:1,default:['ag'],favorites:['ag']});
  h.handlers.chooseSymbolGroup('default');emit(h.row('ag').children[2],'click');assert.deepEqual(h.groups(),{version:1,default:[],favorites:['ag']});assert.deepEqual(h.ids(),[]);
  const reloaded=harness({storage:h.storage});assert.deepEqual(reloaded.ids(),[]);reloaded.handlers.chooseSymbolGroup('all');assert.equal(reloaded.ids().length,4);
});

test('sector row drops and drops without an internal drag cannot reorder or add memberships',()=>{
  const h=harness();h.handlers.chooseSymbolGroup('precious');const before=h.groups();emit(h.button('au'),'dragstart',{dataTransfer:transfer()});
  assert.equal(emit(h.row('ag'),'dragover',{clientY:5,dataTransfer:transfer()}).defaultPrevented,false);emit(h.row('ag'),'drop',{clientY:5});emit(h.tab('energy'),'drop');emit(h.button('au'),'dragend');assert.deepEqual(h.groups(),before);
  assert.equal(emit(h.tab('default'),'drop',{dataTransfer:transfer()}).defaultPrevented,false);assert.deepEqual(h.groups(),before);
});

test('filtered custom-list reordering preserves hidden members and does not reprioritize favorites',()=>{
  const h=harness({symbols:[{symbol:'rb',name:'金属 螺纹'},{symbol:'ag',name:'白银'},{symbol:'au',name:'金属 黄金'},{symbol:'rm',name:'菜粕'}]});h.$('symbol-search').value='金属';h.handlers.renderSymbols();
  assert.deepEqual(h.ids(),['rb','au']);emit(h.button('au'),'dragstart',{dataTransfer:transfer()});emit(h.row('rb'),'drop',{clientY:5});
  assert.deepEqual(h.groups().default,['ag','au','rb','rm']);h.$('symbol-search').value='';h.handlers.renderSymbols();emit(h.row('rm').children[1],'click');assert.deepEqual(h.ids(),['ag','au','rb','rm']);assert.deepEqual(h.groups().favorites,['ag','rm']);
  h.handlers.chooseSymbolGroup('favorites');emit(h.button('rm'),'dragstart',{dataTransfer:transfer()});emit(h.row('ag'),'drop',{clientY:5});assert.deepEqual(h.groups().favorites,['rm','ag']);assert.deepEqual(h.groups().default,['ag','au','rb','rm']);
});

test('Alt+arrow reorders selected custom rows and restores keyboard focus without chart switching',()=>{
  const h=harness(),event=emit(h.button('au'),'keydown',{altKey:true,key:'ArrowUp'});assert.equal(event.defaultPrevented,true);assert.deepEqual(h.ids(),['ag','au','rb','rm']);assert.equal(h.button('au').focused,true);assert.deepEqual(h.switched,[]);
  const before=h.groups();h.handlers.chooseSymbolGroup('all');assert.equal(emit(h.button('au'),'keydown',{altKey:true,key:'ArrowUp'}).defaultPrevented,false);assert.deepEqual(h.groups(),before);
});

test('group members and selected group persist per browser identity without modifying legacy preferences',()=>{
  const storage=new Map([['favorites',['ag']]]),alice=harness({storage,userId:'alice'});alice.handlers.toggleDefaultSymbol('rb');alice.handlers.toggleFavorite('rm');alice.handlers.chooseSymbolGroup('favorites');
  const bob=harness({storage,userId:'bob'});assert.equal(bob.state.symbolGroup,'default');assert.deepEqual(bob.groups().favorites,['ag']);assert.ok(bob.groups().default.includes('rb'));
  const reload=harness({storage,userId:'alice'});assert.equal(reload.state.symbolGroup,'favorites');assert.deepEqual(reload.ids(),['ag','rm']);assert.equal(reload.groups().default.includes('rb'),false);assert.deepEqual(storage.get('favorites'),['ag']);
});

test('startup market preview before catalog arrival cannot erase saved memberships',()=>{
  const h=harness(),before=h.groups();h.state.symbols=null;h.state.symbolGroups=null;h.handlers.renderSymbols();assert.deepEqual(h.groups(),before);assert.deepEqual(h.ids(),[]);
  h.state.symbols=[];h.handlers.renderSymbols();assert.deepEqual(h.groups(),before);h.handlers.toggleFavorite('rb');assert.deepEqual(h.groups(),before);
  h.state.symbols=['rb','ag','au','rm'];h.handlers.renderSymbols();assert.deepEqual(h.groups(),before);
});

test('read-only mode prevents group membership, order, and migration writes',()=>{
  const storage=new Map([['favorites',['ag']]]),h=harness({viewOnly:true,storage});assert.equal(h.button('rb').draggable,false);assert.equal(h.row('rb').children[1].disabled,true);assert.equal(h.row('rb').children[2].disabled,true);
  assert.equal(emit(h.button('rb'),'dragstart',{dataTransfer:transfer()}).defaultPrevented,true);emit(h.row('rb').children[1],'click');emit(h.row('rb').children[2],'click');emit(h.button('rb'),'keydown',{altKey:true,key:'ArrowDown'});assert.equal(storage.has('symbol-groups:alice'),false);
});

test('selection protection blocks chart-row text selection without cancelling native drag or input edits',()=>{
  const h=harness(),document=new EventTarget();WorkspaceShortcuts.install(document);
  const select=target=>{const event=new Event('selectstart',{cancelable:true});Object.defineProperty(event,'composedPath',{value:()=>[target,document]});document.dispatchEvent(event);return event;};
  assert.equal(select(h.button('rb').children[0]).defaultPrevented,true);assert.equal(select(h.$('symbol-search')).defaultPrevented,false);
  assert.equal(emit(h.button('rb'),'dragstart',{dataTransfer:transfer()}).defaultPrevented,false);assert.equal(h.state.symbolDrag,'rb');emit(h.button('rb'),'dragend');
});
