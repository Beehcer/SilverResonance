'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const C=require('./static/workspace-chart.js');
const M=require('./static/workspace-merge.js');

test('blank price remains unknown and invalid numeric values never become prices',()=>{
  for(const value of ['', '  ', null, undefined, 'bad', Infinity, NaN, 0, -1]) assert.equal(C.priceNumber(value),null);
  assert.equal(C.priceNumber('0.0025'),.0025);
  assert.equal(C.priceNumber('7200.5'),7200.5);
});
test('fractional contract ticks snap without binary noise',()=>{
  assert.equal(C.snap(123.349999999, .2),123.4);
  assert.equal(C.snap(0.00031,.0001),.0003);
  assert.equal(C.snap(7213,5),7215);
  assert.equal(C.snap(123.45,0),123.45);
});
test('chart transforms preserve exact price and time after zoom/pan, including fractional ticks',()=>{
  const view={start:1794211200000,end:1794211200000+1800000*77,min:7190.25,max:7280.5};
  const plot={x:12,y:19,w:709,h:311},transform=C.transform(view,plot);
  for(const price of [view.min,7200.5,view.max])assert.ok(Math.abs(transform.price(transform.y(price))-price)<1e-9);
  for(const time of [view.start,view.start+1800000,view.end])assert.ok(Math.abs(transform.time(transform.x(time))-time)<.001);
  assert.equal(transform.x(view.start),plot.x);
  assert.equal(transform.y(view.max),plot.y);
});
test('session gaps compress without changing candle centers or inverse time coordinates',()=>{
  const bars=[{time:0},{time:180000},{time:360000},{time:1000000}],plot={x:0,y:0,w:300,h:100},tx=C.transform({start:0,end:1000000,min:90,max:110},plot,bars,'m3');
  assert.equal(tx.x(0),0);assert.equal(tx.x(180000),100);assert.equal(tx.x(360000),200);assert.equal(tx.x(1000000),300);
  assert.equal(tx.x(540000),228.125);
  for(const value of [0,180000,360000,540000,1000000,1100000])assert.ok(Math.abs(tx.time(tx.x(value))-value)<.001);
});

test('visible bars use the viewport span rather than the full history span',()=>{
  const bars=Array.from({length:5000},(_,index)=>({time:index*C.INTERVAL.m3})),view={start:4900*C.INTERVAL.m3,end:5000*C.INTERVAL.m3,min:90,max:110},tx=C.transform(view,{x:0,y:0,w:300,h:100},bars,'m3');
  const span=tx.x(bars.at(-1).time)-tx.x(bars.at(-65).time);assert.ok(span>190&&span<200);
});

test('zones preserve exact numeric boundaries regardless of drag direction',()=>{
  const points=[{time:5000,price:7211.2},{time:1000,price:7199.4}];
  assert.deepEqual(C.zoneBounds({points}),{low:7199.4,high:7211.2,start:1000,end:5000});
  assert.deepEqual(C.zoneBounds({points:points.slice().reverse()}),C.zoneBounds({points}));
  assert.equal(C.zoneBounds({points:[{time:0,price:0},points[0]]}),null);
  assert.equal(C.zoneBounds({points:[{time:Infinity,price:1},points[0]]}),null);
});
test('flat valid market has a drawable scale; absent data never generates mock candles',()=>{
  assert.equal(C.fitView([], 'm30'),null);
  assert.equal(C.fitView([{dt:'bad',open:4,high:4,low:4,close:4}], 'm30'),null);
  const time='2026-11-09T09:30:00+08:00';
  const view=C.fitView([{dt:time,open:4,high:4,low:4,close:4}], 'm30',[3.5,5]);
  assert.ok(view.min<3.5&&view.max>5);
  assert.ok(view.end>view.start);
  assert.equal(C.timestamp(time),Date.parse('2026-11-09T01:30:00Z'));
});
test('wheel zoom changes both time and price axes around the cursor',()=>{
  const view={start:0,end:1800000*30,min:90,max:110};
  const zoomed=C.zoomView(view,'m3',1.25,{time:60000,price:104});
  assert.ok(zoomed.end-zoomed.start<view.end-view.start);
  assert.ok(zoomed.max-zoomed.min<view.max-view.min);
  const tx=C.transform(view,{x:0,y:0,w:500,h:200}),next=C.transform(zoomed,{x:0,y:0,w:500,h:200});
  assert.ok(Math.abs(next.time(tx.x(60000))-60000)<.001);
  assert.ok(Math.abs(next.price(tx.y(104))-104)<1e-9);
  const out=C.zoomView(view,'m3',.8);
  assert.ok(out.end-out.start>view.end-view.start);
  assert.ok(out.max-out.min>view.max-view.min);
});

test('line hit geometry supports endpoints and zero-length segments',()=>{
  assert.equal(C.segmentDistance({x:15,y:0},{x:0,y:0},{x:10,y:0}),5);
  assert.equal(C.segmentDistance({x:5,y:3},{x:0,y:0},{x:10,y:0}),3);
  assert.equal(C.segmentDistance({x:3,y:4},{x:0,y:0},{x:0,y:0}),5);
});

test('path hover editing inserts at the hovered segment and never removes below two anchors',()=>{
  const points=[{time:0,price:100},{time:100,price:110},{time:200,price:105}];
  const inserted=C.editPathPoints(points,0,1,.2,180000);
  assert.equal(inserted.length,4);
  assert.deepEqual(inserted[1],{time:50,price:105});
  assert.equal(points.length,3);
  const removed=C.editPathPoints(inserted,0,-1,.2,180000);
  assert.deepEqual(removed,points);
  const minimum=C.editPathPoints(points.slice(0,2),0,-1,.2,180000);
  assert.equal(minimum.length,2);
  const appended=C.editPathPoints(points,null,1,.2,100);
  assert.equal(appended.length,4);
  assert.equal(appended.at(-1).time,300);
});

test('paths map only to smaller periods and reduce opacity by sixty percent per level',()=>{
  assert.equal(C.pathPeriodOpacity('day','day'),1);
  assert.equal(C.pathPeriodOpacity('day','m30'),.4);
  assert.ok(Math.abs(C.pathPeriodOpacity('day','m3')-.16)<1e-12);
  assert.equal(C.pathPeriodOpacity('m30','m3'),.4);
  assert.equal(C.pathPeriodOpacity('m3','day'),null);
  assert.equal(C.pathPeriodOpacity('m3','m30'),null);
  const path={kind:'path',period:'day'};
  assert.equal(C.visibleOnPeriod(path,'m3'),true);
  assert.equal(C.visibleOnPeriod({...path,period:'m3'},'day'),false);
  assert.equal(C.visibleOnPeriod({kind:'trend',period:'m3'},'day'),false);
});

test('trend and reference geometry extends to both plot boundaries',()=>{
  const segment=C.infiniteSegment({x:20,y:80},{x:60,y:40},{x:0,y:0,w:100,h:100});
  assert.equal(segment[0].x,0);assert.equal(segment[1].x,100);
  assert.equal(segment[0].y,100);assert.equal(segment[1].y,0);
  const vertical=C.infiniteSegment({x:30,y:10},{x:30,y:80},{x:0,y:0,w:100,h:100});
  assert.deepEqual(vertical,[{x:30,y:0},{x:30,y:100}]);
});

test('remote edits to another object merge while the local unfinished object stays intact',()=>{
  const remote={revision:4,title:'伙伴改的标题',stop_price:99,objects:[{id:'a',name:'remote a'},{id:'b',name:'remote b'}]};
  const pending={changes:{stop_price:98},upsert_objects:[{id:'a',name:'my a'}],delete_object_ids:[]};
  const merged=C.mergePending(remote,pending);
  assert.equal(merged.title,'伙伴改的标题');assert.equal(merged.stop_price,98);
  assert.deepEqual(merged.objects,[{id:'a',name:'my a'},{id:'b',name:'remote b'}]);
  assert.equal(remote.objects[0].name,'remote a');
});
test('delete and newly drawn objects survive a simultaneous remote plan update',()=>{
  const pending={changes:{},upsert_objects:[{id:'c',name:'new'}],delete_object_ids:['a']};
  assert.deepEqual(C.mergePending({objects:[{id:'a'},{id:'b'}]},pending).objects,[{id:'b'},{id:'c',name:'new'}]);
});
test('a successful sync clears only the exact edits sent, preserving typing and drag changes made in flight',()=>{
  const sent={changes:{title:'first',stop_price:98},upsert_objects:[{id:'a',points:[1]},{id:'b',points:[2]}],delete_object_ids:['x']};
  const current={changes:{title:'second',stop_price:98,notes:'new note'},upsert_objects:[{id:'a',points:[3]},{id:'b',points:[2]},{id:'c',points:[4]}],delete_object_ids:['x','y']};
  assert.deepEqual(C.pendingAfterAck(current,sent),{changes:{title:'second',notes:'new note'},upsert_objects:[{id:'a',points:[3]},{id:'c',points:[4]}],delete_object_ids:['y']});
});

