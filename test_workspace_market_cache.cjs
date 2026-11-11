const {test}=require('node:test'),assert=require('node:assert/strict');
const {Store,PERIODS}=require('./static/workspace-market-cache.js');
const copy=value=>JSON.parse(JSON.stringify(value));
const SYMBOL='SHFE.rb2701',OTHER='CZCE.RM701';
function bars(count,step=180000){return Array.from({length:count},(_,index)=>({dt:new Date(1700000000000+index*step).toISOString(),open:100,high:105,low:95,close:101,volume:0,oi:0,closed:index<count-1}));}
function full(symbol=SYMBOL,token='history-1',counts={day:4,m30:7,m3:10}){
  return{symbol,history_token:token,incremental:false,period_counts:{...counts},periods:Object.fromEntries(PERIODS.map(period=>[period,bars(counts[period])])),fresh:true,connected:true,feed_status:'live',price:101,freshness:{day:{available:true},m30:{available:true},m3:{available:true}},as_of:'2026-11-11T20:00:00+08:00'};
}
function delta(base){return{...copy(base),incremental:true,periods:Object.fromEntries(PERIODS.map(period=>[period,copy(base.periods[period].slice(-2))]))};}
function accept(store,value){return store.accept(value.symbol,value,store.begin(value.symbol));}

test('a verified full response seeds all three periods and a token; access returns independent snapshots',()=>{
  const store=new Store(),source=full(),result=accept(store,source);
  assert.equal(result.accepted,true);assert.equal(result.ready,true);assert.equal(store.ready(SYMBOL),true);assert.equal(store.history(SYMBOL),'history-1');
  source.periods.m3[0].close=98;result.market.periods.day[0].close=99;const read=store.get(SYMBOL);read.freshness.day.available=false;
  assert.equal(store.get(SYMBOL).periods.m3[0].close,101);assert.equal(store.get(SYMBOL).periods.day[0].close,101);assert.equal(store.get(SYMBOL).freshness.day.available,true);
});

test('tail updates replace the last two rows atomically and retain the whole 6000/10000 history',()=>{
  const store=new Store(),base=full(SYMBOL,'large',{day:180,m30:6000,m3:10000});accept(store,base);const patch=delta(base);
  patch.periods.m3[0].volume=0;patch.periods.m3[1].close=104;patch.periods.m30[0].closed_at='2026-11-11T20:30:00+08:00';patch.periods.day[1].source_trade_day='2026-11-12';
  const result=accept(store,patch);assert.equal(result.accepted,true);assert.equal(result.market.periods.m30.length,6000);assert.equal(result.market.periods.m3.length,10000);
  assert.deepEqual(result.market.periods.m3.slice(0,-2),base.periods.m3.slice(0,-2));assert.equal(result.market.periods.m3.at(-1).close,104);assert.equal(result.market.periods.m3.at(-2).volume,0);
  patch.periods.m3[1].close=99;assert.equal(store.get(SYMBOL).periods.m3.at(-1).close,104);
});

test('new metadata replaces old flags honestly, including false, zero, null and unavailable',()=>{
  const store=new Store(),base=full();accept(store,base);const patch=delta(base);
  Object.assign(patch,{fresh:false,connected:false,price:0,quote_at:null,unavailable:true,feed_status:'disconnected',freshness:{day:{available:false,checked_recent:false},m30:{available:false},m3:{available:false}}});
  const result=accept(store,patch);assert.equal(result.market.fresh,false);assert.equal(result.market.connected,false);assert.equal(result.market.price,0);assert.equal(result.market.quote_at,null);assert.equal(result.market.unavailable,true);assert.equal(result.market.freshness.day.checked_recent,false);
  delete patch.unavailable;accept(store,patch);assert.equal(Object.hasOwn(store.get(SYMBOL),'unavailable'),false);
});

test('loading full responses can be shown but do not qualify for delta requests',()=>{
  const store=new Store(),loading=full(SYMBOL,'loading',{day:2,m30:0,m3:0});loading.fresh=false;loading.feed_status='loading';
  assert.equal(accept(store,loading).accepted,true);assert.equal(store.history(SYMBOL),null);assert.equal(store.ready(SYMBOL),false);assert.equal(store.get(SYMBOL).feed_status,'loading');
  const result=accept(store,delta(loading));assert.equal(result.retryFull,true);assert.equal(result.reason,'missing_full_history');assert.deepEqual(store.get(SYMBOL),loading);
});

