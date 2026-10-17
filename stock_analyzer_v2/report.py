"""HTML 报告渲染层：把 AnalysisResult 渲染成浅底研报风的单页报告。

ECharts option 一律用骨架填充 data，交付前需做 node --check 语法自检。
"""

from __future__ import annotations

import json
import os
from typing import List

import pandas as pd

from .analyzer import AnalysisResult

CSS = r"""
:root{--bg:#f5f6f8;--card:#fff;--ink:#1a1d23;--ink2:#4a5160;--ink3:#858c9b;
--line:#e3e6ec;--red:#d92b2b;--green:#15935a;--amber:#c47d10;--blue:#2563c9;--chip:#f0f2f6}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);line-height:1.7;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1140px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:25px;font-weight:700;letter-spacing:-.3px}
.sub{color:var(--ink3);font-size:13px;margin-top:6px}
.tag{display:inline-block;background:var(--chip);color:var(--ink2);font-size:12px;padding:3px 9px;border-radius:5px;margin-right:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin-bottom:16px}
h2{font-size:17px;font-weight:650;margin-bottom:4px;display:flex;align-items:center;gap:8px}
h2::before{content:"";width:3px;height:15px;background:var(--blue);border-radius:2px}
.h2note{font-size:12px;color:var(--ink3);margin-bottom:14px}
h3{font-size:14px;font-weight:650;margin:16px 0 8px}
p,li{font-size:14px;color:var(--ink2)}
ul{margin:6px 0 0 18px}li{margin-bottom:5px}
b,strong{color:var(--ink);font-weight:650}
.grid{display:grid;gap:12px}
.g4{grid-template-columns:repeat(4,1fr)}
.g3{grid-template-columns:repeat(3,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .lb{font-size:12px;color:var(--ink3)}
.kpi .v{font-size:22px;font-weight:700;margin:3px 0 1px;letter-spacing:-.5px}
.kpi .ex{font-size:11.5px;color:var(--ink3)}
.up{color:var(--red)}.down{color:var(--green)}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right}
th{background:#f8f9fb;color:var(--ink2);font-weight:600;font-size:12px;white-space:nowrap}
td:first-child,th:first-child{text-align:left}
tbody tr:hover{background:#fafbfc}
.src{font-size:11.5px;color:var(--ink3);margin-top:10px;line-height:1.6}
.verdict{border-radius:10px;padding:18px 20px;margin-bottom:16px;border:1px solid}
.v-pos{background:linear-gradient(135deg,#fff5f5,#fffafa);border-color:#f5cfcf}
.v-neu{background:linear-gradient(135deg,#f7f9fc,#fbfcfe);border-color:#d6dfec}
.v-neg{background:linear-gradient(135deg,#f4fbf7,#f9fdfb);border-color:#c6e4d5}
.v-pos .t{color:var(--red)}.v-neu .t{color:var(--blue)}.v-neg .t{color:var(--green)}
.verdict .t{font-size:16px;font-weight:700;margin-bottom:8px}
.score{float:right;font-size:13px;color:var(--ink3);font-weight:400}
.chart{width:100%;height:430px}.chart-sm{width:100%;height:290px}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tab{font-size:12.5px;padding:5px 13px;border:1px solid var(--line);background:#fff;color:var(--ink2);
border-radius:6px;cursor:pointer;transition:.15s}
.tab.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.hide{display:none}
.kv{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px dashed var(--line);font-size:13px;gap:14px}
.kv span:first-child{color:var(--ink3);flex:0 0 auto}
.kv span:last-child{color:var(--ink);font-weight:600;text-align:right}
.warn{background:#fffaf0;border:1px solid #f0dfbc;border-radius:10px;padding:16px 18px;margin-bottom:16px}
.warn li{color:#7a5c18}
.disc{margin-top:22px;padding:16px 18px;background:#f8f9fb;border:1px solid var(--line);
border-radius:10px;font-size:12.5px;color:var(--ink3);line-height:1.75}
.tl{position:relative;padding-left:20px}
.tl::before{content:"";position:absolute;left:5px;top:6px;bottom:6px;width:2px;background:var(--line)}
.tl .it{position:relative;margin-bottom:12px}
.tl .it::before{content:"";position:absolute;left:-18px;top:6px;width:9px;height:9px;border-radius:50%;
background:#fff;border:2.5px solid var(--blue)}
.tl .dt{font-size:12px;color:var(--ink3);font-weight:600}
.tl .tx{font-size:13.5px;color:var(--ink2);margin-top:2px}
@media(max-width:860px){.g4,.g3,.g2{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.g4,.g3,.g2{grid-template-columns:1fr}}
"""


