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
function append(base,added={day:0,m30:0,m3:1},drops={day:0,m30:0,m3:0}){
  const periods={},counts={};
  for(const period of PERIODS){
    const old=base.periods[period],tail=copy(old.slice(-2));
    for(let index=0;index<(added[period]||0);index++){
      const previous=tail.at(-1);previous.closed=true;previous.closed_at=new Date(Date.parse(previous.dt)+180000).toISOString();
      tail.push({...previous,dt:previous.closed_at,closed:false});delete tail.at(-1).closed_at;
    }
    periods[period]=tail;counts[period]=old.length-(drops[period]||0)+(added[period]||0);
  }
  return{...base,base_history_token:base.history_token,history_token:base.history_token+'-next',incremental:true,period_drops:{...drops},period_counts:counts,periods};
}
function rebased(base=full(),id='reference-1'){
  return{...copy(base),candle_basis:{m30:'trading_minutes_v1'},anchor_reference_id:id,
    anchor_reference:{m30:{basis:'native_v1',target_basis:'trading_minutes_v1',source_times:base.periods.m30.map(row=>Date.parse(row.dt)),target_base_time:Date.parse(base.periods.m30.at(-1).dt)}}};
}

test('a candle basis switch replaces full history and retains an immutable complete anchor reference',()=>{
  const store=new Store(),legacy=full(),next=rebased();accept(store,legacy);
  assert.equal(accept(store,delta(next)).retryFull,true);assert.deepEqual(store.get(SYMBOL),legacy);
  assert.equal(accept(store,next).accepted,true);const view=store.view(SYMBOL);
  assert.deepEqual(view.candle_basis,{m30:'trading_minutes_v1'});assert.equal(view.anchor_reference_id,'reference-1');
  for(const value of [view.candle_basis,view.anchor_reference,view.anchor_reference.m30,view.anchor_reference.m30.source_times])assert.equal(Object.isFrozen(value),true);
  next.anchor_reference.m30.source_times[0]=0;store.get(SYMBOL).anchor_reference.m30.source_times[1]=0;
  assert.notEqual(view.anchor_reference.m30.source_times[0],0);assert.notEqual(view.anchor_reference.m30.source_times[1],0);
});

test('same-basis delta and append packets share the verified reference by ID without resending source timestamps',()=>{
  const store=new Store(),base=rebased();accept(store,base);const reference=store.view(SYMBOL).anchor_reference;
  const tail=delta(base);delete tail.anchor_reference;tail.periods.m3.at(-1).close=104;
  assert.equal(accept(store,tail).accepted,true);assert.equal(store.view(SYMBOL).anchor_reference,reference);assert.equal(store.view(SYMBOL).periods.m3.at(-1).close,104);
  const rolled=append(store.view(SYMBOL));delete rolled.anchor_reference;
  assert.equal(accept(store,rolled).accepted,true);assert.equal(store.view(SYMBOL).anchor_reference,reference);
  assert.equal(store.get(SYMBOL).anchor_reference.m30.source_times.length,base.periods.m30.length);
});

test('different reference IDs, edited same-ID references and different bases cannot patch an existing history',()=>{
  const changes=[packet=>packet.anchor_reference_id='reference-other',packet=>packet.candle_basis.m30='trading_minutes_v2',
    packet=>{packet.anchor_reference=copy(baseReference);packet.anchor_reference.m30.target_base_time++;},packet=>delete packet.anchor_reference_id];
  const baseReference=rebased().anchor_reference;
  for(const change of changes){const store=new Store(),base=rebased();accept(store,base);const before=store.view(SYMBOL),packet=delta(base);delete packet.anchor_reference;change(packet);
    assert.equal(accept(store,packet).retryFull,true);assert.equal(store.view(SYMBOL),before);assert.equal(store.history(SYMBOL),null);
  }
  const store=new Store();accept(store,rebased());const replacement=rebased(full(SYMBOL,'new-full'),'reference-new');replacement.anchor_reference.m30.target_base_time++;
  assert.equal(accept(store,replacement).accepted,true);assert.equal(store.view(SYMBOL).anchor_reference_id,'reference-new');
});

