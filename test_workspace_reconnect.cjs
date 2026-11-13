'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const MarketCache=require('./static/workspace-market-cache.js'),M=require('./static/workspace-merge.js');
const source=fs.readFileSync(path.join(__dirname,'static/workspace.js'),'utf8');
function helper(name){
  const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));
  assert.ok(line,`Missing helper ${name}`);if(line.trimEnd().endsWith('}'))return line;
  const start=source.indexOf(line),end=source.indexOf('\n  }',start);assert.ok(end>=0);return source.slice(start,end+4);
}

test('reconnecting clears old readiness before an early disk preview while bootstrap requests remain pending',async()=>{
  const symbol='SHFE.rb2701',oldPlan={symbol,board_id:'previous-board',notes:'unsaved old session draft',objects:[]};
  const state={session:{user:{id:'previous-user'}},plan:oldPlan,saved:oldPlan,planReady:true,board:'previous-board',revision:42,
    generation:9,cacheEpoch:1,loading:false,dirty:true,polling:false,evaluation:{status:'ready',source:'live'},lastSignal:'obsolete-ready'};
  const calls=[],nodes=new Map(),savedDrafts=[],pending=new Promise(()=>{}),noop=()=>{},oldRead={controller:new AbortController(),cancelled:false};let closedEvents=0;state.eventSource={close(){closedEvents++;}};state.contextObject='old-layer';state.contextDocument=symbol+':previous-board';state.contextGeneration=9;
  const preview={symbol,partial:true,client_cached:true,fresh:false,periods:{day:[],m30:[],m3:[]}};
  const context=vm.createContext({
    state,WorkspaceMarketCache:MarketCache,marketCache:new MarketCache.Store(),marketRequests:new Map([[symbol,pending]]),documentCache:new Map(),readRequests:new Set([oldRead]),
    marketPreview:{get:async()=>preview},presenceSender:{reset:noop},URLSearchParams,Set,Map,Promise,
    location:{search:'?symbol='+symbol},
    $:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);},
    persistDraft:()=>savedDrafts.push(structuredClone(state.plan)),emptyPending:M.empty,
    setPlanLoading:value=>{state.loading=value;},renderMembers:noop,alertKey:()=>'',storageGet:(key,fallback)=>fallback,setLayout:noop,
    paintOpeningPreview:(market,generation)=>{assert.equal(generation,state.generation);state.market=market;state.loading=true;},
    api:url=>{calls.push(url);return pending;},
    refreshMarket:()=>{calls.push('UNEXPECTED_MARKET_RETRY');return pending;},
    notice:noop,setUnavailable:noop,renderLoadState:noop,applyViewMode:noop,
  });
  for(const name of ['cancelObsoleteReads','closeObjectContext','preloadSavedMarket','enter','poll'])vm.runInContext(helper(name),context);
  void context.enter({user:{id:'restored-user'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(savedDrafts,[oldPlan],'The old draft must be retained before clearing live state.');
  assert.equal(oldRead.cancelled,true);assert.equal(oldRead.controller.signal.aborted,true);assert.equal(context.readRequests.size,0);assert.equal(context.marketRequests.size,0);assert.equal(closedEvents,1);assert.equal(state.contextObject,null);assert.equal(state.contextGeneration,null);
  assert.deepEqual(calls,['/symbols','/rules','/risk']);assert.equal(state.symbol,symbol);assert.equal(state.market.partial,true);
  assert.equal(state.loading,true);assert.equal(state.planReady,false);assert.equal(state.plan,null);assert.equal(state.evaluation,null);assert.equal(state.lastSignal,null);
  await context.poll();
  assert.deepEqual(calls,['/symbols','/rules','/risk'],'A timer must not request the previous board/revision or full market before bootstrap completes.');
});

function modeHarness(overrides={}){
  const nodes=new Map(),storage=new Map(),noop=()=>{},state={generation:5,viewOnly:false,bootstrapping:false,session:{user:{id:'test-user'}},symbol:'SHFE.rb2701',board:'',pending:M.empty(),layout:'main',loading:false,...overrides},calls={navigations:[],writes:[],readOnly:[]};
  const range={start:10,end:30,min:90,max:110},charts={m30:{period:'m30',view:range,autoPrice:true,followLatest:false,viewCustomized:true,render:noop,setReadOnly:value=>calls.readOnly.push(value)}};
  const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,value:'',options:[],classList:{toggle:noop},close:noop,replaceChildren(){this.options=[];},append(option){this.options.push(option);if(!this.value)this.value=option.value;}});return nodes.get(id);};
  const context=vm.createContext({state,charts,M,E:{equal:(a,b)=>JSON.stringify(a)===JSON.stringify(b)},PREFIX:'test:',WorkspaceMarketCache:MarketCache,marketCache:new MarketCache.Store(),marketRequests:new Map(),documentCache:new Map(),readRequests:new Set(),presenceSender:{reset:noop},marketPreview:{get:async()=>null},location:{search:'?symbol=SHFE.rb2701'},URLSearchParams,Set,Map,Promise,clearTimeout,
    pageZoomGuard:{setReadOnly:noop},openPanel:noop,renderLoadState:noop,width:1200,mobileDevice(){return context.width<=900;},clone:structuredClone,$:node,document:{documentElement:{classList:{toggle:noop}}},
    storageGet:(key,fallback=null)=>storage.has(key)?structuredClone(storage.get(key)):fallback,storageSet:(key,value)=>{calls.writes.push(key);storage.set(key,structuredClone(value));return true;},localStorage:{removeItem:key=>storage.delete(key.slice(5))},
    emptyPending:M.empty,hasPending:pending=>M.hasPending(pending??state.pending),persistDraft:noop,readPlan:()=>state.plan,computePending:()=>M.diff(state.saved||{},state.plan||{}),
    setLayout:id=>{state.layout=state.viewOnly?'mobile':id;},setPlanLoading:value=>{state.loading=value;},renderMobilePlan:noop,renderSyncStatus:noop,renderMembers:noop,alertKey:()=>'',el:(tag,text)=>({tag,textContent:text}),notice:noop,setUnavailable:noop,
    switchSymbol:async(symbol,board,options={})=>{calls.navigations.push({symbol,board,...options});if(!options.skipSaveViews)context.saveViews();context.restoreViews();},
  });
  for(const name of ['documentKey','currentPlan','draftKey','viewKey','saveViews','restoreViews','closeObjectContext','applyViewMode'])vm.runInContext(helper(name),context);
  return{context,state,calls,charts,storage,nodes};
}

