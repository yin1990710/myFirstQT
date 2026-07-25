#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import csv
import math
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
    folder_name = f"5日均线上扬{target_date}"
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
        d.close,
        d.amount,
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

def calculate_angle(line1_start, line1_end, line2_start, line2_end):
    x1, y1 = line1_start
    x2, y2 = line1_end
    x3, y3 = line2_start
    x4, y4 = line2_end

    vector1 = (x2 - x1, y2 - y1)
    vector2 = (x4 - x3, y4 - y3)

    dot_product = vector1[0] * vector2[0] + vector1[1] * vector2[1]
    magnitude1 = math.sqrt(vector1[0] ** 2 + vector1[1] ** 2)
    magnitude2 = math.sqrt(vector2[0] ** 2 + vector2[1] ** 2)

    if magnitude1 == 0 or magnitude2 == 0:
        return 0

    cos_theta = dot_product / (magnitude1 * magnitude2)
    cos_theta = max(min(cos_theta, 1.0), -1.0)
    angle = math.acos(cos_theta) * (180 / math.pi)

    return angle

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
            'close': float(record['close'] or 0),
            'amount': float(record['amount'] or 0),
            'total_mv': float(record['total_mv'] or 0)
        })

    result = []

    for ts_code, info in stock_data.items():
        records = info['records']
        
        if len(records) < 80:
            continue

        records = records[:80]
        records.sort(key=lambda x: x['trade_date'])

        latest_record = records[-1]
        
        if latest_record['total_mv'] <= 8000000000:
            continue

        if latest_record['amount'] * 1000 <= 500000000:
            continue

        close_prices = [r['close'] for r in records]

        ma5_list = []
        ma30_list = []

        for i in range(len(close_prices)):
            if i >= 4:
                ma5 = sum(close_prices[i-4:i+1]) / 5
                ma5_list.append(ma5)
            else:
                ma5_list.append(None)

            if i >= 29:
                ma30 = sum(close_prices[i-29:i+1]) / 30
                ma30_list.append(ma30)
            else:
                ma30_list.append(None)

        valid_start = 29
        valid_ma5 = ma5_list[valid_start:]
        valid_ma30 = ma30_list[valid_start:]

        if len(valid_ma5) < 10:
            continue

        recent_ma5 = valid_ma5[-10:]
        recent_ma30 = valid_ma30[-10:]

        day1_idx = 9
        day5_idx = 5
        day6_idx = 4
        day10_idx = 0

        a1_slope = (recent_ma5[day6_idx] - recent_ma5[day10_idx]) / (day6_idx - day10_idx)
        a2_slope = (recent_ma30[day6_idx] - recent_ma30[day10_idx]) / (day6_idx - day10_idx)
        b1_slope = (recent_ma5[day1_idx] - recent_ma5[day5_idx]) / (day1_idx - day5_idx)
        b2_slope = (recent_ma30[day1_idx] - recent_ma30[day5_idx]) / (day1_idx - day5_idx)

        if a1_slope <= 0 or a2_slope <= 0 or b1_slope <= 0 or b2_slope <= 0:
            continue

        a1_start = (day10_idx, recent_ma5[day10_idx])
        a1_end = (day6_idx, recent_ma5[day6_idx])
        a2_start = (day10_idx, recent_ma30[day10_idx])
        a2_end = (day6_idx, recent_ma30[day6_idx])

        b1_start = (day5_idx, recent_ma5[day5_idx])
        b1_end = (day1_idx, recent_ma5[day1_idx])
        b2_start = (day5_idx, recent_ma30[day5_idx])
        b2_end = (day1_idx, recent_ma30[day1_idx])

        x1 = calculate_angle(a1_start, a1_end, a2_start, a2_end)
        x2 = calculate_angle(b1_start, b1_end, b2_start, b2_end)

        if x2 <= x1:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': info['stock_name'],
            'x1': x1,
            'x2': x2
        })

    result.sort(key=lambda x: x['x2'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "ma5_ma30_up.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', 'x1(前半段夹角)', 'x2(后半段夹角)'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'], 
                            f"{stock['x1']:.2f}°", f"{stock['x2']:.2f}°"])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("MA5/MA30上扬选股策略")
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
            print(f"• {stock['ts_code']} - {stock['stock_name']} | x1={stock['x1']:.2f}° | x2={stock['x2']:.2f}°")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()