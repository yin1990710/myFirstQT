#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_latest_trade_date(conn):
    """获取数据库中最近的交易日期（限制查询最近20个自然日的数据）"""
    cutoff_date = (datetime.now() - timedelta(days=20)).strftime('%Y%m%d')
    sql = "SELECT MAX(trade_date) AS latest_date FROM stock_daily_t WHERE trade_date >= %s"
    with conn.cursor() as cursor:
        cursor.execute(sql, (cutoff_date,))
        row = cursor.fetchone()
    return row['latest_date'] if row else None


def read_stock_data(conn, start_date, end_date, fetch_start_date):
    """读取指定日期范围的股票数据（从fetch_start_date开始，确保有足够数据计算MA30）"""
    query_sql = """
        SELECT ts_code, trade_date, close
        FROM stock_daily_t
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY ts_code, trade_date
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query_sql, (fetch_start_date, end_date))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据（范围: {fetch_start_date} ~ {end_date}）")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return None


def calculate_ma_and_update(data, conn, start_date, end_date):
    """计算MA5和MA30并更新到数据库"""
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close'] or 0)
        })

    update_count = 0
    total_stocks = len(stock_data)

    try:
        with conn.cursor() as cursor:
            for ts_code, records in stock_data.items():
                records.sort(key=lambda x: x['trade_date'])

                for i, record in enumerate(records):
                    trade_date = record['trade_date']
                    # 只更新 start_date ~ end_date 范围内的日期
                    if trade_date < start_date or trade_date > end_date:
                        continue

                    closes = [r['close'] for r in records[:i+1]]

                    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
                    ma30 = sum(closes[-30:]) / 30 if len(closes) >= 30 else None

                    if ma5 is not None and ma30 is not None:
                        update_sql = """
                            UPDATE stock_daily_t
                            SET ma5 = %s, ma30 = %s
                            WHERE ts_code = %s AND trade_date = %s
                        """
                        cursor.execute(update_sql, (ma5, ma30, ts_code, trade_date))
                        update_count += 1

            conn.commit()
        print(f"✅ 成功更新 {update_count} 条记录的MA5和MA30（{total_stocks} 只股票）")
    except Exception as e:
        print(f"❌ 更新数据失败: {e}")
        conn.rollback()


def main():
    print("=" * 80)
    print("📊 更新股票MA5和MA30")
    print("=" * 80)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    try:
        # 获取最新交易日期作为默认值
        latest_date = get_latest_trade_date(conn)

        # 解析命令行参数
        if len(sys.argv) >= 3:
            start_date = sys.argv[1]
            end_date = sys.argv[2]
        elif len(sys.argv) == 2:
            start_date = sys.argv[1]
            end_date = sys.argv[1]
        else:
            start_date = latest_date
            end_date = latest_date

        # 计算抓取数据的起始日期（start_date - 42天，确保有足够数据计算MA30）
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        fetch_start_dt = start_dt - timedelta(days=42)
        fetch_start_date = fetch_start_dt.strftime('%Y%m%d')

        print(f"📅 目标日期范围: {start_date} ~ {end_date}")
        print(f"📅 数据抓取范围: {fetch_start_date} ~ {end_date}")

        data = read_stock_data(conn, start_date, end_date, fetch_start_date)
        if data is None or len(data) == 0:
            print("❌ 未读取到数据")
            return

        calculate_ma_and_update(data, conn, start_date, end_date)
    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
