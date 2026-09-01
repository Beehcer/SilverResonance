"""
品种预设表：合约代码 → 中文名 / 最小变动价位 / 每跳每手盈亏。
- 配置页下拉选项来自 PRESETS；
- 切换品种后，config.PRICE_TICK / TICK_VALUE 按本表自动解析；
- 换月：通过 refresh_months() 按当前日期自动推算主力月份，启动时调用一次即可，
  不再需要在表里手动维护合约月份。

月份推算规则（按品种历史主力轮换习惯，覆盖国内主要期货品种）：
  - SHFE ag / au          : 6月 / 12月 切换
  - SHFE rb / ss          : 1月 / 5月 / 10月 切换
  - SHFE bu               : 6月 / 9月 / 12月 切换
  - SHFE sp               : 5月 / 9月 切换
  - DCE  m / jm           : 1月 / 5月 / 9月 切换
  - CZCE SA / FG / CF / MA: 1月 / 5月 / 9月 切换

规则意图：永远指向"距离今天最近、尚未到期"的那个轮换月。
不追求 100% 准确（主力偶尔漂移到非轮换月），但避免"面板放着放着就合约到期挂掉"。
需要精确主力时，仍可在配置页下拉中改任意一个具体合约覆盖。
"""

from datetime import date, timedelta


# (品种 key, 交易所, 代码, 中文名, tick, tick_value, 轮换月份(1-12))
_TEMPLATES = [
    ("ag", "SHFE", "ag", "沪银",   1.0,  15.0, (6, 12)),
    ("au", "SHFE", "au", "沪金",   0.02, 20.0, (6, 12)),
    ("rb", "SHFE", "rb", "螺纹钢", 1.0,  10.0, (1, 5, 10)),
    ("SA", "CZCE", "SA", "纯碱",   1.0,  20.0, (1, 5, 9)),
    ("FG", "CZCE", "FG", "玻璃",   1.0,  20.0, (1, 5, 9)),
    ("bu", "SHFE", "bu", "沥青",   1.0,  10.0, (6, 9, 12)),
    ("CF", "CZCE", "CF", "棉花",   5.0,  25.0, (1, 5, 9)),
    ("sp", "SHFE", "sp", "纸浆",   2.0,  20.0, (5, 9)),
    ("ss", "SHFE", "ss", "不锈钢", 5.0,  25.0, (1, 5, 10)),
    ("m",  "DCE",  "m",  "豆粕",   1.0,  10.0, (1, 5, 9)),
    ("jm", "DCE",  "jm", "焦煤",   0.5,  30.0, (1, 5, 9)),
    ("MA", "CZCE", "MA", "甲醇",   1.0,  10.0, (1, 5, 9)),
]


def _next_rollover(rot_months, today=None):
    """
    给定轮换月份元组，返回 (年, 月) —— 距离 today 最近、且流动性仍充足的那个轮换月。

    实务规则：交割月前 ADVANCE_DAYS 天左右，主力合约的流动性就开始向下一轮换月迁移
    （市场惯例，提前换月避免被"交割月套牢"）。
    例如轮换 (1,5,9)，今天 2026-08-27，ADVANCE_DAYS=20：
      - 9月合约 9-15 到期，今天距到期还有 19 天 < 20 → 换到下一个轮换月 (2027, 1)
    今天 2026-08-20，距 9-15 还有 26 天 >= 20 → 仍可用 (2026, 9)
    """
    today = today or date.today()
    ADVANCE_DAYS = 20   # 提前多少天开始换月
    y = today.year
    # 候选：今年/明年/后年所有轮换月（防御）
    cands = []
    for yy in (y, y + 1, y + 2):
        for mm in rot_months:
            cands.append((yy, mm))
    cands.sort()
    for yy, mm in cands:
        # 该轮换月合约的"近似最后交易日"：当月 15 号
        expiry = date(yy, mm, 15)
        # 合约可用窗口：到期日 - ADVANCE_DAYS 之前
        usable_until = expiry - timedelta(days=ADVANCE_DAYS)
        if today <= usable_until:
            return yy, mm
    return cands[-1]  # 理论到不了


def _format_contract(code, exch, year, month):
    """
    按交易所规则格式化合约代码：
      SHFE / DCE  : 4 位数字  yymm  （如 ag2612, m2601）
      CZCE        : 3 位数字  ymm   （如 SA701, MA701；2020 年起改 3 位）
    """
    yy = year % 100
    if exch == "CZCE":
        return f"{code}{yy % 10}{month:02d}"
    return f"{code}{yy:02d}{month:02d}"


def _build_presets(today=None):
    """按当前日期生成 PRESETS 列表（每项 symbol 是最新主力合约代码）。"""
    out = []
    for key, exch, code, name, tick, tick_value, rot in _TEMPLATES:
        y, m = _next_rollover(rot, today)
        contract = _format_contract(code, exch, y, m)
        # 展示名只保留数字月份部分（"沪银2612"），symbol 用全代码（"SHFE.ag2612"）
        month_digits = "".join(ch for ch in contract if ch.isdigit())
        symbol = f"{exch}.{contract}"
        out.append({
            "symbol": symbol,
            "name": f"{name}{month_digits}",
            "tick": tick,
            "tick_value": tick_value,
        })
    return out


# 启动时根据当天日期生成一次。所有引用方（app.py / backtest.py / fundamentals.py）
# 拿到的都是"当前主力"代码，无需手动换月。
PRESETS = _build_presets()


def refresh_months(today=None):
    """
    重新计算 PRESETS（一般不需要调用，进程启动时已自动算过；
    面板长时间运行跨月时，可在数据线程中定期调用）。
    返回新的 PRESETS 列表。
    """
    global PRESETS
    PRESETS = _build_presets(today)
    return PRESETS


def find(symbol):
    """按合约代码查预设，返回该条 dict；查不到返回 None。"""
    if not symbol:
        return None
    for p in PRESETS:
        if p["symbol"] == symbol:
            return p
    return None


def name_of(symbol):
    """返回品种中文名；非预设（自定义合约）返回 None。"""
    p = find(symbol)
    return p["name"] if p else None


def apply_symbol_params(cfg):
    """
    根据 cfg.SYMBOL 从预设表解析 PRICE_TICK / TICK_VALUE 并写回 cfg。
    查不到预设时保留 cfg 现有值（即 config.py 里的默认值，
    自定义合约可在 config.py 手动改默认值来覆盖）。
    """
    p = find(getattr(cfg, "SYMBOL", ""))
    if p:
        cfg.PRICE_TICK = p["tick"]
        cfg.TICK_VALUE = p["tick_value"]
    return p


def rollover_if_expired(cfg_symbol):
    """
    如果 cfg_symbol 在预设表中已找不到（说明已过期被新主力替换），
    且能找到同品种（去数字后代码一致）的新主力合约，则返回新代码；
    否则返回原代码不变。供启动时静默换月用。
    """
    if not cfg_symbol:
        return cfg_symbol
    if find(cfg_symbol):
        return cfg_symbol  # 表里就有，说明仍是当前主力
    # 拆品种代码（去数字）
    try:
        exch, code_with_num = cfg_symbol.split(".", 1)
    except ValueError:
        return cfg_symbol
    prod = "".join(ch for ch in code_with_num if not ch.isdigit())
    for p in PRESETS:
        p_exch, p_code = p["symbol"].split(".", 1)
        p_prod = "".join(ch for ch in p_code if not ch.isdigit())
        if p_exch == exch and p_prod == prod:
            return p["symbol"]
    return cfg_symbol
