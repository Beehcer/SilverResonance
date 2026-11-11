'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const M=require('./static/workspace-merge.js');
const E=require('./static/workspace-editing.js');
const source=fs.readFileSync(path.join(__dirname,'static/workspace.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));

// Run the production recovery helpers, with only their browser/network boundaries
// replaced. These helpers are single-line declarations in the workspace bundle.
function helper(name){
  const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));
  assert.ok(line,`Missing workspace helper ${name}`);if(line.trimEnd().endsWith('}'))return line;const start=source.indexOf(line),end=source.indexOf('\n  }',start);assert.ok(end>=0);return source.slice(start,end+4);
}
function harness(overrides={}){
  const saved={revision:1,notes:'A',objects:[]},state={session:{user:{id:'me'}},symbol:'SHFE.rb2701',board:'first',plan:clone(saved),saved:clone(saved),revision:1,baseRevision:1,pending:M.empty(),outbox:null,dirty:false,saving:false,loading:false,conflict:false};
  const storage=new Map(),elements=new Map(),calls={flush:0,notices:[],savedViews:0};
  const context=vm.createContext({
    state,M,E,clone,PREFIX:'test:',charts:{},Date,Promise,
    $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},
    storageSet:(key,value)=>{storage.set('test:'+key,JSON.stringify(value));return true;},
    storageGet:(key,fallback)=>storage.has('test:'+key)?JSON.parse(storage.get('test:'+key)):fallback,
    hasPending:pending=>M.hasPending(pending??state.pending),emptyPending:M.empty,
    rememberCurrentDocument:()=>{},defaultPlan:()=>({notes:'',objects:[]}),fillPlan:()=>{},fillPlanQuietly:()=>{},planMeta:()=>{},renderObjects:()=>{},renderEvaluation:()=>{},renderSyncStatus:()=>{},
    shownPlan:()=>state.plan,flush:async()=>{calls.flush++;},notice:value=>calls.notices.push(value),
    setUnavailable:()=>{},saveViews:()=>{calls.savedViews++;},sendPresence:()=>{},presenceSender:{reset:()=>{}},
    setTimeout:callback=>{callback();return 1;},
    ...overrides,
  });
  for(const name of ['documentKey','draftKey','persistDraft','drawingCharts','finishDrawings'])vm.runInContext(helper(name),context);
  return{context,state,storage,elements,calls,load:name=>vm.runInContext(helper(name),context),key:()=>`test:draft:me:${state.symbol}:${state.board}`};
}

test('net-zero edits clear the old local draft so reload cannot resurrect an undone change',()=>{
  const h=harness();h.state.plan.notes='B';h.state.pending=M.diff(h.state.saved,h.state.plan);h.context.persistDraft();
  assert.equal(JSON.parse(h.storage.get(h.key())).plan.notes,'B');
  h.state.plan=clone(h.state.saved);h.state.pending=M.empty();h.context.persistDraft();
  assert.equal(h.storage.has(h.key()),false);
});

test('empty pending still persists an unknown commit and its original baseline',()=>{
  const h=harness(),base=clone(h.state.saved),sent=M.diff(base,{...base,notes:'B'});
  h.state.outbox={id:'unknown',pending:sent,base,base_revision:1};h.context.persistDraft();
  const draft=JSON.parse(h.storage.get(h.key()));
  assert.equal(draft.outbox.id,'unknown');assert.equal(draft.outbox.base.notes,'A');assert.equal(M.hasPending(draft.pending),false);
});

