"""stock_analyzer_v2 命令行入口。

用法（可在项目根或本目录内执行）：
    python analyze.py 600577 --html ./reports   # 生成 HTML 报告到 ./reports
    python analyze.py 600577 300750 --html ./reports
    python analyze.py 600577                    # 仅输出文本报告
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # 在包目录内直接 python analyze.py 时补齐包导入路径
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from stock_analyzer_v2.analyzer import analyze
    from stock_analyzer_v2.report import save_html
else:
    from .analyzer import analyze
    from .report import save_html


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="analyze",
        description="A 股个股走势分析（stock_analyzer_v2 命令行入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python analyze.py 600577 --html ./reports",
    )
    p.add_argument("codes", nargs="+",
                   help="股票代码，支持 600577 / sh600577 / 600577.SH")
    p.add_argument("--html", metavar="OUTDIR", help="生成 HTML 报告到指定目录")
    p.add_argument("--bars", type=int, default=95, help="HTML 走势图K线根数，默认 95")
    args = p.parse_args(argv)

    ok = 0
    for raw in args.codes:
        try:
            res = analyze(raw)
        except Exception as e:
            print(f"✗ {raw} 分析失败：{e}", file=sys.stderr)
            continue
        print(res.to_text())
        if args.html:
            path = save_html(res, args.html, bars=args.bars)
            print(f"\n报告已生成：{path}")
        ok += 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
