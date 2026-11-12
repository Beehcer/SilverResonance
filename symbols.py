"""
品种预设表：合约代码 → 中文名 / 最小变动价位 / 每跳每手盈亏。
优先使用天勤主连 underlying_symbol 指向的真实主力，并缓存到 state/contracts.json。
导入仅读取七天内的有效缓存；无缓存时按日历推算，绝不自行联网。
行情线程取得 TqApi 后可调用 refresh_from_api(api)，全部品种验证成功才替换预设。

月份推算规则（按品种历史主力轮换习惯，覆盖国内主要期货品种）：
  - SHFE ag / au          : 6月 / 12月 切换
  - SHFE rb / ss          : 1月 / 5月 / 10月 切换
  - SHFE bu               : 6月 / 9月 / 12月 切换
  - SHFE sp               : 5月 / 9月 切换
  - DCE  m / jm / jd / l / p: 1月 / 5月 / 9月 切换
  - CZCE SA / FG / CF / MA / RM / TA: 1月 / 5月 / 9月 切换
  - CZCE AP              : 1月 / 5月 / 10月 切换

日历规则只作为离线兜底，不代表交易所当前真实主力。
"""

from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile

import user_settings


_BEIJING = timezone(timedelta(hours=8))
_CACHE_VERSION = 1
_CACHE_MAX_AGE = timedelta(days=7)


def _now(today=None):
    """默认北京时间；传入日期时统一以当天零点作为测试/回放时钟。"""
    if today is None:
        return datetime.now(_BEIJING)
    if isinstance(today, datetime):
        return today.replace(tzinfo=_BEIJING) if today.tzinfo is None else today.astimezone(_BEIJING)
    return datetime.combine(today, time.min, tzinfo=_BEIJING)


def contracts_path():
    """与程序同目录，兼容 PyInstaller onefile；不访问用户账号配置。"""
    return Path(user_settings.app_dir()) / "state" / "contracts.json"


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
    ("RM", "CZCE", "RM", "菜粕",   1.0,  10.0, (1, 5, 9)),
    ("jd", "DCE",  "jd", "鸡蛋",   1.0,  10.0, (1, 5, 9)),
    ("AP", "CZCE", "AP", "苹果",   1.0,  10.0, (1, 5, 10)),
    ("TA", "CZCE", "TA", "PTA",    2.0,  10.0, (1, 5, 9)),
    ("l",  "DCE",  "l",  "塑料",   1.0,   5.0, (1, 5, 9)),
    # 棕榈油自2026-04-10交易日起最小变动价位为1元/吨，每手10吨。
    ("p",  "DCE",  "p",  "棕榈",   1.0,  10.0, (1, 5, 9)),
]

# 扩充品种前的缓存固定为以上前 12 或 16 项；仅兼容这些已发布版本，
# 不把任意残缺列表当成有效缓存。新增项仍等待行情线程核实真实主力。
_LEGACY_CACHE_SIZES = (12, 16)


def _next_rollover(rot_months, today=None):
    """
    给定轮换月份元组，返回 (年, 月) —— 距离 today 最近、且流动性仍充足的那个轮换月。

    实务规则：交割月前 ADVANCE_DAYS 天左右，主力合约的流动性就开始向下一轮换月迁移
    （市场惯例，提前换月避免被"交割月套牢"）。
    例如轮换 (1,5,9)，今天 2026-08-27，ADVANCE_DAYS=20：
      - 9月合约 9-15 到期，今天距到期还有 19 天 < 20 → 换到下一个轮换月 (2027, 1)
    今天 2026-08-20，距 9-15 还有 26 天 >= 20 → 仍可用 (2026, 9)
    """
    today = _now(today).date()
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
    """按日期推算备用合约，不把日历估计当作真实主力。"""
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


def _positive_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须为有限正数")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} 必须为有限正数")
    return value


def _contract_digits(symbol, template, today):
    _, exch, code, *_ = template
    width = 3 if exch == "CZCE" else 4
    match = re.fullmatch(re.escape(f"{exch}.{code}") + rf"([0-9]{{{width}}})", symbol or "")
    if not match:
        raise ValueError(f"主力合约与预设品种不匹配：{symbol!r}")
    digits = match.group(1)
    month = int(digits[-2:])
    # 郑商所年份仅一位：在当前年附近解析，拒绝过去年份或异常远期缓存。
    candidates = [y for y in range(today.year - 1, today.year + 3)
                  if y % (10 if width == 3 else 100) == int(digits[:-2])]
    if not candidates or not 1 <= month <= 12:
        raise ValueError(f"合约月份不合理：{symbol}")
    year = candidates[0]
    months_ahead = (year - today.year) * 12 + month - today.month
    if not 0 <= months_ahead <= 24:
        raise ValueError(f"合约月份已过去或过远：{symbol}")
    return digits


