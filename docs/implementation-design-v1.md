# 规则引擎实现设计 v1.0（Implementation Design）

> 把第二版 PRD（三层闸门式引擎）落到代码。本文是"代码蓝图"，确认后再实现。
> 定位：**手动决策 + 工具给可解释建议**。不自动下单。

---

## 0. 总览：三层闸门

```
Layer 1  ENV 宏观威胁扫描   → SUPPORTIVE / NEUTRAL / PRESSURE
            └─ 决定：组合仓位上限、加仓倍率 macro_factor、是否禁止高波动加仓
Layer 2  Trend 个股趋势状态  → UP_TREND / UP_PULLBACK / SIDEWAYS / DOWN_TRANSITION / DOWN_TREND
            └─ 决定：方向对不对
Layer 3  Trigger 动作引擎    → ADD / TRIM / EXIT / REENTRY / HOLD / NO_ACTION
            └─ 决定：什么时候动、动多少（ATR% 风险预算）
```
每层都输出：**一句话结论 + 数值化理由（Explain）**。

---

## 1. 文件结构（改动清单）

| 文件 | 动作 | 内容 |
|---|---|---|
| `config/portfolio.csv` | **新增**（取代 holdings.csv） | 持仓配置，字段见 §2 |
| `config/rules.yaml` | **新增** | 所有阈值参数，可调，见 §6 |
| `engine.py` | **新增** | 规则引擎核心：ENV / Trend / Trigger / 仓位，纯函数 |
| `indicators.py` | **新增** | 派生指标：MA/ATR/分位/相对强弱等纯函数 |
| `analytics.py` | 保留+精简 | 旧打分逻辑下线，保留还在用的工具函数 |
| `app.py` | 改 | 用新引擎；总览表换 action 列；个股展开 rationale；网页表格编辑持仓 |
| `scripts/fetch_us.py` | 改 | 读 portfolio.csv；A股(akshare)抓取分支 |
| `scripts/fetch_cn.py` | **新增** | akshare 抓 A 股收盘数据 → data/cn/ |

---

## 2. 持仓配置 config/portfolio.csv（各市场内占比）

CSV 仍是底层存储（git 可追溯），但**录入走网页表格**（§7），你不用手写。

| 列 | 含义 | 示例 |
|---|---|---|
| market | US / CN | US |
| symbol | 代码 | NVDA |
| name | 名称 | 英伟达 |
| current_weight | **当前持仓**（市场内%，整数） | 30 |
| target_max_weight | 策略上限（能加到多少） | 35 |
| target_min_weight | 最低持仓（可选） | 0 |
| role | CORE / SATELLITE / WATCHLIST | CORE |
| risk_class | HIGH / MEDIUM / LOW | HIGH |
| allow_add | 人工开关，是否允许加仓 | TRUE |
| enabled | 是否纳入计算 | TRUE |

> weight 用**整数**(30=30%)，比 0.30 好填。WATCHLIST 即旧"候选"，current_weight=0 但仍跟踪、给 REENTRY。

**你的初始配置（请明天核对）：**
```
US: NVDA 30/CORE/HIGH, GOOG 30/CORE/MEDIUM, NBIS 30/SATELLITE/HIGH,
    NEE 5/SATELLITE/LOW, IBKR 2/SATELLITE/LOW,
    V 0/WATCHLIST/LOW, MU 0/WATCHLIST/HIGH, AVGO 0/WATCHLIST/HIGH,
    GEV 0/WATCHLIST/MEDIUM, DUOL 0/WATCHLIST/HIGH
CN: 洛阳钼业 30/CORE/MEDIUM, 大金重工 30/CORE/MEDIUM, 协创数据 40/CORE/HIGH
```

---

## 3. Layer 1：ENV 宏观威胁扫描

**只做威胁扫描，不选股、不定方向。** 输出 ENV + macro_factor。

US 用：TNX(10年期)、VIX、DXY、SOXX、HYG。CN 用：暂用全局 ENV（A股专属宏观 v2 再加）。

| ENV | 触发（威胁扫描） | macro_factor | 行为 |
|---|---|---|---|
| **PRESSURE** | TNX 10日↑≥20bp 且 VIX>VIX60日均线 ／ DXY 10日↑≥1.5% 且 VIX>均线 ／ VIX≥30 | 0.5 | 禁止 HIGH 波动股加仓，压缩总仓位上限 |
| **NEUTRAL** | 其余 | 0.8 | 加仓打 8 折 |
| **SUPPORTIVE** | TNX 下行 且 VIX<均线 且 SOXX 走强 | 1.0 | 正常 |

macro_factor 进 §5 仓位计算（取代旧的"宏观分×0.4"黑盒）。

---

## 4. Layer 2：Trend 状态机（5 态）

基于 MA50 / MA200 结构 + MA200 斜率 + ATR 缓冲。

| 状态 | 判定 |
|---|---|
| **UP_TREND** | close>MA200 且 MA50≥MA200 且 MA200 斜率≥0 |
| **UP_PULLBACK** | UP_TREND 结构下，close 回调到 MA50/MA20 附近（`close ≤ 近20日高 − pullback_atr_mult×ATR`） |
| **SIDEWAYS** | MA50≈MA200（斜率平），无明确方向 |
| **DOWN_TRANSITION** | close<MA50 但 MA200 未确认破（预警过渡） |
| **DOWN_TREND** | close<MA200 且 MA50≤MA200（**美股需≥2条破坏确认、连续confirm_days天**防洗） |

> A股 v1：用收盘价判定（盘中跌破确认 v2）。

---

## 5. Layer 3：Trigger 动作引擎 + 仓位

