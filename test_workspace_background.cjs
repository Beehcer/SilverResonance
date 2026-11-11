'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const M=require('./static/workspace-merge.js'),E=require('./static/workspace-editing.js');
const T=require('./static/workspace-ticket.js'),C=require('./static/workspace-chart.js');
const MarketCache=require('./static/workspace-market-cache.js');
const MarketPreview=require('./static/workspace-market-preview.js');
const source=fs.readFileSync(path.join(__dirname,'static/workspace.js'),'utf8');
const copy=value=>JSON.parse(JSON.stringify(value)),tick=()=>new Promise(resolve=>setImmediate(resolve));
const A='SHFE.rb2701',B='CZCE.RM701',D='DCE.jd2612';
function helper(name){
  const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));
  assert.ok(line,`Missing helper ${name}`);if(line.trimEnd().endsWith('}'))return line;
  const start=source.indexOf(line),end=source.indexOf('\n  }',start);assert.ok(end>=0);return source.slice(start,end+4);
}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
function market(symbol,token='token-'+symbol,price=101){
  const periods=Object.fromEntries(Object.entries(C.INTERVAL).map(([period,step])=>[period,Array.from({length:4},(_,i)=>({dt:new Date(1700000000000+i*step).toISOString(),open:100,high:110,low:90,close:price,volume:0,oi:0}))]));
  return{symbol,name:symbol,history_token:token,incremental:false,period_counts:{day:4,m30:4,m3:4},periods,price,fresh:true,connected:true,source:'live',feed_status:'live'};
}
function plan(symbol,board='',revision=1){return{symbol,board_id:board,revision,title:'Plan '+board,objects:[],notes:'',enabled:true,rule_version:'r1'};}
function harness(){
  const state={session:{user:{id:'me'},csrf_token:'test-token'},symbol:A,board:'',plan:plan(A),saved:plan(A),market:market(A),revision:1,baseRevision:1,pending:M.empty(),outbox:null,dirty:false,saving:false,loading:false,conflict:false,generation:1,cacheEpoch:1,visitedSymbols:new Set([A]),markedSymbols:new Set(),active:'m30',rules:{version:'r1'},risk:{},alerts:[],evaluation:{status:'ready',source:'live',signal_key:'old'},lastSignal:'old',lastSuccess:1};
  const elements=new Map(),storage=new Map(),calls={network:[],paints:[],notices:[],events:0,flush:0,views:0};
  const $=id=>{if(!elements.has(id))elements.set(id,{dataset:{},style:{},value:'',hidden:false,textContent:'',elements:[]});return elements.get(id);};
  const charts=Object.fromEntries(['day','m30','m3'].map(period=>[period,{path:[],setTool(){},setPlan(){},setPresence(){},setData(value,shared,reset){calls.paints.push({period,market:value?copy(value):null,plan:shared?copy(shared):null,reset,active:state.symbol});}}]));
  let responder=async request=>{
    const url=new URL(request.url,'https://example.test'),symbol=url.searchParams.get('symbol')||decodeURIComponent(url.pathname.split('/').at(-1)),board=url.searchParams.get('board')||'';
    if(url.pathname.endsWith('/market'))return{market:market(symbol)};
    if(url.pathname.includes('/plans/'))return{plan:plan(symbol,board)};
    if(url.pathname.endsWith('/review'))return{evaluation:{status:'waiting',source:'live'},plan_revision:state.revision};
    if(url.pathname.endsWith('/quotes'))return{quotes:[]};
    throw new Error('Unexpected request '+url.pathname);
  };
  const context=vm.createContext({
    state,M,E,T,C,clone:copy,WorkspaceMarketPreview:MarketPreview,marketPreview:new MarketPreview.Store({indexedDB:null}),WorkspaceMarketCache:MarketCache,marketCache:new MarketCache.Store(),marketRequests:new Map(),documentCache:new Map(),readRequests:new Set(),
    $,charts,PREFIX:'test:',PERIODS:{day:'日线',m30:'30分钟',m3:'3分钟'},Date,Promise,Map,Set,URL,URLSearchParams,AbortController,
    navigator:{onLine:true},document:{hidden:false,activeElement:null},location:{href:'https://example.test/workspace',hostname:'example.test'},history:{replaceState(){}},
    fetch:async(url,options)=>{const request={url,...options};calls.network.push(request);const data=await responder(request);return{ok:true,status:200,json:async()=>data};},
    setTimeout:callback=>1,clearTimeout:()=>{},
    localStorage:{removeItem:key=>storage.delete(key)},
    storageSet:(key,value)=>{storage.set(key,copy(value));return true;},storageGet:(key,fallback=null)=>storage.has(key)?copy(storage.get(key)):fallback,
    notice:value=>calls.notices.push(value),renderMembers:()=>{},renderSyncStatus:()=>{},renderObjects:()=>{},fillPlan:()=>{},fillPlanQuietly:()=>{},planMeta:()=>{},renderMarket:()=>{},renderSymbols:()=>{},renderQuickPlan:()=>{},updateHistoryButtons:()=>{},renderChecks:()=>{},renderRisk:()=>{},fmtTime:()=>'',addAlert:()=>{},
    setPlanLoading:value=>{state.loading=value;},setSaveDisabled:()=>{},setUnavailable:undefined,
    setLayout:()=>{},stopFollowing:()=>{},saveViews:()=>{},restoreViews:()=>{calls.views++;},sendPresence:()=>{},presenceSender:{reset(){}},
    connectEvents:()=>{calls.events++;},recoverableDraft:()=>null,loadBoards:async()=>{},
    flush:async()=>{calls.flush++;},
  });
  const names=['sharedDocumentKey','cacheDocument','rememberCurrentDocument','restoreOpeningDraft','restoreDraft','batchCharts','preloadSavedMarket','paintOpeningPreview','loadOpeningMarket','applyActiveMarket','api','refreshMarket','refreshBackground','documentKey','currentPlan','marketComplete','cancelObsoleteReads','renderLoadState','closeObjectContext','draftKey','persistDraft','drawingCharts','finishDrawings','emptyPending','hasPending','defaultPlan','shownPlan','clean','acceptPlan','receivePlan','setUnavailable','renderEvaluation','ensureSaved','switchSymbol','poll'];
  for(const name of names)vm.runInContext(helper(name),context);
  return{context,state,calls,elements,storage,charts,$,respond:fn=>{responder=fn;},seed:value=>context.marketCache.accept(value.symbol,value,context.marketCache.begin(value.symbol))};
}

