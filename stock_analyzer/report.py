"""报告层：把数据包与分析结论渲染为 HTML 研报。"""

from __future__ import annotations

import html
import json
from typing import Any

from . import engines as E
from .datasource import Bundle

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:#f5f6f8;color:#1a1d24;line-height:1.7;padding:24px}
.wrap{max-width:1180px;margin:0 auto}
.hd{background:linear-gradient(135deg,#c0392b,#e74c3c);color:#fff;border-radius:10px;padding:26px 30px;margin-bottom:18px}
.hd h1{font-size:26px;font-weight:700;letter-spacing:.5px}
.hd .sub{opacity:.92;font-size:13px;margin-top:8px}
.tldr{background:#fff;border-radius:10px;padding:24px 28px;margin-bottom:18px;border-left:5px solid #c0392b;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.tldr h2{font-size:18px;color:#c0392b;margin-bottom:12px}
.tldr p{font-size:14.5px;margin-bottom:9px}
.card{background:#fff;border-radius:10px;padding:22px 26px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.card h2{font-size:17px;margin-bottom:14px;padding-bottom:9px;border-bottom:2px solid #f0f0f0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:14px 0}
.kpi{background:#fafbfc;border:1px solid #ecedf0;border-radius:8px;padding:13px 15px}
.kpi .l{font-size:12px;color:#8a8f99;margin-bottom:5px}
.kpi .v{font-size:21px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .n{font-size:11px;color:#8a8f99;margin-top:3px}
.up{color:#c0392b!important}
.dn{color:#1e8449!important}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:12px 0}
th{background:#f7f8fa;text-align:left;padding:10px 12px;font-weight:600;color:#4a4f58;border-bottom:1px solid #e3e5e9;font-size:13px}
td{padding:9px 12px;border-bottom:1px solid #f0f1f3;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600}
.tag-r{background:#fdecea;color:#c0392b}
.tag-g{background:#e8f6ef;color:#1e8449}
.tag-b{background:#eaf2fd;color:#2471a3}
.tag-y{background:#fef9e7;color:#b8860b}
.tag-n{background:#f0f1f3;color:#6c717a}
.chart{width:100%;height:400px;margin:10px 0}
.src{font-size:11.5px;color:#9aa0a8;margin-top:8px;font-style:italic}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.lv{display:flex;justify-content:space-between;padding:10px 14px;border-radius:6px;margin-bottom:8px;font-size:14px;align-items:center}
.lv-r{background:#fdecea;border-left:4px solid #c0392b}
.lv-g{background:#e8f6ef;border-left:4px solid #1e8449}
.lv b{font-variant-numeric:tabular-nums;font-size:15px}
.note{background:#f8f9fa;border:1px solid #e8eaed;border-radius:8px;padding:14px 18px;font-size:13.5px;color:#4a4f58;margin:12px 0}
ul{margin-left:20px;font-size:14px}li{margin-bottom:7px}
.disc{background:#fff;border:1px solid #e3e5e9;border-radius:10px;padding:18px 22px;font-size:12.5px;color:#6c717a;line-height:1.8}
.warn{background:#fff9e6;border:1px solid #ffe08a;border-radius:8px;padding:14px 18px;font-size:13.5px;margin:12px 0}
h3.sub{font-size:15px;margin:16px 0 10px;color:#2c3e50}
@media(max-width:768px){.two{grid-template-columns:1fr}body{padding:14px}}
"""

DISC = (
    "<strong>免责声明</strong>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。"
    "市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，"
    "必要时咨询持牌专业机构。过往表现不预示未来收益。"
)

UP, DN = "#c0392b", "#1e8449"


def _cls(v: float | None, invert: bool = False) -> str:
    if v is None:
        return ""
    pos = v < 0 if invert else v > 0
    return "up" if pos else ("dn" if v != 0 else "")


def _n(v: float | None, d: int = 2, unit: str = "") -> str:
    if v is None:
        return "—"
    s = f"{v:,.{d}f}{unit}"
    return f'<span class="{_cls(v)}">{s}</span>'


def _yi(v: float | None) -> str:
    """元 → 亿元"""
    if v is None:
        return "—"
    return _cls(v) + f"{v/1e8:.2f} 亿"


def _tag(sig: str, score: int) -> str:
    if score > 0:
        c = "tag-r"
    elif score < 0:
        c = "tag-g"
    else:
        c = "tag-n"
    return f'<span class="tag {c}">{html.escape(sig)}</span>'


# ---------------------------------------------------------------- 结论生成

def build_conclusions(b: Bundle, st: E.Structure, votes: list[E.Vote]) -> list[tuple[str, str]]:
    """生成结论先行段落，返回 [(小标题, 正文)]。"""
    out: list[tuple[str, str]] = []
    q = b.quote
    if not q or not q.price:
        return [("数据不足", "未能获取有效行情数据，无法给出走势结论。")]

    price = q.price
    chg = q.change_pct or 0
    trend_cn = {"up": "上行", "down": "下行", "range": "震荡"}.get(st.trend, "未知")

    # 1 走势定性
    t = f"**走势定性：{trend_cn}。**{st.trend_detail}。"
    if st.pattern:
        t += f" 技术形态上构成 **{st.pattern}**：{st.pattern_detail}。"
    if st.rebound_pct is not None:
        t += f" 现价处本轮区间 **{st.rebound_pct}%** 分位。"
    out.append(("走势定性", t))

    # 2 技术面
    closes = [x.close for x in b.bars]
    if len(closes) >= 60:
        ma = {n: E.sma(closes, n)[-1] for n in (5, 10, 20, 60)}
        above = [n for n, v in ma.items() if v and price > v]
        bull = ma[5] > ma[10] > ma[20]
        t = "**技术面：**"
        t += (f"收盘价站上 {len(above)}/4 条主要均线（MA5 {ma[5]:.2f}、MA10 {ma[10]:.2f}、"
              f"MA20 {ma[20]:.2f}、MA60 {ma[60]:.2f}），"
              + ("MA5>MA10>MA20 形成短期多头排列。" if bull else "均线仍纠缠。"))
        m = E.macd(closes)
        rising = all(m["hist"][-i] > m["hist"][-i - 1] for i in range(1, 6))
        t += f" MACD 柱状 {m['hist'][-1]:+.3f}（DIF {m['dif'][-1]:+.3f} / DEA {m['dea'][-1]:+.3f}）"
        if rising:
            t += "，连续 5 日放大。"
        kd = E.kdj(b.bars)
        if kd["j"][-1] is not None:
            j = kd["j"][-1]
            t += f" KDJ 的 J 值 {j:.1f}" + ("，**已进入短线超买区**。" if j > 90 else
                  ("，处于超买临界。" if j > 80 else "，指标中性。"))
        out.append(("技术面", t))

    # 3 量价资金
    if b.flows:
        f = b.flows[-1]
        v20 = sum(x.volume for x in b.bars[-21:-1]) / 20 if len(b.bars) >= 21 else 0
        ratio = f.close and (q.volume or 0) and v20 and (q.volume / v20)
        t = "**量价与资金：**"
        if ratio:
            t += f"当日成交量 {q.volume/1e4:.0f} 万股，为 20 日均量的 **{ratio:.2f} 倍**（量比 {q.volume_ratio:.2f}）。" if q.volume_ratio else ""
        main = f.main / 1e8
        agree = (main > 0) == (chg > 0)
        if abs(main) >= 0.3 and not agree:
            t += (f"**但主力资金净{'流入' if main>0 else '流出'} {abs(main):.2f} 亿元，与股价方向背离**"
                  f"（中小单净{'流入' if f.mid+f.small>0 else '流出'} {abs(f.mid+f.small)/1e8:.2f} 亿元）"
                  f"——推动上涨的是承接买盘而非主动扫货，需警惕。")
        elif abs(main) >= 0.3:
            t += f"主力资金净{'流入' if main>0 else '流出'} {abs(main):.2f} 亿元，与股价方向一致。"
        else:
            t += f"主力资金净{'流入' if main>0 else '流出'} {abs(main):.2f} 亿元，金额不显著。"
        if b.margins:
            m = b.margins[-1]
            if m.finance_dod is not None and abs(m.finance_dod) >= 1:
                t += (f" 融资余额 {m.finance_balance/1e8:.3f} 亿"
                      f"（环比 {m.finance_dod:+.2f}%，杠杆资金{'加仓' if m.finance_dod>0 else '减仓'}）。")
        out.append(("量价与资金", t))

    # 4 中期定位
    if st.fib:
        t = "**中期定位：**" + "；".join(st.notes[:2]) if st.notes else ""
        t += " 上方压力与下方支撑见关键价位表。"
        out.append(("中期定位", t))

    # 5 投票
    bull = sum(1 for v in votes if v.score > 0)
    bear = sum(1 for v in votes if v.score < 0)
    neu = len(votes) - bull - bear
    div = [v for v in votes if "背离" in v.signal]
    t = (f"**多体系投票：{bull} 票偏多 / {bear} 票偏空 / {neu} 票中性。**")
    if bull > bear * 1.8:
        t += "多数体系指向向上，信号一致性较好。"
    elif bear > bull * 1.8:
        t += "多数体系指向向下，弱势格局。"
    else:
        t += "**多空分歧明显**，"
        if div:
            t += f"分歧核心在**{div[0].system}**——{div[0].detail}。"
        else:
            t += "价格形态与技术指标方向不完全一致。"
    out.append(("信号投票", t))

    return out


def build_scenarios(b: Bundle, st: E.Structure) -> list[tuple[str, str, str, str]]:
    """条件化操作框架。返回 [(情景, 触发条件, 结构含义, 价位)]"""
    q = b.quote
    if not q:
        return []
    price = q.price
    res = [x for x in st.levels if x.price > price]
    sup = [x for x in st.levels if x.price < price]
    r1 = res[0].price if res else round(price * 1.03, 2)
    r2 = res[1].price if len(res) > 1 else round(price * 1.06, 2)
    s1 = sup[0].price if sup else round(price * 0.97, 2)
    s2 = sup[1].price if len(sup) > 1 else round(price * 0.94, 2)
    v20 = sum(x.volume for x in b.bars[-21:-1]) / 20 if len(b.bars) >= 21 else 0
    vth = round(v20 * 1.5 / 1e4) if v20 else 800

    rows = [
        ("突破确认", f"次日收盘站上 {r1:.2f} 且成交量维持 {vth} 万股以上",
         f"突破{'颈线/前高' if st.pattern else '压力位'}，趋势确认度提升", f"{r1:.2f} → {r2:.2f}"),
        ("假突破", f"跌回 {s1:.2f} 以下且放量", "突破失败，回测前支撑概率上升", f"{s1:.2f} / {s2:.2f}"),
    ]
    if st.trend in ("down", "range"):
        lows = [x for x in st.levels if x.kind == "support"]
        if lows:
            rows.append(("结构破坏", f"有效跌破 {lows[-1].price:.2f}",
                         "底部形态失效，下跌趋势延续", f"< {lows[-1].price:.2f}"))
    rows.append(("资金验证", "主力资金连续 2–3 日净转正", "突破获得资金确认，形态成立概率提升", "—"))
    kd = E.kdj(b.bars)["j"][-1] if len(b.bars) >= 20 else None
    if kd:
        rows.append(("过热预警", "J 值持续 > 95 或 RSI6 > 80",
                     "短线超买，回踩 MA5 概率加大", f"{E.sma([x.close for x in b.bars], 5)[-1]:.2f}"))
    return rows


# ---------------------------------------------------------------- 各区块

def _sec_conclusion(b, st, votes) -> str:
    rows = "".join(
        f"<p><strong>{html.escape(k)}：</strong>{v}</p>" for k, v in build_conclusions(b, st, votes)
    )
    return f'<div class="tldr"><h2>结论先行</h2>{rows}</div>'


def _sec_kpi(b: Bundle) -> str:
    q = b.quote
    if not q:
        return '<div class="card"><h2>行情数据</h2><div class="warn">行情数据获取失败。</div></div>'
    items = [
        ("收盘价", f"{q.price:.2f} 元", f"{q.change_pct:+.2f}%（{q.change:+.2f}）" if q.change is not None else "", True),
        ("当日振幅", f"{q.amplitude:.2f}%" if q.amplitude else "—",
         f"{q.low:.2f} ~ {q.high:.2f}" if q.low and q.high else "", False),
        ("成交量 / 量比", f"{q.volume/1e4:.0f} 万股" if q.volume else "—",
         (f"量比 {q.volume_ratio:.2f}，换手 {q.turnover_rate:.2f}%" if q.volume_ratio and q.turnover_rate else ""), False),
        ("成交额", f"{q.amount/1e8:.2f} 亿" if q.amount else "—", "", False),
        ("总市值", f"{q.total_mcap/1e8:.2f} 亿" if q.total_mcap else "—",
         f"流通 {q.float_mcap/1e8:.2f} 亿" if q.float_mcap else "", False),
    ]
    if q.pe_ttm:
        items.append(("PE(TTM) / PB", f"{q.pe_ttm:.2f} / {q.pb:.2f}" if q.pb else f"{q.pe_ttm:.2f}",
                      f"动态 PE {q.pe_fwd:.2f}" if q.pe_fwd else "", False))
    if q.high_52w and q.low_52w:
        d = (q.price / q.high_52w - 1) * 100
        items.append(("52 周高 / 低", f"{q.high_52w:.2f} / {q.low_52w:.2f}", f"距高点 {d:+.1f}%", True))
    rng = []
    for lbl, v in (("5日", q.chg_5d), ("10日", q.chg_10d), ("20日", q.chg_20d),
                   ("60日", q.chg_60d), ("YTD", q.chg_ytd)):
        if v is not None:
            rng.append(f"{lbl} {v:+.2f}%")
    if rng:
        items.append(("区间涨跌", f'<span class="{_cls(q.chg_20d)}">{q.chg_20d:+.2f}%</span>' if q.chg_20d else "—",
                      " ｜ ".join(rng[1:]) if len(rng) > 1 else "20 日", False))

    cells = "".join(
        f'<div class="kpi"><div class="l">{html.escape(l)}</div>'
        f'<div class="v {"up" if c else ""}">{v}</div>'
        f'<div class="n {"up" if c else ""}">{html.escape(n)}</div></div>'
        for l, v, n, c in items
    )
    if b.mcap_date and len(b.mcap_date) == 8:
        md = f"{b.mcap_date[:4]}-{b.mcap_date[4:6]}-{b.mcap_date[6:]}"
        src_mcap = f"总市值/流通市值取自本地 stock_daily_basic_info_t（{md}）；"
    else:
        src_mcap = ""
    return (f'<div class="card"><h2>一、核心行情数据（{html.escape(q.date)} 收盘）</h2>'
            f'<div class="kpis">{cells}</div>'
            f'<div class="src">来源：{src_mcap}其余行情字段来自 WeStock 实时行情接口，{html.escape(q.date)} 收盘。</div></div>')


def _sec_kline(b: Bundle) -> str:
    if not b.bars:
        return ""
    n = min(len(b.bars), 120)
    src = b.bars[-n:]
    return (
        f'<div class="card"><h2>二、日K 结构（近 {n} 个交易日）</h2>'
        f'<div class="chart" id="kline"></div>'
        f'<div class="src">数据：stock_daily_t 本地日K（前复权），{html.escape(src[0].date)} 至 '
        f'{html.escape(src[-1].date)}，共 {n} 根。均线由本工具根据收盘价计算。</div></div>'
    )


def _sec_vol(b: Bundle) -> str:
    if not b.bars:
        return ""
    closes = [x.close for x in b.bars]
    v = [x.volume / 1e4 for x in b.bars]
    v20 = sum(x.volume for x in b.bars[-21:-1]) / 20 if len(b.bars) >= 21 else 0
    rows = []
    seg = max(1, len(b.bars) // 6)
    for i in range(0, len(b.bars) - seg + 1, seg):
        w = b.bars[i : i + seg]
        rows.append((f"{w[0].date} ~ {w[-1].date}", f"{sum(x.volume for x in w)/1e4/len(w):.0f}",
                     f"{(w[-1].close/w[0].close-1)*100:+.2f}%",
                     "放量上行" if (w[-1].close > w[0].close and sum(x.volume for x in w)/len(w) > v20) else
                     ("放量下跌" if (w[-1].close < w[0].close and sum(x.volume for x in w)/len(w) > v20) else
                      ("缩量" if sum(x.volume for x in w)/len(w) < v20 * 0.8 else "温和"))))
    tr = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{b_} 万股</td>"
        f'<td class="{_cls(float(c.replace("%","")))}">{c}</td>'
        f'<td><span class="tag {"tag-r" if d.startswith("放量") else ("tag-n" if d=="缩量" else "tag-y")}">{d}</span></td></tr>'
        for a, b_, c, d in rows
    )
    return (
        f'<div class="card"><h2>三、量能结构分段统计</h2>'
        f'<div class="two"><div><div class="chart" id="volchart" style="height:300px"></div>'
        f'<div class="src">20 日均量基准：{v20/1e4:.0f} 万股 / 日。</div></div>'
        f'<div><table><tr><th>区间</th><th>日均量</th><th>区间涨跌</th><th>量能状态</th></tr>{tr}</table>'
        f'<div class="src">分段由本工具按等分区间自动统计，红色为放量，灰色为缩量。</div></div></div></div>'
    )


def _sec_tech(b: Bundle, st: E.Structure) -> str:
    if not b.bars or len(b.bars) < 30:
        return ""
    closes = [x.close for x in b.bars]
    m = E.macd(closes)
    kd = E.kdj(b.bars)
    rs = E.rsi(closes)
    bb = E.boll(closes)
    rows = []
    for n in (5, 10, 20, 60, 120):
        s = E.sma(closes, n)
        if s[-1]:
            above = b.quote and b.quote.price > s[-1]
            rows.append((f"MA{n}", f"{s[-1]:.2f}",
                         f'<span class="tag {"tag-r" if above else "tag-g"}">{"站上" if above else "跌破"}</span>'))
    j = kd["j"][-1]
    rows += [
        ("MACD 柱", f"{m['hist'][-1]:+.4f}", f'<span class="tag {"tag-r" if m["hist"][-1]>0 else "tag-g"}">{"红柱" if m["hist"][-1]>0 else "绿柱"}</span>'),
        ("DIF / DEA", f"{m['dif'][-1]:+.3f} / {m['dea'][-1]:+.3f}",
         f'<span class="tag {"tag-r" if m["dif"][-1]>0 else "tag-g"}">{"零轴上" if m["dif"][-1]>0 else "零轴下"}</span>'),
        ("KDJ J 值", f"{j:.1f}" if j is not None else "—",
         f'<span class="tag {"tag-y" if (j or 0)>80 else "tag-b"}">{"超买" if (j or 0)>80 else "正常"}</span>'),
        ("RSI6 / RSI12", f"{(rs[-6] or 0):.1f} / {(rs[-12] or 0):.1f}" if rs[-6] and rs[-12] else "—",
         f'<span class="tag {"tag-y" if (rs[-6] or 0)>70 else "tag-b"}">{"偏热" if (rs[-6] or 0)>70 else "中性"}</span>'),
        ("BOLL 上/中/下", f"{bb['upper'][-1]:.2f} / {bb['mid'][-1]:.2f} / {bb['lower'][-1]:.2f}",
         f'<span class="tag {"tag-r" if b.quote and b.quote.price > (bb["upper"][-1] or 0) else "tag-b"}">{"突破上轨" if b.quote and b.quote.price > (bb["upper"][-1] or 0) else "轨内"}</span>'),
    ]
    if b.chip:
        rows.append(("筹码均价", f"{b.chip.get('avg_cost',0):.2f} 元",
                     f'现价偏离 {(b.quote.price/b.chip["avg_cost"]-1)*100:+.1f}%' if b.chip.get("avg_cost") and b.quote else ""))
        pr = b.chip.get("profit_rate", 0)
        rows.append(("筹码获利率", f"{pr:.2f}%",
                     f'<span class="tag {"tag-y" if pr>80 else "tag-b"}">{"过重" if pr>80 else "正常"}</span>'))
    tr = "".join(f"<tr><td>{html.escape(a)}</td><td>{c}</td><td>{d}</td></tr>" for a, c, d in rows)
    return (
        f'<div class="card"><h2>四、技术指标全景</h2><div class="two"><div>'
        f'<table><tr><th>指标</th><th>数值</th><th>状态</th></tr>{tr}</table>'
        f'<div class="src">来源：WeStock 筹码成本接口（{html.escape(str(b.chip.get("date","")))}）；'
        f'其余指标由本工具基于日K自行计算（MA / MACD 12-26-9 / KDJ 9-3-3 / RSI / BOLL 20-2）。</div></div>'
        f'<div><div class="chart" id="macdchart" style="height:300px"></div>'
        f'<div class="src">MACD 与成交量对照，区间为近 30 个交易日。</div></div></div></div>'
    )


def _sec_flow(b: Bundle) -> str:
    if not b.flows:
        return ""
    rows = ""
    for f in b.flows[-8:]:
        rows += (
            f"<tr><td>{html.escape(f.date)}</td><td>{f.close:.2f}</td>"
            f'<td>{_yi(f.main)}</td><td>{_yi(f.jumbo)}</td><td>{_yi(f.big)}</td>'
            f'<td>{_yi(f.mid)}</td><td>{_yi(f.small)}</td></tr>'
        )
    cum5 = sum(f.main for f in b.flows[-5:]) / 1e8
    cum20 = sum(f.main for f in b.flows) / 1e8
    mt = (f'<div class="card"><h2>五、资金面</h2>'
          f'<div class="kpis"><div class="kpi"><div class="l">近 5 日主力累计</div>'
          f'<div class="v {_cls(cum5)}">{cum5:+.2f} 亿</div><div class="n">'
          f'{"净流入" if cum5>0 else "净流出"}</div></div>'
          f'<div class="kpi"><div class="l">区间主力累计</div>'
          f'<div class="v {_cls(cum20)}">{cum20:+.2f} 亿</div><div class="n">共 {len(b.flows)} 个交易日</div></div>'
          f'</div><table><tr><th>日期</th><th>收盘</th><th>主力净额</th><th>超大单</th>'
          f'<th>大单</th><th>中单</th><th>小单</th></tr>{rows}</table>')
    if b.margins:
        mr = ""
        for m in b.margins[-6:]:
            mr += (f"<tr><td>{html.escape(m.date)}</td><td>{m.finance_balance/1e8:.3f} 亿</td>"
                   f'<td class="{_cls(m.finance_dod)}">{m.finance_dod:+.2f}%</td>'
                   f'<td class="{_cls(m.finance_net_buy)}">{m.finance_net_buy/1e4:+.0f} 万</td>'
                   f'<td>{(m.security_balance or 0)/1e4:.0f} 万</td></tr>')
        mt += (f'<h3 class="sub">融资融券</h3><table><tr><th>日期</th><th>融资余额</th>'
               f'<th>环比</th><th>净买入</th><th>融券余额</th></tr>{mr}</table>')
    mt += ('<div class="src">来源：WeStock 资金流向与融资融券接口。融资余额为 T+1 披露，'
           '最新一日数据可能尚未更新。</div></div>')
    return mt


def _sec_levels(b: Bundle, st: E.Structure) -> str:
    if not b.quote or not st.levels:
        return ""
    price = b.quote.price
    res = [x for x in st.levels if x.price > price][:4]
    sup = sorted([x for x in st.levels if x.price < price], key=lambda x: -x.price)[:4]
    rh = "".join(
        f'<div class="lv lv-r"><span>{html.escape(x.label)}</span>'
        f'<b>+{abs(x.dist_pct):.2f}%</b></div>' for x in res
    ) or '<div class="lv lv-r"><span>上方无明显结构压力</span><b>—</b></div>'
    sh = "".join(
        f'<div class="lv lv-g"><span>{html.escape(x.label)}</span>'
        f'<b>-{abs(x.dist_pct):.2f}%</b></div>' for x in sup
    ) or '<div class="lv lv-g"><span>下方无明显结构支撑</span><b>—</b></div>'
    fibt = ""
    if st.fib:
        fibt = "".join(
            f"<tr><td>{k}</td><td>{v:.2f} 元</td>"
            f'<td><span class="{"up" if v>price else "dn"}">{(v/price-1)*100:+.1f}%</span></td></tr>'
            for k, v in st.fib.items()
        )
        fibt = f"<h3 class='sub'>斐波那契回撤位</h3><table><tr><th>比例</th><th>价位</th><th>相对现价</th></tr>{fibt}</table>"
    note = ""
    if st.notes:
        note = '<div class="note">' + "<br>".join(html.escape(x) for x in st.notes) + "</div>"
    return (
        f'<div class="card"><h2>六、关键价位地图</h2><div class="two"><div>'
        f'<h3 class="sub" style="color:#c0392b">压力位</h3>{rh}'
        f'<h3 class="sub" style="color:#1e8449;margin-top:16px">支撑位</h3>{sh}'
        f'<div class="src">相对 {html.escape(b.quote.date)} 收盘价 {price:.2f} 元计算。'
        f'价位来自摆动高低点与斐波那契回撤，由本工具自动计算。</div></div>'
        f'<div>{fibt}{note}</div></div></div>'
    )


def _sec_fundamental(b: Bundle) -> str:
    if not (b.consensus or b.score or b.profile):
        return ""
    out = ""
    if b.profile:
        p = b.profile
        out += (f'<div class="note"><strong>公司简况（{html.escape(p.get("industry",""))}）：</strong>'
                f'{html.escape(p.get("business",""))}　上市日期 {html.escape(p.get("listed",""))}'
                f'　董事长 {html.escape(p.get("chairman",""))}</div>')
    if b.consensus.get("years"):
        rows = ""
        for y in b.consensus["years"]:
            rows += (f"<tr><td>{html.escape(y['year'])}E</td>"
                     f"{(y['revenue'] or 0)/1e4:.2f} 亿</td>"
                     f'{_cls(y["revenue_yoy"])}>{y["revenue_yoy"]:+.1f}%</td>'
                     f"{(y['net_profit'] or 0)/1e4:.2f} 亿</td>"
                     f'{_cls(y["profit_yoy"])}>{y["profit_yoy"]:+.1f}%</td>'
                     f'<td>{y["pe"]:.1f}×</td></tr>' if y["revenue"] and y["net_profit"] and y["pe"] else "")
        tp = b.consensus.get("target_price")
        tpr = (f'<div class="kpi" style="margin-top:12px"><div class="l">一致目标价</div>'
               f'<div class="v {"up" if tp>b.quote.price else "dn"}">{tp:.2f} 元</div>'
               f'<div class="n">隐含空间 {(tp/b.quote.price-1)*100:+.1f}%</div></div>') if tp and b.quote else ""
        out += (f'<h3 class="sub">一致预期</h3><table><tr><th>年度</th><th>营收</th><th>同比</th>'
                f'<th>净利</th><th>同比</th><th>PE</th></tr>{rows}</table>{tpr}')
    if b.score:
        s = b.score
        rows = ""
        for k, label in (("comp", "综合评分"), ("tech", "技术评分"),
                         ("fund", "资金评分"), ("risk", "风险评分"), ("cap", "成长评分")):
            if s.get(k) is not None:
                v = s[k]
                rows += (f'<tr><td>{label}</td><td>{v:.2f}</td>'
                         f'<td><div style="background:#eef0f3;border-radius:3px;height:8px;width:100%">'
                         f'<div style="background:#c0392b;height:8px;border-radius:3px;width:{v}%"></div></div></td></tr>')
        out += f'<h3 class="sub">评分体系</h3><table><tr><th>维度</th><th>得分</th><th>强度</th></tr>{rows}</table>'
    out += ('<div class="src">来源：WeStock 公司简况 / 一致预期 / 评分接口。一致预期为卖方分析师预测汇总，'
            '属非一手来源，需核实原文；覆盖机构数未返回时样本代表性需谨慎。</div>')
    return f'<div class="card"><h2>七、基本面与估值</h2>{out}</div>'


def _sec_votes(votes: list[E.Vote]) -> str:
    if not votes:
        return ""
    rows = ""
    for v in votes:
        rows += (f"<tr><td>{html.escape(v.system)}</td><td>{_tag(v.signal, v.score)}</td>"
                 f"<td>{html.escape(v.detail)}</td></tr>")
    bull = sum(1 for v in votes if v.score > 0)
    bear = sum(1 for v in votes if v.score < 0)
    neu = len(votes) - bull - bear
    return (
        f'<div class="card"><h2>八、多体系信号汇总与投票</h2>'
        f'<div class="kpis"><div class="kpi"><div class="l">偏多</div><div class="v up">{bull}</div></div>'
        f'<div class="kpi"><div class="l">偏空</div><div class="v dn">{bear}</div></div>'
        f'<div class="kpi"><div class="l">中性/观望</div><div class="v">{neu}</div></div></div>'
        f'<table><tr><th>分析体系</th><th>信号</th><th>依据</th></tr>{rows}</table>'
        f'<div class="src">来源：本工具基于日K、资金流、两融、筹码数据自动计算。</div></div>'
    )


def _sec_scenario(b: Bundle, st: E.Structure) -> str:
    rows = build_scenarios(b, st)
    if not rows:
        return ""
    tagmap = {"突破确认": "tag-r", "假突破": "tag-y", "结构破坏": "tag-g",
              "资金验证": "tag-b", "过热预警": "tag-n"}
    tr = ""
    for name, cond, mean, px in rows:
        tr += (f'<tr><td><span class="tag {tagmap.get(name,"tag-n")}">{name}</span></td>'
               f"<td>{html.escape(cond)}</td><td>{html.escape(mean)}</td><td>{html.escape(px)}</td></tr>")
    return (
        f'<div class="card"><h2>九、条件化情景框架</h2>'
        f'<div class="warn"><strong>前提缺失声明：</strong>以下为基于技术结构的条件化观察框架，'
        f'<strong>不构成针对任何具体投资者的操作建议</strong>。使用者自身的风险偏好、持仓成本、'
        f'资金规模与投资期限均未知，所列价位仅为图表结构位，不应直接作为买卖指令。</div>'
        f'<table><tr><th>情景</th><th>触发条件</th><th>结构含义</th><th>关注价位</th></tr>{tr}</table></div>'
    )


def _sec_events(b: Bundle) -> str:
    if not b.reports and not b.news:
        return ""
    items = []
    for r in b.reports[:6]:
        t = r.get("title", "")
        d = (r.get("time") or "")[:10]
        rt = r.get("typeStr", "")
        rating = r.get("tzpj", "")
        if t:
            items.append((d, t, f"{rt} {rating}".strip()))
    for n in b.news[:6]:
        d = (n.get("time") or "")[:10]
        t = n.get("title", "")
        if t:
            items.append((d, t, "资讯"))
    items = items[:10]
    if not items:
        return ""
    ul = "".join(
        f'<li><strong>{html.escape(d)}</strong>　{html.escape(t)}'
        + (f'<span class="tag tag-b" style="margin-left:6px">{html.escape(k)}</span>' if k else "")
        + "</li>"
        for d, t, k in items
    )
    return (
        f'<div class="card"><h2>十、近期研报与资讯</h2><ul>{ul}</ul>'
        f'<div class="src">来源：WeStock 研报列表 / 新闻资讯接口。媒体报道与研报引用属非一手来源，'
        f'需核实原文。</div></div>'
    )


def _sec_errors(b: Bundle) -> str:
    if not b.errors:
        return ""
    li = "".join(f"<li>{html.escape(x)}</li>" for x in b.errors)
    return (f'<div class="card"><h2>数据缺口说明</h2>'
            f'<div class="warn">以下数据源未能获取，相关分析维度已跳过或降级处理：'
            f'<ul style="margin-top:8px">{li}</ul></div></div>')


# ---------------------------------------------------------------- 图表脚本

def _chart_script(b: Bundle) -> str:
    if not b.bars:
        return ""
    n = min(len(b.bars), 120)
    bars = b.bars[-n:]
    closes = [x.close for x in bars]
    ma = {k: E.sma(closes, k) for k in (5, 10, 20, 60)}
    m = E.macd(closes)
    v20 = sum(x.volume for x in b.bars[-21:-1]) / 20 if len(b.bars) >= 21 else 0

    data = {
        "dates": [x.date[5:] for x in bars],
        "ohlc": [[x.open, x.close, x.low, x.high] for x in bars],
        "vol": [round(x.volume / 1e4) for x in bars],
        "ma5": ma[5], "ma10": ma[10], "ma20": ma[20], "ma60": ma[60],
        "dif": m["dif"][-30:], "dea": m["dea"][-30:], "hist": m["hist"][-30:],
        "v20": round(v20 / 1e4),
    }
    tail = f'<div class="src">来源：本工具基于 stock_daily_t 本地日K 计算。</div></div>'

    return (
        "<script>\n"
        "var D=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
        """
var host=document.createElement('div');
function opt(id){return echarts.init(document.getElementById(id));}

opt('kline').setOption({
  title:{text:'日K + 均线系统',left:'center',top:4,textStyle:{fontSize:13,color:'#4a4f58',fontWeight:'normal'}},
  tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
  legend:{data:['日K','MA5','MA20','MA60','成交量'],top:26,textStyle:{fontSize:11}},
  grid:[{left:58,right:20,top:56,height:'50%'},{left:58,right:20,top:'72%',height:'16%'}],
  xAxis:[{type:'category',data:D.dates,axisLabel:{fontSize:10},axisLine:{lineStyle:{color:'#ccc'}}},
         {type:'category',data:D.dates,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:'#ccc'}}}],
  yAxis:[{scale:true,splitLine:{lineStyle:{color:'#f0f1f3'}},axisLabel:{fontSize:10}},
         {scale:true,gridIndex:1,splitLine:{show:false},axisLabel:{fontSize:9}}],
  series:[
    {name:'日K',type:'candlestick',data:D.ohlc,itemStyle:{color:'#c0392b',color0:'#1e8449',borderColor:'#c0392b',borderColor0:'#1e8449'}},
    {name:'MA5',type:'line',data:D.ma5,smooth:true,showSymbol:false,lineStyle:{width:1.3,color:'#e67e22'}},
    {name:'MA20',type:'line',data:D.ma20,smooth:true,showSymbol:false,lineStyle:{width:1.3,color:'#2471a3'}},
    {name:'MA60',type:'line',data:D.ma60,smooth:true,showSymbol:false,lineStyle:{width:1.3,color:'#8e44ad'}},
    {name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:D.vol,
     itemStyle:{color:function(p){return p.data>D.v20?'#c0392b':'#c8cdd4';}}}
  ]
});

opt('volchart').setOption({
  title:{text:'成交量（万股） vs 20日均量',left:'center',top:4,textStyle:{fontSize:13,color:'#4a4f58',fontWeight:'normal'}},
  tooltip:{trigger:'axis'},
  legend:{data:['成交量','20日均量'],top:24,textStyle:{fontSize:10}},
  grid:{left:56,right:18,top:46,bottom:34},
  xAxis:{type:'category',data:D.dates,axisLabel:{fontSize:9,interval:Math.floor(D.dates.length/10)},axisLine:{lineStyle:{color:'#ccc'}}},
  yAxis:{type:'value',splitLine:{lineStyle:{color:'#f0f1f3'}},axisLabel:{fontSize:9}},
  series:[
    {name:'成交量',type:'bar',data:D.vol,itemStyle:{color:function(p){return p.data>D.v20?'#c0392b':'#c8cdd4';}}},
    {name:'20日均量',type:'line',data:D.dates.map(function(){return D.v20;}),smooth:true,
     showSymbol:false,lineStyle:{width:1.4,color:'#2471a3',type:'dashed'}}
  ]
});

var t30=D.dates.slice(-30);
opt('macdchart').setOption({
  title:{text:'MACD（近30日）',left:'center',top:4,textStyle:{fontSize:13,color:'#4a4f58',fontWeight:'normal'}},
  tooltip:{trigger:'axis'},
  legend:{data:['MACD柱','DIF','DEA'],top:24,textStyle:{fontSize:10}},
  grid:{left:56,right:18,top:46,bottom:28},
  xAxis:{type:'category',data:t30,axisLabel:{fontSize:9},axisLine:{lineStyle:{color:'#ccc'}}},
  yAxis:{type:'value',splitLine:{lineStyle:{color:'#f0f1f3'}},axisLabel:{fontSize:9}},
  series:[
    {name:'MACD柱',type:'bar',data:D.hist,itemStyle:{color:function(p){return p.data>=0?'#c0392b':'#1e8449';}}},
    {name:'DIF',type:'line',data:D.dif,smooth:true,showSymbol:false,lineStyle:{width:1.4,color:'#e67e22'}},
    {name:'DEA',type:'line',data:D.dea,smooth:true,showSymbol:false,lineStyle:{width:1.4,color:'#2471a3'}}
  ]
});

window.addEventListener('resize',function(){
  ['kline','volchart','macdchart'].forEach(function(i){
    var c=echarts.getInstanceByDom(document.getElementById(i)); if(c)c.resize();
  });
});
"""
        "</script>"
    )


# ---------------------------------------------------------------- 主入口

def render(b: Bundle) -> str:
    q = b.quote
    name = (q.name if q and q.name else "") or b.profile.get("name", "")
    title = f"{name}（{b.code}）走势分析" if name else f"{b.code} 走势分析"
    date = (q.date if q and q.date else "") or "最新"

    st = E.Structure()
    votes: list[E.Vote] = []
    if b.bars and q and q.price:
        st = E.analyze_structure(b.bars, q.price)
        votes = E.build_votes(b)

    sections = "".join([
        _sec_conclusion(b, st, votes),
        _sec_kpi(b),
        _sec_kline(b),
        _sec_vol(b),
        _sec_tech(b, st),
        _sec_flow(b),
        _sec_levels(b, st),
        _sec_fundamental(b),
        _sec_votes(votes),
        _sec_scenario(b, st),
        _sec_events(b),
        _sec_errors(b),
    ])

    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>\n'
        f"<style>{CSS}</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
        f'<div class="hd"><h1>{html.escape(title)}</h1>'
        f'<div class="sub">数据截至 {html.escape(date)} ｜ 行情与技术数据：WeStock ｜ '
        f'结构/指标/投票：由 stock_analyzer 自动生成</div></div>\n'
        f"{sections}\n"
        f'<div class="disc">{DISC}<br><br>'
        f"<strong>方法论与口径</strong>：MA 为简单移动平均；MACD 参数 12-26-9；KDJ 参数 9-3-3；"
        f"BOLL 参数 20-2；斐波那契回撤与 W/M 顶底目标位由本工具根据摆动高低点自动计算，"
        f"属图表结构测算，不构成价格预测。所有数据时点为 {html.escape(date)}。"
        f"一致预期、研报、新闻为非一手来源，需核实原文。</div>\n"
        "</div>\n"
        + _chart_script(b)
        + "\n</body>\n</html>"
    )