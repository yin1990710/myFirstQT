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

def read_stock_data(ts_code, limit=100):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None

    query_sql = """
    SELECT
        ts_code,
        trade_date,
        ma5,
        turning_point
    FROM stock_daily_t
    WHERE ts_code = %s
    ORDER BY trade_date DESC
    LIMIT %s
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (ts_code, limit))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条 {ts_code} 数据")
        close_connection(connection)
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        close_connection(connection)
        return None

def plot_chart(data, ts_code):
    records = sorted(data, key=lambda x: x['trade_date'])

    dates = [pd.to_datetime(r['trade_date']) for r in records]
    ma5_values = [float(r['ma5']) if r['ma5'] is not None else None for r in records]
    turning_points = [r['turning_point'] for r in records]

    fig, ax1 = plt.subplots(figsize=(16, 8))

    ax1.plot(dates, ma5_values, label='MA5', color='#ff7f0e', linewidth=2)

    tag_colors = {
        '波峰': '#d62728',
        '波谷': '#2ca02c',
        '上升': '#17becf',
        '下降': '#9467bd',
        '波中': '#7f7f7f'
    }

    tag_markers = {
        '波峰': '^',
        '波谷': 'v',
        '上升': 'o',
        '下降': 's',
        '波中': 'D'
    }

    for i, (date, ma5, tag) in enumerate(zip(dates, ma5_values, turning_points)):
        if tag and tag in tag_colors and ma5 is not None:
            ax1.scatter(date, ma5, color=tag_colors[tag], marker=tag_markers[tag], s=120, zorder=5)
            ax1.annotate(tag, (date, ma5), textcoords="offset points", xytext=(0, 12), ha='center', fontsize=9, fontweight='bold')

    ax1.set_xlabel('日期', fontsize=12)
    ax1.set_ylabel('MA5', fontsize=12)
    ax1.set_title(f'{ts_code} MA5走势与转折点标记', fontsize=16, fontweight='bold')
    ax1.legend(loc='upper left', fontsize=12)
    ax1.grid(True, alpha=0.3)

    plt.xticks(rotation=45)
    plt.tight_layout()

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f'{ts_code}.png')
    plt.savefig(output_path, dpi=150)
    print(f"✅ 图片已保存: {output_path}")

    plt.close()

def print_turning_points(data):
    records = sorted(data, key=lambda x: x['trade_date'])
    print("\n" + "=" * 80)
    print("转折点数据")
    print("=" * 80)
    print(f"{'日期':<12} {'MA5':<10} {'turning_point':<8}")
    print("-" * 80)
    for r in records:
        tp = r['turning_point'] if r['turning_point'] else '无'
        ma5 = f"{float(r['ma5']):.2f}" if r['ma5'] else 'None'
        print(f"{r['trade_date']:<12} {ma5:<10} {tp:<8}")
    print("=" * 80)

def main():
    if len(sys.argv) < 2:
        print("用法: python plot_turning_point_688169.py <股票代码> [天数]")
        print("示例: python plot_turning_point_688169.py 688169.SH 100")
        sys.exit(1)

    ts_code = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    print("=" * 80)
    print(f"{ts_code} MA5走势与转折点标记")
    print("=" * 80)

    data = read_stock_data(ts_code, limit)

    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    print_turning_points(data)

    plot_chart(data, ts_code)

if __name__ == "__main__":
    main()