"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path

from . import datasource as ds
from . import engines as E
from . import report as rp


def _check_js(path: Path) -> bool:
    """校验 HTML 内联 JS 语法。返回 True 表示通过或跳过。"""
    html = path.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", html, re.S)
    if not scripts:
        return True
    tmp = path.with_suffix("._check.js")
    tmp.write_text("\n".join(scripts), encoding="utf-8")
    try:
        import subprocess
        r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"✗ JS 语法校验失败：\n{r.stderr[:500]}", file=sys.stderr)
            return False
        print("✓ JS 语法校验通过")
        return True
    except FileNotFoundError:
        print("· 未找到 node，跳过 JS 校验")
        return True
    finally:
        tmp.unlink(missing_ok=True)


def _out_name(code: str, name: str, date: str) -> str:
    d = re.sub(r"[^0-9]", "", date)[:8] or "latest"
    safe = re.sub(r'[\\/:*?"<>|]', "", name or code)
    return f"{safe}-{code}-{d}.html"


def analyze_one(code: str, *, days: int = 30, kline_bars: int = 250) -> tuple[Path, str]:
    """分析单只标的，返回 (输出路径, HTML内容)。"""
    b = ds.load(code, days=days, kline_bars=kline_bars)
    name = (b.quote.name if b.quote and b.quote.name else "") or b.profile.get("name", "")
    date = (b.quote.date if b.quote and b.quote.date else "") or ""

    if b.errors and not b.quote:
        raise ds.DataError(
            f"{code} 核心数据（行情/K线）获取失败，无法生成报告：{b.errors[0]}"
        )

    out = Path.cwd() / _out_name(code, name, date)
    html = rp.render(b)
    out.write_text(html, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  {name}（{code}）  数据截至 {date}")
    print(f"{'='*60}")
    if b.quote:
        q = b.quote
        print(f"  收盘 {q.price:.2f} 元（{q.change_pct:+.2f}%），"
              f"成交额 {q.amount/1e8:.2f} 亿，量比 {q.volume_ratio or 0:.2f}")
    if b.bars and b.quote:
        st = E.analyze_structure(b.bars, b.quote.price)
        print(f"  趋势：{st.trend}　形态：{st.pattern or '未识别到明确形态'}")
        if st.target:
            print(f"  形态目标位：{st.target:.2f} 元")
        votes = E.build_votes(b)
        bull = sum(1 for v in votes if v.score > 0)
        bear = sum(1 for v in votes if v.score < 0)
        print(f"  信号投票：偏多 {bull} / 偏空 {bear} / 中性 {len(votes)-bull-bear}")
        for v in votes:
            mark = "多" if v.score > 0 else ("空" if v.score < 0 else "中")
            print(f"    [{mark}] {v.system:<12} {v.signal:<10} {v.detail[:60]}")
    if b.errors:
        print(f"\n  数据缺口 {len(b.errors)} 项：")
        for e in b.errors:
            print(f"    - {e}")
    print(f"\n  报告已生成：{out}")
    _check_js(out)
    return out, html


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="stock_analyzer",
        description="通用 A 股走势分析报告生成器（输入股票代码即可）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python -m stock_analyzer 688213
  python -m stock_analyzer 688213.SH -o out.html
  python -m stock_analyzer sh600519 sz300750 --open
  python -m stock_analyzer 300750 --json          # 只输出结构化结论
  python -m stock_analyzer 002594 --days 60      # 拉取更长资金/两融窗口""",
    )
    p.add_argument("codes", nargs="+", help="股票代码，支持 688213 / 688213.SH / sh688213")
    p.add_argument("-o", "--output", help="输出文件路径（仅单标的时有效）")
    p.add_argument("-d", "--output-dir", help="输出目录（多标的时生效）")
    p.add_argument("--days", type=int, default=30, help="资金流/两融回溯天数，默认 30")
    p.add_argument("--bars", type=int, default=250, help="日K 拉取根数，默认 250")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结构化结论，不生成 HTML")
    p.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    p.add_argument("--no-check", action="store_true", help="跳过 JS 语法校验")
    args = p.parse_args(argv)

    results = []
    for raw in args.codes:
        try:
            code = ds.normalize_code(raw)
        except ds.DataError as e:
            print(f"✗ {e}", file=sys.stderr)
            continue

        try:
            b = ds.load(code, days=args.days, kline_bars=args.bars)
        except ds.DataError as e:
            print(f"✗ {raw} 数据获取失败：{e}", file=sys.stderr)
            continue

        # 行情与 K 线全缺 → 视为无效标的，不生成空报告
        if not b.quote and not b.bars:
            print(f"✗ {raw}（{code}）未获取到行情与 K 线数据，"
                  f"请确认代码是否为有效 A 股/港股代码。已跳过。", file=sys.stderr)
            continue

        if args.json:
            results.append(_json_summary(b))
            print(json.dumps(results[-1], ensure_ascii=False, indent=2))
            continue

        name = (b.quote.name if b.quote and b.quote.name else "") or b.profile.get("name", "")
        date = (b.quote.date if b.quote and b.quote.date else "") or ""
        if args.output:
            out = Path(args.output)
        elif args.output_dir:
            d = Path(args.output_dir)
            d.mkdir(parents=True, exist_ok=True)
            out = d / _out_name(code, name, date)
        else:
            out = Path.cwd() / _out_name(code, name, date)

        out.write_text(rp.render(b), encoding="utf-8")
        _print_summary(b, out)
        if not args.no_check:
            _check_js(out)
        if args.open:
            webbrowser.open(out.resolve().as_uri())
        results.append(out)

    if args.json and results:
        return 0
    print(f"\n共生成 {len(results)} 份报告。")
    return 0 if results else 1


