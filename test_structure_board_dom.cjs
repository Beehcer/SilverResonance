/* Offline DOM-contract smoke tests. Run: node test_structure_board_dom.cjs
 * This small DOM models the APIs used by the board, including destructive
 * textContent assignment and select options. It does not test browser layout.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {};
    this.attributes = {}; this.className = ''; this._text = ''; this._value = undefined;
    this.disabled = false; this.valid = true;
    this.classList = {
      add: name => { this.className = [this.className, name].filter(Boolean).join(' '); },
      toggle: (name, on) => {
        const values = new Set(this.className.split(/\s+/).filter(Boolean));
        if (on) values.add(name); else values.delete(name);
        this.className = [...values].join(' ');
      }
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set value(value) { this._value = String(value); }
  get value() {
    if (this.tagName !== 'select') return this._value === undefined ? '' : this._value;
    if (this._value === undefined) return this.children[0]?.value || '';
    return this.children.some(child => child.tagName === 'option' && child.value === this._value) ? this._value : '';
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = String(value); if (key === 'class') this.className = String(value); }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatch(type) { for (const callback of this.listeners[type] || []) callback({preventDefault() {}}); }
  reportValidity() { return this.valid; }
}
const NOW = Date.parse('2026-10-19T10:00:05+08:00');
const key = (symbol = 'TEST.X', id = 'S1') => 'silver-structure-note:v1:' + encodeURIComponent(symbol) + ':' + encodeURIComponent(id);
const copy = value => JSON.parse(JSON.stringify(value));
function fixture() {
  return {symbol: 'TEST.X', period: 1800, structure_id: 'S1', phase: 'up_hold',
    range: {low: 100, high: 110, kind: 'reference'}, buffer: 1, atr: 2,
    realtime: true, updated: NOW / 1000, market_updated: NOW / 1000,
    completed: [{dt: '2026-10-19 09:00:00', open: 111, high: 113, low: 110, close: 112},
      {dt: '2026-10-19 09:30:00', open: 112, high: 114, low: 111, close: 113}],
    plan: {status: 'ready', mode: 'confirmed', structure_id: 'S1', direction: 'long', entry: 113, stop: 109, target: 130},
    risk: {equity: 50000, max_loss: 1000, remaining_budget: 1000, fee_configured: true,
      max_lots: 1, net_rr: 2, rows: [{lots: 1, loss: 100, allowed: true}]}};
}
function environment(seed = {}) {
  const saved = new Map(Object.entries(seed));
  const storage = {failRead: false, failWrite: false,
    getItem(k) { if (this.failRead) throw Error('blocked'); return saved.get(k) || null; },
    setItem(k, v) { if (this.failWrite) throw Error('quota'); saved.set(k, String(v)); },
    removeItem(k) { if (this.failWrite) throw Error('blocked'); saved.delete(k); }};
  const sandbox = {console, Date: {now: () => NOW, parse: Date.parse}, localStorage: storage,
    document: {createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
      createTextNode: text => { const element = new Element('#text'); element.textContent = text; return element; }}};
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  for (const file of ['structure-model.js', 'structure-board.js'])
    vm.runInContext(fs.readFileSync(path.join(__dirname, 'static', file), 'utf8'), sandbox, {filename: file});
  const host = new Element('section'); sandbox.StructureBoard.mount(host);
  const all = () => { const visit = node => [node, ...node.children.flatMap(visit)]; return visit(host); };
  const field = name => all().find(item => item.name === name);
  const cls = name => all().find(item => item.className.split(/\s+/).includes(name));
  const form = () => all().find(item => item.tagName === 'form');
  const update = context => sandbox.StructureBoard.update(context);
  const edit = (name, value) => { field(name).value = value; form().dispatch('input'); };
  const save = () => form().dispatch('submit');
  return {sandbox, host, saved, storage, field, cls, form, update, edit, save};
}
let count = 0;
function test(name, run) {
  try { run(); console.log('PASS ' + name); count += 1; }
  catch (error) { console.error('FAIL ' + name + ': ' + error.message); process.exitCode = 1; }
}

test('render preserves phase SELECT options and its distinct readout', () => {
  const e = environment(); e.update(fixture());
  assert.ok(e.field('phase').children.length > 1);
  assert.equal(e.field('phase').value, 'unknown');
  assert.match(e.cls('sb-phase').textContent, /回踩守住/);
  e.edit('phase', 'pullback'); e.update(fixture());
  assert.equal(e.field('phase').value, 'pullback');
  assert.ok(e.field('phase').children.every(item => item.tagName === 'option'));
});
test('same identity refresh and temporary disconnect preserve unsaved draft', () => {
  const e = environment(), c = fixture(); e.update(c); e.edit('reason', 'unsaved A'); e.edit('phase', 'pullback');
  e.update(copy(c)); assert.equal(e.field('reason').value, 'unsaved A');
  e.sandbox.StructureBoard.clear('offline', c.symbol);
  assert.equal(e.field('reason').value, 'unsaved A');
  assert.equal(e.cls('sb-actions').children[0].disabled, true);
  e.save(); assert.equal(e.saved.size, 0);
  e.update(c); assert.equal(e.field('reason').value, 'unsaved A'); assert.equal(e.field('phase').value, 'pullback');
});
test('different structure clears draft and never copies the old saved identity', () => {
  const e = environment(), c = fixture(); e.update(c); e.edit('reason', 'saved A'); e.save();
  e.edit('reason', 'unsaved old draft'); e.update({...c, structure_id: 'S2'});
  assert.equal(e.field('reason').value, ''); assert.equal(e.field('stage').value, 'unknown');
  assert.equal(JSON.parse(e.saved.get(key())).reason, 'saved A'); assert.equal(e.saved.has(key('TEST.X', 'S2')), false);
});
test('switching symbols during disconnected state clears the previous symbol draft', () => {
  const e = environment(), c = fixture(); e.update(c); e.edit('reason', 'symbol A draft');
  e.sandbox.StructureBoard.clear('offline A', c.symbol);
  e.sandbox.StructureBoard.clear('loading B', 'TEST.Y');
  assert.equal(e.field('reason').value, '');
  assert.equal(e.cls('sb-actions').children[0].disabled, true);
});
test('form validation and storage failure preserve the uncommitted user input', () => {
  const e = environment(); e.update(fixture()); e.edit('reason', 'keep this');
  e.form().valid = false; e.save(); assert.equal(e.saved.size, 0);
  e.form().valid = true; e.storage.failWrite = true; e.save();
  assert.equal(e.saved.size, 0); assert.equal(e.field('reason').value, 'keep this');
  assert.match(e.cls('sb-message').textContent, /保存失败/);
  e.storage.failWrite = false; e.save();
  const note = JSON.parse(e.saved.get(key()));
  assert.equal(note.reason, 'keep this'); assert.equal(note.reviewed_bar_time, '2026-10-19 09:30:00');
});
test('corrupt or blocked localStorage does not become a manual structure', () => {
  for (const blocked of [false, true]) {
    const e = environment({[key()]: '{'}); e.storage.failRead = blocked; e.update(fixture());
    assert.match(e.cls('sb-warning').textContent, /不可读取/); assert.equal(e.field('stage').value, 'unknown');
    assert.match(e.cls('sb-source').textContent, /程序规则/);
  }
});
test('failed deletion preserves the saved annotation and visible inputs', () => {
  const e = environment(); e.update(fixture()); e.edit('reason', 'saved note'); e.save();
  const before = e.saved.get(key()); e.storage.failWrite = true;
  e.cls('sb-actions').children[1].dispatch('click');
  assert.equal(e.saved.get(key()), before); assert.equal(e.field('reason').value, 'saved note');
  assert.match(e.cls('sb-message').textContent, /清除失败/);
});
test('stale context and newer completed bars do not highlight the old current segment', () => {
  const c = fixture(), note = {symbol: c.symbol, structure_id: 'S1', type: 'up', stage: 'point3', segment: '3-4',
    intent: 'continuation', reviewed_bar_time: '2026-10-19 09:00:00'};
  const e = environment({[key()]: JSON.stringify(note)}); e.update(c);
  assert.match(e.cls('sb-numbering').textContent, /待复核/);
  assert.equal(e.cls('sb-diagram').innerHTML.includes('stroke-width="4"'), false);
  e.update({...c, realtime: false});
  assert.equal(e.cls('sb-diagram').innerHTML.includes('stroke-width="4"'), false);
  assert.match(e.cls('sb-statuses').textContent, /历史快照/);
});
test('manual annotation cannot overwrite server risk and empty breakeven stays undefined', () => {
  const c = fixture(); c.plan.status = 'risk_blocked'; c.risk.max_lots = 0; c.risk.rows[0].allowed = false;
  const before = JSON.stringify(c), e = environment(); e.update(c);
  e.edit('type', 'up'); e.edit('stage', 'point3'); e.edit('intent', 'continuation'); e.save();
  assert.equal(JSON.stringify(c), before);
  assert.match(e.cls('sb-statuses').textContent, /风险未通过/);
  const model = e.sandbox.StructureModel.build(c, JSON.parse(e.saved.get(key())), NOW);
  assert.equal(model.plan.ready, false); assert.equal(model.levels.breakeven.price, null);
  const levels = e.cls('sb-levels').children;
  assert.equal(levels[3].children[1].textContent, '待定义');
});
console.log(count + ' DOM contract tests passed');
