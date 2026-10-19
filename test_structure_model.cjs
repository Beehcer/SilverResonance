'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {build, OPTIONS} = require('./static/structure-model.js');

const NOW = Date.parse('2026-10-19T10:30:00+08:00');
const copy = value => JSON.parse(JSON.stringify(value));
const bar = (dt, open, close, extra = {}) => ({dt, open, high: Math.max(open, close) + 1,
  low: Math.min(open, close) - 1, close, ...extra});
function context(patch = {}) {
  return {symbol: 'SHFE.ag2612', period: 1800, structure_id: 'structure-a', phase: 'down_break',
    state_label: '下破待反抽', summary: '完成收盘跌破参考下沿，等待后续证据。',
    range: {low: 100, high: 110, kind: 'reference'}, buffer: 1, atr: 3,
    candidate: null, events: [], reference_end: '2026-10-19 07:00:00',
    completed: [bar('2026-10-19 08:00:00', 101, 100), bar('2026-10-19 08:30:00', 100, 99),
      bar('2026-10-19 09:00:00', 99, 98), bar('2026-10-19 09:30:00', 98, 96)],
    updated: NOW / 1000, market_updated: NOW / 1000 - 1, realtime: true, price: 96,
    plan: {status: 'waiting', technical_status: 'waiting', direction: 'short', entry: 96,
      stop: 102, target: 85, trigger: '等待反抽', invalidation: '回到101上方', wait_for: '等待3分钟确认',
      target_basis: '历史支撑', mode: 'confirmed'}, risk: null, ...patch};
}
function note(patch = {}) {
  return {symbol: 'SHFE.ag2612', structure_id: 'structure-a', type: 'unknown', phase: 'unknown',
    stage: 'unknown', segment: 'unknown', intent: 'observe', reason: '',
    reviewed_bar_time: '2026-10-19 09:30:00', ...patch};
}
function readyContext(patch = {}) {
  const c = context();
  c.plan.status = 'ready';
  c.risk = {fee_configured: true, max_lots: 1, net_rr: 1.6, rows: [{lots: 1, allowed: true}]};
  return {...c, ...patch};
}

function marketLayer(patch = {}) {
  return {status: 'ok', period: 1800, decision_bar_time: '2026-10-19 09:30:00',
    phase: {key: 'down_no_follow', label: '下跌延续不足', direction: 'down', summary: '尚不能据单根K线确认震荡。'},
    scenarios: [{condition: '反弹到观察带后，等待完成K线受阻。'}], ...patch};
}

test('aligned completed trend evidence updates automatic phase without changing plan or numbering', () => {
  const c = context({market_structure: marketLayer()});
  const result = build(c, {}, NOW);
  assert.equal(result.structure.phase_label, '下跌延续不足');
  assert.equal(result.structure.rule_phase, 'down_break');
  assert.equal(result.observation.detail, '尚不能据单根K线确认震荡。');
  assert.equal(result.numbering.status, 'pending');
  assert.equal(result.plan.ready, false);
  assert.deepEqual(result.transition.conditions, ['反弹到观察带后，等待完成K线受阻。']);
  const manual = build(c, note({type: 'up', phase: 'pullback'}), NOW);
  assert.equal(manual.structure.phase_label, '回撤中');
  assert.equal(manual.direction.value, 'up');
  assert.equal(manual.direction.conflict, true);
});

test('wrong period or misaligned trend layer never overrides current completed evidence', () => {
  for (const patch of [{period: 180}, {decision_bar_time: '2026-10-19 09:00:00'}, {status: 'insufficient'}]) {
    const result = build(context({market_structure: marketLayer(patch)}), {}, NOW);
    assert.equal(result.structure.phase_label, '下破待反抽');
  }
});

test('a local support break inside the outer reference cannot promote the whole structure to a downtrend', () => {
  const layer = marketLayer({phase: {key: 'line_broken_down', scope: 'local', direction: 'down',
    label: '参考带内：局部回升支撑被破，待反抽', summary: '外层参考带未被突破。'}});
  const c = context({phase: 'inside', market_structure: layer});
  const m = build(c, {}, NOW);
  assert.equal(m.structure.type, 'unknown');
  assert.match(m.structure.label, /外层参考带内/);
  assert.match(m.structure.phase_label, /局部回升支撑被破/);
  assert.equal(m.direction.local, 'down');
  assert.equal(m.diagram.direction, 'unknown');
  assert.equal(m.levels.shelter.price, null);
  c.range.kind = 'range';
  assert.equal(build(c, {}, NOW).structure.type, 'range');
  const manual = build(c, note({type: 'down', phase: 'pullback'}), NOW);
  assert.equal(manual.structure.type, 'down');
  assert.equal(manual.structure.type_source, 'manual');
  assert.equal(manual.structure.phase_label, '反弹中');
});

