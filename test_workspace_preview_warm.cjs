'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const P=require('./static/workspace-market-preview.js');
const fs=require('node:fs'),vm=require('node:vm');
const NOW=Date.parse('2026-11-15T02:00:00Z'),PERIODS=['day','m30','m3'];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function market(symbol='A',now=NOW,price=101){return{symbol,name:symbol,source:'live',connected:true,partial:true,fresh:false,incremental:false,history_token:null,feed_status:'preview',as_of:new Date(now).toISOString(),price,tick:1,period_counts:{day:300,m30:300,m3:300},periods:Object.fromEntries(PERIODS.map(p=>[p,Array.from({length:300},(_,i)=>({dt:new Date(NOW-86400000+i*60000).toISOString(),open:100,high:110,low:90,close:price,volume:i,oi:3}))]))};}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return{promise,resolve,reject};}
function harness(){
 let now=NOW,next=0,busy=false;const timers=new Map(),disk=new Map(),requests=[];
 const setTimer=(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimer=id=>timers.delete(id);
 const store=new P.Store({now:()=>now,storage:{get:async s=>disk.get(s),put:async item=>disk.set(item.symbol,item)}});
 const hub=new P.Hub({store,now:()=>now,setTimer,clearTimer,isBusy:()=>busy,random:()=>0,fetch:(url,options)=>{const d=deferred(),r={url,options,...d};requests.push(r);options.signal.addEventListener('abort',()=>d.reject(new Error('aborted')));return d.promise;}});
 return{hub,store,timers,disk,requests,now:value=>now=value,busy:value=>busy=value,answer:(index,symbol,price)=>requests[index].resolve({ok:true,json:async()=>({market:market(symbol,now,price)})})};
}

test('server previews persist bounded candles and synchronous peek never becomes fresh or a delta baseline',async()=>{
 const h=harness(),task=h.hub.refresh('A');h.answer(0,'A');const view=await task;await tick();
 assert.deepEqual(PERIODS.map(p=>view.periods[p].length),[240,240,240]);assert.equal(h.disk.size,1);
 const cached=h.store.peek('A');assert.equal(cached.partial,true);assert.equal(cached.fresh,false);assert.equal(cached.history_token,null);
 cached.periods.m30[0].close=999;assert.equal(h.store.peek('A').periods.m30[0].close,101);
 const loaded=new P.Store({now:()=>NOW+1000,storage:{get:async s=>h.disk.get(s)}});await loaded.get('A');assert.equal(loaded.peek('A').preview_saved_at,NOW);
});

test('repeated identical partials keep original saved age and changed current candles update the picture',()=>{
 const h=harness();h.store.putPreview(market());h.now(NOW+61000);
 h.store.putPreview(market('A',NOW+61000));assert.equal(h.store.peek('A').preview_saved_at,NOW);
 h.store.putPreview(market('A',NOW+61000,102));assert.equal(h.store.peek('A').price,102);assert.equal(h.store.peek('A').preview_saved_at,NOW+61000);
 h.store.putPreview(market('A',NOW,99));assert.equal(h.store.peek('A').price,102);
});

test('partial write rejects foreign shape, stale/future timestamps and unavailable data',()=>{
 for(const patch of [{partial:false},{incremental:true},{client_cached:true},{unavailable:true},{source:'unknown'},{as_of:'invalid'},{as_of:new Date(NOW+1).toISOString()},{preview_saved_at:NOW+1},{preview_saved_at:NOW-190*86400000}]){
  const h=harness();assert.equal(h.store.putPreview({...market(),...patch}),null);assert.equal(h.store.peek('A'),null);
 }
});

test('catalogue local priming is bounded and never starts network before first plot warm',async()=>{
 const h=harness();for(let i=0;i<20;i++)h.disk.set('S'+i,P.prepare(market('S'+i),NOW));
 h.hub.prime(Array.from({length:40},(_,i)=>({symbol:'S'+i})));await tick();
 assert.equal(h.hub.catalogue.length,32);assert.equal(h.store.memory.size,20);assert.equal(h.requests.length,0);assert.equal(h.timers.size,0);
 assert.equal(h.store.peek('S19').periods.m3.length,240);
});

test('background warming uses one light request, skips active panes and yields to foreground history',async()=>{
 const h=harness();h.hub.prime(['A','B','C','D']);h.hub.focus('top','A');h.hub.focus('bottom','B');h.hub.warm();
 h.busy(true);await h.hub.tick();assert.equal(h.requests.length,0);h.busy(false);
 const first=h.hub.tick();await h.hub.tick();assert.equal(h.requests.length,1);assert.match(h.requests[0].url,/symbol=C&preview=240$/);
 h.answer(0,'C');await first;const second=h.hub.tick();assert.equal(h.requests.length,2);assert.match(h.requests[1].url,/symbol=D&preview=240$/);h.answer(1,'D');await second;
});

test('foreground same-symbol promotion survives a second pane focus and coalesces its response',async()=>{
 const h=harness();const bg=h.hub.refresh('B',{background:true});h.hub.focus('top','B');const current=h.hub.refresh('B');h.hub.focus('bottom','C');
 assert.equal(bg,current);assert.equal(h.requests.length,1);assert.equal(h.requests[0].options.signal.aborted,false);assert.equal(h.hub.background,null);
 assert.ok([...h.timers.values()].some(t=>t.ms===5000));h.answer(0,'B');await current;
});

test('new foreground contract aborts only background, then failed warm gets a short retry rather than a minute cooldown',async()=>{
 const h=harness();h.hub.prime(['A','B','C']);h.hub.focus('top','A');h.hub.warm();
 const bg=h.hub.refresh('B',{background:true});h.hub.focus('top','C');await assert.rejects(bg,/aborted/);
 assert.equal(h.hub.checked.has('B'),false);assert.equal(h.hub.retryAt.get('B'),NOW+1500);
 const fg=h.hub.refresh('C');assert.equal(h.requests[1].options.signal.aborted,false);h.answer(1,'C');await fg;
 h.now(NOW+1600);const retry=h.hub.refresh('B',{background:true});h.answer(2,'B');await retry;assert.equal(h.hub.checked.get('B'),NOW+1600);
});

test('both panes share one preview and releasing last owner stops warming timers',async()=>{
 const h=harness();h.hub.focus('top','A');h.hub.focus('bottom','A');h.hub.warm();
 const a=h.hub.refresh('A'),b=h.hub.refresh('A');assert.equal(a,b);h.answer(0,'A');await a;
 h.hub.release('top');assert.equal(h.hub.warmed,true);h.hub.release('bottom');assert.equal(h.hub.warmed,false);assert.equal(h.timers.size,0);
});

test('immediately selecting an aborted background contract starts a new foreground read',async()=>{
 const h=harness(),old=h.hub.refresh('B',{background:true});h.hub.focus('top','C');h.hub.focus('top','B');
 const next=h.hub.refresh('B');assert.notEqual(next,old);assert.equal(h.requests.length,2);
 await assert.rejects(old,/aborted/);assert.equal(h.hub.requests.get('B').task,next);
 h.answer(1,'B');await next;assert.equal(h.store.peek('B').symbol,'B');assert.equal(h.hub.retryAt.has('B'),false);
});

test('default timers retain their browser Window receiver when the hub is shared across panes',()=>{
 const context=vm.createContext({marker:'window',WorkspaceMarketCache:require('./static/workspace-market-cache.js'),setTimeout(){assert.equal(this.marker,'window');return 1;},clearTimeout(){assert.equal(this.marker,'window');}});
 vm.runInContext(fs.readFileSync(require.resolve('./static/workspace-market-preview.js'),'utf8'),context);
 vm.runInContext('const hub=new WorkspaceMarketPreview.Hub();hub.warm();hub.stop();',context);
});
