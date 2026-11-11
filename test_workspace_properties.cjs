const test=require('node:test'),assert=require('node:assert/strict'),P=require('./static/workspace-properties.js');
test('opacity accepts the full percentage range, preserves zero and edits only its property',()=>{
  const object={kind:'zone',opacity:1,points:[{time:10,price:100},{time:30,price:110}]};
  for(const [input,expected]of [['0',0],['1',.01],['37',.37],['100',1]]){
    assert.deepEqual(P.propertyPatch(object,'opacity',input),{patch:{opacity:expected}});
    assert.deepEqual(P.propertyPatch(object,'opacity-value',input),{patch:{opacity:expected}});
    assert.deepEqual(P.opacityPatch(input,'plan_opacity'),{patch:{plan_opacity:expected}});
  }
  for(const input of ['',null,true,'-1','101','NaN',Infinity])assert.ok(P.opacityPatch(input).error);
  assert.ok(P.propertyPatch({...object,locked:true},'opacity',30).error);
  assert.equal(P.opacity(0),0);assert.equal(P.opacity(undefined),1);
  assert.equal(P.opacity(null),1);assert.equal(P.opacity(NaN),1);
});
test('opacity changes survive collaboration and undo without overwriting peer geometry or notes',()=>{
  const M=require('./static/workspace-merge.js'),E=require('./static/workspace-editing.js');
  const before={notes:'initial',objects:[{id:'a',kind:'horizontal',color:'#f1c40f',points:[{time:10,price:100}]}]},after=JSON.parse(JSON.stringify(before));
  after.plan_opacity=0;after.objects[0].opacity=.35;
  const delta=M.diff(before,after);assert.equal(delta.changes.plan_opacity,0);
  assert.deepEqual(delta.object_edits[0].changes,{opacity:.35});
  const peer=JSON.parse(JSON.stringify(before));peer.notes='伙伴备注';peer.objects[0].points[0].price=103;
  const merged=M.apply(peer,delta);assert.equal(merged.plan_opacity,0);assert.equal(merged.objects[0].opacity,.35);
  assert.equal(merged.objects[0].points[0].price,103);assert.equal(merged.notes,'伙伴备注');
  const operation=E.operation(before,after);
  const result=E.apply(merged,operation,true);assert.equal(result.conflicts.length,0);
  assert.equal(P.opacity(result.plan.plan_opacity),1);assert.equal(P.opacity(result.plan.objects[0].opacity),1);
  assert.equal(result.plan.objects[0].points[0].price,103);assert.equal(result.plan.notes,'伙伴备注');
});
test('property edits change only their own field, including empty names and notes',()=>{
  const object={name:'危险区',color:'#f1c40f',points:[{time:10,price:100},{time:30,price:110}]};
  assert.deepEqual(P.propertyPatch(object,'color','#00ff00'),{patch:{color:'#00ff00'}});
  assert.deepEqual(P.propertyPatch(object,'name',''),{patch:{name:''}});
  assert.deepEqual(P.propertyPatch(object,'note','等反弹'),{patch:{note:'等反弹'}});
  assert.ok(P.propertyPatch({...object,locked:true},'color','#00ff00').error);
});
test('zone boundary edit preserves corner orientation and rejects incomplete invalid input',()=>{
  const object={kind:'zone',points:[{time:10,price:110},{time:30,price:100}]};
  assert.deepEqual(P.propertyPatch(object,'low','102').patch.points,[{time:10,price:110},{time:30,price:102}]);
  for(const value of ['','-',NaN,'111'])assert.ok(P.propertyPatch(object,'low',value).error);
  assert.deepEqual(P.propertyPatch(object,'color','#00ff00').patch,{color:'#00ff00'});
});
test('node edits follow stable IDs through peer insertion and reject deleted nodes',()=>{
  const object={points:[{id:'new',time:5,price:98},{id:'target',time:10,price:100},{id:'b',time:30,price:110}]};
  const next=P.nodePatch(object,'target','price','103').patch.points;
  assert.equal(next[1].price,103);assert.deepEqual(next[0],object.points[0]);assert.deepEqual(next[2],object.points[2]);
  assert.ok(P.nodePatch(object,'deleted','price','103').error);
  assert.ok(P.nodePatch(object,'target','price','').error);
});
