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
    folder_name = f"均线上升{target_date}"
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

def read_stock_data(days=30):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 20)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.high,
        d.low,
        d.amount,
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
            'open': float(record['open'] or 0),
            'close': float(record['close'] or 0),
            'high': float(record['high'] or 0),
            'low': float(record['low'] or 0),
            'amount': float(record['amount'] or 0)
        })

    result = []

    for ts_code, info in stock_data.items():
        if info['total_mv'] <= 10000000000:
            continue

        records = info['records']
        records.sort(key=lambda x: x['trade_date'])

        if len(records) < 35:
            continue

        last_35_days = records[-35:]
        last_30_days = records[-30:]
        last_10_days = records[-10:]
        last_5_days = records[-5:]

        total_turnover = 0
        for r in last_10_days:
            if info['total_mv'] > 0:
                turnover_rate = (r['amount'] * 1000) / info['total_mv'] * 100
                total_turnover += turnover_rate

        if total_turnover <= 50:
            continue

        ma5 = sum(r['close'] for r in last_5_days) / len(last_5_days)
        ma30 = sum(r['close'] for r in last_30_days) / len(last_30_days)

        if ma30 <= 0 or ma5 <= ma30:
            continue

        prev_ma5 = sum(r['close'] for r in last_35_days[-10:-5]) / 5
        prev_ma30 = sum(r['close'] for r in last_35_days[-35:-5]) / 30

        if prev_ma5 <= 0 or prev_ma30 <= 0:
            continue

        if ma5 <= prev_ma5 or ma30 <= prev_ma30:
            continue

        up_days_amount = []
        down_days_amount = []
        for r in last_10_days:
            if r['open'] < r['close']:
                up_days_amount.append(r['amount'])
            elif r['open'] > r['close']:
                down_days_amount.append(r['amount'])

        if len(up_days_amount) == 0 or len(down_days_amount) == 0:
            continue

        avg_up_amount = sum(up_days_amount) / len(up_days_amount)
        avg_down_amount = sum(down_days_amount) / len(down_days_amount)

        if avg_down_amount == 0 or avg_up_amount <= avg_down_amount * 1.5:
            continue

        ma_diff_pct = (ma5 - ma30) / ma30 * 100
        if ma_diff_pct >= 10:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': info['stock_name'],
            'total_turnover': round(total_turnover, 2),
            'ma5': round(ma5, 2),
            'ma30': round(ma30, 2),
            'ma_diff_pct': round(ma_diff_pct, 2)
        })

    result.sort(key=lambda x: x['ma_diff_pct'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "ma_10d.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '10日累计换手率(%)', 'MA5', 'MA30', 'MA5-MA30差值(%)'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'], stock['total_turnover'],
                            stock['ma5'], stock['ma30'], stock['ma_diff_pct']])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("均线上升选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data(days=30)

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
            print(f"• {stock['ts_code']} - {stock['stock_name']}")
            print(f"  ├─ 10日累计换手率: {stock['total_turnover']}%")
            print(f"  ├─ MA5: {stock['ma5']}, MA30: {stock['ma30']}")
            print(f"  └─ MA5-MA30差值: {stock['ma_diff_pct']}%")
        
        if len(selected_stocks) > 10:
            print(f"  ... 还有 {len(selected_stocks) - 10} 只股票")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()