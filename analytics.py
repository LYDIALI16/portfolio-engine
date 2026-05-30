"""
分析引擎：技术面指标、宏观因素、加减仓信号
全部为纯函数，输入 DataFrame / Series，方便测试，不联网。
"""
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# 技术面指标
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> dict:
    """从 OHLCV DataFrame 计算技术指标，返回最新值字典。"""
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    n = len(close)

    def last(series):
        v = series.iloc[-1]
        return float(v) if pd.notna(v) else np.nan

    ind = {"price": last(close), "n": n}

    ind["sma20"] = last(close.rolling(20).mean())
    ind["sma50"] = last(close.rolling(50).mean())
    ind["sma200"] = last(close.rolling(200).mean())

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    ind["rsi"] = last(rsi)

    # MACD(12,26,9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - macd_signal
    ind["macd_hist"] = last(hist)
    ind["macd_hist_prev"] = float(hist.iloc[-2]) if n > 1 and pd.notna(hist.iloc[-2]) else np.nan

    # 52 周高低
    win = close.iloc[-252:]
    ind["high52"] = float(win.max())
    ind["low52"] = float(win.min())
    ind["pct_from_high"] = (ind["price"] - ind["high52"]) / ind["high52"] * 100 if ind["high52"] else np.nan
    ind["pct_from_low"] = (ind["price"] - ind["low52"]) / ind["low52"] * 100 if ind["low52"] else np.nan

    # 成交量
    ind["vol"] = last(volume)
    ind["vol_avg20"] = last(volume.rolling(20).mean())
    ind["vol_ratio"] = ind["vol"] / ind["vol_avg20"] if ind["vol_avg20"] else np.nan

    # 当日涨跌（用于判断量价配合 / 跳空突破）
    ind["day_change_pct"] = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if n > 1 else 0.0

    return ind


def technical_score(ind: dict) -> dict:
    """根据指标打分 (-100~+100)，返回分数、理由、是否重大突破。"""
    score = 0
    reasons = []
    breakout = False

    price, s50, s200 = ind["price"], ind["sma50"], ind["sma200"]

    # 趋势（均线排列）
    if not np.isnan(s50) and not np.isnan(s200):
        if price > s50 > s200:
            score += 25
            reasons.append("📈 多头排列（价>50日>200日）")
        elif price < s50 < s200:
            score -= 25
            reasons.append("📉 空头排列（价<50日<200日）")
        elif price > s200:
            score += 8
            reasons.append("站上200日均线")
        else:
            score -= 8
            reasons.append("跌破200日均线")

    # RSI
    rsi = ind["rsi"]
    if not np.isnan(rsi):
        if rsi < 30:
            score += 15
            reasons.append(f"RSI={rsi:.0f} 超卖")
        elif rsi > 75:
            score -= 15
            reasons.append(f"RSI={rsi:.0f} 严重超买")
        elif rsi > 70:
            score -= 8
            reasons.append(f"RSI={rsi:.0f} 超买")

    # MACD 金叉 / 死叉
    h, hp = ind["macd_hist"], ind["macd_hist_prev"]
    if not np.isnan(h) and not np.isnan(hp):
        if h > 0 and hp <= 0:
            score += 20
            reasons.append("MACD 金叉")
        elif h < 0 and hp >= 0:
            score -= 20
            reasons.append("MACD 死叉")
        elif h > 0:
            score += 6
        else:
            score -= 6

    # 52 周高位突破（重大突破）
    pfh = ind["pct_from_high"]
    if not np.isnan(pfh):
        if pfh >= -2:
            score += 22
            breakout = True
            reasons.append("🚀 突破/逼近52周新高")
        elif pfh < -40:
            score -= 10
            reasons.append(f"较52周高回撤{pfh:.0f}%")

    # 量价齐升的事件驱动突破（放量大涨，近似“消息面突破”）
    vr, dc = ind["vol_ratio"], ind["day_change_pct"]
    if not np.isnan(vr):
        if vr >= 2 and dc >= 4:
            score += 18
            breakout = True
            reasons.append(f"⚡ 放量大涨（量比{vr:.1f}，+{dc:.1f}%）")
        elif vr >= 2 and dc <= -4:
            score -= 18
            reasons.append(f"放量大跌（量比{vr:.1f}，{dc:.1f}%）")

    score = int(max(-100, min(100, score)))
    return {"score": score, "reasons": reasons, "breakout": breakout}


