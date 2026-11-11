'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'static/workspace.js'),'utf8');
const template=fs.readFileSync(path.join(__dirname,'templates/workspace.html'),'utf8');
const copy=value=>JSON.parse(JSON.stringify(value));
function helper(name){
  const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));
  assert.ok(line,`Missing production helper ${name}`);if(line.trimEnd().endsWith('}'))return line;
  const start=source.indexOf(line),end=source.indexOf('\n  }',start);assert.ok(end>=0);return source.slice(start,end+4);
}
function harness(objects=[{id:'a',locked:false},{id:'b',locked:false},{id:'locked',locked:true}]){
  const state={symbol:'SHFE.rb2701',board:'night',generation:7,plan:{symbol:'SHFE.rb2701',board_id:'night',objects:copy(objects),planned_entry_price:3030,stop_price:3040,notes:'keep notes'},saved:{symbol:'SHFE.rb2701',board_id:'night',objects:copy(objects)},selected:'a',outbox:null};
  const calls={shown:0,focused:[],history:0,dirty:0,flush:0,saves:0,panels:[],updates:[]},elements=new Map();
  const $=id=>{if(!elements.has(id))elements.set(id,{hidden:false,textContent:'',style:{},listeners:{},focus:()=>calls.focused.push(id),addEventListener(type,callback){this.listeners[type]=callback;},querySelector:selector=>$(id+':'+selector),getBoundingClientRect:()=>({width:180,height:240,left:0,top:0})});return elements.get(id);};
  // Native dialogs keep returnValue when reopened or dismissed with Escape.
  // The production helpers, not this test double, must reset it.
  const dialog={open:false,returnValue:'',showModal(){this.open=true;calls.shown++;}};elements.set('delete-all-dialog',dialog);
  const charts={day:{selected:'a',selectedAnchor:1},m30:{selected:'a',selectedAnchor:1},m3:{selected:'a',selectedAnchor:1}};
  const context=vm.createContext({state,$,charts,innerWidth:1000,innerHeight:700,notice:()=>{},selectObject:id=>{state.selected=id;},openPanel:id=>calls.panels.push(id),updateObject:(id,patch)=>calls.updates.push({id,patch:copy(patch)}),recordHistory:()=>calls.history++,dirty:()=>calls.dirty++,flush:()=>calls.flush++,ensureSaved:async()=>{calls.saves++;return true;}});
  for(const name of ['documentKey','currentPlan','hasUnsettledLock','requestDeleteAll','finishDeleteAll','deleteAll','deleteObject','clearLevel','clearOpenPlan','closeObjectContext','objectContext'])vm.runInContext(helper(name),context);
  const menuListener=source.split(/\r?\n/).find(line=>line.startsWith("  $('object-context-menu').addEventListener('click'"));assert.ok(menuListener);vm.runInContext(menuListener,context);
  function close(value){if(value!==undefined)dialog.returnValue=value;dialog.open=false;return context.finishDeleteAll();}
  return{state,calls,dialog,charts,context,$,close};
}

test('opening bulk delete asks for confirmation, focuses cancel and does not write or change any object',()=>{
  const h=harness(),before=copy(h.state.plan);h.context.requestDeleteAll();
  assert.equal(h.dialog.open,true);assert.equal(h.calls.shown,1);assert.deepEqual(h.calls.focused,['cancel-delete-all']);assert.equal(h.$('object-context-menu').hidden,true);
  assert.match(h.$('delete-all-summary').textContent,/2 个未锁定图层/);assert.deepEqual(h.state.plan,before);assert.equal(h.calls.history,0);assert.equal(h.calls.flush,0);
});

