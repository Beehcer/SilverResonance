const test=require('node:test');
const assert=require('node:assert/strict');
const T=require('./static/workspace-ticket.js');

test('two prices determine color/direction without a separate selection',()=>{
  assert.equal(T.directionOf({planned_entry_price:100,stop_price:105}),'short');
  assert.equal(T.directionOf({planned_entry_price:'100',stop_price:'95'}),'long');
  for(const stop of ['',null,0,NaN,Infinity,100])assert.equal(T.directionOf({planned_entry_price:100,stop_price:stop}),null);
});
test('editing prices preserves existing notes, waves and hidden metadata, and suspends monitoring',()=>{
  const plan={direction:'long',title:'旧计划',mode:'range',notes:'保留',expires_at:'2027-01-01',objects:[{id:'wave'}],planned_entry_price:100,stop_price:95,enabled:true,context_confirmed:true};
  const next=T.withLevels(plan,{stop_price:105});
  assert.equal(next.direction,'short');assert.equal(next.enabled,false);assert.equal(next.context_confirmed,false);
  for(const key of ['title','mode','notes','expires_at','objects'])assert.deepEqual(next[key],plan[key]);
  assert.equal(plan.stop_price,95);
  assert.equal(T.withLevels(next,{planned_entry_price:110}).direction,'long');
});
test('continuous marking always advances to stop, while individual edits stay individual',()=>{
  const existing={planned_entry_price:100,stop_price:95};
  assert.equal(T.nextTool('planned_entry_price',existing,true),'planned-stop');
  assert.equal(T.nextTool('planned_entry_price',existing,false),null);
  assert.equal(T.nextTool('stop_price',existing,true),null);
});