test('choosing a peer version resolves only the conflicts and retains unrelated local edits',()=>{
  const pending={changes:{title:'mine',stop_price:97,notes:'keep'},upsert_objects:[{id:'a'},{id:'b'}],delete_object_ids:['c','d']};
  assert.deepEqual(C.adoptRemoteConflicts(pending,['title','objects:a','objects:c']),{changes:{stop_price:97,notes:'keep'},upsert_objects:[{id:'b'}],delete_object_ids:['d']});
});

test('planned entry and protective stop use mirrored sides without changing the chosen direction',()=>{
  assert.equal(C.validStopSide('long',100,99),true);
  assert.equal(C.validStopSide('long',100,101),false);
  assert.equal(C.validStopSide('short',100,101),true);
  assert.equal(C.validStopSide('short',100,99),false);
  for(const direction of ['long','short'])assert.equal(C.validStopSide(direction,100,100),false);
  for(const stop of [null,'',NaN,Infinity,0])assert.equal(C.validStopSide('long',100,stop),false);
  assert.equal(C.planLevelStatus({direction:'long',planned_entry_price:100}).state,'missing-stop');
  assert.equal(C.planLevelStatus({direction:'long',planned_entry_price:100,stop_price:101}).state,'invalid-stop');
  assert.match(C.planLevelStatus({direction:'short',planned_entry_price:100,stop_price:99}).text,/方向仍为做空/);
});

test('price-axis labels and wide line bands both hit the exact editable plan field',()=>{
  const plot={x:12,y:20,w:400,h:200},width=500,tx=C.transform({start:0,end:100,min:90,max:110},plot);
  const levels=C.planLevelGeometry({planned_entry_price:100,stop_price:95},tx,plot,width);
  const entry=levels.find(level=>level.field==='planned_entry_price'),stop=levels.find(level=>level.field==='stop_price');
  assert.equal(C.hitPlanLevel({x:100,y:entry.y+11},levels,plot,width).field,'planned_entry_price');
  assert.equal(C.hitPlanLevel({x:100,y:entry.y+13},levels,plot,width),null);
  assert.equal(C.hitPlanLevel({x:stop.label.x+30,y:stop.label.y+14},levels,plot,width).field,'stop_price');
  assert.equal(C.hitPlanLevel({x:width+20,y:entry.y},levels,plot,width),null);
  assert.ok(stop.label.h>=28);
});

test('overlapping plan lines retain separate draggable price-axis labels',()=>{
  const plot={x:12,y:20,w:400,h:200},tx=C.transform({start:0,end:100,min:90,max:110},plot);
  const levels=C.planLevelGeometry({planned_entry_price:100,stop_price:100},tx,plot,500);
  assert.ok(levels[1].label.y>=levels[0].label.y+levels[0].label.h);
  for(const level of levels)assert.equal(C.hitPlanLevel({x:level.label.x+25,y:level.label.y+14},levels,plot,500).field,level.field);
});

test('a level drag snaps ticks and commits only its own field, never the trigger or direction',()=>{
  const drag=C.moveLevelDrag(C.beginLevelDrag('planned_entry_price',100,.2),101.3499999,.2);
  assert.deepEqual(C.levelCommit(drag),{planned_entry_price:101.4});
  const stopped=C.moveLevelDrag(C.beginLevelDrag('stop_price',99,.2),103.31,.2);
  assert.deepEqual(C.levelCommit(stopped),{stop_price:103.4});
  assert.deepEqual(C.levelCommit(stopped,true),{});
  assert.deepEqual(C.levelCommit(C.beginLevelDrag('stop_price',99,.2)),{});
  assert.equal(C.beginLevelDrag('trigger_price',100,.2),null);
  assert.equal(C.beginLevelDrag('direction',100,.2),null);
});

function chartHarness(plan={direction:'long',planned_entry_price:100,stop_price:95,trigger_price:102,objects:[]}){
  const oldDocument=global.document,oldObserver=global.ResizeObserver;
  const node={style:{},listeners:{},setAttribute(){},append(){},addEventListener(type,handler){this.listeners[type]=handler;},getBoundingClientRect(){return{left:0,top:0};},setPointerCapture(id){this.capture=id;},hasPointerCapture(id){return this.capture===id;},releasePointerCapture(){this.capture=null;this.listeners.lostpointercapture?.();}};
  const host={style:{},clientWidth:500,clientHeight:250,listeners:{},replaceChildren(){},focus(){},addEventListener(type,handler){this.listeners[type]=handler;}};
  const events={levels:[],placed:[],presence:[],context:[],cancelled:0};
  let chart;
  try{
    global.document={createElementNS(){return node;}};global.ResizeObserver=class{observe(){}};
    chart=new C.Chart(host,'m3',{dragLevel:(field,price,meta)=>events.levels.push({field,price,phase:meta.phase}),placeLevel:(field,price)=>events.placed.push({field,price}),presence:value=>events.presence.push(value),context:(...args)=>events.context.push(args),cancelTool:()=>events.cancelled++});
  }finally{global.document=oldDocument;global.ResizeObserver=oldObserver;}
  chart.render=()=>{};chart.plan=structuredClone(plan);chart.tick=.2;chart.view={start:0,end:100,min:90,max:110};chart.plot={x:12,y:20,w:400,h:200};chart.tx=C.transform(chart.view,chart.plot);
  const pointer=(x,y)=>({button:0,clientX:x,clientY:y,pointerId:1,detail:1,preventDefault(){}});
  return{chart,node,host,events,pointer};
}

test('large histories only select visible bars and unchanged quotes skip rendering',()=>{
  const{chart}=chartHarness();chart.view={start:0,end:C.INTERVAL.m3*500,min:90,max:110};chart.bars=Array.from({length:5000},(_,index)=>({time:index*C.INTERVAL.m3,open:100,high:102,low:98,close:101}));
  assert.ok(chart.visibleBars().length<600);
  const base=1794200000000,bars=Array.from({length:30},(_,index)=>({dt:new Date(base+index*180000).toISOString(),open:100,high:102,low:98,close:101,volume:10,oi:20}));
  let renders=0;chart.render=()=>renders++;
  chart.setData({tick:1,fresh:true,periods:{m3:bars}},chart.plan,true);
  const afterFirst=renders;chart.setData({tick:1,fresh:true,periods:{m3:bars}},chart.plan);
  assert.equal(renders,afterFirst);
});

test('pointer rendering coalesces into one animation frame',()=>{
  const{chart}=chartHarness(),previous=globalThis.requestAnimationFrame,frames=[];
  try{globalThis.requestAnimationFrame=callback=>{frames.push(callback);return frames.length;};chart.scheduleRender();chart.scheduleRender();assert.equal(frames.length,1);frames[0]();assert.equal(chart.renderFrame,null);
  }finally{globalThis.requestAnimationFrame=previous;}
});

test('feed status changes redraw unchanged bars and separate idle from disconnection',()=>{
  const {chart}=chartHarness(),bars=[{dt:'2026-11-11T11:27:00+08:00',open:100,high:102,low:98,close:101}],previous=global.document;
  let renders=0;chart.render=()=>renders++;
  const market={tick:1,source:'live',fresh:false,feed_status:'idle',periods:{m3:bars}};
  chart.setData(market,chart.plan,true);const initial=renders;
  chart.setData({...market,feed_status:'disconnected'},chart.plan);
  assert.ok(renders>initial);
  const makeNode=tag=>({tag,attrs:{},style:{},setAttribute(k,v){this.attrs[k]=v;},append(){},replaceChildren(){}});
  try{
    for(const [status,label] of [['idle','已连接 · 暂无新报价'],['disconnected','连接中断']]){
      const created=[];chart.svg=makeNode('svg');chart.market={...market,feed_status:status};
      global.document={createElementNS(ns,tag){const node=makeNode(tag);created.push(node);return node;}};
      C.Chart.prototype.render.call(chart);
      assert.ok(created.some(node=>node.textContent===label));
      assert.ok(!created.some(node=>node.textContent==='行情未就绪'));
    }
  }finally{global.document=previous;}
});

test('snapping locks time to the candle center and price to the nearest OHLC anchor',()=>{
  const{chart}=chartHarness();chart.bars=[{time:40,open:100,high:104,low:96,close:102}];
  const value=chart.toValue({x:chart.tx.x(40)+5,y:chart.tx.y(104)+8});
  assert.deepEqual(value,{time:40,price:104});
  const distant=chart.toValue({x:chart.tx.x(40)+2,y:chart.tx.y(90)});
  assert.deepEqual(distant,{time:40,price:96});
  chart.tick=1;chart.bars=[{time:40,open:100.2,high:104.4,low:96.2,close:102.3}];
  assert.equal(chart.toValue({x:chart.tx.x(40),y:chart.tx.y(104.4)}).price,104.4);
});

test('market refreshes preserve a customized zoom until the user resets the view',()=>{
  const{chart}=chartHarness(),base=1794200000000;
  const bars=offset=>Array.from({length:30},(_,index)=>({dt:new Date(base+index*180000).toISOString(),open:100+offset,high:102+offset,low:98+offset,close:101+offset,volume:10,oi:20}));
  chart.setData({tick:1,periods:{m3:bars(0)}},chart.plan,true);
  chart.zoom(1.11);
  const customized=structuredClone(chart.view);
  chart.setData({tick:1,periods:{m3:bars(3)}},chart.plan);
  assert.deepEqual(chart.view,customized);
  assert.equal(chart.viewCustomized,true);
  chart.fit();
  assert.equal(chart.viewCustomized,false);
  assert.notDeepEqual(chart.view,customized);
});