test('a delta without a verified base requests full history without storing partial arrays',()=>{
  const store=new Store(),result=accept(store,delta(full()));assert.equal(result.retryFull,true);assert.equal(store.get(SYMBOL),null);assert.equal(store.history(SYMBOL),null);
});

test('mismatched tokens, counts and tail timestamps preserve the prior picture and require a fresh full read',()=>{
  const cases=[d=>d.history_token='other-process',d=>d.period_counts.m3++,d=>d.periods.day[1].dt='2030-01-01T00:00:00Z',d=>d.periods.m30.pop(),d=>d.periods.m3[0].close=NaN,d=>d.periods.m30[1].low=200];
  for(const change of cases){const store=new Store(),base=full();accept(store,base);const patch=delta(base);change(patch);const result=accept(store,patch);assert.equal(result.retryFull,true);assert.deepEqual(store.get(SYMBOL),base);assert.equal(store.history(SYMBOL),null);assert.equal(accept(store,delta(base)).retryFull,true);assert.equal(accept(store,base).accepted,true);assert.equal(store.history(SYMBOL),base.history_token);}
});

test('a failure in the last period cannot partially apply earlier period updates',()=>{
  const store=new Store(),base=full();accept(store,base);const patch=delta(base);patch.periods.day[1].close=104;patch.periods.m30[1].close=103;patch.periods.m3[0].dt='2000-01-01T00:00:00Z';
  assert.equal(accept(store,patch).retryFull,true);assert.deepEqual(store.get(SYMBOL),base);
});

test('rollover, old-history corrections and process restarts replace a full history even with equal row counts',()=>{
  const store=new Store(),base=full();accept(store,base);
  const rolled=full(SYMBOL,'rolled');for(const period of PERIODS)rolled.periods[period]=rolled.periods[period].map(row=>({...row,dt:new Date(Date.parse(row.dt)+180000).toISOString()}));
  assert.equal(accept(store,rolled).accepted,true);assert.equal(store.get(SYMBOL).periods.m3[0].dt,rolled.periods.m3[0].dt);
  const corrected=copy(rolled);corrected.history_token='corrected';corrected.periods.m30[0].close=104;accept(store,corrected);assert.equal(store.get(SYMBOL).periods.m30[0].close,104);
  const restarted=copy(corrected);restarted.history_token='new-process';accept(store,restarted);assert.equal(store.history(SYMBOL),'new-process');
});

test('out-of-order responses and duplicate tickets cannot roll back newer full history',()=>{
  const store=new Store(),oldTicket=store.begin(SYMBOL),latestTicket=store.begin(SYMBOL),latest=full(SYMBOL,'new-process');
  assert.equal(store.accept(SYMBOL,latest,latestTicket).accepted,true);assert.equal(store.accept(SYMBOL,full(),oldTicket).ignored,true);assert.equal(store.accept(SYMBOL,full(),latestTicket).ignored,true);assert.equal(store.history(SYMBOL),'new-process');
  const waitingTicket=store.begin(SYMBOL);assert.equal(store.accept(SYMBOL,delta(latest),latestTicket).ignored,true);assert.equal(store.accept(SYMBOL,latest,waitingTicket).accepted,true);
});

test('crossed contracts cannot poison snapshots and separate contracts retain independent histories',()=>{
  const store=new Store(),base=full(),other=full(OTHER,'other');accept(store,base);accept(store,other);
  const result=store.accept(SYMBOL,other,store.begin(SYMBOL));assert.equal(result.retryFull,true);assert.equal(result.reason,'symbol_mismatch');assert.deepEqual(store.get(SYMBOL),base);assert.deepEqual(store.get(OTHER),other);assert.equal(store.history(OTHER),'other');
});

test('full validation rejects missing periods, inconsistent counts, duplicate dates and oversized arrays',()=>{
  const cases=[d=>delete d.periods.day,d=>delete d.period_counts.m3,d=>d.period_counts.day=true,d=>d.period_counts.m30--,d=>d.history_token='',d=>d.incremental='false',d=>d.periods.m3[1].dt=d.periods.m3[0].dt,d=>{d.period_counts.day=181;d.periods.day=bars(181);}];
  for(const change of cases){const store=new Store(),bad=full();change(bad);assert.equal(accept(store,bad).retryFull,true);assert.equal(store.get(SYMBOL),null);}
});

