# SilverResonance · 多品种期货信号监控面板 — 工具总览（供 openclaw 阅读调用）

> 本文档是 openclaw 等 AI 智能体对接本工具的唯一必读说明。
> 项目目录：`D:\SilverResonance` ｜ 本文档位于项目根目录内。
> 最后更新：2026-09-03（Bug×3 修复 + 面板建议历史回测；开发日志见第 12 节）

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
全局监控层：12 品种 × 3 条K线（15分/3分/1h）→ 每轮计算灯色 + 轨道B评分
聚焦层：    当前查看品种额外挂 30秒触发腿 + 7 档多周期K线（懒加载）
落盘：      每轮把全部品种快照原子写入 state/signals_state.json
```

- 总订阅约 38–44 条（(品种,周期) 缓存复用），免费天勤账号可承受；若报订阅超限，调小 `config.py` 全局层参数或精简品种
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
      "fundamentals": {
        "inventory_trend": "去库↓偏多|累库↑偏空|持平|null",
        "basis_rate": 0.4,
        "broker_flow": "东证偏多↑；高盛偏空↓",
        "summary": "去库↓偏多 · 现货升水↑ · 东证偏多↑",
        "date": "20260722", "stale": false
      },
      "decision": {
        "quadrant": "main|probe|confirm|avoid|standby",
        "position_pct": 50,
        "location_score": 47,
        "certainty_score": 63,
        "direction": "long|short|none",
        "stabilized": false,
        "trigger_met": false,
        "stop": { "price": 8044, "basis": "结构宽止损：颈位下方1.0×ATR" },
        "wait_for": null
      },
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
- `decision`：双轴决策块（与 `/api/entry_check` 同引擎同口径，entry_logic.decide）。字段：
  - `quadrant` / `position_pct`：象限与建议仓位%（判读见第 5 节象限表）
  - `location_score` / `certainty_score`：位置分（无结构为 null）/ 确定性分
  - `stabilized`：小周期企稳——聚焦品种用完整三腿触发腿结果，其余品种用 3 分级 K线跑 `trigger_ok`（与 entry_check 小周期验证同一口径）
  - `trigger_met`：阶段触发条件（企稳K线/缩量回踩/波动收缩）是否满足，随轨道B节流节奏（`GLOBAL_TRACKB_EVERY` 轮）重算
  - `stop.price` / `stop.basis`：结构止损位与依据；`wait_for`：观望/不做时明说等什么（含价位）
  - 数据不足的品种降级为 `standby` + `wait_for` 说明，不会缺失该块
  - ⚠️ 命名说明：顶层 `entry/stop/target1` 是轨道B计划的**价格**（旧契约保留不变）；双轴决策块独立命名为 `decision`，openclaw 规则请引用 `decision.quadrant` / `decision.position_pct` / `decision.certainty_score` / `decision.trigger_met` / `decision.stop.price`
- `fundamentals`：基本面摘要（akshare 日频，后台线程每日 20:30 后刷新，失败保留旧缓存 `stale=true`）：
  - `inventory_trend`：库存近 5 期趋势（去库↓偏多 / 累库↑偏空 / 持平；无数据为 null）
  - `basis_rate`：基差率 %（现货−期货，正=现货升水偏多 / 负=贴水；|率|≤1% 视为平水）
  - `broker_flow`：关注席位（高盛/东证/摩根大通，config.BROKER_WATCHLIST 可配）日度多/空持仓变化方向；席位未上前 20 榜或大商所品种（接口不可用）为 null
  - `status`：`ok` 全部维度刷新成功 / `partial` 部分维度失败 / `stale` 刷新失败显示旧缓存 / `empty` 无数据
  - `summary`：三维度合成一句话，可直接用于推送文案
  - 排障：`/api/fundamentals?symbol=X` 返回每维度 `errors`（如"接口超时(>20s)"/"交易所未公布"/"大商所排名接口暂不可用"）与 `meta.refreshing/last_error`；面板三卡空态按此区分"加载中…/拉取失败原因/交易所未公布"
- `reasons`：人话理由，可直接用于推送文案

## 5. 面板 HTTP API（备用，优先读 state 文件）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/overview` | GET | 12 品种总览卡片数据（灯色/评分/方向/理由/基本面摘要） |
| `/api/state` | GET | 当前聚焦品种完整状态 |
| `/api/symbols` | GET | 品种清单（代码/中文名/tick） |
| `/api/fundamentals` | GET | `?symbol=X` 该品种库存/基差/席位动向（读后台日频缓存，不阻塞） |
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

