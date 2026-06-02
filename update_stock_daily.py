#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
from datetime import datetime, timedelta

from mysql_connection import get_mysql_connection, close_connection
from tushare_get_daily import get_stock_daily


""" 更新数据库stock_daily_t表中的股票日线数据 """

CALL_INTERVAL = 1

def get_stock_codes_from_db(conn):
    """
    从数据库stock_info_t表读取所有股票代码（去除ST股和北证股）

    :param conn: 数据库连接
    :return: 股票代码列表
    """
    try:
        cursor = conn.cursor()
        # 过滤条件：
        # 1. 去除ST股（股票名称包含ST、*ST、S*ST）
        # 2. 去除北证股（股票代码以.BJ结尾）
        sql = """
            SELECT ts_code 
            FROM stock_info_t 
            WHERE ts_code IS NOT NULL 
              AND ts_code NOT LIKE '%.BJ'
              AND stock_name NOT LIKE '%ST%'
            GROUP BY ts_code
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        if isinstance(rows[0], dict):
            codes = [row['ts_code'] for row in rows]
        else:
            codes = [row[0] for row in rows]
        return codes
    except Exception as e:
        print(f"❌ 读取股票代码失败: {e}")
        return []

def is_data_exists(conn, ts_code, trade_date):
    """
    检查数据是否已存在

    :param conn: 数据库连接
    :param ts_code: 股票代码
    :param trade_date: 交易日期
    :return: 是否存在
    """
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM stock_daily_t WHERE ts_code = %s AND trade_date = %s",
            (ts_code, trade_date)
        )
        result = cursor.fetchone()
        if isinstance(result, dict):
            count = result['COUNT(*)']
        else:
            count = result[0]
        return count > 0
    except Exception as e:
        print(f"⚠️ 检查数据存在性失败: {e}")
        return False

def insert_stock_daily(conn, df):
    """
    将股票日线数据插入数据库

    :param conn: 数据库连接
    :param df: 包含日线数据的DataFrame
    :return: 成功插入的记录数
    """
    if df is None or df.empty:
        return 0

    inserted_count = 0
    cursor = conn.cursor()

    for _, row in df.iterrows():
        try:
            ts_code = row['ts_code']
            trade_date = row['trade_date']

            if is_data_exists(conn, ts_code, trade_date):
                continue

            insert_sql = """
                INSERT INTO stock_daily_t (
                    ts_code, trade_date, open, high, low, close, pre_close,
                    `change`, pct_chg, vol, amount
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            cursor.execute(insert_sql, (
                ts_code,
                trade_date,
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
            inserted_count += 1

        except Exception as e:
            print(f"⚠️ 插入数据失败: {e}")

    conn.commit()
    cursor.close()
    return inserted_count

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

def main():
    """
    主函数：从stock_info_t读取股票代码，查询最新交易数据并插入stock_daily_t
    """
    print("=" * 60)
    print("📊 股票日线数据更新程序")
    print("=" * 60)

    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库，程序退出")
        sys.exit(1)

    try:
        print("\n📋 步骤2: 从stock_info_t读取股票代码...")
        stock_codes = get_stock_codes_from_db(conn)
        if not stock_codes:
            print("❌ 没有找到股票代码，程序退出")
            return

        print(f"   ✅ 共读取到 {len(stock_codes)} 个股票代码")

        latest_date = get_latest_trade_date()
        print(f"\n📅 步骤3: 查询最新交易日数据 ({latest_date})...")

        total_inserted = 0
        total_updated = 0
        error_count = 0

        for i, ts_code in enumerate(stock_codes, 1):
            try:
                if i % 10 == 0:
                    print(f"   进度: {i}/{len(stock_codes)} ({i*100//len(stock_codes)}%)")

                df = get_stock_daily(ts_code, start_date=latest_date, end_date=latest_date, limit=1)

                if df is not None and not df.empty:
                    inserted = insert_stock_daily(conn, df)
                    total_inserted += inserted

                #time.sleep(CALL_INTERVAL)

            except Exception as e:
                error_count += 1
                print(f"   ⚠️ 处理 {ts_code} 时出错: {e}")

        print(f"\n📈 更新结果统计:")
        print("-" * 40)
        print(f"   处理股票数量: {len(stock_codes)}")
        print(f"   新增记录数: {total_inserted}")
        print(f"   出错次数: {error_count}")

        print("\n🎉 股票日线数据更新完成！")

    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()
