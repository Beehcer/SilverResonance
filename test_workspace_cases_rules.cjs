const test = require('node:test');
const assert = require('node:assert/strict');
const D = require('./static/workspace-cases-data.js');
const tradingCases=D.cases.filter(c=>!c.educational);
const V = require('./static/workspace-cases.js');
const nearly = (a, b, label) => assert.ok(Math.abs(a - b) < 1e-7, label || `${a} != ${b}`);
const sign = c => c.direction === 'long' ? 1 : -1;
const at = (l, i) => {
  const [a, b] = l.points;
  return a.price + (b.price - a.price) * (i - a.index) / (b.index - a.index);
};

test('all four authorized primary modes and every illustrated feature have distinct long/short cases', () => {
  assert.equal(new Set(tradingCases.map(c => c.id)).size, tradingCases.length);
  assert.equal(tradingCases.length, 28);
  for (const mode of ['continuation', 'reversal_a', 'reversal_b', 'range']) {
    for (const direction of ['long', 'short']) assert.ok(tradingCases.some(c => c.mode === mode && c.direction === direction));
  }
  for (const family of new Set(tradingCases.map(c => c.family))) {
    assert.deepEqual(tradingCases.filter(c => c.family === family).map(c => c.direction).sort(), ['long', 'short']);
  }
  assert.ok(tradingCases.every(c => ['continuation', 'reversal_a', 'reversal_b', 'range'].includes(c.mode)));
});

test('illustrated permits use completed earlier evidence and never the forming main candle or future wave 4', () => {
  for (const c of tradingCases) {
    const e = c.evidence;
    assert.ok(Date.parse(e.dailyClosedAt) < Date.parse(e.mainPermissionAt), c.id);
    assert.ok(Date.parse(e.mainPermissionAt) < Date.parse(e.localValidationAt), c.id);
    assert.ok(Date.parse(e.localValidationAt) < Date.parse(e.triggerAt), c.id);
    assert.ok(Date.parse(e.triggerAt) <= Date.parse(e.cutoff), c.id);
    assert.equal(c.charts.m30.bars.at(-1).completed, false, c.id);
    assert.equal(c.charts.m3.bars.at(-1).completed, true, c.id);
    for (const chart of Object.values(c.charts)) {
      for (const b of chart.bars) {
        assert.ok(b.low <= Math.min(b.open, b.close) && b.high >= Math.max(b.open, b.close), c.id);
        assert.ok(b.low > 0 && b.high >= b.low, c.id);
        if (b.completed) assert.ok(Date.parse(b.closedAt) <= Date.parse(e.cutoff), c.id);
      }
      for (const n of chart.nodes.filter(n => n.label === '4')) {
        assert.equal(n.future, true, c.id);
        assert.ok(n.index > chart.bars.length - 1, c.id);
      }
    }
  }
});

test('entry remains in the planned point-3 area with protective stop, nearby obstacle and exact illustrative risk', () => {
  for (const c of tradingCases) {
    const e = c.entry, r = e.risk, s = sign(c);
    assert.ok(e.price >= e.zoneLow && e.price <= e.zoneHigh, c.id);
    assert.ok(e.trigger >= e.zoneLow && e.trigger <= e.zoneHigh, c.id);
    assert.ok((e.price - e.trigger) * s >= .1 - 1e-7, c.id);
    assert.ok((e.price - e.stop) * s > 0, c.id);
    assert.ok((e.obstacle - e.price) * s > 0, c.id);
    nearly(e.price, c.charts.m3.bars.at(-1).close, c.id);
    nearly(r.loss, (Math.abs(e.price - e.stop) * r.multiplier + r.fee + r.slippage) * r.lots, c.id);
    assert.equal(r.capital, 50000); assert.equal(r.budget, 1000);
    assert.ok(r.loss <= r.budget && [1, 2].includes(r.lots), c.id);
    assert.match(r.assumption, /教学|演示/);
    assert.match(e.protection, /不是系统统一固定价|没有统一/);
    assert.match(c.outcome, /未展示|未发生/);
  }
});

test('continuation cases actually retain the plotted original trend support or resistance through the entry cutoff', () => {
  for (const c of tradingCases.filter(c => c.mode === 'continuation')) {
    const chart = c.charts.m30, line = chart.lines.find(l => l.kind === 'trend');
    for (const p of line.points) nearly(p.price, c.direction === 'long' ? chart.bars[p.index].low : chart.bars[p.index].high, c.id);
    for (const b of chart.bars.slice(line.points[1].index + 1)) {
      const extremum = c.direction === 'long' ? b.low : b.high;
      assert.ok((extremum - at(line, b.index)) * sign(c) >= -.1, `${c.id}: original trend violated at ${b.index}`);
    }
    const ref = chart.lines.find(l => l.role === 'trigger');
    assert.ok((chart.bars.filter(b => b.completed).at(-1).close - ref.points[0].price) * sign(c) >= .1 - 1e-7, c.id);
  }
});

test('A and B remain different at the explicitly chosen main key level', () => {
  for (const c of tradingCases.filter(c => c.mode === 'reversal_a' || c.mode === 'reversal_b')) {
    const chart = c.charts.m30, key = chart.lines.find(l => l.kind === 'horizontal' && l.role === 'key').points[0].price;
    const one = chart.nodes.find(n => n.label === '1');
    const later = chart.bars.filter(b => b.index > one.index && b.completed);
    if (c.mode === 'reversal_a') assert.ok(later.some(b => (b.close - key) * sign(c) >= .1 - 1e-7), c.id);
    else assert.ok(later.every(b => ((c.direction === 'long' ? b.high : b.low) - key) * sign(c) < 0), c.id);
  }
});

