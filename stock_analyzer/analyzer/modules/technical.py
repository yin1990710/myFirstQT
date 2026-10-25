"""模块 2：技术面分析。

覆盖：趋势结构（均线排列/MA30 方向）、动量（区间涨幅/相对行业超额）、
摆荡指标（MACD/RSI/KDJ/BOLL）、量能（5/20 日均量比、量比、成交额）、
形态位置（W23 波浪+均线+量能模型、距 250 日高低点、支撑压力）。
"""
from __future__ import annotations

import pandas as pd

from ..indicators import add_indicators, detect_w23, relative_strength, support_resistance
from ..utils import band_score, fmt_pct, scale_score
from .base import ModuleResult

MAX_SCORE = 30.0


def analyze(code: str, df: pd.DataFrame, bench: pd.DataFrame, quote: dict) -> ModuleResult:
    res = ModuleResult(key="technical", name="技术面", max_score=MAX_SCORE)
    if df.empty or len(df) < 60:
        res.available = False
        res.headline = "日线数据不足，技术面维度不参与加权"
        return res

    d = add_indicators(df)
    last = d.iloc[-1]
    close = float(last["close"])
    res.details["df"] = d

    # ---------------- 趋势结构（8 分）
    ma = {w: float(last[f"ma{w}"]) if pd.notna(last[f"ma{w}"]) else None for w in (5, 10, 20, 30, 60)}
    bull_stack = all(ma[a] and ma[b] and ma[a] > ma[b] for a, b in [(5, 10), (10, 20), (20, 30)])
    above30 = ma[30] is not None and close > ma[30]
    above60 = ma[60] is not None and close > ma[60]
    slope30 = None
    if len(d) > 21 and pd.notna(d["ma30"].iloc[-21]) and d["ma30"].iloc[-21]:
        slope30 = (ma[30] / float(d["ma30"].iloc[-21]) - 1) * 100

    s_trend = 3.0 if bull_stack else (1.8 if (ma[5] and ma[10] and ma[5] > ma[10]) else 0.6)
    s_trend += 2.0 if above30 else 0.4
    s_trend += band_score(slope30, [(3, 2.0), (0, 1.4), (-3, 0.6)], default=0.2)
    s_trend += 1.0 if above60 else 0.3

    # ---------------- 动量（6 分）
    ret20 = float(last["ret20"]) if pd.notna(last["ret20"]) else None
    ret5 = float(last["ret5"]) if pd.notna(last["ret5"]) else None
    ret60 = float(last["ret60"]) if pd.notna(last["ret60"]) else None
    rs20 = relative_strength(d, bench, 20)

    s_mom = band_score(ret20, [(20, 3.0), (8, 2.5), (0, 1.8), (-8, 1.0), (-20, 0.4)], default=0.1)
    s_mom += band_score(rs20, [(15, 3.0), (5, 2.4), (0, 1.7), (-8, 0.9), (-20, 0.3)], default=0.6)

    # ---------------- 摆荡指标（6 分）
    dif, dea, macd = last["dif"], last["dea"], last["macd"]
    rsi = float(last["rsi14"]) if pd.notna(last["rsi14"]) else None
    k, dd = last["kdj_k"], last["kdj_d"]
    boll_pos = float(last["boll_pos"]) if pd.notna(last["boll_pos"]) else None

    s_ind = 0.0
    if pd.notna(dif) and pd.notna(dea):
        if dif > dea and dif > 0:
            s_ind += 2.0
        elif dif > dea:
            s_ind += 1.4
        elif dif > 0:
            s_ind += 0.8
        else:
            s_ind += 0.2
    else:
        s_ind += 0.8

    if rsi is None:
        s_ind += 1.0
    elif 45 <= rsi <= 70:
        s_ind += 2.0
    elif 35 <= rsi < 45:
        s_ind += 1.5
    elif 70 < rsi <= 80:
        s_ind += 1.2
    elif rsi > 80:
        s_ind += 0.5
    else:
        s_ind += 0.6

    if pd.notna(k) and pd.notna(dd):
        s_ind += 2.0 if (k > dd and k < 80) else (1.2 if k > dd else 0.6)
    else:
        s_ind += 1.0

    # ---------------- 量能（5 分）
    vr = float(last["vol_ratio_5_20"]) if pd.notna(last["vol_ratio_5_20"]) else None
    vol_ratio = quote.get("vol_ratio")
    turnover = quote.get("turnover")
    s_vol = band_score(vr, [(1.6, 2.0), (1.2, 1.7), (0.9, 1.3), (0.7, 0.9)], default=0.5)
    # 放量上涨加分 / 放量下跌减分
    if vr and ret5 is not None:
        if vr > 1.3 and ret5 > 0:
            s_vol += 1.5
        elif vr > 1.3 and ret5 < -3:
            s_vol += 0.2
        else:
            s_vol += 1.0
    else:
        s_vol += 0.8
    s_vol += band_score(vol_ratio, [(2.0, 1.5), (1.2, 1.2), (0.8, 0.9)], default=0.6)

    # ---------------- 形态位置（5 分）
    w23 = detect_w23(d)
    res.details["w23"] = w23
    s_shape = w23.score
    dist_high = float(last["dist_high"]) if pd.notna(last["dist_high"]) else None
    dist_low = float(last["dist_low"]) if pd.notna(last["dist_low"]) else None
    if dist_high is not None and dist_high > -8:
        s_shape -= 0.4        # 贴近年内高点，追高风险

    res.score = round(max(0.0, min(MAX_SCORE, s_trend + s_mom + s_ind + s_vol + s_shape)), 2)

    # ---------------- 展示
    res.add("最新价", f"{close:.2f}", note=f"涨跌 {fmt_pct(quote.get('change_pct'), sign=True)}")
    res.add("均线结构",
            "多头排列" if bull_stack else ("短期走强" if (ma[5] and ma[10] and ma[5] > ma[10]) else "空头/纠缠"),
            "good" if bull_stack else ("bad" if not above30 else "neutral"))
    res.add("MA5/MA20/MA30/MA60",
            " / ".join(f"{ma[w]:.2f}" if ma[w] else "—" for w in (5, 20, 30, 60)))
    res.add("MA30 斜率（20日）", fmt_pct(slope30, sign=True),
            "good" if (slope30 or 0) > 0 else "bad")
    res.add("近 5/20/60 日涨幅",
            f"{fmt_pct(ret5, sign=True)} / {fmt_pct(ret20, sign=True)} / {fmt_pct(ret60, sign=True)}")
    res.add("相对行业 20 日超额", fmt_pct(rs20, sign=True),
            "good" if (rs20 or 0) > 0 else "bad", "个股 vs 板块指数")
    res.add("MACD", f"DIF {dif:.2f} / DEA {dea:.2f}" if pd.notna(dif) else "—",
            "good" if (pd.notna(dif) and dif > dea) else "bad",
            f"柱 {macd:.2f}" if pd.notna(macd) else "")
    res.add("RSI(14)", f"{rsi:.1f}" if rsi else "—",
            "bad" if (rsi or 50) > 80 else ("good" if 45 <= (rsi or 0) <= 70 else "neutral"))
    res.add("KDJ", f"K {k:.1f} / D {dd:.1f}" if pd.notna(k) else "—",
            "good" if (pd.notna(k) and k > dd) else "bad")
    res.add("BOLL 位置", f"{boll_pos:.0f}%（0=下轨 100=上轨）" if boll_pos else "—",
            "bad" if (boll_pos or 50) > 95 else "neutral")
    res.add("5日/20日均量比", f"{vr:.2f}" if vr else "—",
            "good" if (vr or 1) > 1.2 else ("bad" if (vr or 1) < 0.7 else "neutral"))
    res.add("量比 / 换手率",
            f"{vol_ratio:.2f} / {fmt_pct(turnover)}" if vol_ratio else fmt_pct(turnover))
    res.add("振幅(ATR%)", fmt_pct(float(last["atr_pct"])) if pd.notna(last["atr_pct"]) else "—")
    res.add("距 250 日收盘高/低点",
            f"{fmt_pct(dist_high, sign=True)} / {fmt_pct(dist_low, sign=True)}")
    res.add("W23 形态", w23.stage, "good" if w23.stage_code in ("W2_END", "W3_START") else "neutral",
            f"置信度 {w23.confidence:.0f}")

    sr = support_resistance(d)
    if sr:
        res.add("支撑位", " / ".join(f"{x:.2f}" for x in sr.get("support", [])[:2]))
        res.add("压力位", " / ".join(f"{x:.2f}" for x in sr.get("resistance", [])[:2]))

    # ---------------- 结论
    if bull_stack and above30 and (slope30 or 0) > 0:
        res.sig("均线多头排列且 MA30 上行，中期趋势健康", "bullish", 3)
    elif not above30 and (slope30 or 0) < 0:
        res.sig("股价位于 MA30 下方且 MA30 下行，趋势仍处弱势", "bearish", 3)
    if w23.stage_code in ("W2_END", "W3_START"):
        res.sig(f"W23 模型判定为「{w23.stage}」：{'；'.join(w23.notes[:2])}", "bullish", 3)
    if w23.stage_code == "LATE":
        res.sig("股价偏离 MA30 过大，处于高位风险区，回撤概率上升", "bearish", 2)
    if rsi and rsi > 80:
        res.sig(f"RSI {rsi:.0f} 处于超买区，短线有回调压力", "bearish", 1)
    if rsi and rsi < 30:
        res.sig(f"RSI {rsi:.0f} 处于超卖区，存在反弹可能", "bullish", 1)
    if rs20 is not None and rs20 > 8:
        res.sig(f"近 20 日跑赢行业 {rs20:.1f}pct，为板块内强势股", "bullish", 2)
    if rs20 is not None and rs20 < -8:
        res.sig(f"近 20 日跑输行业 {abs(rs20):.1f}pct，资金关注度弱于板块", "bearish", 2)
    if vr and vr < 0.7:
        res.sig(f"5/20 日均量比仅 {vr:.2f}，量能持续萎缩", "neutral", 1)

    # 近 10 日走势表
    rows = []
    for _, r in d.tail(10).iloc[::-1].iterrows():
        rows.append([
            r["date"].strftime("%Y-%m-%d"),
            f"{r['close']:.2f}",
            f"{r['change_pct']:.2f}%" if "change_pct" in r and pd.notna(r.get("change_pct")) else "—",
            f"{r['volume'] / 1e4:,.0f} 万股",
            f"{r['turnover']:.2f}%" if pd.notna(r.get("turnover")) else "—",
        ])
    res.table("近 10 个交易日", ["日期", "收盘", "涨跌", "成交量", "换手"], rows)

    res.headline = (f"收盘 {close:.2f}，均线{'多头排列' if bull_stack else '未成多头'}，"
                    f"20日涨幅 {fmt_pct(ret20, sign=True)}，相对行业 {fmt_pct(rs20, sign=True)}，"
                    f"形态：{w23.stage}")
    return res
