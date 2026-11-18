'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('./static/workspace-chart.js');
const {Store,PERIODS}=require('./static/workspace-market-cache.js');
const clone=value=>JSON.parse(JSON.stringify(value));
const symbol='QA.ohlc';
const start=Date.parse('2026-11-18T21:00:00+08:00');
const rows=()=>Array.from({length:24},(_,i)=>({dt:new Date(start+i*C.INTERVAL.m3).toISOString(),open:10950+i%4,high:10978+i%3,low:10940,close:10955+i%4,volume:200+i,oi:10000+i}));
function packet(){return{symbol,tick:1,source:'live',fresh:true,connected:true,feed_status:'live',history_token:'raw-prefix',incremental:false,period_counts:{day:24,m30:24,m3:24},periods:Object.fromEntries(PERIODS.map(p=>[p,rows()]))};}
function accept(store,value){const result=store.accept(symbol,value,store.begin(symbol),{readOnly:true});assert.equal(result.accepted,true);return result.market;}
function delta(base){return{...clone(base),incremental:true,periods:Object.fromEntries(PERIODS.map(p=>[p,clone(base.periods[p].slice(-2))]))};}
function fixture(run){
  const original={document:global.document,ResizeObserver:global.ResizeObserver};
  const make=tag=>({tag,attrs:{},style:{},children:[],setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return this.attrs[k];},append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;},addEventListener(){},getBoundingClientRect(){return{left:0,top:0,width:1000,height:600};},focus(){},setPointerCapture(){},hasPointerCapture(){return false;}});
  global.document={createElement:make,createElementNS:(_,tag)=>make(tag)};global.ResizeObserver=class{observe(){}};
  try{return run(new C.Chart(Object.assign(make('div'),{clientWidth:1000,clientHeight:600}),'m3',{}));}finally{Object.assign(global,original);}
}
function candlePair(root,time){for(const [index,node]of root.children.entries()){if(node.attrs['data-candle']===String(time))return{body:node,wick:root.children[index-1]};const result=candlePair(node,time);if(result)return result;}}
function verifyRendered(chart,row){
  const time=Date.parse(row.dt),{body,wick}=candlePair(chart.svg,time),center=Number(body.attrs.x)+Number(body.attrs.width)/2;
  assert.equal(wick.tag,'line');assert.ok(Math.abs(center-chart.tx.x(time))<1e-8);
  assert.ok(Math.abs(Number(wick.attrs.x1)-center)<1e-8);assert.equal(wick.attrs.x1,wick.attrs.x2);
  assert.ok(Math.abs(chart.tx.price(Number(wick.attrs.y1))-row.high)<1e-7);
  assert.ok(Math.abs(chart.tx.price(Number(wick.attrs.y2))-row.low)<1e-7);
  assert.equal(wick.attrs.stroke,row.close>=row.open?'#ef5350':'#22c55e');assert.equal(body.attrs.stroke,wick.attrs.stroke);
  assert.ok(Math.abs(Number(body.attrs.y)-Math.min(chart.tx.y(row.open),chart.tx.y(row.close)))<1e-8);
}

test('wire-tail OHLC corrections preserve timestamp identity, wick center, body and candle direction',()=>fixture(chart=>{
  const store=new Store(),base=accept(store,packet());chart.setData(base,{objects:[]},true);const previousBars=chart.bars;
  const update=delta(base);Object.assign(update.periods.m3.at(-2),{open:10954,close:10962,high:10980});Object.assign(update.periods.m3.at(-1),{open:10970,close:10958,high:10978});
  const next=accept(store,update);chart.setData(next,chart.plan);
  assert.strictEqual(chart.bars[0],previousBars[0]);assert.notStrictEqual(chart.bars.at(-2),previousBars.at(-2));assert.notStrictEqual(chart.bars.at(-1),previousBars.at(-1));
  for(const row of next.periods.m3.slice(-2))verifyRendered(chart,row);
  assert.equal(next.periods.m3.at(-2).high,10980);assert.equal(next.periods.m3.at(-1).high,10978);
}));

test('one earlier close correction of three changes SMA5 by .6 and SMA10 by .3 with the same latest close',()=>fixture(chart=>{
  const store=new Store(),base=accept(store,packet());chart.setData(base,{objects:[]},true);
  const before5=chart.getAverages(5),before10=chart.getAverages(10),latest=chart.bars.at(-1).close;
  const update=delta(base);update.periods.m3.at(-2).close+=3;
  const next=accept(store,update);chart.setData(next,chart.plan);
  assert.equal(chart.bars.at(-1).close,latest);
  assert.notStrictEqual(chart.getAverages(5),before5);assert.notStrictEqual(chart.getAverages(10),before10);
  assert.ok(Math.abs(chart.getAverages(5).at(-1).value-before5.at(-1).value-.6)<1e-8);
  assert.ok(Math.abs(chart.getAverages(10).at(-1).value-before10.at(-1).value-.3)<1e-8);
  next.periods.m3.slice(-2).forEach(row=>verifyRendered(chart,row));
}));

test('append protocol keeps a prior high on its original candle and draws the new candle at its own time',()=>fixture(chart=>{
  const store=new Store(),base=accept(store,packet());chart.setData(base,{objects:[]},true);
  const update=delta(base),last=clone(base.periods.m3.at(-1));update.base_history_token=base.history_token;update.history_token='next-prefix';update.period_drops={day:0,m30:0,m3:0};update.period_counts.m3++;
  update.periods.m3.push({...last,dt:new Date(Date.parse(last.dt)+C.INTERVAL.m3).toISOString(),open:10970,high:10978,close:10958});
  const next=accept(store,update);chart.setData(next,chart.plan);
  assert.deepEqual(next.periods.m3.at(-2),last);next.periods.m3.slice(-3).forEach(row=>verifyRendered(chart,row));
}));
