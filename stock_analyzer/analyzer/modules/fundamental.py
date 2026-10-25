"""模块 1：基本面分析。

覆盖：成长性（营收/净利同比）、盈利能力（毛利率/净利率/ROE）、收益质量
（现金流含量/应收/存货周转）、财务健康（资产负债率）、相对估值（PE vs 行业中位）。
"""
from __future__ import annotations

import pandas as pd

from .. import datasource as ds
from ..utils import band_score, fmt_pct, fmt_yi, inverse_scale_score, safe_div, to_num
from .base import ModuleResult

MAX_SCORE = 25.0

# 财务摘要里用到的指标名（与新浪口径一致）
FIELDS = {
    "revenue": "营业总收入",
    "np_parent": "归母净利润",
    "np_deduct": "扣非净利润",
    "ocf": "经营现金流量净额",
    "gross_margin": "毛利率",
    "net_margin": "销售净利率",
    "roe": "净资产收益率(ROE)",
    "debt_ratio": "资产负债率",
    "rev_yoy": "营业总收入增长率",
    "np_yoy": "归属母公司净利润增长率",
    "ocf_to_np": "经营活动净现金/归属母公司的净利润",
    "ar_days": "应收账款周转天数",
    "inv_days": "存货周转天数",
    "eps": "基本每股收益",
    "bps": "每股净资产",
}


def _pivot(fin: pd.DataFrame) -> pd.DataFrame:
    """行=指标 / 列=报告期 的宽表 -> 索引=报告期 的长表。"""
    if fin.empty:
        return pd.DataFrame()
    id_col, ind_col = fin.columns[0], fin.columns[1]
    periods = [c for c in fin.columns if str(c).isdigit() and len(str(c)) == 8]
    if not periods:
        return pd.DataFrame()
    sub = fin[[ind_col] + periods].copy()
    sub = sub.dropna(subset=[ind_col]).drop_duplicates(subset=[ind_col], keep="first")
    out = sub.set_index(ind_col)[periods].T
    out.index = pd.to_datetime(out.index, format="%Y%m%d", errors="coerce")
    out = out[~out.index.isna()].sort_index()
    for c in out.columns:
        out[c] = out[c].map(to_num)
    return out


def _annualize_roe(roe: float | None, period: pd.Timestamp) -> float | None:
    """把报告期累计 ROE 年化，便于跨期比较（Q1=×4，H1=×2，Q3=×4/3）。"""
    if roe is None:
        return None
    month = period.month
    factor = {3: 4.0, 6: 2.0, 9: 4 / 3, 12: 1.0}.get(month, 1.0)
    return roe * factor


