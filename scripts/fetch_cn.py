"""
拉取 A 股持仓的日线数据 -> data/cn/{code}.csv
用 akshare（东方财富/新浪源）。从 config/portfolio.csv 读取 market=CN 的标的。
输出格式对齐 data/us：列含 Open/High/Low/Close/Volume，索引为日期。

注意：A 股数据源对境外 IP 可能 403/限流，单只失败不阻断整体（跳过，保留旧 CSV）。
"""
import os
import pandas as pd

OUTPUT_DIR = "data/cn"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_cn_symbols():
    df = pd.read_csv("config/portfolio.csv")
    df = df[df["market"].astype(str).str.strip().str.upper() == "CN"]
    return list(zip(df["symbol"].astype(str).str.strip(),
                    df["name"].astype(str).str.strip()))


def fetch_one(ak, code: str):
    """抓单只 A 股，返回对齐 data/us 格式的 DataFrame。失败抛异常。"""
    df = ak.stock_zh_a_hist(symbol=code, period="daily",
                            start_date="20240101", adjust="qfq")
    if df is None or len(df) == 0:
        raise ValueError("空数据")
    # akshare 中文列 -> 英文，对齐 data/us
    df = df.rename(columns={
        "日期": "Date", "开盘": "Open", "最高": "High",
        "最低": "Low", "收盘": "Close", "成交量": "Volume",
    })
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
    return df.dropna()


def main():
    try:
        import akshare as ak
    except Exception as e:
        print(f"akshare 未安装/不可用：{e}")
        return

    symbols = load_cn_symbols()
    print(f"开始拉取 {len(symbols)} 只 A 股...")
    ok = fail = 0
    for code, name in symbols:
        try:
            df = fetch_one(ak, code)
            df.to_csv(f"{OUTPUT_DIR}/{code}.csv")
            print(f"  ✓ {code} ({name}): {len(df)}行, 最新 "
                  f"{df.index[-1]:%Y-%m-%d} 收盘 {df['Close'].iloc[-1]:.2f}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {code} ({name}) 失败: {type(e).__name__} {str(e)[:50]}")
            fail += 1
    print(f"\nA股完成：成功 {ok}，失败 {fail}"
          + ("（失败多半因数据源对境外IP限流）" if fail else ""))


if __name__ == "__main__":
    main()
