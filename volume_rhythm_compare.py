#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
volume_rhythm_compare.py —— 两只股票「成交量走势 / 放量缩量节奏」相似度对比

设计要点
--------
1. 不同股票的绝对成交量量级差异极大（股本、股价、流通盘都不同），
   直接比成交量原始值毫无意义。因此本脚本统一转为「量能节奏」序列：

       量比 VR_t   = 当日成交量 / 过去 N 日均量          （剔除了量级，保留放缩倍率）
       量能 Z 分数 = (ln V_t - MA(ln V)) / STD(ln V)      （对数化 + 标准化，抗偏态）

   之后所有相似度计算都建立在这两条节奏序列上。

2. 「节奏」包含两层含义，脚本分别度量：
   (a) 幅度同步性：两股是不是同时在放量、同时缩量（相关性类指标）
   (b) 节拍同步性：放量/缩量的**时序形状**是否一致，允许轻微错位（DTW / 状态序列类指标）

   只看 Pearson 相关会把「同步」和「错位但形状一样」混为一谈，
   所以必须同时给出 DTW 距离、方向一致率、状态一致率、放量事件重合度。

3. 全部指标均为「量」的维度，不含价格，避免把价量关系和纯量能节奏混淆。

依赖
----
    pip install akshare pandas numpy matplotlib

用法
----
    # 对比 思特威(688213) 与 豪威集团(603501)，近 1 年，输出控制台报告 + 图表
    python volume_rhythm_compare.py 688213 603501 --start 2025-09-01

    # 自定义窗口、指定输出路径、跳过画图
    python volume_rhythm_compare.py 688213 603501 --ma 20 --roll 60 --no-chart

    # 只用本地 CSV（列名需含 date/open/high/low/close/volume）
    python volume_rhythm_compare.py --csv a.csv b.csv --names 股票A 股票B
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 常量：放量 / 缩量的状态阈值（基于量比 VR）
# ---------------------------------------------------------------------------
STATE_THRESHOLDS: Tuple[Tuple[str, float], ...] = (
    ("巨量", 2.00),
    ("放量", 1.30),
    ("平量", 0.75),
    ("缩量", 0.55),
)
STATE_LABELS: Tuple[str, ...] = ("巨量", "放量", "平量", "缩量", "地量")

# 相似度分档，用于给出结论性判断
SIM_GRADES: Tuple[Tuple[float, str], ...] = (
    (0.80, "高度同步 —— 量能节奏几乎同步，适合做同一逻辑下的组合/轮动"),
    (0.65, "较高同步 —— 节奏大体一致，存在偶发错位"),
    (0.50, "中度同步 —— 有共同驱动因素，但各自独立事件较多"),
    (0.00, "弱同步 —— 量能节奏基本各走各的，不宜按同一节奏交易"),
)


# ---------------------------------------------------------------------------
# 1. 数据获取
# ---------------------------------------------------------------------------
def fetch_akshare(
    symbol: str,
    start: str,
    end: str,
    adjust: str = "qfq",
) -> pd.DataFrame:
    """通过 akshare 拉取 A 股日线行情（含成交量、换手率）。

    Args:
        symbol: 6 位 A 股代码，如 "688213"。
        start: 开始日期 "YYYY-MM-DD" 或 "YYYYMMDD"。
        end: 结束日期，同格式。
        adjust: 复权方式，qfq=前复权，hfq=后复权，""=不复权。

    Returns:
        DataFrame，index 为 datetime，列含 open/high/low/close/volume/amount/turnover。
    """
    import akshare as ak

    s = start.replace("-", "")
    e = end.replace("-", "")
    raw = ak.stock_zh_a_hist(
        symbol=symbol, period="daily", start_date=s, end_date=e, adjust=adjust
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"akshare 未返回 {symbol} 的数据，请检查代码或日期区间")

    ren = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover",
    }
    df = raw.rename(columns=ren)
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "amount", "turnover"] if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def _tencent_symbol(symbol: str) -> str:
    """把 6 位 A 股代码转成腾讯接口需要的前缀格式。"""
    s = symbol.strip()
    if s[:2].lower() in ("sh", "sz", "bj"):
        return s.lower()
    if s.startswith(("60", "68", "90", "51", "58", "56", "50")):
        return "sh" + s
    if s.startswith(("00", "30", "20", "15", "16", "12")):
        return "sz" + s
    if s.startswith(("8", "4", "9")):
        return "bj" + s
    return "sh" + s


