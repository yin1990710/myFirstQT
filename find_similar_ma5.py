#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股策略：5日均线 + 成交量节奏 相似度选股
================================================================
输入模板股票代码、开始日期、结束日期、相似度排名 N，扫描 stock_daily_t 全市场，
计算每只股票与模板股票在选定日期范围内的相似度，按倒序返回前 N 只的
股票代码、名称和相似度得分。

综合相似度 = 5日均线相似度 × 0.5 + 成交量走势相似度 × 0.5
  - 5日均线相似度：来自 module_ma5_similarity_compare.py
      归一化 MA5 曲线相关 / DTW 形状 / MA5 收益率相关 / 斜率拐头 / MA5-MA20 状态
  - 成交量节奏相似度：来自 module_volume_rhythm_compare.py
      量能对数Z分 / 量比VR / DTW 形状 / 方向一致率 / 放缩量状态 Kappa
两个子分均已剔除绝对价位与成交量级差异，且各自在模板打分日历上逐日对齐；
全市场面板一次性取数（含均线/均量预热段），候选与模板的打分口径与两个
成对比较模块完全一致。

输出：
  - CSV「{模板代码}.csv」（csv.writer + utf-8-sig）
  - 文件夹「5日均线相似度+结束日后缀」
  - 选股结果写 strategy_selected_stock_daily_t（便于回测）

用法：
  python3 find_similar_ma5.py 688213.SH --start 20260101 --end 20260630 --top 10
  python3 find_similar_ma5.py 688213.SH --start 2026-01-01 --end 2026-06-30

作为模块调用：
  from find_similar_ma5 import find_similar_ma5
  rows = find_similar_ma5('688213.SH', '20260101', '20260630', top_n=10)
