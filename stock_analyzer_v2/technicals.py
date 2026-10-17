"""技术面分析层：均线体系、形态结构、斐波那契位、量能结构。

不依赖外部技术指标库，全部用 pandas/numpy 实现，保证在任何环境可跑。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 指标


def add_indicators(k: pd.DataFrame) -> pd.DataFrame:
    """在原始日K 上追加均线族、MACD、KDJ、RSI、BOLL、量比、ATR。"""
    d = k.copy()
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"]

    for n in (5, 10, 20, 30, 60, 120, 250):
        d[f"ma{n}"] = c.rolling(n).mean()

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    d["dif"] = ema12 - ema26
    d["dea"] = d["dif"].ewm(span=9, adjust=False).mean()
    d["macd"] = (d["dif"] - d["dea"]) * 2

    ll, hh = l.rolling(9).min(), h.rolling(9).max()
    rsv = (c - ll) / (hh - ll).replace(0, np.nan) * 100
    d["k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d["d"] = d["k"].ewm(alpha=1 / 3, adjust=False).mean()
    d["j"] = 3 * d["k"] - 2 * d["d"]

    delta = c.diff()
    up = delta.clip(lower=0)
    dn = (-delta).clip(lower=0)
    for n, col in ((6, "rsi6"), (12, "rsi12"), (24, "rsi24")):
        au = up.ewm(alpha=1 / n, adjust=False).mean()
        ad = dn.ewm(alpha=1 / n, adjust=False).mean()
        d[col] = au / (au + ad).replace(0, np.nan) * 100

    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    d["boll_mid"], d["boll_up"], d["boll_low"] = mid, mid + 2 * std, mid - 2 * std

    d["vma5"] = v.rolling(5).mean()
    d["vma20"] = v.rolling(20).mean()
    d["vol_ratio"] = v / d["vma20"].replace(0, np.nan)

    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["atr_pct"] = d["atr"] / c * 100

    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    d["obv"] = obv
    return d


# ---------------------------------------------------------------- 摆动点

@dataclass
class Swing:
    date: str
    price: float
    kind: str  # H / L


def find_swings(d: pd.DataFrame, window: int = 10) -> List[Swing]:
    """Zigzag 摆动点：滚动窗口局部极值 + 强制 H/L 交替。"""
    if len(d) < window * 2 + 1:
        return []
    h, l = d["high"], d["low"]
    full = window * 2 + 1
    rmax = h.rolling(full, center=True).max()
    rmin = l.rolling(full, center=True).min()

    raw: List[Swing] = []
    for idx in h.index:
        is_h = bool(h[idx] == rmax.get(idx, False))
        is_l = bool(l[idx] == rmin.get(idx, False))
        if is_h and not is_l:
            raw.append(Swing(str(pd.Timestamp(d.loc[idx, "date"]).date()), float(h[idx]), "H"))
        elif is_l and not is_h:
            raw.append(Swing(str(pd.Timestamp(d.loc[idx, "date"]).date()), float(l[idx]), "L"))

    if len(raw) < 2:
        return raw
    out = [raw[0]]
    for pt in raw[1:]:
        last = out[-1]
        if pt.kind == last.kind:
            better = (pt.price > last.price) if pt.kind == "H" else (pt.price < last.price)
            if better:
                out[-1] = pt
        else:
            out.append(pt)
    return out


# ---------------------------------------------------------------- 结构

@dataclass
class TrendLeg:
    label: str
    start_date: str
    end_date: str
    start_price: float
    end_price: float
    pct: float
    bars: int
    high: float
    low: float
    avg_vol_wan: float


def _leg(d: pd.DataFrame, label: str, i0: int, i1: int) -> Optional[TrendLeg]:
    seg = d.iloc[i0:i1 + 1]
    if len(seg) < 2:
        return None
    p0, p1 = float(seg["close"].iloc[0]), float(seg["close"].iloc[-1])
    return TrendLeg(
        label=label,
        start_date=str(pd.Timestamp(seg["date"].iloc[0]).date()),
        end_date=str(pd.Timestamp(seg["date"].iloc[-1]).date()),
        start_price=round(p0, 2), end_price=round(p1, 2),
        pct=round((p1 / p0 - 1) * 100, 2) if p0 else 0.0,
        bars=len(seg),
        high=round(float(seg["high"].max()), 2),
        low=round(float(seg["low"].min()), 2),
        avg_vol_wan=round(float(seg["volume"].mean()) / 1e4, 0),
    )


def major_legs(d: pd.DataFrame) -> List[TrendLeg]:
    """用摆动点切分主要趋势段，自动合并噪音段。

    过滤规则：① 幅度 <8% 的转折视为噪音 ② 单段 K 线数 <4 视为噪音
    ③ 连续同向段合并（避免出现"+0.3% 下跌"这类伪标签）
    """
    sw = find_swings(d, window=10)
    if len(sw) < 4:
        return []
    idx = {str(pd.Timestamp(r.date).date()): i for i, r in d.iterrows()}
    pts = [(idx[s.date], s.kind, s.price) for s in sw if s.date in idx]
    if len(pts) < 4:
        return []

    # 逐点剔除噪音：幅度不足 8% 或跨度过短的转折直接跳过
    kept = [pts[0]]
    for p in pts[1:]:
        prev = kept[-1]
        move = abs(p[2] / prev[2] - 1)
        bars = abs(p[0] - prev[0])
        if move < 0.08 or bars < 4:
            # 幅度不足时，若新点更极端则替换当前点
            if move < 0.08 and ((p[2] > prev[2]) if p[1] == "H" else (p[2] < prev[2])):
                if bars >= 2:
                    kept[-1] = p
            continue
        kept.append(p)
    if len(kept) < 4:
        kept = pts[-4:] if len(pts) >= 4 else pts

    legs: List[TrendLeg] = []
    for a, b in zip(kept[:-1], kept[1:]):
        i0, i1 = min(a[0], b[0]), max(a[0], b[0])
        if i1 - i0 < 3:
            continue
        leg = _leg(d, "上涨" if b[2] >= a[2] else "下跌", i0, i1)
        if leg and abs(leg.pct) >= 3.0:
            legs.append(leg)

    # 合并连续同向段
    merged: List[TrendLeg] = []
    for lg in legs:
        if merged and merged[-1].label == lg.label:
            prev = merged.pop()
            i0 = d.index[d["date"] == pd.Timestamp(prev.start_date)][0]
            i1 = d.index[d["date"] == pd.Timestamp(lg.end_date)][0]
            m = _leg(d, lg.label, min(i0, i1), max(i0, i1))
            if m:
                merged.append(m)
        else:
            merged.append(lg)
    return merged


@dataclass
class Box:
    low: float
    high: float
    low_date: str
    high_date: str
    bars: int
    width_pct: float
    touches_high: int = 0
    touches_low: int = 0


def detect_box(d: pd.DataFrame, lookback: int = 60, min_bars: int = 8,
               max_width: float = 0.35) -> Optional[Box]:
    """箱体检测。

    有效箱体需同时满足：
    ① 振幅在 10%–35% 之间（<10% 是横盘钝化，>35% 是单边趋势，都不算箱体）
    ② 上沿/下沿各被接近 ≥2 次（避免把单边的一次性高低点当箱体边界）
    ③ 边界距现价有一定参考意义
    """
    seg = d.tail(lookback)
    if len(seg) < min_bars:
        return None
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    width = hi / lo - 1 if lo else 0.0
    if width < 0.10 or width > max_width:
        return None

    near_hi = int((seg["high"] >= hi * 0.985).sum())
    near_lo = int((seg["low"] <= lo * 1.015).sum())
    if near_hi < 2 or near_lo < 2:
        return None

    ih, il = int(seg["high"].idxmax()), int(seg["low"].idxmin())
    return Box(
        low=round(lo, 2), high=round(hi, 2),
        low_date=str(pd.Timestamp(seg.loc[il, "date"]).date()),
        high_date=str(pd.Timestamp(seg.loc[ih, "date"]).date()),
        bars=len(seg), width_pct=round(width * 100, 2),
        touches_high=near_hi, touches_low=near_lo,
    )


# ---------------------------------------------------------------- 斐波那契

FIB_RETR = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_EXT = [1.272, 1.414, 1.618]


def fib_levels(low: float, high: float) -> Dict[str, Dict[str, float]]:
    """给定一段行情的高低点，输出回撤位与扩展位。"""
    rng = high - low
    retr = {f"{r:.3f}": round(high - rng * r, 2) for r in FIB_RETR}
    ext = {f"{r:.3f}": round(low + rng * r, 2) for r in FIB_EXT}
    return {"retracement": retr, "extension": ext,
            "swing_low": round(low, 2), "swing_high": round(high, 2),
            "amplitude_pct": round((high / low - 1) * 100, 2) if low else 0.0}


def dominant_swing(d: pd.DataFrame, lookback: int = 250) -> tuple:
    """主导波段：近 lookback 内最高点与最低点所在的先后顺序。"""
    seg = d.tail(lookback)
    ih = int(seg["high"].idxmax())
    il = int(seg["low"].idxmin())
    return il, ih


# ---------------------------------------------------------------- 技术状态

@dataclass
class TechState:
    close: float
    ma_aligned: str
    ma_detail: Dict[str, float]
    ma_bias_pct: Dict[str, float]
    boll_pos: str
    boll_up: float
    boll_mid: float
    boll_low: float
    macd_state: str
    macd_hist_prev: float
    macd_hist_now: float
    kdj_state: str
    rsi_state: str
    rsi6: float
    vol_state: str
    vol_ratio: float
    atr_pct: float
    net_bias: str


def tech_state(d: pd.DataFrame) -> TechState:
    r = d.iloc[-1]
    c = float(r["close"])

    mas = {n: float(r[f"ma{n}"]) for n in (5, 10, 20, 60, 120, 250)
           if f"ma{n}" in d.columns and not np.isnan(r.get(f"ma{n}", np.nan))}
    order = [5, 10, 20, 60, 120, 250]
    seq = [n for n in order if n in mas]
    vals = [mas[n] for n in seq]
    if len(vals) >= 3 and all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)) and c > vals[-1]:
        align = "多头排列"
    elif len(vals) >= 3 and all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)) and c < vals[-1]:
        align = "空头排列"
    elif c > (vals[-1] if vals else c):
        align = "价格在中长期均线上方"
    else:
        align = "价格在中长期均线下方"

    bias = {f"MA{n}": round((c / mas[n] - 1) * 100, 1) for n in seq}

    up, mid, low = float(r["boll_up"]), float(r["boll_mid"]), float(r["boll_low"])
    if c >= up:
        bpos = "触及/突破上轨"
    elif c <= low:
        bpos = "触及/跌破下轨"
    elif c > mid:
        bpos = "中轨上方"
    else:
        bpos = "中轨下方"

    hist_now = float(r["macd"])
    hist_prev = float(d["macd"].iloc[-2]) if len(d) > 1 else hist_now
    if hist_now > 0 and hist_now < hist_prev:
        macd = "红柱缩短"
    elif hist_now > 0:
        macd = "红柱走强"
    elif hist_now < hist_prev:
        macd = "绿柱缩短"
    else:
        macd = "绿柱走弱"

    k, j = float(r["k"]), float(r["j"])
    kdj = "高位钝化" if k > 80 else "低位钝化" if k < 20 else "中位"

    rsi6 = float(r["rsi6"])
    rsi = "超买" if rsi6 > 80 else "超卖" if rsi6 < 20 else "中性偏强" if rsi6 > 55 else "中性偏弱"

    vr = float(r["vol_ratio"]) if not np.isnan(r["vol_ratio"]) else 1.0
    if vr >= 2:
        vol = "显著放量"
    elif vr >= 1.3:
        vol = "温和放量"
    elif vr <= 0.6:
        vol = "明显缩量"
    else:
        vol = "量能平稳"

    above = [n for n in seq if c > mas[n]]
    below = [n for n in seq if c <= mas[n]]
    if seq and below and not above:
        net = f"全部 {len(seq)} 条均线在上方压制"
    elif seq and above and not below:
        net = f"全部 {len(seq)} 条均线在下方支撑"
    else:
        net = f"短均线({','.join('MA'+str(n) for n in above) or '无'})在下方 / 长均线({','.join('MA'+str(n) for n in below) or '无'})在上方"

    return TechState(
        close=round(c, 2), ma_aligned=align, ma_detail={k2: round(v, 2) for k2, v in mas.items()},
        ma_bias_pct=bias, boll_pos=bpos, boll_up=round(up, 2), boll_mid=round(mid, 2),
        boll_low=round(low, 2), macd_state=macd, macd_hist_prev=round(hist_prev, 4),
        macd_hist_now=round(hist_now, 4), kdj_state=kdj, rsi_state=rsi, rsi6=round(rsi6, 1),
        vol_state=vol, vol_ratio=round(vr, 2),
        atr_pct=round(float(r["atr_pct"]), 2) if not np.isnan(r["atr_pct"]) else 0.0,
        net_bias=net,
    )


# ---------------------------------------------------------------- K 线形态

def detect_reversal_patterns(d: pd.DataFrame) -> List[Dict]:
    """轻量反转形态识别：吞没、孕线、锤子、射击之星、十字星。"""
    out: List[Dict] = []
    for i in range(max(2, len(d) - 15), len(d)):
        o, c_, h, l = d.loc[i, ["open", "close", "high", "low"]]
        o0, c0, h0, l0 = d.loc[i - 1, ["open", "close", "high", "low"]]
        body, pbody = abs(c_ - o), abs(c0 - o0)
        rng, prng = h - l, max(h0 - l0, 1e-9)
        up_bar = c_ > o
        name = None
        strength = 0

        if pbody > 1e-9 and up_bar and c_ >= o0 and o <= c0 and body > pbody:
            name, strength = "看涨吞没", 2
        elif pbody > 1e-9 and not up_bar and c_ <= o0 and o >= c0 and body > pbody:
            name, strength = "看跌吞没", -2
        elif max(o, c_) <= h0 and min(o, c_) >= l0 and pbody > body and rng < prng * 0.6:
            name, strength = ("孕线（看涨）" if up_bar else "孕线（看跌）"), (1 if up_bar else -1)
        elif (h - max(o, c_)) / max(rng, 1e-9) > 0.6 and (min(o, c_) - l) / max(rng, 1e-9) < 0.2 and body < rng * 0.35:
            name, strength = "锤子线", 1
        elif (min(o, c_) - l) / max(rng, 1e-9) > 0.6 and (h - max(o, c_)) / max(rng, 1e-9) < 0.2 and body < rng * 0.35:
            name, strength = "射击之星", -1
        elif body / max(rng, 1e-9) < 0.12:
            name, strength = "十字星", 0

        if name:
            out.append({"date": str(pd.Timestamp(d.loc[i, "date"]).date()), "pattern": name,
                        "score": strength, "close": round(float(c_), 2)})
    return out


def gap_events(d: pd.DataFrame) -> List[Dict]:
    """跳空缺口（>0.5%）。"""
    out = []
    for i in range(1, len(d)):
        h0, l1 = float(d["high"].iloc[i - 1]), float(d["low"].iloc[i])
        l0, h1 = float(d["low"].iloc[i - 1]), float(d["high"].iloc[i])
        pct = float(d["close"].iloc[i - 1])
        if pct <= 0:
            continue
        if l1 - h0 > pct * 0.005:
            out.append({"date": str(pd.Timestamp(d["date"].iloc[i]).date()), "kind": "向下缺口",
                        "low": round(h0, 2), "high": round(l1, 2), "size": round(l1 - h0, 2),
                        "filled": bool((d["low"].iloc[i + 1:] <= h0).any()) if i + 1 < len(d) else False})
        elif l0 - h1 > pct * 0.005:
            out.append({"date": str(pd.Timestamp(d["date"].iloc[i]).date()), "kind": "向上缺口",
                        "low": round(h1, 2), "high": round(l0, 2), "size": round(l0 - h1, 2),
                        "filled": bool((d["high"].iloc[i + 1:] >= l0).any()) if i + 1 < len(d) else False})
    return out[-8:]