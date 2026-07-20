"""
白银三频共振面板 - 配置
所有可调参数集中在这里，改完重启服务即可生效。
"""

# ==== 合约 ====
# 白银主力 2608。换月时改这里，例如 "SHFE.ag2609"
SYMBOL = "SHFE.ag2608"

# ==== 三个周期(秒) ====
# 15分=主战场定方向, 3分=过滤位置, 30秒=触发入场
PERIOD_TREND = 15 * 60   # 900  主战场
PERIOD_FILTER = 3 * 60   # 180  过滤
PERIOD_TRIGGER = 30      # 30   触发

# ==== 共振周期组合（面板上可直接切换，无需重启）====
# 约束：趋势周期 > 过滤周期 > 触发周期（单位：秒）
PERIOD_COMBOS = [
    {"key": "scalp",    "name": "日内超短", "trend": 15 * 60, "filter": 3 * 60,  "trigger": 30,
     "trend_label": "15分", "filter_label": "3分",  "trigger_label": "30秒"},
    {"key": "swing",    "name": "短线波段", "trend": 60 * 60, "filter": 15 * 60, "trigger": 3 * 60,
     "trend_label": "1小时", "filter_label": "15分", "trigger_label": "3分"},
    {"key": "position", "name": "波段",     "trend": 24 * 60 * 60, "filter": 60 * 60, "trigger": 15 * 60,
     "trend_label": "日线", "filter_label": "1小时", "trigger_label": "15分"},
]
DEFAULT_COMBO = "scalp"


def get_combo(key=None):
    """按 key 取周期组合；key 为空或不存在时返回默认组合。"""
    default = next((c for c in PERIOD_COMBOS if c["key"] == DEFAULT_COMBO), PERIOD_COMBOS[0])
    if not key:
        return default
    return next((c for c in PERIOD_COMBOS if c["key"] == key), default)


# ==== 多周期K线查看（面板下方，按需懒加载）====
# (周期秒数, 显示名)。30秒触发腿不在这里，仍由共振组合提供。
EXTRA_KLINE_PERIODS = [
    (24 * 60 * 60, "日线"),
    (60 * 60, "1小时"),
    (30 * 60, "30分"),
    (15 * 60, "15分"),
    (5 * 60, "5分"),
    (3 * 60, "3分"),
    (60, "1分"),
]
EXTRA_KLINE_SECONDS = [p for p, _ in EXTRA_KLINE_PERIODS]

# ==== 全局监控层（全品种同时盯）====
# 取舍说明：TqSdk 免费账号有订阅数上限，11 品种 × 全周期会爆。
# 全局层每品种只挂 3 条K线：轨道A的趋势/过滤两腿 + 轨道B评分用 1h；
# 30秒触发腿 和 7档多周期查看 只给"聚焦品种"订阅（见 app.py 懒加载）。
# 全局层轨道A因此是"两腿雷达"（方向+位置过滤），完整三腿共振以聚焦面板为准。
GLOBAL_TREND_PERIOD = 15 * 60    # 全局层轨道A 趋势腿
GLOBAL_FILTER_PERIOD = 3 * 60    # 全局层轨道A 过滤腿
GLOBAL_TRACKB_PERIOD = 60 * 60   # 全局层轨道B 评分周期（1h）
# 全局层 tick 级节流：每 N 轮刷新才重算一次轨道B评分（省CPU，K线未收盘时评分不变）
GLOBAL_TRACKB_EVERY = 3

