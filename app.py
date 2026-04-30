"""
持仓监控Dashboard
只读data/us/下的CSV文件，不联网
"""
import streamlit as st
import pandas as pd
import os

st.set_page_config(
    page_title="持仓监控",
    page_icon="📈",
    layout="wide",
)

# 持仓信息（暂时硬编码，下个里程碑抽到config.yaml）
HOLDINGS = {
    "NVDA": {"name": "英伟达", "weight": 0.32, "type": "持仓"},
    "NBIS": {"name": "Nebius", "weight": 0.06, "type": "持仓"},
    "IBKR": {"name": "盈透证券", "weight": 0.02, "type": "持仓"},
    "V": {"name": "Visa", "weight": 0.02, "type": "持仓"},
    "NEE": {"name": "NextEra Energy", "weight": 0.05, "type": "持仓"},
    "GEV": {"name": "GE Vernova", "weight": 0.0, "type": "候选"},
    "AVGO": {"name": "Broadcom", "weight": 0.0, "type": "候选"},
    "DUOL": {"name": "多邻国", "weight": 0.0, "type": "候选"},
}

st.title("📈 持仓监控引擎")
st.caption("数据每日自动更新（美股收盘后）")

# 顶部摘要
col1, col2, col3 = st.columns(3)
total_weight = sum(h["weight"] for h in HOLDINGS.values())
col1.metric("已用美股仓位", f"{total_weight*100:.0f}%")
col2.metric("现金仓位", f"{(1-total_weight)*100:.0f}%")
col3.metric("跟踪股票数", len(HOLDINGS))

st.divider()

# 持仓表格
data_rows = []
for ticker, info in HOLDINGS.items():
    csv_path = f"data/us/{ticker}.csv"
    if not os.path.exists(csv_path):
        continue
    
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    latest_close = df["Close"].iloc[-1]
    prev_close = df["Close"].iloc[-2] if len(df) > 1 else latest_close
    change_pct = (latest_close - prev_close) / prev_close * 100
    latest_date = df.index[-1].strftime("%Y-%m-%d")
    
    data_rows.append({
        "类型": info["type"],
        "代码": ticker,
        "名称": info["name"],
        "仓位": f"{info['weight']*100:.0f}%",
        "最新价": f"{latest_close:.2f}",
        "日涨跌": f"{change_pct:+.2f}%",
        "数据日期": latest_date,
    })

st.subheader("持仓总览")
st.dataframe(pd.DataFrame(data_rows), use_container_width=True, hide_index=True)

st.divider()

# 价格走势图
st.subheader("价格走势")
selected_ticker = st.selectbox(
    "选择查看",
    options=list(HOLDINGS.keys()),
    format_func=lambda t: f"{t} - {HOLDINGS[t]['name']}",
)

csv_path = f"data/us/{selected_ticker}.csv"
if os.path.exists(csv_path):
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    st.line_chart(df["Close"])
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("最新价", f"${df['Close'].iloc[-1]:.2f}")
    col2.metric("52周高", f"${df['Close'].iloc[-252:].max():.2f}")
    col3.metric("52周低", f"${df['Close'].iloc[-252:].min():.2f}")
    col4.metric("数据点数", f"{len(df)}")
