'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {webcrypto,createHash}=require('node:crypto');
const H=require('./static/workspace-history-cache.js'),M=require('./static/workspace-market-cache.js');
const NOW=Date.parse('2026-11-15T00:00:00+08:00'),PERIODS=['day','m30','m3'],clone=value=>JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const sha=text=>createHash('sha256').update(text).digest('hex');
function fixture(symbol='CZCE.MA701'){
  const values=new Map(),market={symbol,source:'live',fresh:true,connected:true,feed_status:'live',as_of:new Date(NOW).toISOString(),history_token:'current-token',incremental:false,period_counts:{day:4,m30:4,m3:4},candle_basis:{m30:'trading_minutes_v1'},anchor_reference_id:'fixed-reference',periods:{}};
  const history={},history_counts={};
  for(const period of PERIODS){
    const bars=Array.from({length:4},(_,index)=>({dt:new Date(NOW-86400000+index*180000).toISOString(),open:100,high:110,low:90,close:101,volume:10,oi:20,closed:index<3}));
    if(period==='m30')market.anchor_reference={m30:{basis:'native_v1',target_basis:'trading_minutes_v1',source_times:bars.map(row=>Date.parse(row.dt)),target_base_time:Date.parse(bars.at(-1).dt)}};
    market.periods[period]=bars.slice(-2);
    const block={schema:1,symbol,period,candle_basis:market.candle_basis[period]||'native_v1',anchor_reference_id:market.anchor_reference_id,bars:bars.slice(0,2)},text=JSON.stringify(block),hash=sha(text);
    const desc={sha256:hash,url:'/api/workspace/market/history/'+hash+'.json',count:2,first:bars[0].dt,last:bars[1].dt};history[period]=[desc];history_counts[period]=2;values.set(desc.url,text);
  }
  return{bundle:{schema:1,snapshot_id:sha('snapshot'),market,history,history_counts},values};
}
function memory(){const items=new Map();return{items,get:async key=>items.has(key)?clone(items.get(key)):undefined,put:async value=>items.set(value.sha256,clone(value)),prune:async()=>{}};}
function blocks(storage){return new H.Blocks({storage,crypto:webcrypto,indexedDB:null,now:()=>NOW});}
async function settled(store){await Promise.all([...store.writes]);await tick();}

test('cold bootstrap assembles a coherent full snapshot and persists verified historical chunks',async()=>{
  const f=fixture(),storage=memory(),store=blocks(storage),calls=[];
  const full=await store.assemble(f.bundle,async path=>{calls.push(path);return f.values.get(path);});
  const cache=new M.Store();assert.equal(cache.accept(full.symbol,full,cache.begin(full.symbol)).accepted,true);
  assert.deepEqual(PERIODS.map(p=>full.periods[p].length),[4,4,4]);assert.equal(full.history_token,'current-token');assert.equal(full.anchor_reference_id,'fixed-reference');
  assert.equal(full.fresh,true);assert.equal(calls.length,3);await settled(store);assert.equal(storage.items.size,3);
  for(const saved of storage.items.values()){assert.equal(saved.sha256,sha(saved.text));assert.equal('market' in saved,false);assert.equal('history_token' in JSON.parse(saved.text),false);}
});

test('page reload reuses disk chunks but takes current tails, freshness and token from a new bootstrap',async()=>{
  const f=fixture(),storage=memory(),first=blocks(storage);await first.assemble(f.bundle,async path=>f.values.get(path));await settled(first);
  const next=clone(f.bundle);next.market.history_token='new-process-token';next.market.fresh=false;next.market.feed_status='idle';next.market.periods.m3.at(-1).close=104;
  let progress=0;
  const reloaded=blocks(storage),full=await reloaded.assemble(next,async()=>assert.fail('Unchanged historical chunks must not be downloaded again'),()=>progress++);
  assert.equal(reloaded.stats.diskHits,3);assert.equal(full.history_token,'new-process-token');assert.equal(full.periods.m3.at(-1).close,104);assert.equal(full.fresh,false);assert.equal(full.feed_status,'idle');
  assert.equal(progress,3);
  await reloaded.assemble(next,async()=>assert.fail('Memory hit expected'),()=>progress++);assert.equal(reloaded.stats.memoryHits,3);assert.equal(progress,6);
});

