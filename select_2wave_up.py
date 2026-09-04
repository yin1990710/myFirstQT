#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
二浪启动选股策略 (select_2wave_up.py)

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
3. CSV 输出对齐 select_2wave_up_v2.py（csv.writer + utf-8-sig + 表头 股票代码）
4. 文件夹「2浪趋势+当日日期后缀」（已存在则复用）
"""

import os
import sys
import csv
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

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


def analyze_stocks(data):
    """逐只股票执行二浪启动选股逻辑"""

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
    cnt_need_more   = 0   # 交易天数不足60
    cnt_mv_under    = 0   # 最近交易日 total_mv < 100亿
    cnt_fail_peak   = 0   # 60日内无波峰
    cnt_fail_trough = 0   # 60日内无波谷
    cnt_fail_gap    = 0   # 无满足 T<=N-15 的(T,N)组合
    cnt_fail_npos   = 0   # 有T<=N-15组合但 N日 < A日-8日
    cnt_fail_vol    = 0   # 峰谷量能比 < 2
    cnt_fail_ratio  = 0   # N收盘/T收盘 >= 65%
    cnt_fail_ma5    = 0   # A-3~A日存在 close<=ma5

    for ts_code, records in stock_data.items():
        # 清理脏数据（close<=0），按日期排序
        records = [r for r in records if r['close'] and r['close'] > 0]
        if not records:
            continue
        records.sort(key=lambda x: x['trade_date'])

        # ---------- 数据天数 ----------
        if len(records) < 60:
            cnt_need_more += 1
            continue

        # ---------- 条件1：最近一个交易日总市值 > 100亿（1,000,000万元） ----------
        latest_mv = 0.0
        for r in reversed(records):
            if r['total_mv'] > 0:
                latest_mv = r['total_mv']
                break
        if latest_mv < 1000000:
            cnt_mv_under += 1
            continue

        last_60 = records[-60:]
        a_idx = len(last_60) - 1          # A 日下标
        n_min = a_idx - 8                 # 条件5：N日 > A日-8日 → 下标必须大于 n_min

        # ---------- 条件2：60日内出现过波峰（收集全部波峰下标） ----------
        peak_idxs = [i for i, r in enumerate(last_60) if r['turning_point'] == '波峰']
        if not peak_idxs:
            cnt_fail_peak += 1
            continue

        # ---------- 条件3：60日内出现过波谷（收集全部波谷下标） ----------
        trough_idxs = [i for i, r in enumerate(last_60) if r['turning_point'] == '波谷']
        if not trough_idxs:
            cnt_fail_trough += 1
            continue

        # ---------- 条件4/5/6/7：T<=N-15 且 N>A-8 且 量能比>=1.5 且 N收盘/T收盘<70% ----------
        matched = None
        for i_t in peak_idxs:
            for i_n in trough_idxs:
                if i_n - i_t < 15:          # 条件4：T <= N-15
                    continue
                if i_n <= n_min:            # 条件5：N日 > A日-8日
                    continue
                avg_t = avg_amount(last_60, i_t)          # T-5 ~ T（含）
                avg_n = avg_amount(last_60, i_n)          # N-5 ~ N（含）
                if avg_t is None or avg_n is None or avg_n <= 0:
                    continue
                if avg_t < avg_n * 1.5:                   # 条件7：量能比
                    continue
                t_close = last_60[i_t]['close']
                n_close = last_60[i_n]['close']
                if t_close <= 0 or (n_close / t_close) >= 0.70:   # 条件6：回调幅度
                    continue
                matched = {'i_t': i_t, 'i_n': i_n, 'avg_t': avg_t, 'avg_n': avg_n}
                break
            if matched:
                break

        if not matched:
            # 细分失败原因：依次检查 组合存在→量能→回调幅度
            has_gap_pair = any(i_n - i_t >= 15 for i_t in peak_idxs for i_n in trough_idxs)
            if not has_gap_pair:
                cnt_fail_gap += 1
                continue
            has_pos_pair = any(i_n - i_t >= 15 and i_n > n_min
                               for i_t in peak_idxs for i_n in trough_idxs)
            if not has_pos_pair:
                cnt_fail_npos += 1
                continue
            vol_ok = False
            ratio_ok = False
            for i_t in peak_idxs:
                for i_n in trough_idxs:
                    if i_n - i_t < 15 or i_n <= n_min:
                        continue
                    avg_t = avg_amount(last_60, i_t)
                    avg_n = avg_amount(last_60, i_n)
                    if not (avg_t and avg_n and avg_n > 0 and avg_t >= avg_n * 1.5):
                        continue
                    vol_ok = True
                    t_close = last_60[i_t]['close']
                    n_close = last_60[i_n]['close']
                    if t_close > 0 and (n_close / t_close) < 0.70:
                        ratio_ok = True
            if not vol_ok:
                cnt_fail_vol += 1
            elif not ratio_ok:
                cnt_fail_ratio += 1
            else:
                cnt_fail_ma5 += 1
            continue

        i_t = matched['i_t']
        i_n = matched['i_n']

        # ---------- 条件8：A-3日至A日（含，共4日）收盘价均高于ma5 ----------
        last_4 = last_60[-4:]
        ma5_ok = all(r['ma5'] is not None and r['close'] > r['ma5'] for r in last_4)
        if not ma5_ok:
            cnt_fail_ma5 += 1
            continue

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
    print("满足条件统计：")
    print(f"  股票总数量:                       {len(stock_data)}")
    print(f"  - 交易天数不足60过滤:             {cnt_need_more}")
    print(f"  - 最新日总市值<100亿过滤:         {cnt_mv_under}")
    print(f"  - 60日内无波峰 淘汰:              {cnt_fail_peak}")
    print(f"  - 60日内无波谷 淘汰:              {cnt_fail_trough}")
    print(f"  - 无T<=N-15组合 淘汰:             {cnt_fail_gap}")
    print(f"  - N日<=A-8日 淘汰:                {cnt_fail_npos}")
    print(f"  - 峰谷量能比<1.5 淘汰:             {cnt_fail_vol}")
    print(f"  - N收盘/T收盘>=70% 淘汰:          {cnt_fail_ratio}")
    print(f"  - A-3~A日close≤ma5 淘汰:          {cnt_fail_ma5}")
    print("-" * 40)
    print(f"  最终选出: {len(result)}")
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
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