test('next evidence prioritizes the current relevant branch and excludes withdrawn price paths', () => {
  const layer = marketLayer({selected_scenario: 'direct_down', scenarios: [
    {key: 'old', status: 'target_passed', drawable: false, condition: '旧目标已走过'},
    {key: 'pause_watch', status: 'waiting_evidence', drawable: false, condition: '未形成整理'},
    {key: 'retest_down', drawable: true, condition: '反抽后受阻'},
    {key: 'direct_down', drawable: true, condition: '直接下行先观察前低，不追'}]});
  const m = build(context({market_structure: layer}), {}, NOW);
  assert.deepEqual(m.transition.conditions, ['直接下行先观察前低，不追', '反抽后受阻']);
  assert.equal(m.plan.ready, false);
});

test('exhausted continuation space does not promote a hypothetical reversal in the top readout', () => {
  const layer = marketLayer({selected_scenario: 'direct_down', scenarios: [
    {key: 'direct_down', label: '直接下行', status: 'target_passed', status_label: '下方参考已越过', drawable: false},
    {key: 'reclaim_up', status: 'waiting', drawable: true, condition: '假设先反弹收回'}]});
  const m = build(context({market_structure: layer}), {}, NOW);
  assert.match(m.transition.conditions[0], /下方参考已越过/);
  assert.doesNotMatch(m.transition.conditions.join(''), /假设先反弹/);
});

test('new trend evidence cannot make a stale plan ready or invent an old manual record', () => {
  const result = build(readyContext({market_structure: marketLayer(), realtime: false,
    market_updated: NOW / 1000 - 60}), {}, NOW);
  assert.equal(result.plan.ready, false);
  assert.equal(result.numbering.stage, 'unknown');
  assert.match(result.structure.phase_label, /历史观察：下跌延续不足/);
  assert.doesNotMatch(result.numbering.label, /人工记录/);
});

test('latest available display requires a recent series check and never relaxes trading freshness', () => {
  const c = readyContext({market_structure: marketLayer(), realtime: false, market_updated: NOW / 1000 - 60,
    freshness: {status: 'latest_available', checked_at: NOW / 1000}});
  const result = build(c, {}, NOW);
  assert.match(result.statuses.data.label, /最新可用/);
  assert.match(result.structure.phase_label, /^最新可用观察/);
  assert.equal(result.plan.ready, false);
  assert.equal(result.numbering.stage, 'unknown');
  c.freshness.checked_at -= 25;
  const expired = build(c, {}, NOW);
  assert.match(expired.statuses.data.label, /历史快照/);
});

test('UMD exposes the same API to CommonJS and browser without requiring a DOM', () => {
  assert.equal(typeof build, 'function');
  const browser = {};
  browser.window = browser;
  vm.createContext(browser);
  vm.runInContext(fs.readFileSync(require.resolve('./static/structure-model.js'), 'utf8'), browser);
  assert.equal(typeof browser.window.StructureModel.build, 'function');
  assert.equal(browser.window.StructureModel.build(context(), {}, NOW).status, 'ok');
  assert.ok(OPTIONS.phases.some(item => item.value === 'pullback'));
  assert.ok(OPTIONS.stages.some(item => item.value === 'point9'));
});

test('phase mappings preserve the automatic phase and never fabricate document numbering', () => {
  for (const phase of ['inside', 'up_break', 'down_break', 'up_hold', 'down_hold', 'up_extension', 'down_extension', 'up_failed', 'down_failed']) {
    const m = build(context({phase}), {}, NOW);
    assert.equal(m.structure.rule_phase, phase);
    assert.equal(m.numbering.status, 'pending');
    assert.equal(m.numbering.stage, 'unknown');
    assert.equal(m.numbering.segment, 'unknown');
    assert.equal(m.diagram.highlight_source, 'none');
    assert.ok(!m.numbering.label.includes('点3'));
  }
});

