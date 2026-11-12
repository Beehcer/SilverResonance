'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');

function fixture(run,period='m3'){
  const before={document:global.document,ResizeObserver:global.ResizeObserver,requestAnimationFrame:global.requestAnimationFrame};
  function make(tag){return{tag,attrs:{},style:{},children:[],listeners:{},hidden:false,value:'',capture:null,captureCount:0,
    setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return this.attrs[k];},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},
    addEventListener(type,fn){this.listeners[type]=fn;},getBoundingClientRect(){return{left:0,top:0};},focus(){this.focused=true;},select(){},remove(){this.removed=true;},setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;},
    setPointerCapture(id){this.capture=id;this.captureCount++;},hasPointerCapture(id){return this.capture!==null&&this.capture===id;},releasePointerCapture(){this.capture=null;this.listeners.lostpointercapture?.();}};}
  const changes=[],presence=[],drafts=[],activations=[],contexts=[],selected=[],levelEvents=[],viewEvents=[];
  try{
    global.document={createElement:make,createElementNS:(_,tag)=>make(tag)};global.ResizeObserver=class{observe(){}};global.requestAnimationFrame=undefined;
    const host=Object.assign(make('div'),{clientWidth:500,clientHeight:300});host.style.touchAction='manipulation';
    const chart=new C.Chart(host,period,{add:object=>changes.push({kind:'add',object}),update:(id,patch)=>changes.push({kind:'update',id,patch}),deleteObject:id=>changes.push({kind:'delete',id}),placeLevel:(...args)=>changes.push({kind:'place',args}),dragLevel:(field,price,meta)=>{levelEvents.push(meta.phase);if(meta.phase==='end')changes.push({kind:'level',field,price});},presence:value=>presence.push(value),draftChanged:value=>drafts.push(value),activate:value=>activations.push(value),context:(...args)=>contexts.push(args),select:value=>selected.push(value),viewChanged:()=>viewEvents.push(true)});
    const base=Date.parse('2026-11-12T01:00:00Z'),data=(count=32)=>({symbol:'SHFE.rb2701',fresh:true,tick:1,history_token:'history-'+count,periods:{[period]:Array.from({length:count},(_,i)=>({time:base+i*C.INTERVAL[period],open:100+i%3,high:106,low:94,close:101+i%4,volume:10+i,oi:100+i}))}});
    const object=(kind='wave',extra={})=>({id:kind,kind,period,name:kind==='text'?'等待回抽\n受阻再观察':'观察',color:'#f1c40f',points:C.wavePoints([{time:base+10*C.INTERVAL[period],price:101},{time:base+15*C.INTERVAL[period],price:105},{time:base+20*C.INTERVAL[period],price:99}]).slice(0,['text','horizontal'].includes(kind)?1:['zone','trend','reference','segment'].includes(kind)?2:3),...extra});
    chart.setData(data(),{objects:[]},true);
    const event=(x=200,y=80,extra={})=>({button:0,pointerId:4,pointerType:'touch',clientX:x,clientY:y,timeStamp:1000,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},stopPropagation(){},...extra});
    const emit=(type,ev=event())=>{(chart.svg.listeners[type]||host.listeners[type])?.(ev);return ev;};
    return run({chart,host,make,data,object,event,emit,changes,presence,drafts,activations,contexts,selected,levelEvents,viewEvents});
  }finally{Object.assign(global,before);}
}
const walk=node=>[node,...node.children.flatMap(walk)];

test('read-only API cancels a wave draft, releases capture and restores desktop tool behaviour',()=>fixture(({chart,host,event,emit,changes,drafts})=>{
  assert.equal(chart.readOnly,false);chart.setTool('wave');emit('pointerdown',event(180,80));emit('pointerdown',event(230,100,{timeStamp:1800}));assert.equal(chart.path.length,2);
  chart.setReadOnly(true);assert.equal(chart.path.length,0);assert.equal(drafts.at(-1).count,0);assert.equal(changes.length,0);assert.equal(chart.tool,'select');assert.equal(chart.svg.capture,null);
  assert.equal(host.attrs['data-read-only'],'true');assert.equal(host.style.touchAction,'pan-y');assert.equal(chart.svg.style.touchAction,'pan-y');assert.equal(chart.touchSurface.style.touchAction,'pan-y');
  for(const tool of ['horizontal','segment','trend','reference','zone','text','wave','path','planned-entry','planned-stop','pan']){chart.setTool(tool);assert.equal(chart.tool,'select');}
  chart.setReadOnly(false);assert.equal(host.style.touchAction,'manipulation');assert.equal(chart.svg.style.touchAction,'none');assert.equal(chart.tool,'select');
  chart.setTool('horizontal');emit('pointerdown',event());assert.equal(changes.length,1);assert.equal(changes[0].object.kind,'horizontal');
}));

