'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Model = require('./model.js');
const symbols = ['CZCE.MA701', 'GFEX.lc2612', 'DCE.p2701'];
const single = () => ({panes: [{id: 'pane-a', symbol: symbols[0]}], activeId: 'pane-a'});
const dual = () => ({panes: [
  {id: 'pane-a', symbol: symbols[0]},
  {id: 'pane-b', symbol: symbols[1]}
], activeId: 'pane-a'});
function freeze(value) {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

test('first visit and unusable saved layouts start with exactly the first available product', () => {
  for (const raw of [null, undefined, false, [], {}, {panes: 'wrong'}, {panes: []},
    {panes: [{id: 'not-a-pane', symbol: symbols[1]}], activeId: 'not-a-pane'}]) {
    assert.deepEqual(Model.normalize(raw, symbols), single());
  }
  assert.deepEqual(Model.normalize(null, []), {panes: [], activeId: null});
  assert.deepEqual(Model.normalize(null, symbols.map(symbol => ({symbol}))), single());
});

test('restoring removes unknown products, duplicate products and duplicate slots without inventing panes', () => {
  const raw = {panes: [null, {id: 'pane-a', symbol: 'SHFE.expired'},
    {id: 'pane-b', symbol: symbols[1], extra: 'ignored'},
    {id: 'pane-a', symbol: symbols[1]}, {id: 'pane-b', symbol: symbols[2]},
    {id: 'pane-a', symbol: symbols[0]}, {id: 'pane-c', symbol: symbols[2]}], activeId: 'pane-b'};
  assert.deepEqual(Model.normalize(raw, symbols), {
    panes: [{id: 'pane-b', symbol: symbols[1]}, {id: 'pane-a', symbol: symbols[0]}], activeId: 'pane-b'
  });
  assert.equal(Model.normalize({...raw, activeId: 'wrong'}, symbols).activeId, 'pane-b');
});

test('clicking a product switches only the active pane; clicking an open product focuses it', () => {
  const before = dual();
  const selected = Model.selectSymbol(before, symbols[2], symbols);
  assert.deepEqual(selected.panes, [{id: 'pane-a', symbol: symbols[2]}, before.panes[1]]);
  const focused = Model.selectSymbol(before, symbols[1], symbols);
  assert.deepEqual(focused.panes, before.panes);
  assert.equal(focused.activeId, 'pane-b');
  assert.deepEqual(Model.selectSymbol(before, 'unknown', symbols), before);
});

test('single-pane drop adds the second pane below and activates it without replacing the first', () => {
  const next = Model.dropSymbol(single(), symbols[1], 'pane-a', symbols);
  assert.deepEqual(next, {...dual(), activeId: 'pane-b'});
  const afterClose = {panes: [{id: 'pane-b', symbol: symbols[1]}], activeId: 'pane-b'};
  assert.deepEqual(Model.dropSymbol(afterClose, symbols[2], 'pane-b', symbols), {
    panes: [...afterClose.panes, {id: 'pane-a', symbol: symbols[2]}], activeId: 'pane-a'
  });
});

test('two-pane drop replaces only the target and never creates a third pane', () => {
  const before = dual(), next = Model.dropSymbol(before, symbols[2], 'pane-b', symbols);
  assert.deepEqual(next, {
    panes: [before.panes[0], {id: 'pane-b', symbol: symbols[2]}], activeId: 'pane-b'
  });
  assert.deepEqual(Model.dropSymbol(before, symbols[0], 'pane-b', symbols), before);
  assert.deepEqual(Model.dropSymbol(before, symbols[1], 'pane-a', symbols), {...before, activeId: 'pane-b'});
});

test('outside and invalid drops cannot add, replace or focus even an already-open product', () => {
  for (const state of [single(), dual()]) {
    for (const target of [null, undefined, '', 'pane-c']) {
      for (const symbol of symbols) assert.deepEqual(Model.dropSymbol(state, symbol, target, symbols), state);
    }
    assert.deepEqual(Model.dropSymbol(state, 'unknown', 'pane-a', symbols), state);
  }
  assert.deepEqual(Model.dropSymbol(single(), symbols[1], 'pane-b', symbols), single());
});

test('close keeps the remaining identity, chooses a valid active pane, and cannot remove the final pane', () => {
  const before = dual();
  assert.deepEqual(Model.closePane(before, 'pane-a'), {panes: [before.panes[1]], activeId: 'pane-b'});
  assert.deepEqual(Model.closePane(before, 'pane-b'), single());
  assert.deepEqual(Model.closePane({...before, activeId: 'pane-b'}, 'pane-a'), {
    panes: [before.panes[1]], activeId: 'pane-b'
  });
  assert.deepEqual(Model.closePane(single(), 'pane-a'), single());
  assert.deepEqual(Model.closePane(before, 'not-a-pane'), before);
});

test('only explicit valid activation changes the active pane', () => {
  assert.deepEqual(Model.activate(dual(), 'pane-b'), {...dual(), activeId: 'pane-b'});
  assert.deepEqual(Model.activate(dual(), null), dual());
  assert.deepEqual(Model.activate(single(), 'pane-b'), single());
});

test('all transformations leave frozen source layouts and the catalog untouched', () => {
  const state = freeze(dual()), catalog = freeze([...symbols]);
  for (const result of [Model.normalize(state, catalog), Model.activate(state, 'pane-b'),
    Model.selectSymbol(state, symbols[2], catalog), Model.dropSymbol(state, symbols[2], 'pane-a', catalog),
    Model.closePane(state, 'pane-b')]) {
    assert.notEqual(result, state);
    for (const pane of result.panes) assert.ok(!state.panes.includes(pane));
  }
  assert.deepEqual(state, dual());
  assert.deepEqual(catalog, symbols);
});

test('late market responses must match pane, symbol, request generation and returned symbol', () => {
  const ticket = {id: 'pane-a', symbol: symbols[0], request: 4};
  assert.equal(Model.sameTicket({...ticket}, ticket, symbols[0]), true);
  for (const current of [null, {...ticket, id: 'pane-b'}, {...ticket, symbol: symbols[1]},
    {...ticket, request: 5}, {...ticket, request: undefined}, {...ticket, request: '4'}]) {
    assert.equal(Model.sameTicket(current, ticket, symbols[0]), false);
  }
  assert.equal(Model.sameTicket(ticket, ticket, symbols[1]), false);
  assert.equal(Model.sameTicket(ticket, null, symbols[0]), false);
  for (const request of [undefined, null, NaN, -1, Infinity, '4']) {
    const malformed = {...ticket, request};
    assert.equal(Model.sameTicket(malformed, malformed, symbols[0]), false);
  }
  const closed = {id: 'pane-a', symbol: null, request: 4};
  assert.equal(Model.sameTicket(closed, ticket, symbols[0]), false);
});