test('live updates keep path anchors fixed and extend the existing view without refitting',()=>{
  const{chart}=chartHarness(),base=1794200000000;
  const makeBars=()=>Array.from({length:30},(_,index)=>({dt:new Date(base+index*180000).toISOString(),open:100,high:102,low:98,close:101,volume:10,oi:20}));
  chart.plan.objects=[{id:'route',kind:'path',period:'m3',points:[{time:base+500000,price:100},{time:base+900000,price:102}]}];
  const anchors=structuredClone(chart.plan.objects[0].points);
  chart.setData({tick:1,periods:{m3:makeBars()}},chart.plan,true);
  const initial=structuredClone(chart.view);
  chart.setData({tick:1,periods:{m3:makeBars()}},chart.plan);
  assert.deepEqual(chart.view,initial);
  const appended=makeBars();appended.push({dt:new Date(base+30*180000).toISOString(),open:101,high:103,low:99,close:102,volume:11,oi:21});
  chart.setData({tick:1,periods:{m3:appended}},chart.plan);
  assert.equal(chart.view.start,initial.start+180000);
  assert.equal(chart.view.end,initial.end+180000);
  assert.deepEqual(chart.plan.objects[0].points,anchors);
});

test('fit ignores distant drawings by default but can include them explicitly',()=>{
  const{chart}=chartHarness(),base=1794200000000;
  const bars=Array.from({length:20},(_,index)=>({dt:new Date(base+index*180000).toISOString(),open:100,high:102,low:98,close:101}));
  const oldTime=base-180000*1000,plan={...chart.plan,objects:[{id:'far',kind:'horizontal',period:'all',visible:true,points:[{time:oldTime,price:1000000}]}]};
  chart.setData({tick:1,periods:{m3:bars}},plan,true);
  assert.ok(chart.view.max<1000);
  chart.fit(true);
  assert.ok(chart.view.max>=1000000);
  assert.ok(chart.view.start<oldTime);
});

test('right clicking a plan line reports its exact field instead of a drawing object',()=>{
  const{chart,events,pointer}=chartHarness();
  chart.contextMenu(pointer(100,chart.tx.y(95)));
  assert.equal(events.context.length,1);
  assert.equal(events.context[0][0],null);
  assert.deepEqual(events.context[0][2],{field:'stop_price'});
  chart.contextMenu(pointer(100,chart.tx.y(100)));
  assert.deepEqual(events.context[1][2],{field:'planned_entry_price'});
});

test('right press opens the object menu before SVG replacement and the later native event does not retarget it',()=>{
  const{chart,node,host,events,pointer}=chartHarness({objects:[{id:'label',kind:'text',period:'m3',text_side:'left',name:'左侧文字',points:[{time:60,price:104}]}]});
  const target={closest:selector=>selector==='[data-chart-object]'?{getAttribute:()=> 'label'}:null};
  const event={...pointer(150,chart.tx.y(104)),button:2,type:'pointerdown',timeStamp:100,target};
  node.listeners.pointerdown(event);
  assert.equal(events.context[0][0],'label');assert.equal(chart.drag,null);assert.equal(chart.selectedAnchor,null);
  host.listeners.contextmenu({...event,type:'contextmenu',timeStamp:130,target:null});
  assert.equal(events.context.length,1);
  host.listeners.contextmenu({...event,type:'contextmenu',timeStamp:1500,target:null});
  assert.equal(events.context.length,2);assert.equal(events.context[1][0],null);
});

test('visible drawing labels select their actual layer even outside line geometry or near plan levels',()=>{
  for(const kind of ['text','horizontal','segment','trend','zone','wave']){
    const{chart,events,pointer}=chartHarness();
    chart.plan.objects=[{id:kind,kind,period:'m3',points:[{time:50,price:102},{time:60,price:104}]}];
    const target={closest:selector=>selector==='[data-chart-object]'?{getAttribute:()=>kind}:null};
    chart.contextMenu({...pointer(100,chart.tx.y(95)),target});
    assert.equal(events.context[0][0],kind);
  }
});

test('forecast right-click wins at a plan-line crossing and clears a previously highlighted point',()=>{
  const{chart,events,pointer}=chartHarness();chart.plan.objects=[{id:'wave',kind:'wave',period:'m3',points:[{time:30,price:98},{time:60,price:102}]}];chart.selectedAnchor=1;
  chart.contextMenu(pointer(chart.tx.x(45),chart.tx.y(100)));
  assert.equal(events.context[0][0],'wave');assert.equal(chart.selectedAnchor,null);
  chart.tool='trend';chart.contextMenu(pointer(100,chart.tx.y(95)));
  assert.equal(events.context[1][2].field,'stop_price');
});

test('plan paths keep free coordinates instead of snapping to candle OHLC',()=>{
  const{chart,pointer}=chartHarness();chart.bars=[{time:40,open:100,high:104,low:96,close:102}];chart.setTool('path');
  const point={x:chart.tx.x(40)+2,y:chart.tx.y(101.3)};chart.down(pointer(point.x,point.y));
  assert.deepEqual(chart.path.map(({time,price})=>({time,price})),[chart.toValue(point,false)]);
  assert.equal(chart.path[0].chart_anchor.base_time,40);
  assert.notEqual(chart.path[0].price,104);
});

test('path controls retain a generous pointer target around the compact button group',()=>{
  const{chart}=chartHarness();chart.pathControl={id:'route',segment:0,x:100,y:100,w:40,h:17};
  assert.equal(chart.hitPathControl({x:98,y:100}).direction,-1);
  assert.equal(chart.hitPathControl({x:138,y:115}).direction,1);
  assert.equal(chart.hitPathControl({x:80,y:100}),null);
});

test('clicking an infinite trend line does not move it or commit an accidental drag',()=>{
  const{chart,pointer}=chartHarness();chart.plan.objects=[{id:'line',kind:'trend',period:'m3',points:[{time:20,price:100},{time:60,price:105}]}];
  let updated=null;chart.callbacks.update=(id,patch)=>updated=patch;
  const x=chart.tx.x(40),y=chart.tx.y(102.5);chart.down(pointer(x,y));
  assert.equal(chart.drag.kind,'object');
  assert.deepEqual(chart.drag.origin,chart.toValue({x,y},false));
  chart.move(pointer(x+1,y));assert.equal(chart.drag.moved,false);chart.up(pointer(x+1,y));
  assert.equal(updated,null);
});

test('legacy paths use the unified editing gestures without obstructing hover buttons',()=>{
  const{chart,node}=chartHarness();chart.plan.objects=[{id:'route',kind:'path',period:'m3',points:[{time:20,price:100},{time:40,price:105},{time:60,price:102}]}];
  const cursor={x:chart.tx.x(40)+2,y:chart.tx.y(105)};chart.hoverPath={id:'route',segment:0,point:cursor};const previous=global.document;
  try{global.document={createElementNS(){return node;}};chart.drawPathControl();}finally{global.document=previous;}
  assert.equal(chart.pathControls,null);
});

test('each path node control edits that exact node',()=>{
  const{chart}=chartHarness();chart.plan.objects=[{id:'route',kind:'path',period:'m3',points:[{time:20,price:100},{time:40,price:105},{time:60,price:102}]}];
  let updated;chart.callbacks.update=(id,patch)=>updated=patch;
  chart.adjustPathControl({id:'route',index:0,direction:-1});
  assert.deepEqual(updated.points.map(point=>point.time),[40,60]);
  chart.adjustPathControl({id:'route',index:1,direction:1});
  assert.deepEqual(updated.points.map(point=>point.time),[20,40,50,60]);
});

test('selecting a path anchor preserves its individual highlight after the selection callback',()=>{
  const{chart,pointer}=chartHarness();
  chart.plan.objects=[{id:'route',kind:'path',period:'m3',points:[{time:20,price:100},{time:40,price:105}]}];
  chart.selected='route';chart.callbacks.select=()=>{chart.selectedAnchor=null;};
  chart.down(pointer(chart.tx.x(40),chart.tx.y(105)));
  assert.equal(chart.selectedAnchor,1);
  assert.equal(chart.drag.anchor,1);
});

test('dragging the price-axis label edits entry without panning or changing another plan field',()=>{
  const{chart,events,pointer}=chartHarness();const view=structuredClone(chart.view),plan=structuredClone(chart.plan);
  chart.down(pointer(450,chart.tx.y(100)));
  assert.equal(chart.drag.kind,'plan-level');
  chart.move(pointer(450,chart.tx.y(103.4)));chart.up(pointer(450,chart.tx.y(103.4)));
  assert.deepEqual(chart.view,view);assert.deepEqual(chart.plan,plan);
  assert.deepEqual(events.levels.map(item=>item.phase),['start','move','end']);
  assert.deepEqual(events.levels.at(-1),{field:'planned_entry_price',price:103.4,phase:'end'});
  assert.ok(events.presence.some(item=>item.object_id==='field:planned_entry_price'&&item.cursor.price===103.4));
  assert.deepEqual(events.placed,[]);
});

