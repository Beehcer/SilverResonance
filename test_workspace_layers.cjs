const test=require('node:test'),assert=require('node:assert/strict'),L=require('./static/workspace-layers.js');
const ids=items=>items.map(item=>item.id);
function apply(objects,changes){return objects.map(object=>({...object,...changes.find(change=>change.id===object.id)?.patch}));}
function contextHarness(){
  class Element extends EventTarget{
    constructor(){super();this.children=[];this.dataset={};this.attributes={};}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    before(){}
    setAttribute(key,value){this.attributes[key]=value;}
  }
  const document={createElement:()=>new Element()},list=new Element(),calls=[];list.ownerDocument=document;
  const controller=new L.Controller({list,callbacks:{context:(id,event)=>calls.push({id,event}),select:()=>assert.fail('Menu routing must not invoke a different selection callback.')}});
  return{controller,calls};
}
function dispatch(node,type,values={}){const event=new Event(type,{bubbles:true,cancelable:true});Object.assign(event,values);const accepted=node.dispatchEvent(event);return{event,accepted};}

test('layer contextmenu listener routes the clicked row rather than the previously selected layer',()=>{
  const{controller,calls}=contextHarness();controller.objects=[{id:'selected'},{id:'clicked'}];controller.selected='selected';
  const row=controller.row('clicked').row,{event,accepted}=dispatch(row,'contextmenu',{button:2,clientX:325,clientY:171});
  assert.equal(accepted,false);assert.equal(event.defaultPrevented,true);assert.equal(calls.length,1);
  assert.equal(calls[0].id,'clicked');assert.equal(calls[0].event,event);assert.equal(event.target,row);
  assert.equal(controller.selected,'selected');
});
test('hidden and locked layers still expose their own contextual actions',()=>{
  const{controller,calls}=contextHarness();
  controller.objects=[{id:'hidden',visible:false},{id:'locked',locked:true},{id:'both',visible:false,locked:true}];
  for(const object of controller.objects){const{event}=dispatch(controller.row(object.id).row,'contextmenu',{button:2});assert.equal(event.defaultPrevented,true);}
  assert.deepEqual(calls.map(call=>call.id),['hidden','locked','both']);
  assert.deepEqual(controller.objects,[{id:'hidden',visible:false},{id:'locked',locked:true},{id:'both',visible:false,locked:true}]);
});
test('Shift F10 and the context menu key use the same layer handler and suppress native menus',()=>{
  const{controller,calls}=contextHarness(),row=controller.row('keyboard').row;
  for(const keys of[{key:'F10',shiftKey:true},{key:'ContextMenu'}]){
    const{event,accepted}=dispatch(row,'keydown',keys);assert.equal(accepted,false);assert.equal(event.defaultPrevented,true);
    assert.equal(calls.at(-1).id,'keyboard');assert.equal(calls.at(-1).event,event);
  }
  assert.equal(calls.length,2);
  for(const keys of[{key:'F10'},{key:'F10',shiftKey:true,ctrlKey:true},{key:'Delete'},{key:'ArrowUp'}]){
    const{event,accepted}=dispatch(row,'keydown',keys);assert.equal(accepted,true);assert.equal(event.defaultPrevented,false);
  }
  assert.equal(calls.length,2);
});
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
