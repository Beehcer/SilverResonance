'use strict';
// Offline Canvas/HTTP-state tests: no browser, network, quote session or storage.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { execFileSync } = require('node:child_process');
const chart = require('./static/market-structure-chart.js');

const CURRENT = 3068;
const colors = { down: '#52c79c', up: '#ef737a', range: '#dfb65e' };
function scenario(key = 'retest_down', overrides = {}) {
  return { key, role: 'retest', direction: 'down', label: '反弹受阻后观察下方参考',
    status: 'waiting', drawable: true, target: 3065,
    trigger: { price: 3071, low: 3072, high: 3077 },
    path: [CURRENT, 3074.5, 3071, 3065], ...overrides };
}
function geometry(selectedKey = 'retest_down') {
  return { first: 0, last: 31, x: index => 40 + 12 * index,
    y: price => 60 + (3200 - price) * 2,
    left: 30, right: 1100, split: 700, top: 60, bottom: 420,
    slot: 12, narrow: false, indexAt: (time, fallback) => fallback,
    currentPrice: CURRENT, selectedKey, viewMode: 'single' };
}
class RecordingCanvas {
  constructor() { this.strokes = []; this.texts = []; this.stack = []; this.points = []; this.dash = []; this.globalAlpha = 1; }
  save() { this.stack.push({ dash: [...this.dash], strokeStyle: this.strokeStyle, lineWidth: this.lineWidth, globalAlpha: this.globalAlpha }); }
  restore() { const state = this.stack.pop(); if (state) Object.assign(this, state); }
  beginPath() { this.points = []; }
  closePath() {}
  clip() {}
  fill() {}
  clearRect(...values) { this.finite(values); }
  fillRect(...values) { this.finite(values); }
  rect(...values) { this.finite(values); }
  arc(...values) { this.finite(values); }
  setLineDash(values) { this.finite(values); this.dash = [...values]; }
  moveTo(x, y) { this.finite([x, y]); this.points.push({ op: 'move', x, y }); }
  lineTo(x, y) { this.finite([x, y]); this.points.push({ op: 'line', x, y }); }
  stroke() { this.strokes.push({ points: this.points.map(p => ({ ...p })), dash: [...this.dash], color: this.strokeStyle, alpha: this.globalAlpha }); }
  fillText(text, ...values) { this.finite(values); this.texts.push(String(text)); }
  measureText(text) { return { width: String(text).length * 7 }; }
  finite(values) { assert(values.every(Number.isFinite), `Canvas received invalid coordinates: ${values}`); }
}
function branchStrokes(canvas, direction) {
  return canvas.strokes.filter(s => s.color === colors[direction] && s.dash.length && s.points.length >= 2);
}

test('selected bearish path uses the exact history y mapping, including its live-price origin', () => {
  const ctx = new RecordingCanvas(), g = geometry(), branch = scenario();
  chart.drawPaths(ctx, [branch], g);
  const strokes = branchStrokes(ctx, 'down');
  assert.equal(strokes.length, 1, 'must draw one actual scenario polyline');
  assert.deepEqual(strokes[0].points.map(p => p.y), branch.path.map(g.y));
  assert.equal(strokes[0].points[0].y, g.y(CURRENT));
  assert(strokes[0].points.at(-1).y > strokes[0].points[0].y, 'downside reference must be below current price on screen');
  assert(strokes[0].points[1].y < strokes[0].points[0].y, 'a conditional rebound is allowed before continuation');
});

test('bullish mirror shares the history axis and finishes above current price', () => {
  const down = scenario(), mirror = 6200;
  const up = scenario('retest_up', { direction: 'up', target: mirror - down.target,
    trigger: { price: mirror - down.trigger.price }, path: down.path.map(p => mirror - p) });
  const ctx = new RecordingCanvas(), g = { ...geometry('retest_up'), currentPrice: mirror - CURRENT };
  chart.drawPaths(ctx, [up], g);
  const strokes = branchStrokes(ctx, 'up');
  assert.equal(strokes.length, 1);
  assert.deepEqual(strokes[0].points.map(p => p.y), up.path.map(g.y));
  assert(strokes[0].points.at(-1).y < strokes[0].points[0].y);
});

