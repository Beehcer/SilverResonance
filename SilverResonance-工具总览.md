# SilverResonance · 多品种期货信号监控面板 — 工具总览（供 openclaw 阅读调用）

> 本文档是 openclaw 等 AI 智能体对接本工具的唯一必读说明。
> 项目目录：`D:\SilverResonance` ｜ 本文档位于项目根目录内。
> 最后更新：2026-07-21（entry_check 升级为双轴评分引擎：位置分×确定性分→象限决策，entry_logic.py 纯函数）

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
| `/api/entry_check` | GET | "我要开仓"决策面板：双轴评分（位置分×确定性分→象限），见 5.1 节；mock 下 `?demo=main|probe|confirm|avoid` 强制象限演示 |
| `/api/config` | GET/POST | 配置读写（同 setup 页） |

### 5.1 开仓决策面板（entry_check）双轴评分模型（一期）

**设计立场：位置优先 vs 确定性优先是两种入场逻辑（取舍立场），2B/回踩/点3 只是光谱上的入场点采样。** 位置分与确定性分各自独立量化，入场方案由两个分数驱动；**唯一的"不做"区是双低象限（avoid）**，消灭"永远无法开仓"。引擎为 `entry_logic.py` 纯函数（可单测），输入是轨道B `evaluate()` 输出 + 现价 + 可选小周期验证，不改 resonance/double_bottom 判定语义。

**位置分 location_score（0–100，无结构时 null）**：
- 距离用 ATR(14) 度量：贴 L2/强位 ±`AX_L2_ZONE_ATR`×ATR = 90–100；颈位 ±`AX_NECK_ZONE_ATR`×ATR = 55–70；突破后延伸 >`AX_EXTEND_ATR`×ATR = <30（之后每 ATR −10）；跌破 L2 = 15
- 隐含盈亏比按 30% 权重并入（`AX_RR_WEIGHT`）：结构止损 L2 外侧 `DB_STOP_ATR`×ATR → 目标1，RR≥2 满分、RR=1 得 50、<1 大幅减分
- 方向侧强位（score≥70）贴合奖励：±0.5×ATR 内至少 92 分

**确定性分 certainty_score（0–100）**：证据计数加权，全部来自轨道B已有输出——
结构成立 +20；确认信号每条 +5（上限15）；RSI背离 +10；动能共振每条 +5（上限15）；仓量健康 +10；方向一致且 score≥75 +15（55–74 +8）；小周期企稳验证 +15（缺数据不扣分）；**假突破迹象（价破颈位但无放量确认）−15**。

**象限决策（quadrant 字段）**：

| quadrant | 条件 | position_pct | 止损 |
|---|---|---|---|
| `main` 主仓 | 位置≥65 且 确定性≥60 | 75–100（按双分深度内插） | L2 外侧 1.5×ATR |
| `probe` 试单 | 位置≥65 且 确定性<60 | 25 | L2 外侧 1.5×ATR |
| `confirm` 确认仓 | 位置<65 且 确定性≥60 | 50 | 结构宽止损：颈位外侧 1×ATR |
| `avoid` 不做 | 双低 | 0 | —（wait_for 明说等什么、含价位） |
| `standby` 观望 | 位置分 null（无结构）且确定性<45 | 0 | — |

位置分 null（无结构）时降级为仅确定性分驱动：≥`AX_CERT_DEGRADED`(45) → probe 25%，否则 standby。`entry_point_ref`（2b/retest/point3/none）由 stage 推导，仅作坐标参考，不驱动决策。

**返回 JSON 契约**：

```json
{
  "mode": "scalp|swing", "mode_label": "判定口径文案",
  "location_score": 96, "certainty_score": 95,
  "quadrant": "main|probe|confirm|avoid|standby", "quadrant_name": "主仓",
  "direction": "long|short|none", "position_pct": 97,
  "stop": {"price": 6790.0, "basis": "L2下方1.5×ATR"},
  "entry_point_ref": "2b|retest|point3|none",
  "stage": "2b_probe|…", "stage_name": "（参考信息，不驱动决策）",
  "scores_detail": {"location": [{"item": "贴近 L2 6820（0.25×ATR）", "value": 95}],
                    "certainty": [{"item": "结构成立（双底）", "delta": 20}]},
  "ladder": {"L1":…, "L2":…, "neck":…, "price":…, "stop":…, "target1":…, "target2":…},
  "wait_for": null, "summary": "人话总结"
}
```

