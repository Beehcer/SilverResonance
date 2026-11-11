const test=require('node:test'),assert=require('node:assert/strict'),L=require('./static/workspace-layers.js');
const ids=items=>items.map(item=>item.id);
function apply(objects,changes){return objects.map(object=>({...object,...changes.find(change=>change.id===object.id)?.patch}));}
test('controller takes over only its drawing list and removes the template empty placeholder',()=>{
  const document={createElement(){return{children:[],append(...nodes){this.children.push(...nodes);},setAttribute(){},addEventListener(){}};}};
  const planRow={textContent:'开仓计划'},placeholder={textContent:'还没有手工标记'};
  const list={ownerDocument:document,children:[placeholder],replaceChildren(...nodes){this.children=nodes;},before(){},setAttribute(){}};
  const controller=new L.Controller({list});
  assert.deepEqual(list.children,[]);assert.equal(planRow.textContent,'开仓计划');assert.equal(controller.rows.size,0);
});
test('legacy layers keep paint order and ordered ranks are stable without mutation',()=>{
  const objects=[{id:'a'},{id:'b'},{id:'c',z_index:-2},{id:'d',z_index:1}];
  assert.deepEqual(ids(L.ordered(objects)),['c','a','b','d']);assert.deepEqual(ids(objects),['a','b','c','d']);
  assert.equal(L.nextZ(objects),2);assert.equal(L.nextZ([]),0);
});
test('drag above target changes only moved rank and preserves all intervening hidden layers',()=>{
  const objects=[{id:'a'},{id:'hidden',visible:false},{id:'b',locked:true},{id:'c'}];
  const changes=L.reorder(objects,'a','b',true);
  assert.deepEqual(changes,[{id:'a',patch:{z_index:2.5}}]);
  assert.deepEqual(ids(L.ordered(apply(objects,changes))),['hidden','b','a','c']);
  assert.equal(objects[0].z_index,undefined);assert.equal(objects[2].locked,true);
});
test('drag below target, top and bottom have sidebar rather than chart-order semantics',()=>{
  const objects=[{id:'a'},{id:'b'},{id:'c'}];
  assert.deepEqual(ids(L.ordered(apply(objects,L.reorder(objects,'c','a',false)))),['c','a','b']);
  assert.deepEqual(ids(L.ordered(apply(objects,L.reorder(objects,'a','c',true)))),['b','c','a']);
  assert.deepEqual(L.reorder(objects,'a','b',false),[]);
});
test('locked, missing and same-object reorders never mutate objects',()=>{
  const objects=[{id:'a',locked:true},{id:'b'}];
  for(const input of[['a','b'],['x','b'],['b','x'],['b','b']])assert.deepEqual(L.reorder(objects,...input),[]);
});
test('equal neighboring ranks cannot silently change unrelated or locked objects',()=>{
  const objects=[{id:'a',z_index:0},{id:'b',z_index:1,locked:true},{id:'c',z_index:1}];
  assert.deepEqual(L.reorder(objects,'a','b',true),[]);
  assert.deepEqual(L.reorder(objects,'a','c',true),[{id:'a',patch:{z_index:2}}]);
});
test('period filter includes larger forecast mappings and all-period lines but never smaller waves',()=>{
  const objects=[{id:'daywave',kind:'wave',period:'day'},{id:'m30wave',kind:'path',period:'m30'},{id:'m3wave',kind:'wave',period:'m3'},{id:'dayzone',kind:'zone',period:'day'},{id:'global',kind:'horizontal',period:'all'}];
  assert.deepEqual(ids(L.filterObjects(objects,{period:'m30'})),['global','m30wave','daywave']);
  assert.deepEqual(ids(L.filterObjects(objects,{period:'day'})),['global','dayzone','daywave']);
  assert.deepEqual(ids(L.filterObjects(objects,{period:'m3'})),['global','m3wave','m30wave','daywave']);
});
test('search matches name, annotation notes and type while retaining current period filter',()=>{
  const objects=[{id:'a',name:'上方压力',kind:'horizontal',period:'all',note:'晚盘关注'},{id:'b',name:'ABC',kind:'zone',period:'day'}];
  assert.deepEqual(ids(L.filterObjects(objects,{query:' 晚盘 '})),['a']);
  assert.deepEqual(ids(L.filterObjects(objects,{query:'abc'})),['b']);
  assert.deepEqual(ids(L.filterObjects(objects,{query:'区域',period:'m30'})),[]);
});
test('provenance reports source and exact cumulative mapping opacity',()=>{
  const object={kind:'wave',period:'day',source_period:'day'};
  const info=L.provenanceInfo(object,'m3');assert.equal(info.originPeriod,'day');assert.equal(info.mapped,true);assert.ok(Math.abs(info.opacity-.16)<1e-10);
  assert.match(L.provenance(object,'m3').text,/日线原图.*3分映射.*16%/);
  assert.match(L.provenance(object,'m30').text,/40%/);
  assert.equal(L.provenanceInfo(object,'day').mapped,false);
});
test('all-period legacy objects never invent an origin and changed display period retains creation provenance',()=>{
  assert.match(L.provenance({kind:'trend',period:'all'},'m30').text,/来源周期未记录/);
  assert.equal(L.provenanceInfo({kind:'trend',period:'all'},'m30').originPeriod,null);
  assert.match(L.provenance({kind:'trend',period:'all',source_period:'day'},'m30').text,/日线绘制.*全周期/);
  const info=L.provenanceInfo({kind:'zone',period:'m30',source_period:'day'},'m30');assert.equal(info.originPeriod,'day');assert.equal(info.drawingPeriod,'m30');assert.match(info.detail,/最初在日线.*现位于30分/);
});