test('switching a warm contract paints cached bars before network resolution and never replays cached READY',async()=>{
  const h=harness(),m=market(B),p=plan(B,'night',4);h.seed(m);h.context.cacheDocument(B,'night',p);
  const marketResponse=deferred(),planResponse=deferred();h.respond(request=>request.url.includes('/market?')?marketResponse.promise:planResponse.promise);
  const switching=h.context.switchSymbol(B,'night');await tick();
  assert.equal(h.state.symbol,B);assert.equal(h.state.loading,false);assert.equal(h.state.plan.board_id,'night');assert.equal(h.state.revision,4);
  assert.ok(h.calls.paints.length>=3);assert.ok(h.calls.paints.every(p=>p.market?.symbol===B&&p.market.periods.m30.length===4));
  assert.equal(h.state.market.fresh,false);assert.equal(h.state.market.client_cached,true);assert.equal(h.state.evaluation,null);assert.notEqual(h.$('decision').dataset.status,'READY');
  h.respond(async request=>request.url.includes('/market?')?{market:m}:request.url.includes('/review?')?{evaluation:{status:'waiting',source:'live'},plan_revision:4}:request.url.includes('/quotes')?{quotes:[]}:{plan:p});
  marketResponse.resolve({market:m});planResponse.resolve({plan:p});await switching;assert.equal(h.state.market.fresh,true);
});

