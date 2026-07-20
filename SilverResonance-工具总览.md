# SilverResonance · 多品种期货信号监控面板 — 工具总览（供 openclaw 阅读调用）

> 本文档是 openclaw 等 AI 智能体对接本工具的唯一必读说明。
> 项目目录：`D:\SilverResonance` ｜ 本文档位置：与项目目录同级，便于快速定位。
> 最后更新：2026-07-20（对应 git 提交 7675ca7 "v2: 多品种双层监控架构"）

---

## 1. 这是什么

本地运行的期货盯盘面板：连接天勤量化（TqSdk）实时行情，对 11 个期货品种同时计算双轨技术信号，产出"灯色 + 评分 + 交易计划"。**只提醒、不下单**。

- **轨道 A · 三频共振**：趋势周期定方向 + 过滤周期看位置 + 触发周期抓企稳，三层同向 → 亮灯（做多红 / 做空绿 / 观望灰）
- **轨道 B · 双底结构评分**：识别双底/双顶，结合支撑压力位强度、动能三指标、量价持仓四象限，输出 0–100 综合评分与入场/止损/目标

## 2. 运行方式

| 项 | 值 |
|---|---|
| 可执行文件 | `D:\SilverResonance\dist\SilverResonance.exe`（免 Python，双击即用） |
| 源码运行 | `python app.py`（需 `pip install -r requirements.txt`） |
| 面板地址 | `http://127.0.0.1:8848`（仅监听本机，不对外） |
| 行情账号 | 天勤账号，存于 `user_config.json`；**同一账号不能两台电脑同时在线** |
| 离线演示 | `config.py` 的 `MOCK_MODE = True`，无需账号和网络 |
| 打包 | 双击 `build.bat`（自动装依赖 + PyInstaller 打包到 dist/） |

**openclaw 注意**：面板 exe 必须保持运行，盯盘数据才是活的。判断方法见第 4 节"新鲜度检查"。

## 3. 架构（v2 双层监控）

```
全局监控层：11 品种 × 3 条K线（15分/3分/1h）→ 每轮计算灯色 + 轨道B评分
聚焦层：    当前查看品种额外挂 30秒触发腿 + 7 档多周期K线（懒加载）
落盘：      每轮把全部品种快照原子写入 state/signals_state.json
```

- 总订阅约 35–41 条（(品种,周期) 缓存复用），免费天勤账号可承受；若报订阅超限，调小 `config.py` 全局层参数或精简品种
- 全局层轨道 A 是"两腿雷达"（趋势+过滤，无 30 秒触发腿）；聚焦品种才是完整三腿

## 4. openclaw 对接核心：`state/signals_state.json`

**路径**：`D:\SilverResonance\state\signals_state.json`（exe 运行时在 exe 同目录 state/ 下）

**新鲜度检查（每次必读）**：`updated_at` 距当前时间 > 60 秒 → 面板没在跑，推送"面板离线"提醒而不是行情信号。

**结构**（字段全部来自真实计算，无计划时 entry/stop/target1 为 null）：

```json
{
  "updated_at": "2026-07-20 21:35:00",
  "symbols": {
    "SHFE.ag2608": {
      "name": "沪银2608",
      "price": 8120.0,
      "track_a": { "direction": "long|short|none", "resonance": true },
      "track_b": {
        "score": 78,
        "direction": "long|short|wait",
        "pattern": { "found": true, "type": "double_bottom|double_top",
                     "L1": 7950, "L2": 7980, "neck": 8110 },
        "support":    [ { "price": 7980, "score": 82 } ],
        "resistance": [ { "price": 8260, "score": 74 } ],
        "volume_oi":  { "quadrant": "价升量增仓增·健康多头", "healthy": true }
      },
      "lamp": "red|green|gray",
      "entry": 8115, "stop": 7945, "target1": 8270,
      "reasons": ["识别到双底结构：L1=7950 L2=7980 颈位=8110", "..."]
    }
  }
}
```

**字段判读规则**：
- `lamp`：red=做多共振 / green=做空共振 / gray=观望。聚焦品种为三腿结果，其余品种为两腿雷达
- `track_b.score`：≥75 出方向+交易计划；55–74 观望；<55 结构不成立
- `track_b.pattern.found`：是否存在双底/双顶结构；三价位 L1/L2/neck 可直接引用
- `support/resistance[].score`：≥70 强位 / 40–69 中性 / <40 弱位
- `volume_oi.healthy`：量价持仓是否配合方向
- `reasons`：人话理由，可直接用于推送文案

## 5. 面板 HTTP API（备用，优先读 state 文件）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/overview` | GET | 11 品种总览卡片数据（灯色/评分/方向/理由） |
| `/api/state` | GET | 当前聚焦品种完整状态 |
| `/api/symbols` | GET | 品种清单（代码/中文名/tick） |
| `/api/symbol` | POST | 热切换聚焦品种，body `{"symbol": "SHFE.rb2610"}` |
| `/api/periods` | GET | 共振周期组合清单 |
| `/api/period` | POST | 切换共振组合（scalp/swing/position） |
| `/api/kline?period=N` | GET | 多周期 K 线（86400/3600/1800/900/300/180/60） |
| `/api/double_bottom?period=N` | GET | 指定周期轨道 B 完整评分 |
| `/api/entry_check` | GET | 开仓前自检清单 |
| `/api/config` | GET/POST | 配置读写（同 setup 页） |

