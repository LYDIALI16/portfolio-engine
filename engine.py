"""
规则引擎 v1.0 —— 三层闸门式（ENV → Trend → Trigger）
纯函数，输入指标快照 + 配置，输出可解释的动作建议。不联网、不下单。

设计文档：docs/implementation-design-v1.md
"""
import numpy as np


# ---------------------------------------------------------------------------
# Layer 1：ENV 宏观威胁扫描
# ---------------------------------------------------------------------------
def env_scan(macro: dict, rules: dict) -> dict:
    """
    macro: {"TNX": series, "VIX": series, "DXY": series, "SOXX": series, ...}
    返回 {env, macro_factor, reasons}。env ∈ SUPPORTIVE/NEUTRAL/PRESSURE。
    只做威胁扫描，不选股、不定方向。
    """
    cfg = rules["env"]
    reasons = []

    def chg_bp_10d(s):  # 收益率类，单位百分点→bp
        s = s.dropna()
        if len(s) <= 10:
            return np.nan
        return float(s.iloc[-1] - s.iloc[-11]) * 100

    def chg_pct_10d(s):
        s = s.dropna()
        if len(s) <= 10:
            return np.nan
        return float(s.iloc[-1] / s.iloc[-11] - 1) * 100

    tnx, vix, dxy, soxx = (macro.get(k) for k in ("TNX", "VIX", "DXY", "SOXX"))

    vix_now = float(vix.dropna().iloc[-1]) if vix is not None and len(vix.dropna()) else np.nan
    vix_ma = np.nan
    if vix is not None and len(vix.dropna()) > cfg["vix_ma_days"]:
        vix_ma = float(vix.dropna().rolling(cfg["vix_ma_days"]).mean().iloc[-1])
    vix_high = (not np.isnan(vix_now) and not np.isnan(vix_ma) and vix_now > vix_ma)

    tnx_up = tnx is not None and not np.isnan(chg_bp_10d(tnx)) and chg_bp_10d(tnx) >= cfg["us10y_up_bp_10d"]
    dxy_up = dxy is not None and not np.isnan(chg_pct_10d(dxy)) and chg_pct_10d(dxy) >= cfg["dxy_up_pct_10d"]

    pressure = False
    if not np.isnan(vix_now) and vix_now >= cfg["vix_panic"]:
        pressure = True
        reasons.append(f"VIX={vix_now:.0f} 恐慌(≥{cfg['vix_panic']})")
    if tnx_up and vix_high:
        pressure = True
        reasons.append(f"10年期10日上行{chg_bp_10d(tnx):.0f}bp 且 VIX高于均线（利率冲击）")
    if dxy_up and vix_high:
        pressure = True
        reasons.append(f"美元10日涨{chg_pct_10d(dxy):.1f}% 且 VIX高于均线（美元压力）")

    supportive = False
    if not pressure:
        tnx_down = tnx is not None and not np.isnan(chg_bp_10d(tnx)) and chg_bp_10d(tnx) <= -cfg["us10y_up_bp_10d"]
        soxx_strong = soxx is not None and not np.isnan(chg_pct_10d(soxx)) and chg_pct_10d(soxx) >= 3
        if tnx_down and not vix_high:
            supportive = True
            reasons.append("利率下行 且 VIX平稳")
        if soxx_strong:
            supportive = True
            reasons.append(f"半导体板块10日强势(+{chg_pct_10d(soxx):.1f}%)")

    env = "PRESSURE" if pressure else ("SUPPORTIVE" if supportive else "NEUTRAL")
    if not reasons:
        reasons.append("宏观中性，无明显威胁")
    return {"env": env, "macro_factor": rules["env"]["macro_factor"][env], "reasons": reasons}


