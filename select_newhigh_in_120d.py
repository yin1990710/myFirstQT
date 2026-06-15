#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""

主要功能是从stock_daily_t表中查出最近120天股票交易数据，从stock_info_t表中查询股票的基本信息，通过ts_code将两个表关联起来，选出符合以下条件的股票：

1. 最近1个交易日收盘价（close），超过最近120天（不含最近一天）的最高收盘价（close）。
2. 最近1个交易日的涨幅（(close-前一日收盘价)/前一日收盘价）超过5%。

最后，将符合以上条件的股票的ts_code保存在生成一个名称为区间新高加当天日期（如果当前时间在0-15时之间，则取前一天日期）的csv文件，ts_code之间用英文逗号分隔，新建一个名称为区间新高加当天日期（如果当前时间在0-15时之间，则取前一天日期））的文件夹，将csv文件放在该文件夹下，如果文件夹已存在则先删除再新建。
"""

import os
import shutil
import pandas as pd
from datetime import datetime, timedelta

from mysql_connection import get_mysql_connection, close_connection


def get_date_170_days_ago() -> str:
    """获取170个自然日之前的日期，确保能取出120个交易日"""
    date_170_days_ago = datetime.now() - timedelta(days=170)
    return date_170_days_ago.strftime('%Y%m%d')


def get_trade_date() -> str:
    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 15:
        target_date = now - timedelta(days=1)
        return target_date.strftime('%Y%m%d')
    else:
        return now.strftime('%Y%m%d')


def get_stock_data(conn) -> pd.DataFrame:
    date_170_days_ago = get_date_170_days_ago()
    try:
        cursor = conn.cursor()
        sql = """
            SELECT
                d.ts_code,
                d.trade_date,
                d.open,
                d.high,
                d.low,
                d.close,
                d.vol,
                d.amount,
                i.stock_name
            FROM stock_daily_t d
            LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
            WHERE d.trade_date >= %s
            ORDER BY d.ts_code, d.trade_date
        """
        cursor.execute(sql, (date_170_days_ago,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        if rows and isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame(rows, columns=columns)

        return df
    except Exception as e:
        print(f"❌ 获取股票数据失败: {e}")
        return pd.DataFrame()


def analyze_newhigh_stocks(df: pd.DataFrame) -> list:
    qualified_stocks = []

    grouped = df.groupby('ts_code')

    for ts_code, group in grouped:
        group = group.sort_values('trade_date').reset_index(drop=True)

        # 取最近120个交易日
        if len(group) > 120:
            group = group.tail(120).reset_index(drop=True)

        group['close'] = pd.to_numeric(group['close'], errors='coerce')
        group['open'] = pd.to_numeric(group['open'], errors='coerce')
        group['high'] = pd.to_numeric(group['high'], errors='coerce')
        group['vol'] = pd.to_numeric(group['vol'], errors='coerce')
        group['amount'] = pd.to_numeric(group['amount'], errors='coerce')

        if len(group) < 120:
            continue

        latest = group.iloc[-1]
        previous_119_days = group.iloc[:-1]

        if len(previous_119_days) < 60:
            continue

        # 取前119天收盘价的最高价和最低价
        max_close_119 = previous_119_days['close'].max()
        min_close_119 = previous_119_days['close'].min()

        if max_close_119 == 0 or min_close_119 == 0:
            continue

        # 检查波动幅度 <= 30%
        amplitude = (max_close_119 - min_close_119) / min_close_119 * 100
        if amplitude > 35:
            continue

        if latest['close'] <= max_close_119:
            continue

        previous_close = group.iloc[-2]['close']
        gain = (latest['close'] - previous_close) / previous_close * 100

        if gain <= 5:
            continue

        if latest['amount'] * 1000 <= 500000000:
            continue

        break_ratio = (latest['close'] - max_close_119) / max_close_119 * 100

        qualified_stocks.append({
            'ts_code': ts_code,
            'stock_name': group.iloc[0].get('stock_name', ''),
            'latest_close': latest['close'],
            'max_close_119': max_close_119,
            'break_ratio': break_ratio,
            'gain': gain
        })

    return qualified_stocks


def save_results_to_csv(results: list, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)

    csv_path = os.path.join(output_dir, f"区间新高{get_trade_date()}.csv")
    with open(csv_path, 'w') as f:
        ts_codes = [r['ts_code'] for r in results]
        f.write(','.join(ts_codes))

    detail_csv_path = os.path.join(output_dir, f"区间新高详情{get_trade_date()}.csv")
    df = pd.DataFrame(results)
    if not df.empty:
        df.to_csv(detail_csv_path, index=False, encoding='utf-8-sig')


def main():
    print("=" * 60)
    print("📊 区间新高选股模型")
    print("=" * 60)

    trade_date = get_trade_date()
    print(f"\n⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 输出日期: {trade_date} (前一日)")

    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    print("🔌 正在连接数据库: root@localhost:3306/stock_daily_db")
    print("✅ 数据库连接成功！")

    print("\n📋 步骤2: 获取股票数据...")
    df = get_stock_data(conn)
    if df.empty:
        print("❌ 没有获取到数据")
        close_connection(conn)
        return

    print(f"   ✅ 获取到 {len(df)} 条记录")

    print("\n📈 步骤3: 分析区间新高股票...")
    results = analyze_newhigh_stocks(df)
    print(f"   ✅ 找到 {len(results)} 只符合条件的股票")

    if results:
        print("\n📊 符合条件的股票列表:")
        print("-" * 80)
        print(f"{'股票代码':<12} {'股票名称':<15} {'最新收盘':<10} {'120日收盘最高':<12} {'突破幅度':<10} {'涨幅(%)':<10}")
        print("-" * 80)

        for r in results[:20]:
            print(f"{r['ts_code']:<12} {r['stock_name']:<15} "
                  f"{r['latest_close']:>8.2f}   "
                  f"{r['max_close_119']:>10.2f}   "
                  f"{r['break_ratio']:>7.2f}%   "
                  f"{r['gain']:>7.2f}%")

    print("\n💾 步骤4: 保存结果到CSV文件...")
    folder_name = f"区间新高{trade_date}"
    output_dir = os.path.join(os.getcwd(), folder_name)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

    save_results_to_csv(results, output_dir)
    print(f"   ✅ 已保存到: {output_dir}")

    print(f"\n🎉 选股完成！共选出 {len(results)} 只股票")
    close_connection(conn)


if __name__ == "__main__":
    main()