test('text-only consolidation stays in the cards and cannot produce a zigzag', () => {
  const text = scenario('pause_watch', { role: 'consolidation', direction: 'range', drawable: false,
    status: 'waiting', target: null, path: [] });
  const market = { status: 'ok', price: CURRENT, candidate: null, scenarios: [scenario(), text] };
  const cards = chart.scenarios({ market_structure: market });
  assert.equal(cards.length, 2);
  assert(cards.some(s => s.key === 'pause_watch'));
  const ctx = new RecordingCanvas();
  chart.drawPaths(ctx, cards, geometry('pause_watch'));
  assert.equal(branchStrokes(ctx, 'range').length, 0);
  assert.equal(branchStrokes(ctx, 'down').length, 0, 'choosing text must not silently draw another scenario');
});

test('changing selected branch changes only which scenario is drawn', () => {
  const first = scenario();
  const second = scenario('direct_down', { role: 'direct', path: [CURRENT, 3065] });
  const ctx = new RecordingCanvas();
  chart.drawPaths(ctx, [first, second], geometry('direct_down'));
  const strokes = branchStrokes(ctx, 'down');
  assert.equal(strokes.length, 1);
  assert.deepEqual(strokes[0].points.map(p => p.y), second.path.map(geometry().y));
});

test('comparison mode retains every valid direction when focus changes', () => {
  const branches = [scenario(), scenario('direct_down', {role: 'direct', path: [CURRENT, 3065]}),
    scenario('reclaim_up', {role: 'reclaim', direction: 'up', path: [CURRENT, 3078, 3077, 3084]})];
  for (const focus of ['retest_down', 'direct_down', 'reclaim_up', 'consolidation']) {
    const ctx = new RecordingCanvas();
    chart.drawPaths(ctx, branches, {...geometry(focus), viewMode: 'all'});
    assert.equal(branchStrokes(ctx, 'down').length, 2);
    assert.equal(branchStrokes(ctx, 'up').length, 1);
    for (const stroke of [...branchStrokes(ctx, 'down'), ...branchStrokes(ctx, 'up')]) {
      assert.equal(stroke.points[0].y, geometry().y(CURRENT));
    }
  }
});

test('secondary levels need a continuous sourced chain and cannot revive an invalid branch', () => {
  const next = (from, target, trigger) => ({from, price: target, trigger, condition: '完成越界且后续未收回', source: {price: target}});
  const branch = scenario('retest_down', {continuations: [next(3065,3061,3063),next(3061,3053,3059)]});
  assert.deepEqual(chart.continuationSteps(branch,CURRENT).map(s=>s.price),[3061,3053]);
  const ctx = new RecordingCanvas();
  chart.drawPaths(ctx,[branch],geometry());
  assert(ctx.texts.some(text=>text.includes('破3,065后')));
  assert(ctx.texts.some(text=>text.includes('破3,061后')));
  assert.deepEqual(chart.continuationSteps({...branch,status:'invalid'},CURRENT),[]);
  assert.deepEqual(chart.continuationSteps({...branch,continuations:[next(3065,3070,3063)]},CURRENT),[]);
  assert.deepEqual(chart.continuationSteps({...branch,continuations:[next(3065,3061,3063),next(3060,3053,3058)]},CURRENT).map(s=>s.price),[3061]);
});

