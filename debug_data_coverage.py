#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

def debug_data_coverage():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return

    # 计算日期范围
    target_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')  # 使用昨天
    start_date_40 = (datetime.now() - timedelta(days=40)).strftime('%Y%m%d')
    start_date_50 = (datetime.now() - timedelta(days=50)).strftime('%Y%m%d')

    print(f"📅 查询范围 (40天): {start_date_40} ~ {target_date}")
    print(f"📅 查询范围 (50天): {start_date_50} ~ {target_date}")

    # 查询每个股票在40天范围内的数据条数分布
    query = """
    SELECT record_count, COUNT(*) as stock_count
    FROM (
        SELECT ts_code, COUNT(*) as record_count
        FROM stock_daily_t
        WHERE trade_date >= %s AND trade_date <= %s
        GROUP BY ts_code
    ) t
    GROUP BY record_count
    ORDER BY record_count DESC
    LIMIT 20
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (start_date_40, target_date))
            results = cursor.fetchall()

        print("\n📊 股票数据条数分布 (40天范围):")
        print("-" * 50)
        print(f"{'数据条数':<12} {'股票数量':<10}")
        print("-" * 50)
        for row in results:
            print(f"{row['record_count']:<12} {row['stock_count']:<10}")

        # 查询有多少股票有>=30条数据
        query_30 = """
        SELECT COUNT(*) as count
        FROM (
            SELECT ts_code, COUNT(*) as record_count
            FROM stock_daily_t
            WHERE trade_date >= %s AND trade_date <= %s
            GROUP BY ts_code
            HAVING COUNT(*) >= 30
        ) t
        """

        cursor.execute(query_30, (start_date_40, target_date))
        row = cursor.fetchone()
        print(f"\n📈 有>=30条数据的股票数: {row['count']}")

        cursor.execute(query_30, (start_date_50, target_date))
        row = cursor.fetchone()
        print(f"📈 有>=30条数据的股票数(50天范围): {row['count']}")

        # 查看最新交易日期
        cursor.execute("SELECT MAX(trade_date) FROM stock_daily_t")
        row = cursor.fetchone()
        print(f"\n🔍 数据库最新交易日期: {row['MAX(trade_date)']}")

    except Exception as e:
        print(f"❌ 查询失败: {e}")
    finally:
        close_connection(connection)

if __name__ == "__main__":
    debug_data_coverage()