#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
监控stock_daily_t表中数据的更新情况。
1. 最近10个交易日，每日ts_code数量（表格1）
2. 最近10个交易日，每日turning_point分布（表格2）
3. 最近10个交易日，每日ma5和ma30大于0的记录数（表格3）
4. 将以上图表保存为monitor_stock_data.png
5. 放入monitor_stock_data+当日日期后缀的文件夹下
"""

import os
import sys
import shutil
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def get_recent_dates(conn, days=10):
    """获取最近N个交易日"""
    sql = """
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        ORDER BY trade_date DESC
        LIMIT %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (days,))
        rows = cursor.fetchall()
    dates = [row['trade_date'] for row in rows]
    dates.reverse()
    return dates


def get_ts_code_count(conn, dates):
    """每日ts_code数量"""
    results = []
    for d in dates:
        sql = """
            SELECT COUNT(DISTINCT ts_code) AS cnt
            FROM stock_daily_t
            WHERE trade_date = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            row = cursor.fetchone()
        results.append(row['cnt'] if row else 0)
    return results


def get_turning_point_distribution(conn, dates):
    """每日turning_point分布"""
    all_tags = []
    data = {}
    for d in dates:
        sql = """
            SELECT turning_point, COUNT(*) AS cnt
            FROM stock_daily_t
            WHERE trade_date = %s
            GROUP BY turning_point
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            rows = cursor.fetchall()
        dist = {}
        for row in rows:
            tag = row['turning_point'] if row['turning_point'] else '(空)'
            dist[tag] = row['cnt']
            if tag not in all_tags:
                all_tags.append(tag)
        data[d] = dist

    # 构建表格行：每行是一个turning_point类型，每列是一个日期
    all_tags.sort()
    return all_tags, data


def get_ma_count(conn, dates):
    """每日ma5>0和ma30>0的记录数"""
    ma5_counts = []
    ma30_counts = []
    for d in dates:
        sql = """
            SELECT
                SUM(CASE WHEN ma5 IS NOT NULL AND ma5 > 0 THEN 1 ELSE 0 END) AS ma5_cnt,
                SUM(CASE WHEN ma30 IS NOT NULL AND ma30 > 0 THEN 1 ELSE 0 END) AS ma30_cnt
            FROM stock_daily_t
            WHERE trade_date = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            row = cursor.fetchone()
        ma5_counts.append(row['ma5_cnt'] if row and row['ma5_cnt'] else 0)
        ma30_counts.append(row['ma30_cnt'] if row and row['ma30_cnt'] else 0)
    return ma5_counts, ma30_counts


def create_folder():
    """创建文件夹"""
    today = datetime.now().strftime('%Y%m%d')
    folder_name = f"monitor_stock_data{today}"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)

    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")

    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


def plot_monitor(dates, ts_counts, tp_tags, tp_data, ma5_counts, ma30_counts, output_path):
    """绘制监控图表"""
    n_dates = len(dates)
    n_tags = len(tp_tags)

    # 计算总行数：表格1(标题+表头+数据行) + 空行 + 表格2(...) + 空行 + 表格3(...)
    fig_height = 3 + n_dates * 0.35 + n_tags * 0.35 + n_dates * 0.35
    fig, ax = plt.subplots(figsize=(max(14, n_dates * 1.5), fig_height))
    ax.axis('off')

    y_cursor = 1.0
    row_height = 0.06

    # ===== 表格1：每日ts_code数量 =====
    ax.text(0.5, y_cursor, '表格1：每日ts_code数量', fontsize=14, fontweight='bold',
            ha='center', va='top', transform=ax.transAxes)
    y_cursor -= row_height * 1.5

    table1_data = [['交易日期'] + dates]
    table1_data.append(['ts_code数量'] + [str(c) for c in ts_counts])

    table1 = ax.table(cellText=table1_data, loc='upper center',
                      bbox=[0.0, y_cursor - row_height * 2, 1.0, row_height * 2])
    table1.auto_set_font_size(False)
    table1.set_fontsize(9)
    y_cursor -= row_height * 2.5

    # ===== 表格2：每日turning_point分布 =====
    ax.text(0.5, y_cursor, '表格2：每日turning_point分布', fontsize=14, fontweight='bold',
            ha='center', va='top', transform=ax.transAxes)
    y_cursor -= row_height * 1.5

    table2_data = [['turning_point'] + dates]
    for tag in tp_tags:
        row = [tag]
        for d in dates:
            row.append(str(tp_data.get(d, {}).get(tag, 0)))
        table2_data.append(row)

    table2_height = row_height * (len(tp_tags) + 1)
    table2 = ax.table(cellText=table2_data, loc='upper center',
                      bbox=[0.0, y_cursor - table2_height, 1.0, table2_height])
    table2.auto_set_font_size(False)
    table2.set_fontsize(9)
    y_cursor -= table2_height + row_height * 0.5

    # ===== 表格3：每日ma5>0和ma30>0记录数 =====
    ax.text(0.5, y_cursor, '表格3：每日ma5>0和ma30>0记录数', fontsize=14, fontweight='bold',
            ha='center', va='top', transform=ax.transAxes)
    y_cursor -= row_height * 1.5

    table3_data = [
        ['交易日期'] + dates,
        ['ma5>0记录数'] + [str(c) for c in ma5_counts],
        ['ma30>0记录数'] + [str(c) for c in ma30_counts],
    ]

    table3_height = row_height * 3
    table3 = ax.table(cellText=table3_data, loc='upper center',
                      bbox=[0.0, y_cursor - table3_height, 1.0, table3_height])
    table3.auto_set_font_size(False)
    table3.set_fontsize(9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"✅ 图片已保存: {output_path}")


def main():
    print("=" * 60)
    print("📊 stock_daily_t 数据更新监控")
    print("=" * 60)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    try:
        # 获取最近10个交易日
        dates = get_recent_dates(conn, days=10)
        print(f"📅 最近10个交易日: {dates}")

        # 表格1：每日ts_code数量
        ts_counts = get_ts_code_count(conn, dates)
        print(f"✅ 每日ts_code数量: {ts_counts}")

        # 表格2：每日turning_point分布
        tp_tags, tp_data = get_turning_point_distribution(conn, dates)
        print(f"✅ turning_point类型: {tp_tags}")

        # 表格3：每日ma5>0和ma30>0记录数
        ma5_counts, ma30_counts = get_ma_count(conn, dates)
        print(f"✅ ma5>0记录数: {ma5_counts}")
        print(f"✅ ma30>0记录数: {ma30_counts}")

        # 创建文件夹并保存图片
        folder_path = create_folder()
        output_path = os.path.join(folder_path, "monitor_stock_data.png")
        plot_monitor(dates, ts_counts, tp_tags, tp_data, ma5_counts, ma30_counts, output_path)

        print("\n🎉 监控完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 图片路径: {output_path}")

    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
