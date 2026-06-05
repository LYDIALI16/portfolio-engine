"""
持仓监控 Dashboard v2 —— 规则引擎驱动（三层闸门 ENV→Trend→Trigger）
- 每只股票输出明确动作（ADD/TRIM/EXIT/REENTRY/HOLD/NO_ACTION/WATCH）+ 可解释理由
- 盘中实时报价（yfinance，带缓存，断网回退 CSV）
- 持仓配置 config/portfolio.csv（含成本价、目标仓位、角色、风险类别）
- 阈值参数 config/rules.yaml 可调
"""
import os
import yaml
import pandas as pd
import streamlit as st

import indicators
import engine

st.set_page_config(page_title="持仓监控引擎", page_icon="📈", layout="wide")

US_DIR = "data/us"
CN_DIR = "data/cn"
MACRO_DIR = "data/macro"
EVENTS_DIR = "data/events"
PORTFOLIO_FILE = "config/portfolio.csv"
RULES_FILE = "config/rules.yaml"
BENCHMARK_KEY = "SOXX"   # 个股相对强弱基准（半导体板块）

ACTION_STYLE = {
    "ADD":      ("🟢 加仓", "#1a7f37"),
    "REENTRY":  ("🟢 接回", "#1a7f37"),
    "HOLD":     ("🔵 持有", "#0969da"),
    "WATCH":    ("🟡 观望", "#9a6700"),
    "TRIM":     ("🟠 减仓", "#bc4c00"),
    "EXIT":     ("🔴 清出", "#cf222e"),
    "NO_ACTION":("⚪ 不动", "#57606a"),
}
TREND_CN = {
    "UP_TREND": "上升", "UP_PULLBACK": "上升回调", "SIDEWAYS": "横盘",
    "DOWN_TRANSITION": "转弱", "DOWN_TREND": "下降",
}


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
@st.cache_data(ttl=600)
def load_rules() -> dict:
    return yaml.safe_load(open(RULES_FILE, encoding="utf-8"))


@st.cache_data(ttl=600)
def load_portfolio() -> list:
    """读取 config/portfolio.csv，返回 holding dict 列表。容错 role 拼写。"""
    df = pd.read_csv(PORTFOLIO_FILE)
    roles = {"CORE", "SATELLITE", "WATCHLIST"}
    out = []
    for _, r in df.iterrows():
        if not bool(r.get("enabled", True)):
            continue
        role = str(r["role"]).strip().upper()
        if role not in roles:  # 容错：ATELLITE -> SATELLITE 等
            role = "SATELLITE" if "ATEL" in role else ("WATCHLIST" if "WATCH" in role else "CORE")
        out.append({
            "market": str(r["market"]).strip().upper(),
            "symbol": str(r["symbol"]).strip(),
            "name": str(r["name"]).strip(),
            "current_weight": float(r["current_weight"]),
            "cost_basis": float(r.get("cost_basis", 0) or 0),
            "target_max_weight": float(r["target_max_weight"]),
            "role": role,
            "risk_class": str(r["risk_class"]).strip().upper(),
            "allow_add": bool(r.get("allow_add", True)),
        })
    return out


@st.cache_data(ttl=3600)
def load_history(market: str, symbol: str) -> pd.DataFrame:
    d = CN_DIR if market == "CN" else US_DIR
    path = f"{d}/{symbol}.csv"
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
            df = pd.read_csv(f"{MACRO_DIR}/{fname}", index_col=0, parse_dates=True)
            out[fname[:-4]] = df["Close"]
    return out


@st.cache_data(ttl=3600)
def load_events() -> pd.DataFrame:
    path = f"{EVENTS_DIR}/events.csv"
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ticker", "name", "type", "date", "detail"])
    return pd.read_csv(path, parse_dates=["date"])


@st.cache_data(ttl=60, show_spinner=False)
def live_quotes(symbols: tuple) -> dict:
    """美股盘中实时报价；失败返回空，回退 CSV。A股不走实时。"""
    try:
        import yfinance as yf
        data = yf.download(tickers=list(symbols), period="2d", interval="1m",
                           group_by="ticker", progress=False, auto_adjust=True)
        out = {}
        for t in symbols:
            try:
                c = data[t]["Close"].dropna()
                if len(c):
                    out[t] = float(c.iloc[-1])
            except Exception:
                pass
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------
try:
    RULES = load_rules()
    HOLDINGS = load_portfolio()
