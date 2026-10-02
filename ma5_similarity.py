#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ma5_similarity.py —— 计算两只股票在指定日期区间内「5 日均线（MA5）相似度」

为什么不能直接对比两条 MA5
--------------------------
思特威 MA5 ≈ 98 元，豪威集团 MA5 ≈ 130 元，两条曲线的绝对价位天然不同，
直接算相关/距离会被「谁价格高」这一个常数项主导，得到的结论没有意义。
本程序把 MA5 转成三种可比的归一化形态，每种回答一个不同的问题：

    rebase  相对路径 : ma5 / 区间首日 ma5        -> 这段区间里谁的涨幅路径一样
    zscore  标准分   : (ln ma5 - 均值) / 标准差  -> 剔除量级与波动幅度后的曲线形状
    return  MA5 收益 : diff(ln ma5)              -> 最纯粹的「均线抬升/走平/下压节奏」

「相似」拆成两层分别度量
------------------------
  (a) 曲线形态像不像  —— 相关系数、DTW 形状相似度、最大背离
  (b) 趋势节奏像不像  —— MA5 收益率相关、斜率方向一致率、金叉死叉事件重合

只看相关系数会把「同涨同跌但幅度差很多」和「形态几乎重合」混为一谈，
所以必须同时给出 DTW 距离、斜率一致率、均线多空状态一致率。

另外三条关键工程处理
--------------------
  1. 均线预热：取数起点自动前移 90 个自然日，先算 MA 再截取目标区间，
     避免区间头 4 天因窗口不足而缺失（MA20 更要预热）。
  2. 各算各的日历：先在各自原始交易日上算 MA，再取共同交易日对齐，
     停牌日不会污染均线。
  3. 剔除常数项：所有形态指标都建立在归一化序列上，量级差异不进入结论。

依赖
----
    pip install pandas numpy matplotlib        # akshare 可选，装了更全

用法
----
    # 思特威 vs 豪威集团，指定区间，输出报告 + 图表
    python ma5_similarity.py 688213 603501 --start 2025-09-01 --end 2026-09-30

    # 换成 10 日均线对比，导出明细
    python ma5_similarity.py 688213 603501 --start 2026-01-01 --ma 10 --export

    # 用本地 CSV（列名含 date / close 即可）
    python ma5_similarity.py --csv a.csv b.csv --names 股票A 股票B
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
# 常量
# ---------------------------------------------------------------------------
WARMUP_DAYS = 90            # 均线预热自然日数（保证 MA60 也能算出来）
STATE_TOL = 0.005           # MA5 与 MA20 相差 0.5% 以内视为「纠缠」
DIVERGE_WIN = 20            # 背离诊断的滚动窗口（交易日）

SIM_GRADES: Tuple[Tuple[float, str], ...] = (
    (0.80, "高度相似 —— 均线形态与节奏几乎重合，可视为同一逻辑驱动"),
    (0.65, "较高相似 —— 走势大体同步，偶有阶段性背离"),
    (0.50, "中度相似 —— 有共同驱动，但节奏与幅度差异明显"),
    (0.00, "低相似 —— 均线形态基本各走各的，不宜按同一节奏操作"),
)


