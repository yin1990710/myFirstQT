#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection
import pandas as pd

def main():
    conn = get_mysql_connection()
    
    query = """
    SELECT trade_date, close, amount 
    FROM stock_daily_t 
    WHERE ts_code = '603399.SH' 
      AND trade_date >= '20251201' 
      AND trade_date <= '20260421' 
    ORDER BY trade_date
    """
    cursor = conn.cursor()
    cursor.execute(query)
    data = cursor.fetchall()
    
    if not data:
        print("未找到数据")
        close_connection(conn)
        return
    
    df = pd.DataFrame(data)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df = df.dropna()
    
    print("603399.SH 数据统计")
    print(f"数据条数: {len(df)}")
    print(f"日期范围: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
    
    # 找到20260421的位置
    target_idx = None
    for i, row in df.iterrows():
        if row['trade_date'] == '20260421':
            target_idx = i
            break
    
    if target_idx is None:
        print("未找到20260421的数据")
        close_connection(conn)
        return
    
    if target_idx < 119:
        print("历史数据不足120天")
        close_connection(conn)
        return
    
    window = df.iloc[target_idx-119:target_idx+1]
    latest = window.iloc[-1]
    previous_119 = window.iloc[:-1]
    
    print("\n20260421数据:")
    print(f"收盘价: {latest['close']:.2f}")
    print(f"成交额: {latest['amount'] * 1000 / 100000000:.2f}亿")
    
    max_close_119 = previous_119['close'].max()
    min_close_119 = previous_119['close'].min()
    amplitude = (max_close_119 - min_close_119) / min_close_119 * 100
    
    print("\n前119天统计:")
    print(f"最高收盘价: {max_close_119:.2f}")
    print(f"最低收盘价: {min_close_119:.2f}")
    print(f"波动幅度: {amplitude:.2f}%")
    
    prev_close = df.iloc[target_idx-1]['close']
    gain = (latest['close'] - prev_close) / prev_close * 100
    print(f"\n涨幅: {gain:.2f}%")
    
    print("\n条件检查:")
    cond1 = latest['close'] > max_close_119
    cond2 = gain > 5
    cond3 = amplitude <= 35
    cond4 = latest['amount'] * 1000 > 500000000
    
    print(f"1. 收盘价突破: {cond1}")
    print(f"2. 涨幅>5%: {cond2}")
    print(f"3. 波动幅度<=35%: {cond3}")
    print(f"4. 成交额>5亿: {cond4}")
    
    all_conditions = cond1 and cond2 and cond3 and cond4
    print(f"\n综合判断: {'满足所有条件' if all_conditions else '不满足所有条件'}")
    
    close_connection(conn)

if __name__ == "__main__":
    main()
