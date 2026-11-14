const test=require('node:test'),assert=require('node:assert/strict');
const V=require('./static/workspace-cases.js'),D=require('./static/workspace-cases-data.js');
const tradingCases=D.cases.filter(c=>!c.educational);
test('2B comparison shows the same support in both periods and only the aggregated pin on the right',()=>{
  const c=D.cases[0],small=V.conceptChartSvg(c,'m3'),large=V.conceptChartSvg(c,'m30');
  for(const svg of [small,large]){
    assert.doesNotMatch(svg,/NaN|Infinity|undefined/);
    assert.match(svg,/100/);assert.match(svg,/98/);
    assert.doesNotMatch(svg,/data-case-wave="conditional"|data-case-node="4"|入场 [\d,.]+|止损 [\d,.]+|已完成触发|证[123]/);
  }
  assert.equal((small.match(/data-bar=/g)||[]).length,c.charts.m3.bars.length);
  assert.equal((large.match(/data-bar=/g)||[]).length,1);
  const support=svg=>svg.match(/<line x1="22" x2="495" y1="([^"]+)"/)[1];
  assert.equal(support(small),support(large));
  assert.match(large,/长下影针/);assert.match(small,/同一段30分钟/);
  assert.equal(V.filterCases(D.cases,{query:'2B：多空'})[0],c);
  assert.ok(!V.filterCases(D.cases,{mode:'reversal_a'}).includes(c));
});

test('case filters preserve the long/short mirrors and separate base modes from features',()=>{
  assert.equal(V.filterCases(tradingCases,{direction:'short'}).length,tradingCases.length/2);
  const feature=V.filterCases(tradingCases,{family:'2b-zone'});assert.equal(feature.length,2);
  assert.ok(feature.every(c=>c.mode==='reversal_a'));
  assert.equal(V.filterCases(tradingCases,{family:'2b-zone',direction:'long'}).length,1);
  assert.ok(V.filterCases(tradingCases,{query:'内包'}).length>=4);
  assert.equal(V.filterCases(tradingCases,{mode:'range',family:'2b-zone'}).length,0);
});


test('the top 2B diagrams explain a failed upward break without turning it into a sell signal',()=>{
  const c=D.get('2b-top-definition');
  for(const period of ['m3','m30']){
    const svg=V.conceptChartSvg(c,period);
    assert.match(svg,/前高 100/);assert.match(svg,/102/);
    assert.doesNotMatch(svg,/NaN|Infinity|undefined|前低|下影针|收回100上方|入场 [\d,.]+|已完成触发/);
    assert.match(svg,period==='m30'?/长上影针/:/跌回100下方/);
  }
  const svg=V.quickConceptSvg(c);
  assert.equal((svg.match(/data-bar=/g)||[]).length,c.quickExample.bars.length);
  assert.match(svg,/同根收回100下方/);assert.doesNotMatch(svg,/data-quote-path|秒|NaN|Infinity|undefined/);

});

test('a changed or unavailable rule source cannot be presented as checked',()=>{
  assert.equal(V.ruleState(D.source,{available:true,version:D.source.sha256}).kind,'current');
  assert.equal(V.ruleState(D.source,{available:true,version:'changed'}).kind,'changed');
  assert.equal(V.ruleState(D.source,{available:false,version:D.source.sha256}).kind,'unknown');
  assert.equal(V.ruleState(D.source,null).kind,'unknown');
});

test('all case charts render finite OHLC geometry and explicit conditional extensions',()=>{
  for(const c of tradingCases)for(const period of ['day','m30','m3']){
    const svg=V.chartSvg(c.charts[period],period,{id:c.id,direction:c.direction});
    assert.ok(!/NaN|Infinity|undefined/.test(svg),c.id+':'+period);
    assert.equal((svg.match(/data-bar=/g)||[]).length,c.charts[period].bars.length);
    if(period!=='day'){
      assert.match(svg,/data-case-wave="conditional"[^>]*stroke-dasharray="6 5"/);
      assert.match(svg,/data-case-node="4" data-future="true"/);
      assert.match(svg,/条件预期/);
    }
  }
});

test('trigger emphasis never upgrades the forming 30m bar or daily context into a completed trigger',()=>{
  const c=tradingCases[0];
  const main=V.chartSvg(c.charts.m30,'m30',{stage:'trigger'}),daily=V.chartSvg(c.charts.day,'day',{stage:'trigger'}),execution=V.chartSvg(c.charts.m3,'m3',{stage:'trigger'});
  assert.match(main,/data-completed="false" opacity="0.5"/);
  assert.match(main,/末根未收盘 · 仅定位现价/);
  assert.doesNotMatch(main,/已完成触发/);assert.doesNotMatch(daily,/已完成触发/);
  assert.match(execution,/已完成触发/);
  const forming=structuredClone(c.charts.m3);forming.bars.at(-1).completed=false;
  assert.doesNotMatch(V.chartSvg(forming,'m3',{stage:'trigger'}),/已完成触发/);
});

