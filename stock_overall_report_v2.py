#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def get_target_date():
    """获取目标日期（当前时间为0-15点则取前一天日期）"""
    now = datetime.now()
    current_hour = now.hour
    if current_hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date


def read_index_codes():
    """读取股指代码和股指期货代码"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 股指代码文件路径
    index_code_file = os.path.join(script_dir, '股指代码', '股指代码.csv')
    index_codes = {}
    if os.path.exists(index_code_file):
        df = pd.read_csv(index_code_file, encoding='utf-8-sig')
        for _, row in df.iterrows():
            index_codes[row['ts_code']] = row['name']
    
    # 股指期货代码文件路径
    future_code_file = os.path.join(script_dir, '股指期货代码', '股指期货代码.csv')
    future_codes = {}
    if os.path.exists(future_code_file):
        df = pd.read_csv(future_code_file, encoding='utf-8-sig')
        for _, row in df.iterrows():
            future_codes[row['ts_code']] = row['name']
    
    return index_codes, future_codes


def get_index_data(conn, days=90):
    """读取股指日交易数据"""
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    sql = """
    SELECT ts_code, trade_date, close, high, low
    FROM stock_index_daily_t
    WHERE trade_date >= %s
    ORDER BY trade_date
    """
    
    cursor = conn.cursor()
    cursor.execute(sql, (start_date,))
    results = cursor.fetchall()
    cursor.close()
    
    if not results:
        return pd.DataFrame(columns=['ts_code', 'trade_date', 'close', 'high', 'low'])
    
    if isinstance(results[0], dict):
        df = pd.DataFrame(results)
    else:
        df = pd.DataFrame(results, columns=['ts_code', 'trade_date', 'close', 'high', 'low'])
    return df


def get_future_data(conn, days=90):
    """读取股指期货日交易数据"""
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    sql = """
    SELECT ts_code, trade_date, close
    FROM stock_index_future_daily_t
    WHERE trade_date >= %s
    ORDER BY trade_date
    """
    
    cursor = conn.cursor()
    cursor.execute(sql, (start_date,))
    results = cursor.fetchall()
    cursor.close()
    
    if not results:
        return pd.DataFrame(columns=['ts_code', 'trade_date', 'close'])
    
    if isinstance(results[0], dict):
        df = pd.DataFrame(results)
    else:
        df = pd.DataFrame(results, columns=['ts_code', 'trade_date', 'close'])
    return df


def get_rzrq_data(conn, days=90):
    """读取融资融券数据"""
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    sql = """
    SELECT trade_date, rzye, rzmre, rzche, rqye, rqmcl, rqyl
    FROM rzrq_ye_t
    WHERE trade_date >= %s
    ORDER BY trade_date
    """
    
    cursor = conn.cursor()
    cursor.execute(sql, (start_date,))
    results = cursor.fetchall()
    cursor.close()
    
    if not results:
        return pd.DataFrame(columns=['trade_date', 'rzye', 'rzmre', 'rzche', 'rqye', 'rqmcl', 'rqyl'])
    
    if isinstance(results[0], dict):
        df = pd.DataFrame(results)
    else:
        df = pd.DataFrame(results, columns=['trade_date', 'rzye', 'rzmre', 'rzche', 'rqye', 'rqmcl', 'rqyl'])
    return df


def get_stock_daily_data(conn, days=30):
    """读取股票日线数据"""
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
    
    sql = """
    SELECT ts_code, trade_date, close
    FROM stock_daily_t
    WHERE trade_date >= %s
    ORDER BY ts_code, trade_date
    """
    
    cursor = conn.cursor()
    cursor.execute(sql, (start_date,))
    results = cursor.fetchall()
    cursor.close()
    
    if not results:
        return pd.DataFrame(columns=['ts_code', 'trade_date', 'close'])
    
    if isinstance(results[0], dict):
        df = pd.DataFrame(results)
    else:
        df = pd.DataFrame(results, columns=['ts_code', 'trade_date', 'close'])
    return df


def plot_basis(index_df, future_df, ax, title, index_code, future_code_prefix):
    """绘制期现差走势图"""
    # 筛选股指数据
    index_data = index_df[index_df['ts_code'] == index_code].copy()
    index_data['trade_date'] = pd.to_datetime(index_data['trade_date'], format='%Y%m%d')
    
    # 筛选股指期货数据（取主力合约）
    future_data = future_df[future_df['ts_code'].str.startswith(future_code_prefix)].copy()
    # 取每个交易日最新的合约
    future_data = future_data.sort_values('trade_date').groupby('trade_date').last().reset_index()
    future_data['trade_date'] = pd.to_datetime(future_data['trade_date'], format='%Y%m%d')
    
    # 合并数据计算期现差
    merged = pd.merge(index_data, future_data, on='trade_date', suffixes=('_index', '_future'))
    merged['basis'] = merged['close_future'] - merged['close_index']
    
    ax.plot(merged['trade_date'], merged['basis'], linewidth=2, color='blue')
    ax.axhline(y=0, color='red', linestyle='--', alpha=0.5)
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)
    ax.set_ylabel('期现差')


def plot_index_table(ax, index_df):
    """绘制股指最大震幅表格"""
    # 指数列表
    indices = {
        '000001.SH': '上证指数',
        '000300.SH': '沪深300',
        '000905.SH': '中证500',
        '000852.SH': '中证1000'
    }
    
    results = []
    
    for ts_code, name in indices.items():
        index_data = index_df[index_df['ts_code'] == ts_code].copy()
        index_data = index_data.sort_values('trade_date').reset_index(drop=True)
        
        if len(index_data) < 31:  # 需要至少31天数据才能计算近30天（不含当日）
            results.append([name, '-', '-', '-', '-'])
            continue
        
        # 获取当日的最低价
        today_low = float(index_data.iloc[-1]['low'])
        
        # 计算各时间段最大震幅 = (当日最低价 - 前N日内的最高价) / 当日最低价 * 100
        # 近30天：当日最低价与前30日（不含当日）的最高价的比值
        max_30d = float(index_data.iloc[-31:-1]['high'].max()) if len(index_data) >= 31 else None
        pct_30d = (today_low - max_30d) / today_low * 100 if max_30d is not None else None
        
        # 近20天：当日最低价与前20日（不含当日）的最高价的比值
        max_20d = float(index_data.iloc[-21:-1]['high'].max()) if len(index_data) >= 21 else None
        pct_20d = (today_low - max_20d) / today_low * 100 if max_20d is not None else None
        
        # 近10天：当日最低价与前10日（不含当日）的最高价的比值
        max_10d = float(index_data.iloc[-11:-1]['high'].max()) if len(index_data) >= 11 else None
        pct_10d = (today_low - max_10d) / today_low * 100 if max_10d is not None else None
        
        # 近5天：当日最低价与前5日（不含当日）的最高价的比值
        max_5d = float(index_data.iloc[-6:-1]['high'].max()) if len(index_data) >= 6 else None
        pct_5d = (today_low - max_5d) / today_low * 100 if max_5d is not None else None
        
        results.append([
            name,
            f'{pct_30d:.2f}%' if pct_30d is not None else '-',
            f'{pct_20d:.2f}%' if pct_20d is not None else '-',
            f'{pct_10d:.2f}%' if pct_10d is not None else '-',
            f'{pct_5d:.2f}%' if pct_5d is not None else '-'
        ])
    
    # 创建表格
    columns = ['指数名称', '近30天最大震幅', '近20天最大震幅', '近10天最大震幅', '近5天最大震幅']
    ax.axis('off')
    
    table = ax.table(
        cellText=results,
        colLabels=columns,
        cellLoc='center',
        loc='center',
        bbox=[0, 0, 1, 1]
    )
    
    table.auto_set_font_size(False)
    table.set_fontsize(12)
    table.scale(1.2, 1.5)
    
    # 设置标题
    ax.text(0.5, 1.1, '股指近期最大震幅统计', fontsize=14, fontweight='bold', ha='center', transform=ax.transAxes)


def plot_rzrq_amount(rzrq_df, ax):
    """绘制融资额走势图"""
    rzrq_df = rzrq_df.copy()
    rzrq_df['trade_date'] = pd.to_datetime(rzrq_df['trade_date'], format='%Y%m%d')
    
    # 按日期分组求和，单位转换为亿元
    grouped = rzrq_df.groupby('trade_date').agg({
        'rzye': 'sum',
        'rzmre': 'sum',
        'rzche': 'sum'
    }).reset_index()
    
    grouped['rzye'] = grouped['rzye'] / 100000000  # 元转亿元
    grouped['rzmre'] = grouped['rzmre'] / 100000000  # 元转亿元
    grouped['rzche'] = grouped['rzche'] / 100000000  # 元转亿元
    
    ax.plot(grouped['trade_date'], grouped['rzye'], label='融资余额', linewidth=2)
    ax.plot(grouped['trade_date'], grouped['rzmre'], label='融资买入额', linewidth=2)
    ax.plot(grouped['trade_date'], grouped['rzche'], label='融资偿还额', linewidth=2)
    ax.set_title('融资额走势', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)
    ax.set_ylabel('亿元')


def plot_rq_amount(rzrq_df, ax):
    """绘制融券额走势图"""
    rzrq_df = rzrq_df.copy()
    rzrq_df['trade_date'] = pd.to_datetime(rzrq_df['trade_date'], format='%Y%m%d')
    
    # 按日期分组求和，单位转换为亿元
    grouped = rzrq_df.groupby('trade_date').agg({
        'rqye': 'sum'
    }).reset_index()
    
    grouped['rqye'] = grouped['rqye'] / 100000000  # 元转亿元
    
    ax.plot(grouped['trade_date'], grouped['rqye'], label='融券余额', linewidth=2, color='red')
    ax.set_title('融券额走势', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)
    ax.set_ylabel('亿元')


def plot_rq_volume(rzrq_df, ax):
    """绘制融券量走势图"""
    rzrq_df = rzrq_df.copy()
    rzrq_df['trade_date'] = pd.to_datetime(rzrq_df['trade_date'], format='%Y%m%d')
    
    # 按日期分组求和
    grouped = rzrq_df.groupby('trade_date').agg({
        'rqmcl': 'sum',
        'rqyl': 'sum'
    }).reset_index()
    
    ax.plot(grouped['trade_date'], grouped['rqmcl'], label='融券卖出量', linewidth=2)
    ax.plot(grouped['trade_date'], grouped['rqyl'], label='融券余量', linewidth=2)
    ax.set_title('融券量走势', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)


def plot_stock_extreme_moves(stock_df, ax):
    """绘制涨跌超8%股票数量走势图"""
    stock_df = stock_df.copy()
    stock_df['trade_date'] = pd.to_datetime(stock_df['trade_date'], format='%Y%m%d')
    
    # 计算每只股票每日涨跌幅
    stock_df = stock_df.sort_values(['ts_code', 'trade_date'])
    stock_df['prev_close'] = stock_df.groupby('ts_code')['close'].shift(1)
    stock_df['pct_change'] = (stock_df['close'] - stock_df['prev_close']) / stock_df['prev_close'] * 100
    
    # 统计每日涨跌超8%的股票数量
    daily_stats = stock_df.groupby('trade_date').apply(
        lambda x: pd.Series({
            'up_count': (x['pct_change'] > 8).sum(),
            'down_count': (x['pct_change'] < -8).sum()
        })
    ).reset_index()
    
    ax.plot(daily_stats['trade_date'], daily_stats['up_count'], label='涨幅>8%', linewidth=2, color='red')
    ax.plot(daily_stats['trade_date'], daily_stats['down_count'], label='跌幅<-8%', linewidth=2, color='green')
    ax.set_title('股票涨跌超8%数量走势', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)
    ax.set_ylabel('股票数量')


def main():
    print("=" * 80)
    print("大盘趋势报告 V2")
    print("=" * 80)
    
    # 连接数据库
    print("\n🔌 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return
    print("✅ 数据库连接成功")
    
    # 读取股指和期货代码
    print("\n📋 读取股指和期货代码...")
    index_codes, future_codes = read_index_codes()
    print(f"✅ 读取到 {len(index_codes)} 个股指代码，{len(future_codes)} 个期货代码")
    
    # 获取数据
    print("\n📊 获取数据...")
    index_df = get_index_data(conn, days=90)
    print(f"✅ 获取股指数据: {len(index_df)} 条")
    
    future_df = get_future_data(conn, days=90)
    print(f"✅ 获取股指期货数据: {len(future_df)} 条")
    
    rzrq_df = get_rzrq_data(conn, days=90)
    print(f"✅ 获取融资融券数据: {len(rzrq_df)} 条")
    
    stock_df = get_stock_daily_data(conn, days=30)
    print(f"✅ 获取股票日线数据: {len(stock_df)} 条")
    
    # 关闭数据库连接
    close_connection(conn)
    
    # 创建图表（8个子图）
    print("\n📈 生成图表...")
    fig, axes = plt.subplots(8, 1, figsize=(14, 35))
    fig.suptitle(f'大盘趋势报告 V2 - {get_target_date()}', fontsize=16, fontweight='bold', y=0.995)
    
    # 第一部分：期现差走势（3个图表）
    plot_basis(index_df, future_df, axes[0], '沪深300期现差走势', '000300.SH', 'IF')
    plot_basis(index_df, future_df, axes[1], '中证500期现差走势', '000905.SH', 'IC')
    plot_basis(index_df, future_df, axes[2], '中证1000期现差走势', '000852.SH', 'IM')
    
    # 第二部分：股指涨跌幅表格
    plot_index_table(axes[3], index_df)
    
    # 第三部分：融资融券走势（3个图表）
    plot_rzrq_amount(rzrq_df, axes[4])
    plot_rq_amount(rzrq_df, axes[5])
    plot_rq_volume(rzrq_df, axes[6])
    
    # 第四部分：涨跌超8%股票数量走势
    plot_stock_extreme_moves(stock_df, axes[7])
    
    plt.tight_layout(rect=[0, 0, 1, 0.99])
    
    # 保存图表
    target_date = get_target_date()
    output_filename = f"大盘趋势报告v2{target_date}.png"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, output_filename)
    
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"\n✅ 报告已生成: {output_path}")
    
    plt.close()
    
    print("\n" + "=" * 80)
    print("🎉 大盘趋势报告 V2 生成完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()