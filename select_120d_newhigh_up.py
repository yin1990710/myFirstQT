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
    current_hour = now.hour
    if current_hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date


def get_folder_name():
    target_date = get_target_date()
    folder_name = f"120区间突破{target_date}"
    return folder_name


def create_folder():
    folder_name = get_folder_name()
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
    start_date = (datetime.now() - timedelta(days=300)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
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

        if not results:
            cursor.execute("SELECT MAX(trade_date) as max_date FROM stock_daily_t")
            db_max_date = cursor.fetchone()['max_date']
            print(f"⚠️ 目标日期{target_date}无数据，使用数据库最新日期{db_max_date}")
            target_date = db_max_date
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()

        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)


def analyze_stocks(data):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] else None,
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_mv = 0
    count_range = 0
    count_breakout = 0

    for ts_code, records in stock_data.items():
        if len(records) < 121:
            continue

        records.sort(key=lambda x: x['trade_date'])

        latest = records[-1]
        stock_name = latest['name']

        if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH')):
            continue

        total_mv = latest['total_mv']
        if total_mv < 10000000000:
            continue

        count_total += 1
        count_mv += 1

        T_close = latest['close']
        if T_close is None:
            continue

        if len(records) < 121:
            continue

        range_records = records[-121:-1]

        prices = []
        for r in range_records:
            close = r['close']
            if close is not None:
                prices.append(close)

        if len(prices) < 2:
            continue

        min_price = min(prices)
        max_price = max(prices)

        if max_price == 0:
            continue

        ratio = min_price / max_price
        if ratio <= 0.75:
            continue

        count_range += 1

        if T_close <= max_price:
            continue

        if len(records) < 2:
            continue

        prev_close = records[-2]['close']
        if prev_close is None or prev_close <= 0:
            continue

        gain = (T_close - prev_close) / prev_close * 100
        if gain <= 5:
            continue

        count_breakout += 1

        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv,
            'T_close': T_close,
            'range_max': max_price,
            'range_min': min_price,
            'range_ratio': ratio * 100,
            'gain': gain
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件a(市值>100亿): {count_mv}")
    print(f"满足条件a+b(区间最低/最高>75%): {count_range}")
    print(f"满足条件a+b+c(T日突破区间且涨幅>5%): {count_breakout}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "120区间突破.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for stock in stocks:
            writer.writerow([stock['ts_code']])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("📈 120区间突破策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  a. 总市值 > 100亿")
    print("  b. T-120至T-1日最低/最高收盘价 > 75%")
    print("  c. T日收盘价突破T-120至T-1日最高价，且涨幅>5%")
    print("  d. 仅保留A股股票（.SZ/.SH）")
    print("=" * 80)

    folder_path = create_folder()

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
        print(f"📄 CSV路径: {folder_path}/120区间突破.csv")
        print("=" * 80)

        print("\n🔥 精选股票：")
        for i, stock in enumerate(stocks[:10], 1):
            mv_billion = stock['total_mv'] / 100000000
            print(f"{i}. {stock['ts_code']} {stock['name']}")
            print(f"   └─ 市值: {mv_billion:.2f}亿")
            print(f"   └─ T日收盘价: {stock['T_close']:.2f}")
            print(f"   └─ 区间最高价: {stock['range_max']:.2f}")
            print(f"   └─ 区间最低/最高: {stock['range_ratio']:.2f}%")
            print(f"   └─ T日涨幅: {stock['gain']:.2f}%")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()