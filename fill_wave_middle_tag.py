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
        d.ts_code,
        d.trade_date,
        d.turning_point
    FROM stock_daily_t d
    INNER JOIN (
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        ORDER BY trade_date DESC
        LIMIT 12
    ) t ON d.trade_date = t.trade_date
    ORDER BY d.ts_code, d.trade_date DESC
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

def replace_peak_valley(data, connection):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'turning_point': record['turning_point']
        })

    update_count = 0
    total_stocks = len(stock_data)

    try:
        with connection.cursor() as cursor:
            for ts_code, records in stock_data.items():
                if len(records) < 12:
                    continue

                for i in range(4):
                    if records[i]['turning_point'] not in ('波峰', '波谷'):
                        continue

                    fill_tag = None
                    for j in range(4, 12):
                        if records[j]['turning_point'] in ('上升', '下降'):
                            fill_tag = records[j]['turning_point']
                            break

                    if fill_tag is None:
                        fill_tag = '下降'

                    trade_date = records[i]['trade_date']

                    update_sql = """
                    UPDATE stock_daily_t
                    SET turning_point = %s
                    WHERE ts_code = %s AND trade_date = %s
                    """
                    cursor.execute(update_sql, (fill_tag, ts_code, trade_date))
                    update_count += 1

            connection.commit()
        print(f"✅ 成功更新 {update_count} 条记录的turning_point字段（{total_stocks} 只股票）")
    except Exception as e:
        print(f"❌ 更新数据失败: {e}")
        connection.rollback()

def main():
    print("=" * 80)
    print("替换波峰波谷为前向上升/下降标记")
    print("=" * 80)

    data, connection = read_stock_data()

    if data is None or connection is None:
        return

    replace_peak_valley(data, connection)

    close_connection(connection)

if __name__ == "__main__":
    main()