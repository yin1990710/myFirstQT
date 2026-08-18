#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_folder_path():
    target_date = get_target_date()
    folder_name = f"10天涨幅大于20个点{target_date}"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
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
        d.trade_date,
        d.close,
        i.stock_name,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date >= (
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        ORDER BY trade_date DESC
        LIMIT 1 OFFSET 9
    )
    AND d.trade_date <= %s
    ORDER BY d.ts_code, d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (target_date,))
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
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] else None,
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []

    for ts_code, records in stock_data.items():
        if len(records) < 10:
            continue

        records.sort(key=lambda x: x['trade_date'])

        # 去除市值小于100亿的股票
        total_mv = records[-1]['total_mv']
        if total_mv < 10000000000:
            continue

        closes = [r['close'] for r in records[-10:] if r['close'] is not None]
        if len(closes) < 2:
            continue

        min_close = min(closes)
        max_close = max(closes)

        if min_close == 0:
            continue

        gain = (max_close - min_close) / min_close

        if gain > 0.20:
            result.append({
                'ts_code': ts_code,
                'name': records[-1]['name'],
                'min_close': min_close,
                'max_close': max_close,
                'gain': gain
            })

    result.sort(key=lambda x: x['gain'], reverse=True)

    print(f"\n满足条件股票数: {len(result)}")
    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "10天涨幅大于20个点.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '最低收盘价', '最高收盘价', '涨幅'])
        for stock in stocks:
            writer.writerow([
                stock['ts_code'],
                stock['name'],
                f"{stock['min_close']:.2f}",
                f"{stock['max_close']:.2f}",
                f"{stock['gain']*100:.2f}%"
            ])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("📈 10天涨幅大于20%选股策略")
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
        print(f"📄 CSV路径: {folder_path}/10天涨幅大于20个点.csv")
        print("=" * 80)
        print("\n🔥 精选股票（前10）：")
        for i, stock in enumerate(stocks[:10], 1):
            print(f"{i}. {stock['ts_code']} {stock['name']} 涨幅: {stock['gain']*100:.2f}%")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()