# ===========================================================================
# 1. 数据层（akshare 主源 + 腾讯行情兜底 + 本地 CSV）
# ===========================================================================
def fetch_akshare(symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """akshare 拉取 A 股日线。"""
    import akshare as ak

    s, e = start.replace("-", ""), end.replace("-", "")
    raw = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=s, end_date=e, adjust=adjust)
    if raw is None or raw.empty:
        raise RuntimeError(f"akshare 未返回 {symbol} 数据")
    ren = {"日期": "date", "收盘": "close", "开盘": "open", "最高": "high",
           "最低": "low", "成交量": "volume", "成交额": "amount", "换手率": "turnover"}
    df = raw.rename(columns=ren)
    keep = [c for c in ["date", "open", "high", "low", "close", "volume", "amount", "turnover"] if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def _tencent_symbol(symbol: str) -> str:
    """6 位 A 股代码 -> sh/sz/bj 前缀。"""
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
    """腾讯行情接口拉取日线（不依赖 akshare 的备用源）。"""
    import json
    import urllib.request

    key = {"qfq": "qfqday", "hfq": "hfqday", "": "day"}.get(adjust, "qfqday")
    sym = _tencent_symbol(symbol)
    span = int(max((pd.Timestamp(end) - pd.Timestamp(start)).days * 5 / 7 + 40, 80))
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={sym},day,{start},{end},{span},{adjust or ''}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    node = (payload.get("data") or {}).get(sym) or {}
    rows = node.get(key) or node.get("day") or []
    if not rows:
        raise RuntimeError(f"腾讯接口未返回 {symbol}({sym}) 数据")

    df = pd.DataFrame(
        [{"date": r[0], "open": float(r[1]), "close": float(r[2]),
          "high": float(r[3]), "low": float(r[4]), "volume": float(r[5])} for r in rows]
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def fetch_data(symbol: str, start: str, end: str, adjust: str = "qfq",
               source: str = "auto") -> pd.DataFrame:
    """统一取数入口：auto 模式先 akshare，失败自动降级到腾讯。"""
    if source in ("auto", "akshare"):
        try:
            return fetch_akshare(symbol, start, end, adjust)
        except Exception as exc:  # noqa: BLE001
            if source == "akshare":
                raise
            print(f"  [提示] akshare 取数失败（{type(exc).__name__}），改用腾讯行情接口",
                  file=sys.stderr)
    return fetch_tencent(symbol, start, end, adjust)


def load_csv(path: str) -> pd.DataFrame:
    """读取本地 CSV，自动识别日期列与收盘列（中英文表头均可）。"""
    df = pd.read_csv(path)
    alias = {
        "date": ("date", "日期", "trade_date", "time"),
        "close": ("close", "收盘", "收盘价", "adjclose", "adj_close", "price"),
    }
    cols = {str(c).lower().strip(): c for c in df.columns}

    def pick(f: str) -> Optional[str]:
        return next((cols[k] for k in alias[f] if k in cols), None)

    date_col = pick("date") or df.columns[0]
    close_col = pick("close")
    if close_col is None:
        raise ValueError(f"{path} 中找不到收盘价列（close/收盘）")
    out = pd.DataFrame({"close": pd.to_numeric(df[close_col], errors="coerce")})
    out.index = pd.to_datetime(df[date_col])
    return out.sort_index().dropna(subset=["close"])


# ===========================================================================
# 2. 均线计算与归一化
# ===========================================================================
def compute_mas(df: pd.DataFrame, windows: Sequence[int]) -> pd.DataFrame:
    """在各自原始交易日上计算移动均线（不做对齐，避免停牌污染）。"""
    out = df.copy()
    for w in windows:
        out[f"ma{w}"] = out["close"].rolling(w, min_periods=w).mean()
    return out


def normalize_mas(ma: pd.Series) -> Dict[str, pd.Series]:
    """把一条均线转成三种可比的归一化形态。"""
    s = ma.astype(float)
    rebase = s / s.iloc[0]                          # 首日 = 1.0，看相对涨跌路径
    ls = np.log(s)
    z = (ls - ls.mean()) / ls.std(ddof=0) if ls.std(ddof=0) > 0 else ls * 0.0
    ret = ls.diff()                                  # MA5 日对数收益率
    return {"rebase": rebase, "zscore": z, "return": ret}


# ===========================================================================
# 3. 相似度度量
# ===========================================================================
def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman 秩相关（纯 numpy，无需 scipy）。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    if len(a) < 3:
        return float("nan")
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def safe_corr(a: np.ndarray, b: np.ndarray, min_n: int = 5) -> float:
    """带缺失值掩码的 Pearson 相关。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < min_n or a[m].std() == 0 or b[m].std() == 0:
        return float("nan")
    return float(np.corrcoef(a[m], b[m])[0, 1])


def dtw_path_stats(a: np.ndarray, b: np.ndarray,
                   window: Optional[int] = None) -> Tuple[float, int]:
    """DTW 累计距离 + 最优路径步数（回溯得到）。

    返回路径步数是为了把累计距离换算成「平均每步失配」，否则 300 天的序列
    距离天然是 100 天的 3 倍，不同长度区间之间完全没法比。
    """
    a, b = np.asarray(a, float), np.asarray(b, float)
    m_ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[m_ok], b[m_ok]
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("nan"), 0
    w = window if window is not None else max(5, int(0.15 * max(n, m)))
    w = max(w, abs(n - m) + 1)

    INF = float("inf")
    D = np.full((n + 1, m + 1), INF)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        lo, hi = max(1, i - w), min(m, i + w)
        for j in range(lo, hi + 1):
            D[i, j] = abs(a[i - 1] - b[j - 1]) + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])

    # 回溯最优路径，统计步数
    i, j, steps = n, m, 0
    while i > 0 or j > 0:
        steps += 1
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            cand = ((D[i - 1, j - 1], i - 1, j - 1),
                    (D[i - 1, j], i - 1, j),
                    (D[i, j - 1], i, j - 1))
            _, i, j = min(cand, key=lambda t: t[0])
    return float(D[n, m]), steps


DTW_SIGMA = 0.5  # 每步失配 0.5 个标准差 -> 相似度 exp(-1) ≈ 0.37


def dtw_similarity(a: np.ndarray, b: np.ndarray,
                   sigma: float = DTW_SIGMA) -> Tuple[float, float]:
    """把 DTW 累计距离换算成 0~1 相似度。

    输入约定为独立标准分序列（各自 std=1），因此「每步平均失配」可以直接按
    标准差倍数解读：0.1 步失配 ≈ 极其贴合，0.5 ≈ 明显不同，1.0 ≈ 基本无关。
    映射函数取 exp(-per_step / sigma)，sigma=0.5：
        per_step=0.05 -> 0.90    per_step=0.20 -> 0.67
        per_step=0.35 -> 0.50    per_step=0.70 -> 0.25

    Returns:
        (similarity, per_step_distance)
    """
    d, steps = dtw_path_stats(a, b)
    if not np.isfinite(d) or steps == 0:
        return float("nan"), float("nan")
    per_step = d / steps
    return float(np.exp(-per_step / sigma)), float(per_step)


def slope_direction(ma: pd.Series) -> np.ndarray:
    """均线斜率方向：+1 上行 / -1 下行 / 0 走平。"""
    return np.sign(ma.diff().values)


def slope_agreement(sa: np.ndarray, sb: np.ndarray) -> float:
    """斜率方向一致率（MA5 是否同一天同时拐头向上 / 向下）。"""
    m = np.isfinite(sa) & np.isfinite(sb) & (sa != 0) & (sb != 0)
    if m.sum() == 0:
        return float("nan")
    return float((sa[m] == sb[m]).mean())


def ma_state(ma_fast: pd.Series, ma_slow: pd.Series, tol: float = STATE_TOL) -> pd.Series:
    """均线多空状态：多头（快线在上）/ 空头（快线在下）/ 纠缠（差距在容差内）。"""
    gap = (ma_fast - ma_slow) / ma_slow.replace(0, np.nan)
    return pd.Series(
        np.where(gap > tol, "多头", np.where(gap < -tol, "空头", "纠缠")),
        index=ma_fast.index,
    )


def categorical_agreement(a: Sequence[str], b: Sequence[str]) -> Tuple[float, float]:
    """分类一致率 + Cohen's Kappa（剔除随机巧合后的真实一致性）。"""
    a, b = np.asarray(a), np.asarray(b)
    m = (a != "NA") & (b != "NA")
    a, b = a[m], b[m]
    if len(a) == 0:
        return float("nan"), float("nan")
    po = float((a == b).mean())
    labels = sorted(set(a.tolist()) | set(b.tolist()))
    pe = sum((a == lb).mean() * (b == lb).mean() for lb in labels)
    kappa = float((po - pe) / (1 - pe)) if pe < 1 else float("nan")
    return po, kappa


def cross_events(ma_fast: pd.Series, ma_slow: pd.Series) -> Tuple[List[int], List[int]]:
    """找出金叉（快线上穿慢线）与死叉（快线下穿慢线）的位置序号。"""
    diff = (ma_fast - ma_slow).values
    prev = np.roll(diff, 1)
    prev[0] = diff[0]
    golden = np.where((prev <= 0) & (diff > 0))[0].tolist()
    death = np.where((prev >= 0) & (diff < 0))[0].tolist()
    return golden, death


def event_overlap(ea: Sequence[int], eb: Sequence[int], tol: int = 2) -> Dict[str, object]:
    """金叉/死叉事件重合度：允许 ±tol 天错位，返回命中数、Jaccard 与明细。"""
    if not ea or not eb:
        return {"count_a": len(ea), "count_b": len(eb), "hit": 0, "jaccard": float("nan"), "matched": []}
    set_b = set(eb)
    matched = [i for i in ea if any((i + d) in set_b for d in range(-tol, tol + 1))]
    union = len(set(ea) | set(eb))
    return {
        "count_a": len(ea),
        "count_b": len(eb),
        "hit": len(matched),
        "jaccard": len(matched) / union if union else float("nan"),
        "matched": matched,
    }


def best_lag_corr(a: np.ndarray, b: np.ndarray, max_lag: int = 10) -> Tuple[int, float]:
    """扫描滞后阶数：lag > 0 表示 B 的均线领先 A；< 0 表示 A 领先 B。"""
    best_lag, best_r = 0, -np.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            x, y = a[lag:], b[: len(b) - lag]
        elif lag < 0:
            x, y = a[: len(a) + lag], b[-lag:]
        else:
            x, y = a, b
        if len(x) < 20:
            continue
        r = safe_corr(x, y, min_n=20)
        if np.isfinite(r) and r > best_r:
            best_lag, best_r = lag, r
    return best_lag, best_r


def ols_log_regression(a: pd.Series, b: pd.Series) -> Tuple[float, float, float, float]:
    """对数价回归：ln MA_b = α + β·ln MA_a。

    返回 (β, R², 残差标准差(对数), 残差最大偏离(对数))。
    β 是相对弹性（A 的均线涨 1%，B 的均线平均涨 β%）；
    R² 说明 A 的均线能解释 B 均线多少比例的变动；
    残差标准差是「剔除共同趋势后，B 相对 A 的典型偏离幅度」——
    注意不能按 √252 年化：均线本身高度平滑、残差强自相关，年化会严重虚高。
    """
    la, lb = np.log(a.values), np.log(b.values)
    m = np.isfinite(la) & np.isfinite(lb)
    la, lb = la[m], lb[m]
    if len(la) < 10 or la.std() == 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    beta, alpha = np.polyfit(la, lb, 1)
    resid = lb - (alpha + beta * la)
    ss_tot = ((lb - lb.mean()) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot > 0 else float("nan")
    return float(beta), float(r2), float(np.std(resid, ddof=1)), float(np.max(np.abs(resid)))


def divergence_periods(norm_a: pd.Series, norm_b: pd.Series, win: int = DIVERGE_WIN,
                       top: int = 3) -> List[Dict[str, object]]:
    """背离诊断：找出归一化曲线差距最大的若干区段（非重叠）。"""
    gap = (norm_a - norm_b).abs()
    roll = gap.rolling(win, min_periods=win // 2).mean()
    out: List[Dict[str, object]] = []
    used = np.zeros(len(roll), dtype=bool)
    order = np.argsort(-np.nan_to_num(roll.values, nan=-1))
    for i in order:
        if len(out) >= top or not np.isfinite(roll.values[i]) or roll.values[i] <= 0:
            break
        lo, hi = max(0, i - win), min(len(roll), i + win)
        if used[lo:hi].any():
            continue
        used[lo:hi] = True
        seg = slice(max(0, i - win + 1), i + 1)
        out.append({
            "start": norm_a.index[seg.start].strftime("%Y-%m-%d"),
            "end": norm_a.index[seg.stop - 1].strftime("%Y-%m-%d"),
            "mean_gap": float(roll.values[i]),
            "max_gap": float(gap.iloc[seg].max()),
            "during": "A 强于 B" if (norm_a.iloc[seg] - norm_b.iloc[seg]).mean() > 0 else "B 强于 A",
        })
    return out


# ===========================================================================
# 4. 汇总
# ===========================================================================
@dataclass
class MaReport:
    name_a: str
    name_b: str
    ma_period: int
    n_days: int
    date_range: Tuple[str, str]
    metrics: Dict[str, float] = field(default_factory=dict)
    extra: Dict[str, object] = field(default_factory=dict)

    @property
    def composite(self) -> float:
        """综合均线相似度（0~1）。

        权重依据：
          - DTW 形状相似度 25%：最能代表「曲线形态」，且允许节拍错位
          - 归一化曲线相关 20%：整体路径是否同向同行
          - MA5 收益率相关 20%：均线抬升/走平的节奏是否同步（尺度无关，最干净）
          - 斜率方向一致率 15%：是不是同一天拐头
          - 多空状态一致率(Kappa) 20%：MA5 相对 MA20 的排列是否长期一致，最稳健
        """
        m = self.metrics
        parts = [
            (m.get("dtw_similarity"), 0.25),
            (m.get("corr_rebase"), 0.20),
            (m.get("corr_ma_return"), 0.20),
            (m.get("slope_agreement"), 0.15),
            (m.get("kappa_state"), 0.20),
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


def compare(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    name_a: str,
    name_b: str,
    start: str,
    end: str,
    ma: int = 5,
    slow: int = 20,
    roll: int = 60,
    cross_tol: int = 2,
    max_lag: int = 10,
) -> Tuple[MaReport, pd.DataFrame, pd.DataFrame]:
    """主流程：算均线 -> 截区间 -> 对齐 -> 归一化 -> 全套相似度指标。"""
    windows = sorted({ma, ma * 2, slow, slow * 3, 60})
    windows = [w for w in windows if w >= 2]

    # 各算各的均线（用原始交易日，避免停牌污染），再截取目标区间
    ma_a_full = compute_mas(df_a, windows)
    ma_b_full = compute_mas(df_b, windows)

    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    out_full = ma_a_full.index[(ma_a_full.index >= t0) & (ma_a_full.index <= t1)]
    if len(out_full) < 10:
        raise RuntimeError(f"{name_a} 在 {start} ~ {end} 区间内仅有 {len(out_full)} 个交易日，样本不足")

    common = ma_a_full.index.intersection(ma_b_full.index)
    common = common[(common >= t0) & (common <= t1)]
    if len(common) < 10:
        raise RuntimeError(f"两只标的在该区间内共同交易日仅 {len(common)} 天，无法对比")

    a = ma_a_full.loc[common]
    b = ma_b_full.loc[common]
    if len(common) < len(out_full):
        # 提示因停牌/区间差异被剔除的交易日
        pass

    fk, sk = f"ma{ma}", f"ma{slow}"
    if sk not in a.columns:
        sk = f"ma{min(windows, key=lambda w: abs(w - slow))}"
    fast_a, fast_b = a[fk], b[fk]
    slow_a, slow_b = a[sk], b[sk]

    nz_a, nz_b = normalize_mas(fast_a), normalize_mas(fast_b)

    m: Dict[str, float] = {}
    # —— 形态同步性 ——
    m["corr_rebase"] = safe_corr(nz_a["rebase"].values, nz_b["rebase"].values)
    m["spearman_rebase"] = spearman(nz_a["rebase"].values, nz_b["rebase"].values)
    m["corr_zscore"] = safe_corr(nz_a["zscore"].values, nz_b["zscore"].values)
    dtw_sim, dtw_per_step = dtw_similarity(nz_a["zscore"].values, nz_b["zscore"].values)
    m["dtw_similarity"] = dtw_sim
    m["dtw_per_step"] = dtw_per_step
    m["rmse_rebase"] = float(np.sqrt(np.nanmean((nz_a["rebase"] - nz_b["rebase"]).values ** 2)))
    m["max_gap_rebase"] = float(np.nanmax(np.abs((nz_a["rebase"] - nz_b["rebase"]).values)))

    # —— 趋势节奏同步性 ——
    m["corr_ma_return"] = safe_corr(nz_a["return"].values, nz_b["return"].values)
    m["corr_close_return"] = safe_corr(
        np.log(a["close"]).diff().values, np.log(b["close"]).diff().values
    )
    m["slope_agreement"] = slope_agreement(slope_direction(fast_a), slope_direction(fast_b))

    # —— 均线多空状态 ——
    st_a, st_b = ma_state(fast_a, slow_a), ma_state(fast_b, slow_b)
    po, kappa = categorical_agreement(st_a.tolist(), st_b.tolist())
    m["state_agreement"] = po
    m["kappa_state"] = kappa

    # —— 金叉死叉事件 ——
    ga, da = cross_events(fast_a, slow_a)
    gb, db = cross_events(fast_b, slow_b)
    g_ov = event_overlap(ga, gb, tol=cross_tol)
    d_ov = event_overlap(da, db, tol=cross_tol)
    m["golden_jaccard"] = float(g_ov["jaccard"])
    m["death_jaccard"] = float(d_ov["jaccard"])
    all_ov = event_overlap(sorted(ga + da), sorted(gb + db), tol=cross_tol)
    m["cross_jaccard"] = float(all_ov["jaccard"])

    # —— 领先滞后 & 回归 ——
    lag, r_lag = best_lag_corr(nz_a["zscore"].values, nz_b["zscore"].values, max_lag=max_lag)
    m["best_lag"], m["corr_at_best_lag"] = float(lag), r_lag
    beta, r2, resid_std, resid_max = ols_log_regression(fast_a, fast_b)
    m["beta"], m["r_squared"] = beta, r2
    m["resid_std_log"], m["resid_max_log"] = resid_std, resid_max

    rc = nz_a["zscore"].rolling(roll, min_periods=max(10, roll // 3)).corr(nz_b["zscore"])
    extra = {
        "roll_corr_mean": float(rc.mean()),
        "roll_corr_min": float(rc.min()),
        "roll_corr_max": float(rc.max()),
        "divergences": divergence_periods(nz_a["rebase"], nz_b["rebase"]),
        "golden_a": [fast_a.index[i].strftime("%Y-%m-%d") for i in ga],
        "golden_b": [fast_b.index[i].strftime("%Y-%m-%d") for i in gb],
        "death_a": [fast_a.index[i].strftime("%Y-%m-%d") for i in da],
        "death_b": [fast_b.index[i].strftime("%Y-%m-%d") for i in db],
        "golden_hit_dates": [fast_a.index[i].strftime("%Y-%m-%d") for i in g_ov["matched"]],
        "death_hit_dates": [fast_a.index[i].strftime("%Y-%m-%d") for i in d_ov["matched"]],
        "state_share_a": {k: float(v) for k, v in st_a.value_counts(normalize=True).items()},
        "state_share_b": {k: float(v) for k, v in st_b.value_counts(normalize=True).items()},
        "ma_a_start": float(fast_a.iloc[0]),
        "ma_a_end": float(fast_a.iloc[-1]),
        "ma_b_start": float(fast_b.iloc[0]),
        "ma_b_end": float(fast_b.iloc[-1]),
        "pct_a": float(fast_a.iloc[-1] / fast_a.iloc[0] - 1),
        "pct_b": float(fast_b.iloc[-1] / fast_b.iloc[0] - 1),
        "slow_period": int(sk.replace("ma", "")),
        "skipped_days": int(len(out_full) - len(common)),
    }

    rep = MaReport(
        name_a=name_a, name_b=name_b, ma_period=ma, n_days=len(common),
        date_range=(common[0].strftime("%Y-%m-%d"), common[-1].strftime("%Y-%m-%d")),
        metrics=m, extra=extra,
    )
    return rep, a, b


# ===========================================================================
# 5. 报告与图表
# ===========================================================================
def print_report(rep: MaReport, roll: int = 60, cross_tol: int = 2) -> None:
    m, e = rep.metrics, rep.extra
    line = "=" * 70
    print(line)
    print(f"{rep.ma_period} 日均线相似度报告   {rep.name_a}  vs  {rep.name_b}")
    print(line)
    print(f"区间: {rep.date_range[0]} ~ {rep.date_range[1]}   共同交易日: {rep.n_days} 天"
          + (f"   （因交易日不重合剔除 {e['skipped_days']} 天）" if e["skipped_days"] else ""))
    print()

    print("【一】核心结论")
    print(f"  综合均线相似度 : {rep.composite:.3f} / 1.000")
    print(f"  判定           : {rep.grade()}")
    lag = int(m["best_lag"])
    if lag == 0:
        print("  最佳同步滞后   : 0 天（均线同步拐头，无领先滞后）")
    elif lag > 0:
        print(f"  最佳同步滞后   : {lag} 天（{rep.name_b} 的均线领先，相关系数 {m['corr_at_best_lag']:.3f}）")
    else:
        print(f"  最佳同步滞后   : {abs(lag)} 天（{rep.name_a} 的均线领先，相关系数 {m['corr_at_best_lag']:.3f}）")
    print()

    print("【二】曲线形态同步性（归一化后的 MA 曲线像不像）")
    print(f"  归一化曲线相关(首日=1)   : {m['corr_rebase']:+.3f}")
    print(f"  归一化曲线 Spearman      : {m['spearman_rebase']:+.3f}   （抗极端段）")
    print(f"  标准分曲线相关           : {m['corr_zscore']:+.3f}")
    print(f"  DTW 每步失配 / 归一化相似度: {m['dtw_per_step']:.3f} / {m['dtw_similarity']:.3f}"
          "   （失配单位：标准差倍数）")
    print(f"  归一化 RMSE / 最大偏离   : {m['rmse_rebase']:.4f} / {m['max_gap_rebase']:.4f}"
          "   （单位：倍数，0.10 表示累计偏离 10%）")
    print()

    print("【三】趋势节奏同步性（均线抬升 / 走平 / 下压的节奏）")
    print(f"  {rep.ma_period} 日均线收益率相关      : {m['corr_ma_return']:+.3f}   ← 尺度无关，最干净")
    print(f"  原始收盘收益率相关       : {m['corr_close_return']:+.3f}   （对照：未平滑的日频同步度）")
    print(f"  斜率方向一致率           : {m['slope_agreement']:.1%}   （同一天同时拐头向上的比例）")
    print(f"  {roll} 日滚动相关 均值/最低 : {e['roll_corr_mean']:+.3f} / {e['roll_corr_min']:+.3f}")
    print()

    print(f"【四】均线多空状态一致（MA{rep.ma_period} vs MA{e['slow_period']}）")
    print(f"  状态一致率               : {m['state_agreement']:.1%}")
    print(f"  Cohen's Kappa（扣随机）  : {m['kappa_state']:+.3f}")
    sa, sb = e["state_share_a"], e["state_share_b"]
    for label in ("多头", "空头", "纠缠"):
        print(f"    {label}占比   {rep.name_a}: {sa.get(label, 0):.1%} | {rep.name_b}: {sb.get(label, 0):.1%}")
    print()

    print(f"【五】金叉 / 死叉事件重合（容错 ±{cross_tol} 天）")
    print(f"  金叉  {rep.name_a}: {len(e['golden_a'])} 次 | {rep.name_b}: {len(e['golden_b'])} 次"
          f"   重合 Jaccard: {m['golden_jaccard']:.3f}")
    print(f"  死叉  {rep.name_a}: {len(e['death_a'])} 次 | {rep.name_b}: {len(e['death_b'])} 次"
          f"   重合 Jaccard: {m['death_jaccard']:.3f}")
    if e["golden_hit_dates"]:
        print(f"  共同金叉日: {', '.join(e['golden_hit_dates'][:6])}")
    if e["death_hit_dates"]:
        print(f"  共同死叉日: {', '.join(e['death_hit_dates'][:6])}")
    print()

    print("【六】背离诊断（归一化后差距最大的区段）")
    if e["divergences"]:
        for d in e["divergences"]:
            print(f"  {d['start']} ~ {d['end']}   平均偏离 {d['mean_gap']:.4f}"
                  f"  最大 {d['max_gap']:.4f}   {d['during']}")
    else:
        print("  无明显背离区段")
    print()

    print("【七】回归诊断（ln MA 回归）")
    print(f"  相对弹性 β              : {m['beta']:.3f}   （A 均线涨 1%，B 均线平均涨 {m['beta']:.2f}%）")
    print(f"  R²                      : {m['r_squared']:.3f}   （A 的均线能解释 B 变动的比例）")
    print(f"  残差典型偏离             : {m['resid_std_log']:.1%}   （剔除共同趋势后 B 相对 A 的常态偏离）")
    print(f"  残差最大偏离             : {m['resid_max_log']:.1%}   （区间内最极端的一次脱钩）")
    print()

    print("【八】区间表现对照（MA 口径）")
    print(f"  {rep.name_a} MA 起点/终点: {e['ma_a_start']:.2f} -> {e['ma_a_end']:.2f}   "
          f"({e['pct_a']:+.1%})")
    print(f"  {rep.name_b} MA 起点/终点: {e['ma_b_start']:.2f} -> {e['ma_b_end']:.2f}   "
          f"({e['pct_b']:+.1%})")
    print()
    print("注：所有形态指标均基于归一化序列，已剔除均线绝对价位的量级差异。")
    print("    本报告仅为均线形态与节奏的统计对比，不含买卖判断。")
    print(line)


def plot_ma(
    a: pd.DataFrame,
    b: pd.DataFrame,
    name_a: str,
    name_b: str,
    ma_period: int,
    slow_period: int,
    out_path: str,
    roll: int = 60,
    cross_tol: int = 2,
) -> str:
    """四联图：归一化 MA 曲线 / 标准分曲线 / 均线偏离度 / 滚动相关。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _setup_cjk_font()

    fk, sk = f"ma{ma_period}", f"ma{slow_period}"
    fast_a, fast_b = a[fk], b[fk]
    nz_a, nz_b = normalize_mas(fast_a), normalize_mas(fast_b)
    idx = nz_a["rebase"].index

    fig, axes = plt.subplots(4, 1, figsize=(13, 14), sharex=True,
                             gridspec_kw={"height_ratios": [1.2, 1.0, 1.0, 0.9]})
    ax1, ax2, ax3, ax4 = axes

    C_A, C_B = "#c0392b", "#2471a3"   # 沿用上一版：A 红、B 蓝

    # 共同金叉 / 死叉：底色竖带标注（避免与个股配色冲突）
    g_a, d_a = cross_events(fast_a, a[sk])
    g_b, d_b = cross_events(fast_b, b[sk])
    g_ov = event_overlap(g_a, g_b, tol=cross_tol)["matched"]
    d_ov = event_overlap(d_a, d_b, tol=cross_tol)["matched"]
    for i in g_ov:
        ax1.axvspan(idx[max(0, i - 2)], idx[min(len(idx) - 1, i + 2)],
                    color="#e8c547", alpha=0.20, lw=0)
    for i in d_ov:
        ax1.axvspan(idx[max(0, i - 2)], idx[min(len(idx) - 1, i + 2)],
                    color="#7f9c8c", alpha=0.20, lw=0)

    ax1.plot(idx, nz_a["rebase"], color=C_A, lw=1.6, label=f"{name_a} MA{ma_period}（首日=1）")
    ax1.plot(idx, nz_b["rebase"], color=C_B, lw=1.6, label=f"{name_b} MA{ma_period}（首日=1）")
    ax1.axhline(1.0, color="#7f8c8d", ls="--", lw=0.8)
    ax1.set_ylabel("归一化均线")
    ax1.set_title(f"{ma_period} 日均线相似度对比：{name_a} vs {name_b}", fontsize=13, pad=10)
    ax1.legend(loc="upper left", fontsize=9, ncol=2)
    ax1.grid(alpha=0.25)

    ax2.plot(idx, nz_a["zscore"], color=C_A, lw=1.3, label=f"{name_a} 标准分")
    ax2.plot(idx, nz_b["zscore"], color=C_B, lw=1.3, label=f"{name_b} 标准分")
    ax2.fill_between(idx, nz_a["zscore"], nz_b["zscore"],
                     where=nz_a["zscore"] > nz_b["zscore"], color=C_A, alpha=0.08)
    ax2.fill_between(idx, nz_a["zscore"], nz_b["zscore"],
                     where=nz_a["zscore"] <= nz_b["zscore"], color=C_B, alpha=0.08)
    ax2.axhline(0, color="#7f8c8d", ls="--", lw=0.8)
    ax2.set_ylabel("标准分")
    ax2.legend(loc="upper left", fontsize=9, ncol=2)
    ax2.grid(alpha=0.25)

    gap_a = (fast_a - a[sk]) / a[sk] * 100
    gap_b = (fast_b - b[sk]) / b[sk] * 100
    ax3.plot(gap_a.index, gap_a.values, color=C_A, lw=1.2, label=f"{name_a} MA{ma_period}-MA{slow_period} 偏离%")
    ax3.plot(gap_b.index, gap_b.values, color=C_B, lw=1.2, label=f"{name_b} MA{ma_period}-MA{slow_period} 偏离%")
    ax3.axhline(0, color="#7f8c8d", ls="--", lw=0.8)
    ax3.set_ylabel("均线偏离 %")
    ax3.legend(loc="upper left", fontsize=9, ncol=2)
    ax3.grid(alpha=0.25)

    rc = nz_a["zscore"].rolling(roll, min_periods=max(10, roll // 3)).corr(nz_b["zscore"])
    ax4.plot(rc.index, rc.values, color="#6c3483", lw=1.3)
    ax4.axhline(0, color="#7f8c8d", ls="--", lw=0.8)
    ax4.axhline(0.6, color="#27ae60", ls=":", lw=0.8)
    ax4.set_ylabel(f"{roll} 日滚动相关")
    ax4.set_xlabel("日期")
    ax4.grid(alpha=0.25)

    for ax in axes:
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    fig.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _setup_cjk_font() -> None:
    """matplotlib 中文字体回退。"""
    import matplotlib
    from matplotlib import font_manager

    for name in ("PingFang SC", "Heiti TC", "Songti SC", "Arial Unicode MS",
                 "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei"):
        if name in {f.name for f in font_manager.fontManager.ttflist}:
            matplotlib.rcParams["font.sans-serif"] = [name] + matplotlib.rcParams["font.sans-serif"]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def export_csv(a: pd.DataFrame, b: pd.DataFrame, ma_period: int, slow_period: int,
               out_path: str, name_a: str, name_b: str) -> str:
    """导出均线明细，方便接入 W23 / SSS 等既有模型。"""
    fk, sk = f"ma{ma_period}", f"ma{slow_period}"
    out = pd.DataFrame({
        f"{name_a}_close": a["close"],
        f"{name_a}_{fk}": a[fk].round(4),
        f"{name_a}_{sk}": a[sk].round(4),
        f"{name_a}_norm": (a[fk] / a[fk].iloc[0]).round(5),
        f"{name_a}_gap_pct": ((a[fk] - a[sk]) / a[sk] * 100).round(3),
        f"{name_b}_close": b["close"],
        f"{name_b}_{fk}": b[fk].round(4),
        f"{name_b}_{sk}": b[sk].round(4),
        f"{name_b}_norm": (b[fk] / b[fk].iloc[0]).round(5),
        f"{name_b}_gap_pct": ((b[fk] - b[sk]) / b[sk] * 100).round(3),
    })
    out.index.name = "date"
    out.to_csv(out_path, encoding="utf-8-sig")
    return out_path


# ===========================================================================
# 6. CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="计算两只股票在指定日期区间内的均线（默认 MA5）相似度",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python ma5_similarity.py 688213 603501 --start 2025-09-01 --end 2026-09-30",
    )
    p.add_argument("symbols", nargs="*", help="两只 A 股代码，如 688213 603501")
    p.add_argument("--csv", nargs=2, metavar=("A.csv", "B.csv"), help="改用本地 CSV")
    p.add_argument("--names", nargs=2, metavar=("名称A", "名称B"), help="自定义显示名称")
    p.add_argument("--start", required=False, help="开始日期 YYYY-MM-DD")
    p.add_argument("--end", required=False, help="结束日期 YYYY-MM-DD")
    p.add_argument("--ma", type=int, default=5, help="快线周期，默认 5（即 5 日均线）")
    p.add_argument("--slow", type=int, default=20, help="慢线周期，用于多空状态与金叉死叉判定，默认 20")
    p.add_argument("--roll", type=int, default=60, help="滚动相关窗口，默认 60")
    p.add_argument("--cross-tol", type=int, default=2, help="金叉死叉事件容错天数，默认 ±2")
    p.add_argument("--max-lag", type=int, default=10, help="领先滞后扫描上限（天），默认 10")
    p.add_argument("--adjust", default="qfq", choices=["qfq", "hfq", ""], help="复权方式，默认前复权")
    p.add_argument("--source", default="auto", choices=["auto", "akshare", "tencent"],
                   help="数据源：auto=先 akshare 再腾讯兜底（默认）")
    p.add_argument("--out-dir", default=".", help="输出目录")
    p.add_argument("--no-chart", action="store_true", help="不输出图表")
    p.add_argument("--export", action="store_true", help="导出均线明细 CSV")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    if pd.Timestamp(start) >= pd.Timestamp(end):
        print("错误：开始日期必须早于结束日期", file=sys.stderr)
        return 2

    # 均线预热：多取 WARMUP_DAYS 的行情，算完均线再截区间
    warm = (pd.Timestamp(start) - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")

    try:
        if args.csv:
            df_a, df_b = load_csv(args.csv[0]), load_csv(args.csv[1])
            default_names = [args.csv[0].split("/")[-1], args.csv[1].split("/")[-1]]
        else:
            if len(args.symbols) != 2:
                print("错误：需要恰好两个 A 股代码，例如：python ma5_similarity.py 688213 603501 --start 2025-09-01",
                      file=sys.stderr)
                return 2
            df_a = fetch_data(args.symbols[0], warm, end, args.adjust, args.source)
            df_b = fetch_data(args.symbols[1], warm, end, args.adjust, args.source)
            default_names = args.symbols
    except Exception as exc:  # noqa: BLE001
        print(f"数据获取失败：{exc}", file=sys.stderr)
        return 1

    names = args.names or default_names
    name_a, name_b = names[0], names[1]

    try:
        rep, a, b = compare(
            df_a, df_b, name_a, name_b, start, end,
            ma=args.ma, slow=args.slow, roll=args.roll,
            cross_tol=args.cross_tol, max_lag=args.max_lag,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"计算失败：{exc}", file=sys.stderr)
        return 1

    print_report(rep, roll=args.roll, cross_tol=args.cross_tol)

    stamp = datetime.now().strftime("%Y%m%d")
    base = f"{args.out_dir}/ma{args.ma}_similarity_{name_a}_{name_b}_{stamp}".replace(" ", "_")

    if not args.no_chart:
        try:
            print(f"图表已保存: {plot_ma(a, b, name_a, name_b, args.ma, rep.extra['slow_period'],
                                        base + '.png', roll=args.roll, cross_tol=args.cross_tol)}")
        except Exception as exc:  # noqa: BLE001
            print(f"绘图失败（不影响相似度结果）：{exc}", file=sys.stderr)

    if args.export:
        print(f"明细已导出: {export_csv(a, b, args.ma, rep.extra['slow_period'], base + '.csv',
                                    name_a, name_b)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