test('corrupted disk data is replaced and public OSS failures retry the exact same hash at the origin',async()=>{
  const f=fixture(),storage=memory(),desc=f.bundle.history.m3[0];
  storage.items.set(desc.sha256,{sha256:desc.sha256,text:'corrupt',last_time:NOW,saved_at:NOW,bytes:7});
  for(const period of PERIODS){const d=f.bundle.history[period][0];d.oss_url='https://silver-resonance.oss-cn-hangzhou.aliyuncs.com/history/v1/blocks/'+d.sha256+'.json';}
  const store=blocks(storage),calls=[];
  const full=await store.assemble(f.bundle,async(path,options)=>{calls.push({path,...options});if(options.public)throw new Error('OSS offline');return f.values.get(path);});
  assert.equal(full.periods.m3.length,4);assert.equal(store.stats.fallbacks,3);assert.equal(calls.length,6);
  assert.ok(calls.filter(c=>c.public).every(c=>c.timeout===2500));await settled(store);assert.equal(sha(storage.items.get(desc.sha256).text),desc.sha256);
});

test('hash, contract, period, coordinates, boundaries and counts are checked before cache activation',async()=>{
  for(const change of [
    f=>{f.values.set(f.bundle.history.day[0].url,'bad bytes');},
    f=>{f.bundle.market.symbol='SHFE.au2612';},
    f=>{f.bundle.market.candle_basis.m30='wrong_basis';},
    f=>{f.bundle.market.anchor_reference_id='different_reference';},
    f=>{f.bundle.history.m3[0].first=f.bundle.history.m3[0].last;},
    f=>{f.bundle.history_counts.day++;},
    f=>{f.bundle.market.periods.day[0].dt=f.bundle.history.day[0].last;},
    f=>{f.bundle.history.day[0].url='https://unknown.example/steal';},
    f=>{f.bundle.history.day[0].oss_url='https://unknown.example/steal';},
  ]){const f=fixture();change(f);await assert.rejects(blocks(memory()).assemble(f.bundle,async path=>f.values.get(path)));}
});

test('a cached chunk cannot be reused for another period or coordinate generation',async()=>{
  const f=fixture(),store=blocks(memory()),desc=f.bundle.history.day[0];await store.block(desc,f.bundle.market,'day',async path=>f.values.get(path));
  await assert.rejects(store.block(desc,f.bundle.market,'m3',async()=>assert.fail()),/match snapshot/);
  await assert.rejects(store.block(desc,{...f.bundle.market,anchor_reference_id:'changed'},'day',async()=>assert.fail()),/match snapshot/);
});

test('broken optional local storage leaves network data usable and the memory budget stays bounded',async()=>{
  const f=fixture(),store=new H.Blocks({crypto:webcrypto,indexedDB:null,now:()=>NOW,memoryBytes:1,storage:{get:async()=>{throw new Error('blocked');},put:async()=>{throw new Error('quota');}}});
  const full=await store.assemble(f.bundle,async path=>f.values.get(path));assert.equal(full.periods.m30.length,4);await settled(store);assert.equal(store.memory.size,0);assert.equal(store.bytes,0);
});

test('shared hub coalesces concurrent pane requests, then only requests the current incremental tail',async()=>{
  const f=fixture(),calls=[];let release;const gate=new Promise(resolve=>{release=resolve;});
  const hub=new H.Hub({now:()=>NOW,blocks:blocks(memory()),fetch:async(path,options)=>{
    calls.push({path,options});assert.equal(options.headers['X-Workspace-Candle-Protocol'],'2');assert.equal(options.credentials,'same-origin');
    if(path.includes('/bootstrap')){await gate;return{ok:true,json:async()=>({bootstrap:f.bundle})};}
    if(f.values.has(path))return{ok:true,text:async()=>f.values.get(path)};
    assert.match(path,/history=current-token/);assert.match(path,/history_fallback=bootstrap/);return{ok:true,json:async()=>({market:{...f.bundle.market,incremental:true,base_history_token:'current-token',period_drops:{day:0,m30:0,m3:0}}})};
  }});
  const first=hub.refresh(f.bundle.market.symbol),second=hub.refresh(f.bundle.market.symbol);assert.strictEqual(first,second);release();
  const [a,b]=await Promise.all([first,second]);assert.strictEqual(a,b);assert.equal(calls.length,4);assert.equal(hub.requests.size,0);
  await hub.refresh(a.symbol);assert.equal(calls.length,5);assert.equal(hub.stats.bootstrap,1);assert.equal(hub.stats.incremental,1);
});

