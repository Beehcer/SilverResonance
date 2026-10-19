"""
双轴评分引擎（一期）：位置分 × 确定性分 → 象限决策。
纯函数，不依赖行情库，方便单独测试；不改 resonance.py / double_bottom.py 的判定语义。

设计立场：位置优先 vs 确定性优先是两种入场逻辑（取舍立场），
2B/回踩/点3 只是光谱上的三个入场点采样（坐标参考，不驱动决策）。
位置分与确定性分各自独立量化，入场方案由两个分数驱动；
评分是规则证据分，不是胜率；结构/价格方向无效时硬性拒绝，小周期未确认不能主仓。

输入：轨道B double_bottom.evaluate() 的输出 dict + 现价
     + 可选 ATR（调用方用 double_bottom._atr 算好传入；缺省按价格 0.5% 估算）
     + 可选小周期验证标记（30分/3分级别企稳：短均拐头+收盘站稳，
       复用 resonance.trigger_ok 思路，由调用方判定后以 bool 传入；缺数据传 None 不扣分）。
输出契约：见 decide() 返回 dict。
"""

import math
import config
import double_bottom   # 只读复用 _ma_list（不改其判定语义）


def _tick(tick):
    value = config.PRICE_TICK if tick is None else tick
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("tick 必须为有限正数")
    return float(value)


def _snap(price, tick, up=False):
    return round((math.ceil(price / tick - 1e-10) if up else math.floor(price / tick + 1e-10)) * tick, 8)


