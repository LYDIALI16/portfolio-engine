"""
拉取 A 股持仓的日线数据 -> data/cn/{code}.csv
从 config/portfolio.csv 读取 market=CN 的标的。输出对齐 data/us（Open/High/Low/Close/Volume）。

抗封锁加固：
- 多源回退：东财历史 → 新浪历史（任一成功即可）
- 每只之间随机延时 3~6 秒，避免高频被东财掐断（RemoteDisconnected）
- 单只失败自动重试，最终失败不阻断整体（保留旧 CSV）
"""
import os
import time
import random
import pandas as pd

OUTPUT_DIR = "data/cn"
os.makedirs(OUTPUT_DIR, exist_ok=True)

STD_COLS = ["Open", "High", "Low", "Close", "Volume"]


def load_cn_symbols():
    df = pd.read_csv("config/portfolio.csv")
    df = df[df["market"].astype(str).str.strip().str.upper() == "CN"]
    return list(zip(df["symbol"].astype(str).str.strip(),
                    df["name"].astype(str).str.strip()))


def _normalize(df, mapping):
    df = df.rename(columns=mapping)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")[STD_COLS]
    return df.apply(pd.to_numeric, errors="coerce").dropna()


def from_eastmoney(ak, code):
    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date="20240101", adjust="qfq")
    if df is None or len(df) == 0:
        raise ValueError("东财空数据")
    return _normalize(df, {"日期": "Date", "开盘": "Open", "最高": "High",
                           "最低": "Low", "收盘": "Close", "成交量": "Volume"})


def _sina_symbol(code):
    # 新浪要带市场前缀：6开头沪市 sh，其余深市 sz
    return ("sh" if code.startswith("6") else "sz") + code


def from_sina(ak, code):
    df = ak.stock_zh_a_daily(symbol=_sina_symbol(code),
                             start_date="20240101", adjust="qfq")
    if df is None or len(df) == 0:
        raise ValueError("新浪空数据")
    df = df.reset_index()
    cols = {c.lower(): c for c in df.columns}
    # 新浪列：date/open/high/low/close/volume
    return _normalize(df.rename(columns={
        cols.get("date", "date"): "Date", cols.get("open", "open"): "Open",
        cols.get("high", "high"): "High", cols.get("low", "low"): "Low",
        cols.get("close", "close"): "Close", cols.get("volume", "volume"): "Volume",
    }), {})


def fetch_one(ak, code, retries=3):
    """多源 + 重试抓单只。全失败抛最后一个异常。"""
    last_err = None
    for attempt in range(retries):
        for source in (from_eastmoney, from_sina):
            try:
                df = source(ak, code)
                if len(df):
                    return df, source.__name__
            except Exception as e:
                last_err = e
        time.sleep(2 + attempt * 2)  # 重试前退避
    raise last_err if last_err else ValueError("未知失败")


def main():
    try:
        import akshare as ak
    except Exception as e:
        print(f"akshare 未安装/不可用：{e}")
        return

    symbols = load_cn_symbols()
    print(f"开始拉取 {len(symbols)} 只 A 股（多源+延时+重试）...")
    ok = fail = 0
    for i, (code, name) in enumerate(symbols):
        try:
            df, src = fetch_one(ak, code)
            df.to_csv(f"{OUTPUT_DIR}/{code}.csv")
            print(f"  ✓ {code} ({name})[{src}]: {len(df)}行, 最新 "
                  f"{df.index[-1]:%Y-%m-%d} 收盘 {df['Close'].iloc[-1]:.2f}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {code} ({name}) 失败: {type(e).__name__} {str(e)[:50]}")
            fail += 1
        # 每只之间随机延时，避免高频被掐断（最后一只不等）
        if i < len(symbols) - 1:
            time.sleep(random.uniform(3, 6))
    print(f"\nA股完成：成功 {ok}，失败 {fail}"
          + ("（仍失败可能是数据源限流，过会儿重试或本地挂代理）" if fail else ""))


if __name__ == "__main__":
    main()
