#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
module_volume_rhythm_compare.py —— 两只股票「成交量节奏（放量/平量/缩量）」相似度对比
=========================================================================================
输入模板股票代码、目标股票代码和起止日期，基于本地 stock_daily_t 表，读取两只股票
在选定区间内的成交量，计算并返回目标股票相对模板股票的成交量节奏相似度得分。

方法论（纯量维度，不含价格）
----------------------------
不同股票成交量绝对量级差异极大，先把原始成交量转成量能节奏序列：
  量比 VR_t     = 当日成交量 / 过去 N 日均量        （剔除量级，保留放缩倍率）
  量能 Z 分数   = (ln V_t - MA(ln V)) / STD(ln V)   （对数化+标准化，抗偏态）
  状态 label   = 巨量 / 放量 / 平量 / 缩量 / 地量（按 VR 阈值离散化）

相似度分两层度量后加权合成（0~1，越高越像）：
  DTW 形状相似度      30%  允许±15%窗口的轻微节拍错位（核心）
  量能 Z 分数相关     20%  幅度同步
  量比 VR 相关        15%  放缩倍率同步
  方向一致率          15%  同日同时转放量/转缩量的比例（拍点对齐）
  状态一致 Kappa      20%  离散状态一致率扣随机巧合（Cohen's Kappa）

取数说明
--------
  - 量比/Z 分需要滚动均量种子：实际取数从 start 再向前取约 2*MA+15 个自然日，
    但打分仅使用 start~end 区间内、两只股票【共同有行情】的交易日。
  - 共同交易日不足（默认 <80% 模板交易日或 <20 天）直接报错（停牌/次新股）。

数据源：本地 MySQL
  - stock_daily_t（ts_code, trade_date, vol）
  - stock_info_t（stock_name，可空）

用法（CLI）
  python3 module_volume_rhythm_compare.py 688213.SH 603501.SH --start 20250901 --end 20260831
  python3 module_volume_rhythm_compare.py 688213.SH 603501.SH --start 2025-09-01 --end 2026-08-31 --ma 20

作为模块调用
  from module_volume_rhythm_compare import compare_volume_rhythm
  res = compare_volume_rhythm('688213.SH', '603501.SH', '20250901', '20260831')
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
DEFAULT_MA = 20             # 量比/对数Z分的滚动均量窗口（交易日）
MIN_OVERLAP_DAYS = 20       # 共同交易日绝对下限
MIN_OVERLAP_RATIO = 0.80    # 共同交易日 / 模板交易日 比例下限
DTW_BAND_RATIO = 0.15       # DTW Sakoe-Chiba 带：±15% 窗口长度

# 放量/缩量状态阈值（按量比 VR）
STATE_THRESHOLDS: Tuple[Tuple[str, float], ...] = (
    ("巨量", 2.00),
    ("放量", 1.30),
    ("平量", 0.75),
    ("缩量", 0.55),
)
STATE_LABELS: Tuple[str, ...] = ("巨量", "放量", "平量", "缩量", "地量")

# 综合相似度合成权重（须合计为 1）
W_DTW, W_CORR_Z, W_CORR_VR, W_DIR, W_KAPPA = 0.30, 0.20, 0.15, 0.15, 0.20

# 相似度分档
SIM_GRADES: Tuple[Tuple[float, str], ...] = (
    (0.80, "高度同步 —— 量能节奏几乎同步，适合做同一逻辑下的组合/轮动"),
    (0.65, "较高同步 —— 节奏大体一致，存在偶发错位"),
    (0.50, "中度同步 —— 有共同驱动因素，但各自独立事件较多"),
    (0.00, "弱同步 —— 量能节奏基本各走各的，不宜按同一节奏交易"),
)


# ---------------------------------------------------------------------------
# 1. 数据读取（本地 MySQL，仅两只股票）
# ---------------------------------------------------------------------------

def _norm_date(d: str) -> str:
    """统一为 YYYYMMDD。"""
    return str(d).replace("-", "").strip()


def _seed_start(start: str, ma: int) -> str:
    """量比/Z分需要 start 之前约 ma 个交易日的滚动均量种子，多取自然日冗余。"""
    return (datetime.strptime(start, "%Y%m%d")
            - timedelta(days=ma * 2 + 15)).strftime("%Y%m%d")