def _validated_preset(item, template, now):
    if not isinstance(item, dict):
        raise ValueError("合约缓存条目必须是对象")
    symbol = item.get("symbol")
    if not isinstance(symbol, str):
        raise ValueError("合约代码必须是字符串")
    digits = _contract_digits(symbol, template, now.date())
    tick = _positive_number(item.get("tick"), "tick")
    tick_value = _positive_number(item.get("tick_value"), "tick_value")
    expiry = _positive_number(item.get("expire_datetime"), "expire_datetime")
    if not now.timestamp() < expiry <= (now + timedelta(days=3 * 366)).timestamp():
        raise ValueError(f"合约到期时间无效或已过期：{symbol}")
    return {"symbol": symbol, "name": f"{template[3]}{digits}",
            "tick": tick, "tick_value": tick_value, "expire_datetime": expiry}


def _load_cached(now):
    try:
        data = json.loads(contracts_path().read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
            return None
        if data["schema_version"] != _CACHE_VERSION or data.get("source") != "tqsdk":
            return None
        fetched = datetime.fromisoformat(data["fetched_at"])
        if fetched.utcoffset() != timedelta(hours=8) or not timedelta(0) <= now - fetched <= _CACHE_MAX_AGE:
            return None
        rows = data["presets"]
        if not isinstance(rows, list) or len(rows) not in (*_LEGACY_CACHE_SIZES, len(_TEMPLATES)):
            return None
        validated = [_validated_preset(row, template, now) for row, template in zip(rows, _TEMPLATES)]
        # 不重写缓存、延长缓存有效期或把日历占位保存成已核验主力。
        return validated + _build_presets(now.date())[len(rows):]
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return None


def _save_cached(presets, now):
    path = contracts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="contracts-", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump({"schema_version": _CACHE_VERSION, "source": "tqsdk",
                       "fetched_at": now.isoformat(), "presets": presets}, stream,
                      ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


PRESETS = []


def refresh_months(today=None):
    """离线刷新：七天内的有效主力缓存优先，否则按给定日期推算。"""
    now = _now(today)
    PRESETS[:] = _load_cached(now) or _build_presets(now.date())
    return PRESETS


def refresh_from_api(api, today=None):
    """复用调用者的 TqApi，批量验证全部主力后原子保存；失败不更改预设。

    本函数不创建连接，不读取账号。API/校验/写盘失败均抛异常，调用者可保留
    refresh_months 得到的缓存或日历兜底。today 与 refresh_months 使用相同时钟。
    """
    now = _now(today)
    continuous = [f"KQ.m@{t[1]}.{t[2]}" for t in _TEMPLATES]
    main_quotes = list(api.get_quote_list(continuous))
    if len(main_quotes) != len(_TEMPLATES):
        raise ValueError("主连行情返回数量不完整")
    contracts = []
    for quote, template in zip(main_quotes, _TEMPLATES):
        symbol = getattr(quote, "underlying_symbol", None)
        if not isinstance(symbol, str):
            raise ValueError("主连未返回 underlying_symbol")
        _contract_digits(symbol, template, now.date())
        contracts.append(symbol)
    quotes = list(api.get_quote_list(contracts))
    if len(quotes) != len(_TEMPLATES):
        raise ValueError("实际合约行情返回数量不完整")
    proposed = []
    for symbol, quote, template in zip(contracts, quotes, _TEMPLATES):
        if getattr(quote, "instrument_id", None) != symbol or getattr(quote, "expired", None) is not False:
            raise ValueError(f"实际合约不匹配或已经下市：{symbol}")
        tick = _positive_number(getattr(quote, "price_tick", None), "price_tick")
        multiplier = _positive_number(getattr(quote, "volume_multiple", None), "volume_multiple")
        proposed.append(_validated_preset({"symbol": symbol, "tick": tick,
                         "tick_value": tick * multiplier,
                         "expire_datetime": getattr(quote, "expire_datetime", None)}, template, now))
    _save_cached(proposed, now)
    PRESETS[:] = proposed
    return PRESETS


refresh_months()


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
