"""模块 5：消息面 / 近期大事。

覆盖：
  1. 新闻情绪（正负向词典打分 + 关键事件提取）
  2. 公告事件分类（增减持、回购、并购、重大合同、股权质押、解禁、监管问询等）
  3. 卖方研报评级与盈利预测（覆盖机构数、买入占比、未来两年 EPS 增速）
  4. 龙虎榜资金（近 90 日机构/游资上榜与净买额）
  5. 主要产品价格走势（有期货主力合约的直接取价格序列，无则给出跟踪要点）
"""
from __future__ import annotations

import re

import pandas as pd

from .. import datasource as ds
from ..config import (ANNOUNCE_CATEGORIES, FUTURES_UNIT, NEGATIVE_WORDS,
                      POSITIVE_WORDS, PRODUCT_HINTS)
from ..utils import band_score, fmt_pct, to_num
from .base import ModuleResult

MAX_SCORE = 20.0


# ------------------------------------------------------------------ 情绪打分
def _sentiment(text: str) -> tuple[int, list[str]]:
    hits, total = [], 0
    for w, v in POSITIVE_WORDS.items():
        if w in text:
            total += v
            if v >= 3:
                hits.append(f"+{w}")
    for w, v in NEGATIVE_WORDS.items():
        if w in text:
            total += v
            if v <= -3:
                hits.append(f"-{w}")
    return total, hits


def _classify_ann(title: str, types: str) -> tuple[str, int]:
    hay = f"{title} {types}"
    for kw, label, w in ANNOUNCE_CATEGORIES:
        if kw in hay:
            return label, w
    return "其他", 0


def _core_name(name: str) -> str:
    """从简称里提取用于相关性判断的核心词：去掉 -W/-U 等后缀与字母。"""
    s = re.sub(r"[-－][A-Za-z]+$", "", str(name or "").strip())
    s = re.sub(r"[A-Za-z0-9]", "", s)
    return s[:4]


