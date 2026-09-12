#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
量价形态相似选股 (find_similar_price_vol.py)

以 605111.SH 在 20260127 ~ 20260615（共90个交易日）的交易数据为模板，
从 stock_daily_t 表扫描全市场股票近 180 个交易日的走势，
用滑动窗口 + DTW 找出量价形态最相似的股票。

相似度算法（沿用本文件原逻辑）：
  1. 特征：基于 close 计算的 5日均线 MA5 + 成交量 vol，窗口长度 = 模板交易日数（90）
  2. 各自做 MinMax 归一化，只保留形态、消除量纲与价格差异
  3. 分别计算均线 / 成交量的 DTW 距离（Sakoe-Chiba 带状约束 ±10 交易日）
     sim = 1 - dist / 窗口长度（dist 为最优对齐路径的平均逐点绝对偏差）
  4. 综合相似度 = 0.6 × MA5相似度 + 0.4 × 成交量相似度
  5. 形态结束日若不满足「close>ma30 且 ma30 较5日前上行」的多头结构，得分 ×0.3（惩罚，不直接淘汰）

扫描方式：
  - 每只股票近 LOOKBACK 个交易日内，按步长5滑动窗口（含最新窗口），取最高分窗口
  - 仅保留形态结束日在最近 RECENT_END_DAYS 个交易日内的匹配（形态是近期/当前的）

过滤条件：
  - 最新交易日总市值 total_mv（万元）>= 100亿（1,000,000 万元）
  - STOCK_FILTERS 灵活过滤（当前：最新交易日成交额 > 5亿、近5日至少1日涨停涨幅>9.9%，可在注册表中追加）
  - 仅 A 股（.SH / .SZ）
  - 最新交易日有数据（剔除停牌）
  - 综合相似度 >= MIN_SCORE

输出：
  - 文件夹「量价相似+当日日期后缀」（已存在则复用）
  - CSV「量价相似.csv」（csv.writer + utf-8-sig）
