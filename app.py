"""
白银三频共振面板 - 后端
- MOCK_MODE=True：生成随机行情，纯本地预览面板，无需网络/账号
- MOCK_MODE=False：用 TQSDK 拉真实行情(在独立线程运行其事件循环)

启动： python app.py  然后浏览器打开 http://127.0.0.1:8848
"""

import threading
import time
import random
import math
import os
import sys
import webbrowser
from flask import Flask, jsonify, render_template, request, redirect, url_for

import config
import resonance
import user_settings


def _resource_dir():
    """PyInstaller 打包后模板在 sys._MEIPASS，源码运行时用脚本目录。"""
    if getattr(sys, "_MEIPASS", None):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


app = Flask(__name__, template_folder=os.path.join(_resource_dir(), "templates"))

# 返回给前端画图的最大K线根数
CHART_BARS = 60
# 画图用的均线周期
CHART_MAS = (5, 20, 60)


def _attach_mas(full_bars):
    """
    用完整序列(full_bars)算 MA5/20/60，把均线值附到每根K线上，
    再返回最后 CHART_BARS 根。这样可见区间内三条均线都是完整的，
    不会因为切片后数据不足导致 MA60 画不出、MA20 从中间才开始。
    """
    closes = [b["close"] for b in full_bars]
    for i, b in enumerate(full_bars):
        for p in CHART_MAS:
            if i + 1 >= p:
                b[f"ma{p}"] = round(sum(closes[i - p + 1:i + 1]) / p, 1)
            else:
                b[f"ma{p}"] = None
    return full_bars[-CHART_BARS:]


def _find_sr(full_bars, direction=None):
    """
    自动找支撑/压力位（基于摆动高低点 pivot/分形）。
    - 摆动高点(左右各 window 根都比它低) → 潜在压力
    - 摆动低点(左右各 window 根都比它高) → 潜在支撑
    临近的点聚合成一条线，并统计被触碰次数(触碰越多越有效)。
    返回 {"support": [{price, touches}], "resistance": [...]}。
    """
    window = 3
    n = len(full_bars)
    highs = [b["high"] for b in full_bars]
    lows = [b["low"] for b in full_bars]
    piv_hi, piv_lo = [], []
    for i in range(window, n - window):
        seg_h = highs[i - window:i + window + 1]
        seg_l = lows[i - window:i + window + 1]
        if highs[i] == max(seg_h):
            piv_hi.append(highs[i])
        if lows[i] == min(seg_l):
            piv_lo.append(lows[i])

    # 价格容差：用近段振幅的百分比聚合相近的点
    if not full_bars:
        return {"support": [], "resistance": []}
    ref = full_bars[-1]["close"] or 1
    tol = ref * 0.0015  # 0.15%

    def cluster(points):
        points = sorted(points)
        groups = []
        for p in points:
            if groups and abs(p - groups[-1]["sum"] / groups[-1]["n"]) <= tol:
                groups[-1]["sum"] += p
                groups[-1]["n"] += 1
            else:
                groups.append({"sum": p, "n": 1})
        out = [{"price": round(g["sum"] / g["n"], 1), "touches": g["n"]} for g in groups]
        # 触碰次数多的优先，最多取 3 条
        out.sort(key=lambda x: x["touches"], reverse=True)
        return out[:3]

    price = full_bars[-1]["close"]
    res = [r for r in cluster(piv_hi) if r["price"] >= price]   # 压力在现价上方
    sup = [s for s in cluster(piv_lo) if s["price"] <= price]   # 支撑在现价下方
    return {"support": sup, "resistance": res}


def _nearest_levels(price, sr):
    """返回现价上方最近压力、下方最近支撑（含跳数距离）。"""
    tick = config.PRICE_TICK or 1.0
    res_above = sorted([r for r in sr.get("resistance", []) if r["price"] >= price],
                       key=lambda x: x["price"])
    sup_below = sorted([s for s in sr.get("support", []) if s["price"] <= price],
                       key=lambda x: -x["price"])
    near_res = res_above[0] if res_above else None
    near_sup = sup_below[0] if sup_below else None
    res_ticks = round((near_res["price"] - price) / tick) if near_res else None
    sup_ticks = round((price - near_sup["price"]) / tick) if near_sup else None
    return near_res, res_ticks, near_sup, sup_ticks


