const test=require('node:test');
const assert=require('node:assert/strict');
const D=require('./static/workspace-cases-data.js');
test('catalog covers every designed family in both directions and keeps feature tags under one main mode',()=>{
 assert.equal(D.cases.length,28);assert.equal(new Set(D.cases.map(c=>c.id)).size,28);
 for(const family of new Set(D.cases.map(c=>c.family)))assert.deepEqual(D.cases.filter(c=>c.family===family).map(c=>c.direction),['long','short']);
 for(const c of D.cases){assert.ok(['continuation','reversal_a','reversal_b','range'].includes(c.mode));assert.ok(c.ruleRefs.length>=2);assert.ok(c.assumptions.some(t=>t.includes('教学')));assert.match(c.outcome,/未展示后续盈亏/);}
});
test('all plotted candles have valid OHLC, known history cutoff, and only main final bar remains forming',()=>{
 for(const c of D.cases)for(const [period,chart] of Object.entries(c.charts)){
  for(const b of chart.bars){assert.ok(b.low<=Math.min(b.open,b.close),c.id+' '+period);assert.ok(b.high>=Math.max(b.open,b.close),c.id+' '+period);assert.ok(b.low>0);assert.ok(Number.isFinite(b.volume));if(b.completed)assert.ok(Date.parse(b.closedAt)<=Date.parse(c.evidence.cutoff));}
  assert.equal(chart.bars.filter(b=>!b.completed).length,period==='m30'?1:0);
 }
});
test('all hypothetical entries are inside predeclared zones, beyond trigger, protected and within budget',()=>{
 for(const c of D.cases){const e=c.entry,sign=c.direction==='long'?1:-1;assert.ok(e.price>=e.zoneLow&&e.price<=e.zoneHigh,c.id);assert.ok((e.price-e.trigger)*sign>=.099,c.id);assert.ok((e.price-e.stop)*sign>0,c.id);assert.ok((e.obstacle-e.price)*sign>0,c.id);assert.equal(e.risk.loss,Math.round((Math.abs(e.price-e.stop)*e.risk.multiplier+e.risk.fee+e.risk.slippage)*100)/100);assert.ok(e.risk.loss<=e.risk.budget);assert.ok(e.risk.twoLotsLoss<=e.risk.budget);assert.match(e.risk.assumption,/非任何实际合约参数/);assert.match(e.protection,/不是系统统一固定价/);}
});
test('background then main permission then local validation then final trigger are strictly ordered',()=>{
 for(const c of D.cases){const e=c.evidence,seq=['dailyClosedAt','mainPermissionAt','localValidationAt','triggerAt'].map(k=>Date.parse(e[k]));for(let i=1;i<seq.length;i++)assert.ok(seq[i]>seq[i-1],c.id);assert.equal(seq.at(-1),Date.parse(e.cutoff));assert.equal(c.charts.m3.bars.at(-1).close,c.entry.price);}
});
test('main numbered structure has candle-anchored historical extrema and future 4 only beyond history',()=>{
 for(const c of D.cases)for(const period of ['m30','m3']){const chart=c.charts[period];assert.deepEqual(chart.nodes.map(n=>n.label),['1','2','3','4']);const future=chart.nodes.at(-1);assert.equal(future.future,true);assert.ok(future.index>chart.bars.at(-1).index);for(const n of chart.nodes.slice(0,3)){const bar=chart.bars[n.index];assert.ok(n.price===bar.high||n.price===bar.low,c.id+' '+period);}}
});
test('short cases mirror numeric geometry while narrative avoids corrupting ordinary Chinese words',()=>{
 for(const c of D.cases.filter(c=>c.direction==='long')){const s=D.get(c.family+'-short');for(const period of ['day','m30','m3'])for(let i=0;i<c.charts[period].bars.length;i++){const a=c.charts[period].bars[i],b=s.charts[period].bars[i];assert.ok(Math.abs(a.high+b.low-220)<1e-8);assert.ok(Math.abs(a.low+b.high-220)<1e-8);}assert.doesNotMatch(JSON.stringify(s),/最空|最多头/);}
});
