#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
from datetime import datetime, timedelta

from mysql_connection import get_mysql_connection, close_connection

import tushare as ts
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

CALL_INTERVAL = 0.3


def get_stock_codes_from_db(conn):
    """
    从stock_info_t表读取所有股票代码（去除ST股和北证股）
    """
    try:
        cursor = conn.cursor()
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


def get_adj_factor(ts_code, start_date=None, end_date=None):
    """
    通过tushare接口获取前复权因子

    :param ts_code: 股票代码
    :param start_date: 开始日期，格式 'YYYYMMDD'
    :param end_date: 结束日期，格式 'YYYYMMDD'
    :return: 包含复权因子的DataFrame
    """
    df = pro.adj_factor(**{
        "ts_code": ts_code,
        "start_date": start_date,
        "end_date": end_date
    }, fields=[
        "ts_code",
        "trade_date",
        "adj_factor"
    ])
    return df


def update_qfq_factor(conn, ts_code, start_date, end_date):
    """
    获取复权因子并更新stock_daily_t表中的qfq_adj_factor字段

    :param conn: 数据库连接
    :param ts_code: 股票代码
    :param start_date: 开始日期
    :param end_date: 结束日期
    :return: 更新记录数
    """
    df = get_adj_factor(ts_code, start_date=start_date, end_date=end_date)

    if df is None or df.empty:
        return 0

    updated_count = 0
    cursor = conn.cursor()

    for _, row in df.iterrows():
        try:
            update_sql = """
                UPDATE stock_daily_t 
                SET qfq_adj_factor = %s 
                WHERE ts_code = %s AND trade_date = %s
            """
            cursor.execute(update_sql, (
                row.get('adj_factor'),
                ts_code,
                row['trade_date']
            ))
            updated_count += cursor.rowcount
        except Exception as e:
            print(f"   ⚠️ 更新 {ts_code} {row['trade_date']} 失败: {e}")

    conn.commit()
    cursor.close()
    return updated_count


def main():
    """
    主函数：批量更新stock_daily_t表中的qfq_adj_factor字段
    """
    # 解析命令行参数
    if len(sys.argv) >= 3:
        start_date = sys.argv[1]
        end_date = sys.argv[2]
    elif len(sys.argv) == 2:
        start_date = sys.argv[1]
        end_date = sys.argv[1]
    else:
        today = datetime.now().strftime('%Y%m%d')
        start_date = today
        end_date = today

    print("=" * 60)
    print("📊 股票前复权因子批量更新程序")
    print("=" * 60)
    print(f"\n📅 日期范围: {start_date} ~ {end_date}")

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

        print(f"\n🔄 步骤3: 批量获取复权因子并更新stock_daily_t...")

        total_updated = 0
        error_count = 0

        for i, ts_code in enumerate(stock_codes, 1):
            try:
                if i % 50 == 0:
                    print(f"   进度: {i}/{len(stock_codes)} ({i*100//len(stock_codes)}%), 已更新: {total_updated}")

                updated = update_qfq_factor(conn, ts_code, start_date, end_date)
                total_updated += updated

                time.sleep(CALL_INTERVAL)

            except Exception as e:
                error_count += 1
                print(f"   ⚠️ 处理 {ts_code} 时出错: {e}")

        print(f"\n📈 更新结果统计:")
        print("-" * 40)
        print(f"   处理股票数量: {len(stock_codes)}")
        print(f"   更新记录数: {total_updated}")
        print(f"   出错次数: {error_count}")

        print("\n🎉 前复权因子批量更新完成！")

    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
