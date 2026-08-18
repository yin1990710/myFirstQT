#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import csv
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
    folder_name = f"低幅震荡{target_date}"
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

def read_stock_data(days=10):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 15)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.high,
        d.low,
        d.amount,
        i.stock_name
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

def analyze_stocks(data):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'open': float(record['open'] or 0),
            'close': float(record['close'] or 0),
            'high': float(record['high'] or 0),
            'low': float(record['low'] or 0),
            'amount': float(record['amount'] or 0),
            'stock_name': record['stock_name'] or ''
        })

    result = []

    for ts_code, records in stock_data.items():
        if len(records) < 10:
            continue

        records.sort(key=lambda x: x['trade_date'])
        last_10_days = records[-10:]

        if len(last_10_days) < 10:
            continue

        up_days_amount = []
        down_days_amount = []
        all_high = []
        all_low = []

        for r in last_10_days:
            if r['open'] < r['close']:
                if r['amount'] * 1000 <= 500000000:
                    up_days_amount = None
                    break
                up_days_amount.append(r['amount'])
            elif r['open'] > r['close']:
                down_days_amount.append(r['amount'])
            all_high.append(r['high'])
            all_low.append(r['low'])

        if up_days_amount is None:
            continue

        if len(up_days_amount) < 4:
            continue

        if len(down_days_amount) == 0:
            continue

        avg_up_amount = sum(up_days_amount) / len(up_days_amount)
        avg_down_amount = sum(down_days_amount) / len(down_days_amount)

        if avg_down_amount == 0:
            continue

        if avg_up_amount <= avg_down_amount * 1.5:
            continue

        max_high = max(all_high)
        min_low = min(all_low)

        if min_low <= 0:
            continue

        high_low_pct = (max_high - min_low) / min_low * 100

        if high_low_pct >= 15:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': last_10_days[-1]['stock_name']
        })

    result.sort(key=lambda x: x['ts_code'])

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "lowwave_10d.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name']])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("低幅波动选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data(days=10)

    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    selected_stocks = analyze_stocks(data)

    print(f"\n✅ 共选出 {len(selected_stocks)} 只满足条件的股票")

    if selected_stocks:
        csv_path = generate_csv_file(selected_stocks, folder_path)
        print("\n" + "=" * 80)
        print(f"🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        for stock in selected_stocks:
            print(f"• {stock['ts_code']} - {stock['stock_name']}")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()