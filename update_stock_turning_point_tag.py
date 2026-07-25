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
        ma5
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

def analyze_and_update(data, connection):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'ma5': float(record['ma5']) if record['ma5'] is not None else None
        })

    update_count = 0
    total_stocks = len(stock_data)

    try:
        with connection.cursor() as cursor:
            for ts_code, records in stock_data.items():
                if len(records) < 11:
                    continue

                records = records[:100]
                records.sort(key=lambda x: x['trade_date'])

                ma5_values = [r['ma5'] for r in records]

                for i in range(len(records)):
                    if i < 5 or i >= len(records) - 5:
                        continue

                    ma5_before5 = ma5_values[i - 5]
                    ma5_current = ma5_values[i]
                    ma5_after5 = ma5_values[i + 5]

                    if ma5_before5 is None or ma5_before5 <= 0 or ma5_after5 is None or ma5_after5 <= 0:
                        continue

                    a1 = (ma5_current - ma5_before5) / 5
                    b1 = (ma5_after5 - ma5_current) / 5

                    if a1 > 0 and b1 < 0:
                        tag = '波峰'
                    elif a1 < 0 and b1 > 0:
                        tag = '波谷'
                    elif a1 < 0 and b1 < 0:
                        tag = '下降'
                    elif a1 > 0 and b1 > 0:
                        tag = '上升'
                    else:
                        continue

                    trade_date = records[i]['trade_date']

                    update_sql = """
                    UPDATE stock_daily_t 
                    SET turning_point = %s 
                    WHERE ts_code = %s AND trade_date = %s
                    """
                    cursor.execute(update_sql, (tag, ts_code, trade_date))
                    update_count += 1

            connection.commit()
        print(f"✅ 成功更新 {update_count} 条记录的turning_point字段（{total_stocks} 只股票）")
    except Exception as e:
        print(f"❌ 更新数据失败: {e}")
        connection.rollback()

def main():
    print("=" * 80)
    print("更新股票turning_point字段（波峰/波谷/上升/下降）")
    print("=" * 80)

    data, connection = read_stock_data()
    
    if data is None or connection is None:
        return

    analyze_and_update(data, connection)

    close_connection(connection)

if __name__ == "__main__":
    main()