test('read-only transition cancels every provisional drawing and plan placement without committing',()=>{
  for(const tool of ['trend','reference','zone','segment','planned-entry','planned-stop'])fixture(({chart,emit,event,changes,data})=>{
    chart.setTool(tool);emit('pointerdown',event(150,70));emit('pointermove',event(250,110));assert.ok(chart.drag,tool);
    const next=data(33);chart.setData(next,chart.plan);assert.equal(chart.bars.length,32,'market is deferred during the gesture');
    chart.setReadOnly(true);assert.equal(chart.drag,null,tool);assert.equal(chart.svg.capture,null,tool);assert.equal(chart.bars.length,33);assert.equal(chart.pendingMarket,null);
    emit('pointerup',event(250,110));assert.deepEqual(changes,[],tool);
  });
});

test('transition rolls back local plan/object drag preview and keeps authoritative plan geometry',()=>fixture(({chart,object,emit,event,changes,levelEvents})=>{
  const wave=object(),plan={objects:[wave],direction:'short',planned_entry_price:103,stop_price:107};chart.setPlan(plan);chart.selected=wave.id;chart.selectedAnchor=1;
  let point=chart.objectPoints(wave)[1];emit('pointerdown',event(point.x,point.y));emit('pointermove',event(point.x+20,point.y+20));assert.equal(chart.drag?.kind,'object');
  chart.setReadOnly(true);emit('pointerup',event(point.x+20,point.y+20));assert.equal(chart.plan,plan);assert.deepEqual(changes,[]);
  chart.setReadOnly(false);const level=chart.levelGeometry().find(item=>item.field==='planned_entry_price');emit('pointerdown',event(chart.plot.x+30,level.y,{timeStamp:2000}));assert.equal(chart.drag?.kind,'plan-level');emit('pointermove',event(chart.plot.x+30,level.y+20));
  chart.setReadOnly(true);assert.equal(levelEvents.at(-1),'cancel');assert.equal(levelEvents.includes('end'),false);assert.equal(chart.displayPlan().planned_entry_price,103);assert.deepEqual(changes,[]);
}));

test('touch exploration and wheel keep native page scrolling while taps show crosshair and presence',()=>fixture(({chart,emit,event,presence,activations,selected,changes})=>{
  chart.setReadOnly(true);const view=structuredClone(chart.view),captures=chart.svg.captureCount;
  for(const type of ['pointerdown','pointermove','pointerup']){const result=emit(type,event(type==='pointermove'?220:200,80));assert.equal(result.defaultPrevented,false,type);}
  assert.equal(chart.svg.captureCount,captures);assert.equal(chart.drag,null);assert.deepEqual(chart.view,view);assert.deepEqual(selected,[]);assert.deepEqual(changes,[]);assert.deepEqual(activations,['m3']);
  assert.ok(walk(chart.svg).some(node=>node.attrs['data-crosshair']==='vertical'));assert.equal(presence.at(-1).editing,false);assert.equal(presence.at(-1).object_id,null);
  const wheel=emit('wheel',event(200,80,{deltaY:100}));assert.equal(wheel.defaultPrevented,false);assert.deepEqual(chart.view,view);
  chart.zoom(1.5);assert.notEqual(chart.view.end-chart.view.start,view.end-view.start);chart.fit();assert.equal(chart.followLatest,true);
}));

