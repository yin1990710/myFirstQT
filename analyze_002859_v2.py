#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection
from datetime import datetime, timedelta

conn = get_mysql_connection()
cursor = conn.cursor()

ts_code = '002859.SZ'

# select_2wave_up.py 的查询范围是150天
start_date = (datetime.now() - timedelta(days=150)).strftime('%Y%m%d')
target_date = datetime.now().strftime('%Y%m%d')
if datetime.now().hour < 15:
    target_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')

sql = """
SELECT d.ts_code, d.trade_date, d.close, d.amount, d.ma5, d.turning_point,
       i.stock_name, i.total_mv
FROM stock_daily_t d
LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
WHERE d.ts_code = %s AND d.trade_date >= %s AND d.trade_date <= %s
ORDER BY d.trade_date
"""
cursor.execute(sql, (ts_code, start_date, target_date))
rows = cursor.fetchall()

stock_name = rows[-1]['stock_name'] or ''
total_mv = float(rows[-1]['total_mv'] or 0) if rows[-1]['total_mv'] else 0

print("=" * 80)
print(f"股票: {ts_code} {stock_name}")
print(f"总市值: {total_mv / 100000000:.2f}亿")
print(f"数据量: {len(rows)} 条")
print("=" * 80)

records = []
for r in rows:
    records.append({
        'trade_date': r['trade_date'],
        'close': float(r['close']) if r['close'] else None,
        'amount': float(r['amount']) if r['amount'] else None,
        'ma5': float(r['ma5']) if r['ma5'] else None,
        'turning_point': r['turning_point'],
    })

n = len(records)

# 条件0: 数据量 >= 100
print(f"\n--- 条件0: 数据量 >= 100 ---")
print(f"数据量: {n} {'✅' if n >= 100 else '❌'}")

# 条件1: A股
print(f"\n--- 条件1: A股 ---")
print(f"{'✅' if ts_code.endswith('.SZ') or ts_code.endswith('.SH') else '❌'}")

# 条件2: 总市值 > 100亿
print(f"\n--- 条件2: 总市值 > 100亿 ---")
print(f"总市值: {total_mv / 100000000:.2f}亿 {'✅' if total_mv > 10000000000 else '❌'}")

# 条件3: 最近2日close>ma5
print(f"\n--- 条件3: 最近2日close>ma5 ---")
if n >= 2:
    ok = True
    for r in records[-2:]:
        close = r['close']
        ma5 = r['ma5']
        if close is not None and ma5 is not None and ma5 > 0:
            passed = close > ma5
            print(f"  {r['trade_date']}: close={close:.2f}, ma5={ma5:.2f} {'✅' if passed else '❌'}")
            if not passed:
                ok = False
        else:
            print(f"  {r['trade_date']}: close={close}, ma5={ma5} ❌")
            ok = False
    if not ok:
        print("  ❌ 条件3不满足")

# 条件4: 最近30日有波峰, T日amount>1000000
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
    tp_dist = {}
    for r in recent_30:
        tp = r['turning_point'] or 'NULL'
        tp_dist[tp] = tp_dist.get(tp, 0) + 1
    print("  最近30日turning_point分布:")
    for tp, cnt in tp_dist.items():
        print(f"    {tp}: {cnt}")

# 条件5: T日后至少10个交易日下降
if T_found:
    print(f"\n--- 条件5: T日后至少10日下降 ---")
    T_plus_10 = T_index + 10
    if T_plus_10 >= n:
        print(f"  ❌ T日后不足10个交易日 (T_index={T_index}, n={n})")
    else:
        all_decline = True
        for i in range(T_index + 1, T_plus_10 + 1):
            tp = records[i]['turning_point']
            if tp != '下降':
                all_decline = False
                print(f"  ❌ {records[i]['trade_date']}: turning_point={tp}")
        if all_decline:
            print(f"  ✅ T日后10日全部为下降")

# 条件6: T日至最近日，最低/最高 < 80%
if T_found:
    print(f"\n--- 条件6: T日至最近日 最低/最高 < 80% ---")
    prices = [records[i]['close'] for i in range(T_index, n) if records[i]['close'] is not None]
    if len(prices) >= 2:
        min_p = min(prices)
        max_p = max(prices)
        ratio = min_p / max_p if max_p > 0 else 0
        print(f"  最低: {min_p:.2f}, 最高: {max_p:.2f}, 比值: {ratio:.4f} {'✅' if ratio < 0.8 else '❌'}")

# 条件7: n1/n2>1 且 avg1/avg2>1.5
if T_found:
    print(f"\n--- 条件7: n1/n2>1 且 avg1/avg2>1.5 ---")
    # n1: T日前连续上升天数
    n1 = 0
    avg1_amounts = []
    i = T_index - 1
    while i >= 0:
        if records[i]['turning_point'] == '上升':
            n1 += 1
            amt = records[i]['amount']
            if amt and amt > 0:
                avg1_amounts.append(amt)
            i -= 1
        else:
            break
    avg1 = sum(avg1_amounts) / len(avg1_amounts) if avg1_amounts else 0

    # n2: T日后连续下降天数
    n2 = 0
    avg2_amounts = []
    i = T_index + 1
    while i < n:
        if records[i]['turning_point'] == '下降':
            n2 += 1
            amt = records[i]['amount']
            if amt and amt > 0:
                avg2_amounts.append(amt)
            i += 1
        else:
            break
    avg2 = sum(avg2_amounts) / len(avg2_amounts) if avg2_amounts else 0

    n_ratio = n1 / n2 if n2 > 0 else 0
    avg_ratio = avg1 / avg2 if avg2 > 0 else 0

    print(f"  n1(上升天数)={n1}, n2(下降天数)={n2}, n1/n2={n_ratio:.2f} {'✅' if n_ratio > 1 else '❌'}")
    print(f"  avg1(上升均量)={avg1:.0f}, avg2(下降均量)={avg2:.0f}, avg1/avg2={avg_ratio:.2f} {'✅' if avg_ratio > 1.5 else '❌'}")

    # 打印T日前后的turning_point详情
    print(f"\n  T日附近turning_point详情:")
    start_show = max(0, T_index - 5)
    end_show = min(n, T_index + 15)
    for i in range(start_show, end_show):
        tp = records[i]['turning_point'] or 'NULL'
        amt = records[i]['amount'] or 0
        marker = " <== T日" if i == T_index else ""
        print(f"    [{i}] {records[i]['trade_date']}: tp={tp}, amount={amt:.0f}{marker}")

close_connection(conn)