test('defensive renderer rejects wrong-side targets, stale origins, and invalid statuses', () => {
  const invalid = [
    scenario('wrong-side', { target: 3069, path: [CURRENT, 3074, 3069] }),
    scenario('old-origin', { path: [3072, 3074, 3065] }),
    scenario('nan', { path: [CURRENT, NaN, 3065] }),
    scenario('passed', { status: 'target_passed' }),
    scenario('invalid', { status: 'invalid' }),
    scenario('no-target', { status: 'no_target', target: null }),
    scenario('hidden', { drawable: false }),
  ];
  for (const branch of invalid) {
    assert.deepEqual(chart.drawablePath(branch, CURRENT), [], branch.key);
    const ctx = new RecordingCanvas();
    chart.drawPaths(ctx, [branch], geometry(branch.key));
    assert.equal(branchStrokes(ctx, 'down').length, 0, branch.key);
  }
});

test('invalid or text-only paths cannot expand the shared price scale', () => {
  const branch = scenario();
  const hidden = scenario('hidden', { drawable: false, path: [CURRENT, 99999], target: 99999 });
  const market = { status: 'ok', price: CURRENT, candidate: null,
    observation_zone: { low: 3072, high: 3077 }, reference: { low: 3053, high: 3102 },
    scenarios: [branch, hidden] };
  const values = chart.values(market, 0, 31);
  assert(!values.includes(99999));
  for (const value of branch.path) assert(values.includes(value), `drawable path value missing from axis: ${value}`);
});

test('the same point has the same canvas height in historical and scenario layers', () => {
  const ctx = new RecordingCanvas(), g = geometry();
  const market = { status: 'ok', trend_lines: [{ kind: 'support', anchors: [
    { index: 10, price: 3065 }, { index: 20, price: CURRENT }], end_index: 20 }], candidate: null };
  chart.drawHistory(ctx, market, g, 'over');
  chart.drawPaths(ctx, [scenario()], g);
  const historical = ctx.strokes.find(s => s.color === '#81b7ef' && s.points.length === 2);
  assert(historical);
  assert.equal(historical.points.at(-1).y, branchStrokes(ctx, 'down')[0].points[0].y);
});

test('compact quantitative layout preserves prices and stays within small and large panels', () => {
  const path = scenario().path;
  for (const width of [90, 180, 400, 900]) {
    const g = { ...geometry(), right: 700 + width, narrow: width < 200 };
    const points = chart.pricePathPoints(path, g);
    assert.deepEqual(points.map(p => p[1]), path.map(g.y));
    assert(points.at(-1)[0] < g.right - 15);
    for (let i = 1; i < points.length; i++) {
      assert(points[i][0] > points[i-1][0]);
      assert(points[i][0] - points[i-1][0] <= 76, 'a tiny move must not stretch across the whole right panel');
    }
  }
});

test('rhythm schematic preserves every relative price and turn without adding a rebound', () => {
  const branches = [scenario(), scenario('direct_down', {role:'direct',path:[CURRENT,3065]}),
    scenario('reclaim_up',{direction:'up',role:'reclaim',path:[CURRENT,3078,3077,3084]})];
  for (const s of branches) {
    const nodes = chart.rhythmNodes(s, CURRENT);
    assert.deepEqual(nodes.map(p => p.price), s.path);
    for (let i = 0; i < nodes.length; i++) for (let j = 0; j < nodes.length; j++) {
      assert.equal(Math.sign(nodes[j].y - nodes[i].y) || 0, Math.sign(s.path[i] - s.path[j]) || 0);
    }
  }
  assert.equal(chart.rhythmNodes(branches[1],CURRENT).length,2,'direct continuation must not gain invented zigzags');
  assert.deepEqual(chart.rhythmNodes(scenario('invalid',{status:'invalid'}),CURRENT),[]);
});