test('inside a reference band has no confirmed range or direction; a rule range remains a rule observation', () => {
  const c = context({phase: 'inside', state_label: '点3上涨结构'});
  const reference = build(c, {}, NOW);
  assert.equal(reference.structure.type, 'unknown');
  assert.match(reference.structure.label, /方向未确认/);
  assert.equal(reference.direction.value, 'unknown');
  c.range.kind = 'range';
  const ranged = build(c, {}, NOW);
  assert.equal(ranged.structure.type, 'range');
  assert.equal(ranged.structure.source, 'rule');
  assert.match(ranged.structure.label, /规则观察/);
  assert.equal(ranged.numbering.status, 'pending');
});

test('the final completed bar is used; a single rebound or pullback is only counter-move evidence', () => {
  const c = context({phase: 'down_extension'});
  c.completed[c.completed.length - 1] = bar('2026-10-19 09:30:00', 97, 99);
  const rebound = build(c, {}, NOW);
  assert.equal(rebound.observation.key, 'rebound_bar');
  assert.match(rebound.observation.label, /待确认/);
  assert.equal(rebound.direction.rule, 'down');
  assert.equal(rebound.freshness.completed_bar_time, '2026-10-19 09:30:00');
  c.phase = 'up_extension';
  c.completed[c.completed.length - 1] = bar('2026-10-19 09:30:00', 99, 96);
  const pullback = build(c, {}, NOW);
  assert.equal(pullback.observation.key, 'pullback_bar');
  assert.equal(pullback.direction.rule, 'up');
  assert.equal(pullback.numbering.status, 'pending');
});

test('explicitly incomplete and future bars cannot create a new rebound stage', () => {
  for (const flags of [{complete: false}, {completed: false}, {closed: false}, {is_complete: false}, {final: false}, {unfinished: true}]) {
    const c = context();
    c.completed.push(bar('2026-10-19 10:00:00', 96, 108, flags));
    const m = build(c, {}, NOW);
    assert.equal(m.observation.key, 'down_break_observation');
    assert.equal(m.freshness.completed_bar_time, '2026-10-19 09:30:00');
    assert.ok(m.warnings.some(item => item.includes('已排除')));
  }
  const future = context();
  future.completed.push(bar('2026-10-19 11:00:00', 96, 108));
  assert.equal(build(future, {}, NOW).observation.key, 'down_break_observation');
});

test('no completed evidence, wrong period, malformed or out-of-order bars disable current state', () => {
  for (const c of [context({completed: undefined}), context({completed: [], bars: context().completed}),
    context({period: 900}), context({structure_id: ''}), context({phase: 'imaginary'})]) {
    const m = build(c, note({stage: 'point3'}), NOW);
    assert.equal(m.status, 'insufficient');
    assert.equal(m.plan.ready, false);
    assert.equal(m.numbering.status, 'pending');
  }
  const reversed = context();
  reversed.completed.reverse();
  assert.equal(build(reversed, {}, NOW).status, 'insufficient');
  const bad = context({completed: [bar('2026-10-19 09:00:00', 98, 97),
    bar('2026-10-19 09:30:00', 97, 99, {high: 90})]});
  assert.equal(build(bad, {}, NOW).status, 'insufficient');
});

test('historical, expired, missing-time and future-time snapshots cannot show ready or current numbering', () => {
  const manual = note({type: 'down', stage: 'point3', segment: '3-4'});
  for (const patch of [{realtime: false}, {market_updated: NOW / 1000 - 20.001},
    {market_updated: null}, {market_updated: NOW / 1000 + 6}]) {
    const m = build(readyContext(patch), manual, NOW);
    assert.equal(m.status, 'stale');
    assert.equal(m.plan.ready, false);
    assert.equal(m.plan.status, 'stale');
    assert.equal(m.numbering.status, 'pending');
    assert.equal(m.numbering.stage, 'unknown');
    assert.equal(m.diagram.highlight_source, 'none');
    assert.match(m.structure.phase_label, /历史/);
  }
  assert.equal(build(readyContext({market_updated: NOW / 1000 - 20}), {}, NOW).plan.ready, true);
});

