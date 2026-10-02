#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
module_ma5_similarity_compare.py —— 两只股票「5 日均线（MA5）走势」相似度对比
=========================================================================================
输入模板股票代码、目标股票代码和起止日期，基于本地 stock_daily_t 表，读取两只股票
在选定区间内的收盘价计算 MA5，计算并返回目标股票 MA5 走势相对模板股票的相似度得分。

为什么不能直接对比两条 MA5
--------------------------
两只股票绝对价位天然不同（如 A 股 98 元、B 股 130 元），直接算相关/距离会被
「谁价格高」这个常数项主导。本模块把 MA5 转成三种归一化形态，各回答一个问题：
  rebase  相对路径 : ma5 / 区间首日 ma5        -> 这段区间谁的涨跌路径一样
  zscore  标准分   : (ln ma5 - 均值) / 标准差  -> 剔除量级与波动后的曲线形状
  return  MA5 收益 : diff(ln ma5)              -> 最纯粹的均线抬升/走平/下压节奏

相似度分两层度量后加权合成（0~1，越高越像）：
  DTW 形状相似度        25%  允许±15%窗口的轻微节拍错位（核心，基于标准分）
  归一化曲线相关        20%  整体路径是否同向同行（rebase 首日=1）
  MA5 收益率相关        20%  均线抬升/走平节奏是否同步（尺度无关，最干净）
  斜率方向一致率        15%  是否同一天同时拐头向上/向下
  多空状态 Kappa        20%  MA5 相对 MA20 的多空/纠缠排列是否长期一致（最稳健）

工程处理
--------
  1. 均线预热：取数起点按慢线窗口自动前移（slow*2+15 自然日），先算 MA 再截区间，
     保证区间首日 MA5/MA20 可用。
  2. 各算各的日历：先在各自原始交易日上算 MA，再按模板打分日历对齐，停牌日不污染均线。
  3. 剔除常数项：所有形态指标建立在归一化序列上，绝对价位差异不进入结论。
  4. MA5/MA20 均由 close 自行滚动计算，不依赖 DB 的 ma5/ma30 打标完整性。

数据源：本地 MySQL
  - stock_daily_t（ts_code, trade_date, close）
  - stock_info_t（stock_name，可空）

用法（CLI）
  python3 module_ma5_similarity_compare.py 688213.SH 603501.SH --start 20250901 --end 20260930
  python3 module_ma5_similarity_compare.py 688213.SH 603501.SH --start 2025-09-01 --end 2026-09-30 --ma 5 --slow 20

作为模块调用
  from module_ma5_similarity_compare import compare_ma5_similarity
  res = compare_ma5_similarity('688213.SH', '603501.SH', '20250901', '20260930')
  print(res['score'], res['grade'])
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from module_mysql_connection import get_mysql_connection, close_connection


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_FAST = 5            # 快线周期（5 日均线）
DEFAULT_SLOW = 20           # 慢线周期（多空状态 / 金叉死叉判定）
STATE_TOL = 0.005           # MA5 与 MA20 相差 0.5% 以内视为「纠缠」
MIN_OVERLAP_DAYS = 20       # 共同交易日绝对下限
MIN_OVERLAP_RATIO = 0.80    # 共同交易日 / 模板交易日 比例下限
DTW_BAND_RATIO = 0.15       # DTW Sakoe-Chiba 带：±15% 窗口长度
DTW_SIGMA = 0.5             # 每步失配 0.5 个标准差 -> 相似度 exp(-1)≈0.37
CROSS_TOL = 2               # 金叉/死叉事件容错天数

# 综合相似度合成权重（须合计为 1）
W_DTW, W_CORR_REBASE, W_CORR_RET, W_SLOPE, W_KAPPA = 0.25, 0.20, 0.20, 0.15, 0.20

# 相似度分档
SIM_GRADES: Tuple[Tuple[float, str], ...] = (
    (0.80, "高度相似 —— 均线形态与节奏几乎重合，可视为同一逻辑驱动"),
    (0.65, "较高相似 —— 走势大体同步，偶有阶段性背离"),
    (0.50, "中度相似 —— 有共同驱动，但节奏与幅度差异明显"),
    (0.00, "低相似 —— 均线形态基本各走各的，不宜按同一节奏操作"),
)


