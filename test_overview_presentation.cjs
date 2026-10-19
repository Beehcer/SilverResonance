'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const html = fs.readFileSync('templates/index.html', 'utf8');
const start = html.indexOf('  function overviewObservation(');
const end = html.indexOf('  // ===== 多周期K线查看', start);
assert(start > 0 && end > start);
const nodes = new Map();
const context = vm.createContext({
  document: {getElementById(id) {if (!nodes.has(id)) nodes.set(id,{innerHTML:'',querySelectorAll:()=>[]});return nodes.get(id);}},
  escapeOutlookText: value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  outlookPrice: value => String(value), outlookTime: value => String(value), focusSymbol: () => {},
});
vm.runInContext(html.slice(start,end),context);
function card(overrides={}) {
  return {name:'螺纹',symbol:'SHFE.rb2701',price:3068,stale:true,realtime:false,display_available:true,
    freshness:{status:'latest_available',last_bar_time:'2026-10-19 14:30:00'},
    outlook:{period:1800,market_phase:'支撑破位，待反抽',market_summary:'外层方向未确认',preparation:{
      observation_zone:{low:3072,high:3077}, key_levels:{current_support:{price:3065},current_resistance:{price:3078}}}},
    // A previous ready plan must not leak through presentation-only preparation data.
    decision:{plan_status:'ready',quadrant:'confirm',direction:'short',max_lots:2,label:'旧的已确认信号'}, ...overrides};
}
function render(c,connected=true) {context.snapshot={symbols:[c],connected,current:c.symbol};vm.runInContext('renderOverview(snapshot)',context);return {grid:nodes.get('ov-grid').innerHTML,advice:nodes.get('ov-advice').innerHTML};}

test('quiet overview shows historical structure and date, with an explicit preparation group',()=>{
  const {grid,advice}=render(card());
  for (const text of ['3068','支撑破位，待反抽','2026-10-19 14:30:00','3072–3077','参考价 · 非实时']) assert(grid.includes(text),text);
  assert(!grid.includes('等待快照')); assert(!grid.includes('旧的已确认信号'));
  assert(advice.includes('夜盘 / 次日预案 · 最新可用结构'));
  assert(advice.includes('暂无满足全部条件的确认计划'));
  assert(!advice.includes('风险额度最多2手'));
});
test('missing 3m may block execution without hiding usable 30m structure',()=>{
  const {grid,advice}=render(card({confirmation_freshness:{status:'loading'}}));
  assert(grid.includes('支撑破位，待反抽')); assert(advice.includes('预案观察'));
  assert(!advice.includes('风险额度最多'));
});
test('cache failure and disconnection do not masquerade as preparation or show stale signal',()=>{
  for (const connected of [true,false]) {
    const {grid,advice}=render(card({display_available:connected?false:true,freshness:{status:'stale',label:'缓存刷新中断'}}),connected);
    assert(!grid.includes('>3068<')); assert(!grid.includes('支撑破位，待反抽'));
    assert(!advice.includes('夜盘 / 次日预案 · 最新可用结构'));
    assert(!advice.includes('风险额度最多2手'));
  }
});
test('only refreshed real-time evidence can return a backend-ready plan to the ready group',()=>{
  const {grid,advice}=render(card({stale:false,realtime:true}));
  assert(grid.includes('行情更新中')); assert(!grid.includes('参考价 · 非实时'));
  assert(advice.includes('风险额度最多2手')); assert(!advice.includes('夜盘 / 次日预案 · 最新可用结构'));
});
test('preparation text is escaped and invalid prices are not rendered as markup',()=>{
  const {grid}=render(card({name:'<img onerror=x>',price:'<script>bad</script>'}));
  assert(!grid.includes('<img')); assert(!grid.includes('<script>')); assert(grid.includes('&lt;img'));
});