**市场状态 regime（二期）**：`detect_regime(1h K线)` 用两个维度判定市况——
1. **方向效率（Kaufman ER）**：ER = |近 `REGIME_ER_WINDOW`(20) 根净位移| ÷ 逐根位移绝对值之和。ER→1 一路走（趋势顺畅），ER→0 来回拉锯。
2. **均线斜率**：MA20 现值相对 `REGIME_SLOPE_WINDOW`(5) 根前的变化率。

ER ≥ `REGIME_ER_TREND`(0.35) 且 |斜率| ≥ `REGIME_SLOPE_MIN`(0.002) → `trend_up`/`trend_down`；否则 `range`。接入确定性分：**震荡市方向项减半**（+15→+8、+8→+4，震荡里"方向"本身是噪音），**结构类信号 +5 补偿**（震荡边沿反转结构比趋势可靠）；趋势市照旧。`scores_detail.certainty` 首行单列"市场状态"明细。响应 JSON 含 `"regime": {"regime","er","slope","label"}` 块；总览卡片与 signals_state.json 每品种亦带 regime 字段（openclaw 可做"震荡市+逼近边沿"提醒）。

**象限决策（quadrant 字段）**：

| quadrant | 条件 | position_pct | 止损 |
|---|---|---|---|
| `main` 主仓 | 位置≥65 且 确定性≥60 | 75–100（按双分深度内插） | L2 外侧 1.5×ATR |
| `probe` 试单 | 位置≥65 且 确定性<60 | 25 | L2 外侧 1.5×ATR |
| `confirm` 确认仓 | 位置<65 且 确定性≥60，**仅趋势市** | 25（修复后：50→25） | 结构宽止损：颈位外侧 1×ATR |
| `avoid` 不做 | 双低 | 0 | —（wait_for 明说等什么、含价位） |
| `standby` 观望 | 位置分 null（无结构）且确定性<45 | 0 | — |

**confirm 修复（回测验证后落地）**：①震荡市（regime=range）的 confirm 一律降级为 probe 25%（summary 注明"震荡市确认仓降级为试单"），止损/目标不变——依据：震荡市 confirm 13 笔胜率仅 7.7%、-3.15R；②confirm 仓位系数 50→25——依据：confirm 象限期望 -0.196R，纯降权后总体 +0.018 expR（回测报告第七节变体 C/D，落地验证见第八节）。

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
| `REGIME_ER_WINDOW / REGIME_ER_TREND` | 20 / 0.35 | Kaufman ER 窗口与趋势阈值（ER≥阈值且斜率同向=趋势市） |
| `REGIME_MA_PERIOD / REGIME_SLOPE_WINDOW / REGIME_SLOPE_MIN` | 20 / 5 / 0.002 | 斜率判定：MA20 相对 5 根前变化率 ≥0.2% 才算有方向 |
| `REGIME_RANGE_DIR_DISCOUNT / REGIME_RANGE_STRUCT_BONUS` | 0.5 / 5 | 震荡市：方向项减半（+15→+8、+8→+4）、结构补偿 +5 |
| `GLOBAL_TREND_PERIOD` 等 | 15分/3分/1h | 全局监控层周期（省订阅的取舍，注释有说明） |
| `GLOBAL_TRACKB_EVERY` | 3 | 轨道 B 节流：每 N 轮重算一次评分 |
| `DB_*`（19 个） | 见文件 | 轨道 B 全部参数：回溯窗口 60、两底容差 3%、颈位反弹 5%、放量 1.5×、四因子权重等 |

## 9. 源文件职责

