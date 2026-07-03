#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from mysql_connection import get_mysql_connection, close_connection
from datetime import datetime, timedelta

conn = get_mysql_connection()
if not conn:
    print("数据库连接失败")
    sys.exit(1)

cursor = conn.cursor()

date_170_days_ago = (datetime.now() - timedelta(days=170)).strftime('%Y%m%d')
query = """
SELECT trade_date, close, amount
FROM stock_daily_t
WHERE ts_code = '688131.SH' AND trade_date >= %s
ORDER BY trade_date
"""
cursor.execute(query, (date_170_days_ago,))
rows = cursor.fetchall()

if not rows:
    print("没有找到数据")
    close_connection(conn)
    sys.exit(1)

data = []
for row in rows:
    data.append({
        'trade_date': row['trade_date'],
        'close': float(row['close']),
        'amount': float(row['amount'])
    })

if len(data) > 120:
    data = data[-120:]

print(f"股票代码: 688131.SH")
print(f"数据条数: {len(data)}")
print(f"日期范围: {data[0]['trade_date']} ~ {data[-1]['trade_date']}")

if len(data) >= 60:
    latest = data[-1]
    prev_days = data[:-1]
    prev_close = data[-2]['close']
    
    max_p = max(d['close'] for d in prev_days)
    min_p = min(d['close'] for d in prev_days)
    amplitude = (max_p - min_p) / min_p * 100
    gain = (latest['close'] - prev_close) / prev_close * 100
    amount_actual = latest['amount'] * 1000
    
    print(f"\n用现有{len(data)}天数据模拟检查:")
    print(f"最新收盘: {latest['close']:.2f}")
    print(f"前{len(prev_days)}天最高: {max_p:.2f}")
    print(f"前{len(prev_days)}天最低: {min_p:.2f}")
    print(f"振幅: {amplitude:.2f}%")
    print(f"当日涨幅: {gain:.2f}%")
    print(f"成交额: {amount_actual/100000000:.2f}亿")
    
    print(f"\n条件检查(假设数据足够):")
    print(f"1. 最新收盘 > 前N天最高: {'✅' if latest['close'] > max_p else '❌'}")
    print(f"2. 涨幅 > 5%: {'✅' if gain > 5 else '❌'}")
    print(f"3. 振幅 <= 35%: {'✅' if amplitude <= 35 else '❌'}")
    print(f"4. 成交额 > 5亿: {'✅' if amount_actual > 500000000 else '❌'}")
    
    print(f"\n最近10天数据:")
    for d in data[-10:]:
        idx = data.index(d)
        prev_c = data[idx-1]['close'] if idx > 0 else d['close']
        g = (d['close'] - prev_c) / prev_c * 100 if prev_c != 0 else 0
        print(f"{d['trade_date']}  收盘:{d['close']:.2f}  涨幅:{g:.2f}%")
else:
    print(f"数据不足120天")

close_connection(conn)