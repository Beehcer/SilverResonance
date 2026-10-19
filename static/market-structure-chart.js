(function (root) {
  'use strict';
  const colors = { up: '#ef737a', down: '#52c79c', range: '#dfb65e', neutral: '#a4b5cc' };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const price = value => finite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '—';
  const direction = scenario => colors[scenario.direction] ? scenario.direction : 'range';
  const shortLabels = { retest_down: '反抽受阻↓', retest_up: '回踩守住↑', direct_down: '直接下行↓', direct_up: '直接上行↑', pause_watch: '整理观察↔',
    reclaim_up: '收回原带↑', reclaim_down: '收回原带↓', range_watch: '参考带内↔',
    break_retest_up: '上破回踩↑', break_retest_down: '下破反弹↓' };
  function scenarios(analysis) {
    const market = analysis && analysis.market_structure;
    return ((market && market.status === 'ok' && Array.isArray(market.scenarios)) ? market.scenarios : analysis && analysis.scenarios || [])
      .slice(0, 4).filter(item => item && typeof item === 'object');
  }
  function drawablePath(scenario, currentPrice, market) {
    if (!scenario || scenario.drawable === false || ['invalid', 'target_passed', 'no_target'].includes(scenario.status)) return [];
    const path = scenario.path;
    if (!finite(currentPrice) || !Array.isArray(path) || path.length < 2 || !path.every(value => finite(value) && value > 0)) return [];
    const tolerance = Math.max(1e-7, Math.abs(currentPrice) * 1e-10);
    if (Math.abs(path[0] - currentPrice) > tolerance) return [];
    const last = path[path.length - 1];
    if (scenario.direction === 'down' && last >= currentPrice || scenario.direction === 'up' && last <= currentPrice) return [];
    if (scenario.direction === 'down' && last >= path[path.length - 2] || scenario.direction === 'up' && last <= path[path.length - 2]) return [];
    if (scenario.role === 'direct' && path.some((value, index) => index > 0 &&
      (scenario.direction === 'down' ? value > path[index - 1] : scenario.direction === 'up' && value < path[index - 1]))) return [];
    if (scenario.direction === 'range' || scenario.role === 'consolidation') {
      const candidate = market && market.candidate;
      if (!candidate || !finite(candidate.low) || !finite(candidate.high) || currentPrice < candidate.low || currentPrice > candidate.high) return [];
    }
    return path.slice();
  }
  function selectedKey(market, list, previous, currentPrice) {
    if (list.some(item => item.key === previous)) return previous;
    const preferred = market && market.selected_scenario;
    const key = preferred && typeof preferred === 'object' ? preferred.key : preferred;
    if (list.some(item => item.key === key)) return key;
    return (list.slice().sort((a, b) => (Number.isFinite(a.priority) ? a.priority : 99) - (Number.isFinite(b.priority) ? b.priority : 99))
      .find(item => drawablePath(item, currentPrice, market).length) || list[0] || {}).key || null;
  }
  function continuationSteps(scenario, currentPrice, market) {
    const path = drawablePath(scenario, currentPrice, market);
    if (!path.length || !['up', 'down'].includes(scenario.direction)) return [];
    let previous = path[path.length - 1];
    const result = [];
    for (const step of (scenario.continuations || []).slice(0, 2)) {
      if (!step || !finite(step.from) || !finite(step.price) || !finite(step.trigger) || step.price <= 0 ||
          Math.abs(step.from - previous) > 1e-7 || !step.condition || !step.source || step.source.price !== step.price ||
          (scenario.direction === 'down' ? !(step.price < step.trigger && step.trigger < previous) : !(step.price > step.trigger && step.trigger > previous))) break;
      result.push(step); previous = step.price;
    }
    return result;
  }
  const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  function stageLabels(scenario, length) {
    const down = scenario.direction === 'down';
    if (length === 2) return ['现价', down ? '首个支撑' : '首个压力'];
    if (scenario.role === 'retest') return ['现价', down ? '先反弹到观察带' : '先回踩到观察带', down ? '受阻后再破' : '守住后再上', down ? '首个支撑' : '首个压力'];
    if (scenario.role === 'reclaim') return ['现价', down ? '收回下方' : '收回上方', down ? '回抽未越回' : '回踩未跌回', '回原带观察'];
    return Array.from({ length }, (_, i) => i === 0 ? '现价' : i === length - 1 ? '参考位' : '等待确认');
  }
  // Schematic only: equal rank spacing preserves price ordering and every actual turn,
  // but deliberately has no price/time scale. The separate K-line chart remains quantitative.
  function rhythmNodes(scenario, currentPrice, market) {
    const path = drawablePath(scenario, currentPrice, market), ranks = [...new Set(path)].sort((a, b) => a - b);
    const labels = stageLabels(scenario || {}, path.length);
    return path.map((value, i) => ({ price: value, label: labels[i] || '等待确认',
      x: 45 + 230 * i / (path.length - 1), y: 170 - 132 * ranks.indexOf(value) / Math.max(1, ranks.length - 1) }));
  }
  function rhythmMarkup(paths, g) {
    const { currentPrice, market, selectedKey: chosen } = g;
    const shown = (g.viewMode === 'single' ? paths.filter(s => s.key === chosen) : paths);
    const drawable = shown.filter(s => drawablePath(s, currentPrice, market).length);
    if (!shown.length) return '';
    const cards = drawable.map(s => {
      const nodes = rhythmNodes(s, currentPrice, market), color = colors[direction(s)], selected = s.key === chosen;
      const lines = nodes.slice(1).map((node, i) => {
        const prev = nodes[i], angle = Math.atan2(node.y - prev.y, node.x - prev.x);
        const tip = { x: prev.x + (node.x - prev.x) * .66, y: prev.y + (node.y - prev.y) * .66 };
        const arrow = [tip, { x: tip.x - 9 * Math.cos(angle - .48), y: tip.y - 9 * Math.sin(angle - .48) },
          { x: tip.x - 9 * Math.cos(angle + .48), y: tip.y - 9 * Math.sin(angle + .48) }];
        return '<line x1="' + prev.x + '" y1="' + prev.y + '" x2="' + node.x + '" y2="' + node.y + '" stroke="currentColor" stroke-width="3" stroke-dasharray="7 3"/>' +
          '<polygon points="' + arrow.map(p => p.x + ',' + p.y).join(' ') + '" fill="currentColor"/>';
      }).join('');
      const labels = nodes.map((node, i) => {
        const trigger = s.trigger || {}, zone = i === 1 && s.role === 'retest' && finite(trigger.low) && finite(trigger.high);
        const value = zone ? price(trigger.low) + '–' + price(trigger.high) : price(node.price);
        const anchor = i === 0 ? 'start' : i === nodes.length - 1 ? 'end' : 'middle';
        return '<circle cx="' + node.x + '" cy="' + node.y + '" r="4" fill="#101923" stroke="currentColor" stroke-width="2"/>' +
          '<text x="' + node.x + '" y="' + (node.y - 18) + '" text-anchor="' + anchor + '" class="rhythm-stage">' + escape(node.label) + '</text>' +
          '<text x="' + node.x + '" y="' + (node.y - 5) + '" text-anchor="' + anchor + '" class="rhythm-price">' + escape(value) + '</text>';
      }).join('');
      const after = continuationSteps(s, currentPrice, market);
      const next = after.length ? '继续' + (s.direction === 'down' ? '跌破' : '突破') + '并确认 → ' + after.map(step => price(step.price)).join(' → ') : '继续延伸需新的有效参考位';
      const reaction = s.role === 'direct' ? '直接走掉不追；等新的回测条件。' : s.role === 'reclaim' ? '局部收回后，仍需核对外层结构。' : '观察带内折返只是示意；必须等待完成K线确认。';
      return '<button type="button" class="market-rhythm-card' + (selected ? ' selected' : '') + '" data-scenario-key="' + escape(s.key) +
        '" aria-pressed="' + selected + '" aria-label="查看节奏：' + escape(s.label) + '" style="--scenario-color:' + color + '">' +
        '<span class="rhythm-title">' + (paths.indexOf(s) + 1) + ' · ' + escape(s.label) + '</span>' +
        '<span class="rhythm-state">' + escape(s.status_label || '等待条件完成') + '</span>' +
        '<svg viewBox="0 0 320 192" role="img" aria-label="' + escape(nodes.map(n => n.label + ' ' + price(n.price)).join('，') + '；仅示意条件顺序，不按价差或时间比例') + '">' + lines + labels + '</svg>' +
        '<span class="rhythm-reaction">' + escape(reaction) + '</span><span class="rhythm-next">到位以后：' + escape(next) +
        '。未延续 / 收回 → 重新判阶段，不预接反弹线。</span></button>';
    }).join('');
    const waiting = shown.filter(s => !drawablePath(s, currentPrice, market).length).map(s => escape(s.label) + '：' + escape(s.status_label || '证据不足，暂不画波动'));
    return '<div class="market-rhythm-heading"><strong>条件节奏 · 先看怎么走</strong><span>形态示意：不按价差 / 时间比例；实际幅度见下方同价轴图</span></div>' +
      '<div class="market-rhythm-grid">' + cards + '</div>' + (waiting.length ? '<div class="market-rhythm-waiting">' + waiting.join(' · ') + '（仅文字观察）</div>' : '');
  }
  function pricePathPoints(path, g) {
    if (!Array.isArray(path) || path.length < 2 || !path.every(finite) || typeof g.y !== 'function') return [];
    const start = g.split + 13, budget = Math.max(0, (g.right - start - 15) * .78);
    const gaps = path.slice(1).map((value, i) => Math.max(g.narrow ? 24 : 46, Math.min(g.narrow ? 48 : 76, Math.abs(g.y(value) - g.y(path[i])) * .9)));
    const factor = Math.min(1, budget / gaps.reduce((sum, gap) => sum + gap, 0));
    let x = start;
    return path.map((value, i) => { if (i) x += gaps[i - 1] * factor; return [x, g.y(value)]; });
  }
  function summary(market) {
    if (!market || market.status !== 'ok') return [];
    const reference = market.reference || {}, priceNow = market.price;
    const lines = finite(reference.low) && finite(reference.high) ? [{
      label: reference.kind === 'reference' ? '外层参考带（未确认震荡）' : '外层参考区间', detail: price(reference.low) + ' — ' + price(reference.high) + ' · ' +
        (priceNow < reference.low ? '现价在下沿之外' : priceNow > reference.high ? '现价在上沿之外' : '现价仍在参考范围内') + '；局部破线不等于整体趋势反转。', color: '#e7c460'
    }] : [];
    lines.push(...(market.trend_lines || []).slice(0, 2).map(line => ({
      label: '局部 · ' + (line.label || (line.kind === 'support' ? '上升支撑线' : '下降压力线')),
      detail: '两处确认拐点 · ' + ({ intact: '尚未破线', broken: '已破线，观察延续', retested: '已回测', reclaimed: '已收回' }[line.status] || '观察中'),
      color: line.kind === 'support' ? '#81b7ef' : '#bc9af2'
    })));
    if (!(market.trend_lines || []).length) lines.push({ label: '局部趋势线待确认', detail: '尚无满足规则的两处同类拐点，不强画趋势线。', color: '#93a4b9' });
    const zone = market.observation_zone;
    if (zone && finite(zone.low) && finite(zone.high)) lines.push({ label: zone.label || '关键观察区域',
      detail: price(zone.low) + ' — ' + price(zone.high) + ' · ' + (zone.basis || '观察价格到达后的反应，区域本身不是进场条件。'), color: '#d5a96a' });
    const levels = market.key_levels || {};
    [['下方摆动参考', levels.current_support], ['上方摆动参考', levels.current_resistance]].forEach(([label, level]) => {
      if (level && finite(level.price)) lines.push({ label, detail: price(level.price) + ' · ' +
        (level.tier === 'outer' ? '外层参考' : '局部参考') + ' · ' + (level.label || ''), color: '#b3c2d4' });
    });
    return lines;
  }
  function values(market, first, last) {
    if (!market || market.status !== 'ok') return [];
    const result = [], zone = market.observation_zone;
    if (zone) [zone.low, zone.high].filter(finite).forEach(value => result.push(value));
    const reference = market.reference || {}, levels = market.key_levels || {};
    [reference.low, reference.high, market.price, levels.current_support && levels.current_support.price,
      levels.current_resistance && levels.current_resistance.price, levels.outer_low && levels.outer_low.price,
      levels.outer_high && levels.outer_high.price].filter(finite).forEach(value => result.push(value));
    scenarios({ market_structure: market }).forEach(scenario => {
      result.push(...drawablePath(scenario, market.price, market));
      result.push(...continuationSteps(scenario, market.price, market).map(step => step.price));
    });
    // All drawable branches share this scale, so selecting another branch never changes the apparent price geometry.
    return result;
  }
  function drawHistory(ctx, market, g, pass) {
    if (!market || market.status !== 'ok') return;
    const { x, y, first, last, left, split, top, bottom, slot, indexAt } = g;
    const index = (time, fallback, end) => indexAt(time, fallback, end);
    ctx.save(); ctx.beginPath(); ctx.rect(left, top, split - left - 10, bottom - top); ctx.clip();
    if (pass === 'under') {
      const zone = market.observation_zone;
      if (zone && finite(zone.low) && finite(zone.high) && zone.high >= zone.low) {
        const start = Math.max(left, x(Math.max(first, index(zone.start_time, zone.start_index))) - slot / 2);
        ctx.fillStyle = 'rgba(211,160,89,.11)';
        ctx.fillRect(start, y(zone.high), Math.max(0, split - 12 - start), y(zone.low) - y(zone.high));
        ctx.strokeStyle = '#b68e54'; ctx.lineWidth = 1; ctx.setLineDash([3, 5]);
        [zone.low, zone.high].forEach(value => { ctx.beginPath(); ctx.moveTo(start, y(value)); ctx.lineTo(split - 12, y(value)); ctx.stroke(); });
      }
      const candidate = market.candidate;
      if (candidate && finite(candidate.low) && finite(candidate.high)) {
        const start = Math.max(left, x(Math.max(first, index(candidate.start_time, candidate.start_index))) - slot / 2);
        ctx.fillStyle = 'rgba(94,182,195,.07)';
        ctx.fillRect(start, y(candidate.high), Math.max(0, split - 12 - start), y(candidate.low) - y(candidate.high));
      }
    } else {
      (market.trend_lines || []).slice(0, 2).forEach(line => {
        const anchors = (line.anchors || []).slice(0, 2).map(anchor => ({ index: finite(anchor.index) ? anchor.index : index(anchor.time, anchor.index), price: anchor.price }));
        if (anchors.length !== 2 || !anchors.every(a => finite(a.index) && finite(a.price)) || anchors[1].index <= anchors[0].index) return;
        const color = line.kind === 'support' ? '#81b7ef' : '#bc9af2';
        const slope = (anchors[1].price - anchors[0].price) / (anchors[1].index - anchors[0].index);
        const start = Math.max(first, anchors[0].index), end = Math.min(last, finite(line.end_index) ? line.end_index : index(line.end_time, line.end_index, true));
        if (end < start) return;
        const at = i => anchors[0].price + slope * (i - anchors[0].index);
        ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.setLineDash([]);
        ctx.beginPath(); ctx.moveTo(x(start), y(at(start))); ctx.lineTo(x(end), y(at(end))); ctx.stroke();
        anchors.forEach((anchor, i) => {
          if (anchor.index < first || anchor.index > last) return;
          ctx.fillStyle = '#101722'; ctx.beginPath(); ctx.arc(x(anchor.index), y(anchor.price), 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
          ctx.font = '9px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = color;
          ctx.fillText(i ? 'B' : 'A', x(anchor.index), y(anchor.price) + (line.kind === 'support' ? 16 : -9));
        });
      });
      const candidate = market.candidate;
      if (candidate && finite(candidate.low) && finite(candidate.high)) {
        const start = Math.max(left, x(Math.max(first, index(candidate.start_time, candidate.start_index))) - slot / 2);
        ctx.strokeStyle = '#69b6c5'; ctx.lineWidth = 1.3; ctx.setLineDash([3, 4]);
        [candidate.low, candidate.high].forEach(value => { ctx.beginPath(); ctx.moveTo(start, y(value)); ctx.lineTo(split - 12, y(value)); ctx.stroke(); });
      }
    }
    ctx.restore();
  }
  function drawPaths(ctx, paths, g) {
    const { split, right, top, bottom, narrow, y, currentPrice, selectedKey: chosen, market } = g;
    if (typeof y !== 'function' || !finite(currentPrice)) return;
    const selected = paths.find(item => item.key === chosen) || (!chosen ? paths[0] : null);
    const multiple = g.viewMode !== 'single';
    const visible = (multiple ? paths : selected ? [selected] : []).filter(item => drawablePath(item, currentPrice, market).length);
    const startX = split + 13, available = right - startX - 15;
    ctx.save(); ctx.font = '10px "Microsoft YaHei",sans-serif'; ctx.textAlign = 'left';
    ctx.fillStyle = '#b6c7da'; ctx.fillText('现价 / 起点 ' + price(currentPrice), split + 10, 55);
    if (!visible.length || available < 45) {
      ctx.fillStyle = '#8498ae'; ctx.fillText('当前分支仅文字观察', split + 10, top + 18);
      ctx.fillText(narrow ? '条件未齐，不画路径' : '条件或有效参考目标尚未具备，不补画价格波动。', split + 10, top + 35);
      ctx.restore(); return;
    }
    const occupied = [], labels = [], ends = new Map();
    function stroke(points, color, dash, width, alpha, arrow = true) {
      ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.lineWidth = width; ctx.setLineDash(dash); ctx.beginPath();
      points.forEach((point, i) => i ? ctx.lineTo(...point) : ctx.moveTo(...point)); ctx.stroke(); ctx.setLineDash([]);
      if (arrow) points.slice(1).forEach((end, index) => {
        const before = points[index], angle = Math.atan2(end[1] - before[1], end[0] - before[0]);
        ctx.beginPath(); ctx.moveTo(...end); ctx.lineTo(end[0] - 6 * Math.cos(angle - .45), end[1] - 6 * Math.sin(angle - .45));
        ctx.lineTo(end[0] - 6 * Math.cos(angle + .45), end[1] - 6 * Math.sin(angle + .45)); ctx.closePath(); ctx.fill();
      });
      ctx.restore();
    }
    // Node heights always use the historical y function. Horizontal positions encode conditional order, never time.
    ctx.save(); ctx.beginPath(); ctx.rect(split + 2, top - 3, right - split - 2, bottom - top + 6); ctx.clip();
    visible.slice().sort((a, b) => Number(a === selected) - Number(b === selected)).forEach(scenario => {
      const path = drawablePath(scenario, currentPrice, market), order = paths.indexOf(scenario) + 1;
      const emphasized = scenario === selected, color = colors[direction(scenario)];
      const points = pricePathPoints(path, g);
      stroke(points, color, scenario.role === 'direct' ? [2, 5] : scenario.role === 'reclaim' ? [9, 3, 2, 3] : [7, 4],
        emphasized ? 2.7 : 1.8, emphasized ? 1 : .78);
      ctx.fillStyle = color;
      points.forEach(point => { ctx.beginPath(); ctx.arc(...point, 2.4, 0, Math.PI * 2); ctx.fill(); });
      const end = points.at(-1); ends.set(scenario.key, end);
      labels.push({ point: end, text: order + ' ' + (shortLabels[scenario.key] || String(scenario.label || '条件').slice(0, 6)) + ' ' + price(path.at(-1)),
        color, dx: 7, dy: scenario.role === 'direct' ? 18 : -12, strong: emphasized });
      if (!narrow && points.length >= 4) {
        const trigger = scenario.trigger || {};
        const zone = scenario.role === 'retest' ? '回测 ' + price(trigger.low) + '–' + price(trigger.high) : '收回 ' + price(trigger.price);
        labels.push({ point: points[1], text: zone, color, dx: -25, dy: -15 });
        if (emphasized) labels.push({ point: points[2], text: stageLabels(scenario, path.length)[2], color, dx: -20, dy: 18 });
      }
    });
    const continuations = selected ? continuationSteps(selected, currentPrice, market) : [];
    if (continuations.length && ends.has(selected.key)) {
      let previous = ends.get(selected.key);
      const tailX = Math.min(startX + available * .88, previous[0] + continuations.length * (narrow ? 38 : 56)), originX = previous[0];
      continuations.forEach((step, i) => {
        const next = [originX + (tailX - originX) * (i + 1) / continuations.length, y(step.price)];
        stroke([previous, next], colors[direction(selected)], [2, 7], 1.2, .65);
        ctx.strokeStyle = colors[direction(selected)]; ctx.fillStyle = '#0f1620'; ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.arc(...next, 3.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
        labels.push({ point: next, text: '破' + price(step.from) + '后 → ' + price(step.price),
          color: colors[direction(selected)], dx: 7, dy: 18 });
        previous = next;
      });
    }
    ctx.restore();
    // Move labels, not prices: small leader lines preserve the true price geometry when labels collide.
    labels.sort((a, b) => Number(b.strong) - Number(a.strong)).forEach(label => {
      ctx.font = (label.strong ? 'bold ' : '') + '10px "Microsoft YaHei",sans-serif';
      const width = Math.min(right - split - 12, ctx.measureText(label.text).width + 8), height = 17;
      const baseX = Math.max(split + 5, Math.min(right - width - 3, label.point[0] + label.dx));
      let box;
      for (const offset of [0, -22, 22, -44, 44, -66, 66, -88, 88, -110, 110]) {
        const yy = Math.max(top + 2, Math.min(bottom - height - 2, label.point[1] + label.dy + offset));
        const candidate = { x: baseX, y: yy - 11, w: width, h: height };
        if (!occupied.some(b => candidate.x < b.x + b.w + 5 && candidate.x + candidate.w + 5 > b.x && candidate.y < b.y + b.h + 3 && candidate.y + candidate.h + 3 > b.y)) { box = candidate; break; }
      }
      if (!box) return; // Cards retain the full wording at small sizes; never stack illegible labels.
      occupied.push(box);
      ctx.strokeStyle = label.color; ctx.lineWidth = .6; ctx.globalAlpha = .65; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(...label.point); ctx.lineTo(Math.max(box.x, Math.min(box.x + box.w, label.point[0])), box.y + 8); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillStyle = '#0f1620'; ctx.fillRect(box.x, box.y, box.w, box.h);
      ctx.fillStyle = label.color; ctx.textAlign = 'left'; ctx.fillText(label.text, box.x + 4, box.y + 12);
    });
    if (multiple) {
      const textOnly = paths.filter(item => !drawablePath(item, currentPrice, market).length);
      ctx.fillStyle = '#a69a7e'; ctx.font = '9px "Microsoft YaHei",sans-serif';
      ctx.fillText('加粗：当前观察 · 细点线/空心点：再破前级后才看', split + 10, 72);
      if (textOnly.length) {
        ctx.fillStyle = '#a69a7e'; ctx.fillText(textOnly.map(item => (paths.indexOf(item) + 1) + ' ' + (item.role === 'consolidation' ? '整理待证据' : '条件不足')).join(' · ') + '（不画价格路径）', split + 10, bottom + 16);
      }
    }
    ctx.restore();
  }
  const api = { scenarios, drawablePath, selectedKey, continuationSteps, rhythmNodes, rhythmMarkup, pricePathPoints, summary, values, drawHistory, drawPaths };
  root.MarketStructureChart = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