```
app.py              TqSdk 数据线程 + Flask 后端 + 全局/聚焦双层调度 + 落盘
resonance.py        轨道A 三频共振纯函数（可单测）
double_bottom.py    轨道B 双底评分引擎纯函数（无前视偏差，可单测）
entry_logic.py      双轴评分引擎纯函数（位置分×确定性分→象限决策 + detect_regime 市场状态判定，可单测）
test_double_bottom.py  轨道B 自测 6 项
test_entry_logic.py    双轴引擎自测 11 项（四象限+降级+假突破+做空镜像+regime×4）
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
---

## 12. 开发日志

### 2026-09-03（Bug 修复 ×3 + 面板建议历史回测）

#### 12.1 三个 UI/逻辑 Bug 修复（`templates/index.html`，全部已重打包 exe + 浏览器实测通过）

**Bug1 · 首页品种上限**：原总览只列当日 3 个品种且"金银占大头"，疑似有截断逻辑。
- 修复：首页**不设品种上限**，12 品种卡片全部渲染；建议区不再截断（`renderOverview` 中 `const top = active` / `const watchTop = watch` 全量引用，不再 `.slice(0,3)`），新增 CSS `.dir.none` 处理无方向灰字。

**Bug2 · 首页面板与内页/开仓面板方向不一致**：同一品种首页显示的方向词与详情内页、`/api/entry_check` 弹窗对不上。
- 修复：**双轨同显**——总览卡片同时展示轨道A方向词（track_a 灯色方向）与轨道B结构方向词（decWord2 双词 chip，如"偏空 · 结构多"），区分两类信号口径；弹窗内方向词明确"做多/做空/方向不明"。
- 实测：纯碱 701"偏空/结构多"冲突可见、不锈钢"无方向/结构空"可见，口径不再被吞。

**Bug3 · 做空方向标反误读**：用户看到做空品种的止损/目标标法与做多一致（误以为标反）。
- 排查结论：**阶梯 drawLadder 数学本身正确**——做空 = 向上止损（L2 上方）+ 向下目标（下方），实测 SS2610 做空：止损 13950.47（L2 上方）、目标 13250.0（下方）、RR 4.6，方向正确。
- 误读根因：弹窗打开后**切换品种**，弹窗内阶梯仍是旧品种、且弹窗无方向词/品种标签，造成"新品种标题 + 旧品种阶梯"错配。
- 修复：①弹窗顶部加当前品种标签 `#modal-sym`（`entryBtn.onclick` 打开时写入 `currentSymbol`）；②**切换品种时自动关闭弹窗**（`focusSymbol` 中 `modalMask.classList.remove('show')`），杜绝错配。

**方向/颜色契约（全站统一）**：涨红跌绿。红=做多 `#da3633`、绿=做空 `#2ea043`、琥珀 `#d29922`、灰无方向 `#8b949e`。

#### 12.2 发布

- `dist/SilverResonance.exe` 于 2026-09-03 15:45:19 重打包（119,184,314 字节），新进程 PID 44624 运行于 `http://127.0.0.1:8848`，boot_phase=ready。
- `dist/state/` 与 `dist/user_config.json` 重打包后保留（Flask 模板从 `sys._MEIPASS` 读取，磁盘模板改动必须重打包才生效）。

#### 12.3 面板建议历史回测（`panel_backtest.py`）

目的：量化验证"面板评分/建议"是否有历史统计价值（openclaw/用户是否应跟随面板建议交易）。

**数据**：`backtest/cache/` 12 品种 × 250 天 1h K线（`backtest.py --cache-only` 抓取），每根含 `oi`（open_oi−close_oi 持仓变化）。

**方案A · IC 分析（评分预测力，已完成）**：
- 方法：每 5 根取采样时点 t，用 `bars[:t+1]`（无未来函数）跑 `compute_panel_score_fast` 得评分/分组，再算 t 后 1/3/5/10 根收益，Spearman 相关。
- 结果：**IC 全为负且 |IC|<0.03（无效）**：h1=−0.0165、h3=−0.0119、h5=−0.0270、h10=−0.0231（样本 4800）。
- 分组收益：h3 active +0.01%(47.8%) / **watch +0.09%(49.9%)** / none +0.01%(47.6%)；h5 active +0.00%(47.0%) / **watch +0.10%(48.9%)** / none +0.06%(48.1%)。
- 初步解读：评分排序无单调预测力；watch 组反而略优于 active 组（信号触发点常在短线过热处）。**局限**：方案A 用"绝对涨幅"评不带方向的多空评分，测不出方向价值，最终结论以方案B为准。

