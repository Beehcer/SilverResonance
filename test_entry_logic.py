"""
entry_logic.py 自测：双轴评分引擎（位置分 × 确定性分 → 象限决策）。
合成 evaluate() 输出 dict（不依赖 K 线生成），覆盖：
四象限各一例 + 无结构降级（standby / 降级 probe）+ 假突破扣分 + 位置分 None 路径。
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
        "plan": {"entry": L2, "stop": L2 - 30, "target1": plan_t1, "target2": None}
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
check("3 仓位=50", d["position_pct"] == 50, d["position_pct"])
check("3 位置分<65", d["location_score"] < 65, d["location_score"])
check("3 结构宽止损=颈位下方1×ATR", d["stop"] and d["stop"]["price"] == 6980.0, d["stop"])
check("3 入场点参考=point3", d["entry_point_ref"] == "point3", d["entry_point_ref"])

print("== 4. 双低象限 → avoid（中间位 + 证据薄弱，唯一不做区）==")
d = el.decide(_ev(confs=("L2反转K线",), rsi_div=False, mom=0, healthy=False,
                  score=40, direction="wait"), price=6970.0, atr=ATR, small_tf_ok=None)
show("avoid", d)
check("4 quadrant=avoid", d["quadrant"] == "avoid", d["quadrant"])
check("4 仓位=0", d["position_pct"] == 0)
check("4 wait_for 含具体价位", d["wait_for"] and "6820" in d["wait_for"], d["wait_for"])
check("4 无止损（不交易）", d["stop"] is None)

print("== 5. 无结构降级：位置分 None，仅确定性分驱动 ==")
d = el.decide(_ev(found=False, mom=0, healthy=False, score=0, direction="wait"),
              price=6900.0, atr=ATR, small_tf_ok=None)
show("standby", d)
check("5a 位置分 None", d["location_score"] is None)
check("5a quadrant=standby", d["quadrant"] == "standby", d["quadrant"])
check("5a wait_for 非空", bool(d["wait_for"]))
d = el.decide(_ev(found=False, mom=3, healthy=True, score=60, direction="wait"),
              price=6900.0, atr=ATR, small_tf_ok=True)
show("degraded-probe", d)
check("5b 降级 probe（确定性 48≥45）", d["quadrant"] == "probe", d["quadrant"])
check("5b 仓位=25", d["position_pct"] == 25)
check("5b 位置分 None", d["location_score"] is None)

print("== 6. 假突破扣分：价破颈位但无放量确认 → -15 ==")
d = el.decide(_ev(confs=("L2反转K线",), rsi_div=True, mom=2, healthy=True,
                  score=60, direction="wait"), price=7050.0, atr=ATR, small_tf_ok=None)
show("fake-breakout", d)
fake_items = [i for i in d["scores_detail"]["certainty"] if i["delta"] < 0]
check("6 有假突破负分项", len(fake_items) == 1 and fake_items[0]["delta"] == -15, fake_items)
check("6 确定性分=48（63-15）", d["certainty_score"] == 48, d["certainty_score"])

print("== 7. 做空镜像：双顶结构方向与止损侧 ==")
d = el.decide(_ev(ptype="double_top", L1=7200.0, L2=7180.0, neck=7000.0,
                  confs=("L2反转K线", "RSI顶背离"), direction="short"),
              price=7175.0, atr=ATR, small_tf_ok=True)
show("short-mirror", d)
check("7 direction=short", d["direction"] == "short", d["direction"])
check("7 止损在 L2 上方", d["stop"] and d["stop"]["price"] == 7180.0 + 30, d["stop"])
check("7 quadrant=main", d["quadrant"] == "main", d["quadrant"])

print()
if FAILS:
    print("TESTS_FAILED:", FAILS)
    sys.exit(1)
print("ALL_TESTS_OK")
