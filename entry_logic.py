"""
双轴评分引擎（一期）：位置分 × 确定性分 → 象限决策。
纯函数，不依赖行情库，方便单独测试；不改 resonance.py / double_bottom.py 的判定语义。

设计立场：位置优先 vs 确定性优先是两种入场逻辑（取舍立场），
2B/回踩/点3 只是光谱上的三个入场点采样（坐标参考，不驱动决策）。
位置分与确定性分各自独立量化，入场方案由两个分数驱动；
唯一的"不做"区是双低象限（avoid），消灭"永远无法开仓"。

输入：轨道B double_bottom.evaluate() 的输出 dict + 现价
     + 可选 ATR（调用方用 double_bottom._atr 算好传入；缺省按价格 0.5% 估算）
     + 可选小周期验证标记（30分/3分级别企稳：短均拐头+收盘站稳，
       复用 resonance.trigger_ok 思路，由调用方判定后以 bool 传入；缺数据传 None 不扣分）。
输出契约：见 decide() 返回 dict。
"""

import config
import double_bottom   # 只读复用 _ma_list（不改其判定语义）


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


def _location_score(ev, kind, price, atr, sgn):
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
    stop_price = round(L2 - sgn * config.DB_STOP_ATR * atr, 2)
    plan = ev.get("plan") or {}
    t1 = plan.get("target1") or round(neck + (neck - L1) * sgn, 2)
    risk = abs(price - stop_price)
    rr = abs(t1 - price) / risk if risk else 0.0
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
        add("RSI背离（最强确认）", config.AX_CERT_RSI_DIV)
    mc = (ev.get("momentum") or {}).get("resonance_count", 0)
    if mc:
        add(f"动能三指标共振 {mc}/3", min(mc * config.AX_CERT_MOMENTUM, config.AX_CERT_MOMENTUM_CAP))
    if (ev.get("volume_oi") or {}).get("healthy"):
        add("仓量象限健康", config.AX_CERT_VOL_OI)
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


# ================= 象限决策 =================

_QUADRANT_NAME = {"main": "主仓", "probe": "试单", "confirm": "确认仓",
                  "avoid": "不做", "standby": "观望"}


def decide(ev, price, atr=None, small_tf_ok=None, regime=None):
    """
    双轴评分 → 象限决策主入口。
    ev: double_bottom.evaluate() 输出；price: 现价；
    atr: ATR(14)（缺省按 price×0.005 估算）；small_tf_ok: 小周期验证（True/False/None=缺数据）；
    regime: detect_regime() 输出（缺省 None = 不做市况调整）。
    """
    if atr is None or atr <= 0:
        atr = (price or 1.0) * 0.005
    p = ev.get("pattern") or {}
    found = bool(p.get("found"))
    kind = {"double_bottom": "bottom", "double_top": "top"}.get(p.get("type")) if found else None
    sgn = 1 if kind == "bottom" else -1
    target_dir = {"bottom": "long", "top": "short"}.get(kind, "none")

    # ---- 双分 ----
    location_score, loc_items, stop = None, [], None
    if found and price:
        location_score, loc_items, stop = _location_score(ev, kind, price, atr, sgn)
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
        # 位置分 None：降级为仅确定性分（结构/确认分天然拿不到，用降级阈值）
        if certainty_score >= config.AX_CERT_DEGRADED:
            quadrant, pct = "probe", config.AX_POS_PROBE
            summary = (f"无结构可评估位置，但证据较足（确定性 {certainty_score}）："
                       f"仅可 {pct}% 试单仓，须自设止损、破位即走。")
        else:
            quadrant, pct = "standby", 0
            wait_for = "等双底/双顶结构形成（左底/左顶枢轴 + 颈位出现）后再评估位置"
            summary = f"无结构且证据不足（确定性 {certainty_score}）：{wait_for}。"
    else:
        L2, neck = p["L2"]["price"], p["neck"]
        loc_hi = location_score >= config.AX_LOCATION_HIGH
        cer_hi = certainty_score >= config.AX_CERTAINTY_HIGH
        d_txt = "做多" if target_dir == "long" else "做空"
        if loc_hi and cer_hi:
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
            stop = {"price": round(neck - sgn * config.AX_CONFIRM_STOP_ATR * atr, 2),
                    "basis": f"结构宽止损：颈位{'下方' if sgn > 0 else '上方'}{config.AX_CONFIRM_STOP_ATR}×ATR"}
            summary = (f"证据足但位置偏差（位置 {location_score} / 确定性 {certainty_score}）："
                       f"{d_txt}确认仓 {pct}%，{stop['basis']}（{stop['price']}），不追满。")
        else:
            quadrant, pct = "avoid", 0
            stop = None
            wait_for = (f"等回落至 L2 {L2} 区域（±{config.AX_L2_ZONE_ATR}×ATR）改善位置，"
                        f"或确认信号补齐（当前 {len(p.get('confirmations') or [])} 项）提高确定性")
            summary = (f"双低象限（位置 {location_score} / 确定性 {certainty_score}）：唯一不做区。{wait_for}。")

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