test('2B single-candle, later reclaim and full-zone reclaim examples satisfy their separate definitions', () => {
  for (const c of tradingCases.filter(c => c.family.startsWith('2b'))) {
    const bars = c.charts.m3.bars, first = bars[31], second = bars[32], s = sign(c);
    const mirrored = n => c.direction === 'long' ? n : 220 - n;
    const edge = mirrored(110), firstExtreme = c.direction === 'long' ? first.low : first.high;
    if (c.family === '2b-wick') {
      assert.ok((firstExtreme - edge) * s < -.1);
      assert.ok((first.close - edge) * s > .1);
    } else if (c.family === '2b-multibar') {
      assert.ok((first.close - edge) * s < -.1);
      assert.ok((second.close - edge) * s > .1);
    } else {
      const outside = mirrored(109.8), reclaim = mirrored(110.8);
      assert.ok((firstExtreme - outside) * s < -.1);
      assert.ok((first.close - outside) * s > 0 && (first.close - reclaim) * s < 0);
      assert.ok((second.close - reclaim) * s > .1);
    }
    assert.match(c.assumptions.join(' '), /最多3根/);
    assert.ok(Date.parse(bars[32].closedAt) < Date.parse(c.evidence.localValidationAt));
  }
});

test('a 2B clock starts on the first qualifying breach after its preselected confirmed reference', () => {
  for (const c of tradingCases.filter(c => c.family.startsWith('2b'))) {
    const bars = c.charts.m3.bars, s = sign(c), edge = c.direction === 'long' ? (c.family === '2b-zone' ? 109.8 : 110) : 220 - (c.family === '2b-zone' ? 109.8 : 110);
    const extreme = b => c.direction === 'long' ? b.low : b.high;
    nearly(extreme(bars[18]), edge, c.id + ' prior reference must be an actual OHLC extreme');
    assert.ok((extreme(bars[17]) - edge) * s > 0, c.id + ' reference left confirmation');
    assert.ok((extreme(bars[19]) - edge) * s > 0, c.id + ' reference right confirmation');
    for (const b of bars.slice(19, 31)) assert.ok((extreme(b) - edge) * s >= -.1 + 1e-7, `${c.id}: breach occurred before the claimed first bar at ${b.index}`);
    assert.ok((extreme(bars[31]) - edge) * s < -.1, c.id + ' first qualifying breach');
  }
});

test('local attack and continuation references are actual confirmed swing extremes before their breaks', () => {
  for (const c of tradingCases) {
    const bars = c.charts.m3.bars, s = sign(c), highSide = b => c.direction === 'long' ? b.high : b.low;
    assert.ok((highSide(bars[29]) - highSide(bars[28])) * s > 0, c.id);
    assert.ok((highSide(bars[29]) - highSide(bars[30])) * s > 0, c.id);
    assert.ok((bars[33].close - highSide(bars[29])) * s >= .1 - 1e-7, c.id);
    if (c.mode === 'continuation') {
      const m = c.charts.m30.bars;
      assert.ok((highSide(m[40]) - highSide(m[39])) * s > 0, c.id);
      assert.ok((highSide(m[40]) - highSide(m[41])) * s > 0, c.id);
      const line = c.charts.m30.lines.find(l => l.role === 'trigger');
      nearly(line.points[0].price, highSide(m[40]), c.id);
    }
  }
  for (const c of tradingCases.filter(c => c.direction === 'short' && ['inside-child', 'inside-outer', 'harami'].includes(c.family))) {
    assert.match(c.assumptions.join(' '), /组合高点|最高价/);
    assert.doesNotMatch(c.assumptions.join(' '), /组合低点|最低价|最空/);
  }
});

test('inside-bar branches and Harami use different completed pattern boundaries and preselected triggers', () => {
  for (const c of tradingCases.filter(c => ['inside-child', 'inside-outer', 'harami'].includes(c.family))) {
    const [mother, child] = c.charts.m3.bars.slice(34, 36), s = sign(c);
    assert.equal(mother.completed, true); assert.equal(child.completed, true);
    if (c.family !== 'harami') {
      assert.ok(child.high < mother.high && child.low > mother.low, c.id);
      const expected = c.family === 'inside-child' ? child : mother;
      nearly(c.entry.trigger, c.direction === 'long' ? expected.high : expected.low, c.id);
      if (c.family === 'inside-child') assert.ok((c.entry.price - (c.direction === 'long' ? mother.high : mother.low)) * s < 0, c.id);
    } else {
      assert.ok(Math.min(child.open, child.close) > Math.min(mother.open, mother.close));
      assert.ok(Math.max(child.open, child.close) < Math.max(mother.open, mother.close));
      assert.ok(c.direction === 'long' ? child.high > mother.high : child.low < mother.low, c.id);
      nearly(c.entry.trigger, c.direction === 'long' ? Math.max(mother.high, child.high) : Math.min(mother.low, child.low), c.id);
    }
    assert.ok((c.entry.stop - (c.direction === 'long' ? Math.min(mother.low, child.low) : Math.max(mother.high, child.high))) * s < 0, c.id);
  }
});

test('trigger emphasis cannot label the unfinished 30-minute bar or daily context as an executed entry', () => {
  const c = tradingCases[0];
  assert.doesNotMatch(V.chartSvg(c.charts.day, 'day', {stage:'trigger'}), /已完成触发/);
  assert.doesNotMatch(V.chartSvg(c.charts.m30, 'm30', {stage:'trigger'}), /已完成触发/);
  assert.match(V.chartSvg(c.charts.m3, 'm3', {stage:'trigger'}), /已完成触发/);
  assert.match(V.chartSvg(c.charts.m30, 'm30'), /未收盘/);
});
