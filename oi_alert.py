"""
oi_alert.py — 持仓量异动（大量增仓）检测器（纯函数，可单测/离线回放）。

语义约定（与 PRD 一致，量价仓同向才算异动）：
  - 每根 K 线的 "oi" 字段 = 该根内持仓量变化（close_oi - open_oi），天勤原生字段。
  - 价涨 + oi>0   → 多头增仓（long_add）  新多进场，红
  - 价跌 + oi<0   → 空头增仓（short_add） 新空进场，绿
  - 价涨 + oi<0   → 空头回补（short_cover）假涨，不弹
  - 价跌 + oi>0   → 多头离场（long_exit） 假跌/多杀多，不弹

触发条件（全部满足才算异动）：
  1. |oi| >= baseline * mult            # 持仓变化相对自身近期基线显著放大
  2. 方向为 long_add / short_add（同向才算，背离不弹）
  3. 成交量也相对放大（vol_mult，防垃圾数据）
  4. |oi| 绝对值 >= min_abs（过滤太小变化，可选）

设计为可对任意周期 K 线列表调用（1h/15m/3m 通用），方便：
  - 离线：用 cache 1h 回放验证阈值有效性（见 oi_alert_validate.py）
  - 实盘：app.py 每轮取全局层 3m/1h bars 检测（节流）
"""
from __future__ import annotations


def detect_surge(bars, window: int = 20, mult: float = 5.0,
                 vol_mult: float = 1.0, min_abs: float | None = None) -> dict | None:
    """
    检测 bars 列表【最新一根】是否存在持仓异动。

    参数：
      bars      K线列表，每根含 close/volume/oi（oi=单根持仓变化）。按时间升序，最后一根为当前。
      window    基线窗口根数（默认 20），取 [-(window+1):-1] 的 |oi| 均值做基线。
      mult      触发倍数：|最新oi| >= 基线均值 * mult。
      vol_mult  成交量放大要求：最新volume >= 近window均值 * vol_mult。设为 0 关闭。
      min_abs   最小 |oi| 手数门槛（防小仓位噪音）。None = 用基线均值（相当于再要求超过基线本身）。

    返回 dict（未触发返回 None）：
      {surge, direction, oi_delta, baseline, ratio, vol_ratio, price,
       close, volume, dt}
      direction: long_add / short_add
    """
    n = len(bars)
    if n < window + 3:
        return None
    last = bars[-1]
    price = last.get("close")
    if not price:
        return None
    oi_delta = last.get("oi", 0) or 0
    vol = last.get("volume", 0) or 0

    # 基线：前 window 根 |oi| 均值（不含最新根），用均值；窗口内全 0 则退化
    hist = bars[-(window + 1):-1]
    oi_abs = [abs(b.get("oi", 0) or 0) for b in hist]
    base_oi = sum(oi_abs) / max(1, len(oi_abs))
    vols = [b.get("volume", 0) or 0 for b in hist]
    base_vol = sum(vols) / max(1, len(vols))

    # 绝对值门槛：默认要求超过基线本身（即 ratio>1 自然满足）；给了 min_abs 用它
    floor = min_abs if min_abs is not None else (base_oi * 0.5 if base_oi > 0 else 0)
    if abs(oi_delta) < floor:
        return None

    if base_oi <= 0:
        return None  # 基线为零（新上市/无数据），无法比较
    ratio = abs(oi_delta) / base_oi
    if ratio < mult:
        return None

    # 成交量放大（防单笔垃圾数据）；vol_mult=0 关闭该检查
    vol_ratio = (vol / base_vol) if base_vol > 0 else 1.0
    if vol_mult and vol_ratio < vol_mult:
        return None

    # 方向：量价仓同向才弹
    prev = bars[-2].get("close") or price
    price_up = price > prev
    if price_up and oi_delta > 0:
        direction = "long_add"
    elif (not price_up) and oi_delta < 0:
        direction = "short_add"
    else:
        return None  # 背离，只记录不弹

    return {
        "surge": True,
        "direction": direction,
        "oi_delta": int(oi_delta),
        "baseline": round(base_oi, 1),
        "ratio": round(ratio, 2),
        "vol_ratio": round(vol_ratio, 2),
        "price": round(price, 2),
        "close": round(price, 2),
        "volume": int(vol),
        "dt": last.get("dt", ""),
    }


# 方向 → 文案/颜色（与面板涨红跌绿一致）
DIR_TEXT = {
    "long_add": "多头增仓",
    "short_add": "空头增仓",
}
DIR_COLOR = {  # 与前端 CSS 变量一致
    "long_add": "#da3633",   # 红
    "short_add": "#2ea043",  # 绿
}
DIR_EMOJI = {
    "long_add": "🟥",
    "short_add": "🟩",
}


def format_alert(a: dict, name: str, sym: str, note: str = "") -> str:
    """把检测结果格式化成公告文案（面板顶栏用）。"""
    if not a:
        return ""
    d = a["direction"]
    return (f"{DIR_EMOJI[d]} {name}·{DIR_TEXT[d]} "
            f"{abs(a['oi_delta']):,}手（{a['ratio']:.1f}×基线）"
            f" 现价{a['price']}"
            + (f" · {note}" if note else ""))
