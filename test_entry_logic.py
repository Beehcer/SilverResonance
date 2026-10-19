"""
entry_logic.py 自测：双轴评分引擎（位置分 × 确定性分 → 象限决策）。
合成 evaluate() 输出 dict（不依赖 K 线生成），覆盖：
四象限各一例 + 无结构待机 + 假突破扣分 + 结构/方向/小周期确认硬门禁。
运行：python test_entry_logic.py
"""

import sys
import entry_logic as el


def _ev(found=True, ptype="double_bottom", L1=6800.0, L2=6820.0, neck=7000.0,
        confs=("L2反转K线", "放量突破颈位", "回踩不破颈位"), rsi_div=True,
        mom=3, healthy=True, score=80, direction="long", plan_t1=7200.0,
        support=None, resistance=None):
    """合成一份轨道B evaluate() 输出（字段与 double_bottom.evaluate 契约一致）。"""
    return {
        "score": score, "direction": direction,
        "pattern": {"found": found, "type": ptype if found else None,
                    "L1": {"price": L1, "index": 10} if found else None,
                    "L2": {"price": L2, "index": 30} if found else None,
                    "neck": neck if found else None,
                    "confirmations": list(confs) if found else [],
                    "rsi_divergence": rsi_div if found else False},
        "levels": {"support": support or [], "resistance": resistance or []},
        "momentum": {"macd": mom >= 1, "rsi": mom >= 2, "ma_slope": mom >= 3,
                     "resonance_count": mom},
        "volume_oi": {"quadrant": "价升量增仓增·健康多头" if healthy else "价跌量增仓增·空头增仓",
                      "healthy": healthy},
        "plan": {"entry": L2, "stop": L2 + (30 if ptype == "double_top" else -30),
                 "target1": plan_t1, "target2": None}
        if (found and direction != "wait") else None,
        "reasons": [],
    }


ATR = 20.0
FAILS = []


def check(name, cond, info=""):
    tag = "OK " if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" | {info}" if info else ""))
    if not cond:
        FAILS.append(name)


def show(tag, d):
    print(f"  {tag}: loc={d['location_score']} cert={d['certainty_score']} "
          f"quadrant={d['quadrant']} pct={d['position_pct']} ref={d['entry_point_ref']}")
    print(f"    summary: {d['summary']}")


print("== 1. 双高象限 → main（贴 L2 + 证据齐全）==")
d = el.decide(_ev(confs=("L2反转K线", "回踩不破颈位", "RSI底背离")), price=6825.0,
              atr=ATR, small_tf_ok=True)
show("main", d)
check("1 quadrant=main", d["quadrant"] == "main", d["quadrant"])
check("1 位置分≥65", d["location_score"] >= 65, d["location_score"])
check("1 确定性分≥60", d["certainty_score"] >= 60, d["certainty_score"])
check("1 仓位 75–100", 75 <= d["position_pct"] <= 100, d["position_pct"])
check("1 止损=L2下方1.5×ATR", d["stop"] and d["stop"]["price"] == 6790.0, d["stop"])
check("1 入场点参考=2b", d["entry_point_ref"] == "2b", d["entry_point_ref"])
check("1 wait_for 为空", d["wait_for"] is None)

print("== 2. 位置高 + 确定性低 → probe（同位置，证据薄弱）==")
d = el.decide(_ev(confs=("L2反转K线",), rsi_div=False, mom=0, healthy=False,
                  score=40, direction="wait"), price=6825.0, atr=ATR, small_tf_ok=None)
show("probe", d)
check("2 quadrant=probe", d["quadrant"] == "probe", d["quadrant"])
check("2 仓位=25", d["position_pct"] == 25, d["position_pct"])
check("2 确定性分=25", d["certainty_score"] == 25, d["certainty_score"])
check("2 有止损（试单也要带）", d["stop"] is not None)

print("== 3. 确定性高 + 位置低 → confirm（证据齐全但突破后延伸）==")
d = el.decide(_ev(), price=7100.0, atr=ATR, small_tf_ok=True)
show("confirm", d)
check("3 quadrant=confirm", d["quadrant"] == "confirm", d["quadrant"])
check("3 仓位=25（修复后：50→25，回测变体 C）", d["position_pct"] == 25, d["position_pct"])
check("3 位置分<65", d["location_score"] < 65, d["location_score"])
check("3 结构宽止损=颈位下方1×ATR", d["stop"] and d["stop"]["price"] == 6980.0, d["stop"])
check("3 入场点参考=point3", d["entry_point_ref"] == "point3", d["entry_point_ref"])

