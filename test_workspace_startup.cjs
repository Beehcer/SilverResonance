'use strict';

// Exercise the complete shell with deterministic network responses and clocks.
// No real browser storage, remote requests or shared plans are touched.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Model = require('./static/workspace-dual-model.js');
const WorkspaceHistoryCache = require('./static/workspace-history-cache.js');
const root = __dirname;
const source = () => fs.readFileSync(path.join(root, 'static/workspace-dual-shell.js'), 'utf8');
const template = () => fs.readFileSync(path.join(root, 'templates/workspace-dual.html'), 'utf8');

function node(tag = 'div') {
  const listeners = new Map(), classes = new Set();
  return {
    tagName: tag.toUpperCase(), children: [], dataset: {}, style: {}, attributes: {},
    hidden: false, inert: false, textContent: '', value: '', parentNode: null,
    classList: {
      add(...values) { values.forEach(value => classes.add(value)); },
      remove(...values) { values.forEach(value => classes.delete(value)); },
      contains(value) { return classes.has(value); },
      toggle(value, force) { const on = force ?? !classes.has(value); on ? classes.add(value) : classes.delete(value); return on; }
    },
    setAttribute(key, value) { this.attributes[key] = String(value); },
    getAttribute(key) { return this.attributes[key] ?? null; },
    removeAttribute(key) { delete this.attributes[key]; },
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); },
    dispatch(type, event = {}) { for (const callback of listeners.get(type) || []) callback({target: this, preventDefault() {}, ...event}); },
    append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } },
    appendChild(child) { this.append(child); return child; },
    replaceChildren(...children) { this.children = []; this.textContent = ''; this.append(...children); },
    remove() { this.removed = true; if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this); },
    getBoundingClientRect: () => ({left: 0, top: 0, right: 1400, bottom: 1000, width: 1400, height: 1000}),
    focus() {},
    querySelector(selector) { return this.children.find(child => selector === child.tagName.toLowerCase() || selector === '#' + child.id) || null; },
    querySelectorAll(selector) { return this.children.filter(child => selector === child.tagName.toLowerCase()); }
  };
}