**方案B · 每日 Top3 组合回测（已完成 2026-09-03）**：
- [1/3] 逐品种信号重放：**rows 预计算瘦身**（每品种 1 次 `precompute_rows` 生成入场决策表 {t: quadrant/direction/stop/t1/regime/score}），秒级重放生成信号。品种信号数：沪银98/沪金46/螺纹23/纯碱69/玻璃71/沥青62/棉花23/纸浆37/不锈钢35/豆粕28/焦煤95/甲醇77。
- [2/3] 256 个交易日每日评分选 Top3（直接查预计算表，不再逐根重算）。
- [3/3] 对比：基准(全信号) vs 面板Top3 vs C6半仓止盈。

**最终结果（逐位复核通过，日志 `backtest/panelB_daily_out.log`）**：

| 组别 | 交易数 | 胜率 | 平均R | 期望R | 盈亏比 | 累计R |
|---|---|---|---|---|---|---|
| 基准(全部) | 664 | 36.3% | −0.048 | +0.024 | 1.12 | +15.80 |
| 面板Top3 | 334 | 38.0% | −0.029 | +0.051 | 1.25 | +16.87 |
| C6半仓止盈 | 447 | 48.8% | +0.131 | −0.015 | 0.89 | −6.82 |

- 结论：Top3 过滤把交易数砍半、单笔期望 +0.024→+0.051（**面板当"过滤器"有效**），但累计 R 仅 +1.08；C6 半仓止盈大幅提高胜率但期望转负（止盈过早砍掉了大趋势单）。
- **日内（按交易日聚合）统计**：日胜率普遍高于笔胜率（同日多笔分散）：基准 43.8%（112盈/144亏）、Top3 45.9%、C6 49.7%；日级盈亏比 基准1.17 / Top3 1.31 / C6 0.87；最长连亏 基准6日 / Top3 7日 / C6 5日。
- **⚠️ 单日集中度极高**：三组最佳日同为 2026-01-22 — 基准单日 +18.80R（占累计 119%，去掉即亏）、Top3 单日 +12.27R（占 73%）、C6 单日 +7.12R。"面板有效"的证据几乎押在单一极端行情日上，实盘跟随前需清醒。

**rows 磁盘缓存（执行层秒级迭代）**：
- rows 只含入场决策要素，与执行层参数（TOP_N/MAX_HOLD_BARS/COST_TICKS/variant）无关 → 落盘 `backtest/cache_rows/` 复用。
- 失效机制：决策源文件（entry_logic/double_bottom/resonance/config）mtime 更新；或本文件评分权重 W_* 指纹变化（入文件名）；或手动 bump `ROWS_CACHE_VERSION`（改动 precompute_rows/入场门时）。**改执行层参数不会打掉缓存**。
- 实测：沪银首跑 87s 冷算 → 二次 `rows缓存命中` 秒级且结果逐位一致；全量 12 品种冷算约 12-15min 后，所有执行层改动即可秒级回测。强制重算用 `--no-cache`。

