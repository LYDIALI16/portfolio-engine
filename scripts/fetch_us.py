"""
每日拉取美股持仓和候选名单的行情数据
存为 CSV 文件到 data/us/ 文件夹
"""
import yfinance as yf
import pandas as pd
import os
from datetime import datetime

TICKERS = {
    "NVDA": "英伟达",
    "NBIS": "Nebius",
    "IBKR": "盈透证券",
    "V": "Visa",
    "NEE": "NextEra Energy",
    "GEV": "GE Vernova",
    "AVGO": "Broadcom",
    "DUOL": "多邻国",
}

OUTPUT_DIR = "data/us"
os.makedirs(OUTPUT_DIR, exist_ok=True)

print(f"开始拉取，时间: {datetime.now()}")

data = yf.download(
    tickers=list(TICKERS.keys()),
    period="2y",
    group_by="ticker",
    auto_adjust=True,
    progress=False,
)

for ticker, name in TICKERS.items():
    df = data[ticker].copy().dropna()
    df.to_csv(f"{OUTPUT_DIR}/{ticker}.csv")
    latest_date = df.index[-1].strftime("%Y-%m-%d")
    latest_close = df["Close"].iloc[-1]
    print(f"  ✓ {ticker} ({name}): {latest_date}, 收盘 {latest_close:.2f}")

print(f"\n全部完成。")
