(function () {
  'use strict';
  const M = window.DualWorkspaceModel, $ = id => document.getElementById(id);
  const KEY = 'resonance-workspace-dual-layout-v1:';
  let layoutKey = KEY;
  const frames = new Map();
  // Public market data outlives individual panes; drafts and shared plans do not.
  window.WorkspaceMarketHub = new WorkspaceHistoryCache.Hub();
  let startupOpened = false;
  let symbols = [], ids = [], state, busy = false, drag = null, animation = 0, toastTimer;
  // A temporary view of this browser page, never part of the saved/shared plan.
  let workspaceMaximized = false;
  let fullscreenSidebars = {leftOpen: false, rightOpen: false}, normalDockUI = null;
  const name = symbol => symbols.find(item => item.symbol === symbol)?.name || symbol;
  const activeFrame = () => frames.get(state?.activeId);
  const bridge = record => { try { return record?.frame.contentWindow.FullWorkspace; } catch { return null; } };
  const announce = text => { $('toast').textContent = text; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3600); };
  function readLayout() { try { return JSON.parse(localStorage.getItem(layoutKey)); } catch { return null; } }
  function persist() { try { localStorage.setItem(layoutKey, JSON.stringify(state)); } catch { announce('浏览器未能保存窗口布局，请留意计划的同步状态。'); } }
  function rectStyle(node, box) {
    for (const key of ['left', 'top', 'width', 'height']) { const value = Math.round(box[key] * 100) / 100 + 'px'; if (node.style[key] !== value) node.style[key] = value; }
  }
  function workspaceRect() {
    const record = activeFrame(), geometry = bridge(record)?.geometry();
    if (!geometry?.slots || geometry.slots.width < 1) return null;
    const outer = record.frame.getBoundingClientRect();
    return {...geometry.slots, left: geometry.slots.left + outer.left, top: geometry.slots.top + outer.top};
  }
  function slotRect(id, count = state.panes.length) {
    const box = workspaceRect(); if (!box) return null;
    if (count === 1) return box;
    const height = (box.height - 6) / 2, index = state.panes.findIndex(pane => pane.id === id);
    return {left: box.left, top: box.top + (index === 1 ? height + 6 : 0), width: box.width, height};
  }
  function renderSidebarControls() {
    const box = workspaceMaximized ? workspaceRect() : null;
    for (const side of ['left', 'right']) {
      const button = $('sidebar-' + side + '-toggle');
      button.hidden = !box; if (!box) continue;
      const open = fullscreenSidebars[side + 'Open'], label = (open ? '收起' : '展开') + (side === 'left' ? '品种大纲' : '计划和图层');
      button.textContent = side === 'left' ? (open ? '‹' : '›') : (open ? '›' : '‹');
      button.title = label; button.setAttribute('aria-label', label); button.setAttribute('aria-expanded', String(open));
      const height = Math.min(64, box.height);
      rectStyle(button, {left: side === 'left' ? box.left - 18 : box.left + box.width, top: box.top + (box.height - height) / 2, width: 18, height});
    }
  }
  function setFullscreenSidebar(side, open) {
    if (!workspaceMaximized || busy || drag || !['left', 'right'].includes(side)) return;
    open = Boolean(open);
    if (!open && fullscreenSidebars[side + 'Open'] && !bridge(activeFrame())?.canHideSidebar(side, {notify: true})) { announce('侧栏中有未完成的编辑，请先完成或取消，再收起。'); return; }
    fullscreenSidebars[side + 'Open'] = open; layout();
  }
  function layout() {
    if (!state) return;
    document.body.classList.toggle('workspace-maximized', workspaceMaximized);
    const stageRect = $('stage').getBoundingClientRect(), selected = activeFrame();
    if (!selected) return;
    selected.frame.hidden = false;
    selected.frame.inert = false; selected.frame.removeAttribute('aria-hidden'); selected.frame.removeAttribute('tabindex');
    selected.frame.className = 'workspace-frame is-active' + (bridge(selected)?.snapshot().pageDialogOpen ? ' has-page-dialog' : ''); selected.frame.style.cssText = '';
    // Read chart-slot geometry only after the selected frame has restored its
    // full workspace chrome. The newly active bottom pane was passive before.
    bridge(selected)?.setMode({active: true, slot: state.panes[0].id === state.activeId ? 'top' : 'bottom', count: state.panes.length, maximized: workspaceMaximized, sidebars: fullscreenSidebars});
    for (const pane of state.panes) {
      const record = frames.get(pane.id); if (!record) continue;
      const isActive = pane.id === state.activeId;
      if (!isActive) {
        bridge(record)?.setMode({active: false, slot: state.panes[0].id === pane.id ? 'top' : 'bottom', count: state.panes.length, maximized: workspaceMaximized, sidebars: fullscreenSidebars});
        // Fullscreen belongs to the entire workspace. Both symbol panes stay
        // visible and interactive, including their independent close buttons.
        record.frame.hidden = false;
        record.frame.inert = false;
        record.frame.removeAttribute('aria-hidden'); record.frame.removeAttribute('tabindex');
        const box = slotRect(pane.id); if (!box) continue;
        record.frame.className = 'workspace-frame is-passive';
        rectStyle(record.frame, {...box, left: box.left - stageRect.left, top: box.top - stageRect.top});
      }
    }
    renderSidebarControls();
    if (drag) showGuide(drag.x, drag.y);
  }
  function scheduleLayout() { if (animation) return; animation = requestAnimationFrame(() => { animation = 0; layout(); }); }
  function maximizeWorkspace(id) {
    if (busy || drag || !state || workspaceMaximized) return;
    if (!frames.get(id)?.ready || !activeFrame()?.ready) { announce('工作区仍在加载，请稍后再全屏。'); return; }
    if (!bridge(activeFrame())?.canHideSidebar('right', {notify: true})) { announce('右侧有未完成的编辑，请先完成或取消，再进入全屏。'); return; }
    const ui = bridge(activeFrame()).snapshot().ui;
    normalDockUI = {dockOpen: ui.dockOpen, dockWidth: ui.dockWidth};
    fullscreenSidebars = {leftOpen: false, rightOpen: false};
    // Pure presentation: do not switch the active symbol, save a draft, change
    // a period layout, or persist the temporary fullscreen sidebar choices.
    workspaceMaximized = true; layout();
  }
  function restoreMaximized() {
    if (busy || drag || !workspaceMaximized) return;
    if (!normalDockUI?.dockOpen && fullscreenSidebars.rightOpen && !bridge(activeFrame())?.canHideSidebar('right', {notify: true})) { announce('右侧有未完成的编辑，请先完成或取消，再还原。'); return; }
    workspaceMaximized = false; layout();
    bridge(activeFrame())?.applyUI(normalDockUI); normalDockUI = null; layout();
  }
  function startupProgress(completed, label, detail) {
    if (startupOpened) return;
    const progress = $('startup-progress');
    if (progress) { progress.value = Math.max(Number(progress.value) || 0, completed); progress.setAttribute('aria-valuetext', progress.value + ' / 4 个打开阶段完成'); }
    if ($('startup-label')) $('startup-label').textContent = label;
    if ($('startup-detail')) $('startup-detail').textContent = detail;
  }
  function markShowable(record) {
    record.showable = true; record.resolveVisible?.();
    if (record !== activeFrame() || startupOpened) return;
    startupProgress(4, '工作台已打开', '行情与共享计划继续在各窗口加载。');
    startupOpened = true; $('startup').hidden = true;
    if (window.workspaceStartupTimer) window.clearInterval(window.workspaceStartupTimer);
    scheduleLayout();
  }
  function makeFrame(pane, {layoutState = state, staged = false} = {}) {
    const frame = document.createElement('iframe'); frame.className = 'workspace-frame is-active'; frame.title = name(pane.symbol) + '完整工作台'; frame.dataset.paneId = pane.id;
    // A new pane must finish connecting before it can cover or resize the
    // current workspace. Loading a hidden iframe still runs its bootstrap.
    if (staged) { frame.hidden = true; frame.inert = true; frame.setAttribute('aria-hidden', 'true'); frame.setAttribute('tabindex', '-1'); }
    const url = new URL('/workspace', location.origin); url.searchParams.set('pane', pane.id); url.searchParams.set('symbol', pane.symbol);
    url.searchParams.set('active', String(!staged && pane.id === layoutState.activeId)); url.searchParams.set('slot', layoutState.panes[0].id === pane.id ? 'top' : 'bottom'); url.searchParams.set('count', String(layoutState.panes.length));
    // An explicit empty board means the shared default plan. Omitting it would
    // incorrectly restore this visitor's previously selected named plan.
    if (typeof pane.board === 'string') url.searchParams.set('board', pane.board);
    frame.src = url.pathname + url.search;
    const record = {frame, symbol: pane.symbol, ready: false, showable: false};
    record.loaded = new Promise(resolve => { record.resolve = resolve; });
    record.visible = new Promise(resolve => { record.resolveVisible = resolve; });
    frame.addEventListener?.('load', () => {
      if (record === activeFrame()) startupProgress(3, '正在初始化图表…', '页面组件已载入，正在恢复窗口与近期行情。');
      if (bridge(record)?.snapshot().showable) markShowable(record);
    });
    frames.set(pane.id, record); $('stage').append(frame); return record;
  }
  async function waitReady(record) {
    if (record.ready) return true;
    let timer; try { return await Promise.race([record.loaded.then(() => true), new Promise(resolve => { timer = setTimeout(() => resolve(false), 20000); })]); } finally { clearTimeout(timer); }
  }
  async function waitVisible(record) {
    if (record.showable) return true;
    let timer; try { return await Promise.race([record.visible.then(() => true), new Promise(resolve => { timer = setTimeout(() => resolve(false), 20000); })]); } finally { clearTimeout(timer); }
  }
  async function transition(next) {
    if (busy || !state || JSON.stringify(next) === JSON.stringify(state)) return;
    busy = true; document.body.classList.add('is-busy');
    const staged = []; let committed = false, loadingMessage = '';
    try {
      const oldActive = activeFrame();
      const affected = state.panes.filter(old => {
        const destination = next.panes.find(pane => pane.id === old.id);
        return !destination || destination.symbol !== old.symbol || old.id === state.activeId && next.activeId !== old.id;
      });
      // Check every affected pane before any save/switch, so closing the other
      // pane cannot silently commit the active pane's unfinished drawing.
      for (const old of affected) {
        if (!bridge(frames.get(old.id))?.canChangeFocus({notify: true})) { announce('请先完成或取消这个品种的当前编辑，原窗口已保留。'); return; }
      }
      for (const pane of next.panes) if (!frames.has(pane.id)) {
        const record = makeFrame(pane, {layoutState: next, staged: true}); staged.push({id: pane.id, record});
        loadingMessage = '正在加载 ' + name(pane.symbol) + '，原窗口可继续查看。'; announce(loadingMessage);
        if (!await waitReady(record)) { announce('新窗口没有及时加载，保留原窗口，可稍后再拖入。'); return; }
      }
      // The original pane stays usable during loading. Do not steal focus if
      // the user has started a drawing, text composition or another draft.
      for (const old of affected) {
        if (!bridge(frames.get(old.id))?.canChangeFocus({notify: true})) { announce('加载期间有新的编辑，原窗口已保留。'); return; }
      }
      for (const old of affected) {
        const destination = next.panes.find(pane => pane.id === old.id), runtime = bridge(frames.get(old.id));
        const leavesSymbol = !destination || destination.symbol !== old.symbol;
        const prepared = leavesSymbol ? await runtime.prepareDeactivate({closing: true}) : runtime.prepareFocusSwitch();
        if (!prepared) { announce('当前编辑尚未保存，原窗口已保留。'); return; }
      }
      // A slow save may have allowed a new draft to start while awaiting it.
      // Recheck before replacing/removing any of the old workspaces.
      for (const old of affected) {
        if (!bridge(frames.get(old.id))?.canChangeFocus({notify: true})) { announce('保存期间有新的编辑，原窗口已保留。'); return; }
      }
      for (const old of state.panes) {
        const destination = next.panes.find(pane => pane.id === old.id), record = frames.get(old.id);
        if (destination && old.symbol !== destination.symbol) {
          if (!bridge(record) || !await bridge(record).switchSymbol(destination.symbol)) { announce('品种尚未切换，原窗口已保留。'); return; }
          record.symbol = destination.symbol; record.frame.title = name(destination.symbol) + '完整工作台';
        }
      }
      const sharedUI = bridge(oldActive)?.snapshot().ui;
      state = next; committed = true;
      for (const [id, record] of frames) if (!state.panes.some(pane => pane.id === id)) { record.frame.remove(); frames.delete(id); }
      // Restore the shared dock before measuring the newly active workspace.
      // Otherwise an old dock width causes a second resize after activation.
      if (oldActive !== activeFrame()) bridge(activeFrame())?.applyUI?.(sharedUI);
      persist(); layout();
      if (oldActive !== activeFrame()) activeFrame()?.frame.contentWindow.focus();
    } catch (error) { announce('切换暂未完成，请留意计划的同步状态。'); console.error(error); }
    finally {
      if (!committed) for (const {id, record} of staged) {
        record.frame.remove(); if (frames.get(id) === record) frames.delete(id);
      }
      if (loadingMessage && $('toast').textContent === loadingMessage) { clearTimeout(toastTimer); $('toast').hidden = true; }
      busy = false; document.body.classList.remove('is-busy'); scheduleLayout();
    }
  }
  function targetAt(x, y) {
    const box = workspaceRect(); if (!box || x < box.left || x > box.left + box.width || y < box.top || y > box.top + box.height) return null;
    if (state.panes.length === 1) return state.panes[0].id;
    return state.panes[y < box.top + box.height / 2 ? 0 : 1].id;
  }
  function dropPlacementAt(y) {
    const box = workspaceRect();
    return box && y < box.top + box.height / 2 ? 'top' : 'bottom';
  }
  function showGuide(x, y) {
    if (!drag) return;
    drag.x = x; drag.y = y;
    const target = targetAt(x, y), guide = $('drop-guide');
    if (!target) { guide.hidden = true; return; }
    const existing = state.panes.find(pane => pane.symbol === drag.symbol);
    let box;
    if (existing) { box = slotRect(existing.id); guide.textContent = '松开，切换到 ' + name(drag.symbol); }
    else if (state.panes.length === 1) {
      const total = workspaceRect(), above = dropPlacementAt(y) === 'top';
      box = {...total, top: above ? total.top : total.top + total.height / 2 + 3, height: total.height / 2 - 3};
      guide.textContent = '松开，在' + (above ? '上' : '下') + '方同时查看 ' + name(drag.symbol);
    }
    else { box = slotRect(target); guide.textContent = '松开，将此窗口切换为 ' + name(drag.symbol); }
    rectStyle(guide, box); guide.hidden = false;
    if (drag.method === 'pointer') { const ghost = $('drag-ghost'); ghost.hidden = false; ghost.textContent = name(drag.symbol); ghost.style.left = (x + 14) + 'px'; ghost.style.top = (y + 14) + 'px'; }
  }
  function beginDrag(record, data) {
    if (busy || !ids.includes(data.symbol)) return;
    const offset = record.frame.getBoundingClientRect(); drag = {symbol: data.symbol, source: record, method: data.method || 'native', x: data.clientX + offset.left, y: data.clientY + offset.top};
    // The shield covers chart slots only. Native sidebar sorting remains usable.
    const box = workspaceRect(); if (box && drag.method !== 'pointer') { rectStyle($('drop-shield'), box); $('drop-shield').hidden = false; }
    showGuide(drag.x, drag.y);
  }
  function endDrag(x, y, cancelled = false) {
    if (!drag) return;
    const gesture = drag, target = cancelled ? null : targetAt(x, y); drag = null;
    $('drop-shield').hidden = true; $('drop-guide').hidden = true; $('drag-ghost').hidden = true;
    if (target) void transition(M.dropSymbol(state, gesture.symbol, target, ids, dropPlacementAt(y)));
  }
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || !event.data || typeof event.data.type !== 'string') return;
    const pair = [...frames.entries()].find(([, record]) => record.frame.contentWindow === event.source); if (!pair) return;
    const [id, record] = pair, data = event.data; if (data.paneId !== id) return;
    switch (data.type) {
      case 'full-workspace:ready': record.ready = true; record.resolve(); markShowable(record); scheduleLayout(); break;
      case 'full-workspace:geometry': scheduleLayout(); break;
      case 'full-workspace:state': {
        const snapshot = data.snapshot || data.state || data;
        // A previous contract's queued message can arrive after a fast switch
        // has released busy. It must not roll back the selected pane or layout.
        const current = bridge(record)?.snapshot();
        if (snapshot.symbol && current?.symbol && snapshot.symbol !== current.symbol) break;
        if (snapshot.showable) markShowable(record);
        if (record.modalOpen !== snapshot.modalOpen) { record.modalOpen = snapshot.modalOpen; scheduleLayout(); }
        if (record.pageDialogOpen !== snapshot.pageDialogOpen) { record.pageDialogOpen = snapshot.pageDialogOpen; scheduleLayout(); }
        if (snapshot.symbol && record.symbol !== snapshot.symbol && ids.includes(snapshot.symbol) && !busy) { record.symbol = snapshot.symbol; const pane = state.panes.find(pane => pane.id === id); if (pane) pane.symbol = snapshot.symbol; persist(); }
        break;
      }
      case 'full-workspace:activate': if (id !== state.activeId) void transition(M.activate(state, id)); break;
      case 'full-workspace:maximize': maximizeWorkspace(id); break;
      case 'full-workspace:restore': restoreMaximized(); break;
      case 'full-workspace:dock-toggle': if (id === state.activeId) setFullscreenSidebar('right', data.open); break;
      case 'full-workspace:close': void transition(M.closePane(state, id)); break;
      case 'full-workspace:select-symbol': void transition(M.selectSymbol(state, data.symbol, ids)); break;
      case 'full-workspace:drag-start': beginDrag(record, data); break;
      case 'full-workspace:drag-move': if (drag) { const offset = record.frame.getBoundingClientRect(); showGuide(data.clientX + offset.left, data.clientY + offset.top); } break;
      case 'full-workspace:drag-end': if (drag?.method === 'pointer') { const offset = record.frame.getBoundingClientRect(); endDrag(data.clientX + offset.left, data.clientY + offset.top, Boolean(data.cancelled)); } else endDrag(0, 0, true); break;
    }
  });
  $('drop-shield').addEventListener('dragover', event => { if (!drag) return; event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; showGuide(event.clientX, event.clientY); });
  $('drop-shield').addEventListener('drop', event => { event.preventDefault(); endDrag(event.clientX, event.clientY); });
  $('drop-shield').addEventListener('dragleave', () => { $('drop-guide').hidden = true; });
  for (const side of ['left', 'right']) {
    const button = $('sidebar-' + side + '-toggle');
    button.addEventListener('pointerdown', event => event.preventDefault());
    button.addEventListener('click', () => setFullscreenSidebar(side, !fullscreenSidebars[side + 'Open']));
  }
  window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    if (drag) endDrag(0, 0, true);
    else if (workspaceMaximized && !bridge(activeFrame())?.escapeHasPriority()) restoreMaximized();
  });
  window.addEventListener('blur', () => { if (drag?.method === 'native') endDrag(0, 0, true); });
  window.addEventListener('resize', scheduleLayout);
  async function startupRequest(path) {
    const attempts = 3;
    for (let attempt = 0; attempt < attempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 12000);
      try {
        const response = await fetch('/api/workspace' + path, {
          headers: {'X-Workspace-Candle-Protocol': '2'}, credentials: 'same-origin',
          cache: 'no-store', signal: controller.signal
        });
        if (!response.ok) throw Object.assign(new Error('启动接口暂不可用'), {retryable: response.status >= 500 || response.status === 429});
        return await response.json();
      } catch (error) {
        if (error.retryable === false || attempt === attempts - 1) throw error;
        startupProgress(path === '/session' ? 0 : 1, '连接较慢，正在重试（' + (attempt + 2) + '/' + attempts + '）…', path === '/session' ? '正在连接共同画板。' : '正在读取品种列表。');
        await new Promise(resolve => setTimeout(resolve, 700 * (attempt + 1)));
      } finally { clearTimeout(timer); }
    }
  }
  function mobileDevice() {
    return Boolean(navigator.userAgentData?.mobile || /Android|iPhone|iPad|iPod|Windows Phone|IEMobile|BlackBerry|BB10|Opera Mini|Mobi|Tablet|Kindle|Silk|webOS/i.test(navigator.userAgent || '') || navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1 || navigator.maxTouchPoints > 0 && window.matchMedia?.('(pointer: coarse)').matches || window.innerWidth <= 900);
  }
  function singleViewUrl() {
    const url = new URL(location.href); url.pathname = '/workspace';
    for (const key of ['pane', 'active', 'slot', 'count']) url.searchParams.delete(key);
    url.searchParams.set('view', 'single'); return url.href;
  }
  function savedSymbol(userId) {
    try { return JSON.parse(localStorage.getItem('resonance-workspace-v1:symbol:' + userId)) || JSON.parse(localStorage.getItem('resonance-workspace-v1:last-market-symbol')); }
    catch { return null; }
  }
  function startupError(message) {
    if (startupOpened) return;
    startupProgress(0, message, '可以刷新重试或打开单窗口；原计划和图层不会被清除。');
    $('startup-actions').hidden = false; $('startup').hidden = false;
  }
  async function start() {
    try {
      window.workspaceShellStarted = true;
      startupProgress(0, '正在连接共同画板…', '正在恢复当前浏览器身份。');
      // Preserve the established phone/tablet read-only view; never duplicate
      // its scrolling chart UI into constrained desktop iframe slots.
      if (mobileDevice()) { location.replace(singleViewUrl()); return; }
      // Cookies are shared between the two panes; establish one identity first.
      const session = await startupRequest('/session');
      if (session.mobile_device) { location.replace(singleViewUrl()); return; }
      layoutKey = KEY + (session.user?.id || 'browser');
      startupProgress(1, '正在读取品种列表…', '共同画板已连接。');
      const response = await startupRequest('/symbols');
      symbols = Array.isArray(response.symbols) ? response.symbols : []; ids = symbols.map(item => item.symbol || item);
      if (!ids.length) { startupError('暂无可用品种，请稍后重试。'); return; }
      startupProgress(2, '正在加载图表组件…', '品种列表已就绪，正在恢复上次窗口。');
      window.DualWorkspaceBootstrap = {session, symbols: response, createdAt: Date.now()};
      const previousSymbol = savedSymbol(session.user?.id);
      const preferred = ids.includes(previousSymbol) ? previousSymbol : ids[0];
      const url = new URL(location.href), requested = url.searchParams.get('symbol');
      const initial = requested && ids.includes(requested) ? {panes: [{id: 'pane-a', symbol: requested}], activeId: 'pane-a'} : readLayout();
      state = M.normalize(initial || {panes: [{id: 'pane-a', symbol: preferred}], activeId: 'pane-a'}, ids);
      for (const pane of state.panes) { if (pane.symbol === requested && url.searchParams.has('board')) pane.board = url.searchParams.get('board'); makeFrame(pane); }
      layout();
      if (!await waitVisible(activeFrame())) { startupError('图表组件加载较慢，请稍后重试。'); return; }
      persist(); layout();
      // A shared link is an initial destination, not a permanent refresh override.
      if (url.searchParams.has('symbol') || url.searchParams.has('board')) {
        url.searchParams.delete('symbol'); url.searchParams.delete('board');
        history.replaceState(null, '', url.pathname + url.search + url.hash);
      }
    } catch (error) { startupError('工作台暂未能连接，请检查网络后重试。'); console.error(error); }
  }
  start();
}());