test('Escape and pointer cancellation discard level drag previews and never commit',()=>{
  for(const cancel of ['escape','pointercancel','lostpointercapture']){
    const{chart,node,host,events,pointer}=chartHarness();
    chart.down(pointer(100,chart.tx.y(95)));chart.move(pointer(100,chart.tx.y(104)));
    if(cancel==='escape')host.listeners.keydown({key:'Escape',preventDefault(){}});else node.listeners[cancel]();
    chart.up(pointer(100,chart.tx.y(104)));
    assert.equal(chart.drag,null);assert.equal(chart.plan.stop_price,95);assert.equal(chart.plan.direction,'long');
    assert.equal(events.levels.at(-1).phase,'cancel');assert.equal(events.levels.filter(item=>item.phase==='end').length,0);
    assert.equal(events.presence.at(-1).preview_object,null);
  }
});

test('chart placement commits on release, while cancelling placement creates nothing',()=>{
  const{chart,node,events,pointer}=chartHarness();chart.setTool('planned-entry',{direction:'short'});
  chart.down(pointer(200,chart.tx.y(101.31)));assert.deepEqual(events.placed,[]);
  assert.equal(chart.displayPlan().direction,'short');assert.equal(chart.plan.direction,'long');
  node.listeners.pointercancel();chart.up(pointer(200,chart.tx.y(101.31)));assert.deepEqual(events.placed,[]);
  chart.setTool('planned-stop');chart.down(pointer(200,chart.tx.y(104.31)));chart.up(pointer(200,chart.tx.y(104.31)));
  assert.deepEqual(events.placed,[{field:'stop_price',price:104.4}]);assert.deepEqual(events.levels,[]);
});

test('wave nodes are free and remain editable with stable IDs',()=>{
  const{chart,pointer}=chartHarness();chart.callbacks.snapEnabled=()=>true;
  chart.bars=[{time:40,open:100,high:104,low:96,close:102}];chart.setTool('wave');
  const targets=[{x:175,y:43.17},{x:193,y:91.63},{x:245,y:64.23},{x:290,y:139.71}];
  for(const point of targets)chart.down(pointer(point.x,point.y));
  targets.forEach((p,i)=>{assert.equal(chart.path[i].time,Math.round(chart.tx.time(p.x)));assert.ok(Math.abs(chart.path[i].price-chart.tx.price(p.y))<1e-9);});
  let added;chart.callbacks.add=object=>added=object;chart.finishPath();
  assert.equal(added.locked,false);assert.deepEqual(added.points.map(p=>p.label),['1','2','3','4']);
  assert.equal(new Set(added.points.map(p=>p.id)).size,4);
});

test('unlocked wave dragging retains free price, label and ID instead of jumping to OHLC',()=>{
  const{chart,pointer}=chartHarness({objects:[]});chart.callbacks.snapEnabled=()=>true;
  chart.bars=[{time:40,open:100,high:104,low:96,close:102}];
  chart.plan.objects=[{id:'w',kind:'wave',period:'m3',locked:false,points:[{id:'p1',label:'1',time:40,price:105.33},{id:'p2',label:'2',time:60,price:99.11}]}];
  chart.selected='w';
  let updated;chart.callbacks.update=(id,patch)=>updated=patch;
  const x=chart.tx.x(40),y=chart.tx.y(105.33);chart.down(pointer(x,y));
  assert.equal(chart.drag.origin.price,105.33);
  chart.move(pointer(x+20,y+13.1));chart.up(pointer(x+20,y+13.1));
  assert.ok(Math.abs(updated.points[0].price-104.02)<1e-9);assert.equal(updated.points[0].id,'p1');assert.equal(updated.points[0].label,'1');
});

test('rendered crosshair uses exact cursor coordinates even with candle snapping enabled',()=>{
  const{chart}=chartHarness({objects:[]}),previous=global.document,created=[];
  const makeNode=tag=>({tag,attrs:{},style:{},setAttribute(k,v){this.attrs[k]=v;},append(){},replaceChildren(){}});
  chart.svg=makeNode('svg');chart.bars=[{time:40,open:100,high:104,low:96,close:102}];chart.callbacks.snapEnabled=()=>true;chart.crosshair={x:172.37,y:83.19};
  try{global.document={createElementNS(ns,tag){const node=makeNode(tag);created.push(node);return node;}};C.Chart.prototype.render.call(chart);}finally{global.document=previous;}
  assert.equal(Number(created.find(n=>n.attrs['data-crosshair']==='vertical').attrs.x1),172.37);
  assert.equal(Number(created.find(n=>n.attrs['data-crosshair']==='horizontal').attrs.y1),83.19);
});

test('daily linkage includes the preceding night, weekend and holiday gaps but excludes the next night',()=>{
  const friday={dt:'2026-11-06T00:00:00+08:00',source_trade_day:'2026-11-06'},monday={dt:'2026-11-09T00:00:00+08:00',source_trade_day:'2026-11-09'};
  const range=C.dailyRange(monday,friday);
  const bars=['2026-11-06T14:30:00+08:00','2026-11-06T21:00:00+08:00','2026-11-07T01:00:00+08:00','2026-11-09T14:30:00+08:00','2026-11-09T21:00:00+08:00'].map(dt=>({time:Date.parse(dt)}));
  assert.deepEqual(C.rangeBars(bars,range),bars.slice(1,4));
  const holiday=C.dailyRange({dt:'2026-10-09T00:00:00+08:00'},{dt:'2026-09-30T00:00:00+08:00'});
  assert.equal(holiday.start,Date.parse('2026-09-30T18:00:00+08:00'));
  assert.equal(holiday.end,Date.parse('2026-10-09T18:00:00+08:00'));
});

test('daily hover emits once per bar and clears on leave; crosshair remains free',()=>{
  const{chart,node,pointer}=chartHarness(),seen=[];chart.period='day';
  chart.bars=[{time:20,dt:'2026-11-09T00:00:00+08:00'},{time:40,dt:'2026-11-10T00:00:00+08:00'},{time:60,dt:'2026-11-11T00:00:00+08:00'}];chart.callbacks.hoverDay=r=>seen.push(r);
  const x=chart.tx.x(40)+3;chart.move(pointer(x,80));chart.move(pointer(x+2,83));
  assert.equal(seen.length,1);assert.equal(seen[0].label,'2026-11-10');assert.deepEqual(chart.crosshair,{x:x+2,y:83});
  node.listeners.pointerleave();assert.equal(seen.at(-1),null);assert.equal(chart.crosshair,null);
  assert.equal(chart.dayAt({x:chart.tx.x(90),y:80}),null);
});

test('day hover never changes either chart axis, even when the matching day is off screen',()=>{
  const{chart}=chartHarness(),before=structuredClone(chart.view);
  chart.setLinkedRange({start:-900,end:-600,label:'old day'});assert.deepEqual(chart.view,before);
  chart.setLinkedRange(null);assert.deepEqual(chart.view,before);
  assert.deepEqual(C.rangeBars(chart.bars,{start:-900,end:-600}),[]);
});

test('zoom fits visible OHLC vertically and widens candle bodies beyond the old 14px cap',()=>{
  const bars=Array.from({length:30},(_,i)=>({time:i*1800000,dt:new Date(i*1800000).toISOString(),open:100+i,close:101+i,low:99+i,high:103+i}));
  bars[0].high=900;
  const view={start:20*1800000,end:25*1800000,min:0,max:1000},fit=C.fitVisiblePrice(bars,view,1);
  assert.equal(fit.start,view.start);assert.equal(fit.end,view.end);assert.equal(fit.min,117);assert.equal(fit.max,130);
  const plot={x:0,y:0,w:600,h:400},tx=C.transform(fit,plot,bars,'m30');
  assert.ok(C.candleWidth(bars,tx,plot,'m30')>80);
  const{chart}=chartHarness();chart.bars=bars;chart.period='m30';chart.view={start:0,end:29*1800000,min:0,max:1000};chart.zoom(5);
  const visible=bars.filter(b=>b.time>=chart.view.start&&b.time<=chart.view.end);
  assert.equal(chart.view.end,29*1800000);assert.ok(chart.view.max<200);assert.ok(chart.view.max>Math.max(...visible.map(b=>b.high)));assert.ok(chart.view.min<Math.min(...visible.map(b=>b.low)));
  const empty={start:-9000000,end:-6000000,min:10,max:20};assert.deepEqual(C.fitVisiblePrice(bars,empty,1),empty);
});

