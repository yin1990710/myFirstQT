#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
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
    folder_name = f"涨跌停{target_date}"
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

    query_sql = """
    SELECT
        d.ts_code,
        i.stock_name,
        d.trade_date,
        d.open,
        d.close,
        d.pre_close
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date = %s
    ORDER BY d.ts_code
    """

    try:
        cursor = connection.cursor()
        cursor.execute(query_sql, (target_date,))
        results = cursor.fetchall()
        
        if len(results) == 0:
            cursor.execute('SELECT MAX(trade_date) FROM stock_daily_t')
            row = cursor.fetchone()
            max_date = row['MAX(trade_date)'] if row else None
            if max_date:
                print(f"⚠️ {target_date} 无数据，使用最新日期 {max_date}")
                cursor.execute(query_sql, (max_date,))
                results = cursor.fetchall()
        
        cursor.close()
        connection.commit()
        print(f"✅ 成功读取 {len(results)} 条数据")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)

def analyze_stocks(data):
    result = []

    for record in data:
        ts_code = record['ts_code']
        name = record['stock_name'] if record['stock_name'] else ''
        open_price = float(record['open'] or 0)
        close_price = float(record['close'] or 0)
        pre_close = float(record['pre_close'] or 0)

        if pre_close <= 0:
            continue

        gain = (close_price - open_price) / pre_close * 100

        is_main_board = False
        is_gem_or_kcb = False

        if ts_code.endswith('.SH'):
            if ts_code.startswith('60'):
                is_main_board = True
            elif ts_code.startswith('688'):
                is_gem_or_kcb = True
        elif ts_code.endswith('.SZ'):
            if ts_code.startswith('00'):
                is_main_board = True
            elif ts_code.startswith('30'):
                is_gem_or_kcb = True

        qualified = False
        if gain > 9 or gain < -9.95:
            qualified = True

        if qualified:
            result.append({
                'ts_code': ts_code,
                'name': name,
                'gain': gain
            })

    result.sort(key=lambda x: x['gain'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    target_date = get_target_date()
    csv_filename = f"涨跌停股票{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write('股票代码,股票名称,涨幅(%)\n')
        for stock in stocks:
            f.write(f"{stock['ts_code']},{stock['name']},{stock['gain']:.2f}\n")

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("每日涨跌停选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data()

    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    selected_stocks = analyze_stocks(data)

    print(f"\n✅ 共选出 {len(selected_stocks)} 只涨跌停股票")

    if selected_stocks:
        csv_path = generate_csv_file(selected_stocks, folder_path)
        print("\n" + "=" * 80)
        print(f"🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n涨跌停股票列表:")
        for stock in selected_stocks:
            print(f"• {stock['ts_code']} {stock['name']}: {stock['gain']:.2f}%")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的涨停股票")
        print("=" * 80)

if __name__ == "__main__":
    main()