def _print_summary(b: ds.Bundle, out: Path) -> None:
    print(f"\n{'='*60}")
    name = (b.quote.name if b.quote and b.quote.name else "") or b.profile.get("name", "")
    date = (b.quote.date if b.quote and b.quote.date else "") or "最新"
    print(f"  {name}（{b.code}）  数据截至 {date}")
    print(f"{'='*60}")
    if b.quote:
        q = b.quote
        print(f"  收盘 {q.price:.2f} 元（{q.change_pct:+.2f}%），成交额 "
              f"{q.amount/1e8:.2f} 亿，量比 {q.volume_ratio or 0:.2f}")
    if b.bars and b.quote and b.quote.price:
        st = E.analyze_structure(b.bars, b.quote.price)
        print(f"  趋势：{st.trend}　形态：{st.pattern or '未识别到明确形态'}")
        if st.target:
            print(f"  形态目标位：{st.target:.2f} 元")
        votes = E.build_votes(b)
        bull = sum(1 for v in votes if v.score > 0)
        bear = sum(1 for v in votes if v.score < 0)
        print(f"  信号投票：偏多 {bull} / 偏空 {bear} / 中性 {len(votes)-bull-bear}")
        for v in votes:
            mark = "多" if v.score > 0 else ("空" if v.score < 0 else "中")
            print(f"    [{mark}] {v.system:<14} {v.signal:<10} {v.detail[:58]}")
    if b.errors:
        print(f"\n  数据缺口 {len(b.errors)} 项：")
        for e in b.errors:
            print(f"    - {e}")
    print(f"\n  报告：{out}")


def _json_summary(b: ds.Bundle) -> dict:
    out: dict = {"code": b.code, "name": "", "date": "", "quote": {}, "structure": {},
                 "votes": [], "errors": b.errors}
    if b.quote:
        q = b.quote
        out["name"] = q.name
        out["date"] = q.date
        out["quote"] = {
            "price": q.price, "change_pct": q.change_pct, "amount": q.amount,
            "volume_ratio": q.volume_ratio, "turnover_rate": q.turnover_rate,
            "pe_ttm": q.pe_ttm, "pb": q.pb,
            "total_mcap": q.total_mcap, "high_52w": q.high_52w, "low_52w": q.low_52w,
        }
    if b.bars and b.quote and b.quote.price:
        st = E.analyze_structure(b.bars, b.quote.price)
        out["structure"] = {
            "trend": st.trend, "trend_detail": st.trend_detail,
            "pattern": st.pattern, "pattern_detail": st.pattern_detail,
            "target": st.target, "fib": st.fib,
            "rebound_pct": st.rebound_pct,
            "levels": [{"price": x.price, "label": x.label, "kind": x.kind,
                        "dist_pct": x.dist_pct} for x in st.levels[:12]],
        }
        out["votes"] = [{"system": v.system, "signal": v.signal,
                         "score": v.score, "detail": v.detail} for v in E.build_votes(b)]
    out["consensus"] = b.consensus
    out["score"] = b.score
    out["chip"] = b.chip
    return out


if __name__ == "__main__":
    raise SystemExit(main())