使用方式：
python find_similar_price_vol.py --code 300207.SZ --start 20260722 --end 20260911 [--min-score 0.7]
"""

import os
import sys
import csv
from datetime import datetime, timedelta

import numpy as np
import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- 模板与扫描参数（模板代码/日期可用命令行参数覆盖） ----------
TEMPLATE_CODE = '605111.SH'
TEMPLATE_START = '20260127'
TEMPLATE_END = '20260615'

LOOKBACK_DAYS = 180        # 候选股票回溯交易日数（需 >= 模板长度，供滑动窗口）
SLIDE_STEP = 5             # 滑动窗口步长（交易日）
RECENT_END_DAYS = 20       # 形态结束日须在最近N个交易日内
MIN_MARKET_CAP_WAN = 1_000_000  # 最新日总市值下限（万元）= 100亿
MIN_SCORE = 0.70           # 综合相似度阈值
TOP_N = 20                 # 输出数量
WEIGHT_MA = 0.5            # MA5 形态权重
WEIGHT_VOL = 0.5           # 成交量形态权重
DTW_BAND = 10              # DTW Sakoe-Chiba 带状约束（对齐偏移不超过±10个交易日）

# ---------- 灵活过滤条件（新增条件只需在 STOCK_FILTERS 中追加一行） ----------
MIN_AMOUNT_YI = 5.0        # 最近一个交易日最低成交额（亿元）；amount 单位千元，5亿=500000千元
LIMIT_UP_LOOKBACK = 5      # 涨停回溯交易日数
LIMIT_UP_PCT = 9         # 涨停涨幅阈值（%）：(当日close-前日close)/前日close*100
MA30_DEVIATION_MAX = 0.12  # 最新收盘价相对 ma30 的最大正偏离 +12%，排除严重超买


def _filter_min_amount(records):
    """最近一个交易日成交额 > MIN_AMOUNT_YI 亿元（amount 单位千元，amount*1000 为元）。"""
    latest = records[-1]
    if latest.get('amount') is None:
        return False
    return latest['amount'] * 1000 > MIN_AMOUNT_YI * 1e8


def _filter_recent_limit_up(records):
    """最近 LIMIT_UP_LOOKBACK 个交易日内至少有一日涨幅 > LIMIT_UP_PCT%（涨停）。"""
    if len(records) < LIMIT_UP_LOOKBACK + 1:
        return False
    for i in range(len(records) - LIMIT_UP_LOOKBACK, len(records)):
        curr, prev = records[i], records[i - 1]
        if curr['close'] is None or prev['close'] is None or prev['close'] <= 0:
            continue
        gain = (curr['close'] - prev['close']) / prev['close'] * 100
        if gain > LIMIT_UP_PCT:
            return True
    return False


def _filter_ma30_deviation(records):
    """最近一个交易日收盘价相对 ma30 偏离不超过 +MA30_DEVIATION_MAX（排除严重超买）。"""
    latest = records[-1]
    if latest.get('close') is None or latest.get('ma30') is None or latest['ma30'] <= 0:
        return False
    return (latest['close'] / latest['ma30'] - 1) <= MA30_DEVIATION_MAX


def _filter_close_above_ma5(records):
    """最近一个交易日收盘价 > ma5（ma5 由 close 自算，字段 ma5）。"""
    latest = records[-1]
    if latest.get('close') is None or latest.get('ma5') is None or latest['ma5'] <= 0:
        return False
    return latest['close'] > latest['ma5']


# 过滤条件注册表：每个条件为 (名称, 函数)；函数入参为该股票记录（按日期升序），返回 True=通过
STOCK_FILTERS = [
    (f'最新日成交额<={MIN_AMOUNT_YI:g}亿', _filter_min_amount),
    (f'近{LIMIT_UP_LOOKBACK}日无涨停(>{LIMIT_UP_PCT}%)', _filter_recent_limit_up),
    (f'最新日close相对ma30偏离>+{MA30_DEVIATION_MAX*100:g}%', _filter_ma30_deviation),
    ('最新日close<=ma5', _filter_close_above_ma5),
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
    return f"量价相似{get_target_date()}"


def get_folder_path():
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


def normalize_series(series):
    """MinMax 归一化到 [0,1]；常数序列返回全 0.5，消除量纲只保留形态。"""
    arr = np.array(series, dtype=float)
    lo, hi = np.min(arr), np.max(arr)
    if hi - lo < 1e-9:
        return np.full_like(arr, 0.5)
    return (arr - lo) / (hi - lo)


def add_ma5(records):
    """基于 close 为记录列表就地计算 ma5_calc（前4日为 None）。"""
    for i, r in enumerate(records):
        if i >= 4 and all(records[j]['close'] is not None for j in range(i - 4, i + 1)):
            r['ma5_calc'] = sum(records[j]['close'] for j in range(i - 4, i + 1)) / 5
        else:
            r['ma5_calc'] = None


def dtw_distance(a, b, band=DTW_BAND):
    """
    纯 numpy 实现的 DTW 距离（绝对误差累积），带 Sakoe-Chiba 带状约束。
    等长形态序列只允许 ±band 范围内的对齐偏移，大幅减少计算量且避免病态扭曲。
    返回最优对齐路径上的累积绝对误差。
    """
    n, m = len(a), len(b)
    INF = float('inf')
    prev = np.full(m + 1, INF)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = np.full(m + 1, INF)
        j_lo = max(1, i - band)
        j_hi = min(m, i + band)
        ai = a[i - 1]
        for j in range(j_lo, j_hi + 1):
            cost = abs(ai - b[j - 1])
            cur[j] = cost + min(cur[j - 1], prev[j], prev[j - 1])
        prev = cur
    return prev[m]


def window_similarity(tpl_ma, tpl_vol, ma_seq, vol_seq):
    """计算单个窗口与模板的 (综合相似度, MA5相似度, 量相似度)，均为 0~1。

    DTW 累积绝对误差 / 窗口长度 = 平均逐点偏差；sim = 1 - 平均偏差。
    """
    dist_ma = dtw_distance(tpl_ma, ma_seq)
    dist_vol = dtw_distance(tpl_vol, vol_seq)
    n = len(tpl_ma)
    sim_ma = max(0.0, 1.0 - dist_ma / n)
    sim_vol = max(0.0, 1.0 - dist_vol / n)
    total = WEIGHT_MA * sim_ma + WEIGHT_VOL * sim_vol
    return total, sim_ma, sim_vol


# ---------- 数据读取 ----------

def load_template(template_code=TEMPLATE_CODE,
                  template_start=TEMPLATE_START,
                  template_end=TEMPLATE_END):
    """读取模板股票在 template_start~template_end 的K线，返回归一化模板序列。

    向前多取15个自然日作为 MA5 计算种子，避免模板前4日 ma5 缺失；
    MA5 直接由 close 滚动计算，不依赖 DB 打标完整性。
    """
    seed_start = (datetime.strptime(template_start, '%Y%m%d')
                  - timedelta(days=15)).strftime('%Y%m%d')
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT trade_date, close, ma30, vol
                FROM stock_daily_t
                WHERE ts_code = %s AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date
            """, (template_code, seed_start, template_end))
            rows = cursor.fetchall()
    finally:
        close_connection(conn)

    if not rows:
        raise RuntimeError(f"模板股票 {template_code} 在 {template_start}~{template_end} 无数据")

    seed_records = [{
        'trade_date': r['trade_date'],
        'close': float(r['close']) if r['close'] is not None else None,
        'ma30': float(r['ma30']) if r['ma30'] is not None else None,
        'vol': float(r['vol']) if r['vol'] is not None else None,
    } for r in rows]
    add_ma5(seed_records)

    # 截取严格落在模板日期区间内的记录
    records = [r for r in seed_records if template_start <= r['trade_date'] <= template_end]
    if any(r['close'] is None or r['ma5_calc'] is None or r['vol'] is None or r['vol'] <= 0
           for r in records):
        raise RuntimeError(f"模板股票 {template_code} 存在 close/ma5/vol 缺失，无法构建模板")

    seq_len = len(records)
    tpl_ma = normalize_series([r['ma5_calc'] for r in records])
    tpl_vol = normalize_series([r['vol'] for r in records])

    print(f"✅ 模板加载: {template_code} {records[0]['trade_date']}~{records[-1]['trade_date']}"
          f"（{seq_len}个交易日）")
    print(f"   收盘 {records[0]['close']:.2f} → {records[-1]['close']:.2f} "
          f"({(records[-1]['close'] / records[0]['close'] - 1) * 100:+.1f}%)")
    return tpl_ma, tpl_vol, records, seq_len


