'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');

function harness(run){
  const oldDocument=global.document,oldObserver=global.ResizeObserver;
  function make(tag){return{tag,style:{},attrs:{},listeners:{},children:[],hidden:false,value:'',setAttribute(key,value){this.attrs[key]=String(value);},getAttribute(key){return this.attrs[key];},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},addEventListener(type,fn){this.listeners[type]=fn;},getBoundingClientRect(){return{left:0,top:0};},focus(){this.focused=true;},select(){this.selectedText=true;},setPointerCapture(id){this.capture=id;},hasPointerCapture(id){return this.capture===id;},releasePointerCapture(){this.capture=null;}};}
  try{
    global.document={createElement:make,createElementNS:(_,tag)=>make(tag)};global.ResizeObserver=class{observe(){}};
    const host=Object.assign(make('div'),{clientWidth:500,clientHeight:260}),updates=[],presence=[],notices=[],chart=new C.Chart(host,'m3',{update(id,patch){updates.push({id,patch:structuredClone(patch)});chart.setPlan({...chart.plan,objects:chart.plan.objects.map(object=>object.id===id?{...object,...patch}:object)});},presence:value=>presence.push(value),notice:message=>notices.push(message)});
    chart.render=function(){this.syncTitleEditor();};chart.view={start:0,end:100,min:90,max:110};chart.plot={x:5,y:5,w:400,h:200};chart.tx=C.transform(chart.view,chart.plot);
    const pointer=(x,y,timeStamp=1000)=>({button:0,clientX:x,clientY:y,pointerId:1,timeStamp,preventDefault(){},stopPropagation(){}});
    const key=(value,extra={})=>({key:value,preventDefault(){},stopPropagation(){},...extra});
    const names=(kind='text',extra={})=>({id:kind,kind,period:'m3',name:'等待回抽',color:'#ffcc44',points:[{time:50,price:103},...(kind==='text'||kind==='horizontal'?[]:[{time:80,price:kind==='segment'?103:99}])],...extra});
    const textPoint=object=>{const label=chart.nameLabel(object);return{x:label.left+label.width/2,y:label.y-3};};
    run({chart,host,updates,presence,notices,pointer,key,names,textPoint,make});
  }finally{global.document=oldDocument;global.ResizeObserver=oldObserver;}
}

test('all existing editable names open inline on two presses after SVG replacement, including left-side text',()=>{
  harness(({chart,updates,pointer,key,names,textPoint})=>{
    for(const [kind,extra] of [['text',{}],['text',{text_side:'left'}],['zone',{}],['trend',{}],['reference',{}],['horizontal',{}],['segment',{}]]){
      const object=names(kind,extra);chart.setPlan({objects:[object]});chart.lastTitleClick=null;chart.suppressDoubleClickUntil=0;const point=textPoint(object);
      chart.down(pointer(point.x,point.y,1000));chart.up(pointer(point.x,point.y,1020));chart.down(pointer(point.x,point.y,1200));chart.up(pointer(point.x,point.y,1220));
      assert.equal(chart.titleObjectId,object.id,kind+JSON.stringify(extra));assert.equal(chart.titleEditor.hidden,false);assert.equal(chart.titleEditor.value,object.name);
      assert.equal(chart.titleEditor.attrs['aria-label'],kind==='text'?'文本内容':'图层名称');assert.equal(chart.titleEditor.selectedText,true);assert.equal(updates.length,0);
      chart.titleEditor.listeners.keydown(key('Enter'));assert.equal(chart.titleObjectId,null);
    }
  });
});

test('native doubleclick uses exact rendered text target and opens at its label instead of at the candle',()=>{
  harness(({chart,pointer,names,textPoint})=>{
    const object=names('text',{text_side:'left'});chart.plan={objects:[object]};const label=chart.nameLabel(object),point=textPoint(object);
    chart.doubleClick({...pointer(point.x,point.y),target:{closest(selector){return selector==='[data-edit-name]'?{getAttribute:()=>object.id}:null;}}});
    assert.equal(chart.titleObjectId,object.id);assert.equal(parseFloat(chart.titleEditor.style.left),label.left);
    assert.equal(chart.hit(point).object.id,object.id,'left-side text must also select and drag from its rendered label');
  });
});

test('typing publishes only name immediately and preserves remote style, geometry and other layers',()=>{
  harness(({chart,updates,presence,key,names,textPoint})=>{
    const object=names(),point=textPoint(object);chart.plan={objects:[object]};chart.openTitleEditor(object,point);
    chart.titleEditor.value='反弹受阻';chart.titleEditor.listeners.input({isComposing:false});
    assert.deepEqual(updates,[{id:object.id,patch:{name:'反弹受阻'}}]);assert.equal(chart.plan.objects[0].name,'反弹受阻');assert.equal(chart.titleEditor.hidden,false);
    const peer={...chart.plan.objects[0],color:'#00ff88',points:[{time:60,price:105}]};chart.setPlan({objects:[peer,{...object,id:'peer'}]});
    chart.titleEditor.value='反弹受阻再考虑';chart.titleEditor.listeners.input({isComposing:false});
    assert.deepEqual(updates.at(-1).patch,{name:'反弹受阻再考虑'});assert.equal(chart.plan.objects[0].color,'#00ff88');assert.deepEqual(chart.plan.objects[0].points,peer.points);assert.equal(chart.plan.objects.length,2);
    chart.titleEditor.listeners.keydown(key('Escape'));assert.equal(chart.titleEditor.hidden,true);assert.equal(chart.plan.objects[0].name,'反弹受阻再考虑','Escape ends editing without rolling back text already shared');
    assert.equal(presence.at(-1).editing,false);
  });
});