test('recovering a lost acknowledgement keeps the draft dirty and buffers newer peer state',()=>{
  const h=harness();h.load('restoreDraft');h.load('receivePlan');
  const base=clone(h.state.saved),sent=M.diff(base,{...base,notes:'B'}),current={...base,revision:8,notes:'B',objects:[{id:'peer',kind:'text',name:'伙伴备注'}]};
  h.state.saved=clone(current);h.state.revision=8;
  h.context.restoreDraft({key:h.context.draftKey(),draft:{version:2,base_revision:1,pending:M.empty(),outbox:{id:'unknown',pending:sent,base,base_revision:1}}});
  assert.equal(h.state.dirty,true);assert.equal(h.state.plan.notes,'A');assert.equal(h.state.saved.revision,1);assert.equal(h.state.queuedRemote.revision,8);assert.equal(h.calls.flush,1);
  const newer={...current,revision:9,objects:[{id:'peer',kind:'text',name:'伙伴的新备注'}]};
  h.context.receivePlan(newer);assert.equal(h.state.saved.revision,1);assert.equal(h.state.queuedRemote.revision,9);assert.equal(h.state.plan.notes,'A');
  h.context.receivePlan(current);assert.equal(h.state.queuedRemote.revision,9);
  const remaining=M.ack(h.state.pending,sent,h.state.saved),settled=M.apply(h.state.queuedRemote,remaining);
  assert.equal(settled.notes,'A');assert.equal(settled.objects[0].name,'伙伴的新备注');
});

test('document switching waits for an outstanding commit even with no ordinary dirty flag',async()=>{
  const h=harness();h.load('ensureSaved');h.state.outbox={id:'unknown'};
  h.context.flush=async()=>{h.calls.flush++;h.state.outbox=null;};
  assert.equal(await h.context.ensureSaved(),true);assert.equal(h.calls.flush,1);
});

test('switching commits an unfinished valid forecast before checking saved state',async()=>{
  const h=harness();h.load('ensureSaved');let finished=0;
  h.context.charts.m30={path:[{},{}],finishPath(){finished++;this.path=[];h.state.dirty=true;}};
  h.context.flush=async()=>{h.calls.flush++;h.state.dirty=false;};
  assert.equal(await h.context.ensureSaved(),true);assert.equal(finished,1);assert.equal(h.calls.flush,1);
});

test('Escape cancels plan placement before a chart ever receives focus or a point',()=>{
  const h=harness();h.load('cancelActiveTool');h.state.quickTool='planned-entry';h.state.active='m30';h.state.quickSequence=true;h.state.levelPreview={field:'planned_entry_price',price:100};
  let cancelled=0,focused=0;h.context.charts.m30={tool:'planned-entry',path:[],cancelGesture(){cancelled++;},host:{focus(){focused++;}}};
  h.context.setQuickTool=tool=>{h.state.quickTool=tool;h.state.quickSequence=false;h.state.levelPreview=null;h.context.charts.m30.tool='select';};h.context.renderWaveTools=()=>{};
  const original=clone(h.state.plan);
  assert.equal(h.context.cancelActiveTool(),true);assert.equal(h.state.quickTool,null);assert.equal(h.state.quickSequence,false);assert.equal(h.state.levelPreview,null);assert.equal(cancelled,1);assert.equal(focused,1);assert.deepEqual(h.state.plan,original);assert.equal(h.calls.flush,0);
  assert.equal(h.context.cancelActiveTool(),false);
});

test('deleting immediately after unlocking waits for the lock mutation before removing the layer',async()=>{
  const h=harness();h.load('hasUnsettledLock');h.load('deleteObject');h.state.generation=1;
  h.state.saved.objects=[{id:'zone',locked:true}];h.state.plan.objects=[{id:'zone',locked:false}];let release,recorded=0;
  h.context.ensureSaved=()=>new Promise(resolve=>{release=resolve;});h.context.recordHistory=()=>recorded++;h.context.dirty=()=>{};
  const deletion=h.context.deleteObject('zone');assert.equal(h.state.plan.objects.length,1);assert.equal(recorded,0);
  h.state.saved.objects[0].locked=false;release(true);await deletion;
  assert.equal(h.state.plan.objects.length,0);assert.equal(recorded,1);assert.equal(h.calls.flush,1);
});

test('a pending lock acknowledgement also gates deletion and a peer lock is respected afterward',async()=>{
  const h=harness();h.load('hasUnsettledLock');h.load('deleteObject');h.state.generation=1;
  h.state.plan.objects=[{id:'zone',locked:false}];h.state.saved.objects=[{id:'zone',locked:false}];h.state.outbox={pending:{object_edits:[{id:'zone',changes:{locked:true}}]}};
  let settled=0;h.context.ensureSaved=async()=>{settled++;h.state.plan.objects[0].locked=true;return true;};
  await h.context.deleteObject('zone');assert.equal(settled,1);assert.equal(h.state.plan.objects.length,1);assert.match(h.calls.notices[0],/伙伴已锁定/);
});

