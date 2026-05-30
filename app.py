"""
持仓监控 Dashboard
- 盘中实时报价（yfinance，带缓存，断网回退到 data/us/ 的 CSV）
- 技术面打分 + 宏观因素 -> 加减仓信号标识（宏观权重低于个股，重大突破可压过宏观）
- 风险预警标识（价格层面的烟雾报警器，不含新闻面）
- 自动抓取的持仓大事件（财报 / 除息）及预计时间
- 持仓清单从 config/holdings.csv 读取，可在网页直接编辑
"""
import os
import pandas as pd
import streamlit as st

import analytics

st.set_page_config(page_title="持仓监控", page_icon="📈", layout="wide")

US_DIR = "data/us"
MACRO_DIR = "data/macro"
EVENTS_DIR = "data/events"
CONFIG_FILE = "config/holdings.csv"

# 用作个股“跑输板块”比较的基准（半导体板块）；data/us 里有则启用
BENCHMARK_TICKER = "SOXX"


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600)
def load_holdings() -> dict:
    """从 config/holdings.csv 读取持仓/候选清单，支持网页直接编辑。"""
    df = pd.read_csv(CONFIG_FILE)
    holdings = {}
    for _, r in df.iterrows():
        raw_tags = str(r.get("tags", "") or "")
        tags = [t.strip() for t in raw_tags.split(";")
                if t.strip() and t.strip().lower() != "nan"]
        holdings[str(r["ticker"]).strip()] = {
            "name": str(r["name"]).strip(),
            "weight": float(r["weight"]),
            "type": str(r["type"]).strip(),
            "tags": tags,
        }
    return holdings


@st.cache_data(ttl=3600)
def load_history(ticker: str) -> pd.DataFrame:
    path = f"{US_DIR}/{ticker}.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, index_col=0, parse_dates=True)


@st.cache_data(ttl=3600)
def load_macro() -> dict:
    out = {}
    if not os.path.isdir(MACRO_DIR):
        return out
    for fname in os.listdir(MACRO_DIR):
        if fname.endswith(".csv"):
            key = fname[:-4]
            df = pd.read_csv(f"{MACRO_DIR}/{fname}", index_col=0, parse_dates=True)
            out[key] = df["Close"]
    return out


@st.cache_data(ttl=3600)
def load_events() -> pd.DataFrame:
    path = f"{EVENTS_DIR}/events.csv"
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ticker", "name", "type", "date", "detail"])
    return pd.read_csv(path, parse_dates=["date"])


@st.cache_data(ttl=60, show_spinner=False)
def live_quotes(tickers: tuple) -> dict:
    """盘中实时报价；带 60 秒缓存，断网/失败时返回空，让调用方回退 CSV。"""
    try:
        import yfinance as yf

        data = yf.download(
            tickers=list(tickers), period="2d", interval="1m",
            group_by="ticker", progress=False, auto_adjust=True,
        )
        out = {}
        for t in tickers:
            try:
                close = data[t]["Close"].dropna()
                if len(close):
                    out[t] = float(close.iloc[-1])
            except Exception:
                pass
        return out
    except Exception:
        return {}


def days_to_next_earnings(events: pd.DataFrame, ticker: str):
    """从事件表算该股距下次财报的天数；无数据返回 None。"""
    if events is None or events.empty:
        return None
    today = pd.Timestamp.now().normalize()
    sub = events[(events["ticker"] == ticker) & (events["type"] == "财报")].copy()
    if sub.empty:
        return None
    sub["date"] = pd.to_datetime(sub["date"])
    future = sub[sub["date"] >= today]
    if future.empty:
        return None
    return int((future["date"].min() - today).days)


try:
    HOLDINGS = load_holdings()
except Exception as e:
    st.error(f"读取持仓配置 {CONFIG_FILE} 失败：{e}")
    st.stop()

# ---------------------------------------------------------------------------
# 侧边栏：实时刷新控制
# ---------------------------------------------------------------------------
st.sidebar.header("⚙️ 设置")
realtime = st.sidebar.toggle("盘中实时报价", value=True,
                             help="开启后用 yfinance 拉盘中价；断网自动回退到每日 CSV")
