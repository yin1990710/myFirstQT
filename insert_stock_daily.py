#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
根据输入的股票代码、起始日期、结束日期，从Tushare获取该股票的交易数据并插入stock_daily_t表。

用法:
    python insert_stock_daily.py <股票代码> <起始日期> <结束日期>
示例:
    python insert_stock_daily.py 002241.SZ 20260101 20260803
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

import tushare as ts
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


def get_stock_daily_qfq(ts_code, start_date, end_date):
    """从Tushare获取前复权日线数据"""
    df = ts.pro_bar(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        adj="qfq",
        fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
    )
    return df


def insert_data_to_db(ts_code, start_date, end_date):
    """获取数据并插入数据库"""
    print(f"📊 正在从Tushare获取 {ts_code} 的日线数据 ({start_date} ~ {end_date})...")

    df = get_stock_daily_qfq(ts_code, start_date, end_date)

    if df is None or df.empty:
        print(f"⚠️ 未获取到 {ts_code} 在 {start_date} ~ {end_date} 期间的数据")
        return 0

    df = df.sort_values('trade_date')
    print(f"✅ 获取到 {len(df)} 条数据")

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return 0

    inserted = 0
    try:
        cursor = conn.cursor()

        for _, row in df.iterrows():
            # 先删除已有记录，避免主键冲突
            delete_sql = "DELETE FROM stock_daily_t WHERE ts_code = %s AND trade_date = %s"
            cursor.execute(delete_sql, (row['ts_code'], row['trade_date']))

            insert_sql = """
                INSERT INTO stock_daily_t (
                    ts_code, trade_date, open, high, low, close, pre_close,
                    `change`, pct_chg, vol, amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_sql, (
                row['ts_code'],
                row['trade_date'],
                row.get('open'),
                row.get('high'),
                row.get('low'),
                row.get('close'),
                row.get('pre_close'),
                row.get('change'),
                row.get('pct_chg'),
                row.get('vol'),
                row.get('amount')
            ))
            inserted += 1

        conn.commit()
        cursor.close()
        print(f"✅ 成功插入 {inserted} 条数据到 stock_daily_t 表")

    except Exception as e:
        print(f"❌ 插入数据失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)

    return inserted


def main():
    if len(sys.argv) < 4:
        print("用法: python insert_stock_daily.py <股票代码> <起始日期> <结束日期>")
        print("示例: python insert_stock_daily.py 002241.SZ 20260101 20260803")
        sys.exit(1)

    ts_code = sys.argv[1]
    start_date = sys.argv[2]
    end_date = sys.argv[3]

    print("=" * 60)
    print(f"📈 股票日线数据获取工具")
    print(f"   股票代码: {ts_code}")
    print(f"   起始日期: {start_date}")
    print(f"   结束日期: {end_date}")
    print("=" * 60)

    insert_data_to_db(ts_code, start_date, end_date)

    print("\n🎉 完成！")


if __name__ == "__main__":
    main()
