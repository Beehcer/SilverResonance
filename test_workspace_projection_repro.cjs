'use strict';
// Anonymous regression derived from the 2026-11-18 silver 30m -> 3m report.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');
const ms=value=>Date.parse(value+'+08:00');
const t=value=>ms('2026-11-18T'+value+':00');
const bar=(start,end)=>({time:start,dt:new Date(start).toISOString(),end_time:new Date(end).toISOString(),open:100,high:102,low:98,close:101});
const base=t('14:45'),open=t('21:00');
const source=[bar(t('13:45'),t('14:15')),bar(t('14:15'),base),bar(base,t('15:00'))];
const target=['14:45','14:48','14:51','14:54','14:57','21:00','21:03'].map(value=>bar(t(value),t(value)+C.INTERVAL.m3));
const market=(opened=true)=>({symbol:'QA.projection',tick:1,candle_basis:{m30:'trading_minutes_v1',m3:'native_v1'},periods:{m30:opened?[...source,bar(open,t('21:30'))]:source,m3:target,day:[]}});
function chart(data){
  const result=Object.create(C.Chart.prototype);
  Object.assign(result,{period:'m3',market:data,bars:target,tick:1,anchorScales:{},timeScale:null});
  return result;
}
function point(offset){return{time:base+offset*C.INTERVAL.m30,price:100,chart_anchor:{period:'m30',basis:'trading_minutes_v1',base_time:base,offset}};}

test('fractional source candle slot crosses a session gap on the target trading axis, not on wall time',()=>{
  const data=market(),view=chart(data),node=point(.5),before=JSON.stringify(node),scale=C.timeScale(target,'m3');
  const actual=scale.position(view.pointTime(node));
  const expected=(scale.position(base)+scale.position(open))/2;
  assert.ok(Math.abs(actual-expected)<1e-6,`expected target slot ${expected}, got ${actual}; wall-time interpolation squeezes fractional points into a closed-session interval`);
  assert.equal(JSON.stringify(node),before);
});

test('already synchronized future nodes retain their distance after night open',()=>{
  const data=market(),view=chart(data),scale=C.timeScale(target,'m3'),nodes=[point(2.1979927777779267),point(10.19231111111094)];
  const gap=scale.position(view.pointTime(nodes[1]))-scale.position(view.pointTime(nodes[0]));
  assert.ok(Math.abs(gap-(nodes[1].chart_anchor.offset-nodes[0].chart_anchor.offset)*10)<1e-6);
});

test('target-first opening hides the entire mapped wave on a cold load and preserves its saved points',()=>{
  const data=market(false),view=chart(data),wave={id:'anonymous-wave',kind:'wave',period:'m30',points:[{time:source[0].time,price:101,chart_anchor:null},point(.5),point(2.1979927777779267),point(10.19231111111094)]};
  view.plan={objects:[wave]};const saved=JSON.stringify(wave);
  assert.equal(view.projectionStatus().ready,false);
  assert.deepEqual(view.projectionStatus().reasons,['source_period_behind']);
  assert.equal(view.projectionStatus().blockedCount,1);
  assert.deepEqual(view.objects(),[]);
  assert.equal(view.hit({x:10,y:10}),null);
  assert.deepEqual(view.editForecastPoints(wave,1,1),wave.points);
  view.tx=C.transform({start:target[0].time,end:open+30*C.INTERVAL.m3,min:90,max:110},{x:0,y:0,w:600,h:300},target,'m3');
  view.plot={x:0,y:0,w:600,h:300};view.callbacks={update(){throw Error('Blocked mapped coordinates must never be committed');}};
  view.insertWavePoint(wave.id,{x:200,y:200},1);
  assert.equal(view.objectDragPreview({object:wave,anchor:2,value:{time:open,price:102},point:{x:20,y:20},start:{x:10,y:10}}),null);
  assert.equal(JSON.stringify(wave),saved);
});

test('lagging source, aligned snapshots and refreshes alternate without mutating or reconnecting hidden wave nodes',()=>{
  const wave={id:'alternating-wave',kind:'wave',period:'m30',points:[point(2.1979927777779267),point(10.19231111111094)]},saved=JSON.stringify(wave);
  for(const opened of [false,true,false,true,true,false,true]){
    const data=market(opened),view=chart(data);view.plan={objects:[wave]};
    assert.equal(view.projectionStatus().ready,opened);
    assert.equal(view.objects().length,opened?1:0);
    if(opened){const axis=C.timeScale(target,'m3'),values=wave.points.map(p=>axis.position(view.pointTime(p)));assert.ok(values[1]-values[0]>79);}
    assert.equal(JSON.stringify(wave),saved);
  }
});

test('missing base and fixed cutover evidence wait instead of falling back to bare gap timestamps',()=>{
  const data=market(),node=point(2),view=chart(data);
  data.periods.m30=data.periods.m30.slice(-1);
  assert.equal(view.projectionStatus({id:'missing',kind:'wave',points:[node,point(3)]}).ready,false);
  assert.equal(C.projectPoint(node,data,'m3').reason,'missing_anchor_history');
  const legacy=point(2);legacy.chart_anchor.basis='native_v1';
  data.anchor_reference={m30:{basis:'native_v1',target_basis:'trading_minutes_v1',source_times:source.map(row=>row.time),target_base_time:base}};
  assert.equal(C.projectPoint(legacy,data,'m3').reason,'missing_reference_history');
  data.periods.m30=[...source,bar(open,t('21:30'))];
  assert.equal(C.projectPoint(legacy,data,'m3').ready,true);
  assert.equal(C.projectPoint(legacy,data,'m3').time,open+C.INTERVAL.m30);
});