# ---------------------------------------------------------------------------
# Layer 2：Trend 状态机（5 态）
# ---------------------------------------------------------------------------
def trend_state(ind: dict, rc: dict, market: str = "US") -> dict:
    """返回 {state, reasons}。state ∈ UP_TREND/UP_PULLBACK/SIDEWAYS/DOWN_TRANSITION/DOWN_TREND。"""
    close, ma20, ma50, ma200 = ind["close"], ind["ma20"], ind["ma50"], ind["ma200"]
    slope, atr = ind["ma200_slope"], ind["atr"]
    reasons = []

    if np.isnan(ma200) or np.isnan(ma50):
        return {"state": "SIDEWAYS", "reasons": ["数据不足，均线未成形"]}

    up_struct = close > ma200 and ma50 >= ma200 and (np.isnan(slope) or slope >= 0)
    down_struct = close < ma200 and ma50 <= ma200

    if up_struct:
        # 是否回调到均线附近 → UP_PULLBACK
        ref_ma = ma50 if rc.get("pullback_ma") == "MA50" else ma20
        pull_line = ind["high20"] - rc["pullback_atr_mult"] * atr
        near_ma = not np.isnan(ref_ma) and close <= ref_ma * 1.03
        if close <= pull_line or near_ma:
            reasons.append(f"上升趋势内回调（接近{rc.get('pullback_ma')}/-{rc['pullback_atr_mult']}×ATR）")
            return {"state": "UP_PULLBACK", "reasons": reasons}
        reasons.append("close>MA200 且 MA50≥MA200（多头结构）")
        return {"state": "UP_TREND", "reasons": reasons}

    if down_struct:
        reasons.append("close<MA200 且 MA50≤MA200（空头结构）")
        return {"state": "DOWN_TREND", "reasons": reasons}

    if close < ma50:
        reasons.append("跌破MA50，MA200未确认破（过渡）")
        return {"state": "DOWN_TRANSITION", "reasons": reasons}

    reasons.append("均线纠缠，方向不明")
    return {"state": "SIDEWAYS", "reasons": reasons}


# ---------------------------------------------------------------------------
# 风险 & 拥挤度
# ---------------------------------------------------------------------------
def risk_flag(ind: dict, rc: dict, env: str, risk_class: str) -> dict:
    reasons = []
    flag = "GREEN"
    dd, atr_p = ind["dd60"], ind["atr_pct_pctile"]
    vr = ind["vol_ratio"]
    red = amber = False

    if not np.isnan(atr_p) and atr_p >= rc["atr_pct_red"]:
        red = True; reasons.append(f"波动处历史高位(ATR%分位{atr_p:.0%})")
    if not np.isnan(dd) and dd >= rc["dd_red"]:
        red = True; reasons.append(f"60日回撤{dd:.0%}(≥{rc['dd_red']:.0%})")
    if env == "PRESSURE" and risk_class == "HIGH":
        red = True; reasons.append("宏观承压且高波动成长")

    if not red:
        if not np.isnan(atr_p) and atr_p >= rc["atr_pct_amber"]:
            amber = True; reasons.append(f"波动偏高(ATR%分位{atr_p:.0%})")
        if not np.isnan(dd) and dd >= rc["dd_amber"]:
            amber = True; reasons.append(f"60日回撤{dd:.0%}")
        if not np.isnan(vr) and vr >= 2 and ind["close"] < ind["open"]:
            amber = True; reasons.append(f"放量阴线(量比{vr:.1f})")

    flag = "RED" if red else ("AMBER" if amber else "GREEN")
    return {"flag": flag, "reasons": reasons}


def crowding_level(ind: dict, rules: dict) -> str:
    cfg = rules["crowding"]
    rp, vr = ind["ret20_pctile"], ind["vol_ratio"]
    if not np.isnan(rp) and not np.isnan(vr) and rp >= cfg["return_percentile_high"] and vr >= cfg["volume_ratio_high"]:
        return "HIGH"
    if (not np.isnan(rp) and rp >= 0.60) or (not np.isnan(vr) and vr >= cfg["volume_ratio_medium"]):
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# 仓位计算（ATR% 风险预算）
# ---------------------------------------------------------------------------
def position_sizing(ind: dict, holding: dict, rc: dict, macro_factor: float, rules: dict) -> dict:
    """
    目标仓位 = target_max（由你设定，ATR 不再决定目标）。
    ATR 仅用于：止损价 stop_price、以及加仓幅度的波动调节（见 decide_action）。
    """
    g = rules["global"]
    close, atr, ma200 = ind["close"], ind["atr"], ind["ma200"]
    target = float(holding["target_max_weight"])

    if np.isnan(atr) or not close:
        return {"target_pct": target, "stop_price": np.nan, "stop_distance_pct": np.nan}

    struct_stop = (ma200 - g["structure_buffer_atr_mult"] * atr) if not np.isnan(ma200) else -np.inf
    atr_stop = close - g["initial_stop_atr_mult"] * atr
    stop_price = max(struct_stop, atr_stop)
    stop_distance_pct = (close - stop_price) / close
    if stop_distance_pct <= 0:
        stop_distance_pct = g["initial_stop_atr_mult"] * atr / close  # 兜底

    return {"target_pct": round(target, 1), "stop_price": round(stop_price, 2),
            "stop_distance_pct": round(stop_distance_pct, 3)}


