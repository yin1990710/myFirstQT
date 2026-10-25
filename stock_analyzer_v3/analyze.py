#!/usr/bin/env python3
"""个股多维度分析 —— 命令行入口。

用法
----
    python analyze.py 688213                # 分析思特威
    python analyze.py 600707 --industry 光学光电子
    python analyze.py 002129 --no-html      # 只出控制台 + Markdown
    python analyze.py 688213 --out ./output # 指定输出目录

五个维度：基本面 / 技术面 / 行业板块资金 / 同板块龙头 / 消息面与近期大事
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import traceback

from analyzer import datasource as ds
from analyzer import report as rp
from analyzer.config import WEIGHTS
from analyzer.modules import fundamental, leader, news, sector, technical
from analyzer.scoring import aggregate, verdict
from analyzer.utils import CACHE, fmt_pct, guess_market_label, to_num


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="个股多维度分析（基本面/技术面/行业资金/板块地位/消息面）")
    p.add_argument("code", help="6 位股票代码，如 688213")
    p.add_argument("--industry", help="手动指定同花顺行业板块名（覆盖自动识别）")
    p.add_argument("--out", default="output", help="输出目录，默认 output")
    p.add_argument("--no-html", action="store_true", help="不生成 HTML")
    p.add_argument("--no-md", action="store_true", help="不生成 Markdown")
    p.add_argument("--json", action="store_true", help="额外输出 JSON 结果")
    p.add_argument("--no-cache", action="store_true", help="不使用缓存（重新抓取全部数据）")
    p.add_argument("--quiet", action="store_true", help="不打印控制台报告")
    p.add_argument("--days", type=int, default=420, help="日线回溯天数，默认 420")
    return p.parse_args(argv)


def log(msg: str) -> None:
    print(f"[*] {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    code = str(args.code).strip().zfill(6)

    if args.no_cache:
        n = CACHE.clear()
        log(f"已清空缓存 {n} 个文件")

    t0 = dt.datetime.now()
    log(f"开始分析 {code}（{guess_market_label(code)}）")

    # ---------------------------------------------------------------- 行情
    log("拉取实时行情…")
    q = ds.quote(code)
    if not q:
        print(f"❌ 未能获取 {code} 的行情数据，请检查代码是否正确或网络是否可用。", file=sys.stderr)
        return 1
    name = q.get("name") or code
    log(f"{name} 最新价 {q.get('price')}（{fmt_pct(q.get('change_pct'), sign=True)}）")

    log("拉取日线数据…")
    kline = ds.kline_daily(code, days=args.days)

    # ---------------------------------------------------------------- 行业
    log("识别所属行业板块…")
    if args.industry:
        ilist = ds.industry_list()
        bcode = ""
        if not ilist.empty:
            hit = ilist[ilist["name"] == args.industry]
            if not hit.empty:
                bcode = str(hit.iloc[0]["code"])
        cons = ds.industry_constituents(bcode, args.industry)
        ind = {"industry": args.industry, "board_code": bcode, "verified": not cons.empty,
               "em2016": ds.company_survey(code).get("em2016", ""), "constituents": cons}
    else:
        ind = ds.resolve_industry(code)
        cons = ind.get("constituents")
    industry = ind["industry"]
    import pandas as pd
    if cons is None or (isinstance(cons, pd.DataFrame) and cons.empty):
        cons = ds.industry_constituents(ind.get("board_code", ""), industry)
    if cons is None:
        cons = pd.DataFrame()
    log(f"行业：{industry}（{'已用成分股校验' if ind.get('verified') else '未校验，可能为近似匹配'}），"
        f"成分股 {len(cons)} 只")

    # 行业中位 PE（供基本面估值比较）
    industry_pe = None
    if not cons.empty and "pe" in cons.columns:
        pes = cons["pe"].dropna()
        pes = pes[pes > 0]
        if not pes.empty:
            industry_pe = float(pes.median())

    # ---------------------------------------------------------------- 五维分析
    results = []
    steps = [
        ("基本面", lambda: fundamental.analyze(code, q, industry_pe)),
        ("技术面", lambda: technical.analyze(code, kline, ds.industry_index_hist(industry), q)),
        ("行业资金", lambda: sector.analyze(code, industry, cons, kline, ind.get("boards"))),
        ("同板块龙头", lambda: leader.analyze(code, industry, cons)),
        ("消息面", lambda: news.analyze(code, industry, name, (ind.get("em2016") or ""))),
    ]
    for label, fn in steps:
        log(f"分析：{label}…")
        try:
            results.append(fn())
        except Exception as e:      # noqa: BLE001 单模块失败不应中断整份报告
            log(f"  !! {label} 分析异常：{type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)
            from analyzer.modules.base import ModuleResult
            key = {"基本面": "fundamental", "技术面": "technical", "行业资金": "sector",
                   "同板块龙头": "leader", "消息面": "news"}[label]
            r = ModuleResult(key=key, name=label, max_score=WEIGHTS[key])
            r.available = False
            r.headline = f"模块异常：{type(e).__name__}"
            results.append(r)

    agg = aggregate(results)
    vd = verdict(agg, results)

    rep = {
        "meta": {
            "code": code,
            "name": name,
            "industry": industry,
            "em2016": ind.get("em2016", ""),
            "industry_verified": ind.get("verified", False),
            "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market": guess_market_label(code),
            "elapsed": round((dt.datetime.now() - t0).total_seconds(), 1),
        },
        "quote": {k: (None if v is None else v) for k, v in q.items()},
        "kline": results[1].details.get("df") if results[1].available else None,
        "agg": agg,
        "modules": results,
        "verdict": vd,
        "industry_pe": industry_pe,
    }

    # ---------------------------------------------------------------- 输出
    if not args.quiet:
        print(rp.render_console(rep))

    os.makedirs(args.out, exist_ok=True)
    stem = f"{code}_{name}_{dt.datetime.now():%Y%m%d_%H%M}"
    written = []
    if not args.no_md:
        path = os.path.join(args.out, f"{stem}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(rp.render_markdown(rep))
        written.append(path)
    if not args.no_html:
        path = os.path.join(args.out, f"{stem}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(rp.render_html(rep))
        written.append(path)
    if args.json:
        path = os.path.join(args.out, f"{stem}.json")
        payload = {
            "meta": rep["meta"], "quote": rep["quote"], "score": agg["total"],
            "rating": agg["rating"], "verdict": vd,
            "dimensions": [{k: r[k] for k in ("key", "name", "score", "max", "ratio", "grade")} for r in agg["rows"]],
            "signals": [{"text": s.text, "direction": s.direction} for r in results for s in r.signals],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        written.append(path)

    print(file=sys.stderr)
    log(f"完成，用时 {rep['meta']['elapsed']}s，综合评分 {agg['total']}（{agg['rating']}）")
    for p in written:
        print(f"  -> {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
