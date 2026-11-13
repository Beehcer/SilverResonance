'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');

function fixture(run){
  const before={document:global.document,ResizeObserver:global.ResizeObserver,requestAnimationFrame:global.requestAnimationFrame};
  function make(tag){return{tag,attrs:{},style:{},children:[],listeners:{},hidden:false,capture:null,
    setAttribute(key,value){this.attrs[key]=String(value);},getAttribute(key){return this.attrs[key];},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},
    addEventListener(type,fn){this.listeners[type]=fn;},getBoundingClientRect(){return{left:0,top:0};},focus(){},
    setPointerCapture(id){this.capture=id;},hasPointerCapture(id){return this.capture===id;},releasePointerCapture(){this.capture=null;this.listeners.lostpointercapture?.();}};}
  try{
    global.document={createElement:make,createElementNS:(_,tag)=>make(tag)};global.ResizeObserver=class{observe(){}};global.requestAnimationFrame=undefined;
    const added=[],updates=[],chart=new C.Chart(Object.assign(make('div'),{clientWidth:500,clientHeight:300}),'m3',{add:object=>added.push(object),update:(id,patch)=>updates.push({id,patch})});
    const base=Date.parse('2026-11-12T01:00:00Z');
    chart.setData({symbol:'SHFE.rb2701',fresh:true,tick:1,periods:{m3:Array.from({length:32},(_,i)=>({time:base+i*C.INTERVAL.m3,open:100,high:106,low:94,close:101,volume:10,oi:100}))}},{objects:[]},true);
    const event=(x,y,extra={})=>({button:0,pointerId:4,clientX:x,clientY:y,timeStamp:1000,preventDefault(){},...extra});
    return run({chart,added,updates,event,base});
  }finally{Object.assign(global,before);}
}
const walk=node=>[node,...node.children.flatMap(walk)];
const styled={color:'#c044bb',width:3,line_style:'dashed',opacity:.35};
function artwork(chart,key){const group=walk(chart.svg).find(node=>node.attrs['data-object-artwork']===key);assert.ok(group,`rendered ${key}`);return{group,line:walk(group).find(node=>['line','polyline'].includes(node.tag))};}
function assertStyle(chart,key,style,{selected=false,thin=false}={}){
  const {group,line}=artwork(chart,key);assert.ok(line);assert.equal(group.attrs.opacity,String(style.opacity));assert.equal(line.attrs.stroke,style.color);
  assert.equal(line.attrs['stroke-dasharray'],style.line_style==='dashed'?'5 4':'');assert.equal(line.attrs['stroke-width'],String((style.width+(selected?1:0))/(thin?2:1)));
}

test('dragging a new trend, reference or segment uses the final line style before pointer release',()=>{
  for(const kind of ['trend','reference','segment'])fixture(({chart,event,added})=>{
    chart.setTool(kind,styled);chart.down(event(150,65));chart.move(event(260,125));assertStyle(chart,kind,styled,{thin:kind==='segment'});
    chart.up(event(260,125));assert.equal(added.length,1);for(const key of Object.keys(styled))assert.equal(added[0][key],styled[key],`${kind}.${key}`);
    chart.setPlan({objects:[{...added[0],id:'saved'}]});assertStyle(chart,'saved',styled,{thin:kind==='segment'});
  });
});

test('reference defaults to dashed only when the caller did not select a line style',()=>fixture(({chart,event,added})=>{
  for(const style of [undefined,'solid','dashed']){
    chart.setTool('reference',style?{line_style:style}:{});chart.down(event(150,65));chart.move(event(260,125));
    assertStyle(chart,'reference',{color:'#f1c40f',width:2,line_style:style||'dashed',opacity:1});chart.up(event(260,125));assert.equal(added.at(-1).line_style,style||'dashed');
  }
}));

test('path and wave drafts respond to style changes without losing points and commit the visible style',()=>{
  for(const kind of ['path','wave'])fixture(({chart,event,added})=>{
    chart.setTool(kind,styled);chart.down(event(150,65));chart.down(event(260,125,{timeStamp:2000}));assertStyle(chart,kind,styled);
    const points=structuredClone(chart.path),changed={...styled,color:'#00abce',width:1,line_style:'solid',opacity:0};chart.setDrawingStyle(changed);
    assert.deepEqual(chart.path,points);assertStyle(chart,kind,changed);assert.equal(chart.finishPath(),true);assert.equal(added.length,1);
    for(const key of Object.keys(changed))assert.equal(added[0][key],changed[key]);chart.setPlan({objects:[{...added[0],id:'saved'}]});assertStyle(chart,'saved',changed);
  });
});

test('horizontal placement and partial draft-style changes preserve all selected presentation properties',()=>fixture(({chart,event,added})=>{
  chart.setTool('horizontal',styled);chart.setDrawingStyle({opacity:.6});chart.down(event(200,80));const expected={...styled,opacity:.6};
  for(const key of Object.keys(expected))assert.equal(added[0][key],expected[key]);chart.setPlan({objects:[{...added[0],id:'saved'}]});assertStyle(chart,'saved',expected,{thin:true});
}));

test('editing existing line and forecast anchors retains object style despite opposite toolbar settings',()=>{
  for(const kind of ['horizontal','segment','trend','reference','path','wave'])fixture(({chart,event,updates,base})=>{
    const points=[{time:base+10*C.INTERVAL.m3,price:101},{time:base+20*C.INTERVAL.m3,price:105}],object={id:'existing',kind,name:'观察',period:'m3',...styled,points:kind==='horizontal'?points.slice(0,1):kind==='segment'?points.map(point=>({...point,price:101})):points};
    chart.setPlan({objects:[object]});chart.setTool('select',{color:'#ff0000',width:1,line_style:'solid',opacity:1});chart.selected=object.id;chart.render();
    const point=chart.objectPoints(object)[0];chart.down(event(point.x,point.y));chart.move(event(point.x+25,point.y+18));assert.equal(chart.drag.kind,'object');assert.ok(chart.drag.preview);
    chart.setDrawingStyle({color:'#00ff00',width:2,line_style:'solid',opacity:.9});assertStyle(chart,object.id,styled,{selected:true,thin:['horizontal','segment'].includes(kind)});
    chart.up(event(point.x+25,point.y+18));assert.equal(updates.length,1);assert.deepEqual(Object.keys(updates[0].patch),['points']);for(const key of Object.keys(styled))assert.equal(chart.plan.objects[0][key],styled[key]);
  });
});