test('wave nodes can be grabbed on first selection, inserted freely and renumbered on deletion',()=>{
  const{chart,pointer}=chartHarness({objects:[]});chart.plan.objects=[{id:'w',kind:'wave',period:'m3',locked:false,points:C.wavePoints([{time:20,price:100},{time:40,price:105},{time:60,price:99}])}];
  const original=structuredClone(chart.plan.objects[0].points);chart.callbacks.update=(id,patch)=>Object.assign(chart.plan.objects[0],patch);
  chart.down(pointer(chart.tx.x(40),chart.tx.y(105)));assert.equal(chart.drag.anchor,1);chart.cancelGesture();
  chart.selected='w';chart.selectedAnchor=1;const point={x:225,y:103.33};chart.insertWavePoint('w',point);
  const points=chart.plan.objects[0].points;assert.equal(points.length,4);assert.deepEqual(points.map(p=>p.label),['1','2','3','4']);assert.equal(points[2].price,chart.tx.price(point.y));
  chart.deleteWavePoint();assert.deepEqual(chart.plan.objects[0].points,original);
  chart.plan.objects[0].locked=true;chart.insertWavePoint('w',point);chart.selectedAnchor=0;chart.deleteWavePoint();assert.deepEqual(chart.plan.objects[0].points,original);
  chart.plan.objects[0].locked=false;chart.plan.objects[0].points=original.slice(0,2);chart.deleteWavePoint();assert.equal(chart.plan.objects[0].points.length,2);
});

test('forecast nodes win over entry lines and zones, and only the grabbed point moves',()=>{
  const{chart,pointer,events}=chartHarness();
  const wave={id:'w',kind:'wave',period:'m3',points:C.wavePoints([{time:20,price:105},{time:40,price:100},{time:65,price:106}])};
  chart.plan.objects=[wave,{id:'z',kind:'zone',period:'m3',points:[{time:30,price:108},{time:60,price:98}]}];
  const original=structuredClone(wave.points);chart.callbacks.select=()=>{chart.selectedAnchor=null;};chart.callbacks.update=(id,patch)=>Object.assign(wave,patch);
  const x=chart.tx.x(40),y=chart.tx.y(100);chart.down(pointer(x,y));
  assert.equal(chart.selected,'w');assert.equal(chart.selectedAnchor,1);assert.equal(chart.drag.anchor,1);
  chart.move(pointer(x+27,y+13.7));chart.up(pointer(x+27,y+13.7));
  assert.deepEqual(wave.points[0],original[0]);assert.deepEqual(wave.points[2],original[2]);
  assert.ok(Math.abs(wave.points[1].price-98.63)<1e-9);assert.equal(wave.points[1].id,original[1].id);assert.deepEqual(events.levels,[]);
  const a=chart.objectPoints(wave)[1],b=chart.objectPoints(wave)[2];
  const hit=chart.hit({x:(a.x+b.x)/2,y:(a.y+b.y)/2});assert.equal(hit.object.id,'w');assert.equal(hit.anchor,null);
});

test('double click inserts on the clicked segment for waves and legacy paths without moving existing points',()=>{
  for(const kind of ['wave','path']){
    const{chart,pointer}=chartHarness({objects:[]});
    const object={id:'w',kind,period:'m3',points:C.wavePoints([{time:10,price:107},{time:35,price:100},{time:60,price:108},{time:90,price:99}])};
    chart.plan.objects=[object];chart.selected='w';chart.selectedAnchor=0;const original=structuredClone(object.points);
    chart.callbacks.update=(id,patch)=>Object.assign(object,patch);
    const a=chart.objectPoints(object)[2],b=chart.objectPoints(object)[3],point={x:(a.x+b.x)/2,y:(a.y+b.y)/2+1.3};
    chart.doubleClick(pointer(point.x,point.y));
    assert.equal(object.points.length,5);assert.deepEqual(object.points.map(p=>p.label),['1','2','3','4','5']);assert.equal(chart.selectedAnchor,3);
    assert.equal(object.points[3].price,chart.tx.price(point.y));assert.equal(object.points[3].time,Math.round(chart.tx.time(point.x)));
    assert.deepEqual(object.points.filter((_,i)=>i!==3).map(({label,...p})=>p),original.map(({label,...p})=>p));
    chart.doubleClick(pointer(chart.tx.x(object.points[3].time),chart.tx.y(object.points[3].price)));assert.equal(object.points.length,4);assert.deepEqual(object.points,original);
  }
});

test('Delete distinguishes highlighted point from whole line and never removes a two-point line by accident',()=>{
  for(const kind of ['wave','path']){
    const{chart,pointer}=chartHarness({objects:[]}),deleted=[],notices=[];
    const object={id:'w',kind,period:'m3',points:C.wavePoints([{time:10,price:105},{time:45,price:99},{time:90,price:106}])};
    chart.plan.objects=[object];chart.callbacks.update=(id,patch)=>Object.assign(object,patch);chart.callbacks.deleteObject=id=>deleted.push(id);chart.callbacks.notice=text=>notices.push(text);
    const removed=object.points[1].id;chart.down(pointer(chart.tx.x(45),chart.tx.y(99)));chart.up(pointer(chart.tx.x(45),chart.tx.y(99)));chart.deleteSelection();
    assert.equal(object.points.length,2);assert.ok(object.points.every(p=>p.id!==removed));assert.deepEqual(object.points.map(p=>p.label),['1','2']);assert.deepEqual(deleted,[]);
    chart.selectedAnchor=0;chart.deleteSelection();assert.equal(object.points.length,2);assert.deepEqual(deleted,[]);assert.equal(notices.length,1);
    chart.down(pointer(chart.tx.x(50),chart.tx.y(105.5)));assert.equal(chart.selectedAnchor,null);assert.equal(chart.drag,null);chart.deleteSelection();assert.deepEqual(deleted,['w']);
  }
});

test('two pointer clicks still insert once when SVG redraw replaces the native double-click target',()=>{
  const{chart,pointer}=chartHarness({objects:[]});
  const object={id:'w',kind:'wave',period:'m3',points:C.wavePoints([{time:20,price:105},{time:80,price:99}])};
  chart.plan.objects=[object];chart.callbacks.update=(id,patch)=>Object.assign(object,patch);
  const at=pointer(chart.tx.x(50),chart.tx.y(102));chart.down({...at,timeStamp:1000});chart.up(at);assert.equal(object.points.length,2);
  chart.down({...at,timeStamp:1150});chart.up(at);assert.equal(object.points.length,3);assert.equal(chart.selectedAnchor,1);
  chart.doubleClick({...at,timeStamp:1160});assert.equal(object.points.length,3);
});

test('new forecast completes on two close pointer presses even when native double click is lost and detail resets',()=>{
  for(const kind of ['wave','path'])for(const detail of [0,1]){
    const{chart,pointer}=chartHarness({objects:[]}),added=[],drafts=[];
    chart.callbacks.draftChanged=draft=>drafts.push(draft);
    chart.callbacks.add=object=>{assert.equal(chart.path.length,0);added.push(object);chart.plan.objects=[{...object,id:'saved'}];chart.setTool('select');};
    chart.callbacks.update=()=>assert.fail('Trailing native double click must not edit the just-created forecast');
    chart.setTool(kind);
    const points=[[70,80],[150,150],[230,90],[310,140]];
    points.forEach(([x,y],index)=>{const event={...pointer(x,y),detail,timeStamp:1000+index*800};chart.down(event);chart.up(event);});
    assert.equal(chart.path.length,4);assert.equal(added.length,0);
    const last={...pointer(313,142),detail,timeStamp:3550};chart.down(last);chart.up(last);
    assert.equal(added.length,1);assert.equal(added[0].kind,kind);assert.equal(added[0].points.length,4);
    assert.equal(added[0].points[3].price,chart.toValue({x:310,y:140},false,kind==='wave').price);
    if(kind==='wave')assert.deepEqual(added[0].points.map(p=>p.label),['1','2','3','4']);
    assert.equal(chart.path.length,0);assert.deepEqual(drafts.filter(d=>d.count).map(d=>d.count),[1,2,3,4]);
    assert.deepEqual(drafts.at(-1),{period:'m3',count:0});
    chart.doubleClick({...last,timeStamp:3560,detail:2});assert.equal(added.length,1);assert.equal(chart.plan.objects[0].points.length,4);
  }
});

test('forecast completion requires both the timing and distance thresholds',()=>{
  const{chart,pointer}=chartHarness({objects:[]}),added=[];chart.callbacks.add=object=>added.push(object);chart.setTool('wave');
  for(const[x,y,stamp]of[[80,80,1000],[180,150,1100],[180,150,1700],[190,150,1800]])chart.down({...pointer(x,y),detail:1,timeStamp:stamp});
  assert.equal(added.length,0);assert.equal(chart.path.length,4);
  chart.keydown({key:'Enter',preventDefault(){}});assert.equal(added.length,1);assert.equal(added[0].points.length,3);assert.equal(chart.path.length,0);
});

test('finishing clears the draft before callbacks so synchronous tool changes cannot save it twice',()=>{
  const{chart}=chartHarness({objects:[]}),added=[],drafts=[];chart.tool='wave';chart.path=[{time:20,price:100},{time:40,price:105}];
  chart.callbacks.draftChanged=draft=>drafts.push(draft);
  chart.callbacks.add=object=>{added.push(object);assert.equal(chart.path.length,0);assert.equal(drafts.at(-1).count,0);assert.equal(chart.finishPath(),false);chart.setTool('select');};
  assert.equal(chart.finishPath(),true);assert.equal(chart.finishPath(),false);assert.equal(added.length,1);assert.equal(chart.tool,'select');
});