print("== 4. 双低象限 → avoid（突破后位置偏离 + 证据薄弱）==")
d = el.decide(_ev(confs=("L2反转K线",), rsi_div=False, mom=0, healthy=False,
                  score=40, direction="wait"), price=7040.0, atr=ATR, small_tf_ok=None)
show("avoid", d)
check("4 quadrant=avoid", d["quadrant"] == "avoid", d["quadrant"])
check("4 仓位=0", d["position_pct"] == 0)
check("4 wait_for 含具体价位", d["wait_for"] and "6820" in d["wait_for"], d["wait_for"])
check("4 无止损（不交易）", d["stop"] is None)

print("== 5. 无结构待机：动量与小周期证据不能凭空产生方向和止损 ==")
d = el.decide(_ev(found=False, mom=0, healthy=False, score=0, direction="wait"),
              price=6900.0, atr=ATR, small_tf_ok=None)
show("standby", d)
check("5a 位置分 None", d["location_score"] is None)
check("5a quadrant=standby", d["quadrant"] == "standby", d["quadrant"])
check("5a wait_for 非空", bool(d["wait_for"]))
d = el.decide(_ev(found=False, mom=3, healthy=True, score=60, direction="wait"),
              price=6900.0, atr=ATR, small_tf_ok=True)
show("no-structure-standby", d)
check("5b 有动量也必须 standby", d["quadrant"] == "standby", d["quadrant"])
check("5b 仓位=0", d["position_pct"] == 0)
check("5b 位置分 None", d["location_score"] is None)
check("5b 不制造止损或交易方向", d["stop"] is None and d["direction"] == "none", d["direction"])
check("5b 等待结构说明非空", bool(d["wait_for"]))

print("== 6. 假突破扣分：价破颈位但无放量确认 → -15 ==")
d = el.decide(_ev(confs=("L2反转K线",), rsi_div=True, mom=2, healthy=True,
                  score=60, direction="wait"), price=7050.0, atr=ATR, small_tf_ok=None)
show("fake-breakout", d)
fake_items = [i for i in d["scores_detail"]["certainty"] if i["delta"] < 0]
check("6 有假突破负分项", len(fake_items) == 1 and fake_items[0]["delta"] == -15, fake_items)
check("6 确定性分=48（63-15）", d["certainty_score"] == 48, d["certainty_score"])

print("== 7. 做空镜像：双顶结构方向与止损侧 ==")
d = el.decide(_ev(ptype="double_top", L1=7200.0, L2=7180.0, neck=7000.0,
                  confs=("L2反转K线", "RSI顶背离"), direction="short", healthy=False,
                  plan_t1=6800.0),
              price=7175.0, atr=ATR, small_tf_ok=True)
show("short-mirror", d)
check("7 direction=short", d["direction"] == "short", d["direction"])
check("7 止损在 L2 上方", d["stop"] and d["stop"]["price"] == 7180.0 + 30, d["stop"])
check("7 quadrant=main", d["quadrant"] == "main", d["quadrant"])
check("7 无 regime 时输出 regime=None", d["regime"] is None)


def _bars(n, step_fn, base=7000.0):
    """合成K线：step_fn(i, px) -> 新价。"""
    out, px = [], base
    for i in range(n):
        o = px
        px = step_fn(i, px)
        out.append({"open": round(o, 2), "close": round(px, 2),
                    "high": round(max(o, px) * 1.0005, 2), "low": round(min(o, px) * 0.9995, 2),
                    "volume": 1000, "oi": 10})
    return out


print("== 8. detect_regime：稳涨序列 → trend_up ==")
rg = el.detect_regime(_bars(60, lambda i, px: px * 1.004))
print(f"  regime={rg['regime']} er={rg['er']} slope={rg['slope']} label={rg['label']}")
check("8 trend_up", rg["regime"] == "trend_up", rg)
check("8 ER 接近 1", rg["er"] >= 0.9, rg["er"])
check("8 label=趋势市·多", rg["label"] == "趋势市·多")

print("== 9. detect_regime：拉锯序列 → range ==")
rg = el.detect_regime(_bars(60, lambda i, px: px * (1.004 if i % 2 == 0 else 0.996)))
print(f"  regime={rg['regime']} er={rg['er']} slope={rg['slope']} label={rg['label']}")
check("9 range", rg["regime"] == "range", rg)
check("9 ER 接近 0", rg["er"] < 0.15, rg["er"])

