"""模块 3：行业板块资金流入情况。

数据基于同花顺行业资金流（90 个行业即时净流入）+ 板块指数历史（5/20 日强度）
+ 全市场个股资金流（用于计算个股在同行业内的资金流排名）。

说明：东财 push2 资金流接口在部分网络环境下不可用，此处统一走同花顺口径，
      单位为「亿元」，为当日主力资金净额（含大单口径差异，仅作相对比较）。
"""
from __future__ import annotations

import pandas as pd

from .. import datasource as ds
from ..utils import band_score, fmt_pct, scale_score
from .base import ModuleResult

MAX_SCORE = 15.0


def analyze(code: str, industry: str, constituents: pd.DataFrame,
            df: pd.DataFrame | None = None, boards: dict | None = None) -> ModuleResult:
    res = ModuleResult(key="sector", name="行业资金", max_score=MAX_SCORE)
    flow = ds.industry_fund_flow()
    if flow.empty:
        res.available = False
        res.headline = "行业资金流数据获取失败，该维度不参与加权"
        return res

    res.details["industry"] = industry
    row = flow[flow["行业"] == industry]
    if row.empty:
        # 名称不完全一致时做包含匹配
        cand = flow[flow["行业"].str.contains(industry[:2], na=False)]
        row = cand.head() if not cand.empty else flow.head(0)

    n_ind = len(flow)
    if row.empty:
        res.add("行业板块", f"{industry}（未在资金流榜匹配到同名板块）", "warn")
        res.score = 0.5 * MAX_SCORE
        res.headline = f"{industry} 板块资金数据缺失，按中性处理"
        return res

    r = row.iloc[0]
    net = float(r["净额"]) if pd.notna(r["净额"]) else 0.0
    inflow = float(r["流入资金"]) if pd.notna(r["流入资金"]) else None
    outflow = float(r["流出资金"]) if pd.notna(r["流出资金"]) else None
    ind_chg = float(r["行业-涨跌幅"]) if pd.notna(r["行业-涨跌幅"]) else None
    n_firm = r.get("公司家数")
    leader_name = r.get("领涨股")
    leader_chg = r.get("领涨股-涨跌幅")

    # 排名（净额降序）
    ranked = flow.copy()
    ranked["rank"] = ranked["净额"].rank(ascending=False, method="min")
    rank = int(ranked.loc[row.index[0], "rank"]) if row.index[0] in ranked.index else None
    pct_rank = (1 - (rank - 1) / n_ind) * 100 if rank else None   # 100 = 资金流入最强

    # ---------------- 行业当日资金（5 分）
    s_flow = band_score(pct_rank, [(90, 5.0), (75, 4.2), (50, 3.2), (30, 2.2), (10, 1.2)], default=0.5)
    if net < 0:
        s_flow = max(0.3, s_flow - 0.8)

    # ---------------- 行业趋势强度（4 分）
    idx_hist = ds.industry_index_hist(industry)
    ind_ret5 = ind_ret20 = None
    if not idx_hist.empty and len(idx_hist) > 21:
        c = idx_hist["close"]
        ind_ret5 = round((c.iloc[-1] / c.iloc[-6] - 1) * 100, 2) if len(c) > 6 else None
        ind_ret20 = round((c.iloc[-1] / c.iloc[-21] - 1) * 100, 2)
    s_trend = band_score(ind_ret20, [(10, 2.5), (3, 2.0), (0, 1.4), (-5, 0.8)], default=0.3)
    s_trend += band_score(ind_ret5, [(5, 1.5), (1, 1.1), (-3, 0.6)], default=0.3)

    # ---------------- 资金强度（3 分）：净额 / 流入总额
    strength = (net / inflow * 100) if inflow else None
    s_strength = band_score(strength, [(20, 3.0), (10, 2.4), (3, 1.8), (0, 1.2)], default=0.4)

    # ---------------- 个股在同行业内的资金流排名（3 分）
    stock_flow = ds.stock_fund_flow_all()
    own_net = own_rank_pct = None
    ind_flow_rank = in_cons = None
    if not stock_flow.empty:
        target = str(code).zfill(6)
        me = stock_flow[stock_flow["股票代码"] == target]
        if not me.empty:
            own_net = float(me.iloc[0]["净额"]) if pd.notna(me.iloc[0]["净额"]) else None
        if not constituents.empty:
            codes = set(constituents["code"])
            peers = stock_flow[stock_flow["股票代码"].isin(codes)].copy()
            if not peers.empty and target in set(peers["股票代码"]):
                peers["rk"] = peers["净额"].rank(ascending=False, method="min")
                my_rk = int(peers.loc[peers["股票代码"] == target, "rk"].iloc[0])
                own_rank_pct = (1 - (my_rk - 1) / len(peers)) * 100
                ind_flow_rank = (my_rk, len(peers))
    s_own = band_score(own_rank_pct, [(90, 3.0), (70, 2.4), (50, 1.8), (30, 1.0)], default=1.0)
    if own_net is not None and own_net < 0:
        s_own = max(0.3, s_own - 0.6)

    res.score = round(max(0.0, min(MAX_SCORE, s_flow + s_trend + s_strength + s_own)), 2)

    # ---------------- 展示
    res.add("行业板块", f"{industry}", note=f"共 {int(n_firm) if n_firm else '—'} 家公司")
    concepts = [b["name"] for b in (boards or {}).get("concept", []) if b.get("name")]
    if concepts:
        res.add("所属概念（东财）", "、".join(concepts[:6]),
                note=f"共 {len(concepts)} 个概念标签")
    res.add("行业当日涨跌", fmt_pct(ind_chg, sign=True),
            "good" if (ind_chg or 0) > 0 else "bad")
    res.add("行业主力净流入", f"{net:+.2f} 亿",
            "good" if net > 0 else "bad",
            f"流入 {inflow:.2f}亿 / 流出 {outflow:.2f}亿" if inflow and outflow else "")
    res.add("全市场资金流排名", f"{rank} / {n_ind} 位（前 {100 - pct_rank:.0f}% 分位）"
            if rank and pct_rank is not None else "—",
            "good" if (pct_rank or 0) > 70 else ("bad" if (pct_rank or 0) < 30 else "neutral"))
    res.add("资金强度（净额/流入）", fmt_pct(strength),
            "good" if (strength or 0) > 10 else "neutral")
    res.add("板块指数 5 日/20 日", f"{fmt_pct(ind_ret5, sign=True)} / {fmt_pct(ind_ret20, sign=True)}",
            "good" if (ind_ret20 or 0) > 0 else "bad")
    res.add("板块领涨股", f"{leader_name}（{fmt_pct(leader_chg, sign=True)}）" if leader_name else "—")
    res.add("个股主力净流入", f"{own_net / 1e8:+.2f} 亿" if own_net is not None else "—",
            "good" if (own_net or 0) > 0 else "bad")
    res.add("个股资金行业排名",
            f"{ind_flow_rank[0]} / {ind_flow_rank[1]} 位（前 {100 - own_rank_pct:.0f}% 分位）"
            if ind_flow_rank and own_rank_pct is not None else "—",
            "good" if (own_rank_pct or 0) > 70 else ("bad" if (own_rank_pct or 0) < 30 else "neutral"))

    # ---------------- 结论
    if pct_rank is not None and pct_rank >= 80 and net > 0:
        res.sig(f"{industry} 板块资金净流入 {net:+.2f} 亿，位列全市场第 {rank}，板块处于资金流入前列", "bullish", 3)
    elif pct_rank is not None and pct_rank <= 25:
        res.sig(f"{industry} 板块资金净流出 {net:+.2f} 亿，排名靠后，短期缺乏板块合力", "bearish", 3)
    if ind_ret20 is not None and ind_ret20 > 8:
        res.sig(f"板块 20 日涨幅 {ind_ret20:+.1f}%，beta 端持续走强", "bullish", 2)
    if own_rank_pct is not None and own_rank_pct >= 80:
        res.sig("个股主力资金在同行业排名前列，为板块内资金聚焦标的", "bullish", 2)
    elif own_rank_pct is not None and own_rank_pct <= 30:
        res.sig("个股资金在行业内排名靠后，资金关注度弱于同业", "bearish", 2)

    # ---------------- 资金流榜（Top / Bottom）
    top = flow.sort_values("净额", ascending=False).head(8)
    bottom = flow.sort_values("净额").head(5)
    rows = []
    for _, x in pd.concat([top, bottom]).iterrows():
        rows.append([str(x["行业"]), f"{x['净额']:+.2f}",
                     fmt_pct(x["行业-涨跌幅"], sign=True),
                     str(x.get("领涨股", "—"))])
    res.table("行业资金流排行（净流入 Top8 / 净流出 Top5）",
              ["行业", "净额(亿)", "涨跌幅", "领涨股"], rows,
              note="同花顺行业资金流，即时口径，单位亿元")
    res.headline = (f"{industry} 今日涨跌 {fmt_pct(ind_chg, sign=True)}，主力净流入 {net:+.2f} 亿"
                    f"（全市场第 {rank}/{n_ind}），板块 20 日 {fmt_pct(ind_ret20, sign=True)}，"
                    f"个股资金行业内排名 {ind_flow_rank[0]}/{ind_flow_rank[1]}"
                    if ind_flow_rank and rank else
                    f"{industry} 今日涨跌 {fmt_pct(ind_chg, sign=True)}，主力净流入 {net:+.2f} 亿")
    return res
