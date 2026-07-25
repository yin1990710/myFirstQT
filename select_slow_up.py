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
    folder_name = f"慢牛选股{target_date}"
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

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.pre_close,
        d.amount,
        d.ma5,
        d.ma30,
        i.stock_name,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    ORDER BY d.ts_code, d.trade_date DESC
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql)
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据")
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
                'stock_name': record['stock_name'] or ''
            }
        stock_data[ts_code]['records'].append({
            'trade_date': record['trade_date'],
            'open': float(record['open'] or 0),
            'close': float(record['close'] or 0),
            'pre_close': float(record['pre_close'] or 0),
            'amount': float(record['amount'] or 0),
            'ma5': float(record['ma5']) if record['ma5'] is not None else None,
            'ma30': float(record['ma30']) if record['ma30'] is not None else None,
            'total_mv': float(record['total_mv'] or 0)
        })

    result = []

    for ts_code, info in stock_data.items():
        records = info['records']
        
        if len(records) < 40:
            continue

        records = records[:40]
        records.sort(key=lambda x: x['trade_date'])

        latest_record = records[-1]
        
        if latest_record['total_mv'] <= 5000000000:
            continue

        recent_20_days = records[-20:]

        up_amount_total = 0
        down_amount_total = 0
        has_big_up = False
        closes = []

        for r in recent_20_days:
            closes.append(r['close'])
            
            if r['open'] < r['close']:
                up_amount_total += r['amount']
            else:
                down_amount_total += r['amount']
            
            if r['pre_close'] > 0:
                pct_chg = (r['close'] - r['pre_close']) / r['pre_close'] * 100
                if pct_chg > 8:
                    has_big_up = True

        if down_amount_total > 0 and up_amount_total <= down_amount_total * 2:
            continue

        if not has_big_up:
            continue

        if len(closes) < 2:
            continue

        min_close = min(closes)
        max_close = max(closes)

        if max_close == 0 or min_close / max_close <= 0.75:
            continue

        recent_3_days = records[-3:]
        
        for r in recent_3_days:
            if r['ma5'] is None or r['ma30'] is None:
                continue
            if r['ma5'] <= r['ma30']:
                continue

        if len(records) < 10:
            continue

        ma5_day10 = records[-10]['ma5']
        ma5_day1 = records[-1]['ma5']

        if ma5_day10 is None or ma5_day1 is None:
            continue

        slope = (ma5_day1 - ma5_day10) / 9
        if slope <= 0:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': info['stock_name'],
            'total_mv': latest_record['total_mv'],
            'up_amount_ratio': up_amount_total / down_amount_total if down_amount_total > 0 else float('inf'),
            'min_max_ratio': min_close / max_close,
            'ma5_slope': slope
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "slow_up.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '总市值', '阳线成交额/阴线成交额', '最低/最高收盘价', 'MA5斜率'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'], 
                            f"{stock['total_mv'] / 100000000:.2f}亿",
                            f"{stock['up_amount_ratio']:.2f}",
                            f"{stock['min_max_ratio']:.2%}",
                            f"{stock['ma5_slope']:.4f}"])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("慢牛选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data()

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
            print(f"• {stock['ts_code']} - {stock['stock_name']} | 市值={stock['total_mv']/100000000:.2f}亿 | 阳线/阴线={stock['up_amount_ratio']:.2f} | 最低/最高={stock['min_max_ratio']:.2%} | MA5斜率={stock['ma5_slope']:.4f}")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()