test('a one-bar history accepts a single-row tail without fabricating extra bars',()=>{
  const store=new Store(),base=full(SYMBOL,'one',{day:1,m30:1,m3:1});accept(store,base);const patch=delta(base);patch.periods.day[0].close=104;const result=accept(store,patch);assert.equal(result.accepted,true);assert.equal(result.market.periods.day.length,1);assert.equal(result.market.periods.day[0].close,104);
});

test('the bounded LRU never exceeds sixteen contracts and evicted request tickets cannot reinsert old data',()=>{
  const store=new Store(),ticket=store.begin('SHFE.ag2702');
  for(let i=0;i<16;i++)accept(store,full('TEST.p'+i,'t'+i));
  assert.equal(store.entries.size,16);assert.equal(store.accept('SHFE.ag2702',full('SHFE.ag2702'),ticket).ignored,true);assert.equal(store.get('SHFE.ag2702'),null);
  store.get('TEST.p0');accept(store,full('TEST.new','new'));assert.notEqual(store.get('TEST.p0'),null);assert.equal(store.get('TEST.p1'),null);assert.equal(store.entries.size,16);
  assert.throws(()=>new Store({limit:17}),RangeError);assert.throws(()=>new Store({limit:0}),RangeError);
});

test('the same contract is shared across boards without any plan state in the cache API',()=>{
  const store=new Store(),market=full();accept(store,market);const boardA=store.get(SYMBOL),boardB=store.get(SYMBOL);assert.deepEqual(boardA,boardB);assert.notEqual(boardA,boardB);assert.equal(Object.hasOwn(boardA,'plan'),false);
  store.clear(SYMBOL);assert.equal(store.get(SYMBOL),null);assert.equal(store.history(SYMBOL),null);assert.equal(store.ready(SYMBOL),false);
});

test('pinning the active contract keeps it and its token while background activity evicts older unpinned entries',()=>{
  const store=new Store();accept(store,full(SYMBOL,'active'));store.pin(SYMBOL);
  for(let index=0;index<24;index++)accept(store,full('TEST.bg'+index,'background-'+index));
  assert.equal(store.entries.size,16);assert.equal(store.ready(SYMBOL),true);assert.equal(store.history(SYMBOL),'active');
  assert.equal(store.get('TEST.bg0'),null);assert.notEqual(store.get('TEST.bg23'),null);
});

test('pinning a new active contract releases the previous pin without allowing the cache to grow',()=>{
  const store=new Store({limit:2});accept(store,full(SYMBOL));store.pin(SYMBOL);accept(store,full(OTHER));store.pin(OTHER);
  accept(store,full('CZCE.AP701'));assert.equal(store.entries.size,2);assert.equal(store.get(SYMBOL),null);assert.notEqual(store.get(OTHER),null);
  store.pin(null);accept(store,full('CZCE.TA701'));accept(store,full('DCE.jd2612'));assert.equal(store.get(OTHER),null);assert.equal(store.entries.size,2);
});

test('an active in-flight request remains valid under background cache pressure',()=>{
  const store=new Store({limit:2});store.pin(SYMBOL);const ticket=store.begin(SYMBOL);
  for(let index=0;index<4;index++)accept(store,full('TEST.bg'+index));
  assert.equal(store.accept(SYMBOL,full(),ticket).accepted,true);assert.equal(store.entries.size,2);assert.equal(store.ready(SYMBOL),true);
});

test('a one-entry cache retains only its pin and safely ignores an evicted background response',()=>{
  const store=new Store({limit:1});accept(store,full());store.pin(SYMBOL);const ticket=store.begin(OTHER);
  assert.equal(store.entries.size,1);assert.equal(store.accept(OTHER,full(OTHER),ticket).ignored,true);assert.notEqual(store.get(SYMBOL),null);
  store.clear();assert.equal(store.pinned,null);assert.equal(store.entries.size,0);assert.equal(accept(store,full(OTHER)).accepted,true);
});
