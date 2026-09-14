#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
二浪候选 × DTW 量价相似度二次打分 (find_similar_wave2.py)

对齐豆包对话第四节「扩展思路」：先筛选 2 浪标的（select_wave2.py 的全部硬条件+打分），
再用 DTW 量价相似度，匹配一个标杆 2 浪模板K线，对候选池做二次打分排序。

流程：
  1. 复用 select_wave2.run_wave2_selection 完成二浪筛选（市值/A股/硬条件/打分阈值同前）
  2. 确定标杆模板：
     - 默认：自动取当日二浪打分最高的候选股，以其「2浪回调段（H1日 → 最新日）」为模板
     - 或命令行指定 --code/--start/--end（模板取该股该区间的 MA5+成交量序列）
  3. 每只候选同样截取「H1 → 最新日」的 MA5 与 成交量 序列，MinMax 归一化后
     与模板分别做 DTW（平均逐点绝对偏差），sim = 1 - 平均偏差
  4. 综合相似度 = 0.5×MA5相似度 + 0.5×成交量相似度，按相似度降序输出
  5. 标杆股自身不参与排名

输出：
  - 文件夹「二浪相似+当日日期后缀」（已存在则复用）
  - CSV「{模板代码}.csv」（csv.writer + utf-8-sig，含相似度与二浪结构信息）
  - 结果为0时不创建文件夹/CSV

用法：
  python3 find_similar_wave2.py                                  # 自动标杆
  python3 find_similar_wave2.py --code 605111.SH --start 20260127 --end 20260615
  python3 find_similar_wave2.py --top 20 --min-score 60