### 5.1 动作判定（按优先级输出唯一动作）
```
EXIT > TRIM > REENTRY > ADD > WATCH(WATCH=PRESSURE下的提示) > HOLD > NO_ACTION
```

| 动作 | 条件 |
|---|---|
| **EXIT** | DOWN_TREND 确认（MA200破，美股≥2条/连续N天）／ 吊灯止损 `close<(近22日高 − 3×ATR)` |
| **TRIM** | UP_TREND 且 风险AMBER ／ 拥挤HIGH ／ 放量长阴(量比≥2且收<开) ／ ENV=PRESSURE且HIGH波动 |
| **REENTRY** | 曾EXIT/大幅TRIM(状态记录) 且 重回UP_TREND确认 且 风险≠RED 且 ENV≠PRESSURE。分级 15%→25%→35% |
| **ADD** | UP_PULLBACK 且 allow_add 且 current<target_max 且 ENV允许(非PRESSURE+HIGH) 且 非追高(RSI<75/波动分位<0.8/不拥挤) |
| **HOLD** | UP_TREND 持有中、无触发 |
| **NO_ACTION** | 纯噪音 / 调整量<最小阈值 |

### 5.2 仓位计算（ATR% 风险预算，输出具体%）
```
risk_budget = 单次风险预算（默认组合 0.5%）
stop_distance = max(MA200 − buffer×ATR, close − initial_stop_atr_mult×ATR)
raw_target_pct = risk_budget / (stop_distance/close)          # 风险反推目标仓位
target_pct = min(raw_target_pct, target_max_weight) × macro_factor × risk_class系数
trade_pct = target_pct − current_weight
```
- HIGH 波动 risk_class 系数<1（加仓打折）
- `|trade_pct| < 最小执行阈值(3%)` → NO_ACTION
- 每日最大动作数 ≤3（超出按风险/趋势强度排序取前3，其余降级 WATCH）

### 5.3 输出 schema（每只股票）
```
symbol, market, role, risk_class, env(全局),
trend_state, risk_flag, crowding,
current_weight, target_pct, action, trade_pct(具体%),
rationale[≤3条，带数值], as_of_date
```

---

## 6. config/rules.yaml（阈值全可调）
```yaml
global:
  confirm_days_trend_break: 2
  min_trade_pct: 0.03          # <3% 不动作
  max_actions_per_day: 3
  risk_per_trade: 0.005        # 0.5% 风险预算
  initial_stop_atr_mult: 2.0
  chandelier_n: 22
  chandelier_atr_mult: 3.0
  structure_buffer_atr_mult: 1.0
env:
  us10y_up_bp_10d: 20
  dxy_up_pct_10d: 1.5
  vix_panic: 30
  macro_factor: {SUPPORTIVE: 1.0, NEUTRAL: 0.8, PRESSURE: 0.5}
risk_class:
  HIGH:   {add_discount: 0.7, dd_red: 0.18, dd_amber: 0.10, overheat_rsi: 75, pullback_atr_mult: 1.2, pullback_ma: MA50}
  MEDIUM: {add_discount: 0.85, dd_red: 0.15, dd_amber: 0.10, pullback_atr_mult: 1.5, pullback_ma: MA50}
  LOW:    {add_discount: 1.0, dd_red: 0.12, dd_amber: 0.08, pullback_atr_mult: 1.0, pullback_ma: MA20}
reentry_ladder: [0.15, 0.25, 0.35]
```

---

## 7. 网页表格编辑持仓（解决"CSV难写"）

dashboard 侧边/顶部加「✏️ 编辑持仓」区，用 `st.data_editor` 表格：直接改数字、加删行、下拉选 role/risk_class。
保存方式（明天三选一，我会现场演示）：
- **A 导出回传**：改完下载新 CSV，你传回 GitHub
- **B 自动写回 GitHub**：配 token 存 Streamlit secrets，改完即存（最丝滑）
- **C 本地编辑**：本地跑时直接写文件再 push

---

## 8. Dashboard 变化
- 顶部：全局 **ENV 一句话**（"ENV=NEUTRAL → 加仓打8折"）
- 总览表：`action`（带色）+ `trade_pct` + `trend_state` + `risk_flag`，移除旧综合分/技术分/宏观分
- 个股明细：展开 **rationale（≤3条带数值）** + 仓位计算过程
- 风险预警横幅：同股多预警**合并一行**（修"两个V"）
- 保留：宏观指标明细表、价格图、事件日历

---

## 9. A 股数据（akshare，收盘版）
- `scripts/fetch_cn.py`：akshare 抓三只 A 股日线 → `data/cn/{code}.csv`，格式对齐 data/us
- GitHub Action 加一步跑 fetch_cn；**实测云端能否稳定抓**（akshare 偶发不稳，失败则跳过不阻断）
- 代码 = 6位数字（洛阳钼业 603993 / 大金重工 002487 / 协创数据 300857）

---

## 10. 实施顺序（建议分 3 个 patch，降低风险）
1. **引擎核心**：indicators.py + engine.py + rules.yaml + portfolio.csv（纯逻辑，充分单测）
2. **接 Dashboard**：app.py 改用引擎 + 网页表格编辑 + 修两个V
3. **A 股数据**：fetch_cn.py + Action（实测 akshare）

---

## 待你确认的点
1. §2 初始配置的 role / risk_class 分类对不对？（尤其 GOOG=MEDIUM、NBIS=SATELLITE 还是 CORE）
2. §6 阈值默认值可接受先跑起来再调吗？
3. §7 网页编辑保存方式，倾向 A/B/C 哪个？（明天演示后定也行）
4. §10 分 3 个 patch 的顺序 OK 吗？