def analyze(code: str, quote: dict, industry_pe: float | None = None) -> ModuleResult:
    res = ModuleResult(key="fundamental", name="基本面", max_score=MAX_SCORE)

    fin = _pivot(ds.financial_abstract(code))
    survey = ds.company_survey(code)
    profile = ds.business_profile(code)

    # ---------------- 公司画像
    if survey:
        res.details["company"] = survey
        res.add("公司全称", survey.get("full_name", "—"))
        res.add("所属行业", f"{survey.get('em2016', '—')}（东财三级）")
        res.add("上市板块", f"{survey.get('market', '—')}")
        if survey.get("employees"):
            res.add("员工人数", f"{survey['employees']:,.0f} 人")
    if profile.get("main_business"):
        res.details["main_business"] = profile
        res.add("主营业务", profile["main_business"][:120])
    if profile.get("product_type"):
        res.add("产品类型", profile["product_type"])

    if fin.empty:
        res.available = False
        res.headline = "财务数据获取失败，基本面维度不参与加权"
        return res

    latest = fin.index[-1]
    cur = fin.loc[latest]
    # 去年同期
    prev_year = latest - pd.DateOffset(years=1)
    prev = None
    for idx in fin.index:
        if abs((idx - prev_year).days) <= 5:
            prev = fin.loc[idx]
            break

    g = lambda k: cur.get(FIELDS[k])          # noqa: E731
    gp = (lambda k: prev.get(FIELDS[k]) if prev is not None else None)   # noqa: E731

    revenue, np_parent, np_deduct = g("revenue"), g("np_parent"), g("np_deduct")
    rev_yoy = g("rev_yoy") if g("rev_yoy") is not None else (
        safe_div(revenue - gp("revenue"), abs(gp("revenue"))) * 100 if gp("revenue") else None)
    np_yoy = g("np_yoy") if g("np_yoy") is not None else (
        safe_div(np_parent - gp("np_parent"), abs(gp("np_parent"))) * 100 if gp("np_parent") else None)

    gross, net_m, roe_raw = g("gross_margin"), g("net_margin"), g("roe")
    roe = _annualize_roe(roe_raw, latest)
    debt = g("debt_ratio")
    ocf, ocf_ratio, ar_days, inv_days = g("ocf"), g("ocf_to_np"), g("ar_days"), g("inv_days")

    # ---------------- 成长性（8 分）
    s_growth = band_score(rev_yoy, [
        (50, 5.0), (30, 4.2), (15, 3.4), (5, 2.6), (0, 2.0), (-15, 1.0)], default=0.2)
    s_growth += band_score(min(np_yoy, 300) if np_yoy is not None else None, [
        (100, 3.0), (50, 2.6), (20, 2.2), (0, 1.5), (-30, 0.7)], default=0.0)

    # ---------------- 盈利能力（7 分）
    s_profit = band_score(gross, [(45, 3.0), (30, 2.4), (20, 1.8), (10, 1.0)], default=0.3)
    s_profit += band_score(net_m, [(15, 2.0), (8, 1.5), (3, 1.0), (0, 0.5)], default=0.0)
    s_profit += band_score(roe, [(12, 2.0), (8, 1.5), (4, 1.0), (0, 0.4)], default=0.0)

    # ---------------- 收益质量（5 分）
    s_quality = band_score(ocf_ratio, [(1.0, 2.0), (0.7, 1.6), (0.4, 1.0), (0, 0.5)], default=0.3)
    s_quality += band_score(-ar_days if ar_days is not None else None,
                            [(-60, 1.5), (-120, 1.1), (-200, 0.6)], default=0.2)
    s_quality += band_score(-inv_days if inv_days is not None else None,
                            [(-90, 1.5), (-180, 1.1), (-300, 0.6)], default=0.2)

    # ---------------- 财务健康（2 分）
    s_health = inverse_scale_score(debt, 20, 85, 2.0) if debt is not None else 1.0

    # ---------------- 相对估值（3 分）
    pe = quote.get("pe_ttm")
    pb = quote.get("pb")
    s_value = 1.2
    if pe is not None and pe > 0 and industry_pe and industry_pe > 0:
        ratio = pe / industry_pe
        s_value = band_score(-ratio, [(-0.7, 3.0), (-1.0, 2.4), (-1.5, 1.6), (-2.5, 0.8)], default=0.3)
    elif pe is not None and pe <= 0:
        s_value = 0.5

    res.score = round(s_growth + s_profit + s_quality + s_health + s_value, 2)

    # ---------------- 展示
    period_label = latest.strftime("%Y-%m-%d")
    res.add("最新报告期", period_label)
    res.add("营业总收入", f"{fmt_yi(revenue)}（同比 {fmt_pct(rev_yoy, sign=True)}）",
            "good" if (rev_yoy or 0) > 15 else ("bad" if (rev_yoy or 0) < 0 else "neutral"))
    res.add("归母净利润", f"{fmt_yi(np_parent)}（同比 {fmt_pct(np_yoy, sign=True)}）",
            "good" if (np_yoy or 0) > 20 else ("bad" if (np_yoy or 0) < 0 else "neutral"))
    if np_deduct is not None:
        res.add("扣非净利润", fmt_yi(np_deduct),
                "good" if (np_deduct or 0) > 0 else "bad")
    res.add("毛利率", fmt_pct(gross), "good" if (gross or 0) >= 30 else "neutral")
    res.add("净利率", fmt_pct(net_m), "good" if (net_m or 0) >= 10 else "neutral")
    res.add("ROE（年化）", fmt_pct(roe),
            "good" if (roe or 0) >= 10 else "neutral",
            f"报告期累计 {fmt_pct(roe_raw)}")
    res.add("经营现金流", fmt_yi(ocf), "good" if (ocf or 0) > 0 else "bad")
    res.add("现金流/净利润", fmt_pct(ocf_ratio * 100 if ocf_ratio is not None else None),
            "good" if (ocf_ratio or 0) >= 0.8 else ("bad" if (ocf_ratio or 0) < 0.3 else "neutral"))
    res.add("资产负债率", fmt_pct(debt), "good" if (debt or 100) < 50 else ("bad" if (debt or 0) > 70 else "neutral"))
    res.add("应收账款周转天数", f"{ar_days:.0f} 天" if ar_days else "—")
    res.add("存货周转天数", f"{inv_days:.0f} 天" if inv_days else "—")
    res.add("PE(TTM)", f"{pe:.1f}" if pe and pe > 0 else ("亏损" if pe is not None else "—"),
            "good" if pe and 0 < pe < 30 else ("bad" if pe and pe > 80 else "neutral"),
            f"行业中位 {industry_pe:.1f}" if industry_pe else "")
    res.add("PB", f"{pb:.2f}" if pb else "—")
    res.add("总市值", fmt_yi(quote.get("total_mv")))
    res.add("流通市值", fmt_yi(quote.get("float_mv")))

    # ---------------- 结论
    if (rev_yoy or 0) > 20 and (np_yoy or 0) > 20:
        res.sig(f"营收与利润同步高增（营收 {fmt_pct(rev_yoy, sign=True)}，净利 {fmt_pct(np_yoy, sign=True)}）", "bullish", 3)
    elif (rev_yoy or 0) > 0 and (np_yoy or 0) < 0:
        res.sig("增收不增利，需关注毛利率与费用变化", "bearish", 3)
    elif (rev_yoy or 0) < 0 and (np_yoy or 0) < 0:
        res.sig("营收与利润双降，基本面承压", "bearish", 3)
    else:
        res.sig("业绩增速平稳，缺乏明显弹性", "neutral", 1)

    if gross is not None and gross >= 35:
        res.sig(f"毛利率 {gross:.1f}%，具备较强产品议价力", "bullish", 2)
    if ocf_ratio is not None and ocf_ratio < 0.5:
        res.sig(f"经营现金流仅覆盖净利润的 {ocf_ratio * 100:.0f}%，利润含金量偏弱", "bearish", 2)
    if debt is not None and debt > 65:
        res.sig(f"资产负债率 {debt:.1f}%，财务杠杆偏高", "bearish", 2)
    if pe is not None and industry_pe and pe > 0 and pe > industry_pe * 1.5:
        res.sig(f"PE {pe:.0f} 显著高于行业中位 {industry_pe:.0f}，估值溢价需业绩兑现", "bearish", 2)
    if pe is not None and industry_pe and 0 < pe < industry_pe * 0.8:
        res.sig(f"PE 低于行业中位（{pe:.0f} vs {industry_pe:.0f}），存在相对估值折价", "bullish", 2)

    # ---------------- 近 8 期财务表
    rows = []
    for idx in fin.index[-8:][::-1]:
        r = fin.loc[idx]
        rows.append([
            idx.strftime("%Y-%m-%d"),
            fmt_yi(r.get(FIELDS["revenue"])),
            fmt_yi(r.get(FIELDS["np_parent"])),
            fmt_pct(r.get(FIELDS["rev_yoy"]), sign=True),
            fmt_pct(r.get(FIELDS["np_yoy"]), sign=True),
            fmt_pct(r.get(FIELDS["gross_margin"])),
            fmt_pct(_annualize_roe(r.get(FIELDS["roe"]), idx)),
            fmt_pct(r.get(FIELDS["debt_ratio"])),
        ])
    res.table("近 8 个报告期财务概览", ["报告期", "营业收入", "归母净利", "营收同比", "净利同比", "毛利率", "ROE(年化)", "负债率"], rows)

    # ---------------- 主营构成
    mb = ds.main_business(code)
    if not mb.empty:
        latest_date = mb["报告日期"].max()
        sub = mb[mb["报告日期"] == latest_date]
        for ctype in ["按产品分类", "按行业分类", "按地区分类"]:
            part = sub[sub["分类类型"] == ctype]
            if part.empty:
                continue
            part = part.sort_values("收入比例", ascending=False).head(6)
            rows = []
            for _, r in part.iterrows():
                rev = to_num(r["主营收入"])
                rows.append([
                    str(r["主营构成"]),
                    fmt_yi(rev),
                    f"{to_num(r['收入比例']) * 100:.1f}%",
                    f"{to_num(r['毛利率']) * 100:.1f}%" if to_num(r["毛利率"]) is not None else "—",
                ])
            res.table(f"主营构成（{ctype}，{str(latest_date)[:10]}）",
                      ["构成", "收入", "占比", "毛利率"], rows)

    res.headline = (f"{period_label} 营收 {fmt_yi(revenue)}（{fmt_pct(rev_yoy, sign=True)}）、"
                    f"净利 {fmt_yi(np_parent)}（{fmt_pct(np_yoy, sign=True)}）、"
                    f"毛利率 {fmt_pct(gross)}、ROE(年化) {fmt_pct(roe)}")
    return res
