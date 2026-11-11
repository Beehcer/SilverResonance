const test=require('node:test'),assert=require('node:assert/strict'),P=require('./static/workspace-properties.js');
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
