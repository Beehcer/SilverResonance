"""
三频共振判定 - 纯函数，不依赖行情库，方便单独测试。

设计原则(对应用户交易纪律)：
- 15分(主战场) 定方向：均线多头/空头排列 + 斜率
- 3分 过滤位置：方向须与15分同向，且价格未过度延伸(避免在压力/支撑极端位追单)
- 30秒 触发：短均线相对慢均线的方向 + 现价站上/跌破短均线

三者同向且各司其职 → 共振(多/空)。任一环节不满足 → 无共振(观望)。

重要：本模块只判断"技术方向是否对齐"，不计算盈亏比、不读CCL持仓。
亮绿灯 = 值得抬头细看，不等于可以进场。R:R 和持仓仍须人工确认。
"""

from typing import List, Dict, Optional
import config


def sma(values: List[float], period: int) -> Optional[float]:
    """简单移动平均；数据不足返回 None。"""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _closes(klines: List[Dict]) -> List[float]:
    return [k["close"] for k in klines]


def trend_direction(klines: List[Dict]) -> str:
    """
    主战场(15分)定方向。
    返回 "long" / "short" / "none"。
    条件：MA_FAST 与 MA_SLOW 排列一致 + 慢均线有足够斜率。
    """
    closes = _closes(klines)
    ma_fast = sma(closes, config.MA_FAST)
    ma_slow = sma(closes, config.MA_SLOW)
    if ma_fast is None or ma_slow is None:
        return "none"

    lb = config.TREND_SLOPE_LOOKBACK
    if len(closes) < config.MA_SLOW + lb:
        return "none"
    ma_slow_prev = sma(closes[:-lb], config.MA_SLOW)
    if ma_slow_prev is None or ma_slow_prev == 0:
        return "none"

    slope_permille = (ma_slow - ma_slow_prev) / ma_slow_prev * 1000
    threshold = config.TREND_SLOPE_MIN

    if ma_fast > ma_slow and slope_permille >= threshold:
        return "long"
    if ma_fast < ma_slow and slope_permille <= -threshold:
        return "short"
    return "none"


def filter_ok(klines: List[Dict], direction: str) -> bool:
    """
    3分过滤：方向同向 + 未过度延伸(现价没离慢均线太远)。
    过度延伸时说明可能已在压力/支撑极端位，追单盈亏比差 → 不通过。
    """
    if direction == "none":
        return False
    closes = _closes(klines)
    ma_fast = sma(closes, config.MA_FAST)
    ma_slow = sma(closes, config.MA_SLOW)
    if ma_fast is None or ma_slow is None:
        return False

    # 方向须同向
    if direction == "long" and not (ma_fast >= ma_slow):
        return False
    if direction == "short" and not (ma_fast <= ma_slow):
        return False

    # 过度延伸保护
    price = closes[-1]
    if ma_slow == 0:
        return False
    deviation_pct = abs(price - ma_slow) / ma_slow * 100
    if deviation_pct > config.OVEREXTENSION_PCT:
        return False
    return True


def trigger_ok(klines: List[Dict], direction: str) -> bool:
    """
    30秒触发：企稳确认，防止一根影线扫过 MA 就误判。
    要求（做多为例，做空对称）：
      1. 最近连续 N 根"收盘价"都站在 MA_FAST 上方，且越过至少 buffer 跳（用收盘价，影线不算）；
      2. MA_FAST 自身在向上（拐头/延续），与方向一致。
    """
    if direction == "none":
        return False
    closes = _closes(klines)
    n_confirm = max(1, config.TRIGGER_CONFIRM_BARS)
    buffer = config.TRIGGER_MA_BUFFER_TICKS * config.PRICE_TICK

    # 数据要够：算 MA_FAST + 回看 n_confirm 根 + MA 斜率比较
    if len(closes) < config.MA_FAST + n_confirm:
        return False

    ma_fast = sma(closes, config.MA_FAST)
    ma_fast_prev = sma(closes[:-1], config.MA_FAST)
    if ma_fast is None or ma_fast_prev is None:
        return False

    # 1. 连续 N 根收盘价站稳同侧(含缓冲)。逐根用"截至该根"的 MA_FAST 比较。
    for k in range(n_confirm):
        # 截至倒数第 (k+1) 根的序列
        end = len(closes) - k
        sub = closes[:end]
        ma_k = sma(sub, config.MA_FAST)
        if ma_k is None:
            return False
        c_k = sub[-1]
        if direction == "long" and not (c_k >= ma_k + buffer):
            return False
        if direction == "short" and not (c_k <= ma_k - buffer):
            return False

    # 2. MA_FAST 方向一致
    if direction == "long":
        return ma_fast >= ma_fast_prev
    if direction == "short":
        return ma_fast <= ma_fast_prev
    return False


def evaluate(kl_trend: List[Dict], kl_filter: List[Dict], kl_trigger: List[Dict],
             labels: Optional[Dict] = None) -> Dict:
    """
    综合判定，返回面板需要的完整状态字典。
    labels：三个周期的显示文案，如 {"trend": "1小时", "filter": "15分", "trigger": "3分"}；
            不传则用默认 15分/3分/30秒（判定逻辑与周期文案无关）。
    """
    labels = labels or {"trend": "15分", "filter": "3分", "trigger": "30秒"}
    direction = trend_direction(kl_trend)
    f_ok = filter_ok(kl_filter, direction) if direction != "none" else False
    t_ok = trigger_ok(kl_trigger, direction) if direction != "none" else False

    resonance = direction != "none" and f_ok and t_ok

    def price_of(kl):
        return round(kl[-1]["close"], 2) if kl else None

    return {
        "direction": direction,              # long / short / none
        "resonance": resonance,              # 三频是否共振
        "legs": {
            "trend": {                       # 主战场
                "period": labels["trend"],
                "role": "定方向",
                "ok": direction != "none",
                "detail": {"long": "多头排列↑", "short": "空头排列↓", "none": "方向不明"}[direction],
            },
            "filter": {                      # 中周期
                "period": labels["filter"],
                "role": "过滤位置",
                "ok": f_ok,
                "detail": "同向且未过度延伸" if f_ok else ("方向不符/已过度延伸" if direction != "none" else "等待方向"),
            },
            "trigger": {                     # 小周期
                "period": labels["trigger"],
                "role": "触发入场",
                "ok": t_ok,
                "detail": (f"连续{config.TRIGGER_CONFIRM_BARS}根收盘站稳" if t_ok
                           else ("未企稳(等收盘确认)" if direction != "none" else "等待方向")),
            },
        },
        "price": price_of(kl_trigger) or price_of(kl_filter) or price_of(kl_trend),
    }
