'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const P=require('./static/workspace-market-preview.js'),Cache=require('./static/workspace-market-cache.js');
const SYMBOL='SHFE.rb2701',PERIODS=['day','m30','m3'],copy=value=>JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function full(symbol=SYMBOL,price=101,counts={day:180,m30:6000,m3:10000}){
  return{symbol,name:'螺纹',price,tick:1,source:'live',fresh:true,feed_status:'live',history_token:'verified-full-token',incremental:false,
    as_of:'2026-11-11T21:20:00+08:00',quote_at:'2026-11-11T21:20:00+08:00',period_counts:{...counts},
    periods:Object.fromEntries(PERIODS.map(period=>[period,Array.from({length:counts[period]},(_,index)=>({
      dt:new Date(1794380400000+index*({day:86400000,m30:1800000,m3:180000}[period])).toISOString(),
      open:100,high:110,low:90,close:price,volume:0,oi:100+index,closed:index<counts[period]-1}))]))};
}
const small=(symbol=SYMBOL,price=101)=>full(symbol,price,{day:3,m30:4,m3:5});
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
async function writesDone(store){await Promise.all([...store.writes.values()]);await tick();}

// Model the asynchronous IndexedDB request/transaction boundary, then exercise
// Store's real disk adapter, including its cursor-based eviction policy.
function memoryIndexedDB(){
  const records=new Map(),schema={};let upgraded=false;
  const db={
    createObjectStore(name,options){schema.store=name;schema.keyPath=options.keyPath;return{createIndex(name,key){schema.index=name;schema.indexKey=key;}};},
    transaction(name,mode){
      assert.equal(name,'markets');const tx={mode};
      tx.objectStore=()=>({
        get(symbol){const request={};queueMicrotask(()=>{request.result=records.has(symbol)?copy(records.get(symbol)):undefined;request.onsuccess?.();});return request;},
        put(item){assert.equal(mode,'readwrite');records.set(item.symbol,copy(item));},
        delete(symbol){assert.equal(mode,'readwrite');records.delete(symbol);},
        index(name){assert.equal(name,'saved_at');return{openKeyCursor(){
          const request={},entries=[...records.values()].sort((a,b)=>a.saved_at-b.saved_at||a.symbol.localeCompare(b.symbol));let index=0;
          const advance=()=>queueMicrotask(()=>{
            const item=entries[index++];request.result=item?{primaryKey:item.symbol,key:item.saved_at,continue:advance}:null;
            request.onsuccess?.();if(!item)queueMicrotask(()=>tx.oncomplete?.());
          });advance();return request;
        }};},
      });return tx;
    },
  };
  return{records,schema,open(name,version){assert.equal(name,'resonance-market-preview-v1');assert.equal(version,1);const request={};queueMicrotask(()=>{request.result=db;if(!upgraded){upgraded=true;request.onupgradeneeded?.();}request.onsuccess?.();});return request;}};
}

test('prepare retains only recent candles and strips full-history and live-permission metadata',()=>{
  const source=full(),item=P.prepare(source,1000),market=item.market;
  assert.equal(item.symbol,SYMBOL);assert.equal(item.saved_at,1000);assert.deepEqual(PERIODS.map(p=>market.periods[p].length),[180,240,240]);
  assert.deepEqual(market.periods.m3,source.periods.m3.slice(-240));assert.equal(market.periods.m3.at(-1).closed,false);
  assert.equal(market.fresh,false);assert.equal(market.partial,true);assert.equal(market.client_cached,true);assert.equal(market.feed_status,'preview');
  assert.equal(market.history_token,null);assert.equal(market.incremental,false);assert.equal(market.preview_saved_at,1000);
  assert.deepEqual(market.period_counts,source.period_counts);assert.equal(market.quote_at,source.quote_at);assert.equal(source.fresh,true);assert.equal(source.periods.m3.length,10000);
  market.periods.m3[0].close=99;market.period_counts.m3=240;assert.equal(source.periods.m3.at(-240).close,101);assert.equal(source.period_counts.m3,10000);
});

