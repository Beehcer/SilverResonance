'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Model = require('./static/plan-board-model.js');

const points = [{time: 30000, price: 7200}, {time: 60000, price: 7260}];

test('drawing tools use red, green and yellow presets without fixing semantic direction', () => {
  assert.equal(Model.TOOL_DEFAULTS.horizontal.color, Model.COLORS.yellow);
  assert.equal(Model.TOOL_DEFAULTS.reference.color, Model.COLORS.yellow);
  assert.equal(Model.TOOL_DEFAULTS.reference.line_style, 'dashed');
  assert.equal(Model.TOOL_DEFAULTS.trend.color, Model.COLORS.yellow);
  assert.equal(Model.COLORS.green, '#48be8b');
});

test('objects persist time and price anchors rather than pixels', () => {
  const object = Model.createObject('trend', points, {name: '测试趋势线'});
  assert.deepEqual(object.points, points);
  assert.equal('x' in object.points[0], false);
  assert.equal('y' in object.points[0], false);
  assert.equal(Model.normalizeObject(Model.clone(object)).id, object.id);
});

test('horizontal lines support legacy and positioned handles and reject incomplete paths', () => {
  const legacy = Model.createObject('horizontal', [{time: null, price: 7200}]);
  const positioned = Model.createObject('horizontal', [{time: 45000, price: 7200}]);
  assert.deepEqual(legacy.points, [{time: null, price: 7200}]);
  assert.deepEqual(positioned.points, [{time: 45000, price: 7200}]);
  assert.equal(Model.createObject('path', [points[0]]), null);
});

test('document edits are immutable and increment the revision once per operation', () => {
  const initial = Model.createDocument();
  const object = Model.createObject('trend', points);
  const added = Model.addObject(initial, object);
  assert.equal(initial.objects.length, 0);
  assert.equal(added.revision, 1);
  const changed = Model.updateObject(added, object.id, {color: Model.COLORS.green, width: 3});
  assert.equal(changed.revision, 2);
  assert.equal(added.objects[0].color, Model.COLORS.yellow);
  assert.equal(changed.objects[0].color, Model.COLORS.green);
  assert.equal(changed.objects[0].width, 3);
});

test('translation and anchor movement retain object identity and valid coordinates', () => {
  const object = Model.createObject('trend', points);
  const moved = Model.translateObject(object, 30000, 20);
  assert.deepEqual(moved.points, [{time: 60000, price: 7220}, {time: 90000, price: 7280}]);
  const anchored = Model.moveAnchor(moved, 1, {time: 120000, price: 7300});
  assert.deepEqual(anchored.points[1], {time: 120000, price: 7300});
  assert.equal(anchored.id, object.id);
  assert.equal(Model.translateObject(object, 0, -100000), null);
});

test('duplicates receive a new id and remain independently editable', () => {
  const object = Model.createObject('reference', points, {name: '参考'});
  const copy = Model.duplicateObject(object, 30000, 5);
  assert.notEqual(copy.id, object.id);
  assert.equal(copy.kind, 'reference');
  assert.match(copy.name, /副本/);
  assert.deepEqual(copy.points[0], {time: 60000, price: 7205});
});

test('history supports undo, redo and clears redo after a divergent edit', () => {
  const initial = Model.createDocument();
  const history = new Model.PlanHistory(initial);
  const object = Model.createObject('horizontal', [{time: null, price: 7200}]);
  history.commit(Model.addObject(initial, object));
  assert.equal(history.present.objects.length, 1);
  assert.equal(history.canUndo(), true);
  assert.equal(history.undo().objects.length, 0);
  assert.equal(history.canRedo(), true);
  assert.equal(history.redo().objects.length, 1);
  history.undo();
  history.commit(Model.addObject(history.present, Model.createObject('horizontal', [{time: null, price: 7300}])));
  assert.equal(history.canRedo(), false);
  assert.equal(history.present.objects[0].points[0].price, 7300);
});

test('normalization filters invalid objects and constrains style and text', () => {
  const document = Model.normalizeDocument({title: '  计划  ', revision: -2, objects: [
    {kind: 'trend', points, color: 'red;stroke:url(x)', width: 99, line_style: 'unknown'},
    {kind: 'unknown', points},
    {kind: 'path', points: [points[0]]}
  ]});
  assert.equal(document.title, '计划');
  assert.equal(document.revision, 0);
  assert.equal(document.objects.length, 1);
  assert.equal(document.objects[0].color, Model.COLORS.red);
  assert.equal(document.objects[0].width, 6);
  assert.equal(document.objects[0].line_style, 'solid');
});

test('remove only increments revision when the selected object exists', () => {
  const object = Model.createObject('text', [points[0]], {note: '等待确认'});
  const document = Model.addObject(Model.createDocument(), object);
  assert.equal(Model.removeObject(document, 'missing').revision, document.revision);
  assert.equal(Model.removeObject(document, object.id).objects.length, 0);
});

test('segment distance supports selection hit testing', () => {
  const a = {x: 0, y: 0}, b = {x: 100, y: 100};
  assert.equal(Model.distanceToSegment({x: 50, y: 50}, a, b), 0);
  assert.ok(Math.abs(Model.distanceToSegment({x: 0, y: 100}, a, b) - Math.sqrt(5000)) < 1e-9);
});

test('text names retain multiline content beyond eighty characters and never borrow note',()=>{
  const content='  开盘计划\r\n\r\n'+'等待回抽'.repeat(35)+'\r受阻再入场\n';
  const object=Model.createObject('text',[points[0]],{name:content,note:'独立备注'});
  assert.equal(object.name,content.replace(/\r\n?/g,'\n'));assert.equal(object.note,'独立备注');assert.equal(Model.normalizeObject(object).name,object.name);
  assert.equal(Model.createObject('text',[points[0]],{note:'不是文本内容'}).name,'');
  assert.equal(Model.createObject('text',[points[0]],{name:'字'.repeat(2000)}).name.length,2000);
  assert.equal(Array.from(Model.createObject('text',[points[0]],{name:'😀'.repeat(2000)}).name).length,2000);
  assert.equal(Model.createObject('text',[points[0]],{name:'字'.repeat(2001)}).name.length,2000);
  assert.equal(Model.createObject('horizontal',[points[0]],{name:'一\r\n二\r三'}).name,'一 二 三');
  assert.equal(Model.createObject('horizontal',[points[0]],{name:'字'.repeat(100)}).name.length,80);
});
