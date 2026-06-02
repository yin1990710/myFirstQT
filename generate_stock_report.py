#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np

from mysql_connection import get_mysql_connection, close_connection

plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti']
plt.rcParams['axes.unicode_minus'] = False

def get_report_dir() -> str:
    """
    获取报告目录路径（带日期后缀）

    :return: 目录路径，格式为 品字定向法股票分析报告_YYYYMMDD
    如果当前时间为0-18点，则取前一天的日期
    """
    now = datetime.now()
    hour = now.hour

    if 0 <= hour < 18:
        target_date = now - timedelta(days=1)
    else:
        target_date = now

    return os.path.join(os.getcwd(), f'品字定向法股票分析报告_{target_date.strftime("%Y%m%d")}')

def create_report_dir() -> str:
    """
    创建报告目录，如果已存在则先删除再新建

    :return: 目录路径
    """
    report_dir = get_report_dir()

    if os.path.exists(report_dir):
        shutil.rmtree(report_dir)
        print(f"   🗑️ 删除已存在的目录: {report_dir}")

    os.makedirs(report_dir)
    print(f"   ✅ 创建新目录: {report_dir}")

    return report_dir

def get_table_name() -> str:
    """
    获取带日期后缀的win_win_stock_t表名

    :return: 表名，格式为 win_win_stock_t_YYYYMMDD
    如果当前时间为0-18点，则取前一天的日期
    """
    now = datetime.now()
    hour = now.hour

    if 0 <= hour < 18:
        target_date = now - timedelta(days=1)
        print(f"   ℹ️ 当前时间 {hour}点，使用前一天日期: {target_date.strftime('%Y%m%d')}")
    else:
        target_date = now

    return f"win_win_stock_t_{target_date.strftime('%Y%m%d')}"

def get_stock_basic_info(conn, ts_code: str) -> dict:
    """
    从stock_info_t表获取股票基本信息

    :param conn: 数据库连接
    :param ts_code: 股票代码
    :return: 股票信息字典
    """
    try:
        cursor = conn.cursor()
        sql = """
            SELECT ts_code, stock_name, industry, area, total_share, float_share,
                   total_mv, circ_mv, list_date, market
            FROM stock_info_t
            WHERE ts_code = %s
        """
        cursor.execute(sql, (ts_code,))
        result = cursor.fetchone()
        return result if result else {}
    except Exception as e:
        print(f"⚠️ 获取 {ts_code} 基本信息失败: {e}")
        return {}

def get_win_win_stocks(conn) -> list:
    """
    从win_win_stock_t_YYYYMMDD表获取所有股票代码列表

    :param conn: 数据库连接
    :return: 股票代码列表
    """
    table_name = get_table_name()
    try:
        cursor = conn.cursor()
        sql = f"SELECT DISTINCT ts_code FROM {table_name}"
        cursor.execute(sql)
        results = cursor.fetchall()
        return [r['ts_code'] for r in results]
    except Exception as e:
        print(f"❌ 获取股票列表失败: {e}")
        return []