# ==== 轨道B 双底评分引擎参数（double_bottom.py 使用）====
# A. 结构识别
DB_LOOKBACK = 60         # N：回溯窗口根数（在此内找左底/左顶）
DB_PIVOT_M = 5           # M：局部极值前后各M根无更低/高点（右侧确认，无前视）
DB_REBOUND_MIN = 0.05    # 颈位反弹最小幅度 5%
DB_L2_TOL = 0.03         # |L2-L1|/L1 容差 3%
DB_L2_GAP_MIN = 10       # L2 与 L1 最小间隔根数
# B. 确认信号
DB_VOL_MA = 20           # 均量周期（放量判断基准）
DB_VOL_BREAKOUT = 1.5    # 突破放量倍数：成交量 ≥ 均量×1.5
DB_RETEST_BUF = 0.98     # 回踩不破阈值：回踩低点 > 颈位×0.98
DB_RSI_PERIOD = 14
# C. 关键位有效性评分（四因子权重：触碰0.30 / 反转幅度0.30 / 新鲜度0.20 / 放量0.20）
DB_LEVEL_TOL = 0.01      # 触碰判定容差 ±1%
DB_LEVEL_HALF_LIFE = 60  # 时间新鲜度半衰期（根）
DB_LEVEL_STRONG = 70     # ≥70 强位
DB_LEVEL_MID = 40        # 40-69 中性，<40 弱位
# D/E. 动能与评分
DB_ATR_PERIOD = 14
DB_STOP_ATR = 1.5        # 止损 = L2 外 1.5×ATR
DB_SCORE_GO = 75         # ≥75 可做多/做空
DB_SCORE_WAIT = 55       # 55-74 观望；<55 不做

# 每个周期拉取的K线根数(够算均线即可，越多越慢)
KLINE_LENGTH = 120

# ==== 均线参数 ====
MA_FAST = 5
MA_SLOW = 20

# ==== 判定阈值 ====
# 趋势斜率门槛：MA_SLOW 相对 N 根前的涨跌幅(千分比)，超过才算有方向
TREND_SLOPE_MIN = 0.5      # 千分之0.5
TREND_SLOPE_LOOKBACK = 10  # 看10根前

# 过度延伸保护：现价偏离 MA_SLOW 超过这个百分比，视为已在压力/支撑极端位，不触发追单
OVEREXTENSION_PCT = 0.8    # 0.8%

# ==== 30秒 企稳触发（防毛刺误判）====
# 需要连续 N 根"收盘价"站稳 MA_FAST 同侧，才算企稳（用收盘价，一根影线扫过不算）
TRIGGER_CONFIRM_BARS = 2
# 站稳缓冲：收盘价须越过 MA_FAST 至少这么多跳，才算真正站稳（过滤"扫一点点"）
TRIGGER_MA_BUFFER_TICKS = 1
# 触发还要求 MA_FAST 自身方向与交易方向一致（拐头/延续）

# ==== 合约跳动 & 压力贴近警告 ====
# 启动时会按 SYMBOL 从 symbols.py 预设表自动覆盖这两个值；
# 预设查不到（自定义合约）时用下面的默认值，需要时手动改这里覆盖。
# 默认白银 ag：1 跳 = 1 元/千克，最小变动 1 点；1 跳盈亏 = ¥15/手
PRICE_TICK = 1.0            # 最小变动价位（默认值，预设品种自动解析）
TICK_VALUE = 15.0           # 每跳每手盈亏(元)（默认值，预设品种自动解析）
# 现价距最近压力/支撑 ≤ 这么多跳 → 触发"逼近警告"(勿追)
PROXIMITY_TICKS = 5
# 开仓前检查：最小可接受盈亏比
MIN_RR = 1.5
# 开仓前检查：结构止损的回退跳数（价值层优先取触发周期摆动低/高点做结构止损，
# 找不到结构位时才按这个固定跳数估算，并在文案中注明）
DEFAULT_STOP_TICKS = 13

# ==== 天勤账号 ====
# 分发版：账号不写在这里，由首次启动的配置页填入，存到 user_config.json。
# 下面仅作为运行时默认占位，app.py 启动时会用用户配置覆盖。
TQ_USER = ""
TQ_PASSWORD = ""

# ==== 运行模式 ====
# True = 不连天勤，用随机模拟数据跑面板(用于先看界面/离线开发)
# False = 连接 TQSDK 拉真实行情
MOCK_MODE = False

# ==== Web 服务 ====
HOST = "127.0.0.1"
PORT = 8848
# 前端轮询间隔(毫秒)
POLL_INTERVAL_MS = 1000