test('a server restart requests a small bootstrap handshake and reuses persisted blocks instead of a full download',async()=>{
  const f=fixture(),storage=memory(),first=blocks(storage);await first.assemble(f.bundle,async path=>f.values.get(path));await settled(first);
  const hub=new H.Hub({now:()=>NOW,blocks:blocks(storage)}),baseline=await first.assemble(f.bundle,async()=>assert.fail());hub.cache.accept(baseline.symbol,baseline,hub.cache.begin(baseline.symbol));
  const changed=clone(f.bundle);changed.market.history_token='after-server-restart';changed.market.fresh=false;changed.market.feed_status='idle';changed.market.periods.m3.at(-1).close=106;
  const calls=[];hub.fetch=async path=>{calls.push(path);if(path.includes('history=current-token')){assert.match(path,/history_fallback=bootstrap/);return{ok:true,json:async()=>({status:'bootstrap_required'})};}assert.match(path,/\/market\/bootstrap\?/);return{ok:true,json:async()=>({status:'ok',bootstrap:changed})};};
  const result=await hub.refresh(baseline.symbol);assert.equal(calls.length,2);assert.equal(hub.stats.full,0);assert.equal(hub.blocks.stats.diskHits,3);assert.equal(result.history_token,'after-server-restart');assert.equal(result.periods.m3.at(-1).close,106);assert.equal(result.fresh,false);
});

test('historical correction replaces only changed chunks after the lightweight invalid-token handshake',async()=>{
  const f=fixture(),store=blocks(memory()),hub=new H.Hub({now:()=>NOW,blocks:store});
  const baseline=await store.assemble(f.bundle,async path=>f.values.get(path));hub.cache.accept(baseline.symbol,baseline,hub.cache.begin(baseline.symbol));
  const changed=clone(f.bundle),previous=changed.history.m3[0],data=JSON.parse(f.values.get(previous.url));data.bars[0].close=103;
  const text=JSON.stringify(data),hash=sha(text);changed.history.m3[0]={...previous,sha256:hash,url:'/api/workspace/market/history/'+hash+'.json'};changed.market.history_token='corrected-history';
  const calls=[];hub.fetch=async path=>{calls.push(path);if(path.includes('history='))return{ok:true,json:async()=>({status:'bootstrap_required'})};if(path.includes('/bootstrap'))return{ok:true,json:async()=>({bootstrap:changed})};assert.equal(path,changed.history.m3[0].url);return{ok:true,text:async()=>text};};
  const result=await hub.refresh(baseline.symbol);assert.equal(calls.length,3);assert.equal(hub.stats.full,0);assert.equal(result.periods.m3[0].close,103);assert.equal(result.periods.day[0].close,101);assert.equal(result.history_token,'corrected-history');
});

test('shared quotes use one request and a brief overlap cache without sharing plans or permissions',async()=>{
  let now=NOW,calls=0,release;const gate=new Promise(resolve=>{release=resolve;});
  const hub=new H.Hub({now:()=>now,blocks:blocks(memory()),fetch:async path=>{assert.equal(path,'/api/workspace/quotes');calls++;await gate;return{ok:true,json:async()=>({quotes:[{symbol:'A',price:1}]})};}});
  const a=hub.quotes(),b=hub.quotes();assert.strictEqual(a,b);release();await a;await hub.quotes();assert.equal(calls,1);now+=1001;await hub.quotes();assert.equal(calls,2);
});

