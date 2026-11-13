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
test('2B definition shows a real 100 reference, later 98 breach, and reclaim without future candles',()=>{
 const c=D.cases[0],chart=c.charts.m30,bars=chart.bars;
 assert.deepEqual(Object.keys(c.charts),['m30']);
 assert.equal(bars[2].low,100);assert.ok(bars[1].low>100&&bars[3].low>100);
 assert.ok(bars.slice(3,6).every(b=>b.low>100));assert.equal(bars[6].low,98);assert.ok(bars[6].close<100);
 assert.ok(bars[7].close>100);assert.equal(bars[7].closedAt,c.evidence.cutoff);
 for(const b of bars){assert.ok(b.low<=Math.min(b.open,b.close));assert.ok(b.high>=Math.max(b.open,b.close));assert.equal(b.completed,true);}
 assert.deepEqual(chart.lines.map(l=>l.points[0].price),[100,98]);
 assert.ok(chart.lines.every(l=>l.role==='key'));assert.deepEqual(chart.nodes,[]);assert.deepEqual(chart.zones,[]);
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
