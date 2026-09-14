#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
5日均线相似度选股 (find_similar_ma5.py)

核心功能：
  根据输入的目标股票代码，从 stock_daily_t 表读取所有股票最近 10 个交易日的 MA5 / MA30
  序列，找出与目标股票综合相似度最高的前 10 只股票。

计算方法：
  1. 取窗口长度 N=10，提取目标股票A、对比股票B 近 10 日 MA5 序列、MA30 序列
  2. 分别做 MinMax 归一化
  3. 计算归一后两个序列的 DTW 距离 d
     （DTW 累积平方距离除以对齐步数取根号 —— 即逐步RMS偏差，
       使 d 落在 [0,1] 量级：两序列完全一致时 d=0，逐点偏差为1时 d=1）
  4. 换算得分：score = 1/(1+d)
  5. 5日均线相似度 与 30日均线相似度 均按上述方法计算
  6. 最终相似度 = WEIGHT_MA5 × 5日均线相似度 + WEIGHT_MA30 × 30日均线相似度
     （权重常量各 0.5），按最终相似度降序取前 10 只

输出：
  - CSV「{目标股票代码}.csv」（csv.writer + utf-8-sig，表头 股票代码）
  - 文件夹「5日均线相似度+当日日期后缀」（已存在的删除重建）

用法：
  python3 find_similar_ma5.py 301171.SZ                 # 默认窗口N=10
  python3 find_similar_ma5.py 301171.SZ --window 30     # 自定义近30日窗口
  python3 find_similar_ma5.py 301171.SZ --top 10
"""

import os
import sys
import csv
import shutil
import argparse
import math
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection
from insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- 灵活过滤条件（新增条件只需在 STOCK_FILTERS 中追加一行） ----------
MA30_DEVIATION_MAX = 0.12  # 最新收盘价相对 ma30 的最大正偏离 +12%，排除严重超买
MIN_AMOUNT_YI = 5.0        # 最近一个交易日最低成交额（亿元）；amount 单位千元，5亿=500000千元
MIN_TURNOVER_RATE = 5.0    # 最近一个交易日最低换手率 turnover_rate_f（%）
WEIGHT_MA5 = 0.5           # 5日均线相似度权重
WEIGHT_MA30 = 0.5          # 30日均线相似度权重


def _filter_ma30_deviation(records):
    """最近一个交易日收盘价相对 ma30 偏离不超过 +12%（排除严重超买）。"""
    latest = records[-1]
    if latest['close'] is None or latest['ma30'] is None or latest['ma30'] <= 0:
        return False
    return (latest['close'] / latest['ma30'] - 1) <= MA30_DEVIATION_MAX


def _filter_min_amount(records):
    """最近一个交易日成交额 > MIN_AMOUNT_YI 亿元（amount 单位千元，amount*1000 为元）。"""
    latest = records[-1]
    if latest['amount'] is None:
        return False
    return latest['amount'] * 1000 > MIN_AMOUNT_YI * 1e8


def _filter_turnover_rate(records):
    """最近一个交易日换手率 turnover_rate_f >= MIN_TURNOVER_RATE%。"""
    latest = records[-1]
    tr = latest.get('turnover_rate_f')
    if tr is None:
        return False
    return tr >= MIN_TURNOVER_RATE


def _filter_close_above_ma5(records):
    """最近2个交易日收盘价均高于 ma5。"""
    if len(records) < 2:
        return False
    for r in records[-2:]:
        if r['close'] is None or r['ma5'] is None or r['ma5'] <= 0:
            return False
        if r['close'] <= r['ma5']:
            return False
    return True


# 过滤条件注册表：每个条件为 (名称, 函数)；函数入参为该股票近N日记录（按日期升序），返回 True=通过
STOCK_FILTERS = [
    ('最新日close相对ma30偏离>+12%', _filter_ma30_deviation),
    (f'最新日成交额<={MIN_AMOUNT_YI:g}亿', _filter_min_amount),
    (f'最新日换手率<{MIN_TURNOVER_RATE:g}%', _filter_turnover_rate),
    ('近2日close未均高于ma5', _filter_close_above_ma5),
]


def apply_filters(records):
    """依次执行 STOCK_FILTERS 中的全部过滤条件，返回 (是否通过, 未通过的条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(records)]
    return (len(reasons) == 0), reasons


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    """返回 target_date 前（含）最近 n 个交易日列表。"""
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 15)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) > n:
        opens = opens[-n:]
    return opens


def get_folder_name():
    return f"5日均线相似度{get_target_date()}"


def get_folder_path():
    """新建（已存在的删除重建）5日均线相似度+日期后缀的文件夹，返回路径"""
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


# ---------- 相似度算法 ----------