def read_candidates(start_date, end_date):
    """读取全市场 [start_date, end_date] 的K线 + 最新日总市值。"""
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT
            d.ts_code,
            d.trade_date,
            d.close,
            d.ma30,
            d.ma5,
            d.vol,
            d.amount,
            b.total_mv
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
        print(f"✅ 成功读取 {len(results)} 条候选数据 ({start_date} ~ {end_date})")
        return results
    except Exception as e:
        print(f"❌ 查询候选数据失败: {e}")
        return []
    finally:
        close_connection(conn)


# ---------- 核心扫描 ----------

def scan_stocks(data, tpl_ma, tpl_vol, seq_len, latest_date, template_code=TEMPLATE_CODE):
    """滑动窗口扫描全市场，返回每个股票的最佳匹配窗口，按综合相似度降序。"""
    stock_data = {}
    for record in data:
        code = record['ts_code']
        stock_data.setdefault(code, []).append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] is not None else None,
            'ma30': float(record['ma30']) if record['ma30'] is not None else None,
            'ma5': float(record['ma5']) if record['ma5'] is not None else None,
            'vol': float(record['vol']) if record['vol'] is not None else None,
            'amount': float(record['amount']) if record['amount'] is not None else None,
            'total_mv': float(record['total_mv']) if record['total_mv'] is not None else 0.0,
        })

    # 每只股票基于 close 自行计算 MA5（不依赖 DB 打标完整性）
    for recs in stock_data.values():
        add_ma5(recs)

    cnt_no_latest = 0      # 最新交易日无数据（停牌）
    cnt_not_a = 0          # 非A股
    cnt_mv = 0             # 市值不足
    cnt_short = 0          # 历史长度不足一个模板窗口
    cnt_filter = {name: 0 for name, _ in STOCK_FILTERS}  # 灵活过滤条件分别计数
    matched = 0

    result = []
    for code, recs in stock_data.items():
        # 最新交易日必须有数据
        if recs[-1]['trade_date'] != latest_date:
            cnt_no_latest += 1
            continue
        # 仅 A 股
        if not (code.endswith('.SH') or code.endswith('.SZ')):
            cnt_not_a += 1
            continue
        # 最新日总市值 >= 100亿
        latest_mv = recs[-1]['total_mv']
        if latest_mv < MIN_MARKET_CAP_WAN:
            cnt_mv += 1
            continue
        # 灵活过滤条件（STOCK_FILTERS），按首个未通过条件计数
        ok, reasons = apply_filters(recs)
        if not ok:
            cnt_filter[reasons[0]] += 1
            continue
        # 历史长度需 >= 模板窗口
        if len(recs) < seq_len:
            cnt_short += 1
            continue

        n = len(recs)
        # 形态结束日候选位置：从 seq_len-1 开始按步长滑动，且强制包含最新窗口
        end_positions = list(range(seq_len - 1, n, SLIDE_STEP))
        if end_positions[-1] != n - 1:
            end_positions.append(n - 1)
        # 仅扫描结束日在最近 RECENT_END_DAYS 个交易日内的窗口
        end_positions = [e for e in end_positions if e >= n - 1 - RECENT_END_DAYS]

        best = None
        for end_idx in end_positions:
            win = recs[end_idx - seq_len + 1: end_idx + 1]
            if any(r['ma5_calc'] is None or r['vol'] is None or r['vol'] <= 0
                   for r in win):
                continue

            ma_seq = normalize_series([r['ma5_calc'] for r in win])
            vol_seq = normalize_series([r['vol'] for r in win])
            total, sim_ma, sim_vol = window_similarity(tpl_ma, tpl_vol, ma_seq, vol_seq)

            # 多头结构惩罚：形态结束日 close>ma30 且 ma30 较5日前上行
            end_rec = recs[end_idx]
            ref_rec = recs[max(0, end_idx - 5)]
            bullish = (end_rec['ma30'] is not None and ref_rec['ma30'] is not None
                       and end_rec['close'] is not None
                       and end_rec['close'] > end_rec['ma30']
                       and end_rec['ma30'] > ref_rec['ma30'])
            if not bullish:
                total *= 0.3

            if best is None or total > best['total']:
                best = {
                    'total': total, 'sim_ma': sim_ma, 'sim_vol': sim_vol,
                    'start_idx': end_idx - seq_len + 1, 'end_idx': end_idx,
                    'bullish': bullish,
                }

        if best is None:
            cnt_short += 1
            continue
        matched += 1

        if best['total'] < MIN_SCORE:
            continue

        s, e = best['start_idx'], best['end_idx']
        result.append({
            'ts_code': code,
            'total_mv': latest_mv,
            'close': recs[-1]['close'],
            'score': round(best['total'], 4),
            'sim_ma': round(best['sim_ma'], 4),
            'sim_vol': round(best['sim_vol'], 4),
            'win_start': recs[s]['trade_date'],
            'win_end': recs[e]['trade_date'],
            'bullish': best['bullish'],
        })

    result.sort(key=lambda x: x['score'], reverse=True)

    print("\n" + "=" * 60)
    print(f"模板: {template_code}（{seq_len}日量价形态） | 滑动步长{SLIDE_STEP}日")
    print(f"评分: {WEIGHT_MA}×MA5相似度 + {WEIGHT_VOL}×成交量相似度（非多头结构×0.3）")
    print("-" * 40)
    print(f"  股票总数:                 {len(stock_data)}")
    print(f"  - 最新日停牌无数据:       {cnt_no_latest}")
    print(f"  - 非A股:                  {cnt_not_a}")
    print(f"  - 市值<100亿:             {cnt_mv}")
    for name, cnt in cnt_filter.items():
        print(f"  - 过滤[{name}]:      {cnt}")
    print(f"  - 窗口数据不足/缺失:      {cnt_short}")
    print("-" * 40)
    print(f"  完成扫描: {matched}，score>={MIN_SCORE}: {len(result)}")
    print("=" * 60)
    return result