test('the source leading the small chart also waits for the receiving opening instead of stretching across the gap',()=>{
  const data=market();data.periods.m3=target.slice(0,5);
  assert.equal(C.projectPoint(point(2),data,'m3').reason,'target_period_behind');
});

test('weekend fractional daily anchors interpolate on observed small-period slots',()=>{
  const friday=ms('2026-11-13T00:00:00'),monday=ms('2026-11-16T00:00:00');
  const data={periods:{day:[bar(friday,friday+C.INTERVAL.day),bar(monday,monday+C.INTERVAL.day)],m3:[bar(friday,friday+C.INTERVAL.m3),bar(friday+C.INTERVAL.m3,friday+C.INTERVAL.m3*2),bar(monday,monday+C.INTERVAL.m3)]}};
  const node={time:friday+C.INTERVAL.day*.5,price:100,chart_anchor:{period:'day',base_time:friday,offset:.5}},axis=C.timeScale(data.periods.m3,'m3');
  assert.equal(axis.position(C.projectPoint(node,data,'m3').time),1);
  assert.equal(C.resolvePointTime(node,data),friday+(monday-friday)*.5,'own-period legacy resolver stays unchanged');
});

test('drawing and remote preview skip unavailable mapped artwork and recover when aligned',()=>{
  const originalDocument=global.document,make=tag=>({tag,attrs:{},children:[],setAttribute(k,v){this.attrs[k]=v;},append(...values){this.children.push(...values);}});
  global.document={createElementNS:(_,tag)=>make(tag)};
  try{
    const view=chart(market(false)),wave={id:'drawing',kind:'wave',period:'m30',points:[point(2),point(10)]},layer=make('g');view.plan={objects:[wave]};
    view.drawObject(layer,wave,{selection:false});assert.equal(layer.children.length,0);
    view.market=market(true);view.anchorScales={};view.plot={x:0,y:0,w:600,h:300};view.tx=C.transform({start:target[0].time,end:open+300*C.INTERVAL.m3,min:90,max:110},view.plot,target,'m3');
    view.drawObject(layer,wave,{selection:false});assert.ok(layer.children.length>0);
  }finally{global.document=originalDocument;}
});

test('a wave edited on the small chart retains mixed anchor periods on both synchronized charts',()=>{
  const data=market(),wave={id:'mixed',kind:'wave',period:'m30',points:[point(2),{time:open+8*C.INTERVAL.m3,price:99,chart_anchor:{period:'m3',base_time:target.at(-1).time,offset:7}},point(10)]},saved=JSON.stringify(wave);
  for(const period of ['m3','m30']){
    const view=chart(data);view.period=period;view.bars=data.periods[period];view.plan={objects:[wave]};
    assert.equal(view.projectionStatus().ready,true);
    assert.equal(view.objects().length,1);
    assert.ok(wave.points.every(node=>Number.isFinite(view.pointTime(node))));
    assert.equal(JSON.stringify(wave),saved);
  }
});

test('source lag does not hide a fully historical mapped wave whose anchor evidence is retained',()=>{
  const data=market(false),wave={id:'historical',kind:'wave',period:'m30',points:[{time:source[0].time,price:99,chart_anchor:null},{time:source[1].time,price:101,chart_anchor:{period:'m30',basis:'trading_minutes_v1',base_time:source[0].time,offset:1}},{time:base,price:100,chart_anchor:null}]},view=chart(data);
  view.plan={objects:[wave]};assert.equal(view.projectionStatus().ready,true);assert.equal(view.objects().length,1);
  assert.deepEqual(wave.points.map(node=>view.pointTime(node)),wave.points.map(node=>node.time));
});

test('setData invalidates projection caches across alternating partial and aligned snapshots without a saved prior frame',()=>{
  const wave={id:'cached',kind:'wave',period:'m30',points:[point(2),point(10)]},saved=JSON.stringify(wave),observed=[];
  const view=Object.create(C.Chart.prototype);Object.assign(view,{period:'m3',callbacks:{dataDisplayed(chart){observed.push(chart.projectionStatus().ready);}},bars:[],plan:{objects:[]},view:null,tick:1,path:[],drag:null,autoPrice:true,plot:{x:0,y:0,w:600,h:300},host:{clientWidth:667},tool:'select',selected:null});
  view.render=function(){if(this.view){this.tx=C.transform(this.view,this.plot,this.bars,this.period);this.painted=this.objects().map(item=>item.id);}};
  for(const [index,opened] of [false,true,false,true].entries()){
    view.setData(market(opened),{objects:[wave]},index===0);
    assert.equal(view.projectionStatus().ready,opened);
    assert.deepEqual(view.painted,opened?['cached']:[]);
    assert.equal(JSON.stringify(wave),saved);
  }
  assert.deepEqual(observed,[false,true,false,true]);
});