test('an old contract response may fill its own cache but cannot repaint the newly selected contract',async()=>{
  const h=harness(),waitA=deferred();h.seed(market(B));h.context.cacheDocument(B,'',plan(B));
  h.respond(request=>{
    const url=new URL(request.url,'https://example.test'),symbol=url.searchParams.get('symbol');
    if(url.pathname.endsWith('/market'))return symbol===A?waitA.promise:{market:market(B)};
    if(url.pathname.endsWith('/review'))return{evaluation:{status:'waiting',source:'live'},plan_revision:1};
    if(url.pathname.endsWith('/quotes'))return{quotes:[]};
    return{plan:plan(decodeURIComponent(url.pathname.split('/').at(-1)))};
  });
  const oldPoll=h.context.poll();await tick();await h.context.switchSymbol(B);const paintedBefore=h.calls.paints.length;
  waitA.resolve({market:market(A,'old-A',103)});await oldPoll;
  assert.equal(h.state.symbol,B);assert.equal(h.state.market.symbol,B);assert.equal(h.context.marketCache.get(A).price,103);
  assert.ok(h.calls.paints.slice(paintedBefore).every(p=>p.market?.symbol===B));
});

test('market requests coalesce per contract and an obsolete result cannot roll back a newer same-contract cache',async()=>{
  const h=harness(),response=deferred();h.respond(()=>response.promise);
  const first=h.context.refreshMarket(A),second=h.context.refreshMarket(A);assert.equal(h.calls.network.length,1);
  const latest=market(A,'new-generation',104),ticket=h.context.marketCache.begin(A);h.context.marketCache.accept(A,latest,ticket);
  response.resolve({market:market(A,'old-generation',101)});const result=await Promise.all([first,second]);
  assert.ok(result.every(m=>m.history_token==='new-generation'&&m.price===104));assert.equal(h.context.marketCache.get(A).price,104);assert.equal(h.context.marketRequests.size,0);
});

test('a malformed delta retries without history using the same ticket and keeps the full baseline until replacement',async()=>{
  const h=harness(),base=market(A,'base');h.seed(base);h.state.board='unrelated-board';
  const fullResponse=deferred();let count=0;h.respond(request=>{count++;if(count===1){const bad=copy(base);bad.incremental=true;bad.periods=Object.fromEntries(Object.entries(bad.periods).map(([p,rows])=>[p,rows.slice(-2)]));bad.periods.m3[0].dt='2000-01-01T00:00:00Z';return{market:bad};}return fullResponse.promise;});
  const task=h.context.refreshMarket(A);await tick();assert.equal(h.calls.network.length,2);
  const urls=h.calls.network.map(call=>new URL(call.url,'https://example.test'));assert.equal(urls[0].searchParams.get('history'),'base');assert.equal(urls[1].searchParams.has('history'),false);assert.ok(urls.every(url=>!url.searchParams.has('board')));
  assert.equal(h.context.marketCache.get(A).history_token,'base');const replacement=market(A,'full-fixed',105);fullResponse.resolve({market:replacement});const result=await task;assert.equal(result.price,105);assert.equal(h.context.marketCache.history(A),'full-fixed');
});

test('invalid full retry is bounded and does not corrupt the previously rendered history',async()=>{
  const h=harness(),base=market(A);h.seed(base);h.respond(()=>({market:{...copy(base),symbol:B}}));
  await assert.rejects(h.context.refreshMarket(A),/未能对齐/);assert.equal(h.calls.network.length,2);assert.equal(h.context.marketRequests.size,0);assert.deepEqual(h.context.marketCache.get(A),base);
});

test('hidden-page background work only refreshes visited contracts using one sequential worker',async()=>{
  const h=harness();h.context.document.hidden=true;h.state.visitedSymbols=new Set([A,B,D,'CZCE.AP701']);
  const pending=[],requested=[];let active=0,max=0;h.respond(request=>{const url=new URL(request.url,'https://example.test'),symbol=url.searchParams.get('symbol');requested.push(symbol);active++;max=Math.max(max,active);const wait=deferred();pending.push({wait,symbol});return wait.promise.finally(()=>active--);});
  const background=h.context.refreshBackground();await tick();assert.equal(h.calls.network.length,1);assert.equal(h.state.backgroundRefreshing,true);
  await h.context.refreshBackground();assert.equal(h.calls.network.length,1);
  pending[0].wait.resolve({market:market(pending[0].symbol)});await tick();assert.equal(h.calls.network.length,2);
  pending[1].wait.resolve({market:market(pending[1].symbol)});await tick();assert.equal(h.calls.network.length,3);pending[2].wait.resolve({market:market(pending[2].symbol)});await background;
  assert.equal(max,1);assert.deepEqual(new Set(requested),new Set([B,D,'CZCE.AP701']));assert.equal(h.state.backgroundRefreshing,false);assert.equal(h.calls.paints.length,0);
});

