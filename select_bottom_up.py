#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
import shutil
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_folder_path():
    folder_name = f"底部企稳{get_target_date()}"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    # 220个交易日约需要320个自然日
    start_date = (datetime.now() - timedelta(days=320)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.amount,
        d.ma5,
        d.ma30,
        i.stock_name,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date >= %s AND d.trade_date <= %s
    ORDER BY d.ts_code, d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)


def check_price_range(records):
    """最近200个交易日内，最低收盘价/最高收盘价 > 50%，且220日内最低收盘价在最近20日内"""
    if len(records) < 200:
        return False

    recent_200 = records[-200:]
    closes_200 = [r['close'] for r in recent_200 if r['close'] is not None]
    if len(closes_200) < 2:
        return False
    min_200 = min(closes_200)
    max_200 = max(closes_200)
    if max_200 == 0:
        return False
    if min_200 / max_200 <= 0.5:
        return False

    # 220日内最低收盘价在最近20日内
    recent_220 = records[-220:] if len(records) >= 220 else records
    closes_220 = [(i, r['close']) for i, r in enumerate(recent_220) if r['close'] is not None]
    if not closes_220:
        return False
    min_idx_220 = min(closes_220, key=lambda x: x[1])[0]
    # recent_220 的索引转换为 records 的索引
    offset = len(records) - len(recent_220)
    min_idx_in_records = offset + min_idx_220
    # 检查最低点是否在最近20个交易日内
    if min_idx_in_records < len(records) - 20:
        return False

    return True


def check_gain_10d(records):
    """最近10个交易日至少有1日涨幅>8%"""
    if len(records) < 11:
        return False
    recent_10 = records[-10:]
    for i in range(len(recent_10)):
        prev_close = recent_10[i - 1]['close'] if i == 0 else recent_10[i]['close']
        # i=0时需要取前一天的close
        if i == 0:
            prev_idx = len(records) - 11
            if prev_idx < 0:
                continue
            prev_close = records[prev_idx]['close']
            curr_close = recent_10[i]['close']
        else:
            prev_close = recent_10[i - 1]['close']
            curr_close = recent_10[i]['close']

        if prev_close and curr_close and prev_close > 0:
            gain = (curr_close - prev_close) / prev_close
            if gain > 0.08:
                return True
    return False


def check_close_above_ma5_3d(records):
    """最近3个交易日收盘价>ma5"""
    if len(records) < 3:
        return False
    for r in records[-3:]:
        close = float(r['close']) if r['close'] else None
        ma5 = float(r['ma5']) if r['ma5'] else None
        if close is None or ma5 is None:
            return False
        if close <= ma5:
            return False
    return True


def check_ma30_increasing_3d(records):
    """最近3个交易日ma30单调递增"""
    if len(records) < 3:
        return False
    recent_3 = records[-3:]
    for i in range(len(recent_3) - 1):
        ma30_1 = float(recent_3[i]['ma30']) if recent_3[i]['ma30'] else None
        ma30_2 = float(recent_3[i + 1]['ma30']) if recent_3[i + 1]['ma30'] else None
        if ma30_1 is None or ma30_2 is None:
            return False
        if ma30_2 <= ma30_1:
            return False
    return True


def analyze_stocks(data):
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] else None,
            'amount': float(record['amount']) if record['amount'] else None,
            'ma5': float(record['ma5']) if record['ma5'] else None,
            'ma30': float(record['ma30']) if record['ma30'] else None,
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_mv = 0
    count_range = 0
    count_gain = 0
    count_ma5 = 0
    count_ma30 = 0

    for ts_code, records in stock_data.items():
        if len(records) < 200:
            continue

        records.sort(key=lambda x: x['trade_date'])
        latest = records[-1]
        stock_name = latest['name']

        # 去除非A股
        if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH')):
            continue

        # 去除ST股
        if 'ST' in stock_name:
            continue

        count_total += 1

        # 条件2：总市值 > 100亿
        total_mv = latest['total_mv']
        if total_mv < 10000000000:
            continue
        count_mv += 1

        # 条件3：价格范围 + 最低点位置
        if not check_price_range(records):
            continue
        count_range += 1

        # 条件4：最近10日至少1日涨幅>8%
        if not check_gain_10d(records):
            continue
        count_gain += 1

        # 条件5：最近3日close>ma5
        if not check_close_above_ma5_3d(records):
            continue
        count_ma5 += 1

        # 条件6：最近3日ma30单调递增
        if not check_ma30_increasing_3d(records):
            continue
        count_ma30 += 1

        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件(市值>100亿): {count_mv}")
    print(f"满足条件(价格范围+最低点位置): {count_range}")
    print(f"满足条件(10日涨幅>8%): {count_gain}")
    print(f"满足条件(close>ma5): {count_ma5}")
    print(f"满足条件(ma30递增): {count_ma30}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "底部企稳.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['name']])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("📊 底部企稳选股策略")
    print("=" * 80)
    print("\n选股逻辑：")
    print("  1. 总市值 > 100亿")
    print("  2. 最近200日 最低/最高收盘价 > 50%，且220日最低点在最近20日内")
    print("  3. 最近10日至少1日涨幅 > 8%")
    print("  4. 最近3日收盘价 > MA5")
    print("  5. 最近3日MA30单调递增")
    print("  6. 去除非A股股票")
    print("=" * 80)

    folder_path = get_folder_path()

    data = read_stock_data()
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    stocks = analyze_stocks(data)

    if stocks:
        generate_csv_file(stocks, folder_path)
        print("\n" + "=" * 80)
        print("🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {folder_path}/底部企稳.csv")
        print("=" * 80)
        print("\n🔥 精选股票：")
        for i, stock in enumerate(stocks[:10], 1):
            mv_billion = stock['total_mv'] / 100000000
            print(f"{i}. {stock['ts_code']} {stock['name']} (市值: {mv_billion:.2f}亿)")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()