test('a plan only displays readiness with current data, valid signed prices and explicit risk evidence', () => {
  assert.equal(build(readyContext(), {}, NOW).plan.ready, true);
  for (const risk of [null, {}, {fee_configured: false, max_lots: 1, net_rr: 2, rows: [{allowed: true}]},
    {fee_configured: true, max_lots: 0, net_rr: 2, rows: [{allowed: false}]},
    {fee_configured: true, max_lots: 1, net_rr: -1, rows: [{allowed: true}]}]) {
    const m = build(readyContext({risk}), {}, NOW);
    assert.equal(m.plan.ready, false);
    assert.equal(m.plan.status, 'review');
    assert.equal(m.plan.original_status, 'ready');
  }
  for (const status of ['waiting', 'risk_blocked', 'invalid', 'candidate']) {
    const c = readyContext();
    c.plan.status = status;
    const m = build(c, note({type: 'down', stage: 'point3', segment: '3-4'}), NOW);
    assert.equal(m.plan.ready, false);
    assert.equal(m.plan.status, status);
  }
  const c = readyContext();
  c.plan.target = 105;
  assert.equal(build(c, {}, NOW).plan.ready, false);
  c.plan.target = 85;
  c.plan.stop = 90;
  assert.equal(build(c, {}, NOW).plan.ready, false);
});

test('actual tracking candidate shape is recognized at four and five bars without claiming confirmation', () => {
  for (const bars of [4, 5]) {
    const candidate = {id: 'candidate-a', label: '新整理平台候选', status: 'forming', low: 91,
      high: 96, start_time: '2026-10-19 08:00:00', end_time: '2026-10-19 09:30:00',
      bars, required_bars: 6, reason: '4根独立完成棒形成窄幅重叠区', atr: 2, buffer: 1, confirmations: bars - 4};
    const m = build(context({candidate}), {}, NOW);
    assert.equal(m.observation.key, 'consolidation_candidate');
    assert.match(m.observation.detail, new RegExp(bars + '/6'));
    assert.match(m.observation.label, /尚未确认/);
    assert.ok(m.transition.conditions.some(item => item.includes(bars + '/6')));
    assert.equal(m.structure.rule_phase, 'down_break');
    assert.equal(m.numbering.status, 'pending');
  }
});

test('failed break does not silently become a confirmed reverse direction', () => {
  for (const phase of ['up_failed', 'down_failed']) {
    const m = build(context({phase}), {}, NOW);
    assert.equal(m.direction.rule, 'unknown');
    assert.equal(m.observation.key, phase);
    assert.match(m.observation.detail, /不自动推断反向/);
  }
});

test('manual notes must match both symbol and structure ID, including prices and numbering', () => {
  for (const patch of [{symbol: 'SHFE.au2612'}, {structure_id: 'structure-b'}, {symbol: undefined}, {structure_id: undefined}]) {
    const m = build(context(), note({type: 'up', stage: 'point3', segment: '3-4', shelter: 123,
      breakeven: 124, ...patch}), NOW);
    assert.equal(m.manual.applied, false);
    assert.equal(m.structure.type, 'down');
    assert.equal(m.numbering.status, 'pending');
    assert.equal(m.levels.shelter.price, 100);
    assert.equal(m.levels.breakeven.price, null);
  }
});

test('only explicit supported manual stages and segments produce manual labels, never inferred points', () => {
  const segment = build(context(), note({segment: '1-2'}), NOW);
  assert.equal(segment.numbering.status, 'manual');
  assert.equal(segment.numbering.stage, 'unknown');
  assert.match(segment.numbering.label, /1→2/);
  assert.equal(segment.diagram.highlight_source, 'manual');
  const point = build(context(), note({stage: 'point3'}), NOW);
  assert.equal(point.numbering.stage, 'point3');
  assert.equal(point.numbering.source, 'manual');
  assert.match(point.numbering.label, /人工确认/);
  assert.equal(point.diagram.highlight_source, 'none');
  for (const stage of ['3', 'point10', '点3', '<b>point3</b>']) {
    const m = build(context(), note({stage, segment: '3-9', reason: '现在点3'}), NOW);
    assert.equal(m.numbering.status, 'pending');
    assert.equal(m.diagram.highlight_source, 'none');
  }
});

