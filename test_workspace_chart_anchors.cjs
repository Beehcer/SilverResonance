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