test('inactive plans use their own explicit board scope while same-contract charts stay shared',async()=>{
  const h=harness();h.state.board='active';h.context.cacheDocument(A,'active',plan(A,'active',1));h.context.cacheDocument(A,'other',plan(A,'other',2));h.context.cacheDocument(B,'',plan(B,'',3));h.state.visitedSymbols=new Set([A]);
  h.respond(request=>{const url=new URL(request.url,'https://example.test'),symbol=decodeURIComponent(url.pathname.split('/').at(-1)),board=url.searchParams.get('board')||'';return{plan:plan(symbol,board,9)};});
  await h.context.refreshBackground();assert.equal(h.calls.network.length,2);
  const urls=h.calls.network.map(call=>new URL(call.url,'https://example.test'));
  assert.equal(urls.find(url=>url.pathname.endsWith(A)).searchParams.get('board'),'other');assert.equal(urls.find(url=>url.pathname.endsWith(B)).searchParams.has('board'),false);
  assert.equal(h.context.documentCache.get(A+':other').plan.revision,9);assert.equal(h.context.documentCache.get(B+':').plan.revision,9);assert.equal(h.context.documentCache.get(A+':active').plan.revision,1);assert.equal(h.state.plan.symbol,A);assert.equal(h.calls.paints.length,0);
});

test('changing session epoch stops queued background work and prevents an old plan from repopulating the new document cache',async()=>{
  const h=harness();h.state.visitedSymbols=new Set([A]);for(const symbol of [B,D,'CZCE.AP701'])h.context.cacheDocument(symbol,'night',plan(symbol,'night'));
  const pending=[];h.respond(()=>{const wait=deferred();pending.push(wait);return wait.promise;});const background=h.context.refreshBackground();await tick();assert.equal(pending.length,1);
  h.state.cacheEpoch++;h.state.session={user:{id:'other'}};h.context.documentCache.clear();h.state.backgroundRefreshing=false;
  pending[0].resolve({plan:plan(B,'night',7)});await background;
  assert.equal(h.calls.network.length,1);assert.equal(h.context.documentCache.size,0);assert.equal(h.state.backgroundRefreshing,false);
});

test('switch intent waits for an outstanding commit instead of clearing dirty data or starting target requests',async()=>{
  const h=harness(),commit=deferred();h.state.outbox={id:'unknown'};h.state.dirty=true;
  h.context.flush=async()=>{h.calls.flush++;await commit.promise;h.state.outbox=null;h.state.dirty=false;};
  const switching=h.context.switchSymbol(B);await tick();assert.equal(h.state.symbol,A);assert.equal(h.state.outbox.id,'unknown');assert.equal(h.calls.network.length,0);
  commit.resolve();await switching;assert.equal(h.state.symbol,B);assert.equal(h.state.outbox,null);assert.equal(h.calls.flush,1);
});

test('a failed save guard retains the current contract and outbox without touching target caches',async()=>{
  const h=harness();h.state.outbox={id:'unknown'};h.context.Date={now:(()=>{const values=[0,1,15000];return()=>values.shift()??15000;})()};
  h.context.setTimeout=fn=>{fn();return 1;};await h.context.switchSymbol(B);
  assert.equal(h.state.symbol,A);assert.equal(h.state.outbox.id,'unknown');assert.equal(h.calls.network.length,0);assert.equal(h.context.marketCache.get(B),null);assert.ok(h.calls.notices.some(text=>text.includes('尚未保存')));
});