test('bootstrap errors fall back to the existing full response and never clear a valid rendered baseline',async()=>{
  const f=fixture(),full=await blocks(memory()).assemble(f.bundle,async path=>f.values.get(path)),calls=[];
  const hub=new H.Hub({now:()=>NOW,blocks:blocks(memory()),fetch:async path=>{calls.push(path);return path.includes('/bootstrap')?{ok:false,status:503}:{ok:true,json:async()=>({market:full})};}});
  const result=await hub.refresh(full.symbol);assert.equal(result.history_token,full.history_token);assert.equal(calls.length,2);assert.equal(hub.stats.full,1);
  hub.fetch=async()=>({ok:false,status:503});await assert.rejects(hub.refresh(full.symbol));assert.equal(hub.cache.view(full.symbol),result);
});

test('a failed or timed-out bootstrap aborts all its pending block downloads before falling back',async()=>{
  const f=fixture(),full=await blocks(memory()).assemble(f.bundle,async path=>f.values.get(path));
  for(const mode of ['corrupt','timeout']){
    let pending=0,aborted=0,fallbackPending=null;
    const hub=new H.Hub({now:()=>NOW,bootstrapTimeout:30,blocks:blocks(memory()),fetch:async(path,{signal})=>{
      if(path.includes('/bootstrap'))return{ok:true,json:async()=>({bootstrap:f.bundle})};
      if(path.includes('/history/')){
        if(mode==='corrupt'&&path===f.bundle.history.day[0].url)return{ok:true,text:async()=>'broken'};
        pending++;return new Promise((_,reject)=>signal.addEventListener('abort',()=>{aborted++;pending--;reject(new Error('cancelled'));},{once:true}));
      }
      fallbackPending=pending;return{ok:true,json:async()=>({market:full})};
    }});
    const result=await hub.refresh(full.symbol);assert.equal(result.symbol,full.symbol);assert.equal(fallbackPending,0);assert.ok(aborted>=2);assert.equal(hub.requests.size,0);
  }
});

test('validated block progress keeps a slow restore instead of discarding finished blocks at the initial deadline',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fixture(),waiting=new Map(),signals=[];let metadata;
  const store=new H.Blocks({storage:memory(),indexedDB:null,now:()=>NOW,crypto:{subtle:{digest:async(_,bytes)=>new Uint8Array(createHash('sha256').update(bytes).digest()).buffer}}});
  const hub=new H.Hub({now:()=>NOW,bootstrapTimeout:30,bootstrapMaxTimeout:120,blocks:store,fetch:async(path,{signal})=>{
    signals.push(signal);
    if(path.includes('/bootstrap'))return new Promise(resolve=>{metadata=()=>resolve({ok:true,json:async()=>({bootstrap:f.bundle})});});
    assert.ok(f.values.has(path),'A progressing restore must not request the full market');
    return new Promise(resolve=>waiting.set(path,()=>resolve({ok:true,text:async()=>f.values.get(path)})));
  }});
  const result=hub.refresh(f.bundle.market.symbol);
  t.mock.timers.tick(20);metadata();await tick();assert.equal(waiting.size,3);
  for(const period of PERIODS){t.mock.timers.tick(20);waiting.get(f.bundle.history[period][0].url)();await tick();}
  const market=await result;
  assert.equal(market.periods.m3.length,4);assert.equal(hub.stats.full,0);assert.equal(store.stats.downloads,3);assert.equal(hub.requests.size,0);
  // Total 80 > idle limit 30, but each actual completion arrives within 20.
  t.mock.timers.tick(1000);await tick();assert.ok(signals.every(signal=>!signal.aborted));
});

