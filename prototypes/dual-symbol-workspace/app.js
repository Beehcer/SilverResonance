(function () {
  'use strict';
  const M = window.DualWorkspaceModel, C = window.WorkspaceChart, S = window.WorkspaceSymbols;
  const $ = selector => document.querySelector(selector);
  const clone = value => JSON.parse(JSON.stringify(value));
  const NS = 'silverresonance-dual-sample-v1:';
  const periods = ['day', 'm30', 'm3'];
  const records = new Map(), plans = new Map(), undo = new Map(), views = new Map();
  let symbols = [], ids = [], state, groups, group = 'default', drag = null, suppressClickUntil = 0;
  let fieldDraft = null, currentTool = 'select', sequence = false, storageFailed = false;
  const status = message => { $('#workspace-status').textContent = message; };
  function read(key, fallback) { try { return JSON.parse(localStorage.getItem(NS + key)) ?? fallback; } catch { return fallback; } }
  function save(key, value) {
    try { localStorage.setItem(NS + key, JSON.stringify(value)); return true; }
    catch { storageFailed = true; status('浏览器保存失败，当前编辑仍保留在本页，请勿刷新。'); return false; }
  }
  const info = symbol => symbols.find(item => item.symbol === symbol);
  const name = symbol => info(symbol)?.name || symbol;
  const active = () => records.get(state?.activeId);
  const tick = symbol => info(symbol)?.tick || 1;
  const price = (value, symbol) => value == null ? '—' : C.priceLabel(value, tick(symbol));
  const newPlan = symbol => ({symbol, objects: [], planned_entry_price: null, stop_price: null, direction: null, plan_opacity: 1, enabled: false});
  function planFor(symbol) {
    if (!plans.has(symbol)) {
      const raw = read('plan:' + symbol, null);
      const plan = raw && raw.symbol === symbol && Array.isArray(raw.objects) ? {...newPlan(symbol), ...raw} : newPlan(symbol);
      plans.set(symbol, plan);
    }
    return plans.get(symbol);
  }
  function direction(plan) {
    const entry = C.priceNumber(plan.planned_entry_price), stop = C.priceNumber(plan.stop_price);
    return entry && stop && entry !== stop ? (stop < entry ? 'long' : 'short') : null;
  }
  function commit(symbol, mutate) {
    const previous = planFor(symbol), next = clone(previous);
    mutate(next); next.direction = direction(next);
    if (next.planned_entry_price != null && next.planned_entry_price === next.stop_price) { status('入场与止损不能重合，请另选一个价位。'); return false; }
    if (JSON.stringify(next) === JSON.stringify(previous)) return true;
    const history = undo.get(symbol) || [];
    history.push(clone(previous)); if (history.length > 30) history.shift(); undo.set(symbol, history);
    plans.set(symbol, next); save('plan:' + symbol, next); updatePlan(symbol); return true;
  }
  function updatePlan(symbol) {
    for (const rec of records.values()) if (rec.symbol === symbol) for (const chart of Object.values(rec.charts)) chart.setPlan(planFor(symbol));
    if (active()?.symbol === symbol) renderEditor();
  }
  function flushFields() {
    if (!fieldDraft) return true;
    const draft = fieldDraft;
    const parse = raw => raw.trim() === '' ? null : Number(raw);
    const entry = parse(draft.entry), stop = parse(draft.stop);
    if ([entry, stop].some(value => value !== null && (!Number.isFinite(value) || value <= 0)) || draft.invalid) {
      status('价位需为正数；请先修正或按 Esc 取消输入。'); return false;
    }
    const snappedEntry = entry === null ? null : C.snap(entry, tick(draft.symbol));
    const snappedStop = stop === null ? null : C.snap(stop, tick(draft.symbol));
    if ((snappedEntry !== null && snappedEntry <= 0) || (snappedStop !== null && snappedStop <= 0) || (snappedEntry !== null && snappedEntry === snappedStop)) {
      status('入场和止损需要不同的有效价位；请先修正或按 Esc 取消。'); return false;
    }
    fieldDraft = null;
    commit(draft.symbol, plan => { plan.planned_entry_price = snappedEntry; plan.stop_price = snappedStop; });
    renderEditor(); return true;
  }
  function setTool(tool) {
    currentTool = tool;
    for (const rec of records.values()) for (const chart of Object.values(rec.charts)) chart.setTool(rec.id === state?.activeId ? tool : 'select');
    document.querySelectorAll('[data-tool]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tool === tool)));
    $('#mark-entry').setAttribute('aria-pressed', String(tool.startsWith('planned-')));
    $('#ticket-hint').textContent = tool === 'planned-entry' ? '在图表上点击入场价位' : tool === 'planned-stop' ? '再点击止损价位，可拖线调整' : '先点入场，再点止损';
  }
  function applyState(next) {
    if (!flushFields()) return false;
    sequence = false; setTool('select'); state = next; save('layout', state);
    const visible = new Set(state.panes.map(pane => pane.id));
    for (const rec of records.values()) {
      if (!visible.has(rec.id)) {
        rec.controller?.abort(); rec.request++; rec.element.hidden = true; rec.symbol = null; rec.market = null;
      }
    }
    for (const pane of state.panes) {
      const rec = records.get(pane.id), changed = rec.symbol !== pane.symbol;
      rec.element.hidden = false; $('#panes').append(rec.element);
      if (changed) {
        rec.symbol = pane.symbol; rec.market = null; rec.selected = null;
        rec.element.querySelector('.pane-title').textContent = name(pane.symbol);
        rec.element.setAttribute('aria-label', name(pane.symbol) + '图表窗口');
        rec.element.querySelector('.pane-price').textContent = '—';
        for (const chart of Object.values(rec.charts)) chart.selected = null;
        loadMarket(rec, true);
      }
      rec.element.classList.toggle('is-active', pane.id === state.activeId);
      rec.element.querySelector('.pane-title').setAttribute('aria-pressed', String(pane.id === state.activeId));
      rec.element.querySelector('.close-pane').hidden = state.panes.length < 2;
      rec.element.querySelector('.close-pane').setAttribute('aria-label', '关闭' + name(pane.symbol) + '窗口');
    }
    $('#preview').classList.toggle('is-single', state.panes.length === 1);
    $('#pane-empty-hint').hidden = state.panes.length === 2;
    renderEditor(); renderSymbols(); return true;
  }
  function activate(id) { return id === state.activeId || applyState(M.activate(state, id)); }
  function renderEditor() {
    const rec = active(); if (!rec?.symbol) return;
    const plan = planFor(rec.symbol);
    $('#active-symbol').textContent = name(rec.symbol);
    if (!fieldDraft) {
      $('#ticket-entry').value = plan.planned_entry_price ?? '';
      $('#ticket-stop').value = plan.stop_price ?? '';
    }
    $('#ticket-entry').setAttribute('aria-label', name(rec.symbol) + '入场价');
    $('#ticket-stop').setAttribute('aria-label', name(rec.symbol) + '止损价');
    $('#ticket-direction').textContent = plan.direction === 'long' ? '做多计划' : plan.direction === 'short' ? '做空计划' : '未设置';
    $('#ticket-direction').dataset.direction = plan.direction || '';
    $('#undo').disabled = !(undo.get(rec.symbol)?.length);
    $('#delete-selected').disabled = !plan.objects.some(object => object.id === rec.selected);
    const list = $('#layer-list'); list.replaceChildren();
    if (!plan.objects.length) { const p = document.createElement('p'); p.className = 'dl-empty'; p.textContent = '暂无标记'; list.append(p); }
    for (const object of plan.objects) {
      const row = document.createElement('div'); row.className = 'dl-layer-row';
      const button = document.createElement('button'); button.type = 'button'; button.textContent = object.name || object.kind;
      button.setAttribute('aria-pressed', String(rec.selected === object.id));
      button.onclick = () => { rec.selected = object.id; for (const chart of Object.values(rec.charts)) { chart.selected = object.id; chart.render(); } renderEditor(); };
      row.append(button); list.append(row);
    }
  }
  function createPane(id) {
    const element = $('#pane-template').content.firstElementChild.cloneNode(true);
    element.dataset.paneId = id; element.hidden = true; $('#panes').append(element);
    const rec = {id, element, symbol: null, market: null, charts: {}, request: 0, controller: null, selected: null};
    records.set(id, rec);
    element.addEventListener('pointerdown', event => {
      if (event.target.closest('.close-pane') || state.activeId === id) return;
      const drawing = currentTool !== 'select';
      if (!activate(id) || drawing) { event.preventDefault(); event.stopPropagation(); }
    }, true);
    element.querySelector('.pane-title').onclick = () => activate(id);
    element.querySelector('.close-pane').onclick = () => { if (applyState(M.closePane(state, id))) status('窗口已关闭，绘图和计划已保留。'); };
    element.querySelector('.retry-pane').onclick = () => loadMarket(rec, !rec.market);
    for (const period of periods) {
      const chart = new C.Chart(element.querySelector('[data-chart=' + period + ']'), period, {
        activate: () => activate(id),
        select: objectId => { rec.selected = objectId; renderEditor(); },
        add: raw => {
          if (!rec.symbol) return;
          const object = {...raw, id: window.PlanBoardModel.makeId(), source_period: period, visible: true, width: 2, line_style: raw.line_style || 'solid', opacity: raw.opacity ?? 1};
          commit(rec.symbol, plan => { object.z_index = plan.objects.length; plan.objects.push(object); });
          rec.selected = object.id; setTool('select'); renderEditor();
        },
        update: (objectId, patch) => commit(rec.symbol, plan => { const object = plan.objects.find(item => item.id === objectId); if (object) Object.assign(object, patch); }),
        deleteObject: objectId => commit(rec.symbol, plan => { plan.objects = plan.objects.filter(item => item.id !== objectId); }),
        placeLevel: (field, value) => {
          if (!commit(rec.symbol, plan => { plan[field] = value; })) return;
          if (sequence && field === 'planned_entry_price') setTool('planned-stop');
          else { sequence = false; setTool('select'); }
        },
        dragLevel: (field, value, meta) => { if (meta.phase === 'end') commit(rec.symbol, plan => { plan[field] = value; }); },
        notice: status,
        cancelTool: () => { sequence = false; setTool('select'); },
        snapEnabled: () => true,
        hoverDay: range => { if (period === 'day') for (const p of ['m30', 'm3']) rec.charts[p]?.setLinkedRange(range); },
        viewChanged: changed => { if (rec.symbol && changed.view) views.set(rec.symbol + ':' + period, {view: {...changed.view}, autoPrice: changed.autoPrice}); }
      });
      chart.uid = 'dual-' + id + '-' + period; rec.charts[period] = chart;
    }
  }
  async function loadMarket(rec, reset) {
    rec.controller?.abort(); const controller = new AbortController(); rec.controller = controller;
    const ticket = {id: rec.id, symbol: rec.symbol, request: ++rec.request};
    const overlay = rec.element.querySelector('.pane-loading');
    overlay.hidden = false; rec.element.querySelector('.pane-loading-text').textContent = '正在准备模拟行情…'; rec.element.querySelector('.retry-pane').hidden = true;
    try {
      const response = await fetch('/api/market?symbol=' + encodeURIComponent(ticket.symbol), {signal: controller.signal});
      if (!response.ok) throw new Error('行情加载失败');
      const {market} = await response.json(); if (!M.sameTicket(rec, ticket, market?.symbol)) return;
      rec.market = market;
      for (const period of periods) {
        const chart = rec.charts[period], savedView = views.get(rec.symbol + ':' + period);
        chart.setData(market, planFor(rec.symbol), reset);
        if (reset && savedView) { chart.view = {...savedView.view}; chart.autoPrice = savedView.autoPrice; chart.viewCustomized = true; chart.followLatest = false; views.set(rec.symbol + ':' + period, savedView); chart.render(); }
        const bars = market.periods[period];
        const average = count => bars.slice(-count).reduce((sum, bar) => sum + bar.close, 0) / Math.min(count, bars.length);
        const stats = rec.element.querySelector('[data-stats=' + period + ']'); stats.replaceChildren();
        for (const count of [5, 10, 20]) { const span = document.createElement('span'); span.className = 'ma' + count; span.textContent = 'MA' + count + ' ' + price(average(count), rec.symbol) + '  '; stats.append(span); }
      }
      rec.element.querySelector('.pane-price').textContent = price(market.price, rec.symbol);
      overlay.hidden = true; renderSymbols();
      if (!storageFailed) status('模拟行情 · 拖入品种并排查看，点击窗口切换编辑。');
    } catch (error) {
      if (error.name === 'AbortError' || !M.sameTicket(rec, ticket, ticket.symbol)) return;
      rec.element.querySelector('.pane-loading-text').textContent = '暂时无法加载模拟行情'; rec.element.querySelector('.retry-pane').hidden = false;
      status('本地服务可能已停止；可重启启动文件后重试。');
    }
  }
  function renderSymbols() {
    if (drag?.started || !state) return;
    const list = $('#symbol-list'); list.replaceChildren();
    for (const item of S.groupItems(symbols, groups, group, $('#symbol-search').value)) {
      const row = document.createElement('div'); row.className = 'dl-symbol-row'; row.dataset.symbol = item.symbol;
      const pick = document.createElement('button'); pick.type = 'button'; pick.className = 'dl-symbol-pick'; pick.dataset.symbol = item.symbol;
      pick.classList.toggle('is-active', active()?.symbol === item.symbol);
      pick.title = '点击切换；拖入图表同时查看；Shift+Enter 在下方打开';
      const label = document.createElement('span'); label.className = 'dl-symbol-name'; label.textContent = item.name;
      const quote = document.createElement('span'); quote.className = 'dl-symbol-price'; quote.textContent = price([...records.values()].find(rec => rec.symbol === item.symbol)?.market?.price, item.symbol);
      pick.append(label, quote); row.append(pick);
      for (const target of ['favorites', 'default']) {
        const exists = groups[target].includes(item.symbol), button = document.createElement('button'); button.type = 'button';
        button.className = target === 'favorites' ? 'dl-symbol-star' : 'dl-symbol-default';
        button.textContent = target === 'favorites' ? (exists ? '★' : '☆') : (exists ? '−' : '+');
        button.setAttribute('aria-pressed', String(exists)); button.title = (exists ? '移出' : '加入') + (target === 'favorites' ? '喜爱' : '默认'); button.setAttribute('aria-label', button.title + item.name);
        button.onclick = () => { groups = exists ? S.removeFromGroup(groups, target, item.symbol) : S.addToGroup(groups, target, item.symbol); save('groups', groups); renderSymbols(); };
        row.append(button);
      }
      list.append(row);
    }
    if (!list.childElementCount) { const p = document.createElement('p'); p.className = 'dl-empty'; p.textContent = '暂无品种，可从“全部”加入'; list.append(p); }
  }
  function clearDropGuide() {
    $('#panes').classList.remove('is-add-preview'); $('#drop-preview').hidden = true;
    document.querySelectorAll('.drop-before,.drop-after,.is-over').forEach(node => node.classList.remove('drop-before', 'drop-after', 'is-over'));
  }
  function dropTarget(x, y) {
    const hit = document.elementFromPoint(x, y); if (!hit) return null;
    const tab = hit.closest('[data-symbol-group]');
    if (tab && ['default', 'favorites'].includes(tab.dataset.symbolGroup)) return {kind: 'group', group: tab.dataset.symbolGroup, node: tab};
    const row = hit.closest('.dl-symbol-row');
    if (row && ['default', 'favorites'].includes(group)) return {kind: 'sort', symbol: row.dataset.symbol, node: row, before: y < row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2};
    if (hit.closest('#workspace')) {
      if (state.panes.length === 1) return {kind: 'pane', id: state.panes[0].id};
      const pane = hit.closest('.dl-pane'); if (pane && !pane.hidden) return {kind: 'pane', id: pane.dataset.paneId};
    }
    return null;
  }
  function drawDropGuide(target) {
    clearDropGuide(); if (!target) return;
    if (target.kind === 'group') { target.node.classList.add('is-over'); return; }
    if (target.kind === 'sort') { if (target.symbol !== drag.symbol) target.node.classList.add(target.before ? 'drop-before' : 'drop-after'); return; }
    const existing = state.panes.find(pane => pane.symbol === drag.symbol), preview = $('#drop-preview');
    const adding = state.panes.length === 1 && !existing;
    preview.hidden = false; preview.dataset.mode = adding ? 'add' : 'replace';
    preview.style.cssText = '';
    if (adding) { $('#panes').classList.add('is-add-preview'); preview.textContent = '松开，在下方查看 ' + name(drag.symbol); }
    else {
      const rec = records.get(existing?.id || target.id), box = rec.element.getBoundingClientRect(), parent = $('#workspace').getBoundingClientRect();
      preview.style.inset = 'auto'; preview.style.left = (box.left - parent.left) + 'px'; preview.style.top = (box.top - parent.top) + 'px'; preview.style.width = box.width + 'px'; preview.style.height = box.height + 'px';
      preview.textContent = existing ? '松开，切换到 ' + name(drag.symbol) : '松开，将此窗口切换为 ' + name(drag.symbol);
    }
  }
  function finishDrag(event, cancelled = false) {
    if (!drag) return;
    const gesture = drag;
    // Keep the original button attached until a normal click has bubbled.
    if (!gesture.started) { drag = null; return; }
    const target = !cancelled ? dropTarget(event.clientX, event.clientY) : null;
    drag = null; clearDropGuide(); $('#drag-ghost').hidden = true; $('#preview').classList.remove('is-dragging');
    if (gesture.started) {
      suppressClickUntil = performance.now() + 400;
      try { gesture.node.releasePointerCapture(gesture.pointerId); } catch { /* Outside release is also a cancellation. */ }
      if (target?.kind === 'pane') {
        if (applyState(M.dropSymbol(state, gesture.symbol, target.id, ids))) status('已打开 ' + name(gesture.symbol) + '，点击任一窗口即可编辑。');
      } else if (target?.kind === 'group') { groups = S.addToGroup(groups, target.group, gesture.symbol); save('groups', groups); }
      else if (target?.kind === 'sort') { groups = S.moveInGroup(groups, group, gesture.symbol, target.symbol, target.before); save('groups', groups); }
      else status('已取消拖放，窗口保持原样。');
    }
    renderSymbols();
  }
  function bindEvents() {
    $('#symbol-search').oninput = renderSymbols;
    $('#symbol-groups').onclick = event => {
      const button = event.target.closest('[data-symbol-group]'); if (!button) return;
      group = button.dataset.symbolGroup;
      document.querySelectorAll('[data-symbol-group]').forEach(node => { node.classList.toggle('is-active', node === button); node.setAttribute('aria-pressed', String(node === button)); }); renderSymbols();
    };
    $('#symbol-list').addEventListener('click', event => {
      const button = event.target.closest('.dl-symbol-pick'); if (!button || performance.now() < suppressClickUntil) return;
      applyState(M.selectSymbol(state, button.dataset.symbol, ids));
    });
    $('#symbol-list').addEventListener('keydown', event => {
      const button = event.target.closest('.dl-symbol-pick'); if (!button) return;
      if (event.key === 'Enter' && event.shiftKey) { event.preventDefault(); applyState(M.dropSymbol(state, button.dataset.symbol, state.activeId, ids)); }
      if (event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key) && ['default', 'favorites'].includes(group)) {
        event.preventDefault(); const index = groups[group].indexOf(button.dataset.symbol), offset = event.key === 'ArrowUp' ? -1 : 1, target = groups[group][index + offset];
        groups = S.moveInGroup(groups, group, button.dataset.symbol, target, offset < 0); save('groups', groups); renderSymbols();
        [...document.querySelectorAll('.dl-symbol-pick')].find(node => node.dataset.symbol === button.dataset.symbol)?.focus();
      }
    });
    $('#symbol-list').addEventListener('pointerdown', event => {
      const node = event.target.closest('.dl-symbol-pick'); if (!node || event.button !== 0) return;
      drag = {node, symbol: node.dataset.symbol, x: event.clientX, y: event.clientY, pointerId: event.pointerId, started: false};
    });
    document.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (!drag.started && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 7) return;
      if (!drag.started) { if (!flushFields()) { drag = null; return; } drag.started = true; try { drag.node.setPointerCapture(event.pointerId); } catch {} $('#preview').classList.add('is-dragging'); drag.node.closest('.dl-symbol-row').classList.add('is-dragging'); }
      event.preventDefault(); const ghost = $('#drag-ghost'); ghost.hidden = false; ghost.textContent = name(drag.symbol); ghost.style.transform = `translate(${event.clientX + 16}px,${event.clientY + 14}px)`;
      drawDropGuide(dropTarget(event.clientX, event.clientY));
    }, {passive: false});
    document.addEventListener('pointerup', event => { if (drag?.pointerId === event.pointerId) finishDrag(event); });
    document.addEventListener('pointercancel', event => { if (drag?.pointerId === event.pointerId) finishDrag(event, true); });
    window.addEventListener('blur', () => finishDrag({}, true));
    for (const id of ['ticket-entry', 'ticket-stop']) {
      $('#' + id).addEventListener('input', () => { fieldDraft = {symbol: active().symbol, entry: $('#ticket-entry').value, stop: $('#ticket-stop').value, invalid: $('#ticket-entry').validity.badInput || $('#ticket-stop').validity.badInput}; });
      $('#' + id).addEventListener('change', flushFields);
    }
    document.querySelectorAll('[data-tool]').forEach(button => button.onclick = () => { if (flushFields()) { sequence = false; setTool(button.dataset.tool); } });
    $('#mark-entry').onclick = () => { if (flushFields()) { sequence = true; setTool('planned-entry'); } };
    $('#clear-plan').onclick = () => { if (flushFields()) { sequence = false; setTool('select'); commit(active().symbol, plan => { plan.planned_entry_price = null; plan.stop_price = null; }); } };
    $('#delete-selected').onclick = () => { const rec = active(); if (rec?.selected) commit(rec.symbol, plan => { plan.objects = plan.objects.filter(object => object.id !== rec.selected); }); };
    $('#undo').onclick = () => { if (!flushFields()) return; const symbol = active().symbol, history = undo.get(symbol); if (history?.length) { plans.set(symbol, history.pop()); save('plan:' + symbol, planFor(symbol)); updatePlan(symbol); } };
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') { finishDrag({}, true); fieldDraft = null; renderEditor(); sequence = false; setTool('select'); return; }
      if (event.target.closest('input,textarea,[contenteditable=true]')) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); $('#undo').click(); return; }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const shortcuts = {v: 'select', h: 'horizontal', t: 'trend', z: 'zone'};
      if (shortcuts[event.key.toLowerCase()] && flushFields()) { event.preventDefault(); sequence = false; setTool(shortcuts[event.key.toLowerCase()]); }
      if (event.key === 'Delete') { event.preventDefault(); $('#delete-selected').click(); }
    });
    window.addEventListener('beforeunload', event => { if (!flushFields() || storageFailed) { event.preventDefault(); event.returnValue = ''; } });
  }
  async function start() {
    try {
      const response = await fetch('/api/symbols'); if (!response.ok) throw new Error('目录加载失败');
      symbols = (await response.json()).symbols; ids = symbols.map(item => item.symbol);
      groups = S.normalizeGroups(read('groups', null), symbols);
      const preferred = symbols.find(item => /甲醇/.test(item.name))?.symbol || ids[0];
      state = M.normalize(read('layout', {panes: [{id: 'pane-a', symbol: preferred}], activeId: 'pane-a'}), ids);
      M.PANE_IDS.forEach(createPane); bindEvents(); applyState(state);
    } catch (error) { status('样板启动失败，请确认本地服务仍在运行，再刷新页面。'); console.error(error); }
  }
  start();
}());
