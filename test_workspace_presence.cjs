const {test}=require('node:test'),assert=require('node:assert/strict'),{Sender}=require('./static/workspace-presence.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('cursor delivery coalesces motion in flight and sends the final mouse position',async()=>{
  const sent=[],responses=[];let complete,now=100;const timers=[];
  const sender=new Sender(value=>{sent.push(value);return new Promise(resolve=>complete=resolve);},value=>responses.push(value),{now:()=>now,setTimer:fn=>{timers.push(fn);return timers.length;},clearTimer:()=>{}});
  sender.push({cursor:1});sender.push({cursor:2});sender.push({cursor:3});assert.equal(sent.length,1);
  now=170;complete('first');await tick();assert.deepEqual(sent,[{cursor:1},{cursor:3}]);complete('last');await tick();assert.deepEqual(responses,['first','last']);
});
test('throttled trailing point and immediate pointer leave are both delivered',async()=>{
  const sent=[];let now=100,timer;const sender=new Sender(async value=>sent.push(value),()=>{},{now:()=>now,setTimer:fn=>(timer=fn,1),clearTimer:()=>timer=null});
  sender.push({cursor:1});await tick();now=110;sender.push({cursor:2});assert.equal(sent.length,1);now=150;timer();await tick();assert.deepEqual(sent,[{cursor:1},{cursor:2}]);
  now=155;sender.push({cursor:null},true);await tick();assert.equal(sent.at(-1).cursor,null);
});
test('switching plans cancels pending motion and ignores old response',async()=>{
  const sent=[],received=[];let finish;const sender=new Sender(value=>{sent.push(value);return new Promise(resolve=>finish=resolve);},value=>received.push(value));
  sender.push({symbol:'old',cursor:1});const oldFinish=finish;sender.push({symbol:'old',cursor:2});sender.reset();sender.push({symbol:'new',cursor:null},true);oldFinish('old');await tick();assert.deepEqual(received,[]);finish('new');await tick();assert.deepEqual(received,['new']);assert.equal(sent.length,2);
});

test('browser timer functions retain their global receiver for trailing cursor delivery',async()=>{
  const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout;let scheduled,cleared=false;
  globalThis.setTimeout=function(fn){assert.equal(this,globalThis);scheduled=fn;return 1;};
  globalThis.clearTimeout=function(){assert.equal(this,globalThis);cleared=true;};
  try{const sender=new Sender(async()=>{},()=>{},{now:()=>100});sender.push({cursor:1});await tick();sender.push({cursor:2});assert.equal(typeof scheduled,'function');sender.reset();assert.equal(cleared,true);}finally{globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear;}
});
