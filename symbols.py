"""
品种预设表：合约代码 → 中文名 / 最小变动价位 / 每跳每手盈亏。
- 配置页下拉选项来自 PRESETS；
- 切换品种后，config.PRICE_TICK / TICK_VALUE 按本表自动解析；
- 换月时只需更新表内合约月份（如 ag2608 → ag2609），并同步修改代码。
"""

# 预设品种清单（下单前请与交易所合约表核对 tick / 合约乘数）
PRESETS = [
    {"symbol": "SHFE.ag2608", "name": "沪银2608",   "tick": 1.0,  "tick_value": 15.0},
    {"symbol": "SHFE.au2608", "name": "沪金2608",   "tick": 0.02, "tick_value": 20.0},
    {"symbol": "SHFE.rb2610", "name": "螺纹钢2610", "tick": 1.0,  "tick_value": 10.0},
    {"symbol": "CZCE.SA609",  "name": "纯碱609",    "tick": 1.0,  "tick_value": 20.0},
    {"symbol": "CZCE.FG609",  "name": "玻璃609",    "tick": 1.0,  "tick_value": 20.0},
    {"symbol": "SHFE.bu2609", "name": "沥青2609",   "tick": 1.0,  "tick_value": 10.0},
    {"symbol": "CZCE.CF609",  "name": "棉花609",    "tick": 5.0,  "tick_value": 25.0},
    {"symbol": "SHFE.sp2609", "name": "纸浆2609",   "tick": 2.0,  "tick_value": 20.0},
    {"symbol": "SHFE.ss2608", "name": "不锈钢2608", "tick": 5.0,  "tick_value": 25.0},
    {"symbol": "DCE.m2609",   "name": "豆粕2609",   "tick": 1.0,  "tick_value": 10.0},
    {"symbol": "DCE.jm2609",  "name": "焦煤2609",   "tick": 0.5,  "tick_value": 30.0},
]


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
