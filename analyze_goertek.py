#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
cursor = conn.cursor()

query = """
SELECT ts_code, trade_date, open, close, high, low, amount
FROM stock_daily_t
WHERE ts_code = '002241.SZ'
ORDER BY trade_date DESC
LIMIT 15
"""
cursor.execute(query)
rows = cursor.fetchall()
rows.reverse()

print('歌尔股份(002241.SZ) 近15个交易日数据:')
print('-' * 100)
header = f'{"日期":<12} {"开盘":<10} {"收盘":<10} {"最高":<10} {"最低":<10} {"成交额(千元)":<15} {"涨跌":<8} {"阳线/阴线":<8}'
print(header)
print('-' * 100)

for r in rows:
    open_p = float(r['open'] or 0)
    close_p = float(r['close'] or 0)
    high_p = float(r['high'] or 0)
    low_p = float(r['low'] or 0)
    amount = float(r['amount'] or 0)
    is_up = '阳线' if open_p < close_p else ('阴线' if open_p > close_p else '平盘')
    pct = (close_p - open_p) / open_p * 100 if open_p > 0 else 0
    print(f'{r["trade_date"]:<12} {open_p:<10.2f} {close_p:<10.2f} {high_p:<10.2f} {low_p:<10.2f} {amount:<15.0f} {pct:+.2f}%   {is_up}')

last_10 = rows[-10:]
print()
print('=' * 100)
print('最近10个交易日条件分析:')
print('=' * 100)

up_days_amount = []
down_days_amount = []
all_high = []
all_low = []
failed_reason = None

for r in last_10:
    open_p = float(r['open'] or 0)
    close_p = float(r['close'] or 0)
    high_p = float(r['high'] or 0)
    low_p = float(r['low'] or 0)
    amount = float(r['amount'] or 0)

    if open_p < close_p:
        if amount * 1000 <= 500000000:
            failed_reason = f'条件1失败: {r["trade_date"]} 阳线成交额不足5亿 (实际: {amount * 1000 / 100000000:.2f}亿)'
            up_days_amount = None
            break
        up_days_amount.append(amount)
    elif open_p > close_p:
        down_days_amount.append(amount)

    all_high.append(high_p)
    all_low.append(low_p)

if failed_reason:
    print(f'FAIL: {failed_reason}')
else:
    print('PASS: 条件1 - 所有阳线成交额均>5亿')
    print(f'  阳线数量: {len(up_days_amount)} 天')

    if len(up_days_amount) < 4:
        print(f'FAIL: 条件2 - 阳线数量不足4天 (实际: {len(up_days_amount)}天)')
    else:
        print('PASS: 条件2 - 阳线数量 >= 4天')

    if len(down_days_amount) == 0:
        print('FAIL: 条件3 - 没有阴线日')
    else:
        print(f'PASS: 条件3 - 存在阴线日 ({len(down_days_amount)}天)')

        avg_up = sum(up_days_amount) / len(up_days_amount)
        avg_down = sum(down_days_amount) / len(down_days_amount)
        print(f'  阳线平均成交额: {avg_up * 1000 / 100000000:.2f}亿')
        print(f'  阴线平均成交额: {avg_down * 1000 / 100000000:.2f}亿')

        if avg_up <= avg_down * 1.5:
            print(f'FAIL: 条件4 - 阳线平均成交额 <= 阴线*1.5 ({avg_up*1000/1e8:.2f}亿 <= {avg_down*1.5*1000/1e8:.2f}亿)')
        else:
            print('PASS: 条件4 - 阳线放量是阴线的1.5倍以上')

        max_high = max(all_high)
        min_low = min(all_low)
        high_low_pct = (max_high - min_low) / min_low * 100
        print(f'  10日最高: {max_high:.2f}')
        print(f'  10日最低: {min_low:.2f}')
        print(f'  振幅: {high_low_pct:.2f}%')

        if high_low_pct >= 15:
            print(f'FAIL: 条件5 - 振幅 >= 15% (实际: {high_low_pct:.2f}%)')
        else:
            print('PASS: 条件5 - 振幅 < 15%')

up_count = sum(1 for r in last_10 if float(r['open'] or 0) < float(r['close'] or 0))
down_count = sum(1 for r in last_10 if float(r['open'] or 0) > float(r['close'] or 0))
flat_count = 10 - up_count - down_count
print(f'\n10日统计: 阳线{up_count}天, 阴线{down_count}天, 平盘{flat_count}天')

close_connection(conn)