test('warm and cold openings restore saved drafts before enabling editing and never replay them over newer local edits',async()=>{
  for(const warm of [false,true]){
    const h=harness(),baseline=plan(B,'night',4),draftPlan={...baseline,notes:'restored draft'};
    if(warm){h.seed(market(B));h.context.cacheDocument(B,'night',baseline);}
    let recoveries=0;h.context.recoverableDraft=()=>{recoveries++;return{key:'draft:me:'+B+':night',draft:{version:2,base_revision:4,pending:M.diff(baseline,draftPlan),outbox:null,plan:draftPlan}};};
    const opened=[];h.context.setPlanLoading=value=>{h.state.loading=value;if(!value)opened.push({notes:h.state.plan.notes,dirty:h.state.dirty});};
    const planResponse=deferred(),marketResponse=deferred();h.respond(request=>request.url.includes('/market?')?marketResponse.promise:planResponse.promise);
    const switching=h.context.switchSymbol(B,'night');await tick();
    if(warm){assert.equal(opened[0].notes,'restored draft');assert.equal(opened[0].dirty,true);h.state.plan.notes='newer local typing';h.state.pending=M.diff(h.state.saved,h.state.plan);}
    h.respond(async request=>request.url.includes('/market?')?{market:market(B)}:request.url.includes('/review?')?{evaluation:{status:'waiting',source:'live'},plan_revision:5}:request.url.includes('/quotes')?{quotes:[]}:{plan:{...baseline,revision:5,notes:'remote update'}});
    planResponse.resolve({plan:{...baseline,revision:5,notes:'remote update'}});marketResponse.resolve({market:market(B)});await switching;
    assert.equal(recoveries,1);assert.equal(opened[0].notes,'restored draft');assert.equal(opened[0].dirty,true);assert.equal(h.state.plan.notes,warm?'newer local typing':'restored draft');assert.notEqual(h.$('decision').dataset.status,'READY');
  }
});

test('draft restore observes the real loading save guard and an EventSource open retries its pending save',async()=>{
  const h=harness(),baseline=plan(B,'night',4),draft={...baseline,notes:'pending draft'};
  h.state.symbol=B;h.state.board='night';h.state.plan=baseline;h.state.saved=baseline;h.state.loading=true;h.state.pending=M.empty();
  vm.runInContext(helper('flush'),h.context);
  h.context.restoreDraft({key:h.context.draftKey(),draft:{version:2,base_revision:4,pending:M.diff(baseline,draft),outbox:null,plan:draft}});
  await tick();assert.equal(h.state.dirty,true);assert.equal(h.calls.network.length,0);assert.equal(h.state.plan.notes,'pending draft');
  let source;h.context.EventSource=class{constructor(){this.listeners={};source=this;}addEventListener(type,callback){this.listeners[type]=callback;}close(){}};
  h.context.flush=async()=>{h.calls.flush++;};h.state.loading=false;vm.runInContext(helper('connectEvents'),h.context);h.context.connectEvents();source.listeners.open();await tick();assert.equal(h.calls.flush,1);
});

test('an obsolete switch or poll cannot repopulate shared documents after a session reset',async()=>{
  for(const operation of ['switch','poll']){
    const h=harness(),waiting=deferred();h.respond(()=>waiting.promise);const task=operation==='switch'?h.context.switchSymbol(B,'night'):h.context.poll();await tick();
    h.state.cacheEpoch++;h.state.generation++;h.state.session={user:{id:'other'}};h.context.documentCache.clear();const paints=h.calls.paints.length;
    waiting.resolve({plan:plan(operation==='switch'?B:A,'night',9),market:market(operation==='switch'?B:A),evaluation:{status:'ready',source:'live'},quotes:[]});await task;
    assert.equal(h.context.documentCache.size,0);assert.equal(h.calls.paints.length,paints);
  }
});

