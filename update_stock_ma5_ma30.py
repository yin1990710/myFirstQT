#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None, None

    query_sql = """
    SELECT
        ts_code,
        trade_date,
        close
    FROM stock_daily_t
    ORDER BY ts_code, trade_date DESC
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql)
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据")
        return results, connection
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        close_connection(connection)
        return None, None

def calculate_ma_and_update(data, connection):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close'] or 0)
        })

    update_count = 0
    total_stocks = len(stock_data)

    try:
        with connection.cursor() as cursor:
            for ts_code, records in stock_data.items():
                if len(records) < 30:
                    continue

                records = records[:50]
                records.sort(key=lambda x: x['trade_date'])

                closes = [r['close'] for r in records]
                latest_date = records[-1]['trade_date']

                ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
                ma30 = sum(closes[-30:]) / 30 if len(closes) >= 30 else None

                update_sql = """
                UPDATE stock_daily_t 
                SET ma5 = %s, ma30 = %s 
                WHERE ts_code = %s AND trade_date = %s
                """
                cursor.execute(update_sql, (ma5, ma30, ts_code, latest_date))
                update_count += 1

            connection.commit()
        print(f"✅ 成功更新 {update_count}/{total_stocks} 只股票的MA5和MA30")
    except Exception as e:
        print(f"❌ 更新数据失败: {e}")
        connection.rollback()

def main():
    print("=" * 80)
    print("更新股票MA5和MA30")
    print("=" * 80)

    data, connection = read_stock_data()
    
    if data is None or connection is None:
        return

    calculate_ma_and_update(data, connection)

    close_connection(connection)

if __name__ == "__main__":
    main()