def _fmt_pct(x, digits=2):
    try:
        return f"{float(x):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _cls(x):
    try:
        return "up" if float(x) >= 0 else "down"
    except (TypeError, ValueError):
        return ""


def build_data(res: AnalysisResult, bars: int = 95) -> dict:
    d = res.kline.tail(bars).reset_index(drop=True)
    dates = [pd.Timestamp(x).strftime("%m-%d") for x in d["date"]]
    ohlc = [[round(float(r.open), 2), round(float(r.close), 2),
             round(float(r.low), 2), round(float(r.high), 2)] for r in d.itertuples()]
    vols = [round(float(v) / 1e4) for v in d["volume"]]
    ma = {}
    for n in (5, 10, 20, 60):
        ma[str(n)] = [None if pd.isna(v) else round(float(v), 2)
                      for v in d[f"ma{n}"].tolist()] if f"ma{n}" in d else [None] * len(d)

    data = {"dates": dates, "ohlc": ohlc, "vols": vols,
            "ma5": ma["5"], "ma10": ma["10"], "ma20": ma["20"], "ma60": ma["60"]}

    # 标注关键位
    data["marks"] = [{"name": lv.label, "y": round(lv.low, 2),
                      "color": "#d92b2b" if lv.kind == "压力" else "#15935a"}
                     for lv in res.key_levels[:8]]

    # 资金流
    if res.fund:
        dd = res.fund.daily[-30:]
        data["ffDates"] = [x["date"][5:] for x in dd]
        data["ffMain"] = [x["main"] for x in dd]
        data["ffJumbo"] = [x["jumbo"] for x in dd]
        data["ffSmall"] = [x["small"] for x in dd]

    # 两融
    if res.margin.available:
        pass
    return data