test('rhythm cards declare their unscaled geometry and retain alternatives, invalid branches stay text-only', () => {
  const branches = [scenario(),scenario('direct_down',{role:'direct',path:[CURRENT,3065]}),
    scenario('pause_watch',{role:'consolidation',drawable:false,path:[],status_label:'等待整理证据'})];
  const g = {currentPrice:CURRENT, selectedKey:'retest_down', viewMode:'all'};
  const all = chart.rhythmMarkup(branches,g);
  assert(all.includes('不按价差 / 时间比例'));
  assert(all.includes('实际幅度见下方同价轴图'));
  assert.equal((all.match(/<svg /g)||[]).length,2);
  assert(all.includes('等待整理证据（仅文字观察）'));
  assert.equal((chart.rhythmMarkup(branches,{...g,viewMode:'single'}).match(/<svg /g)||[]).length,1);
  assert.equal((chart.rhythmMarkup(branches,{...g,viewMode:'single',selectedKey:'pause_watch'}).match(/<svg /g)||[]).length,0);
});

test('rhythm markup escapes source labels and uses no unvalidated target extensions', () => {
  const malicious = scenario('x\" onclick=\"alert(1)', {label:'<img src=x onerror=alert(1)>',status_label:'<script>run()</script>',
    continuations:[{from:3065,price:99999,trigger:3063,condition:'invalid',source:{price:99999}}]});
  const html = chart.rhythmMarkup([malicious],{currentPrice:CURRENT,selectedKey:malicious.key});
  assert(!html.includes('<img')); assert(!html.includes('<script>')); assert(!html.includes(' onclick="'));
  assert(!html.includes('99,999')); assert(html.includes('&lt;img'));
});

test('backend fixture and renderer agree on every branch across live and mirrored prices', () => {
  const script = 'import json; from test_scenario_geometry import analyze; '
    + 'print(json.dumps([analyze(p, m) for m in (False,True) for p in (3040,3053,3068,3074,3078,3088,3120)],allow_nan=False))';
  const markets = JSON.parse(execFileSync(process.env.PYTHON || 'python', ['-X', 'utf8', '-c', script],
    { cwd: __dirname, encoding: 'utf8' }));
  for (const market of markets) for (const branch of market.scenarios) {
    const drawable = chart.drawablePath(branch, market.price, market);
    assert.equal(drawable.length > 0, branch.drawable, `${market.price} ${branch.key}`);
    if (drawable.length) assert.equal(drawable[0], market.price);
  }
});

class Element {
  constructor(tag) {
    this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.style = { setProperty() {} }; this.attributes = {}; this.textContent = ''; this.className = '';
    this.classList = { add() {}, toggle() {} };
  }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  setAttribute(key, value) { this.attributes[key] = value; }
  closest(selector) {
    if (selector === '.outlook-scenario' && this.className.split(' ').includes('outlook-scenario')) return this;
    return this.parent ? this.parent.closest(selector) : null;
  }
  querySelectorAll(selector) {
    const found = [];
    for (const child of this.children) {
      if (selector === 'button[data-scenario-key]' && child.tag === 'button' && child.dataset.scenarioKey) found.push(child);
      found.push(...child.querySelectorAll(selector));
    }
    return found;
  }
  focus() {}
}
function selectionHarness() {
  const html = fs.readFileSync(path.join(__dirname, 'templates/index.html'), 'utf8');
  const start = html.indexOf('  function renderOutlookScenarios('), end = html.indexOf('  function drawOutlook(', start);
  const clickStart = html.indexOf("  outlookScenarios.addEventListener('click',"), clickEnd = html.indexOf('  async function loadOutlook(', clickStart);
  assert(start >= 0 && end > start && clickStart >= 0 && clickEnd > clickStart);
  const root = new Element('div'), nodes = new Map();
  const document = { activeElement: null, createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element('#text'), { textContent: text }),
    getElementById: id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); } };
  const context = vm.createContext({ document, window: { MarketStructureChart: chart }, Number, Array, String, root });
  vm.runInContext(`let currentSymbol='TEST',outlookPeriod=1800,outlookSelectedKey=null,outlookSelectionIdentity='',outlookSelectionPinned=false,outlookViewMode='all';
    let outlookData=null;
    const outlookScenarios=root,outlookColors={up:'red',down:'green',range:'yellow'};
    function outlookDirection(s){return s.direction;}
    function outlookPrice(p){return String(p);}
    function drawOutlook(){}
    ${html.slice(start, end)}
    ${html.slice(clickStart, clickEnd)}`, context);
  return {
    render(data) { context.payload = data; vm.runInContext('outlookData=payload;renderOutlookScenarios(payload.analysis)', context); },
    click(key) {
      const button = root.querySelectorAll('button[data-scenario-key]').find(item => item.dataset.scenarioKey === key);
      assert(button, `missing card ${key}`); root.listeners.click({ target: button });
    },
    state: () => vm.runInContext('({key:outlookSelectedKey,pinned:outlookSelectionPinned,identity:outlookSelectionIdentity})', context),
    label: () => document.getElementById('outlook-selection').textContent,
  };
}
function selectionPayload(priceNow, preferred, options = {}) {
  const direct = scenario('direct_down', { role: 'direct', path: [priceNow, 3065],
    ...(options.directStatus ? { status: options.directStatus, drawable: false, target: null, path: [] } : {}) });
  const retest = scenario('retest_down', { path: [priceNow, 3074.5, 3071, 3065] });
  const reclaim = scenario('reclaim_up', { direction: 'up', role: 'reclaim', path: [priceNow, 3078, 3077, 3096], target: 3096 });
  const scenarios = [direct, retest, reclaim];
  return { bars: [{ close: priceNow }], analysis: {
    structure_tracking: { active: { id: options.id || 'generation-a' } },
    market_structure: { status: 'ok', price: priceNow, selected_scenario: preferred, scenarios, candidate: null },
  } };
}

