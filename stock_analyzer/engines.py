"""技术分析引擎层：指标计算、形态识别、结构判定、信号投票。

全部基于 OHLCV 纯 Python 实现，不依赖 skill 目录，可独立复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .datasource import Bar


# ================================================================ 指标计算

def sma(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    acc: list[float] = []
    for v in vals:
        acc.append(v)
        if len(acc) > n:
            acc.pop(0)
        out.append(round(sum(acc) / len(acc), 4) if len(acc) == n else None)
    return out


def ema(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2.0 / (n + 1)
    prev: float | None = None
    for v in vals:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(round(prev, 4))
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, sig: int = 9) -> dict[str, list]:
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [
        (a - b) if (a is not None and b is not None) else 0.0 for a, b in zip(ef, es)
    ]
    dea = ema(dif, sig)
    hist = [round((a - b) * 2, 4) for a, b in zip(dif, dea)]
    return {
        "dif": [round(x, 4) for x in dif],
        "dea": [round(x, 4) for x in dea],
        "hist": hist,
    }


def kdj(bars: list[Bar], n: int = 9, m1: int = 3, m2: int = 3) -> dict[str, list]:
    k_list: list[float | None] = []
    d_list: list[float | None] = []
    j_list: list[float | None] = []
    k, d = 50.0, 50.0
    for i in range(len(bars)):
        if i < n - 1:
            k_list.append(None); d_list.append(None); j_list.append(None)
            continue
        w = bars[i - n + 1 : i + 1]
        hh = max(b.high for b in w)
        ll = min(b.low for b in w)
        rsv = 50.0 if hh == ll else (bars[i].close - ll) / (hh - ll) * 100
        k = (rsv + (m1 - 1) * k) / m1
        d = (k + (m2 - 1) * d) / m2
        k_list.append(round(k, 4)); d_list.append(round(d, 4))
        j_list.append(round(3 * k - 2 * d, 4))
    return {"k": k_list, "d": d_list, "j": j_list}


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = []
    up = dn = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        u, d = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            up += u / n if i > 1 else 0.0
            dn += d / n if i > 1 else 0.0
            if i < n:
                out.append(None)
                continue
        else:
            up = (up * (n - 1) + u) / n
            dn = (dn * (n - 1) + d) / n
        out.append(round(100 * up / (up + dn), 4) if (up + dn) > 0 else 50.0)
    return out


def boll(closes: list[float], n: int = 20, k: float = 2.0) -> dict[str, list]:
    mid = sma(closes, n)
    up: list[float | None] = []
    lo: list[float | None] = []
    for i in range(len(closes)):
        if mid[i] is None:
            up.append(None); lo.append(None)
            continue
        w = closes[i - n + 1 : i + 1]
        var = sum((x - mid[i]) ** 2 for x in w) / n
        sd = var ** 0.5
        up.append(round(mid[i] + k * sd, 4))
        lo.append(round(mid[i] - k * sd, 4))
    return {"upper": up, "mid": mid, "lower": lo}


def atr(bars: list[Bar], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return round(sum(trs[-n:]) / n, 4)


def obv(bars: list[Bar]) -> list[float]:
    out = [0.0]
    for i in range(1, len(bars)):
        if bars[i].close > bars[i - 1].close:
            out.append(out[-1] + bars[i].volume)
        elif bars[i].close < bars[i - 1].close:
            out.append(out[-1] - bars[i].volume)
        else:
            out.append(out[-1])
    return out


# ================================================================ 形态识别

@dataclass
class CandleSignal:
    index: int
    date: str
    pattern: str
    bias: int  # 1 多头 -1 空头
    note: str = ""


def _body(b: Bar) -> float:
    return abs(b.close - b.open)


def _rng(b: Bar) -> float:
    return max(b.high - b.low, 1e-9)


def detect_candles(bars: list[Bar]) -> list[CandleSignal]:
    """识别 12 种常见 K 线形态。"""
    out: list[CandleSignal] = []
    n = len(bars)
    for i in range(max(2, n - 90), n):
        b0, b1, b2 = bars[i - 2], bars[i - 1], bars[i]
        body, rng = _body(b2), _rng(b2)
        up_wick = b2.high - max(b2.open, b2.close)
        dn_wick = min(b2.open, b2.close) - b2.low
        p, q = None, None

        if body < rng * 0.12 and rng > 0:
            # 锤子 / 上吊
            if dn_wick > body * 2 and up_wick < body * 0.6:
                trend_down = i > 3 and b2.close < bars[i - 3].close
                p = ("锤子线" if not trend_down else "上吊线", 1 if not trend_down else -1,
                     "下影线长，实体小" + ("（下跌末端，潜在反转）" if not trend_down else "（上涨高位，警惕见顶）"))
        # 倒锤 / 流星
        if body < rng * 0.12 and up_wick > body * 2 and dn_wick < body * 0.6:
            trend_up = i > 3 and b2.close > bars[i - 3].close
            p = ("倒锤线" if not trend_up else "流星线", 1 if not trend_up else -1,
                 "上影线长" + ("（低位潜在反转）" if not trend_up else "（高位见顶信号）"))
        # 十字
        if body < rng * 0.08:
            p = ("十字星", 0, "多空分歧，转折观察点")
        # 纺锤
        if body < rng * 0.35 and body > rng * 0.12:
            p = ("纺锤线", 0, "实体收敛，动能减弱")

        # 吞噬
        pb, qb = _body(b1), _body(b0)
        if pb > 0 and qb > 0:
            if b2.close > b1.open and b2.open < b1.close and b2.close > b1.close:
                p = ("看涨 engulfing", 1, "阳线吞没阴线，多方接管")
            elif b2.close < b1.open and b2.open > b1.close and b2.close < b1.close:
                p = ("看跌 engulfing", -1, "阴线吞没阳线，空方接管")
        # 孕线
        if pb > 0 and b2.high <= b1.high and b2.low >= b1.low and _body(b2) < pb * 0.6:
            p = ("孕线（内包）", 0, "波动收敛，等待方向选择")

        # 三根
        if i >= 3:
            b3 = bars[i - 3]
            b3b, b2b = _body(b3), _body(b2)
            if (b3.close < b3.open and _body(b1) < pb * 0.4
                    and b2.close > b2.open and b2.close > (b3.open + b1.close) / 2):
                p = ("晨星", 1, "三根见底反转形态")
            if (b3.close > b3.open and _body(b1) < pb * 0.4
                    and b2.close < b2.open and b2.close < (b3.open + b1.close) / 2):
                p = ("暮星", -1, "三根见顶反转形态")
            if b3.close > b3.open and b2.close > b3.open and b2b > b3b * 0.8:
                if bars[i - 4].close < bars[i - 4].open if i >= 4 else True:
                    p = ("红三兵", 1, "连续三阳，趋势延续")
            if b3.close < b3.open and b2.close < b3.open and b2b > b3b * 0.8:
                if bars[i - 4].close > bars[i - 4].open if i >= 4 else True:
                    p = ("黑三鸦", -1, "连续三阴，趋势延续")

        if p:
            out.append(CandleSignal(index=i, date=b2.date, pattern=p[0], bias=p[1], note=p[2]))
    return out


def ichimoku(bars: list[Bar]) -> dict[str, Any]:
    """一目均衡表。"""
    n = len(bars)
    if n < 78:
        return {"status": "warmup", "note": f"K线不足 78 根（当前 {n}），信号不可靠"}

    def mid(i: int, p: int) -> float:
        w = bars[i - p + 1 : i + 1]
        return (max(b.high for b in w) + min(b.low for b in w)) / 2

    i = n - 1
    tenkan = mid(i, 9)
    kijun = mid(i, 26)
    span_a = (tenkan + kijun) / 2
    sa_b = mid(i, 52)
    span_b = mid(i - 26, 52)  # 前移26周期
    price = bars[i].close
    above_cloud = price > max(span_a, span_b)
    below_cloud = price < min(span_a, span_b)
    if above_cloud:
        st, bias = "多头（价在云上）", 1
    elif below_cloud:
        st, bias = "空头（价在云下）", -1
    else:
        st, bias = "云内震荡", 0
    return {
        "status": "ok", "tenkan": round(tenkan, 2), "kijun": round(kijun, 2),
        "span_a": round(span_a, 2), "span_b": round(sa_b, 2),
        "chikou_ref": round(bars[i - 26].close, 2) if i >= 26 else None,
        "price": price, "state": st, "bias": bias,
    }


def swing_points(bars: list[Bar], k: int = 3) -> tuple[list[tuple[int, str, float]], list[tuple[int, str, float]]]:
    """摆动高低点。k 为左右确认窗口。"""
    highs: list[tuple[int, str, float]] = []
    lows: list[tuple[int, str, float]] = []
    n = len(bars)
    for i in range(k, n - k):
        w = bars[i - k : i + k + 1]
        if bars[i].high == max(b.high for b in w):
            highs.append((i, bars[i].date, bars[i].high))
        if bars[i].low == min(b.low for b in w):
            lows.append((i, bars[i].date, bars[i].low))
    return highs, lows


# ================================================================ 结构判定

@dataclass
class Level:
    price: float
    label: str
    kind: str  # resistance / support
    dist_pct: float
    basis: str


@dataclass
class Structure:
    """一段行情的结构画像。"""
    trend: str = "unknown"
    trend_detail: str = ""
    pattern: str = ""
    pattern_detail: str = ""
    target: float | None = None
    fib: dict[str, float] = field(default_factory=dict)
    levels: list[Level] = field(default_factory=list)
    highs: list[tuple[int, str, float]] = field(default_factory=list)
    lows: list[tuple[int, str, float]] = field(default_factory=list)
    rebound_pct: float | None = None
    notes: list[str] = field(default_factory=list)


def analyze_structure(bars: list[Bar], price: float) -> Structure:
    """判定趋势方向、形态与关键价位。"""
    st = Structure()
    if len(bars) < 30:
        st.trend = "unknown"
        st.trend_detail = f"K线仅 {len(bars)} 根，数据不足以判定结构"
        return st

    highs, lows = swing_points(bars, 3)
    st.highs, st.lows = highs, lows
    closes = [b.close for b in bars]

    # ---- 趋势方向：按近 1/3 区间的高低点比较
    seg = len(bars) // 3
    recent = bars[-seg:]
    prior = bars[-2 * seg : -seg] if len(bars) >= 2 * seg else bars[:seg]
    r_hi, r_lo = max(b.high for b in recent), min(b.low for b in recent)
    p_hi, p_lo = max(b.high for b in prior), min(b.low for b in prior)
    if r_hi > p_hi and r_lo > p_lo:
        st.trend = "up"
        st.trend_detail = f"近 {seg} 日高点 {r_hi:.2f} > 前段 {p_hi:.2f}，低点 {r_lo:.2f} > {p_lo:.2f}，高点低点同步抬升"
    elif r_hi < p_hi and r_lo < p_lo:
        st.trend = "down"
        st.trend_detail = f"近 {seg} 日高点 {r_hi:.2f} < 前段 {p_hi:.2f}，低点 {r_lo:.2f} < {p_lo:.2f}，高点低点同步下移"
    else:
        st.trend = "range"
        st.trend_detail = f"近 {seg} 日在 {r_lo:.2f}–{r_hi:.2f} 区间震荡（前段 {p_lo:.2f}–{p_hi:.2f}），方向未定"

    # ---- 长周期涨跌幅定位
    if len(bars) >= 60:
        hi = max(b.high for b in bars)
        lo = min(b.low for b in bars)
        hi_i = max(range(len(bars)), key=lambda i: bars[i].high)
        lo_i = min(range(len(bars)), key=lambda i: bars[i].low)
        if hi > lo:
            st.rebound_pct = round((price - lo) / (hi - lo) * 100, 1)
            st.fib = {
                "0.236": round(hi - (hi - lo) * 0.236, 2),
                "0.382": round(hi - (hi - lo) * 0.382, 2),
                "0.5": round(hi - (hi - lo) * 0.5, 2),
                "0.618": round(hi - (hi - lo) * 0.618, 2),
                "0.786": round(hi - (hi - lo) * 0.786, 2),
            }
            st.notes.append(
                f"区间 {bars[lo_i].date} 低点 {lo:.2f} → {bars[hi_i].date} 高点 {hi:.2f}；"
                f"现价处该区间 {st.rebound_pct}% 分位"
            )
            bars_ = bars  # noqa: F841
            st.notes.append(f"区间高点日期 {bars[hi_i].date}，低点日期 {bars[lo_i].date}")

    # ---- 形态识别：双底 / 双顶
    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        sep = abs(l2[0] - l1[0])
        diff_pct = abs(l2[2] - l1[2]) / l1[2] * 100
        between = [b for b in bars[l1[0] : l2[0] + 1]]
        if 3 <= sep <= 80 and diff_pct <= 6 and between:
            neck = max(b.high for b in between)
            if neck > l1[2] * 1.02 and price > l1[2] and price < neck * 1.12:
                h = neck - l1[2]
                rise = l2[2] > l1[2]
                st.pattern = "W底（抬升双底）" if rise else "W底（双底）"
                st.target = round(neck + h, 2)
                st.pattern_detail = (
                    f"左脚 {l1[1]} {l1[2]:.2f}、右脚 {l2[1]} {l2[2]:.2f}"
                    f"（右脚{'高于' if rise else '低于/持平于'}左脚 {abs(l2[2]-l1[2]):.2f} 元，偏离 {diff_pct:.1f}%），"
                    f"颈线 {neck:.2f}，形态高度 {h:.2f} 元，理论目标 {st.target:.2f} 元"
                )
                if rise:
                    st.notes.append("右脚高于左脚，属抬升双底，反弹动能强于标准 W 底")
                st.notes.append(
                    f"颈线 {neck:.2f} {'已被突破' if price > neck else f'尚未突破（现价 {price:.2f}）'}"
                    + ("，突破首日未经回抽确认，假突破风险存在" if price > neck
                       else "，需放量突破颈线方能确认形态")
                )
    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        sep = abs(h2[0] - h1[0])
        diff_pct = abs(h2[2] - h1[2]) / h1[2] * 100
        between = [b for b in bars[h1[0] : h2[0] + 1]]
        if 3 <= sep <= 80 and diff_pct <= 6 and between:
            neck = min(b.low for b in between)
            if neck < h1[2] * 0.98 and price < h1[2] and price > neck * 0.88:
                h = h1[2] - neck
                # 双顶发生在下降趋势中时，明确标注为下降双顶，避免与趋势方向混淆
                decline = h2[2] < h1[2]
                st.pattern = "M顶（下降双顶）" if decline else "M顶（双顶）"
                st.target = round(neck - h, 2)
                st.pattern_detail = (
                    f"左肩 {h1[1]} {h1[2]:.2f}、右肩 {h2[1]} {h2[2]:.2f}"
                    f"（右肩{'低于' if decline else '持平/高于'}左肩 {abs(h2[2]-h1[2]):.2f} 元，偏离 {diff_pct:.1f}%），"
                    f"颈线 {neck:.2f}，形态高度 {h:.2f} 元，理论目标 {st.target:.2f} 元"
                )
                if decline:
                    st.notes.append("双顶右肩低于左肩，属下降趋势中的双顶，反弹至颈线即为减仓位")

    # ---- 关键价位
    lv: list[Level] = []
    seen: set[str] = set()

    def add(p: float | None, label: str, basis: str, kind: str) -> None:
        if p is None or p <= 0 or label in seen:
            return
        seen.add(label)
        lv.append(Level(round(p, 2), label, kind, round((p - price) / price * 100, 2), basis))

    for i, dt, p in reversed(highs):
        if p > price:
            add(p, f"{dt} 波段高点 {p:.2f}元", "摆动高点", "resistance")
        else:
            break
    for i, dt, p in reversed(lows):
        if p < price:
            add(p, f"{dt} 波段低点 {p:.2f}元", "摆动低点", "support")
        else:
            break
    if highs and highs[-1][2] > price:
        add(highs[-1][2], f"{highs[-1][1]} 波段高点 {highs[-1][2]:.2f}元", "最近摆动高点", "resistance")
    if lows and lows[-1][2] < price:
        add(lows[-1][2], f"{lows[-1][1]} 波段低点 {lows[-1][2]:.2f}元", "最近摆动低点", "support")
    for k, v in st.fib.items():
        add(v, f"斐波那契 {k} 回撤位 {v:.2f}元", "斐波那契回撤", "resistance" if v > price else "support")

    lv.sort(key=lambda x: x.price)
    st.levels = lv
    return st


# ================================================================ 信号投票

@dataclass
class Vote:
    system: str
    signal: str
    score: int  # 1 多 / -1 空 / 0 中性
    detail: str


def build_votes(bundle) -> list[Vote]:
    """多体系投票。票数一致时以资金面为最终裁决。"""
    vs: list[Vote] = []
    bars = bundle.bars
    q = bundle.quote

    # 1. 均线系统
    closes = [b.close for b in bars]
    if len(closes) >= 60:
        ma = {n: sma(closes, n)[-1] for n in (5, 10, 20, 60)}
        if None not in ma.values() and q and q.price:
            p = q.price
            above = [n for n, v in ma.items() if p > v]
            bull = ma[5] > ma[10] > ma[20]
            if len(above) == 4 and bull:
                vs.append(Vote("均线系统", "做多", 1,
                    f"站上全部 {len(above)} 条均线，MA5({ma[5]:.2f})>MA10({ma[10]:.2f})>MA20({ma[20]:.2f}) 多头排列"))
            elif len(above) == 0:
                vs.append(Vote("均线系统", "做空", -1, "跌破全部均线"))
            else:
                vs.append(Vote("均线系统", "中性", 0,
                    f"站上 {len(above)}/4 条均线" + ("，短期多头排列" if bull else "，均线纠缠")))

    # 2. MACD
    if len(closes) >= 40:
        m = macd(closes)
        h, d, de = m["hist"][-1], m["dif"][-1], m["dea"][-1]
        rising = len(m["hist"]) >= 6 and all(
            m["hist"][-i] > m["hist"][-i - 1] for i in range(1, 6))
        if d > 0 and de > 0 and h > 0:
            vs.append(Vote("MACD", "做多", 1,
                f"零轴上方金叉，柱状 {h:+.4f}" + ("，连续 5 日放大" if rising else "")))
        elif d < 0 and de < 0 and h < 0:
            vs.append(Vote("MACD", "做空", -1, f"零轴下方死叉，柱状 {h:+.4f}"))
        else:
            vs.append(Vote("MACD", "中性", 0, f"DIF {d:+.3f} / DEA {de:+.3f}，未形成明确方向"))

    # 3. KDJ —— 优先报告极值（超买/超卖），极值即隐含反转风险，优先级高于金叉死叉
    if len(bars) >= 20:
        k = kdj(bars)
        j, kk, dd = k["j"][-1], k["k"][-1], k["d"][-1]
        if j is not None:
            if j > 90:
                vs.append(Vote("KDJ", "超买", -1,
                    f"J 值 {j:.1f}，已进入超买区，短线回踩概率上升"
                    + ("；同时 K 上穿 D，强势但动能透支" if kk > dd else "")))
            elif j < 5:
                vs.append(Vote("KDJ", "超卖", 1,
                    f"J 值 {j:.1f}，进入超卖区，短线反弹概率上升"))
            elif kk > dd:
                vs.append(Vote("KDJ", "做多", 1, f"K({kk:.1f}) 上穿 D({dd:.1f})，J 值 {j:.1f}"))
            else:
                vs.append(Vote("KDJ", "做空", -1, f"K({kk:.1f}) 下穿 D({dd:.1f})，J 值 {j:.1f}"))

    # 4. 成交量
    if len(bars) >= 25 and q and q.volume:
        v20 = sum(b.volume for b in bars[-21:-1]) / 20
        if v20 > 0:
            ratio = q.volume / v20
            vr = q.volume_ratio or 0
            if ratio >= 1.5 and (q.change_pct or 0) > 0:
                vs.append(Vote("成交量", "做多", 1,
                    f"放量 {ratio:.2f} 倍（20 日均量 {v20/1e4:.0f} 万股），价涨量增"))
            elif ratio >= 1.5 and (q.change_pct or 0) < 0:
                vs.append(Vote("成交量", "做空", -1, f"放量 {ratio:.2f} 倍下跌，抛压沉重"))
            elif ratio < 0.7 and (q.change_pct or 0) > 0:
                vs.append(Vote("成交量", "中性", 0, f"缩量 {ratio:.2f} 倍上涨，持续性待验证"))
            else:
                vs.append(Vote("成交量", "中性", 0, f"量能 {ratio:.2f} 倍（量比 {vr:.2f}）"))
            vs[-1].detail += f"，量比 {vr:.2f}" if vr else ""

    # 5. K 线形态
    sigs = detect_candles(bars)
    recent = [s for s in sigs if s.index >= len(bars) - 3]
    longs = [s for s in sigs if s.bias == 1]
    shorts = [s for s in sigs if s.bias == -1]
    if recent:
        s = recent[-1]
        vs.append(Vote("K 线形态", s.pattern, 0,
            f"{s.date} 出现「{s.pattern}」：{s.note}"))
    else:
        last = sigs[-1] if sigs else None
        if last:
            vs.append(Vote("K 线形态", last.pattern if not last.bias else
                ("多头形态" if last.bias > 0 else "空头形态"), 0,
                f"最近信号 {last.date}「{last.pattern}」：{last.note}"))

    # 6. 一目均衡表
    ich = ichimoku(bars)
    if ich["status"] == "ok":
        vs.append(Vote("一目均衡表",
            "做多" if ich["bias"] > 0 else ("做空" if ich["bias"] < 0 else "观望"),
            ich["bias"],
            f"价格 {ich['price']:.2f} {ich['state']}，云顶 {max(ich['span_a'], ich['span_b']):.2f} / 云底 {min(ich['span_a'], ich['span_b']):.2f}"))
    else:
        vs.append(Vote("一目均衡表", "观望", 0, ich.get("note", "数据不足")))

    # 7. 结构
    if bars and q and q.price:
        st = analyze_structure(bars, q.price)
        vs.append(Vote("价格结构", {"up": "上行", "down": "下行", "range": "震荡"}.get(st.trend, "未知"),
            {"up": 1, "down": -1}.get(st.trend, 0), st.trend_detail
            + (f"；形态：{st.pattern_detail}" if st.pattern_detail else "")))

    # 8. 资金面（关键裁决项）
    if bundle.flows and q:
        last = bundle.flows[-1]
        main_yi = last.main / 1e8
        dirn = "净流入" if main_yi > 0 else "净流出"
        agree = (main_yi > 0) == ((q.change_pct or 0) > 0)
        if abs(main_yi) >= 0.3 and not agree:
            vs.append(Vote("主力资金", "背离", -1 if (q.change_pct or 0) > 0 else 1,
                f"股价 {(q.change_pct or 0):+.2f}% 但主力{dirn} {abs(main_yi):.2f} 亿元，"
                f"中小单{'净流入' if last.mid+last.small>0 else '净流出'} "
                f"{abs(last.mid+last.small)/1e8:.2f} 亿元，量价与资金方向背离"))
        elif abs(main_yi) >= 0.3:
            vs.append(Vote("主力资金", "做多" if main_yi > 0 else "做空",
                1 if main_yi > 0 else -1, f"主力{dirn} {abs(main_yi):.2f} 亿元，与股价方向一致"))
        else:
            vs.append(Vote("主力资金", "中性", 0, f"主力{dirn} {abs(main_yi):.2f} 亿元，金额不显著"))

        # 近 5 日主力累计
        cum5 = sum(f.main for f in bundle.flows[-5:]) / 1e8
        if cum5 != 0:
            s = "净流入" if cum5 > 0 else "净流出"
            vs.append(Vote("主力资金(5日)", s, 1 if cum5 > 0 else -1,
                f"近 5 日主力累计{s} {abs(cum5):.2f} 亿元"))

    # 9. 两融
    if bundle.margins:
        last = bundle.margins[-1]
        if last.finance_dod is not None and abs(last.finance_dod) >= 2:
            s = "增仓" if last.finance_dod > 0 else "减仓"
            vs.append(Vote("融资余额",
                "做多" if last.finance_dod > 0 else "做空",
                1 if last.finance_dod > 0 else -1,
                f"{last.date} 融资余额 {last.finance_balance/1e8:.3f} 亿（环比 {last.finance_dod:+.2f}%），杠杆资金{s}"
                f"，当日净{'买入' if last.finance_net_buy>0 else '卖出'} {abs(last.finance_net_buy)/1e4:.0f} 万"))

    # 10. 筹码
    if bundle.chip and q and q.price:
        pr = bundle.chip.get("profit_rate", 0)
        ac = bundle.chip.get("avg_cost", 0)
        # 过重阈值取 80：获利盘超 80% 时，一旦跌破平均成本易触发连锁兑现
        if pr >= 80:
            vs.append(Vote("筹码结构", "偏空", -1,
                f"获利盘 {pr:.2f}%（超 80% 阈值），平均成本 {ac:.2f} 元，"
                f"现价高于成本 {(q.price/ac-1)*100:+.1f}%，获利盘过重存在兑现压力"))
        elif pr <= 20:
            vs.append(Vote("筹码结构", "偏多", 1, f"获利盘仅 {pr:.2f}%，抛压较轻"))
        else:
            vs.append(Vote("筹码结构", "中性", 0,
                f"获利盘 {pr:.2f}%，平均成本 {ac:.2f} 元（现价偏离 {(q.price/ac-1)*100:+.1f}%）"))

    # 11. 趋势评分
    if bundle.score.get("tech") is not None:
        t = bundle.score["tech"]
        vs.append(Vote("技术评分", "偏强" if t >= 70 else ("偏弱" if t < 50 else "中性"),
            1 if t >= 70 else (-1 if t < 50 else 0), f"技术评分 {t:.2f}"))
    if bundle.score.get("cap") is not None:
        c = bundle.score["cap"]
        vs.append(Vote("成长评分", "偏强" if c >= 70 else ("偏弱" if c < 50 else "中性"),
            1 if c >= 70 else (-1 if c < 50 else 0), f"成长评分 {c:.2f}"))

    return vs