def render_html(res: AnalysisResult, bars: int = 95) -> str:
    d = build_data(res, bars)
    q = res.quote
    t = res.tech
    v = res.verdict
    score = v["score"]
    vclass = "v-pos" if score >= 1 else "v-neg" if score <= -1 else "v-neu"

    def yi(x):
        try:
            return f"{float(x)/1e8:.2f}"
        except (TypeError, ValueError):
            return "—"

    kpis = f"""
<div class="grid g4" style="margin-bottom:16px">
  <div class="kpi"><div class="lb">收盘价</div><div class="v">{q.get('price','—')}</div>
    <div class="ex {_cls(q.get('change_percent'))}">{_fmt_pct(q.get('change_percent'))}　振幅{q.get('range_pct','—')}%</div></div>
  <div class="kpi"><div class="lb">成交额 / 换手</div><div class="v">{yi(q.get('amount'))}亿</div>
    <div class="ex">换手{q.get('turnover_rate','—')}%　量比{q.get('volume_ratio','—')}</div></div>
  <div class="kpi"><div class="lb">主力资金（最近一日）</div>
    <div class="v {_cls(res.fund.main_net_1d if res.fund else 0)}">{yi(res.fund.main_net_1d) if res.fund else '—'}亿</div>
    <div class="ex">5日 {_fmt_pct((res.fund.main_net_5d/1e4) if res.fund else 0,0)}万</div></div>
  <div class="kpi"><div class="lb">PE-TTM / PB</div>
    <div class="v">{q.get('pe_ratio','—')}×</div>
    <div class="ex">PB {q.get('pb_ratio','—')}　股息率{q.get('dividend_ratio_ttm','—')}%</div></div>
  <div class="kpi"><div class="lb">52周区间</div><div class="v" style="font-size:19px">{q.get('low_52week','—')}–{q.get('high_52week','—')}</div>
    <div class="ex">距高 {_fmt_pct((float(q.get('price',0))/float(q['high_52week'])-1)*100 if q.get('high_52week') else 0,0)}</div></div>
  <div class="kpi"><div class="lb">年初至今</div><div class="v {_cls(q.get('chg_ytd'))}">{_fmt_pct(q.get('chg_ytd'))}</div>
    <div class="ex">近20日 {_fmt_pct(res.multi_period.get('20日',{}).get('pct'))}</div></div>
  <div class="kpi"><div class="lb">筹码成本</div>
    <div class="v">{res.chip.avg_cost if res.chip.available else '—'}</div>
    <div class="ex">获利盘 {f"{res.chip.profit_rate:.1f}%" if res.chip.available else '—'}</div></div>
  <div class="kpi"><div class="lb">总市值</div><div class="v">{yi(q.get('total_market_cap'))}亿</div>
    <div class="ex">换手活跃度 {q.get('turnover_rate','—')}%</div></div>
</div>"""

    reasons = "".join(f"<li>{x}</li>" for x in v["reasons"]) or "<li>无显著规则命中</li>"
    verdict_html = f"""
<div class="verdict {vclass}">
  <span class="score">规则评分 {score}</span>
  <div class="t">综合判断：{v['label']} —— {v['tone']}</div>
  <p>{'　'.join(res.narrative[:3])}</p>
  <ul style="margin-top:8px">{reasons}</ul>
</div>"""

    # 趋势段表
    leg_rows = "".join(
        f"<tr><td>{lg.label}</td><td>{lg.start_date}</td><td class='right'>{lg.start_price}</td>"
        f"<td>{lg.end_date}</td><td>{lg.end_price}</td>"
        f"<td class='{_cls(lg.pct)}'>{_fmt_pct(lg.pct)}</td>"
        f"<td>{lg.high} / {lg.low}</td><td>{lg.bars}</td><td>{lg.avg_vol_wan:.0f}</td></tr>"
        for lg in res.trend_legs[-6:])
    legs_html = f"""
<div class="card">
  <h2>走势结构：趋势段切分</h2>
  <div class="h2note">用 Zigzag 摆动点（窗口10）切分、过滤幅度 &lt;8% 的噪音段后得到</div>
  <table><thead><tr><th>方向</th><th>起</th><th>起价</th><th>止</th><th>止价</th>
  <th>幅度</th><th>高/低</th><th>K线数</th><th>均量(万手)</th></tr></thead>
  <tbody>{leg_rows}</tbody></table>
  <div class="src">数据来源：腾讯自选股日K接口，{res.as_of}；趋势段由 astock.technicals.major_legs 计算。</div>
</div>"""

    fib_items = "".join(
        f"<div class='kv'><span>回撤 {r}</span><span>{px}</span></div>"
        for r, px in res.fib.get("retracement", {}).items())
    fib_items2 = "".join(
        f"<div class='kv'><span>扩展 {r}</span><span>{px}</span></div>"
        for r, px in res.fib.get("extension", {}).items())

    box_html = (f"<p>近 {res.box.bars} 日形成 <b>{res.box.low}–{res.box.high}</b> 的箱体"
                f"（振幅 {res.box.width_pct:.1f}%，低点 {res.box.low_date}／高点 {res.box.high_date}），"
                f"现价位于箱体{'上部' if res.tech.close > (res.box.low+res.box.high)/2 else '下部'}。</p>"
                if res.box else "<p>近 60 日未形成有效箱体（振幅不足或边界未被反复确认），属单边或宽幅震荡。</p>")

    tech_html = f"""
<div class="card">
  <h2>技术面：多空状态与斐波那契定位</h2>
  <div class="grid g2" style="margin-top:12px">
    <div>
      <h3>指标状态（{res.as_of}）</h3>
      <div class="kv"><span>均线排列</span><span class="{'up' if '多头' in t.ma_aligned else ''}">{t.ma_aligned}</span></div>
      <div class="kv"><span>多空位置</span><span>{t.net_bias}</span></div>
      {"".join(f"<div class='kv'><span>{k}</span><span class='{_cls(v2)}'>{v2:+.1f}%</span></div>" for k, v2 in t.ma_bias_pct.items())}
      <div class="kv"><span>BOLL 上/中/下</span><span>{t.boll_up} / {t.boll_mid} / {t.boll_low}</span></div>
      <div class="kv"><span>价格位置</span><span>{t.boll_pos}</span></div>
      <div class="kv"><span>MACD</span><span>{t.macd_state}（柱 {t.macd_hist_prev} → {t.macd_hist_now}）</span></div>
      <div class="kv"><span>KDJ / RSI6</span><span>{t.kdj_state} / {t.rsi6}（{t.rsi_state}）</span></div>
      <div class="kv"><span>量能 / ATR</span><span>{t.vol_state}（量比{t.vol_ratio}） / ATR占价{t.atr_pct}%</span></div>
    </div>
    <div>
      <h3>主导波段斐波那契</h3>
      <p style="font-size:13px">主导波段 {res.fib.get('swing_low')} → {res.fib.get('swing_high')}，
      幅度 {res.fib.get('amplitude_pct')}%（近 250 日）</p>
      {fib_items}{fib_items2}
      <h3>形态与缺口</h3>
      {"<p style='font-size:13px'>" + "；".join(f"{p['date']} {p['pattern']}" for p in res.patterns[-5:]) + "</p>" if res.patterns else "<p style='font-size:13px'>近15日无明显反转形态</p>"}
      {"<p style='font-size:13px'>" + "；".join(f"{g['date']} {g['kind']} {g['low']}→{g['high']}（{'已回补' if g['filled'] else '未回补'}）" for g in res.gaps[-3:]) + "</p>" if res.gaps else ""}
    </div>
  </div>
</div>"""

    lv_rows = "".join(
        f"<tr><td class='{_cls(1 if lv.kind=='压力' else -1)}'>{lv.kind}</td><td>{lv.label}</td>"
        f"<td>{lv.low:.2f}</td><td style='text-align:left'>{lv.basis}</td>"
        f"<td style='text-align:left'>{lv.significance}</td></tr>"
        for lv in res.key_levels)
    levels_html = f"""
<div class="card">
  <h2>关键价位与观察框架</h2>
  <div class="h2note">由均线、布林带、摆动点、筹码成本自动聚合。条件化观察清单，非买卖建议。</div>
  <table><thead><tr><th>方向</th><th>类型</th><th>价位</th><th style="text-align:left">技术依据</th>
  <th style="text-align:left">触发后的观察意义</th></tr></thead><tbody>{lv_rows}</tbody></table>
  <div class="src">价位来源：日K与技术指标接口（{res.as_of}），聚合逻辑见 astock.analyzer.build_key_levels。</div>
</div>"""

    fund_body = "<p>资金流数据不可用（该标的可能不在资金流接口覆盖范围）。</p>"
    ff_chart = ""
    if res.fund:
        f = res.fund
        fund_body = f"""
<h3>主力资金：{f.pulse_pattern}</h3>
<div class="grid g3" style="margin-top:8px">
  <div><div class="kv"><span>最近一日</span><span class="{_cls(f.main_net_1d)}">{f.main_net_1d/1e4:+.0f}万</span></div>
  <div class="kv"><span>近3日累计</span><span class="{_cls(f.main_net_3d)}">{f.main_net_3d/1e4:+.0f}万</span></div>
  <div class="kv"><span>近5日累计</span><span class="{_cls(f.main_net_5d)}">{f.main_net_5d/1e4:+.0f}万</span></div>
  <div class="kv"><span>近10日累计</span><span class="{_cls(f.main_net_10d)}">{f.main_net_10d/1e4:+.0f}万</span></div>
  <div class="kv"><span>区间累计</span><span class="{_cls(f.main_net_total)}">{f.main_net_total/1e4:+.0f}万</span></div></div>
  <div><div class="kv"><span>区间峰值日</span><span>{f.peak_date}</span></div>
  <div class="kv"><span>峰值净额</span><span class="up">{f.peak_net/1e4:+.0f}万</span></div>
  <div class="kv"><span>回吐比例</span><span class="down">{f.giveback_ratio:.0%}</span></div>
  <div class="kv"><span>近期峰值日</span><span>{f.recent_peak_date}</span></div>
  <div class="kv"><span>近期回吐</span><span class="down">{f.recent_giveback:.0%}</span></div></div>
  <div><div class="kv"><span>大单结构</span><span>{f.retail_vs_main}</span></div>
  <div class="kv"><span>价资关系</span><span>{f.trend}</span></div></div>
</div>
<p style="margin-top:10px;font-size:13.5px"><b>脉冲判定逻辑</b>：取窗口内单日最大净流入为峰值，其后累计净额 ÷ 峰值 = 回吐比例。
≥95% 判为"完全回吐"（典型一日冲、多日撤），40%–95% 为"回吐中"。同时独立计算近 10 日的
"近期峰值"以捕捉当下正在发生的资金行为——避免长窗口下峰值落在数月前而失去现实意义。
这是识别题材炒作派发最直接的指标。</p>"""
        ff_chart = '<div id="chartFF" class="chart-sm"></div>'

    margin_body = ""
    if res.margin.available:
        m = res.margin
        div_html = f"<p style='color:var(--amber);margin-top:6px'>⚠ {m.divergence}</p>" if m.divergence else ""
        margin_body = f"""
<h3>杠杆资金</h3>
<div class="grid g2" style="margin-top:8px">
  <div>
    <div class="kv"><span>融资余额</span><span>{m.fin_balance/1e8:.2f}亿</span></div>
    <div class="kv"><span>占流通市值</span><span>{m.fin_ratio_pct}%</span></div>
    <div class="kv"><span>融券余额</span><span>{m.sec_balance/1e4:.0f}万</span></div>
  </div>
  <div>
    <div class="kv"><span>近3日净买入</span><span class="{_cls(m.net_buy_3d)}">{m.net_buy_3d:+.0f}万</span></div>
    <div class="kv"><span>近5日净买入</span><span class="{_cls(m.net_buy_5d)}">{m.net_buy_5d:+.0f}万</span></div>
    <div class="kv"><span>行为特征</span><span>{m.pattern}</span></div>
  </div>
</div>{div_html}"""

    chip_body = f"<h3>筹码结构</h3><p>{res.chip.verdict}</p>" if res.chip.available \
        else "<h3>筹码结构</h3><p>筹码数据不可用。</p>"
    val_body = f"<h3>估值</h3><p>{res.valuation.note or '数据不足'}</p>"

    fund_html = f"""
<div class="grid g2">
  <div class="card">
    <h2>资金面</h2>
    <div class="h2note">主力净额（万元），红=净流入，绿=净流出</div>
    {ff_chart}
    <div class="src">来源：腾讯自选股资金流接口，截至 {res.as_of}。识别"脉冲式资金"是本模块的核心目标。</div>
  </div>
  <div class="card">
    <h2>杠杆与筹码</h2>
    {margin_body}{chip_body}{val_body}
    <div class="src">来源：交易所两融数据（截至 {res.as_of}）、筹码成本接口、行情快照。融券余额分位需跨年数据对比，本工具不自动计算。</div>
  </div>
</div>
<div class="card">{fund_body}</div>"""

    warn_html = ""
    if res.warnings:
        items = "".join(f"<li>{x}</li>" for x in res.warnings)
        warn_html = f'<div class="warn"><h3>风险提示</h3><ul>{items}</ul></div>'

    mp_rows = "".join(
        f"<tr><td>{k}</td><td class='{_cls(m2['pct'])}'>{_fmt_pct(m2['pct'])}</td>"
        f"<td>{m2['hi']}</td><td>{m2['lo']}</td>"
        f"<td>{'上轨' if m2['hi'] >= res.tech.close >= m2['lo'] else ('创阶段新高' if m2['hi']==res.tech.close else '创阶段新低' if m2['lo']==res.tech.close else '区间内')}</td></tr>"
        for k, m2 in res.multi_period.items())
    mp_html = f"""
<div class="card">
  <h2>多周期收益与箱体</h2>
  <table><thead><tr><th>周期</th><th>涨跌幅</th><th>区间高</th><th>区间低</th><th>现价位置</th></tr></thead>
  <tbody>{mp_rows}</tbody></table>
  <div style="margin-top:12px">{box_html}</div>
  <div class="src">多周期收益按日K收盘价直接计算；箱体由 astock.technicals.detect_box 判定（要求上下沿各被接近≥2次）。</div>
</div>"""

    return _TEMPLATE.format(
        css=CSS, name=res.name, code=res.code, as_of=res.as_of,
        gen=res.generated_at, state=res.market_state, kpis=kpis, verdict=verdict_html,
        kchart='<div id="chartK" class="chart"></div>',
        ktable='<div id="tableK" class="hide"></div>',
        legs_html=legs_html, tech_html=tech_html, levels_html=levels_html,
        fund_html=fund_html, mp_html=mp_html, warn_html=warn_html,
        data_js="const D=" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) + ";",
    )


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{name}（{code}）走势分析 · {as_of}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>{css}</style></head><body><div class="wrap">

