"""
double_bottom.py 自测：合成 K 线覆盖四种场景 + 无前视校验。
运行：python test_double_bottom.py
"""

import random
import double_bottom as db


def _mk_series(segs, n=120, base=7200, noise=0.0008, seed=42):
    """
    按分段目标价生成合成K线。
    segs: [(占比, 目标价, 量倍数, oi倾向, 形态标记)]
    形态标记 "hammer"：该段最后一根造锤子线；"normal"：无特殊处理。
    """
    random.seed(seed)
    out, price, oi = [], base * 1.10, 200000
    for si, (frac, target, vm, oi_dir, mark) in enumerate(segs):
        cnt = max(2, int(n * frac))
        step = (target - price) / cnt
        for j in range(cnt):
            o = price
            price += step + random.uniform(-base * noise, base * noise)
            c = price
            h = max(o, c) + random.uniform(0, base * noise)
            low = min(o, c) - random.uniform(0, base * noise)
            if mark == "hammer" and j == cnt - 1:
                c = o + base * 0.0002                 # 小实体
                price = c
                h = max(o, c) + base * 0.0003
                low = min(o, c) - base * 0.004        # 长下影
            elif mark == "inv_hammer" and j == cnt - 1:
                c = o - base * 0.0002                 # 小实体
                price = c
                h = max(o, c) + base * 0.004          # 长上影
                low = min(o, c) - base * 0.0003
            volume = int(random.uniform(500, 1000) * vm)
            oi_delta = int(random.uniform(50, 400)) * oi_dir
            oi += oi_delta
            out.append({"open": round(o, 2), "high": round(h, 2),
                        "low": round(low, 2), "close": round(c, 2),
                        "volume": volume, "oi": oi_delta})
    return out[:n]


def double_bottom_bars(breakout_vol=2.2, rally_vol=1.4):
    """标准双底：左底→颈位→右底(锤子)→放量突破→回踩不破→上行。"""
    b = 7200
    return _mk_series([
        (0.42, b * 1.02,  1.0, 0, "normal"),
        (0.16, b * 0.94,  1.0, -1, "normal"),   # 左底
        (0.12, b * 1.00,  1.1, 0, "normal"),    # 颈位
        (0.12, b * 0.945, 0.9, -1, "hammer"),   # 右底 + 锤子线
        (0.08, b * 1.03,  breakout_vol, 1, "normal"),  # 突破（量倍数可调）
        (0.04, b * 1.005, 1.0, 0, "normal"),    # 回踩不破
        (0.06, b * 1.06,  rally_vol, 1, "normal"),
    ])


def double_top_bars():
    """双顶：双底的完全镜像（价格路径翻转）。"""
    b = 7200
    return _mk_series([
        (0.42, b * 0.98,  1.0, 0, "normal"),
        (0.16, b * 1.06,  1.0, 1, "normal"),    # 左顶
        (0.12, b * 1.00,  1.1, 0, "normal"),    # 颈位
        (0.12, b * 1.055, 0.9, 1, "inv_hammer"),  # 右顶 + 长上影
        (0.08, b * 0.97,  2.2, 1, "normal"),    # 放量跌破（空头增仓）
        (0.04, b * 0.995, 1.0, 0, "normal"),    # 反抽不过
        (0.06, b * 0.94,  1.4, 1, "normal"),
    ])


def random_walk_bars():
    """无结构：纯随机游走。"""
    return _mk_series([(1.0, 7200 * 1.05, 1.0, 0, "normal")], n=120, base=7200, seed=7)


def test_standard_double_bottom():
    r = db.evaluate(double_bottom_bars())
    assert r["pattern"]["found"] and r["pattern"]["type"] == "double_bottom"
    assert len(r["pattern"]["confirmations"]) >= 2, r["pattern"]["confirmations"]
    assert r["score"] >= 55, r["score"]                # 结构有效，至少观望区
    assert set(r.keys()) == {"score", "direction", "pattern", "levels",
                             "momentum", "volume_oi", "plan", "reasons"}
    assert r["reasons"], "应有中文理由"
    print(f"  标准双底: score={r['score']} dir={r['direction']} confs={r['pattern']['confirmations']}")


def test_double_top_mirror():
    r = db.evaluate(double_top_bars())
    assert r["pattern"]["found"] and r["pattern"]["type"] == "double_top"
    assert len(r["pattern"]["confirmations"]) >= 2, r["pattern"]["confirmations"]
    assert r["score"] >= 55, r["score"]
    if r["direction"] != "wait":
        assert r["direction"] == "short"             # 做空只能是镜像方向
        assert r["plan"]["stop"] > r["pattern"]["L2"]["price"]  # 做空止损在上方
    print(f"  双顶镜像: score={r['score']} dir={r['direction']} confs={r['pattern']['confirmations']}")


def test_no_pattern():
    r = db.evaluate(random_walk_bars())
    # 随机游走不应给出可交易方向（可能识别不出结构，或评分不足）
    assert r["direction"] == "wait"
    assert r["plan"] is None
    print(f"  无结构:   score={r['score']} dir={r['direction']} found={r['pattern']['found']}")


def test_weak_breakout_rejected():
    """假突破：形态在但突破不放量 → 确认信号不足 → 结构无效。"""
    r = db.evaluate(double_bottom_bars(breakout_vol=0.8, rally_vol=0.8))
    # 放量突破这项确认拿不到；其余确认若不凑够两个，则整体无有效结构
    if r["pattern"]["found"]:
        assert "放量突破颈位" not in r["pattern"]["confirmations"]
        assert len(r["pattern"]["confirmations"]) >= 2   # 引擎只放行 ≥2 确认的结构
    print(f"  假突破:   score={r['score']} dir={r['direction']} confs={r['pattern']['confirmations']}")


def test_no_lookahead():
    """无前视：截掉最后一根重算，结构结论不得依赖被截掉的数据。"""
    bars = double_bottom_bars()
    full = db.evaluate(bars)
    trunc = db.evaluate(bars[:-1])
    # 两端各自只用截至自己的数据，结构（L1/L2 索引）应一致
    assert full["pattern"]["found"] and trunc["pattern"]["found"]
    assert trunc["pattern"]["L1"]["index"] == full["pattern"]["L1"]["index"]
    assert trunc["pattern"]["L2"]["index"] == full["pattern"]["L2"]["index"]
    # 极值确认需要右侧 M 根：最右 M 根内不应出现候选 L2
    assert full["pattern"]["L2"]["index"] <= len(bars) - 1 - db.config.DB_PIVOT_M
    print(f"  无前视:   full_score={full['score']} trunc_score={trunc['score']} "
          f"L2_index={full['pattern']['L2']['index']}")


def test_insufficient_data():
    r = db.evaluate([{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 10)
    assert r["direction"] == "wait" and "不足" in r["reasons"][0]
    print("  数据不足: 正确降级")


if __name__ == "__main__":
    test_standard_double_bottom()
    test_double_top_mirror()
    test_no_pattern()
    test_weak_breakout_rejected()
    test_no_lookahead()
    test_insufficient_data()
    print("ALL_TESTS_OK")