## 6. openclaw 盯盘任务建议（三个定时任务）

**任务 1 · 盘中信号监控**：交易时段每 3–5 分钟读 state 文件，与上次缓存对比，仅在以下情况推送（同状态不重复推）：
1. 某品种 `lamp` 由灰变红/绿（共振出现）
2. `track_b.score` 上穿 75 或下穿 55（结构形成/失效）
3. 价格突破 `neck` 但 `volume_oi.healthy=false`（疑似假突破）

推送模板：`灯色 emoji + 品种名 + 方向 + 现价 + 评分 + 三价位 + 入场/止损/目标 + reasons[0]`

**任务 2 · 夜盘开盘简报（21:05）**：各品种评分排名 + 夜盘需盯关键位（接近颈位/强支撑压力的品种）。

**任务 3 · 收盘复盘（15:05）**：当日亮灯信号与实际走势对照，统计命中率。

**交易时段参考**：日盘 09:00–10:15 / 10:30–11:30 / 13:30–15:00；夜盘 21:00 起（au/ag 至次日 02:30，其余至 23:00）。

**红线**：openclaw 只读 state 文件和 API；不读 `user_config.json`（含账号密码）；不执行任何下单/转账；止损必须由用户在交易端挂条件单，推送不能当风控。

## 7. 品种配置（symbols.py）

| 品种 | 代码 | 每跳盈亏 | 夜盘 |
|---|---|---|---|
| 沪金2608 | SHFE.au2608 | 20 元 | 至 02:30 |
| 沪银2608 | SHFE.ag2608 | 15 元 | 至 02:30 |
| 螺纹钢2610 | SHFE.rb2610 | 10 元 | 至 23:00 |
| 纯碱609 | CZCE.SA609 | 20 元 | 至 23:00 |
| 玻璃609 | CZCE.FG609 | 20 元 | 至 23:00 |
| 沥青2609 | SHFE.bu2609 | 10 元 | 至 23:00 |
| 棉花609 | CZCE.CF609 | 25 元 | 至 23:00 |
| 纸浆2609 | SHFE.sp2609 | 20 元 | 至 23:00 |
| 不锈钢2608 | SHFE.ss2608 | 25 元 | 至 23:00 |
| 豆粕2609 | DCE.m2609 | 10 元 | 至 23:00 |
| 焦煤2609 | DCE.jm2609 | 30 元 | 至 23:00 |

换月维护：到期后修改 `symbols.py` 中对应代码与中文名（如 ag2608→ag2609），全局层自动跟随。

## 8. 关键配置（config.py，改完重启生效）

| 参数 | 默认 | 说明 |
|---|---|---|
| `PERIOD_COMBOS` | 3 档 | 共振组合：scalp 15分/3分/30秒；swing 1h/15分/3分；position 日线/1h/15分 |
| `MA_FAST / MA_SLOW` | 5 / 20 | 均线周期 |
| `TREND_SLOPE_MIN` | 0.5‰ | 趋势斜率门槛，太敏感调大 |
| `OVEREXTENSION_PCT` | 0.8% | 过度延伸保护，越小越不容易追高亮灯 |
| `PROXIMITY_TICKS` | 5 | 距压力/支撑 ≤ N 跳触发逼近警告 |
| `MIN_RR` | 1.5 | 开仓自检最小盈亏比 |
| `GLOBAL_TREND_PERIOD` 等 | 15分/3分/1h | 全局监控层周期（省订阅的取舍，注释有说明） |
| `GLOBAL_TRACKB_EVERY` | 3 | 轨道 B 节流：每 N 轮重算一次评分 |
| `DB_*`（19 个） | 见文件 | 轨道 B 全部参数：回溯窗口 60、两底容差 3%、颈位反弹 5%、放量 1.5×、四因子权重等 |

## 9. 源文件职责

```
app.py              TqSdk 数据线程 + Flask 后端 + 全局/聚焦双层调度 + 落盘
resonance.py        轨道A 三频共振纯函数（可单测）
double_bottom.py    轨道B 双底评分引擎纯函数（无前视偏差，可单测）
test_double_bottom.py  轨道B 自测 6 项
symbols.py          品种预设表（代码/中文名/tick/每跳盈亏）
config.py           全部可调参数
user_settings.py    user_config.json 读写
templates/index.html   总览页 + 详情面板
templates/setup.html   首次配置页（账号/品种）
build.bat / build.spec 一键打包
```

## 10. 常见故障排查

| 现象 | 原因与处理 |
|---|---|
| state 文件 >60 秒未更新 | 面板没运行，提醒用户启动 exe |
| 卡在"正在拉取K线" | 合约已换月（改 symbols.py）/ 账号在别处登录 / 非交易时段 |
| 订阅超限报错 | 精简品种或调小全局层周期 |
| 某品种显示"数据收集中" | 该品种 K 线尚未填满，等 1–2 分钟 |
| exe 被拷走后要账号 | 正常，首次运行在配置页填各自天勤账号 |

## 11. 免责

本工具只做技术信号过滤与提醒，不构成投资建议，不保证盈利。亮灯 ≠ 可进场，盈亏比与仓位管理由用户自行决策。