**回测运行备忘（踩坑记录）**：
- Python 3.13 不在 PATH，必须全路径 `C:\Users\dingzequn\AppData\Local\Programs\Python\Python313\python.exe`。
- PowerShell 重定向坑：`Start-Process -RedirectStandardOutput` **完全缓冲** Python stdout，进程跑完日志仍为空 → 正解：`python -u`（无缓冲）+ 预先 `$env:PYTHONIOENCODING='utf-8'`；`*>` 重定向会 GBK UnicodeEncodeError。
- Out-File 写 UTF-8 日志前先 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`，否则中文乱码（纯 Python 侧无关）。

### 12.4 五档真实K线 + 用户系统正版回测（2026-09-03 晚）

**背景**：方案U 用户系统回测用的低周期是 1h 线性插值伪造 → 共振 trigger 腿永不满足、灯恒灭，方案U 实为"15m 共振方向近似版"。要判用户系统正版成绩必须真实 15m/3m/30s。

**五档K线落地（`fetch_klines.py` → `backtest/klines/{SYM}_{1d|1h|15m|3m|30s}.json`，60 文件全主连真实数据）**：
- 覆盖（免费账号单次拉取上限 ~8900 根）：1d 380根/563天、1h 2130根/456天（走 cache 复用零联网）、15m 8880根/587天、3m 8900根/110天、30s 8900根/**仅 16 天**（ag 10 天 8-24 起）
- 格式：bars {dt:"%Y-%m-%d %H:%M:%S"带秒, o/h/l/c/volume/oi}，oi=close_oi−open_oi（正=增仓）
- 用法：`python fetch_klines.py [--symbols ag,rb] [--periods 15m,30s] [--force]`；**需面板(8848)关闭**（同账号互踢）
- ⚠️ 数据天花板：免费账号单次 8900 根硬上限 → 小周期历史只有 16 天；翻页(start_time 往前)能否破上限待探针验证

**方案 U 全量结果（降级近似版收官，2026-09-03，12 品种×250 交易日一手）**：
- v0 纯信号 1726 笔 胜率17.8% 累计 −104,790元；user 量仓确认 319 笔 胜率4.4% 累计 −25,720元（stop305/target14，止盈几乎摸不到）；量仓过滤砍 82% 交易但胜率反降 → **过滤方向是错的**

**正版共振系统回测（`real_ubacktest.py`，真实三周期数据）**：
- 数据：1h = 15m 聚合（`agg_1h`，与 cache 1h 重叠段逐根 close/vol/oi 100% 一致已验证）作主时钟；15m/3m/30s 真实主连；窗口 = 30s 覆盖区间 ≈8 交易日/品种
- 入场（用户实盘全要件）：①共振灯亮 = 扫 1h bar 内每根 30s 收盘时刻跑 `resonance.evaluate`（贴实盘"盘中看灯"；只看 1h 收盘会漏 9 倍信号）②双底结构 = `compute_panel_score_fast(b1h[:t+1], 三腿真实尾)` 同面板口径：decide 有方向 + quadrant∈(main,probe,confirm) + 有止损位 ③共振方向 == 双底方向 ④次根 1h 放量(>前 20 根均量) + oi>0 + 收线同向 → **t+2 开盘入场**
- 出场：**30s 逐根判定先碰哪个算哪个**（stop 50 元 / target 100 元按 tick_value 换算跳数，扣 2 跳成本 COST_TICKS=2），60 根 1h 超时，末尾强平；R=盈亏/50
- **两次实现 bug（教训）**：①入场 t+2 但主循环在"确认根"（入场前一根）就判出场 → 全部交易实际未发生即被误杀（修复：`t < pos["entry_i"]` 前只等待不判出场）②1h OHLC 判出场看不到 bar 内先摸止盈再回落 → 系统性低估止盈（修复：30s 逐根，止盈 0→3）
- **✅ 全量结果（修复后，窗口 8 天）：13 笔 胜率23% 期望−43元/笔 累计−560元，出场 stop10/target3**（m 5笔−50 / MA 4笔−130 / SA·FG·CF·ss 各1笔各−90~−100 / 其余 6 品种 0 笔）
- 诚实结论：①机制验证成功——真实 30s 上共振灯高频亮（ag 51/89 个 1h bar 内亮过），trigger 腿复活，用户系统全要件链闭环可跑 ②样本 13 笔撑不起统计结论（数据天花板非策略结论）；正版系统统计级验证需更长小周期历史 ③与方案U 结论一致的方向证据：量仓/动能过滤后仍全灭，入场时机(t+2 开盘追高)与 100/50 盈亏平衡点(需>33% 胜率)是下一步调优焦点
- 运行：`python real_ubacktest.py [--symbols ag,MA]`（纯本地缓存，面板开着也能跑）；输出到 backtest/realU_*.log