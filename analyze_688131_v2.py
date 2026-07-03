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

date_200_days_ago = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')
query = """
SELECT trade_date, close, amount
FROM stock_daily_t
WHERE ts_code = '688131.SH' AND trade_date >= %s
ORDER BY trade_date
"""
cursor.execute(query, (date_200_days_ago,))
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

if len(data) >= 120:
    latest = data[-1]
    prev_119 = data[:-1]
    prev_close = data[-2]['close']
    
    max_119 = max(d['close'] for d in prev_119)
    min_119 = min(d['close'] for d in prev_119)
    amplitude = (max_119 - min_119) / min_119 * 100
    gain = (latest['close'] - prev_close) / prev_close * 100
    amount_actual = latest['amount'] * 1000
    
    print(f"\n条件检查(120天):")
    print(f"最新收盘: {latest['close']:.2f}")
    print(f"前119天最高: {max_119:.2f}")
    print(f"前119天最低: {min_119:.2f}")
    print(f"前一日收盘: {prev_close:.2f}")
    print(f"振幅: {amplitude:.2f}%")
    print(f"当日涨幅: {gain:.2f}%")
    print(f"成交额: {amount_actual/100000000:.2f}亿")
    
    print(f"\n条件验证:")
    print(f"1. 最新收盘 > 前119天最高: {'✅' if latest['close'] > max_119 else '❌'} ({latest['close']:.2f} > {max_119:.2f})")
    print(f"2. 涨幅 > 5%: {'✅' if gain > 5 else '❌'} ({gain:.2f}% > 5%)")
    print(f"3. 振幅 <= 35%: {'✅' if amplitude <= 35 else '❌'} ({amplitude:.2f}% <= 35%)")
    print(f"4. 成交额 > 5亿: {'✅' if amount_actual > 500000000 else '❌'} ({amount_actual/100000000:.2f}亿 > 5亿)")
    
    print(f"\n最近10天数据:")
    for d in data[-10:]:
        idx = data.index(d)
        prev_c = data[idx-1]['close'] if idx > 0 else d['close']
        g = (d['close'] - prev_c) / prev_c * 100 if prev_c != 0 else 0
        print(f"{d['trade_date']}  收盘:{d['close']:.2f}  涨幅:{g:.2f}%")

close_connection(conn)