# ---------------------------------------------------------------------------
# 宏观面
# ---------------------------------------------------------------------------
def macro_stance(macro: dict) -> dict:
    """
    macro: {"TNX": series, "VIX": series, "DXY": series, ...}（可缺失）
    返回宏观分 (-100~+100，正=顺风) 与理由。宏观分会被赋予较低权重。
    """
    score = 0
    reasons = []
    tightening = False

    def trend(series, lookback=20):
        s = series.dropna()
        if len(s) <= lookback:
            return np.nan
        return float(s.iloc[-1] - s.iloc[-1 - lookback])

    # 10年期美债收益率：上行=利率收紧=逆风
    tnx = macro.get("TNX")
    if tnx is not None and len(tnx.dropna()):
        d = trend(tnx)
        if not np.isnan(d):
            if d > 0.15:
                score -= 25
                tightening = True
                reasons.append(f"🔺 10年期美债收益率近月上行{d:.2f}%（利率收紧）")
            elif d < -0.15:
                score += 20
                reasons.append(f"🔻 10年期美债收益率近月下行{abs(d):.2f}%（利率宽松）")
            else:
                reasons.append("10年期美债收益率窄幅波动")

    # VIX：恐慌指数
    vix = macro.get("VIX")
    if vix is not None and len(vix.dropna()):
        v = float(vix.dropna().iloc[-1])
        if v >= 30:
            score -= 20
            reasons.append(f"VIX={v:.0f} 市场恐慌")
        elif v >= 20:
            score -= 8
            reasons.append(f"VIX={v:.0f} 波动偏高")
        elif v < 15:
            score += 10
            reasons.append(f"VIX={v:.0f} 情绪平稳")

    # 美元指数：走强对美股偏逆风
    dxy = macro.get("DXY")
    if dxy is not None and len(dxy.dropna()):
        d = trend(dxy)
        if not np.isnan(d):
            if d > 1.5:
                score -= 8
                reasons.append("美元指数走强")
            elif d < -1.5:
                score += 6
                reasons.append("美元指数走弱")

    score = int(max(-100, min(100, score)))
    return {"score": score, "reasons": reasons, "tightening": tightening}


# ---------------------------------------------------------------------------
# 综合信号：宏观权重 < 个股，个股重大突破可压过宏观逆风
# ---------------------------------------------------------------------------
MACRO_WEIGHT = 0.4  # 宏观影响小于个股单独因素

LEVELS = [
    (50, "🟢🟢 强烈加仓"),
    (20, "🟢 加仓"),
    (-20, "🔵 持有"),
    (-50, "🟡 减仓"),
    (-1000, "🔴 强烈减仓"),
]


def _label(score: float) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return LEVELS[-1][1]


def combined_signal(tech: dict, macro: dict) -> dict:
    """
    合并技术面与宏观面。
    规则：综合分 = 技术分 + 宏观分 * 权重(<1)。
    若个股出现重大突破（技术/事件），即便利率收紧，下限锁定在“加仓”，
    宏观逆风不能把强突破拖到持有/减仓以下。
    """
    tech_score = tech["score"]
    macro_score = macro["score"]
    combined = tech_score + macro_score * MACRO_WEIGHT

    overridden = False
    if tech["breakout"] and macro_score < 0:
        # 重大突破 + 宏观逆风：锁定不低于“加仓”阈值
        if combined < 20:
            combined = 20
            overridden = True

    combined = max(-100, min(100, combined))
    return {
        "combined": round(combined, 1),
        "label": _label(combined),
        "tech_score": tech_score,
        "macro_score": macro_score,
        "macro_overridden": overridden,
    }


