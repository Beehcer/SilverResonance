const test=require('node:test'),assert=require('node:assert/strict'),L=require('./static/workspace-layers.js');
const ids=items=>items.map(item=>item.id);
test('user opacity multiplies with each lower-period mapping and zero remains a selectable listed layer',()=>{
  const wave={id:'faded',kind:'wave',period:'day',opacity:.5};
  assert.equal(L.provenance(wave,'day').opacity,.5);
  assert.equal(L.provenance(wave,'m30').opacity,.2);
  assert.ok(Math.abs(L.provenance(wave,'m3').opacity-.08)<1e-9);
  const clear={...wave,opacity:0};assert.equal(L.provenance(clear,'m3').opacity,0);
  assert.deepEqual(ids(L.filterObjects([clear],{period:'m3'})),['faded']);
  assert.match(L.provenance(clear,'day').text,/0%/);
  assert.equal(L.provenance({kind:'horizontal',period:'all'},'m30').opacity,1);
});
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

function scopedHarness(){
  class Element extends EventTarget{
    constructor(tag){super();this.tag=tag;this.children=[];this.dataset={};this.attributes={};this.parentElement=null;this.value='';this.clientHeight=0;const classes=new Set();this.classList={add:value=>classes.add(value),remove:value=>classes.delete(value),contains:value=>classes.has(value),toggle(value,on){if(on??!classes.has(value))classes.add(value);else classes.delete(value);}};}
    append(...nodes){for(const node of nodes){node.remove();node.parentElement=this;this.children.push(node);}}
    replaceChildren(...nodes){for(const node of this.children)node.parentElement=null;this.children=[];this.append(...nodes);}
    insertBefore(node,target){if(node===target)return;node.remove();const at=target?this.children.indexOf(target):this.children.length;assert.ok(at>=0);this.children.splice(at,0,node);node.parentElement=this;}
    remove(){if(!this.parentElement)return;const children=this.parentElement.children,index=children.indexOf(this);if(index>=0)children.splice(index,1);this.parentElement=null;}
    before(){}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    get firstElementChild(){return this.children[0]||null;}
    get nextElementSibling(){if(!this.parentElement)return null;return this.parentElement.children[this.parentElement.children.indexOf(this)+1]||null;}
    setAttribute(key,value){this.attributes[key]=value;}
    getBoundingClientRect(){return{top:0,bottom:40,height:40};}
  }
  const document={createElement:tag=>new Element(tag)},list=new Element('div'),calls=[];list.ownerDocument=document;
  const callbacks=Object.fromEntries(['select','locate','context','update','reorder'].map(kind=>[kind,(...args)=>calls.push({kind,args})]));
  return{controller:new L.Controller({list,callbacks}),list,calls,objects:[{id:'a',kind:'text',period:'m30',name:'A'},{id:'b',kind:'text',period:'m30',name:'B'},{id:'c',kind:'text',period:'m3',name:'C'}]};
}

test('changing document scope discards rows, filters, and active drag state even when layer IDs recur',()=>{
  const{controller,list,objects}=scopedHarness();controller.update({scope:'A:1',objects});const old=controller.rows.get('a');
  controller.search.value='A';dispatch(controller.search,'input');controller.periodSelect.value='m30';dispatch(controller.periodSelect,'change');
  dispatch(old.row,'dragstart');old.row.dataset.drop='before';assert.equal(controller.dragged,'a');assert.equal(controller.query,'A');
  controller.update({scope:'B:2',objects:objects.map(object=>({...object,name:'另一板 '+object.name})),selected:'a'});
  assert.equal(controller.scope,'B:2');assert.equal(controller.dragged,null);assert.equal(controller.query,'');assert.equal(controller.filter,'all');assert.equal(controller.search.value,'');assert.equal(controller.periodSelect.value,'all');
  assert.equal(old.row.parentElement,null);assert.equal(old.row.classList.contains('dragging'),false);assert.equal(old.row.dataset.drop,undefined);assert.notEqual(controller.rows.get('a').row,old.row);assert.equal(list.children.length,3);assert.equal(controller.rows.get('a').row.classList.contains('selected'),true);
});