# ---------------------------------------------------------------------------
# 1. 数据读取（本地 MySQL，仅两只股票）
# ---------------------------------------------------------------------------

def _norm_date(d: str) -> str:
    """统一为 YYYYMMDD。"""
    return str(d).replace("-", "").strip()


def _seed_start(start: str, slow: int) -> str:
    """慢线 MA(slow) 需要 start 之前 slow 个交易日，多取自然日冗余。"""
    return (datetime.strptime(start, "%Y%m%d")
            - timedelta(days=slow * 2 + 15)).strftime("%Y%m%d")


def load_two_stocks(code_a: str, code_b: str, start: str, end: str,
                    slow: int = DEFAULT_SLOW
                    ) -> Tuple[List[str], Dict[str, pd.DataFrame], Dict[str, str]]:
    """读取模板/目标两只股票在 [start,end]（含前置预热）内的收盘价。

    返回 (template_dates, grouped, name_map)：
      template_dates —— 模板股票在 [start,end] 内有行情的交易日（升序，打分日历）
      grouped        —— {ts_code: DataFrame(trade_date, close)}（含预热段）
      name_map       —— {ts_code: stock_name}
    """
    seed = _seed_start(start, slow)
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT trade_date FROM stock_daily_t
                WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (code_a, start, end))
            template_dates = [r["trade_date"] for r in cur.fetchall()]
            if not template_dates:
                raise RuntimeError(f"模板股票 {code_a} 在 {start}~{end} 内无行情数据")

            cur.execute("""
                SELECT d.ts_code, d.trade_date, d.close, i.stock_name
                FROM stock_daily_t d
                LEFT JOIN stock_info_t i
                  ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
                WHERE d.trade_date >= %s AND d.trade_date <= %s
                  AND d.ts_code IN (%s, %s)
                ORDER BY d.ts_code, d.trade_date
            """, (seed, end, code_a, code_b))
            rows = cur.fetchall()
    finally:
        close_connection(conn)

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"stock_daily_t 在 {seed}~{end} 内无数据")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    name_map = (df.dropna(subset=["stock_name"])
                  .drop_duplicates("ts_code")
                  .set_index("ts_code")["stock_name"].to_dict())
    grouped = {code: g[["trade_date", "close"]].reset_index(drop=True)
               for code, g in df.groupby("ts_code", sort=False)}
    return template_dates, grouped, name_map


# ---------------------------------------------------------------------------
# 2. 均线计算与归一化（各算各的日历，停牌不污染）
# ---------------------------------------------------------------------------

