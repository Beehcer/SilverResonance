'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('templates/plan_board_demo.html', 'utf8');
const script = fs.readFileSync('static/plan-board-demo.js', 'utf8');

test('every chart preserves independent horizontal and vertical zoom values', () => {
  assert.match(html, /id="zoom-y-range"/);
  assert.match(html, /id="zoom-y-value"/);
  assert.match(script, /zoomX: 100, zoomY: 100/);
  assert.match(script, /\$\('zoom-range'\)\.value = charts\[period\]\.zoomX/);
  assert.match(script, /\$\('zoom-y-range'\)\.value = charts\[period\]\.zoomY/);
  assert.match(script, /bindZoomSlider\(\$\('zoom-range'\), 'x'\)/);
  assert.match(script, /bindZoomSlider\(\$\('zoom-y-range'\), 'y'\)/);
});

test('mouse wheel scales both time and price around the cursor', () => {
  assert.match(script, /const price = priceForY\(pos\.y\)/);
  assert.match(script, /view\.min = Math\.max\(1, price - \(price - view\.min\) \* scale\)/);
  assert.match(script, /view\.max = price \+ \(view\.max - price\) \* scale/);
});

test('reset uses bars only and daily labels use calendar dates', () => {
  assert.match(script, /fitPrices\(false\);[\s\S]*?charts\[chartPeriod\]\.zoomX = 100/);
  assert.match(script, /chartPeriod === 'day'[\s\S]*?toLocaleDateString/);
});

test('simple selection does not create revisions and pointer cancellation aborts instead of committing', () => {
  assert.match(script, /Math\.hypot\(pos\.x - drag\.start\.x, pos\.y - drag\.start\.y\) <= 3/);
  assert.match(script, /layer\.addEventListener\('pointercancel', abortPointerInteraction\)/);
  assert.match(script, /function abortPointerInteraction\(\)/);
});

test('remote updates preserve history, selection and focused input', () => {
  assert.match(script, /history\.present = Model\.clone\(doc\)/);
  assert.match(script, /const activeInput = document\.activeElement/);
  assert.match(script, /selectionChanged \|\| activeInput !== 'object-name'/);
  assert.match(script, /activeInput !== 'plan-notes'/);
});

test('shared stars and connection recovery use server state', () => {
  assert.match(script, /const sharedPlans = \{\}/);
  assert.match(script, /sharedPlans\[item\.symbol\] \|\|/);
  assert.match(script, /async function refreshSharedStars\(\)/);
  assert.match(script, /eventSource\.onopen/);
  assert.match(script, /function scheduleSharedRetry\(symbol\)/);
  assert.match(script, /setInterval\(refreshSharedStars, 15000\)/);
});