<header>
  <h1>{name} <span style="color:var(--ink3);font-weight:500">{code}</span></h1>
  <div class="sub">
    <span class="tag">数据截至 {as_of}</span>
    <span class="tag">{state}</span>
    <span class="tag">生成于 {gen}</span>
    <br>行情/资金/财务：腾讯自选股结构化接口（westock-data）｜ 分析框架：astock_analyzer v1.0
  </div>
</header>

{kpis}
{verdict}

<div class="card">
  <h2>日K 结构与均线体系</h2>
  <div class="h2note">近 95 个交易日，不复权。虚线为自动识别的关键压力/支撑位。可拖动缩放，或切换"数据表"查精确值。</div>
  <div class="tabs">
    <div class="tab on" data-t="k" data-target="chartK">走势图</div>
    <div class="tab" data-t="k" data-target="tableK">数据表</div>
  </div>
  {kchart}
  {ktable}
  <div class="src">量能单位：万手。数据来源：腾讯自选股日K接口。</div>
</div>

{legs_html}
{mp_html}
{tech_html}
{levels_html}
{fund_html}
{warn_html}

<div class="disc">
<b>免责声明</b>：以上内容基于公开数据和量化规则计算，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>

</div>
<script>
{data_js}
var UP='#d92b2b',DOWN='#15935a',LINE='#c9cfda',BLUE='#2563c9';
function tip(){{return{{trigger:'axis',axisPointer:{{type:'cross'}},
backgroundColor:'rgba(255,255,255,.96)',borderColor:'#e3e6ec',
textStyle:{{color:'#1a1d23',fontSize:12}},padding:[8,11]}};}}