print("== 10. 震荡市：方向项减半（+15→+8）+ 结构补偿 +5 ==")
RG_RANGE = {"regime": "range", "er": 0.12, "slope": 0.0004, "label": "震荡市"}
RG_TREND = {"regime": "trend_up", "er": 0.71, "slope": 0.011, "label": "趋势市·多"}
EV1 = _ev(confs=("L2反转K线", "回踩不破颈位", "RSI底背离"))
d_trend = el.decide(EV1, price=6825.0, atr=ATR, small_tf_ok=True, regime=RG_TREND)
d_range = el.decide(EV1, price=6825.0, atr=ATR, small_tf_ok=True, regime=RG_RANGE)
show("trend ", d_trend)
show("range ", d_range)
cer_r = d_range["scores_detail"]["certainty"]
dir_item = [i for i in cer_r if "方向一致" in i["item"]]
check("10 震荡市方向项=+8", dir_item and dir_item[0]["delta"] == 8, dir_item)
check("10 方向项注明减半", dir_item and "减半" in dir_item[0]["item"])
bonus = [i for i in cer_r if "结构补偿" in i["item"]]
check("10 震荡市结构补偿+5", bonus and bonus[0]["delta"] == 5, bonus)
rg_row = [i for i in cer_r if i["item"].startswith("市场状态")]
check("10 市场状态明细行", len(rg_row) == 1 and rg_row[0]["delta"] is None, rg_row)
check("10 趋势市不调整（95 vs 93）",
      d_trend["certainty_score"] == 95 and d_range["certainty_score"] == 93,
      (d_trend["certainty_score"], d_range["certainty_score"]))
check("10 输出含 regime 块", d_range["regime"] and d_range["regime"]["regime"] == "range")

print("== 11. 数据不足时 detect_regime 保守返回 range ==")
rg = el.detect_regime(_bars(10, lambda i, px: px * 1.01))
check("11 短序列→range", rg["regime"] == "range" and rg["er"] == 0.0, rg)

print("== 12. confirm 修复：震荡市 confirm 降级为 probe，趋势市 confirm 不变 ==")
# 同一份"确定性高+位置低"输入（同用例 3），仅 regime 不同
EV_CF = _ev()
d_tr = el.decide(EV_CF, price=7100.0, atr=ATR, small_tf_ok=True, regime=RG_TREND)
d_ra = el.decide(EV_CF, price=7100.0, atr=ATR, small_tf_ok=True, regime=RG_RANGE)
show("trend-confirm", d_tr)
show("range-demoted", d_ra)
check("12 趋势市保持 confirm", d_tr["quadrant"] == "confirm", d_tr["quadrant"])
check("12 趋势市 confirm 仓位=25", d_tr["position_pct"] == 25, d_tr["position_pct"])
check("12 震荡市降级为 probe", d_ra["quadrant"] == "probe", d_ra["quadrant"])
check("12 降级后仓位=25（AX_POS_PROBE）", d_ra["position_pct"] == 25, d_ra["position_pct"])
check("12 降级 summary 注明", "震荡市确认仓降级为试单" in d_ra["summary"], d_ra["summary"])
check("12 降级仍带颈位宽止损", d_ra["stop"] and d_ra["stop"]["price"] == 6980.0, d_ra["stop"])
check("12 降级 wait_for 为空", d_ra["wait_for"] is None)

print("== 13. 硬门禁：目标在亏损侧 / 结构破坏，即使满分也禁止 ==")
bad_short = _ev(ptype="double_top", L1=7200.0, L2=7180.0, neck=7000.0,
                direction="short", healthy=False, score=100, plan_t1=7200.0)
for name, ev, px in (("做空目标高于入场", bad_short, 7175.0),
                     ("多头结构已跌破L2", _ev(score=100), 6810.0)):
    d = el.decide(ev, price=px, atr=ATR, small_tf_ok=True, tick=1.0)
    check(name + "必须avoid", d["quadrant"] == "avoid", d["quadrant"])
    check(name + "不输出仓位和止损", d["position_pct"] == 0 and d["stop"] is None)
    check(name + "明确等待原因", bool(d["wait_for"]))

print("== 14. 硬门禁：小周期未确认不能main ==")
for small_ok in (False, None):
    d = el.decide(_ev(confs=("L2反转K线", "回踩不破颈位", "RSI底背离")),
                  price=6825.0, atr=ATR, small_tf_ok=small_ok)
    check(f"小周期{small_ok}不能main", d["quadrant"] == "probe", d["quadrant"])
    check(f"小周期{small_ok}仅试探且带止损", d["position_pct"] == 25 and d["stop"] is not None)

print()
if FAILS:
    print("TESTS_FAILED:", FAILS)
    sys.exit(1)
print("ALL_TESTS_OK")
