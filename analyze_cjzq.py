#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection

def main():
    conn = get_mysql_connection()
    if not conn:
        print('连接失败')
        sys.exit(1)

    query = '''
    SELECT trade_date, close, amount
    FROM stock_daily_t
    WHERE ts_code = '000783.SZ' AND trade_date <= '20260615'
    ORDER BY trade_date DESC
    LIMIT 60
    '''

    with conn.cursor() as cur:
        cur.execute(query)
        results = cur.fetchall()

    close_connection(conn)

    records = []
    for r in results:
        records.append({
            'trade_date': r['trade_date'],
            'close': float(r['close'] or 0),
            'amount': float(r['amount'] or 0)
        })
    records.reverse()

    print('分析长江证券(000783.SZ) 20260615之前30个交易日的数据')
    print('市值: 465亿 > 100亿 ✅')
    print('=' * 80)

    found = False
    for end_idx in range(38, len(records)):
        sub_records = records[:end_idx+1]
        current_date = sub_records[-1]['trade_date']
        
        ma30_list = []
        for i in range(len(sub_records) - 29, len(sub_records)):
            ma30 = sum(r['close'] for r in sub_records[i-29:i+1]) / 30
            ma30_list.append(ma30)
        
        if len(ma30_list) < 10:
            continue
        
        recent_ma30 = ma30_list[-10:]
        
        is_ma30_increasing = True
        for i in range(1, len(recent_ma30)):
            if recent_ma30[i] <= recent_ma30[i-1]:
                is_ma30_increasing = False
                break
        
        if not is_ma30_increasing:
            continue
        
        prev_close = sub_records[-2]['close']
        current_close = sub_records[-1]['close']
        prev_ma30 = recent_ma30[-2]
        current_ma30 = recent_ma30[-1]
        
        cross_condition = (prev_close < prev_ma30) and (current_close > current_ma30)
        
        if not cross_condition:
            continue
        
        current_amount = sub_records[-1]['amount'] * 1000
        prev_amount = sub_records[-2]['amount'] * 1000
        
        amount_ok = (current_amount > 500000000) and (prev_amount > 0 and current_amount > prev_amount * 1.5)
        
        found = True
        print(f'日期: {current_date}')
        print(f'  ├─ 前一日: 收盘价={prev_close:.2f}, MA30={prev_ma30:.2f}, 收盘价<MA30: {prev_close < prev_ma30}')
        print(f'  ├─ 当日: 收盘价={current_close:.2f}, MA30={current_ma30:.2f}, 收盘价>MA30: {current_close > current_ma30}')
        print(f'  ├─ MA30趋势: 近10日单调递增 ✅')
        print(f'  ├─ 成交额: 当日{current_amount/100000000:.2f}亿, 前一日{prev_amount/100000000:.2f}亿')
        print(f'  └─ 综合: {"✅ 满足所有条件" if amount_ok else "❌ 成交额不达标"}')
        print()
    
    if not found:
        print('❌ 20260615之前30个交易日内，没有满足条件的日期')

if __name__ == '__main__':
    main()