def _proximity_warning(price, sr):
    """
    压力/支撑贴近警告。现价距最近压力(或支撑) ≤ PROXIMITY_TICKS 跳 → 警告。
    返回 None 或 {side, level, ticks, text}。
    """
    if price is None or not sr:
        return None
    near_res, res_ticks, near_sup, sup_ticks = _nearest_levels(price, sr)
    lim = config.PROXIMITY_TICKS
    # 优先报更近的一侧
    cand = []
    if near_res and res_ticks is not None and res_ticks <= lim:
        cand.append(("resistance", near_res["price"], res_ticks))
    if near_sup and sup_ticks is not None and sup_ticks <= lim:
        cand.append(("support", near_sup["price"], sup_ticks))
    if not cand:
        return None
    cand.sort(key=lambda x: x[2])
    side, level, ticks = cand[0]
    if side == "resistance":
        text = f"🚧 逼近压力 {level}（仅差 {ticks} 跳）— 做多风险区，勿追突破！"
    else:
        text = f"🚧 逼近支撑 {level}（仅差 {ticks} 跳）— 做空风险区，勿追破位！"
    return {"side": side, "level": level, "ticks": ticks, "text": text}


def _entry_check():
    """
    "我要开仓" 前的盘面自检。返回结论 + 逐条检查。
    只做客观技术条件核对，不替用户下单。
    """
    st = get_state()
    res = st.get("result")
    if not res:
        return {"verdict": "no-data", "title": "无数据", "checks": [],
                "summary": "未取到行情，无法检查。"}

    direction = res.get("direction", "none")
    resonance = res.get("resonance", False)
    price = res.get("price")
    legs = res.get("legs", {})
    sr = (res.get("sr") or {}).get("trigger") or (res.get("sr") or {}).get("trend") or {}

    checks = []

    # 1. 方向是否明确
    dir_txt = {"long": "偏多", "short": "偏空", "none": "方向不明"}[direction]
    checks.append({"ok": direction != "none", "name": "大周期方向",
                   "detail": f"15分定方向：{dir_txt}"})

    # 2. 三频共振
    checks.append({"ok": bool(resonance), "name": "三频共振",
                   "detail": "三频对齐" if resonance else "未共振（15/3/30 未全部同向就位）"})

    # 3. 三腿明细
    for key, label in (("trend", "15分"), ("filter", "3分"), ("trigger", "30秒")):
        leg = legs.get(key, {})
        checks.append({"ok": bool(leg.get("ok")), "name": f"{label}·{leg.get('role','')}",
                       "detail": leg.get("detail", "-")})

    # 4. 压力/支撑贴近（追单风险）
    warn = _proximity_warning(price, sr)
    checks.append({"ok": warn is None, "name": "位置安全（远离压力/支撑）",
                   "detail": warn["text"] if warn else "现价未贴近关键位，位置较安全"})

    # 5. 掩体 / 盈亏比预估
    near_res, res_ticks, near_sup, sup_ticks = _nearest_levels(price, sr) if (price and sr) else (None, None, None, None)
    stop_ticks = config.DEFAULT_STOP_TICKS
    rr_txt, rr_ok = "无法估算（缺支撑压力位）", False
    if direction == "long" and near_res and res_ticks is not None:
        rr = res_ticks / stop_ticks if stop_ticks else 0
        rr_ok = rr >= config.MIN_RR
        rr_txt = f"上方掩体空间 {res_ticks} 跳 / 止损 {stop_ticks} 跳 ≈ 盈亏比 {rr:.1f}"
    elif direction == "short" and near_sup and sup_ticks is not None:
        rr = sup_ticks / stop_ticks if stop_ticks else 0
        rr_ok = rr >= config.MIN_RR
        rr_txt = f"下方掩体空间 {sup_ticks} 跳 / 止损 {stop_ticks} 跳 ≈ 盈亏比 {rr:.1f}"
    checks.append({"ok": rr_ok, "name": f"掩体/盈亏比 (≥{config.MIN_RR})", "detail": rr_txt})

    # 结论：硬条件 = 方向明确 + 共振 + 位置安全 + 盈亏比达标
    hard_ok = (direction != "none") and resonance and (warn is None) and rr_ok
    fail = [c["name"] for c in checks if not c["ok"]]
    if hard_ok:
        verdict, title = "go", "✅ 技术条件允许观察进场"
        summary = f"方向{dir_txt}、三频共振、位置安全、盈亏比达标。仍需你手动确认结构止损位。"
    elif warn is not None:
        verdict, title = "stop", "🚧 不建议：正贴近关键位（追单陷阱）"
        summary = warn["text"] + " 等回踩确认，别追当根。"
    else:
        verdict, title = "wait", "⛔ 条件不足，建议观望"
        summary = "未通过：" + "、".join(fail) + "。任一硬条件不满足都不该进。"

    return {"verdict": verdict, "title": title, "direction": direction,
            "price": price, "checks": checks, "summary": summary}

