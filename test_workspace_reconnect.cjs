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
  const calls=[],nodes=new Map(),savedDrafts=[],pending=new Promise(()=>{}),noop=()=>{};
  const preview={symbol,partial:true,client_cached:true,fresh:false,periods:{day:[],m30:[],m3:[]}};
  const context=vm.createContext({
    state,WorkspaceMarketCache:MarketCache,marketCache:new MarketCache.Store(),marketRequests:new Map(),documentCache:new Map(),
    marketPreview:{get:async()=>preview},presenceSender:{reset:noop},URLSearchParams,Set,Map,Promise,
    location:{search:'?symbol='+symbol},
    $:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);},
    persistDraft:()=>savedDrafts.push(structuredClone(state.plan)),emptyPending:M.empty,
    setPlanLoading:value=>{state.loading=value;},renderMembers:noop,alertKey:()=>'',storageGet:(key,fallback)=>fallback,setLayout:noop,
    paintOpeningPreview:(market,generation)=>{assert.equal(generation,state.generation);state.market=market;state.loading=true;},
    api:url=>{calls.push(url);return pending;},
    refreshMarket:()=>{calls.push('UNEXPECTED_MARKET_RETRY');return pending;},
    notice:noop,setUnavailable:noop,
  });
  for(const name of ['preloadSavedMarket','enter','poll'])vm.runInContext(helper(name),context);
  void context.enter({user:{id:'restored-user'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(savedDrafts,[oldPlan],'The old draft must be retained before clearing live state.');
  assert.deepEqual(calls,['/symbols','/rules','/risk']);assert.equal(state.symbol,symbol);assert.equal(state.market.partial,true);
  assert.equal(state.loading,true);assert.equal(state.planReady,false);assert.equal(state.plan,null);assert.equal(state.evaluation,null);assert.equal(state.lastSignal,null);
  await context.poll();
  assert.deepEqual(calls,['/symbols','/rules','/risk'],'A timer must not request the previous board/revision or full market before bootstrap completes.');
});
