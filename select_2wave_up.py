#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: 二浪启动选股策略

选股条件（对齐 prompt#L373-384）：
1. 读取 stock_daily_t + stock_daily_basic_info_t 最近 60 个交易日（LEFT JOIN 按 ts_code + trade_date）
   记最近一个交易日为 A 日
2. 选股条件：
   1. 最近一个交易日总市值 total_mv（万元）> 100亿（1,000,000 万元）
   2. 最近 60 个交易日 turning_point 出现过「波峰」，记波峰日期为 T 日
   3. 最近 60 个交易日 turning_point 出现过「波谷」，记波谷日期为 N 日
   4. T 日 <= N 日 - 15 日（N 日至少在 T 日后 15 个交易日）
   5. N 日 > A 日 - 8 日（波谷出现在最近 8 个交易日内）
   6. N 日收盘价 / T 日收盘价 < 70%
   7. T-5 日至 T 日（含）的平均成交额 >= N-5 日至 N 日（含）平均成交额的 1.5 倍
   8. A-3 日至 A 日（含，共 4 日）收盘价均高于 ma5

过滤条件采用 STOCK_FILTERS 注册表方式（参考 find_similar_ma5.py），
新增条件只需追加一个 _filter_xxx 函数与一行注册；apply_filters 统一执行并返回未通过原因。
3. CSV 输出对齐 select_2wave_up_v2.py（csv.writer + utf-8-sig + 表头 股票代码）
4. 文件夹「2浪趋势+当日日期后缀」（已存在则复用）
"""

import os
import sys
import csv
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    """返回 target_date 前（含）最近 n 个交易日列表，预留日历日缓冲。"""
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 10)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) > n:
        opens = opens[-n:]
    return opens


def get_folder_name():
    return f"2浪趋势{get_target_date()}"


def get_folder_path():
    """文件夹「2浪趋势+日期后缀」，已存在则复用"""
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


# ---------- 核心逻辑 ----------

def read_stock_data(start_date, end_date):
    """
    读取 stock_daily_t + stock_daily_basic_info_t 在 [start_date, end_date] 区间，
    LEFT JOIN 按 ts_code + trade_date
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []

    query_sql = """
        SELECT
            d.ts_code,
            d.trade_date,
            d.close,
            d.amount,
            d.ma5,
            d.ma30,
            d.turning_point,
            b.total_mv
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code  = b.ts_code
              AND d.trade_date = b.trade_date
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


def avg_amount(records, idx_end, window=5):
    """[idx_end-window, idx_end] 闭区间的平均成交额，越界自动截断；无有效数据返回 None"""
    start_idx = max(0, idx_end - window + 1)
    vals = [float(records[i]['amount']) for i in range(start_idx, idx_end + 1)
            if records[i]['amount'] is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# ---------- 灵活过滤条件（新增条件只需在 STOCK_FILTERS 中追加一行） ----------
MIN_BARS = 60                  # 近60个交易日
MIN_MARKET_CAP_WAN = 1_000_000 # 最新日总市值下限（万元）= 100亿
PEAK_TROUGH_MIN_GAP = 15       # T日 <= N日-15日：波谷至少在波峰后15个交易日
TROUGH_RECENT_DAYS = 8         # N日 > A日-8日：波谷须出现在最近8个交易日内
MIN_VOL_RATIO = 1.5            # T-5~T日均额 >= N-5~N日均额 × 1.5
MAX_CLOSE_RATIO = 0.70         # N日收盘 / T日收盘 < 70%
ABOVE_MA5_DAYS = 4             # A-3~A日（共4日）收盘价均高于ma5


def _peak_trough_idxs(window):
    """返回近60日窗口内波峰/波谷下标列表。"""
    peaks = [i for i, r in enumerate(window) if r['turning_point'] == '波峰']
    troughs = [i for i, r in enumerate(window) if r['turning_point'] == '波谷']
    return peaks, troughs


def _iter_gap_recent_pairs(window):
    """生成满足 条件4(T<=N-15) 与 条件5(N>A-8) 的 (i_t, i_n) 候选组合。"""
    peaks, troughs = _peak_trough_idxs(window)
    n_min = len(window) - 1 - TROUGH_RECENT_DAYS
    for i_t in peaks:
        for i_n in troughs:
            if i_n - i_t >= PEAK_TROUGH_MIN_GAP and i_n > n_min:
                yield i_t, i_n


def find_qualified_pair(window):
    """在近60日窗口内寻找第一组同时满足 条件4/5/6/7 的峰谷组合，返回 dict 或 None。

    与原嵌套循环口径一致：T<=N-15、N>A-8、T-5~T日均额≥N-5~N日均额×1.5、
    N日收盘/T日收盘<70%。
    """
    for i_t, i_n in _iter_gap_recent_pairs(window):
        avg_t = avg_amount(window, i_t)
        avg_n = avg_amount(window, i_n)
        if avg_t is None or avg_n is None or avg_n <= 0:
            continue
        if avg_t < avg_n * MIN_VOL_RATIO:
            continue
        t_close = window[i_t]['close']
        n_close = window[i_n]['close']
        if not t_close or t_close <= 0 or (n_close / t_close) >= MAX_CLOSE_RATIO:
            continue
        return {'i_t': i_t, 'i_n': i_n, 'avg_t': avg_t, 'avg_n': avg_n}
    return None


def _filter_enough_bars(records):
    """有效交易日不少于60日。"""
    return len(records) >= MIN_BARS


def _filter_min_market_cap(records):
    """最近一个有市值记录的交易日总市值 ≥ 100亿。"""
    for r in reversed(records):
        if r['total_mv'] > 0:
            return r['total_mv'] >= MIN_MARKET_CAP_WAN
    return False


def _filter_has_peak(records):
    """近60个交易日 turning_point 出现过波峰。"""
    if len(records) < MIN_BARS:
        return False
    peaks, _ = _peak_trough_idxs(records[-MIN_BARS:])
    return bool(peaks)


def _filter_has_trough(records):
    """近60个交易日 turning_point 出现过波谷。"""
    if len(records) < MIN_BARS:
        return False
    _, troughs = _peak_trough_idxs(records[-MIN_BARS:])
    return bool(troughs)


def _filter_gap_pair(records):
    """存在 T<=N-15 的波峰波谷组合（不强制波谷位置）。"""
    if len(records) < MIN_BARS:
        return False
    window = records[-MIN_BARS:]
    peaks, troughs = _peak_trough_idxs(window)
    return any(i_n - i_t >= PEAK_TROUGH_MIN_GAP for i_t in peaks for i_n in troughs)


def _filter_trough_recent(records):
    """存在 T<=N-15 且 N>A-8（波谷在最近8个交易日内）的组合。"""
    if len(records) < MIN_BARS:
        return False
    # _iter_gap_recent_pairs 已同时包含间隔与位置两个条件
    return next(_iter_gap_recent_pairs(records[-MIN_BARS:]), None) is not None


def _filter_volume_expand(records):
    """存在满足间隔/位置且 T-5~T日均额 ≥ N-5~N日均额×1.5 的组合。"""
    if len(records) < MIN_BARS:
        return False
    window = records[-MIN_BARS:]
    for i_t, i_n in _iter_gap_recent_pairs(window):
        avg_t = avg_amount(window, i_t)
        avg_n = avg_amount(window, i_n)
        if avg_t is not None and avg_n is not None and avg_n > 0 and avg_t >= avg_n * MIN_VOL_RATIO:
            return True
    return False


def _filter_deep_pullback(records):
    """存在满足全部结构条件（含 N收盘/T收盘<70%）的峰谷组合。"""
    if len(records) < MIN_BARS:
        return False
    return find_qualified_pair(records[-MIN_BARS:]) is not None


def _filter_above_ma5(records):
    """A-3日至A日（共4日）收盘价均高于ma5。"""
    if len(records) < ABOVE_MA5_DAYS:
        return False
    return all(r['ma5'] is not None and r['close'] is not None and r['close'] > r['ma5']
               for r in records[-ABOVE_MA5_DAYS:])


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)；函数入参为该股票记录（按日期升序、已剔除close<=0脏数据），返回 True=通过
STOCK_FILTERS = [
    (f'有效交易日不足{MIN_BARS}日', _filter_enough_bars),
    (f'最新日总市值<{MIN_MARKET_CAP_WAN/10000:.0f}亿', _filter_min_market_cap),
    (f'近{MIN_BARS}日无波峰', _filter_has_peak),
    (f'近{MIN_BARS}日无波谷', _filter_has_trough),
    (f'无T<=N-{PEAK_TROUGH_MIN_GAP}峰谷组合', _filter_gap_pair),
    (f'波谷N不在最近{TROUGH_RECENT_DAYS}个交易日内', _filter_trough_recent),
    (f'峰谷量能比(T/N均额)<{MIN_VOL_RATIO:g}', _filter_volume_expand),
    (f'N收盘/T收盘>={MAX_CLOSE_RATIO*100:.0f}%', _filter_deep_pullback),
    (f'近{ABOVE_MA5_DAYS}日存在close<=ma5', _filter_above_ma5),
]


def apply_filters(records):
    """依次执行 STOCK_FILTERS 中的全部过滤条件，返回 (是否通过, 未通过的条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(records)]
    return (len(reasons) == 0), reasons