test('full reference validation rejects malformed coordinate data without corrupting the previous picture',()=>{
  const changes=[packet=>delete packet.anchor_reference,packet=>delete packet.anchor_reference_id,packet=>packet.anchor_reference_id='',
    packet=>packet.anchor_reference.m30.source_times=[],packet=>packet.anchor_reference.m30.source_times=[1,1],
    packet=>packet.anchor_reference.m30.source_times=[2,1],packet=>packet.anchor_reference.m30.source_times=[.5,2],
    packet=>packet.anchor_reference.m30.source_times=[true,2],packet=>packet.anchor_reference.m30.source_times=[-1,2],
    packet=>packet.anchor_reference.m30.source_times=Array.from({length:10001},(_,i)=>i),
    packet=>packet.anchor_reference.m30.target_base_time=NaN,packet=>packet.anchor_reference.m30.target_basis='other',
    packet=>packet.anchor_reference.m30.basis='other',packet=>packet.candle_basis={unknown:'trading_minutes_v1'},
    packet=>packet.candle_basis.m30='',packet=>packet.anchor_reference={m3:packet.anchor_reference.m30}];
  for(const change of changes){const store=new Store(),legacy=full();accept(store,legacy);const bad=rebased();change(bad);
    assert.equal(accept(store,bad).retryFull,true);assert.deepEqual(store.get(SYMBOL),legacy);
  }
});

test('an accepted transformed basis cannot silently downgrade to legacy or explicit native until the cache is cleared',()=>{
  for(const explicit of [false,true]){const store=new Store(),current=rebased();accept(store,current);const old=full(SYMBOL,'old-process');if(explicit)old.candle_basis={m30:'native_v1'};
    const rejected=accept(store,old);assert.equal(rejected.retryFull,true);assert.equal(rejected.reason,'candle_basis_downgrade');assert.deepEqual(store.get(SYMBOL),current);
    store.clear(SYMBOL);assert.equal(accept(store,old).accepted,true);assert.deepEqual(store.get(SYMBOL),old);
  }
});

test('LRU eviction does not forget the basis downgrade guard and explicit clear releases it',()=>{
  const store=new Store({limit:1});accept(store,rebased());accept(store,full(OTHER));assert.equal(store.get(SYMBOL),null);assert.equal(store.entries.size,1);
  assert.equal(accept(store,full()).reason,'candle_basis_downgrade');assert.equal(store.get(SYMBOL),null);
  store.clear();assert.equal(accept(store,full()).accepted,true);
});

test('metadata failures and older requests cannot move an accepted basis reference',()=>{
  const store=new Store(),obsolete=store.begin(SYMBOL),latest=store.begin(SYMBOL),current=rebased();
  assert.equal(store.accept(SYMBOL,current,latest).accepted,true);const before=store.view(SYMBOL);
  assert.equal(store.accept(SYMBOL,full(),obsolete).ignored,true);assert.equal(store.view(SYMBOL),before);
  const bad=delta(current);delete bad.anchor_reference;bad.periods.m3.at(-1).close=104;bad.anchor_reference_id='wrong';bad.fresh=false;
  assert.equal(accept(store,bad).retryFull,true);assert.equal(store.view(SYMBOL),before);assert.equal(before.fresh,true);
});

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

test('read-only views deeply protect cached prices, arrays and nested metadata without copying',()=>{
  const store=new Store(),source=full();source.periods.m3[0].details={flags:['verified']};
  const result=store.accept(SYMBOL,source,store.begin(SYMBOL),{readOnly:true}),view=store.view(SYMBOL);
  assert.equal(view,result.market);assert.equal(store.view(SYMBOL),view);assert.equal(store.view(OTHER),null);
  for(const value of [view,view.periods,view.periods.m3,view.periods.m3[0],view.periods.m3[0].details,view.periods.m3[0].details.flags,view.freshness.day])assert.equal(Object.isFrozen(value),true);
  assert.equal(Reflect.set(view.periods.m3[0],'close',0),false);assert.equal(Reflect.set(view.freshness.day,'available',false),false);
  assert.throws(()=>view.periods.m3.push({}),TypeError);assert.throws(()=>view.periods.m3[0].details.flags.push('bad'),TypeError);
  source.periods.m3[0].details.flags.push('wire mutation');assert.deepEqual(view.periods.m3[0].details.flags,['verified']);assert.equal(Object.isFrozen(source),false);
  const independent=store.get(SYMBOL);independent.periods.m3[0].close=99;assert.equal(view.periods.m3[0].close,101);
});