test('queued background jobs skip contracts dropped from the visited set or newly made active',async()=>{
  const h=harness(),pending=[];h.state.visitedSymbols=new Set([A,B,D,'CZCE.AP701','CZCE.TA701']);
  h.respond(request=>{const symbol=new URL(request.url,'https://example.test').searchParams.get('symbol'),wait=deferred();pending.push({symbol,wait});return wait.promise;});
  const task=h.context.refreshBackground();await tick();assert.equal(pending.length,1);h.state.visitedSymbols.delete('CZCE.AP701');h.state.symbol='CZCE.TA701';
  pending[0].wait.resolve({market:market(pending[0].symbol)});await tick();assert.equal(pending.length,2);assert.equal(pending[1].symbol,D);pending[1].wait.resolve({market:market(D)});await task;assert.equal(h.calls.network.length,2);
});

test('opening a contract pins it and retains only the sixteen most recently visited contracts',async()=>{
  const h=harness();h.state.visitedSymbols=new Set(Array.from({length:16},(_,i)=>'TEST.p'+i));await h.context.switchSymbol(B);
  assert.equal(h.context.marketCache.pinned,B);assert.equal(h.state.visitedSymbols.size,16);assert.equal(h.state.visitedSymbols.has('TEST.p0'),false);assert.equal([...h.state.visitedSymbols].at(-1),B);
});

test('a warm plan-load failure can be retried by clicking the same contract',async()=>{
  const h=harness();h.seed(market(B));h.context.cacheDocument(B,'',plan(B));h.respond(request=>request.url.includes('/market?')?{market:market(B)}:Promise.reject(new Error('read failed')));
  await h.context.switchSymbol(B);assert.equal(h.state.planLoadFailed,true);assert.equal(h.state.loading,false);const before=h.calls.network.length;
  h.respond(async request=>request.url.includes('/market?')?{market:market(B)}:request.url.includes('/review?')?{evaluation:{status:'waiting',source:'live'},plan_revision:1}:request.url.includes('/quotes')?{quotes:[]}:{plan:plan(B)});
  await h.context.switchSymbol(B);assert.ok(h.calls.network.length>before);assert.equal(h.state.planLoadFailed,false);
});

test('cold opening paints a short preview before full download and keeps plan anchors hidden until aligned',async()=>{
  const h=harness(),full=deferred(),p=plan(B);p.objects=[{id:'anchored',kind:'wave',points:[{time:1,price:100,chart_anchor:{period:'m30',base_time:1,offset:30}}]}];
  h.respond(request=>{if(request.url.includes('preview=240'))return{market:{...market(B),partial:true,history_token:null,fresh:false}};if(request.url.includes('/market?'))return full.promise;if(request.url.includes('/review?'))return{evaluation:{status:'waiting'},plan_revision:1};if(request.url.includes('/quotes'))return{quotes:[]};return{plan:p};});
  const switching=h.context.switchSymbol(B);await tick();
  assert.equal(h.state.market.partial,true);assert.equal(h.state.loading,true);assert.equal(h.state.plan.objects.length,1);assert.equal(h.context.marketCache.history(B),null);
  assert.ok(h.calls.paints.filter(p=>p.market?.partial).every(p=>p.plan.objects.length===0));
  full.resolve({market:market(B)});await switching;
  assert.equal(h.state.loading,false);assert.equal(h.state.market.fresh,true);assert.equal(h.calls.paints.at(-1).plan.objects[0].id,'anchored');
  assert.equal(h.calls.network.filter(r=>r.url.includes('/market?')).length,2);assert.equal(h.calls.network.filter(r=>r.url.includes('/plans/')).length,1);
});

test('persisted preview paints while the first network response is still pending, without restoring READY',async()=>{
  const h=harness(),wait=deferred();h.state.symbol=B;h.state.market=null;h.state.planReady=false;h.context.marketPreview.put(market(B));h.respond(()=>wait.promise);
  const loading=h.context.loadOpeningMarket(B,h.state.generation);await tick();
  assert.equal(h.state.market.partial,true);assert.equal(h.state.market.fresh,false);assert.equal(h.context.marketCache.history(B),null);assert.notEqual(h.$('decision').dataset.status,'READY');
  h.state.generation++;h.state.symbol=D;wait.resolve({market:market(B)});assert.equal(await loading,null);
});

