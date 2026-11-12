'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');
const ms=value=>Date.parse(value+'+08:00'),step=C.INTERVAL.m30;
const bar=time=>({dt:new Date(time).toISOString(),time,open:3035,high:3053,low:3020,close:3040});
const history=[ms('2026-11-11T13:30:00'),ms('2026-11-11T14:00:00'),ms('2026-11-11T14:30:00')].map(bar);
const night=ms('2026-11-11T21:00:00');
const market=(m30=history,m3=m30)=>({tick:1,symbol:'SHFE.rb2701',periods:{m30,m3,day:[]},feed_status:'live'});
const view=()=>({start:history[0].time,end:history.at(-1).time+14*step,min:2980,max:3070});
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} differs from ${b}`);
function chartAt(period='m30',data=market(),plan={objects:[]}){
  const chart=Object.create(C.Chart.prototype);
  Object.assign(chart,{period,callbacks:{},bars:[],plan:{objects:[]},view:null,tick:1,path:[],drag:null,autoPrice:true,plot:{x:0,y:0,w:800,h:300},host:{clientWidth:867},tool:'select',selected:null});
  chart.render=function(){if(this.view){if(this.autoPrice&&!this.drag)this.view=this.priceFit(this.view);this.tx=C.transform(this.view,this.plot,this.bars,this.period);}};
  chart.setData(data,structuredClone(plan),true);
  return chart;
}
function futureWave(){return{id:'wave',kind:'wave',period:'m30',points:C.wavePoints([1,3.25,5,7.5].map((offset,index)=>C.anchorPoint({time:history.at(-1).time+offset*step,price:[3040,3065,3009,3058][index]},history,'m30')))};}

test('reproduces old natural-time compression and preserves anchored shape at night opening',()=>{
  const wave=futureWave(),before=C.transform(view(),{x:0,y:0,w:800,h:300},history,'m30'),opened=[...history,bar(night)],oldAfter=C.transform(view(),{x:0,y:0,w:800,h:300},opened,'m30');
  const originalWidth=before.x(wave.points[2].time)-before.x(wave.points[0].time),brokenWidth=oldAfter.x(wave.points[2].time)-oldAfter.x(wave.points[0].time);
  assert.ok(brokenWidth<originalWidth/3,'the pre-fix transform crushes future points into the closed-session gap');
  const chart=chartAt('m30',market(),{objects:[wave]});chart.view=view();chart.autoPrice=false;chart.followLatest=false;chart.render();
  const positions=chart.objectPoints(wave),saved=JSON.stringify(wave);
  chart.setData(market(opened),{objects:[wave]});
  chart.objectPoints(wave).forEach((point,index)=>{close(point.x,positions[index].x);close(point.y,positions[index].y);});
  assert.equal(chart.pointTime(wave.points[0]),night);
  assert.equal(JSON.stringify(wave),saved,'a quote update must not rewrite persisted time, price, IDs, labels or anchors');
});

test('follow mode advances one candle slot without stretching a forecast across the opening gap',()=>{
  const wave=futureWave(),chart=chartAt('m30',market(),{objects:[wave]});chart.view=view();chart.autoPrice=false;chart.render();
  const before=chart.objectPoints(wave),slot=chart.tx.x(history.at(-1).time+step)-chart.tx.x(history.at(-1).time);
  chart.setData(market([...history,bar(night)]),{objects:[wave]});
  chart.objectPoints(wave).forEach((point,index)=>close(point.x,before[index].x-slot));
});

test('future daily anchors survive the weekend without mutating saved Saturday timestamps',()=>{
  const friday=ms('2026-11-06T00:00:00'),monday=ms('2026-11-09T00:00:00'),days=[bar(friday-C.INTERVAL.day),bar(friday)];
  const point=C.anchorPoint({time:friday+C.INTERVAL.day*1.5,price:100},days,'day'),saved=structuredClone(point);
  assert.equal(C.resolvePointTime(point,{periods:{day:[...days,bar(monday)]}}),monday+C.INTERVAL.day*.5);
  assert.deepEqual(point,saved);
});

test('cross-period mapping resolves source candles before mapping into smaller-period slots',()=>{
  const wave=futureWave(),m30=[...history,bar(night),bar(night+step)],m3=Array.from({length:21},(_,i)=>bar(night+i*C.INTERVAL.m3));
  const chart=chartAt('m3',market(m30,m3),{objects:[wave]});
  const point=wave.points[1];
  assert.equal(chart.pointTime(point),night+2.25*step);
  close(chart.objectPoints(wave)[1].x,chart.tx.x(night+2.25*step));
  assert.equal(point.chart_anchor.period,'m30');
});

test('history prepends, retained-window shifts, reloads and repeated full snapshots keep anchor identity',()=>{
  const wave=futureWave(),opened=[...history,...Array.from({length:10},(_,i)=>bar(night+i*step))],prefixed=[bar(history[0].time-step),...opened];
  const expected=wave.points.map(p=>C.resolvePointTime(p,market(opened)));
  for(const rows of [prefixed,opened,opened.slice(2),prefixed,opened]){
    const chart=chartAt('m30',market(rows),{objects:[JSON.parse(JSON.stringify(wave))]});
    assert.deepEqual(chart.plan.objects[0].points.map(p=>chart.pointTime(p)),expected);
    assert.deepEqual(chart.plan.objects[0].points.map(p=>p.label),['1','2','3','4']);
  }
  const unavailable=market(opened.slice(3));
  assert.deepEqual(wave.points.map(p=>C.resolvePointTime(p,unavailable)),wave.points.map(p=>p.time),'missing anchor history uses saved absolute time without guessing');
});

test('moving an anchored node into history explicitly clears the anchor and preserves its identity',()=>{
  const wave=futureWave(),chart=chartAt('m30',market([...history,bar(night)]),{objects:[wave]});
  const value={time:history[0].time,price:3022};
  const preview=chart.objectDragPreview({object:structuredClone(wave),anchor:1,value,origin:{time:night,price:3065},point:{x:20,y:30},start:{x:40,y:60}});
  assert.equal(preview.points[1].chart_anchor,null);assert.equal(preview.points[1].time,value.time);
  assert.equal(preview.points[1].id,wave.points[1].id);
  assert.deepEqual(preview.points[0],wave.points[0]);
});

test('whole forecast drag rebases every anchor from rendered positions without changing its shape',()=>{
  const wave=futureWave(),chart=chartAt('m30',market([...history,bar(night)]),{objects:[wave]});chart.autoPrice=false;chart.render();
  const before=chart.objectPoints(wave),drag={object:structuredClone(wave),anchor:null,value:{time:night+step,price:3050},origin:{time:night,price:3040},start:{x:100,y:100},point:{x:140,y:80}};
  const moved=chart.objectDragPreview(drag),after=chart.objectPoints(moved);
  after.forEach((point,i)=>{close(point.x,before[i].x+40);assert.equal(moved.points[i].price,wave.points[i].price+10);assert.equal(moved.points[i].id,wave.points[i].id);});
});

test('plus controls add after resolved endpoints and retain untouched saved points and numbering',()=>{
  const wave=futureWave(),chart=chartAt('m30',market([...history,bar(night)]),{objects:[wave]});
  const appended=chart.editForecastPoints(wave,null,1);
  assert.deepEqual(appended.slice(0,wave.points.length),wave.points);
  assert.ok(chart.pointTime(appended.at(-1))>chart.pointTime(wave.points.at(-1)));
  assert.ok(appended.at(-1).chart_anchor);
  const inserted=C.wavePoints(chart.editForecastPoints(wave,1,1));
  assert.deepEqual(inserted.map(point=>point.label),['1','2','3','4','5']);
  assert.equal(inserted[3].id,wave.points[2].id);
  assert.equal(inserted[3].time,wave.points[2].time);
});

test('vertical-only node or whole-line drags preserve stored time and anchor for collaborative coordinate edits',()=>{
  for(const anchor of [1,null]){
    const wave=futureWave(),chart=chartAt('m30',market([...history,bar(night)]),{objects:[wave]});
    const before=structuredClone(wave),drag={object:before,anchor,value:{time:chart.pointTime(wave.points[1]),price:3067},origin:{time:chart.pointTime(wave.points[1]),price:3065},start:{x:100,y:100},point:{x:100,y:80}};
    const moved=chart.objectDragPreview(drag);
    moved.points.forEach((point,index)=>{assert.equal(point.time,before.points[index].time);assert.deepEqual(point.chart_anchor,before.points[index].chart_anchor);});
  }
});

test('in-progress drawing freezes its coordinate frame while accepting remote edits, then consumes latest bars',()=>{
  const wave=futureWave(),chart=chartAt('m30',market(),{objects:[wave]});chart.path=[structuredClone(wave.points[0])];
  const before=chart.objectPoints(wave),remote=structuredClone(chart.plan);remote.objects[0].color='#00cc88';
  chart.setData(market([...history,bar(night)]),remote);
  assert.equal(chart.bars.at(-1).time,history.at(-1).time);assert.equal(chart.plan.objects[0].color,'#00cc88');
  assert.deepEqual(chart.objectPoints(wave),before);
  chart.cancelPath();
  assert.equal(chart.bars.at(-1).time,night);assert.equal(chart.pendingMarket,null);assert.equal(chart.plan.objects[0].color,'#00cc88');
});

test('small-period automatic price range includes visible mapped forecast extremes',()=>{
  const data=market(history,Array.from({length:10},(_,i)=>bar(history.at(-1).time+i*C.INTERVAL.m3))),wave=futureWave();
  const chart=chartAt('m3',data,{objects:[wave]});chart.view={start:history.at(-1).time,end:history.at(-1).time+8*step,min:3020,max:3053};chart.render();
  assert.ok(chart.view.min<3009&&chart.view.max>3065);
  for(const point of chart.objectPoints(wave))assert.ok(point.y>0&&point.y<300);
  chart.zoom(.8);
  assert.ok(chart.view.min<3009&&chart.view.max>3065,'zooming out cannot snap the scale back to candles alone');
});

test('visible finite segments affect autoscale; hidden, offscreen and infinite trend extensions do not',()=>{
  const bars=[bar(0),bar(step)],view={start:0,end:step,min:3020,max:3053};
  const objects=[{kind:'wave',period:'m30',points:[{time:-step,price:2900},{time:2*step,price:3200}]},
    {kind:'segment',points:[{time:.4*step,price:3070},{time:.6*step,price:3070}]},
    {kind:'wave',visible:false,points:[{time:.5*step,price:9000}]},{kind:'wave',opacity:0,points:[{time:.5*step,price:8000}]},
    {kind:'wave',points:[{time:2*step,price:7000},{time:3*step,price:8000}]},
    {kind:'trend',points:[{time:.4*step,price:10000},{time:.6*step,price:20000}]}];
  const fit=C.fitVisiblePrice(bars,view,1,'m30',objects);
  assert.ok(fit.min<3000&&fit.max>3100);assert.ok(fit.min>2900&&fit.max<3200);
});

test('fit all and locating a layer use resolved anchors and include all nodes',()=>{
  const wave=futureWave(),chart=chartAt('m3',market([...history,bar(night)]),{objects:[wave]});
  for(const action of [()=>chart.fit(true),()=>chart.locateObject(wave)]){
    action();for(const point of chart.objectPoints(wave))assert.ok(point.x>=0&&point.x<=800&&point.y>=0&&point.y<=300,JSON.stringify(point));
  }
});

test('zoom out never shrinks a view already wider than 700 slots or its configured limit',()=>{
  for(const count of [900,12000,25000]){
    const before={start:history[0].time,end:history.at(-1).time+step*count,min:2980,max:3070},scale=C.timeScale(history,'m30');
    const after=C.zoomView(before,'m30',.8,null,history);
    assert.ok(scale.position(after.end)-scale.position(after.start)>=scale.position(before.end)-scale.position(before.start));
  }
});

test('fit all then zoom out keeps distant legacy forecast nodes visible and repeated zoom can reach them',()=>{
  const base=history.at(-1).time,legacy={id:'legacy',kind:'wave',period:'m30',points:C.wavePoints([100,700,900,1400].map((offset,index)=>({time:base+offset*C.INTERVAL.m3,price:[3040,3065,3009,3058][index]})))},chart=chartAt('m3',market(history,history),{objects:[legacy]});
  chart.fit(true);const before=chart.view.end-chart.view.start;chart.zoom(.8);
  assert.ok(chart.view.end-chart.view.start>=before);
  for(const point of chart.objectPoints(legacy))assert.ok(point.x>=0&&point.x<=800&&point.y>=0&&point.y<=300,JSON.stringify(point));
  chart.fit();for(let i=0;i<35;i++)chart.zoom(.8);
  for(const point of chart.objectPoints(legacy))assert.ok(point.x>=0&&point.x<=800&&point.y>=0&&point.y<=300,JSON.stringify(point));
});

test('forecast line, numbered nodes and remote preview use identical resolved coordinates',()=>{
  const old=global.document,make=tag=>({tag,attrs:{},children:[],setAttribute(key,value){this.attrs[key]=value;},append(...children){this.children.push(...children);}});
  global.document={createElementNS:(_,tag)=>make(tag)};
  try{
    const wave=futureWave(),chart=chartAt('m30',market([...history,bar(night)]),{objects:[wave]}),layer=make('g');chart.uid='test';chart.drawObject(layer,wave,{selection:false});
    const flatten=node=>[node,...node.children.flatMap(flatten)],nodes=flatten(layer),line=nodes.find(n=>n.attrs['data-wave-line']==='wave'),coords=line.attrs.points.split(' ').map(point=>point.split(',').map(Number));
    const circles=nodes.filter(n=>n.attrs['data-wave-node']);assert.equal(circles.length,4);
    circles.forEach((circle,index)=>{close(Number(circle.attrs.cx),coords[index][0]);close(Number(circle.attrs.cy),coords[index][1]);});
  }finally{global.document=old;}
});

test('setData redraws a corrected penultimate candle even when token and latest candle are unchanged',()=>{
  for(const [field,value] of [['open',3037],['high',3056],['low',3017],['close',3044],['volume',1234],['oi',-35]]){
    const data={...market(),history_token:'same-prefix-token'},chart=chartAt('m30',data),render=chart.render;
    let renders=0;chart.render=function(){renders++;render.call(this);};
    const corrected=structuredClone(data);corrected.periods.m30.at(-2)[field]=value;
    chart.setData(corrected,{objects:[]});
    assert.equal(renders,1,field+' correction must redraw');
    assert.equal(chart.bars.at(-2)[field],value);
    assert.deepEqual(chart.bars.at(-1),data.periods.m30.at(-1));
    chart.setData(structuredClone(corrected),{objects:[]});
    assert.equal(renders,1,'identical refreshed tail must still skip a redundant render');
  }
});

// Anonymous trading-session fixture: drawings made after the night close must
// survive each later opening, not merely the first gap in a single snapshot.
const lateHistory=['2026-11-04T21:00:00','2026-11-04T21:30:00','2026-11-04T22:00:00','2026-11-04T22:30:00'].map(value=>bar(ms(value)));
const reopenTimes=['2026-11-05T09:00:00','2026-11-05T09:30:00','2026-11-05T10:00:00','2026-11-05T10:30:00','2026-11-05T11:00:00','2026-11-05T13:30:00','2026-11-05T14:00:00','2026-11-05T14:30:00','2026-11-05T21:00:00','2026-11-05T21:30:00','2026-11-05T22:00:00','2026-11-05T22:30:00','2026-11-06T09:00:00'].map(ms);
function lateWave(){return{id:'anonymous-forecast',kind:'wave',period:'m30',points:C.wavePoints([1.25,5.5,9.75,14.5].map((offset,index)=>C.anchorPoint({time:lateHistory.at(-1).time+offset*step,price:[3040,3062,3010,3050][index]},lateHistory,'m30')))};}
function fixedLateChart(data=market(lateHistory),plan={objects:[]},period='m30'){
  const chart=chartAt(period,data,plan);chart.autoPrice=false;chart.followLatest=false;
  chart.view={start:lateHistory[0].time,end:lateHistory.at(-1).time+20*step,min:2980,max:3070};chart.render();return chart;
}
function eventChart(data=market(lateHistory),plan={objects:[]},period='m30'){
  const chart=fixedLateChart(data,plan,period),added=[],updates=[];
  chart.host.style={};chart.host.focus=()=>{};chart.svg={getBoundingClientRect:()=>({left:0,top:0,width:800,height:300}),setPointerCapture(){},hasPointerCapture:()=>false};
  chart.callbacks={snapEnabled:()=>false,add(object){added.push(object);chart.plan.objects.push({...object,id:'created-'+added.length});},update(id,patch){updates.push({id,patch});const object=chart.plan.objects.find(o=>o.id===id);if(object)Object.assign(object,patch);}};
  const event=(point,at=1000)=>({clientX:point.x,clientY:point.y,button:0,pointerId:1,timeStamp:at,detail:1,preventDefault(){}});
  return{chart,added,updates,event};
}

test('midnight snapshot, morning, lunch and successive night openings preserve every forecast node',()=>{
  const wave=lateWave(),plan={objects:[wave]},chart=fixedLateChart(market(lateHistory),plan),before=chart.objectPoints(wave),saved=JSON.stringify(plan);
  const snapshots=[lateHistory,...reopenTimes.map((_,index)=>[...lateHistory,...reopenTimes.slice(0,index+1).map(bar)])];
  for(const rows of snapshots){
    chart.setData(market(rows),JSON.parse(saved));
    chart.objectPoints(chart.plan.objects[0]).forEach((point,index)=>{close(point.x,before[index].x);close(point.y,before[index].y);});
    assert.equal(JSON.stringify(chart.plan),saved,'a market refresh cannot convert or overwrite user coordinates');
    const reloaded=chartAt('m30',market(structuredClone(rows)),JSON.parse(saved));reloaded.view={...chart.view};reloaded.autoPrice=false;reloaded.followLatest=false;reloaded.render();
    reloaded.objectPoints(reloaded.plan.objects[0]).forEach((point,index)=>{close(point.x,before[index].x);close(point.y,before[index].y);});
  }
});

test('peers with different history prefixes agree across repeated session openings while the base candle is retained',()=>{
  const wave=lateWave(),saved=JSON.stringify(wave);
  for(const count of [1,6,9,13]){
    const rows=[...lateHistory,...reopenTimes.slice(0,count).map(bar)],prefix=[bar(lateHistory[0].time-2*step),bar(lateHistory[0].time-step),...rows];
    const full=fixedLateChart(market(rows),{objects:[wave]});
    for(const peerRows of [prefix,rows.slice(2)]){
      const peer=fixedLateChart(market(peerRows),{objects:[JSON.parse(saved)]});peer.view={...full.view};peer.render();
      assert.deepEqual(peer.plan.objects[0].points.map(point=>peer.pointTime(point)),wave.points.map(point=>full.pointTime(point)));
      peer.objectPoints(peer.plan.objects[0]).forEach((point,index)=>close(point.x,full.objectPoints(wave)[index].x));
    }
  }
  assert.equal(JSON.stringify(wave),saved);
});

test('cross-period reload uses source anchors after morning, lunch and night opens',()=>{
  const wave=lateWave();
  for(const count of [1,6,9,13]){
    const source=[...lateHistory,...reopenTimes.slice(0,count).map(bar)],m3=source.flatMap(row=>Array.from({length:10},(_,index)=>bar(row.time+index*C.INTERVAL.m3))),data=market(source,m3);
    const sourceChart=fixedLateChart(data,{objects:[wave]}),peer=chartAt('m3',JSON.parse(JSON.stringify(data)),{objects:[JSON.parse(JSON.stringify(wave))]});
    const resolved=wave.points.map(point=>sourceChart.pointTime(point));
    assert.deepEqual(peer.plan.objects[0].points.map(point=>peer.pointTime(point)),resolved);
    peer.objectPoints(peer.plan.objects[0]).forEach((point,index)=>close(point.x,peer.tx.x(resolved[index])));
  }
});

test('source-period-only refresh redraws mapped anchors even when target candles and plan are unchanged',()=>{
  const wave=lateWave(),m3=[bar(lateHistory.at(-1).time),bar(lateHistory.at(-1).time+C.INTERVAL.m3)],chart=fixedLateChart(market(lateHistory,m3),{objects:[wave]},'m3');
  const render=chart.render;let renders=0,painted=chart.objectPoints(wave);
  chart.render=function(){renders++;render.call(this);painted=this.objectPoints(wave);};
  const before=painted.map(point=>point.x);
  chart.setData(market([...lateHistory,bar(reopenTimes[0])],m3),chart.plan);
  assert.equal(renders,1,'the source timeframe is part of the drawing render dependency');
  assert.notDeepEqual(painted.map(point=>point.x),before);
  painted.forEach((point,index)=>close(point.x,chart.tx.x(chart.pointTime(wave.points[index]))));
  chart.setData(market([...lateHistory,bar(reopenTimes[0])],m3),chart.plan);
  assert.equal(renders,1,'an identical source timeline does not redraw');
});

test('new historical forecast coordinates carry explicit null while each future coordinate carries its source anchor',()=>{
  for(const time of [lateHistory[0].time,lateHistory.at(-1).time,lateHistory.at(-1).time+step*.25]){
    const point=C.anchorPoint({time,price:3040},lateHistory,'m30');
    assert.ok(Object.hasOwn(point,'chart_anchor'));
    if(time<=lateHistory.at(-1).time)assert.equal(point.chart_anchor,null);
    else assert.deepEqual(point.chart_anchor,{period:'m30',base_time:lateHistory.at(-1).time,offset:.25});
  }
});

test('actual wave and legacy path drawing events anchor every new future node before double-click completion',()=>{
  for(const kind of ['wave','path']){
    const {chart,added,event}=eventChart();chart.tool=kind;
    const values=[{time:lateHistory[1].time,price:3037},...[[2.25,3057],[5.5,3021],[10,3052]].map(([offset,price])=>({time:lateHistory.at(-1).time+offset*step,price}))];
    values.forEach((value,index)=>{const point={x:chart.tx.x(value.time),y:chart.tx.y(value.price)};chart.down(event(point,1000+index*1000));chart.up(event(point,1100+index*1000));});
    const last=values.at(-1),point={x:chart.tx.x(last.time),y:chart.tx.y(last.price)};
    chart.down(event(point,4200));chart.doubleClick(event(point,4201));
    assert.equal(added.length,1);assert.equal(added[0].points.length,4);assert.equal(chart.path.length,0);
    assert.equal(added[0].points[0].chart_anchor,null);
    added[0].points.slice(1).forEach((point,index)=>{assert.equal(point.time,values[index+1].time);assert.equal(point.chart_anchor.period,'m30');assert.equal(point.chart_anchor.base_time,lateHistory.at(-1).time);});
    if(kind==='wave')assert.deepEqual(added[0].points.map(point=>point.label),['1','2','3','4']);
    const before=chart.objectPoints(added[0]);chart.setData(market([...lateHistory,bar(reopenTimes[0])]),chart.plan);
    chart.objectPoints(added[0]).forEach((point,index)=>close(point.x,before[index].x));
  }
});

test('actual horizontal node drag and double-click insertion emit anchored points and keep untouched node IDs',()=>{
  const wave=lateWave(),{chart,updates,event}=eventChart(market(lateHistory),{objects:[wave]}),before=structuredClone(chart.plan.objects[0]);
  const start=chart.objectPoints(before)[1],end={x:start.x+25,y:start.y+5};
  chart.down(event(start,1000));chart.move(event(end,1100));chart.up(event(end,1200));
  assert.equal(updates.length,1);const moved=chart.plan.objects[0];assert.equal(moved.points[1].id,before.points[1].id);assert.ok(moved.points[1].chart_anchor);assert.notEqual(moved.points[1].time,before.points[1].time);
  assert.deepEqual(moved.points[0],before.points[0]);assert.deepEqual(moved.points[2],before.points[2]);
  const screen=chart.objectPoints(moved),middle={x:(screen[1].x+screen[2].x)/2,y:(screen[1].y+screen[2].y)/2};chart.doubleClick(event(middle,2000));
  assert.equal(updates.length,2);const inserted=chart.plan.objects[0].points;
  assert.equal(inserted.length,5);assert.ok(inserted[2].chart_anchor);assert.equal(inserted[2].chart_anchor.period,'m30');assert.equal(inserted[3].id,before.points[2].id);assert.deepEqual(inserted.map(point=>point.label),['1','2','3','4','5']);
  const positions=chart.objectPoints(chart.plan.objects[0]);chart.setData(market([...lateHistory,...reopenTimes.slice(0,9).map(bar)]),chart.plan);
  chart.objectPoints(chart.plan.objects[0]).forEach((point,index)=>close(point.x,positions[index].x));
});

test('legacy path midpoint coinciding with an existing bare node is still a newly anchored coordinate',()=>{
  const base=lateHistory.at(-1).time,path={id:'legacy-path',kind:'path',period:'m30',points:[{time:base+2*step,price:3040},{time:base+6*step,price:3060},{time:base+4*step,price:3050}]};
  const chart=fixedLateChart(market(lateHistory),{objects:[path]}),points=chart.editForecastPoints(path,0,1);
  assert.equal(points.length,4);assert.deepEqual(points[0],path.points[0]);assert.deepEqual(points[2],path.points[1]);assert.deepEqual(points[3],path.points[2]);
  assert.deepEqual(points[1].chart_anchor,{period:'m30',base_time:base,offset:4});
  assert.ok(points.every(point=>!Object.hasOwn(point,'sourceIndex')),'temporary provenance never leaks into shared data');
});

test('mapped wave horizontal edits in a smaller chart explicitly distinguish its history from its future',()=>{
  const wave=lateWave(),m3=[...lateHistory,...Array.from({length:20},(_,index)=>bar(reopenTimes[0]+index*C.INTERVAL.m3))],data=market([...lateHistory,bar(reopenTimes[0])],m3);
  const chart=chartAt('m3',data,{objects:[wave]});chart.autoPrice=false;chart.followLatest=false;chart.render();
  const start=chart.objectPoints(wave)[0];
  for(const time of [m3.at(-5).time,m3.at(-1).time+C.INTERVAL.m3*2.5]){
    const value={time,price:3041},point={x:chart.tx.x(time),y:chart.tx.y(value.price)},moved=chart.objectDragPreview({object:structuredClone(wave),anchor:0,origin:{time:chart.pointTime(wave.points[0]),price:wave.points[0].price},value,start,point});
    assert.equal(moved.points[0].id,wave.points[0].id);assert.equal(moved.points[0].time,time);assert.ok(Object.hasOwn(moved.points[0],'chart_anchor'));
    if(time<=m3.at(-1).time)assert.equal(moved.points[0].chart_anchor,null);
    else assert.deepEqual(moved.points[0].chart_anchor,{period:'m3',base_time:m3.at(-1).time,offset:2.5});
  }
});

test('completing a drawing after a buffered opening preserves the frame in which its points were placed',()=>{
  const {chart,added,event}=eventChart();chart.tool='wave';
  const values=[{time:lateHistory.at(-1).time+2.5*step,price:3040},{time:lateHistory.at(-1).time+8.5*step,price:3060}],expected=values.map(value=>({x:chart.tx.x(value.time),y:chart.tx.y(value.price)}));
  chart.down(event(expected[0],1000));chart.setData(market([...lateHistory,bar(reopenTimes[0])]),chart.plan);
  assert.equal(chart.bars.at(-1).time,lateHistory.at(-1).time);
  chart.down(event(expected[1],2000));chart.down(event(expected[1],2200));
  assert.equal(added.length,1);assert.equal(chart.pendingMarket,null);assert.equal(chart.bars.at(-1).time,reopenTimes[0]);
  added[0].points.forEach((point,index)=>{assert.equal(point.time,values[index].time);assert.equal(point.chart_anchor.base_time,lateHistory.at(-1).time);});
  chart.objectPoints(added[0]).forEach((point,index)=>{close(point.x,expected[index].x);close(point.y,expected[index].y);});
});

const nativeCutover=['2026-11-05T09:00:00','2026-11-05T09:30:00','2026-11-05T10:00:00','2026-11-05T10:30:00','2026-11-05T11:00:00'].map(value=>bar(ms(value)));
const tradingCutover=['2026-11-05T09:00:00','2026-11-05T09:30:00','2026-11-05T10:00:00','2026-11-05T10:45:00','2026-11-05T11:15:00'].map(value=>bar(ms(value)));
const tradingGrowth=['2026-11-05T13:45:00','2026-11-05T14:15:00','2026-11-05T14:45:00','2026-11-05T21:00:00','2026-11-05T21:30:00','2026-11-05T22:00:00','2026-11-05T22:30:00','2026-11-06T09:00:00','2026-11-06T09:30:00','2026-11-06T10:00:00','2026-11-06T10:45:00','2026-11-06T11:15:00'].map(ms);
const cutoverReference=()=>({basis:'native_v1',target_basis:'trading_minutes_v1',source_times:nativeCutover.map(row=>row.time),target_base_time:tradingCutover.at(-1).time});
function versionedMarket(trading=tradingCutover,native=nativeCutover,reference=cutoverReference()){
  return{...market(trading,trading),candle_basis:{day:'native_v1',m30:'trading_minutes_v1',m3:'native_v1'},anchor_periods:{m30:native.map(row=>({dt:row.dt}))},...(reference?{anchor_reference:{m30:reference}}:{})};
}
function cutoverWave(){return{id:'cutover-wave',kind:'wave',period:'m30',points:C.wavePoints([1.25,3.5,6.25,9.5].map((offset,index)=>C.anchorPoint({time:nativeCutover.at(-1).time+offset*step,price:[3040,3061,3012,3054][index]},nativeCutover,'m30')))};}

test('old and new anchor bases resolve against their own timelines even when a native base vanished',()=>{
  const data=versionedMarket(tradingCutover,[...nativeCutover,bar(ms('2026-11-05T13:30:00'))],null);
  const old={time:ms('2026-11-05T11:30:00'),price:3040,chart_anchor:{period:'m30',base_time:ms('2026-11-05T10:30:00'),offset:2}};
  assert.equal(C.resolvePointTime(old,data),ms('2026-11-05T13:30:00'));
  assert.equal(C.resolvePointTime({...old,chart_anchor:{...old.chart_anchor,basis:'native_v1'}},data),ms('2026-11-05T13:30:00'));
  const sameBase={time:0,price:3040,chart_anchor:{period:'m30',base_time:ms('2026-11-05T10:00:00'),offset:1}};
  assert.equal(C.resolvePointTime(sameBase,data),ms('2026-11-05T10:30:00'));
  const modern={...sameBase,chart_anchor:{...sameBase.chart_anchor,basis:'trading_minutes_v1'}};
  assert.equal(C.resolvePointTime(modern,data),ms('2026-11-05T10:45:00'));
  const chart=chartAt('m30',data,{objects:[]});
  for(const point of [sameBase,modern,old,modern,sameBase])assert.equal(chart.pointTime(point),C.resolvePointTime(point,data),'cached scales are separated by basis');
});

test('fixed cutover reference preserves future forecast spacing through lunch, night and next-day opens',()=>{
  const wave=cutoverWave(),saved=JSON.stringify({objects:[wave]}),chart=chartAt('m30',market(nativeCutover),JSON.parse(saved));
  chart.view={start:nativeCutover[0].time,end:nativeCutover.at(-1).time+12*step,min:2980,max:3070};chart.autoPrice=false;chart.followLatest=false;chart.render();
  const positions=chart.objectPoints(wave),oldEnd=chart.view.end;
  chart.setData(versionedMarket(),JSON.parse(saved));
  assert.equal(chart.view.end,oldEnd+15*60000,'future viewport endpoint follows the same fixed cutover map');
  for(let count=0;count<=tradingGrowth.length;count++){
    const rows=[...tradingCutover,...tradingGrowth.slice(0,count).map(bar)],liveNative=[...nativeCutover,...tradingGrowth.slice(0,count).map(time=>bar(time-15*60000))],data=versionedMarket(rows,liveNative);
    chart.setData(data,JSON.parse(saved));
    chart.objectPoints(chart.plan.objects[0]).forEach((point,index)=>{close(point.x,positions[index].x);close(point.y,positions[index].y);});
    assert.equal(JSON.stringify(chart.plan),saved,'compatibility projections do not migrate user data');
    const reload=chartAt('m30',JSON.parse(JSON.stringify(data)),JSON.parse(saved));reload.view={...chart.view};reload.autoPrice=false;reload.followLatest=false;reload.render();
    reload.objectPoints(reload.plan.objects[0]).forEach((point,index)=>close(point.x,positions[index].x));
  }
});

test('fixed source history is immutable in meaning and absolute historical points retain their time',()=>{
  const reference=cutoverReference(),data=versionedMarket([...tradingCutover,bar(tradingGrowth[0])],[],reference);
  const historical={time:0,price:3040,chart_anchor:{period:'m30',base_time:nativeCutover[0].time,offset:3.5}},absolute={time:ms('2026-11-05T10:30:00'),price:3040,chart_anchor:null};
  assert.equal(C.resolvePointTime(historical,data),ms('2026-11-05T10:45:00'));
  assert.equal(C.resolvePointTime(absolute,data),absolute.time);
  const saved=JSON.stringify([historical,absolute,reference]);
  for(const rows of [nativeCutover,[],[...nativeCutover,bar(tradingGrowth[0])]])assert.equal(C.resolvePointTime(historical,versionedMarket(data.periods.m30,rows,reference)),ms('2026-11-05T10:45:00'));
  assert.equal(JSON.stringify([historical,absolute,reference]),saved);
});

test('fixed-reference forecast coordinates agree on smaller periods and peers with trimmed retained history',()=>{
  const wave=cutoverWave(),rows=[...tradingCutover,...tradingGrowth.map(bar)],data=versionedMarket(rows),main=chartAt('m30',data,{objects:[wave]}),expected=wave.points.map(point=>main.pointTime(point));
  for(const period of ['m30','m3'])for(const target of [rows,rows.slice(3)]){
    const peer=chartAt(period,versionedMarket(target),{objects:[JSON.parse(JSON.stringify(wave))]});
    assert.deepEqual(peer.plan.objects[0].points.map(point=>peer.pointTime(point)),expected);
  }
  const missingBase=versionedMarket(rows.slice(5));
  assert.deepEqual(wave.points.map(point=>C.resolvePointTime(point,missingBase)),wave.points.map(point=>point.time),'missing persisted target base never guesses a new reference');
});

test('anchor reference arrival and native-only timeline changes invalidate an otherwise unchanged chart',()=>{
  const wave=cutoverWave(),chart=chartAt('m30',versionedMarket(tradingCutover,nativeCutover,null),{objects:[wave]}),render=chart.render;chart.autoPrice=false;chart.followLatest=false;
  let paints=0;chart.render=function(){paints++;render.call(this);};
  chart.setData(versionedMarket(),chart.plan);assert.equal(paints,1,'fixed reference installation must repaint old anchors');
  chart.setData(JSON.parse(JSON.stringify(versionedMarket())),chart.plan);assert.equal(paints,1,'identical metadata need not repaint');
  chart.setData(versionedMarket(tradingCutover,[...nativeCutover,bar(tradingGrowth[0])]),chart.plan);assert.equal(paints,2);
});

test('new drawings and inserted or horizontally moved future points carry the current basis',()=>{
  const data={...market(lateHistory),candle_basis:{m30:'trading_minutes_v1'}},h=eventChart(data),{chart,added,event}=h;chart.tool='wave';
  for(const [offset,price,at]of [[2,3040,1000],[6,3060,2000]])chart.down(event({x:chart.tx.x(lateHistory.at(-1).time+offset*step),y:chart.tx.y(price)},at));
  chart.finishPath();assert.ok(added[0].points.every(point=>point.chart_anchor.basis==='trading_minutes_v1'));
  const object=chart.plan.objects[0];chart.tool='select';const first=chart.objectPoints(object)[0];chart.down(event(first,3000));chart.move(event({x:first.x+20,y:first.y},3100));chart.up(event({x:first.x+20,y:first.y},3200));
  assert.equal(object.points[0].chart_anchor.basis,'trading_minutes_v1');
  const screen=chart.objectPoints(object),mid={x:(screen[0].x+screen[1].x)/2,y:(screen[0].y+screen[1].y)/2};chart.doubleClick(event(mid,4000));
  assert.equal(object.points[1].chart_anchor.basis,'trading_minutes_v1');
});

test('vertical edits retain a legacy reference while horizontal edits adopt the new basis',()=>{
  const wave=cutoverWave(),chart=chartAt('m30',versionedMarket(),{objects:[wave]}),start=chart.objectPoints(wave)[1],origin={time:chart.pointTime(wave.points[1]),price:wave.points[1].price};
  const vertical=chart.objectDragPreview({object:structuredClone(wave),anchor:1,origin,value:{...origin,price:origin.price+2},start,point:{x:start.x,y:start.y-5}});
  assert.deepEqual(vertical.points[1].chart_anchor,wave.points[1].chart_anchor);assert.equal(vertical.points[1].time,wave.points[1].time);
  const horizontal=chart.objectDragPreview({object:structuredClone(wave),anchor:1,origin,value:{time:chart.tx.time(start.x+20),price:origin.price},start,point:{x:start.x+20,y:start.y}});
  assert.equal(horizontal.points[1].chart_anchor.basis,'trading_minutes_v1');assert.deepEqual(horizontal.points[0],wave.points[0]);
});

test('moving average labels preserve indicator decimals without changing price formatting or calculations',()=>{
  assert.equal(C.indicatorLabel(5990.8,2),'5,990.80');assert.equal(C.indicatorLabel(5996.8,2),'5,996.80');assert.equal(C.indicatorLabel(5984.1,2),'5,984.10');
  assert.equal(C.indicatorLabel(5994,2),'5,994.00');assert.equal(C.indicatorLabel(872.14,.02),'872.14');assert.equal(C.indicatorLabel(null,2),'—');
  assert.equal(C.indicatorLabel(3040.05,1),'3,040.05');assert.equal(C.indicatorLabel(872.141,.02),'872.141');
  assert.equal(C.priceLabel(5990.8,2),'5,991','ordinary market price labels retain their existing precision');
  const rows=Array.from({length:20},(_,index)=>({...bar(index*step),close:index===19?5994:5980+index*2})),chart=chartAt('m30',market(rows));
  for(const length of [5,10,20])assert.equal(chart.getAverages(length).at(-1).value,rows.slice(-length).reduce((sum,row)=>sum+row.close,0)/length);
});
