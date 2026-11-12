'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/workspace.js','utf8'),M=require('./static/workspace-merge.js');
function helper(name){const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));assert.ok(line,name);if(line.trimEnd().endsWith('}'))return line;const start=source.indexOf(line);return source.slice(start,source.indexOf('\n  }',start)+4);}
function context(values,names){const c=vm.createContext(values);for(const name of names)vm.runInContext(helper(name),c);return c;}

test('mobile page blocks only multi-touch zoom, without stopping chart handlers or single-finger scroll',()=>{
  const listeners=new Map(),original='width=device-width, initial-scale=1, viewport-fit=cover';let content=original;
  const doc={querySelector:()=>({getAttribute:()=>content,setAttribute:(key,value)=>{content=value;}}),addEventListener:(name,callback,options)=>listeners.set(name,{callback,options})};
  const c=context({},['installPageZoomGuard']),guard=c.installPageZoomGuard(doc);
  const fire=(name,touches,cancelable=true)=>{let cancelled=0;listeners.get(name).callback({touches:Array.from({length:touches}),cancelable,preventDefault(){cancelled++;},stopPropagation(){assert.fail('Chart must receive the gesture');}});return cancelled;};
  assert.equal(fire('touchmove',2),0,'Desktop browser zoom remains available');
  guard.setReadOnly(true);assert.match(content,/maximum-scale=1/);assert.match(content,/user-scalable=no/);
  for(const name of ['touchstart','touchmove']){assert.equal(listeners.get(name).options.passive,false);assert.equal(fire(name,1),0);assert.equal(fire(name,2),1);assert.equal(fire(name,3),1);assert.equal(fire(name,2,false),0);}
  for(const name of ['gesturestart','gesturechange','gestureend']){assert.equal(listeners.get(name).options.passive,false);assert.equal(fire(name,0),1);assert.equal(fire(name,0,false),0);}
  guard.setReadOnly(false);assert.equal(content,original);assert.equal(fire('touchmove',2),0);assert.equal(fire('gesturechange',0),0);
  assert.equal(listeners.size,5,'Mode changes do not multiply listeners');
});

test('mobile zoom changes only the touched period even when desktop linked views were enabled',()=>{
  const state={viewOnly:true},sourceChart={period:'m30',view:{start:10,end:20}},other={view:{start:1,end:99},render(){}};
  let saves=0,presences=0,stops=0;
  const c=context({state,charts:{m30:sourceChart,m3:other},$:()=>({classList:{contains:()=>true}}),stopFollowing(){stops++;},saveViews(){saves++;},sendPresence(){presences++;}},['syncView']);
  c.syncView(sourceChart);assert.equal(other.view.start,1);assert.equal(other.view.end,99);assert.equal(saves,1);assert.equal(presences,1);assert.equal(stops,1);
  state.viewOnly=false;c.syncView(sourceChart);assert.equal(other.view.start,10);assert.equal(other.view.end,20,'Desktop linking still works');
});

test('phone landscape, Android tablet, iPad desktop UA and narrow windows stay view-only',()=>{
  const c=context({navigator:{},window:{innerWidth:1400}},['mobileDevice']);
  assert.equal(c.mobileDevice(),false);
  for(const ua of ['Mozilla iPhone','Android Tablet','Windows Phone','Opera Mini']){c.navigator.userAgent=ua;assert.equal(c.mobileDevice(),true);}
  c.navigator={platform:'MacIntel',maxTouchPoints:5};assert.equal(c.mobileDevice(),true);
  c.navigator={userAgentData:{mobile:true}};assert.equal(c.mobileDevice(),true);
  c.navigator={userAgent:'Mozilla/5.0 (X11; Linux x86_64) Chrome/140',platform:'Linux aarch64',maxTouchPoints:5};c.window.matchMedia=()=>({matches:true});assert.equal(c.mobileDevice(),true,'Mobile desktop-site mode stays read-only at wide viewports');
  c.window.matchMedia=()=>({matches:false});assert.equal(c.mobileDevice(),false,'A fine primary mouse on a large touchscreen desktop keeps editing');
  c.navigator={};c.window.innerWidth=900;assert.equal(c.mobileDevice(),true);c.window.innerWidth=901;assert.equal(c.mobileDevice(),false);
});

test('view-only transport rejects all writes except presence before calling fetch',async()=>{
  const requests=[],state={viewOnly:true,generation:1,session:{csrf_token:'test'}};
  const c=context({state,AbortController,readRequests:new Set(),setTimeout,clearTimeout,fetch:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({ok:true})};}},['api']);
  for(const [path,method]of [['/plans/A/patch','POST'],['/plans/A','PUT'],['/boards','POST'],['/profile','POST'],['/risk','PUT']])await assert.rejects(c.api(path,method,{}),/仅供查看/);
  assert.equal(requests.length,0);
  await c.api('/plans/A');await c.api('/presence','POST',{symbol:'A'});
  assert.equal(requests.length,2);assert.ok(requests.every(r=>r.options.headers['X-Workspace-View-Only']==='1'));
  state.viewOnly=false;await c.api('/plans/A/patch','POST',{});assert.equal(requests[2].options.headers['X-Workspace-View-Only'],undefined);
});

