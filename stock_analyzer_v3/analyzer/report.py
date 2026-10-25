"""报告渲染：控制台文本 / Markdown / 单文件 HTML（含自绘价格走势 SVG）。"""
from __future__ import annotations

import datetime as dt
import html as html_lib
import unicodedata

import pandas as pd

from .config import DIMENSION_LABELS
from .modules.base import ModuleResult

TONE_MARK = {"good": "✅", "bad": "⚠️", "warn": "⚡", "neutral": "·"}


# ------------------------------------------------------------------ 工具
def _w(s: str) -> int:
    """按终端显示宽度计算字符串宽度（中日韩字符算 2）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s: str, width: int, align: str = "l") -> str:
    s = str(s)
    gap = max(0, width - _w(s))
    if align == "r":
        return " " * gap + s
    if align == "c":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def _table_text(headers: list[str], rows: list[list[str]], indent: str = "  ") -> str:
    if not rows:
        return ""
    widths = [_w(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], _w(c))
    sep = indent + "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    top = indent + "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    bot = indent + "└" + "┴".join("─" * (w + 2) for w in widths) + "┘"
    lines = [top]
    lines.append(indent + "│ " + " │ ".join(_pad(h, widths[i], "c") for i, h in enumerate(headers)) + " │")
    lines.append(sep)
    for r in rows:
        cells = [_pad(c, widths[i], "r" if i > 0 and _is_num(c) else "l") for i, c in enumerate(r[:len(widths)])]
        lines.append(indent + "│ " + " │ ".join(cells) + " │")
    lines.append(bot)
    return "\n".join(lines)


def _is_num(s: str) -> bool:
    t = str(s).replace(",", "").replace("%", "").replace("+", "").replace("亿", "").replace("万", "")
    try:
        float(t)
        return True
    except ValueError:
        return False


def _bar(ratio: float, width: int = 20, char: str = "█") -> str:
    n = int(round(ratio * width))
    return char * n + "·" * (width - n)


# ------------------------------------------------------------------ 控制台
def render_console(rep: dict) -> str:
    meta, agg, res_list, vd = rep["meta"], rep["agg"], rep["modules"], rep["verdict"]
    L = []
    bar = "═" * 78
    L.append(bar)
    L.append(f"  {meta['name']}（{meta['code']}） 个股多维度分析报告")
    L.append(f"  生成时间：{meta['time']}    数据源：腾讯/新浪/同花顺/东方财富（akshare）")
    L.append(bar)

    q = rep["quote"]
    if q:
        L.append(f"  最新价 {q.get('price'):.2f}   涨跌 {q.get('change_pct'):+.2f}%   "
                 f"换手 {q.get('turnover'):.2f}%   PE(TTM) {q.get('pe_ttm') or 0:.1f}   "
                 f"PB {q.get('pb') or 0:.2f}")
        L.append(f"  总市值 {q.get('total_mv', 0) / 1e8:.1f}亿   流通市值 {q.get('float_mv', 0) / 1e8:.1f}亿   "
                 f"所属行业 {meta['industry']}（{meta['em2016']}）")
    L.append("")

    # 总评分
    L.append("─" * 78)
    L.append(f"  综合评分  {agg['total']:.1f} / 100     评级  {agg['rating']}（{agg['rating_desc']}）")
    L.append(f"  操作建议  {vd['stance']}    风险等级  {vd['risk_level']}    W23 形态  {vd['w23_stage']}")
    L.append("─" * 78)
    L.append(f"  {'维度':<10}{'得分':>12}{'权重':>8}   强度")
    for r in agg["rows"]:
        if not r["available"]:
            L.append(f"  {r['name']:<10}{'—':>12}{'—':>8}   (数据缺失，已剔除加权)")
            continue
        L.append(f"  {r['name']:<10}{r['score']:>6.1f}/{r['max']:<5.0f}{r['eff_weight']:>7.1f}%   "
                 f"{_bar(r['ratio'] / 100, 22)} {r['ratio']:>5.1f}%  {r['grade']}")
    L.append("")

    for i, m in enumerate(res_list, 1):
        L.append("─" * 78)
        head = f"  {i}. {m.name}【{m.score:.1f}/{m.max_score:.0f}】"
        L.append(head + " " * max(0, 74 - _w(head)) + f" {m.grade}")
        L.append(f"  {m.headline}")
        L.append("")
        for mt in m.metrics:
            note = f"  ({mt.note})" if mt.note else ""
            L.append(f"    {TONE_MARK.get(mt.tone, '·')} {_pad(mt.label, 24)}{mt.value}{note}")
        if m.signals:
            L.append("")
            for s in m.signals:
                arrow = {"bullish": "▲", "bearish": "▼"}.get(s.direction, "·")
                L.append(f"    {arrow} {s.text}")
        for tb in m.tables:
            L.append("")
            L.append(f"    【{tb['title']}】")
            L.append(_table_text(tb["headers"], tb["rows"], indent="    "))
            if tb.get("note"):
                L.append(f"    注：{tb['note']}")
        L.append("")

    L.append("═" * 78)
    L.append("  核心结论")
    L.append("─" * 78)
    if agg["bullish"]:
        L.append("  正向因素：")
        for s in agg["bullish"][:6]:
            L.append(f"    ▲ {s.text}")
    if agg["bearish"]:
        L.append("  负向因素：")
        for s in agg["bearish"][:6]:
            L.append(f"    ▼ {s.text}")
    L.append("")
    L.append("  操作策略：")
    for line in _wrap(vd["action"], 70):
        L.append(f"    {line}")
    if vd["risks"]:
        L.append("")
        L.append("  主要风险：")
        for r in vd["risks"]:
            L.append(f"    ! {r}")
    L.append("")
    L.append("  强势维度：" + "、".join(f"{r['name']}({r['ratio']:.0f}%)" for r in agg["strong"]))
    if agg["weak"]:
        L.append("  短板维度：" + "、".join(f"{r['name']}({r['ratio']:.0f}%)" for r in agg["weak"]))
    L.append(bar)
    L.append("  免责声明：本报告由程序按固定规则自动生成，仅用于研究框架验证，")
    L.append("            不构成任何投资建议。数据来自公开接口，可能存在延迟或口径差异。")
    L.append(bar)
    return "\n".join(L)


def _wrap(text: str, width: int) -> list[str]:
    lines, cur, cur_w = [], "", 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if cur_w + w > width:
            lines.append(cur)
            cur, cur_w = ch, w
        else:
            cur += ch
            cur_w += w
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ Markdown
def render_markdown(rep: dict) -> str:
    meta, agg, res_list, vd = rep["meta"], rep["agg"], rep["modules"], rep["verdict"]
    q = rep["quote"]
    L = [f"# {meta['name']}（{meta['code']}）个股多维度分析", ""]
    L.append(f"> 生成时间：{meta['time']}　|　所属行业：{meta['industry']}（{meta['em2016']}）")
    L.append("")
    L.append("## 一、结论速览")
    L.append("")
    L.append(f"- **综合评分**：{agg['total']:.1f} / 100　**评级**：{agg['rating']}（{agg['rating_desc']}）")
    L.append(f"- **操作建议**：{vd['stance']}　**风险等级**：{vd['risk_level']}　**W23 形态**：{vd['w23_stage']}")
    if q:
        L.append(f"- **价格**：{q.get('price'):.2f}（{q.get('change_pct'):+.2f}%）　"
                 f"PE(TTM)：{q.get('pe_ttm') or 0:.1f}　PB：{q.get('pb') or 0:.2f}　"
                 f"总市值：{q.get('total_mv', 0) / 1e8:.1f} 亿")
    L.append("")
    L.append("### 维度评分")
    L.append("")
    L.append("| 维度 | 得分 | 满分 | 实际权重 | 得分率 | 评价 |")
    L.append("|---|---:|---:|---:|---:|---|")
    for r in agg["rows"]:
        if r["available"]:
            L.append(f"| {r['name']} | {r['score']:.1f} | {r['max']:.0f} | {r['eff_weight']:.1f}% | {r['ratio']:.1f}% | {r['grade']} |")
        else:
            L.append(f"| {r['name']} | — | — | — | — | 数据缺失 |")
    L.append("")

    sections = ["二、基本面分析", "三、技术面分析", "四、行业板块资金流入", "五、同板块龙头情况", "六、消息面与近期大事"]
    for idx, m in enumerate(res_list):
        title = sections[idx] if idx < len(sections) else f"{idx + 2}. {m.name}"
        L.append(f"## {title}")
        L.append("")
        L.append(f"**得分**：{m.score:.1f} / {m.max_score:.0f}（{m.grade}）")
        L.append("")
        if m.headline:
            L.append(f"> {m.headline}")
            L.append("")
        if m.metrics:
            L.append("| 指标 | 数值 | 备注 |")
            L.append("|---|---|---|")
            for mt in m.metrics:
                L.append(f"| {mt.label} | {mt.value} | {mt.note or '—'} |")
            L.append("")
        if m.signals:
            L.append("**要点**：")
            L.append("")
            for s in m.signals:
                mark = {"bullish": "🟥", "bearish": "🟩"}.get(s.direction, "⬜")
                L.append(f"- {mark} {s.text}")
            L.append("")
        for tb in m.tables:
            L.append(f"**{tb['title']}**")
            L.append("")
            L.append("| " + " | ".join(tb["headers"]) + " |")
            L.append("|" + "|".join(["---"] * len(tb["headers"])) + "|")
            for r in tb["rows"]:
                L.append("| " + " | ".join(str(c) for c in r) + " |")
            L.append("")
            if tb.get("note"):
                L.append(f"*{tb['note']}*")
                L.append("")

    L.append("## 七、综合结论与操作策略")
    L.append("")
    if agg["bullish"]:
        L.append("**正向因素**")
        L.append("")
        for s in agg["bullish"][:8]:
            L.append(f"- {s.text}")
        L.append("")
    if agg["bearish"]:
        L.append("**负向因素**")
        L.append("")
        for s in agg["bearish"][:8]:
            L.append(f"- {s.text}")
        L.append("")
    L.append(f"**操作策略**：{vd['action']}")
    L.append("")
    if vd["risks"]:
        L.append("**主要风险**")
        L.append("")
        for r in vd["risks"]:
            L.append(f"- {r}")
        L.append("")
    L.append("---")
    L.append("")
    L.append("> 免责声明：本报告由程序按固定规则自动生成，仅用于研究框架验证，不构成投资建议。")
    L.append("> 数据来源：腾讯财经、新浪财经、同花顺、东方财富（经 akshare 获取），可能存在延迟。")
    return "\n".join(L)


# ------------------------------------------------------------------ SVG 图
def _svg_chart(df: pd.DataFrame, width: int = 860, height: int = 300) -> str:
    """价格 + MA20/MA30 折线 + 成交量柱，纯 SVG 无依赖。"""
    if df is None or df.empty or len(df) < 30:
        return ""
    d = df.tail(120).reset_index(drop=True)
    ml, mr, mt, mb = 8, 46, 12, 46
    pw, ph = width - ml - mr, height - mt - mb
    vol_h = 46
    price_h = ph - vol_h - 8

    lo = float(min(d["low"].min(), d["ma20"].min() if "ma20" in d else d["low"].min()))
    hi = float(max(d["high"].max(), d["ma20"].max() if "ma20" in d else d["high"].max()))
    rng = max(hi - lo, 1e-6)
    n = len(d)

    def X(i: int) -> float:
        return ml + (i / max(n - 1, 1)) * pw

    def Y(v: float) -> float:
        return mt + (1 - (v - lo) / rng) * price_h

    vmax = float(d["volume"].max()) or 1.0
    up_color, down_color = "#d64545", "#2e9e5b"     # A股习惯：涨红跌绿
    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
             f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fbfbfd"/>')
    # 水平网格
    for k in range(5):
        y = mt + price_h * k / 4
        val = hi - rng * k / 4
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" stroke="#e8e8ef" stroke-width="1"/>')
        parts.append(f'<text x="{ml + pw + 5}" y="{y + 3.5:.1f}" font-size="10" fill="#8a8a99">{val:.1f}</text>')
    # 成交量
    bw = max(1.2, pw / n * 0.62)
    for i, r in d.iterrows():
        h = (float(r["volume"]) / vmax) * vol_h
        x = X(i) - bw / 2
        y = mt + price_h + 8 + (vol_h - h)
        c = up_color if r["close"] >= r["open"] else down_color
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{c}" opacity="0.35"/>')
    # 折线
    def poly(col: str, color: str, w: float, dash: str = "") -> str:
        if col not in d:
            return ""
        pts = " ".join(f"{X(i):.1f},{Y(float(v)):.1f}" for i, v in d[col].items() if pd.notna(v))
        if not pts:
            return ""
        da = f' stroke-dasharray="{dash}"' if dash else ""
        return (f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{w}" '
                f'stroke-linejoin="round"{da}/>')

    parts.append(poly("close", "#1f6feb", 1.8))
    parts.append(poly("ma20", "#e08b1a", 1.2, "4 3"))
    parts.append(poly("ma30", "#7c4dff", 1.2, "2 3"))
    # 首尾标注
    last = d.iloc[-1]
    parts.append(f'<circle cx="{X(n - 1):.1f}" cy="{Y(float(last["close"])):.1f}" r="3" fill="#1f6feb"/>')
    parts.append(f'<text x="{ml}" y="{height - 8}" font-size="10" fill="#8a8a99">'
                 f'{d["date"].iloc[0]:%Y-%m-%d}</text>')
    parts.append(f'<text x="{ml + pw - 60}" y="{height - 8}" font-size="10" fill="#8a8a99">'
                 f'{d["date"].iloc[-1]:%Y-%m-%d}</text>')
    # 图例
    lx = ml + 6
    for label, color, dash in (("收盘价", "#1f6feb", ""), ("MA20", "#e08b1a", "4 3"), ("MA30", "#7c4dff", "2 3")):
        parts.append(f'<line x1="{lx}" y1="6" x2="{lx + 14}" y2="6" stroke="{color}" stroke-width="2"'
                     + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")
        parts.append(f'<text x="{lx + 18}" y="9.5" font-size="10" fill="#55556a">{label}</text>')
        lx += 74
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------------ HTML
def render_html(rep: dict) -> str:
    meta, agg, res_list, vd = rep["meta"], rep["agg"], rep["modules"], rep["verdict"]
    q = rep["quote"]
    esc = html_lib.escape
    df = rep.get("kline")
    chart = _svg_chart(df) if df is not None else ""

    rating_color = {"A+": "#c0392b", "A": "#d35400", "B": "#b7950b", "C": "#5d6d7e", "D": "#1e7e34"}.get(agg["rating"], "#333")

    css = """