# ---------------------------------------------------------------------------
# Layer 3：Trigger 动作引擎（按优先级输出唯一动作）
# ---------------------------------------------------------------------------
def decide_action(ind, holding, trend, risk, crowding, env_info, rc, rules) -> dict:
    """
    优先级：EXIT > TRIM > REENTRY > ADD > WATCH > HOLD > NO_ACTION
    返回 {action, size_hint, trade_pct, target_pct, rationale[]}
    """
    g = rules["global"]
    state = trend["state"]
    flag = risk["flag"]
    env = env_info["env"]
    cur = holding["current_weight"]
    rationale = []

    sizing = position_sizing(ind, holding, rc, env_info["macro_factor"], rules)
    target = sizing["target_pct"]

    close, ma200, atr = ind["close"], ind["ma200"], ind["atr"]
    chandelier = (ind["high22"] - g["chandelier_atr_mult"] * atr) if not np.isnan(atr) else np.nan

    def out(action, trade_pct=0.0, extra=None):
        r = list(rationale)
        if extra:
            r.append(extra)
        # 截断到 3 条
        size = abs(trade_pct)
        hint = "—"
        if action in ("ADD", "REENTRY", "TRIM"):
            hint = "小幅" if size < 5 else ("中幅" if size < 12 else "大幅")
        return {"action": action, "size_hint": hint, "trade_pct": round(trade_pct, 1),
                "target_pct": target, "stop_price": sizing["stop_price"], "rationale": r[:3]}

    # ---- EXIT ----
    exit_reasons = []
    if state == "DOWN_TREND":
        exit_reasons.append("确认空头结构(close<MA200, MA50≤MA200)")
    if not np.isnan(chandelier) and close < chandelier:
        exit_reasons.append(f"跌破吊灯止损({ind['high22']:.1f}-{g['chandelier_atr_mult']}×ATR)")
    # 美股需≥2条破坏确认；A股 1 条即可（收盘版）
    need = 2 if holding["market"] == "US" else 1
    if cur > 0 and len(exit_reasons) >= need:
        rationale += exit_reasons
        return out("EXIT", trade_pct=-cur)

    # ---- TRIM ----
    if cur > 0 and state in ("UP_TREND", "UP_PULLBACK", "DOWN_TRANSITION"):
        trim = False
        if flag == "AMBER":
            trim = True; rationale.append(f"风险升至AMBER：{'/'.join(risk['reasons'][:2])}")
        if crowding == "HIGH":
            trim = True; rationale.append("拥挤度HIGH（涨多+放量，防回吐）")
        if env == "PRESSURE" and holding["risk_class"] == "HIGH":
            trim = True; rationale.append("宏观承压且高波动成长")
        if not np.isnan(ind["vol_ratio"]) and ind["vol_ratio"] >= 2 and close < ind["open"]:
            trim = True; rationale.append(f"放量长阴(量比{ind['vol_ratio']:.1f})")
        if trim:
            trim_pct = -min(cur * rc["trim_step"], cur) if "trim_step" in rc else -cur * 0.3
            if abs(trim_pct) >= g["min_trade_pct"]:
                return out("TRIM", trade_pct=trim_pct)

    # ---- REENTRY ----（0 仓、非 WATCHLIST 冻结、重回上升趋势，分级接回）
    # reentry_ladder = target_max 的百分比阶梯 [15,25,35]，取第一档接回
    if cur == 0 and state in ("UP_TREND", "UP_PULLBACK") \
            and flag != "RED" and env != "PRESSURE" and holding["target_max_weight"] > 0:
        first_step = rules["reentry_ladder"][0] / 100 * holding["target_max_weight"]
        if first_step >= g["min_trade_pct"]:
            rationale.append(f"由0仓重回上升趋势，接回第一档(目标{target:.0f}%的{rules['reentry_ladder'][0]}%)")
            rationale += trend["reasons"][:1]
            return out("REENTRY", trade_pct=round(first_step, 1))

    # ---- ADD ----（目标仓位=target_max，按缺口×gap_fill×macro_factor 渐进加）
    if state == "UP_PULLBACK" and holding["allow_add"] and cur < holding["target_max_weight"]:
        blocked = []
        if env == "PRESSURE" and holding["risk_class"] == "HIGH":
            blocked.append("宏观承压禁高波动加仓")
        if not np.isnan(ind["rsi"]) and ind["rsi"] >= rc["overheat_rsi"]:
            blocked.append(f"RSI={ind['rsi']:.0f}过热")
        if not np.isnan(ind["atr_pct_pctile"]) and ind["atr_pct_pctile"] >= 0.80:
            blocked.append("波动处高位")
        if crowding == "HIGH":
            blocked.append("拥挤度HIGH")
        if flag == "RED":
            blocked.append("风险RED")
        if not blocked:
            gap = holding["target_max_weight"] - cur
            add_pct = gap * rc["gap_fill"] * env_info["macro_factor"]
            if add_pct >= g["min_trade_pct"]:
                rationale += trend["reasons"][:1]
                rationale.append(f"风险{flag}，ENV={env}，补缺口{gap:.0f}%的{rc['gap_fill']:.0%}")
                return out("ADD", trade_pct=add_pct)
        else:
            rationale.append("回调但禁止加仓：" + "；".join(blocked[:2]))
            return out("WATCH")

    # ---- WATCH ----
    if env == "PRESSURE" and cur > 0:
        rationale.append(f"宏观承压(ENV=PRESSURE)：{env_info['reasons'][0]}")
        return out("WATCH")
    if state in ("SIDEWAYS", "DOWN_TRANSITION") and cur > 0:
        rationale += trend["reasons"][:1]
        return out("WATCH")

    # ---- HOLD / NO_ACTION ----
    if cur > 0:
        rationale.append(f"{state}，无触发，继续持有")
        return out("HOLD")
    rationale.append("0仓且无入场信号")
    return out("NO_ACTION")


