"""模块 4：同板块龙头情况。

用同花顺行业成分股（含流通市值、成交额、涨跌幅、PE）做行业内横向对比：
市值分位、成交额分位、涨幅相对中位数、与龙头的市值差距，并输出龙头梯队表。
"""
from __future__ import annotations

import pandas as pd

from ..utils import band_score, fmt_pct, fmt_yi
from .base import ModuleResult

MAX_SCORE = 10.0


def _pct_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if s.empty or value is None:
        return 0.0
    return float((s <= value).sum() / len(s) * 100)


def analyze(code: str, industry: str, cons: pd.DataFrame) -> ModuleResult:
    res = ModuleResult(key="leader", name="板块地位", max_score=MAX_SCORE)
    target = str(code).zfill(6)
    if cons.empty or target not in set(cons["code"]):
        res.available = False
        res.headline = f"未获取到 {industry} 板块成分股数据，板块地位维度不参与加权"
        return res

    df = cons.copy()
    df = df[df["float_mv"].notna() & (df["float_mv"] > 0)]
    if df.empty:
        res.available = False
        res.headline = "板块成分股市值数据缺失"
        return res

    me = df[df["code"] == target].iloc[0]
    my_mv = float(me["float_mv"])
    my_amt = float(me["amount"]) if pd.notna(me["amount"]) else None
    my_chg = float(me["change_pct"]) if pd.notna(me["change_pct"]) else None
    my_pe = float(me["pe"]) if pd.notna(me["pe"]) else None

    leader = df.sort_values("float_mv", ascending=False).iloc[0]
    leader_mv = float(leader["float_mv"])
    mv_rank = int((df["float_mv"] > my_mv).sum() + 1)
    mv_pct = _pct_rank(df["float_mv"], my_mv)
    mv_ratio = my_mv / leader_mv if leader_mv else 0

    amt_pct = _pct_rank(df["amount"], my_amt) if my_amt else None
    med_chg = df["change_pct"].median()
    chg_diff = (my_chg - med_chg) if (my_chg is not None and pd.notna(med_chg)) else None

    # ---------------- 评分（10 分）
    s_mv = band_score(mv_pct, [(90, 4.0), (75, 3.3), (50, 2.4), (25, 1.5)], default=0.6)
    s_amt = band_score(amt_pct, [(90, 2.0), (70, 1.6), (50, 1.1), (25, 0.6)], default=0.3)
    s_chg = band_score(chg_diff, [(5, 2.0), (1, 1.5), (0, 1.1), (-3, 0.6)], default=0.3)
    s_gap = band_score(mv_ratio, [(0.8, 2.0), (0.5, 1.6), (0.25, 1.2), (0.1, 0.8)], default=0.4)
    res.score = round(max(0.0, min(MAX_SCORE, s_mv + s_amt + s_chg + s_gap)), 2)

    # ---------------- 展示
    n = len(df)
    res.add("所属板块", f"{industry}（{n} 只有效成分股）")
    res.add("流通市值", fmt_yi(my_mv), note=f"板块内第 {mv_rank} / {n} 位")
    res.add("市值分位", f"前 {100 - mv_pct:.0f}%",
            "good" if mv_pct >= 80 else ("bad" if mv_pct < 40 else "neutral"))
    res.add("板块龙头", f"{leader['name']}（{fmt_yi(leader_mv)}）")
    if mv_rank == 1:
        res.add("与龙头市值比", "—", "good", "自身即为板块市值龙头")
    else:
        res.add("与龙头市值比", fmt_pct(mv_ratio * 100, digits=1),
                "good" if mv_ratio >= 0.5 else "neutral",
                f"龙头为其 {1 / mv_ratio:.1f} 倍" if mv_ratio > 0 else "")
    res.add("成交额", f"{my_amt / 1e8:.2f} 亿" if my_amt else "—",
            note=f"板块内前 {100 - amt_pct:.0f}% 分位" if amt_pct is not None else "")
    res.add("今日涨幅 vs 板块中位", f"{fmt_pct(my_chg, sign=True)} vs {fmt_pct(med_chg, sign=True)}",
            "good" if (chg_diff or 0) > 0 else "bad")
    res.add("PE 相对板块", f"{my_pe:.1f} vs 中位 {df['pe'].median():.1f}"
            if my_pe and pd.notna(df["pe"].median()) else "—")

    # ---------------- 结论
    if mv_rank == 1:
        res.sig(f"自身即为 {industry} 板块市值第一的龙头，具备板块定价权与资金锚定效应", "bullish", 3)
    elif mv_rank <= 3:
        res.sig(f"流通市值板块第 {mv_rank} 名，属于 {industry} 板块核心权重股", "bullish", 3)
    elif mv_pct < 40:
        res.sig("市值处于板块中下游，弹性大但定价权与资金关注度偏弱", "neutral", 2)
    if mv_rank > 1 and mv_ratio >= 0.5:
        res.sig(f"市值已达龙头 {leader['name']} 的 {mv_ratio * 100:.0f}%，具备双龙头格局", "bullish", 2)
    if chg_diff is not None and chg_diff > 3:
        res.sig(f"今日涨幅跑赢板块中位 {chg_diff:.1f}pct，短期为板块领涨方向", "bullish", 2)
    elif chg_diff is not None and chg_diff < -3:
        res.sig(f"今日涨幅落后板块中位 {abs(chg_diff):.1f}pct，短期跟随度弱", "bearish", 1)

    # ---------------- 龙头梯队表（按市值前 10）
    top = df.sort_values("float_mv", ascending=False).head(10)
    rows = []
    for i, (_, x) in enumerate(top.iterrows(), 1):
        mark = " ★" if str(x["code"]) == target else ""
        rows.append([
            str(i),
            f"{x['name']}{mark}",
            str(x["code"]),
            fmt_yi(x["float_mv"]),
            f"{x['pe']:.1f}" if pd.notna(x["pe"]) else "—",
            fmt_pct(x["change_pct"], sign=True),
            fmt_pct(x["turnover"]),
        ])
    res.table(f"{industry} 板块市值前 10（★ 为当前标的）",
              ["#", "名称", "代码", "流通市值", "PE", "涨跌幅", "换手"], rows)

    # ---------------- 今日涨幅前 5
    hot = df[df["change_pct"].notna()].sort_values("change_pct", ascending=False).head(5)
    rows = [[str(x["name"]), fmt_pct(x["change_pct"], sign=True),
             fmt_yi(x["float_mv"]), fmt_pct(x["turnover"])] for _, x in hot.iterrows()]
    res.table("今日板块涨幅前 5", ["名称", "涨跌幅", "流通市值", "换手"], rows)

    res.details["leader"] = leader.to_dict()
    res.headline = (f"{industry} 板块第 {mv_rank}/{n} 大市值（前 {100 - mv_pct:.0f}%），"
                    f"龙头 {leader['name']} 流通市值 {fmt_yi(leader_mv)}，"
                    f"今日涨幅 {fmt_pct(my_chg, sign=True)} vs 中位 {fmt_pct(med_chg, sign=True)}")
    return res
