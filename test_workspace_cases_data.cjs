const test=require('node:test');
const assert=require('node:assert/strict');
const D=require('./static/workspace-cases-data.js');
const tradingCases=D.cases.filter(c=>!c.educational);
test('first entry teaches 2B without inventing an entry permission or assigning a trading mode',()=>{
 const c=D.cases[0];
 assert.equal(D.cases.length,29);assert.equal(new Set(D.cases.map(c=>c.id)).size,29);
 assert.equal(c.id,'2b-definition');assert.equal(D.get('2b-definition'),c);assert.equal(c.educational,true);
 assert.equal(c.mode,'concept');assert.equal(c.direction,'neutral');assert.equal(c.entry,null);
 assert.equal(c.headline,'2B = 突破失败 = 原方向依据减弱；');assert.equal(c.caution,'不等于行情一定反转');
 assert.match(c.definition,/突破前高／前低后没能延续，又收回原位/);
 assert.match(c.summary,/先真正越界，再迅速收回/);assert.match(c.context,/仍可再跌/);
 assert.equal(c.evidence.mainPermissionAt,undefined);assert.equal(c.evidence.triggerAt,undefined);
 assert.deepEqual(c.steps.map(s=>s.key),['reference','breach','reclaim']);
 assert.equal(D.coverage.examples,28);assert.equal(D.coverage.concepts,1);
});
test('2B definition establishes the prior low before its breach and reclaim without later results',()=>{
 const c=D.cases[0],a=c.aggregation,bars=c.charts.m3.bars;
 assert.deepEqual(Object.keys(c.charts),['m3','m30']);
 assert.ok(a.referenceIndex<a.startIndex);assert.equal(bars[a.referenceIndex].low,a.reference);
 assert.ok(bars[a.referenceIndex-1].low>a.reference&&bars[a.referenceIndex+1].low>a.reference);
 assert.ok(bars.slice(a.referenceIndex+1,a.breachIndex).every(b=>b.low>a.reference));
 assert.ok(bars[a.breachIndex].close<a.reference);assert.equal(bars[a.lowIndex].low,98);
 assert.ok(bars.slice(a.breachIndex,a.reclaimIndex).every(b=>b.close<a.reference));
 assert.ok(bars[a.reclaimIndex].close>a.reference);assert.equal(bars.at(-1).closedAt,c.evidence.cutoff);
 for(const chart of Object.values(c.charts)){
  for(const b of chart.bars){assert.ok(b.low<=Math.min(b.open,b.close));assert.ok(b.high>=Math.max(b.open,b.close));assert.equal(b.completed,true);assert.ok(Date.parse(b.closedAt)<=Date.parse(c.evidence.cutoff));}
  assert.ok(chart.lines.every(l=>l.role==='key'));assert.deepEqual(chart.nodes,[]);assert.deepEqual(chart.zones,[]);
 }
 assert.match(c.avoid.join(' '),/只有长下影线不够/);assert.match(c.avoid.join(' '),/不是直接买点/);
 assert.match(c.assumptions.join(' '),/跨多根收回，不一定是一根针/);
});
test('the upper-period pin is an exact aggregation of ten consecutive 3-minute candles',()=>{
 const c=D.cases[0],a=c.aggregation,source=c.charts[a.sourcePeriod].bars.slice(a.startIndex,a.endIndex+1),target=c.charts[a.targetPeriod].bars;
 assert.equal(source.length,10);assert.equal(target.length,1);
 for(let i=0;i<source.length;i++){
  assert.equal(Date.parse(source[i].closedAt)-Date.parse(source[i].dt),3*60000);
  if(i)assert.equal(source[i].dt,source[i-1].closedAt);
 }
 const pin=target[0];
 assert.equal(pin.dt,source[0].dt);assert.equal(pin.closedAt,source.at(-1).closedAt);
 assert.equal(Date.parse(pin.closedAt)-Date.parse(pin.dt),30*60000);
 assert.equal(pin.open,source[0].open);assert.equal(pin.close,source.at(-1).close);
 assert.equal(pin.high,Math.max(...source.map(b=>b.high)));assert.equal(pin.low,Math.min(...source.map(b=>b.low)));
 assert.equal(pin.volume,source.reduce((sum,b)=>sum+b.volume,0));assert.equal(pin.low,98);
 assert.ok(pin.open>a.reference&&pin.close>a.reference);
 assert.ok(Math.min(pin.open,pin.close)-pin.low>5*Math.abs(pin.close-pin.open));
});
test('the second definition teaches a failed upward breakout without granting short entry',()=>{
 const c=D.get('2b-top-definition'),a=c.aggregation,bars=c.charts.m3.bars;
 assert.equal(c.id,'2b-top-definition');assert.equal(D.get(c.id),c);assert.equal(c.pattern,'top');
 assert.equal(c.educational,true);assert.equal(c.direction,'neutral');assert.equal(c.entry,null);
 assert.equal(c.headline,D.cases[0].headline);assert.equal(c.caution,D.cases[0].caution);
 assert.equal(c.evidence.mainPermissionAt,undefined);assert.equal(c.evidence.triggerAt,undefined);
 assert.ok(a.referenceIndex<a.breachIndex);assert.equal(bars[a.referenceIndex].high,100);
 assert.ok(bars[a.referenceIndex-1].high<100&&bars[a.referenceIndex+1].high<100);
 assert.ok(bars.slice(a.referenceIndex+1,a.breachIndex).every(b=>b.high<100));
 assert.ok(bars[a.breachIndex].close>100);assert.equal(bars[a.highIndex].high,102);
 assert.ok(bars.slice(a.breachIndex,a.reclaimIndex).every(b=>b.close>100));
 assert.ok(bars[a.reclaimIndex].close<100);
 assert.match(c.avoid.join(' '),/长上影线不够/);assert.match(c.avoid.join(' '),/不是直接卖点/);
 assert.match(c.entryExplanation,/主结构允许做空、反弹受阻/);assert.match(c.entryExplanation,/3分钟转弱触发/);
 assert.match(c.context,/仍可再涨/);assert.doesNotMatch(JSON.stringify(c),/前低100|下刺98|长下影针|做多许可/);
});
test('the top pin exactly mirrors and aggregates the same completed ten-candle interval',()=>{
 const bottom=D.cases[0],top=D.get('2b-top-definition'),a=top.aggregation,event=top.charts.m3.bars.slice(a.startIndex,a.endIndex+1),pin=top.charts.m30.bars[0];
 for(const period of ['m3','m30'])for(let i=0;i<top.charts[period].bars.length;i++){
  const b=bottom.charts[period].bars[i],t=top.charts[period].bars[i];
  assert.ok(Math.abs(t.open+b.open-200)<1e-8);assert.ok(Math.abs(t.close+b.close-200)<1e-8);
  assert.ok(Math.abs(t.high+b.low-200)<1e-8);assert.ok(Math.abs(t.low+b.high-200)<1e-8);
  assert.ok(t.low<=Math.min(t.open,t.close));assert.ok(t.high>=Math.max(t.open,t.close));
  assert.equal(t.dt,b.dt);assert.equal(t.closedAt,b.closedAt);assert.equal(t.completed,true);
 }
 assert.equal(event.length,10);assert.equal(pin.dt,event[0].dt);assert.equal(pin.closedAt,event.at(-1).closedAt);
 assert.equal(pin.open,event[0].open);assert.equal(pin.close,event.at(-1).close);
 assert.equal(pin.high,Math.max(...event.map(b=>b.high)));assert.equal(pin.low,Math.min(...event.map(b=>b.low)));
 assert.equal(pin.volume,event.reduce((sum,b)=>sum+b.volume,0));
 assert.ok(pin.open<100&&pin.close<100);assert.equal(pin.high,102);
 assert.ok(pin.high-Math.max(pin.open,pin.close)>5*Math.abs(pin.close-pin.open));
});
test('catalog covers every designed family in both directions and keeps feature tags under one main mode',()=>{
 assert.equal(tradingCases.length,28);assert.equal(new Set(tradingCases.map(c=>c.id)).size,28);
 for(const family of new Set(tradingCases.map(c=>c.family)))assert.deepEqual(tradingCases.filter(c=>c.family===family).map(c=>c.direction),['long','short']);
 for(const c of tradingCases){assert.ok(['continuation','reversal_a','reversal_b','range'].includes(c.mode));assert.ok(c.ruleRefs.length>=2);assert.ok(c.assumptions.some(t=>t.includes('教学')));assert.match(c.outcome,/未展示后续盈亏/);}
});
test('all plotted candles have valid OHLC, known history cutoff, and only main final bar remains forming',()=>{
 for(const c of tradingCases)for(const [period,chart] of Object.entries(c.charts)){
  for(const b of chart.bars){assert.ok(b.low<=Math.min(b.open,b.close),c.id+' '+period);assert.ok(b.high>=Math.max(b.open,b.close),c.id+' '+period);assert.ok(b.low>0);assert.ok(Number.isFinite(b.volume));if(b.completed)assert.ok(Date.parse(b.closedAt)<=Date.parse(c.evidence.cutoff));}
  assert.equal(chart.bars.filter(b=>!b.completed).length,period==='m30'?1:0);
 }
});
test('all hypothetical entries are inside predeclared zones, beyond trigger, protected and within budget',()=>{
 for(const c of tradingCases){const e=c.entry,sign=c.direction==='long'?1:-1;assert.ok(e.price>=e.zoneLow&&e.price<=e.zoneHigh,c.id);assert.ok((e.price-e.trigger)*sign>=.099,c.id);assert.ok((e.price-e.stop)*sign>0,c.id);assert.ok((e.obstacle-e.price)*sign>0,c.id);assert.equal(e.risk.loss,Math.round((Math.abs(e.price-e.stop)*e.risk.multiplier+e.risk.fee+e.risk.slippage)*100)/100);assert.ok(e.risk.loss<=e.risk.budget);assert.ok(e.risk.twoLotsLoss<=e.risk.budget);assert.match(e.risk.assumption,/非任何实际合约参数/);assert.match(e.protection,/不是系统统一固定价/);}
});
test('background then main permission then local validation then final trigger are strictly ordered',()=>{
 for(const c of tradingCases){const e=c.evidence,seq=['dailyClosedAt','mainPermissionAt','localValidationAt','triggerAt'].map(k=>Date.parse(e[k]));for(let i=1;i<seq.length;i++)assert.ok(seq[i]>seq[i-1],c.id);assert.equal(seq.at(-1),Date.parse(e.cutoff));assert.equal(c.charts.m3.bars.at(-1).close,c.entry.price);}
});
test('main numbered structure has candle-anchored historical extrema and future 4 only beyond history',()=>{
 for(const c of tradingCases)for(const period of ['m30','m3']){const chart=c.charts[period];assert.deepEqual(chart.nodes.map(n=>n.label),['1','2','3','4']);const future=chart.nodes.at(-1);assert.equal(future.future,true);assert.ok(future.index>chart.bars.at(-1).index);for(const n of chart.nodes.slice(0,3)){const bar=chart.bars[n.index];assert.ok(n.price===bar.high||n.price===bar.low,c.id+' '+period);}}
});
test('short cases mirror numeric geometry while narrative avoids corrupting ordinary Chinese words',()=>{
 for(const c of tradingCases.filter(c=>c.direction==='long')){const s=D.get(c.family+'-short');for(const period of ['day','m30','m3'])for(let i=0;i<c.charts[period].bars.length;i++){const a=c.charts[period].bars[i],b=s.charts[period].bars[i];assert.ok(Math.abs(a.high+b.low-220)<1e-8);assert.ok(Math.abs(a.low+b.high-220)<1e-8);}assert.doesNotMatch(JSON.stringify(s),/最空|最多头/);}
});

test('quick examples use continuous closed three-minute bars and a prior boundary',()=>{
 for(const c of [D.cases[0],D.cases[0].topExample]){
  const q=c.quickExample,top=c.pattern==='top',pin=q.bars[q.pinIndex];
  assert.ok(q.referenceIndex<q.pinIndex);assert.equal(q.bars[q.referenceIndex][top?'high':'low'],100);
  assert.ok(top?pin.high>100&&pin.close<100:pin.low<100&&pin.close>100);
  for(const [i,b] of q.bars.entries()){
   assert.equal(Date.parse(b.closedAt)-Date.parse(b.dt),180000);assert.equal(b.completed,true);
   assert.ok(b.high>=Math.max(b.open,b.close)&&b.low<=Math.min(b.open,b.close));
   if(i)assert.equal(b.dt,q.bars[i-1].closedAt);
  }
  assert.equal(q.samples,undefined);assert.equal(c.entry,null);
 }
 assert.equal(D.cases.filter(c=>c.educational).length,1);
});