auto = st.sidebar.toggle("页面自动刷新", value=True)
interval = st.sidebar.select_slider("刷新间隔（秒）", options=[30, 60, 120, 300], value=60)

if auto:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=interval * 1000, key="refresh")
    except Exception:
        st.sidebar.caption("（未安装 streamlit-autorefresh，自动刷新不可用）")

macro = load_macro()
events = load_events()
quotes = live_quotes(tuple(HOLDINGS.keys())) if realtime else {}
data_source = "🟢 盘中实时" if quotes else "🕒 每日收盘（CSV）"

# 板块基准（用于个股“跑输板块”预警）
benchmark = load_history(BENCHMARK_TICKER)
benchmark_series = benchmark["Close"] if not benchmark.empty else None

st.title("📈 持仓监控引擎")
st.caption(f"数据来源：{data_source}　|　刷新于 {pd.Timestamp.now():%Y-%m-%d %H:%M:%S}")

# 宏观面板
mac = analytics.macro_stance(macro)
m1, m2 = st.columns([1, 3])
m1.metric("宏观环境", "逆风 🔻" if mac["score"] < 0 else ("顺风 🔺" if mac["score"] > 0 else "中性"),
          f"{mac['score']:+d} 分（权重 {analytics.MACRO_WEIGHT}）")
with m2:
    if mac["reasons"]:
        st.write("**宏观因素：** " + "　·　".join(mac["reasons"]))
    else:
        st.write("**宏观因素：** 暂无宏观数据（运行 fetch_us.py 后生成 data/macro/）")
    if mac["tightening"]:
        st.caption("⚠️ 当前利率收紧。注意：个股若有重大突破，信号仍可锁定在“加仓”。")

st.divider()

# ---------------------------------------------------------------------------
# 全部分析（一次算好，供下面各区块复用）
# ---------------------------------------------------------------------------
analysis_cache = {}
for ticker in HOLDINGS:
    df = load_history(ticker)
    if df.empty:
        continue
    dte = days_to_next_earnings(events, ticker)
    analysis_cache[ticker] = analytics.analyze(
        df, macro, benchmark=benchmark_series, days_to_earnings=dte
    )

# ---------------------------------------------------------------------------
# 风险预警横幅：把命中 high 级预警的持仓置顶提醒
# ---------------------------------------------------------------------------
st.subheader("🚨 风险预警")
alert_lines = []
for ticker, info in HOLDINGS.items():
    res = analysis_cache.get(ticker)
    if not res:
        continue
    for a in res["alerts"]:
        icon = "🔴" if a["level"] == "high" else "🟡"
        alert_lines.append((a["level"], f"{icon} **{ticker} {info['name']}** ｜ {a['tag']}：{a['detail']}"))

if alert_lines:
    # high 级在前
    alert_lines.sort(key=lambda x: 0 if x[0] == "high" else 1)
    for _lvl, line in alert_lines:
        st.markdown(line)
    st.caption("⚠️ 这是**价格层面**的预警（量价/均线/波动/板块/财报日），不含新闻消息面。"
               "请结合公司公告与新闻自行判断。")
else:
    st.success("当前无持仓触发价格层面风险预警。")

st.divider()

# ---------------------------------------------------------------------------
# 持仓总览 + 信号
# ---------------------------------------------------------------------------
total_weight = sum(h["weight"] for h in HOLDINGS.values())
c1, c2, c3 = st.columns(3)
c1.metric("已用美股仓位", f"{total_weight*100:.0f}%")
c2.metric("现金仓位", f"{(1-total_weight)*100:.0f}%")
c3.metric("跟踪股票数", len(HOLDINGS))

