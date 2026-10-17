"""资金面分析层：主力资金脉冲、筹码结构、两融行为、机构筹码变化。

核心识别三个"资金说谎"的情形：
1. 脉冲式资金（单日巨量流入后连续流出，净额被回吐）
2. 大单出货 + 散单接盘（主力/散户净额反向）
3. 融资盘高位追高后撤退（融资余额与股价背离）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class FundPulse:
    """主力资金脉冲结构判定。"""
    days: int
    main_net_1d: float          # 最近一日主力净额（元）
    main_net_3d: float
    main_net_5d: float
    main_net_10d: float
    main_net_total: float
    peak_date: str              # 区间内最大单日净流入
    peak_net: float
    days_since_peak: int
    giveback_ratio: float       # 峰值后累计流出 / 峰值流入，>1 表示流出已超峰值（资金反转为净流出）
    recent_peak_date: str       # 近 10 日内的最大净流入（更贴近当下）
    recent_peak_net: float
    recent_giveback: float
    pulse_pattern: str          # 综合资金形态
    retail_vs_main: str         # 主力与散单关系
    trend: str                  # 价资关系
    retail_dir: int = 0         # +1 机构吸筹 / -1 机构出货 / 0 同向
    daily: List[Dict] = field(default_factory=list)


def analyze_fund_flow(ff: pd.DataFrame, price: pd.Series | None = None) -> Optional[FundPulse]:
    """判断主力资金是脉冲还是趋势。"""
    if ff is None or ff.empty or "main_net" not in ff.columns:
        return None
    d = ff.dropna(subset=["main_net"]).copy().sort_values("date").reset_index(drop=True)
    if len(d) < 3:
        return None

    main = d["main_net"].astype(float)
    net1 = float(main.iloc[-1])
    net3 = float(main.tail(3).sum())
    net5 = float(main.tail(5).sum())
    net10 = float(main.tail(min(10, len(main))).sum())

    # ── 区间峰值：最大单日净流入 ──
    pos = main[main > 0]
    has_peak = len(pos) > 0 and float(pos.max()) > 5e6
    if has_peak:
        pidx = int(pos.idxmax())
        peak_net = float(pos.max())
    else:
        pidx, peak_net = -1, 0.0
    peak_date = str(pd.Timestamp(d["date"].iloc[pidx]).date()) if has_peak else "—"
    since = len(d) - 1 - pidx if has_peak else 0
    after = float(main.iloc[pidx + 1:].sum()) if has_peak else 0.0
    giveback = -after / peak_net if peak_net > 0 else 0.0

    # ── 近期峰值：近 10 个交易日内，捕捉"当下正在发生"的资金行为 ──
    recent = main.tail(10)
    rpos = recent[recent > 0]
    if len(rpos) > 0 and float(rpos.max()) > 5e6:
        ridx = int(rpos.idxmax())
        r_peak = float(rpos.max())
        r_date = str(pd.Timestamp(d["date"].iloc[ridx]).date())
        r_after = float(main.iloc[ridx + 1:].sum())
        r_gb = -r_after / r_peak
    else:
        ridx, r_peak, r_date, r_gb = -1, 0.0, "—", 0.0

    # ── 巨量流出日（反向脉冲）──
    worst = float(main.min())
    dump_note = ""
    if worst < -1e7:
        widx = int(main.idxmin())
        if float(main.iloc[widx + 1:].sum()) < abs(worst) * 0.5:
            dump_note = (f"{str(pd.Timestamp(d['date'].iloc[widx]).date())} "
                         f"单日巨量流出 {worst/1e4:.0f} 万且其后未修复")

    # ── 综合定性：近期脉冲优先，其次区间脉冲 ──
    if r_gb >= 0.95:
        pattern = f"近期脉冲：{r_date} 净流入 {r_peak/1e4:+.0f} 万已完全回吐"
    elif r_gb >= 0.4:
        pattern = f"近期脉冲回吐中：{r_date} 净流入 {r_peak/1e4:+.0f} 万已回吐 {r_gb:.0%}"
    elif giveback >= 0.95 and has_peak:
        pattern = (f"区间脉冲：{peak_date} 净流入 {peak_net/1e4:+.0f} 万已完全回吐")
    elif giveback >= 0.4 and has_peak:
        pattern = f"区间脉冲回吐：{peak_date} 峰值已回吐 {giveback:.0%}"
    elif net10 > 0 and main.tail(5).mean() > 0:
        pattern = "持续净流入"
    elif net10 < 0 and main.tail(5).mean() < 0:
        pattern = "持续净流出"
    else:
        pattern = "震荡（多空平衡）"
    if dump_note:
        pattern += f"｜{dump_note}"

    # 主力 vs 散单（大中小单四档）
    tail = d.tail(3)
    m3 = float(tail["main_net"].sum()) if "main_net" in tail else 0.0
    s3 = float(tail["small_net"].sum()) if "small_net" in tail else 0.0
    b3 = float(tail["block_net"].sum()) if "block_net" in tail else 0.0
    j3 = float(tail["jumbo_net"].sum()) if "jumbo_net" in tail else 0.0
    mid3 = float(tail["mid_net"].sum()) if "mid_net" in tail else 0.0
    retail3 = s3 + mid3  # 中小单 ≈ 散户

    if m3 < 0 < retail3:
        retail = f"机构出货、散户接盘（3日主力 {m3/1e4:.0f}万 / 中小单 {retail3/1e4:+.0f}万）"
        retail_dir = -1
    elif m3 > 0 > retail3:
        retail = f"机构吸筹、散户出货（3日主力 {m3/1e4:+.0f}万 / 中小单 {retail3/1e4:.0f}万）"
        retail_dir = 1
    elif j3 > 0 and m3 > 0:
        retail, retail_dir = "超大单主导买入", 1
    elif j3 < 0 and m3 < 0:
        retail, retail_dir = "超大单主导卖出", -1
    else:
        retail, retail_dir = "主力与散单同向", 0
    if b3 < 0 and abs(b3) > abs(m3) * 0.5:
        retail += f"（含大单净流出 {b3/1e4:.0f} 万）"

    # 价格趋势与资金背离
    if price is not None and len(price) >= len(tail):
        p3 = float(price.tail(3).iloc[-1]) - float(price.tail(3).iloc[0])
        if p3 > 0 and m3 < 0:
            trend = "价涨钱走（背离）"
        elif p3 < 0 and m3 > 0:
            trend = "价跌钱进（背离）"
        elif p3 > 0:
            trend = "价涨钱进（同步）"
        elif p3 < 0:
            trend = "价跌钱出（同步）"
        else:
            trend = "横盘"
    else:
        trend = "数据不足"

    daily = [{"date": str(pd.Timestamp(r["date"]).date()),
              "main": round(float(r["main_net"]) / 1e4, 0),
              "jumbo": round(float(r.get("jumbo_net", np.nan)) / 1e4, 0),
              "small": round(float(r.get("small_net", np.nan)) / 1e4, 0),
              "close": round(float(r.get("close", np.nan)), 2) if "close" in r else None}
             for _, r in d.iterrows()]

    return FundPulse(days=len(d), main_net_1d=net1, main_net_3d=net3, main_net_5d=net5,
                     main_net_10d=net10, main_net_total=float(main.sum()),
                     peak_date=peak_date, peak_net=peak_net,
                     days_since_peak=since, giveback_ratio=round(giveback, 2),
                     recent_peak_date=r_date, recent_peak_net=r_peak,
                     recent_giveback=round(r_gb, 2),
                     pulse_pattern=pattern, retail_vs_main=retail,
                     retail_dir=retail_dir, trend=trend, daily=daily)


@dataclass
class MarginState:
    available: bool
    fin_balance: float = 0.0
    fin_ratio_pct: float = 0.0        # 融资余额 / 流通市值
    sec_balance: float = 0.0
    net_buy_3d: float = 0.0
    net_buy_5d: float = 0.0
    pattern: str = "无两融数据"
    divergence: str = ""


def analyze_margin(mg: pd.DataFrame, float_mv: Optional[float] = None,
                   price: Optional[pd.Series] = None) -> MarginState:
    """两融行为分析：杠杆资金的追高/撤退。"""
    if mg is None or mg.empty or "finance_balance" not in mg.columns:
        return MarginState(available=False)

    d = mg.dropna(subset=["finance_balance"]).copy().sort_values("date").reset_index(drop=True)
    if len(d) < 2:
        return MarginState(available=False)

    last = d.iloc[-1]
    fb = float(last["finance_balance"])
    sb = float(last.get("security_balance", 0) or 0)
    ratio = (fb / float_mv * 100) if float_mv else 0.0

    nbuy = d["finance_net_buy"] if "finance_net_buy" in d else pd.Series([0.0] * len(d))
    n3 = float(nbuy.tail(3).sum())
    n5 = float(nbuy.tail(min(5, len(d))).sum())
    recent = d.tail(5)
    up_days = int((recent["finance_balance"].diff() > 0).sum())

    if n3 > 0 and up_days >= 3:
        pattern = f"杠杆资金近3日净买入 {n3/1e4:+.0f} 万，追高"
    elif n3 < 0:
        pattern = f"杠杆资金近3日净卖出 {n3/1e4:+.0f} 万，撤退"
    else:
        pattern = f"杠杆资金近3日基本持平（{n3/1e4:+.0f} 万）"

    div = ""
    if price is not None and len(price) >= len(d):
        pchg = float(price.iloc[-1]) - float(price.iloc[-len(d)])
        if pchg > 0 and n5 < 0:
            div = "股价上涨但融资净流出——杠杆资金借反弹出货"
        elif pchg < 0 and n5 > 0:
            div = "股价下跌但融资净流入——杠杆资金逆势抄底"
    return MarginState(available=True, fin_balance=fb, fin_ratio_pct=round(ratio, 2),
                       sec_balance=sb, net_buy_3d=round(n3 / 1e4, 0),
                       net_buy_5d=round(n5 / 1e4, 0), pattern=pattern, divergence=div)


@dataclass
class ChipState:
    available: bool
    avg_cost: float = 0.0
    profit_rate: float = 0.0      # 获利盘比例 %
    conc70: float = 0.0
    conc90: float = 0.0
    verdict: str = ""


def analyze_chip(chip: Optional[Dict], close: float) -> ChipState:
    if not chip:
        return ChipState(available=False)
    ac = float(chip.get("chipAvgCost", 0) or 0)
    pr = float(chip.get("chipProfitRate", 0) or 0)
    c70 = float(chip.get("chipConcentration70", 0) or 0)
    c90 = float(chip.get("chipConcentration90", 0) or 0)
    if ac <= 0:
        return ChipState(available=False)
    pos = "上方" if close < ac else "下方"
    v = (f"筹码平均成本 {ac:.2f} 元在现价 {pos}，获利盘 {pr:.1f}%；"
         f"90% 筹码集中度 {c90:.1f}（{'集中' if c90 < 15 else '分散'}）")
    return ChipState(available=True, avg_cost=ac, profit_rate=pr,
                     conc70=c70, conc90=c90, verdict=v)


@dataclass
class ValuationView:
    pe_ttm: Optional[float]
    pb: Optional[float]
    dividend_ttm: Optional[float]
    total_mv: Optional[float]
    note: str = ""


def analyze_valuation(q: Dict, growth: Optional[Dict] = None) -> ValuationView:
    def num(k):
        try:
            v = float(q.get(k, 0) or 0)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    pe, pb, dv, mv = num("pe_ratio"), num("pb_ratio"), num("dividend_ratio_ttm"), num("total_market_cap")
    notes = []
    if pe:
        if pe > 50:
            notes.append(f"PE-TTM {pe:.1f}× 显著偏高")
        elif pe > 25:
            notes.append(f"PE-TTM {pe:.1f}× 偏贵")
        elif pe > 0:
            notes.append(f"PE-TTM {pe:.1f}×")
    if growth and growth.get("gross_margin") is not None:
        gm = growth["gross_margin"]
        if gm < 10:
            notes.append(f"毛利率仅 {gm:.2f}%，属加工费模式，PE 溢价缺乏业绩支撑")
        elif gm > 30:
            notes.append(f"毛利率 {gm:.2f}%，PE 溢价有基本面支撑")
    return ValuationView(pe_ttm=pe, pb=pb, dividend_ttm=dv, total_mv=mv, note="；".join(notes))