test('automatic selection follows direct-to-retest evidence without becoming a manual pin', () => {
  const h = selectionHarness();
  h.render(selectionPayload(3068, 'direct_down'));
  assert.equal(h.state().key, 'direct_down'); assert.equal(h.state().pinned, false);
  h.render(selectionPayload(3074, 'retest_down'));
  assert.equal(h.state().key, 'retest_down'); assert.equal(h.state().pinned, false);
});

test('a manually selected branch remains selected as text when its premise becomes invalid', () => {
  const h = selectionHarness();
  h.render(selectionPayload(3068, 'retest_down')); h.click('direct_down');
  assert.equal(h.state().pinned, true);
  h.render(selectionPayload(3088, 'reclaim_up', { directStatus: 'invalid' }));
  assert.equal(h.state().key, 'direct_down');
  assert.equal(h.state().pinned, true);
  assert(h.label().includes('当前仅文字观察'));
});

test('a new structure identity releases a manual selection and chooses its new default', () => {
  const h = selectionHarness();
  h.render(selectionPayload(3068, 'retest_down')); h.click('direct_down');
  h.render(selectionPayload(3088, 'reclaim_up', { id: 'generation-b', directStatus: 'invalid' }));
  assert.equal(h.state().key, 'reclaim_up');
  assert.equal(h.state().pinned, false);
});

test('an explicit exhausted-direction default remains text instead of falling back to a bounce', () => {
  const h = selectionHarness();
  h.render(selectionPayload(3053, 'direct_down', { directStatus: 'target_passed' }));
  assert.equal(h.state().key, 'direct_down');
  assert(h.label().includes('当前仅文字观察'));
});