function harness({runShell = true, html = template(), href = 'https://example.test/workspace'} = {}) {
  let now = 1000, nextTimer = 0;
  const nodes = new Map(), timers = new Map(), listeners = new Map();
  const requests = [], frames = [], errors = [], storage = new Map(), urls = [];
  for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const item = node(match[1]); item.id = match[3]; item.hidden = /\bhidden\b/.test(match[2]); nodes.set(item.id, item);
  }
  const document = {
    body: node('body'), documentElement: node('html'), readyState: 'complete',
    getElementById: id => nodes.get(id) || null,
    querySelector: selector => selector.startsWith('#') ? nodes.get(selector.slice(1)) || null : null,
    createElement(tag) {
      const item = node(tag);
      if (tag === 'iframe') {
        item.contentWindow = {focus() {}, document: {readyState: 'loading'}};
        frames.push(item);
      }
      return item;
    },
    createTextNode: text => ({textContent: text}),
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); }
  };
  const setTimer = (callback, delay = 0, interval = 0) => {
    const id = ++nextTimer; timers.set(id, {callback, at: now + Number(delay), interval}); return id;
  };
  const location = {origin: 'https://example.test', href, search: new URL(href).search,
    reload() { urls.push('reload'); }, replace(url) { urls.push(url); }};
  const window = {document, location, innerWidth: 1400, innerHeight: 1000, DualWorkspaceModel: Model,
    matchMedia: () => ({matches: false}),
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); }
  };
  window.parent = window;
  const context = vm.createContext({window, document, location, WorkspaceHistoryCache,
    navigator: {userAgent: 'Desktop', platform: 'Win32', maxTouchPoints: 0, onLine: true},
    Date: class extends Date { static now() { return now; } }, performance: {now: () => now},
    URL, URLSearchParams, AbortController, Promise, Map, Set,
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    history: {replaceState: (_state, _title, url) => urls.push(url)},
    console: {error: error => errors.push(error), warn() {}, log() {}},
    setTimeout: (callback, delay) => setTimer(callback, delay), clearTimeout: id => timers.delete(id),
    setInterval: (callback, delay) => setTimer(callback, delay, delay), clearInterval: id => timers.delete(id),
    requestAnimationFrame: callback => setTimer(callback, 16), cancelAnimationFrame: id => timers.delete(id),
    fetch: (url, options = {}) => new Promise((resolve, reject) => {
      const request = {url, options, resolve(data, status = 200) { resolve({ok: status >= 200 && status < 300, status, json: async () => data}); }, reject};
      requests.push(request);
      options.signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
    })
  });
  Object.assign(window, {setTimeout: context.setTimeout, clearTimeout: context.clearTimeout,
    setInterval: context.setInterval, clearInterval: context.clearInterval});
  function emit(type, event = {}) { for (const callback of listeners.get(type) || []) callback(event); }
  async function flush() { for (let count = 0; count < 12; count++) await Promise.resolve(); }
  async function advance(ms) {
    const end = now + ms; let cycles = 0;
    for (;;) {
      const next = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      assert.ok(++cycles < 10000, 'Unexpected timer loop');
      const [id, timer] = next; now = timer.at;
      if (timer.interval) timer.at += timer.interval; else timers.delete(id);
      timer.callback(); await flush();
    }
    now = end; await flush();
  }
  function runtime(frame, snapshot = {}) {
    const value = {symbol: new URL(frame.src, location.origin).searchParams.get('symbol'), ready: false, showable: false, loading: true, ui: {dockOpen: true, dockWidth: 204}, ...snapshot};
    frame.contentWindow.FullWorkspace = {
      snapshot: () => value, geometry: () => ({slots: {left: 200, top: 80, width: 996, height: 900}}),
      setMode(next) { value.mode = {...next}; }, applyUI() {}, canChangeFocus: () => true, canHideSidebar: () => true,
      prepareFocusSwitch: () => true, prepareDeactivate: async () => true,
      switchSymbol: async symbol => { value.symbol = symbol; return true; }
    };
    return value;
  }
  function message(frame, type = 'state', detail = {}) {
    emit('message', {origin: location.origin, source: frame.contentWindow,
      data: {type: 'full-workspace:' + type, paneId: frame.dataset.paneId,
        snapshot: frame.contentWindow.FullWorkspace?.snapshot(), ...detail}});
  }
  function runInline() {
    for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
      if (!/\bsrc=/.test(match[1]) && match[2].trim()) vm.runInContext(match[2], context, {filename: 'workspace-dual-inline.js'});
    }
  }
  if (runShell) {
    runInline();
    const code = source().replace(/\n  start\(\);\s*\n\}\(\)\);\s*$/, '\n  window.__shellTest = {transition, frames, getState:()=>state};\n  start();\n}());');
    assert.match(code, /window\.__shellTest/);
    vm.runInContext(code, context, {filename: 'workspace-dual-shell.js'});
  }
  return {nodes, requests, frames, errors, storage, urls, context, window, timers, flush, advance, runtime, message, emit, runInline};
}

async function connect(h) {
  assert.equal(h.requests[0]?.url, '/api/workspace/session');
  h.requests[0].resolve({user: {id: 'owner'}}); await h.flush();
  assert.equal(h.requests[1]?.url, '/api/workspace/symbols');
  h.requests[1].resolve({symbols: [{symbol: 'MA', name: '甲醇'}, {symbol: 'SP', name: '纸浆'}, {symbol: 'AU', name: '黄金'}]}); await h.flush();
  assert.equal(h.frames.length, 1);
  return h.frames[0];
}

function startupText(h) {
  return [...h.nodes].filter(([id]) => id === 'startup' || id.startsWith('startup-'))
    .map(([, item]) => item.textContent + ' ' + item.children.map(child => child.textContent || '').join(' ')).join(' ');
}

test('slow startup configuration reports its stage and elapsed wait before any iframe opens', async () => {
  const h = harness();
  await h.advance(2300);
  assert.equal(h.frames.length, 0);
  assert.equal(h.nodes.get('startup').hidden, false);
  assert.match(startupText(h), /连接|身份|会话/);
  assert.match(startupText(h), /[12]\s*秒/);
  h.requests[0].resolve({user: {id: 'owner'}}); await h.flush();
  assert.equal(h.requests[1]?.url, '/api/workspace/symbols');
  assert.match(startupText(h), /品种/);
});

