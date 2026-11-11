(function () {
  'use strict';

  const Model = globalThis.PlanBoardModel;
  const $ = id => document.getElementById(id);
  globalThis.addEventListener('error', event => {
    const status = $('save-state');
    if (status) {
      status.dataset.state = 'error';
      status.textContent = '画板脚本错误：' + event.message;
    }
  });
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const STORAGE_KEY = 'silver-plan-board-demo:v1';
  const LAST_SYMBOL_KEY = STORAGE_KEY + ':last-symbol';
  const SYMBOLS = [
    {symbol: 'DEMO.ag', name: '沪银', base: 7200, seed: 927431},
    {symbol: 'DEMO.au', name: '沪金', base: 560, seed: 527341},
    {symbol: 'DEMO.rb', name: '螺纹钢', base: 3200, seed: 327451},
    {symbol: 'DEMO.SA', name: '纯碱', base: 1400, seed: 727461}
  ];
  const PERIODS = {
    day: {label: '日线', interval: 86400000},
    m3: {label: '3分钟', interval: 180000},
    m30: {label: '30分钟', interval: 1800000}
  };
  const REAL_BAR_MS = 4000;
  const TICK = 1;
  let canvas, host, svg, ctx, bars, view, follow, seed, lastRollAt, INTERVAL;
  let width = 0, height = 0, dpr = 1, plot = null, frame = 0;
  let currentSymbol = localStorage.getItem(LAST_SYMBOL_KEY) || 'DEMO.ag';
  if (!SYMBOLS.some(item => item.symbol === currentSymbol)) currentSymbol = 'DEMO.ag';
  let doc = loadDocument(currentSymbol);
  let history = new Model.PlanHistory(doc);
  let selectedId = null, selectedPointIndex = null, lastPathClick = null, lastInspectorSelection = null;
  let tool = 'select';
  let activeStyle = {color: Model.COLORS.red, width: 2, line_style: 'solid'};
  let draft = null, pathDraft = null, drag = null, crosshair = null;
  let dirty = false, saveTimer = null, switching = false, contextTargetId = null;
  const charts = {};
  let activePeriod = 'm30', chartPeriod = 'm30';
  const Sync = globalThis.PlanBoardSync;
  const CLIENT_ID = Model.makeId();
  const syncBases = {}, syncPending = {}, syncInFlight = {}, syncAgain = {}, syncConflicts = {}, syncRetryTimers = {};
  let eventSource = null, syncGeneration = 0;

  function symbolConfig(symbol) {
    return SYMBOLS.find(item => item.symbol === symbol) || SYMBOLS[0];
  }

  function storageKey(symbol) {
    return STORAGE_KEY + (symbol === 'DEMO.ag' ? '' : ':' + symbol);
  }

  function random() {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  }

  function createBars(count, symbol, period) {
    const result = [];
    const interval = PERIODS[period].interval;
    const volatility = Math.max(.3, Math.sqrt(interval / PERIODS.m30.interval));
    let price = symbolConfig(symbol).base;
    const end = Math.floor(Date.now() / interval) * interval;
    for (let i = 0; i < count; i += 1) {
      const open = price;
      const change = (Math.sin(i / 8) * 7 + (random() - .48) * 15) * volatility;
      const close = Math.max(100, open + change);
      const high = Math.max(open, close) + random() * 8 * volatility;
      const low = Math.min(open, close) - random() * 8 * volatility;
      result.push({dt: end - (count - 1 - i) * interval, open, high, low, close,
        volume: Math.round(300 + random() * 900), live: i === count - 1});
      price = close;
    }
    return result;
  }

  function useChart(period) {
    chartPeriod = period;
    const chart = charts[period];
    canvas = chart.canvas;
    host = chart.host;
    svg = chart.svg;
    ctx = chart.ctx;
    bars = chart.bars;
    view = chart.view;
    follow = chart.follow;
    seed = chart.seed;
    lastRollAt = chart.lastRollAt;
    INTERVAL = chart.interval;
    width = chart.width;
    height = chart.height;
    dpr = chart.dpr;
    plot = chart.plot;
  }

  function saveChart(period) {
    Object.assign(charts[period], {bars, view, follow, seed, lastRollAt, width, height, dpr, plot});
  }

  function withChart(period, callback) {
    useChart(period);
    callback();
    saveChart(period);
  }

  function activateChart(period) {
    if (!charts[period]) return;
    activePeriod = period;
    useChart(period);
    document.querySelectorAll('.chart-panel').forEach(panel => panel.classList.toggle('active', panel.dataset.period === period));
    $('active-view').textContent = '当前：' + PERIODS[period].label;
    $('follow').checked = follow;
    $('zoom-range').value = charts[period].zoomX;
    $('zoom-value').textContent = charts[period].zoomX + '%';
    $('zoom-y-range').value = charts[period].zoomY;
    $('zoom-y-value').textContent = charts[period].zoomY + '%';
    scheduleRender();
  }

  for (const [index, period] of Object.keys(PERIODS).entries()) {
    seed = symbolConfig(currentSymbol).seed + index * 100003;
    const canvasElement = $('kline-canvas-' + period);
    charts[period] = {
      canvas: canvasElement, host: $('chart-host-' + period), svg: $('drawing-layer-' + period),
      ctx: canvasElement.getContext('2d'), bars: createBars(96, currentSymbol, period),
      view: {start: 0, end: 0, min: 0, max: 1}, follow: false, seed, lastRollAt: Date.now(),
      interval: PERIODS[period].interval, zoomX: 100, zoomY: 100, width: 0, height: 0, dpr: 1, plot: null
    };
  }
  useChart(activePeriod);

  function updateMarket() {
    for (const period of Object.keys(PERIODS)) {
      withChart(period, () => {
        const last = bars[bars.length - 1];
        const volatility = Math.max(.3, Math.sqrt(INTERVAL / PERIODS.m30.interval));
        const change = (random() - .49) * 7 * volatility + Math.sin(Date.now() / 7000) * .8;
        last.close = Math.max(100, last.close + change);
        last.high = Math.max(last.high, last.close);
        last.low = Math.min(last.low, last.close);
        last.volume += Math.round(random() * 80);
        if (Date.now() - lastRollAt >= REAL_BAR_MS) {
          lastRollAt = Date.now();
          last.live = false;
          last.rolled = true;
          const open = last.close;
          bars.push({dt: last.dt + INTERVAL, open, high: open, low: open, close: open,
            volume: 0, live: true, rolled: false});
          if (bars.length > 240) bars.shift();
          if (follow && !drag && !pathDraft) {
            const span = view.end - view.start;
            view.end = bars[bars.length - 1].dt + INTERVAL * 18;
            view.start = view.end - span;
          }
        }
        if (follow && !drag && !pathDraft) fitPrices(false);
      });
    }
    useChart(activePeriod);
    scheduleRender();
  }

  function loadDocument(symbol) {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey(symbol)) || 'null');
      return saved ? Model.normalizeDocument({...saved, symbol}) : Model.createDocument({symbol});
    } catch (_) {
      return Model.createDocument({symbol});
    }
  }

  function syncStatus(state, message) {
    $('save-state').dataset.state = state;
    $('save-state').textContent = message;
  }

  function writeLocal(symbol, documentValue) {
    localStorage.setItem(storageKey(symbol), JSON.stringify(documentValue));
  }

  function saveNow() {
    clearTimeout(saveTimer);
    saveTimer = null;
    try {
      writeLocal(currentSymbol, doc);
      dirty = false;
      syncStatus('saving', '本地已保存，正在同步…');
      syncShared(currentSymbol);
    } catch (_) {
      syncStatus('error', '本地保存失败，请导出计划备份');
    }
  }

  function scheduleSave() {
    dirty = true;
    syncPending[currentSymbol] = true;
    syncStatus('saving', '正在保存并同步…');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 250);
  }

  async function syncShared(symbol) {
    if (syncConflicts[symbol]) return;
    if (syncInFlight[symbol]) {
      syncAgain[symbol] = true;
      return;
    }
    const local = symbol === currentSymbol ? doc : loadDocument(symbol);
    const base = syncBases[symbol] || local;
    const payload = Sync.diff(base, local);
    if (!Sync.hasOperations(payload)) {
      syncPending[symbol] = false;
      if (symbol === currentSymbol) syncStatus('saved', '多人同步已连接 · 当前计划一致');
      return;
    }
    const localRevision = local.revision;
    syncInFlight[symbol] = true;
    syncAgain[symbol] = false;
    try {
      const response = await fetch('/api/board-demo/plans/' + encodeURIComponent(symbol), {
        method: 'PUT', headers: {'Content-Type': 'application/json', 'X-Board-Client': CLIENT_ID},
        body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (response.status === 409) {
        syncConflicts[symbol] = true;
        syncBases[symbol] = result.plan;
        if (symbol === currentSymbol) syncStatus('error', '同步冲突：本地内容已保留，请导出备份后刷新核对');
        return;
      }
      if (!response.ok) throw new Error(result.message || '同步失败');
      syncBases[symbol] = result.plan;
      sharedPlans[symbol] = result.plan;
      const latest = symbol === currentSymbol ? doc : loadDocument(symbol);
      const merged = latest.revision === localRevision ? result.plan
        : Sync.applyOperations(result.plan, Sync.diff(local, latest));
      if (symbol === currentSymbol) {
        doc = Model.normalizeDocument(merged);
        history.present = Model.clone(doc);
        refreshSymbolOptions();
        refreshInspector();
        scheduleRender();
      }
      writeLocal(symbol, merged);
      syncPending[symbol] = Sync.hasOperations(Sync.diff(result.plan, merged));
      if (symbol === currentSymbol) syncStatus('saved', '多人同步已保存 · ' + new Date().toLocaleTimeString('zh-CN', {hour12: false}));
    } catch (_) {
      syncPending[symbol] = true;
      if (symbol === currentSymbol) syncStatus('error', '同步失败，已保留本地备份，5秒后重试');
      scheduleSharedRetry(symbol);
    } finally {
      syncInFlight[symbol] = false;
      if (syncAgain[symbol]) {
        syncAgain[symbol] = false;
        syncShared(symbol);
      }
    }
  }

  function applyRemotePlan(symbol, plan) {
    if (syncConflicts[symbol]) return;
    if (syncPending[symbol] || syncInFlight[symbol] || drag || pathDraft) {
      syncPending[symbol] = true;
      return;
    }
    syncBases[symbol] = plan;
    sharedPlans[symbol] = plan;
    doc = Model.normalizeDocument(plan);
    history.present = Model.clone(doc);
    if (selectedId && !doc.objects.some(object => object.id === selectedId)) {
      selectedId = null;
      selectedPointIndex = null;
    } else if (selectedId && selectedPointIndex !== null) {
      const selected = doc.objects.find(object => object.id === selectedId);
      selectedPointIndex = Math.max(-1, Math.min(selectedPointIndex, selected.points.length - 1));
    }
    writeLocal(symbol, doc);
    refreshSymbolOptions();
    refreshInspector();
    scheduleRender();
    syncStatus('saved', '已收到其他客户端更新');
  }

  function scheduleSharedRetry(symbol) {
    if (syncRetryTimers[symbol]) return;
    syncRetryTimers[symbol] = setTimeout(() => {
      delete syncRetryTimers[symbol];
      if (symbol === currentSymbol) connectShared();
    }, 5000);
  }

  function openSharedEvents(symbol, generation) {
    if (eventSource) eventSource.close();
    eventSource = new EventSource('/api/board-demo/events/' + encodeURIComponent(symbol));
    eventSource.onopen = () => {
      if (generation === syncGeneration) {
        clearTimeout(syncRetryTimers[symbol]);
        delete syncRetryTimers[symbol];
        syncStatus('saved', '多人同步已连接 · 当前计划一致');
      }
    };
    eventSource.addEventListener('plan', event => {
      if (generation !== syncGeneration || symbol !== currentSymbol) return;
      const message = JSON.parse(event.data);
      if (message.client_id === CLIENT_ID) return;
      applyRemotePlan(symbol, Model.normalizeDocument(message.plan));
    });
    eventSource.onerror = () => {
      if (generation === syncGeneration && !syncConflicts[symbol]) syncStatus('error', '实时通道重连中 · 本地编辑会保留');
    };
  }

  async function connectShared() {
    const symbol = currentSymbol;
    const generation = ++syncGeneration;
    if (eventSource) eventSource.close();
    delete syncConflicts[symbol];
    syncStatus('saving', '正在连接共享计划服务…');
    try {
      const response = await fetch('/api/board-demo/plans/' + encodeURIComponent(symbol), {cache: 'no-store'});
      if (!response.ok) throw new Error('连接失败');
      const result = await response.json();
      if (generation !== syncGeneration || symbol !== currentSymbol) return;
      const server = Model.normalizeDocument(result.plan);
      syncBases[symbol] = server;
      sharedPlans[symbol] = server;
      const uploadLocal = server.revision === 0 && server.objects.length === 0 && Sync.hasOperations(Sync.diff(server, doc));
      if (uploadLocal) {
        scheduleSave();
      } else {
        doc = server;
        history = new Model.PlanHistory(doc);
        selectedId = null;
        writeLocal(symbol, doc);
        refreshSymbolOptions();
        refreshInspector();
        resetView();
        syncStatus('saved', '多人同步已连接 · 当前计划一致');
      }
      openSharedEvents(symbol, generation);
    } catch (_) {
      if (generation === syncGeneration) {
        syncStatus('error', '共享服务不可用 · 当前仅本地保存，5秒后重试');
        scheduleSharedRetry(symbol);
      }
    }
  }

  function svgNode(tag, attributes = {}, value = null) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, val] of Object.entries(attributes)) node.setAttribute(key, String(val));
    if (value !== null) node.textContent = value;
    return node;
  }

  function resize() {
    const rect = host.getBoundingClientRect();
    const nextWidth = Math.max(1, rect.width), nextHeight = Math.max(1, rect.height);
    const nextDpr = Math.min(2, globalThis.devicePixelRatio || 1);
    if (nextWidth === width && nextHeight === height && nextDpr === dpr) return;
    width = nextWidth;
    height = nextHeight;
    dpr = nextDpr;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    plot = {left: 12, top: 12, right: width - 72, bottom: height - 30};
    plot.width = Math.max(1, plot.right - plot.left);
    plot.height = Math.max(1, plot.bottom - plot.top);
  }

  function xForTime(time) { return plot.left + (time - view.start) / (view.end - view.start) * plot.width; }
  function timeForX(x) { return view.start + (x - plot.left) / plot.width * (view.end - view.start); }
  function yForPrice(price) { return plot.top + (view.max - price) / (view.max - view.min) * plot.height; }
  function priceForY(y) { return view.max - (y - plot.top) / plot.height * (view.max - view.min); }
  const insidePlot = pos => pos.x >= plot.left && pos.x <= plot.right && pos.y >= plot.top && pos.y <= plot.bottom;

  function fitPrices(includeObjects = true) {
    const visible = bars.filter(bar => bar.dt + INTERVAL >= view.start && bar.dt <= view.end);
    let values = visible.flatMap(bar => [bar.low, bar.high]);
    if (includeObjects) {
      for (const object of doc.objects) {
        if (object.visible && (object.period === chartPeriod || object.period === 'all')) values.push(...object.points.map(point => point.price));
      }
    }
    if (!values.length) values = [bars[bars.length - 1].close - 50, bars[bars.length - 1].close + 50];
    let min = Math.min(...values), max = Math.max(...values);
    const padding = Math.max(10, (max - min) * .09);
    min -= padding;
    max += padding;
    view.min = Math.max(1, min);
    view.max = max;
  }

  function resetView() {
    const visibleBars = {day: 60, m3: 55, m30: 72}[chartPeriod];
    view.end = bars[bars.length - 1].dt + INTERVAL * 18;
    view.start = view.end - INTERVAL * visibleBars;
    fitPrices(false);
    follow = false;
    $('follow').checked = false;
    charts[chartPeriod].zoomX = 100;
    charts[chartPeriod].zoomY = 100;
    $('zoom-range').value = 100;
    $('zoom-value').textContent = '100%';
    $('zoom-y-range').value = 100;
    $('zoom-y-value').textContent = '100%';
    saveChart(chartPeriod);
    scheduleRender();
  }

  function resetAllViews() {
    for (const period of Object.keys(PERIODS)) withChart(period, resetView);
    useChart(activePeriod);
    activateChart(activePeriod);
  }

  function scheduleRender() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      for (const period of Object.keys(PERIODS)) {
        withChart(period, () => {
          resize();
          drawChart();
          drawObjects();
        });
      }
      useChart(activePeriod);
    });
  }

  function drawChart() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#0a111a';
    ctx.fillRect(0, 0, width, height);
    ctx.font = '10px Segoe UI, sans-serif';
    ctx.textBaseline = 'middle';
    const futureBoundary = xForTime(bars[bars.length - 1].dt + INTERVAL);
    if (futureBoundary > plot.left && futureBoundary < plot.right) {
      ctx.fillStyle = 'rgba(108, 117, 125, 0.055)';
      ctx.fillRect(futureBoundary, plot.top, plot.right - futureBoundary, plot.height);
    }
    const priceStep = (view.max - view.min) / 6;
    for (let i = 0; i <= 6; i += 1) {
      const price = view.min + priceStep * i;
      const y = yForPrice(price);
      ctx.strokeStyle = '#1a2735';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.fillStyle = '#73889d';
      ctx.textAlign = 'left';
      ctx.fillText(price.toFixed(1), plot.right + 7, y);
    }
    const timeStep = (view.end - view.start) / 6;
    for (let i = 0; i <= 6; i += 1) {
      const time = view.start + timeStep * i;
      const x = xForTime(time);
      ctx.strokeStyle = '#172432';
      ctx.beginPath();
      ctx.moveTo(x, plot.top);
      ctx.lineTo(x, plot.bottom);
      ctx.stroke();
      ctx.fillStyle = '#73889d';
      ctx.textAlign = i === 0 ? 'left' : i === 6 ? 'right' : 'center';
      const label = chartPeriod === 'day'
        ? new Date(time).toLocaleDateString('zh-CN', {month: '2-digit', day: '2-digit'})
        : new Date(time).toLocaleTimeString('zh-CN', {hour12: false, hour: '2-digit', minute: '2-digit'});
      ctx.fillText(label, x, plot.bottom + 15);
    }
    const candleWidth = Math.max(1, Math.min(12, INTERVAL / (view.end - view.start) * plot.width * .68));
    ctx.save();
    ctx.beginPath();
    ctx.rect(plot.left, plot.top, plot.width, plot.height);
    ctx.clip();
    for (const bar of bars) {
      if (bar.dt + INTERVAL < view.start || bar.dt > view.end) continue;
      const x = xForTime(bar.dt + INTERVAL / 2);
      const rising = bar.close >= bar.open;
      const color = rising ? '#ef6268' : '#48be8b';
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yForPrice(bar.high));
      ctx.lineTo(x, yForPrice(bar.low));
      ctx.stroke();
      const top = yForPrice(Math.max(bar.open, bar.close));
      const bottom = yForPrice(Math.min(bar.open, bar.close));
      ctx.fillRect(x - candleWidth / 2, top, candleWidth, Math.max(1, bottom - top));
    }
    ctx.restore();
    const live = bars[bars.length - 1];
    const liveY = yForPrice(live.close);
    if (liveY >= plot.top && liveY <= plot.bottom) {
      ctx.strokeStyle = '#e7c46099';
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(plot.left, liveY);
      ctx.lineTo(plot.right, liveY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#e7c460';
      ctx.textAlign = 'left';
      ctx.fillText(live.close.toFixed(1), plot.right + 7, liveY + 10);
    }
    const boundary = xForTime(live.dt + INTERVAL);
    if (boundary > plot.left && boundary < plot.right) {
      ctx.fillStyle = '#64798e';
      ctx.textAlign = 'left';
      ctx.fillText('未来计划区', boundary + 8, plot.top + 16);
    }
  }

  function displayPoints(object) {
    const points = object.points.map(point => ({...point}));
    if ((object.kind === 'trend' || object.kind === 'reference') && object.extend) {
      const [a, b] = points;
      if (b.time !== a.time) {
        const ratio = (view.end + INTERVAL * 20 - a.time) / (b.time - a.time);
        points[1] = {time: view.end + INTERVAL * 20, price: a.price + (b.price - a.price) * ratio};
      }
    }
    return points;
  }

  function drawLine(parent, a, b, object, extra = {}) {
    parent.appendChild(svgNode('line', {
      x1: xForTime(a.time), y1: yForPrice(a.price), x2: xForTime(b.time), y2: yForPrice(b.price),
      stroke: object.color, 'stroke-width': object.width,
      'stroke-dasharray': object.line_style === 'dashed' ? '8 5' : 'none',
      'stroke-linecap': 'round', ...extra
    }));
  }

  function renderObject(object, isDraft = false) {
    if (!object.visible && !isDraft) return;
    const wholeSelected = object.id === selectedId && (object.kind !== 'path' || selectedPointIndex === null || selectedPointIndex === -1);
    const group = svgNode('g', {class: 'drawing-object' + (wholeSelected ? ' selected' : '') + (isDraft ? ' draft-object' : ''), 'data-id': object.id});
    const points = displayPoints(object);
    if (object.kind === 'horizontal') {
      const y = yForPrice(points[0].price);
      group.appendChild(svgNode('line', {x1: plot.left, y1: y, x2: plot.right, y2: y,
        stroke: object.color, 'stroke-width': object.width, 'data-infinite': 'both',
        'stroke-dasharray': object.line_style === 'dashed' ? '8 5' : 'none'}));
      group.appendChild(svgNode('polygon', {points: `${plot.left},${y} ${plot.left + 8},${y - 4} ${plot.left + 8},${y + 4}`, fill: object.color}));
      group.appendChild(svgNode('polygon', {points: `${plot.right},${y} ${plot.right - 8},${y - 4} ${plot.right - 8},${y + 4}`, fill: object.color}));
      group.appendChild(svgNode('text', {x: plot.right + 6, y: y - 6, fill: object.color, 'font-size': 10}, points[0].price.toFixed(1)));
    } else if (object.kind === 'text') {
      const point = points[0], x = xForTime(point.time), y = yForPrice(point.price);
      const left = object.text_side === 'left';
      group.appendChild(svgNode('circle', {class: 'text-anchor-dot', cx: x, cy: y, r: 3.5, fill: object.color}));
      group.appendChild(svgNode('text', {x: x + (left ? -11 : 11), y, fill: object.color, 'dominant-baseline': 'middle',
        'text-anchor': left ? 'end' : 'start', 'font-size': 13, 'font-weight': 500,
        'paint-order': 'stroke', stroke: '#0a111a', 'stroke-width': 3},
        object.note || object.name || '文本注记'));
    } else {
      for (let i = 1; i < points.length; i += 1) drawLine(group, points[i - 1], points[i], object);
      if (object.kind === 'path') {
        points.forEach((point, index) => {
          const x = xForTime(point.time), y = yForPrice(point.price);
          group.appendChild(svgNode('circle', {class: 'path-node', cx: x, cy: y, r: 8,
            fill: '#0a111a', stroke: object.color, 'stroke-width': 1.5}));
          group.appendChild(svgNode('text', {class: 'path-number', x, y, dy: '0.32em', fill: object.color,
            'font-size': 9, 'font-weight': 600, 'text-anchor': 'middle'}, String(index + 1)));
        });
      }
    }
    svg.appendChild(group);
    if (object.id === selectedId && !isDraft) {
      let handleEntries = object.points.map((point, index) => ({point, index}));
      if (object.kind === 'horizontal' && object.points[0].time === null) {
        handleEntries = [{point: {time: timeForX((plot.left + plot.right) / 2), price: object.points[0].price}, index: 0}];
      } else if (object.kind === 'path' && selectedPointIndex === null) {
        handleEntries = [];
      } else if (object.kind === 'path' && selectedPointIndex >= 0) {
        handleEntries = handleEntries.filter(entry => entry.index === selectedPointIndex);
      }
      handleEntries.forEach(({point, index}) => {
        svg.appendChild(svgNode('circle', {class: 'anchor-handle' + (object.kind === 'path' ? ' path-handle' : ''),
          cx: xForTime(point.time), cy: yForPrice(point.price),
          r: object.kind === 'path' ? 11 : 5, fill: object.kind === 'path' ? 'transparent' : '#0c1520', 'data-anchor': index}));
      });
    }
  }

  function drawObjects() {
    svg.replaceChildren();
    const visibleObjects = doc.objects.filter(object => object.period === chartPeriod || object.period === 'all');
    for (const object of visibleObjects) renderObject(object);
    if (draft && activePeriod === chartPeriod) renderObject(draft, true);
    if (pathDraft && activePeriod === chartPeriod) {
      renderObject(pathDraft, true);
      if (crosshair && insidePlot(crosshair)) {
        const points = pathDraft.points;
        const last = points[points.length - 1];
        const anchor = anchorAt(crosshair);
        drawLine(svg, last, anchor, {...pathDraft, line_style: 'dashed', width: 1});
      }
    }
    if (crosshair && insidePlot(crosshair) && activePeriod === chartPeriod) drawCrosshair();
    $('empty-chart-tip-' + chartPeriod).hidden = visibleObjects.length > 0 || Boolean(pathDraft) || tool !== 'select';
  }

  function drawCrosshair() {
    const anchor = anchorAt(crosshair);
    const x = xForTime(anchor.time), y = yForPrice(anchor.price);
    const group = svgNode('g', {class: 'crosshair'});
    group.appendChild(svgNode('line', {x1: x, y1: plot.top, x2: x, y2: plot.bottom, stroke: '#7790a866', 'stroke-dasharray': '3 4'}));
    group.appendChild(svgNode('line', {x1: plot.left, y1: y, x2: plot.right, y2: y, stroke: '#7790a866', 'stroke-dasharray': '3 4'}));
    group.appendChild(svgNode('text', {x: Math.min(plot.right - 120, x + 8), y: Math.max(plot.top + 10, y - 10), fill: '#9cb0c3', 'font-size': 10},
      `${new Date(anchor.time).toLocaleTimeString('zh-CN', {hour12: false})} · ${anchor.price.toFixed(1)}`));
    svg.appendChild(group);
  }

  function eventPoint(event) {
    const rect = host.getBoundingClientRect();
    return {x: event.clientX - rect.left, y: event.clientY - rect.top};
  }

  function anchorAt(pos) {
    let time = timeForX(pos.x), price = priceForY(pos.y);
    if ($('snap').checked) {
      price = Math.round(price / TICK) * TICK;
      let nearestBar = null;
      for (const bar of bars) {
        const distance = Math.abs(xForTime(bar.dt + INTERVAL / 2) - pos.x);
        if (distance <= 9 && (!nearestBar || distance < nearestBar.distance)) nearestBar = {bar, distance};
      }
      time = nearestBar ? nearestBar.bar.dt + INTERVAL / 2
        : Math.round((time - INTERVAL / 2) / INTERVAL) * INTERVAL + INTERVAL / 2;
      let nearest = null;
      if (nearestBar) {
        for (const value of [nearestBar.bar.open, nearestBar.bar.high, nearestBar.bar.low, nearestBar.bar.close]) {
          const distance = Math.abs(yForPrice(value) - pos.y);
          if (distance <= 9 && (!nearest || distance < nearest.distance)) nearest = {value, distance};
        }
      }
      if (nearest) price = nearest.value;
    }
    return {time, price: Math.max(TICK, price)};
  }

  function objectSegments(object) {
    const points = displayPoints(object);
    if (object.kind === 'horizontal') {
      return [{a: {x: plot.left, y: yForPrice(points[0].price)}, b: {x: plot.right, y: yForPrice(points[0].price)}}];
    }
    if (object.kind === 'text') return [];
    return points.slice(1).map((point, index) => ({
      a: {x: xForTime(points[index].time), y: yForPrice(points[index].price)},
      b: {x: xForTime(point.time), y: yForPrice(point.price)}
    }));
  }

  function hitTest(pos) {
    const selected = doc.objects.find(object => object.id === selectedId && (object.period === chartPeriod || object.period === 'all'));
    if (selected) {
      const points = selected.kind === 'horizontal' && selected.points[0].time === null
        ? [{time: timeForX((plot.left + plot.right) / 2), price: selected.points[0].price}] : selected.points;
      for (let i = 0; i < points.length; i += 1) {
        if (Math.hypot(pos.x - xForTime(points[i].time), pos.y - yForPrice(points[i].price)) <= 10) return {object: selected, anchor: i};
      }
    }
    for (const object of [...doc.objects].reverse()) {
      if (!object.visible || (object.period !== chartPeriod && object.period !== 'all')) continue;
      if (object.kind === 'text') {
        const point = object.points[0], x = xForTime(point.time), y = yForPrice(point.price);
        const left = object.text_side === 'left';
        const textWidth = Math.max(70, (object.note || object.name).length * 14);
        const withinX = left ? pos.x <= x + 5 && pos.x >= x - textWidth : pos.x >= x - 5 && pos.x <= x + textWidth;
        if (withinX && Math.abs(pos.y - y) <= 13) return {object, anchor: null};
      } else if (objectSegments(object).some(segment => Model.distanceToSegment(pos, segment.a, segment.b) <= 8)) {
        return {object, anchor: null};
      }
    }
    return null;
  }

  function selectedObject() { return doc.objects.find(object => object.id === selectedId) || null; }

  function selectObject(id, pointIndex = null) {
    selectedId = id;
    selectedPointIndex = pointIndex;
    refreshInspector();
    scheduleRender();
  }

  function commitDocument(next) {
    doc = {...history.commit(next), symbol: currentSymbol};
    if (selectedId && !doc.objects.some(object => object.id === selectedId)) selectedId = null;
    scheduleSave();
    refreshSymbolOptions();
    refreshInspector();
    scheduleRender();
  }

  const sharedPlans = {};
  function refreshSymbolOptions() {
    const select = $('symbol-select');
    if (document.activeElement === select) return;
    select.replaceChildren();
    for (const item of SYMBOLS) {
      const candidate = sharedPlans[item.symbol] || (item.symbol === currentSymbol ? doc : loadDocument(item.symbol));
      const marked = candidate.objects.length > 0 || candidate.notes.trim().length > 0;
      const option = document.createElement('option');
      option.value = item.symbol;
      option.textContent = (marked ? '★ ' : '') + item.name + ' ' + item.symbol;
      select.appendChild(option);
    }
    select.value = currentSymbol;
  }

  async function refreshSharedStars() {
    await Promise.all(SYMBOLS.map(async item => {
      try {
        const response = await fetch('/api/board-demo/plans/' + encodeURIComponent(item.symbol), {cache: 'no-store'});
        if (response.ok) sharedPlans[item.symbol] = (await response.json()).plan;
      } catch (_) {}
    }));
    refreshSymbolOptions();
  }

  async function switchSymbol(symbol) {
    if (switching || !SYMBOLS.some(item => item.symbol === symbol) || symbol === currentSymbol) {
      $('symbol-select').value = currentSymbol;
      return;
    }
    saveNow();
    switching = true;
    ++syncGeneration;
    if (eventSource) eventSource.close();
    $('symbol-select').disabled = true;
    for (const period of Object.keys(PERIODS)) $('chart-loading-' + period).hidden = false;
    try {
      const prepared = {};
      await Promise.all(Object.keys(PERIODS).map((period, index) => new Promise(resolve => {
        setTimeout(() => {
          const nextSeed = symbolConfig(symbol).seed + index * 100003;
          seed = nextSeed;
          prepared[period] = {bars: createBars(96, symbol, period), seed: nextSeed};
          resolve();
        }, 400 + index * 250);
      })));
      currentSymbol = symbol;
      localStorage.setItem(LAST_SYMBOL_KEY, symbol);
      doc = loadDocument(symbol);
      history = new Model.PlanHistory(doc);
      selectedId = null;
      selectedPointIndex = null;
      draft = null;
      pathDraft = null;
      drag = null;
      crosshair = null;
      for (const period of Object.keys(PERIODS)) {
        charts[period].bars = prepared[period].bars;
        charts[period].seed = prepared[period].seed;
        charts[period].lastRollAt = Date.now();
        charts[period].follow = false;
        charts[period].zoomX = 100;
        charts[period].zoomY = 100;
        charts[period].view = {start: 0, end: 0, min: 0, max: 1};
        withChart(period, resetView);
      }
      useChart(activePeriod);
      setTool('select');
      refreshSymbolOptions();
      refreshInspector();
      activateChart(activePeriod);
      connectShared();
    } finally {
      switching = false;
      $('symbol-select').disabled = false;
      for (const period of Object.keys(PERIODS)) $('chart-loading-' + period).hidden = true;
    }
  }

  function changeSelected(patch) {
    if (!selectedId) return;
    commitDocument(Model.updateObject(doc, selectedId, patch));
  }

  function replaceLocalObject(object) {
    doc = {...doc, objects: doc.objects.map(item => item.id === object.id ? object : item)};
    scheduleRender();
  }

  function setColor(color, applyToSelection = true) {
    activeStyle.color = color;
    $('custom-color').value = color;
    document.querySelectorAll('.swatch').forEach(button => button.classList.toggle('active', button.dataset.color.toLowerCase() === color.toLowerCase()));
    if (applyToSelection && selectedId) changeSelected({color});
  }

  const TOOL_HELP = {
    select: 'V 选择 · 单击选中，拖动移动，控制点调整锚点',
    pan: 'B 平移 · 拖动图表，滚轮缩放时间范围',
    horizontal: 'H 水平线 · 在目标价格单击',
    trend: 'T 趋势线 · 按住拖动两个锚点',
    reference: 'R 参考线 · 按住拖动两个锚点，默认黄色虚线',
    path: 'P 计划路径 · 逐点单击，双击或 Enter 完成，Esc 取消',
    text: 'A 文本 · 单击位置后输入注记'
  };

  function setTool(next) {
    tool = next;
    draft = null;
    if (next !== 'path') pathDraft = null;
    document.querySelectorAll('.tool[data-tool]').forEach(button => button.classList.toggle('active', button.dataset.tool === next));
    document.querySelectorAll('.chart-host svg').forEach(layer => { layer.dataset.tool = next; });
    $('tool-help').textContent = TOOL_HELP[next];
    if (Model.TOOL_DEFAULTS[next]) {
      const defaults = Model.TOOL_DEFAULTS[next];
      setColor(defaults.color, false);
      activeStyle.line_style = defaults.line_style;
      $('default-style').value = defaults.line_style;
    }
    scheduleRender();
  }

  function refreshInspector() {
    const activeInput = document.activeElement ? document.activeElement.id : '';
    const selectionChanged = lastInspectorSelection !== selectedId;
    lastInspectorSelection = selectedId;
    if (activeInput !== 'plan-title') $('plan-title').value = doc.title;
    if (activeInput !== 'plan-notes') $('plan-notes').value = doc.notes;
    $('object-count').textContent = doc.objects.length + ' 项';
    $('undo').disabled = !history.canUndo();
    $('redo').disabled = !history.canRedo();
    $('duplicate').disabled = !selectedObject();
    const list = $('object-list');
    list.replaceChildren();
    if (!doc.objects.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-list';
      empty.textContent = '尚未绘制内容';
      list.appendChild(empty);
    }
    const periodNames = {day: '日线', m3: '3分钟', m30: '30分钟', all: '跨周期'};
    let previousPeriod = '';
    const orderedObjects = [...doc.objects].sort((a, b) => ['day', 'm3', 'm30', 'all'].indexOf(a.period) - ['day', 'm3', 'm30', 'all'].indexOf(b.period));
    for (const object of orderedObjects) {
      if (object.period !== previousPeriod) {
        previousPeriod = object.period;
        const heading = document.createElement('div');
        heading.className = 'object-period-heading';
        heading.textContent = periodNames[previousPeriod];
        list.appendChild(heading);
      }
      const row = document.createElement('div');
      row.className = 'object-item' + (object.id === selectedId ? ' selected' : '') + (object.visible ? '' : ' hidden-object');
      const color = document.createElement('i');
      color.className = 'object-color';
      color.style.setProperty('--object-color', object.color);
      const name = document.createElement('span');
      name.className = 'object-name';
      name.textContent = Model.objectLabel(object);
      name.title = object.note || Model.objectLabel(object);
      name.onclick = () => selectObject(object.id);
      const badge = document.createElement('small');
      badge.className = 'object-period-badge';
      badge.textContent = periodNames[object.period];
      name.appendChild(badge);
      const actions = document.createElement('span');
      actions.className = 'object-actions';
      const visibility = document.createElement('button');
      visibility.className = 'icon-button';
      visibility.title = object.visible ? '隐藏' : '显示';
      visibility.textContent = object.visible ? '隐' : '显';
      visibility.onclick = () => commitDocument(Model.updateObject(doc, object.id, {visible: !object.visible}));
      const remove = document.createElement('button');
      remove.className = 'icon-button';
      remove.title = '删除';
      remove.textContent = '×';
      remove.onclick = () => commitDocument(Model.removeObject(doc, object.id));
      actions.append(visibility, remove);
      row.append(color, name, actions);
      list.appendChild(row);
    }
    const object = selectedObject();
    $('no-selection').hidden = Boolean(object);
    $('property-fields').hidden = !object;
    $('selected-kind').textContent = object ? Model.LABELS[object.kind] : '未选择';
    if (object) {
      if (selectionChanged || activeInput !== 'object-name') $('object-name').value = object.name;
      $('object-color').value = object.color;
      $('object-width').value = object.width;
      $('object-line-style').value = object.line_style;
      $('object-extend').checked = object.extend;
      $('object-extend').disabled = !['trend', 'reference'].includes(object.kind);
      $('extend-row').hidden = !['trend', 'reference'].includes(object.kind);
      $('all-periods-row').hidden = !['horizontal', 'trend', 'reference'].includes(object.kind);
      $('object-all-periods').checked = object.period === 'all';
      $('object-all-periods')._originPeriod = object.period === 'all' ? activePeriod : object.period;
      $('line-style-row').hidden = object.kind === 'text';
      $('text-side-row').hidden = object.kind !== 'text';
      $('object-text-side').value = object.text_side;
      if (selectionChanged || activeInput !== 'object-note') $('object-note').value = object.note;
      $('object-width').disabled = object.kind === 'text';
      $('object-line-style').disabled = object.kind === 'text';
    }
  }

  function finishPath() {
    if (!pathDraft) return;
    const points = pathDraft.points.filter((point, index, all) => index === 0 || point.time !== all[index - 1].time || point.price !== all[index - 1].price);
    if (points.length >= 2) {
      const object = Model.createObject('path', points, {...activeStyle, period: activePeriod, name: '计划路径'});
      commitDocument(Model.addObject(doc, object));
      selectObject(object.id);
    }
    pathDraft = null;
    scheduleRender();
  }

  function nearestPathPoint(object, pos) {
    let nearest = 0, distance = Infinity;
    object.points.forEach((point, index) => {
      const current = Math.hypot(pos.x - xForTime(point.time), pos.y - yForPrice(point.price));
      if (current < distance) {
        nearest = index;
        distance = current;
      }
    });
    return nearest;
  }

  function creationPeriod(kind) {
    return ['horizontal', 'trend', 'reference'].includes(kind) && $('map-all-periods').checked ? 'all' : activePeriod;
  }

  function isDoublePathClick(object, pos) {
    const now = performance.now();
    const doubleClick = Boolean(lastPathClick && lastPathClick.id === object.id && lastPathClick.period === activePeriod
      && now - lastPathClick.time <= 400 && Math.hypot(pos.x - lastPathClick.x, pos.y - lastPathClick.y) <= 14);
    lastPathClick = doubleClick ? null : {id: object.id, period: activePeriod, time: now, x: pos.x, y: pos.y};
    return doubleClick;
  }

  function pointerDown(event) {
    if (event.button !== 0) return;
    closeContextMenu();
    const pos = eventPoint(event);
    if (!insidePlot(pos)) return;
    svg.setPointerCapture(event.pointerId);
    const anchor = anchorAt(pos);
    if (tool === 'pan') {
      follow = false;
      $('follow').checked = false;
      drag = {type: 'pan', start: pos, view: {...view}};
      return;
    }
    if (tool === 'select') {
      const hit = hitTest(pos);
      if (!hit) {
        selectObject(null);
        return;
      }
      if (hit.object.kind === 'path') {
        if (isDoublePathClick(hit.object, pos)) {
          selectObject(hit.object.id, -1);
          drag = {type: 'move', object: Model.clone(hit.object), start: pos, startAnchor: anchor, moved: false};
        } else {
          const pointIndex = hit.anchor === null ? nearestPathPoint(hit.object, pos) : hit.anchor;
          selectObject(hit.object.id, pointIndex);
          drag = {type: 'anchor', object: Model.clone(hit.object), anchorIndex: pointIndex, start: pos, startAnchor: anchor, moved: false};
        }
      } else {
        selectObject(hit.object.id);
        drag = {type: hit.anchor === null ? 'move' : 'anchor', object: Model.clone(hit.object),
          anchorIndex: hit.anchor, start: pos, startAnchor: anchor, moved: false};
      }
      return;
    }
    follow = false;
    $('follow').checked = false;
    if (tool === 'horizontal') {
      const object = Model.createObject('horizontal', [{time: anchor.time, price: anchor.price}], {...activeStyle, period: creationPeriod(tool), name: '水平线'});
      commitDocument(Model.addObject(doc, object));
      selectObject(object.id);
    } else if (tool === 'trend' || tool === 'reference') {
      draft = Model.createObject(tool, [anchor, anchor], {...activeStyle, period: creationPeriod(tool), name: Model.LABELS[tool]});
      drag = {type: 'draw', start: pos};
    } else if (tool === 'path') {
      if (!pathDraft) {
        pathDraft = Model.createObject('path', [anchor, anchor], {...activeStyle, period: activePeriod, name: '计划路径'});
        pathDraft.points = [anchor];
      } else {
        pathDraft.points.push(anchor);
      }
      scheduleRender();
    } else if (tool === 'text') {
      const value = globalThis.prompt('输入计划注记', '');
      if (value && value.trim()) {
        const object = Model.createObject('text', [anchor], {...activeStyle, period: activePeriod, name: value.trim().slice(0, 30), note: value.trim()});
        commitDocument(Model.addObject(doc, object));
        selectObject(object.id);
      }
    }
  }

  function pointerMove(event) {
    const pos = eventPoint(event);
    crosshair = pos;
    if (insidePlot(pos)) {
      const anchor = anchorAt(pos);
      $('cursor-readout').textContent = `时间 ${new Date(anchor.time).toLocaleTimeString('zh-CN', {hour12: false})} · 价格 ${anchor.price.toFixed(1)}`;
    } else {
      $('cursor-readout').textContent = '时间 — · 价格 —';
    }
    if (!drag) {
      scheduleRender();
      return;
    }
    if (drag.type === 'pan') {
      const span = drag.view.end - drag.view.start;
      const deltaTime = -(pos.x - drag.start.x) / plot.width * span;
      const deltaPrice = (pos.y - drag.start.y) / plot.height * (drag.view.max - drag.view.min);
      view = {start: drag.view.start + deltaTime, end: drag.view.end + deltaTime,
        min: drag.view.min + deltaPrice, max: drag.view.max + deltaPrice};
      saveChart(activePeriod);
    }
    if ((drag.type === 'move' || drag.type === 'anchor') && Math.hypot(pos.x - drag.start.x, pos.y - drag.start.y) <= 3) {
      scheduleRender();
      return;
    }
    if (drag.type === 'draw' && draft) {
      draft.points[1] = anchorAt(pos);
    } else if (drag.type === 'move') {
      let deltaTime = (pos.x - drag.start.x) / plot.width * (view.end - view.start);
      let deltaPrice = -(pos.y - drag.start.y) / plot.height * (view.max - view.min);
      if ($('snap').checked) {
        deltaTime = Math.round(deltaTime / INTERVAL) * INTERVAL;
        deltaPrice = Math.round(deltaPrice / TICK) * TICK;
      }
      const moved = Model.translateObject(drag.object, deltaTime, deltaPrice);
      if (moved) replaceLocalObject(moved);
      drag.moved = true;
    } else if (drag.type === 'anchor') {
      const current = doc.objects.find(object => object.id === drag.object.id);
      const point = anchorAt(pos);
      const moved = Model.moveAnchor(current, drag.anchorIndex, point);
      if (moved) replaceLocalObject(moved);
      drag.moved = true;
    }
    scheduleRender();
  }

  function pointerUp(event) {
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    if (!drag) return;
    const finished = drag;
    if (finished.moved) lastPathClick = null;
    drag = null;
    if (finished.type === 'draw' && draft) {
      const [a, b] = draft.points;
      if (a.time !== b.time || a.price !== b.price) {
        commitDocument(Model.addObject(doc, draft));
        selectObject(draft.id);
      }
      draft = null;
    } else if ((finished.type === 'move' || finished.type === 'anchor') && finished.moved) {
      commitDocument({...doc, revision: doc.revision + 1});
    }
    scheduleRender();
  }

  function closeContextMenu() {
    $('context-menu').hidden = true;
    contextTargetId = null;
  }

  function openContextMenu(object, event) {
    contextTargetId = object.id;
    const menu = $('context-menu');
    menu.hidden = false;
    const left = Math.min(event.clientX, globalThis.innerWidth - 185);
    const top = Math.min(event.clientY, globalThis.innerHeight - 90);
    menu.style.left = Math.max(4, left) + 'px';
    menu.style.top = Math.max(4, top) + 'px';
  }

  function adjustPathPoints(delta) {
    const object = selectedObject();
    if (!object || object.kind !== 'path') return;
    const index = selectedPointIndex === null || selectedPointIndex < 0
      ? object.points.length - 1 : Math.min(selectedPointIndex, object.points.length - 1);
    const points = Model.clone(object.points);
    if (delta > 0) {
      let point;
      if (index < points.length - 1) {
        const next = points[index + 1];
        point = {time: (points[index].time + next.time) / 2, price: (points[index].price + next.price) / 2};
      } else {
        const previous = points[index - 1];
        point = {time: points[index].time + (points[index].time - previous.time || INTERVAL),
          price: points[index].price + (points[index].price - previous.price || TICK)};
      }
      points.splice(index + 1, 0, point);
      commitDocument(Model.updateObject(doc, object.id, {points}));
      selectObject(object.id, index + 1);
    } else {
      if (points.length <= 2) {
        $('tool-help').textContent = '计划路径至少保留 2 个点';
        return;
      }
      points.splice(index, 1);
      commitDocument(Model.updateObject(doc, object.id, {points}));
      selectObject(object.id, Math.max(0, index - 1));
    }
  }

  function cancelInteraction() {
    closeContextMenu();
    if (pathDraft) {
      const points = pathDraft.points.filter((point, index, all) => index === 0 || point.time !== all[index - 1].time || point.price !== all[index - 1].price);
      if (points.length >= 2) {
        const object = Model.createObject('path', points, {...activeStyle, period: activePeriod, name: '计划路径'});
        commitDocument(Model.addObject(doc, object));
        selectObject(object.id);
      }
    }
    if (draft && (draft.points[0].time !== draft.points[1].time || draft.points[0].price !== draft.points[1].price)) {
      commitDocument(Model.addObject(doc, draft));
    }
    if (drag && (drag.type === 'move' || drag.type === 'anchor') && drag.moved) {
      commitDocument({...doc, revision: doc.revision + 1});
    }
    drag = null;
    draft = null;
    pathDraft = null;
    setTool('select');
    scheduleRender();
  }

  function abortPointerInteraction() {
    closeContextMenu();
    if (drag && (drag.type === 'move' || drag.type === 'anchor')) doc = history.present;
    drag = null;
    draft = null;
    pathDraft = null;
    setTool('select');
    scheduleRender();
  }

  function undo() {
    doc = history.undo();
    if (selectedId && !doc.objects.some(object => object.id === selectedId)) selectedId = null;
    scheduleSave();
    refreshSymbolOptions();
    refreshInspector();
    scheduleRender();
  }

  function redo() {
    doc = history.redo();
    scheduleSave();
    refreshSymbolOptions();
    refreshInspector();
    scheduleRender();
  }

  function exportPlan() {
    saveNow();
    const blob = new Blob([JSON.stringify(doc, null, 2)], {type: 'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = (doc.title.replace(/[\\/:*?"<>|]/g, '-') || '计划画板') + '.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  document.querySelectorAll('[data-tool]').forEach(button => button.addEventListener('click', () => setTool(button.dataset.tool)));
  document.querySelectorAll('.swatch').forEach(button => button.addEventListener('click', () => setColor(button.dataset.color)));
  $('custom-color').addEventListener('input', event => setColor(event.target.value));
  $('default-width').addEventListener('change', event => { activeStyle.width = Number(event.target.value); });
  $('default-style').addEventListener('change', event => { activeStyle.line_style = event.target.value; });
  $('follow').addEventListener('change', event => {
    follow = event.target.checked;
    if (follow) {
      const span = view.end - view.start;
      view.end = bars[bars.length - 1].dt + INTERVAL * 18;
      view.start = view.end - span;
      fitPrices(false);
    }
    saveChart(activePeriod);
    scheduleRender();
  });
  $('reset-view').addEventListener('click', resetAllViews);
  $('undo').addEventListener('click', undo);
  $('redo').addEventListener('click', redo);
  $('duplicate').addEventListener('click', () => {
    const object = selectedObject();
    if (!object) return;
    const copy = Model.duplicateObject(object, INTERVAL * 2, TICK * 2);
    commitDocument(Model.addObject(doc, copy));
    selectObject(copy.id);
  });
  $('clear-board').addEventListener('click', () => {
    if (doc.objects.length && globalThis.confirm('清空当前画板？可通过撤销恢复。')) commitDocument({...doc, revision: doc.revision + 1, objects: []});
  });
  $('export-plan').addEventListener('click', exportPlan);
  $('plan-title').addEventListener('change', event => commitDocument({...doc, revision: doc.revision + 1, title: event.target.value}));
  $('plan-notes').addEventListener('change', event => commitDocument({...doc, revision: doc.revision + 1, notes: event.target.value}));
  $('object-name').addEventListener('change', event => changeSelected({name: event.target.value}));
  $('object-color').addEventListener('input', event => { setColor(event.target.value, false); changeSelected({color: event.target.value}); });
  $('object-width').addEventListener('change', event => changeSelected({width: Number(event.target.value)}));
  $('object-line-style').addEventListener('change', event => changeSelected({line_style: event.target.value}));
  $('object-extend').addEventListener('change', event => changeSelected({extend: event.target.checked}));
  $('object-all-periods').addEventListener('change', event => {
    changeSelected({period: event.target.checked ? 'all' : event.target._originPeriod || activePeriod});
  });
  $('object-text-side').addEventListener('change', event => changeSelected({text_side: event.target.value}));
  $('object-note').addEventListener('change', event => changeSelected({note: event.target.value}));
  function bindZoomSlider(input, axis) {
    input.addEventListener('pointerdown', () => { input._view = {...view}; });
    input.addEventListener('input', () => {
      const base = input._view || {...view};
      input._view = base;
      const scale = 100 / Number(input.value);
      if (axis === 'x') {
        const center = (base.start + base.end) / 2, span = (base.end - base.start) * scale;
        view.start = center - span / 2;
        view.end = center + span / 2;
        charts[activePeriod].zoomX = Number(input.value);
        $('zoom-value').textContent = input.value + '%';
      } else {
        const center = (base.min + base.max) / 2, span = (base.max - base.min) * scale;
        view.min = Math.max(1, center - span / 2);
        view.max = center + span / 2;
        charts[activePeriod].zoomY = Number(input.value);
        $('zoom-y-value').textContent = input.value + '%';
      }
      follow = false;
      $('follow').checked = false;
      saveChart(activePeriod);
      scheduleRender();
    });
    input.addEventListener('change', () => { input._view = null; });
  }
  bindZoomSlider($('zoom-range'), 'x');
  bindZoomSlider($('zoom-y-range'), 'y');
  $('context-delete').addEventListener('click', () => {
    if (contextTargetId) commitDocument(Model.removeObject(doc, contextTargetId));
    closeContextMenu();
  });
  $('context-delete-all').addEventListener('click', () => {
    closeContextMenu();
    if (globalThis.confirm('确定删除全部三视图对象？此操作可通过撤销恢复。')) commitDocument({...doc, revision: doc.revision + 1, objects: []});
  });
  for (const period of Object.keys(PERIODS)) {
    const layer = $('drawing-layer-' + period);
    layer.addEventListener('pointerdown', event => { activateChart(period); pointerDown(event); });
    layer.addEventListener('pointermove', event => {
      if (activePeriod !== period && !drag) return;
      pointerMove(event);
    });
    layer.addEventListener('pointerup', pointerUp);
    layer.addEventListener('pointercancel', abortPointerInteraction);
    layer.addEventListener('pointerleave', () => { if (!drag) { crosshair = null; scheduleRender(); } });
    layer.addEventListener('dblclick', event => { if (tool === 'path') { event.preventDefault(); finishPath(); } });
    layer.addEventListener('contextmenu', event => {
      event.preventDefault();
      activateChart(period);
      const hit = hitTest(eventPoint(event));
      if (hit) {
        openContextMenu(hit.object, event);
        return;
      }
      if (tool === 'path') finishPath();
    });
    layer.addEventListener('wheel', event => {
      event.preventDefault();
      activateChart(period);
      const pos = eventPoint(event);
      if (!insidePlot(pos)) return;
      follow = false;
      $('follow').checked = false;
      const time = timeForX(pos.x);
      const ratio = Math.exp(event.deltaY * .001);
      const current = view.end - view.start;
      const next = Math.max(INTERVAL * 12, Math.min(INTERVAL * 400, current * ratio));
      const scale = next / current;
      view.start = time - (time - view.start) * scale;
      view.end = time + (view.end - time) * scale;
      const price = priceForY(pos.y);
      view.min = Math.max(1, price - (price - view.min) * scale);
      view.max = price + (view.max - price) * scale;
      saveChart(period);
      scheduleRender();
    }, {passive: false});
  }
  document.addEventListener('pointerdown', event => {
    if (!$('context-menu').contains(event.target)) closeContextMenu();
  });
  $('symbol-select').addEventListener('change', event => switchSymbol(event.target.value));
  globalThis.addEventListener('resize', scheduleRender);
  globalThis.addEventListener('beforeunload', () => { if (dirty) saveNow(); });
  globalThis.addEventListener('keydown', event => {
    if (event.key.toLowerCase() === 'v' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      event.stopImmediatePropagation();
      cancelInteraction();
      return;
    }
    if (event.key === 'Escape') { event.preventDefault(); cancelInteraction(); return; }
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveNow(); return; }
    if (['+', '=', '-', '_'].includes(event.key)) { event.preventDefault(); adjustPathPoints(['+', '='].includes(event.key) ? 1 : -1); return; }
    if (event.key === 'Enter' && tool === 'path') { event.preventDefault(); finishPath(); return; }
    if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId) { event.preventDefault(); commitDocument(Model.removeObject(doc, selectedId)); return; }
    const keys = {b: 'pan', h: 'horizontal', t: 'trend', r: 'reference', p: 'path', a: 'text'};
    const next = keys[event.key.toLowerCase()];
    if (next) setTool(next);
  });

  refreshSymbolOptions();
  for (const period of Object.keys(PERIODS)) withChart(period, resetView);
  useChart(activePeriod);
  refreshInspector();
  setTool('select');
  activateChart(activePeriod);
  connectShared();
  refreshSharedStars();
  setInterval(refreshSharedStars, 15000);
  setInterval(updateMarket, 1000);
  scheduleRender();
}());