test('bulk deletion waits for unlocks but never deletes a peer layer arriving during the wait',async()=>{
  const h=harness();h.load('hasUnsettledLock');h.load('deleteAll');h.state.generation=1;
  h.state.saved.objects=[{id:'mine',locked:true}];h.state.plan.objects=[{id:'mine',locked:false}];
  h.context.ensureSaved=async()=>{h.state.plan.objects.push({id:'peer',locked:false});return true;};h.context.recordHistory=()=>{};h.context.dirty=()=>{};
  await h.context.deleteAll({document:h.context.documentKey(),generation:h.state.generation,ids:['mine']});assert.equal(h.state.plan.objects.length,1);assert.equal(h.state.plan.objects[0].id,'peer');
});

test('unconfirmed commit prevents switching when reconnection cannot complete',async()=>{
  const ticks=[0,1,15000],h=harness({Date:{now:()=>ticks.shift()??15000}});h.load('ensureSaved');h.state.outbox={id:'unknown'};
  assert.equal(await h.context.ensureSaved(),false);assert.equal(h.calls.flush,1);assert.equal(h.state.outbox.id,'unknown');
  assert.match(h.calls.notices[0],/尚未保存/);
});

test('switching boards cleans the old document without deleting the target recovery draft',async()=>{
  const h=harness();h.load('switchSymbol');h.state.market={};
  const oldKey=h.key(),targetKey='test:draft:me:SHFE.rb2701:second';
  h.storage.set(oldKey,'old stale draft');h.storage.set(targetKey,'target unsent draft');
  h.context.ensureSaved=async()=>true;
  // Stop immediately after switch preparation; loading the destination is outside
  // this regression, which specifically protects the draft-key transition.
  h.context.setPlanLoading=()=>{throw new Error('stop after draft transition');};
  await assert.rejects(h.context.switchSymbol(h.state.symbol,'second'),/stop after draft transition/);
  assert.equal(h.storage.has(oldKey),false);assert.equal(h.storage.get(targetKey),'target unsent draft');
});

test('closing with an unknown commit stores it and triggers the existing leave guard',()=>{
  const h=harness();let beforeUnload;
  h.context.window={addEventListener:(type,callback)=>{if(type==='beforeunload')beforeUnload=callback;}};
  const listener=source.split(/\r?\n/).find(line=>line.includes("window.addEventListener('beforeunload'"));assert.ok(listener);
  vm.runInContext(listener,h.context);h.state.outbox={id:'unknown',base:clone(h.state.saved),pending:M.empty()};
  let prevented=false;const event={preventDefault:()=>{prevented=true;}};beforeUnload(event);
  assert.equal(prevented,true);assert.equal(event.returnValue,'');assert.equal(JSON.parse(h.storage.get(h.key())).outbox.id,'unknown');
});

test('undo during an HTTP save produces a compensating edit without clobbering a peer',()=>{
  const h=harness(),base=clone(h.state.plan),edited={...base,notes:'B'},op=E.operation(base,edited),sent=M.diff(base,edited);
  h.load('travelHistory');h.state.plan=edited;h.state.saving=true;h.state.outbox={id:'in-flight',base,pending:sent};
  const history={undo:[op],redo:[]};h.context.editHistory=()=>history;h.context.updateHistoryButtons=()=>{};
  h.context.dirty=()=>{h.state.pending=M.diff(h.state.saved,h.state.plan);h.state.dirty=M.hasPending(h.state.pending)||Boolean(h.state.outbox);};
  h.context.travelHistory(true);assert.equal(h.state.plan.notes,'A');assert.equal(history.undo.length,0);assert.equal(history.redo.length,1);
  const peer={...edited,revision:3,objects:[{id:'peer',name:'伙伴修改'}]},remaining=M.ack(h.state.pending,sent,base),settled=M.apply(peer,remaining);
  assert.equal(settled.notes,'A');assert.equal(settled.objects[0].name,'伙伴修改');assert.equal(h.calls.flush,1);
});