def minmax_normalize(vals):
    """MinMax 归一化到 [0,1]；常数序列（max==min）统一映射为 0.5。"""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def dtw_distance(a, b):
    """
    归一化序列的 DTW 距离。
    经典 DP：D[i][j] = 局部平方误差 + min(三个前驱)。
    最终距离 d = sqrt(累积平方误差 / 对齐步数)（逐步 RMS 偏差，量级 [0,1]）。
    两序列完全一致时 d=0；逐点偏差为 1 时 d=1。
    """
    n, m = len(a), len(b)
    INF = float('inf')
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    K = [[0] * (m + 1) for _ in range(n + 1)]  # 对齐步数
    D[0][0] = 0.0
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, m + 1):
            c = (ai - b[j - 1]) ** 2
            # 三个前驱：对角 / 上 / 左
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
        return float('inf')
    return math.sqrt(D[n][m] / steps)


def similarity_score(a_raw, b_raw):
    """MinMax归一化 + DTW 距离 + score = 1/(1+d)。"""
    a = minmax_normalize(a_raw)
    b = minmax_normalize(b_raw)
    d = dtw_distance(a, b)
    return 1.0 / (1.0 + d), d


# ---------- 核心逻辑 ----------

def read_ma5_data(start_date, end_date):
    """读取 stock_daily_t 全市场 [start_date, end_date] 区间的 ts_code/trade_date/ma5/vol。"""
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []

    query_sql = """
        SELECT d.ts_code, d.trade_date, d.close, d.ma5, d.ma30, d.amount,
               b.turnover_rate_f
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        WHERE d.trade_date >= %s AND d.trade_date <= %s
        ORDER BY d.ts_code, d.trade_date
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query_sql, (start_date, end_date))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {end_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(conn)


def find_similar(target_code, data, trade_dates, window, top_n=10):
    """计算全市场与目标股票的最终相似度（WEIGHT_MA5×MA5 + WEIGHT_MA30×MA30），返回降序前 top_n 只。

    参数 window：序列窗口长度 N（交易日数）
    """

    # ---------- 组装：每只股票的 MA5 / MA30 序列 ----------
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close':      float(record['close']) if record['close'] is not None else None,
            'ma5': float(record['ma5']) if record['ma5'] is not None else None,
            'ma30': float(record['ma30']) if record['ma30'] is not None else None,
            'amount': float(record['amount']) if record['amount'] is not None else None,
            'turnover_rate_f': (float(record['turnover_rate_f'])
                                if record['turnover_rate_f'] is not None else None),
        })

    if target_code not in stock_data:
        print(f"❌ 目标股票 {target_code} 不在 stock_daily_t 表中")
        return []

    cnt_data_missing = 0  # 近 N 日 MA5/MA30 数据不全
    cnt_filtered = {}     # 过滤条件淘汰数（按条件名统计）

    def extract_series(ts_code, field):
        """提取恰好 window 个交易日的完整字段序列（日期与 trade_dates 一致），否则 None。"""
        recs = stock_data.get(ts_code, [])
        if len(recs) != window:
            return None
        dates = [r['trade_date'] for r in recs]
        if dates != trade_dates:
            return None
        vals = [r[field] for r in recs]
        if any(v is None for v in vals):
            return None
        return vals

    ma5_a = extract_series(target_code, 'ma5')
    ma30_a = extract_series(target_code, 'ma30')
    if ma5_a is None or ma30_a is None:
        print(f"❌ 目标股票 {target_code} 近{window}个交易日 MA5/MA30 数据不全，无法比较")
        return []

    result = []
    for ts_code in stock_data:
        if ts_code == target_code:
            continue
        recs = stock_data[ts_code]

        # 过滤条件（如：最新日close相对ma30偏离≤+12%，排除严重超买）
        ok, reasons = apply_filters(recs)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
            continue

        ma5_b = extract_series(ts_code, 'ma5')
        ma30_b = extract_series(ts_code, 'ma30')
        if ma5_b is None or ma30_b is None:
            cnt_data_missing += 1
            continue

        # 5日均线相似度
        ma5_score, ma5_d = similarity_score(ma5_a, ma5_b)
        # 30日均线相似度（方法同MA5：MinMax归一化 + DTW）
        ma30_score, ma30_d = similarity_score(ma30_a, ma30_b)
        # 最终相似度 = WEIGHT_MA5×5日均线相似度 + WEIGHT_MA30×30日均线相似度
        final_score = WEIGHT_MA5 * ma5_score + WEIGHT_MA30 * ma30_score

        result.append({
            'ts_code': ts_code,
            'final_score': round(final_score, 4),
            'ma5_score': round(ma5_score, 4),
            'ma30_score': round(ma30_score, 4),
            'ma5_d': round(ma5_d, 4),
            'ma30_d': round(ma30_d, 4),
        })

    result.sort(key=lambda x: x['final_score'], reverse=True)

    # ---------- 漏斗统计 ----------
    total = len(stock_data)
    print("\n" + "=" * 60)
    print(f"目标股票: {target_code}")
    print(f"窗口长度: N={window} 个交易日（MA5 + MA30，MinMax归一化 + DTW）")
    print(f"评分公式: 最终相似度 = {WEIGHT_MA5}×5日均线相似度 + {WEIGHT_MA30}×30日均线相似度")
    print("         （每项 score = 1/(1+d)，d 为归一化序列的DTW距离）")
    print("-" * 40)
    print(f"  股票总数量:                       {total}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print(f"  - 近{window}日MA5/MA30数据不全 淘汰: {cnt_data_missing}")
    print(f"  - 目标股票自身:                   1")
    print("-" * 40)
    print(f"  参与排名: {len(result)}（无相似度阈值过滤，按最终相似度降序取前{top_n}）")
    print("=" * 60)

    return result[:top_n]


def generate_csv_file(stocks, folder_path, target_code):
    """CSV 以目标股票命名（如 301171.SZ.csv），csv.writer + utf-8-sig，单列 股票代码。"""
    csv_filename = f"{target_code}.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for s in stocks:
            writer.writerow([s['ts_code']])
    print(f"✅ CSV文件已生成（{len(stocks)}只）: {csv_path}")
    return csv_path


# ---------- 选股结果入库（便于回测） ----------

STRATEGY_NAME = 'similar_ma5'


# ---------- 主入口 ----------

def main():
    parser = argparse.ArgumentParser(description='5日均线相似度选股（MinMax归一化 + DTW）')
    parser.add_argument('target', type=str, help='目标股票代码，如 301171.SZ')
    parser.add_argument('--window', type=int, default=10,
                        help='序列窗口长度N（交易日数），默认10')
    parser.add_argument('--top', type=int, default=10, help='输出前N只，默认10')
    args = parser.parse_args()

    target_code = args.target.upper().strip()
    window = args.window
    if window <= 1:
        print("❌ --window 必须大于1")
        return
    target_date = get_target_date()

    print("=" * 80)
    print("🔍 5日均线相似度选股（目标股票 vs 全市场）")
    print("=" * 80)
    print(f"  目标股票: {target_code}")
    print(f"  算法: 近{window}日 MA5/MA30 → MinMax归一化 → DTW距离 d → score=1/(1+d)")
    print(f"  最终相似度 = {WEIGHT_MA5}×5日均线相似度 + {WEIGHT_MA30}×30日均线相似度（无阈值过滤）")
    print(f"  输出: 最终相似度最高的前 {args.top} 只")
    print("=" * 80)

    # ---------- 步骤A：最近 N 个交易日 ----------
    print(f"\n📅 目标日期: {target_date}")
    trade_dates = get_last_n_trade_dates(target_date, window)
    start_date, end_date = trade_dates[0], trade_dates[-1]
    print(f"   查询区间: {start_date} ~ {end_date}（共{len(trade_dates)}个交易日）")

    # ---------- 步骤B：读取 ----------
    data = read_ma5_data(start_date, end_date)
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    # ---------- 步骤C：计算相似度 ----------
    top_stocks = find_similar(target_code, data, trade_dates, window, top_n=args.top)

    if not top_stocks:
        print("\n⚠️ 没有可比较的股票（数据不全或目标股票数据缺失）")
        return

    # ---------- 步骤D：输出 ----------
    folder_path = get_folder_path()
    csv_path = generate_csv_file(top_stocks, folder_path, target_code)

    # 选股结果入库（便于回测）
    record_selected_stocks(STRATEGY_NAME,
                           [{'ts_code': s['ts_code'], 'selected': 1}
                            for s in top_stocks], end_date)

    print("\n" + "=" * 80)
    print("🎉 5日均线相似度选股完成！")
    print(f"📁 文件夹路径: {folder_path}")
    print(f"📄 CSV路径: {csv_path}")
    print("=" * 80)

    print(f"\n🔥 最终相似度排名前{len(top_stocks)}（降序）：")
    for i, s in enumerate(top_stocks, 1):
        print(f"{i:>2}. {s['ts_code']:<11} 最终={s['final_score']:.4f}  "
              f"(MA5={s['ma5_score']:.4f} + MA30={s['ma30_score']:.4f})  "
              f"d_ma5={s['ma5_d']:.3f} d_ma30={s['ma30_d']:.3f}")


if __name__ == "__main__":
    main()