# 共享状态：数据线程写，Flask 读。用锁保护。
_state_lock = threading.Lock()
_state = {
    "connected": False,
    "mock": config.MOCK_MODE,
    "symbol": config.SYMBOL,
    "result": None,       # resonance.evaluate 的输出
    "updated": 0,         # 最后更新时间戳
    "error": None,
    # 启动阶段：need_config / booting / connecting / loading / ready / error
    "boot_phase": "booting",
    "boot_pct": 0,        # 进度 0-100
    "boot_msg": "正在启动…",
}


def _set_boot(phase, pct, msg):
    _set_state(boot_phase=phase, boot_pct=pct, boot_msg=msg)


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)
        _state["updated"] = time.time()


def get_state():
    with _state_lock:
        return dict(_state)


# ---------------- MOCK 数据 ----------------
def _mock_series(base, n, drift, vol):
    """生成一段随机游走 OHLC 序列（含成交量/持仓量变化）。"""
    out = []
    price = base
    oi = 200000
    for _ in range(n):
        o = price
        price += drift + random.uniform(-vol, vol)
        c = price
        h = max(o, c) + random.uniform(0, vol)
        low = min(o, c) - random.uniform(0, vol)
        volume = int(abs(c - o) * 300 + random.uniform(200, 1500))
        oi_delta = int((c - o) * 200 + random.uniform(-400, 400))
        oi += oi_delta
        out.append({
            "open": round(o, 1), "high": round(h, 1),
            "low": round(low, 1), "close": round(c, 1),
            "volume": volume, "oi": oi_delta,
        })
    return out


def _mock_loop():
    """随机模拟三个周期，方向会缓慢漂移，用来观察面板各种状态。"""
    _set_boot("loading", 60, "生成模拟数据…")
    _set_state(connected=True, error=None)
    phase = 0.0
    first = True
    while True:
        # 用正弦让方向周期性在多/空/震荡之间切换，便于看灯变化
        phase += 0.15
        drift = math.sin(phase) * 0.6
        base = 7200
        kl_trend = _mock_series(base, config.KLINE_LENGTH, drift * 0.4, 3)
        kl_filter = _mock_series(kl_trend[-1]["close"], config.KLINE_LENGTH, drift * 0.5, 2)
        kl_trigger = _mock_series(kl_filter[-1]["close"], config.KLINE_LENGTH, drift * 0.6, 1)
        result = resonance.evaluate(kl_trend, kl_filter, kl_trigger)
        result["klines"] = {
            "trend": _attach_mas(kl_trend),
            "filter": _attach_mas(kl_filter),
            "trigger": _attach_mas(kl_trigger),
        }
        # 支撑/压力：用当前显示周期算(默认15分)，跟随各周期
        result["sr"] = {
            "trend": _find_sr(kl_trend),
            "filter": _find_sr(kl_filter),
            "trigger": _find_sr(kl_trigger),
        }
        # 压力/支撑贴近警告（用最短周期30秒的S/R + 现价）
        result["proximity"] = _proximity_warning(result.get("price"), result["sr"]["trigger"])
        _set_state(result=result)
        if first:
            _set_boot("ready", 100, "就绪")
            first = False
        time.sleep(1)