test('confirming deletes only captured unlocked layers while preserving plans, notes and locked layers',async()=>{
  const h=harness();h.context.requestDeleteAll();await h.close('confirm');
  assert.deepEqual(h.state.plan.objects,[{id:'locked',locked:true}]);assert.equal(h.state.plan.planned_entry_price,3030);assert.equal(h.state.plan.stop_price,3040);assert.equal(h.state.plan.notes,'keep notes');
  assert.equal(h.calls.history,1);assert.equal(h.calls.dirty,1);assert.equal(h.calls.flush,1);assert.equal(h.state.selected,null);
  for(const chart of Object.values(h.charts)){assert.equal(chart.selected,null);assert.equal(chart.selectedAnchor,null);}
  assert.equal(h.state.deleteAllRequest,null);assert.equal(h.dialog.returnValue,'');
});

test('cancel and Escape close without deleting or syncing, and discard the captured request',async()=>{
  for(const action of ['cancel',undefined]){const h=harness(),before=copy(h.state.plan);h.context.requestDeleteAll();await h.close(action);assert.deepEqual(h.state.plan,before);assert.equal(h.calls.flush,0);assert.equal(h.calls.history,0);assert.equal(h.state.deleteAllRequest,null);assert.equal(h.dialog.returnValue,'');}
});

test('confirming once then reopening and pressing Escape cannot reuse the previous confirmation',async()=>{
  const h=harness();h.context.requestDeleteAll();await h.close('confirm');assert.equal(h.calls.flush,1);
  h.state.plan.objects.push({id:'later',locked:false});const before=copy(h.state.plan);h.context.requestDeleteAll();await h.close();
  assert.deepEqual(h.state.plan,before);assert.equal(h.calls.history,1);assert.equal(h.calls.flush,1);assert.equal(h.state.deleteAllRequest,null);
});

test('opening resets a preexisting native returnValue before showModal, preventing stale confirm on Escape',async()=>{
  const h=harness(),before=copy(h.state.plan);h.dialog.returnValue='confirm';
  h.dialog.showModal=function(){assert.equal(this.returnValue,'');this.open=true;h.calls.shown++;};
  h.context.requestDeleteAll();await h.close();assert.deepEqual(h.state.plan,before);assert.equal(h.calls.flush,0);
});

test('a repeated close or a confirm without a pending request cannot delete another layer',async()=>{
  const h=harness();h.context.requestDeleteAll();await h.close('confirm');h.state.plan.objects.push({id:'new',locked:false});const before=copy(h.state.plan);
  await h.close('confirm');assert.deepEqual(h.state.plan,before);assert.equal(h.calls.flush,1);assert.equal(h.dialog.returnValue,'');
});

test('peer additions and new locks during confirmation are retained, including previously locked layers just unlocked',async()=>{
  const h=harness();h.context.requestDeleteAll();h.state.plan.objects.find(o=>o.id==='b').locked=true;h.state.plan.objects.find(o=>o.id==='locked').locked=false;h.state.plan.objects.push({id:'peer-added',locked:false});
  await h.close('confirm');assert.deepEqual(h.state.plan.objects.map(o=>o.id),['b','locked','peer-added']);assert.equal(h.calls.flush,1);
});

test('switching board, symbol or generation makes the old confirmation inert',async()=>{
  for(const change of [h=>h.state.board='other',h=>h.state.symbol='CZCE.RM701',h=>h.state.generation++]){
    const h=harness();h.context.requestDeleteAll();change(h);const before=copy(h.state.plan);await h.close('confirm');assert.deepEqual(h.state.plan,before);assert.equal(h.calls.history,0);assert.equal(h.calls.flush,0);assert.equal(h.state.deleteAllRequest,null);
  }
});

