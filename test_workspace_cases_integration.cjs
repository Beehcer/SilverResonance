'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function keyboard(open){
  const source=fs.readFileSync('static/workspace.js','utf8');
  const line=source.split(/\r?\n/).find(line=>line.includes("document.addEventListener('keydown',event=>")&&line.includes('deleteSelection'));
  const body=line.slice(line.indexOf('event=>'),line.lastIndexOf(',true)'));
  const calls=[];
  const context={document:{querySelector:()=>open?{open:true}:null},E:{isTextTarget:()=>false},
    state:{active:'m30'},charts:{m30:{deleteSelection:()=>calls.push('delete'),fit:()=>calls.push('fit'),adjustSelectedPath:()=>calls.push('node')}},
    chooseTool:()=>calls.push('tool'),undoEdit:()=>calls.push('undo'),redoEdit:()=>calls.push('redo'),openPanel:()=>calls.push('panel')};
  return {calls,handle:vm.runInNewContext('('+body+')',context)};
}

test('browsing cases protects the selected shared drawing from workspace hotkeys',()=>{
  const {calls,handle}=keyboard(true);
  for(const key of ['Delete','w','h','z','f','F2','+','-'])handle({key,target:{tagName:'BUTTON'},ctrlKey:key==='z',preventDefault(){throw Error('workspace consumed case key');},stopImmediatePropagation(){throw Error('workspace intercepted modal');}});
  assert.deepEqual(calls,[]);
});

test('closing cases restores the existing workspace Delete behavior',()=>{
  const {calls,handle}=keyboard(false);
  handle({key:'Delete',target:{tagName:'BUTTON'},preventDefault(){},stopImmediatePropagation(){}});
  assert.deepEqual(calls,['delete']);
});

test('case entry and assets are wired in dependency order',()=>{
  const html=fs.readFileSync('templates/workspace.html','utf8');
  assert.match(html,/<button id="cases-button"[^>]+aria-haspopup="dialog"[^>]*>案例<\/button>/);
  assert.ok(html.indexOf('workspace-cases-data.js')<html.indexOf('workspace-cases.js'));
  assert.ok(html.indexOf('workspace-cases.js')<html.indexOf('/static/workspace.js'));
});
