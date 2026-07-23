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

    print('长江证券(000783.SZ) 20260615之前60个交易日的MA30分析')
    print('=' * 90)
    print(f'{"日期":<12} {"收盘价":>8} {"MA30":>8} {"收盘-MA30":>12} {"MA30趋势":>10}')
    print('-' * 90)

    for end_idx in range(29, len(records)):
        sub_records = records[:end_idx+1]
        current_date = sub_records[-1]['trade_date']
        current_close = sub_records[-1]['close']
        
        ma30 = sum(r['close'] for r in sub_records[-30:]) / 30
        
        diff = current_close - ma30
        
        if end_idx >= 38:
            ma30_list = []
            for i in range(end_idx - 29, end_idx + 1):
                ma30_val = sum(r['close'] for r in records[i-29:i+1]) / 30
                ma30_list.append(ma30_val)
            
            recent_ma30 = ma30_list[-10:]
            is_increasing = True
            for i in range(1, len(recent_ma30)):
                if recent_ma30[i] <= recent_ma30[i-1]:
                    is_increasing = False
                    break
            trend = '递增' if is_increasing else '非递增'
        else:
            trend = '-'
        
        print(f'{current_date:<12} {current_close:>8.2f} {ma30:>8.2f} {diff:>12.2f} {trend:>10}')

if __name__ == '__main__':
    main()