:root{--bg:#f6f7fb;--card:#fff;--line:#e6e8f0;--txt:#1c1c28;--sub:#6b6b80;
--red:#d64545;--green:#2e9e5b;--blue:#1f6feb;--amber:#e08b1a;--violet:#7c4dff;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
line-height:1.65;padding:28px 18px 60px}
.wrap{max-width:1080px;margin:0 auto}
.hero{background:linear-gradient(135deg,#fff 0%,#f0f4ff 100%);border:1px solid var(--line);border-radius:16px;
padding:26px 28px;margin-bottom:18px;box-shadow:0 2px 12px rgba(28,28,40,.05)}
.hero h1{font-size:26px;letter-spacing:-.3px}
.hero .sub{color:var(--sub);font-size:13px;margin-top:6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-top:18px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .k{font-size:11px;color:var(--sub);letter-spacing:.4px}
.kpi .v{font-size:19px;font-weight:650;margin-top:3px;font-variant-numeric:tabular-nums}
.rating{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-top:20px;padding-top:18px;border-top:1px dashed var(--line)}
.badge{font-size:34px;font-weight:800;line-height:1}
.badge small{font-size:13px;font-weight:500;color:var(--sub);margin-left:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-bottom:16px;
box-shadow:0 1px 6px rgba(28,28,40,.04)}
.card h2{font-size:17px;margin-bottom:4px;display:flex;align-items:center;gap:9px}
.card h2 .idx{display:inline-flex;width:24px;height:24px;border-radius:7px;background:#eef2ff;color:var(--blue);
align-items:center;justify-content:center;font-size:12px;font-weight:700}
.card .head{color:var(--sub);font-size:13px;margin:6px 0 16px;padding-left:33px}
.scoreline{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--sub);margin-bottom:2px}
.scoreline b{color:var(--txt);font-size:14px}
.bar{height:7px;background:#eef0f6;border-radius:4px;overflow:hidden;flex:1}
.bar i{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,#5b8def,#1f6feb)}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 4px}
th{text-align:left;color:var(--sub);font-weight:600;font-size:11.5px;letter-spacing:.3px;
padding:8px 10px;border-bottom:1px solid var(--line);background:#fafbff}
td{padding:8px 10px;border-bottom:1px solid #f1f2f7;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right}
.metric{display:grid;grid-template-columns:170px 1fr;gap:2px 14px;padding:7px 0;border-bottom:1px solid #f4f5fa;font-size:13.5px}
.metric:last-child{border-bottom:none}
.metric .l{color:var(--sub)}
.metric .note{grid-column:2;color:#9a9aad;font-size:11.5px;margin-top:-1px}
.good{color:var(--red);font-weight:600}
.bad{color:var(--green);font-weight:600}
.warn{color:var(--amber);font-weight:600}
.sig{font-size:13.5px;padding:7px 0 7px 24px;position:relative;border-bottom:1px dashed #f1f2f7}
.sig:before{position:absolute;left:2px;top:7px;font-size:11px}
.sig.bullish:before{content:"▲";color:var(--red)}
.sig.bearish:before{content:"▼";color:var(--green)}
.sig.neutral:before{content:"·";color:var(--sub)}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;background:#eef2ff;color:#3b5bdb;margin-right:6px}
.stance{display:inline-block;padding:5px 14px;border-radius:8px;background:#fff4e6;color:#b35c00;font-weight:700;font-size:15px}
.risk{background:#fff8f8;border-left:3px solid var(--red);padding:10px 14px;border-radius:6px;font-size:13px;margin:6px 0}
.disc{color:#9a9aad;font-size:11.5px;line-height:1.7;margin-top:22px;padding-top:14px;border-top:1px solid var(--line)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 28px}
@media(max-width:720px){.grid2{grid-template-columns:1fr}.metric{grid-template-columns:1fr}}
"""

    h = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<title>{esc(meta['name'])} 个股分析</title><style>{css}</style></head><body><div class='wrap'>"]

    # Hero
    h.append(f"<div class='hero'><h1>{esc(meta['name'])} <span style='color:#8a8a99;font-size:18px'>{esc(meta['code'])}</span></h1>")
    h.append(f"<div class='sub'>{esc(meta['industry'])} · {esc(meta['em2016'])} · 生成于 {esc(meta['time'])}</div>")
    if q:
        kpis = [
            ("最新价", f"{q.get('price', 0):.2f}", q.get("change_pct")),
            ("PE(TTM)", f"{q.get('pe_ttm') or 0:.1f}", None),
            ("PB", f"{q.get('pb') or 0:.2f}", None),
            ("总市值", f"{q.get('total_mv', 0) / 1e8:.0f}亿", None),
            ("换手率", f"{q.get('turnover') or 0:.2f}%", None),
            ("量比", f"{q.get('vol_ratio') or 0:.2f}", None),
        ]
        h.append("<div class='kpis'>")
        for k, v, chg in kpis:
            color = ""
            extra = ""
            if chg is not None:
                color = "color:var(--red)" if chg > 0 else "color:var(--green)"
                extra = f"<span style='font-size:12px;{color};margin-left:5px'>{chg:+.2f}%</span>"
            h.append(f"<div class='kpi'><div class='k'>{k}</div><div class='v' style='{color}'>{v}{extra}</div></div>")
        h.append("</div>")

    h.append("<div class='rating'>")
    h.append(f"<div class='badge' style='color:{rating_color}'>{agg['rating']}<small>{agg['total']:.1f} 分</small></div>")
    h.append(f"<div><div style='font-size:12px;color:var(--sub)'>操作建议</div>"
             f"<div class='stance'>{esc(vd['stance'])}</div></div>")
    h.append(f"<div><div style='font-size:12px;color:var(--sub)'>风险等级</div>"
             f"<div style='font-size:15px;font-weight:700'>{esc(vd['risk_level'])}</div></div>")
    h.append(f"<div><div style='font-size:12px;color:var(--sub)'>W23 形态</div>"
             f"<div style='font-size:15px;font-weight:700'>{esc(vd['w23_stage'])}</div></div>")
    h.append("</div></div>")

    # 评分总表
    h.append("<div class='card'><h2><span class='idx'>∑</span>五维评分</h2>")
    h.append("<div style='margin-top:14px'>")
    for r in agg["rows"]:
        if r["available"]:
            h.append("<div class='scoreline'>"
                     f"<span style='width:74px'>{esc(r['name'])}</span>"
                     f"<div class='bar'><i style='width:{r['ratio']:.1f}%'></i></div>"
                     f"<b style='width:96px;text-align:right'>{r['score']:.1f}/{r['max']:.0f}</b>"
                     f"<span style='width:52px;text-align:right'>{r['ratio']:.0f}%</span>"
                     f"<span class='tag'>{r['grade']}</span></div>")
        else:
            h.append(f"<div class='scoreline'><span style='width:74px'>{esc(r['name'])}</span>"
                     "<div class='bar'><i style='width:0'></i></div>"
                     "<b style='width:96px;text-align:right'>—</b>"
                     "<span style='width:52px;text-align:right'>缺数据</span></div>")
    h.append("</div></div>")

    # 走势图
    if chart:
        h.append("<div class='card'><h2><span class='idx'>〰</span>近 120 日走势</h2>"
                 "<div class='head'>收盘价与 MA20 / MA30，下方为成交量（红涨绿跌）</div>"
                 f"<div style='margin-top:8px'>{chart}</div></div>")

    # 各维度
    for i, m in enumerate(res_list, 1):
        h.append(f"<div class='card'><h2><span class='idx'>{i}</span>{esc(m.name)}"
                 f"<span style='margin-left:auto;font-size:13px;color:var(--sub)'>"
                 f"{m.score:.1f} / {m.max_score:.0f}</span></h2>")
        if m.headline:
            h.append(f"<div class='head'>{esc(m.headline)}</div>")
        if m.metrics:
            h.append("<div class='grid2'>")
            half = (len(m.metrics) + 1) // 2
            for group in (m.metrics[:half], m.metrics[half:]):
                h.append("<div>")
                for mt in group:
                    tone = f" class='{mt.tone}'" if mt.tone in ("good", "bad", "warn") else ""
                    h.append(f"<div class='metric'><div class='l'>{esc(mt.label)}</div>"
                             f"<div{tone}>{esc(str(mt.value))}</div>")
                    if mt.note:
                        h.append(f"<div class='note'>{esc(mt.note)}</div>")
                    h.append("</div>")
                h.append("</div>")
            h.append("</div>")
        if m.signals:
            h.append("<div style='margin-top:14px'>")
            for s in m.signals:
                h.append(f"<div class='sig {s.direction}'>{esc(s.text)}</div>")
            h.append("</div>")
        for tb in m.tables:
            h.append(f"<div style='margin-top:16px'><div style='font-size:12.5px;font-weight:650;"
                     f"color:var(--sub);letter-spacing:.3px'>{esc(tb['title'])}</div>")
            h.append("<table><thead><tr>")
            for j, hd in enumerate(tb["headers"]):
                h.append(f"<th class='{'n' if j else ''}'>{esc(str(hd))}</th>")
            h.append("</tr></thead><tbody>")
            for row in tb["rows"]:
                h.append("<tr>")
                for j, c in enumerate(row):
                    cls = " class='n'" if j and _is_num(str(c)) else ""
                    txt = str(c)
                    style = ""
                    if txt.startswith("+") and _is_num(txt):
                        style = " style='color:var(--red)'"
                    elif txt.startswith("-") and _is_num(txt):
                        style = " style='color:var(--green)'"
                    h.append(f"<td{cls}{style}>{esc(txt)}</td>")
                h.append("</tr>")
            h.append("</tbody></table>")
            if tb.get("note"):
                h.append(f"<div style='font-size:11.5px;color:#9a9aad;margin-top:2px'>{esc(tb['note'])}</div>")
            h.append("</div>")
        h.append("</div>")

    # 结论
    h.append("<div class='card'><h2><span class='idx'>✓</span>综合结论与操作策略</h2>")
    h.append(f"<div style='margin-top:14px'><div class='head' style='padding-left:0'>{esc(vd['action'])}</div></div>")
    if agg["bullish"]:
        h.append("<div style='margin-top:10px;font-size:12.5px;font-weight:650;color:var(--sub)'>正向因素</div>")
        for s in agg["bullish"][:8]:
            h.append(f"<div class='sig bullish'>{esc(s.text)}</div>")
    if agg["bearish"]:
        h.append("<div style='margin-top:14px;font-size:12.5px;font-weight:650;color:var(--sub)'>负向因素</div>")
        for s in agg["bearish"][:8]:
            h.append(f"<div class='sig bearish'>{esc(s.text)}</div>")
    if vd["risks"]:
        h.append("<div style='margin-top:16px;font-size:12.5px;font-weight:650;color:var(--sub)'>主要风险</div>")
        for r in vd["risks"]:
            h.append(f"<div class='risk'>{esc(r)}</div>")
    h.append("</div>")

    h.append("<div class='disc'>免责声明：本报告由程序按固定规则自动生成，仅用于研究框架验证，不构成任何投资建议。"
             "数据来源：腾讯财经、新浪财经、同花顺、东方财富（经 akshare 获取），可能存在延迟、口径差异或抓取失败。"
             f"<br>生成时间：{esc(meta['time'])}</div>")
    h.append("</div></body></html>")
    return "".join(h)
