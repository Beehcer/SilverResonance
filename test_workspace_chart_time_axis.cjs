'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');
const ms=value=>Date.parse(value+'+08:00'),step=C.INTERVAL.m30;
const row=time=>({time,open:100,high:106,low:95,close:103,volume:10,oi:1});
const bars=['2026-11-05T09:00:00','2026-11-05T09:30:00','2026-11-05T10:00:00','2026-11-05T10:45:00','2026-11-05T11:15:00','2026-11-05T13:45:00','2026-11-05T14:15:00','2026-11-05T14:45:00','2026-11-05T21:00:00','2026-11-05T21:30:00','2026-11-06T09:00:00','2026-11-06T09:30:00'].map(value=>row(ms(value)));
const same=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
function ticks(width=800,view={start:bars[0].time,end:bars.at(-1).time+step*4,min:90,max:110},period='m30',data=bars){
  const plot={x:5,y:5,w:width,h:200},tx=C.transform(view,plot,data,period);
  return{ticks:C.timeAxisTicks(data,view,plot,period,tx),tx,plot,view};
}

test('historical time ticks land on actual candles across breaks and multiple dates',()=>{
  const result=ticks(),history=result.ticks.filter(tick=>tick.kind==='history');
  assert.ok(history.length>=3);assert.ok(history.some(tick=>tick.label.startsWith('11/06')));
  for(const tick of history){
    assert.ok(bars.some(bar=>bar.time===tick.time));same(tick.x,result.tx.x(tick.time));
    const actual=new Date(tick.time+8*3600000).toISOString().slice(5,16).replace('-','/').replace('T',' ');
    assert.equal(tick.label,actual);
    assert.doesNotMatch(tick.label,/09:22|09:37/);
  }
});

test('time labels use Beijing date and clock regardless of browser/system timezone',()=>{
  const previous=process.env.TZ;
  try{
    process.env.TZ='America/Los_Angeles';const west=ticks().ticks.map(tick=>tick.label);
    process.env.TZ='Europe/London';assert.deepEqual(ticks().ticks.map(tick=>tick.label),west);
    const overnight=[row(ms('2026-11-06T00:00:00')),row(ms('2026-11-06T00:30:00'))];
    const selected=ticks(200,{start:overnight[0].time,end:overnight[1].time,min:90,max:110},'m30',overnight).ticks;
    assert.ok(selected.length);assert.ok(selected.every(tick=>tick.label.startsWith('11/06 00:')));
  }finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});

test('future-only axes show whole candle offsets instead of invented reopening times',()=>{
  const last=bars.at(-1).time,result=ticks(800,{start:last+step*2,end:last+step*18,min:90,max:110});
  assert.ok(result.ticks.length>=3);
  for(const tick of result.ticks){
    assert.equal(tick.kind,'future');assert.ok(Number.isInteger(tick.offset)&&tick.offset>0);
    assert.equal(tick.label,`未来 +${tick.offset}根`);assert.equal(tick.time,undefined);
    same(tick.x,result.tx.x(last+tick.offset*step));
    assert.doesNotMatch(tick.label,/\d{2}:\d{2}|\d{2}\/\d{2}/);
  }
});

test('axis density and clamped labels remain within small screens without overlap',()=>{
  for(const width of [80,120,180,280,390,700,1400]){
    const result=ticks(width);
    assert.ok(result.ticks.length<=Math.max(1,Math.min(8,Math.floor(width/96))));
    for(let index=0;index<result.ticks.length;index++){
      const tick=result.ticks[index];assert.ok(tick.labelX>=result.plot.x);assert.ok(tick.right<=result.plot.x+width);
      if(index)assert.ok(tick.labelX>=result.ticks[index-1].right+10);
    }
  }
});

test('daily ticks show actual Beijing calendar dates and no intraday or synthetic future clocks',()=>{
  const days=['2026-11-06T00:00:00','2026-11-09T00:00:00','2026-11-10T00:00:00'].map(value=>row(ms(value))),result=ticks(420,{start:days[0].time,end:days.at(-1).time+C.INTERVAL.day*3,min:90,max:110},'day',days);
  assert.ok(result.ticks.some(tick=>tick.kind==='history'));
  for(const tick of result.ticks)if(tick.kind==='history'){assert.match(tick.label,/^11\/(06|09|10)$/);same(tick.x,result.tx.x(tick.time));}else assert.match(tick.label,/^未来 \+\d+根$/);
});

test('opening candles replace future labels with actual timestamps without relabeling closed-session gaps',()=>{
  const before=bars.slice(0,8),after=bars.slice(0,10),view={start:before.at(-4).time,end:before.at(-1).time+step*7,min:90,max:110};
  const initial=ticks(600,view,'m30',before),nextView=C.preserveView(view,before,after,'m30',false),opened=ticks(600,nextView,'m30',after);
  assert.ok(initial.ticks.some(tick=>tick.kind==='future'));
  assert.ok(opened.ticks.some(tick=>tick.label==='11/05 21:00'||tick.label==='11/05 21:30'));
  for(const tick of opened.ticks.filter(tick=>tick.kind==='history'))assert.ok(after.some(bar=>bar.time===tick.time));
  assert.ok(!opened.ticks.some(tick=>/15:|16:|17:|18:/.test(tick.label)));
});

test('actual SVG axis aligns its history grids while free crosshair and point conversion stay unsnapped',()=>{
  const previous=global.document,make=tag=>({tag,attrs:{},children:[],style:{},setAttribute(key,value){this.attrs[key]=String(value);},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];}});
  global.document={createElementNS:(_,tag)=>make(tag)};
  try{
    const chart=Object.create(C.Chart.prototype);
    Object.assign(chart,{uid:'axis-test',period:'m30',host:{clientWidth:800,clientHeight:350},svg:make('svg'),bars,plan:{objects:[]},callbacks:{snapEnabled:()=>true},tick:1,path:[],peers:[],tool:'select',autoPrice:false,view:{start:bars[0].time,end:bars.at(-1).time+step*4,min:90,max:110},market:{source:'live',fresh:true,periods:{m30:bars}}});
    chart.render();const oldView={...chart.view},cursor={x:chart.plot.x+chart.plot.w*.4137,y:chart.plot.y+55.3};chart.crosshair=cursor;chart.render();
    const walk=node=>[node,...node.children.flatMap(walk)],nodes=walk(chart.svg),labels=nodes.filter(node=>node.attrs['data-time-axis']),grids=nodes.filter(node=>node.attrs['data-time-grid']);
    assert.ok(labels.length>=3);assert.equal(labels.length,grids.length);
    labels.forEach((label,index)=>{if(label.attrs['data-time-axis']==='history'){const time=Number(label.attrs['data-bar-time']);assert.ok(bars.some(bar=>bar.time===time));same(Number(grids[index].attrs.x1),chart.tx.x(time));assert.match(label.textContent,/^11\/0[56] \d{2}:\d{2}$/);}else assert.match(label.textContent,/^未来 \+\d+根$/);});
    const crosshair=nodes.find(node=>node.attrs['data-crosshair']==='vertical');same(Number(crosshair.attrs.x1),cursor.x);
    const value=chart.toValue(cursor,false,true);assert.ok(!bars.some(bar=>bar.time===value.time));same(value.time,Math.round(chart.tx.time(cursor.x)));assert.deepEqual(chart.view,oldView);
  }finally{global.document=previous;}
});