echarts.init(document.getElementById('chartK')).setOption({{
  tooltip:tip(),
  legend:{{data:['日K','MA5','MA10','MA20','MA60','成交量'],top:0,textStyle:{{fontSize:11,color:'#4a5160'}},itemGap:12}},
  grid:[{{left:54,right:20,top:34,height:'56%'}},{{left:54,right:20,top:'72%',height:'17%'}}],
  xAxis:[
    {{type:'category',data:D.dates,axisLine:{{lineStyle:{{color:LINE}}}},axisLabel:{{fontSize:10,color:'#858c9b'}},splitLine:{{show:false}},min:'dataMin',max:'dataMax'}},
    {{type:'category',gridIndex:1,data:D.dates,axisLine:{{lineStyle:{{color:LINE}}}},axisLabel:{{show:false}},splitLine:{{show:false}},min:'dataMin',max:'dataMax'}}
  ],
  yAxis:[
    {{scale:true,splitLine:{{lineStyle:{{color:'#eef0f4'}}}},axisLabel:{{fontSize:10,color:'#858c9b'}}}},
    {{gridIndex:1,scale:true,splitNumber:2,splitLine:{{show:false}},axisLabel:{{fontSize:10,color:'#858c9b'}},name:'万手',nameTextStyle:{{fontSize:10,color:'#858c9b'}}}}
  ],
  dataZoom:[{{type:'inside',xAxisIndex:[0,1],start:55,end:100}},
             {{type:'slider',xAxisIndex:[0,1],start:55,end:100,height:15,bottom:4,borderColor:'#e3e6ec',
              fillerColor:'rgba(37,99,201,.10)',textStyle:{{fontSize:10,color:'#858c9b'}}}}],
  series:[
    {{name:'日K',type:'candlestick',data:D.ohlc,itemStyle:{{color:UP,color0:'#fff',borderColor:UP,borderColor0:DOWN}},
     markLine:{{silent:true,symbol:'none',label:{{fontSize:10,color:'#858c9b',formatter:p=>p.name}},
       data:(D.marks||[]).map(m=>({{yAxis:m.y,name:m.name,lineStyle:{{color:m.color,type:'dashed',width:1}}}}))}}}},
    {{name:'MA5',type:'line',data:D.ma5,smooth:true,showSymbol:false,lineStyle:{{width:1.2,color:'#c47d10'}}}},
    {{name:'MA10',type:'line',data:D.ma10,smooth:true,showSymbol:false,lineStyle:{{width:1.2,color:'#7c4dff'}}}},
    {{name:'MA20',type:'line',data:D.ma20,smooth:true,showSymbol:false,lineStyle:{{width:1.7,color:BLUE}}}},
    {{name:'MA60',type:'line',data:D.ma60,smooth:true,showSymbol:false,lineStyle:{{width:1.7,color:'#0d9488'}}}},
    {{name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:D.vols,
     itemStyle:{{color:function(p){{return D.ohlc[p.dataIndex][1]>=D.ohlc[p.dataIndex][0]?'rgba(217,43,43,.55)':'rgba(21,147,90,.55)';}}}}}}
  ]
}});