def analyze_stocks(data):
    """逐只股票执行二浪启动选股逻辑（STOCK_FILTERS 注册式过滤）"""

    # ---------- 组装 ----------
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date':    record['trade_date'],
            'close':         float(record['close']) if record['close'] else None,
            'amount':        float(record['amount']) if record['amount'] else None,
            'ma5':           float(record['ma5']) if record['ma5'] else None,
            'turning_point': record['turning_point'],
            'total_mv':      float(record['total_mv']) if record['total_mv'] else 0.0,
        })

    result = []
    count_empty = 0      # 清理脏数据后无有效行情
    cnt_filtered = {}    # 各过滤条件淘汰数（一只股票可同时计入多个条件）

    for ts_code, records in stock_data.items():
        # 清理脏数据（close<=0），按日期排序
        records = [r for r in records if r['close'] and r['close'] > 0]
        if not records:
            count_empty += 1
            continue
        records.sort(key=lambda x: x['trade_date'])

        # 注册式过滤：统一执行全部条件，返回未通过原因列表
        ok, reasons = apply_filters(records)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
            continue

        # 取最终命中的峰谷组合用于结果明细（_filter_deep_pullback 已保证存在）
        last_60 = records[-MIN_BARS:]
        matched = find_qualified_pair(last_60)
        i_t, i_n = matched['i_t'], matched['i_n']
        t_close = last_60[i_t]['close']
        n_close = last_60[i_n]['close']

        result.append({
            'ts_code':     ts_code,
            'close':       last_60[-1]['close'],
            'ma5':         last_60[-1]['ma5'],
            'total_mv':    last_60[-1]['total_mv'],
            'T_date':      last_60[i_t]['trade_date'],
            'N_date':      last_60[i_n]['trade_date'],
            'vol_ratio':   matched['avg_t'] / matched['avg_n'] if matched['avg_n'] else 0,
            'close_ratio': n_close / t_close * 100 if t_close > 0 else 0,
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print("二浪启动选股策略 · 过滤漏斗")
    print("-" * 40)
    print(f"  股票总数量:                 {len(stock_data)}")
    if count_empty:
        print(f"  - 无有效行情(close<=0) 淘汰: {count_empty}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print("-" * 40)
    print(f"  最终选出:                   {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    """CSV 输出对齐 select_2wave_up_v2.py：csv.writer + utf-8-sig + 表头 股票代码"""
    csv_filename = "二浪趋势.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for stock in stocks:
            writer.writerow([stock['ts_code']])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


# ---------- 主入口 ----------

def main():
    target_date = get_target_date()

    print("=" * 80)
    print("🌊 二浪启动选股策略 (波峰放量→波谷缩量→回升)")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 最近一个交易日总市值 > 100亿")
    print("  2. 最近60个交易日 turning_point 出现过波峰(T日)和波谷(N日)")
    print("  3. T日 <= N日-15日")
    print("  4. N日 > A日-8日")
    print("  5. N日收盘价/T日收盘价 < 70%")
    print("  6. T-5~T日平均成交额 >= N-5~N日平均成交额的1.5倍")
    print("  7. A-3日至A日收盘价均高于ma5")
    print("=" * 80)

    # ---------- 步骤A：获取最近 60 个交易日 ----------
    print(f"\n📅 目标日期: {target_date}")
    trade_dates = get_last_n_trade_dates(target_date, 60)
    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    print(f"   查询区间: {start_date} ~ {end_date}（共{len(trade_dates)}个交易日）")

    # ---------- 步骤B：读取 ----------
    data = read_stock_data(start_date, end_date)
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    # ---------- 步骤C：选股 ----------
    selected = analyze_stocks(data)
    print(f"\n✅ 共选出 {len(selected)} 只满足条件的股票")

    if selected:
        folder_path = get_folder_path()
        csv_path = generate_csv_file(selected, folder_path)
        print("\n" + "=" * 80)
        print("🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n🔥 精选股票（按市值降序，前20只）：")
        for i, s in enumerate(selected[:20], 1):
            mv_txt = f"{s['total_mv']/10000:.0f}亿" if s['total_mv'] > 0 else "N/A"
            ma5_txt = f"{s['ma5']:.2f}" if s['ma5'] is not None else "N/A"
            print(
                f"{i:>2}. {s['ts_code']:<11} "
                f"收{s['close']:>8.2f} MA5{ma5_txt:>8} | 市值{mv_txt:>10} | "
                f"T={s['T_date']} N={s['N_date']} | "
                f"量比{s['vol_ratio']:>5.2f} 回调{s['close_ratio']:>5.1f}%"
            )

        # 选股结果入库（便于回测）
        record_selected_stocks(
            '二浪启动选股策略',
            [{'ts_code': s['ts_code'], 'selected': 1} for s in selected],
            end_date)
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