test('a usable runtime reveals the initial workspace before full market readiness', async () => {
  const h = harness(), frame = await connect(h);
  const value = h.runtime(frame, {showable: true, ready: false});
  h.message(frame); await h.advance(1100);
  assert.equal(h.nodes.get('startup').hidden, true);
  assert.equal(value.ready, false, 'Showing the workspace must not claim that its market is complete');
  assert.equal(h.window.__shellTest.frames.get('pane-a').ready, false);
  assert.equal(frame.hidden, false);
});

test('showable alone cannot commit a staged second pane or remove the original pane', async () => {
  const h = harness(), original = await connect(h);
  h.runtime(original, {showable: true, ready: true}); h.message(original, 'ready'); await h.advance(50);
  const shell = h.window.__shellTest;
  const transition = shell.transition(Model.dropSymbol(shell.getState(), 'SP', 'pane-a', ['MA', 'SP']));
  await h.flush();
  assert.equal(h.frames.length, 2);
  const second = h.frames[1], value = h.runtime(second, {showable: true, ready: false});
  h.message(second); await h.advance(1200);
  assert.equal(shell.getState().panes.length, 1);
  assert.equal(second.hidden, true);
  assert.equal(original.hidden, false);
  assert.notEqual(original.removed, true);
  assert.equal(shell.frames.get('pane-b').ready, false);
  value.ready = true; h.message(second, 'ready'); await transition; await h.advance(20);
  assert.equal(shell.getState().panes.length, 2);
  assert.equal(second.hidden, false);
  assert.equal(original.hidden, false);
});

test('a late usable runtime clears startup timeout and stays visible after old timers fire', async () => {
  const h = harness(), frame = await connect(h);
  await h.advance(60000);
  assert.equal(h.nodes.get('startup').hidden, false);
  assert.match(startupText(h), /重试|刷新|较慢|超时/);
  h.runtime(frame, {showable: true, ready: false}); h.message(frame); await h.advance(1200);
  assert.equal(h.nodes.get('startup').hidden, true);
  await h.advance(60000);
  assert.equal(h.nodes.get('startup').hidden, true, 'An old watchdog must not cover a recovered workspace');
});

test('template watchdog offers recovery when external shell scripts never execute', async () => {
  const h = harness({runShell: false, href: 'https://example.test/workspace?symbol=MA&board=shared&pane=pane-a&count=2'});
  h.runInline(); await h.advance(60000);
  assert.equal(h.requests.length, 0);
  assert.equal(h.frames.length, 0);
  assert.equal(h.nodes.get('startup').hidden, false);
  assert.match(startupText(h), /脚本|资源|页面|组件/);
  assert.match(startupText(h), /重试|刷新/);
  assert.equal(h.nodes.get('startup-actions').hidden, false);
  h.nodes.get('startup-retry').dispatch('click');
  assert.deepEqual(h.urls, ['reload']);
  const fallback = new URL(h.nodes.get('startup-fallback').href);
  assert.equal(fallback.searchParams.get('view'), 'single');
  assert.equal(fallback.searchParams.get('symbol'), 'MA');
  assert.equal(fallback.searchParams.get('board'), 'shared');
  assert.equal(fallback.searchParams.has('pane'), false);
  assert.equal(fallback.searchParams.has('count'), false);
});

async function openSingle(h) {
  const frame = await connect(h), value = h.runtime(frame, {showable: true, ready: true, chartState: {zoom: 3, drawing: 'keep'}});
  h.message(frame, 'ready'); await h.advance(30);
  return {frame, value};
}

function dragTo(h, frame, symbol, y) {
  h.message(frame, 'drag-start', {symbol, method: 'pointer', clientX: 500, clientY: y});
}

function releaseAt(h, frame, y, cancelled = false) {
  h.message(frame, 'drag-end', {clientX: 500, clientY: y, cancelled});
}

