"""分析编排层：把行情/技术/资金/估值合成为一份可解释的研报式结论。

设计原则：
- 每个结论必须能追溯到具体数据（trend_legs / key_levels / fund / valuation）
- 不给"看着像"的判断，只给带阈值的规则判断
- 支持多周期（1年/6月/3月/1月）横向验证
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import datasource as ds
from . import moneyflow as mf
from . import technicals as ta


# ---------------------------------------------------------------- 结果容器

@dataclass
class KeyLevel:
    kind: str          # 压力 / 支撑
    label: str
    low: float
    high: float
    basis: str
    significance: str


@dataclass
class AnalysisResult:
    code: str
    name: str
    as_of: str
    generated_at: str
    market_state: str
    quote: Dict[str, Any]
    kline: pd.DataFrame
    trend_legs: List[ta.TrendLeg]
    swings: List[ta.Swing]
    box: Optional[ta.Box]
    fib: Dict[str, Dict[str, float]]
    tech: ta.TechState
    fund: Optional[mf.FundPulse]
    margin: mf.MarginState
    chip: mf.ChipState
    valuation: mf.ValuationView
    patterns: List[Dict]
    gaps: List[Dict]
    key_levels: List[KeyLevel]
    multi_period: Dict[str, Dict[str, float]]
    verdict: Dict[str, Any]
    narrative: List[str]
    warnings: List[str]

    def to_text(self) -> str:
        return render_text(self)


# ---------------------------------------------------------------- 关键价位

def build_key_levels(d: pd.DataFrame, tech: ta.TechState, chip: mf.ChipState,
                     box: Optional[ta.Box]) -> List[KeyLevel]:
    """从均线/布林/摆动点/筹码/BOLL 聚合出压力与支撑带，并按技术依据排序。"""
    c = tech.close
    r = d.iloc[-1]
    levels: List[KeyLevel] = []

    # 压力：布林上轨 / 前高 / 箱体上沿 / 长均线
    up_cands = []
    if not np.isnan(r.get("boll_up", np.nan)):
        up_cands.append(("BOLL 上轨", float(r["boll_up"]), f"20日布林上轨，波动率上沿"))
    for n in (120, 250):
        if not np.isnan(r.get(f"ma{n}", np.nan)) and float(r[f"ma{n}"]) > c:
            up_cands.append((f"MA{n}", float(r[f"ma{n}"]), f"{n} 日均线，长期趋势分界"))
    if box:
        up_cands.append(("箱体上沿", box.high, f"近{box.bars}日箱体高点 {box.high_date}"))
    recent_high = float(d.tail(20)["high"].max())
    if recent_high > c:
        up_cands.append(("20日高点", recent_high, "近20日最高价，短期强阻力"))

    seen = set()
    for name, px, basis in sorted(up_cands, key=lambda x: x[1]):
        key = round(px, 1)
        if key in seen or px <= c * 1.001:
            continue
        seen.add(key)
        sig = "需主力单日净流入显著放大 + 放量收实体突破才有效" if px - c < 0.08 else "中期强阻力，站上才谈趋势修复"
        levels.append(KeyLevel("压力", name, px, px, basis, sig))

    # 支撑：筹码成本 / MA20 / BOLL中轨 / 近期低点
    dn_cands = []
    if chip.available:
        dn_cands.append(("筹码平均成本", chip.avg_cost, f"获利盘 {chip.profit_rate:.1f}% 的成本线"))
    if not np.isnan(r.get("ma20", np.nan)) and float(r["ma20"]) < c:
        dn_cands.append(("MA20", float(r["ma20"]), "20日线，中短期多空分界"))
    if not np.isnan(r.get("boll_mid", np.nan)) and float(r["boll_mid"]) < c:
        dn_cands.append(("BOLL 中轨", float(r["boll_mid"]), "20日布林中轨"))
    if not np.isnan(r.get("ma60", np.nan)) and float(r["ma60"]) < c:
        dn_cands.append(("MA60", float(r["ma60"]), "60日线，中期趋势线"))
    if box:
        dn_cands.append(("箱体下沿", box.low, f"近{box.bars}日箱体低点 {box.low_date}"))
    recent_low = float(d.tail(20)["low"].min())
    if recent_low < c:
        dn_cands.append(("20日低点", recent_low, "近20日最低价，短线止损参考"))

    seen = set()
    for name, px, basis in sorted(dn_cands, key=lambda x: -x[1]):
        key = round(px, 1)
        if key in seen or px >= c * 0.999:
            continue
        seen.add(key)
        sig = ("最强支撑带，多重技术位重合" if len(seen) == 1 and len(up_cands) else
               "跌破则结构转弱")
        levels.append(KeyLevel("支撑", name, px, px, basis, sig))

    return levels


# ---------------------------------------------------------------- 多周期

def multi_period_return(d: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    c = d["close"]
    out = {}
    for label, days in (("5日", 5), ("10日", 10), ("20日", 20), ("60日", 60), ("120日", 120)):
        if len(c) > days:
            out[label] = {"pct": round((float(c.iloc[-1]) / float(c.iloc[-days - 1]) - 1) * 100, 2),
                          "hi": round(float(d["high"].tail(days).max()), 2),
                          "lo": round(float(d["low"].tail(days).min()), 2)}
    return out


# ---------------------------------------------------------------- 结论

def build_verdict(res_k: pd.DataFrame, tech: ta.TechState, fund: Optional[mf.FundPulse],
                  chip: mf.ChipState, val: mf.ValuationView, mp: Dict[str, Dict[str, float]],
                  box: Optional[ta.Box]) -> Dict[str, Any]:
    """规则化打分：趋势 / 资金 / 估值 三维度 → 综合定性。"""
    score, reasons = 0, []

    # 趋势维度
    if tech.ma_aligned == "多头排列":
        score += 2; reasons.append("均线多头排列")
    if tech.close > tech.ma_detail.get("ma60", tech.close):
        score += 1; reasons.append("站上 MA60，中期趋势转暖")
    if tech.close < tech.ma_detail.get("ma120", 0):
        score -= 1; reasons.append("仍在 MA120 下方，中长期未修复")
    if tech.macd_state == "红柱走强":
        score += 1; reasons.append("MACD 红柱走强")
    elif tech.macd_state == "红柱缩短":
        reasons.append("MACD 红柱缩短，上攻动能衰减")
    if tech.boll_pos == "触及/突破上轨":
        score -= 0.5; reasons.append("触及布林上轨，短期有回踩需求")
    if mp.get("20日", {}).get("pct", 0) > 15:
        reasons.append(f"20日累计涨幅 {mp['20日']['pct']:+.1f}%，短期偏热")

    # 资金维度
    if fund:
        if "脉冲" in fund.pulse_pattern:
            score -= 1.5; reasons.append(f"主力资金{fund.pulse_pattern}")
        elif "持续净流入" in fund.pulse_pattern:
            score += 1.5; reasons.append("主力资金持续净流入")
        elif "持续净流出" in fund.pulse_pattern:
            score -= 1.5; reasons.append("主力资金持续净流出")
        if fund.retail_dir < 0:
            score -= 1; reasons.append(fund.retail_vs_main)
        elif fund.retail_dir > 0:
            score += 0.5; reasons.append(fund.retail_vs_main)
        if "背离" in fund.trend:
            score -= 0.5; reasons.append(f"价资{fund.trend}")

    # 估值维度
    if val.pe_ttm and val.pe_ttm > 40:
        score -= 1; reasons.append(f"PE-TTM {val.pe_ttm:.1f}× 偏高")
    elif val.pe_ttm and val.pe_ttm > 0 and val.pe_ttm < 20:
        score += 0.5; reasons.append(f"PE-TTM {val.pe_ttm:.1f}× 不高")

    # 综合定性
    if score >= 3:
        label, tone = "偏强", "多头占优，回调可观察承接"
    elif score >= 1:
        label, tone = "中性偏多", "结构性机会，需确认关键位突破"
    elif score > -1:
        label, tone = "中性", "多空平衡，等待方向选择"
    elif score > -3:
        label, tone = "中性偏空", "反弹遇阻概率较高，注意仓位"
    else:
        label, tone = "偏空", "下行趋势未改，反弹以减仓思路对待"

    return {"score": round(score, 1), "label": label, "tone": tone, "reasons": reasons}


def build_narrative(name: str, tech: ta.TechState, fund: Optional[mf.FundPulse],
                    box: Optional[ta.Box], legs: List[ta.TrendLeg],
                    val: mf.ValuationView, chip: mf.ChipState) -> List[str]:
    out = []
    if legs:
        last = legs[-1]
        out.append(
            f"最近一段趋势为{last.label}（{last.start_date} {last.start_price} → "
            f"{last.end_date} {last.end_price}，{last.pct:+.1f}%），"
            f"区间高 {last.high} / 低 {last.low}，日均成交 {last.avg_vol_wan:.0f} 万手。"
        )
    if box:
        out.append(
            f"近 {box.bars} 日形成 {box.low}–{box.high} 的箱体（振幅 {box.width_pct:.1f}%），"
            f"现价 {tech.close} 位于箱内{'上部' if tech.close > (box.low + box.high) / 2 else '下部'}。"
        )
    out.append(
        f"均线结构：{tech.ma_aligned}；{tech.net_bias}。"
        f"量能方面{tech.vol_state}（量比 {tech.vol_ratio}），ATR 占价 {tech.atr_pct:.1f}%。"
    )
    if fund:
        out.append(
            f"资金面：近 {fund.days} 日主力净额 5日 {fund.main_net_5d/1e4:+.0f} 万 / "
            f"10日 {fund.main_net_10d/1e4:+.0f} 万；{fund.pulse_pattern}；{fund.retail_vs_main}；{fund.trend}。"
        )
    if chip.available:
        out.append(f"筹码面：{chip.verdict}。")
    if val.note:
        out.append(f"估值面：{val.note}。")
    return out


def build_warnings(tech: ta.TechState, fund: Optional[mf.FundPulse],
                   chip: mf.ChipState, val: mf.ValuationView,
                   margin: mf.MarginState) -> List[str]:
    w = []
    if "红柱缩短" in tech.macd_state:
        w.append("MACD 红柱缩短：上攻动能衰减，若次日不能重回放量则大概率二次回踩")
    if tech.boll_pos == "触及/突破上轨":
        w.append("股价触及布林上轨，短期均值回归压力增大")
    if tech.kdj_state == "高位钝化":
        w.append("KDJ 高位钝化，超买区钝化不等于必然回落，但需警惕")
    if fund and fund.recent_giveback >= 0.4:
        tail = "，净流向已反转" if fund.recent_giveback > 1 else ""
        w.append(f"近期主力资金呈脉冲特征：{fund.recent_peak_date} 单日净流入 "
                 f"{fund.recent_peak_net/1e4:+.0f} 万，其后回吐为其 "
                 f"{fund.recent_giveback:.2f} 倍{tail}")
    elif fund and fund.giveback_ratio >= 0.4:
        gb = fund.giveback_ratio
        tail = "，净流向已由净流入反转为净流出" if gb > 1 else ""
        w.append(f"区间主力资金呈脉冲特征：{fund.peak_date} 单日净流入 {fund.peak_net/1e4:+.0f} 万，"
                 f"其后 {fund.days_since_peak} 个交易日累计流出为峰值的 {gb:.2f} 倍{tail}")
    if fund and fund.retail_dir < 0:
        w.append("存在大资金出货、散户接盘的结构（近3日主力净额与中小单净额反向）")
    elif fund and fund.retail_dir > 0:
        w.append("近3日主力净流入而中小单净流出，机构呈吸筹特征")
    if margin.available and margin.divergence:
        w.append(f"杠杆资金背离：{margin.divergence}")
    if chip.available and chip.profit_rate > 80:
        w.append(f"获利盘 {chip.profit_rate:.1f}% 偏高，一旦破位易触发兑现盘")
    if chip.available and chip.conc90 > 25:
        w.append(f"筹码分散度较高（90% 集中度 {chip.conc90:.1f}），缺乏明确主力区")
    if val.pe_ttm and val.pe_ttm > 40:
        w.append(f"PE-TTM {val.pe_ttm:.1f}×，估值对业绩下修的容忍度低")
    return w


# ---------------------------------------------------------------- 主入口

def analyze(code: str, lookback_days: int = 300, with_margin: bool = True,
            flow_days: int = 30, verbose: bool = False) -> AnalysisResult:
    """对单只 A 股做完整走势分析。

    Args:
        code: 股票代码，支持 600577 / sh600577 / 600577.SH
        lookback_days: K 线回溯根数
        with_margin: 是否拉取两融数据
        flow_days: 资金流分析窗口（交易日）。脉冲判定是"近期资金行为"，
            窗口过长会让峰值落在数月前而失去意义，默认 30 日。
    """
    c = normalize(code)
    q = ds.get_quote(c)
    k = ds.get_kline(c, limit=lookback_days)
    if k.empty:
        raise ds.DataUnavailable(f"{c} 无K线数据")
    d = ta.add_indicators(k)

    as_of = ds.last_trade_date(k)
    in_session, _, _ = ds.is_trading_day()
    market_state = "盘中（数据为最新快照）" if in_session else f"休市／非交易时段，最近收盘 {as_of}"

    # 技术
    tech = ta.tech_state(d)
    swings = ta.find_swings(d, 10)
    legs = ta.major_legs(d)
    box = ta.detect_box(d, lookback=60)
    il, ih = ta.dominant_swing(d, 250)
    fib = ta.fib_levels(float(d["low"].iloc[il]), float(d["high"].iloc[ih]))
    patterns = ta.detect_reversal_patterns(d)
    gaps = ta.gap_events(d)

    # 资金：窗口按交易日折算，留 1.6 倍余量覆盖节假日
    flow_start = (pd.Timestamp(as_of) - pd.Timedelta(days=int(flow_days * 1.7))).strftime("%Y-%m-%d")
    ff = ds.get_fund_flow(c, start=flow_start, end=as_of)
    fund = mf.analyze_fund_flow(ff, price=d["close"].tail(len(ff) if not ff.empty else 1)) \
        if not ff.empty else None

    mg = pd.DataFrame()
    if with_margin:
        mg = ds.get_margin(c, start=(pd.Timestamp(as_of) - pd.Timedelta(days=90)).strftime("%Y-%m-%d"),
                           end=as_of)
    float_mv = float(q.data.get("circulating_market_cap") or 0) or None
    margin = mf.analyze_margin(mg, float_mv, price=d["close"])

    chip = mf.analyze_chip(ds.get_chip(c), tech.close)
    val = mf.analyze_valuation(q.data)

    levels = build_key_levels(d, tech, chip, box)
    mp = multi_period_return(d)
    verdict = build_verdict(d, tech, fund, chip, val, mp, box)
    narrative = build_narrative(q.name, tech, fund, box, legs, val, chip)
    warnings = build_warnings(tech, fund, chip, val, margin)

    bj = timezone(timedelta(hours=8))
    return AnalysisResult(
        code=c, name=q.name or c, as_of=as_of,
        generated_at=datetime.now(bj).strftime("%Y-%m-%d %H:%M"),
        market_state=market_state, quote=q.data, kline=d,
        trend_legs=legs, swings=swings, box=box, fib=fib, tech=tech,
        fund=fund, margin=margin, chip=chip, valuation=val,
        patterns=patterns, gaps=gaps, key_levels=levels, multi_period=mp,
        verdict=verdict, narrative=narrative, warnings=warnings,
    )


def normalize(code: str) -> str:
    return ds.normalize_code(code)


# ---------------------------------------------------------------- 文本输出

def render_text(r: AnalysisResult) -> str:
    L = []
    A = L.append
    A(f"{r.name}（{r.code}）走势分析　|　数据截至 {r.as_of}　|　{r.market_state}")
    A("=" * 72)
    q = r.quote
    A(f"收盘 {q.get('price')}（{(q.get('change_percent') or 0):+.2f}%）　"
      f"成交额 {(q.get('amount') or 0)/1e8:.2f}亿　换手 {q.get('turnover_rate')}%　"
      f"PE-TTM {q.get('pe_ratio')}　PB {q.get('pb_ratio')}　"
      f"总市值 {(q.get('total_market_cap') or 0)/1e8:.1f}亿")
    A("")

    v = r.verdict
    A(f"【综合判断】{v['label']}（评分 {v['score']}）—— {v['tone']}")
    for x in v["reasons"]:
        A(f"   · {x}")
    A("")

    A("【走势结构】")
    for lg in r.trend_legs[-4:]:
        A(f"   {lg.label}：{lg.start_date} {lg.start_price} → {lg.end_date} {lg.end_price} "
          f"（{lg.pct:+.1f}%，高{lg.high}/低{lg.low}，{lg.bars}根K线）")
    if r.box:
        A(f"   箱体：{r.box.low}–{r.box.high}（{r.box.bars}日，振幅 {r.box.width_pct:.1f}%）")
    A("   多周期：" + "　".join(f"{k}{m['pct']:+.1f}%" for k, m in r.multi_period.items()))
    A("")

    A("【技术状态】")
    t = r.tech
    A(f"   均线：{t.ma_aligned}　{t.net_bias}")
    A(f"   偏离：{'　'.join(f'{k}{v:+.1f}%' for k, v in t.ma_bias_pct.items())}")
    A(f"   BOLL：上{t.boll_up} 中{t.boll_mid} 下{t.boll_low}　现价位于{t.boll_pos}")
    A(f"   MACD：{t.macd_state}（柱 {t.macd_hist_prev} → {t.macd_hist_now}）　"
      f"KDJ {t.kdj_state}　RSI6 {t.rsi6}（{t.rsi_state}）")
    A(f"   量能：{t.vol_state}（量比 {t.vol_ratio}）　ATR% {t.atr_pct}")
    if r.patterns:
        A("   近15日形态：" + "；".join(f"{p['date']} {p['pattern']}" for p in r.patterns[-5:]))
    if r.gaps:
        A("   缺口：" + "；".join(f"{g['date']} {g['kind']} {g['low']}→{g['high']}"
                                  f"（{'已回补' if g['filled'] else '未回补'}）" for g in r.gaps[-3:]))
    A("")

    A("【关键价位】")
    for lv in r.key_levels[:10]:
        A(f"   {lv.kind} {lv.label} {lv.low:.2f}　依据：{lv.basis}")
    A("")

    A("【资金与筹码】")
    if r.fund:
        f = r.fund
        A(f"   主力：5日 {f.main_net_5d/1e4:+.0f}万　10日 {f.main_net_10d/1e4:+.0f}万　"
          f"最近一日 {f.main_net_1d/1e4:+.0f}万")
        A(f"   形态：{f.pulse_pattern}")
        A(f"   结构：{f.retail_vs_main}　｜　{f.trend}")
    else:
        A("   资金流数据不可用")
    if r.margin.available:
        A(f"   两融：融资余额 {r.margin.fin_balance/1e8:.2f}亿（占流通市值 {r.margin.fin_ratio_pct}%）　"
          f"{r.margin.pattern}")
        if r.margin.divergence:
            A(f"   ⚠ {r.margin.divergence}")
    if r.chip.available:
        A(f"   筹码：{r.chip.verdict}")
    A(f"   估值：{r.valuation.note or 'N/A'}")
    A("")

    if r.warnings:
        A("【风险提示】")
        for w in r.warnings:
            A(f"   ! {w}")
        A("")
    return "\n".join(L)