test('resizing to viewer during delayed bootstrap does not abandon enter or issue a premature document switch',async()=>{
  const {context,state,calls}=modeHarness({symbol:null,session:null,plan:null,saved:null});
  let release;const gate=new Promise(resolve=>{release=resolve;}),requests=[];
  context.api=async url=>{requests.push(url);await gate;return url==='/symbols'?{symbols:[{symbol:'SHFE.rb2701',name:'螺纹'}]}:url==='/rules'?{rules:{version:'1'}}:{risk:{}};};
  for(const name of ['cancelObsoleteReads','preloadSavedMarket','enter'])vm.runInContext(helper(name),context);
  const entering=context.enter({user:{id:'test-user'},mobile_device:false});await new Promise(resolve=>setImmediate(resolve));
  const generation=state.generation;assert.equal(state.bootstrapping,true);assert.equal(state.symbol,null);assert.deepEqual(requests,['/symbols','/rules','/risk']);
  context.width=390;context.applyViewMode();assert.equal(state.viewOnly,true);assert.equal(state.generation,generation,'Mode resize must not invalidate the pending symbol/rules bootstrap.');assert.deepEqual(calls.navigations,[]);assert.equal(state.layout,'mobile');
  release();await entering;assert.equal(state.bootstrapping,false);assert.equal(state.symbols[0].symbol,'SHFE.rb2701');assert.equal(calls.navigations.length,1);assert.equal(calls.navigations[0].symbol,'SHFE.rb2701');assert.equal(state.layout,'mobile');
});

