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

target_date = '20260629'
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

target_idx = None
for i, d in enumerate(data):
    if d['trade_date'] == target_date:
        target_idx = i
        break

if target_idx is None:
    print(f"没有找到{target_date}的数据")
    close_connection(conn)
    sys.exit(1)

if target_idx < 119:
    print(f"{target_date}之前只有{target_idx}天数据，不足119天")
    close_connection(conn)
    sys.exit(1)

selected_data = data[:target_idx+1]
if len(selected_data) > 120:
    selected_data = selected_data[-120:]

target_day = selected_data[-1]
prev_119 = selected_data[:-1]
prev_close = selected_data[-2]['close']

max_119 = max(d['close'] for d in prev_119)
min_119 = min(d['close'] for d in prev_119)
amplitude = (max_119 - min_119) / min_119 * 100
gain = (target_day['close'] - prev_close) / prev_close * 100
amount_actual = target_day['amount'] * 1000

print(f"股票代码: 688131.SH")
print(f"分析日期: {target_date}")
print(f"数据范围: {selected_data[0]['trade_date']} ~ {selected_data[-1]['trade_date']}")
print(f"数据条数: {len(selected_data)}")

print(f"\n{'='*60}")
print(f"条件检查({target_date}):")
print(f"{'='*60}")
print(f"最新收盘: {target_day['close']:.2f}")
print(f"前119天最高: {max_119:.2f}")
print(f"前119天最低: {min_119:.2f}")
print(f"前一日收盘: {prev_close:.2f}")
print(f"振幅: {amplitude:.2f}%")
print(f"当日涨幅: {gain:.2f}%")
print(f"成交额: {amount_actual/100000000:.2f}亿")

print(f"\n{'='*60}")
print(f"条件验证:")
print(f"{'='*60}")
cond1 = target_day['close'] > max_119
cond2 = gain > 5
cond3 = amplitude <= 35
cond4 = amount_actual > 500000000

print(f"1. 最新收盘 > 前119天最高: {'✅ 通过' if cond1 else '❌ 未通过'} ({target_day['close']:.2f} > {max_119:.2f})")
print(f"2. 涨幅 > 5%: {'✅ 通过' if cond2 else '❌ 未通过'} ({gain:.2f}% > 5%)")
print(f"3. 振幅 <= 35%: {'✅ 通过' if cond3 else '❌ 未通过'} ({amplitude:.2f}% <= 35%)")
print(f"4. 成交额 > 5亿: {'✅ 通过' if cond4 else '❌ 未通过'} ({amount_actual/100000000:.2f}亿 > 5亿)")

print(f"\n{'='*60}")
print(f"结论: {'✅ 符合条件，可以选出' if all([cond1, cond2, cond3, cond4]) else '❌ 不符合条件'}")

print(f"\n最近10天数据:")
print(f"{'日期':<12} {'收盘':<10} {'涨幅':<10}")
for d in selected_data[-10:]:
    idx = selected_data.index(d)
    prev_c = selected_data[idx-1]['close'] if idx > 0 else d['close']
    g = (d['close'] - prev_c) / prev_c * 100 if prev_c != 0 else 0
    flag = '⭐' if d['trade_date'] == target_date else ''
    print(f"{d['trade_date']} {flag:<2} {d['close']:<10.2f} {g:<10.2f}%")

close_connection(conn)