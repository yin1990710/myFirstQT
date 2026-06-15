#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
回测select_newhigh_in_120d.py的选股条件
从20250801以来满足条件的第一天买入，计算持有10、20、60个交易日的最大区间收益
"""

import pandas as pd
from mysql_connection import get_mysql_connection, close_connection


def get_stock_list(conn, min_market_cap=100):
    """获取总市值超过min_market_cap亿的股票列表"""
    cursor = conn.cursor()
    query = """
    SELECT ts_code, stock_name, total_mv 
    FROM stock_info_t 
    WHERE total_mv > %s * 100000000
    """
    cursor.execute(query, (min_market_cap,))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def get_stock_data(conn, ts_code, start_date='20250401', end_date='20260630'):
    """获取指定股票的日线数据"""
    cursor = conn.cursor()
    query = """
    SELECT trade_date, close, amount 
    FROM stock_daily_t 
    WHERE ts_code = %s 
      AND trade_date >= %s 
      AND trade_date <= %s 
    ORDER BY trade_date
    """
    cursor.execute(query, (ts_code, start_date, end_date))
    rows = cursor.fetchall()
    cursor.close()
    return rows


def analyze_one_stock(conn, ts_code, stock_name, start_date='20250801'):
    """分析单只股票的突破信号和收益"""
    # 从20250401开始获取数据，确保有足够的历史数据
    data = get_stock_data(conn, ts_code, '20250401', '20260630')
    
    if len(data) < 180:
        return None
    
    df = pd.DataFrame(data)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df = df.dropna()
    
    # 找到start_date的索引
    start_idx = None
    for i, row in df.iterrows():
        if row['trade_date'] == start_date:
            start_idx = i
            break
    
    if start_idx is None:
        return None
    
    results = []
    
    # 遍历寻找突破信号（与select_newhigh_in_120d.py完全相同的逻辑）
    for i in range(start_idx, len(df)):
        # 需要有足够的历史数据（至少120天）
        if i < 119:
            continue
        
        # 取最近120个交易日
        window = df.iloc[i-119:i+1]
        
        if len(window) < 120:
            continue
        
        latest = window.iloc[-1]
        previous_119_days = window.iloc[:-1]
        
        if len(previous_119_days) < 60:
            continue
        
        # 取前119天收盘价的最高价和最低价
        max_close_119 = previous_119_days['close'].max()
        min_close_119 = previous_119_days['close'].min()
        
        if max_close_119 == 0 or min_close_119 == 0:
            continue
        
        # 条件1: 波动幅度 <= 35%
        amplitude = (max_close_119 - min_close_119) / min_close_119 * 100
        if amplitude > 35:
            continue
        
        # 条件2: 收盘价突破
        if latest['close'] <= max_close_119:
            continue
        
        # 条件3: 涨幅 > 5%
        previous_close = window.iloc[-2]['close']
        gain = (latest['close'] - previous_close) / previous_close * 100
        if gain <= 5:
            continue
        
        # 条件4: 成交额 > 5亿
        if latest['amount'] * 1000 <= 500000000:
            continue
        
        # 找到突破日（第一次满足条件）
        buy_date = latest['trade_date']
        buy_price = latest['close']
        
        # 计算后续收益
        if i + 60 >= len(df):
            future_data = df.iloc[i+1:]
        else:
            future_data = df.iloc[i+1:i+61]
        
        if len(future_data) < 10:
            continue
        
        max_return_10 = None
        if len(future_data) >= 10:
            max_10 = future_data.iloc[:10]['close'].max()
            max_return_10 = (max_10 - buy_price) / buy_price * 100
        
        max_return_20 = None
        if len(future_data) >= 20:
            max_20 = future_data.iloc[:20]['close'].max()
            max_return_20 = (max_20 - buy_price) / buy_price * 100
        
        max_return_60 = None
        if len(future_data) >= 60:
            max_60 = future_data.iloc[:60]['close'].max()
            max_return_60 = (max_60 - buy_price) / buy_price * 100
        
        results.append({
            'ts_code': ts_code,
            'stock_name': stock_name,
            'buy_date': buy_date,
            'buy_price': buy_price,
            'max_return_10d': max_return_10,
            'max_return_20d': max_return_20,
            'max_return_60d': max_return_60,
            'amplitude': amplitude,
            'gain': gain
        })
        
        # 只记录第一次突破
        break
    
    return results if results else None


def main():
    print("=" * 80)
    print("120日突破策略回测")
    print("=" * 80)
    
    conn = get_mysql_connection()
    if not conn:
        print("数据库连接失败")
        return
    
    print("\n获取股票列表...")
    stocks = get_stock_list(conn, 100)
    print(f"获取到 {len(stocks)} 只市值超100亿的股票")
    
    all_results = []
    
    print("\n开始回测 (从20250801开始检查)...")
    
    for i, stock in enumerate(stocks, 1):
        ts_code = stock['ts_code']
        stock_name = stock.get('stock_name', '')
        
        if i % 500 == 0:
            print(f"进度: {i}/{len(stocks)}")
        
        results = analyze_one_stock(conn, ts_code, stock_name)
        if results:
            all_results.extend(results)
    
    print(f"\n回测完成，共找到 {len(all_results)} 个突破信号")
    
    if all_results:
        df = pd.DataFrame(all_results)
        output_path = '120日突破回测.csv'
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\n结果已保存到: {output_path}")
        
        print("\n回测统计:")
        print("-" * 60)
        print(f"总突破信号数: {len(all_results)}")
        
        if df['max_return_10d'].notna().any():
            avg_10d = df['max_return_10d'].mean()
            print(f"平均10日最大收益: {avg_10d:.2f}%")
        
        if df['max_return_20d'].notna().any():
            avg_20d = df['max_return_20d'].mean()
            print(f"平均20日最大收益: {avg_20d:.2f}%")
        
        if df['max_return_60d'].notna().any():
            avg_60d = df['max_return_60d'].mean()
            print(f"平均60日最大收益: {avg_60d:.2f}%")
        
        # 打印部分结果
        print("\n部分突破信号:")
        print("-" * 100)
        print(f"{'股票代码':<12} {'股票名称':<12} {'买入日期':<10} {'买入价':<8} {'波动幅度':<8} {'涨幅':<8} {'10日收益':<10} {'20日收益':<10} {'60日收益':<10}")
        print("-" * 100)
        for r in df.head(10).to_dict('records'):
            print(f"{r['ts_code']:<12} {r['stock_name'][:12]:<12} {r['buy_date']:<10} {r['buy_price']:<8.2f} "
                  f"{r['amplitude']:>6.1f}%   "
                  f"{r['gain']:>6.1f}%   "
                  f"{r['max_return_10d']:>8.2f}%   "
                  f"{r['max_return_20d']:>8.2f}%   "
                  f"{r['max_return_60d']:>8.2f}%")
    else:
        print("\n未找到满足条件的股票")
        # 输出空的CSV文件
        output_path = '120日突破回测.csv'
        pd.DataFrame(columns=['ts_code', 'stock_name', 'buy_date', 'buy_price', 
                             'max_return_10d', 'max_return_20d', 'max_return_60d',
                             'amplitude', 'gain']).to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"已保存空结果到: {output_path}")
    
    close_connection(conn)


if __name__ == "__main__":
    main()