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
    SELECT trade_date, close
    FROM stock_daily_t
    WHERE ts_code = '000783.SZ' AND trade_date <= '20260615'
    ORDER BY trade_date DESC
    LIMIT 30
    '''

    with conn.cursor() as cur:
        cur.execute(query)
        results = cur.fetchall()

    close_connection(conn)

    results.reverse()

    print('20260615当天及之前29个交易日的数据:')
    print('=' * 60)
    total = 0
    for i, r in enumerate(results):
        close_val = float(r['close'] or 0)
        total += close_val
        print(f'{i+1:2d}. {r["trade_date"]} | 收盘价: {close_val:.2f}')

    ma30 = total / len(results)
    close_615 = float(results[-1]['close'] or 0)
    
    print('=' * 60)
    print(f'MA30总和: {total:.2f}')
    print(f'MA30均值: {ma30:.4f}')
    print(f'20260615收盘价: {close_615:.2f}')
    print(f'收盘价 - MA30 = {close_615 - ma30:.4f}')
    print(f'结论: 收盘价{"高于" if close_615 > ma30 else "低于"}MA30')

    print('\n' + '=' * 60)
    print('20260612(前一日)的数据:')
    conn2 = get_mysql_connection()
    query2 = '''
    SELECT trade_date, close
    FROM stock_daily_t
    WHERE ts_code = '000783.SZ' AND trade_date <= '20260612'
    ORDER BY trade_date DESC
    LIMIT 30
    '''
    with conn2.cursor() as cur:
        cur.execute(query2)
        results2 = cur.fetchall()
    close_connection(conn2)
    
    results2.reverse()
    total2 = sum(float(r['close'] or 0) for r in results2)
    ma30_612 = total2 / len(results2)
    close_612 = float(results2[-1]['close'] or 0)
    
    print(f'20260612收盘价: {close_612:.2f}')
    print(f'20260612当天MA30: {ma30_612:.4f}')
    print(f'收盘价 - MA30 = {close_612 - ma30_612:.4f}')
    print(f'结论: 收盘价{"高于" if close_612 > ma30_612 else "低于"}MA30')
    
    print('\n' + '=' * 60)
    print('上穿条件判断:')
    print(f'前一日(20260612): 收盘价{close_612:.2f} < MA30{ma30_612:.2f}: {close_612 < ma30_612}')
    print(f'当日(20260615): 收盘价{close_615:.2f} > MA30{ma30:.2f}: {close_615 > ma30}')
    print(f'上穿条件满足: {(close_612 < ma30_612) and (close_615 > ma30)}')

if __name__ == '__main__':
    main()