test('pending unlocks wait for acknowledgement and confirmation is consumed before that await',async()=>{
  const h=harness([{id:'a',locked:false},{id:'b',locked:false}]);h.state.saved.objects[0].locked=true;let release;
  h.context.ensureSaved=()=>{h.calls.saves++;return new Promise(resolve=>release=resolve);};h.context.requestDeleteAll();const deletion=h.close('confirm');
  assert.equal(h.calls.saves,1);assert.equal(h.state.deleteAllRequest,null);assert.equal(h.dialog.returnValue,'');assert.equal(h.calls.history,0);
  h.state.plan.objects[1].locked=true;h.state.plan.objects.push({id:'peer-new',locked:false});h.state.saved.objects[0].locked=false;release(true);await deletion;
  assert.deepEqual(h.state.plan.objects.map(o=>o.id),['b','peer-new']);assert.equal(h.calls.flush,1);
});

test('failed unlock acknowledgement or document change during the wait prevents bulk deletion',async()=>{
  for(const changedDocument of [false,true]){
    const h=harness([{id:'a',locked:false}]);h.state.saved.objects[0].locked=true;let release;h.context.ensureSaved=()=>new Promise(resolve=>release=resolve);
    h.context.requestDeleteAll();const deletion=h.close('confirm');if(changedDocument)h.state.board='new-board';release(changedDocument);await deletion;
    assert.equal(h.state.plan.objects.length,1);assert.equal(h.calls.history,0);assert.equal(h.calls.flush,0);
  }
});

test('empty or entirely locked plans do not open a destructive dialog',()=>{
  for(const objects of [[],[{id:'locked',locked:true}]]){const h=harness(objects);h.context.requestDeleteAll();assert.equal(h.calls.shown,0);assert.equal(h.calls.flush,0);assert.equal(h.state.deleteAllRequest,undefined);}
});

test('remote deletion of all captured targets results in no redundant history or save',async()=>{
  const h=harness();h.context.requestDeleteAll();h.state.plan.objects=[{id:'peer-new',locked:false}];await h.close('confirm');assert.deepEqual(h.state.plan.objects,[{id:'peer-new',locked:false}]);assert.equal(h.calls.history,0);assert.equal(h.calls.flush,0);
});

test('opening again while the modal is active cannot expand the captured deletion scope',async()=>{
  const h=harness();h.context.requestDeleteAll();h.state.plan.objects.push({id:'peer-new',locked:false});h.context.requestDeleteAll();assert.equal(h.calls.shown,1);await h.close('confirm');assert.deepEqual(h.state.plan.objects.map(o=>o.id),['locked','peer-new']);
});

test('calling the deletion helper without an explicit captured request is harmless',async()=>{
  const h=harness(),before=copy(h.state.plan);await h.context.deleteAll();assert.deepEqual(h.state.plan,before);assert.equal(h.calls.history,0);assert.equal(h.calls.flush,0);
});

test('both bulk-delete entry points route through confirmation and the native dialog has explicit cancel/confirm actions',()=>{
  assert.match(source,/\$\('delete-all-objects'\)\.addEventListener\('click',requestDeleteAll\)/);
  assert.match(source,/action==='delete-all'\)requestDeleteAll\(\)/);
  assert.match(source,/\$\('delete-all-dialog'\)\.addEventListener\('close',finishDeleteAll\)/);
  const dialogTag=template.match(/<dialog\b[^>]*\bid="delete-all-dialog"[^>]*>/)?.[0]||'';
  assert.match(dialogTag,/\baria-labelledby="delete-all-title"/);
  assert.match(dialogTag,/\baria-describedby="delete-all-summary"/);
  assert.match(template,/<dialog\b[^>]*\bid="delete-all-dialog"[^>]*>[\s\S]*?<form\b[^>]*\bmethod="dialog"[^>]*>/);
  assert.match(template,/<button[^>]*id="cancel-delete-all"[^>]*value="cancel"/);
  assert.match(template,/<button[^>]*id="confirm-delete-all"[^>]*value="confirm"/);
});

