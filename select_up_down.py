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
    folder_name = f"上下波动{target_date}"
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
        d.open,
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
            'open': float(record['open'] or 0),
            'amount': float(record['amount'] or 0),
            'total_mv': float(record['total_mv'] or 0)
        })

    result = []

    for ts_code, info in stock_data.items():
        records = info['records']
        
        if len(records) < 30:
            continue

        records = records[:30]
        records.sort(key=lambda x: x['trade_date'])

        latest_record = records[-1]
        
        if latest_record['total_mv'] <= 8000000000:
            continue

        recent_30 = records[-30:]
        recent_15 = records[-15:]

        up_days = []
        down_days = []

        for i in range(len(recent_15)):
            record = recent_15[i]
            if record['open'] < record['close']:
                up_days.append(record)
            elif record['open'] > record['close']:
                down_days.append(record)

        if len(up_days) <= 7:
            continue

        up_amounts = [r['amount'] * 1000 for r in up_days]
        avg_up_amount = sum(up_amounts) / len(up_amounts)
        
        if avg_up_amount <= 500000000:
            continue

        big_up_count = 0
        for i in range(len(recent_15)):
            record = recent_15[i]
            if record['open'] < record['close']:
                if i > 0:
                    prev_close = recent_15[i-1]['close']
                    if prev_close > 0:
                        pct_chg = (record['close'] - prev_close) / prev_close * 100
                        if pct_chg > 6:
                            big_up_count += 1

        if big_up_count < 2:
            continue

        if len(down_days) == 0:
            continue

        down_amounts = [r['amount'] for r in down_days]
        avg_down_amount = sum(down_amounts) / len(down_amounts)
        
        if avg_down_amount == 0:
            continue
        
        if (avg_up_amount / 1000) / avg_down_amount <= 1.5:
            continue

        close_15 = [r['close'] for r in recent_15]
        close_30 = [r['close'] for r in recent_30]
        
        avg_close_15 = sum(close_15) / len(close_15)
        avg_close_30 = sum(close_30) / len(close_30)
        
        if avg_close_30 == 0:
            continue
        
        ratio = avg_close_15 / avg_close_30
        
        if ratio <= 1 or ratio >= 1.2:
            continue

        result.append({
            'ts_code': ts_code,
            'stock_name': info['stock_name'],
            'up_days': len(up_days),
            'avg_up_amount': avg_up_amount / 100000000,
            'big_up_count': big_up_count,
            'ratio': ratio
        })

    result.sort(key=lambda x: x['up_days'], reverse=True)

    return result

def generate_csv_file(stocks, folder_path):
    csv_filename = "up_down.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '阳线天数', '阳线平均成交额(亿)', '涨幅>6%天数', '近15日/30日均价比'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'], 
                            stock['up_days'],
                            f"{stock['avg_up_amount']:.2f}",
                            stock['big_up_count'],
                            f"{stock['ratio']:.2f}"])

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("上下波动选股策略")
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
            print(f"• {stock['ts_code']} - {stock['stock_name']} | 阳线={stock['up_days']}天 | 平均成交额={stock['avg_up_amount']:.2f}亿 | 大涨={stock['big_up_count']}天 | 均价比={stock['ratio']:.2f}")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()