test('late disk preview and a wrong-symbol preview cannot overwrite the selected market',async()=>{
  const h=harness(),disk=deferred();h.state.symbol=null;h.context.marketPreview={get:()=>disk.promise};
  const preload=h.context.preloadSavedMarket(A,h.state.generation);h.state.symbol=B;h.state.market=market(B);h.state.generation++;disk.resolve(market(A));await preload;
  h.context.paintOpeningPreview(market(A),h.state.generation);
  assert.equal(h.state.market.symbol,B);assert.equal(h.calls.paints.length,0);
});

test('disk preview starts without waiting for rules or risk and never restores saved view on a partial timeline',async()=>{
  const h=harness();h.state.symbol=null;h.state.market=null;h.context.marketPreview.put(market(B));
  await h.context.preloadSavedMarket(B,h.state.generation);
  assert.equal(h.state.symbol,B);assert.equal(h.state.market.partial,true);assert.equal(h.calls.views,0);assert.equal(h.state.loading,true);
});

test('a failed full download can recover through polling while the partial view remains locked',async()=>{
  const h=harness();h.state.symbol=B;h.state.market={...market(B),partial:true,fresh:false};h.state.plan=plan(B);h.state.planReady=true;h.state.loading=true;
  h.respond(request=>request.url.includes('/market?')?Promise.reject(new Error('temporary')):request.url.includes('/review?')?{evaluation:{status:'ready'},plan_revision:1}:request.url.includes('/quotes')?{quotes:[]}:{plan:plan(B)});
  await h.context.poll();assert.equal(h.state.loading,true);assert.equal(h.state.market.fresh,false);
  h.respond(request=>request.url.includes('/market?')?{market:market(B)}:request.url.includes('/review?')?{evaluation:{status:'waiting'},plan_revision:1}:request.url.includes('/quotes')?{quotes:[]}:{plan:plan(B)});
  await h.context.poll();assert.equal(h.state.loading,false);assert.equal(h.state.market.partial,undefined);assert.equal(h.state.market.fresh,true);
});

test('completing full history resumes a draft that could not flush during the preview',()=>{
  const h=harness();h.state.market={...market(A),partial:true,fresh:false};h.state.loading=true;h.state.planReady=true;h.state.pending.fields={notes:'kept draft'};h.state.outbox={id:'pending'};
  h.context.applyActiveMarket(market(A));assert.equal(h.state.loading,false);assert.equal(h.calls.flush,1);
});

test('receivePlan ignores wrong symbol or board even with a higher revision and an outstanding outbox',()=>{
  for(const outbox of [null,{id:'pending-save'}])for(const incoming of [plan(B,'night',99),plan(A,'other',99),{revision:99,objects:[]},null]){
    const h=harness(),baseline=plan(A,'night',3);h.state.board='night';h.state.plan={...baseline,notes:'local draft'};h.state.saved=copy(baseline);h.state.revision=3;h.state.pending=M.diff(baseline,h.state.plan);h.state.dirty=true;h.state.outbox=outbox;h.state.queuedRemote=plan(A,'night',4);
    const before=copy(h.state),beforePaints=h.calls.paints.length;h.context.receivePlan(incoming);
    assert.deepEqual(copy(h.state),before);assert.equal(h.calls.paints.length,beforePaints);assert.equal(h.calls.notices.length,0);
  }
});

test('acceptPlan rejects a foreign cached document before clearing local selection, edits or revision',()=>{
  const h=harness();h.state.selected='local-layer';h.state.plan.objects=[{id:'local-layer',kind:'text',name:'本机计划'}];h.state.pending=M.diff(h.state.saved,h.state.plan);h.state.dirty=true;const before=copy(h.state);
  for(const incoming of [plan(B,'',99),plan(A,'other',99)])h.context.acceptPlan(incoming,true);
  assert.deepEqual(copy(h.state),before);assert.equal(h.calls.paints.length,0);
});