def fetch_tencent(symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """通过腾讯行情接口拉取日线（无需装 akshare，作为备用数据源）。

    接口返回 qfqday / hfqday / day 三类数组，元素为
    [日期, 开盘, 收盘, 最高, 最低, 成交量]。

    Args:
        symbol: 6 位 A 股代码。
        start / end: "YYYY-MM-DD"。
        adjust: qfq / hfq / ""。
    """
    import json
    import urllib.request

    key = {"qfq": "qfqday", "hfq": "hfqday", "": "day"}.get(adjust, "qfqday")
    sym = _tencent_symbol(symbol)
    span = int(max((pd.Timestamp(end) - pd.Timestamp(start)).days * 5 / 7 + 30, 60))
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={sym},day,{start},{end},{span},{'qfq' if adjust == 'qfq' else adjust or ''}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    node = (payload.get("data") or {}).get(sym) or {}
    rows = node.get(key) or node.get("day") or []
    if not rows:
        raise RuntimeError(f"腾讯接口未返回 {symbol}({sym}) 的数据")

    recs = []
    for r in rows:
        recs.append({
            "date": r[0],
            "open": float(r[1]),
            "close": float(r[2]),
            "high": float(r[3]),
            "low": float(r[4]),
            "volume": float(r[5]),
        })
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def fetch_data(
    symbol: str,
    start: str,
    end: str,
    adjust: str = "qfq",
    source: str = "auto",
) -> pd.DataFrame:
    """统一取数入口。

    source="auto" 时先试 akshare（字段更全，含换手率），失败自动降级到腾讯接口。
    """
    if source in ("auto", "akshare"):
        try:
            return fetch_akshare(symbol, start, end, adjust)
        except Exception as exc:  # noqa: BLE001
            if source == "akshare":
                raise
            print(f"  [提示] akshare 取数失败（{type(exc).__name__}），改用腾讯行情接口", file=sys.stderr)
    return fetch_tencent(symbol, start, end, adjust)


def load_csv(path: str) -> pd.DataFrame:
    """读取本地 CSV，自动识别日期列与成交量列（支持中英文表头）。"""
    df = pd.read_csv(path)
    alias = {
        "date": ("date", "日期", "trade_date", "time", "trade_date_time"),
        "volume": ("volume", "vol", "成交量", "volume_shares", "volume_lots"),
        "close": ("close", "收盘", "收盘价", "adjclose", "adj_close"),
        "turnover": ("turnover", "换手率", "turnover_rate"),
    }
    cols = {str(c).lower().strip(): c for c in df.columns}

    def pick(field: str) -> Optional[str]:
        for k in alias[field]:
            if k in cols:
                return cols[k]
        return None

    date_col = pick("date") or df.columns[0]
    vol_col = pick("volume")
    if vol_col is None:
        raise ValueError(f"{path} 中找不到成交量列（volume/vol/成交量）")
    close_col = pick("close")

    out = pd.DataFrame({"volume": pd.to_numeric(df[vol_col], errors="coerce")})
    out["close"] = pd.to_numeric(df[close_col], errors="coerce") if close_col else np.nan
    toch = pick("turnover")
    if toch:
        out["turnover"] = pd.to_numeric(df[toch], errors="coerce")
    out.index = pd.to_datetime(df[date_col])
    return out.sort_index().dropna(subset=["volume"])


def align(df_a: pd.DataFrame, df_b: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """按共同交易日对齐（内连接），保证两条序列逐一对应。"""
    common = df_a.index.intersection(df_b.index)
    if len(common) < 30:
        raise RuntimeError(f"共同交易日仅 {len(common)} 天，样本太少无法做节奏对比")
    return df_a.loc[common].copy(), df_b.loc[common].copy()


# ---------------------------------------------------------------------------
# 2. 量能节奏序列构造（核心：把「量级」换成「节奏」）
# ---------------------------------------------------------------------------
def build_rhythm(df: pd.DataFrame, ma: int = 20) -> pd.DataFrame:
    """把原始成交量转换成可比对的量能节奏特征。

    输出列：
        volume  —— 原始成交量
        base    —— N 日均量（放缩的基准）
        vr      —— 量比 = 成交量 / N 日均量，1.0 表示与近期均量持平
        lv      —— 对数成交量
        z       —— 量能 Z 分数 = (ln V - MA(ln V)) / STD(ln V)
        d_z     —— Z 分数一阶差分，用于判断「正在放量 / 正在缩量」
        state   —— 放量状态标签（巨量/放量/平量/缩量/地量）
        turnover—— 换手率（若有）
    """
    out = pd.DataFrame(index=df.index)
    v = df["volume"].astype(float).replace(0, np.nan)

    minp = max(3, ma // 3)
    out["volume"] = v
    out["base"] = v.rolling(ma, min_periods=minp).mean()
    out["vr"] = v / out["base"]

    lv = np.log(v)
    out["lv"] = lv
    mu = lv.rolling(ma, min_periods=minp).mean()
    sd = lv.rolling(ma, min_periods=minp).std(ddof=0).replace(0, np.nan)
    out["z"] = (lv - mu) / sd
    out["d_z"] = out["z"].diff()

    out["state"] = out["vr"].apply(classify_state)
    if "turnover" in df.columns:
        out["turnover"] = pd.to_numeric(df["turnover"], errors="coerce")
    return out


def classify_state(vr: float) -> str:
    """把量比映射成离散状态标签。"""
    if not np.isfinite(vr):
        return "未知"
    for label, thr in STATE_THRESHOLDS:
        if vr >= thr:
            return label
    return STATE_LABELS[-1]


# ---------------------------------------------------------------------------
# 3. 相似度度量
# ---------------------------------------------------------------------------
def dtw_distance(a: np.ndarray, b: np.ndarray, window: Optional[int] = None) -> float:
    """动态时间规整距离（Sakoe-Chiba 带约束），允许两条节奏序列在时间轴上有错位。

    相比欧氏距离，DTW 能识别「形状一样、但节拍稍微拉开」的相似节奏，
    这正是判断「放量缩量节奏相似」时最需要的能力。
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("nan")
    w = window if window is not None else max(5, int(0.15 * max(n, m)))
    w = max(w, abs(n - m) + 1)

    INF = float("inf")
    prev = np.full(m + 1, INF)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = np.full(m + 1, INF)
        lo = max(1, i - w)
        hi = min(m, i + w)
        for j in range(lo, hi + 1):
            cost = abs(a[i - 1] - b[j - 1])
            cur[j] = cost + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return float(prev[m])


def dtw_similarity(a: np.ndarray, b: np.ndarray, window: Optional[int] = None) -> float:
    """把 DTW 距离归一化成 0~1 的相似度（1 = 完全相同）。"""
    d = dtw_distance(a, b, window)
    if not np.isfinite(d):
        return float("nan")
    # 归一化基准：用两条序列各自的平均绝对波动幅度做尺度校准
    scale = float(np.nanmean([np.nanmean(np.abs(np.diff(a))) if len(a) > 1 else 0.0,
                              np.nanmean(np.abs(np.diff(b))) if len(b) > 1 else 0.0]))
    scale = max(scale, 1e-6)
    return float(np.exp(-d / max(len(a), len(b)) / scale))


def best_lag_corr(a: np.ndarray, b: np.ndarray, max_lag: int = 10) -> Tuple[int, float]:
    """扫描滞后阶数，找出相关性最高时的滞后与相关系数。

    返回值 > 0 表示 B 领先 A（A_t 与 B_{t-k} 更相关），
    即 B 先放量、A 后跟随。
    """
    best_lag, best_r = 0, -np.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            # A 的第 t 天 对齐 B 的第 t-lag 天：检验 B 是否领先 A
            x, y = a[lag:], b[: len(b) - lag]
        elif lag < 0:
            # A 的第 t 天 对齐 B 的第 t+lag 天：检验 A 是否领先 B
            x, y = a[: len(a) + lag], b[-lag:]
        else:
            x, y = a, b
        if len(x) < 20:
            continue
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 20 or np.std(x[mask]) == 0 or np.std(y[mask]) == 0:
            continue
        r = float(np.corrcoef(x[mask], y[mask])[0, 1])
        if r > best_r:
            best_lag, best_r = lag, r
    return best_lag, best_r


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman 秩相关系数（纯 numpy 实现，避免额外依赖 scipy）。

    对极端放量日（如涨停天量、解禁日天量）比 Pearson 稳健得多。
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    a, b = a[mask], b[mask]
    if len(a) < 3:
        return float("nan")
    ra = pd.Series(a).rank().values
    rb = pd.Series(b).rank().values
    if np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def direction_agreement(a: np.ndarray, b: np.ndarray) -> float:
    """方向一致率：两者同一天「同时转放量」或「同时转缩量」的比例。

    只看量的变化方向（一阶差分符号），不看幅度，衡量节奏的「拍点」是否对齐。
    """
    da, db = np.sign(np.diff(a)), np.sign(np.diff(b))
    mask = np.isfinite(da) & np.isfinite(db) & (da != 0) & (db != 0)
    if mask.sum() == 0:
        return float("nan")
    return float((da[mask] == db[mask]).mean())


def state_agreement(states_a: Sequence[str], states_b: Sequence[str]) -> Tuple[float, float]:
    """状态一致率与 Cohen's Kappa（剔除随机巧合后的真实一致性）。

    Kappa 比原始一致率更严谨：一致率 60% 在只有 2 个状态时可能纯属随机，
    Kappa 会把这种随机性扣掉。
    """
    a = np.array(states_a)
    b = np.array(states_b)
    mask = (a != "未知") & (b != "未知")
    a, b = a[mask], b[mask]
    if len(a) == 0:
        return float("nan"), float("nan")
    po = float((a == b).mean())
    labels = sorted(set(a.tolist()) | set(b.tolist()))
    pe = 0.0
    for lb in labels:
        pe += (a == lb).mean() * (b == lb).mean()
    kappa = float((po - pe) / (1 - pe)) if pe < 1 else float("nan")
    return po, kappa


def event_overlap(vr_a: pd.Series, vr_b: pd.Series, thr: float = 1.8, tol: int = 1) -> Dict[str, float]:
    """放量脉冲重合度（Jaccard）。

    对「放量日」（量比 > thr）做集合比对，允许 ±tol 天的容错，
    衡量两股是不是被同一批资金 / 事件同时点燃。
    """
    idx = vr_a.index
    ia = np.where(vr_a.fillna(0).values >= thr)[0]
    ib = np.where(vr_b.fillna(0).values >= thr)[0]
    if len(ia) == 0 or len(ib) == 0:
        return {"count_a": len(ia), "count_b": len(ib), "jaccard": float("nan"), "overlap": 0}

    set_b = set(ib.tolist())
    matched = 0
    matched_pos: List[int] = []
    for i in ia:
        hit = any((i + d) in set_b for d in range(-tol, tol + 1))
        if hit:
            matched += 1
            matched_pos.append(i)
    union = len(set(ia.tolist()) | set(ib.tolist()))
    return {
        "count_a": len(ia),
        "count_b": len(ib),
        "overlap": matched,
        "jaccard": matched / union if union else float("nan"),
        "dates_a": [idx[i].strftime("%Y-%m-%d") for i in matched_pos],
    }


def rolling_correlation(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    """滚动相关系数，用于看节奏同步性是「一直同步」还是「只在某段行情里同步」。"""
    return x.rolling(window, min_periods=max(5, window // 3)).corr(y)


# ---------------------------------------------------------------------------
# 4. 汇总
# ---------------------------------------------------------------------------
@dataclass
class RhythmReport:
    name_a: str
    name_b: str
    n_days: int
    date_range: Tuple[str, str]
    metrics: Dict[str, float] = field(default_factory=dict)
    extra: Dict[str, object] = field(default_factory=dict)

    @property
    def composite(self) -> float:
        """综合节奏相似度（0~1）的加权合成。

        权重设计依据：
          - DTW 形状相似度 30%：最能代表「节奏形状」，允许错位，是核心
          - 量能 Z 分数相关 20%：捕捉幅度层面的同步
          - 量比相关 15%      ：原始放缩倍率的同步
          - 方向一致率 15%    ：拍点对齐
          - 状态一致率(Kappa)20%：离散化后的稳健一致性
        """
        m = self.metrics
        parts = [
            (m.get("dtw_similarity"), 0.30),
            (_clip01(m.get("corr_z")), 0.20),
            (_clip01(m.get("corr_vr")), 0.15),
            (_clip01(m.get("direction_agreement")), 0.15),
            (_clip01(m.get("kappa_state")), 0.20),
        ]
        num = sum(v * w for v, w in parts if v is not None and np.isfinite(v))
        den = sum(w for v, w in parts if v is not None and np.isfinite(v))
        return float(num / den) if den else float("nan")

    def grade(self) -> str:
        c = self.composite
        if not np.isfinite(c):
            return "无法判定"
        for thr, text in SIM_GRADES:
            if c >= thr:
                return text
        return SIM_GRADES[-1][1]


def _clip01(v: Optional[float]) -> Optional[float]:
    if v is None or not np.isfinite(v):
        return None
    return float(min(1.0, max(-1.0, v)))


def compare(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    name_a: str,
    name_b: str,
    ma: int = 20,
    roll: int = 60,
    event_thr: float = 1.8,
    max_lag: int = 10,
) -> Tuple[RhythmReport, pd.DataFrame, pd.DataFrame]:
    """主流程：对齐 → 构造节奏 → 计算全部相似度指标。"""
    df_a, df_b = align(df_a, df_b)
    ra = build_rhythm(df_a, ma=ma)
    rb = build_rhythm(df_b, ma=ma)

    z_a, z_b = ra["z"].values, rb["z"].values
    vr_a, vr_b = ra["vr"], rb["vr"]

    mask = np.isfinite(z_a) & np.isfinite(z_b)
    zc_a, zc_b = z_a[mask], z_b[mask]
    vmask = np.isfinite(vr_a.values) & np.isfinite(vr_b.values)

    m: Dict[str, float] = {}
    m["corr_z"] = float(np.corrcoef(zc_a, zc_b)[0, 1]) if len(zc_a) > 2 else np.nan
    m["corr_vr"] = float(np.corrcoef(vr_a.values[vmask], vr_b.values[vmask])[0, 1]) if vmask.sum() > 2 else np.nan
    m["spearman_z"] = spearman(zc_a, zc_b)

    m["dtw_distance"] = dtw_distance(zc_a, zc_b)
    m["dtw_similarity"] = dtw_similarity(zc_a, zc_b)
    m["direction_agreement"] = direction_agreement(zc_a, zc_b)

    po, kappa = state_agreement(ra["state"].tolist(), rb["state"].tolist())
    m["state_agreement"] = po
    m["kappa_state"] = kappa

    lag, r_lag = best_lag_corr(zc_a, zc_b, max_lag=max_lag)
    m["best_lag"] = float(lag)
    m["corr_at_best_lag"] = r_lag

    ev = event_overlap(vr_a, vr_b, thr=event_thr)
    m["event_jaccard"] = float(ev["jaccard"])

    # 次要统计量：展示在报告里，不参与合成
    extra = {
        "event_a": ev["count_a"],
        "event_b": ev["count_b"],
        "event_overlap": ev["overlap"],
        "event_dates": ev.get("dates_a", []),
        "vol_share_a": float(np.nanmean(ra["vr"])),
        "vol_share_b": float(np.nanmean(rb["vr"])),
        "vol_cv_a": float(np.nanstd(ra["volume"]) / max(np.nanmean(ra["volume"]), 1e-9)),
        "vol_cv_b": float(np.nanstd(rb["volume"]) / max(np.nanmean(rb["volume"]), 1e-9)),
        "roll_corr_mean": float(rolling_correlation(ra["z"], rb["z"], roll).mean()),
        "roll_corr_min": float(rolling_correlation(ra["z"], rb["z"], roll).min()),
        "up_days_a": int((ra["vr"] >= 1.3).sum()),
        "up_days_b": int((rb["vr"] >= 1.3).sum()),
        "down_days_a": int((ra["vr"] <= 0.75).sum()),
        "down_days_b": int((rb["vr"] <= 0.75).sum()),
    }

    rep = RhythmReport(
        name_a=name_a,
        name_b=name_b,
        n_days=len(df_a),
        date_range=(df_a.index[0].strftime("%Y-%m-%d"), df_a.index[-1].strftime("%Y-%m-%d")),
        metrics=m,
        extra=extra,
    )
    return rep, ra, rb


# ---------------------------------------------------------------------------
# 5. 输出
# ---------------------------------------------------------------------------
def print_report(rep: RhythmReport, roll_window: int = 60, event_thr: float = 1.8) -> None:
    m, e = rep.metrics, rep.extra
    line = "=" * 68
    print(line)
    print(f"成交量节奏相似度报告   {rep.name_a}  vs  {rep.name_b}")
    print(line)
    print(f"样本区间: {rep.date_range[0]} ~ {rep.date_range[1]}   共同交易日: {rep.n_days} 天")
    print()

    print("【一】核心结论")
    print(f"  综合节奏相似度 : {rep.composite:.3f} / 1.000")
    print(f"  判定           : {rep.grade()}")
    if np.isfinite(m.get("corr_at_best_lag", np.nan)):
        lag = int(m["best_lag"])
        if lag == 0:
            print(f"  最佳同步滞后   : 0 天（两者同拍，无领先滞后）")
        elif lag > 0:
            print(f"  最佳同步滞后   : {lag} 天（{rep.name_b} 领先 {rep.name_a}，相关系数 {m['corr_at_best_lag']:.3f}）")
        else:
            print(f"  最佳同步滞后   : {abs(lag)} 天（{rep.name_a} 领先 {rep.name_b}，相关系数 {m['corr_at_best_lag']:.3f}）")
    print()

    print("【二】幅度同步性（是否同时放量 / 同时缩量）")
    print(f"  量能 Z 分数 Pearson 相关   : {m.get('corr_z', float('nan')):+.3f}")
    print(f"  量能 Z 分数 Spearman 相关  : {m.get('spearman_z', float('nan')):+.3f}   （抗极端值）")
    print(f"  量比 VR 相关系数           : {m.get('corr_vr', float('nan')):+.3f}")
    print(f"  {roll_window} 日滚动相关均值 / 最低 : {e['roll_corr_mean']:+.3f} / {e['roll_corr_min']:+.3f}")
    print()

    print("【三】节拍同步性（节奏形状是否一致）")
    print(f"  DTW 距离（Z 分数口径）     : {m.get('dtw_distance', float('nan')):.2f}   （越小越像）")
    print(f"  DTW 归一化相似度           : {m.get('dtw_similarity', float('nan')):.3f}")
    print(f"  方向一致率（同向放/缩量）  : {m.get('direction_agreement', float('nan')):.1%}")
    print(f"  离散状态一致率             : {m.get('state_agreement', float('nan')):.1%}")
    print(f"  Cohen's Kappa（扣随机后）  : {m.get('kappa_state', float('nan')):+.3f}")
    print()

    print(f"【四】放量脉冲重合（量比 > {event_thr} 视为放量）")
    print(f"  {rep.name_a} 放量日: {e['event_a']} 天 / {rep.n_days} 天 "
          f"({e['event_a'] / rep.n_days:.1%})")
    print(f"  {rep.name_b} 放量日: {e['event_b']} 天 / {rep.n_days} 天 "
          f"({e['event_b'] / rep.n_days:.1%})")
    print(f"  容错 ±1 天后重合日: {e['event_overlap']} 天   Jaccard: {m.get('event_jaccard', float('nan')):.3f}")
    if e["event_dates"]:
        show = e["event_dates"][:8]
        more = "" if len(e["event_dates"]) <= 8 else f" 等 {len(e['event_dates'])} 天"
        print(f"  共同放量日期: {', '.join(show)}{more}")
    print()

    print("【五】量能结构对比")
    print(f"  放量日(VR≥1.3)   {rep.name_a}: {e['up_days_a']} 天 | {rep.name_b}: {e['up_days_b']} 天")
    print(f"  缩量日(VR≤0.75)  {rep.name_a}: {e['down_days_a']} 天 | {rep.name_b}: {e['down_days_b']} 天")
    print(f"  成交量变异系数   {rep.name_a}: {e['vol_cv_a']:.2f} | {rep.name_b}: {e['vol_cv_b']:.2f}  （越大越躁动）")
    print()
    print("注：VR = 当日成交量 / 近 N 日均量；所有相似度已剔除成交量绝对量级差异。")
    print("    本报告仅做量能节奏统计，不含价格与买卖判断。")
    print(line)


def plot_rhythm(
    ra: pd.DataFrame,
    rb: pd.DataFrame,
    name_a: str,
    name_b: str,
    out_path: str,
    roll: int = 60,
) -> str:
    """画出量能节奏对比图：上图为量比 VR，下图为量能 Z 分数与滚动相关。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _setup_cjk_font()

    fig, axes = plt.subplots(3, 1, figsize=(13, 11), sharex=True,
                             gridspec_kw={"height_ratios": [1.2, 1.2, 0.8]})

    ax1, ax2, ax3 = axes

    ax1.plot(ra.index, ra["vr"], color="#c0392b", lw=1.1, label=f"{name_a} 量比")
    ax1.plot(rb.index, rb["vr"], color="#2471a3", lw=1.1, alpha=0.85, label=f"{name_b} 量比")
    ax1.axhline(1.0, color="#7f8c8d", ls="--", lw=0.8)
    ax1.axhline(1.3, color="#e67e22", ls=":", lw=0.8)
    ax1.axhline(0.75, color="#27ae60", ls=":", lw=0.8)
    ax1.set_ylabel("量比 VR")
    ax1.set_title(f"成交量节奏对比：{name_a} vs {name_b}", fontsize=13, pad=10)
    ax1.legend(loc="upper left", fontsize=9, ncol=2)
    ax1.grid(alpha=0.25)

    ax2.plot(ra.index, ra["z"], color="#c0392b", lw=1.0, label=f"{name_a} 量能Z")
    ax2.plot(rb.index, rb["z"], color="#2471a3", lw=1.0, alpha=0.85, label=f"{name_b} 量能Z")
    ax2.axhline(0, color="#7f8c8d", ls="--", lw=0.8)
    ax2.fill_between(ra.index, ra["z"], rb["z"], where=ra["z"] > rb["z"],
                     color="#c0392b", alpha=0.08)
    ax2.fill_between(ra.index, ra["z"], rb["z"], where=ra["z"] <= rb["z"],
                     color="#2471a3", alpha=0.08)
    ax2.set_ylabel("量能 Z 分数")
    ax2.legend(loc="upper left", fontsize=9, ncol=2)
    ax2.grid(alpha=0.25)

    rc = rolling_correlation(ra["z"], rb["z"], roll)
    ax3.plot(rc.index, rc, color="#6c3483", lw=1.2)
    ax3.axhline(0, color="#7f8c8d", ls="--", lw=0.8)
    ax3.axhline(0.6, color="#27ae60", ls=":", lw=0.8)
    ax3.set_ylabel(f"{roll} 日滚动相关")
    ax3.set_xlabel("日期")
    ax3.grid(alpha=0.25)

    for ax in axes:
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)

    fig.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _setup_cjk_font() -> None:
    """让 matplotlib 正常显示中文（macOS / Windows / Linux 通用回退）。"""
    import matplotlib
    from matplotlib import font_manager

    candidates = ["PingFang SC", "Heiti TC", "Songti SC", "Arial Unicode MS",
                  "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name] + matplotlib.rcParams["font.sans-serif"]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def export_csv(ra: pd.DataFrame, rb: pd.DataFrame, out_path: str, name_a: str, name_b: str) -> str:
    """把量能节奏明细导出为 CSV，方便用户自行做进一步分析。"""
    out = pd.DataFrame({
        f"{name_a}_volume": ra["volume"],
        f"{name_a}_vr": ra["vr"].round(4),
        f"{name_a}_z": ra["z"].round(4),
        f"{name_a}_state": ra["state"],
        f"{name_b}_volume": rb["volume"],
        f"{name_b}_vr": rb["vr"].round(4),
        f"{name_b}_z": rb["z"].round(4),
        f"{name_b}_state": rb["state"],
    })
    out.to_csv(out_path, encoding="utf-8-sig")
    return out_path


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="对比两只股票的成交量走势 / 放量缩量节奏相似度",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python volume_rhythm_compare.py 688213 603501 --start 2025-09-01",
    )
    p.add_argument("symbols", nargs="*", help="两只 A 股代码，如 688213 603501")
    p.add_argument("--csv", nargs=2, metavar=("A.csv", "B.csv"), help="改用本地 CSV 数据")
    p.add_argument("--names", nargs=2, metavar=("名称A", "名称B"), help="自定义显示名称")
    p.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD，默认 400 个自然日前")
    p.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，默认今天")
    p.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""], help="复权方式，默认前复权")
    p.add_argument("--source", default="auto", choices=["auto", "akshare", "tencent"],
                   help="数据源：auto=先 akshare 再腾讯兜底（默认），tencent=只用腾讯接口")
    p.add_argument("--ma", type=int, default=20, help="计算量比 / Z 分数的均量窗口，默认 20")
    p.add_argument("--roll", type=int, default=60, help="滚动相关窗口，默认 60")
    p.add_argument("--event-thr", type=float, default=1.8, help="判定放量脉冲的量比阈值，默认 1.8")
    p.add_argument("--max-lag", type=int, default=10, help="领先滞后扫描上限（天），默认 10")
    p.add_argument("--out-dir", default=".", help="输出目录，默认当前目录")
    p.add_argument("--no-chart", action="store_true", help="不输出图表")
    p.add_argument("--export", action="store_true", help="额外导出量能节奏明细 CSV")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    if args.csv:
        df_a, df_b = load_csv(args.csv[0]), load_csv(args.csv[1])
        default_names = [args.csv[0].split("/")[-1], args.csv[1].split("/")[-1]]
    else:
        if len(args.symbols) != 2:
            print("错误：需要恰好两个 A 股代码，例如：python volume_rhythm_compare.py 688213 603501", file=sys.stderr)
            return 2
        try:
            df_a = fetch_data(args.symbols[0], start, end, args.adjust, args.source)
            df_b = fetch_data(args.symbols[1], start, end, args.adjust, args.source)
        except Exception as exc:  # noqa: BLE001
            print(f"数据获取失败：{exc}", file=sys.stderr)
            return 1
        default_names = args.symbols

    names = args.names or default_names
    name_a, name_b = names[0], names[1]

    try:
        rep, ra, rb = compare(
            df_a, df_b, name_a, name_b,
            ma=args.ma, roll=args.roll, event_thr=args.event_thr, max_lag=args.max_lag,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"计算失败：{exc}", file=sys.stderr)
        return 1

    print_report(rep, roll_window=args.roll, event_thr=args.event_thr)

    stamp = datetime.now().strftime("%Y%m%d")
    if not args.no_chart:
        png = f"{args.out_dir}/volume_rhythm_{name_a}_{name_b}_{stamp}.png".replace(" ", "_")
        try:
            plot_rhythm(ra, rb, name_a, name_b, png, roll=args.roll)
            print(f"图表已保存: {png}")
        except Exception as exc:  # noqa: BLE001
            print(f"绘图失败（不影响相似度结果）：{exc}", file=sys.stderr)

    if args.export:
        csv_path = f"{args.out_dir}/volume_rhythm_{name_a}_{name_b}_{stamp}.csv".replace(" ", "_")
        export_csv(ra, rb, csv_path, name_a, name_b)
        print(f"明细已导出: {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
