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

def read_index_codes():
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '股指代码', 'stock_index.csv')
    try:
        df = pd.read_csv(csv_path)
        return dict(zip(df['股指代码'], df['含义']))
    except Exception as e:
        print(f"❌ 读取股指代码失败: {e}")
        return {}

def read_future_codes():
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '股指期货代码', 'stock_index_future.csv')
    try:
        df = pd.read_csv(csv_path)
        return dict(zip(df['股指合约代码'], df['含义']))
    except Exception as e:
        print(f"❌ 读取股指期货代码失败: {e}")
        return {}

def read_index_data(days=90):
    connection = get_mysql_connection()
    if not connection:
        return None

    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query_sql = """
    SELECT ts_code, trade_date, close
    FROM stock_index_daily_t
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        return results
    except Exception as e:
        print(f"❌ 读取股指数据失败: {e}")
        return None
    finally:
        close_connection(connection)

def read_future_data(days=90):
    connection = get_mysql_connection()
    if not connection:
        return None

    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query_sql = """
    SELECT ts_code, trade_date, close
    FROM stock_index_future_daily_t
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        return results
    except Exception as e:
        print(f"❌ 读取股指期货数据失败: {e}")
        return None
    finally:
        close_connection(connection)

def read_rzrq_data(days=90):
    connection = get_mysql_connection()
    if not connection:
        return None

    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        trade_date,
        SUM(rzye) as total_rzye,
        SUM(rzmre) as total_rzmre,
        SUM(rzche) as total_rzche,
        SUM(rqye) as total_rqye,
        SUM(rqmcl) as total_rqmcl,
        SUM(rqyl) as total_rqyl
    FROM rzrq_ye_t
    WHERE trade_date >= %s AND trade_date <= %s
    GROUP BY trade_date
    ORDER BY trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        return results
    except Exception as e:
        print(f"❌ 读取融资融券数据失败: {e}")
        return None
    finally:
        close_connection(connection)

def calculate_basis_spread(index_data, future_data, index_code, future_code):
    index_df = pd.DataFrame(index_data, columns=['ts_code', 'trade_date', 'close'])
    future_df = pd.DataFrame(future_data, columns=['ts_code', 'trade_date', 'close'])

    index_df = index_df[index_df['ts_code'] == index_code][['trade_date', 'close']]
    future_df = future_df[future_df['ts_code'] == future_code][['trade_date', 'close']]

    merged = pd.merge(index_df, future_df, on='trade_date', how='inner', suffixes=('_index', '_future'))
    merged['basis'] = merged['close_future'] - merged['close_index']
    merged['trade_date'] = pd.to_datetime(merged['trade_date'], format='%Y%m%d')
    merged = merged.sort_values('trade_date')

    return merged

def calculate_risk_stars(basis_data_dict):
    risk_stars = 0

    for name, basis_df in basis_data_dict.items():
        if len(basis_df) < 5:
            continue

        recent_5 = basis_df.tail(5)
        recent_5_values = recent_5['basis'].values

        all_negative = all(v < 0 for v in recent_5_values)
        abs_increasing = all(abs(recent_5_values[i]) < abs(recent_5_values[i+1]) for i in range(4))

        if all_negative and abs_increasing:
            risk_stars = max(risk_stars, 5)
            continue

        recent_4 = basis_df.tail(4)
        recent_4_values = recent_4['basis'].values
        all_negative_4 = all(v < 0 for v in recent_4_values)
        abs_increasing_4 = all(abs(recent_4_values[i]) < abs(recent_4_values[i+1]) for i in range(3))

        if all_negative_4 and abs_increasing_4:
            risk_stars = max(risk_stars, 4)
            continue

        recent_3 = basis_df.tail(3)
        recent_3_values = recent_3['basis'].values
        all_negative_3 = all(v < 0 for v in recent_3_values)
        abs_increasing_3 = abs(recent_3_values[0]) < abs(recent_3_values[1]) < abs(recent_3_values[2])

        if all_negative_3 and abs_increasing_3:
            risk_stars = max(risk_stars, 3)
            continue

        recent_2 = basis_df.tail(2)
        recent_2_values = recent_2['basis'].values
        all_negative_2 = all(v < 0 for v in recent_2_values)
        abs_increasing_2 = abs(recent_2_values[0]) < abs(recent_2_values[1])

        if all_negative_2 and abs_increasing_2:
            risk_stars = max(risk_stars, 2)

    return risk_stars

def plot_basis_spread(ax, index_code, index_name, future_code, future_name, index_data, future_data):
    merged = calculate_basis_spread(index_data, future_data, index_code, future_code)

    ax.plot(merged['trade_date'], merged['basis'], linewidth=1.5, color='#1f77b4')
    ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.8)
    ax.set_title(f'{index_name} 期现差走势', fontsize=12, fontweight='bold')
    ax.set_xlabel('交易日期')
    ax.set_ylabel('期现差')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    ax.grid(True, alpha=0.3)

