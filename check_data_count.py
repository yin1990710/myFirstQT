#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
if not conn:
    print("数据库连接失败")
    exit(1)

cursor = conn.cursor()

query = "SELECT COUNT(*) FROM stock_daily_t WHERE ts_code = '688131.SH'"
cursor.execute(query)
total = cursor.fetchone()
print(f"整个表中该股票数据量: {total['COUNT(*)']} 天")

query = "SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily_t WHERE ts_code = '688131.SH'"
cursor.execute(query)
date_range = cursor.fetchone()
print(f"日期范围: {date_range['MIN(trade_date)']} ~ {date_range['MAX(trade_date)']}")

query = """
SELECT COUNT(*) 
FROM stock_daily_t 
WHERE ts_code = '688131.SH' AND trade_date >= (DATE_SUB(CURDATE(), INTERVAL 200 DAY))
"""
cursor.execute(query)
recent = cursor.fetchone()
print(f"最近200天数据量: {recent['COUNT(*)']} 天")

query = """
SELECT MIN(trade_date), MAX(trade_date) 
FROM stock_daily_t 
WHERE ts_code = '688131.SH' AND trade_date >= (DATE_SUB(CURDATE(), INTERVAL 200 DAY))
"""
cursor.execute(query)
recent_range = cursor.fetchone()
print(f"最近200天日期范围: {recent_range['MIN(trade_date)']} ~ {recent_range['MAX(trade_date)']}")

close_connection(conn)