def generate_csv(stocks, folder_path, template_code):
    csv_path = os.path.join(folder_path, f"{template_code}.csv")
    top = stocks[:TOP_N]
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '综合相似度', 'MA5相似度', '成交量相似度',
                         '形态起始日', '形态结束日', '多头结构', '总市值(万)', '最新收盘'])
        for s in top:
            writer.writerow([
                s['ts_code'], s['score'], s['sim_ma'], s['sim_vol'],
                s['win_start'], s['win_end'], '是' if s['bullish'] else '否(惩罚)',
                f"{s['total_mv']:.0f}", s['close'],
            ])
    print(f"✅ CSV已生成（Top {len(top)}）: {csv_path}")
    return csv_path


# ---------- 主入口 ----------

def main():
    global MIN_SCORE
    import argparse
    parser = argparse.ArgumentParser(description='量价形态相似选股（模板 + 滑动窗口DTW）')
    parser.add_argument('--code', default=TEMPLATE_CODE,
                        help=f'模板股票代码，默认{TEMPLATE_CODE}')
    parser.add_argument('--start', default=TEMPLATE_START,
                        help=f'模板起始日YYYYMMDD，默认{TEMPLATE_START}')
    parser.add_argument('--end', default=TEMPLATE_END,
                        help=f'模板结束日YYYYMMDD，默认{TEMPLATE_END}')
    parser.add_argument('--min-score', type=float, default=MIN_SCORE,
                        help=f'综合相似度阈值，默认{MIN_SCORE}')
    args = parser.parse_args()

    template_code = args.code.upper().strip()
    template_start, template_end = args.start, args.end
    MIN_SCORE = args.min_score

    target_date = get_target_date()
    print("=" * 80)
    print(f"🔍 量价形态相似选股 — 模板 {template_code} {template_start}~{template_end}")
    print("=" * 80)

    # 1. 加载模板
    tpl_ma, tpl_vol, tpl_records, seq_len = load_template(
        template_code, template_start, template_end)

    # 2. 候选数据区间（近 LOOKBACK_DAYS 个交易日）
    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    start_date, end_date = trade_dates[0], trade_dates[-1]
    print(f"\n📅 候选扫描区间: {start_date} ~ {end_date}（{len(trade_dates)}个交易日）")

    data = read_candidates(start_date, end_date)
    if not data:
        print("❌ 未获取到候选数据，退出")
        return

    # 3. 滑动窗口扫描
    result = scan_stocks(data, tpl_ma, tpl_vol, seq_len, end_date, template_code)

    if not result:
        print(f"\n⚠️ 没有综合相似度 >= {MIN_SCORE} 的股票，不生成文件")
        return

    # 4. 输出
    folder_path = get_folder_path()
    csv_path = generate_csv(result, folder_path, template_code)

    print("\n" + "=" * 80)
    print("🎉 量价形态相似选股完成！")
    print(f"📁 {folder_path}")
    print(f"📄 {csv_path}")
    print("=" * 80)

    print(f"\n🔥 相似度排名前{min(TOP_N, len(result))}：")
    for i, s in enumerate(result[:TOP_N], 1):
        mv_yi = s['total_mv'] / 10000
        print(f"{i:>2}. {s['ts_code']:<11} 综合={s['score']:.4f} "
              f"(MA5={s['sim_ma']:.3f} 量={s['sim_vol']:.3f}) "
              f"形态={s['win_start']}~{s['win_end']} "
              f"多头={'是' if s['bullish'] else '否'} 市值={mv_yi:.0f}亿")


if __name__ == "__main__":
    main()
