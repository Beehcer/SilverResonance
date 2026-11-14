'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const data=require('./static/workspace-cases-data.js');
const view=require('./static/workspace-cases.js');

test('trend lessons form an independent catalog without replacing the existing thirty cases',()=>{
  assert.equal(data.cases.length,30);
  assert.equal(data.trendCases.length,4);
  assert.equal(view.catalog({data})[0].id,'2b-definition');
  assert.equal(view.catalog({data,category:'trend'})[0].id,'trend-up');
  for(const c of data.trendCases){assert.equal(data.get(c.id),c);assert.equal(c.entry,null);assert.equal(c.educational,true);}
});

test('each trend anchor belongs to a completed key candle breaking an already established reference',()=>{
  for(const c of data.trendCases){
    const {bars,anchors,side}=c.chart;
    for(const b of bars){assert.ok(b.low<=Math.min(b.open,b.close));assert.ok(b.high>=Math.max(b.open,b.close));assert.equal(b.completed,true);}
    for(const a of anchors){
      assert.ok(a.referenceIndex<a.index);
      const reference=bars[a.referenceIndex],key=bars[a.index];
      assert.equal(a.price,side==='up'?key.low:key.high);
      assert.equal(a.reference,side==='up'?reference.high:reference.low);
      assert.ok(side==='up'?key.close>a.reference:key.close<a.reference);
      for(const b of bars.slice(a.referenceIndex+1,a.index))assert.ok(side==='up'?b.high<=a.reference:b.low>=a.reference);
    }
  }
});

test('downward teaching is an exact OHLC and anchor mirror',()=>{
  const [up,down]=data.trendCases;
  up.chart.bars.forEach((b,i)=>{const d=down.chart.bars[i];assert.ok(Math.abs(d.high-(220-b.low))<1e-8);assert.ok(Math.abs(d.low-(220-b.high))<1e-8);assert.ok(Math.abs(d.close-(220-b.close))<1e-8);});
  up.chart.anchors.forEach((a,i)=>assert.equal(down.chart.anchors[i].price,220-a.price));
});

test('line break occurs after anchors are known and does not falsely break the selected horizontal level',()=>{
  const {chart}=data.get('trend-break'),[a,b]=chart.anchors;
  const level=i=>a.price+(i-a.index)*(b.price-a.price)/(b.index-a.index);
  assert.ok(chart.breakIndex>b.index);
  for(let i=b.index;i<chart.breakIndex;i++)assert.ok(chart.bars[i].close>=level(i));
  assert.ok(chart.bars[chart.breakIndex].close<level(chart.breakIndex));
  assert.ok(chart.bars[chart.breakIndex].low>chart.key.price);
  assert.equal(chart.key.price,chart.bars[chart.key.index].low);
  assert.deepEqual(data.get('trend-fixed').chart.anchors,chart.anchors);
});

test('trend diagrams render real anchor values with leaders and no automatic entry permission',()=>{
  for(const c of data.trendCases){
    const svg=view.trendChartSvg(c),html=view.trendContent(c);
    assert.doesNotMatch(svg,/NaN|Infinity|undefined/);
    assert.equal((svg.match(/data-bar=/g)||[]).length,c.chart.bars.length);
    assert.equal((svg.match(/data-anchor=/g)||[]).length,2);
    assert.match(svg,/data-trend-segment="extension"/);
    assert.match(svg,/②收盘后启用/);
    assert.match(html,/不是价格预测/);
    assert.doesNotMatch(html,/示例条件已满足|cases-price-strip|cases-teaching-badge/);
    const content={innerHTML:'',querySelectorAll:()=>[]};
    view.Controller.prototype.renderCase.call({data,category:'trend',selected:c.id,content,renderCharts(){},renderRule(){}});
    assert.equal(content.innerHTML,html);
    const slots=[{dataset:{period:'trend'},innerHTML:''}];
    view.Controller.prototype.renderCharts.call({content:{querySelectorAll:()=>slots}},c);
    assert.equal(slots[0].innerHTML,svg);
  }
});

test('trend filters distinguish placement direction and retain searchable rule explanations',()=>{
  assert.deepEqual(view.filterCases(data.trendCases,{direction:'long'}).map(c=>c.id),['trend-up']);
  assert.deepEqual(view.filterCases(data.trendCases,{direction:'short'}).map(c=>c.id),['trend-down']);
  assert.ok(view.filterCases(data.trendCases,{query:'周期'}).some(c=>c.id==='trend-fixed'));
});