rows = []
for ticker, info in HOLDINGS.items():
    df = load_history(ticker)
    if df.empty:
        continue
    res = analysis_cache[ticker]

    csv_close = df["Close"].iloc[-1]
    price = quotes.get(ticker, csv_close)
    prev = df["Close"].iloc[-2] if len(df) > 1 else csv_close
    change_pct = (price - prev) / prev * 100

    sig = res["signal"]
    # 预警汇总：有 high 显示 🔴，仅 warn 显示 🟡
    levels = [a["level"] for a in res["alerts"]]
    if "high" in levels:
        warn_cell = "🔴 " + "/".join(a["tag"].split(" ")[-1] for a in res["alerts"])
    elif levels:
        warn_cell = "🟡 " + "/".join(a["tag"].split(" ")[-1] for a in res["alerts"])
    else:
        warn_cell = ""

    flag = "↑突破压过宏观" if sig["macro_overridden"] else ""
    rows.append({
        "信号": sig["label"],
        "预警": warn_cell,
        "类型": info["type"],
        "代码": ticker,
        "名称": info["name"],
        "仓位": f"{info['weight']*100:.0f}%",
        "最新价": f"{price:.2f}",
        "涨跌": f"{change_pct:+.2f}%",
        "技术分": sig["tech_score"],
        "宏观分": sig["macro_score"],
        "综合分": sig["combined"],
        "备注": flag,
    })

st.subheader("持仓总览与加减仓信号")
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
st.caption("信号 = 技术分 + 宏观分 × 权重。🟢🟢强烈加仓 ≥50 ｜ 🟢加仓 ≥20 ｜ 🔵持有 ｜ 🟡减仓 ｜ 🔴强烈减仓。"
           "宏观影响小于个股；个股重大突破时下限锁定在“加仓”。")

st.divider()

# ---------------------------------------------------------------------------
# 个股明细
# ---------------------------------------------------------------------------
st.subheader("个股明细")
selected = st.selectbox("选择查看", options=list(HOLDINGS.keys()),
                        format_func=lambda t: f"{t} - {HOLDINGS[t]['name']}")

df = load_history(selected)
if not df.empty and selected in analysis_cache:
    res = analysis_cache[selected]
    ind, tech, sig = res["indicators"], res["technical"], res["signal"]
    price = quotes.get(selected, df["Close"].iloc[-1])

    top = st.columns(4)
    top[0].metric("建议", sig["label"], f"综合 {sig['combined']}")
    top[1].metric("最新价", f"${price:.2f}")
    top[2].metric("52周高", f"${ind['high52']:.2f}", f"{ind['pct_from_high']:+.1f}%")
    top[3].metric("RSI(14)", f"{ind['rsi']:.0f}" if not pd.isna(ind['rsi']) else "—")

    # 个股风险预警
    if res["alerts"]:
        for a in res["alerts"]:
            msg = f"{a['tag']}：{a['detail']}"
            if a["level"] == "high":
                st.error(msg)
            else:
                st.warning(msg)

    left, right = st.columns([3, 2])
    with left:
        st.line_chart(df["Close"])
    with right:
        st.markdown("**技术面理由：**")
        for r in tech["reasons"]:
            st.write("·", r)
        if not tech["reasons"]:
            st.write("· 暂无明显技术信号")
        st.markdown(
            f"**评分：** 技术 `{sig['tech_score']}` + 宏观 `{sig['macro_score']}` × "
            f"`{analytics.MACRO_WEIGHT}` → **综合 `{sig['combined']}`**"
        )
        if sig["macro_overridden"]:
            st.success("个股重大突破，已压过宏观逆风，信号锁定在“加仓”。")

st.divider()

# ---------------------------------------------------------------------------
# 大事件日历
# ---------------------------------------------------------------------------
st.subheader("📅 持仓大事件（预计时间）")
if events.empty:
    st.info("暂无事件数据。运行 scripts/fetch_us.py 后会自动生成财报 / 除息日。")
else:
    ev = events.copy()
    ev["date"] = pd.to_datetime(ev["date"])
    today = pd.Timestamp.now().normalize()
    upcoming = ev[ev["date"] >= today].sort_values("date")
    only_holdings = st.checkbox("只看已持仓标的", value=False)
    if only_holdings:
        held = [t for t, h in HOLDINGS.items() if h["weight"] > 0]
        upcoming = upcoming[upcoming["ticker"].isin(held)]
    upcoming = upcoming.assign(
        距今天数=(upcoming["date"] - today).dt.days,
        日期=upcoming["date"].dt.strftime("%Y-%m-%d"),
    )
    show = upcoming.rename(columns={
        "ticker": "代码", "name": "名称", "type": "类型", "detail": "说明"
    })[["日期", "距今天数", "代码", "名称", "类型", "说明"]]
    st.dataframe(show, use_container_width=True, hide_index=True)