except Exception as e:
    st.error(f"读取配置失败：{e}")
    st.stop()

macro = load_macro()
events = load_events()
benchmark = macro.get(BENCHMARK_KEY)

# 侧边栏
st.sidebar.header("⚙️ 设置")
realtime = st.sidebar.toggle("美股盘中实时报价", value=True)
auto = st.sidebar.toggle("页面自动刷新", value=True)
interval = st.sidebar.select_slider("刷新间隔（秒）", options=[30, 60, 120, 300], value=60)
if auto:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=interval * 1000, key="refresh")
    except Exception:
        st.sidebar.caption("（未安装 streamlit-autorefresh）")

us_symbols = tuple(h["symbol"] for h in HOLDINGS if h["market"] == "US")
quotes = live_quotes(us_symbols) if realtime else {}

# ---------------------------------------------------------------------------
# 跑引擎（全持仓）
# ---------------------------------------------------------------------------
env_info = engine.env_scan(macro, RULES)
results = {}
for h in HOLDINGS:
    df = load_history(h["market"], h["symbol"])
    if df.empty:
        continue
    ind = indicators.compute(df, benchmark=benchmark)
    res = engine.analyze_holding(ind, h, macro, RULES, env_info)
    # 价格 / 盈亏
    csv_close = float(df["Close"].iloc[-1])
    price = quotes.get(h["symbol"], csv_close)
    res["price"] = price
    res["pl_pct"] = ((price / h["cost_basis"] - 1) * 100) if h["cost_basis"] > 0 else None
    res["name"] = h["name"]
    res["ind"] = ind
    results[h["symbol"]] = res

data_source = "🟢 盘中实时" if quotes else "🕒 每日收盘"
st.title("📈 持仓监控引擎")
st.caption(f"数据：{data_source}　|　{pd.Timestamp.now():%Y-%m-%d %H:%M:%S}　|　规则引擎 v1")

# ---------------------------------------------------------------------------
# 顶部：ENV 宏观闸门
# ---------------------------------------------------------------------------
env_label = {"SUPPORTIVE": "🟢 顺风", "NEUTRAL": "🟡 中性", "PRESSURE": "🔴 承压"}[env_info["env"]]
e1, e2 = st.columns([1, 3])
e1.metric("宏观环境 ENV", env_label, f"加仓倍率 ×{env_info['macro_factor']}")
with e2:
    st.write("**威胁扫描：** " + "　·　".join(env_info["reasons"]))
    st.caption("ENV 只做闸门：决定能不能加、加仓打几折。不参与选股、不决定方向。"
               + ("　⚠️ 承压：禁止高波动成长股加仓。" if env_info["env"] == "PRESSURE" else ""))

st.divider()

# ---------------------------------------------------------------------------
# 今日必须动作（非 HOLD/NO_ACTION 的）
# ---------------------------------------------------------------------------
st.subheader("📋 今日操作清单")
actionable = [r for r in results.values() if r["action"] in ("ADD", "REENTRY", "TRIM", "EXIT")]
order = {"EXIT": 0, "TRIM": 1, "REENTRY": 2, "ADD": 3}
actionable.sort(key=lambda r: order.get(r["action"], 9))
if actionable:
    for r in actionable:
        label, color = ACTION_STYLE[r["action"]]
        sign = "+" if r["trade_pct"] > 0 else ""
        st.markdown(
            f"<span style='color:{color};font-weight:700'>{label}</span> "
            f"**{r['symbol']} {r['name']}** "
            f"（{r['size_hint']} {sign}{r['trade_pct']:.1f}%，当前{r['current_weight']:.0f}%→目标{r['target_pct']:.0f}%）"
            f"　·　{r['rationale'][0] if r['rationale'] else ''}",
            unsafe_allow_html=True)
else:
    st.success("今日无必须操作（全部 HOLD / 观望）。")
st.caption("⚠️ 这是基于价格/趋势/宏观的**机械建议**，不含新闻与基本面，仅供参考，最终决策在你。")

st.divider()

