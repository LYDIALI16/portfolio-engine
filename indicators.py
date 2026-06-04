"""
派生指标计算 —— 纯函数，输入 OHLCV DataFrame，便于单测，不联网。
供 engine.py 的三层规则引擎使用。
"""
import numpy as np
import pandas as pd


def _series(df, col):
    return df[col].astype(float)


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """True Range 的 n 日均值。"""
    high = _series(df, "High")
    low = _series(df, "Low")
    close = _series(df, "Close")
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def slope(series: pd.Series, n: int = 20) -> float:
    """最近 n 日的归一化斜率（每日变化 / 当前值），用于 MA200 斜率判定。"""
    s = series.dropna()
    if len(s) < n + 1:
        return np.nan
    y = s.iloc[-n:].values
    x = np.arange(n)
    k = np.polyfit(x, y, 1)[0]
    return float(k / y[-1]) if y[-1] else np.nan


def percentile_of_last(series: pd.Series, lookback: int = 252) -> float:
    """最新值在过去 lookback 个值中的分位 (0~1)。用于 ATR% 历史分位。"""
    s = series.dropna()
    if len(s) < 2:
        return np.nan
    window = s.iloc[-lookback:]
    last = window.iloc[-1]
    return float((window < last).mean())


def drawdown_from_high(series: pd.Series, lookback: int = 60) -> float:
    """近 lookback 日的回撤：1 - close/rolling_max。返回正数（0.15=回撤15%）。"""
    s = series.dropna()
    if len(s) < 2:
        return np.nan
    window = s.iloc[-lookback:]
    peak = window.max()
    return float(1 - window.iloc[-1] / peak) if peak else np.nan


def volume_ratio(df: pd.DataFrame, n: int = 20) -> float:
    """当日成交量 / 近 n 日均量。"""
    vol = _series(df, "Volume")
    avg = vol.rolling(n).mean().iloc[-1]
    if not avg or np.isnan(avg):
        return np.nan
    return float(vol.iloc[-1] / avg)


def return_pct(series: pd.Series, n: int = 20) -> float:
    """近 n 日收益率（小数）。"""
    s = series.dropna()
    if len(s) <= n:
        return np.nan
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1)


def return_percentile(series: pd.Series, n: int = 20, lookback: int = 252) -> float:
    """近 n 日滚动收益率序列里，最新一根的分位（拥挤度用）。"""
    s = series.dropna()
    if len(s) <= n + 1:
        return np.nan
    roll = s / s.shift(n) - 1
    return percentile_of_last(roll.dropna(), lookback)


def compute(df: pd.DataFrame, benchmark: pd.Series = None) -> dict:
    """一次算好引擎需要的全部指标快照（取最新值）。"""
    close = _series(df, "Close")
    n = len(close)

    def last(s):
        v = s.iloc[-1] if len(s) else np.nan
        return float(v) if pd.notna(v) else np.nan

    a = atr(df, 14)
    ind = {
        "n": n,
        "close": last(close),
        "prev_close": float(close.iloc[-2]) if n > 1 else np.nan,
        "open": last(_series(df, "Open")),
        "ma20": last(sma(close, 20)),
        "ma50": last(sma(close, 50)),
        "ma200": last(sma(close, 200)),
        "ma200_slope": slope(sma(close, 200), 20),
        "atr": last(a),
        "rsi": last(rsi(close, 14)),
        "vol_ratio": volume_ratio(df, 20),
        "high20": float(close.iloc[-20:].max()) if n >= 1 else np.nan,
        "high22": float(_series(df, "High").iloc[-22:].max()) if n >= 1 else np.nan,
        "high52": float(close.iloc[-252:].max()),
        "ret20": return_pct(close, 20),
        "ret20_pctile": return_percentile(close, 20, 252),
        "dd60": drawdown_from_high(close, 60),
    }
    ind["atr_pct"] = ind["atr"] / ind["close"] if ind["close"] else np.nan
    # ATR% 历史分位
    atr_pct_series = (a / close).dropna()
    ind["atr_pct_pctile"] = percentile_of_last(atr_pct_series, 252)
    ind["day_change_pct"] = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if n > 1 else 0.0

    # 相对强弱（vs 基准，如 SOXX）
    if benchmark is not None and len(benchmark.dropna()) > 21:
        ind["rs20"] = (return_pct(close, 20) or 0) - (return_pct(benchmark, 20) or 0)
    else:
        ind["rs20"] = np.nan

    return ind
