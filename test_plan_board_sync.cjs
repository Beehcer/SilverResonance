'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Sync = require('./static/plan-board-sync.js');

const object = (id, price = 100) => ({id, kind: 'horizontal', points: [{time: 15000, price}],
  name: '', color: '#ef6268', width: 2, line_style: 'solid', extend: false, visible: true, note: '', z_index: 0});
const plan = (objects, revision = 0) => ({title: '计划', notes: '', revision, objects});

test('diff contains changed objects, deletions and their original versions', () => {
  const before = plan([object('a'), object('b')], 4);
  const after = plan([{...object('a', 110), color: '#48be8b'}], 5);
  after.title = '新计划';
  const payload = Sync.diff(before, after);
  assert.equal(payload.base_revision, 4);
  assert.equal(payload.objects[0].points[0].price, 110);
  assert.deepEqual(payload.delete_ids, ['b']);
  assert.equal(payload.base_objects.a.points[0].price, 100);
  assert.equal(payload.base_objects.b.points[0].price, 100);
  assert.equal(payload.base_title, '计划');
  assert.equal(Sync.hasOperations(payload), true);
});

test('three-way merge keeps server objects while applying outstanding local edits', () => {
  const localAtSend = plan([object('local')], 1);
  const latestLocal = plan([object('local'), object('new-local')], 2);
  const server = plan([object('local'), object('remote')], 3);
  const merged = Sync.applyOperations(server, Sync.diff(localAtSend, latestLocal));
  assert.deepEqual(merged.objects.map(item => item.id).sort(), ['local', 'new-local', 'remote']);
  assert.equal(merged.revision, 3);
});

test('a no-op diff does not produce a server write', () => {
  const value = plan([object('a')], 8);
  const payload = Sync.diff(value, JSON.parse(JSON.stringify(value)));
  assert.equal(Sync.hasOperations(payload), false);
  assert.deepEqual(Sync.applyOperations(value, payload), value);
});