# ---------------------------------------------------------------------------
# 持仓总览表
# ---------------------------------------------------------------------------
st.subheader("持仓总览")
rows = []
for h in HOLDINGS:
    r = results.get(h["symbol"])
    if not r:
        rows.append({"市场": h["market"], "代码": h["symbol"], "名称": h["name"],
                     "角色": h["role"], "建议": "—(无数据)", "当前%": h["current_weight"]})
        continue
    label = ACTION_STYLE[r["action"]][0]
    sign = "+" if r["trade_pct"] > 0 else ""
    rows.append({
        "市场": h["market"], "代码": h["symbol"], "名称": h["name"], "角色": h["role"],
        "建议": label,
        "幅度": f"{sign}{r['trade_pct']:.1f}%" if r["action"] in ("ADD","REENTRY","TRIM","EXIT") else "",
        "当前%": f"{h['current_weight']:.1f}",
        "目标%": f"{r['target_pct']:.0f}",
        "最新价": f"{r['price']:.2f}",
        "盈亏": f"{r['pl_pct']:+.1f}%" if r["pl_pct"] is not None else "—",
        "趋势": TREND_CN.get(r["trend_state"], r["trend_state"]),
        "风险": r["risk_flag"],
        "拥挤": r["crowding"],
    })
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
st.caption("建议来自三层引擎：ENV闸门 → 趋势状态 → 触发器。点下方查看每只的详细理由。")

st.divider()

# ---------------------------------------------------------------------------
# 个股明细（含 rationale）
# ---------------------------------------------------------------------------
st.subheader("个股明细")
syms = [h["symbol"] for h in HOLDINGS if h["symbol"] in results]
if syms:
    sel = st.selectbox("选择查看", options=syms,
                       format_func=lambda s: f"{s} - {results[s]['name']}")
    r = results[sel]
    ind = r["ind"]
    label, color = ACTION_STYLE[r["action"]]

    c = st.columns(5)
    c[0].metric("建议", label, f"{r['size_hint']} {r['trade_pct']:+.1f}%" if r["action"] in ("ADD","REENTRY","TRIM","EXIT") else "")
    c[1].metric("最新价", f"{r['price']:.2f}", f"{r['pl_pct']:+.1f}%" if r["pl_pct"] is not None else None)
    c[2].metric("趋势", TREND_CN.get(r["trend_state"], r["trend_state"]))
    c[3].metric("风险", r["risk_flag"])
    c[4].metric("止损价", f"{r['stop_price']:.2f}" if r.get("stop_price") and not pd.isna(r['stop_price']) else "—")

    st.markdown(f"**为什么是「{label}」：**")
    for reason in r["rationale"]:
        st.write("·", reason)
    if not r["rationale"]:
        st.write("· 无特别触发")

    # 状态展开
    with st.expander("展开：趋势 / 风险 / 指标细节"):
        st.write("**趋势判定：**", "；".join(r["trend_reasons"]))
        st.write("**风险判定：**", "；".join(r["risk_reasons"]) or "GREEN，无风险触发")
        st.write(f"**关键指标：** 价 {ind['close']:.2f}｜MA50 {ind['ma50']:.2f}｜MA200 {ind['ma200']:.2f}"
                 f"｜RSI {ind['rsi']:.0f}｜ATR {ind['atr']:.2f}｜60日回撤 {ind['dd60']*100:.0f}%"
                 f"｜ATR%分位 {ind['atr_pct_pctile']:.0%}")

    df = load_history(next(h["market"] for h in HOLDINGS if h["symbol"] == sel), sel)
    if not df.empty:
        st.line_chart(df["Close"])

st.divider()

# ---------------------------------------------------------------------------
# 大事件日历
# ---------------------------------------------------------------------------
st.subheader("📅 持仓大事件（预计时间）")
if events.empty:
    st.info("暂无事件数据。")
else:
    ev = events.copy()
    ev["date"] = pd.to_datetime(ev["date"])
    today = pd.Timestamp.now().normalize()
    up = ev[ev["date"] >= today].sort_values("date")
    up = up.assign(距今天数=(up["date"] - today).dt.days,
                   日期=up["date"].dt.strftime("%Y-%m-%d"))
    show = up.rename(columns={"ticker": "代码", "name": "名称", "type": "类型", "detail": "说明"})
    cols = [c for c in ["日期", "距今天数", "代码", "名称", "类型", "说明"] if c in show.columns]
    st.dataframe(show[cols], use_container_width=True, hide_index=True)
