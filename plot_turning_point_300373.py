#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import matplotlib.pyplot as plt
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti']
plt.rcParams['axes.unicode_minus'] = False

def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None

    query_sql = """
    SELECT
        ts_code,
        trade_date,
        ma5,
        close
    FROM stock_daily_t
    WHERE ts_code = '300373.SZ'
    ORDER BY trade_date DESC
    LIMIT 100
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql)
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据")
        close_connection(connection)
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        close_connection(connection)
        return None

def analyze_turning_points(data):
    if len(data) < 21:
        print("❌ 数据不足，需要至少21个交易日")
        return []

    records = sorted(data, key=lambda x: x['trade_date'])

    ma5_values = [float(r['ma5']) if r['ma5'] is not None else None for r in records]
    close_values = [float(r['close']) if r['close'] is not None else 0 for r in records]
    dates = [pd.to_datetime(r['trade_date']) for r in records]

    turning_points = []

    for i in range(len(records)):
        if i < 10 or i >= len(records) - 10:
            continue

        has_valid_ma5 = True
        for j in range(i - 10, i + 11):
            if ma5_values[j] is None or ma5_values[j] <= 0:
                has_valid_ma5 = False
                break

        if not has_valid_ma5:
            continue

        ma5_before10 = ma5_values[i - 10]
        ma5_current = ma5_values[i]
        ma5_after10 = ma5_values[i + 10]

        a1 = (ma5_current - ma5_before10) / 10
        b1 = (ma5_after10 - ma5_current) / 10

        if a1 > 0 and b1 < 0:
            tag = '波峰'
        elif a1 < 0 and b1 > 0:
            tag = '波谷'
        elif a1 < 0 and b1 < 0:
            tag = '下降'
        elif a1 > 0 and b1 > 0:
            tag = '上升'
        else:
            continue

        turning_points.append({
            'index': i,
            'trade_date': records[i]['trade_date'],
            'ma5': ma5_current,
            'close': close_values[i],
            'tag': tag,
            'a1': a1,
            'b1': b1
        })

    return records, dates, ma5_values, close_values, turning_points

def plot_chart(records, dates, ma5_values, close_values, turning_points):
    fig, ax1 = plt.subplots(figsize=(16, 8))

    ax1.plot(dates, close_values, label='收盘价', color='#1f77b4', linewidth=2)
    ax1.plot(dates, ma5_values, label='MA5', color='#ff7f0e', linewidth=2)

    peaks = [p for p in turning_points if p['tag'] == '波峰']
    valleys = [p for p in turning_points if p['tag'] == '波谷']
    ups = [p for p in turning_points if p['tag'] == '上升']
    downs = [p for p in turning_points if p['tag'] == '下降']

    if peaks:
        peak_dates = [dates[p['index']] for p in peaks]
        peak_close = [p['close'] for p in peaks]
        ax1.scatter(peak_dates, peak_close, color='#d62728', marker='^', s=150, zorder=5, label='波峰')

    if valleys:
        valley_dates = [dates[p['index']] for p in valleys]
        valley_close = [p['close'] for p in valleys]
        ax1.scatter(valley_dates, valley_close, color='#2ca02c', marker='v', s=150, zorder=5, label='波谷')

    if ups:
        up_dates = [dates[p['index']] for p in ups]
        up_close = [p['close'] for p in ups]
        ax1.scatter(up_dates, up_close, color='#17becf', marker='o', s=80, zorder=5, label='上升')

    if downs:
        down_dates = [dates[p['index']] for p in downs]
        down_close = [p['close'] for p in downs]
        ax1.scatter(down_dates, down_close, color='#9467bd', marker='s', s=80, zorder=5, label='下降')

    for tp in turning_points:
        ax1.annotate(tp['tag'],
                     (dates[tp['index']], tp['close']),
                     textcoords="offset points",
                     xytext=(0, 15),
                     ha='center',
                     fontsize=10,
                     fontweight='bold')

    ax1.set_xlabel('日期', fontsize=12)
    ax1.set_ylabel('价格', fontsize=12)
    ax1.set_title('300373.SZ MA5趋势转折点分析', fontsize=16, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=12)
    ax1.grid(True, alpha=0.3)

    plt.xticks(rotation=45)
    plt.tight_layout()

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '300373.png')
    plt.savefig(output_path, dpi=150)
    print(f"✅ 图片已保存: {output_path}")

    plt.show()

def print_turning_points(turning_points):
    print("\n" + "=" * 80)
    print("转折点分析结果")
    print("=" * 80)
    print(f"{'日期':<12} {'类型':<6} {'MA5':<10} {'a1':<10} {'b1':<10}")
    print("-" * 80)
    for tp in turning_points:
        print(f"{tp['trade_date']:<12} {tp['tag']:<6} {tp['ma5']:<10.2f} {tp['a1']:<10.4f} {tp['b1']:<10.4f}")
    print("=" * 80)

def main():
    print("=" * 80)
    print("300373.SZ MA5趋势转折点分析")
    print("=" * 80)

    data = read_stock_data()

    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    records, dates, ma5_values, close_values, turning_points = analyze_turning_points(data)

    print_turning_points(turning_points)

    plot_chart(records, dates, ma5_values, close_values, turning_points)

if __name__ == "__main__":
    main()