def get_stock_daily_data(conn, ts_code: str) -> pd.DataFrame:
    """
    获取股票日线数据（从win_win_stock_t_YYYYMMDD表）

    :param conn: 数据库连接
    :param ts_code: 股票代码
    :return: 日线数据DataFrame
    """
    table_name = get_table_name()
    try:
        cursor = conn.cursor()
        sql = f"""
            SELECT trade_date, open, high, low, close, vol, amount, turning_point
            FROM {table_name}
            WHERE ts_code = %s
            ORDER BY trade_date
        """
        cursor.execute(sql, (ts_code,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        if rows and isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame(rows, columns=columns)

        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        return df
    except Exception as e:
        print(f"⚠️ 获取 {ts_code} 日线数据失败: {e}")
        return pd.DataFrame()

def get_index_data(conn) -> pd.DataFrame:
    """
    获取中证全指(000985.CSI)日线数据

    :param conn: 数据库连接
    :return: 中证全指日线数据DataFrame
    """
    try:
        cursor = conn.cursor()
        sql = """
            SELECT trade_date, open, high, low, close
            FROM index_daily_t
            WHERE ts_code = '000985.CSI'
            ORDER BY trade_date
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        if rows and isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame(rows, columns=columns)

        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        return df
    except Exception as e:
        print(f"⚠️ 获取中证全指数据失败: {e}")
        return pd.DataFrame()

def generate_index_chart(df: pd.DataFrame, output_path: str) -> dict:
    """
    生成中证全指走势K线图，计算行情风险指数和机会指数

    :param df: 中证全指日线数据
    :param output_path: 输出路径
    :return: 包含风险指数和机会指数信息的字典
    """
    if df.empty or len(df) < 30:
        print("⚠️ 中证全指数据不足，无法生成图表")
        return {}

    # 计算近30日涨跌幅
    df_last30 = df.tail(30)
    start_price = float(df_last30['close'].iloc[0])
    end_price = float(df_last30['close'].iloc[-1])
    pct_change = (end_price - start_price) / start_price * 100

    # 计算行情风险指数
    risk_level = 0
    if pct_change >= 0:
        if pct_change < 2:
            risk_level = 1
        elif pct_change < 3:
            risk_level = 2
        elif pct_change < 5:
            risk_level = 3
        elif pct_change < 7:
            risk_level = 4
        else:
            risk_level = 5

    # 计算行情机会指数
    opportunity_level = 0
    if pct_change < 0:
        abs_change = abs(pct_change)
        if abs_change < 3:
            opportunity_level = 1
        elif abs_change < 5:
            opportunity_level = 2
        elif abs_change < 7:
            opportunity_level = 3
        elif abs_change < 9:
            opportunity_level = 4
        else:
            opportunity_level = 5

    # 生成K线图
    fig, ax = plt.subplots(figsize=(16, 8))
    width = 0.6

    for idx, row in df.iterrows():
        trade_date = mdates.date2num(row['trade_date'])
        open_price = float(row['open'])
        high_price = float(row['high'])
        low_price = float(row['low'])
        close_price = float(row['close'])

        if close_price >= open_price:
            color = 'red'
            body_bottom = open_price
            body_height = close_price - open_price
        else:
            color = 'green'
            body_bottom = close_price
            body_height = open_price - close_price

        ax.plot([trade_date, trade_date], [low_price, high_price], color=color, linewidth=0.8)
        ax.add_patch(plt.Rectangle((trade_date - width/2, body_bottom), width, body_height if body_height > 0 else 0.1,
                                   edgecolor=color, facecolor=color, linewidth=0.8))

    ax.set_ylabel('指数点位', fontsize=12)
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_title('中证全指 (932078.CSI) 走势K线图', fontsize=14, pad=10)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

    return {
        'risk_level': risk_level,
        'opportunity_level': opportunity_level,
        'pct_change': pct_change
    }

def get_latest_valley_peak(df: pd.DataFrame) -> dict:
    """
    从日线数据中获取所有波谷波峰对

    :param df: 日线数据
    :return: 包含波谷波峰信息的字典，包含所有波谷波峰对列表
    """
    info = {
        'valley_date': None,
        'valley_price': None,
        'peak_date': None,
        'peak_price': None,
        'valley_peak_pairs': []
    }

    valleys = df[df['turning_point'] == '波谷']
    peaks = df[df['turning_point'] == '波峰']

    # 获取所有波谷波峰对
    valley_peak_pairs = []
    for i in range(len(valleys)):
        valley_row = valleys.iloc[i]
        valley_date = valley_row['trade_date']
        valley_price = float(valley_row['close'])

        # 找到该波谷之后的第一个波峰
        following_peaks = peaks[peaks['trade_date'] > valley_date]
        if not following_peaks.empty:
            peak_row = following_peaks.iloc[0]
            peak_date = peak_row['trade_date']
            peak_price = float(peak_row['close'])

            valley_peak_pairs.append({
                'pair_index': i + 1,
                'valley_date': valley_date,
                'valley_price': valley_price,
                'peak_date': peak_date,
                'peak_price': peak_price,
                'increase': (peak_price - valley_price) / valley_price * 100
            })

    info['valley_peak_pairs'] = valley_peak_pairs

    if len(valleys) >= 1:
        last_valley = valleys.iloc[-1]
        info['valley_date'] = last_valley['trade_date']
        info['valley_price'] = float(last_valley['close'])

    if len(peaks) >= 1:
        last_peak = peaks.iloc[-1]
        info['peak_date'] = last_peak['trade_date']
        info['peak_price'] = float(last_peak['close'])

    return info

def format_number(num):
    """格式化数字显示"""
    if num is None or (isinstance(num, float) and np.isnan(num)):
        return 'N/A'
    try:
        num = float(num)
        if num >= 1e8:
            return f'{num/1e8:.2f}亿'
        elif num >= 1e4:
            return f'{num/1e4:.2f}万'
        else:
            return f'{num:.2f}'
    except:
        return str(num)

def generate_price_chart(df: pd.DataFrame, valley_info: dict, output_path: str):
    """
    生成股价K线图，标记波谷和波峰，并用弧线箭头从波谷指向波峰

    :param df: 日线数据
    :param valley_info: 波谷波峰信息
    :param output_path: 输出路径
    """
    fig, ax = plt.subplots(figsize=(16, 10))

    width = 0.6

    for idx, row in df.iterrows():
        trade_date = mdates.date2num(row['trade_date'])
        open_price = float(row['open'])
        high_price = float(row['high'])
        low_price = float(row['low'])
        close_price = float(row['close'])

        if close_price >= open_price:
            color = 'red'
            body_bottom = open_price
            body_height = close_price - open_price
        else:
            color = 'green'
            body_bottom = close_price
            body_height = open_price - close_price

        ax.plot([trade_date, trade_date], [low_price, high_price], color=color, linewidth=0.8)
        ax.add_patch(plt.Rectangle((trade_date - width/2, body_bottom), width, body_height if body_height > 0 else 0.1,
                                   edgecolor=color, facecolor=color, linewidth=0.8))

    valleys = df[df['turning_point'] == '波谷']
    peaks = df[df['turning_point'] == '波峰']

    if not valleys.empty:
        ax.scatter(valleys['trade_date'], valleys['close'], c='lime', s=300, marker='^',
                   label='波谷（买入点）', zorder=10, edgecolors='darkgreen', linewidths=2)
        for _, row in valleys.iterrows():
            ax.annotate(f'¥{row["close"]:.2f}',
                        (row['trade_date'], row['close']),
                        xytext=(5, 15), textcoords='offset points',
                        fontsize=11, color='green', fontweight='bold',
                        bbox=dict(boxstyle='round', facecolor='white', edgecolor='green', alpha=0.9))

    if not peaks.empty:
        ax.scatter(peaks['trade_date'], peaks['close'], c='red', s=300, marker='v',
                   label='波峰（卖出点）', zorder=10, edgecolors='darkred', linewidths=2)
        for _, row in peaks.iterrows():
            ax.annotate(f'¥{row["close"]:.2f}',
                        (row['trade_date'], row['close']),
                        xytext=(5, -20), textcoords='offset points',
                        fontsize=11, color='red', fontweight='bold',
                        bbox=dict(boxstyle='round', facecolor='white', edgecolor='red', alpha=0.9))

    if not valleys.empty and not peaks.empty:
        for i in range(len(valleys) - 1):
            valley_row = valleys.iloc[i]
            valley_date = valley_row['trade_date']
            valley_price = float(valley_row['close'])

            following_peaks = peaks[peaks['trade_date'] > valley_date]
            if not following_peaks.empty:
                peak_row = following_peaks.iloc[0]
                peak_date = peak_row['trade_date']
                peak_price = float(peak_row['close'])

                mid_x = mdates.date2num(valley_date) + (mdates.date2num(peak_date) - mdates.date2num(valley_date)) / 2
                mid_y = valley_price + (peak_price - valley_price) / 2
                arc_height = (peak_price - valley_price) * 0.4

                t = np.linspace(0, 1, 50)
                x_arc = mdates.date2num(valley_date) + t * (mdates.date2num(peak_date) - mdates.date2num(valley_date))
                y_arc = valley_price + t * (peak_price - valley_price) + arc_height * np.sin(t * np.pi)

                ax.annotate('',
                           xy=(mdates.date2num(peak_date), peak_price),
                           xytext=(mdates.date2num(valley_date), valley_price),
                           arrowprops=dict(arrowstyle='->', color='orange', lw=2,
                                         connectionstyle='arc3,rad=0.3'))

                ax.plot(x_arc, y_arc, 'orange', linewidth=2, alpha=0.7)
                ax.text(mid_x, mid_y + arc_height * 0.3, f'{i+1}',
                       fontsize=12, color='orange', fontweight='bold',
                       ha='center', va='center',
                       bbox=dict(boxstyle='round', facecolor='yellow', edgecolor='orange', alpha=0.8))

    ax.set_ylabel('价格 (¥)', fontsize=12)
    ax.legend(loc='upper left', fontsize=10)
    ax.grid(True, alpha=0.3, linestyle='--')
    ax.set_title('股票走势K线图 - 波谷波峰标记（弧线箭头指向）', fontsize=14, pad=10)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

def generate_html_report(stock_info: dict, df: pd.DataFrame, valley_info: dict,
                        chart_path: str, output_path: str, index_chart_path: str = None,
                        index_info: dict = None):
    """
    生成HTML格式的分析报告

    :param stock_info: 股票基本信息
    :param df: 日线数据
    :param valley_info: 波谷波峰信息
    :param chart_path: 图表路径
    :param output_path: 输出路径
    :param index_chart_path: 中证全指图表路径
    :param index_info: 中证全指指数信息
    """
    ts_code = stock_info.get('ts_code', '')
    stock_name = stock_info.get('stock_name', ts_code)
    current_price = df['close'].iloc[-1] if not df.empty else None
    current_price_str = f'{current_price:.2f}' if current_price is not None else 'N/A'

    valley_date_str = valley_info['valley_date'].strftime('%Y%m%d') if valley_info['valley_date'] else 'N/A'
    peak_date_str = valley_info['peak_date'].strftime('%Y%m%d') if valley_info['peak_date'] else 'N/A'
    valley_price_str = f'{valley_info["valley_price"]:.2f}' if valley_info['valley_price'] else 'N/A'
    peak_price_str = f'{valley_info["peak_price"]:.2f}' if valley_info['peak_price'] else 'N/A'

    # 生成买卖信号表格行
    signal_table_rows = ''
    for pair in valley_info.get('valley_peak_pairs', []):
        valley_date = pair['valley_date'].strftime('%Y-%m-%d')
        peak_date = pair['peak_date'].strftime('%Y-%m-%d')
        increase_class = 'increase-positive' if pair['increase'] >= 0 else 'increase-negative'
        increase_str = f'{pair["increase"]:.2f}%'

        signal_table_rows += f'''
        <tr class="valley-row">
            <td>{pair["pair_index"]}</td>
            <td><span class="buy-tag">买入（波谷）</span></td>
            <td>{valley_date}</td>
            <td>¥{pair["valley_price"]:.2f}</td>
            <td rowspan="2" class="{increase_class}">{increase_str}</td>
        </tr>
        <tr class="peak-row">
            <td></td>
            <td><span class="sell-tag">卖出（波峰）</span></td>
            <td>{peak_date}</td>
            <td>¥{pair["peak_price"]:.2f}</td>
        </tr>
        '''

    if not signal_table_rows:
        signal_table_rows = '''
        <tr>
            <td colspan="5" style="text-align: center; color: #666;">暂无波谷波峰数据</td>
        </tr>
        '''

    # 生成指数标签和定义
    index_section = ''
    if index_chart_path and index_info:
        risk_level = index_info.get('risk_level', 0)
        opportunity_level = index_info.get('opportunity_level', 0)
        pct_change = index_info.get('pct_change', 0)

        risk_stars = '⭐' * risk_level if risk_level > 0 else '-'
        opportunity_stars = '⭐' * opportunity_level if opportunity_level > 0 else '-'

        index_section = f'''
            <div class="section">
                <div class="section-title">📊 中证全指走势</div>
                <div class="chart-container">
                    <img src="{os.path.basename(index_chart_path)}" alt="中证全指走势图">
                </div>
                <div style="margin-top: 15px;">
                    <div style="display: flex; gap: 20px; flex-wrap: wrap;">
                        <div class="info-card" style="flex: 1; min-width: 250px;">
                            <div class="info-label">行情风险指数</div>
                            <div class="info-value" style="color: #dc3545; font-size: 20px;">{risk_stars}</div>
                        </div>
                        <div class="info-card" style="flex: 1; min-width: 250px;">
                            <div class="info-label">行情机会指数</div>
                            <div class="info-value" style="color: #28a745; font-size: 20px;">{opportunity_stars}</div>
                        </div>
                    </div>
                    <div style="margin-top: 15px; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                        <div style="font-size: 13px; color: #666; margin-bottom: 8px; font-weight: bold;">指数定义：</div>
                        <div style="font-size: 12px; color: #666; line-height: 1.8;">
                            <strong>行情风险指数：</strong>近30日涨{abs(pct_change):.2f}%<br>
                            &nbsp;&nbsp;• 涨2%以内为1星 | 涨2%-2.99%为2星 | 涨3%-4.99%为3星 | 涨5%-6.99%为4星 | 涨超7%为5星<br><br>
                            <strong>行情机会指数：</strong><br>
                            &nbsp;&nbsp;• 跌3%以内为1星 | 跌3.01%-4.99%为2星 | 跌5%-6.99%为3星 | 跌7%-8.99%为4星 | 跌超9%为5星
                        </div>
                    </div>
                </div>
            </div>
        '''

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{stock_name} ({ts_code}) - 股票分析报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.15);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.9; font-size: 14px; }}
        .content {{ padding: 25px; }}
        .section {{ margin-bottom: 25px; }}
        .section-title {{
            display: inline-block;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 20px;
            border-radius: 20px;
            font-size: 16px;
            margin-bottom: 15px;
        }}
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        .info-card {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 10px;
            border-left: 4px solid #11998e;
        }}
        .info-label {{ font-size: 12px; color: #666; margin-bottom: 5px; }}
        .info-value {{ font-size: 18px; font-weight: bold; color: #333; }}
        .chart-container {{ background: #f8f9fa; padding: 15px; border-radius: 10px; }}
        .chart-container img {{ width: 100%; border-radius: 8px; }}
        .signal-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        .signal-table th, .signal-table td {{
            padding: 12px;
            text-align: center;
            border: 1px solid #dee2e6;
        }}
        .signal-table th {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            font-weight: bold;
        }}
        .signal-table tr:nth-child(even) {{
            background-color: #f8f9fa;
        }}
        .signal-table tr:hover {{
            background-color: #e9ecef;
        }}
        .valley-row {{
            background-color: rgba(40, 167, 69, 0.1);
        }}
        .peak-row {{
            background-color: rgba(220, 53, 69, 0.1);
        }}
        .buy-tag {{
            background: #28a745;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }}
        .sell-tag {{
            background: #dc3545;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }}
        .increase-positive {{
            color: #dc3545;
            font-weight: bold;
        }}
        .increase-negative {{
            color: #28a745;
            font-weight: bold;
        }}
        .footer {{
            background: #f8f9fa;
            padding: 15px;
            text-align: center;
            color: #666;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{stock_name} ({ts_code})</h1>
            <p>Win-Win 上涨趋势股票分析报告</p>
        </div>

        <div class="content">
            {index_section}

            <div class="section">
                <div class="section-title">📋 基本信息</div>
                <div class="info-grid">
                    <div class="info-card">
                        <div class="info-label">股票名称</div>
                        <div class="info-value">{stock_info.get('stock_name', 'N/A')}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">股票代码</div>
                        <div class="info-value">{stock_info.get('ts_code', 'N/A')}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">所属行业</div>
                        <div class="info-value">{stock_info.get('industry', 'N/A')}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">所属地区</div>
                        <div class="info-value">{stock_info.get('area', 'N/A')}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">总股本</div>
                        <div class="info-value">{format_number(stock_info.get('total_share'))}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">流通股本</div>
                        <div class="info-value">{format_number(stock_info.get('float_share'))}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">总市值</div>
                        <div class="info-value">{format_number(stock_info.get('total_mv'))}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">流通市值</div>
                        <div class="info-value">{format_number(stock_info.get('circ_mv'))}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">上市日期</div>
                        <div class="info-value">{stock_info.get('list_date', 'N/A')}</div>
                    </div>
                    <div class="info-card">
                        <div class="info-label">当前价格</div>
                        <div class="info-value" style="color: #11998e;">¥{current_price_str}</div>
                    </div>
                </div>
            </div>

            <div class="section">
                <div class="section-title">📊 股价走势图</div>
                <div class="chart-container">
                    <img src="{os.path.basename(chart_path)}" alt="股价走势图">
                </div>
                <div style="margin-top: 10px; font-size: 12px; color: #666; text-align: center;">
                    💡 绿色三角形标记波谷（买入信号），红色倒三角形标记波峰（卖出信号），绿色弧线连接最近一对波谷波峰
                </div>
            </div>

            <div class="section">
                <div class="section-title">📈 买卖信号（波谷波峰对）</div>
                <table class="signal-table">
                    <thead>
                        <tr>
                            <th>序号</th>
                            <th>类型</th>
                            <th>日期</th>
                            <th>收盘价(¥)</th>
                            <th>波谷→波峰涨幅</th>
                        </tr>
                    </thead>
                    <tbody>
                        {signal_table_rows}
                    </tbody>
                </table>
                <div style="margin-top: 10px; font-size: 12px; color: #666; text-align: center;">
                    💡 绿色背景行表示波谷（买入信号），红色背景行表示波峰（卖出信号）
                </div>
            </div>
        </div>

        <div class="footer">
            报告生成时间: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')} | Win-Win 股票分析系统
        </div>
    </div>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

def create_stock_report(conn, ts_code: str, report_dir: str, index_chart_path: str = None,
                        index_info: dict = None) -> bool:
    """
    为单个股票生成分析报告

    :param conn: 数据库连接
    :param ts_code: 股票代码
    :param report_dir: 报告目录
    :param index_chart_path: 中证全指图表路径
    :param index_info: 中证全指指数信息
    :return: 是否成功
    """
    stock_info = get_stock_basic_info(conn, ts_code)
    if not stock_info:
        stock_info = {'ts_code': ts_code, 'stock_name': ts_code}

    df = get_stock_daily_data(conn, ts_code)
    if df.empty:
        print(f"⚠️ {ts_code} 没有日线数据")
        return False

    valley_info = get_latest_valley_peak(df)

    stock_name = stock_info.get('stock_name', ts_code)
    safe_name = stock_name
    for char in ['/', '\\', '*', '?', '"', '<', '>', '|', ':', '{', '}']:
        safe_name = safe_name.replace(char, '_')

    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 18:
        target_date = now - timedelta(days=1)
    else:
        target_date = now
    today = target_date.strftime('%Y%m%d')

    chart_path = os.path.join(report_dir, f'{safe_name}_{today}_走势图.png')
    generate_price_chart(df, valley_info, chart_path)

    html_path = os.path.join(report_dir, f'{safe_name}_{today}_分析报告.html')
    generate_html_report(stock_info, df, valley_info, chart_path, html_path, index_chart_path, index_info)

    print(f"   ✅ {safe_name}_{today}_分析报告.html")
    return True

def main():
    """
    主函数：生成Win-Win股票分析报告
    """
    print("=" * 60)
    print("📊 Win-Win 股票分析报告生成工具")
    print("=" * 60)

    print("\n📁 步骤1: 创建报告目录")
    report_dir = create_report_dir()

    print("\n🔌 步骤2: 连接数据库")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库")
        sys.exit(1)

    try:
        print("\n📋 步骤3: 获取股票列表")
        table_name = get_table_name()
        print(f"   📊 数据源表: {table_name}")
        stock_codes = get_win_win_stocks(conn)

        if not stock_codes:
            print(f"❌ 表 {table_name} 中没有找到股票数据，请先运行筛选程序")
            return

        print(f"   ✅ 找到 {len(stock_codes)} 只股票")

        print("\n📈 步骤4: 生成中证全指图表")
        now = datetime.now()
        hour = now.hour
        if 0 <= hour < 18:
            target_date = now - timedelta(days=1)
        else:
            target_date = now
        today = target_date.strftime('%Y%m%d')
        index_chart_path = os.path.join(report_dir, f'中证全指_{today}_走势图.png')
        index_df = get_index_data(conn)
        index_info = generate_index_chart(index_df, index_chart_path)
        if index_info:
            print(f"   ✅ 中证全指图表生成成功")
        else:
            print(f"   ⚠️ 中证全指图表生成失败")

        print("\n📈 步骤5: 生成股票分析报告")
        success_count = 0
        fail_count = 0

        for ts_code in stock_codes:
            try:
                if create_stock_report(conn, ts_code, report_dir, index_chart_path, index_info):
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                print(f"   ⚠️ 生成 {ts_code} 报告失败: {e}")
                fail_count += 1

        print(f"\n📊 生成结果:")
        print("-" * 40)
        print(f"   成功: {success_count} 只")
        print(f"   失败: {fail_count} 只")
        print(f"   报告目录: {report_dir}")
        print("\n🎉 分析报告生成完成!")

    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()