# ---------------- TQSDK 真实数据 ----------------
def _tqsdk_loop():
    try:
        from tqsdk import TqApi, TqAuth
    except ImportError:
        _set_boot("error", 0, "未安装 tqsdk")
        _set_state(connected=False, error="未安装 tqsdk，请先 pip install -r requirements.txt")
        return

    try:
        _set_boot("connecting", 25, "正在连接天勤行情…")
        auth = None
        if config.TQ_USER:
            auth = TqAuth(config.TQ_USER, config.TQ_PASSWORD)
        api = TqApi(auth=auth) if auth else TqApi()
    except Exception as e:  # noqa: BLE001
        _set_boot("error", 0, f"连接天勤失败：{e}")
        _set_state(connected=False, error=f"连接天勤失败: {e}")
        return

    try:
        # 先校验合约是否存在/有效（换月过期、拼写错误会在这里立刻暴露，而不是死等K线）
        _set_boot("loading", 45, "正在校验合约…")
        try:
            q = api.get_quote(config.SYMBOL)
            api.wait_update(deadline=time.time() + 15)
            # 不存在的合约：datetime 为空、价格为 NaN
            dt = getattr(q, "datetime", "") or ""
            last = getattr(q, "last_price", float("nan"))
            has_price = isinstance(last, (int, float)) and not math.isnan(last)
            if getattr(q, "expired", False):
                raise ValueError("合约已到期/换月")
            if not dt and not has_price:
                raise ValueError("查无此合约（无行情数据）")
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"合约 {config.SYMBOL} 无法识别或已过期。"
                f"请在配置页改成当前主力合约（如 SHFE.ag2510），或核对代码格式。原始信息：{e}"
            )

        _set_boot("loading", 60, "正在拉取三周期K线…")
        kt = api.get_kline_serial(config.SYMBOL, config.PERIOD_TREND, config.KLINE_LENGTH)
        kf = api.get_kline_serial(config.SYMBOL, config.PERIOD_FILTER, config.KLINE_LENGTH)
        kg = api.get_kline_serial(config.SYMBOL, config.PERIOD_TRIGGER, config.KLINE_LENGTH)
        _set_state(connected=True, error=None)

        def to_list(df):
            # 过滤未生成的NaN行，返回 OHLC + 成交量 + 持仓量变化(CCL)
            rows = []
            opens = df["open"].tolist()
            highs = df["high"].tolist()
            lows = df["low"].tolist()
            closes = df["close"].tolist()
            volumes = df["volume"].tolist() if "volume" in df else [0] * len(closes)
            open_ois = df["open_oi"].tolist() if "open_oi" in df else [0] * len(closes)
            close_ois = df["close_oi"].tolist() if "close_oi" in df else [0] * len(closes)
            for o, h, low, c, v, ooi, coi in zip(opens, highs, lows, closes, volumes, open_ois, close_ois):
                if math.isnan(c):
                    continue
                vol = 0 if (isinstance(v, float) and math.isnan(v)) else int(v)
                oi_delta = 0
                if not (isinstance(coi, float) and math.isnan(coi)) and not (isinstance(ooi, float) and math.isnan(ooi)):
                    oi_delta = int(coi - ooi)
                rows.append({"open": float(o), "high": float(h),
                             "low": float(low), "close": float(c),
                             "volume": vol, "oi": oi_delta})
            return rows

        first = True
        wait_start = time.time()
        while True:
            api.wait_update()
            kt_list, kf_list, kg_list = to_list(kt), to_list(kf), to_list(kg)
            # 首次要等三周期都有足够数据
            if first and (len(kt_list) < config.MA_SLOW or len(kg_list) < config.MA_FAST):
                # 看门狗：30秒还填不满K线 → 报错，避免永久卡在"拉取三周期K线"
                if time.time() - wait_start > 30:
                    raise TimeoutError(
                        f"30秒内未取到足够K线（15分{len(kt_list)}根/30秒{len(kg_list)}根）。"
                        f"可能原因：①合约 {config.SYMBOL} 非当前主力/已换月 "
                        f"②同一天勤账号在别处登录（同账号不能多设备同时在线）"
                        f"③当前非交易时段且无历史缓存。"
                        f"请在配置页核对合约、确认账号未在别处使用后重试。"
                    )
                continue
            result = resonance.evaluate(kt_list, kf_list, kg_list)
            result["sr"] = {
                "trend": _find_sr(kt_list),
                "filter": _find_sr(kf_list),
                "trigger": _find_sr(kg_list),
            }
            result["proximity"] = _proximity_warning(result.get("price"), result["sr"]["trigger"])
            result["klines"] = {
                "trend": _attach_mas(kt_list),
                "filter": _attach_mas(kf_list),
                "trigger": _attach_mas(kg_list),
            }
            _set_state(result=result)
            if first:
                _set_boot("ready", 100, "就绪")
                first = False
    except Exception as e:  # noqa: BLE001
        _set_boot("error", 0, f"行情线程异常：{e}")
        _set_state(connected=False, error=f"行情线程异常: {e}")
    finally:
        try:
            api.close()
        except Exception:  # noqa: BLE001
            pass


