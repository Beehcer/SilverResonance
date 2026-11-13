'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('static/workspace.js','utf8');
function helper(name){const line=source.split(/\r?\n/).find(line=>new RegExp('^  (?:async )?function '+name+'\\(').test(line));assert.ok(line,name);if(line.trimEnd().endsWith('}'))return line;const start=source.indexOf(line);return source.slice(start,source.indexOf('\n  }',start)+4);}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
function harness(){
  const timers=[],requests=[],notices=[],elements=new Map(),state={generation:1,symbol:'A',board:'night',revision:1,session:{csrf_token:'csrf',user:{id:'me'}}};
  const $=id=>{if(!elements.has(id))elements.set(id,{dataset:{},hidden:false,setAttribute(key,value){this[key]=value;}});return elements.get(id);};
  const context=vm.createContext({state,$,readRequests:new Set(),marketRequests:new Map(),AbortController,Map,Set,Date,Promise,PERIODS:{day:'日线',m30:'30分',m3:'3分'},encodeURIComponent,
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{},renderSyncStatus(){},notice:m=>notices.push(m),scheduleReview(){},renderMembers(){},sendPresence(){},presenceSender:{reset(){}},hasPending:()=>false,flush(){},
    fetch:(url,options)=>{const wait=deferred();requests.push({url,...options,...wait});options.signal.addEventListener('abort',()=>wait.reject(Object.assign(new Error('aborted'),{name:'AbortError'})));return wait.promise;},
    receivePlan:plan=>{state.revision=plan.revision;},
  });
  for(const name of ['api','cancelObsoleteReads','pullSharedPlan','marketComplete','renderLoadState','connectEvents','refreshPresence'])vm.runInContext(helper(name),context);
  return{context,state,requests,timers,notices,$};
}
function answer(request,value){request.resolve({ok:true,json:async()=>value});}
test('switch cancels obsolete reads and frees queued market slots but never aborts writes',async()=>{
  const h=harness(),read=h.context.api('/market?symbol=A'),write=h.context.api('/plans/A/patch','POST',{mutation_id:'1'});h.context.marketRequests.set('A',read);
  h.context.cancelObsoleteReads();await assert.rejects(read,error=>error.cancelled===true);assert.equal(h.requests[0].signal.aborted,true);assert.equal(h.requests[1].signal.aborted,false);assert.equal(h.context.marketRequests.size,0);assert.equal(h.state.connectionLost,undefined);
  answer(h.requests[1],{ok:true});await write;
});
test('market timeout is contextual and cannot label a working collaboration connection offline',async()=>{
  const h=harness(),request=h.context.api('/market?symbol=A');h.timers[0]();await assert.rejects(request,/行情响应较慢/);assert.equal(h.state.connectionLost,undefined);
  const plan=h.context.api('/plans/A');h.timers[1]();await assert.rejects(plan,/共享计划读取超时/);assert.equal(h.state.connectionLost,true);
});
test('later revision event received during a plan fetch is fetched immediately without a five-second poll',async()=>{
  const h=harness(),work=h.context.pullSharedPlan(2);await tick();await h.context.pullSharedPlan(3);assert.equal(h.requests.length,1);
  answer(h.requests[0],{plan:{revision:2}});await tick();assert.equal(h.requests.length,2);assert.equal(h.state.revision,2);
  answer(h.requests[1],{plan:{revision:3}});await work;assert.equal(h.state.revision,3);assert.equal(h.state.pulling,false);
});
test('old plan fetch cannot unset the new symbol fetch flag or apply its response',async()=>{
  const h=harness(),old=h.context.pullSharedPlan(2);h.state.generation=2;h.state.symbol='B';h.state.pulling=false;h.state.remoteRevision=0;const current=h.context.pullSharedPlan(4);
  answer(h.requests[0],{plan:{revision:2}});await old;assert.equal(h.state.pulling,true);assert.equal(h.state.revision,1);
  answer(h.requests[1],{plan:{revision:4}});await current;assert.equal(h.state.revision,4);assert.equal(h.state.pulling,false);
});
test('old event source callbacks and differently scoped packets never overwrite current presence or status',()=>{
  const h=harness(),sources=[];h.context.EventSource=class{constructor(){this.listeners={};sources.push(this);}addEventListener(name,fn){this.listeners[name]=fn;}close(){}emit(name,data){this.listeners[name]?.({data:JSON.stringify(data)});}};
  h.context.connectEvents();h.state.generation++;h.state.symbol='B';h.context.connectEvents();h.$('connection-status').textContent='current';
  for(const event of ['open','error','unavailable'])sources[0].emit(event);assert.equal(h.$('connection-status').textContent,'current');assert.deepEqual(h.notices,[]);
  sources[1].emit('workspace',{symbol:'A',board:'night',revision:8,presence:[{id:'wrong'}]});assert.equal(h.state.presence,undefined);
  sources[1].emit('workspace',{symbol:'B',board:'other',revision:8,presence:[{id:'wrong'}]});assert.equal(h.state.presence,undefined);
  sources[1].emit('workspace',{symbol:'B',board:'night',revision:1,presence:[{id:'right'}]});assert.equal(h.state.presence[0].id,'right');
});
test('late presence response is discarded after switching',async()=>{const h=harness(),work=h.context.refreshPresence();h.state.generation++;answer(h.requests[0],{presence:[{id:'old'}]});await work;assert.equal(h.state.presence,undefined);});
test('loading state distinguishes partial cache, first subscription, slow request and completed data',()=>{
  const h=harness();h.state.loadingSince=Date.now()-9000;h.context.renderLoadState();assert.equal(h.$('market-loading').hidden,false);assert.match(h.$('market-loading-title').textContent,/近期行情/);assert.equal(h.$('retry-market').hidden,false);
  h.state.planReady=true;h.state.planVerified=true;h.state.market={name:'鸡蛋',partial:true,periods:{day:[{}],m30:[{}],m3:[{}]}};h.context.renderLoadState();assert.match(h.$('market-loading-title').textContent,/下载完整历史/);
  h.state.marketLoadError='slow';h.context.renderLoadState();assert.match(h.$('market-loading-detail').textContent,/自动重试/);assert.equal(h.$('market-loading').dataset.error,'true');
  h.state.marketLoadError=null;h.state.market.partial=false;h.context.renderLoadState();assert.equal(h.$('market-loading').hidden,true);
});