test('all destructive helpers refuse a loaded plan from another symbol or board even with matching object IDs',async()=>{
  for(const foreign of [{symbol:'CZCE.RM701',board_id:'night'},{symbol:'SHFE.rb2701',board_id:'other'}]){
    const h=harness();Object.assign(h.state.plan,foreign);const before=copy(h.state.plan);
    await h.context.deleteObject('a');h.context.clearLevel('planned_entry_price');h.context.clearOpenPlan();h.context.requestDeleteAll();await h.context.deleteAll({document:h.context.documentKey(),generation:h.state.generation,ids:['a','b']});
    assert.deepEqual(h.state.plan,before);assert.equal(h.calls.history,0);assert.equal(h.calls.dirty,0);assert.equal(h.calls.flush,0);assert.equal(h.calls.shown,0);
  }
});

test('single-layer deletion changes only the current plan target and retains sibling and locked layers',async()=>{
  const h=harness(),otherPlan={...copy(h.state.plan),board_id:'other'},otherBefore=copy(otherPlan);await h.context.deleteObject('a');
  assert.deepEqual(h.state.plan.objects.map(object=>object.id),['b','locked']);assert.deepEqual(otherPlan,otherBefore);assert.equal(h.state.plan.planned_entry_price,3030);assert.equal(h.state.plan.stop_price,3040);assert.equal(h.state.plan.notes,'keep notes');assert.equal(h.calls.history,1);assert.equal(h.calls.flush,1);
  await h.context.deleteObject('locked');assert.deepEqual(h.state.plan.objects.map(object=>object.id),['b','locked']);assert.equal(h.calls.flush,1);
});

test('waiting for unlock acknowledgement cannot delete a recurring ID after switching A to B to A',async()=>{
  const h=harness([{id:'a',locked:false}]);h.state.saved.objects[0].locked=true;let release;h.context.ensureSaved=()=>new Promise(resolve=>release=resolve);
  const deletion=h.context.deleteObject('a');assert.equal(h.calls.history,0);h.state.board='other';h.state.generation++;h.state.plan={...copy(h.state.plan),board_id:'other'};h.state.board='night';h.state.generation++;h.state.plan={...copy(h.state.plan),board_id:'night',notes:'新进入的计划'};const before=copy(h.state.plan);release(true);await deletion;
  assert.deepEqual(h.state.plan,before);assert.equal(h.calls.history,0);assert.equal(h.calls.flush,0);
});

test('a menu opened before A to B to A cannot delete, change, or expose actions on the new document',async()=>{
  for(const action of ['hide','lock','delete','delete-plan','delete-all','properties','layers']){
    const h=harness();h.context.objectContext('a',{clientX:100,clientY:100});assert.equal(h.state.contextGeneration,7);assert.equal(h.$('object-context-menu').hidden,false);
    h.state.board='other';h.state.generation++;h.state.plan={...copy(h.state.plan),board_id:'other'};h.state.board='night';h.state.generation++;h.state.plan={...copy(h.state.plan),board_id:'night'};const before=copy(h.state.plan);
    h.$('object-context-menu').listeners.click({target:{closest:()=>({dataset:{contextAction:action},disabled:false})}});await Promise.resolve();
    assert.deepEqual(h.state.plan,before,action);assert.equal(h.calls.history,0,action);assert.equal(h.calls.flush,0,action);assert.equal(h.calls.shown,0,action);assert.deepEqual(h.calls.updates,[],action);assert.deepEqual(h.calls.panels,[],action);assert.equal(h.state.contextObject,null);assert.equal(h.state.contextGeneration,null);assert.equal(h.$('object-context-menu').hidden,true);
  }
});

test('a newly opened current-document menu still deletes its clicked layer and resets captured context',async()=>{
  const h=harness();h.context.objectContext('b',{clientX:100,clientY:100});h.$('object-context-menu').listeners.click({target:{closest:()=>({dataset:{contextAction:'delete'},disabled:false})}});await Promise.resolve();
  assert.deepEqual(h.state.plan.objects.map(object=>object.id),['a','locked']);assert.equal(h.calls.flush,1);assert.equal(h.state.contextObject,null);assert.equal(h.state.contextGeneration,null);
});