test('hot read-only updates share historical rows and unchanged periods while preserving older snapshots',()=>{
  const store=new Store(),base=full(SYMBOL,'large',{day:180,m30:6000,m3:10000});store.accept(SYMBOL,base,store.begin(SYMBOL),{readOnly:true});
  const before=store.view(SYMBOL),patch=delta(base);patch.periods.m3[1].close=104;patch.fresh=false;patch.quote_at=null;
  const result=store.accept(SYMBOL,patch,store.begin(SYMBOL),{readOnly:true}),after=store.view(SYMBOL);
  assert.equal(result.market,after);assert.notEqual(after,before);assert.equal(after.periods.day,before.periods.day);assert.equal(after.periods.m30,before.periods.m30);
  assert.notEqual(after.periods.m3,before.periods.m3);assert.equal(after.periods.m3[0],before.periods.m3[0]);assert.equal(after.periods.m3[9998],before.periods.m3[9998]);assert.notEqual(after.periods.m3[9999],before.periods.m3[9999]);
  assert.equal(before.periods.m3.at(-1).close,101);assert.equal(before.fresh,true);assert.equal(after.periods.m3.at(-1).close,104);assert.equal(after.fresh,false);assert.equal(after.quote_at,null);
  patch.periods.m3[1].close=98;assert.equal(after.periods.m3.at(-1).close,104);
});

test('metadata-only ticks retain all period array identities and replace metadata honestly',()=>{
  const store=new Store(),base=full();accept(store,base);const before=store.view(SYMBOL),patch=delta(base);
  patch.connected=false;patch.feed_status='disconnected';patch.fresh=false;patch.price=0;patch.freshness={day:{available:false},m30:{available:false},m3:{available:false}};
  const after=store.accept(SYMBOL,patch,store.begin(SYMBOL),{readOnly:true}).market;
  for(const period of PERIODS)assert.equal(after.periods[period],before.periods[period]);
  assert.equal(after.connected,false);assert.equal(after.freshness.day.available,false);assert.equal(before.connected,true);assert.equal(before.freshness.day.available,true);
});

test('append responses update the former last bar and append new bars without resending the historical prefix',()=>{
  const store=new Store(),base=full();accept(store,base);const before=store.view(SYMBOL),patch=append(base,{day:1,m30:2,m3:3});
  patch.periods.day.at(-1).source_trade_day='2026-11-12';patch.price=103;patch.periods.m3.at(-1).close=103;
  const result=store.accept(SYMBOL,patch,store.begin(SYMBOL),{readOnly:true}),after=result.market;
  assert.equal(result.accepted,true);assert.equal(store.history(SYMBOL),patch.history_token);
  for(const period of PERIODS){assert.equal(after.periods[period].length,patch.period_counts[period]);assert.equal(after.periods[period][0],before.periods[period][0]);assert.equal(after.periods[period][before.periods[period].length-1].closed,true);assert.equal(before.periods[period].at(-1).closed,false);}
  assert.equal(after.periods.day.at(-1).source_trade_day,'2026-11-12');assert.equal(after.periods.m3.at(-1).close,103);assert.equal(after.price,103);
  const next=append(after,{day:0,m30:0,m3:1});assert.equal(store.accept(SYMBOL,next,store.begin(SYMBOL),{readOnly:true}).accepted,true);assert.equal(store.view(SYMBOL).periods.m3.length,14);
});