test('all plan mutations and draft replay short-circuit in mobile mode',async()=>{
  const names=['restoreOpeningDraft','finishDrawings','startLevel','previewLevel','placeLevel','dragLevel','dirty','persistDraft','selectObject','selectWaveAnchor','recordHistory','travelHistory','addObject','updateObject','deleteObject','requestDeleteAll','deleteAll','clearLevel','clearOpenPlan','objectContext','beginExpectation','syncPlanOpacity','syncObjectField','syncNodeField','flush','restoreDraft','saveProfile','newBoard','createBoard','savePlanForm','planInput'];
  const state={viewOnly:true,plan:{objects:[{id:'kept'}]},pending:{changes:{notes:'desktop draft'}}},before=JSON.stringify(state),c=context({state},names);
  for(const name of names)await c[name]();
  assert.equal(JSON.stringify(state),before);
});

test('viewer presence clears stale desktop editing state but preserves live view and cursor',()=>{
  const sent=[],state={viewOnly:true,symbol:'A',active:'m30',session:{user:{id:'viewer'}},localPresence:{object_id:'old',preview_object:{id:'old'}}};
  const c=context({state,charts:{m30:{period:'m30',view:{start:1,end:2,min:3,max:4}}},presenceSender:{push:(p)=>sent.push(p)},Object,renderEvaluation(){},queueMicrotask},['sendPresence']);
  c.sendPresence({editing:true,object_id:'attempt',preview_object:{id:'attempt'},cursor:{time:1,price:3}},true);
  assert.equal(sent[0].object_id,null);assert.equal(sent[0].preview_object,null);assert.equal(sent[0].view.period,'m30');assert.equal(sent[0].cursor.price,3);assert.equal(state.previewDirty,undefined);
});

test('returning to desktop preserves a suspended local draft until explicitly restored',()=>{
  const stored=new Map([['draft:A','original']]),state={viewOnly:false,session:{user:{id:'u'}},symbol:'A',plan:{symbol:'A',board_id:''},planReady:true,suspendedDrafts:new Set(['A'])};
  const c=context({state,PREFIX:'draft:',draftKey:()=> 'A',hasPending:()=>false,localStorage:{removeItem:key=>stored.delete(key)}},['currentPlan','persistDraft']);
  c.persistDraft();assert.equal(stored.get('draft:A'),'original');
  state.suspendedDrafts.clear();c.persistDraft();assert.equal(stored.size,0);
});

test('a suspended draft already satisfied by peers is removed and cannot overwrite later edits',()=>{
  const E=require('./static/workspace-editing.js'),draft={version:2,pending:M.diff({notes:'A',objects:[]},{notes:'B',objects:[]})},stored=new Map([['draft:A',draft]]);
  const state={viewOnly:false,saved:{notes:'B',objects:[]},suspendedDrafts:new Set(['A'])};
  const c=context({state,M,E,PREFIX:'draft:',draftKey:()=> 'A',storageGet:key=>stored.get('draft:'+key),localStorage:{removeItem:key=>stored.delete(key)},recoverableDraft:()=>stored.get('draft:A')?{key:'A',draft}:null,restoreDraft:()=>assert.fail('Satisfied draft must not replay'),$:()=>({})},['restoreOpeningDraft']);
  c.restoreOpeningDraft();assert.equal(stored.size,0);assert.equal(state.suspendedDrafts.size,0);
  state.saved.notes='C';c.restoreOpeningDraft();assert.equal(state.saved.notes,'C');
});

test('mobile receives newer shared drawings immediately without creating local pending edits',()=>{
  const state={viewOnly:true,symbol:'A',board:'',revision:1,saved:{symbol:'A',board_id:'',revision:1,objects:[]},pending:M.empty()},drawn=[];
  const c=context({state,M,clone:structuredClone,currentPlan:p=>p.symbol==='A'&&!p.board_id,emptyPending:M.empty,hasPending:M.hasPending,fillPlanQuietly(){},planMeta(){},renderObjects(){},charts:{m30:{setPlan:p=>drawn.push(p)}},shownPlan:()=>state.plan,setUnavailable(){}},['receivePlan']);
  c.receivePlan({symbol:'A',board_id:'',revision:2,notes:'伙伴更新',objects:[{id:'remote-wave',points:[1,2,3]}]});
  assert.equal(state.revision,2);assert.equal(state.plan.notes,'伙伴更新');assert.equal(drawn[0].objects[0].id,'remote-wave');assert.equal(state.dirty,false);assert.equal(M.hasPending(state.pending),false);
});

test('a delayed desktop new-board response cannot move a mobile viewer into the wrong symbol',async()=>{
  let reply;const state={viewOnly:false,generation:1,symbol:'A',session:{user:{id:'u'}}},nav=[];
  const c=context({state,ensureSaved:async()=>true,encodeURIComponent,api:()=>new Promise(resolve=>{reply=resolve;}),storageSet:()=>nav.push('storage'),switchSymbol:()=>nav.push('navigation'),openPanel:()=>nav.push('panel')},['createBoard']);
  const work=c.createBoard({title:'New'});await new Promise(resolve=>setImmediate(resolve));state.viewOnly=true;state.generation++;state.symbol='B';reply({plan:{board_id:'board-A'}});assert.equal(await work,false);assert.deepEqual(nav,[]);
});