"""

import os
import sys
import csv
import argparse
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from select_wave2 import (
    MIN_SCORE, TOP_N,
    get_target_date, get_last_n_trade_dates, read_stock_data, add_mas,
)
from insert_strategy_selected_record import record_selected_stocks

WEIGHT_MA = 0.5            # MA5 相似度权重
WEIGHT_VOL = 0.5           # 成交量相似度权重
MIN_SEGMENT = 8            # 相似度比较段的最少交易日数


# ---------- 工具函数 ----------

def get_folder_name():
    return f"二浪相似{get_target_date()}"


def get_folder_path():
    """文件夹「二浪相似+日期后缀」，已存在则复用。"""
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


# ---------- 选股结果入库（便于回测） ----------

STRATEGY_NAME = 'similar_wave2'


def minmax_normalize(vals):
    """MinMax 归一化到 [0,1]；常数序列统一映射 0.5。"""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def dtw_avg_dist(a, b):
    """DTW 平均逐点绝对偏差（带对齐步数归一），两序列可不等长。返回 (avg_dist, steps)。"""
    n, m = len(a), len(b)
    INF = float('inf')
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    K = [[0] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, m + 1):
            c = abs(ai - b[j - 1])
            d_diag, d_up, d_left = D[i - 1][j - 1], D[i - 1][j], D[i][j - 1]
            if d_diag <= d_up and d_diag <= d_left:
                D[i][j] = d_diag + c
                K[i][j] = K[i - 1][j - 1] + 1
            elif d_up <= d_left:
                D[i][j] = d_up + c
                K[i][j] = K[i - 1][j] + 1
            else:
                D[i][j] = d_left + c
                K[i][j] = K[i][j - 1] + 1
    steps = K[n][m]
    if steps <= 0:
        return float('inf'), 0
    return D[n][m] / steps, steps


def extract_segment(recs, start_idx, end_idx):
    """截取 [start_idx, end_idx] 闭区间内 ma5/vol 均有效的序列；不足 MIN_SEGMENT 返回 None。"""
    ma5, vol = [], []
    for r in recs[start_idx: end_idx + 1]:
        if r['ma5'] is None or r['vol'] is None or r['vol'] <= 0:
            continue
        ma5.append(r['ma5'])
        vol.append(r['vol'])
    if len(ma5) < MIN_SEGMENT:
        return None
    return ma5, vol


def similarity(tpl, seg):
    """(模板序列组, 候选序列组) → (综合相似度, MA5相似度, 量相似度)。"""
    tpl_ma, tpl_vol = tpl
    seg_ma, seg_vol = seg
    d_ma, _ = dtw_avg_dist(minmax_normalize(tpl_ma), minmax_normalize(seg_ma))
    d_vol, _ = dtw_avg_dist(minmax_normalize(tpl_vol), minmax_normalize(seg_vol))
    sim_ma = max(0.0, 1 - d_ma)
    sim_vol = max(0.0, 1 - d_vol)
    return WEIGHT_MA * sim_ma + WEIGHT_VOL * sim_vol, sim_ma, sim_vol


def load_template_series(template_code, start_date, end_date):
    """命令行指定模板：读该股数据（向前多取90自然日作均线种子），
    截取 [start, end] 区间的 (ma5序列, vol序列)。"""
    seed_start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
    data = read_stock_data(seed_start, end_date)
    tpl_rows = [r for r in data if r['ts_code'] == template_code]
    if not tpl_rows:
        return None
    recs = [{
        'trade_date': r['trade_date'],
        'close': float(r['close']) if r['close'] is not None else None,
        'vol': float(r['vol']) if r['vol'] is not None else None,
        'ma5': None, 'ma30': None, 'ma_long': None,
    } for r in tpl_rows]
    add_mas(recs)
    idxs = [i for i, r in enumerate(recs) if start_date <= r['trade_date'] <= end_date]
    if not idxs:
        return None
    return extract_segment(recs, idxs[0], idxs[-1])


# ---------- 主流程 ----------

def main():
    parser = argparse.ArgumentParser(description='二浪候选 × DTW量价相似度二次打分')
    parser.add_argument('--code', default=None, help='标杆模板股票代码（默认自动取二浪打分最高者）')
    parser.add_argument('--start', default=None, help='模板起始日YYYYMMDD（与--code搭配）')
    parser.add_argument('--end', default=None, help='模板结束日YYYYMMDD（与--code搭配）')
    parser.add_argument('--min-score', type=float, default=MIN_SCORE,
                        help=f'二浪打分阈值（100分制），默认{MIN_SCORE}')
    parser.add_argument('--top', type=int, default=TOP_N, help=f'输出前N只，默认{TOP_N}')
    args = parser.parse_args()

    import select_wave2
    select_wave2.MIN_SCORE = args.min_score

    target_date = get_target_date()
    print("=" * 80)
    print(f"🌊 二浪候选 × DTW量价相似度二次打分 — 目标日期 {target_date}")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, 250)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    result, stock_data = select_wave2.run_wave2_selection(data, trade_dates)
    if not result:
        print("⚠️ 二浪候选为0，无法做相似度排序，退出")
        return

    # ---------- 确定标杆模板 ----------
    if args.code:
        template_code = args.code.upper().strip()
        if not (args.start and args.end):
            print("❌ 指定 --code 时必须同时给 --start 和 --end")
            return
        tpl = load_template_series(template_code, args.start, args.end)
        tpl_desc = f"{template_code} {args.start}~{args.end}"
        if tpl is None:
            print(f"❌ 模板 {tpl_desc} 数据不足（MA5/vol 缺失或段长<{MIN_SEGMENT}日）")
            return
        # 指定模板时：候选仍取各自「H1→最新日」段与模板对比
    else:
        top = result[0]
        template_code = top['ts_code']
        recs = stock_data[template_code]
        tpl = extract_segment(recs, top['h1_idx'], len(recs) - 1)
        tpl_desc = f"{template_code} 2浪回调段 {top['h1_date']}~{top['l2_date']}（自动标杆）"
        if tpl is None:
            print(f"❌ 自动标杆 {template_code} 回调段数据不足，退出")
            return

    print(f"\n📌 标杆模板: {tpl_desc}")
    print(f"   评分: {WEIGHT_MA}×MA5相似度 + {WEIGHT_VOL}×成交量相似度（DTW平均逐点偏差）")

    # ---------- 候选二次打分 ----------
    ranked = []
    for cand in result:
        if cand['ts_code'] == template_code and not args.code:
            continue  # 自动标杆股自身不参与排名
        recs = stock_data[cand['ts_code']]
        seg = extract_segment(recs, cand['h1_idx'], len(recs) - 1)
        if seg is None:
            continue
        sim, sim_ma, sim_vol = similarity(tpl, seg)
        cand['sim'] = round(sim, 4)
        cand['sim_ma'] = round(sim_ma, 4)
        cand['sim_vol'] = round(sim_vol, 4)
        ranked.append(cand)

    ranked.sort(key=lambda x: x['sim'], reverse=True)

    if not ranked:
        print("⚠️ 相似度排序结果为0，不生成文件夹/CSV")
        return

    # ---------- 输出 ----------
    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, f"{template_code}.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '综合相似度', 'MA5相似度', '成交量相似度', '二浪打分',
                         '回撤幅度', '浪1天数', '浪2天数',
                         'L1日期', 'H1日期', 'L2日期', '总市值(万)', '最新收盘'])
        for s in ranked[:args.top]:
            writer.writerow([
                s['ts_code'], s['sim'], s['sim_ma'], s['sim_vol'], s['score'],
                f"{s['retrace']:.3f}", s['wave1_days'], s['wave2_days'],
                s['l1_date'], s['h1_date'], s['l2_date'],
                f"{s['total_mv']:.0f}", s['close'],
            ])
    print(f"✅ CSV已生成（{min(args.top, len(ranked))}只）: {csv_path}")

    # 选股结果入库（便于回测，与CSV内容一致取Top N）
    record_selected_stocks(STRATEGY_NAME,
                           [{'ts_code': s['ts_code'], 'selected': 1}
                            for s in ranked[:args.top]], trade_dates[-1])

    print(f"\n🔥 相似度排名前{min(args.top, len(ranked))}：")
    for i, s in enumerate(ranked[:args.top], 1):
        print(f"{i:>2}. {s['ts_code']:<11} 相似度={s['sim']:.4f} "
              f"(MA5={s['sim_ma']:.3f} 量={s['sim_vol']:.3f}) "
              f"二浪打分={s['score']} 回撤={s['retrace']:.3f} "
              f"形态={s['h1_date']}~{s['l2_date']} 市值={s['total_mv'] / 10000:.0f}亿")


if __name__ == '__main__':
    main()
