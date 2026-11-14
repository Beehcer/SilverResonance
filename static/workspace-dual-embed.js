(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const paneId = params.get('pane') === 'pane-b' ? 'pane-b' : 'pane-a';
  const initialSymbol = params.get('symbol');
  let runtime = null, mode = {active: params.get('active') === 'true', slot: params.get('slot') === 'bottom' ? 'bottom' : 'top', count: params.get('count') === '2' ? 2 : 1, maximized: false};
  let stateTimer = null, geometryFrame = null, lastState = '', lastGeometry = '', readySent = false;
  let touchDrag = null, suppressClickUntil = 0, popupFrame = null, popupSource = null;
  let fullscreenSidebars = {leftOpen: true, rightOpen: true};
  const popupRegions = new WeakMap();
  const main = document.querySelector('.market-workspace');
  const surface = document.createElement('div');
  surface.id = 'dual-pane-surface';
  while (main.firstChild) surface.append(main.firstChild);
  main.append(surface);
  const brand = document.querySelector('.terminal-brand');
  if (brand) brand.target = '_top';
  const close = document.createElement('button');
  close.type = 'button'; close.className = 'dual-close'; close.textContent = '×';
  close.title = '关闭此品种窗口'; close.setAttribute('aria-label', close.title); close.hidden = true;
  close.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); emit('close'); });
  surface.querySelector('.market-strip-actions').append(close);
  const maximize = document.createElement('button');
  maximize.type = 'button'; maximize.className = 'dual-maximize';
  maximize.setAttribute('aria-controls', surface.id);
  maximize.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); emit(mode.maximized ? 'restore' : 'maximize'); });
  surface.querySelector('.market-strip-actions').prepend(maximize);
  document.documentElement.classList.add('dual-embedded');

  function emit(name, detail = {}) {
    if (window.parent !== window) window.parent.postMessage({type: 'full-workspace:' + name, paneId, ...detail}, location.origin);
  }
  function rect(node) {
    const r = node.getBoundingClientRect();
    return {x: r.x, y: r.y, left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height};
  }
  function geometry() {
    const slots = rect(main);
    if (mode.active && mode.maximized) {
      const edge = Math.min(18, slots.width / 2);
      slots.x += edge; slots.left += edge; slots.right -= edge; slots.width -= edge * 2;
    }
    return {slots, pane: rect(surface)};
  }
  function popupRegion(node) {
    const requested = popupRegions.get(node);
    const dock = document.getElementById('workspace-dock');
    return requested === 'dock' && dock && !dock.hidden && mode.active ? dock : surface;
  }
  function popupArea(node) {
    const bounds = popupRegion(node).getBoundingClientRect(), gap = 6;
    const left = Math.max(gap, bounds.left + gap), top = Math.max(gap, bounds.top + gap);
    const right = Math.min(innerWidth - gap, bounds.right - gap), bottom = Math.min(innerHeight - gap, bounds.bottom - gap);
    return {left, top, right, bottom, width: Math.max(1, right - left), height: Math.max(1, bottom - top)};
  }
  function positionPopups() {
    const nodes = document.querySelectorAll('dialog[open],#members-panel:not([hidden]),#layout-picker[open] .layout-menu,#decision-detail:not([hidden]),#object-context-menu:not([hidden])');
    for (const node of nodes) {
      if (!node.getClientRects().length) continue;
      // The profile belongs to the visible header, not the active chart slot.
      if (node.id === 'members-panel') { window.WorkspaceMembersPopover?.position(); continue; }
      const isCases = node.id === 'cases-dialog' || node.classList.contains('cases-chart-zoom'), isDialog = node.tagName === 'DIALOG';
      const area = isCases ? {left: 0, top: 0, width: innerWidth, height: innerHeight} : popupArea(node);
      const isLayout = node.classList.contains('layout-menu'), isDecision = node.id === 'decision-detail';
      node.classList.add('dual-scoped-popup');
      Object.assign(node.style, {position: 'fixed', right: 'auto', bottom: 'auto', margin: '0', minWidth: '0', minHeight: '0', maxWidth: area.width + 'px', maxHeight: area.height + 'px'});
      if (isCases) {
        Object.assign(node.style, {left: area.left + 'px', top: area.top + 'px', width: area.width + 'px', height: area.height + 'px'});
        continue;
      }
      if (isDialog) node.style.width = Math.min(390, area.width) + 'px';
      else if (isLayout) {
        node.style.width = Math.min(440, area.width) + 'px';
        node.style.gridTemplateColumns = area.width < 400 ? 'repeat(2,minmax(0,1fr))' : 'repeat(4,minmax(0,1fr))';
      } else if (isDecision) node.style.width = area.width + 'px';
      node.style.overflowY = 'auto';
      let bounds = node.getBoundingClientRect(), x = bounds.left, y = bounds.top;
      if (isDialog) { x = area.left + (area.width - bounds.width) / 2; y = area.top + (area.height - bounds.height) / 2; }
      else if (isLayout) { const anchor = document.querySelector('#layout-picker > summary').getBoundingClientRect(); x = anchor.right - bounds.width; y = anchor.bottom + 4; }
      else if (isDecision) { const anchor = document.getElementById('decision-toggle').getBoundingClientRect(); x = area.left; y = anchor.bottom + 4; }
      node.style.left = Math.max(area.left, Math.min(x, area.right - bounds.width)) + 'px';
      node.style.top = Math.max(area.top, Math.min(y, area.bottom - bounds.height)) + 'px';
    }
    queueState();
  }
  function queuePopups() {
    if (popupFrame !== null) return;
    popupFrame = requestAnimationFrame(() => { popupFrame = null; positionPopups(); });
  }
  function renderMaximizeButton() {
    const label = mode.maximized ? '退出全屏（Esc）' : '工作区全屏';
    maximize.title = label;
    maximize.setAttribute('aria-label', label);
    maximize.setAttribute('aria-pressed', String(mode.maximized));
    maximize.innerHTML = '<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false"><path d="' + (mode.maximized ? 'M7 3h10v10M3 7h10v10H3z' : 'M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4') + '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  }
  function applyUI(ui) {
    if (!ui) return;
    runtime?.applyUI(ui);
    queueState();
  }
  function applyFullscreenSidebars(sidebars) {
    if (!mode.maximized || !sidebars) return false;
    fullscreenSidebars = {leftOpen: Boolean(sidebars.leftOpen), rightOpen: Boolean(sidebars.rightOpen)};
    document.documentElement.classList.toggle('dual-left-collapsed', !fullscreenSidebars.leftOpen);
    const dock = document.getElementById('workspace-dock');
    if (dock) dock.hidden = !fullscreenSidebars.rightOpen;
    const label = document.getElementById('layers-toggle-label');
    if (label) label.textContent = fullscreenSidebars.rightOpen ? '收起图层' : '展开图层';
    document.getElementById('objects-toggle')?.setAttribute('aria-expanded', String(fullscreenSidebars.rightOpen));
    queueState(); queueGeometry(); queuePopups();
    return true;
  }
  function focusMaximizeButton() {
    // Keep text/composition and other inner interactions in charge of focus.
    // Even a harmless resize must not cause title-editor blur to save a draft.
    if (runtime?.escapeHasPriority?.() ?? true) return false;
    maximize.focus({preventScroll: true});
    return true;
  }
  function snapshot() {
    const value = runtime?.snapshot() || {symbol: initialSymbol, name: initialSymbol, board: '', loading: true, ready: false};
    const members = document.getElementById('members-panel');
    const membersPopup = mode.active && members && !members.hidden && members.getClientRects().length ? rect(members) : null;
    return {...value, membersPopup, pageDialogOpen: Boolean(document.getElementById('cases-dialog')?.open), paneId, active: mode.active, slot: mode.slot, count: mode.count, maximized: mode.maximized, sidebars: mode.maximized ? {...fullscreenSidebars} : null};
  }
  function queueState() {
    if (stateTimer !== null) return;
    stateTimer = setTimeout(() => {
      stateTimer = null;
      if (!runtime) return;
      const state = snapshot(), serialized = JSON.stringify(state);
      const title = document.querySelector('.workspace-title');
      const titleText = state.name || state.symbol ? '正在编辑：' + (state.name || state.symbol) : '多周期工作台';
      if (title && title.textContent !== titleText) title.textContent = titleText;
      if (state.ready && !readySent) { readySent = true; emit('ready', {snapshot: state, geometry: geometry()}); }
      if (serialized !== lastState) { lastState = serialized; emit('state', {snapshot: state}); }
    }, 40);
  }
  function queueGeometry() {
    if (geometryFrame !== null) return;
    geometryFrame = requestAnimationFrame(() => {
      geometryFrame = null;
      const width = surface.getBoundingClientRect().width;
      surface.classList.toggle('dual-compact-heading', width < 640);
      surface.classList.toggle('dual-tight-heading', width < 460);
      const value = geometry(), serialized = JSON.stringify(value);
      positionPopups();
      if (serialized !== lastGeometry) { lastGeometry = serialized; emit('geometry', {geometry: value}); }
    });
  }
  function setMode(next) {
    const previous = mode.active, wasMaximized = mode.maximized;
    mode = {active: Boolean(next.active), slot: next.slot === 'bottom' ? 'bottom' : 'top', count: next.count === 2 ? 2 : 1, maximized: Boolean(next.maximized)};
    if (!mode.active || mode.maximized) window.WorkspaceMembersPopover?.close();
    document.documentElement.classList.toggle('dual-active', mode.active);
    document.documentElement.classList.toggle('dual-passive', !mode.active);
    document.documentElement.classList.toggle('dual-maximized', mode.maximized);
    document.documentElement.dataset.dualSlot = mode.slot;
    document.documentElement.dataset.dualCount = String(mode.count);
    close.hidden = mode.count < 2;
    if (next.ui) applyUI(next.ui);
    if (mode.maximized && next.sidebars) applyFullscreenSidebars(next.sidebars);
    else if (!mode.maximized) document.documentElement.classList.toggle('dual-left-collapsed', false);
    if (previous !== mode.active) mode.active ? runtime?.becameActive() : runtime?.becamePassive();
    if (wasMaximized !== mode.maximized || !maximize.firstChild) renderMaximizeButton();
    queueState(); queueGeometry(); queuePopups();
  }
  window.FullWorkspace = {
    snapshot, geometry, queueState, setMode, applyFullscreenSidebars,
    closeMembers: options => window.WorkspaceMembersPopover?.close(options),
    profileSaved: (user, fields) => emit('profile-saved', {user: {id: user.id, name: user.name, color: user.color}, fields}),
    profileChanged: user => window.WorkspaceProfile?.receive(user),
    isActive: () => mode.active,
    isMaximized: () => mode.maximized,
    canChangeFocus: options => runtime?.canChangeFocus?.(options) ?? false,
    canHideSidebar: (side, options) => runtime?.canHideSidebar?.(side, options) ?? false,
    prepareFocusSwitch: () => runtime?.prepareFocusSwitch?.() ?? false,
    escapeHasPriority: () => runtime?.escapeHasPriority?.() ?? true,
    focusMaximizeButton,
    register(value) { runtime = value; queueState(); queueGeometry(); },
    requestSymbol(symbol) { emit('select-symbol', {symbol}); },
    requestDockVisible(open) { emit('dock-toggle', {open: Boolean(open)}); },
    async switchSymbol(symbol, board = null) { if (!runtime) return false; const result = await runtime.switchSymbol(symbol, board); queueState(); return result; },
    async prepareDeactivate(options = {}) { if (!runtime) return true; const result = await runtime.prepareDeactivate(options); queueState(); return result; },
    applyUI
  };

  document.addEventListener('pointerdown', event => {
    popupSource = event.target;
    if (event.target.closest('.dual-maximize')) {
      // Resize without blurring an in-chart text editor (blur can save a title).
      event.preventDefault(); return;
    }
    if (event.target.closest('.dual-close')) { event.preventDefault(); return; }
    if (!mode.active) {
      event.preventDefault(); event.stopImmediatePropagation(); emit('outside-members'); emit('activate'); return;
    }
    const button = event.target.closest('.quote-row');
    if (button && event.button === 0 && !runtime?.snapshot().viewOnly) {
      touchDrag = {node: button, symbol: button.dataset.symbol, id: event.pointerId, x: event.clientX, y: event.clientY, started: false, draggable: button.draggable};
      button.draggable = false;
    }
  }, true);
  document.addEventListener('wheel', event => {
    if (!mode.active) { event.preventDefault(); event.stopImmediatePropagation(); emit('activate'); }
  }, {capture: true, passive: false});
  document.addEventListener('keydown', event => {
    popupSource = event.target;
    if (event.target.closest('.dual-maximize') && ['Enter', ' '].includes(event.key)) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (!event.repeat) emit(mode.maximized ? 'restore' : 'maximize');
      return;
    }
    if (!mode.active) {
      if (event.key === 'Escape' && mode.maximized && event.target.closest('.dual-maximize') && !event.repeat && !event.isComposing && event.keyCode !== 229 && runtime?.escapeHasPriority && !runtime.escapeHasPriority()) emit('restore');
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (event.key === 'Escape') {
      if (event.repeat || event.isComposing || event.keyCode === 229) return;
      const consume = () => { event.preventDefault(); event.stopImmediatePropagation(); };
      if (window.WorkspaceMembersPopover?.isOpen()) { window.WorkspaceMembersPopover.close({restoreFocus: true}); consume(); return; }
      // A page-wide case dialog owns Escape even outside workspace fullscreen.
      // Keep native cancellation enabled; underlying chart shortcuts stay idle.
      if (document.getElementById('cases-dialog')?.open) { event.stopImmediatePropagation(); return; }
      if (mode.maximized) {
        // Let the browser dispatch the dialog's normal cancel/close events,
        // without letting the chart's earlier Escape listener cancel a draft.
        if (document.querySelector('dialog[open]')) { event.stopImmediatePropagation(); return; }
        const contextMenu = document.getElementById('object-context-menu');
        if (contextMenu && !contextMenu.hidden) return;
        const layout = document.getElementById('layout-picker');
        if (layout?.open) { layout.open = false; consume(); return; }
        const decision = document.getElementById('decision-detail');
        if (decision && !decision.hidden) {
          decision.hidden = true;
          document.getElementById('decision-toggle')?.setAttribute('aria-expanded', 'false');
          consume(); return;
        }
      }
      if (touchDrag) { endTouchDrag(event, true); consume(); return; }
      if (mode.maximized && runtime?.escapeHasPriority && !runtime.escapeHasPriority()) { consume(); emit('restore'); }
    }
  }, true);
  document.addEventListener('click', event => {
    if (performance.now() < suppressClickUntil && event.target.closest('.quote-row')) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('dragstart', event => {
    if (event.target.closest('.quote-row')) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('pointermove', event => {
    if (!touchDrag || touchDrag.id !== event.pointerId) return;
    if (!touchDrag.started && Math.hypot(event.clientX - touchDrag.x, event.clientY - touchDrag.y) < 8) return;
    if (!touchDrag.started) {
      if (!runtime?.beginSidebarDrag(touchDrag.symbol)) { endTouchDrag(event, true); return; }
      touchDrag.started = true;
      try { touchDrag.node.setPointerCapture(event.pointerId); } catch { /* The parent overlay can take over. */ }
      emit('drag-start', {symbol: touchDrag.symbol, method: 'pointer', clientX: event.clientX, clientY: event.clientY});
    }
    event.preventDefault(); emit('drag-move', {symbol: touchDrag.symbol, method: 'pointer', clientX: event.clientX, clientY: event.clientY});
  }, {passive: false});
  function endTouchDrag(event, cancelled = false) {
    if (!touchDrag) return;
    const gesture = touchDrag; touchDrag = null;
    gesture.node.draggable = gesture.draggable;
    if (!gesture.started) return;
    suppressClickUntil = performance.now() + 400;
    try { gesture.node.releasePointerCapture(gesture.id); } catch { /* Pointer capture may already be released. */ }
    const sidebarHandled = !cancelled && runtime?.sidebarDrop(gesture.symbol, event.clientX, event.clientY);
    runtime?.endSidebarDrag();
    emit('drag-end', {symbol: gesture.symbol, method: 'pointer', clientX: event.clientX, clientY: event.clientY, cancelled: cancelled || Boolean(sidebarHandled), sidebarHandled: Boolean(sidebarHandled)});
  }
  document.addEventListener('pointerup', event => { if (touchDrag?.id === event.pointerId) endTouchDrag(event); });
  document.addEventListener('pointercancel', event => { if (touchDrag?.id === event.pointerId) endTouchDrag(event, true); });
  window.addEventListener('blur', event => { if (touchDrag) endTouchDrag(event, true); });
  window.addEventListener('storage', event => { runtime?.syncStorage(event.key); queueState(); });
  window.addEventListener('resize', queueGeometry);
  const dialogObserver = new MutationObserver(records => {
    let changed = false;
    for (const record of records) {
      const node = record.target;
      // Chart SVGs change on hover. Only popup content/open-state changes need
      // remeasurement; style/class writes by this adapter are not observed.
      if (record.type === 'childList' && !node.closest?.('dialog,#members-panel,#layout-picker,#decision-detail,#object-context-menu') && ![...record.addedNodes].some(added => added.matches?.('dialog,#members-panel'))) continue;
      changed = true;
      if (node.matches?.('dialog[open],#members-panel:not([hidden]),#object-context-menu:not([hidden])')) {
        const fromDock = popupSource?.closest?.('#workspace-dock') || popupSource?.closest?.('#object-context-menu') && popupRegions.get(document.getElementById('object-context-menu')) === 'dock';
        popupRegions.set(node, node.id !== 'cases-dialog' && node.id !== 'members-panel' && fromDock ? 'dock' : 'pane');
      }
    }
    if (changed) { queueState(); queuePopups(); }
  });
  dialogObserver.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'hidden']});
  document.addEventListener('contextmenu', event => { popupSource = event.target; queuePopups(); }, true);
  new ResizeObserver(queueGeometry).observe(main);
  new ResizeObserver(queueGeometry).observe(surface);
  setMode(mode);
}());
