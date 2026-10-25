"""技术指标计算：均线、MACD、RSI、KDJ、BOLL、ATR、量能、波段结构。

全部为纯 pandas/numpy 实现，不依赖 TA-Lib，避免安装负担。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import MA_WINDOWS, W23_MAX_PULLBACK, W23_MIN_PULLBACK, W23_SWING_WINDOW


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在日线 DataFrame 上追加全部技术指标列。"""
    if df.empty:
        return df
    d = df.copy().reset_index(drop=True)
    close = d["close"].astype(float)
    high = d["high"].astype(float)
    low = d["low"].astype(float)
    vol = d["volume"].astype(float)

    for w in MA_WINDOWS:
        d[f"ma{w}"] = close.rolling(w).mean()
    d["ema12"] = close.ewm(span=12, adjust=False).mean()
    d["ema26"] = close.ewm(span=26, adjust=False).mean()
    d["dif"] = d["ema12"] - d["ema26"]
    d["dea"] = d["dif"].ewm(span=9, adjust=False).mean()
    d["macd"] = (d["dif"] - d["dea"]) * 2

    # RSI(14) —— Wilder 平滑
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi14"] = 100 - 100 / (1 + rs)

    # KDJ(9,3,3)
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    d["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d["kdj_d"] = d["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    d["kdj_j"] = 3 * d["kdj_k"] - 2 * d["kdj_d"]

    # BOLL(20,2)
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    d["boll_mid"] = mid
    d["boll_up"] = mid + 2 * std
    d["boll_low"] = mid - 2 * std
    d["boll_pos"] = (close - d["boll_low"]) / (d["boll_up"] - d["boll_low"]).replace(0, np.nan) * 100

    # ATR(14)
    tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean()
    d["atr_pct"] = d["atr14"] / close * 100

    # 量能
    d["vol_ma5"] = vol.rolling(5).mean()
    d["vol_ma20"] = vol.rolling(20).mean()
    d["vol_ratio_5_20"] = d["vol_ma5"] / d["vol_ma20"].replace(0, np.nan)

    # 区间涨跌幅
    d["change_pct"] = close.pct_change() * 100
    for n, label in [(5, "ret5"), (10, "ret10"), (20, "ret20"), (60, "ret60"), (120, "ret120")]:
        d[label] = close.pct_change(n) * 100

    # 距区间高低点
    roll_high = close.rolling(min(len(d), 250), min_periods=20).max()
    roll_low = close.rolling(min(len(d), 250), min_periods=20).min()
    d["dist_high"] = (close / roll_high - 1) * 100
    d["dist_low"] = (close / roll_low - 1) * 100
    return d


# ------------------------------------------------------------------ 波段识别
@dataclass
class Swing:
    idx: int
    date: pd.Timestamp
    price: float
    kind: str          # 'high' | 'low'
    confirmed: bool = True


def find_swings(df: pd.DataFrame, window: int = W23_SWING_WINDOW) -> list[Swing]:
    """局部极值法识别摆动高/低点（左右各 window 根确认）。"""
    if len(df) < window * 2 + 2:
        return []
    highs, lows = df["high"].values, df["low"].values
    out: list[Swing] = []
    for i in range(window, len(df) - window):
        seg_h = highs[i - window:i + window + 1]
        seg_l = lows[i - window:i + window + 1]
        if highs[i] == seg_h.max() and (seg_h == highs[i]).sum() == 1:
            out.append(Swing(i, df["date"].iloc[i], float(highs[i]), "high"))
        elif lows[i] == seg_l.min() and (seg_l == lows[i]).sum() == 1:
            out.append(Swing(i, df["date"].iloc[i], float(lows[i]), "low"))
    return out


@dataclass
class W23Signal:
    """W23 形态模型输出：波浪位置 + 5/30 均线 + 量能三重条件。"""
    stage: str                       # 浪2调整末 | 浪3启动 | 浪3中继 | 浪1上涨 | 高位/浪5 | 下跌趋势 | 震荡
    stage_code: str                  # W2_END / W3_START / W3_MID / W1 / LATE / DOWN / RANGE
    confidence: float                # 0~100
    pullback: float | None           # 本轮回撤幅度 %
    swing_high: float | None
    swing_low: float | None
    ma5: float | None
    ma30: float | None
    ma30_slope: float | None         # 20日 MA30 斜率 %
    vol_shrink: float | None         # 5日均量/20日均量
    notes: list[str] = field(default_factory=list)
    score: float = 0.0               # 0~5，供技术面加分


def detect_w23(df: pd.DataFrame) -> W23Signal:
    """简化版波浪+均线+量能模型（规则引擎，非严格艾略特计数）。

    判定逻辑：
      1. 用 MA30 方向定趋势（上行/下行/走平）。
      2. 找最近一个已确认摆动高点，计算当前回撤幅度。
      3. 回撤落在 12%~55% 区间、且 MA5 上穿 MA30 / 价格重新站上 MA30、且缩量后放量
         -> 判为「浪2调整末 / 浪3启动」。
      4. 价格远高于 MA30 且乖离过大 -> 「高位（浪5 风险区）」。
    """
    if df.empty or len(df) < 60:
        return W23Signal("数据不足", "NA", 0, None, None, None, None, None, None, None, ["日线不足 60 根"], 0.0)

    last = df.iloc[-1]
    close = float(last["close"])
    ma5 = float(last["ma5"]) if pd.notna(last["ma5"]) else None
    ma30 = float(last["ma30"]) if pd.notna(last["ma30"]) else None
    ma30_prev = float(df["ma30"].iloc[-21]) if len(df) > 21 and pd.notna(df["ma30"].iloc[-21]) else None
    slope = ((ma30 / ma30_prev - 1) * 100) if ma30 and ma30_prev else None
    vol_shrink = float(last["vol_ratio_5_20"]) if pd.notna(last["vol_ratio_5_20"]) else None
    bias30 = (close / ma30 - 1) * 100 if ma30 else None

    swings = find_swings(df)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None

    # 回撤：最近高点之后的最低点
    pullback = None
    if last_high is not None and last_high.price > 0:
        after = df.iloc[last_high.idx:]
        trough = float(after["low"].min()) if not after.empty else close
        pullback = (last_high.price - trough) / last_high.price * 100

    notes: list[str] = []
    stage, stage_code, conf, score = "震荡", "RANGE", 40.0, 1.5

    ma_bull = ma5 is not None and ma30 is not None and ma5 > ma30
    price_above = ma30 is not None and close > ma30
    uptrend = slope is not None and slope > 0.5

    if uptrend and price_above and ma_bull:
        if bias30 is not None and bias30 > 25:
            stage, stage_code, conf, score = "高位/浪5风险区", "LATE", 65.0, 2.0
            notes.append(f"价格偏离 MA30 达 {bias30:.1f}%，短线过热")
        elif pullback is not None and W23_MIN_PULLBACK <= pullback <= W23_MAX_PULLBACK:
            vol_ok = vol_shrink is not None and vol_shrink < 1.0
            if vol_ok:
                stage, stage_code, conf, score = "浪2调整末", "W2_END", 70.0, 4.2
                notes.append(f"自高点回撤 {pullback:.1f}%，缩量（5/20日均量 {vol_shrink:.2f}）")
            else:
                stage, stage_code, conf, score = "浪3启动", "W3_START", 72.0, 4.5
                notes.append(f"回撤 {pullback:.1f}% 后放量站上 MA30，量能 {vol_shrink:.2f}")
        else:
            stage, stage_code, conf, score = "浪3中继", "W3_MID", 66.0, 3.8
            notes.append("均线多头 + MA30 上行，趋势延续中")
    elif uptrend and not price_above:
        stage, stage_code, conf, score = "浪2调整中", "W2_ADJ", 55.0, 2.6
        notes.append("MA30 仍上行但股价跌破 MA30，调整未完成")
    elif slope is not None and slope < -0.5 and not price_above:
        stage, stage_code, conf, score = "下跌趋势", "DOWN", 60.0, 1.0
        notes.append("MA30 下行且股价在其下方，右侧信号未出现")
    else:
        notes.append("均线走平，方向待选择")

    if last_high:
        notes.append(f"最近摆动高点 {last_high.price:.2f}（{last_high.date:%Y-%m-%d}）")
    if last_low:
        notes.append(f"最近摆动低点 {last_low.price:.2f}（{last_low.date:%Y-%m-%d}）")

    return W23Signal(
        stage=stage, stage_code=stage_code, confidence=conf, pullback=pullback,
        swing_high=last_high.price if last_high else None,
        swing_low=last_low.price if last_low else None,
        ma5=ma5, ma30=ma30, ma30_slope=slope, vol_shrink=vol_shrink,
        notes=notes, score=score,
    )


def support_resistance(df: pd.DataFrame) -> dict:
    """用摆动点 + 近 20/60 日极值给出支撑/压力位（越近的越靠前）。"""
    if df.empty:
        return {}
    close = float(df["close"].iloc[-1])
    tail = df.tail(120)
    swings = find_swings(tail)
    highs = {round(s.price, 2) for s in swings if s.kind == "high"}
    lows = {round(s.price, 2) for s in swings if s.kind == "low"}
    for w in (20, 60):
        seg = df.tail(w)
        if seg.empty:
            continue
        highs.add(round(float(seg["high"].max()), 2))
        lows.add(round(float(seg["low"].min()), 2))
    resist = sorted([h for h in highs if h > close * 1.005])[:3]
    support = sorted([l for l in lows if l < close * 0.995], reverse=True)[:3]
    return {"support": support, "resistance": resist}


def relative_strength(df: pd.DataFrame, bench: pd.DataFrame, window: int = 20) -> float | None:
    """个股相对基准（行业指数）的超额收益，单位 %。"""
    if df.empty or bench.empty or len(df) < window or len(bench) < window:
        return None
    stock_ret = df["close"].iloc[-1] / df["close"].iloc[-window - 1] - 1
    bench_ret = bench["close"].iloc[-1] / bench["close"].iloc[-window - 1] - 1
    return round((stock_ret - bench_ret) * 100, 2)