test('a capped sliding history drops only the specified oldest bars while appending new bars',()=>{
  const store=new Store(),base=full(SYMBOL,'large',{day:180,m30:6000,m3:10000});store.accept(SYMBOL,base,store.begin(SYMBOL),{readOnly:true});const before=store.view(SYMBOL);
  const patch=append(base,{day:1,m30:2,m3:3},{day:1,m30:2,m3:3}),result=store.accept(SYMBOL,patch,store.begin(SYMBOL),{readOnly:true});
  assert.equal(result.accepted,true);for(const period of PERIODS){const drop=patch.period_drops[period];assert.equal(result.market.periods[period].length,base.period_counts[period]);assert.equal(result.market.periods[period][0],before.periods[period][drop]);assert.equal(result.market.periods[period].at(-1).dt,patch.periods[period].at(-1).dt);assert.equal(before.periods[period][0].dt,base.periods[period][0].dt);}
});

test('append protocol also accepts unchanged tails, one-bar histories and up to sixty-four missing bars',()=>{
  const store=new Store(),base=full(SYMBOL,'one',{day:1,m30:1,m3:1});accept(store,base);const before=store.view(SYMBOL);
  const tick=append(base,{day:0,m30:0,m3:0});tick.history_token=base.history_token;assert.equal(accept(store,tick).accepted,true);
  for(const period of PERIODS)assert.equal(store.view(SYMBOL).periods[period],before.periods[period]);
  const growth=append(store.view(SYMBOL),{day:1,m30:1,m3:64});assert.equal(accept(store,growth).accepted,true);assert.equal(store.view(SYMBOL).periods.m3.length,65);assert.equal(store.view(SYMBOL).periods.m3[0].dt,base.periods.m3[0].dt);
});

test('unsafe append metadata, overlaps, counts and oversized gaps request full history atomically',()=>{
  const changes=[
    d=>d.base_history_token='other-process',d=>delete d.base_history_token,d=>delete d.period_drops,
    d=>delete d.period_drops.day,d=>d.period_drops.m3=-1,d=>d.period_drops.m3=0.5,d=>d.period_drops.m3=true,
    d=>d.period_drops.day=1,d=>d.period_counts.m3++,d=>d.periods.m3.shift(),
    d=>d.periods.m3[0].dt='2000-01-01T00:00:00Z',d=>d.periods.m3.at(-1).dt=d.periods.m3.at(-2).dt,
    d=>d.periods.m3.at(-1).low=200,d=>{d.periods.m30=[];d.period_counts.m30=0;},
    d=>Object.assign(d,append(full(),{day:0,m30:0,m3:65}))
  ];
  for(const change of changes){const store=new Store(),base=full();accept(store,base);const before=store.view(SYMBOL),patch=append(base);patch.periods.day[0].close=104;patch.fresh=false;change(patch);const result=store.accept(SYMBOL,patch,store.begin(SYMBOL),{readOnly:true});assert.equal(result.retryFull,true);assert.equal(store.view(SYMBOL),before);assert.equal(store.history(SYMBOL),null);assert.deepEqual(store.get(SYMBOL),base);}
});

test('append cannot drop an overlap anchor or grow past a period cap',()=>{
  const store=new Store(),base=full(SYMBOL,'short',{day:2,m30:2,m3:2});accept(store,base);
  assert.equal(accept(store,append(base,{day:0,m30:0,m3:1},{day:0,m30:0,m3:1})).retryFull,true);
  const large=full(SYMBOL,'large',{day:180,m30:6000,m3:10000});accept(store,large);assert.equal(accept(store,append(large)).retryFull,true);
});

test('older append requests cannot roll back an already accepted newer cursor',()=>{
  const store=new Store(),base=full();accept(store,base);const older=store.begin(SYMBOL),latest=store.begin(SYMBOL),patch=append(base);
  assert.equal(store.accept(SYMBOL,patch,latest,{readOnly:true}).accepted,true);const after=store.view(SYMBOL);
  assert.equal(store.accept(SYMBOL,delta(base),older,{readOnly:true}).ignored,true);assert.equal(store.view(SYMBOL),after);assert.equal(store.history(SYMBOL),patch.history_token);
});
