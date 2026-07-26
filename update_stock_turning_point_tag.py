#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None, None

    query_sql = """
    SELECT
        ts_code,
        trade_date,
        ma5,
        close
    FROM stock_daily_t
    ORDER BY ts_code, trade_date DESC
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql)
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据")
        return results, connection
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        close_connection(connection)
        return None, None

def fix_consecutive_tags(tags, closes):
    """一次修正：修正连续的波峰和波谷标记"""
    n = len(tags)
    i = 0
    while i < n:
        if tags[i] in ('波峰', '波谷'):
            j = i
            while j + 1 < n and tags[j + 1] == tags[i]:
                j += 1

            if j > i:
                best_idx = i
                if tags[i] == '波峰':
                    for k in range(i, j + 1):
                        if closes[k] > closes[best_idx]:
                            best_idx = k
                else:
                    for k in range(i, j + 1):
                        if closes[k] < closes[best_idx]:
                            best_idx = k

                replacement_tag = None
                for k in range(j + 1, n):
                    if tags[k] != tags[i]:
                        replacement_tag = tags[k]
                        break

                if replacement_tag is None:
                    for k in range(i - 1, -1, -1):
                        if tags[k] != tags[i]:
                            replacement_tag = tags[k]
                            break

                if replacement_tag is None:
                    replacement_tag = '下降' if tags[i] == '波峰' else '上升'

                for k in range(i, j + 1):
                    if k != best_idx:
                        tags[k] = replacement_tag

            i = j + 1
        else:
            i += 1

    return tags

def fix_peak_valley_majority(tags):
    """二次修正：根据前后3个交易日的多数标记修正波峰波谷"""
    n = len(tags)
    for i in range(n):
        if tags[i] not in ('波峰', '波谷'):
            continue

        forward_tags = []
        for j in range(1, 4):
            if i - j >= 0 and tags[i - j] in ('上升', '下降'):
                forward_tags.append(tags[i - j])

        backward_tags = []
        for j in range(1, 4):
            if i + j < n and tags[i + j] in ('上升', '下降'):
                backward_tags.append(tags[i + j])

        if len(forward_tags) == 0 or len(backward_tags) == 0:
            continue

        forward_majority = max(set(forward_tags), key=forward_tags.count)
        backward_majority = max(set(backward_tags), key=backward_tags.count)

        if forward_majority == backward_majority:
            tags[i] = forward_majority

    return tags

def analyze_and_update(data, connection):
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
        with connection.cursor() as cursor:
            for ts_code, records in stock_data.items():
                if len(records) < 11:
                    continue

                records = records[:100]
                records.sort(key=lambda x: x['trade_date'])

                ma5_values = [r['ma5'] for r in records]
                close_values = [r['close'] for r in records]
                tags = [None] * len(records)

                for i in range(len(records)):
                    if i < 5 or i >= len(records) - 5:
                        continue

                    ma5_before5 = ma5_values[i - 5]
                    ma5_current = ma5_values[i]
                    ma5_after5 = ma5_values[i + 5]

                    if ma5_before5 is None or ma5_before5 <= 0 or ma5_after5 is None or ma5_after5 <= 0:
                        continue

                    a1 = (ma5_current - ma5_before5) / 5
                    b1 = (ma5_after5 - ma5_current) / 5

                    if a1 > 0 and b1 < 0:
                        tags[i] = '波峰'
                    elif a1 < 0 and b1 > 0:
                        tags[i] = '波谷'
                    elif a1 < 0 and b1 < 0:
                        tags[i] = '下降'
                    elif a1 > 0 and b1 > 0:
                        tags[i] = '上升'

                tags = fix_consecutive_tags(tags, close_values)

                tags = fix_peak_valley_majority(tags)

                for i in range(len(records)):
                    if tags[i] is None:
                        continue

                    trade_date = records[i]['trade_date']

                    update_sql = """
                    UPDATE stock_daily_t
                    SET turning_point = %s
                    WHERE ts_code = %s AND trade_date = %s
                    """
                    cursor.execute(update_sql, (tags[i], ts_code, trade_date))
                    update_count += 1

            connection.commit()
        print(f"✅ 成功更新 {update_count} 条记录的turning_point字段（{total_stocks} 只股票）")
    except Exception as e:
        print(f"❌ 更新数据失败: {e}")
        connection.rollback()

def main():
    print("=" * 80)
    print("更新股票turning_point字段（波峰/波谷/上升/下降）含连续标记修正和二次修正")
    print("=" * 80)

    data, connection = read_stock_data()

    if data is None or connection is None:
        return

    analyze_and_update(data, connection)

    close_connection(connection)

if __name__ == "__main__":
    main()