def load_two_stocks(code_a: str, code_b: str, start: str, end: str,
                    ma: int = DEFAULT_MA
                    ) -> Tuple[List[str], Dict[str, pd.DataFrame], Dict[str, str]]:
    """读取模板/目标两只股票在 [start,end]（含前置种子）内的成交量。

    返回 (template_dates, grouped, name_map)：
      template_dates —— 模板股票在 [start,end] 内有行情的交易日（升序，打分日历）
      grouped        —— {ts_code: DataFrame(trade_date, vol)}（含种子段）
      name_map       —— {ts_code: stock_name}
    """
    seed = _seed_start(start, ma)
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cur:
            # 模板打分日历
            cur.execute("""
                SELECT DISTINCT trade_date FROM stock_daily_t
                WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (code_a, start, end))
            template_dates = [r["trade_date"] for r in cur.fetchall()]
            if not template_dates:
                raise RuntimeError(f"模板股票 {code_a} 在 {start}~{end} 内无行情数据")

            # 两只股票成交量（含种子段）
            cur.execute("""
                SELECT d.ts_code, d.trade_date, d.vol, i.stock_name
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
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")

    name_map = (df.dropna(subset=["stock_name"])
                  .drop_duplicates("ts_code")
                  .set_index("ts_code")["stock_name"].to_dict())
    grouped = {code: g[["trade_date", "vol"]].reset_index(drop=True)
               for code, g in df.groupby("ts_code", sort=False)}
    return template_dates, grouped, name_map


# ---------------------------------------------------------------------------
# 2. 量能节奏特征构造
# ---------------------------------------------------------------------------

def classify_state(vr: float) -> str:
    """量比 -> 离散状态标签。"""
    if not np.isfinite(vr):
        return "未知"
    for label, thr in STATE_THRESHOLDS:
        if vr >= thr:
            return label
    return STATE_LABELS[-1]


def build_rhythm(vol: pd.Series, dates: pd.Series, ma: int = DEFAULT_MA) -> pd.DataFrame:
    """单只股票成交量 -> 量能节奏特征（含种子段一起滚动，保证区间首日指标可用）。

    输出列：trade_date / volume / vr（量比）/ z（对数Z分）/ state（状态）
    """
    out = pd.DataFrame({"trade_date": dates.values,
                        "volume": vol.astype(float).replace(0, np.nan).values})
    v = out["volume"]
    minp = max(3, ma // 3)
    base = v.rolling(ma, min_periods=minp).mean()
    out["vr"] = v / base
    lv = np.log(v)
    mu = lv.rolling(ma, min_periods=minp).mean()
    sd = lv.rolling(ma, min_periods=minp).std(ddof=0).replace(0, np.nan)
    out["z"] = (lv - mu) / sd
    out["state"] = [classify_state(x) for x in out["vr"]]
    return out


# ---------------------------------------------------------------------------
# 3. 相似度度量
# ---------------------------------------------------------------------------

def dtw_distance(a: np.ndarray, b: np.ndarray,
                 band: Optional[int] = None) -> float:
    """带 Sakoe-Chiba 约束的 DTW 距离（绝对误差累积），允许节拍轻微错位。"""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("nan")
    w = band if band is not None else max(5, int(DTW_BAND_RATIO * max(n, m)))
    w = max(w, abs(n - m) + 1)

    INF = float("inf")
    prev = np.full(m + 1, INF)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = np.full(m + 1, INF)
        lo, hi, ai = max(1, i - w), min(m, i + w), a[i - 1]
        for j in range(lo, hi + 1):
            cost = abs(ai - b[j - 1])
            cur[j] = cost + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    return float(prev[m])


def dtw_similarity(a: np.ndarray, b: np.ndarray,
                   band: Optional[int] = None) -> float:
    """DTW 距离归一化为 0~1 相似度（1=完全相同），尺度用两序列平均波动校准。"""
    d = dtw_distance(a, b, band)
    if not np.isfinite(d):
        return float("nan")
    scale = float(np.nanmean([
        np.nanmean(np.abs(np.diff(a))) if len(a) > 1 else 0.0,
        np.nanmean(np.abs(np.diff(b))) if len(b) > 1 else 0.0]))
    scale = max(scale, 1e-6)
    return float(np.exp(-d / max(len(a), len(b)) / scale))


def direction_agreement(a: np.ndarray, b: np.ndarray) -> float:
    """方向一致率：同日同时转放量/转缩量（Z分一阶差分同号）的比例。"""
    da, db = np.sign(np.diff(a)), np.sign(np.diff(b))
    mask = np.isfinite(da) & np.isfinite(db) & (da != 0) & (db != 0)
    if mask.sum() == 0:
        return float("nan")
    return float((da[mask] == db[mask]).mean())


def state_kappa(states_a: Sequence[str], states_b: Sequence[str]) -> Tuple[float, float]:
    """离散状态一致率 与 Cohen's Kappa（剔除随机巧合）。"""
    a, b = np.asarray(states_a), np.asarray(states_b)
    mask = (a != "未知") & (b != "未知")
    a, b = a[mask], b[mask]
    if len(a) == 0:
        return float("nan"), float("nan")
    po = float((a == b).mean())
    pe = 0.0
    for lb in set(a.tolist()) | set(b.tolist()):
        pe += (a == lb).mean() * (b == lb).mean()
    kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    return po, float(kappa)


def _clip(v: Optional[float]) -> Optional[float]:
    """相关/Kappa 裁剪到 [-1,1]；负相关保留（代表反向节奏，拉低得分）。"""
    if v is None or not np.isfinite(v):
        return None
    return float(min(1.0, max(-1.0, v)))


def composite_score(m: Dict[str, float]) -> float:
    """加权合成 0~1 综合相似度；仅对有效项按权重归一。"""
    parts = [
        (m.get("dtw_similarity"), W_DTW),
        (_clip(m.get("corr_z")), W_CORR_Z),
        (_clip(m.get("corr_vr")), W_CORR_VR),
        (_clip(m.get("direction_agreement")), W_DIR),
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
# 4. 主入口（模块 API）：模板股 × 目标股 成对比较
# ---------------------------------------------------------------------------

def compare_volume_rhythm(template_code: str,
                          target_code: str,
                          start_date: str,
                          end_date: str,
                          ma: int = DEFAULT_MA) -> Dict[str, object]:
    """计算目标股票相对模板股票在 [start_date, end_date] 的成交量节奏相似度。

    参数：
      template_code : 模板股票 ts_code，如 '688213.SH'
      target_code   : 目标股票 ts_code，如 '603501.SH'
      start_date/end_date : YYYYMMDD 或 YYYY-MM-DD（含两端，按实际交易日对齐）
      ma            : 量比/对数Z分滚动窗口，默认 20
    返回 dict：
      template/target（ts_code、stock_name）、date_range、n_days（模板交易日数）、
      overlap_days、overlap_ratio、score（0~1 综合相似度）、grade（判定）、
      以及 dtw_similarity / corr_z / corr_vr / direction_agreement /
      state_agreement / kappa_state 各分项指标
    """
    start, end = _norm_date(start_date), _norm_date(end_date)
    if start > end:
        raise ValueError(f"开始日期 {start} 晚于结束日期 {end}")
    template_code = template_code.upper().strip()
    target_code = target_code.upper().strip()

    template_dates, grouped, name_map = load_two_stocks(
        template_code, target_code, start, end, ma)
    if template_code not in grouped:
        raise RuntimeError(f"模板股票 {template_code} 不在 stock_daily_t 中")
    if target_code not in grouped:
        raise RuntimeError(f"目标股票 {target_code} 在 {start}~{end} 内无行情数据")

    # 两只股票各自构造节奏（含种子滚动），再按模板打分日历对齐
    ra = build_rhythm(grouped[template_code]["vol"],
                      grouped[template_code]["trade_date"], ma) \
        .set_index("trade_date").reindex(template_dates)
    rb = build_rhythm(grouped[target_code]["vol"],
                      grouped[target_code]["trade_date"], ma) \
        .set_index("trade_date").reindex(template_dates)

    z_a, z_b = ra["z"].values, rb["z"].values
    vr_a_all, vr_b_all = ra["vr"].values, rb["vr"].values
    st_a_all, st_b_all = ra["state"].values, rb["state"].values

    # 共同有效日（两者 Z 分均可计算）
    mask = np.isfinite(z_a) & np.isfinite(z_b)
    overlap = int(mask.sum())
    n_tpl = len(template_dates)
    if overlap < MIN_OVERLAP_DAYS or overlap / n_tpl < MIN_OVERLAP_RATIO:
        raise RuntimeError(
            f"共同有效交易日仅 {overlap}/{n_tpl} 天"
            f"（下限 {MIN_OVERLAP_DAYS} 天或 {MIN_OVERLAP_RATIO:.0%}），"
            f"样本不足无法对比（停牌/次新股/成交量缺失）")

    za, zb = z_a[mask], z_b[mask]
    # VR 相关单独做有效掩码（基准均量可能缺失）
    vmask = mask & np.isfinite(vr_a_all) & np.isfinite(vr_b_all)
    vra, vrb = vr_a_all[vmask], vr_b_all[vmask]

    m: Dict[str, float] = {}
    m["corr_z"] = (float(np.corrcoef(za, zb)[0, 1])
                   if len(za) > 2 and np.std(za) > 0 and np.std(zb) > 0
                   else float("nan"))
    m["corr_vr"] = (float(np.corrcoef(vra, vrb)[0, 1])
                    if len(vra) > 2 and np.std(vra) > 0 and np.std(vrb) > 0
                    else float("nan"))
    band = max(3, int(DTW_BAND_RATIO * len(za)))
    m["dtw_similarity"] = dtw_similarity(za, zb, band=band)
    m["direction_agreement"] = direction_agreement(za, zb)
    po, kappa = state_kappa(st_a_all[mask], st_b_all[mask])
    m["state_agreement"] = po
    m["kappa_state"] = kappa

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
        "dtw_similarity": _round(m.get("dtw_similarity")),
        "corr_z": _round(m.get("corr_z")),
        "corr_vr": _round(m.get("corr_vr")),
        "direction_agreement": _round(m.get("direction_agreement")),
        "state_agreement": _round(m.get("state_agreement")),
        "kappa_state": _round(m.get("kappa_state")),
    }


# ---------------------------------------------------------------------------
# 5. 控制台报告
# ---------------------------------------------------------------------------

def print_report(res: Dict[str, object]) -> None:
    tpl, tgt = res["template"], res["target"]
    d0, d1 = res["date_range"]
    line = "=" * 72
    print(line)
    print(f"成交量节奏相似度报告   {tpl['ts_code']}（{tpl['stock_name']}）  vs  "
          f"{tgt['ts_code']}（{tgt['stock_name']}）")
    print(line)
    print(f"样本区间: {d0} ~ {d1}   模板交易日: {res['n_days']} 天   "
          f"共同有效: {res['overlap_days']} 天（{res['overlap_ratio']:.0%}）")
    print()
    print("【核心结论】")
    print(f"  综合节奏相似度 : {res['score']:.3f} / 1.000")
    print(f"  判定           : {res['grade']}")
    print()
    print("【幅度同步性】（是否同时放量 / 同时缩量）")
    print(f"  量能 Z 分数相关系数   : {_pf(res['corr_z']):+.3f}")
    print(f"  量比 VR 相关系数      : {_pf(res['corr_vr']):+.3f}")
    print()
    print("【节拍同步性】（节奏形状是否一致）")
    print(f"  DTW 归一化相似度      : {_pf(res['dtw_similarity']):.3f}   （1=完全相同，允许±15%错位）")
    print(f"  方向一致率            : {_pct(res['direction_agreement'])}")
    print(f"  离散状态一致率        : {_pct(res['state_agreement'])}")
    print(f"  Cohen's Kappa（扣随机）: {_pf(res['kappa_state']):+.3f}")
    print()
    print("注：VR = 当日成交量 / 近20日均量；所有指标已剔除成交量绝对量级差异，仅衡量量能节奏。")
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
        description="两只股票成交量节奏（放量/平量/缩量）相似度对比")
    ap.add_argument("template", help="模板股票代码，如 688213.SH")
    ap.add_argument("target", help="目标股票代码，如 603501.SH")
    ap.add_argument("--start", required=True, help="开始日期 YYYYMMDD（含）")
    ap.add_argument("--end", required=True, help="结束日期 YYYYMMDD（含）")
    ap.add_argument("--ma", type=int, default=DEFAULT_MA,
                    help=f"量比/Z分滚动均量窗口，默认 {DEFAULT_MA}")
    args = ap.parse_args(argv)

    try:
        res = compare_volume_rhythm(
            args.template, args.target, args.start, args.end, ma=args.ma)
    except Exception as exc:
        print(f"❌ 计算失败：{exc}", file=sys.stderr)
        return 1

    print_report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
