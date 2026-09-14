#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
高换手率选股策略 (select_high_exchange.py)

选股条件（对齐 prompt#L302-315）：
1. 读取 stock_daily_t + stock_daily_basic_info_t 最近 10 个交易日，按 ts_code + trade_date 关联
2. 去除最近一个交易日总市值 total_mv < 100亿（1,000,000 万元）的股票
3. 最近 10 个交易日内至少有 5 个交易日的换手率 turnover_rate_f 满足：
   - 100亿~300亿  → 换手率 > 15%
   - 300亿~500亿  → 换手率 > 10%
   - 500亿~800亿  → 换手率 > 8%
   - >800亿       → 换手率 > 6%
4. 最近 2 个交易日 close > ma5 且 ma5 > ma30
5. 去除非 A 股（仅保留 .SH / .SZ）
6. CSV 输出对齐 select_2wave_up_v2.py（csv.writer + utf-8-sig + 表头 股票代码）
7. 文件夹「高换手+当日日期后缀」（已存在则删除重建）
"""

import os
import sys
import csv
import shutil
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection
from insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


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
             - timedelta(days=n * 2 + 10)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) > n:
        opens = opens[-n:]
    return opens


def create_folder(target_date):
    """新建（已存在的删除重建）高换手+日期后缀的文件夹"""
    folder_name = f"高换手{target_date}"
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


def get_turnover_threshold(total_mv_wan):
    """
    根据最新日总市值（万元）返回换手率阈值（%）
    市值分档：100~300亿 → 15%，300~500亿 → 10%，500~800亿 → 8%，>800亿 → 6%
    """
    if total_mv_wan < 1000000:         # < 100亿
        return None
    elif total_mv_wan < 3000000:       # 100~300亿
        return 15.0
    elif total_mv_wan < 5000000:       # 300~500亿
        return 10.0
    elif total_mv_wan < 8000000:       # 500~800亿
        return 8.0
    else:                               # > 800亿
        return 6.0


# ---------- 核心逻辑 ----------

def read_stock_data(start_date, end_date):
    """
    读取 stock_daily_t + stock_daily_basic_info_t 在 [start_date, end_date] 区间，
    LEFT JOIN 按 ts_code + trade_date，获取 close/ma5/ma30/total_mv/turnover_rate_f
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []

    query_sql = """
        SELECT
            d.ts_code,
            d.trade_date,
            d.open,
            d.close,
            d.ma5,
            d.ma30,
            b.total_mv,
            b.turnover_rate_f
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


def analyze_stocks(data):
    """逐只股票执行选股逻辑"""

    # ---------- 组装 ----------
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        total_mv = record['total_mv']
        total_mv = float(total_mv) if total_mv is not None else 0.0
        turnover = record['turnover_rate_f']
        turnover = float(turnover) if turnover is not None else None
        stock_data[ts_code].append({
            'trade_date':      record['trade_date'],
            'open':            float(record['open']  or 0),
            'close':           float(record['close'] or 0),
            'ma5':             float(record['ma5']   or 0),
            'ma30':            float(record['ma30']  or 0),
            'total_mv':        total_mv,
            'turnover_rate_f': turnover,
        })

    result = []
    cnt_not_a       = 0   # 非A股
    cnt_mv_under     = 0   # total_mv < 100亿
    cnt_need_more    = 0   # 交易天数不足10
    cnt_fail_tr      = 0   # 高换手天数不足5
    cnt_fail_ma      = 0   # 近2日 close>ma5>ma30 失败

    for ts_code, records in stock_data.items():

        # ---------- 非A股过滤 ----------
        if not (ts_code.endswith('.SH') or ts_code.endswith('.SZ')):
            cnt_not_a += 1
            continue

        # 清理停牌等脏数据（close<=0），按日期排序
        records = [r for r in records if r['close'] > 0]
        if not records:
            continue
        records.sort(key=lambda x: x['trade_date'])

        # ---------- total_mv：取最新有值的交易日 ----------
        latest_mv = 0.0
        for r in reversed(records):
            if r['total_mv'] > 0:
                latest_mv = r['total_mv']
                break

        if latest_mv < 1000000:          # < 100亿（万元）
            cnt_mv_under += 1
            continue

        # ---------- 数据天数 ----------
        if len(records) < 10:
            cnt_need_more += 1
            continue

        # ---------- 条件3：近10日至少5日换手率达标 ----------
        threshold = get_turnover_threshold(latest_mv)
        if threshold is None:
            cnt_mv_under += 1
            continue

        last_10 = records[-10:]
        hit_days = 0
        for r in last_10:
            tr = r['turnover_rate_f']
            if tr is not None and tr > threshold:
                hit_days += 1

        if hit_days < 5:
            cnt_fail_tr += 1
            continue

        # ---------- 条件4：近2日 close > ma5 且 ma5 > ma30 ----------
        last_2 = records[-2:]
        ma_ok = all(
            r['close'] > 0 and r['ma5'] > 0 and r['ma30'] > 0
            and r['close'] > r['ma5'] and r['ma5'] > r['ma30']
            for r in last_2
        )
        if not ma_ok:
            cnt_fail_ma += 1
            continue

        result.append({
            'ts_code':     ts_code,
            'close':       records[-1]['close'],
            'ma5':         records[-1]['ma5'],
            'ma30':        records[-1]['ma30'],
            'total_mv':    latest_mv,
            'threshold':   threshold,
            'hit_days':    hit_days,
        })

    # 按市值从大到小排序
    result.sort(key=lambda x: x['total_mv'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"  股票总数量:                      {len(stock_data)}")
    print(f"  - 非A股过滤:                     {cnt_not_a}")
    print(f"  - 最新日总市值<100亿过滤:         {cnt_mv_under}")
    print(f"  - 交易天数不足10过滤:            {cnt_need_more}")
    print(f"  - 近10日高换手天数<5 淘汰:       {cnt_fail_tr}")
    print(f"  - 近2日close≤ma5或ma5≤ma30 淘汰: {cnt_fail_ma}")
    print("-" * 40)
    print(f"  最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    """CSV 输出对齐 select_2wave_up_v2.py：csv.writer + utf-8-sig + 表头 股票代码"""
    csv_filename = "高换手.csv"
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
    print("📊 高换手率选股策略 (近10日至少5日换手达标+近2日close>ma5>ma30)")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 基础过滤：A股上市，最新日总市值 > 100亿")
    print("  2. 近10日至少5日换手率达标（按市值分档：15%/10%/8%/6%）")
    print("  3. 近2日 close > ma5 且 ma5 > ma30")
    print("=" * 80)

    # ---------- 步骤A：获取最近 10 个交易日 ----------
    print(f"\n📅 目标日期: {target_date}")
    trade_dates = get_last_n_trade_dates(target_date, 10)
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
        folder_path = create_folder(target_date)
        csv_path = generate_csv_file(selected, folder_path)
        print("\n" + "=" * 80)
        print("🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n🔥 精选股票（按市值降序，前20只）：")
        for i, s in enumerate(selected[:20], 1):
            print(
                f"{i:>2}. {s['ts_code']:<11} "
                f"收{s['close']:>8.2f} MA5{s['ma5']:>8.2f} MA30{s['ma30']:>8.2f} | "
                f"市值{s['total_mv']/10000:>8.1f}亿 | "
                f"阈值{s['threshold']:>4.0f}% 达标{s['hit_days']:>2}天"
            )

        # 选股结果入库（便于回测）
        record_selected_stocks(
            'high_exchange',
            [{'ts_code': s['ts_code'], 'selected': 1} for s in selected],
            end_date)
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
