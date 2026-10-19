(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.StructureModel = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const options = entries => entries.map(([value, label]) => Object.freeze({value, label}));
  const OPTIONS = Object.freeze({
    types: Object.freeze(options([['unknown', '未确认'], ['up', '上涨结构'], ['down', '下跌结构'], ['range', '震荡结构'], ['disordered', '无序震荡']])),
    phases: Object.freeze(options([['unknown', '阶段未确认'], ['impulse', '方向运行中'], ['pullback', '回撤 / 反弹中'], ['rejected', '企稳 / 受阻'], ['consolidation', '回撤 / 反弹后整理']])),
    stages: Object.freeze(options([['unknown', '待编号'], ...Array.from({length: 9}, (_, i) => ['point' + (i + 1), '点' + (i + 1)])])),
    segments: Object.freeze(options([['unknown', '区段未确认'], ...Array.from({length: 7}, (_, i) => [(i + 1) + '-' + (i + 2), (i + 1) + '→' + (i + 2)])])),
    intents: Object.freeze(options([['observe', '仅观察'], ['continuation', '观察顺向延续']]))
  });
  const PHASES = Object.freeze({inside: '参考带内', up_break: '上破待回踩', down_break: '下破待反抽',
    up_hold: '回踩守住', down_hold: '反抽受阻', up_extension: '上行延伸', down_extension: '下行延伸',
    up_failed: '上破收回', down_failed: '下破收回'});
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const positive = value => number(value) && value > 0;
  const text = (value, limit = 280) => typeof value === 'string' ? value.replace(/[\u0000-\u001f]/g, ' ').trim().slice(0, limit) : '';
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const enumValue = (value, list, fallback = 'unknown') => list.some(item => item.value === value) ? value : fallback;
  const fmt = value => positive(value) ? Number(value.toFixed(8)).toString() : '待定义';
  const manualPrice = value => {
    if (typeof value === 'string' && value.trim()) value = Number(value);
    return positive(value) ? value : null;
  };
  const clone = value => {
    try { return JSON.parse(JSON.stringify(value)); } catch (_) { return null; }
  };
  function timestamp(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    let iso = value.trim().replace(' ', 'T');
    if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso)) iso += '+08:00';
    const parsed = Date.parse(iso);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function completedBars(input, now, warnings) {
    const result = [];
    let rejected = 0;
    for (const value of Array.isArray(input) ? input : []) {
      const b = object(value), at = timestamp(b.dt);
      if (b.complete === false || b.completed === false || b.closed === false || b.is_complete === false || b.final === false || b.unfinished === true ||
          !['open', 'high', 'low', 'close'].every(key => positive(b[key])) || b.low > Math.min(b.open, b.close) ||
          b.high < Math.max(b.open, b.close) || b.high < b.low || at === null || at > now ||
          (b.period !== undefined && b.period !== 1800)) {
        rejected += 1;
        continue;
      }
      // completed 是后端已排除live的契约；保留最后一根，不按数组位置再次删除。
      if (result.length && at <= result[result.length - 1].time) {
        warnings.push('完成K线时间重复或乱序，当前证据暂停使用');
        return [];
      }
      result.push({open: b.open, high: b.high, low: b.low, close: b.close, dt: text(b.dt, 40), time: at});
    }
    if (rejected) warnings.push('已排除未完成、未来时间或无效K线；不据此确认新阶段');
    return result.slice(-6);
  }
  function ruleType(phase, kind) {
    if (['up_break', 'up_hold', 'up_extension'].includes(phase)) return 'up';
    if (['down_break', 'down_hold', 'down_extension'].includes(phase)) return 'down';
    return kind === 'range' ? 'range' : 'unknown';
  }
  function manualPhaseLabel(phase, type) {
    const directional = type === 'up' || type === 'down';
    if (phase === 'impulse') return type === 'up' ? '上涨进行' : type === 'down' ? '下跌进行' : '方向运行待确认';
    if (phase === 'pullback') return type === 'up' ? '回撤中' : type === 'down' ? '反弹中' : '回撤 / 反弹待确认';
    if (phase === 'rejected') return type === 'up' ? '回撤企稳' : type === 'down' ? '反弹受阻' : '企稳 / 受阻待确认';
    if (phase === 'consolidation') return directional ? (type === 'up' ? '回撤后整理' : '反弹后整理') : '整理观察';
    return '阶段未确认';
  }
  function observation(phase, type, bars, candidate, available) {
    if (!available) return {key: 'insufficient', label: '完成K线证据不足', detail: '等待有效的30分钟结构和完成K线。', source: 'completed_bars'};
    const last = bars[bars.length - 1], prev = bars[bars.length - 2];
    const counter = prev && (type === 'down' ? last.close > last.open && last.close > prev.close :
      type === 'up' ? last.close < last.open && last.close < prev.close : false);
    if (candidate) return {key: 'consolidation_candidate', label: '整理候选，尚未确认',
      detail: `候选范围 ${fmt(candidate.low)}–${fmt(candidate.high)}，已完成 ${candidate.bars}/${candidate.required_bars} 根；旧边界继续保留。`, source: 'completed_bars'};
    if (counter) return {key: type === 'down' ? 'rebound_bar' : 'pullback_bar',
      label: type === 'down' ? '出现反弹K线，待确认' : '出现回撤K线，待确认',
      detail: `最新完成收盘 ${fmt(last.close)} ${type === 'down' ? '高于' : '低于'}开盘和前收盘；仅是单根反向证据，不据此宣称趋势反转或点位编号。`, source: 'completed_bars'};
    if (phase === 'down_hold' || phase === 'up_hold') return {key: phase, label: PHASES[phase] + '（规则证据）',
      detail: '完成K线规则已有回踩 / 反抽确认；文档点位编号仍需人工核对。', source: 'completed_bars'};
    if (phase === 'up_failed' || phase === 'down_failed') return {key: phase, label: PHASES[phase] + '，方向重新观察',
      detail: '原边界突破情景已经收回；不自动推断反向趋势成立。', source: 'completed_bars'};
    if (type === 'up' || type === 'down') return {key: phase + '_observation', label: (type === 'down' ? '下行' : '上行') + '延续观察',
      detail: `当前阶段为${PHASES[phase]}，最新完成收盘 ${fmt(last.close)}；缺少反向单K不等同趋势必然延续。`, source: 'completed_bars'};
    return {key: 'inside', label: type === 'range' ? '区间内观察' : '参考带内，方向未确认',
      detail: '程序只描述规则参考带；没有自动确认文档结构、方向或奇数点位。', source: 'completed_bars'};
  }
  function transition(phase, range, buffer, candidate, available) {
    if (!available) return {label: '等待结构证据', conditions: ['等待有效完成K线与参考边界'], invalidation: '数据不足时不判断当前转变。'};
    const low = range.low, high = range.high, buf = number(buffer) && buffer >= 0 ? buffer : 0;
    let conditions, invalidation;
    if (['up_break', 'up_hold', 'up_extension'].includes(phase)) {
      conditions = [`完成收盘继续高于 ${fmt(high + buf)}，才保留上破延续观察。`,
        `后续回踩 ${fmt(high - buf)}–${fmt(high + buf)} 并重新收于 ${fmt(high + buf)} 上方，才有新的回踩守住证据。`];
      invalidation = `完成收盘低于 ${fmt(high - buf)}，原上破情景收回；计划止损另列，不等到收盘才关注。`;
    } else if (['down_break', 'down_hold', 'down_extension'].includes(phase)) {
      conditions = [`完成收盘继续低于 ${fmt(low - buf)}，才保留下破延续观察。`,
        `后续反抽 ${fmt(low - buf)}–${fmt(low + buf)} 并重新收于 ${fmt(low - buf)} 下方，才有新的反抽受阻证据。`];
      invalidation = `完成收盘高于 ${fmt(low + buf)}，原下破情景收回；计划止损另列，不等到收盘才关注。`;
    } else {
      conditions = [`完成收盘高于 ${fmt(high + buf)} 后，再观察后续回踩证据。`,
        `完成收盘低于 ${fmt(low - buf)} 后，再观察后续反抽证据。`];
      invalidation = `收盘越过 ${fmt(low - buf)} 或 ${fmt(high + buf)} 后，重新评估当前带内情景。`;
    }
    if (candidate) conditions.push(`整理候选 ${fmt(candidate.low)}–${fmt(candidate.high)} 已完成 ${candidate.bars}/${candidate.required_bars} 根；尚需后续完成棒确认，不能提前替换旧结构。`);
    return {label: '下一项需要的完成K线证据', conditions, invalidation};
  }

  function build(input, inputManual = {}, now = Date.now()) {
    const c = object(input), m = object(inputManual), warnings = [];
    now = number(now) ? now : 0;
    const identity = {symbol: text(c.symbol, 80), period: c.period === 1800 ? 1800 : null,
      structure_id: text(c.structure_id, 160)};
    const range = object(c.range), phase = Object.hasOwn(PHASES, c.phase) ? c.phase : 'unknown';
    const bars = completedBars(c.completed, now, warnings);
    const available = Boolean(identity.symbol && identity.structure_id && identity.period && positive(range.low) &&
      positive(range.high) && range.high > range.low && phase !== 'unknown' && bars.length >= 2);
    const marketStructure = object(c.market_structure), marketPhase = object(marketStructure.phase);
    const marketEvidence = available && marketStructure.status === 'ok' && marketStructure.period === 1800 &&
      timestamp(marketStructure.decision_bar_time) === bars[bars.length - 1].time && text(marketPhase.label);
    const market = positive(c.market_updated) ? c.market_updated : null;
    const age = market === null ? null : (now / 1000 - market);
    const realtime = available && c.realtime === true && age !== null && age >= -5 && age <= 20;
    const status = !available ? 'insufficient' : realtime ? 'ok' : 'stale';
    const freshness = object(c.freshness), checkedAge = positive(freshness.checked_at) ? now / 1000 - freshness.checked_at : Infinity;
    const latestAvailable = available && freshness.status === 'latest_available' && checkedAge >= 0 && checkedAge <= 20;
    if (!available) warnings.push('结构数据不足：不显示当前确定点或可用计划');
    else if (!realtime) warnings.push(latestAvailable ? '已收到最新可用K线，等待行情变化；观察层可复核，实时计划仍暂停' : '历史 / 过期快照：只复核证据，当前编号待核验，实时计划暂停');

    const hasManual = Object.keys(m).length > 0;
    const bound = Boolean(identity.symbol && identity.structure_id && m.structure_id === identity.structure_id &&
      m.symbol === identity.symbol);
    if (hasManual && !bound) warnings.push('人工注记不属于当前品种 / 结构，已忽略');
    const manual = {applied: bound, type: bound ? enumValue(m.type, OPTIONS.types) : 'unknown',
      phase: bound ? enumValue(m.phase, OPTIONS.phases) : 'unknown',
      stage: bound ? enumValue(m.stage, OPTIONS.stages) : 'unknown',
      segment: bound ? enumValue(m.segment, OPTIONS.segments) : 'unknown',
      intent: bound ? enumValue(m.intent, OPTIONS.intents, 'observe') : 'observe',
      reason: bound ? text(m.reason, 500) : '', phase_label: '', needs_review: false};
    if (bound && ((m.stage && manual.stage === 'unknown' && m.stage !== 'unknown') ||
      (m.segment && manual.segment === 'unknown' && m.segment !== 'unknown'))) warnings.push('未支持的人工编号已忽略，不从自由文字推断点位');
    const rule = ruleType(phase, range.kind);
    // A local diagonal break is evidence about that line, not a replacement for the outer structure.
    const localPhase = Boolean(marketEvidence && (marketPhase.scope === 'local' || text(marketPhase.key).startsWith('line_')));
    const observedType = marketEvidence && !localPhase && ['up', 'down', 'range'].includes(marketPhase.direction) ? marketPhase.direction : rule;
    const effectiveType = manual.type !== 'unknown' ? manual.type : observedType;
    const manualDirection = ['up', 'down', 'range'].includes(manual.type) ? manual.type : 'unknown';
    const conflict = manualDirection !== 'unknown' && observedType !== 'unknown' && manualDirection !== observedType;
    if (conflict) warnings.push('人工结构方向与程序规则观察不同，请对照完成K线复核；人工注记不改变交易计划');
    const completedAt = bars.length ? bars[bars.length - 1].dt : null;
    const reviewedAt = bound ? timestamp(m.reviewed_bar_time) : null;
    manual.needs_review = reviewedAt !== null && bars.length > 0 && bars[bars.length - 1].time > reviewedAt;
    if (manual.needs_review) warnings.push('新增完成K线，人工注记需复核');
    manual.phase_label = manualPhaseLabel(manual.phase, effectiveType);
    if (manual.needs_review && manual.phase !== 'unknown') manual.phase_label += '（待复核）';
    const manualStructure = manual.type !== 'unknown' || manual.phase !== 'unknown';
    const typeLabels = {unknown: '参考带：方向未确认', up: '上行规则观察', down: '下行规则观察', range: '区间规则观察', disordered: '无序震荡（人工）'};
    const humanType = OPTIONS.types.find(o => o.value === effectiveType);
    const structure = {type: available ? effectiveType : 'unknown',
      label: manual.type !== 'unknown' && available ? (humanType ? humanType.label : '人工结构') : available ?
        localPhase && phase === 'inside' ? (range.kind === 'range' ? '外层区间规则观察' : '外层参考带内 · 整体方向未确认') : typeLabels[observedType] : '结构数据不足',
      phase: manual.phase !== 'unknown' ? manual.phase : phase,
      phase_label: manual.phase !== 'unknown' ? manual.phase_label : marketEvidence ? text(marketPhase.label) : PHASES[phase] || '阶段未确认',
      rule_type: rule, rule_phase: phase, summary: text(c.summary),
      source: manualStructure ? 'manual' : 'rule',
      type_source: manual.type !== 'unknown' ? 'manual' : 'rule',
      phase_source: manual.phase !== 'unknown' ? 'manual' : 'rule',
      source_label: manualStructure ? '人工观察（程序证据另列）' : localPhase ? '外层结构与局部破线分别观察' : marketEvidence ? '结构与趋势线规则观察（非文档编号）' : '程序规则观察（非文档编号）',
      low: positive(range.low) ? range.low : null, high: positive(range.high) ? range.high : null};
    if (manual.needs_review && manual.type !== 'unknown' && available) structure.label += '（待复核）';
    if (manual.needs_review && manualStructure) structure.source_label = '人工记录待复核（程序证据另列）';
    if (!realtime && available) structure.phase_label = (latestAvailable ? '最新可用观察：' : '历史观察：') + structure.phase_label;

    const numbered = available && realtime && !manual.needs_review && (manual.stage !== 'unknown' || manual.segment !== 'unknown');
    const pointLabel = manual.stage === 'unknown' ? '' : '点' + manual.stage.slice(5);
    const segmentLabel = manual.segment === 'unknown' ? '' : manual.segment.replace('-', '→');
    const numbering = {status: numbered ? 'manual' : 'pending',
      label: numbered ? '人工确认：' + [pointLabel, segmentLabel ? segmentLabel + ' 区段' : ''].filter(Boolean).join(' · ') :
        manual.needs_review ? '待编号（历史人工编号待复核）' : !realtime && available && bound ? '待编号（历史人工记录待核验）' : '待编号（程序不自动编号）',
      stage: numbered ? manual.stage : 'unknown', segment: numbered ? manual.segment : 'unknown', source: numbered ? 'manual' : 'none'};

    const rawCandidate = object(c.candidate);
    const candidate = positive(rawCandidate.low) && positive(rawCandidate.high) && rawCandidate.high > rawCandidate.low &&
      Number.isInteger(rawCandidate.bars) && Number.isInteger(rawCandidate.required_bars) && rawCandidate.bars >= 4 &&
      rawCandidate.bars < rawCandidate.required_bars ? rawCandidate : null;
    const observed = marketEvidence ? {key: text(marketPhase.key), label: text(marketPhase.label),
      detail: text(marketPhase.summary || marketPhase.detail), source: 'completed_bars'} : observation(phase, rule, bars, candidate, available);
    if (status === 'stale') { observed.label = (latestAvailable ? '完成K线证据：' : '历史证据：') + observed.label; }
    const evidence = [];
    if (available) {
      evidence.push({key: 'reference', label: '本代冻结边界', detail: `${fmt(range.low)}–${fmt(range.high)}；缓冲 ${fmt(c.buffer)}`,
        at: text(c.reference_end, 40) || null, source: 'rule'});
      const last = bars[bars.length - 1];
      evidence.push({key: 'completed_bar', label: '最新完成30分K线', detail: `开 ${fmt(last.open)} / 高 ${fmt(last.high)} / 低 ${fmt(last.low)} / 收 ${fmt(last.close)}`,
        at: last.dt, source: 'completed_bars'});
      evidence.push({key: observed.key, label: observed.label, detail: observed.detail, at: last.dt, source: observed.source});
      for (const item of Array.isArray(c.events) ? c.events.slice(-3) : []) {
        const e = object(item), eventTime = timestamp(e.at);
        if (eventTime !== null && eventTime <= last.time && text(e.label)) evidence.push({key: 'event_' + text(e.id, 80),
          label: text(e.label, 80), detail: text(e.detail), at: text(e.at, 40), source: 'rule_event'});
      }
    }
    const rawPlan = object(c.plan), risk = c.risk && typeof c.risk === 'object' ? clone(c.risk) : null;
    const pDirection = ['long', 'short'].includes(rawPlan.direction) ? rawPlan.direction : 'none';
    const geometry = [rawPlan.entry, rawPlan.stop, rawPlan.target].every(positive) && (pDirection === 'long' ?
      rawPlan.stop < rawPlan.entry && rawPlan.entry < rawPlan.target : pDirection === 'short' ? rawPlan.target < rawPlan.entry && rawPlan.entry < rawPlan.stop : false);
    const riskPassed = Boolean(risk && risk.fee_configured === true && positive(risk.max_lots) && positive(risk.net_rr) &&
      Array.isArray(risk.rows) && risk.rows.some(row => object(row).allowed === true));
    const ready = realtime && geometry && riskPassed && rawPlan.status === 'ready';
    const plan = {status: !available ? 'unavailable' : !realtime ? 'stale' : rawPlan.status === 'ready' && !ready ? 'review' : text(rawPlan.status, 40) || 'waiting',
      original_status: text(rawPlan.status, 40) || null, ready,
      label: !available ? '计划数据不足' : !realtime ? '实时计划暂停' : ready ?
        (rawPlan.mode === 'early' ? '试探条件已满足（非确认）' : '程序确认条件已满足') :
        rawPlan.status === 'risk_blocked' ? '风险门禁未通过' : rawPlan.status === 'ready' ? '计划需复核' : '等待条件',
      direction: pDirection, entry: positive(rawPlan.entry) ? rawPlan.entry : null,
      stop: positive(rawPlan.stop) ? rawPlan.stop : null, target: positive(rawPlan.target) ? rawPlan.target : null,
      target_basis: text(rawPlan.target_basis), trigger: text(rawPlan.trigger), invalidation: text(rawPlan.invalidation),
      wait_for: !realtime ? '等待实时数据恢复后重新核对计划' : text(rawPlan.wait_for), source: 'backend'};
    if (rawPlan.status === 'ready' && !ready && realtime) warnings.push('计划状态缺少完整风险或价格方向证据，暂停显示可用计划');
    const manualShelter = bound ? manualPrice(m.shelter === undefined ? m.shelter_price : m.shelter) : null;
    const manualBreakeven = bound ? manualPrice(m.breakeven === undefined ? m.breakeven_trigger : m.breakeven) : null;
    const boundary = available && rule === 'up' ? range.high : available && rule === 'down' ? range.low : null;
    const levels = {
      shelter: {price: manualShelter === null ? boundary : manualShelter, source: manualShelter === null ? boundary === null ? 'undefined' : 'rule' : 'manual',
        label: manualShelter === null ? boundary === null ? '掩体待定义' : '结构边界参考（非计划止损）' : '人工掩体参考'},
      stop: {price: plan.stop, source: plan.stop === null ? 'undefined' : 'backend', label: plan.stop === null ? '计划止损待形成' : '计划止损参考'},
      breakeven: {price: manualBreakeven, source: manualBreakeven === null ? 'undefined' : 'manual', label: manualBreakeven === null ? '平推触发待定义' : '人工平推触发参考'}
    };
    const riskKey = !risk || risk.fee_configured !== true ? 'unknown' : rawPlan.status === 'risk_blocked' || !riskPassed ? 'blocked' : ready ? 'passed' : 'estimated';
    const nextEvidence = transition(phase, range, c.buffer, candidate, available);
    if (marketEvidence && Array.isArray(marketStructure.scenarios)) {
      const preferred = typeof marketStructure.selected_scenario === 'string' ? marketStructure.selected_scenario : object(marketStructure.selected_scenario).key;
      const selected = marketStructure.scenarios.map(object).find(s => s.key === preferred);
      const usable = marketStructure.scenarios.map(object).filter(s =>
        s.drawable !== false && !['invalid', 'target_passed', 'no_target', 'waiting_evidence'].includes(s.status));
      usable.sort((a, b) => Number(b.key === preferred) - Number(a.key === preferred));
      const conditions = usable.slice(0, 2).map(s => text(s.condition, 450)).filter(Boolean);
      if (selected && !usable.includes(selected) && selected.drawable === false) {
        nextEvidence.conditions = [text(selected.label) + '：' + text(selected.status_label) + '；等待新的完成K线与有效参考位，不自动切换反向情景。'];
      } else if (conditions.length) nextEvidence.conditions = conditions;
    }
    return {status, identity, structure, numbering, observation: observed, evidence,
      transition: nextEvidence, levels, plan, manual, warnings,
      direction: {rule, observed: observedType, local: localPhase ? marketPhase.direction : null, manual: manualDirection, value: available ? effectiveType : 'unknown',
        label: {up: '上行', down: '下行', range: '区间', disordered: '无序震荡', unknown: '方向未确认'}[available ? effectiveType : 'unknown'],
        source: manual.type !== 'unknown' ? 'manual' : 'rule', conflict},
      statuses: {data: {key: latestAvailable ? 'waiting' : status, label: status === 'ok' ? '实时证据' : latestAvailable ? '最新可用，等待行情变化' : status === 'stale' ? '历史快照，实时暂停' : '数据不足'},
        structure: {key: !available ? 'unknown' : conflict ? 'conflict' : manualStructure ? 'manual' : 'rule',
          label: !available ? '结构待识别' : conflict ? '人工与规则需核对' : manualStructure ? '人工结构观察' : '规则观察，文档编号未确认'},
        risk: {key: riskKey, label: {unknown: '风险未核算', blocked: '风险未通过', passed: '预算检查已通过', estimated: '仅风险估算，非入场许可'}[riskKey]}},
      diagram: {direction: available ? effectiveType : 'unknown', segment: numbering.segment, stage: numbering.stage,
        highlight_source: numbered && numbering.segment !== 'unknown' ? 'manual' : 'none'},
      freshness: {updated: positive(c.updated) ? c.updated : null, market_updated: market,
        realtime, age_seconds: age === null ? null : Math.max(0, age), completed_bar_time: completedAt}, risk};
  }
  return Object.freeze({build, OPTIONS});
}));