test('detached row listeners cannot select, edit, locate, reorder or reopen menus after A to B to A',()=>{
  const{controller,objects,calls}=scopedHarness();controller.update({scope:'A',objects});const old=controller.rows.get('a');
  controller.update({scope:'B',objects});controller.update({scope:'A',objects});assert.equal(controller.scope,'A','even reuse of a caller scope must not revive old closures');
  for(const button of [old.main,old.locate,old.visibility,old.lock])dispatch(button,'click');
  assert.equal(dispatch(old.row,'contextmenu').event.defaultPrevented,true);dispatch(old.row,'keydown',{key:'ContextMenu'});dispatch(old.row,'keydown',{key:'F10',shiftKey:true});dispatch(old.drag,'keydown',{key:'Home'});
  assert.equal(dispatch(old.row,'dragstart').event.defaultPrevented,true);dispatch(old.row,'dragover',{clientY:0});dispatch(old.row,'drop');dispatch(old.row,'dragend');
  assert.deepEqual(calls,[]);assert.equal(controller.dragged,null);assert.deepEqual(objects.map(object=>object.name),['A','B','C']);assert.equal(controller.rows.get('a').row.parentElement,controller.list);
});

test('late drag events from a detached scope cannot consume or cancel a new document drag',()=>{
  const{controller,objects,calls}=scopedHarness();controller.update({scope:'A:1',objects});const old=controller.rows.get('b');controller.update({scope:'B:2',objects});
  const source=controller.rows.get('a'),target=controller.rows.get('b');dispatch(source.row,'dragstart');dispatch(target.row,'dragover',{clientY:0});assert.equal(target.row.dataset.drop,'before');
  dispatch(old.row,'dragover',{clientY:39});dispatch(old.row,'drop');dispatch(old.row,'dragend');dispatch(old.row,'dragleave',{relatedTarget:null});
  assert.equal(controller.dragged,'a');assert.equal(target.row.dataset.drop,'before');assert.equal(source.row.classList.contains('dragging'),true);assert.deepEqual(calls,[]);
  dispatch(target.row,'drop');assert.equal(controller.dragged,null);assert.deepEqual(calls,[{kind:'reorder',args:[[{id:'a',patch:{z_index:1.5}}]]}]);
});

test('current scope callbacks still work for visibility, locks, menus, location and keyboard ordering',()=>{
  const{controller,objects,calls}=scopedHarness();controller.update({scope:'live:7',objects});const row=controller.rows.get('a');
  dispatch(row.main,'click');dispatch(row.locate,'click');dispatch(row.visibility,'click');dispatch(row.lock,'click');dispatch(row.row,'contextmenu');dispatch(row.drag,'keydown',{key:'Home'});
  assert.deepEqual(calls.map(call=>call.kind),['select','locate','update','update','context','reorder']);assert.deepEqual(calls[0].args,['a']);assert.deepEqual(calls[2].args,['a',{visible:false}]);assert.deepEqual(calls[3].args,['a',{locked:true}]);assert.deepEqual(calls.at(-1).args,[[{id:'a',patch:{z_index:3}}]]);
});

test('same scope and omitted optional scopes preserve cached row identity and existing filters',()=>{
  const{controller,objects,calls}=scopedHarness();controller.update({objects});const legacy=controller.rows.get('a');controller.update({objects});assert.equal(controller.rows.get('a'),legacy);dispatch(legacy.main,'click');assert.equal(calls.length,1);
  controller.update({scope:'A:1',objects});const scoped=controller.rows.get('a');controller.search.value='A';dispatch(controller.search,'input');controller.update({scope:'A:1',objects:objects.map(object=>({...object,note:'updated'}))});assert.equal(controller.rows.get('a'),scoped);assert.equal(controller.query,'A');
  controller.update({objects});assert.equal(controller.scope,'A:1');assert.equal(controller.rows.get('a'),scoped);assert.equal(controller.query,'A');dispatch(scoped.main,'click');assert.equal(calls.length,2);
});

test('destroyed controllers reject all remaining row callbacks instead of editing a recreated sidebar',()=>{
  const{controller,objects,calls}=scopedHarness();controller.update({scope:'A:1',objects});const old=controller.rows.get('a');controller.destroy();
  dispatch(old.main,'click');dispatch(old.locate,'click');dispatch(old.visibility,'click');dispatch(old.lock,'click');dispatch(old.row,'contextmenu');dispatch(old.drag,'keydown',{key:'Home'});assert.equal(dispatch(old.row,'dragstart').event.defaultPrevented,true);dispatch(old.row,'drop');dispatch(old.row,'dragend');assert.deepEqual(calls,[]);assert.equal(controller.rows.size,0);
});
