const test=require('node:test'),assert=require('node:assert/strict');
const D=require('./static/workspace-cases-data.js'),V=require('./static/workspace-cases.js');
const c=D.get('harami-definition');
test('harami concept follows 2B and distinguishes body inclusion from full-range inclusion',()=>{
 assert.equal(D.cases[1],c);assert.equal(c.entry,null);assert.equal(c.direction,'neutral');assert.equal(c.educational,true);
 for(const kind of ['body','inside']){
  const [m,k]=c.diagrams[kind].bars;
  assert.ok(Math.min(k.open,k.close)>Math.min(m.open,m.close));
  assert.ok(Math.max(k.open,k.close)<Math.max(m.open,m.close));
  assert.ok(Math.abs(k.open-k.close)<Math.abs(m.open-m.close));
  if(kind==='body')assert.ok(k.high>m.high);
  else assert.ok(k.high<m.high&&k.low>m.low);
 }
});
test('both execution illustrations wait for a later closed candle and stay within mother bounds',()=>{
 for(const kind of ['long','short']){
  const d=c.diagrams[kind],m=d.bars[d.motherIndex],k=d.bars[d.childIndex],t=d.bars[d.triggerIndex];
  assert.ok(d.motherIndex<d.childIndex&&d.childIndex<d.triggerIndex);
  assert.equal(d.level,kind==='long'?k.high:k.low);
  assert.ok(kind==='long'?t.close>d.level:t.close<d.level);
  assert.ok(t.close>m.low&&t.close<m.high);
 }
 for(const d of Object.values(c.diagrams))for(const [i,b] of d.bars.entries()){
  assert.ok(b.low<=Math.min(b.open,b.close)&&b.high>=Math.max(b.open,b.close));
  assert.equal(b.completed,true);assert.equal(Date.parse(b.closedAt)-Date.parse(b.dt),180000);
  if(i)assert.equal(b.dt,d.bars[i-1].closedAt);
 }
});
test('harami long page renders all four diagrams without granting a trade or invoking 2B rendering',()=>{
 const slots=Object.keys(c.diagrams).map(period=>({dataset:{period}})),content={innerHTML:'',querySelectorAll:selector=>selector==='[data-period]'?slots:[]};
 const controller={data:D,selected:c.id,content,renderRule(){},renderCharts(item){V.Controller.prototype.renderCharts.call(this,item);}};
 V.Controller.prototype.renderCase.call(controller);
 assert.match(content.innerHTML,/实体孕线/);assert.match(content.innerHTML,/严格内包/);
 assert.match(content.innerHTML,/做多/);assert.match(content.innerHTML,/做空/);
 assert.doesNotMatch(content.innerHTML,/示例条件已满足|cases-price-strip|cases-risk|data-concept-example/);
 for(const [i,slot] of slots.entries()){
  assert.match(slot.innerHTML,/data-harami-chart/);assert.doesNotMatch(slot.innerHTML,/NaN|Infinity|undefined/);
  assert.equal((slot.innerHTML.match(/data-bar=/g)||[]).length,Object.values(c.diagrams)[i].bars.length);
 }
 assert.equal(V.filterCases(D.cases,{query:'孕线入门'})[0],c);
});

test('mother, child, range and trigger captions have explicit leaders',()=>{
 for(const kind of ['body','inside','long','short']){
  const svg=V.haramiChartSvg(c,kind);
  for(const name of ['母线','子线'])assert.ok(svg.includes('data-harami-callout="'+name+'"><path'));
  assert.doesNotMatch(svg,/>母<|>子</);
  assert.match(svg,kind==='body'||kind==='inside'?/data-harami-callout="range"/:/data-harami-callout="trigger"/);
 }
});