- 前端弹窗：双轴象限图（X=位置/Y=确定性，四象限底色+发光点）+ 缩小价格阶梯 + 加减分明细列表。
- mock 演示：`/api/entry_check?demo=main|probe|confirm|avoid`（合成输入走真实引擎）；前端开 `/?demo=xxx` 后点"我要开仓"自动透传。

⚠️ 旧三批次模型的 `strength`/`tranche`/`trigger`/`targets` 字段已随双轴升级废弃；stage/stage_name 保留为参考信息。

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
| `MIN_RR` | 1.5 | 最小盈亏比（双轴引擎内置于位置分 RR 修正；stage 参考模型沿用） |
| `DEFAULT_STOP_TICKS` | 13 | 结构止损找不到摆动点时的回退跳数 |
| `TRANCHE_FRACTIONS` | [0.34, 0.33, 0.33] | 三批次计划仓位比例（试单/确认/顺势） |
| `STAGE_ZONE_ATR` | 1.0 | 2B试单区域：现价距 L2 ≤ N×ATR |
| `RETEST_ZONE_ATR` | 0.5 | 回踩确认带：现价距颈位 ≤ N×ATR |
| `RANGE_LOOKBACK / RANGE_CONTRACT_ATR` | 20 / 2.5 | 点3识别：近 N 根振幅 ≤ 系数×ATR 视为波动收缩（触发条件） |
| `AX_LOCATION_HIGH / AX_CERTAINTY_HIGH` | 65 / 60 | 双轴象限阈值：位置分/确定性分"高"半区下沿 |
| `AX_CERT_DEGRADED` | 45 | 无结构降级模式的确定性门槛（结构/确认分天然拿不到） |
| `AX_L2_ZONE_ATR / AX_NECK_ZONE_ATR / AX_EXTEND_ATR` | 0.5 / 0.5 / 2.0 | 位置分锚点：贴 L2/强位=90–100、颈位带=55–70、延伸>N×ATR=<30 |
| `AX_RR_WEIGHT` | 0.3 | 隐含盈亏比并入位置分的权重（RR≥2 满分、<1 大幅减分） |
| `AX_CERT_*`（10 项） | 见文件 | 确定性分各项权重：结构+20、确认+5(≤15)、RSI背离+10、动能+5(≤15)、仓量+10、方向+15/+8、小周期+15、假突破−15 |
| `AX_POS_MAIN_MIN/MAX、AX_POS_PROBE、AX_POS_CONFIRM` | 75/100、25、50 | 象限仓位比例（主仓按双分深度内插 75–100%） |
| `GLOBAL_TREND_PERIOD` 等 | 15分/3分/1h | 全局监控层周期（省订阅的取舍，注释有说明） |
| `GLOBAL_TRACKB_EVERY` | 3 | 轨道 B 节流：每 N 轮重算一次评分 |
| `DB_*`（19 个） | 见文件 | 轨道 B 全部参数：回溯窗口 60、两底容差 3%、颈位反弹 5%、放量 1.5×、四因子权重等 |

## 9. 源文件职责

```
app.py              TqSdk 数据线程 + Flask 后端 + 全局/聚焦双层调度 + 落盘
resonance.py        轨道A 三频共振纯函数（可单测）
double_bottom.py    轨道B 双底评分引擎纯函数（无前视偏差，可单测）
entry_logic.py      双轴评分引擎纯函数（位置分×确定性分→象限决策，可单测）
test_double_bottom.py  轨道B 自测 6 项
test_entry_logic.py    双轴引擎自测 7 项（四象限+降级+假突破+做空镜像）
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
