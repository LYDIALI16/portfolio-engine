"""
每日拉取美股持仓/候选行情、宏观指标、以及自动事件（财报/分红日）
存为 CSV 文件到 data/ 下，供 dashboard 离线读取。
"""
import yfinance as yf
import pandas as pd
import os
from datetime import datetime

# 持仓/候选清单从 config/holdings.csv 读取（与 dashboard 共用同一份配置）
_cfg = pd.read_csv("config/holdings.csv")
TICKERS = dict(zip(
    _cfg["ticker"].astype(str).str.strip(),
    _cfg["name"].astype(str).str.strip(),
))

# 宏观指标：文件名 -> yfinance 代码
MACRO = {
    "TNX": "^TNX",       # 10年期美债收益率（利率/收紧信号）
    "IRX": "^IRX",       # 13周美债收益率（短端，算收益率曲线用）
    "VIX": "^VIX",       # 恐慌指数
    "SOXX": "SOXX",      # 半导体板块（AI 组合命脉 + 个股跑输板块基准）
    "HYG": "HYG",        # 高收益债（信用利差/风险偏好温度计）
    "DXY": "DX-Y.NYB",   # 美元指数
}

US_DIR = "data/us"
MACRO_DIR = "data/macro"
EVENTS_DIR = "data/events"
for d in (US_DIR, MACRO_DIR, EVENTS_DIR):
    os.makedirs(d, exist_ok=True)

print(f"开始拉取，时间: {datetime.now()}")

# ---------------------------------------------------------------------------
# 1) 个股行情
# ---------------------------------------------------------------------------
data = yf.download(
    tickers=list(TICKERS.keys()),
    period="2y",
    group_by="ticker",
    auto_adjust=True,
    progress=False,
)

for ticker, name in TICKERS.items():
    df = data[ticker].copy().dropna()
    df.to_csv(f"{US_DIR}/{ticker}.csv")
    latest_date = df.index[-1].strftime("%Y-%m-%d")
    latest_close = df["Close"].iloc[-1]
    print(f"  ✓ {ticker} ({name}): {latest_date}, 收盘 {latest_close:.2f}")

# ---------------------------------------------------------------------------
# 2) 宏观指标
# ---------------------------------------------------------------------------
print("\n拉取宏观指标...")
macro_data = yf.download(
    tickers=list(MACRO.values()),
    period="1y",
    group_by="ticker",
    auto_adjust=True,
    progress=False,
)
for fname, symbol in MACRO.items():
    try:
        df = macro_data[symbol].copy().dropna()
        df.to_csv(f"{MACRO_DIR}/{fname}.csv")
        print(f"  ✓ {fname} ({symbol}): {df['Close'].iloc[-1]:.2f}")
    except Exception as e:
        print(f"  ✗ {fname} ({symbol}) 失败: {e}")

# ---------------------------------------------------------------------------
# 3) 自动事件：财报日 + 除息日
# ---------------------------------------------------------------------------
print("\n拉取事件（财报/分红）...")
event_rows = []
today = pd.Timestamp.now().normalize()

for ticker, name in TICKERS.items():
    tk = yf.Ticker(ticker)

    # 财报日（取未来的）
    try:
        edf = tk.get_earnings_dates(limit=12)
        if edf is not None and len(edf):
            edf = edf[edf.index >= today - pd.Timedelta(days=2)]
            for dt, _row in edf.iterrows():
                event_rows.append({
                    "ticker": ticker,
                    "name": name,
                    "type": "财报",
                    "date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                    "detail": "季度财报发布",
                })
    except Exception as e:
        print(f"  ✗ {ticker} 财报日失败: {e}")

    # 除息日 / 下次分红（来自 calendar）
    try:
        cal = tk.calendar
        ex = None
        if isinstance(cal, dict):
            ex = cal.get("Ex-Dividend Date")
        elif cal is not None and "Ex-Dividend Date" in getattr(cal, "index", []):
            ex = cal.loc["Ex-Dividend Date"].values[0]
        if ex is not None:
            ex_ts = pd.Timestamp(ex)
            if ex_ts >= today:
                event_rows.append({
                    "ticker": ticker,
                    "name": name,
                    "type": "除息",
                    "date": ex_ts.strftime("%Y-%m-%d"),
                    "detail": "除息日",
                })
    except Exception as e:
        print(f"  ✗ {ticker} 除息日失败: {e}")

events_df = pd.DataFrame(event_rows, columns=["ticker", "name", "type", "date", "detail"])
events_df = events_df.sort_values("date") if len(events_df) else events_df
events_df.to_csv(f"{EVENTS_DIR}/events.csv", index=False)
print(f"  ✓ 共 {len(events_df)} 条事件 -> {EVENTS_DIR}/events.csv")

print(f"\n全部完成。")