def _valid_price(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


# ================= 市场状态判定（趋势/震荡） =================

def detect_regime(klines):
    """
    市场状态判定（二期）：让确定性分"知道现在是什么市"。输入 1h K线。
    两个维度：
      1. 方向效率（Kaufman ER）：ER = |近 N 根净位移| ÷ 逐根位移绝对值之和。
         ER 高 = 趋势顺畅（一路走），ER 低 = 来回拉锯（位移互相抵消）。
      2. 均线斜率：MA20 现值相对 REGIME_SLOPE_WINDOW 根前的变化率（复用斜率思路）。
    ER ≥ REGIME_ER_TREND 且斜率同向（|slope| ≥ REGIME_SLOPE_MIN）= 趋势；否则震荡。
    数据不足时保守返回 range（不加分也不误导）。
    """
    if not klines or len(klines) < config.REGIME_ER_WINDOW + 2:
        return {"regime": "range", "er": 0.0, "slope": 0.0, "label": "震荡市"}
    closes = [k["close"] for k in klines]
    n = config.REGIME_ER_WINDOW
    seg = closes[-n - 1:]
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    er = round(net / path, 3) if path else 0.0
    ma = double_bottom._ma_list(closes, config.REGIME_MA_PERIOD)
    w = config.REGIME_SLOPE_WINDOW
    slope = 0.0
    if len(ma) > w and ma[-1] is not None and ma[-1 - w] is not None and ma[-1 - w]:
        slope = round((ma[-1] - ma[-1 - w]) / ma[-1 - w], 5)
    if er >= config.REGIME_ER_TREND and abs(slope) >= config.REGIME_SLOPE_MIN:
        if slope > 0:
            return {"regime": "trend_up", "er": er, "slope": slope, "label": "趋势市·多"}
        return {"regime": "trend_down", "er": er, "slope": slope, "label": "趋势市·空"}
    return {"regime": "range", "er": er, "slope": slope, "label": "震荡市"}


# ================= 位置分（0–100） =================

def _location_base(dist_atr, neck_atr):
    """
    位置基础分，按"现价距 L2 的 ATR 倍数"分段线性（方向已符号化：>0 = 沿交易方向离开 L2）。
    锚点：贴 L2（±AX_L2_ZONE_ATR）= 90–100；颈位附近（±AX_NECK_ZONE_ATR）= 55–70；
    突破后延伸 >AX_EXTEND_ATR = <30（之后每多 1×ATR 再减 10，地板 0）；
    跌破 L2 = AX_BELOW_L2_SCORE（结构受损，位置无意义）。
    """
    z = config.AX_L2_ZONE_ATR
    nz = config.AX_NECK_ZONE_ATR
    n = max(neck_atr, z + nz + 0.1)          # 防止颈位与 L2 过近时分段退化
    if dist_atr < -z:
        return float(config.AX_BELOW_L2_SCORE)
    if abs(dist_atr) <= z:
        return 90.0 + 10.0 * (1 - abs(dist_atr) / z)       # 90–100
    if dist_atr < n - nz:
        t = (dist_atr - z) / (n - nz - z)                  # L2 区外沿 → 颈位带内沿：90 → 55
        return 90.0 - 35.0 * t
    if abs(dist_atr - n) <= nz:
        return 55.0 + 15.0 * (1 - abs(dist_atr - n) / nz)  # 颈位带 55–70
    over = dist_atr - n                                    # 突破后延伸
    if over <= config.AX_EXTEND_ATR:
        return 55.0 - 25.0 * (over - nz) / (config.AX_EXTEND_ATR - nz)
    return max(0.0, 30.0 - 10.0 * (over - config.AX_EXTEND_ATR))


def _location_score(ev, kind, price, atr, sgn, tick=None):
    """
    位置分 = (1−AX_RR_WEIGHT)×位置基础分 + AX_RR_WEIGHT×盈亏比分。
    返回 (score, detail_items, stop_dict, entry_ref_anchor)；无结构时 score=None。
    """
    p = ev["pattern"]
    L1, L2, neck = p["L1"]["price"], p["L2"]["price"], p["neck"]
    z = config.AX_L2_ZONE_ATR
    dist = (price - L2) * sgn / atr
    neck_atr = abs(neck - L2) / atr
    base = _location_base(dist, neck_atr)
    items = []

    # 锚点描述（明细展示用）
    if dist < -z:
        anchor = f"跌破 L2 {L2}，结构受损"
    elif abs(dist) <= z:
        anchor = f"贴近 L2 {L2}（{abs(dist):.2f}×ATR）"
    elif abs(dist - neck_atr) <= config.AX_NECK_ZONE_ATR:
        anchor = f"颈位 {neck} 附近（回踩带）"
    elif dist > neck_atr + config.AX_NECK_ZONE_ATR:
        anchor = f"突破后延伸 {dist - neck_atr:.1f}×ATR"
    else:
        anchor = f"L2→颈位过渡区（{dist:.1f}×ATR）"
    items.append({"item": anchor, "value": round(base)})

    # 强位贴合奖励：方向侧强位（score≥DB_LEVEL_STRONG）±AX_L2_ZONE_ATR×ATR 内 → 至少 92
    pool = ev["levels"]["support" if kind == "bottom" else "resistance"]
    near = [l for l in pool if l["score"] >= config.DB_LEVEL_STRONG
            and abs(l["price"] - price) <= z * atr]
    if near:
        base = max(base, 92.0)
        items.append({"item": f"贴近强位 {near[0]['price']}（强度{near[0]['score']}）", "value": 92})

    # 隐含盈亏比：结构止损 = L2 外侧 DB_STOP_ATR×ATR；目标1 = 轨道B计划，缺则丈量目标
    tick = _tick(tick)
    stop_price = _snap(L2 - sgn * config.DB_STOP_ATR * atr, tick, up=sgn < 0)
    plan = ev.get("plan") or {}
    t1 = plan.get("target1") or round(2 * neck - L1, 2)   # 丈量目标：2×颈位−L1（双向通用）
    t1 = _snap(t1, tick, up=sgn < 0)
    risk = (price - stop_price) * sgn
    reward = (t1 - price) * sgn
    rr = reward / risk if risk > 0 and reward > 0 else 0.0
    rr_score = min(100.0, 50.0 * rr)                       # RR≥2 满分；RR=1 得 50；<1 大幅减分
    items.append({"item": f"隐含盈亏比 RR {rr:.1f}（止损 {stop_price} → 目标 {t1}）",
                  "value": round(rr_score)})

    w = config.AX_RR_WEIGHT
    score = round((1 - w) * base + w * rr_score)
    stop = {"price": stop_price,
            "basis": f"L2{'下方' if sgn > 0 else '上方'}{config.DB_STOP_ATR}×ATR"}
    return score, items, stop


# ================= 确定性分（0–100） =================

def _certainty_score(ev, target_dir, price, small_tf_ok, regime=None):
    """证据计数加权（全部来自轨道B已有输出，不做新指标）。返回 (score, detail_items)。
    regime（detect_regime 输出）让分数"知道现在是什么市"：
    震荡市方向项减半（方向本身是噪音）+ 结构补偿（边沿反转结构更可靠）；趋势市照旧。"""
    items = []
    total = 0

    def add(item, delta):
        nonlocal total
        items.append({"item": item, "delta": delta})
        total += delta or 0

    p = ev["pattern"]
    is_range = (regime or {}).get("regime") == "range"
    if regime:
        items.append({"item": f"市场状态：{regime['label']}（ER {regime['er']}，斜率 {regime['slope']}）"
                      + ("，方向项减半/结构+5" if is_range else ""), "delta": None})
    confs = p.get("confirmations") or []
    plain = [c for c in confs if "RSI" not in c]            # RSI 背离单独加权，避免重复计
    if p.get("found"):
        add("结构成立（%s）" % ("双底" if p["type"] == "double_bottom" else "双顶"),
            config.AX_CERT_STRUCTURE)
        if is_range:
            add("震荡市结构补偿（边沿反转结构更可靠）", config.REGIME_RANGE_STRUCT_BONUS)
    if plain:
        add(f"确认信号 {len(plain)} 项（{'、'.join(plain)}）",
            min(len(plain) * config.AX_CERT_CONFIRM, config.AX_CERT_CONFIRM_CAP))
    if p.get("rsi_divergence"):
        add("RSI背离（规则证据）", config.AX_CERT_RSI_DIV)
    mc = (ev.get("momentum") or {}).get("resonance_count", 0)
    if mc:
        add(f"动能三指标共振 {mc}/3", min(mc * config.AX_CERT_MOMENTUM, config.AX_CERT_MOMENTUM_CAP))
    vol_oi = ev.get("volume_oi") or {}
    vol_direction = vol_oi.get("direction")
    if vol_direction is None:  # 兼容旧摘要；不得将多头健康证据奖励给空头。
        q = vol_oi.get("quadrant", "")
        vol_direction = "short" if "空头增仓" in q else ("long" if vol_oi.get("healthy") else "none")
    if target_dir in ("long", "short") and vol_direction == target_dir:
        add("仓量方向与结构一致", config.AX_CERT_VOL_OI)
    dir_disc = config.REGIME_RANGE_DIR_DISCOUNT if is_range else 1.0
    ev_score, ev_dir = ev.get("score", 0), ev.get("direction", "wait")
    if ev_score >= config.DB_SCORE_GO and ev_dir == target_dir:
        add(f"轨道B方向一致且评分 {ev_score}≥{config.DB_SCORE_GO}" + ("（震荡市减半）" if is_range else ""),
            round(config.AX_CERT_DIR_GO * dir_disc))
    elif config.DB_SCORE_WAIT <= ev_score < config.DB_SCORE_GO:
        add(f"轨道B评分 {ev_score}（{config.DB_SCORE_WAIT}–{config.DB_SCORE_GO - 1} 中性区）"
            + ("（震荡市减半）" if is_range else ""),
            round(config.AX_CERT_DIR_WAIT * dir_disc))
    if small_tf_ok:
        add("小周期企稳验证通过", config.AX_CERT_SMALL_TF)
    # 假突破迹象：价已破颈位但无"放量突破颈位"确认（量不足）
    if p.get("found") and price:
        sgn = 1 if p["type"] == "double_bottom" else -1
        if (price - p["neck"]) * sgn > 0 and "放量突破颈位" not in confs:
            add("假突破迹象：价破颈位但量不足", config.AX_CERT_FAKE_BREAKOUT)

    return max(0, min(100, total)), items


# ================= 企稳直通道（resonance 三频共振 → 直接 probe） =================

def direct_probe(price, atr, resonance, klines=None, tick=None):
    """
    企稳直通道：轨道A 三频共振 + 现价不极端 → 直接给 probe 仓（不等双底结构）。
    设计立场：双底结构在 1h 周期天然稀缺（250 天 × 11 品种 main 只触发 3 笔），
    必须存在盈利侧的已确认历史摆动障碍；不按风险倍数凭空制造目标。

    输入：
      price: 现价
      atr: ATR(14)
      resonance: resonance.evaluate() 输出 dict（需含 direction/resonance/legs）
      klines: 1h K线（用于找结构止损位，可选）
    返回：decision dict 或 None（不满足条件）
    """
    if not config.DIRECT_PROBE_ENABLED:
        return None
    if not resonance or not resonance.get("resonance"):
        return None
    direction = resonance.get("direction")  # long / short
    if direction not in ("long", "short"):
        return None
    tick = _tick(tick)
    if not _valid_price(price) or not _valid_price(atr) or not klines or len(klines) < 20:
        return None

    sgn = 1 if direction == "long" else -1

    # 位置检查：现价距 MA20 不能太极端（避免追高/追低）
    if klines and len(klines) >= 20:
        closes = [k["close"] for k in klines]
        ma20 = sum(closes[-20:]) / 20
        dist_ma20 = abs(price - ma20) / atr
        if dist_ma20 > config.DIRECT_PROBE_EXTEND_ATR:
            return None  # 已过度延伸，放弃
    else:
        ma20 = None
        dist_ma20 = 0.0

    # 结构止损：找最近 N 根的摆动低点（做多）/ 高点（做空）
    stop = None
    if klines and len(klines) >= 10:
        look = min(20, len(klines))
        if direction == "long":
            swing = min(k["low"] for k in klines[-look:])
            stop_price = _snap(swing - 0.3 * atr, tick)  # 摆动低点下方 0.3×ATR
        else:
            swing = max(k["high"] for k in klines[-look:])
            stop_price = _snap(swing + 0.3 * atr, tick, up=True)  # 摆动高点上方 0.3×ATR
        stop = {"price": stop_price,
                "basis": f"结构止损：近{look}根摆动{'低' if direction == 'long' else '高'}点{'下方' if direction == 'long' else '上方'}0.3×ATR"}
    risk = (price - stop_price) * sgn
    if not _valid_price(stop_price) or risk <= 0:
        return None
    side = "high" if direction == "long" else "low"
    targets = [_snap(klines[i][side], tick, up=sgn < 0)
               for i in double_bottom._pivots(klines, config.DB_PIVOT_M, side)]
    targets = [p for p in targets if (p - price) * sgn > 0]
    if not targets:
        return None
    target = min(targets, key=lambda p: abs(p - price))
    if (target - price) * sgn / risk < config.DIRECT_PROBE_MIN_RR:
        return None

    d_txt = "做多" if direction == "long" else "做空"
    legs = resonance.get("legs") or {}
    trend_ok = legs.get("trend", {}).get("ok", False)
    filter_ok = legs.get("filter", {}).get("ok", False)
    trigger_ok = legs.get("trigger", {}).get("ok", False)

    summary = (f"企稳直通道（三频共振）：{d_txt}试单仓 {config.AX_POS_PROBE}%，"
               f"止损 {stop['price']}（{stop['basis']}），目标 {target}。"
               f"趋势{'✓' if trend_ok else '✗'} 过滤{'✓' if filter_ok else '✗'} 触发{'✓' if trigger_ok else '✗'}")

    return {
        "location_score": 70,  # 直通道默认位置分（三频共振已含位置过滤）
        "certainty_score": 55,  # 直通道默认确定性分（三频共振本身即证据）
        "quadrant": "probe",
        "quadrant_name": _QUADRANT_NAME["probe"],
        "direction": direction,
        "position_pct": config.AX_POS_PROBE,
        "stop": stop,
        "target": target,
        "target_basis": "已确认历史摆动障碍",
        "entry_point_ref": "direct",
        "regime": None,
        "scores_detail": {
            "location": [{"item": f"企稳直通道：距MA20 {dist_ma20:.2f}×ATR（≤{config.DIRECT_PROBE_EXTEND_ATR}）", "value": 70}],
            "certainty": [{"item": f"三频共振：趋势{'✓' if trend_ok else '✗'} 过滤{'✓' if filter_ok else '✗'} 触发{'✓' if trigger_ok else '✗'}", "delta": 55}],
        },
        "wait_for": None,
        "summary": summary,
    }


# ================= 象限决策 =================

_QUADRANT_NAME = {"main": "主仓", "probe": "试单", "confirm": "确认仓",
                  "avoid": "不做", "standby": "观望"}


def decide(ev, price, atr=None, small_tf_ok=None, regime=None, tick=None):
    """
    双轴评分 → 象限决策主入口。
    ev: double_bottom.evaluate() 输出；price: 现价；
    atr: ATR(14)（缺省按 price×0.005 估算）；small_tf_ok: 小周期验证（True/False/None=缺数据）；
    regime: detect_regime() 输出（缺省 None = 不做市况调整）。
    """
    tick = _tick(tick)
    if atr is None or not _valid_price(atr):
        atr = (price or 1.0) * 0.005
    p = ev.get("pattern") or {}
    found = bool(p.get("found"))
    kind = {"double_bottom": "bottom", "double_top": "top"}.get(p.get("type")) if found else None
    sgn = 1 if kind == "bottom" else -1
    target_dir = {"bottom": "long", "top": "short"}.get(kind, "none")

    def reject(reason):
        return {"location_score": 0, "certainty_score": 0, "quadrant": "avoid",
                "quadrant_name": _QUADRANT_NAME["avoid"], "direction": target_dir,
                "position_pct": 0, "stop": None, "entry_point_ref": "none", "regime": regime,
                "scores_detail": {"location": [], "certainty": []}, "wait_for": reason,
                "summary": "价格/结构校验未通过：" + reason}

    if not _valid_price(price):
        return reject("等待有效现价")
    if found:
        L1, L2, neck = (p.get("L1") or {}).get("price"), (p.get("L2") or {}).get("price"), p.get("neck")
        if kind is None or not all(_valid_price(v) for v in (L1, L2, neck)) or (neck - L2) * sgn <= 0:
            return reject("结构价位无效，等待新的完整结构")
        if (price - L2) * sgn <= -tick:
            return reject("现价已越过右侧结构低点/高点，原结构失效")
        target = (ev.get("plan") or {}).get("target1") or (2 * neck - L1)
        if not _valid_price(target) or (_snap(target, tick, up=sgn < 0) - price) * sgn <= 0:
            return reject("历史目标不在现价盈利侧，等待新的有效空间")

    # ---- 双分 ----
    location_score, loc_items, stop = None, [], None
    if found and price:
        location_score, loc_items, stop = _location_score(ev, kind, price, atr, sgn, tick=tick)
        if not _valid_price(stop["price"]) or (price - stop["price"]) * sgn <= 0:
            return reject("止损不在现价亏损侧，不能形成有效计划")
    else:
        loc_items.append({"item": "无结构，位置无法评估", "value": None})
    certainty_score, cer_items = _certainty_score(ev, target_dir, price, small_tf_ok, regime)

    # ---- 入场点坐标参考（2B/回踩/点3 只是光谱采样，不驱动决策）----
    ref = "none"
    if found and price:
        L2, neck = p["L2"]["price"], p["neck"]
        brk = "放量突破颈位" in (p.get("confirmations") or [])
        if not brk and abs(price - L2) <= config.STAGE_ZONE_ATR * atr:
            ref = "2b"
        elif brk and abs(price - neck) <= config.AX_NECK_ZONE_ATR * atr:
            ref = "retest"
        elif brk and (price - neck) * sgn > 0:
            ref = "point3"

    # ---- 象限 ----
    wait_for = None
    if location_score is None:
        # 分数不能补齐方向、止损及目标。无结构时不生成裸露的试单建议。
        quadrant, pct = "standby", 0
        wait_for = "等双底/双顶结构及有效止损、目标形成后再评估"
        summary = f"无有效结构计划（规则证据分 {certainty_score}）：{wait_for}。"
    else:
        L2, neck = p["L2"]["price"], p["neck"]
        loc_hi = location_score >= config.AX_LOCATION_HIGH
        cer_hi = certainty_score >= config.AX_CERTAINTY_HIGH
        d_txt = "做多" if target_dir == "long" else "做空"
        if loc_hi and cer_hi and small_tf_ok is True:
            depth = min((location_score - config.AX_LOCATION_HIGH) / (100 - config.AX_LOCATION_HIGH),
                        (certainty_score - config.AX_CERTAINTY_HIGH) / (100 - config.AX_CERTAINTY_HIGH))
            pct = round(config.AX_POS_MAIN_MIN
                        + (config.AX_POS_MAIN_MAX - config.AX_POS_MAIN_MIN) * depth)
            quadrant = "main"
            summary = (f"双高象限（位置 {location_score} / 确定性 {certainty_score}）："
                       f"{d_txt}主仓 {pct}%，止损 {stop['price']}（{stop['basis']}）。")
        elif loc_hi:
            quadrant, pct = "probe", config.AX_POS_PROBE
            summary = (f"位置好但证据不足（位置 {location_score} / 确定性 {certainty_score}）："
                       f"{d_txt}试单仓 {pct}%，止损 {stop['price']}（{stop['basis']}），"
                       f"等确认信号补齐再加仓。")
        elif cer_hi:
            quadrant, pct = "confirm", config.AX_POS_CONFIRM
            stop = {"price": _snap(neck - sgn * config.AX_CONFIRM_STOP_ATR * atr, tick, up=sgn < 0),
                    "basis": f"结构宽止损：颈位{'下方' if sgn > 0 else '上方'}{config.AX_CONFIRM_STOP_ATR}×ATR"}
            if not _valid_price(stop["price"]) or (price - stop["price"]) * sgn <= 0:
                return reject("确认仓止损不在现价亏损侧，等待有效入场位置")
            summary = (f"证据足但位置偏差（位置 {location_score} / 确定性 {certainty_score}）："
                       f"{d_txt}确认仓 {pct}%，{stop['basis']}（{stop['price']}），不追满。")
            if (regime or {}).get("regime") == "range":
                # 回测变体 D 验证：震荡市 confirm 13 笔胜率仅 7.7%（-3.15R，追区间边沿被均值回归收割），
                # 降级为试单仓（止损/目标不变，只降仓位）；趋势市 confirm 照旧。
                quadrant, pct = "probe", config.AX_POS_PROBE
                summary = (f"证据足但位置偏差（位置 {location_score} / 确定性 {certainty_score}），"
                           f"震荡市确认仓降级为试单：{d_txt}试单仓 {pct}%，"
                           f"{stop['basis']}（{stop['price']}）。")
        else:
            quadrant, pct = "avoid", 0
            stop = None
            wait_for = (f"等回落至 L2 {L2} 区域（±{config.AX_L2_ZONE_ATR}×ATR）改善位置，"
                        f"或确认信号补齐（当前 {len(p.get('confirmations') or [])} 项）提高确定性")
            summary = (f"双低象限（位置 {location_score} / 确定性 {certainty_score}）：暂不形成计划。{wait_for}。")

    return {
        "location_score": location_score,
        "certainty_score": certainty_score,
        "quadrant": quadrant,
        "quadrant_name": _QUADRANT_NAME[quadrant],
        "direction": target_dir,
        "position_pct": pct,
        "stop": stop,
        "entry_point_ref": ref,
        "regime": regime,
        "scores_detail": {"location": loc_items, "certainty": cer_items},
        "wait_for": wait_for,
        "summary": summary,
    }
