#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

def debug_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return

    ts_code = '300182.SZ'
    
    # 模拟select_lowwave_in10day.py的日期逻辑
    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
    
    print(f"📅 脚本查询日期范围: {start_date} ~ {target_date}")
    print(f"⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 查询该股票在指定范围内的数据
    query_sql = """
    SELECT
        d.trade_date,
        d.open,
        d.close,
        d.amount,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.ts_code = %s AND d.trade_date >= %s AND d.trade_date <= %s
    ORDER BY d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (ts_code, start_date, target_date))
            results = cursor.fetchall()

        print(f"\n📊 {ts_code} 在 {start_date} ~ {target_date} 范围内的数据:")
        print("-" * 80)
        print(f"{'日期':<12} {'开盘':<8} {'收盘':<8} {'成交额(千元)':<15} {'市值(元)':<15}")
        print("-" * 80)
        
        for record in results:
            trade_date = record['trade_date']
            open_price = float(record['open'] or 0)
            close_price = float(record['close'] or 0)
            amount = float(record['amount'] or 0)
            total_mv = float(record['total_mv'] or 0) if record['total_mv'] else 0
            
            print(f"{trade_date:<12} {open_price:<8.2f} {close_price:<8.2f} {amount:<15,.0f} {total_mv:<15,.0f}")
        
        print("-" * 80)
        print(f"共 {len(results)} 条数据")
        
        # 查询该股票所有可用数据的日期范围
        query_range = """
        SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date, COUNT(*) as total_count
        FROM stock_daily_t
        WHERE ts_code = %s
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query_range, (ts_code,))
            range_result = cursor.fetchone()
        
        print(f"\n📈 {ts_code} 数据库中所有数据范围:")
        print(f"   最早日期: {range_result['min_date']}")
        print(f"   最晚日期: {range_result['max_date']}")
        print(f"   总记录数: {range_result['total_count']}")
        
        # 检查是否有今天的数据
        today = datetime.now().strftime('%Y%m%d')
        query_today = """
        SELECT * FROM stock_daily_t WHERE ts_code = %s AND trade_date = %s
        """
        with connection.cursor() as cursor:
            cursor.execute(query_today, (ts_code, today))
            today_result = cursor.fetchone()
        
        print(f"\n🔍 今日({today})数据检查: {'✅ 存在' if today_result else '❌ 不存在'}")
        
        # 分析问题
        print("\n🔍 问题分析:")
        if len(results) < 10:
            print(f"   ⚠️ 关键问题: 在查询范围内只有 {len(results)} 条数据，少于10条")
            print(f"   ⚠️ 原因: 脚本使用当前日期作为结束日期，但今天({today})可能还没有数据")
            
            # 查询最近10个交易日的数据（不包括今天）
            query_recent = """
            SELECT trade_date, open, close, amount
            FROM stock_daily_t
            WHERE ts_code = %s
            ORDER BY trade_date DESC
            LIMIT 15
            """
            with connection.cursor() as cursor:
                cursor.execute(query_recent, (ts_code,))
                recent_results = cursor.fetchall()
            
            print("\n   最近15个交易日数据(按日期倒序):")
            print("   " + "-" * 60)
            print(f"   {'日期':<12} {'开盘':<8} {'收盘':<8} {'成交额(千元)':<15}")
            print("   " + "-" * 60)
            for record in recent_results:
                print(f"   {record['trade_date']:<12} {float(record['open']):<8.2f} {float(record['close']):<8.2f} {float(record['amount']):<15,.0f}")
            
        else:
            print("   ✅ 数据充足，问题可能在其他地方")

    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        close_connection(connection)

if __name__ == "__main__":
    debug_stock_data()