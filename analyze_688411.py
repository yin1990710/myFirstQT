#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
分析688411.SH股票是否满足select_newhigh_in_120d.py的选股条件
"""

import pandas as pd
from mysql_connection import get_mysql_connection, close_connection


def main():
    conn = get_mysql_connection()
    if not conn:
        print("数据库连接失败")
        return

    cursor = conn.cursor()
    
    # 获取688411.SH最近170个自然日的数据
    query = """
    SELECT trade_date, close, amount, high, low, open
    FROM stock_daily_t 
    WHERE ts_code = '688411.SH' 
      AND trade_date >= '20251201'
    ORDER BY trade_date
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    
    if not rows:
        print("未获取到数据")
        close_connection(conn)
        return
    
    print(f"获取到 {len(rows)} 条数据")
    
    df = pd.DataFrame(rows)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df['high'] = pd.to_numeric(df['high'], errors='coerce')
    df['low'] = pd.to_numeric(df['low'], errors='coerce')
    df = df.dropna()
    
    print(f"有效数据: {len(df)} 条")
    print(f"日期范围: {df.iloc[0]['trade_date']} ~ {df.iloc[-1]['trade_date']}")
    
    # 取最近120个交易日
    if len(df) > 120:
        group = df.tail(120).reset_index(drop=True)
    else:
        group = df.reset_index(drop=True)
    
    print(f"\n使用最近 {len(group)} 个交易日数据")
    
    if len(group) < 120:
        print(f"\n❌ 条件1: 数据不足120个交易日")
        close_connection(conn)
        return
    
    latest = group.iloc[-1]
    previous_119_days = group.iloc[:-1]
    
    print(f"\n📊 最新数据 (日期: {latest['trade_date']}):")
    print(f"   收盘价: {latest['close']:.2f}")
    print(f"   成交额: {latest['amount'] * 1000:,.2f}")
    
    # 条件1: 前119天收盘价最高
    max_close_119 = previous_119_days['close'].max()
    min_close_119 = previous_119_days['close'].min()
    print(f"\n📊 前119天数据:")
    print(f"   最高收盘价: {max_close_119:.2f}")
    print(f"   最低收盘价: {min_close_119:.2f}")
    
    # 条件2: 波动幅度 <= 35%
    amplitude = (max_close_119 - min_close_119) / min_close_119 * 100
    print(f"   波动幅度: {amplitude:.2f}%")
    
    # 条件3: 收盘价突破
    print(f"\n📊 突破条件检查:")
    print(f"   今日收盘价: {latest['close']:.2f}")
    print(f"   前119天最高收盘价: {max_close_119:.2f}")
    print(f"   是否突破: {latest['close'] > max_close_119}")
    
    # 条件4: 涨幅 > 5%
    previous_close = group.iloc[-2]['close']
    gain = (latest['close'] - previous_close) / previous_close * 100
    print(f"\n📊 涨幅条件检查:")
    print(f"   前一日收盘价: {previous_close:.2f}")
    print(f"   今日涨幅: {gain:.2f}%")
    print(f"   是否超过5%: {gain > 5}")
    
    # 条件5: 成交额 > 5亿
    amount_check = latest['amount'] * 1000 > 500000000
    print(f"\n📊 成交额条件检查:")
    print(f"   今日成交额: {latest['amount'] * 1000:,.2f}")
    print(f"   是否超过5亿: {amount_check}")
    
    print("\n" + "=" * 60)
    print("✅ 满足的条件:")
    print("-" * 60)
    
    if len(group) >= 120:
        print("   ✓ 数据足够(>=120个交易日)")
    
    if amplitude <= 35:
        print(f"   ✓ 波动幅度 <= 35% ({amplitude:.2f}%)")
    else:
        print(f"   ✗ 波动幅度 > 35% ({amplitude:.2f}%)")
    
    if latest['close'] > max_close_119:
        print(f"   ✓ 收盘价突破前119天最高 ({latest['close']:.2f} > {max_close_119:.2f})")
    else:
        print(f"   ✗ 收盘价未突破前119天最高 ({latest['close']:.2f} <= {max_close_119:.2f})")
    
    if gain > 5:
        print(f"   ✓ 涨幅超过5% ({gain:.2f}%)")
    else:
        print(f"   ✗ 涨幅未超过5% ({gain:.2f}%)")
    
    if amount_check:
        print(f"   ✓ 成交额超过5亿")
    else:
        print(f"   ✗ 成交额未超过5亿")
    
    close_connection(conn)


if __name__ == "__main__":
    main()