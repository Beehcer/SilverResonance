'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const C=require('./static/workspace-chart.js'),source=fs.readFileSync('static/workspace.js','utf8');
const start=source.indexOf('  function renderChartHeader('),end=source.indexOf('\n  function renderFeedMotion(',start);
const A='SHFE.ag2702',B='DCE.pp2701';
function market(price=11039,time='2026-11-18T14:45:00+08:00'){
  return{symbol:A,tick:1,source:'live',connected:true,fresh:true,feed_status:'live',periods:{m30:[{dt:time,open:price-1,high:price+2,low:price-2,close:price,volume:20,oi:-3}],m3:[],day:[]}};
}
function harness(){
  const nodes=new Map(),state={symbol:A,market:market()},charts={},pulses=[];
  const node=(text='')=>({textContent:text,dataset:{},style:{},title:'',replacements:0,replaceChildren(...children){this.textContent=children.map(c=>c.textContent).join('');this.replacements++;}});
  const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  const context=vm.createContext({state,charts,$,C,PERIODS:{day:'日线',m30:'30 分钟',m3:'3 分钟'},fmtTime:value=>String(value),document:{createTextNode:node},el:(_,text)=>node(text),renderPriceMotion:(target,value,key,eligible)=>pulses.push({value,key,eligible})});
  vm.runInContext(source.slice(start,end),context);
  return{context,state,charts,$,pulses};
}
function chart(h,period='m30'){
  const value=Object.create(C.Chart.prototype);
  Object.assign(value,{period,callbacks:{dataDisplayed:c=>h.context.renderChartHeader(period,c)},bars:[],plan:{objects:[]},view:null,tick:1,path:[],drag:null,autoPrice:false,plot:{x:0,y:0,w:800,h:300},host:{clientWidth:867},tool:'select',selected:null});
  value.render=function(){};h.charts[period]=value;
  return value;
}

test('gesture-held chart price and MA remain tied to the displayed candles until latest data is applied',()=>{
  const h=harness(),c=chart(h),old=market(),latest=market(10791,'2026-11-18T21:00:00+08:00');
  c.setData(old,{objects:[]},true);assert.match(h.$('quote-m30').textContent,/11,039/);
  c.drag={kind:'pan'};h.state.market=latest;c.setData(latest,{objects:[]});
  assert.match(h.$('quote-m30').textContent,/11,039/);assert.match(h.$('stats-m30').textContent,/MA5 11,039/);
  assert.equal(h.$('quote-m30').dataset.marketHeld,'true');assert.match(h.$('quote-m30').title,/14:45:00/);
  assert.match(h.$('quote-m30').title,/操作结束后/);assert.equal(h.pulses.at(-1).eligible,false);
  c.drag=null;c.applyPendingMarket();
  assert.match(h.$('quote-m30').textContent,/10,791/);assert.match(h.$('stats-m30').textContent,/MA5 10,791/);
  assert.equal(h.$('quote-m30').dataset.marketHeld,'false');assert.match(h.$('quote-m30').title,/21:00:00/);
  assert.equal(c.pendingMarket,null);
});

test('partially aligned periods keep their true distinct last closes and explain their candle time',()=>{
  const h=harness(),data=market();data.periods.m3=market(10791,'2026-11-18T21:00:00+08:00').periods.m30;
  data.freshness={m30:{covers_trigger:false}};h.state.market=data;
  const main=chart(h),small=chart(h,'m3');main.setData(data,{objects:[]},true);small.setData(data,{objects:[]},true);
  assert.match(h.$('quote-m30').textContent,/11,039/);assert.match(h.$('quote-m3').textContent,/10,791/);
  assert.match(h.$('quote-m30').title,/14:45:00/);assert.match(h.$('quote-m3').title,/21:00:00/);
  for(const period of ['m30','m3']){assert.match(h.$('quote-'+period).title,/周期待对齐/);assert.equal(h.$('quote-'+period).dataset.periodAligning,'true');}
  assert.equal(h.pulses.at(-1).eligible,false);
});

test('projection hold is explained and cleared without recreating unchanged header statistics',()=>{
  const h=harness(),c={market:h.state.market,bars:h.state.market.periods.m30,projectionStatus:()=>({ready:false,text:'30分钟预测线待对齐'})};
  h.charts.m30=c;h.context.renderChartHeader('m30');
  assert.equal(h.$('quote-m30').dataset.projectionPending,'true');assert.match(h.$('stats-m30').title,/30分钟预测线待对齐/);
  const renders=h.$('stats-m30').replacements;h.context.renderChartHeader('m30');assert.equal(h.$('stats-m30').replacements,renders);
  c.projectionStatus=()=>({ready:true});h.context.renderChartHeader('m30');
  assert.equal(h.$('quote-m30').dataset.projectionPending,'false');assert.doesNotMatch(h.$('quote-m30').title,/待对齐/);
});

test('switching contract never copies the old chart header while the new chart is empty',()=>{
  const h=harness();h.charts.m30={market:market(),bars:market().periods.m30,pendingMarket:market()};
  h.state.symbol=B;h.state.market={symbol:B,tick:1,periods:{m30:[]}};h.context.renderChartHeader('m30');
  assert.equal(h.$('quote-m30').textContent,'—');assert.equal(h.$('stats-m30').textContent,'等待行情');
  assert.equal(h.$('quote-m30').dataset.marketHeld,'false');assert.match(h.$('quote-m30').title,/尚无数据/);
});

test('workspace binds the displayed-data callback and updates charts before rendering market headers',()=>{
  assert.match(source,/dataDisplayed:chart=>renderChartHeader\(period,chart\)/);
  const apply=source.slice(source.indexOf('  function applyActiveMarket('),source.indexOf('\n  function paintOpeningPreview('));
  assert.ok(apply.indexOf('chart.setData(')<apply.indexOf('renderMarket();'));
});