(function(){{
  if(!D.ffDates){{return;}}
  echarts.init(document.getElementById('chartFF')).setOption({{
    tooltip:tip(),
    legend:{{data:['主力净额','超大单','小单'],top:0,textStyle:{{fontSize:11,color:'#4a5160'}}}},
    grid:{{left:60,right:14,top:32,bottom:42}},
    xAxis:{{type:'category',data:D.ffDates,axisLine:{{lineStyle:{{color:LINE}}}},axisLabel:{{fontSize:9.5,color:'#858c9b',rotate:48}},splitLine:{{show:false}}}},
    yAxis:{{type:'value',splitLine:{{lineStyle:{{color:'#eef0f4'}}}},axisLabel:{{fontSize:10,color:'#858c9b',formatter:v=>(v/10000).toFixed(1)+'亿'}}}},
    series:[
      {{name:'主力净额',type:'bar',data:D.ffMain.map(v=>({{value:v,itemStyle:{{color:v>=0?'rgba(217,43,43,.82)':'rgba(21,147,90,.82)'}}}})),barMaxWidth:16}},
      {{name:'超大单',type:'line',data:D.ffJumbo,smooth:true,showSymbol:false,lineStyle:{{width:1.6,color:'#2563c9'}},itemStyle:{{color:'#2563c9'}}}},
      {{name:'小单',type:'line',data:D.ffSmall,smooth:true,showSymbol:false,lineStyle:{{width:1.3,color:'#c47d10',type:'dashed'}},itemStyle:{{color:'#c47d10'}}}}
    ]
  }});
}})();