test('manual and rule directions and phases retain independent sources and conflict evidence', () => {
  const c = context({phase: 'up_hold'});
  const phaseOnly = build(c, note({phase: 'pullback'}), NOW);
  assert.equal(phaseOnly.structure.label, '上行规则观察');
  assert.equal(phaseOnly.structure.type_source, 'rule');
  assert.equal(phaseOnly.structure.phase_source, 'manual');
  assert.equal(phaseOnly.structure.phase_label, '回撤中');
  assert.equal(phaseOnly.direction.source, 'rule');
  const contrary = build(c, note({type: 'down', phase: 'rejected'}), NOW);
  assert.equal(contrary.structure.phase_label, '反弹受阻');
  assert.equal(contrary.direction.conflict, true);
  assert.equal(contrary.direction.rule, 'up');
  assert.equal(contrary.direction.manual, 'down');
  assert.equal(contrary.observation.key, phaseOnly.observation.key);
  assert.equal(contrary.observation.detail, phaseOnly.observation.detail);
  assert.ok(contrary.warnings.some(item => item.includes('方向与程序规则观察不同')));
});

test('new completed bars require manual re-review and clear current point highlighting while keeping saved values', () => {
  const manual = note({type: 'down', phase: 'rejected', stage: 'point3', segment: '3-4',
    reviewed_bar_time: '2026-10-19 09:00:00'});
  const m = build(context(), manual, NOW);
  assert.equal(m.manual.needs_review, true);
  assert.equal(m.manual.stage, 'point3');
  assert.equal(m.manual.segment, '3-4');
  assert.equal(m.numbering.status, 'pending');
  assert.equal(m.diagram.highlight_source, 'none');
  assert.match(m.numbering.label, /待复核/);
  assert.match(m.structure.label, /待复核/);
  assert.match(m.structure.phase_label, /待复核/);
  assert.match(m.manual.phase_label, /待复核/);
  manual.reviewed_bar_time = m.freshness.completed_bar_time;
  const reviewed = build(context(), manual, NOW);
  assert.equal(reviewed.manual.needs_review, false);
  assert.equal(reviewed.numbering.status, 'manual');
  assert.equal(reviewed.diagram.highlight_source, 'manual');
});

test('shelter boundary, plan stop and manually defined breakeven remain distinct', () => {
  const down = build(context(), {}, NOW);
  assert.equal(down.levels.shelter.price, 100);
  assert.equal(down.levels.stop.price, 102);
  assert.equal(down.levels.breakeven.price, null);
  assert.equal(down.levels.breakeven.source, 'undefined');
  assert.equal(build(context({phase: 'up_break'}), {}, NOW).levels.shelter.price, 110);
  assert.equal(build(context({phase: 'inside'}), {}, NOW).levels.shelter.price, null);
  const manual = build(context(), note({shelter: '99.5', breakeven: '90'}), NOW);
  assert.equal(manual.levels.shelter.price, 99.5);
  assert.equal(manual.levels.shelter.source, 'manual');
  assert.equal(manual.levels.breakeven.price, 90);
  const alias = build(context(), note({shelter_price: 99, breakeven_trigger: 92}), NOW);
  assert.equal(alias.levels.shelter.price, 99);
  assert.equal(alias.levels.breakeven.price, 92);
  for (const value of ['', 'NaN', -1, 0, Infinity]) {
    const m = build(context(), note({shelter: value, breakeven: value}), NOW);
    assert.equal(m.levels.shelter.price, 100);
    assert.equal(m.levels.breakeven.price, null);
  }
});

test('future events are not evidence and user-facing transition conditions have actual boundary values', () => {
  const c = context({events: [{id: 'old', at: '2026-10-19 09:00:00', label: '已完成下破', detail: '低于99'},
    {id: 'future', at: '2026-10-19 10:00:00', label: '未来点3', detail: '不可使用'}]});
  const m = build(c, {}, NOW);
  assert.ok(m.evidence.some(item => item.key === 'event_old'));
  assert.ok(!m.evidence.some(item => item.key === 'event_future'));
  assert.ok(m.transition.conditions.some(item => item.includes('99')));
  assert.ok(m.transition.invalidation.includes('101'));
});

test('build is deterministic and does not mutate the context, risk or manual records', () => {
  const c = readyContext(), manual = note({type: 'down', segment: '1-2'});
  const original = copy({c, manual});
  const first = build(c, manual, NOW), second = build(c, manual, NOW);
  assert.deepEqual(first, second);
  assert.deepEqual({c, manual}, original);
  first.risk.rows[0].allowed = false;
  assert.equal(c.risk.rows[0].allowed, true);
  assert.deepEqual(JSON.parse(JSON.stringify(second)), second);
});