function outlookHarness() {
  const html = fs.readFileSync(path.join(__dirname, 'templates/index.html'), 'utf8');
  const start = html.indexOf('  async function loadOutlook(');
  const end = html.indexOf('  function applyOutlook(', start);
  assert(start >= 0 && end > start);
  const deferred = [], nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, { textContent: '', disabled: false }); return nodes.get(id); };
  const context = vm.createContext({ Date, Number, Array, AbortController, setTimeout, clearTimeout,
    document: { hidden: false, getElementById: node },
    fetch: (url, options) => new Promise((resolve, reject) => deferred.push({ url, options, resolve, reject })) });
  vm.runInContext(`let currentSymbol='TEST',outlookPeriod=1800,outlookSequence=0,outlookController=null;
    let outlookData=null,outlookLatest=null,outlookReceivedAt=0,outlookFollow=true;
    const detailView={style:{display:'block'}},outlookRefreshButton={disabled:false},outlookNotice={textContent:''};
    let applied=[],unavailable=[];
    function applyOutlook(data){outlookData=data;applied.push(data.mark);}
    function outlookUnavailable(message,state){outlookData=null;outlookLatest=null;unavailable.push(state);}
    function outlookTime(value){return String(value);}
    ${html.slice(start, end)}`, context);
  return { deferred, run: source => vm.runInContext(source, context),
    state: () => vm.runInContext('({data:outlookData,latest:outlookLatest,applied:[...applied],unavailable:[...unavailable]})', context) };
}
function response(mark, overrides = {}) {
  return { ok: true, json: async () => ({ status: 'ok', symbol: 'TEST', period: 1800,
    updated: Date.now()/1000, mark, analysis: {}, bars: [{ open: 1, high: 2, low: 0, close: 1 }],
    freshness: { status: 'latest_available', checked_at: Date.now()/1000 }, ...overrides }) };
}

test('slow polling is single-flight and does not abort its own valid request', async () => {
  const h = outlookHarness(), first = h.run('loadOutlook()');
  await h.run('loadOutlook()');
  assert.equal(h.deferred.length, 1);
  const request = h.deferred.shift();
  assert.equal(request.options.signal.aborted, false);
  request.resolve(response('slow'));
  await first;
  assert.equal(h.state().data.mark, 'slow');
});

test('an older response cannot overwrite an explicit refresh', async () => {
  const h = outlookHarness(), first = h.run('loadOutlook()'), older = h.deferred.shift();
  const forced = h.run('loadOutlook(true)'), newer = h.deferred.shift();
  assert.equal(older.options.signal.aborted, true);
  assert(newer.url.includes('refresh=1'));
  newer.resolve(response('new')); await forced;
  older.resolve(response('old')); await first;
  assert.equal(h.state().data.mark, 'new');
  assert(!h.state().applied.includes('old'));
});

test('paused charts receive new snapshots without moving; manual refresh updates exactly once', async () => {
  const h = outlookHarness();
  let pending = h.run('loadOutlook()'); h.deferred.shift().resolve(response('visible')); await pending;
  h.run('outlookFollow=false');
  pending = h.run('loadOutlook(true)'); h.deferred.shift().resolve(response('background')); await pending;
  assert.equal(h.state().data.mark, 'visible');
  assert.equal(h.state().latest.mark, 'background');
  pending = h.run('loadOutlook(true,true)'); h.deferred.shift().resolve(response('manual')); await pending;
  assert.equal(h.state().data.mark, 'manual');
  assert.equal(h.run('outlookFollow'), false);
});

test('disconnection removes drawn paths even while following is paused', async () => {
  const h = outlookHarness();
  let pending = h.run('loadOutlook()'); h.deferred.shift().resolve(response('visible')); await pending;
  h.run('outlookFollow=false');
  pending = h.run('loadOutlook()');
  h.deferred.shift().resolve(response('lost', { freshness: { status: 'disconnected', label: 'lost' } }));
  await pending;
  assert.equal(h.state().data, null);
  assert.equal(h.state().latest, null);
});

test('a response from the previous symbol is ignored after switching focus', async () => {
  const h = outlookHarness(), pending = h.run('loadOutlook()'), request = h.deferred.shift();
  h.run("currentSymbol='OTHER'");
  request.resolve(response('previous')); await pending;
  assert.equal(h.state().data, null);
  assert.equal(h.state().applied.length, 0);
});