test('forecast cancel, Escape and tool reset discard local drafts and publish the cleared count',()=>{
  for(const action of ['cancel','escape','tool']){
    const{chart,pointer}=chartHarness({objects:[]}),drafts=[];chart.callbacks.add=()=>assert.fail('Cancel must not save a forecast');chart.callbacks.draftChanged=draft=>drafts.push(draft);chart.setTool('wave');
    chart.down({...pointer(80,80),timeStamp:1000});chart.down({...pointer(200,150),timeStamp:1800});
    if(action==='cancel')assert.equal(chart.cancelPath(),true);
    else if(action==='escape')chart.keydown({key:'Escape',preventDefault(){}});
    else chart.setTool('horizontal');
    assert.equal(chart.path.length,0);assert.equal(chart.lastDrawingClick,null);assert.deepEqual(drafts.at(-1),{period:'m3',count:0});
    assert.equal(chart.cancelPath(),false);
  }
});

test('double clicking only the first forecast point cannot create a degenerate shared object',()=>{
  const{chart,pointer}=chartHarness({objects:[]});chart.callbacks.add=()=>assert.fail('A single point is not a shared forecast');chart.setTool('wave');
  const at=pointer(80,80);chart.down({...at,timeStamp:1000,detail:1});chart.down({...at,timeStamp:1150,detail:1});chart.doubleClick({...at,timeStamp:1160});
  assert.equal(chart.path.length,1);assert.equal(chart.finishPath(),false);chart.cancelPath();assert.equal(chart.path.length,0);
});

test('double clicking a node removes exactly that point and never deletes the entire two-point line',()=>{
  for(const kind of ['wave','path']){
    const{chart,pointer}=chartHarness({objects:[]});
    const object={id:'w',kind,period:'m3',points:C.wavePoints([{time:20,price:105},{time:50,price:99},{time:80,price:106}])};
    chart.plan.objects=[object];chart.callbacks.update=(id,patch)=>Object.assign(object,patch);
    const original=structuredClone(object.points),at=pointer(chart.tx.x(50),chart.tx.y(99));
    chart.down({...at,timeStamp:1000});chart.up(at);chart.down({...at,timeStamp:1150});chart.up(at);chart.doubleClick({...at,timeStamp:1160});
    assert.deepEqual(object.points.map(p=>p.id),[original[0].id,original[2].id]);assert.deepEqual(object.points.map(p=>p.label),['1','2']);
    chart.callbacks.deleteObject=()=>assert.fail('Double click must not delete the whole line');
    chart.doubleClick({...pointer(chart.tx.x(20),chart.tx.y(105)),timeStamp:3000});assert.equal(object.points.length,2);
  }
});

test('dragging a node resets double-click detection and leaving a chart clears shared cursor',()=>{
  const{chart,pointer,node,events}=chartHarness({objects:[]});const object={id:'w',kind:'wave',period:'m3',points:C.wavePoints([{time:20,price:105},{time:50,price:99},{time:80,price:106}])};chart.plan.objects=[object];
  chart.callbacks.update=(id,patch)=>Object.assign(object,patch);const p=pointer(chart.tx.x(50),chart.tx.y(99));chart.down({...p,timeStamp:1000});chart.move({...p,clientX:p.clientX+4});chart.up(p);
  chart.down({...p,clientX:p.clientX+4,timeStamp:1200});assert.equal(object.points.length,3);chart.up(p);node.listeners.pointerleave();assert.equal(events.presence.at(-1).cursor,null);
});

test('remote cursors update their own overlay without rerendering candles or using another period',()=>{
  const{chart}=chartHarness({objects:[]}),previous=global.document;
  const makeNode=tag=>({tag,attrs:{},children:[],style:{},setAttribute(k,v){this.attrs[k]=String(v);},append(...nodes){this.children.push(...nodes);},replaceChildren(){this.children=[];}});
  chart.peerLayer=makeNode('g');chart.render=()=>assert.fail('Cursor update must not rerender candles');
  try{
    global.document={createElementNS(ns,tag){return makeNode(tag);}};
    chart.setPresence([{id:'a',name:'同伴',color:'#4588cc',period:'m3',cursor:{time:40,price:103}},{id:'b',period:'day',cursor:{time:40,price:103}}]);
    assert.equal(chart.peerLayer.children.length,1);assert.equal(chart.peerLayer.children[0].attrs['data-peer-cursor'],'a');
    assert.equal(chart.peerLayer.children[0].attrs.transform,`translate(${chart.tx.x(40)} ${chart.tx.y(103)})`);
    chart.setPresence([{id:'a',period:'m3',cursor:null}]);assert.equal(chart.peerLayer.children.length,0);
  }finally{global.document=previous;}
});

test('releasing a drag keeps the shared cursor until the pointer leaves the chart',()=>{
  const{chart,pointer,node,events}=chartHarness({objects:[]});
  const at=pointer(chart.tx.x(45),chart.tx.y(104));chart.down(at);chart.up(at);
  assert.ok(events.presence.at(-1).cursor);assert.equal(events.presence.at(-1).cursor.price,104);
  assert.equal(events.presence.at(-1).editing,false);assert.equal(events.presence.at(-1).preview_object,null);
  node.listeners.pointerleave();assert.equal(events.presence.at(-1).cursor,null);
});

test('price segments draw freely, resize endpoints horizontally and move as one price level',()=>{
  const{chart,pointer}=chartHarness({objects:[]}),added=[];chart.tool='segment';chart.callbacks.add=o=>added.push(o);
  chart.down(pointer(120,100));chart.move(pointer(300,160));chart.up(pointer(300,160));
  const object={...added[0],id:'s'};assert.equal(object.kind,'segment');assert.equal(object.points[0].price,object.points[1].price);
  chart.plan.objects=[object];chart.selected='s';chart.tool='select';chart.callbacks.update=(id,p)=>Object.assign(object,p);
  const price=object.points[0].price;chart.down(pointer(300,100));chart.move(pointer(340,140));chart.up(pointer(340,140));
  assert.equal(chart.tx.x(object.points[1].time),340);assert.equal(object.points[1].price,price);
  chart.down(pointer(220,100));chart.move(pointer(240,120));chart.up(pointer(240,120));
  assert.equal(object.points[0].price,price-2);assert.equal(object.points[1].price,price-2);
  assert.equal(chart.hit({x:380,y:120}),null);
});
test('OHLC snapping uses drawn spacing across a long session gap and scaled SVG coordinates',()=>{
  const{chart,node}=chartHarness({objects:[]});chart.period='day';chart.bars=[{time:1000000,open:101,high:109,low:94,close:106},{time:1000000+C.INTERVAL.day*12,open:103,high:108,low:95,close:104}];
  chart.view={start:chart.bars[0].time,end:chart.bars[1].time,min:90,max:110};chart.tx=C.transform(chart.view,chart.plot,chart.bars,'day');
  for(const key of ['open','high','low','close'])assert.deepEqual(chart.toValue({x:chart.tx.x(chart.bars[0].time)+60,y:chart.tx.y(chart.bars[0][key])}),{time:chart.bars[0].time,price:chart.bars[0][key]});
  node.viewBox={baseVal:{x:0,y:0,width:500,height:250}};node.getBoundingClientRect=()=>({left:20,top:30,width:1000,height:500});
  assert.deepEqual(chart.point({clientX:220,clientY:130}),{x:100,y:50});
  chart.callbacks.snapEnabled=()=>false;assert.notEqual(chart.toValue({x:80,y:70}).time,chart.bars[0].time);
});
test('zone title double click opens inline editing without moving the box',()=>{
  const{chart,pointer}=chartHarness({objects:[]}),object={id:'z',kind:'zone',period:'m3',name:'等待',points:[{time:20,price:107},{time:70,price:97}]};chart.plan.objects=[object];const calls=[];chart.openTitleEditor=o=>calls.push(o.id);
  const p=pointer(chart.tx.x(20)+10,chart.tx.y(107)+10);chart.down({...p,timeStamp:1000});chart.up(p);chart.down({...p,timeStamp:1200});chart.up(p);chart.doubleClick({...p,timeStamp:1210});assert.deepEqual(calls,['z']);
});
test('zones use the chosen color in title, border and hatch; horizontal strokes are half width',()=>{
  const{chart}=chartHarness({objects:[]}),previous=global.document;
  const make=tag=>({tag,attrs:{},children:[],setAttribute(k,v){this.attrs[k]=v;},append(...nodes){this.children.push(...nodes);}}),layer=make('g');
  try{global.document={createElementNS:(_,tag)=>make(tag)};
    chart.drawObject(layer,{id:'z',kind:'zone',zone_type:'danger',color:'#00ff00',points:[{time:20,price:107},{time:70,price:97}]});
    const flatten=n=>[n,...n.children.flatMap(flatten)],all=flatten(layer);
    assert.equal(all.find(n=>n.attrs['data-zone']==='z').attrs.stroke,'#00ff00');assert.equal(all.find(n=>n.attrs['data-zone-title']==='z').attrs.fill,'#00ff00');assert.equal(all.find(n=>n.tag==='line').attrs.stroke,'#00ff00');
    const horizontal=make('g');chart.drawObject(horizontal,{id:'h',kind:'horizontal',width:2,points:[{time:10,price:100}]});assert.equal(Number(horizontal.children[0].children[0].attrs['stroke-width']),1);
    const segment=make('g');chart.drawObject(segment,{id:'s',kind:'segment',width:2,points:[{time:10,price:100},{time:60,price:100}]});assert.equal(Number(segment.children[0].children[0].attrs.x2),chart.tx.x(60));assert.equal(Number(segment.children[0].children[0].attrs['stroke-width']),1);assert.ok(segment.children[0].children.some(n=>n.attrs['data-segment-price']==='s'));
  }finally{global.document=previous;}
});