for (const [placement, y, guideTop, order] of [
  ['top', 200, '80px', ['SP', 'MA']], ['bottom', 800, '533px', ['MA', 'SP']]
]) test(`dropping in the ${placement} half previews and commits that position without rebuilding the first pane`, async () => {
  const h = harness(), {frame: original, value: originalState} = await openSingle(h), originalSrc = original.src;
  dragTo(h, original, 'SP', y);
  const guide = h.nodes.get('drop-guide');
  assert.equal(guide.hidden, false);
  assert.match(guide.textContent, placement === 'top' ? /上方/ : /下方/);
  assert.equal(guide.style.top, guideTop);
  assert.equal(guide.style.height, '447px');
  releaseAt(h, original, y); await h.flush();
  const shell = h.window.__shellTest, second = h.frames[1];
  assert.equal(shell.getState().panes.length, 1, 'Order changes only after the new pane becomes ready');
  assert.equal(second.hidden, true); assert.equal(original.hidden, false);
  assert.equal(new URL(second.src, 'https://example.test').searchParams.get('slot'), placement);
  const value = h.runtime(second, {showable: true, ready: true}); h.message(second, 'ready'); await h.advance(30);
  assert.deepEqual(Array.from(shell.getState().panes, pane => pane.symbol), order);
  assert.equal(shell.getState().activeId, 'pane-b');
  assert.equal(shell.frames.get('pane-a').frame, original);
  assert.equal(original.src, originalSrc); assert.notEqual(original.removed, true);
  assert.deepEqual(originalState.chartState, {zoom: 3, drawing: 'keep'});
  assert.equal(originalState.mode.slot, placement === 'top' ? 'bottom' : 'top');
  assert.equal(value.mode.slot, placement);
  const saved = JSON.parse(h.storage.get('resonance-workspace-dual-layout-v1:owner'));
  assert.deepEqual(saved.panes.map(pane => pane.symbol), order);
  assert.equal(saved.activeId, 'pane-b');
});

test('release location decides above or below even if the pointer last previewed the other half', async () => {
  const h = harness(), {frame} = await openSingle(h);
  dragTo(h, frame, 'SP', 800); assert.match(h.nodes.get('drop-guide').textContent, /下方/);
  releaseAt(h, frame, 200); await h.flush();
  const second = h.frames[1];
  assert.equal(new URL(second.src, 'https://example.test').searchParams.get('slot'), 'top');
  h.runtime(second, {showable: true, ready: true}); h.message(second, 'ready'); await h.advance(30);
  assert.deepEqual(Array.from(h.window.__shellTest.getState().panes, pane => pane.symbol), ['SP', 'MA']);
});

test('failed or cancelled top placement leaves the original pane, data and saved order intact', async () => {
  const h = harness(), {frame, value} = await openSingle(h), before = JSON.stringify(h.window.__shellTest.getState());
  const saved = h.storage.get('resonance-workspace-dual-layout-v1:owner');
  dragTo(h, frame, 'SP', 200); releaseAt(h, frame, 200, true); await h.flush();
  assert.equal(h.frames.length, 1);
  dragTo(h, frame, 'SP', 200); releaseAt(h, frame, 200); await h.flush();
  const failed = h.frames[1]; await h.advance(20001);
  assert.equal(JSON.stringify(h.window.__shellTest.getState()), before);
  assert.equal(h.storage.get('resonance-workspace-dual-layout-v1:owner'), saved);
  assert.equal(h.window.__shellTest.frames.size, 1);
  assert.equal(h.window.__shellTest.frames.get('pane-a').frame, frame);
  assert.equal(failed.removed, true); assert.equal(frame.hidden, false);
  assert.deepEqual(value.chartState, {zoom: 3, drawing: 'keep'});
});

test('after adding above, an open symbol only focuses and a third symbol replaces exactly the dropped pane', async () => {
  const h = harness(), {frame: original} = await openSingle(h);
  dragTo(h, original, 'SP', 200); releaseAt(h, original, 200); await h.flush();
  const second = h.frames[1]; h.runtime(second, {showable: true, ready: true}); h.message(second, 'ready'); await h.advance(30);
  const shell = h.window.__shellTest;
  dragTo(h, second, 'MA', 200); assert.match(h.nodes.get('drop-guide').textContent, /切换到/);
  releaseAt(h, second, 200); await h.advance(30);
  assert.deepEqual(Array.from(shell.getState().panes, pane => pane.symbol), ['SP', 'MA']);
  assert.equal(shell.getState().activeId, 'pane-a'); assert.equal(h.frames.length, 2);
  dragTo(h, original, 'AU', 200); assert.match(h.nodes.get('drop-guide').textContent, /将此窗口切换/);
  releaseAt(h, original, 200); await h.advance(30);
  assert.deepEqual(Array.from(shell.getState().panes, pane => pane.symbol), ['AU', 'MA']);
  assert.equal(shell.getState().activeId, 'pane-b'); assert.equal(h.frames.length, 2);
  assert.equal(shell.frames.get('pane-a').frame, original);
  assert.equal(shell.frames.get('pane-b').frame, second);
});