def analyze(df: pd.DataFrame, macro: dict, benchmark: pd.Series = None,
            days_to_earnings: int = None) -> dict:
    """一站式：从行情+宏观得到完整分析结果，并附带风险预警。"""
    ind = compute_indicators(df)
    tech = technical_score(ind)
    mac = macro_stance(macro)
    sig = combined_signal(tech, mac)
    alerts = risk_alerts(df, ind, benchmark=benchmark, days_to_earnings=days_to_earnings)
    return {"indicators": ind, "technical": tech, "macro": mac,
            "signal": sig, "alerts": alerts}


# ---------------------------------------------------------------------------
# 风险预警：价格层面的“烟雾报警器”（不含新闻面）
# ---------------------------------------------------------------------------
def risk_alerts(df: pd.DataFrame, ind: dict, benchmark: pd.Series = None,
                days_to_earnings: int = None) -> list:
    """
    返回命中的风险预警列表，每条 {level, tag, detail}。
    level: 'high'(红) / 'warn'(黄)。数据缺失的规则自动跳过，不报错。
    """
    alerts = []
    close = df["Close"].astype(float)

    # 1) 放量下跌：量比≥2 且 当日跌幅≥4%
    vr, dc = ind.get("vol_ratio", np.nan), ind.get("day_change_pct", np.nan)
    if not np.isnan(vr) and not np.isnan(dc) and vr >= 2 and dc <= -4:
        alerts.append({"level": "high", "tag": "📉 放量下跌",
                       "detail": f"量比{vr:.1f}，当日{dc:.1f}%（疑似利空出货）"})

    # 2) 跌破均线
    price, s50, s200 = ind.get("price"), ind.get("sma50"), ind.get("sma200")
    if price is not None and not np.isnan(s200) and price < s200:
        alerts.append({"level": "high", "tag": "🔻 跌破200日",
                       "detail": "价格跌破200日均线，长期趋势转弱"})
    elif price is not None and not np.isnan(s50) and price < s50:
        alerts.append({"level": "warn", "tag": "🔻 跌破50日",
                       "detail": "价格跌破50日均线，中期走弱"})

    # 3) 异常波动：当日波幅 > 自身20日日常波幅的 2.5 倍
    daily_range = (df["High"].astype(float) - df["Low"].astype(float)) / close
    if len(daily_range) >= 21:
        today_range = float(daily_range.iloc[-1])
        normal = float(daily_range.iloc[-21:-1].mean())
        if normal > 0 and today_range > normal * 2.5:
            alerts.append({"level": "warn", "tag": "⚡ 异常波动",
                           "detail": f"当日振幅{today_range*100:.1f}%，达日常的{today_range/normal:.1f}倍"})

    # 4) 跑输板块：近5日相对基准(SOXX等)跑输≥8%
    if benchmark is not None and len(benchmark.dropna()) > 5 and len(close) > 5:
        try:
            stock_ret = close.iloc[-1] / close.iloc[-6] - 1
            bench_ret = benchmark.dropna().iloc[-1] / benchmark.dropna().iloc[-6] - 1
            diff = (stock_ret - bench_ret) * 100
            if diff <= -8:
                alerts.append({"level": "high", "tag": "📊 跑输板块",
                               "detail": f"近5日相对板块跑输{diff:.1f}%（疑似个股利空）"})
        except Exception:
            pass

    # 5) 财报临近：未来7天内
    if days_to_earnings is not None and 0 <= days_to_earnings <= 7:
        alerts.append({"level": "warn", "tag": "🗓️ 财报临近",
                       "detail": f"{days_to_earnings}天后财报，高波动雷区"})

    return alerts