test('same-document remote revisions merge pending notes and only matching plans can queue behind a save',()=>{
  const h=harness(),baseline=plan(A,'night',3);h.state.board='night';h.state.plan={...baseline,notes:'本地正在编辑'};h.state.saved=copy(baseline);h.state.revision=3;h.state.pending=M.diff(baseline,h.state.plan);h.state.dirty=true;
  const peer={...baseline,revision:4,notes:'伙伴备注',objects:[{id:'peer',kind:'text',name:'伙伴第一行\n伙伴第二行'}]};h.context.receivePlan(peer);
  assert.equal(h.state.revision,4);assert.equal(h.state.saved.notes,'伙伴备注');assert.equal(h.state.plan.notes,'本地正在编辑');assert.deepEqual(h.state.plan.objects,peer.objects);assert.equal(h.state.dirty,true);assert.equal(h.state.evaluation,null);
  h.state.outbox={id:'in-flight'};h.context.receivePlan({...peer,revision:5});assert.equal(h.state.queuedRemote.revision,5);assert.equal(h.state.revision,4);
  h.context.receivePlan(plan(B,'night',100));assert.equal(h.state.queuedRemote.revision,5);assert.equal(h.state.queuedRemote.symbol,A);
});

test('a full response with any missing timeframe keeps drawing locked until all three histories arrive',()=>{
  for(const missing of ['day','m30','m3']){
    const h=harness(),incomplete=market(A);h.state.loading=true;h.state.planReady=true;incomplete.periods[missing]=[];incomplete.period_counts[missing]=0;
    h.context.applyActiveMarket(incomplete);assert.equal(h.state.loading,true,missing);assert.equal(h.calls.flush,0);assert.equal(h.$('market-loading').hidden,false);assert.match(h.$('market-loading-detail').textContent,/等待/);
    h.context.applyActiveMarket(market(A));assert.equal(h.state.loading,false);assert.equal(h.$('market-loading').hidden,true);
  }
});

test('valid plan polling recovery clears the stale plan-load error and removes its loading overlay',async()=>{
  const h=harness();h.state.planLoadFailed=true;h.state.planLoadError='先前的计划读取超时';h.state.planReady=true;h.context.renderLoadState();assert.equal(h.$('market-loading').hidden,false);assert.equal(h.$('market-loading').dataset.error,'true');
  await h.context.poll();
  assert.equal(h.state.planLoadFailed,false);assert.equal(h.state.planLoadError,null);assert.equal(h.state.planReady,true);assert.equal(h.state.revision,1,'same revision is still a successful retry');assert.equal(h.$('market-loading').hidden,true);assert.equal(h.$('market-loading').dataset.error,'false');
});

test('a wrong-document plan polling response cannot dismiss an existing plan-load failure',async()=>{
  for(const foreign of [plan(B,'',99),plan(A,'other',99)]){
    const h=harness();h.state.planLoadFailed=true;h.state.planLoadError='需要重试原计划';h.state.planReady=true;const before=copy(h.state.plan);
    h.respond(request=>request.url.includes('/plans/')?{plan:foreign}:request.url.includes('/market?')?{market:market(A)}:request.url.includes('/review?')?{evaluation:{status:'waiting'},plan_revision:1}:{quotes:[]});
    await h.context.poll();h.context.renderLoadState();
    assert.equal(h.state.planLoadFailed,true);assert.equal(h.state.planLoadError,'需要重试原计划');assert.deepEqual(h.state.plan,before);assert.equal(h.state.revision,1);assert.equal(h.$('market-loading').hidden,false);assert.equal(h.$('market-loading').dataset.error,'true');assert.equal(h.context.documentCache.size,0);
  }
});


test('releasing space after switching cannot restore the old contract drawing draft',async()=>{
  const h=harness();h.state.spaceTool={m30:{tool:'wave',path:[{time:1,price:100},{time:2,price:110}]}};
  h.seed(market(B));h.context.cacheDocument(B,'',plan(B));await h.context.switchSymbol(B);
  assert.equal(h.state.spaceTool,null);
  let keyup;h.context.document.addEventListener=(name,handler)=>{if(name==='keyup')keyup=handler;};
  vm.runInContext(source.split(/\r?\n/).find(line=>line.includes("document.addEventListener('keyup'")),h.context);
  keyup({code:'Space'});assert.equal(h.charts.m30.path.length,0);assert.notEqual(h.charts.m30.tool,'wave');assert.equal(h.state.symbol,B);
});