test('continued progress cannot extend restoration beyond the hard cap and the last request is aborted before fallback',async t=>{
  const f=fixture(),full=await blocks(memory()).assemble(f.bundle,async path=>f.values.get(path));
  t.mock.timers.enable({apis:['setTimeout']});
  const waiting=new Map();let metadata,pending=0,fallbackPending=null,aborted=0;
  const store=new H.Blocks({storage:memory(),indexedDB:null,now:()=>NOW,crypto:{subtle:{digest:async(_,bytes)=>new Uint8Array(createHash('sha256').update(bytes).digest()).buffer}}});
  const hub=new H.Hub({now:()=>NOW,bootstrapTimeout:30,bootstrapMaxTimeout:70,blocks:store,fetch:async(path,{signal})=>{
    if(path.includes('/bootstrap'))return new Promise(resolve=>{metadata=()=>resolve({ok:true,json:async()=>({bootstrap:f.bundle})});});
    if(f.values.has(path)){
      pending++;
      return new Promise((resolve,reject)=>{
        const abort=()=>{pending--;aborted++;reject(new Error('cancelled'));};signal.addEventListener('abort',abort,{once:true});
        waiting.set(path,()=>{signal.removeEventListener('abort',abort);pending--;resolve({ok:true,text:async()=>f.values.get(path)});});
      });
    }
    fallbackPending=pending;return{ok:true,json:async()=>({market:full})};
  }});
  const result=hub.refresh(f.bundle.market.symbol);
  t.mock.timers.tick(20);metadata();await tick();
  for(const period of ['day','m30']){t.mock.timers.tick(20);waiting.get(f.bundle.history[period][0].url)();await tick();}
  assert.equal(pending,1);assert.equal(hub.stats.full,0);
  t.mock.timers.tick(10);await tick();
  assert.equal((await result).periods.m3.length,4);assert.equal(hub.stats.full,1);assert.equal(fallbackPending,0);assert.equal(aborted,1);assert.equal(hub.requests.size,0);
  // The hard cap fires only 10 after the last progress, before idle limit 30.
  t.mock.timers.tick(1000);await tick();assert.equal(hub.stats.full,1);
});

test('new candles and rolling drops preserve fixed references while old coordinate generations force a full recovery',async()=>{
  const f=fixture(),hub=new H.Hub({now:()=>NOW,blocks:blocks(memory()),fetch:async path=>path.includes('/bootstrap')?{ok:true,json:async()=>({bootstrap:f.bundle})}:{ok:true,text:async()=>f.values.get(path)}});
  const initial=await hub.refresh(f.bundle.market.symbol),delta={...f.bundle.market,incremental:true,base_history_token:'current-token',history_token:'appended',period_drops:{day:1,m30:1,m3:1}};
  delta.periods=Object.fromEntries(PERIODS.map(period=>{const tail=f.bundle.market.periods[period];return[period,[...clone(tail),{...tail.at(-1),dt:new Date(Date.parse(tail.at(-1).dt)+180000).toISOString(),close:105}]];}));
  delete delta.anchor_reference;hub.fetch=async()=>({ok:true,json:async()=>({market:delta})});
  const next=await hub.refresh(initial.symbol);assert.equal(next.periods.m3.length,4);assert.equal(next.periods.m3.at(-1).close,105);assert.strictEqual(next.anchor_reference,initial.anchor_reference);
  let recovered=0;hub.fetch=async()=>({ok:true,json:async()=>({market:{...delta,candle_basis:{m30:'native_v1'}}})});hub.full=async()=>{recovered++;return initial;};
  const fixed=await hub.refresh(initial.symbol);assert.equal(recovered,1);assert.equal(fixed.candle_basis.m30,'trading_minutes_v1');assert.equal(fixed.anchor_reference_id,'fixed-reference');
});

test('recovered history does not renew stale market metadata even when all chunks come from disk',async()=>{
  const f=fixture();f.bundle.market.as_of=new Date(NOW-60000).toISOString();
  const hub=new H.Hub({now:()=>NOW,blocks:blocks(memory()),fetch:async path=>path.includes('/bootstrap')?{ok:true,json:async()=>({bootstrap:f.bundle})}:{ok:true,text:async()=>f.values.get(path)}});
  const result=await hub.refresh(f.bundle.market.symbol);assert.equal(result.fresh,false);assert.equal(result.feed_status,'stale');assert.equal(result.as_of,f.bundle.market.as_of);
});

test('six calendar months clamp leap and short months using the exchange day',()=>{
  assert.equal(H.sixMonthsAgo(Date.parse('2026-08-31T09:00:00+08:00')),Date.parse('2026-02-28T09:00:00+08:00'));
  assert.equal(H.sixMonthsAgo(Date.parse('2024-08-31T09:00:00+08:00')),Date.parse('2024-02-29T09:00:00+08:00'));
});