test('desktop and viewer viewport ranges are saved under their old mode before either transition',()=>{
  const {context,state,calls,charts,storage}=modeHarness(),desktop=structuredClone(charts.m30.view),mobile={start:100,end:160,min:200,max:240};
  storage.set('layout','main');storage.set('mobile:view:test-user:SHFE.rb2701:mobile',{m30:{view:mobile,autoPrice:false,followLatest:false,viewCustomized:true}});
  context.width=390;context.applyViewMode();assert.equal(state.viewOnly,true);assert.deepEqual(storage.get('view:test-user:SHFE.rb2701:main').m30.view,desktop);assert.deepEqual(charts.m30.view,mobile);assert.equal(calls.navigations.at(-1).skipSaveViews,true);
  const inspected={start:105,end:130,min:205,max:220};charts.m30.view=inspected;context.width=1200;context.applyViewMode();
  assert.equal(state.viewOnly,false);assert.deepEqual(storage.get('mobile:view:test-user:SHFE.rb2701:mobile').m30.view,inspected);assert.deepEqual(storage.get('view:test-user:SHFE.rb2701:main').m30.view,desktop);assert.deepEqual(charts.m30.view,desktop);assert.equal(calls.navigations.at(-1).skipSaveViews,true);
  assert.deepEqual(calls.readOnly,[true,false]);
});

test('a viewer page reload preserves its existing disk draft when widening before the document recovery step',()=>{
  const saved={symbol:'SHFE.rb2701',board_id:'',notes:'server text',objects:[]},local={...saved,notes:'unsent desktop text'},pending=M.diff(saved,local),draft={version:2,pending,plan:local,base_revision:3,outbox:null};
  const {context,state,storage}=modeHarness({viewOnly:true,layout:'mobile',plan:saved,saved,revision:3}),key='draft:test-user:SHFE.rb2701:',restored=[];
  storage.set(key,draft);assert.equal(state.suspendedDrafts,undefined,'A refreshed viewer has no previous in-memory suspension markers.');
  for(const name of ['persistDraft','recoverableDraft','restoreOpeningDraft'])vm.runInContext(helper(name),context);
  context.restoreDraft=recovery=>restored.push(structuredClone(recovery));
  context.switchSymbol=async()=>{context.persistDraft();context.restoreOpeningDraft();};
  context.persistDraft();assert.ok(storage.has(key),'Viewing never removes the existing desktop draft.');
  context.width=1200;context.applyViewMode();
  assert.equal(state.viewOnly,false);assert.ok(storage.has(key),'The clean viewer plan must not erase a disk draft before recovery.');
  assert.deepEqual(restored,[{key,draft}]);assert.equal(state.suspendedDrafts.has(key),true);
});

test('a disk draft satisfied while the viewer was reloaded is cleaned on widening instead of replayed',()=>{
  const old={symbol:'SHFE.rb2701',board_id:'',notes:'old text',objects:[]},saved={...old,notes:'shared text'},draft={version:2,pending:M.diff(old,saved),plan:saved,outbox:null};
  const {context,state,storage}=modeHarness({viewOnly:true,layout:'mobile',plan:saved,saved}),key='draft:test-user:SHFE.rb2701:';
  storage.set(key,draft);context.recoverableDraft=()=>storage.has(key)?{key,draft}:null;context.restoreDraft=()=>assert.fail('Already satisfied text must not be replayed.');
  for(const name of ['persistDraft','restoreOpeningDraft'])vm.runInContext(helper(name),context);
  context.switchSymbol=async()=>{context.persistDraft();context.restoreOpeningDraft();};
  context.width=1200;context.applyViewMode();assert.equal(storage.has(key),false);assert.equal(state.suspendedDrafts.size,0);
});
