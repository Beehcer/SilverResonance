(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DualWorkspaceModel = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const PANE_IDS = Object.freeze(['pane-a', 'pane-b']);
  const symbolOf = value => typeof value === 'string' ? value : value?.symbol;
  const validSymbol = value => typeof value === 'string' && value.length > 0;

  function knownSymbols(values) {
    return [...new Set((Array.isArray(values) ? values : [])
      .map(symbolOf).filter(validSymbol))];
  }

  // Only presentation state is stored here. Plans and viewports belong to the
  // symbol-specific controller, so replacing/closing a pane cannot delete them.
  function normalize(raw, symbolIds) {
    const symbols = knownSymbols(symbolIds), known = new Set(symbols);
    const seenIds = new Set(), seenSymbols = new Set(), panes = [];
    for (const pane of Array.isArray(raw?.panes) ? raw.panes : []) {
      if (!pane || !PANE_IDS.includes(pane.id) || !known.has(pane.symbol)
          || seenIds.has(pane.id) || seenSymbols.has(pane.symbol)) continue;
      panes.push({id: pane.id, symbol: pane.symbol});
      seenIds.add(pane.id);
      seenSymbols.add(pane.symbol);
      if (panes.length === PANE_IDS.length) break;
    }
    if (!panes.length && symbols.length) panes.push({id: PANE_IDS[0], symbol: symbols[0]});
    const activeId = panes.some(pane => pane.id === raw?.activeId)
      ? raw.activeId : panes[0]?.id ?? null;
    return {panes, activeId};
  }

  function copyCurrent(state) {
    const symbols = (Array.isArray(state?.panes) ? state.panes : [])
      .filter(pane => pane && PANE_IDS.includes(pane.id)).map(pane => pane.symbol);
    return normalize(state, symbols);
  }

  function activate(state, id) {
    const next = copyCurrent(state);
    if (next.panes.some(pane => pane.id === id)) next.activeId = id;
    return next;
  }

  function selectSymbol(state, symbol, symbolIds) {
    const known = knownSymbols(symbolIds), next = normalize(state, known);
    if (!known.includes(symbol)) return next;
    const existing = next.panes.find(pane => pane.symbol === symbol);
    if (existing) next.activeId = existing.id;
    else {
      const active = next.panes.find(pane => pane.id === next.activeId);
      if (active) active.symbol = symbol;
    }
    return next;
  }

  // targetId must identify an open pane even in single-pane mode. An outside
  // drop has no target and must neither add a pane nor steal the active pane.
  function dropSymbol(state, symbol, targetId, symbolIds) {
    const known = knownSymbols(symbolIds), next = normalize(state, known);
    if (!known.includes(symbol) || !next.panes.some(pane => pane.id === targetId)) return next;
    const existing = next.panes.find(pane => pane.symbol === symbol);
    if (existing) {
      next.activeId = existing.id;
      return next;
    }
    if (next.panes.length === 1) {
      const id = PANE_IDS.find(candidate => !next.panes.some(pane => pane.id === candidate));
      next.panes.push({id, symbol});
      next.activeId = id;
    } else {
      next.panes.find(pane => pane.id === targetId).symbol = symbol;
      next.activeId = targetId;
    }
    return next;
  }

  function closePane(state, id) {
    const next = copyCurrent(state);
    if (next.panes.length < 2 || !next.panes.some(pane => pane.id === id)) return next;
    next.panes = next.panes.filter(pane => pane.id !== id);
    if (next.activeId === id) next.activeId = next.panes[0].id;
    return next;
  }

  // A pane can be reused for another symbol or a later request. A late response
  // is accepted only by the exact slot + symbol + request that initiated it.
  function sameTicket(current, ticket, responseSymbol) {
    return Boolean(current && ticket
      && PANE_IDS.includes(current.id) && current.id === ticket.id
      && validSymbol(current.symbol) && current.symbol === ticket.symbol
      && responseSymbol === ticket.symbol
      && Number.isSafeInteger(current.request) && current.request >= 0
      && current.request === ticket.request);
  }

  return Object.freeze({PANE_IDS, normalize, activate, selectSymbol, dropSymbol, closePane, sameTicket});
}));