_data_thread = None
_thread_lock = threading.Lock()


def apply_user_settings():
    """把 user_config.json 覆盖到 config 运行时值。"""
    s = user_settings.load_settings()
    if s.get("tq_user"):
        config.TQ_USER = s["tq_user"]
    if s.get("tq_password"):
        config.TQ_PASSWORD = s["tq_password"]
    if s.get("symbol"):
        config.SYMBOL = s["symbol"]
    config.MOCK_MODE = bool(s.get("mock_mode", False))


def start_data_thread():
    """启动行情线程（只启动一次）。"""
    global _data_thread
    with _thread_lock:
        if _data_thread and _data_thread.is_alive():
            return
        target = _mock_loop if config.MOCK_MODE else _tqsdk_loop
        _data_thread = threading.Thread(target=target, daemon=True)
        _data_thread.start()


# ---------------- 路由 ----------------
@app.route("/")
def index():
    # 未配置 → 去配置页
    if not user_settings.is_configured():
        return redirect(url_for("setup"))
    return render_template("index.html",
                           symbol=config.SYMBOL,
                           poll_ms=config.POLL_INTERVAL_MS)


@app.route("/setup")
def setup():
    s = user_settings.load_settings()
    return render_template("setup.html",
                           tq_user=s.get("tq_user", ""),
                           symbol=s.get("symbol", config.SYMBOL))


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        s = user_settings.load_settings()
        s.pop("tq_password", None)  # 不回传密码
        return jsonify(s)
    # POST：保存配置 → 应用 → 启动行情线程
    data = request.get_json(force=True, silent=True) or {}
    user_settings.save_settings({
        "tq_user": (data.get("tq_user") or "").strip(),
        "tq_password": (data.get("tq_password") or "").strip(),
        "symbol": (data.get("symbol") or "").strip(),
        "mock_mode": bool(data.get("mock_mode", False)),
    })
    apply_user_settings()
    _set_boot("booting", 10, "配置已保存，正在启动…")
    start_data_thread()
    return jsonify({"ok": True})


@app.route("/api/state")
def api_state():
    return jsonify(get_state())


@app.route("/api/entry_check")
def api_entry_check():
    return jsonify(_entry_check())


def _open_browser_later():
    time.sleep(1.0)
    try:
        webbrowser.open(f"http://{config.HOST}:{config.PORT}")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    apply_user_settings()
    # 已配置 → 直接起行情线程；未配置 → 等用户在配置页提交后再起
    if user_settings.is_configured():
        start_data_thread()
    else:
        _set_boot("need_config", 0, "首次使用，请先填写天勤账号")
    threading.Thread(target=_open_browser_later, daemon=True).start()
    print(f"面板已启动: http://{config.HOST}:{config.PORT}  (MOCK_MODE={config.MOCK_MODE})")
    app.run(host=config.HOST, port=config.PORT, debug=False, use_reloader=False)