test('prepare rejects missing periods and invalid chronological OHLC rather than inventing preview candles',()=>{
  for(const bad of [null,{},small(''),{...small(),periods:{}},{...small(),periods:{...small().periods,m3:[]}}])assert.equal(P.prepare(bad),null);
  for(const edit of [row=>row.dt='invalid',row=>row.close=NaN,row=>row.open=Infinity,row=>row.low=0,row=>row.high=99,row=>row.low=102,row=>row.close='101']){
    const source=small();edit(source.periods.m3.at(-1));assert.equal(P.prepare(source),null);
  }
  for(const back of [0,-1]){const source=small(),rows=source.periods.m3;rows.at(-1).dt=rows.at(-2+back).dt;assert.equal(P.prepare(source),null);}
});

test('cached previews cannot seed the verified full-history delta cache',()=>{
  const preview=P.prepare(small(),1000).market,cache=new Cache.Store(),result=cache.accept(SYMBOL,preview,cache.begin(SYMBOL));
  assert.equal(result.accepted,false);assert.equal(result.retryFull,true);assert.equal(cache.history(SYMBOL),null);assert.equal(cache.ready(SYMBOL),false);
  const source=small();assert.equal(cache.accept(SYMBOL,source,cache.begin(SYMBOL)).accepted,true);
  const before=cache.get(SYMBOL);cache.accept(SYMBOL,preview,cache.begin(SYMBOL));assert.deepEqual(cache.get(SYMBOL),before);assert.equal(cache.history(SYMBOL),null);
});

test('stored previews expire exactly at TTL and reject future, mismatched and corrupt records',async()=>{
  const saved=1000,item=P.prepare(small(),saved);
  for(const [now,expected] of [[saved,true],[saved+P.TTL-1,true],[saved+P.TTL,false],[saved-1,false]]){
    const store=new P.Store({now:()=>now,storage:{get:async()=>copy(item)}});assert.equal(Boolean(await store.get(SYMBOL)),expected);
  }
  for(const corrupt of [{...item,symbol:'other'},{...item,market:{...item.market,symbol:'other'}},{...item,saved_at:NaN},{...item,saved_at:'1000'},
    {...item,market:{...item.market,periods:{}}}]){
    const store=new P.Store({now:()=>1001,storage:{get:async()=>corrupt}});assert.equal(await store.get(SYMBOL),null);
  }
});

test('memory and disk reads return independent snapshots without refreshing their original age',async()=>{
  let now=1000;const diskItem=P.prepare(small(),now),store=new P.Store({now:()=>now,storage:{get:async()=>diskItem}});
  const first=await store.get(SYMBOL);first.price=999;first.periods.m3[0].close=99;now=5000;
  const second=await store.get(SYMBOL);assert.equal(second.price,101);assert.equal(second.periods.m3[0].close,101);assert.equal(second.preview_saved_at,1000);
  now=1000+P.TTL;assert.equal(await store.get(SYMBOL),null);
});

test('a delayed disk read cannot replace a newer market remembered while it was pending',async()=>{
  let now=1000;const old=P.prepare(small(SYMBOL,100),now),pending=deferred(),store=new P.Store({now:()=>now,storage:{get:()=>pending.promise,put:async()=>{}}});
  const reading=store.get(SYMBOL);now=2000;store.put(small(SYMBOL,105));pending.resolve(old);
  const result=await reading;assert.equal(result.price,105);assert.equal(result.preview_saved_at,2000);assert.equal((await store.get(SYMBOL)).price,105);await writesDone(store);
});

test('expired disk reads cannot delete valid memory that arrived concurrently',async()=>{
  let now=P.TTL+1000;const disk=deferred(),store=new P.Store({now:()=>now,storage:{get:()=>disk.promise,put:async()=>{}}});
  const reading=store.get(SYMBOL);store.put(small(SYMBOL,104));disk.resolve(P.prepare(small(SYMBOL,100),1));
  assert.equal((await reading).price,104);assert.equal(store.memory.get(SYMBOL).saved_at,now);await writesDone(store);
});

test('missing or broken optional disk storage degrades to memory without losing server previews',async()=>{
  const unavailable=new P.Store({indexedDB:null,now:()=>1000});assert.equal(await unavailable.get(SYMBOL),null);unavailable.put(small());assert.equal((await unavailable.get(SYMBOL)).price,101);
  const broken=new P.Store({now:()=>1000,storage:{get:async()=>{throw new Error('disk denied');},put:async()=>{throw new Error('quota exceeded');}}});
  assert.equal(await broken.get(SYMBOL),null);broken.put(small(SYMBOL,103));await writesDone(broken);assert.equal((await broken.get(SYMBOL)).price,103);assert.equal(broken.writes.size,0);
  const idb={open(){const request={};queueMicrotask(()=>{request.error=new Error('blocked');request.onblocked?.();});return request;}};
  const blocked=new P.Store({indexedDB:idb,now:()=>1000});assert.equal(await blocked.get(SYMBOL),null);blocked.put(small());await writesDone(blocked);assert.equal((await blocked.get(SYMBOL)).price,101);
});