test('visual aids can be hidden independently without removing historical candles',()=>{
  const c=tradingCases[0],chart=c.charts.m30;
  const hidden=V.chartSvg(chart,'m30',{horizontal:false,trend:false,numbers:false});
  assert.doesNotMatch(hidden,/data-case-line=|data-case-node=/);
  assert.equal((hidden.match(/data-bar=/g)||[]).length,chart.bars.length);
  const noFuture=V.chartSvg(chart,'m30',{future:false});
  assert.match(noFuture,/data-case-wave="observed"/);assert.doesNotMatch(noFuture,/data-case-wave="conditional"|data-case-node="4"/);
});

test('diagram labels are escaped and price scales include drawn levels and projected nodes',()=>{
  const chart=structuredClone(tradingCases[0].charts.m30);chart.title='<script>alert(1)</script>';chart.nodes.push({index:90,price:200,label:'x',future:true});
  const svg=V.chartSvg(chart,'m30');assert.doesNotMatch(svg,/<script>/);assert.match(svg,/&lt;script&gt;/);
  const geometry=V.scene(chart,'m30');assert.ok(geometry.max>200);assert.ok(geometry.x(90)<geometry.plot.x+geometry.plot.w);
});

test('compact price labels have distinct vertical positions and do not print lengthy descriptions over candles',()=>{
  for(const c of tradingCases){
    const svg=V.chartSvg(c.charts.m3,'m3');
    const ys=[...svg.matchAll(/data-line-label="\d+" x="[^\"]+" y="([^\"]+)"/g)].map(m=>Number(m[1]));
    assert.equal(new Set(ys).size,ys.length,c.id);
    const sorted=ys.sort((a,b)=>a-b);for(let i=1;i<sorted.length;i++)assert.ok(sorted[i]-sorted[i-1]>=15.9,c.id);
    assert.match(svg,/入场 [\d,.]+/);assert.match(svg,/止损 [\d,.]+/);
  }
});

test('low-height main charts fit a 300-high viewBox while keeping prices and node data',()=>{
  const c=tradingCases[0],normal=V.scene(c.charts.m30,'m30'),compact=V.scene(c.charts.m30,'m30',{compact:true});
  assert.equal(normal.height,460);assert.equal(compact.height,300);assert.equal(compact.plot.h,223);
  const svg=V.chartSvg(c.charts.m30,'m30',{compact:true});
  assert.match(svg,/viewBox="0 0 880 300"/);assert.match(svg,/data-case-node="3"/);
  assert.equal((svg.match(/data-bar=/g)||[]).length,c.charts.m30.bars.length);
});

test('trend anchors never create a long fake horizontal leader and price leaders stay short',()=>{
  for(const c of tradingCases)for(const period of ['m30','m3']){
    const chart=c.charts[period],svg=V.chartSvg(chart,period,{compact:true});
    assert.equal((svg.match(/data-case-trend-label=/g)||[]).length,chart.lines.filter(l=>l.kind==='trend').length);
    const trendIds=new Set(chart.lines.map((l,i)=>l.kind==='trend'?String(i+1):null).filter(Boolean));
    for(const m of svg.matchAll(/data-case-price-leader="(\d+)" d="M ([\d.e-]+) ([\d.e-]+) L ([\d.e-]+) ([\d.e-]+)"/g)){
      assert.equal(trendIds.has(m[1]),false,c.id);
      assert.ok(Math.abs(Number(m[4])-Number(m[2]))<=32,c.id);
      assert.ok(Math.abs(Number(m[5])-Number(m[3]))<=32,c.id);
    }
  }
});

test('case annotations use descriptive words, stay within the plot, and keep full explanations',()=>{
 for(const c of tradingCases)for(const period of ['m30','m3']){
  const chart=c.charts[period],g=V.scene(chart,period,{compact:true}),labels=V.annotationPlacements(chart,g),svg=V.chartSvg(chart,period,{compact:true});
  assert.doesNotMatch(svg,/证\d/);assert.equal(labels.length,chart.annotations.length);
  for(const a of labels){
   assert.ok(a.x>=g.plot.x&&a.y>=g.plot.y);assert.ok(a.x+a.w<=g.plot.x+g.plot.w&&a.y+a.h<=g.plot.y+g.plot.h);
   assert.ok(a.label.length>1);assert.ok(svg.includes(a.label));assert.ok(svg.includes('<title>'+a.text+'</title>'));
  }
 }
 assert.equal(V.annotationLabel('关键K：收盘破前高'),'突破前高');
 assert.equal(V.annotationLabel('另一关键K低点锚'),'低点锚');
});
