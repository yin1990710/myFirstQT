#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti']
plt.rcParams['axes.unicode_minus'] = False

def get_target_date():
    now = datetime.now()
    current_hour = now.hour
    if current_hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date

def read_data(days=5):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 5)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        name,
        SUM(net_amount) as total_net_amount,
        GROUP_CONCAT(DISTINCT lead_stock ORDER BY lead_stock SEPARATOR ',') as lead_stocks
    FROM ths_industry_daily_t
    WHERE trade_date >= %s AND trade_date <= %s
    GROUP BY name
    ORDER BY total_net_amount DESC
    LIMIT 10
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return None
    finally:
        close_connection(connection)

def generate_png_report(data, output_dir):
    target_date = get_target_date()
    filename = f"近5日行业资金流入情况{target_date}.png"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        os.remove(filepath)
        print(f"🗑️ 已删除旧文件: {filepath}")

    df = pd.DataFrame(data)

    fig, ax = plt.subplots(figsize=(16, 8))
    ax.axis('off')

    column_labels = ['序号', '近5日累计净额(亿元)', '行业板块名称', '领涨股票']
    cell_data = []
    for idx, row in enumerate(data, 1):
        cell_data.append([
            str(idx),
            f"{float(row['total_net_amount']):.2f}",
            row['name'],
            row['lead_stocks'] if row['lead_stocks'] else ''
        ])

    table = ax.table(
        cellText=cell_data,
        colLabels=column_labels,
        cellLoc='center',
        loc='center',
        colWidths=[0.08, 0.18, 0.25, 0.49]
    )

    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 2.0)

    for i, label in enumerate(column_labels):
        cell = table[(0, i)]
        cell.set_facecolor('#4472C4')
        cell.set_text_props(color='white', fontweight='bold')

    for row_idx in range(len(data)):
        for col_idx in range(len(column_labels)):
            cell = table[(row_idx + 1, col_idx)]
            if row_idx % 2 == 0:
                cell.set_facecolor('#D9E2F3')
            else:
                cell.set_facecolor('#FFFFFF')

    plt.title(f'近5日行业资金流入情况 ({target_date})', fontsize=16, fontweight='bold', pad=20)

    plt.tight_layout()
    plt.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

    print(f"✅ PNG报告已生成: {filepath}")
    return filepath

def main():
    print("=" * 80)
    print("近5日行业资金流入情况分析")
    print("=" * 80)

    data = read_data(days=5)
    if not data:
        print("❌ 没有数据，退出程序")
        return

    print("\n传给图表的数据：")
    for idx, row in enumerate(data, 1):
        print(f"{idx}. {row['name']}: {row['total_net_amount']}亿元, 领涨: {row['lead_stocks']}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_name = "同花顺行业分析报告"
    folder_path = os.path.join(script_dir, folder_name)

    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"� 创建文件夹: {folder_name}")
    else:
        print(f"📁 使用已有文件夹: {folder_name}")

    filepath = generate_png_report(data, folder_path)

    print("\n" + "=" * 80)
    print(f"🎉 报告生成完成!")
    print(f"📄 报告路径: {filepath}")
    print("=" * 80)

if __name__ == "__main__":
    main()