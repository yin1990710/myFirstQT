#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection
from datetime import datetime, timedelta

conn = get_mysql_connection()
cursor = conn.cursor()

ts_code = '002859.SZ'

# 读取数据
start_date = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')
target_date = datetime.now().strftime('%Y%m%d')
if datetime.now().hour < 15:
    target_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')

sql = """
SELECT d.ts_code, d.trade_date, d.close, d.amount, d.ma5, d.ma30, d.turning_point, d.qfq_adj_factor,
       i.stock_name, i.total_mv
FROM stock_daily_t d
LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
WHERE d.ts_code = %s AND d.trade_date >= %s AND d.trade_date <= %s
ORDER BY d.trade_date
"""
cursor.execute(sql, (ts_code, start_date, target_date))
rows = cursor.fetchall()

if not rows:
    print("未找到数据")
    close_connection(conn)
    sys.exit(1)

stock_name = rows[-1]['stock_name'] or ''
total_mv = float(rows[-1]['total_mv'] or 0) if rows[-1]['total_mv'] else 0

print("=" * 80)
print(f"股票: {ts_code} {stock_name}")
print(f"总市值: {total_mv / 100000000:.2f}亿")
print(f"数据量: {len(rows)} 条")
print("=" * 80)

# 前复权计算
records = []
for r in rows:
    records.append({
        'trade_date': r['trade_date'],
        'close': float(r['close']) if r['close'] else None,
        'amount': float(r['amount']) if r['amount'] else None,
        'ma5': float(r['ma5']) if r['ma5'] else None,
        'ma30': float(r['ma30']) if r['ma30'] else None,
        'turning_point': r['turning_point'],
        'qfq_adj_factor': float(r['qfq_adj_factor']) if r['qfq_adj_factor'] else None,
    })

latest_factor = records[-1]['qfq_adj_factor']
print(f"最新交易日复权因子: {latest_factor}")
if latest_factor and latest_factor > 0:
    for r in records:
        factor = r['qfq_adj_factor']
        if factor and factor > 0 and r['close'] is not None:
            r['close'] = r['close'] * (factor / latest_factor)
    print("已应用前复权计算")

n = len(records)

# 条件0: 数据量 >= 100
print(f"\n--- 条件0: 数据量 >= 100 ---")
print(f"数据量: {n} {'✅' if n >= 100 else '❌'}")

# 条件1: A股
print(f"\n--- 条件1: A股 ---")
print(f"ts_code={ts_code} {'✅' if ts_code.endswith('.SZ') or ts_code.endswith('.SH') else '❌'}")

# 条件2: 总市值 > 100亿
print(f"\n--- 条件2: 总市值 > 100亿 ---")
print(f"总市值: {total_mv / 100000000:.2f}亿 {'✅' if total_mv > 10000000000 else '❌'}")

# 条件3: 最近2个交易日收盘价>ma5，且最近1日成交量>500000
print(f"\n--- 条件3: 最近2日close>ma5，最近1日amount>500000 ---")
if n >= 2:
    for r in records[-2:]:
        close = r['close']
        ma5 = r['ma5']
        if close is not None and ma5 is not None and ma5 > 0:
            print(f"  {r['trade_date']}: close={close:.2f}, ma5={ma5:.2f} {'✅' if close > ma5 else '❌'}")
        else:
            print(f"  {r['trade_date']}: close={close}, ma5={ma5} ❌")
    latest_amount = records[-1]['amount']
    print(f"  最近1日amount: {latest_amount:.0f} {'✅' if latest_amount > 500000 else '❌'}")

# 条件4: 最近30日有波峰(T日), T日amount>1000000
print(f"\n--- 条件4: 最近30日有波峰, T日amount>1000000 ---")
recent_30 = records[-30:] if n >= 30 else records
T_found = False
T_index = None
for i in range(len(recent_30)):
    if recent_30[i]['turning_point'] == '波峰':
        amount = recent_30[i]['amount'] if recent_30[i]['amount'] else 0
        idx = n - len(recent_30) + i
        print(f"  波峰: {recent_30[i]['trade_date']} (index={idx}), amount={amount:.0f} {'✅' if amount > 1000000 else '❌'}")
        if amount > 1000000 and not T_found:
            T_found = True
            T_index = idx

if not T_found:
    print("  ❌ 最近30日无满足条件的波峰")
    # 打印最近30日的turning_point分布
    print("  最近30日turning_point分布:")
    tp_dist = {}
    for r in recent_30:
        tp = r['turning_point'] or 'NULL'
        tp_dist[tp] = tp_dist.get(tp, 0) + 1
    for tp, cnt in tp_dist.items():
        print(f"    {tp}: {cnt}")

# 条件4b: T日后至少5个交易日下降
if T_found:
    print(f"\n--- 条件4b: T日后至少5日下降 ---")
    decline_count = 0
    for i in range(T_index + 1, n):
        if records[i]['turning_point'] == '下降':
            decline_count += 1
    print(f"  T日后下降天数: {decline_count} {'✅' if decline_count >= 5 else '❌'}")
    # 打印T日后的turning_point
    print(f"  T日后各日turning_point:")
    for i in range(T_index + 1, n):
        tp = records[i]['turning_point'] or 'NULL'
        print(f"    {records[i]['trade_date']}: {tp}")

# 条件4c: T日至最近日，最低/最高收盘价 < 80%
if T_found:
    print(f"\n--- 条件4c: T日至最近日 最低/最高 < 80% ---")
    prices = [records[i]['close'] for i in range(T_index, n) if records[i]['close'] is not None]
    if len(prices) >= 2:
        min_p = min(prices)
        max_p = max(prices)
        ratio = min_p / max_p if max_p > 0 else 0
        print(f"  最低: {min_p:.2f}, 最高: {max_p:.2f}, 比值: {ratio:.4f} {'✅' if ratio < 0.8 else '❌'}")

# 条件4d: 最近2日ma30单调递增
print(f"\n--- 条件4d: 最近2日ma30单调递增 ---")
if n >= 2:
    ma30_1 = records[-2]['ma30']
    ma30_2 = records[-1]['ma30']
    if ma30_1 is not None and ma30_2 is not None:
        print(f"  {records[-2]['trade_date']}: ma30={ma30_1:.2f}")
        print(f"  {records[-1]['trade_date']}: ma30={ma30_2:.2f}")
        print(f"  {'✅' if ma30_2 > ma30_1 else '❌'}")

close_connection(conn)
