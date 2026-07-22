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
    folder_name = f"高换手率{target_date}"
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
        d.amount,
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
            stock_data[ts_code] = {
                'records': [],
                'stock_name': record['stock_name'] or '',
                'total_mv': float(record['total_mv'] or 0)
            }
        stock_data[ts_code]['records'].append({
            'trade_date': record['trade_date'],
            'amount': float(record['amount'] or 0),
            'close': float(record['close'] or 0)
        })

    result = []

    for ts_code, info in stock_data.items():
        if info['total_mv'] <= 10000000000:
            continue

        records = info['records']
        records.sort(key=lambda x: x['trade_date'])
        last_10_days = records[-10:]

        if len(last_10_days) < 10:
            continue

        total_turnover = 0
        for r in last_10_days:
            if info['total_mv'] > 0:
                turnover_rate = (r['amount'] * 1000) / info['total_mv'] * 100
                total_turnover += turnover_rate

        if total_turnover <= 80:
            continue

        first_close = last_10_days[0]['close']
        last_close = last_10_days[-1]['close']
        if first_close <= 0 or last_close <= first_close:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': info['stock_name'],
            'total_turnover': round(total_turnover, 2),
            'price_change': round((last_close - first_close) / first_close * 100, 2)
        })

    result.sort(key=lambda x: x['total_turnover'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "high_turnover_10d.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '10日累计换手率(%)', '10日涨幅(%)'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'], stock['total_turnover'], stock['price_change']])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("高换手率选股策略")
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

        for stock in selected_stocks[:10]:
            print(f"• {stock['ts_code']} - {stock['stock_name']} - 10日累计换手率: {stock['total_turnover']}% - 10日涨幅: {stock['price_change']}%")
        
        if len(selected_stocks) > 10:
            print(f"  ... 还有 {len(selected_stocks) - 10} 只股票")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()