(function(){{
  var rows=['<table><thead><tr><th>日期</th><th>开</th><th>高</th><th>低</th><th>收</th><th>涨跌%</th><th>量(万手)</th></tr></thead><tbody>'];
  for(var i=D.dates.length-1;i>=Math.max(0,D.dates.length-45);i--){{
    var o=D.ohlc[i],prev=i>0?D.ohlc[i-1][1]:o[1],chg=(o[1]/prev-1)*100;
    rows.push('<tr><td>'+D.dates[i]+'</td><td>'+o[0].toFixed(2)+'</td><td>'+o[3].toFixed(2)+'</td><td>'+o[2].toFixed(2)+'</td><td>'+o[1].toFixed(2)+'</td><td class="'+(chg>=0?'up':'down')+'">'+chg.toFixed(2)+'%</td><td>'+D.vols[i]+'</td></tr>');
  }}
  rows.push('</tbody></table>');
  document.getElementById('tableK').innerHTML=rows.join('');
}})();

document.querySelectorAll('.tab').forEach(function(t){{
  t.addEventListener('click',function(){{
    document.querySelectorAll('.tab[data-t="k"]').forEach(function(x){{x.classList.remove('on');}});
    t.classList.add('on');
    var tg=t.getAttribute('data-target');
    ['chartK','tableK'].forEach(function(id){{
      if(id.indexOf('chart')===0 && !document.getElementById(id)) return;
      document.getElementById(id).classList.toggle('hide',tg!==id);
    }});
    var ck=echarts.getInstanceByDom(document.getElementById('chartK'));
    if(ck) ck.resize();
  }});
}});

window.addEventListener('resize',function(){{
  ['chartK','chartFF'].forEach(function(id){{
    var el=document.getElementById(id);
    if(el && echarts.getInstanceByDom(el)) echarts.getInstanceByDom(el).resize();
  }});
}});
</script>
</body></html>
"""


def save_html(res: AnalysisResult, outdir: str, bars: int = 95) -> str:
    os.makedirs(outdir, exist_ok=True)
    fn = f"{res.name}_{res.code}_{res.as_of.replace('-', '')}.html"
    path = os.path.join(outdir, fn)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(res, bars))
    return path