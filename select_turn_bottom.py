#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
底部放量反转选股策略

选股条件：
1. 最近200个交易日，最低收盘价/最高收盘价 < 50%
2. 最近200个交易日的最低价出现在最近15个交易日内
3. 股票总市值在50亿以上
4. 最近10个交易日：
   - 至少4个阳线（close>open）
   - 阳线成交额均>5亿（amount>500000千元）
   - 阳线平均成交额 >= 阴线平均成交额 × 1.5
"""

import os
import shutil
import pandas as pd
from datetime import datetime
from mysql_connection import get_mysql_connection, close_connection


def get_folder_name() -> str:
    """生成输出文件夹名称（底部放量反转+日期）"""
    now = datetime.now()
    if 0 <= now.hour < 15:
        date_str = (now - pd.Timedelta(days=1)).strftime('%Y%m%d')
    else:
        date_str = now.strftime('%Y%m%d')
    return f"底部放量反转{date_str}"


def get_stock_data(conn) -> pd.DataFrame:
    """获取股票数据（最近200天）"""
    try:
        cursor = conn.cursor()
        sql = """
            SELECT 
                d.ts_code,
                d.trade_date,
                d.open,
                d.close,
                d.amount,
                i.stock_name,
                i.total_mv
            FROM stock_daily_t d
            LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
            ORDER BY d.ts_code, d.trade_date
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        df = pd.DataFrame(rows, columns=columns)
        
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['open'] = pd.to_numeric(df['open'], errors='coerce')
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
        
        return df
    except Exception as e:
        print(f"❌ 获取股票数据失败: {e}")
        return pd.DataFrame()


def analyze_turn_bottom_stocks(df: pd.DataFrame) -> list:
    """分析底部反转股票"""
    qualified_stocks = []

    grouped = df.groupby('ts_code')

    for ts_code, group in grouped:
        group = group.sort_values('trade_date').reset_index(drop=True)

        if len(group) < 200:
            continue

        # 条件3：市值>50亿
        total_mv = group.iloc[-1].get('total_mv', 0)
        if total_mv <= 5000000000:
            continue

        # 最近200天数据
        recent_200 = group.tail(200)
        min_close_200 = recent_200['close'].min()
        max_close_200 = recent_200['close'].max()

        if max_close_200 == 0:
            continue

        # 条件1：最低价/最高价 < 50%
        ratio = min_close_200 / max_close_200
        if ratio >= 0.5:
            continue

        # 条件2：最低价出现在最近15个交易日内
        recent_200_reset = recent_200.reset_index(drop=True)
        min_close_date = recent_200_reset[recent_200_reset['close'] == min_close_200]['trade_date'].iloc[-1]
        min_index = recent_200_reset[recent_200_reset['trade_date'] == min_close_date].index[0]
        days_since_min = len(recent_200_reset) - 1 - min_index
        if days_since_min > 15:
            continue

        # 条件4：最近10个交易日分析
        recent_10 = group.tail(10)
        
        # 阳线数量
        up_days = recent_10[recent_10['close'] > recent_10['open']]
        if len(up_days) < 4:
            continue

        # 阳线成交额均>5亿
        up_amounts = up_days['amount']
        if any(up_amounts < 500000):  # 5亿 = 500000千元
            continue

        # 阳线平均成交额 >= 阴线平均成交额 × 1.5
        down_days = recent_10[recent_10['close'] < recent_10['open']]
        if len(down_days) == 0:
            continue
        
        avg_up_amount = up_amounts.mean()
        avg_down_amount = down_days['amount'].mean()
        
        if avg_down_amount == 0:
            continue
        
        if avg_up_amount < avg_down_amount * 1.5:
            continue

        stock_name = group.iloc[-1].get('stock_name', '')
        
        qualified_stocks.append({
            'ts_code': ts_code,
            'stock_name': stock_name,
            'latest_close': group.iloc[-1]['close'],
            'min_close_200': min_close_200,
            'max_close_200': max_close_200,
            'ratio': ratio,
            'days_since_min': days_since_min,
            'up_days_count': len(up_days),
            'avg_up_amount': avg_up_amount,
            'avg_down_amount': avg_down_amount,
            'up_down_ratio': avg_up_amount / avg_down_amount
        })

    return qualified_stocks


def main():
    print("=" * 80)
    print("📊 底部放量反转选股策略")
    print("=" * 80)

    now = datetime.now()
    print(f"\n⏰ 当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    folder_name = get_folder_name()
    print(f"📅 输出文件夹: {folder_name}")

    # 创建输出目录
    if os.path.exists(folder_name):
        print(f"\n🗑️ 删除旧目录")
        shutil.rmtree(folder_name)
    os.makedirs(folder_name)

    print("\n🔌 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    print("\n📋 获取股票数据...")
    df = get_stock_data(conn)
    if df.empty:
        print("❌ 没有获取到股票数据")
        close_connection(conn)
        return
    print(f"   ✅ 获取到 {len(df)} 条记录")

    print("\n📈 分析底部反转股票...")
    qualified_stocks = analyze_turn_bottom_stocks(df)
    print(f"   ✅ 找到 {len(qualified_stocks)} 只符合条件的股票")

    if qualified_stocks:
        print("\n📊 符合条件的股票列表:")
        print("-" * 100)
        print(f"{'股票代码':<15} {'股票名称':<20} {'最新收盘':<10} {'低/高比':<8} {'距低点天数':<10} {'阳线数':<6}")
        print("-" * 100)
        
        for stock in qualified_stocks:
            print(f"{stock['ts_code']:<15} {stock['stock_name']:<20} {stock['latest_close']:<10.2f} {stock['ratio']:<8.2%} {stock['days_since_min']:<10} {stock['up_days_count']:<6}")

        # 保存CSV
        result_df = pd.DataFrame(qualified_stocks)
        csv_path = os.path.join(folder_name, '底部反转股票.csv')
        result_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"\n💾 已保存到: {csv_path}")

        # 保存股票代码列表
        codes_path = os.path.join(folder_name, '股票代码.txt')
        with open(codes_path, 'w', encoding='utf-8') as f:
            f.write(','.join([s['ts_code'] for s in qualified_stocks]))
        print(f"💾 股票代码列表已保存到: {codes_path}")
    else:
        print("\n❌ 没有找到符合条件的股票")

    close_connection(conn)
    print(f"\n🎉 选股完成！")


if __name__ == "__main__":
    main()