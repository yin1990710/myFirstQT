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
    folder_name = f"当日涨停{target_date}"
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
        d.close,
        d.pre_close,
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
            'close': float(record['close'] or 0),
            'pre_close': float(record['pre_close'] or 0)
        })

    result = []

    for ts_code, info in stock_data.items():
        if info['total_mv'] <= 10000000000:
            continue

        records = info['records']
        records.sort(key=lambda x: x['trade_date'])

        if len(records) < 2:
            continue

        latest_record = records[-1]
        close = latest_record['close']
        pre_close = latest_record['pre_close']

        if pre_close <= 0:
            continue

        pct_chg = (close - pre_close) / pre_close * 100

        if pct_chg <= 9.5:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': info['stock_name'],
            'pct_chg': pct_chg
        })

    result.sort(key=lambda x: x['pct_chg'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "limitup_1d.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '涨幅'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'], f"{stock['pct_chg']:.2f}%"])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("当日涨停选股策略")
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
            print(f"• {stock['ts_code']} - {stock['stock_name']} ({stock['pct_chg']:.2f}%)")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()