(function (root) {
  'use strict';
  const PREFIX = 'silver-structure-note:v1:';
  let host = null, refs = {}, context = null, model = null, identityKey = '', saved = {}, dirty = false;
  let draftKey = '', draftSymbol = '';
  let storageError = '', transient = '';
  const fields = ['type', 'phase', 'stage', 'segment', 'intent', 'shelter', 'breakeven', 'reason'];
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const price = value => number(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '待定义';
  const money = value => number(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—';
  const text = value => value == null ? '' : String(value);
  const svgEscape = value => text(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const node = (tag, className, value) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value != null) item.textContent = value;
    return item;
  };
  function keyFor(identity) {
    if (!identity || !identity.symbol || !identity.structure_id) return '';
    return PREFIX + encodeURIComponent(identity.symbol) + ':' + encodeURIComponent(identity.structure_id);
  }
  function readNote(key) {
    storageError = '';
    if (!key) return {};
    try {
      const item = JSON.parse(root.localStorage.getItem(key) || '{}');
      return item && typeof item === 'object' && !Array.isArray(item) ? item : {};
    } catch (_) { storageError = '本地人工注记不可读取，当前未采用。'; return {}; }
  }
  function setMessage(message, error) {
    refs.message.textContent = message || '';
    refs.message.dataset.error = error ? 'true' : 'false';
  }
  function field(name, label, options, wide) {
    const wrapper = node('label', 'sb-field' + (wide ? ' sb-field-wide' : ''), label);
    const control = node(options ? 'select' : name === 'reason' ? 'textarea' : 'input');
    control.name = name;
    if (options) options.forEach(option => { const item = node('option', '', option.label); item.value = option.value; control.appendChild(item); });
    else if (name === 'reason') { control.rows = 2; control.maxLength = 400; control.placeholder = '记录结构依据、想拿的段与放弃的情形。'; }
    else { control.type = 'number'; control.min = '0.0001'; control.step = 'any'; control.placeholder = '可留空，不自动推定'; }
    wrapper.appendChild(control); refs[name] = control; return wrapper;
  }
  function hydrate() {
    fields.forEach(name => {
      const value = saved[name];
      refs[name].value = name === 'reason' ? text(value) : ['shelter', 'breakeven'].includes(name)
        ? number(value) ? value : '' : value || (name === 'intent' ? 'observe' : 'unknown');
    });
    dirty = false; draftKey = ''; draftSymbol = '';
  }
  function mount(element) {
    if (!element || typeof element.replaceChildren !== 'function') throw new Error('StructureBoard.mount 需要一个容器元素');
    host = element; refs = {}; identityKey = ''; saved = {}; dirty = false;
    host.classList.add('sb-board'); host.setAttribute('aria-label', '结构与节奏工作台');
    host.replaceChildren();
    const head = node('div', 'sb-head');
    head.appendChild(node('h2', '', '结构与节奏'));
    refs.symbol = node('span', 'sb-symbol'); refs.source = node('span', 'sb-source');
    refs.statuses = node('div', 'sb-statuses'); head.append(refs.symbol, refs.source, refs.statuses); host.appendChild(head);
    const content = node('div', 'sb-content'), chart = node('div', 'sb-diagram-panel');
    const chartTitle = node('div', 'sb-diagram-title'); chartTitle.appendChild(node('strong', '', '节奏示意 · 非实际K线编号'));
    refs.diagramDirection = node('span'); chartTitle.appendChild(refs.diagramDirection); chart.appendChild(chartTitle);
    refs.diagram = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    refs.diagram.setAttribute('class', 'sb-diagram'); refs.diagram.setAttribute('viewBox', '0 0 600 176');
    refs.diagram.setAttribute('role', 'img'); chart.appendChild(refs.diagram);
    refs.diagramEmpty = node('div', 'sb-diagram-empty'); chart.appendChild(refs.diagramEmpty);
    const legend = node('div', 'sb-diagram-caption');
    [['sb-current', '人工当前段'], ['sb-target', '教学顺势段'], ['sb-skip', '回撤放弃段']].forEach(([cls, label]) => {
      const item = node('span', cls); item.append(node('i'), document.createTextNode(label)); legend.appendChild(item);
    }); chart.appendChild(legend);
    const readout = node('div', 'sb-readout');
    refs.structure = node('div', 'sb-structure-title'); refs.phaseLabel = node('div', 'sb-phase');
    refs.numbering = node('div', 'sb-numbering'); refs.observation = node('div', 'sb-observation');
    const phaseRow = node('div', 'sb-phase-row'); phaseRow.append(refs.phaseLabel, refs.numbering);
    refs.next = node('div', 'sb-next');
    const intent = node('div', 'sb-intent');
    ['take', 'skip'].forEach((name, index) => { const box = node('div'); box.appendChild(node('span', 'sb-intent-label', index ? '放弃什么' : '想拿哪段'));
      refs[name] = node('div', 'sb-intent-value'); box.appendChild(refs[name]); intent.appendChild(box); });
    readout.append(refs.structure, phaseRow, refs.observation, refs.next, intent); content.append(chart, readout); host.appendChild(content);
    const levels = node('div', 'sb-levels');
    [['shelter', '结构掩体'], ['stop', '计划止损'], ['target', '参考目标'], ['breakeven', '平推触发价']].forEach(([name, label]) => {
      const card = node('div', 'sb-level'), heading = node('div', 'sb-level-label', label);
      const source = node('span', 'sb-level-source'), value = node('div', 'sb-level-value'), note = node('div', 'sb-level-note');
      heading.appendChild(source); card.append(heading, value, note); levels.appendChild(card); refs['level_' + name] = { value, note, source };
    }); host.appendChild(levels);
    refs.risk = node('div', 'sb-risk'); refs.warning = node('div', 'sb-warning');
    const riskRow = node('div', 'sb-risk-row'), jump = node('button', 'sb-button secondary sb-jump', '查看执行与风险核对 ↓');
    jump.type = 'button'; jump.addEventListener('click', () => {
      const target = document.getElementById('trade-workspace');
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    riskRow.append(refs.risk, jump);
    refs.warning.setAttribute('role', 'status'); host.append(riskRow, refs.warning);
    refs.details = node('details', 'sb-manual'); refs.details.appendChild(node('summary', '', '人工标注 · 结构阶段、当前段与交易意图'));
    refs.details.appendChild(node('p', 'sb-manual-note', '人工标注只属于当前品种与当前结构，不会修改程序确认条件、风险额度或交易记录。点位与阶段可分别保持待确认。'));
    refs.form = node('form'); const grid = node('div', 'sb-form-grid');
    const opts = root.StructureModel && root.StructureModel.OPTIONS || {};
    const unknown = [{ value: 'unknown', label: '待确认' }];
    grid.append(field('type', '人工结构类型', opts.types || unknown), field('phase', '人工观察阶段', opts.phases || unknown),
      field('stage', '人工点位编号', opts.stages || unknown), field('segment', '人工当前段', opts.segments || unknown),
      field('intent', '人工交易意图', opts.intents || [{ value: 'observe', label: '仅观察' }]),
      field('shelter', '人工掩体价'), field('breakeven', '人工平推触发价'), field('reason', '人工判断理由', null, true));
    refs.form.appendChild(grid); const actions = node('div', 'sb-actions');
    refs.save = node('button', 'sb-button', '保存人工标注'); refs.save.type = 'submit';
    refs.remove = node('button', 'sb-button secondary', '清除当前标注'); refs.remove.type = 'button';
    refs.message = node('span', 'sb-message'); refs.message.setAttribute('role', 'status');
    actions.append(refs.save, refs.remove, refs.message); refs.form.appendChild(actions);
    refs.form.appendChild(node('div', 'sb-disclaimer', '平推点仅表示观察/调整的触发价。具体保本止损及费用尚需另行核算，不自动等于开仓价。'));
    refs.details.appendChild(refs.form); host.appendChild(refs.details);
    refs.form.addEventListener('input', () => {
      if (!dirty) { draftKey = identityKey; draftSymbol = model && model.identity && model.identity.symbol || ''; }
      dirty = true; setMessage('有未保存的人工标注；行情刷新不会覆盖输入。', false);
    });
    refs.form.addEventListener('submit', saveNote); refs.remove.addEventListener('click', removeNote);
    if (context) update(context); else clear('等待结构数据；人工编号尚未确认。');
    return api;
  }
  function saveNote(event) {
    event.preventDefault();
    if (!refs.form.reportValidity() || !identityKey || !model || keyFor(model.identity) !== identityKey) {
      setMessage('当前结构身份尚未确认，不能保存人工编号。', true); return;
    }
    const next = { symbol: model.identity.symbol, structure_id: model.identity.structure_id, reviewed_at: Date.now(),
      reviewed_bar_time: model.freshness && model.freshness.completed_bar_time || null };
    fields.forEach(name => { const value = refs[name].value.trim(); next[name] = ['shelter', 'breakeven'].includes(name) ? value ? Number(value) : null : value; });
    try { root.localStorage.setItem(identityKey, JSON.stringify(next)); saved = next; dirty = false; draftKey = ''; draftSymbol = ''; transient = ''; storageError = ''; }
    catch (_) { setMessage('本地保存失败；输入仍保留，请检查浏览器存储权限。', true); return; }
    update(context); setMessage('已保存当前结构的人工标注；程序交易条件未改变。', false);
  }
  function removeNote() {
    if (!identityKey) return;
    try { root.localStorage.removeItem(identityKey); saved = {}; dirty = false; transient = ''; storageError = ''; }
    catch (_) { setMessage('清除失败，当前标注仍保留。', true); return; }
    hydrate(); update(context); setMessage('已清除当前结构的人工标注，编号恢复待确认。', false);
  }
  function statusChip(label, key) {
    const item = node('span', 'sb-status', label);
    item.dataset.tone = /error|invalid|block|stale|unavailable/.test(key || '') ? 'bad' : /pending|unknown|wait|insufficient|paused/.test(key || '') ? 'warn' : /ready|fresh|ok|confirmed/.test(key || '') ? 'good' : '';
    return item;
  }
  function drawDiagram(current) {
    const info = current.diagram || {}, direction = info.direction || (current.direction || {}).value || 'unknown';
    const known = direction === 'up' || direction === 'down';
    const manual = info.highlight_source === 'manual', segment = manual ? info.segment : 'unknown', stage = manual ? info.stage : 'unknown';
    const continuation = (current.manual || {}).applied && current.manual.intent === 'continuation';
    refs.diagramDirection.textContent = known ? (direction === 'up' ? '上涨节奏教学' : '下跌节奏教学') : '方向待确认';
    refs.diagramEmpty.textContent = known ? '编号仅为教学参照；当前段须人工标注，未选择策略时不表示要执行。' : '未知方向不推定上涨或下跌；先核对结构，再选择人工节奏标注。';
    const xs = [32, 108, 184, 260, 336, 412, 488, 564], up = [136, 91, 116, 65, 91, 39, 66, 16];
    const ys = known ? direction === 'up' ? up : up.map(y => 160 - y) : xs.map(() => 79);
    let svg = '<title>节奏示意，非实际K线编号；不表示概率、时间或自动交易信号</title>';
    for (let i = 0; i < 7; i++) {
      const selected = known && manual && segment === (i + 1) + '-' + (i + 2);
      const target = known && [2, 4, 6].includes(i), skip = known && [1, 3].includes(i);
      const color = selected ? '#9ec9f1' : target ? continuation ? '#e5bd70' : '#8e805e' : '#596d82';
      svg += '<line x1="' + xs[i] + '" y1="' + ys[i] + '" x2="' + xs[i + 1] + '" y2="' + ys[i + 1] +
        '" stroke="' + color + '" stroke-width="' + (selected ? 4 : target && continuation ? 2.8 : 1.7) + '"' +
        (!known || skip || target && !continuation ? ' stroke-dasharray="5 5"' : '') + '/>';
    }
    xs.forEach((x, index) => {
      const selected = known && manual && stage === 'point' + (index + 1);
      svg += '<circle cx="' + x + '" cy="' + ys[index] + '" r="' + (selected ? 12 : 9) + '" fill="' + (selected ? '#2d526e' : '#142231') + '" stroke="' + (selected ? '#9ec9f1' : '#657c94') + '"/>' +
        '<text x="' + x + '" y="' + (ys[index] + 3.8) + '" text-anchor="middle" fill="' + (known ? '#cfdae5' : '#8295aa') + '" font-size="11" font-family="Segoe UI, sans-serif">' + (index + 1) + '</text>';
    });
    if (known && manual && segment && segment !== 'unknown') svg += '<text x="300" y="173" text-anchor="middle" fill="#9ec9f1" font-size="10">人工当前段 ' + svgEscape(segment.replace('-', '→')) + '</text>';
    refs.diagram.innerHTML = svg;
    refs.diagram.setAttribute('aria-label', '节奏示意，非实际K线编号。' + refs.diagramDirection.textContent + '。' + (current.numbering || {}).label);
  }
  function showLevel(name, value, source, note) {
    const item = refs['level_' + name];
    item.value.textContent = price(value); item.value.classList.toggle('sb-undefined', !number(value));
    item.source.textContent = source === 'manual' ? '人工' : number(value) ? '规则参考' : '';
    item.note.textContent = note || '尚无可用参考，不自动补值。'; item.note.title = item.note.textContent;
  }
  function render(current) {
    const identity = current.identity || {}, structure = current.structure || {}, numbering = current.numbering || {};
    const observation = current.observation || {}, transition = current.transition || {}, plan = current.plan || {}, manual = current.manual || {};
    refs.symbol.textContent = [identity.symbol, identity.period ? Number(identity.period) / 60 + 'm' : ''].filter(Boolean).join(' · ');
    refs.source.textContent = structure.source_label || (structure.source === 'manual' ? '人工结构判断' : '程序规则观察 · 非文档编号');
    refs.structure.textContent = structure.label || '结构待确认'; refs.phaseLabel.textContent = structure.phase_label || '阶段待确认';
    refs.numbering.textContent = numbering.label || '点位编号待确认 · 尚无人工标注';
    refs.observation.textContent = [observation.label, observation.detail].filter(Boolean).join(' · ');
    refs.observation.title = [structure.summary, observation.detail].filter(Boolean).join(' · ');
    refs.next.replaceChildren(node('b', '', '下一证据'), document.createTextNode((transition.conditions || []).filter(Boolean).map(v => v.replace(/[。；]+$/, '')).join('；') || transition.label || plan.wait_for || '等待完成K线提供结构证据。'));
    const selected = manual.applied && manual.intent === 'continuation';
    refs.take.textContent = selected ? '人工选择顺势段：3→4 / 5→6 / 7→8' : '策略未选择 · 当前仅观察';
    refs.skip.textContent = selected ? '放弃回撤段 2→3 / 4→5；方向不清晰先等待。' : '待明确交易意图；不自动启用顺势策略。';
    refs.statuses.replaceChildren(); const statuses = current.statuses || {};
    [['data', '数据'], ['structure', '结构'], ['risk', '风险']].forEach(([name, title]) => {
      const item = statuses[name] || {}; refs.statuses.appendChild(statusChip(title + ' · ' + (item.label || '待核验'), item.key));
    });
    const levels = current.levels || {};
    ['shelter', 'stop'].forEach(name => { const item = levels[name] || {}; showLevel(name, item.price, item.source,
      item.label || (name === 'shelter' ? '掩体不等同于止损；尚待结构定义。' : '技术止损参考，不代表交易端已挂单。')); });
    showLevel('target', plan.target, 'rule', plan.target_basis || '目标空间待确认。');
    const breakeven = levels.breakeven || {};
    showLevel('breakeven', breakeven.price, breakeven.source, number(breakeven.price) ? '人工触发价；保本止损与费用另待核算。' : '触发条件待定义；不自动等于开仓价。');
    const risk = current.risk;
    refs.risk.textContent = risk && number(risk.equity) ? '风险单独核对 · 本金 ¥' + money(risk.equity) + ' · 本计划上限 ¥' + money(risk.max_loss ?? risk.budget) +
      ' · 剩余 ¥' + money(risk.remaining_budget) + ' · ' + (risk.rows || []).slice(0, 2).map(row => row.lots + '手 ¥' + money(row.loss) +
        (risk.fee_configured !== true ? '（费用待核算）' : row.allowed ? '（额度内）' : '（不可新增）')).join(' / ') : '风险待核验 · 人工结构判断不会改变账户预算或程序交易条件。';
    refs.warning.textContent = [transient, storageError, ...(current.warnings || [])].filter(Boolean).join(' · ');
    refs.save.disabled = !identityKey; refs.remove.disabled = !identityKey || !Object.keys(saved).length;
    if (!identityKey && !dirty) setMessage('结构身份待确认，暂不能保存人工编号。', true);
    drawDiagram(current);
  }
  function update(nextContext) {
    context = nextContext || {};
    if (!host) return;
    if (!root.StructureModel || typeof root.StructureModel.build !== 'function') {
      refs.structure.textContent = '结构模块尚未就绪'; refs.save.disabled = true; return;
    }
    const provisional = root.StructureModel.build(context, {}, Date.now()), nextKey = keyFor(provisional.identity);
    const nextSymbol = provisional.identity && provisional.identity.symbol || '';
    if (nextKey !== identityKey || !nextKey && dirty && nextSymbol !== draftSymbol) {
      const sameDraft = dirty && draftKey && (nextKey === draftKey || !nextKey && provisional.identity && provisional.identity.symbol === draftSymbol);
      transient = sameDraft ? '人工草稿已保留；结构身份有效时才可保存。' : identityKey || draftKey ? '结构或品种已变化，原人工编号未沿用。' : '';
      identityKey = nextKey; saved = readNote(identityKey);
      if (!sameDraft) hydrate();
      setMessage(transient, false);
    }
    model = root.StructureModel.build(context, saved, Date.now());
    render(model);
  }
  function clear(message, symbol) {
    context = { symbol: symbol || context && context.symbol || '', period: 1800, status: 'insufficient', message: message || '等待结构数据。' };
    if (!host) return;
    storageError = ''; update(context);
    refs.structure.textContent = message || '等待结构数据'; refs.phaseLabel.textContent = '阶段待确认';
    refs.numbering.textContent = '编号待确认；旧结构的人工编号未沿用。'; refs.save.disabled = true; refs.remove.disabled = true;
  }
  const api = { mount, update, clear };
  root.StructureBoard = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