def compute_mas(g: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """在股票自身原始交易日上由 close 滚动计算快线/慢线均线。"""
    out = g.copy()
    out[f"ma{fast}"] = out["close"].rolling(fast, min_periods=fast).mean()
    out[f"ma{slow}"] = out["close"].rolling(slow, min_periods=slow).mean()
    return out


def normalize_ma(ma: pd.Series) -> Dict[str, pd.Series]:
    """一条均线 -> 三种可比归一化形态：rebase / zscore / return。"""
    s = ma.astype(float)
    rebase = s / s.iloc[0]
    ls = np.log(s)
    z = (ls - ls.mean()) / ls.std(ddof=0) if ls.std(ddof=0) > 0 else ls * 0.0
    ret = ls.diff()
    return {"rebase": rebase, "zscore": z, "return": ret}


# ---------------------------------------------------------------------------
# 3. 相似度度量
# ---------------------------------------------------------------------------

def safe_corr(a: np.ndarray, b: np.ndarray, min_n: int = 5) -> float:
    """带缺失掩码的 Pearson 相关；常数列或样本不足返回 NaN。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    msk = np.isfinite(a) & np.isfinite(b)
    if msk.sum() < min_n or a[msk].std() == 0 or b[msk].std() == 0:
        return float("nan")
    return float(np.corrcoef(a[msk], b[msk])[0, 1])


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman 秩相关（纯 numpy，抗极端段）。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    msk = np.isfinite(a) & np.isfinite(b)
    a, b = a[msk], b[msk]
    if len(a) < 3:
        return float("nan")
    ra, rb = pd.Series(a).rank().values, pd.Series(b).rank().values
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def dtw_path_stats(a: np.ndarray, b: np.ndarray,
                   band: Optional[int] = None) -> Tuple[float, int]:
    """带 Sakoe-Chiba 约束的 DTW，返回 (累计距离, 最优路径步数)。

    除以步数得到「平均每步失配」，使不同长度区间可比。输入约定为各自标准分
    序列（std=1），每步失配可直接按标准差倍数解读。
    """
    a, b = np.asarray(a, float), np.asarray(b, float)
    msk = np.isfinite(a) & np.isfinite(b)
    a, b = a[msk], b[msk]
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("nan"), 0
    w = band if band is not None else max(5, int(DTW_BAND_RATIO * max(n, m)))
    w = max(w, abs(n - m) + 1)

    INF = float("inf")
    D = np.full((n + 1, m + 1), INF)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        lo, hi = max(1, i - w), min(m, i + w)
        ai = a[i - 1]
        for j in range(lo, hi + 1):
            D[i, j] = abs(ai - b[j - 1]) + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])

    i, j, steps = n, m, 0
    while i > 0 or j > 0:
        steps += 1
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            _, i, j = min(((D[i - 1, j - 1], i - 1, j - 1),
                           (D[i - 1, j], i - 1, j),
                           (D[i, j - 1], i, j - 1)),
                          key=lambda t: t[0])
    return float(D[n, m]), steps


def dtw_similarity(a: np.ndarray, b: np.ndarray,
                   band: Optional[int] = None) -> Tuple[float, float]:
    """DTW -> 0~1 相似度（exp(-每步失配/sigma)），返回 (相似度, 每步失配)。"""
    d, steps = dtw_path_stats(a, b, band)
    if not np.isfinite(d) or steps == 0:
        return float("nan"), float("nan")
    per_step = d / steps
    return float(np.exp(-per_step / DTW_SIGMA)), float(per_step)


def slope_agreement(ma_a: pd.Series, ma_b: pd.Series) -> float:
    """斜率方向一致率：同一天同时拐头向上/向下（走平日剔除）的比例。"""
    sa, sb = np.sign(ma_a.diff().values), np.sign(ma_b.diff().values)
    msk = np.isfinite(sa) & np.isfinite(sb) & (sa != 0) & (sb != 0)
    if msk.sum() == 0:
        return float("nan")
    return float((sa[msk] == sb[msk]).mean())


def ma_state(ma_fast: pd.Series, ma_slow: pd.Series,
             tol: float = STATE_TOL) -> np.ndarray:
    """多空状态：多头（快线在上>tol）/ 空头（在下<-tol）/ 纠缠（容差内）。"""
    gap = (ma_fast - ma_slow) / ma_slow.replace(0, np.nan)
    gv = gap.values
    return np.where(gv > tol, "多头", np.where(gv < -tol, "空头", "纠缠"))


def categorical_agreement(a: Sequence[str], b: Sequence[str]) -> Tuple[float, float]:
    """分类一致率 + Cohen's Kappa（剔除随机巧合）。"""
    a, b = np.asarray(a), np.asarray(b)
    po = float((a == b).mean()) if len(a) else float("nan")
    pe = sum((a == lb).mean() * (b == lb).mean()
             for lb in set(a.tolist()) | set(b.tolist()))
    kappa = float((po - pe) / (1 - pe)) if pe < 1 else float("nan")
    return po, kappa


def cross_events(ma_fast: np.ndarray, ma_slow: np.ndarray) -> Tuple[List[int], List[int]]:
    """金叉（快线上穿慢线）/ 死叉（快线下穿慢线）位置序号。"""
    diff = ma_fast - ma_slow
    prev = np.roll(diff, 1)
    prev[0] = diff[0]
    golden = np.where((prev <= 0) & (diff > 0) & np.isfinite(diff) & np.isfinite(prev))[0].tolist()
    death = np.where((prev >= 0) & (diff < 0) & np.isfinite(diff) & np.isfinite(prev))[0].tolist()
    return golden, death


def event_jaccard(ea: Sequence[int], eb: Sequence[int], tol: int = CROSS_TOL) -> float:
    """事件重合 Jaccard：允许 ±tol 天错位；任一序列无事件返回 NaN。"""
    if not ea or not eb:
        return float("nan")
    set_b = set(eb)
    matched = sum(1 for i in ea if any((i + d) in set_b for d in range(-tol, tol + 1)))
    union = len(set(ea) | set_b)
    return matched / union if union else float("nan")


def _clip(v: Optional[float]) -> Optional[float]:
    """相关/Kappa 裁剪到 [-1,1]；负值保留（代表反向走势，拉低得分）。"""
    if v is None or not np.isfinite(v):
        return None
    return float(min(1.0, max(-1.0, v)))


def composite_score(m: Dict[str, float]) -> float:
    """加权合成 0~1 综合相似度；仅对有效项按权重归一。"""
    parts = [
        (m.get("dtw_similarity"), W_DTW),
        (_clip(m.get("corr_rebase")), W_CORR_REBASE),
        (_clip(m.get("corr_ma_return")), W_CORR_RET),
        (_clip(m.get("slope_agreement")), W_SLOPE),
        (_clip(m.get("kappa_state")), W_KAPPA),
    ]
    num = sum(v * w for v, w in parts if v is not None)
    den = sum(w for v, w in parts if v is not None)
    return float(num / den) if den else float("nan")


def grade(score: float) -> str:
    if not np.isfinite(score):
        return "无法判定"
    for thr, text in SIM_GRADES:
        if score >= thr:
            return text
    return SIM_GRADES[-1][1]


def _round(v, nd: int = 4):
    if v is None or not np.isfinite(v):
        return None
    return round(float(v), nd)


# ---------------------------------------------------------------------------
# 4. 主入口（模块 API）：模板股 × 目标股 MA5 走势对比
# ---------------------------------------------------------------------------

def compare_ma5_similarity(template_code: str,
                           target_code: str,
                           start_date: str,
                           end_date: str,
                           ma: int = DEFAULT_FAST,
                           slow: int = DEFAULT_SLOW) -> Dict[str, object]:
    """计算目标股票 MA5 走势相对模板股票在 [start_date, end_date] 的相似度。

    参数：
      template_code : 模板股票 ts_code，如 '688213.SH'
      target_code   : 目标股票 ts_code，如 '603501.SH'
      start_date/end_date : YYYYMMDD 或 YYYY-MM-DD（含两端，按实际交易日对齐）
      ma            : 快线周期，默认 5
      slow          : 慢线周期（多空状态/金叉死叉），默认 20
    返回 dict：
      template/target（ts_code、stock_name）、date_range、n_days、overlap_days、
      overlap_ratio、score（0~1 综合相似度）、grade（判定），
      以及 corr_rebase / corr_zscore / spearman_rebase / corr_ma_return /
      dtw_similarity / dtw_per_step / rmse_rebase / max_gap_rebase /
      slope_agreement / state_agreement / kappa_state /
      golden_jaccard / death_jaccard / ma_period / slow_period
    """
    start, end = _norm_date(start_date), _norm_date(end_date)
    if start > end:
        raise ValueError(f"开始日期 {start} 晚于结束日期 {end}")
    template_code = template_code.upper().strip()
    target_code = target_code.upper().strip()

    template_dates, grouped, name_map = load_two_stocks(
        template_code, target_code, start, end, slow)
    if template_code not in grouped:
        raise RuntimeError(f"模板股票 {template_code} 不在 stock_daily_t 中")
    if target_code not in grouped:
        raise RuntimeError(f"目标股票 {target_code} 在 {start}~{end} 内无行情数据")

    # 各自在原始交易日上算 MA（预热段一起滚动），再按模板打分日历对齐
    a = compute_mas(grouped[template_code], ma, slow) \
        .set_index("trade_date").reindex(template_dates)
    b = compute_mas(grouped[target_code], ma, slow) \
        .set_index("trade_date").reindex(template_dates)

    fk, sk = f"ma{ma}", f"ma{slow}"
    fast_a, fast_b = a[fk], b[fk]
    slow_a, slow_b = a[sk], b[sk]

    # 共同有效日（两条快线均线均可计算）
    mask = np.isfinite(fast_a.values) & np.isfinite(fast_b.values)
    overlap = int(mask.sum())
    n_tpl = len(template_dates)
    if overlap < MIN_OVERLAP_DAYS or overlap / n_tpl < MIN_OVERLAP_RATIO:
        raise RuntimeError(
            f"共同有效交易日仅 {overlap}/{n_tpl} 天"
            f"（下限 {MIN_OVERLAP_DAYS} 天或 {MIN_OVERLAP_RATIO:.0%}），"
            f"样本不足无法对比（停牌/次新股/收盘价缺失）")

    nz_a, nz_b = normalize_ma(fast_a), normalize_ma(fast_b)
    band = max(3, int(DTW_BAND_RATIO * overlap))

    m: Dict[str, float] = {}
    # —— 曲线形态同步性 ——
    m["corr_rebase"] = safe_corr(nz_a["rebase"].values, nz_b["rebase"].values)
    m["corr_zscore"] = safe_corr(nz_a["zscore"].values, nz_b["zscore"].values)
    m["spearman_rebase"] = spearman(nz_a["rebase"].values, nz_b["rebase"].values)
    dtw_sim, per_step = dtw_similarity(nz_a["zscore"].values, nz_b["zscore"].values, band)
    m["dtw_similarity"] = dtw_sim
    m["dtw_per_step"] = per_step
    diff_rebase = (nz_a["rebase"] - nz_b["rebase"]).values
    m["rmse_rebase"] = float(np.sqrt(np.nanmean(diff_rebase ** 2)))
    m["max_gap_rebase"] = float(np.nanmax(np.abs(diff_rebase)))

    # —— 趋势节奏同步性 ——
    m["corr_ma_return"] = safe_corr(nz_a["return"].values, nz_b["return"].values)
    m["slope_agreement"] = slope_agreement(fast_a, fast_b)

    # —— 均线多空状态（MA快 vs MA慢）——
    st_a = ma_state(fast_a, slow_a)
    st_b = ma_state(fast_b, slow_b)
    po, kappa = categorical_agreement(st_a[mask], st_b[mask])
    m["state_agreement"] = po
    m["kappa_state"] = kappa

    # —— 金叉/死叉事件重合（在共同有效段上）——
    ga, da = cross_events(fast_a.values[mask], slow_a.values[mask])
    gb, db = cross_events(fast_b.values[mask], slow_b.values[mask])
    m["golden_jaccard"] = event_jaccard(ga, gb)
    m["death_jaccard"] = event_jaccard(da, db)

    score = composite_score(m)

    return {
        "template": {"ts_code": template_code,
                     "stock_name": name_map.get(template_code, "")},
        "target": {"ts_code": target_code,
                   "stock_name": name_map.get(target_code, "")},
        "date_range": (template_dates[0], template_dates[-1]),
        "n_days": n_tpl,
        "overlap_days": overlap,
        "overlap_ratio": round(overlap / n_tpl, 3),
        "score": _round(score),
        "grade": grade(score),
        "ma_period": ma,
        "slow_period": slow,
        "corr_rebase": _round(m.get("corr_rebase")),
        "corr_zscore": _round(m.get("corr_zscore")),
        "spearman_rebase": _round(m.get("spearman_rebase")),
        "corr_ma_return": _round(m.get("corr_ma_return")),
        "dtw_similarity": _round(m.get("dtw_similarity")),
        "dtw_per_step": _round(m.get("dtw_per_step")),
        "rmse_rebase": _round(m.get("rmse_rebase")),
        "max_gap_rebase": _round(m.get("max_gap_rebase")),
        "slope_agreement": _round(m.get("slope_agreement")),
        "state_agreement": _round(m.get("state_agreement")),
        "kappa_state": _round(m.get("kappa_state")),
        "golden_jaccard": _round(m.get("golden_jaccard")),
        "death_jaccard": _round(m.get("death_jaccard")),
    }


# ---------------------------------------------------------------------------
# 5. 控制台报告
# ---------------------------------------------------------------------------

def print_report(res: Dict[str, object]) -> None:
    tpl, tgt = res["template"], res["target"]
    d0, d1 = res["date_range"]
    line = "=" * 72
    print(line)
    print(f"MA{res['ma_period']} 日均线走势相似度报告   "
          f"{tpl['ts_code']}（{tpl['stock_name']}）  vs  "
          f"{tgt['ts_code']}（{tgt['stock_name']}）")
    print(line)
    print(f"样本区间: {d0} ~ {d1}   模板交易日: {res['n_days']} 天   "
          f"共同有效: {res['overlap_days']} 天（{res['overlap_ratio']:.0%}）")
    print()
    print("【核心结论】")
    print(f"  综合均线相似度 : {res['score']:.3f} / 1.000")
    print(f"  判定           : {res['grade']}")
    print()
    print("【曲线形态同步性】（归一化后的 MA 曲线像不像）")
    print(f"  归一化曲线相关(首日=1)  : {_pf(res['corr_rebase']):+.3f}")
    print(f"  标准分曲线相关          : {_pf(res['corr_zscore']):+.3f}")
    print(f"  归一化 Spearman         : {_pf(res['spearman_rebase']):+.3f}   （抗极端段）")
    print(f"  DTW 相似度/每步失配     : {_pf(res['dtw_similarity']):.3f} / "
          f"{_pf(res['dtw_per_step']):.3f}   （失配单位：标准差倍数，允许±15%错位）")
    print(f"  归一化 RMSE/最大偏离    : {_pf(res['rmse_rebase']):.4f} / "
          f"{_pf(res['max_gap_rebase']):.4f}   （0.10=累计偏离10%）")
    print()
    print("【趋势节奏同步性】")
    print(f"  MA{res['ma_period']} 收益率相关        : {_pf(res['corr_ma_return']):+.3f}   ← 尺度无关，最干净")
    print(f"  斜率方向一致率          : {_pct(res['slope_agreement'])}   （同一天同时拐头比例）")
    print()
    print(f"【均线多空状态一致】（MA{res['ma_period']} vs MA{res['slow_period']}）")
    print(f"  状态一致率              : {_pct(res['state_agreement'])}")
    print(f"  Cohen's Kappa（扣随机） : {_pf(res['kappa_state']):+.3f}")
    print()
    print(f"【金叉/死叉事件重合】（容错 ±{CROSS_TOL} 天，Jaccard）")
    print(f"  金叉重合: {_pf(res['golden_jaccard']):.3f}   死叉重合: {_pf(res['death_jaccard']):.3f}")
    print()
    print(f"注：所有形态指标均基于归一化序列，已剔除均线绝对价位的量级差异；"
          f"MA 由 close 自行滚动计算。")
    print(line)


def _pf(v) -> float:
    return float(v) if isinstance(v, (int, float)) else float("nan")


def _pct(v) -> str:
    return f"{float(v):.1%}" if isinstance(v, (int, float)) else "N/A"


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="两只股票 5 日均线（MA5）走势相似度对比")
    ap.add_argument("template", help="模板股票代码，如 688213.SH")
    ap.add_argument("target", help="目标股票代码，如 603501.SH")
    ap.add_argument("--start", required=True, help="开始日期 YYYYMMDD（含）")
    ap.add_argument("--end", required=True, help="结束日期 YYYYMMDD（含）")
    ap.add_argument("--ma", type=int, default=DEFAULT_FAST,
                    help=f"快线周期，默认 {DEFAULT_FAST}（5日均线）")
    ap.add_argument("--slow", type=int, default=DEFAULT_SLOW,
                    help=f"慢线周期（多空状态/金叉死叉），默认 {DEFAULT_SLOW}")
    args = ap.parse_args(argv)

    try:
        res = compare_ma5_similarity(
            args.template, args.target, args.start, args.end,
            ma=args.ma, slow=args.slow)
    except Exception as exc:
        print(f"❌ 计算失败：{exc}", file=sys.stderr)
        return 1

    print_report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