test('all mutation entry points reject stale selections, direct tool state and keyboard/context events',()=>fixture(({chart,object,event,emit,changes,contexts})=>{
  const wave=object();chart.setPlan({objects:[wave]});chart.setReadOnly(true);chart.selected=wave.id;chart.selectedAnchor=1;const point=chart.objectPoints(wave)[1];
  chart.deleteSelection();chart.deleteWavePoint();chart.insertWavePoint(wave.id,point,0);chart.editPathAtHit({object:wave,anchor:1},point);chart.adjustPathControl({id:wave.id,index:1,direction:-1});chart.adjustSelectedPath(1);
  chart.tool='wave';chart.path=structuredClone(wave.points);assert.equal(chart.finishPath(event()),false);assert.equal(chart.path.length,0);
  chart.tool='horizontal';emit('pointerdown',event(point.x,point.y));emit('pointerup',event(point.x,point.y));chart.tool='select';emit('dblclick',event(point.x,point.y));emit('contextmenu',event(point.x,point.y,{button:2}));
  for(const key of ['Delete','Backspace','Enter','Escape','ArrowLeft'])emit('keydown',event(0,0,{key}));
  assert.deepEqual(changes,[]);assert.deepEqual(contexts,[]);assert.equal(chart.plan.objects[0],wave);
}));

test('opening either text editor is blocked and delayed events from pre-transition editors cannot save',()=>fixture(({chart,object,event,changes})=>{
  const text=object('text');chart.setPlan({objects:[text]});chart.openTitleEditor(text);const title=chart.titleEditor;assert.equal(title.hidden,false);
  chart.setReadOnly(true);assert.equal(title.hidden,true);title.value='不得在只读模式提交';title.listeners.input({});title.listeners.compositionend({});title.listeners.blur({});title.listeners.keydown(event(0,0,{key:'Enter'}));
  chart.openTitleEditor(text);chart.openTextEditor(text.points[0],{x:50,y:50});assert.equal(chart.titleObjectId,null);assert.equal(chart.textEditor,undefined);assert.deepEqual(changes,[]);
  chart.setReadOnly(false);chart.setTool('text');chart.down(event(200,80));const form=chart.textEditor;assert.equal(chart.svg.capture,4);chart.textInput.value='不得在切换后新增';
  chart.setReadOnly(true);assert.equal(form.hidden,true);assert.equal(chart.svg.capture,null);form.listeners.submit(event());form.listeners.keydown(event(0,0,{key:'Enter'}));assert.deepEqual(changes,[]);
}));

test('remote objects, multiline labels, prices and peer previews remain live without editable selection affordances',()=>fixture(({chart,object,data})=>{
  const wave=object(),text=object('text'),trend=object('trend'),plan={objects:[wave,text,trend],direction:'short',planned_entry_price:103,stop_price:107};chart.setData(data(),plan);chart.setReadOnly(true);
  for(const current of plan.objects){chart.selected=current.id;chart.selectedAnchor=1;chart.setPlan({...plan,objects:plan.objects.map(item=>item.id===current.id?{...item,opacity:0.2}:item)});const nodes=walk(chart.svg);
    assert.equal(nodes.some(node=>node.attrs['data-object-anchor']),false);assert.equal(nodes.some(node=>node.attrs['data-object-selection']),false);assert.equal(nodes.some(node=>node.attrs['data-text-selection']),false);assert.equal(nodes.some(node=>node.attrs['data-node-selected']==='true'),false);assert.equal(nodes.some(node=>node.attrs.style==='cursor:text'),false);
  }
  chart.setPresence([{id:'peer',name:'伙伴',period:'m3',object_id:wave.id,cursor:wave.points[1],preview_object:{...wave,name:'伙伴正在绘制'}}]);
  const nodes=walk(chart.svg);assert.ok(nodes.some(node=>node.attrs['data-peer-cursor']==='peer'));assert.ok(nodes.some(node=>node.attrs['data-peer-preview']==='peer'));assert.ok(nodes.some(node=>node.attrs['data-peer-selection']==='peer'));
  assert.ok(nodes.some(node=>node.attrs['data-plan-overlay']==='planned_entry_price'));assert.ok(nodes.some(node=>node.tag==='tspan'&&node.textContent==='受阻再观察'));
  const updated={...text,name:'伙伴更新第一行\n第二行'};chart.setData(data(33),{...plan,objects:[updated]});assert.equal(chart.bars.length,33);assert.ok(walk(chart.svg).some(node=>node.textContent==='伙伴更新第一行'));
}));

test('readonly Escape clears inspection without consuming keyboard page navigation',()=>fixture(({chart,emit,event})=>{
  chart.setReadOnly(true);emit('pointerdown',event());assert.ok(chart.crosshair);const arrow=emit('keydown',event(0,0,{key:'ArrowDown'}));assert.equal(arrow.defaultPrevented,false);
  const escape=emit('keydown',event(0,0,{key:'Escape'}));assert.equal(escape.defaultPrevented,false);assert.equal(chart.crosshair,null);
}));