# ------------------------------------------------------------------ 主流程
def analyze(code: str, industry: str, name: str = "", product_text: str = "") -> ModuleResult:
    res = ModuleResult(key="news", name="消息面", max_score=MAX_SCORE)

    news = ds.stock_news(code, limit=30)
    anns = ds.stock_announcements(code, days=120)
    reports = ds.research_reports(code)
    lhb = ds.lhb_records(code, days=90)

    # ================================================== 1. 新闻情绪（8 分）
    s_news = 4.0
    n_report = 0
    if not news.empty:
        core = _core_name(name)
        target = str(code).zfill(6)

        def _relevant(row) -> bool:
            hay = f"{row.get('title', '')} {str(row.get('content', ''))[:150]}"
            return bool(core and core in hay) or target in hay

        rel_mask = news.apply(_relevant, axis=1)
        n_rel = int(rel_mask.sum())
        pool = news[rel_mask] if n_rel >= 3 else news      # 相关新闻不足时退回全量
        basis = "相关新闻" if n_rel >= 3 else "全部新闻"

        scores, all_hits, pos_n, neg_n = [], [], 0, 0
        for _, r in pool.iterrows():
            text = f"{r.get('title', '')} {str(r.get('content', ''))[:200]}"
            sc, hits = _sentiment(text)
            scores.append(sc)
            all_hits.extend(hits)
            if sc > 0:
                pos_n += 1
            elif sc < 0:
                neg_n += 1
        avg = sum(scores) / len(scores)
        s_news = band_score(avg, [(2.0, 8.0), (1.0, 6.8), (0.3, 5.8), (-0.3, 4.6), (-1.2, 3.0)], default=1.5)
        res.add("新闻条数", f"{len(news)} 条",
                note=f"与公司直接相关 {n_rel} 条，情绪打分基于{basis} {len(pool)} 条")
        res.add("新闻情绪均值", f"{avg:+.2f}", "good" if avg > 0.3 else ("bad" if avg < -0.3 else "neutral"),
                f"正向 {pos_n} / 负向 {neg_n}；词典：+3 强利好 ~ -5 强利空")

        show = pool.head(8) if n_rel >= 3 else news.head(8)
        rows = []
        for _, r in show.iterrows():
            t = str(r.get("time", ""))[:10]
            rows.append([t, str(r.get("title", ""))[:44], _short_source(r.get("source"))])
        res.table(f"近期新闻（{basis}，最近 8 条）", ["日期", "标题", "来源"], rows)
        keys = list(dict.fromkeys(h for h in all_hits if h.startswith("+") and len(h) > 2))[:6]
        if keys:
            res.sig(f"新闻中的强正向事件词：{'、'.join(k.replace('+', '') for k in keys)}", "bullish", 2)
        negs = list(dict.fromkeys(h for h in all_hits if h.startswith("-") and len(h) > 2))[:4]
        if negs:
            res.sig(f"新闻中的强负向事件词：{'、'.join(k.replace('-', '') for k in negs)}", "bearish", 2)
    else:
        res.add("新闻", "未获取到新闻数据", "warn")

    # ================================================== 2. 公告事件（6 分）
    s_ann = 4.0
    if not anns.empty:
        anns = anns.copy()
        anns["category"], anns["weight"] = zip(*[_classify_ann(str(r.title), str(r.types)) for r in anns.itertuples()])
        counts = anns["category"].value_counts()
        pos = int(anns[anns["weight"] > 0].shape[0])
        neg = int(anns[anns["weight"] < 0].shape[0])
        hold = counts.get("股东增持", 0) - counts.get("股东减持", 0)
        s_ann = band_score(pos - neg, [(3, 6.0), (2, 5.2), (1, 4.6), (0, 4.0), (-1, 2.8), (-3, 1.8)], default=1.0)

        res.add("公告条数（近 120 天）", f"{len(anns)} 条",
                note=f"正向事件 {pos} / 负向事件 {neg}")
        if counts.get("股东增持", 0) or counts.get("股东减持", 0):
            res.add("增减持", f"增持 {int(counts.get('股东增持', 0))} / 减持 {int(counts.get('股东减持', 0))}",
                    "good" if hold > 0 else ("bad" if hold < 0 else "neutral"))
        for cat in ["股份回购", "并购重组", "重大合同/订单", "股权激励", "再融资", "限售解禁", "股权质押", "监管问询", "监管处罚"]:
            if counts.get(cat, 0):
                tone = "good" if cat in ("股份回购", "并购重组", "重大合同/订单", "股权激励") else "bad"
                res.add(f"{cat}", f"{int(counts[cat])} 次", tone)

        rows = []
        for _, r in anns.head(10).iterrows():
            rows.append([r["date"].strftime("%Y-%m-%d"), r["category"], str(r["title"])[:46]])
        res.table("近期重要公告（最近 10 条）", ["日期", "类别", "标题"], rows)

        if hold > 0:
            res.sig(f"近 120 天增持公告多于减持（{int(counts.get('股东增持', 0))} vs {int(counts.get('股东减持', 0))}），股东行为正向", "bullish", 3)
        elif hold < 0:
            res.sig(f"近 120 天减持公告多于增持（{int(counts.get('股东减持', 0))} vs {int(counts.get('股东增持', 0))}），需警惕股东套现", "bearish", 3)
        if counts.get("并购重组", 0):
            res.sig("存在并购重组类公告，外延扩张预期是重要催化", "bullish", 2)
        if counts.get("重大合同/订单", 0):
            res.sig("公告显示重大合同/订单落地，订单能见度提升", "bullish", 2)
        if counts.get("限售解禁", 0):
            res.sig("近期有限售解禁公告，关注解禁前后抛压", "bearish", 2)
        if counts.get("股权质押", 0):
            res.sig("存在股权质押公告，留意大股东质押比例", "bearish", 1)
    else:
        res.add("公告", "未获取到公告数据", "warn")

    # ================================================== 3. 研报评级（4 分）
    s_research = 2.0
    if not reports.empty:
        recent = reports[reports["日期"] >= (pd.Timestamp.today() - pd.Timedelta(days=180))]
        pool = recent if len(recent) >= 3 else reports.head(10)
        ratings = pool["东财评级"].astype(str)
        buy = int(ratings.str.contains("买入", na=False).sum())
        add = int(ratings.str.contains("增持", na=False).sum())
        total = max(1, len(pool))
        buy_ratio = (buy + add) / total

        # 盈利预测增速
        eps_cols = sorted([c for c in pool.columns if "盈利预测-收益" in c])
        growth = None
        if len(eps_cols) >= 2:
            e1 = pd.to_numeric(pool[eps_cols[0]], errors="coerce").median()
            e2 = pd.to_numeric(pool[eps_cols[1]], errors="coerce").median()
            if e1 and e2 and e1 > 0:
                growth = (e2 / e1 - 1) * 100
        n_inst = pool["机构"].nunique()

        s_research = band_score(buy_ratio * 100, [(80, 2.4), (60, 2.0), (40, 1.4)], default=0.8)
        s_research += band_score(growth, [(30, 1.6), (15, 1.2), (5, 0.8)], default=0.3)

        n_report = len(pool)
        res.add("研报覆盖（近 180 天）", f"{len(pool)} 篇 / {n_inst} 家机构")
        res.add("评级分布", f"买入 {buy} / 增持 {add} / 其他 {total - buy - add}",
                "good" if buy_ratio >= 0.7 else "neutral")
        res.add("一致预期 EPS 增速", fmt_pct(growth, sign=True) if growth is not None else "—",
                "good" if (growth or 0) > 20 else "neutral",
                f"{eps_cols[0][:4]}→{eps_cols[1][:4]}" if len(eps_cols) >= 2 else "")

        rows = []
        for _, r in pool.head(6).iterrows():
            rows.append([
                r["日期"].strftime("%Y-%m-%d") if pd.notna(r["日期"]) else "—",
                str(r["机构"])[:10],
                str(r["东财评级"]),
                str(r["报告名称"])[:34],
            ])
        res.table("近期卖方研报", ["日期", "机构", "评级", "标题"], rows)

        if buy_ratio >= 0.8:
            res.sig(f"近半年 {n_inst} 家机构覆盖，买入/增持占比 {buy_ratio * 100:.0f}%，一致预期偏乐观", "bullish", 2)
        if growth is not None and growth > 25:
            res.sig(f"一致预期未来 EPS 增速 {growth:.0f}%，成长逻辑仍被卖方认可", "bullish", 2)
    else:
        res.add("研报", "近期无卖方研报覆盖", "warn")

    # ================================================== 4. 龙虎榜（2 分）
    s_lhb = 1.2
    if not lhb.empty:
        net_buy = pd.to_numeric(lhb["龙虎榜净买额"], errors="coerce").sum()
        n_lhb = len(lhb)
        s_lhb = 2.0 if net_buy > 0 else 1.0
        res.add("近 90 日上榜次数", f"{n_lhb} 次")
        res.add("龙虎榜净买额", f"{net_buy / 1e8:+.2f} 亿",
                "good" if net_buy > 0 else "bad")
        rows = []
        for _, r in lhb.head(5).iterrows():
            rows.append([
                r["上榜日"].strftime("%Y-%m-%d") if pd.notna(r["上榜日"]) else "—",
                f"{to_num(r['龙虎榜净买额']) / 1e8:+.2f} 亿" if to_num(r["龙虎榜净买额"]) is not None else "—",
                str(r["上榜原因"])[:30],
            ])
        res.table("龙虎榜记录（近 5 次）", ["日期", "净买额", "上榜原因"], rows)
        if net_buy > 0:
            res.sig(f"近 90 日龙虎榜净买入 {net_buy / 1e8:+.2f} 亿，游资/机构参与偏积极", "bullish", 1)
    else:
        res.add("龙虎榜", "近 90 日未上榜")

    # ================================================== 5. 产品价格走势
    pick = ds.pick_futures(industry, product_text)
    price_note = ""
    if pick:
        sym, label = pick
        fut = ds.futures_price(sym, days=120)
        if not fut.empty:
            c = fut["close"]
            chg20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100 if len(c) > 21 else None
            chg60 = (c.iloc[-1] / c.iloc[-61] - 1) * 100 if len(c) > 61 else None
            unit = FUTURES_UNIT.get(sym, "元/吨")
            res.add(f"产品价格代理（{label}）", f"{c.iloc[-1]:,.0f} {unit}",
                    "good" if (chg20 or 0) > 0 else "bad",
                    f"20日 {fmt_pct(chg20, sign=True)} / 60日 {fmt_pct(chg60, sign=True)}")
            res.details["futures"] = {"symbol": sym, "label": label, "df": fut}
            rows = []
            for _, r in fut.tail(6).iloc[::-1].iterrows():
                rows.append([r["date"].strftime("%Y-%m-%d"), f"{r['close']:,.0f}"])
            res.table(f"{label} 价格（近 6 个交易日）", ["日期", "收盘价"], rows,
                      note="新浪期货主力连续，作为主要产品价格的代理指标，非公司实际售价")
            if chg20 is not None and chg20 > 5:
                res.sig(f"{label} 近 20 日上涨 {chg20:.1f}%，上游价格上行有利于产品售价与库存收益", "bullish", 2)
            elif chg20 is not None and chg20 < -5:
                res.sig(f"{label} 近 20 日下跌 {abs(chg20):.1f}%，产品价格承压", "bearish", 2)
    if not pick or "futures" not in res.details:
        hint = PRODUCT_HINTS.get(industry, "该行业无连续期货合约，建议跟踪公司季度毛利率与行业月度报价")
        res.add("产品价格跟踪", "无公开连续报价源", "warn", hint)
        price_note = hint

    res.score = round(max(0.0, min(MAX_SCORE, s_news + s_ann + s_research + s_lhb)), 2)

    n_bull = sum(1 for s in res.signals if s.direction == "bullish")
    n_bear = sum(1 for s in res.signals if s.direction == "bearish")
    res.headline = (f"新闻 {len(news)} 条、公告 {len(anns)} 条（近 120 天）、"
                    f"研报 {n_report} 篇（近 180 天）；"
                    f"正向信号 {n_bull} 条 / 负向信号 {n_bear} 条"
                    + (f"；{price_note}" if price_note else ""))
    return res


def _short_source(src) -> str:
    s = str(src or "").strip()
    return s[:8] if s else "—"
