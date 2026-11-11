(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PlanBoardModel = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const COLORS = Object.freeze({
    red: '#ef6268', green: '#48be8b', yellow: '#e7c460', blue: '#63a9ef',
    purple: '#bc9af2', white: '#e6edf3', gray: '#8b949e'
  });
  const LABELS = Object.freeze({horizontal: '水平线', trend: '趋势线', reference: '参考线', path: '计划路径', text: '文本注记'});
  const KINDS = Object.freeze(Object.keys(LABELS));
  const TOOL_DEFAULTS = Object.freeze({
    horizontal: {color: COLORS.yellow, line_style: 'solid'},
    trend: {color: COLORS.yellow, line_style: 'solid'},
    reference: {color: COLORS.yellow, line_style: 'dashed'},
    path: {color: COLORS.red, line_style: 'solid'},
    text: {color: COLORS.white, line_style: 'solid'}
  });
  const clone = value => JSON.parse(JSON.stringify(value));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const positive = value => finite(value) && value > 0;
  const text = (value, limit) => typeof value === 'string' ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').trim().slice(0, limit) : '';
  const objectName = (kind, value) => {
    if (typeof value !== 'string') return '';
    const normalized = value.replace(/\r\n?/g, '\n');
    return kind === 'text'
      ? Array.from(normalized.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')).slice(0, 2000).join('')
      : text(normalized.replace(/\n/g, ' '), 80);
  };
  const validColor = value => typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value.toLowerCase() : COLORS.red;
  let sequence = 0;

  function makeId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    sequence += 1;
    return 'plan-' + Date.now().toString(36) + '-' + sequence.toString(36) + '-' + Math.random().toString(36).slice(2, 9);
  }

  function point(raw, allowNullTime = false) {
    if (!raw || !positive(raw.price)) return null;
    const time = allowNullTime && raw.time === null ? null : Number(raw.time);
    if (time !== null && (!finite(time) || time < 0)) return null;
    return {time, price: Number(raw.price)};
  }

  function normalizePoints(kind, raw) {
    if (!Array.isArray(raw)) return null;
    const result = raw.map(item => point(item, kind === 'horizontal'));
    if (result.some(item => !item)) return null;
    if (kind === 'horizontal') return result.length === 1 ? result : null;
    if (kind === 'text') return result.length === 1 && result[0].time !== null ? result : null;
    if ((kind === 'trend' || kind === 'reference') && result.length === 2) return result;
    if (kind === 'path' && result.length >= 2) return result;
    return null;
  }

  function normalizeObject(raw) {
    if (!raw || !KINDS.includes(raw.kind)) return null;
    const points = normalizePoints(raw.kind, raw.points);
    if (!points) return null;
    return {
      id: text(raw.id, 100) || makeId(), kind: raw.kind, points,
      name: objectName(raw.kind, raw.name), color: validColor(raw.color),
      width: Math.max(1, Math.min(6, Math.round(finite(raw.width) ? raw.width : 2))),
      line_style: raw.line_style === 'dashed' ? 'dashed' : 'solid',
      extend: raw.extend === true, visible: raw.visible !== false,
      period: ['day', 'm3', 'm30', 'all'].includes(raw.period) ? raw.period : 'm30',
      text_side: raw.text_side === 'left' ? 'left' : 'right',
      note: text(raw.note, 500), z_index: finite(raw.z_index) ? Math.round(raw.z_index) : 0
    };
  }

  function createObject(kind, points, options = {}) {
    const defaults = TOOL_DEFAULTS[kind] || TOOL_DEFAULTS.trend;
    return normalizeObject({...options, ...defaults, ...options, id: options.id || makeId(), kind, points});
  }

  function createDocument(options = {}) {
    return normalizeDocument({
      schema_version: 1, id: options.id || 'demo-current-plan', title: options.title || '模拟计划画板',
      symbol: options.symbol || 'DEMO.ag', period: options.period || 30,
      notes: options.notes || '', revision: 0, objects: []
    });
  }

  function normalizeDocument(raw) {
    const source = raw && typeof raw === 'object' ? raw : {};
    const objects = Array.isArray(source.objects) ? source.objects.map(normalizeObject).filter(Boolean) : [];
    return {
      schema_version: 1, id: text(source.id, 100) || 'demo-current-plan',
      title: text(source.title, 100) || '未命名计划', symbol: text(source.symbol, 80) || 'DEMO.ag',
      period: positive(source.period) ? source.period : 30, notes: text(source.notes, 3000),
      revision: Number.isInteger(source.revision) && source.revision >= 0 ? source.revision : 0,
      objects: objects.sort((a, b) => a.z_index - b.z_index)
    };
  }

  function nextDocument(current, objects, patch = {}) {
    const base = normalizeDocument(current);
    return normalizeDocument({...base, ...patch, revision: base.revision + 1, objects});
  }

  function addObject(current, object) {
    const normalized = normalizeObject(object);
    if (!normalized) throw new Error('无效的画板对象');
    return nextDocument(current, [...normalizeDocument(current).objects, normalized]);
  }

  function updateObject(current, id, patch) {
    const base = normalizeDocument(current);
    let found = false;
    const objects = base.objects.map(item => {
      if (item.id !== id) return item;
      found = true;
      return normalizeObject({...item, ...patch, id: item.id, kind: item.kind});
    });
    if (!found || objects.some(item => !item)) throw new Error('画板对象不存在或修改无效');
    return nextDocument(base, objects);
  }

  function removeObject(current, id) {
    const base = normalizeDocument(current);
    const objects = base.objects.filter(item => item.id !== id);
    return objects.length === base.objects.length ? base : nextDocument(base, objects);
  }

  function translateObject(raw, deltaTime, deltaPrice) {
    const object = normalizeObject(raw);
    if (!object || !finite(deltaTime) || !finite(deltaPrice)) return null;
    return normalizeObject({...object, points: object.points.map(item => ({
      time: item.time === null ? null : item.time + deltaTime, price: item.price + deltaPrice
    }))});
  }

  function moveAnchor(raw, index, anchor) {
    const object = normalizeObject(raw);
    if (!object || !Number.isInteger(index) || index < 0 || index >= object.points.length) return null;
    const points = clone(object.points);
    const next = point(anchor, object.kind === 'horizontal');
    if (!next) return null;
    points[index] = next;
    return normalizeObject({...object, points});
  }

  function duplicateObject(raw, deltaTime = 0, deltaPrice = 0) {
    const object = translateObject(raw, deltaTime, deltaPrice);
    return object ? normalizeObject({...object, id: makeId(), name: (object.name || LABELS[object.kind]) + ' 副本'}) : null;
  }

  function objectLabel(object) {
    return object && object.name || LABELS[object && object.kind] || '画板对象';
  }

  function distanceToSegment(pointValue, start, end) {
    const dx = end.x - start.x, dy = end.y - start.y;
    const length = dx * dx + dy * dy;
    const t = length ? Math.max(0, Math.min(1, ((pointValue.x - start.x) * dx + (pointValue.y - start.y) * dy) / length)) : 0;
    return Math.hypot(pointValue.x - start.x - t * dx, pointValue.y - start.y - t * dy);
  }

  class PlanHistory {
    constructor(initial, limit = 80) {
      this.limit = limit;
      this.past = [];
      this.present = normalizeDocument(initial);
      this.future = [];
    }
    commit(next) {
      const normalized = normalizeDocument(next);
      if (JSON.stringify({...normalized, revision: 0}) === JSON.stringify({...this.present, revision: 0})) return this.present;
      this.past.push(this.present);
      if (this.past.length > this.limit) this.past.shift();
      this.present = normalized;
      this.future = [];
      return this.present;
    }
    undo() {
      if (!this.past.length) return this.present;
      this.future.push(this.present);
      this.present = this.past.pop();
      return this.present;
    }
    redo() {
      if (!this.future.length) return this.present;
      this.past.push(this.present);
      this.present = this.future.pop();
      return this.present;
    }
    canUndo() { return this.past.length > 0; }
    canRedo() { return this.future.length > 0; }
  }

  return {COLORS, LABELS, KINDS, TOOL_DEFAULTS, clone, makeId, normalizeObject, normalizeDocument,
    createObject, createDocument, addObject, updateObject, removeObject, translateObject,
    moveAnchor, duplicateObject, objectLabel, distanceToSegment, PlanHistory};
}));