def plot_rzrq_financing(ax, data):
    df = pd.DataFrame(data, columns=['trade_date', 'total_rzye', 'total_rzmre', 'total_rzche', 'total_rqye', 'total_rqmcl', 'total_rqyl'])
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df['total_rzye'] = df['total_rzye'].astype(float)
    df['total_rzmre'] = df['total_rzmre'].astype(float)
    df['total_rzche'] = df['total_rzche'].astype(float)
    df['rzye_yi'] = df['total_rzye'] / 1e8
    df['rzmre_yi'] = df['total_rzmre'] / 1e8
    df['rzche_yi'] = df['total_rzche'] / 1e8
    df = df.sort_values('trade_date')

    ax.plot(df['trade_date'], df['rzye_yi'], linewidth=1.5, label='融资余额', color='#1f77b4')
    ax.plot(df['trade_date'], df['rzmre_yi'], linewidth=1.5, label='融资买入额', color='#ff7f0e')
    ax.plot(df['trade_date'], df['rzche_yi'], linewidth=1.5, label='融资偿还额', color='#2ca02c')
    ax.set_title('融资额走势', fontsize=12, fontweight='bold')
    ax.set_xlabel('交易日期')
    ax.set_ylabel('亿元')
    ax.legend(loc='best')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    ax.grid(True, alpha=0.3)

def plot_rzrq_securities(ax, data):
    df = pd.DataFrame(data, columns=['trade_date', 'total_rzye', 'total_rzmre', 'total_rzche', 'total_rqye', 'total_rqmcl', 'total_rqyl'])
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df['total_rqye'] = df['total_rqye'].astype(float)
    df['rqye_yi'] = df['total_rqye'] / 1e8
    df = df.sort_values('trade_date')

    ax.plot(df['trade_date'], df['rqye_yi'], linewidth=1.5, label='融券余额', color='#d62728')
    ax.set_title('融券额走势', fontsize=12, fontweight='bold')
    ax.set_xlabel('交易日期')
    ax.set_ylabel('亿元')
    ax.legend(loc='best')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    ax.grid(True, alpha=0.3)

def plot_rzrq_securities_vol(ax, data):
    df = pd.DataFrame(data, columns=['trade_date', 'total_rzye', 'total_rzmre', 'total_rzche', 'total_rqye', 'total_rqmcl', 'total_rqyl'])
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    df['total_rqmcl'] = df['total_rqmcl'].astype(float)
    df['total_rqyl'] = df['total_rqyl'].astype(float)
    df = df.sort_values('trade_date')

    ax.plot(df['trade_date'], df['total_rqmcl'], linewidth=1.5, label='融券卖出量', color='#9467bd')
    ax.plot(df['trade_date'], df['total_rqyl'], linewidth=1.5, label='融券余量', color='#8c564b')
    ax.set_title('融券量走势', fontsize=12, fontweight='bold')
    ax.set_xlabel('交易日期')
    ax.set_ylabel('量')
    ax.legend(loc='best')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    ax.grid(True, alpha=0.3)

def plot_risk_stars(ax, stars):
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis('off')

    title = f'大盘风险星级指数: {"★" * stars}{"☆" * (5 - stars)} ({stars}颗星)'
    ax.text(5, 1.5, title, fontsize=18, fontweight='bold', ha='center', va='center',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='lightyellow', edgecolor='orange', linewidth=2))
    ax.set_title('大盘风险评级', fontsize=14, fontweight='bold', pad=10)

def main():
    print("=" * 80)
    print("大盘趋势报告生成")
    print("=" * 80)

    read_index_codes()
    read_future_codes()

    index_data = read_index_data(days=90)
    future_data = read_future_data(days=90)
    rzrq_data = read_rzrq_data(days=90)

    if not index_data or not future_data:
        print("❌ 数据读取失败，退出程序")
        return

    mapping = {
        '沪深300': ('000300.SH', 'IFL.CFX'),
        '中证500': ('000905.SH', 'ICL.CFX'),
        '中证1000': ('000852.SH', 'IML.CFX')
    }

    basis_data_dict = {}
    for name, (idx_code, fut_code) in mapping.items():
        basis_df = calculate_basis_spread(index_data, future_data, idx_code, fut_code)
        basis_data_dict[name] = basis_df

    risk_stars = calculate_risk_stars(basis_data_dict)
    print(f"\n📊 大盘风险星级指数: {'★' * risk_stars}{'☆' * (5 - risk_stars)} ({risk_stars}颗星)")

    fig, axes = plt.subplots(8, 1, figsize=(14, 32))
    plt.tight_layout()

    plot_risk_stars(axes[0], risk_stars)

    idx = 1
    for name, (idx_code, fut_code) in mapping.items():
        plot_basis_spread(axes[idx], idx_code, name, fut_code, name, index_data, future_data)
        idx += 1

    if rzrq_data:
        plot_rzrq_financing(axes[4], rzrq_data)
        plot_rzrq_securities(axes[5], rzrq_data)
        plot_rzrq_securities_vol(axes[6], rzrq_data)
    else:
        axes[4].text(0.5, 0.5, '融资数据获取失败', ha='center', va='center', transform=axes[4].transAxes)
        axes[5].text(0.5, 0.5, '融券数据获取失败', ha='center', va='center', transform=axes[5].transAxes)
        axes[6].text(0.5, 0.5, '融券量数据获取失败', ha='center', va='center', transform=axes[6].transAxes)

    axes[7].axis('off')

    target_date = get_target_date()
    filename = f"大盘趋势报告{target_date}.png"
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

    plt.savefig(filepath, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

    print(f"✅ 报告已生成: {filepath}")
    print("\n" + "=" * 80)
    print("🎉 大盘趋势报告生成完成!")
    print("=" * 80)

if __name__ == "__main__":
    main()