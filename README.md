# portfolio-engine

持仓监控 Dashboard：技术面 + 宏观因素 → 加减仓信号，含大事件日历。

## 运行

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 功能

- **加减仓信号**：综合分 = 技术分 + 宏观分 × 权重（`analytics.MACRO_WEIGHT = 0.4`）。
  宏观影响小于个股；个股出现重大突破（逼近52周新高 / 放量大涨）时，即便利率收紧，
  信号下限锁定在「加仓」，宏观逆风无法将其拖到持有 / 减仓以下。
- **技术面**：均线排列、RSI(14)、MACD 金叉/死叉、52周高位突破、量价配合。
- **宏观面**：10年期美债收益率（利率收紧）、VIX、美元指数。
- **大事件日历**：自动抓取财报日 / 除息日及预计时间（`scripts/fetch_us.py`）。
- **实时查看**：开启盘中实时报价（yfinance，60秒缓存），断网自动回退到每日 CSV；
  页面可按 30/60/120/300 秒自动刷新。

## 数据

- `scripts/fetch_us.py`：拉取个股行情（`data/us/`）、宏观指标（`data/macro/`）、
  事件（`data/events/events.csv`）。由 GitHub Action 每日收盘后自动运行并提交。
- `analytics.py`：纯函数分析引擎（技术 / 宏观 / 综合信号），便于测试。
