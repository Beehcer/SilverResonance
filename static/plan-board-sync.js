(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PlanBoardSync = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const clone = value => JSON.parse(JSON.stringify(value));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  function diff(base, next) {
    const baseObjects = new Map((base.objects || []).map(item => [item.id, item]));
    const nextObjects = new Map((next.objects || []).map(item => [item.id, item]));
    const payload = {base_revision: base.revision || 0, objects: [], delete_ids: [], base_objects: {}};
    for (const [id, item] of nextObjects) {
      if (!same(baseObjects.get(id), item)) {
        payload.objects.push(clone(item));
        if (baseObjects.has(id)) payload.base_objects[id] = clone(baseObjects.get(id));
      }
    }
    for (const [id, item] of baseObjects) {
      if (!nextObjects.has(id)) {
        payload.delete_ids.push(id);
        payload.base_objects[id] = clone(item);
      }
    }
    for (const field of ['title', 'notes']) {
      if (base[field] !== next[field]) {
        payload[field] = next[field];
        payload['base_' + field] = base[field];
      }
    }
    return payload;
  }

  function hasOperations(payload) {
    return Boolean(payload.objects.length || payload.delete_ids.length ||
      Object.prototype.hasOwnProperty.call(payload, 'title') || Object.prototype.hasOwnProperty.call(payload, 'notes'));
  }

  function applyOperations(plan, payload) {
    const result = clone(plan);
    const objects = new Map(result.objects.map(item => [item.id, item]));
    for (const id of payload.delete_ids) objects.delete(id);
    for (const item of payload.objects) objects.set(item.id, clone(item));
    result.objects = [...objects.values()];
    for (const field of ['title', 'notes']) {
      if (Object.prototype.hasOwnProperty.call(payload, field)) result[field] = payload[field];
    }
    return result;
  }

  return {diff, hasOperations, applyOperations};
}));
