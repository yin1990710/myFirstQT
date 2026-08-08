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


def read_stock_data(conn, fetch_start_date, fetch_end_date):
    """读取指定日期范围的股票数据"""
    query_sql = """
        SELECT ts_code, trade_date, ma5, close
        FROM stock_daily_t
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY ts_code, trade_date DESC
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query_sql, (fetch_start_date, fetch_end_date))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据（范围: {fetch_start_date} ~ {fetch_end_date}）")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return None


def fix_turning_points(tags):
    """修正标记：根据前后各3个非NULL标记的交易日，判断波峰/波谷"""
    n = len(tags)
    for i in range(n):
        if tags[i] is None:
            continue

        # 向前找3个非NULL标记
        before = []
        j = i - 1
        while j >= 0 and len(before) < 3:
            if tags[j] is not None:
                before.append(tags[j])
            j -= 1

        # 向后找3个非NULL标记
        after = []
        j = i + 1
        while j < n and len(after) < 3:
            if tags[j] is not None:
                after.append(tags[j])
            j += 1

        if len(before) < 3 or len(after) < 3:
            continue

        before_up = sum(1 for t in before if t == '上升')
        before_down = sum(1 for t in before if t == '下降')
        after_up = sum(1 for t in after if t == '上升')
        after_down = sum(1 for t in after if t == '下降')

        # 前3个多数上升，后3个多数下降 → 波峰
        if before_up >= 2 and after_down >= 2:
            tags[i] = '波峰'
        # 前3个多数下降，后3个多数上升 → 波谷
        elif before_down >= 2 and after_up >= 2:
            tags[i] = '波谷'

    return tags


def analyze_and_update(data, conn, start_date, end_date):
    """计算turning_point并更新到数据库"""
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'ma5': float(record['ma5']) if record['ma5'] is not None else None,
            'close': float(record['close']) if record['close'] is not None else 0
        })

    update_count = 0
    total_stocks = len(stock_data)

    try:
        with conn.cursor() as cursor:
            for ts_code, records in stock_data.items():
                if len(records) < 7:
                    continue

                records.sort(key=lambda x: x['trade_date'])

                ma5_values = [r['ma5'] for r in records]
                tags = [None] * len(records)

                # 第一步：根据斜率计算初始标记（上升/下降/NULL）
                for i in range(len(records)):
                    if i < 3 or i >= len(records) - 3:
                        continue

                    ma5_before3 = ma5_values[i - 3]
                    ma5_current = ma5_values[i]
                    ma5_after3 = ma5_values[i + 3]

                    # T-3和T+3的ma5值都大于0才继续
                    if ma5_before3 is None or ma5_before3 <= 0 or ma5_current is None or ma5_current <= 0 or ma5_after3 is None or ma5_after3 <= 0:
                        continue

                    # 计算斜率a1（T-3到T）和b1（T到T+3）
                    a1 = (ma5_current - ma5_before3) / 3
                    b1 = (ma5_after3 - ma5_current) / 3

                    # 仅标记上升和下降，其他为NULL
                    if a1 < 0 and b1 < 0:
                        tags[i] = '下降'
                    elif a1 > 0 and b1 > 0:
                        tags[i] = '上升'
                    # 不满足条件则为NULL（默认）

                # 第二步：修正标记，判断波峰/波谷
                tags = fix_turning_points(tags)

                # 更新数据库
                for i in range(len(records)):
                    trade_date = records[i]['trade_date']
                    # 只更新 start_date ~ end_date 范围内的日期
                    if trade_date < start_date or trade_date > end_date:
                        continue

                    tag_value = tags[i] if tags[i] is not None else None

                    update_sql = """
                        UPDATE stock_daily_t
                        SET turning_point = %s
                        WHERE ts_code = %s AND trade_date = %s
                    """
                    cursor.execute(update_sql, (tag_value, ts_code, trade_date))
                    update_count += 1

            conn.commit()
        print(f"✅ 成功更新 {update_count} 条记录的turning_point字段（{total_stocks} 只股票）")
    except Exception as e:
        print(f"❌ 更新数据失败: {e}")
        conn.rollback()


def main():
    print("=" * 80)
    print("📊 更新股票turning_point字段 v2（T±3斜率法 + 前后3日修正）")
    print("=" * 80)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    try:
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

        # 数据抓取范围：起始日期-20天 ~ 结束日期+10天
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        fetch_start_date = (start_dt - timedelta(days=20)).strftime('%Y%m%d')
        fetch_end_date = (end_dt + timedelta(days=10)).strftime('%Y%m%d')

        print(f"📅 目标日期范围: {start_date} ~ {end_date}")
        print(f"📅 数据抓取范围: {fetch_start_date} ~ {fetch_end_date}")

        data = read_stock_data(conn, fetch_start_date, fetch_end_date)
        if data is None or len(data) == 0:
            print("❌ 未读取到数据")
            return

        analyze_and_update(data, conn, start_date, end_date)
    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
