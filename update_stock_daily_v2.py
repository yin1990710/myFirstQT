#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


""" 更新数据库stock_daily_t表中的股票日线数据（性能优化版）

与 update_stock_daily.py 的区别：
1. 按 trade_date 查询全市场数据，每交易日仅2次tushare API调用（原版每只股票2次，共1万+次）
2. INSERT IGNORE + executemany 批量插入，替代逐行先查后插（依赖唯一索引 uk_ts_date 防重）
3. 全部日期一次 commit，减少磁盘 fsync 开销
"""

DAILY_FIELDS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount"
]


def get_market_daily(trade_date):
    """按交易日获取全市场日线数据（单次API调用）"""
    return pro.daily(trade_date=trade_date, fields=DAILY_FIELDS)


def get_market_adj_factor(trade_date):
    """按交易日获取全市场前复权因子（单次API调用）"""
    return pro.adj_factor(trade_date=trade_date, fields=["ts_code", "trade_date", "adj_factor"])


def get_trade_dates(start_date, end_date):
    """获取日期范围内的交易日列表"""
    df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return []
    df = df[df['is_open'] == 1]
    return sorted(df['cal_date'].tolist())


def get_valid_stock_codes(conn):
    """
    从数据库stock_info_t表读取所有股票代码（去除ST股和北证股）

    :param conn: 数据库连接
    :return: 股票代码集合
    """
    sql = """
        SELECT ts_code
        FROM stock_info_t
        WHERE ts_code IS NOT NULL
          AND ts_code NOT LIKE '%.BJ'
          AND stock_name NOT LIKE '%ST%'
        GROUP BY ts_code
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    if rows and isinstance(rows[0], dict):
        return {row['ts_code'] for row in rows}
    return {row[0] for row in rows}


def insert_stock_daily_batch(conn, df):
    """
    INSERT IGNORE + executemany 批量插入股票日线数据
    依赖唯一索引 uk_ts_date(ts_code, trade_date) 自动跳过已存在记录

    :param conn: 数据库连接
    :param df: 包含日线数据的DataFrame
    :return: 成功插入的记录数
    """
    if df is None or df.empty:
        return 0

    # NaN 替换为 None，避免插入 nan
    df = df.where(pd.notnull(df), None)

    insert_sql = """
        INSERT IGNORE INTO stock_daily_t (
            ts_code, trade_date, open, high, low, close, pre_close,
            `change`, pct_chg, vol, amount, qfq_adj_factor
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    data = [
        (row.ts_code, row.trade_date, row.open, row.high, row.low, row.close,
         row.pre_close, row.change, row.pct_chg, row.vol, row.amount, row.adj_factor)
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cursor:
        inserted = cursor.executemany(insert_sql, data)
    conn.commit()
    return inserted if inserted else 0


def get_latest_trade_date():
    """
    获取最近一个交易日日期
    如果当前时间为0-15点则取前一个交易日日期

    :return: 日期字符串 YYYYMMDD
    """
    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 15:
        target_date = now - timedelta(days=1)
        return target_date.strftime('%Y%m%d')
    else:
        return now.strftime('%Y%m%d')


def process_one_day(conn, trade_date, valid_codes):
    """
    处理单个交易日：全市场拉取日线+复权因子，合并后批量插入

    :param conn: 数据库连接
    :param trade_date: 交易日 YYYYMMDD
    :param valid_codes: 有效股票代码集合（去ST、去北证）
    :return: (全市场记录数, 新增记录数)
    """
    # 单次API获取全市场日线数据
    df_daily = get_market_daily(trade_date)
    if df_daily is None or df_daily.empty:
        print(f"   ⚠️ {trade_date} 无日线数据")
        return 0, 0

    # 过滤：仅保留有效股票（去ST、去北证）
    df_daily = df_daily[df_daily['ts_code'].isin(valid_codes)]

    # 单次API获取全市场复权因子并关联
    df_factor = get_market_adj_factor(trade_date)
    if df_factor is not None and not df_factor.empty:
        df_daily = df_daily.merge(df_factor[['trade_date', 'adj_factor']],
                                  on='trade_date', how='left')
    else:
        df_daily['adj_factor'] = None

    inserted = insert_stock_daily_batch(conn, df_daily)
    return len(df_daily), inserted


def main():
    """
    主函数：按交易日查询全市场日线数据与复权因子，批量插入stock_daily_t
    可通过命令行参数指定开始日期和结束日期，不指定则默认取当天日期
    """
    start_time = time.time()

    # 解析命令行参数
    if len(sys.argv) >= 3:
        start_date = sys.argv[1]
        end_date = sys.argv[2]
    elif len(sys.argv) == 2:
        start_date = sys.argv[1]
        end_date = sys.argv[1]
    else:
        today = get_latest_trade_date()
        start_date = today
        end_date = today

    print("=" * 60)
    print("📊 股票日线数据更新程序（全市场批量版）")
    print("=" * 60)

    print(f"\n📅 日期范围: {start_date} ~ {end_date}")

    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库，程序退出")
        sys.exit(1)

    try:
        print("\n📋 步骤2: 从stock_info_t读取有效股票代码...")
        valid_codes = get_valid_stock_codes(conn)
        if not valid_codes:
            print("❌ 没有找到股票代码，程序退出")
            return
        print(f"   ✅ 共读取到 {len(valid_codes)} 个有效股票代码")

        print("\n📅 步骤3: 获取交易日列表...")
        trade_dates = get_trade_dates(start_date, end_date)
        if not trade_dates:
            print("❌ 日期范围内没有交易日，程序退出")
            return
        print(f"   ✅ 共 {len(trade_dates)} 个交易日: {trade_dates[0]} ~ {trade_dates[-1]}")

        total_rows = 0
        total_inserted = 0
        error_count = 0

        for i, trade_date in enumerate(trade_dates, 1):
            try:
                rows, inserted = process_one_day(conn, trade_date, valid_codes)
                total_rows += rows
                total_inserted += inserted
                print(f"   [{i}/{len(trade_dates)}] {trade_date}: 全市场{rows}条, 新增{inserted}条")
            except Exception as e:
                error_count += 1
                print(f"   ⚠️ 处理 {trade_date} 时出错: {e}")

        elapsed = time.time() - start_time
        print(f"\n📈 更新结果统计:")
        print("-" * 40)
        print(f"   处理交易日数: {len(trade_dates)}")
        print(f"   全市场记录数: {total_rows}")
        print(f"   新增记录数: {total_inserted}")
        print(f"   出错次数: {error_count}")
        print(f"   总耗时: {elapsed:.1f} 秒")

        print("\n🎉 股票日线数据更新完成！")

    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()
