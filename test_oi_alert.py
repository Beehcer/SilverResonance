"""
test_oi_alert.py — oi_alert.detect_surge 自测
  1. 多头增仓（价涨+oi正大）触发 long_add
  2. 空头增仓（价跌+oi负大）触发 short_add
  3. 背离（价涨但 oi 负）不触发
  4. 倍数不足（ratio<mult）不触发
  5. min_abs 门槛
  6. vol_mult 量能过滤
  7. 数据不足返回 None
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oi_alert

def mk_bars(n, closes, vols, ois):
    """构造 K线列表。"""
    out = []
    for i in range(n):
        out.append({"close": closes[i], "volume": vols[i], "oi": ois[i],
                    "dt": f"2025-01-01 {i:02d}:00"})
    return out

checks = []
def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))

# 1. 多头增仓：基线 20 根 oi 小幅随机，最后一根价涨且 oi=+8000
c = [100.0 + i * 0.1 for i in range(25)]
v = [1000] * 24 + [3000]
o = [200] * 24 + [8000]     # 基线 ~200，8000 = 40×
bars = mk_bars(25, c, v, o)
a = oi_alert.detect_surge(bars)
check("1 多头增仓触发", a and a["direction"] == "long_add", str(a))
check("1 ratio", a and a["ratio"] >= 5, str(a and a.get("ratio")))

# 2. 空头增仓：价跌 + oi 大负
c2 = [100.0 - i * 0.1 for i in range(25)]
o2 = [150] * 24 + [-9000]
bars2 = mk_bars(25, c2, v, o2)
a2 = oi_alert.detect_surge(bars2)
check("2 空头增仓触发", a2 and a2["direction"] == "short_add", str(a2))

# 3. 背离：价涨但 oi 负大 → 不触发
c3 = [100.0 + i * 0.1 for i in range(25)]
o3 = [150] * 24 + [-9000]
a3 = oi_alert.detect_surge(mk_bars(25, c3, v, o3))
check("3 背离不触发", a3 is None, str(a3))

# 4. 倍数不足：oi=600, 基线200 → ratio=3 < 5
o4 = [200] * 24 + [600]
a4 = oi_alert.detect_surge(mk_bars(25, c, v, o4))
check("4 倍数不足不触发", a4 is None, str(a4))

# 5. min_abs
a5 = oi_alert.detect_surge(mk_bars(25, c, v, o), min_abs=10000)
check("5 min_abs过滤", a5 is None, str(a5))

# 6. vol_mult 量能过滤
a6 = oi_alert.detect_surge(mk_bars(25, c, [1000] * 25, o), vol_mult=1.5)
check("6 量能不足过滤", a6 is None, str(a6))
a6b = oi_alert.detect_surge(mk_bars(25, c, v, o), vol_mult=1.5)
check("6b 量能足够触发", a6b and a6b["direction"] == "long_add", str(a6b))

# 7. 数据不足
a7 = oi_alert.detect_surge(mk_bars(5, [1] * 5, [1] * 5, [1] * 5))
check("7 数据不足", a7 is None, str(a7))

# 汇总
fails = [x for x in checks if not x[1]]
print(f"\noi_alert 自测: {len(checks) - len(fails)}/{len(checks)} 通过")
for name, ok, detail in checks:
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if not ok else ""))
sys.exit(1 if fails else 0)
