'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const Shortcuts=require('./static/workspace-shortcuts.js');

function element(tag,parent=null,attributes={}){
  const node={tagName:tag.toUpperCase(),nodeType:1,parentElement:parent,getAttribute(name){return Object.hasOwn(attributes,name)?attributes[name]:null;},closest(selector){const tags=selector.split(',').map(value=>value.trim().toUpperCase());for(let current=this;current;current=current.parentElement)if(tags.includes(current.tagName))return current;return null;}};
  return node;
}

class TestDocument extends EventTarget{
  constructor(){super();this.registrations=[];}
  addEventListener(type,listener,capture){this.registrations.push({type,listener,capture:typeof capture==='boolean'?capture:Boolean(capture?.capture)});super.addEventListener(type,listener,capture);}
  removeEventListener(type,listener,capture){super.removeEventListener(type,listener,capture);}
}
function dispatch(document,type,fields={},target=element('div')){
  const event=new Event(type,{cancelable:true});
  Object.assign(event,fields);Object.defineProperty(event,'composedPath',{value:()=>[target,document]});
  document.dispatchEvent(event);return event;
}

test('save detection includes Windows and Mac save-as but leaves ordinary S and other shortcuts untouched',()=>{
  for(const modifier of ['ctrlKey','metaKey'])for(const shiftKey of [false,true])assert.equal(Shortcuts.shouldPreventSave({key:shiftKey?'S':'s',[modifier]:true,shiftKey}),true);
  for(const key of ['c','v','x','z','a','p','f','ArrowLeft','Delete','Escape'])for(const modifier of ['ctrlKey','metaKey'])assert.equal(Shortcuts.shouldPreventSave({key,[modifier]:true}),false,key);
  assert.equal(Shortcuts.shouldPreventSave({key:'s',shiftKey:true}),false);assert.equal(Shortcuts.shouldPreventSave({key:'s',altKey:true}),false);assert.equal(Shortcuts.shouldPreventSave(null),false);
});

test('captured keydown actually prevents native save even inside editing fields',()=>{
  const document=new TestDocument();Shortcuts.install(document);
  assert.deepEqual(document.registrations.map(item=>[item.type,item.capture]),[['keydown',true],['selectstart',true]]);
  for(const target of [element('div'),element('input'),element('textarea'),element('select'),element('div',null,{contenteditable:'true'})]){
    for(const modifier of ['ctrlKey','metaKey'])for(const shiftKey of [false,true]){
      const event=dispatch(document,'keydown',{key:shiftKey?'S':'s',[modifier]:true,shiftKey},target);assert.equal(event.defaultPrevented,true);
    }
    for(const key of ['c','v','x','z','a'])assert.equal(dispatch(document,'keydown',{key,ctrlKey:true},target).defaultPrevented,false);
  }
});

test('form fields, select options and editable descendants retain text selection',()=>{
  for(const tag of ['input','textarea','select']){
    const control=element(tag);assert.equal(Shortcuts.allowTextSelection(control),true);assert.equal(Shortcuts.allowTextSelection(element('span',control)),true);
  }
  assert.equal(Shortcuts.allowTextSelection(element('option',element('select'))),true);
  for(const value of ['','true','plaintext-only']){
    const editable=element('div',null,{contenteditable:value}),child=element('span',editable),text={nodeType:3,parentElement:child};
    assert.equal(Shortcuts.allowTextSelection(child),true);assert.equal(Shortcuts.allowTextSelection(text),true);
  }
  const browserEditable=element('span');browserEditable.isContentEditable=true;assert.equal(Shortcuts.allowTextSelection(browserEditable),true);
});

test('explicit noneditable islands block selection while nested editable fields remain usable',()=>{
  const outer=element('div',null,{contenteditable:'true'}),island=element('div',outer,{contenteditable:'false'});
  assert.equal(Shortcuts.allowTextSelection(element('span',island)),false);
  assert.equal(Shortcuts.allowTextSelection(element('input',island)),true);
  assert.equal(Shortcuts.allowTextSelection(element('div',island,{contenteditable:'true'})),true);
  assert.equal(Shortcuts.allowTextSelection(element('span',outer,{contenteditable:'inherit'})),true);
  assert.equal(Shortcuts.allowTextSelection(element('button')),false);assert.equal(Shortcuts.allowTextSelection(null),false);
});

test('selectstart prevents chart and label selection but permits editable text and shadow-path inputs',()=>{
  const document=new TestDocument();Shortcuts.install(document);
  for(const target of [element('div'),element('button'),element('svg'),element('text',element('svg')),{nodeType:3,parentElement:element('span')}])assert.equal(dispatch(document,'selectstart',{},target).defaultPrevented,true);
  for(const target of [element('input'),element('textarea'),element('option',element('select')),element('span',element('div',null,{contenteditable:'true'}))])assert.equal(dispatch(document,'selectstart',{},target).defaultPrevented,false);
  assert.equal(dispatch(document,'keydown',{key:'s'}).defaultPrevented,false);
});

test('installation is idempotent and cleanup restores native events',()=>{
  const document=new TestDocument(),uninstall=Shortcuts.install(document);assert.equal(Shortcuts.install(document),uninstall);assert.equal(document.registrations.length,2);
  uninstall();assert.equal(dispatch(document,'keydown',{key:'s',ctrlKey:true}).defaultPrevented,false);assert.equal(dispatch(document,'selectstart').defaultPrevented,false);
  const second=Shortcuts.install(document);assert.notEqual(second,uninstall);uninstall();assert.equal(Shortcuts.install(document),second);assert.equal(dispatch(document,'keydown',{key:'s',metaKey:true}).defaultPrevented,true);second();
});
