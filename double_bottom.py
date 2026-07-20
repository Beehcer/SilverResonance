"""
轨道B：双底/双顶评分引擎 - 纯函数，不依赖行情库，方便单独测试。

输入：K线 dict 列表，至少含 open/high/low/close/volume；
     持仓量字段 "oi"（每根持仓量变化，全 0 / 缺失时降级为 OBV 判断）。
输出：结构化评分 dict（见 evaluate）。

严格无前视偏差：
- 局部极值要求右侧还有 M 根确认，最右 M 根内不会产生"未确认的极值"；
- 放量突破判断里，均量只用截至当根的数据计算；
- RSI/MACD/ATR/OBV 全部由完整收盘价序列递推，不使用任何未来数据。

双顶为双底的完全镜像，同一套函数用 kind 参数化。
"""

import config


# ================= 基础指标（自实现，不依赖 TA-Lib） =================

def sma(values, period):
    """简单移动平均；数据不足返回 None。"""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ma_list(values, period):
    """整列 MA，前 period-1 个为 None。"""
    out = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1:i + 1]) / period
    return out


def _ema_list(values, period):
    """整列 EMA，前 period-1 个为 None。"""
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    out[period - 1] = ema
    for i in range(period, len(values)):
        ema = values[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def _rsi_list(closes, period=None):
    """RSI 整列（Wilder 平滑），与 closes 等长对齐，头部为 None。"""
    period = period or config.DB_RSI_PERIOD
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = 100.0 if avg_loss == 0 else round(100 - 100 / (1 + avg_gain / avg_loss), 2)
    return out


def _macd(closes, fast=12, slow=26, signal=9):
    """返回 (DIF 列, DEA 列)，头部为 None。"""
    ef = _ema_list(closes, fast)
    es = _ema_list(closes, slow)
    dif = [(f - s) if (f is not None and s is not None) else None
           for f, s in zip(ef, es)]
    valid = [d for d in dif if d is not None]
    dea_valid = _ema_list(valid, signal)
    dea, it = [], iter(dea_valid)
    for d in dif:
        dea.append(next(it) if d is not None else None)
    return dif, dea


def _atr(klines, period=None):
    """最新 ATR（Wilder 平滑）；数据不足返回 None。"""
    period = period or config.DB_ATR_PERIOD
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["high"], klines[i]["low"], klines[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _obv(klines):
    """OBV 整列。"""
    out = [0.0]
    for i in range(1, len(klines)):
        v = klines[i].get("volume", 0) or 0
        c, pc = klines[i]["close"], klines[i - 1]["close"]
        out.append(out[-1] + (v if c > pc else (-v if c < pc else 0)))
    return out


# ================= A. 双底/双顶结构识别 =================

def _pivots(klines, m, kind):
    """
    局部极值索引列表。kind='low'（低点）/'high'（高点）。
    第 i 根是极值：其 low/high 是 [i-m, i+m] 区间的最小/最大值。
    右侧必须有 m 根确认（i+m ≤ 最后一根）→ 无前视。
    """
    key = "low" if kind == "low" else "high"
    vals = [k[key] for k in klines]
    n = len(vals)
    idx = []
    for i in range(m, n - m):
        seg = vals[i - m:i + m + 1]
        if kind == "low" and vals[i] == min(seg):
            idx.append(i)
        elif kind == "high" and vals[i] == max(seg):
            idx.append(i)
    return idx


def _find_pattern(klines, kind):
    """
    kind='bottom' 找双底 / 'top' 找双顶（镜像）。
    返回 {"found": bool, "type": ..., "L1": {...}, "L2": {...}, "neck":..., "neck_index":...}
    有多组合法结构时取右底/右顶最新的一对（最贴近当前行情）。
    """
    empty = {"found": False, "type": None}
    n = len(klines)
    if n < config.DB_PIVOT_M * 2 + config.DB_L2_GAP_MIN + 5:
        return empty
    lows = [k["low"] for k in klines]
    highs = [k["high"] for k in klines]
    start = max(0, n - config.DB_LOOKBACK)
    m = config.DB_PIVOT_M
    piv = [i for i in _pivots(klines, m, "low" if kind == "bottom" else "high") if i >= start]

    best = None
    for a in range(len(piv)):
        i1 = piv[a]
        p1 = lows[i1] if kind == "bottom" else highs[i1]
        if p1 == 0:
            continue
        for b in range(a + 1, len(piv)):
            i2 = piv[b]
            if i2 - i1 < config.DB_L2_GAP_MIN:
                continue
            p2 = lows[i2] if kind == "bottom" else highs[i2]
            if abs(p2 - p1) / p1 > config.DB_L2_TOL:
                continue
            # 颈位：L1→L2 之间的反弹最高点（bottom）/ 回抽最低点（top）
            if kind == "bottom":
                seg = highs[i1:i2 + 1]
                neck = max(seg)
                rebound = (neck - p1) / p1
            else:
                seg = lows[i1:i2 + 1]
                neck = min(seg)
                rebound = (p1 - neck) / p1
            if rebound < config.DB_REBOUND_MIN:
                continue
            cand = {"found": True,
                    "type": "double_bottom" if kind == "bottom" else "double_top",
                    "L1": {"price": round(p1, 2), "index": i1},
                    "L2": {"price": round(p2, 2), "index": i2},
                    "neck": round(neck, 2),
                    "neck_index": i1 + seg.index(neck)}
            if best is None or i2 > best["L2"]["index"]:
                best = cand
    return best or empty


# ================= B. "踩稳"确认信号 =================

def _reversal_candle(klines, i, kind):
    """L2 处反转K线：锤子/长下影 或 阳包阴（bottom）；top 完全镜像。"""
    if i < 1 or i >= len(klines):
        return False
    k = klines[i]
    o, h, l, c = k["open"], k["high"], k["low"], k["close"]
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    prev = klines[i - 1]
    if kind == "bottom":
        hammer = lower > 0 and lower >= 2 * body and lower >= 2 * upper
        engulf = (c > o and prev["close"] < prev["open"]
                  and c >= prev["open"] and o <= prev["close"])
        return hammer or engulf
    inv = upper > 0 and upper >= 2 * body and upper >= 2 * lower
    engulf = (c < o and prev["close"] > prev["open"]
              and c <= prev["open"] and o >= prev["close"])
    return inv or engulf


def _breakout_info(klines, pattern, kind):
    """
    L2 之后是否"收盘突破颈位且放量"（量 ≥ 均量×倍数，均量只用截至当根数据）。
    返回 (是否突破, 突破根索引, 突破后的极端价)。
    """
    if not pattern["found"]:
        return False, None, None
    neck = pattern["neck"]
    vols = [k.get("volume", 0) or 0 for k in klines]
    for i in range(pattern["L2"]["index"] + 1, len(klines)):
        vma = sma(vols[:i + 1], config.DB_VOL_MA)
        big_vol = bool(vma and vols[i] >= vma * config.DB_VOL_BREAKOUT)
        c = klines[i]["close"]
        if kind == "bottom" and c > neck and big_vol:
            return True, i, min(x["low"] for x in klines[i:])
        if kind == "top" and c < neck and big_vol:
            return True, i, max(x["high"] for x in klines[i:])
    return False, None, None


def _retest_holds(klines, pattern, kind, brk_i, after_extreme):
    """突破后回踩颈位附近但不破（bottom：回踩低点 > 颈位×0.98；top 镜像）。"""
    if brk_i is None or after_extreme is None or brk_i + 1 >= len(klines):
        return False
    neck = pattern["neck"]
    rest = klines[brk_i + 1:]
    if kind == "bottom":
        came_back = any(x["low"] <= neck * 1.02 for x in rest)  # 回到颈位附近才算回踩
        return came_back and after_extreme > neck * config.DB_RETEST_BUF
    came_back = any(x["high"] >= neck * 0.98 for x in rest)
    return came_back and after_extreme < neck * (2 - config.DB_RETEST_BUF)


def _confirmations(klines, pattern, kind, rsi_list):
    """汇总确认信号名列表 + RSI 背离标记（背离单独加权）。"""
    confs = []
    if _reversal_candle(klines, pattern["L2"]["index"], kind):
        confs.append("L2反转K线")
    brk, brk_i, after_ext = _breakout_info(klines, pattern, kind)
    if brk:
        confs.append("放量突破颈位")
    if _retest_holds(klines, pattern, kind, brk_i, after_ext):
        confs.append("回踩不破颈位")
    rsi_div = False
    i1, i2 = pattern["L1"]["index"], pattern["L2"]["index"]
    if rsi_list[i1] is not None and rsi_list[i2] is not None:
        if kind == "bottom" and rsi_list[i2] > rsi_list[i1]:
            rsi_div = True
        elif kind == "top" and rsi_list[i2] < rsi_list[i1]:
            rsi_div = True
    if rsi_div:
        confs.append("RSI底背离" if kind == "bottom" else "RSI顶背离")
    return confs, rsi_div


# ================= C. 支撑/压力位有效性评分 =================

def _score_level(klines, level, side):
    """
    单个关键位有效性评分 0-100，四因子加权：
    触碰次数(≥3次为强) 30% + 平均反转幅度 30% + 时间新鲜度(半衰期衰减) 20% + 触碰放量 20%。
    """
    if not level:
        return 0.0
    tol = config.DB_LEVEL_TOL
    vols = [k.get("volume", 0) or 0 for k in klines]
    touches = []  # (索引, 触碰后5根内最大反转幅度)
    for i, k in enumerate(klines):
        ref = k["low"] if side == "support" else k["high"]
        if abs(ref - level) / level <= tol:
            seg = klines[i + 1:i + 6]
            if not seg:
                rev = 0.0
            elif side == "support":
                rev = (max(x["high"] for x in seg) - level) / level
            else:
                rev = (level - min(x["low"] for x in seg)) / level
            touches.append((i, max(rev, 0.0)))
    if not touches:
        return 0.0
    n_t = len(touches)
    s_count = min(n_t, 3) / 3                                    # 触碰次数
    s_rev = min(sum(r for _, r in touches) / n_t / 0.05, 1.0)    # 平均反转幅度(5%满分)
    age = len(klines) - 1 - touches[-1][0]                       # 时间新鲜度
    s_fresh = 0.5 ** (age / config.DB_LEVEL_HALF_LIFE)

    def big_vol(i):
        vma = sma(vols[:i + 1], config.DB_VOL_MA)
        return bool(vma and vols[i] >= vma * config.DB_VOL_BREAKOUT)
    s_vol = sum(1 for i, _ in touches if big_vol(i)) / n_t       # 触碰时放量比例

    return round((0.30 * s_count + 0.30 * s_rev + 0.20 * s_fresh + 0.20 * s_vol) * 100, 1)


def level_label(score):
    """≥70 强位，40-69 中性，<40 弱位。"""
    if score >= config.DB_LEVEL_STRONG:
        return "强"
    if score >= config.DB_LEVEL_MID:
        return "中"
    return "弱"


def _levels(klines, pattern):
    """汇总关键位：L1/L2 合并区、颈位、近期摆动高低点；聚合并评分。"""
    price = klines[-1]["close"]
    m = config.DB_PIVOT_M
    sup_src = [klines[i]["low"] for i in _pivots(klines, m, "low")]
    res_src = [klines[i]["high"] for i in _pivots(klines, m, "high")]
    if pattern.get("found"):
        # L1/L2 合并区与颈位同时进两个池，按其与现价的位置关系自动归侧
        # （双底：区在下=支撑/颈位在上=压力；双顶镜像）
        zone = (pattern["L1"]["price"] + pattern["L2"]["price"]) / 2
        sup_src += [zone, pattern["neck"]]
        res_src += [zone, pattern["neck"]]

    def cluster(points):
        points = sorted(points)
        groups = []
        for p in points:
            if groups and abs(p - groups[-1][-1]) / groups[-1][-1] <= config.DB_LEVEL_TOL:
                groups[-1].append(p)
            else:
                groups.append([p])
        return [round(sum(g) / len(g), 2) for g in groups]

    sup = [{"price": p, "score": _score_level(klines, p, "support"),
            "label": level_label(_score_level(klines, p, "support"))}
           for p in cluster(sup_src) if p <= price * 1.001]
    res = [{"price": p, "score": _score_level(klines, p, "resistance"),
            "label": level_label(_score_level(klines, p, "resistance"))}
           for p in cluster(res_src) if p >= price * 0.999]
    sup.sort(key=lambda x: -x["score"])
    res.sort(key=lambda x: -x["score"])
    return {"support": sup[:4], "resistance": res[:4]}


# ================= D. 动能与仓量 =================

def _momentum(klines, kind):
    """动能三选二共振：MACD 金/死叉、RSI 回破50、MA20 斜率转向。kind 控制方向镜像。"""
    closes = [k["close"] for k in klines]
    dif, dea = _macd(closes)
    rsi_list = _rsi_list(closes)
    ma20 = _ma_list(closes, 20)

    # MACD：近5根内金/死叉，并区分零轴上下
    macd_ok, zone = False, "unknown"
    if dif and dif[-1] is not None:
        zone = "above" if dif[-1] > 0 else "below"
        for i in range(max(1, len(dif) - 5), len(dif)):
            if dif[i - 1] is None or dea[i - 1] is None or dif[i] is None or dea[i] is None:
                continue
            if kind == "bottom" and dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
                macd_ok = True
            if kind == "top" and dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
                macd_ok = True

    # RSI：bottom=近10根曾 <40 且现已回升破50；top 镜像（曾 >60 现回落破50）
    rsi_ok = False
    recent = [r for r in rsi_list[-10:] if r is not None]
    if recent and rsi_list[-1] is not None:
        if kind == "bottom":
            rsi_ok = min(recent) < 40 and rsi_list[-1] >= 50
        else:
            rsi_ok = max(recent) > 60 and rsi_list[-1] <= 50

    # MA20 斜率：前段走平/下行，近段转升（bottom）；top 镜像
    slope_ok = False
    if len(ma20) > 9 and None not in (ma20[-1], ma20[-5], ma20[-9]):
        prev_slope = ma20[-5] - ma20[-9]
        cur_slope = ma20[-1] - ma20[-5]
        if kind == "bottom":
            slope_ok = cur_slope > 0 and prev_slope <= 0
        else:
            slope_ok = cur_slope < 0 and prev_slope >= 0

    count = sum([macd_ok, rsi_ok, slope_ok])
    return {"macd": macd_ok, "macd_zone": zone, "rsi": rsi_ok,
            "ma_slope": slope_ok, "resonance_count": count}


def _volume_oi(klines):
    """
    量价持仓四象限（最近5根，价×量×仓三维度方向）。
    无持仓量数据（oi 全 0 / 缺失）时降级 OBV：OBV 创新高而价格未新高 = 资金进场。
    """
    if len(klines) < 12:
        return {"quadrant": "数据不足", "healthy": False}
    closes = [k["close"] for k in klines]
    vols = [k.get("volume", 0) or 0 for k in klines]
    ois = [k.get("oi", 0) or 0 for k in klines]
    p_up = closes[-1] > closes[-6]
    v_up = sum(vols[-5:]) > sum(vols[-10:-5])
    if any(x != 0 for x in ois):
        o_up = sum(ois[-5:]) > 0
        if p_up and v_up and o_up:
            return {"quadrant": "价升量增仓增·健康多头", "healthy": True}
        if p_up and not v_up and not o_up:
            return {"quadrant": "价升量缩仓减·空头回补不可持续", "healthy": False}
        if not p_up and v_up and o_up:
            return {"quadrant": "价跌量增仓增·空头增仓", "healthy": False}
        if not p_up and not v_up and not o_up:
            return {"quadrant": "价跌量缩仓减·多头离场近尾声", "healthy": True}
        name = f"价{'升' if p_up else '跌'}量{'增' if v_up else '缩'}仓{'增' if o_up else '减'}"
        return {"quadrant": name + "·方向不明", "healthy": False}
    obv = _obv(klines)
    look = min(30, len(obv))
    inflow = obv[-1] >= max(obv[-look:]) and closes[-1] < max(closes[-look:])
    return {"quadrant": "无持仓数据·OBV" + ("资金进场" if inflow else "资金平淡"),
            "healthy": inflow}


# ================= E. 综合评分 =================

def _build_candidate(klines, pattern, kind, confs, rsi_div):
    """对一个合法结构算总分并组装完整输出。"""
    price = klines[-1]["close"]
    levels = _levels(klines, pattern)
    momentum = _momentum(klines, kind)
    vol_oi = _volume_oi(klines)

    # 结构分 0-30：找到结构 12 + 确认信号(至多计3个)×4 + RSI背离单独加权 6
    s_struct = min(12 + min(len(confs), 3) * 4 + (6 if rsi_div else 0), 30)
    # 位阶分 0-25：方向侧最强关键位折算
    side = levels["support"] if kind == "bottom" else levels["resistance"]
    s_level = round((side[0]["score"] if side else 0) / 100 * 25)
    # 动能分 0-25：三选二共振折算
    s_mom = round(momentum["resonance_count"] / 3 * 25)
    # 仓量分 0-20：按方向解释四象限
    q = vol_oi["quadrant"]
    if kind == "bottom":
        s_vol = 20 if vol_oi["healthy"] else (12 if ("尾声" in q or "资金进场" in q) else 4)
    else:
        s_vol = 20 if "空头增仓" in q else (12 if "空头回补" in q else 4)

    score = s_struct + s_level + s_mom + s_vol
    direction = "wait"
    if score >= config.DB_SCORE_GO:
        direction = "long" if kind == "bottom" else "short"

    # 交易计划（仅评分达标时给出）
    plan = None
    if direction != "wait":
        atr = _atr(klines) or price * 0.01
        if kind == "bottom":
            stop = round(pattern["L2"]["price"] - config.DB_STOP_ATR * atr, 2)
            t1 = round(pattern["neck"] + (pattern["neck"] - pattern["L1"]["price"]), 2)
            strong = [l for l in levels["resistance"]
                      if l["score"] >= config.DB_LEVEL_STRONG and l["price"] > price]
            t2 = round(min(strong, key=lambda l: l["price"])["price"], 2) if strong else None
        else:
            stop = round(pattern["L2"]["price"] + config.DB_STOP_ATR * atr, 2)
            t1 = round(pattern["neck"] - (pattern["L1"]["price"] - pattern["neck"]), 2)
            strong = [l for l in levels["support"]
                      if l["score"] >= config.DB_LEVEL_STRONG and l["price"] < price]
            t2 = round(max(strong, key=lambda l: l["price"])["price"], 2) if strong else None
        plan = {"entry": round(price, 2), "stop": stop, "target1": t1, "target2": t2}

    # 人话理由（给面板/推送用）
    cn = "双底" if kind == "bottom" else "双顶"
    reasons = [
        f"识别到{cn}结构：L1={pattern['L1']['price']} L2={pattern['L2']['price']} 颈位={pattern['neck']}",
        "确认信号：" + "、".join(confs) + (f"（共{len(confs)}项）" if confs else ""),
    ]
    if rsi_div:
        reasons.append("RSI" + ("底" if kind == "bottom" else "顶") + "背离（最强确认，单独加权）")
    if side:
        reasons.append(f"方向侧最强关键位 {side[0]['price']} 强度{side[0]['score']}/100（{side[0]['label']}位）")
    reasons.append(f"动能共振 {momentum['resonance_count']}/3（MACD{'✓' if momentum['macd'] else '✗'}"
                   f" RSI{'✓' if momentum['rsi'] else '✗'} MA斜率{'✓' if momentum['ma_slope'] else '✗'}）")
    reasons.append(f"仓量象限：{q}")
    if direction != "wait":
        reasons.append(f"综合 {score} 分 ≥ {config.DB_SCORE_GO}：可{'做多' if direction == 'long' else '做空'}，"
                       f"止损{plan['stop']} 目标1={plan['target1']}")
    elif score >= config.DB_SCORE_WAIT:
        reasons.append(f"综合 {score} 分：观望，等确认信号补齐或价格就位")
    else:
        reasons.append(f"综合 {score} 分 < {config.DB_SCORE_WAIT}：不做")

    return {
        "score": score,
        "direction": direction,
        "pattern": {"found": True, "type": pattern["type"],
                    "L1": pattern["L1"], "L2": pattern["L2"], "neck": pattern["neck"],
                    "confirmations": confs, "rsi_divergence": rsi_div},
        "levels": levels,
        "momentum": momentum,
        "volume_oi": vol_oi,
        "plan": plan,
        "reasons": reasons,
    }


def evaluate(klines):
    """
    轨道B 综合评分入口。
    klines: K线 dict 列表（open/high/low/close/volume[/oi]），只用截至最后一根的数据。
    返回：{"score","direction","pattern","levels","momentum","volume_oi","plan","reasons"}
    双底/双顶两个方向都会重算，取总分更高者；确认信号不足两个的结构判无效。
    """
    out = {
        "score": 0, "direction": "wait",
        "pattern": {"found": False, "type": None, "L1": None, "L2": None,
                    "neck": None, "confirmations": [], "rsi_divergence": False},
        "levels": {"support": [], "resistance": []},
        "momentum": {"macd": False, "rsi": False, "ma_slope": False, "resonance_count": 0},
        "volume_oi": {"quadrant": "数据不足", "healthy": False},
        "plan": None, "reasons": [],
    }
    if not klines or len(klines) < 40:
        out["reasons"] = ["K线数据不足（<40根），无法评估双底/双顶结构"]
        return out

    rsi_list = _rsi_list([k["close"] for k in klines])
    best = None
    for kind in ("bottom", "top"):
        pattern = _find_pattern(klines, kind)
        if not pattern["found"]:
            continue
        confs, rsi_div = _confirmations(klines, pattern, kind, rsi_list)
        if len(confs) < 2:
            continue  # 确认信号须 ≥2 才算有效结构
        cand = _build_candidate(klines, pattern, kind, confs, rsi_div)
        if best is None or cand["score"] > best["score"]:
            best = cand

    if best is None:
        # 无有效结构：仍给位阶/动能/仓量参考
        out["levels"] = _levels(klines, {"found": False})
        out["momentum"] = _momentum(klines, "bottom")
        out["volume_oi"] = _volume_oi(klines)
        out["reasons"] = ["回溯窗口内未发现满足条件的双底/双顶（结构缺失或确认信号不足两个）"]
        return out
    return best
