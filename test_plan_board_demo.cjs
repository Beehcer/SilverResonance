'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('templates/plan_board_demo.html', 'utf8');
const script = fs.readFileSync('static/plan-board-demo.js', 'utf8');
const app = fs.readFileSync('app.py', 'utf8');

test('demo has an isolated route and explicit simulated shared-service messaging', () => {
  assert.match(app, /@app\.route\("\/board-demo"\)/);
  assert.match(app, /render_template\("plan_board_demo\.html"\)/);
  assert.match(html, /不连接真实行情/);
  assert.match(html, /plan-board-model\.js/);
  assert.match(html, /plan-board-sync\.js/);
  assert.match(html, /plan-board-demo\.js/);
  assert.doesNotMatch(html, /structure-board\.js|market-structure-chart\.js/);
});

test('demo exposes drawing tools and independent symbol switching', () => {
  for (const tool of ['select', 'pan', 'horizontal', 'trend', 'reference', 'path', 'text']) {
    assert.match(html, new RegExp('data-tool="' + tool + '"'));
  }
  for (const id of ['undo', 'redo', 'snap', 'follow', 'reset-view', 'object-list', 'object-color', 'object-note', 'plan-notes', 'export-plan', 'symbol-select']) {
    assert.match(html, new RegExp('id="' + id + '"'));
  }
  assert.match(script, /function switchSymbol\(symbol\)/);
  assert.match(script, /'★ '/);
});

test('market data stays simulated while plans use local backup and shared APIs', () => {
  assert.match(script, /createBars\(96, currentSymbol, period\)/);
  assert.match(script, /setInterval\(updateMarket, 1000\)/);
  assert.match(script, /localStorage\.setItem\(storageKey/);
  assert.match(script, /new EventSource\('\/api\/board-demo\/events\/'/);
  assert.doesNotMatch(script, /\/api\/outlook|\/api\/kline|TqSdk|tqsdk/);
});

test('demo keeps real-time updates separate from committed drawing history', () => {
  assert.match(script, /function updateMarket\(\)/);
  assert.match(script, /function commitDocument\(next\)/);
  assert.match(script, /new Model\.PlanHistory\(doc\)/);
  assert.match(script, /timeForX|xForTime/);
  assert.match(script, /priceForY|yForPrice/);
});

test('mock random seed is initialized before each chart series', () => {
  assert.ok(script.indexOf('seed = symbolConfig(currentSymbol).seed') < script.indexOf('bars: createBars(96, currentSymbol, period)'));
});

test('snapping uses candle centers and horizontal handles retain their own time anchor', () => {
  assert.match(script, /nearestBar\.bar\.dt \+ INTERVAL \/ 2/);
  assert.match(script, /time: anchor\.time, price: anchor\.price/);
  assert.match(script, /object\.points\[0\]\.time === null/);
  assert.doesNotMatch(script, /current\.kind === 'horizontal' \? \{time: null/);
});

test('selection does not grow the workspace and only the inspector scrolls', () => {
  const css = fs.readFileSync('static/plan-board-demo.css', 'utf8');
  assert.match(css, /height: 100dvh;/);
  assert.match(css, /\.board-layout \{[\s\S]*?flex: 1;/);
  assert.match(css, /\.inspector \{ min-height: 0; overflow-y: auto;/);
  assert.match(css, /\.chart-section \{ min-width: 0; min-height: 0;/);
});

test('infinite rays, text anchors, path numbering and context menu stay explicit', () => {
  assert.match(script, /'data-infinite': 'both'/);
  assert.match(script, /class: 'text-anchor-dot'/);
  assert.match(script, /class: 'path-number'/);
  assert.match(script, /String\(index \+ 1\)/);
  assert.match(script, /'dominant-baseline': 'middle'/);
  assert.match(script, /layer\.addEventListener\('contextmenu'/);
  assert.match(script, /openContextMenu\(hit\.object, event\)/);
  assert.match(script, /\$\('context-delete-all'\)/);
});

test('three independent charts have loading states and clipped K-line rendering', () => {
  for (const period of ['day', 'm3', 'm30']) {
    assert.match(html, new RegExp('data-period="' + period + '"'));
    assert.match(html, new RegExp('id="chart-loading-' + period + '"'));
    assert.match(html, new RegExp('id="drawing-layer-' + period + '"'));
  }
  assert.match(script, /ctx\.clip\(\)/);
  assert.match(script, /object\.period === chartPeriod/);
  assert.match(script, /\$\('symbol-select'\)\.disabled = true/);
  assert.match(script, /if \(switching \|\|/);
  assert.match(script, /function resetAllViews\(\)/);
  assert.match(script, /\$\('reset-view'\)\.addEventListener\('click', resetAllViews\)/);
  assert.doesNotMatch(script, /frameObjectInAllPeriods/);
  assert.doesNotMatch(script, /pointermove', event => \{ activateChart/);
});

test('path editing, ESC preservation and global V follow the upgraded interaction contract', () => {
  assert.match(script, /function isDoublePathClick\(object, pos\)/);
  assert.match(script, /function adjustPathPoints\(delta\)/);
  assert.match(script, /points\.splice\(index \+ 1, 0, point\)/);
  assert.match(script, /points\.splice\(index, 1\)/);
  assert.match(script, /event\.stopImmediatePropagation\(\)/);
  assert.match(script, /if \(pathDraft\)/);
  assert.match(script, /pathDraft\.points = \[anchor\]/);
  assert.match(script, /else \{\s*pathDraft\.points\.push\(anchor\);/);
  assert.match(script, /context-delete-all/);
});

test('text placement, zoom range and future shading are configurable', () => {
  assert.match(html, /id="object-text-side"/);
  assert.match(html, /id="zoom-range" type="range" min="50" max="400"/);
  assert.match(script, /object\.text_side === 'left'/);
  assert.match(script, /const scale = 100 \/ Number\(input\.value\)/);
  assert.match(script, /rgba\(108, 117, 125, 0\.055\)/);
});

test('trend and reference lines can map to every period', () => {
  assert.match(html, /id="object-all-periods"/);
  assert.match(html, /id="map-all-periods" type="checkbox" checked/);
  assert.match(script, /function creationPeriod\(kind\)/);
  assert.match(script, /period: creationPeriod\(tool\)/);
  assert.match(script, /period: event\.target\.checked \? 'all'/);
  assert.match(script, /object\.period === chartPeriod \|\| object\.period === 'all'/);
});
