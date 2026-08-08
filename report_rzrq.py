#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

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

def read_data(days=180):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None

    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        trade_date,
        exchange_id,
        rzye,
        rzmre,
        rzche,
        rqye,
        rqmcl,
        rqyl
    FROM rzrq_ye_t
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")

        columns = ['trade_date', 'exchange_id', 'rzye', 'rzmre', 'rzche', 'rqye', 'rqmcl', 'rqyl']
        df = pd.DataFrame(results, columns=columns)
        return df
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return None
    finally:
        close_connection(connection)

def aggregate_by_date(df):
    agg_df = df.groupby('trade_date').agg({
        'rzye': 'sum',
        'rzmre': 'sum',
        'rzche': 'sum',
        'rqye': 'sum',
        'rqmcl': 'sum',
        'rqyl': 'sum'
    }).reset_index()

    for col in ['rzye', 'rzmre', 'rzche', 'rqye']:
        agg_df[col] = agg_df[col] / 100000000

    agg_df = agg_df.sort_values('trade_date')
    print(f"✅ 数据聚合完成，共 {len(agg_df)} 个交易日")
    return agg_df

def generate_png_report(df, output_dir):
    target_date = get_target_date()
    filename = f"融资融券余额走势{target_date}.png"
    filepath = os.path.join(output_dir, filename)

    dates = pd.to_datetime(df['trade_date'], format='%Y%m%d')

    fig, axes = plt.subplots(3, 1, figsize=(16, 18))

    axes[0].plot(dates, df['rzye'], label='融资余额', linewidth=2, color='#1f77b4')
    axes[0].plot(dates, df['rzmre'], label='融资买入额', linewidth=2, color='#ff7f0e')
    axes[0].plot(dates, df['rzche'], label='融资偿还额', linewidth=2, color='#2ca02c')
    axes[0].set_title('融资走势', fontsize=18, fontweight='bold')
    axes[0].set_xlabel('交易日期', fontsize=12)
    axes[0].set_ylabel('数额 (亿元)', fontsize=12)
    axes[0].legend(loc='upper left', fontsize=11)
    axes[0].grid(True, alpha=0.3)
    axes[0].xaxis.set_major_formatter(mdates.DateFormatter('%Y%m%d'))
    axes[0].xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(axes[0].xaxis.get_majorticklabels(), rotation=45)

    axes[1].plot(dates, df['rqye'], label='融券余额', linewidth=2, color='#d62728')
    axes[1].set_title('融券额走势', fontsize=18, fontweight='bold')
    axes[1].set_xlabel('交易日期', fontsize=12)
    axes[1].set_ylabel('数额 (亿元)', fontsize=12)
    axes[1].legend(loc='upper left', fontsize=11)
    axes[1].grid(True, alpha=0.3)
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%Y%m%d'))
    axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(axes[1].xaxis.get_majorticklabels(), rotation=45)

    axes[2].plot(dates, df['rqmcl'], label='融券卖出量', linewidth=2, color='#9467bd')
    axes[2].plot(dates, df['rqyl'], label='融券余量', linewidth=2, color='#8c564b')
    axes[2].set_title('融券量走势', fontsize=18, fontweight='bold')
    axes[2].set_xlabel('交易日期', fontsize=12)
    axes[2].set_ylabel('数量', fontsize=12)
    axes[2].legend(loc='upper left', fontsize=11)
    axes[2].grid(True, alpha=0.3)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter('%Y%m%d'))
    axes[2].xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.setp(axes[2].xaxis.get_majorticklabels(), rotation=45)

    plt.tight_layout()
    plt.savefig(filepath, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"✅ PNG报告已生成: {filepath}")
    return filepath

def main():
    print("=" * 80)
    print("融资融券余额走势分析")
    print("=" * 80)

    df = read_data(days=180)
    if df is None or df.empty:
        print("❌ 没有数据，退出程序")
        return

    agg_df = aggregate_by_date(df)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = generate_png_report(agg_df, script_dir)

    print("\n" + "=" * 80)
    print(f"🎉 报告生成完成!")
    print(f"📄 报告路径: {filepath}")
    print("=" * 80)

if __name__ == "__main__":
    main()