# ---------------------------------------------------------------------------
# 顶层编排
# ---------------------------------------------------------------------------
def analyze_holding(ind, holding, macro, rules, env_info=None) -> dict:
    """单只股票完整分析。env_info 可预先算好复用（全局一次）。"""
    if env_info is None:
        env_info = env_scan(macro, rules)
    rc = rules["risk_class"][holding["risk_class"]]
    # 把 trim_step/add_step 并入 rc（来自 portfolio 或默认）
    rc = dict(rc)
    rc.setdefault("trim_step", {"HIGH": 0.30, "MEDIUM": 0.30, "LOW": 0.25}[holding["risk_class"]])

    trend = trend_state(ind, rc, holding["market"])
    risk = risk_flag(ind, rc, env_info["env"], holding["risk_class"])
    crowding = crowding_level(ind, rules)
    decision = decide_action(ind, holding, trend, risk, crowding, env_info, rc, rules)

    return {
        "symbol": holding["symbol"], "market": holding["market"],
        "role": holding["role"], "risk_class": holding["risk_class"],
        "env": env_info["env"], "macro_factor": env_info["macro_factor"],
        "trend_state": trend["state"], "trend_reasons": trend["reasons"],
        "risk_flag": risk["flag"], "risk_reasons": risk["reasons"],
        "crowding": crowding,
        "current_weight": holding["current_weight"],
        "target_pct": decision["target_pct"],
        "action": decision["action"], "size_hint": decision["size_hint"],
        "trade_pct": decision["trade_pct"], "stop_price": decision.get("stop_price"),
        "rationale": decision["rationale"],
    }
