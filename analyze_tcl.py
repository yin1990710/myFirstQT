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

    query = 'SELECT ts_code, stock_name, total_mv FROM stock_info_t WHERE ts_code = "000100.SZ"'
    with conn.cursor() as cur:
        cur.execute(query)
        result = cur.fetchone()

    if result:
        print(f'股票信息: {result["stock_name"]}({result["ts_code"]}), 市值: {result["total_mv"]/100000000:.2f}亿')
    else:
        print('未找到股票信息')
        sys.exit(1)

    query2 = '''
    SELECT trade_date, close, amount
    FROM stock_daily_t
    WHERE ts_code = '000100.SZ' AND trade_date <= '20260721'
    ORDER BY trade_date DESC
    LIMIT 45
    '''

    with conn.cursor() as cur:
        cur.execute(query2)
        results = cur.fetchall()

    close_connection(conn)

    results.reverse()

    print('\n近45个交易日数据:')
    print('=' * 80)
    print(f'{"日期":<12} {"收盘价":<8} {"成交额(万)":<12}')
    print('-' * 80)
    for r in results:
        print(f'{r["trade_date"]:<12} {float(r["close"] or 0):<8.2f} {float(r["amount"] or 0):<12.2f}')

    print('\n' + '=' * 80)
    print('选股条件分析:')
    print('=' * 80)

    market_cap = float(result['total_mv'] or 0)
    print(f'条件a: 市值>100亿: {"✅" if market_cap > 10000000000 else "❌"} ({market_cap/100000000:.2f}亿)')

    ma30_list = []
    for i in range(len(results) - 29, len(results)):
        ma30 = sum(float(r['close'] or 0) for r in results[i-29:i+1]) / 30
        ma30_list.append(ma30)

    recent_ma30 = ma30_list[-10:]
    print(f'\n近10日MA30值:')
    for i, v in enumerate(recent_ma30):
        date = results[len(results)-10+i]['trade_date']
        print(f'  {date}: {v:.4f}')

    is_ma30_increasing = True
    for i in range(1, len(recent_ma30)):
        if recent_ma30[i] <= recent_ma30[i-1]:
            is_ma30_increasing = False
            break

    print(f'\n条件c: MA30近10日单调递增: {"✅" if is_ma30_increasing else "❌"}')

    prev_close = float(results[-2]['close'] or 0)
    current_close = float(results[-1]['close'] or 0)
    prev_ma30 = recent_ma30[-2]
    current_ma30 = recent_ma30[-1]

    print(f'\n条件e: 收盘价上穿MA30')
    print(f'  前一日: 收盘价={prev_close:.2f}, MA30={prev_ma30:.4f}, 收盘价<MA30: {prev_close < prev_ma30}')
    print(f'  当日: 收盘价={current_close:.2f}, MA30={current_ma30:.4f}, 收盘价>MA30: {current_close > current_ma30}')

    cross_condition = (prev_close < prev_ma30) and (current_close > current_ma30)
    print(f'  上穿条件满足: {"✅" if cross_condition else "❌"}')

    current_amount = float(results[-1]['amount'] or 0) * 1000
    prev_amount = float(results[-2]['amount'] or 0) * 1000

    print(f'\n条件f: 成交额>5亿且是前一日1.5倍以上')
    print(f'  当日成交额: {current_amount/100000000:.2f}亿')
    print(f'  前一日成交额: {prev_amount/100000000:.2f}亿')
    if prev_amount > 0:
        print(f'  成交额倍数: {current_amount/prev_amount:.2f}倍')

    amount_ok = (current_amount > 500000000) and (prev_amount > 0 and current_amount > prev_amount * 1.5)
    print(f'  成交额条件满足: {"✅" if amount_ok else "❌"}')

    print('\n' + '=' * 80)
    all_ok = market_cap > 10000000000 and is_ma30_increasing and cross_condition and amount_ok
    print(f'综合判断: {"✅ TCL科技满足所有选股条件" if all_ok else "❌ TCL科技不满足选股条件"}')

if __name__ == '__main__':
    main()