test('dragging any zone corner or its body keeps its size across compressed session gaps',()=>{
  for(const anchor of [0,1,null]){
    const{chart,pointer}=chartHarness({objects:[]});chart.tick=1;
    chart.bars=[{time:1000000},{time:1180000},{time:1360000},{time:9000000},{time:9180000}];
    chart.view={start:1000000,end:9360000,min:90,max:110};chart.tx=C.transform(chart.view,chart.plot,chart.bars,'m3');
    const zone={id:'z',kind:'zone',period:'m3',points:[{time:1180000,price:107.35},{time:1360000,price:99.12}]};
    chart.plan.objects=[zone];chart.selected='z';const original=chart.objectPoints(zone),width=original[1].x-original[0].x,height=original[1].y-original[0].y;
    const point=anchor===null?{x:(original[0].x+original[1].x)/2,y:(original[0].y+original[1].y)/2}:original[anchor];
    chart.callbacks.update=(id,patch)=>Object.assign(zone,patch);chart.down(pointer(point.x,point.y));assert.equal(chart.drag.anchor,null);
    chart.move(pointer(point.x+120,point.y-16));chart.up(pointer(point.x+120,point.y-16));
    const after=chart.objectPoints(zone);assert.ok(Math.abs((after[1].x-after[0].x)-width)<.001);assert.ok(Math.abs((after[1].y-after[0].y)-height)<1e-8);
    assert.ok(Math.abs(after[0].x-original[0].x-120)<.001);assert.ok(Math.abs(zone.points[0].price-zone.points[1].price-8.23)<1e-8);
  }
});

test('forecast drag commits only the grabbed ID after peer edits, insertion and deletion',()=>{
  for(const kind of ['wave','path'])for(const timing of ['before-move','after-last-move']){
    const points=[{id:'a',time:20,price:100},{id:'b',time:40,price:105},{id:'c',time:60,price:99},{id:'d',time:80,price:104}];
    const{chart,pointer}=chartHarness({objects:[{id:'w',kind,period:'m3',points:C.wavePoints(points)}]});
    chart.selected='w';let local;
    chart.callbacks.update=(id,patch)=>{local=structuredClone(chart.plan);Object.assign(local.objects.find(object=>object.id===id),patch);};
    const x=chart.tx.x(40),y=chart.tx.y(105);chart.down(pointer(x,y));assert.equal(chart.drag.anchor,1);
    if(timing==='after-last-move')chart.move(pointer(x+16,y+10));
    const remote=structuredClone(chart.plan),wave=remote.objects[0];wave.name='Peer title';wave.color='#00cc88';
    wave.points[0].price=101;wave.points.splice(1,0,{id:'peer-insert',time:30,price:102});wave.points=wave.points.filter(point=>point.id!=='d');wave.points=C.wavePoints(wave.points);
    chart.setPlan(remote);
    if(timing==='before-move')chart.move(pointer(x+16,y+10));
    chart.up(pointer(x+16,y+10));
    assert.ok(local,`${kind}: ${timing}`);const pending=M.diff(remote,local),edit=pending.object_edits[0];
    assert.deepEqual(edit.node_updates,[{id:'b',changes:{time:44,price:104}}]);
    assert.deepEqual(edit.node_inserts,[]);assert.deepEqual(edit.node_deletes,[]);assert.deepEqual(edit.changes,{});
    const saved=M.apply(remote,pending).objects[0];assert.deepEqual(saved.points.map(point=>point.id),['a','peer-insert','b','c']);
    assert.deepEqual(saved.points.map(point=>point.label),['1','2','3','4']);assert.equal(saved.points[0].price,101);assert.equal(saved.name,'Peer title');assert.equal(saved.color,'#00cc88');
    assert.equal(chart.selectedAnchor,2);
  }
});

test('peer deletion or lock during node drag never recreates the point or object',()=>{
  for(const change of ['delete-node','delete-object','lock-object']){
    const{chart,pointer}=chartHarness({objects:[{id:'w',kind:'wave',period:'m3',points:C.wavePoints([{id:'a',time:20,price:100},{id:'b',time:40,price:105},{id:'c',time:60,price:99}])}]});
    let updates=0;chart.callbacks.update=()=>updates++;
    const x=chart.tx.x(40),y=chart.tx.y(105);chart.down(pointer(x,y));chart.move(pointer(x+16,y+10));
    const remote=structuredClone(chart.plan);
    if(change==='delete-node')remote.objects[0].points=C.wavePoints(remote.objects[0].points.filter(point=>point.id!=='b'));
    else if(change==='delete-object')remote.objects=[];else remote.objects[0].locked=true;
    chart.setPlan(remote);chart.up(pointer(x+16,y+10));
    assert.equal(updates,0,change);assert.equal(M.hasPending(M.diff(remote,chart.plan)),false);
  }
});

test('whole-line drag replays displacement on the latest peer geometry',()=>{
  for(const kind of ['trend','reference']){
    const{chart,pointer}=chartHarness({objects:[{id:'line',kind,period:'m3',points:[{time:20,price:105},{time:80,price:95}]}]});
    let local;chart.callbacks.update=(id,patch)=>{local=structuredClone(chart.plan);Object.assign(local.objects[0],patch);};
    const x=chart.tx.x(50),y=chart.tx.y(100);chart.down(pointer(x,y));assert.equal(chart.drag.anchor,null);chart.move(pointer(x+16,y+10));
    const remote=structuredClone(chart.plan);remote.objects[0].points[1]={time:84,price:96};remote.objects[0].color='#00ff00';chart.setPlan(remote);
    chart.up(pointer(x+16,y+10));
    const saved=M.apply(remote,M.diff(remote,local)).objects[0];assert.deepEqual(saved.points,[{time:24,price:104},{time:88,price:95}]);assert.equal(saved.color,'#00ff00');
  }
});

test('segment endpoint drag preserves the latest peer price and other endpoint',()=>{
  const{chart,pointer}=chartHarness({objects:[{id:'s',kind:'segment',period:'m3',points:[{time:20,price:100},{time:60,price:100}]}]});
  chart.selected='s';let local;chart.callbacks.update=(id,patch)=>{local=structuredClone(chart.plan);Object.assign(local.objects[0],patch);};
  const x=chart.tx.x(60),y=chart.tx.y(100);chart.down(pointer(x,y));assert.equal(chart.drag.anchor,1);chart.move(pointer(x+40,y-30));
  const remote=structuredClone(chart.plan);remote.objects[0].points=[{time:25,price:103},{time:60,price:103}];chart.setPlan(remote);chart.up(pointer(x+40,y-30));
  assert.deepEqual(M.apply(remote,M.diff(remote,local)).objects[0].points,[{time:25,price:103},{time:70,price:103}]);
});

test('zone and segment body drags preserve latest dimensions across compressed sessions',()=>{
  for(const kind of ['zone','segment']){
    const{chart,pointer}=chartHarness({objects:[]});chart.tick=1;
    chart.bars=[{time:1000000},{time:1180000},{time:1360000},{time:9000000},{time:9180000}];
    chart.view={start:1000000,end:9360000,min:90,max:115};chart.tx=C.transform(chart.view,chart.plot,chart.bars,'m3');
    chart.plan.objects=[{id:'shape',kind,period:'m3',points:[{time:1180000,price:107},{time:1360000,price:kind==='zone'?99:107}]}];
    const points=chart.objectPoints(chart.plan.objects[0]),x=(points[0].x+points[1].x)/2,y=(points[0].y+points[1].y)/2;
    let local;chart.callbacks.update=(id,patch)=>{local=structuredClone(chart.plan);Object.assign(local.objects[0],patch);};
    chart.down(pointer(x,y));assert.equal(chart.drag.anchor,null);chart.move(pointer(x+120,y-16));
    const remote=structuredClone(chart.plan);remote.objects[0].points[1].time=1540000;remote.objects[0].points.forEach(point=>point.price+=2);chart.setPlan(remote);
    const before=chart.objectPoints(remote.objects[0]);chart.up(pointer(x+120,y-16));
    const saved=M.apply(remote,M.diff(remote,local)).objects[0],after=chart.objectPoints(saved);
    for(let i=0;i<2;i++){assert.ok(Math.abs(after[i].x-before[i].x-120)<.001);assert.equal(saved.points[i].price,remote.objects[0].points[i].price+2);}
    assert.ok(Math.abs((after[1].x-after[0].x)-(before[1].x-before[0].x))<.001);
    assert.equal(saved.points[0].price-saved.points[1].price,remote.objects[0].points[0].price-remote.objects[0].points[1].price);
  }
});