test('offline, stale, unavailable and already partial snapshots never renew disk age',async()=>{
  let now=1000,writes=0;const store=new P.Store({now:()=>now,storage:{put:async()=>{writes++;}}});store.put(small());await writesDone(store);now+=60000;
  const base=small(SYMBOL,105),variants=[{partial:true},{client_cached:true},{unavailable:true},{fresh:false},{fresh:undefined},{feed_status:'loading'},{feed_status:'disconnected'},{feed_status:'stale'}];
  for(const fields of variants)store.put({...base,...fields});store.put(P.prepare(base,now).market);await writesDone(store);
  assert.equal(writes,1);assert.equal((await store.get(SYMBOL)).price,101);assert.equal(store.memory.get(SYMBOL).saved_at,1000);
});

test('mock snapshots remain previews and are never upgraded to live freshness',async()=>{
  const store=new P.Store({now:()=>1000,indexedDB:null});store.put({...small(),source:'mock',fresh:false});
  const read=await store.get(SYMBOL);assert.equal(read.source,'mock');assert.equal(read.fresh,false);assert.equal(read.partial,true);assert.equal(read.history_token,null);
});

test('per-symbol save throttling preserves the last saved picture until the exact 30-second boundary',async()=>{
  let now=1000;const saved=[],store=new P.Store({now:()=>now,storage:{put:async item=>saved.push(copy(item))}});
  store.put(small(SYMBOL,100));now=30999;store.put(small(SYMBOL,104));store.put(small('SHFE.au2612',103));await writesDone(store);
  assert.equal(saved.length,2);assert.equal((await store.get(SYMBOL)).price,100);
  now=31000;store.put(small(SYMBOL,105));await writesDone(store);assert.equal(saved.length,3);assert.equal((await store.get(SYMBOL)).price,105);assert.equal(store.memory.get(SYMBOL).saved_at,31000);
});

test('writes for the same contract serialize and recover after an earlier disk write rejects',async()=>{
  let now=1000;const first=deferred(),second=deferred(),seen=[];
  const store=new P.Store({now:()=>now,storage:{put:item=>{seen.push(item.market.price);return seen.length===1?first.promise:second.promise;}}});
  store.put(small(SYMBOL,100));await tick();now=31000;store.put(small(SYMBOL,104));await tick();assert.deepEqual(seen,[100]);assert.equal((await store.get(SYMBOL)).price,104);
  first.reject(new Error('quota retry'));await tick();assert.deepEqual(seen,[100,104]);second.resolve();await writesDone(store);assert.equal(store.writes.size,0);
});

test('in-memory preview retention is bounded to sixteen contracts',async()=>{
  let now=1000;const store=new P.Store({now:()=>now,indexedDB:null});
  for(let i=0;i<20;i++){store.put(small('TEST.s'+i));now++;}
  assert.equal(store.memory.size,P.MAX_ITEMS);assert.equal(await store.get('TEST.s0'),null);assert.equal((await store.get('TEST.s19')).symbol,'TEST.s19');
});

test('real IndexedDB adapter creates its schema, evicts oldest records beyond sixteen and expires disk records',async()=>{
  const idb=memoryIndexedDB();let now=1000;const store=new P.Store({indexedDB:idb,now:()=>now});
  for(let i=0;i<20;i++){store.put(small('TEST.s'+i));await writesDone(store);now++;}
  assert.deepEqual(idb.schema,{store:'markets',keyPath:'symbol',index:'saved_at',indexKey:'saved_at'});assert.equal(idb.records.size,P.MAX_ITEMS);assert.equal(idb.records.has('TEST.s0'),false);assert.equal(idb.records.has('TEST.s19'),true);
  const reloaded=new P.Store({indexedDB:idb,now:()=>now});const read=await reloaded.get('TEST.s19');assert.equal(read.symbol,'TEST.s19');assert.equal(read.fresh,false);assert.equal(read.partial,true);
  now+=P.TTL+1;store.put(small('TEST.new'));await writesDone(store);assert.deepEqual([...idb.records.keys()],['TEST.new']);
});