"""

import argparse
import csv
import os
import shutil
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks
import module_ma5_similarity_compare as mam
import module_volume_rhythm_compare as vrm


# ---------- 常量 ----------
WEIGHT_MA5 = 0.5                    # 5日均线相似度权重
WEIGHT_VOLUME = 0.5                 # 成交量走势相似度权重
MA_FAST = mam.DEFAULT_FAST          # 快线周期 5
MA_SLOW = mam.DEFAULT_SLOW          # 慢线周期 20（多空状态 / 金叉死叉）
VOL_MA = vrm.DEFAULT_MA             # 量比/对数Z分滚动窗口 20
DEFAULT_TOP_N = 10

STRATEGY_NAME = '5日均线相似度选股策略'


# ---------- 数据读取（全市场面板，一次取数含预热段） ----------

def _norm_date(d: str) -> str:
    return str(d).replace("-", "").strip()


def _seed_start(start: str) -> str:
    """预热自然日：同时满足慢线 MA20 与成交量窗口 MA20 的滚动种子需求。"""
    seed_days = max(MA_SLOW * 2 + 15, VOL_MA * 2 + 15)
    return (datetime.strptime(start, "%Y%m%d") - timedelta(days=seed_days)).strftime("%Y%m%d")


def load_market_panel(template_code: str, start: str, end: str
                      ) -> Tuple[List[str], Dict[str, pd.DataFrame], Dict[str, str]]:
    """一次性读取全市场 [seed, end] 的 close/vol 与股票名称。

    返回 (template_dates, grouped, name_map)：
      template_dates —— 模板股票在 [start,end] 内有行情的交易日（升序，打分日历）
      grouped        —— {ts_code: DataFrame(trade_date, close, vol)}（含预热段）
      name_map       —— {ts_code: stock_name}
    """
    seed = _seed_start(start)
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT trade_date FROM stock_daily_t
                WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (template_code, start, end))
            template_dates = [r["trade_date"] for r in cur.fetchall()]
            if not template_dates:
                raise RuntimeError(f"模板股票 {template_code} 在 {start}~{end} 内无行情数据")

            cur.execute("""
                SELECT d.ts_code, d.trade_date, d.close, d.vol, i.stock_name
                FROM stock_daily_t d
                LEFT JOIN stock_info_t i
                  ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
                WHERE d.trade_date >= %s AND d.trade_date <= %s
                  AND (d.ts_code LIKE '%%.SH' OR d.ts_code LIKE '%%.SZ')
                ORDER BY d.ts_code, d.trade_date
            """, (seed, end))
            rows = cur.fetchall()
    finally:
        close_connection(conn)

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"stock_daily_t 在 {seed}~{end} 内无数据")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")

    name_map = (df.dropna(subset=["stock_name"])
                  .drop_duplicates("ts_code")
                  .set_index("ts_code")["stock_name"].to_dict())
    grouped = {code: g[["trade_date", "close", "vol"]].reset_index(drop=True)
               for code, g in df.groupby("ts_code", sort=False)}
    return template_dates, grouped, name_map


# ---------- 模板特征（两只子模块口径，只构造一次） ----------

def build_template_features(g: pd.DataFrame, dates: List[str]) -> Dict[str, np.ndarray]:
    """在模板自身交易日上算 MA 与量能节奏（含预热滚动），再按打分日历对齐。"""
    ma_df = (mam.compute_mas(g[["trade_date", "close"]], MA_FAST, MA_SLOW)
             .set_index("trade_date").reindex(dates))
    nz = mam.normalize_ma(ma_df[f"ma{MA_FAST}"])
    vol_df = (vrm.build_rhythm(g["vol"], g["trade_date"], VOL_MA)
              .set_index("trade_date").reindex(dates))
    return {
        "ma_fast": ma_df[f"ma{MA_FAST}"].values,
        "ma_slow": ma_df[f"ma{MA_SLOW}"].values,
        "ma_rebase": nz["rebase"].values,
        "ma_z": nz["zscore"].values,
        "ma_ret": nz["return"].values,
        "ma_state": mam.ma_state(ma_df[f"ma{MA_FAST}"], ma_df[f"ma{MA_SLOW}"]),
        "vol_z": vol_df["z"].values,
        "vol_vr": vol_df["vr"].values,
        "vol_state": vol_df["state"].values,
    }


# ---------- 候选打分（复用两个模块的纯计算函数） ----------

def _slope_agreement(a: np.ndarray, b: np.ndarray) -> float:
    """压缩到共同有效段后的均线斜率方向一致率（与 mam.slope_agreement 同口径）。"""
    sa, sb = np.sign(np.diff(a)), np.sign(np.diff(b))
    msk = (sa != 0) & (sb != 0)
    return float((sa[msk] == sb[msk]).mean()) if msk.sum() else float("nan")


def score_candidate(g: pd.DataFrame,
                    dates: List[str],
                    tpl: Dict[str, np.ndarray]) -> Optional[Dict[str, float]]:
    """计算单只候选的 MA5 相似度、成交量节奏相似度与综合分；样本不足返回 None。"""
    # —— 均线侧 ——
    ma_df = (mam.compute_mas(g[["trade_date", "close"]], MA_FAST, MA_SLOW)
             .set_index("trade_date").reindex(dates))
    # —— 量能侧 ——
    vol_df = (vrm.build_rhythm(g["vol"], g["trade_date"], VOL_MA)
              .set_index("trade_date").reindex(dates))

    fast_b, slow_b = ma_df[f"ma{MA_FAST}"].values, ma_df[f"ma{MA_SLOW}"].values
    vz_b, vr_b = vol_df["z"].values, vol_df["vr"].values

    # 共同有效掩码：两侧均线 + 模板/候选量能Z分均可计算
    mask = (np.isfinite(tpl["ma_fast"]) & np.isfinite(fast_b)
            & np.isfinite(tpl["ma_slow"]) & np.isfinite(slow_b)
            & np.isfinite(tpl["vol_z"]) & np.isfinite(vz_b))
    overlap = int(mask.sum())
    n_tpl = len(dates)
    if (overlap < mam.MIN_OVERLAP_DAYS
            or overlap / n_tpl < mam.MIN_OVERLAP_RATIO):
        return None

    # ================= MA5 相似度指标（module_ma5_similarity_compare 口径） =================
    nz_b = mam.normalize_ma(ma_df[f"ma{MA_FAST}"])
    reb_b, z_b, ret_b = nz_b["rebase"].values, nz_b["zscore"].values, nz_b["return"].values
    band = max(3, int(mam.DTW_BAND_RATIO * overlap))

    ma_metrics: Dict[str, float] = {}
    ma_metrics["corr_rebase"] = mam.safe_corr(tpl["ma_rebase"][mask], reb_b[mask])
    ma_metrics["corr_zscore"] = mam.safe_corr(tpl["ma_z"][mask], z_b[mask])
    ma_metrics["corr_ma_return"] = mam.safe_corr(tpl["ma_ret"][mask], ret_b[mask])
    ma_metrics["dtw_similarity"], _ = mam.dtw_similarity(
        tpl["ma_z"][mask], z_b[mask], band)
    ma_metrics["slope_agreement"] = _slope_agreement(
        tpl["ma_fast"][mask], fast_b[mask])
    st_b = mam.ma_state(ma_df[f"ma{MA_FAST}"], ma_df[f"ma{MA_SLOW}"])
    po, kappa = mam.categorical_agreement(tpl["ma_state"][mask], st_b[mask])
    ma_metrics["state_agreement"] = po
    ma_metrics["kappa_state"] = kappa
    ga, da = mam.cross_events(tpl["ma_fast"][mask], tpl["ma_slow"][mask])
    gb, db = mam.cross_events(fast_b[mask], slow_b[mask])
    ma_metrics["golden_jaccard"] = mam.event_jaccard(ga, gb)
    ma_metrics["death_jaccard"] = mam.event_jaccard(da, db)
    ma_score = mam.composite_score(ma_metrics)

    # ================= 成交量节奏相似度指标（module_volume_rhythm_compare 口径） =================
    st_vol_b = vol_df["state"].values
    vol_metrics: Dict[str, float] = {}
    # 相关系数复用均线模块的带掩码 Pearson（与量能模块内部 np.corrcoef 口径等价）
    vol_metrics["corr_z"] = mam.safe_corr(tpl["vol_z"][mask], vz_b[mask])
    vol_metrics["corr_vr"] = mam.safe_corr(tpl["vol_vr"][mask], vr_b[mask])
    vol_metrics["dtw_similarity"] = vrm.dtw_similarity(
        tpl["vol_z"][mask], vz_b[mask], band)
    vol_metrics["direction_agreement"] = vrm.direction_agreement(
        tpl["vol_z"][mask], vz_b[mask])
    _, vol_kappa = vrm.state_kappa(tpl["vol_state"][mask], st_vol_b[mask])
    vol_metrics["kappa_state"] = vol_kappa
    vol_score = vrm.composite_score(vol_metrics)

    if not (np.isfinite(ma_score) and np.isfinite(vol_score)):
        return None

    final_score = WEIGHT_MA5 * ma_score + WEIGHT_VOLUME * vol_score
    return {
        "score": float(final_score),
        "ma5_score": float(ma_score),
        "volume_score": float(vol_score),
        "overlap_days": overlap,
        "overlap_ratio": overlap / n_tpl,
    }


# ---------- 全市场扫描主入口 ----------

def find_similar_ma5(template_code: str,
                     start_date: str,
                     end_date: str,
                     top_n: int = DEFAULT_TOP_N,
                     verbose: bool = True) -> List[Dict[str, object]]:
    """扫描全市场，返回与模板在 [start_date, end_date] 综合相似度最高的前 top_n 只。

    综合相似度 = 5日均线相似度×0.5 + 成交量走势相似度×0.5，按得分降序。
    返回结果首条固定为模板股票自身（相似度 1.0，不占用 top_n 名额），
    其后为相似度排名前 top_n 的候选股票，共 top_n+1 条。
    每条：ts_code / stock_name / score / ma5_score / volume_score /
          overlap_days / overlap_ratio / is_template
    """
    start, end = _norm_date(start_date), _norm_date(end_date)
    if start > end:
        raise ValueError(f"开始日期 {start} 晚于结束日期 {end}")
    template_code = template_code.upper().strip()

    dates, grouped, name_map = load_market_panel(template_code, start, end)
    if template_code not in grouped:
        raise RuntimeError(f"模板股票 {template_code} 不在 stock_daily_t 中")
    tpl = build_template_features(grouped[template_code], dates)

    results: List[Dict[str, object]] = []
    skipped = 0
    total = len(grouped)
    for idx, (code, g) in enumerate(grouped.items(), 1):
        if verbose and idx % 1000 == 0:
            print(f"  扫描进度 {idx}/{total}，已命中 {len(results)}")
        # 模板股票不参与候选扫描，统一以 1.0 分置顶返回
        if code == template_code:
            continue
        try:
            r = score_candidate(g, dates, tpl)
        except Exception:
            skipped += 1
            continue
        if r is None:
            skipped += 1
            continue
        results.append({
            "ts_code": code,
            "stock_name": name_map.get(code, ""),
            "score": round(r["score"], 4),
            "ma5_score": round(r["ma5_score"], 4),
            "volume_score": round(r["volume_score"], 4),
            "overlap_days": r["overlap_days"],
            "overlap_ratio": round(r["overlap_ratio"], 3),
            "is_template": False,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:top_n]

    # 模板记录置顶：自身与自身走势完全一致，相似度赋 1.0
    template_row = {
        "ts_code": template_code,
        "stock_name": name_map.get(template_code, ""),
        "score": 1.0,
        "ma5_score": 1.0,
        "volume_score": 1.0,
        "overlap_days": len(dates),
        "overlap_ratio": 1.0,
        "is_template": True,
    }
    top = [template_row] + top

    if verbose:
        print("=" * 78)
        print(f"模板 {template_code}（{name_map.get(template_code, '')}）"
              f" {dates[0]}~{dates[-1]}，打分日历 {len(dates)} 个交易日")
        print(f"评分公式：综合相似度 = {WEIGHT_MA5}×5日均线相似度 "
              f"+ {WEIGHT_VOLUME}×成交量走势相似度")
        print(f"扫描 {total} 只 → 参与排名 {len(results)} 只，"
              f"样本不足/异常跳过 {skipped} 只，返回模板 + Top {top_n} 候选"
              f"（共 {len(top)} 只）")
        print("=" * 78)
    return top


# ---------- CSV 输出 ----------

def get_folder_name(end_date: str) -> str:
    return f"5日均线相似度{end_date}"


def get_folder_path(end_date: str) -> str:
    """新建（已存在的删除重建）5日均线相似度+结束日后缀文件夹，返回路径。"""
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               get_folder_name(end_date))
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {get_folder_name(end_date)}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {get_folder_name(end_date)}")
    return folder_path


def generate_csv_file(stocks: List[Dict[str, object]], folder_path: str,
                      template_code: str) -> str:
    """CSV 以模板股票命名，utf-8-sig，首行为模板（相似度1.0），其后为候选排名。"""
    csv_path = os.path.join(folder_path, f"{template_code}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["股票代码", "股票名称", "综合相似度",
                         "5日均线相似度", "成交量节奏相似度", "共同交易日", "是否模板"])
        for s in stocks:
            writer.writerow([s["ts_code"], s["stock_name"], s["score"],
                             s["ma5_score"], s["volume_score"], s["overlap_days"],
                             "是" if s.get("is_template") else ""])
    print(f"✅ CSV文件已生成（{len(stocks)}只，含模板）: {csv_path}")
    return csv_path


# ---------- 主入口 ----------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="5日均线+成交量节奏相似度选股（模板区间 vs 全市场）")
    parser.add_argument("template", type=str,
                        help="模板股票代码，如 688213.SH")
    parser.add_argument("--start", required=True,
                        help="开始日期 YYYYMMDD（含）")
    parser.add_argument("--end", required=True,
                        help="结束日期 YYYYMMDD（含）")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help=f"相似度排名前N只，默认 {DEFAULT_TOP_N}")
    args = parser.parse_args()

    if args.top <= 0:
        print("❌ --top 必须大于 0")
        return

    print("=" * 80)
    print("🔍 5日均线+成交量节奏相似度选股（模板股票 vs 全市场）")
    print("=" * 80)
    print(f"  模板股票: {args.template}")
    print(f"  日期区间: {args.start} ~ {args.end}")
    print(f"  评分公式: 综合相似度 = {WEIGHT_MA5}×5日均线相似度 "
          f"+ {WEIGHT_VOLUME}×成交量走势相似度")
    print(f"  输出: 模板（相似度1.0）置顶 + 相似度倒序排名前 {args.top} 只候选")
    print("=" * 80)

    try:
        top_stocks = find_similar_ma5(
            args.template, args.start, args.end, top_n=args.top)
    except Exception as exc:
        print(f"❌ 计算失败：{exc}", file=sys.stderr)
        return

    if not top_stocks:
        print("\n⚠️ 没有可比较的股票（模板数据缺失或全市场共同交易日不足）")
        return

    end_date = _norm_date(args.end)
    folder_path = get_folder_path(end_date)
    csv_path = generate_csv_file(top_stocks, folder_path, args.template.upper().strip())

    # 选股结果入库（便于回测，与 CSV 内容一致）
    record_selected_stocks(STRATEGY_NAME,
                           [{"ts_code": s["ts_code"], "selected": 1}
                            for s in top_stocks], end_date)

    print("\n" + "=" * 80)
    print("🎉 5日均线+成交量节奏相似度选股完成！")
    print(f"📁 文件夹路径: {folder_path}")
    print(f"📄 CSV路径: {csv_path}")
    print("=" * 80)

    print(f"\n🔥 模板置顶 + 综合相似度排名前{args.top}候选（降序）：")
    for i, s in enumerate(top_stocks, 1):
        tag = " [模板]" if s.get("is_template") else ""
        print(f"{i:>2}. {s['ts_code']:<11} {s['stock_name']:<9} "
              f"综合={s['score']:.4f} (MA5={s['ma5_score']:.3f} "
              f"量能={s['volume_score']:.3f}) 共同={s['overlap_days']}天{tag}")


if __name__ == "__main__":
    main()