function captureSvg(run){
  const previous=global.document;
  const make=tag=>({tag,attrs:{},children:[],style:{},listeners:{},setAttribute(key,value){this.attrs[key]=String(value);},append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}},replaceChildren(...nodes){this.children=[];this.append(...nodes);},addEventListener(name,handler){this.listeners[name]=handler;},focus(){},getBoundingClientRect(){return{left:0,top:0};},setPointerCapture(){},hasPointerCapture(){return false;}});
  const entries=(node,inherited=1)=>{const opacity=inherited*(node.attrs.opacity===undefined?1:Number(node.attrs.opacity));return[{node,opacity},...node.children.flatMap(child=>entries(child,opacity))];};
  try{global.document={createElementNS:(_,tag)=>make(tag),createElement:make};return run({make,entries});}finally{global.document=previous;}
}

test('all eight drawing kinds apply optional opacity to every artwork component including zero',()=>{
  captureSvg(({make,entries})=>{
    const{chart}=chartHarness({objects:[]});
    for(const kind of ['horizontal','segment','trend','reference','wave','path','zone','text'])for(const opacity of [undefined,0,.35,1,NaN,-1,2,'0.5',null]){
      const object={id:kind,kind,period:'m3',opacity,points:[{id:'a',label:'1',time:20,price:105},{id:'b',label:'2',time:60,price:kind==='segment'?105:98}]};
      const layer=make('g');chart.drawObject(layer,object);
      const artwork=entries(layer).find(({node})=>node.attrs['data-object-artwork']===kind);assert.ok(artwork,kind);
      const expected=typeof opacity==='number'&&Number.isFinite(opacity)&&opacity>=0&&opacity<=1?opacity:1;
      const painted=entries(artwork.node).filter(({node})=>['line','polyline','rect','circle','text'].includes(node.tag));assert.ok(painted.length,kind);
      for(const item of painted)assert.equal(item.opacity,expected,`${kind} / ${opacity} / ${item.node.tag}`);
      assert.equal(entries(layer).some(({node})=>node.attrs['data-object-selection']),false);
    }
  });
});

test('forecast opacity multiplies with period mapping and peer ghost opacity without dimming cursors',()=>{
  captureSvg(({make,entries})=>{
    const{chart}=chartHarness({objects:[]});
    for(const period of ['day','m30','m3'])for(const opacity of [0,.5,1]){
      chart.period=period;const layer=make('g');chart.drawObject(layer,{id:'w',kind:'wave',period:'day',opacity,points:C.wavePoints([{time:20,price:105},{time:60,price:98}])});
      const line=entries(layer).find(({node})=>node.attrs['data-wave-line']==='w');assert.ok(line);
      assert.ok(Math.abs(line.opacity-opacity*C.pathPeriodOpacity('day',period))<1e-12);
    }
    chart.period='m3';chart.selected='w';chart.peerLayer=make('g');
    chart.setPresence([{id:'peer',name:'同伴',period:'m3',cursor:{time:40,price:103},preview_object:{id:'w',kind:'wave',period:'day',opacity:.5,points:C.wavePoints([{time:20,price:105},{time:60,price:98}])}}]);
    const all=entries(chart.peerLayer),line=all.find(({node})=>node.attrs['data-wave-line']==='w'),cursor=all.find(({node})=>node.attrs['data-peer-cursor']==='peer');
    assert.ok(Math.abs(line.opacity-.5*.5*.16)<1e-12);assert.equal(cursor.opacity,1);
    assert.equal(all.some(({node})=>node.attrs['data-object-selection']),false);
  });
});

test('transparent selected objects retain separate editing affordances and unchanged hit geometry',()=>{
  captureSvg(({make,entries})=>{
    for(const kind of ['horizontal','segment','trend','reference','wave','path','zone','text'])for(const opacity of [0,.1]){
      const{chart,pointer}=chartHarness({objects:[]});
      const object={id:kind,kind,period:'m3',opacity,points:C.wavePoints([{time:20,price:105},{time:60,price:kind==='segment'?105:98}])};
      chart.plan.objects=[object];chart.selected=kind;const point={x:chart.tx.x(20),y:chart.tx.y(105)},hit=chart.hit(point);
      assert.equal(hit.object.id,kind);const layer=make('g');chart.drawObject(layer,object);
      const all=entries(layer),artwork=all.find(({node})=>node.attrs['data-object-artwork']===kind),selection=all.find(({node})=>node.attrs['data-object-selection']===kind);
      assert.equal(artwork.opacity,opacity);assert.ok(selection?.opacity>=.5);assert.ok(selection.node.children.some(node=>node.tag==='circle'));
      assert.equal(selection.node.children.some(node=>node.tag==='text'||node.tag==='polyline'),false);
      let patch;chart.callbacks.update=(id,value)=>patch=value;chart.down(pointer(point.x,point.y));chart.move(pointer(point.x+16,point.y+10));chart.up(pointer(point.x+16,point.y+10));assert.ok(patch?.points,kind);
      assert.equal(object.opacity,opacity);
    }
  });
});

test('plan opacity affects all manual overlays and badges but leaves market and peer visuals independent',()=>{
  captureSvg(({make,entries})=>{
    for(const opacity of [undefined,0,.25,1]){
      const{chart}=chartHarness({direction:'long',planned_entry_price:103,stop_price:97,main_level:106,trigger_price:104,obstacle_price:108,entry_low:100,entry_high:102,plan_opacity:opacity,objects:[{id:'own',kind:'horizontal',opacity:.6,points:[{time:20,price:101}]}]});
      chart.svg=make('svg');chart.autoPrice=false;chart.bars=Array.from({length:30},(_,index)=>({time:index*3,open:100,high:105,low:95,close:102}));chart.crosshair={x:100,y:80};
      chart.peers=[{id:'peer',name:'伙伴',period:'m3',cursor:{time:40,price:103}}];C.Chart.prototype.render.call(chart);
      const all=entries(chart.svg),expected=opacity??1,plan=all.find(({node})=>node.attrs['data-plan-overlays']==='m3');assert.equal(plan.opacity,expected);
      for(const key of ['entry-range','main_level','trigger_price','obstacle_price','risk-fill','planned_entry_price','stop_price'])assert.equal(all.find(({node})=>node.attrs['data-plan-overlay']===key).opacity,expected,key);
      const badges=all.filter(({node})=>node.attrs['data-plan-target']==='price-label');assert.equal(badges.length,2);for(const badge of badges)for(const item of entries(badge.node))assert.equal(item.opacity,expected);
      for(const item of all.filter(({node})=>node.attrs['data-candle']!==undefined||node.attrs['data-crosshair']||node.attrs['data-peer-cursor']))assert.equal(item.opacity,1);
      const averages=all.filter(({node})=>node.tag==='polyline'&&['#dededb','#d8c345','#c65aba'].includes(node.attrs.stroke));assert.equal(averages.length,3);assert.ok(averages.every(item=>item.opacity===.8));
      assert.equal(all.find(({node})=>node.attrs['data-object-artwork']==='own').opacity,.6);
      assert.deepEqual(chart.levelGeometry().map(level=>level.field).sort(),['planned_entry_price','stop_price']);
    }
  });
});

test('new drawing tools preserve chosen opacity in drafts and saved objects',()=>{
  captureSvg(({make,entries})=>{
    for(const kind of ['horizontal','segment','trend','reference','wave','path','zone','text'])for(const opacity of [0,.3]){
      const{chart,pointer,host}=chartHarness({objects:[]}),added=[];host.append=()=>{};chart.callbacks.add=object=>added.push(object);
      chart.view={start:0,end:1000000,min:90,max:110};chart.tx=C.transform(chart.view,chart.plot);chart.bars=[{time:0,open:100,high:105,low:95,close:102},{time:180000,open:101,high:106,low:96,close:103}];chart.autoPrice=false;
      chart.setTool(kind,{opacity});assert.equal(chart.toolOpacity,opacity);
      chart.down({...pointer(100,70),timeStamp:1000});
      if(kind==='text'){chart.textInput.value='测试文字';chart.textEditor.listeners.submit({preventDefault(){}});}
      else if(kind!=='horizontal'){
        if(['wave','path'].includes(kind))chart.down({...pointer(300,150),timeStamp:2000});else chart.move(pointer(300,150));
        const oldSvg=chart.svg;chart.svg=make('svg');C.Chart.prototype.render.call(chart);
        const draft=entries(chart.svg).find(({node})=>node.attrs['data-object-artwork']===(['wave','path'].includes(kind)?'path':kind));assert.equal(draft?.opacity,opacity,kind);chart.svg=oldSvg;
        if(['wave','path'].includes(kind))chart.finishPath();else chart.up(pointer(300,150));
      }
      assert.equal(added.length,1,kind);assert.equal(added[0].opacity,opacity,kind);chart.setTool('select');assert.equal(chart.toolOpacity,1);
    }
  });
});