test('Chinese composition is not saved halfway and Enter during composition cannot close the editor',()=>{
  harness(({chart,updates,key,names,textPoint})=>{
    const object=names();chart.plan={objects:[object]};chart.openTitleEditor(object,textPoint(object));
    chart.titleEditor.listeners.compositionstart({});chart.titleEditor.value='fan';chart.titleEditor.listeners.input({isComposing:true});chart.titleEditor.listeners.keydown(key('Enter',{isComposing:true,keyCode:229}));
    assert.equal(updates.length,0);assert.equal(chart.titleObjectId,object.id);
    chart.titleEditor.value='反弹';chart.titleEditor.listeners.compositionend({isComposing:false});assert.deepEqual(updates,[{id:object.id,patch:{name:'反弹'}}]);
    chart.titleEditor.listeners.keydown(key('Enter'));assert.equal(chart.titleObjectId,null);assert.equal(updates.length,1);
  });
});

test('blur flushes a final value once but untouched text never overwrites a collaborator rename',()=>{
  harness(({chart,updates,names,textPoint})=>{
    const object=names();chart.plan={objects:[object]};chart.openTitleEditor(object,textPoint(object));chart.titleEditor.value='开盘后观察';chart.titleEditor.listeners.blur({});
    assert.deepEqual(updates,[{id:object.id,patch:{name:'开盘后观察'}}]);assert.equal(chart.titleObjectId,null);
    const current=chart.plan.objects[0];chart.openTitleEditor(current,textPoint(current));chart.setPlan({objects:[{...current,name:'伙伴刚刚改了'}]});chart.titleEditor.listeners.blur({});
    assert.equal(updates.length,1);assert.equal(chart.plan.objects[0].name,'伙伴刚刚改了');
  });
});

test('locked, deleted, hidden or deselected objects cannot receive stale inline edits',()=>{
  harness(({chart,updates,notices,names,textPoint})=>{
    const object=names('zone');chart.plan={objects:[{...object,locked:true}]};chart.openTitleEditor(object,textPoint(object));assert.equal(chart.titleObjectId,undefined);assert.equal(notices.length,1);
    for(const change of ['delete','lock','hide','select-other']){
      chart.plan={objects:[object]};chart.openTitleEditor(object,textPoint(object));const input=chart.titleEditor;
      if(change==='select-other'){chart.selected='other';chart.render();}else chart.setPlan({objects:change==='delete'?[]:[{...object,...(change==='lock'?{locked:true}:{visible:false})}]});
      assert.equal(chart.titleObjectId,null,change);assert.equal(input.hidden,true);input.value='不得重新创建';input.listeners.input({});input.listeners.blur({});
      assert.equal(updates.length,0,change);
    }
  });
});

test('switching chart tools closes editing and single click on a name never mutates coordinates',()=>{
  harness(({chart,updates,pointer,names,textPoint})=>{
    const object=names('trend');chart.plan={objects:[object]};const original=structuredClone(object),point=textPoint(object);
    chart.down(pointer(point.x,point.y));chart.up(pointer(point.x,point.y));assert.deepEqual(object,original);assert.equal(updates.length,0);
    chart.openTitleEditor(object,point);chart.setTool('wave');assert.equal(chart.titleObjectId,null);assert.equal(chart.titleEditor.hidden,true);
  });
});

test('automatic price labels and wave numbers stay outside the name editor',()=>{
  harness(({chart,pointer,names,textPoint,make})=>{
    const segment=names('segment',{name:'水平线段'}),wave={id:'wave',kind:'wave',period:'m3',points:C.wavePoints([{time:10,price:107},{time:30,price:103},{time:70,price:106}])};
    chart.plan={objects:[segment,wave]};assert.equal(chart.nameLabel(segment),null);
    const point={x:chart.tx.x(80)+10,y:chart.tx.y(103)-6};chart.doubleClick({...pointer(point.x,point.y),target:{closest:selector=>selector==='[data-layer-price],[data-segment-price]'?{}:null}});
    assert.equal(chart.titleObjectId,undefined);
    const updates=[];chart.callbacks.update=(id,patch)=>updates.push({id,patch});chart.doubleClick(pointer(chart.tx.x(30),chart.tx.y(103),2000));
    assert.equal(updates[0].patch.points.length,2);assert.equal(chart.titleObjectId,undefined,'doubleclick still deletes a selected wave node');
    const layer=make('g');chart.drawObject(layer,segment);const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(layer);assert.equal(nodes.filter(node=>node.attrs['data-edit-name']).length,0);assert.ok(nodes.some(node=>node.attrs['data-segment-price']===segment.id));
  });
});

test('rendered editable labels expose name targets while prices remain separate and default segments stay uncluttered',()=>{
  harness(({chart,names,make})=>{
    for(const kind of ['text','zone','trend','reference','horizontal','segment']){
      const object=names(kind),layer=make('g');chart.drawObject(layer,object);const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(layer),labels=nodes.filter(node=>node.attrs['data-edit-name']===object.id);
      assert.equal(labels.length,1,kind);assert.equal(labels[0].textContent,object.name);
      if(kind==='horizontal')assert.equal(nodes.find(node=>node.attrs['data-layer-price']===object.id).textContent,'103');
      if(kind==='segment')assert.equal(nodes.find(node=>node.attrs['data-segment-price']===object.id).textContent,'103');
    }
  });
});
