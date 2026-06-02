#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测报告生成器

功能：
1. 查找市值>50亿的非ST股
2. 使用select_newhigh_in_120d.py的4个选股条件进行筛选
3. 为每只满足条件的股票生成K线图HTML，标记满足策略的日期
"""

import os
import shutil
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple
from mysql_connection import get_mysql_connection, close_connection


def get_stock_basic_info(conn) -> pd.DataFrame:
    """获取股票基础信息（市值>50亿，非ST）"""
    try:
        cursor = conn.cursor()
        sql = """
            SELECT ts_code, stock_name, total_mv
            FROM stock_info_t
            WHERE total_mv > 5000000000
              AND stock_name NOT LIKE '%ST%'
              AND stock_name NOT LIKE '%*ST%'
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        return pd.DataFrame(rows, columns=['ts_code', 'stock_name', 'total_mv'])
    except Exception as e:
        print(f"❌ 获取股票基础信息失败: {e}")
        return pd.DataFrame()


def get_stock_data_with_info(conn, start_date='20250101') -> pd.DataFrame:
    """获取股票数据（含开盘价、收盘价、最高价、最低价、成交量、成交额）"""
    try:
        cursor = conn.cursor()
        sql = """
            SELECT
                d.ts_code,
                d.trade_date,
                d.open,
                d.high,
                d.low,
                d.close,
                d.vol,
                d.amount,
                d.pre_close,
                i.stock_name
            FROM stock_daily_t d
            LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
            WHERE d.trade_date >= %s
            ORDER BY d.ts_code, d.trade_date
        """
        cursor.execute(sql, (start_date,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return pd.DataFrame(rows, columns=columns)
    except Exception as e:
        print(f"❌ 获取股票数据失败: {e}")
        return pd.DataFrame()


def check_newhigh_strategy_with_dates(group: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    检查股票是否满足选股策略，返回满足的所有日期
    选股策略（4个条件）：
    1. 收盘价创120日新高
    2. 当日涨幅 > 8%
    3. 成交额 > 5亿（500000千元）
    4. 120日波幅 < 25%（最低/最高 > 75%）
    """
    group = group.reset_index(drop=True)
    qualifying_dates = []

    for i in range(120, len(group)):
        current = group.iloc[i]
        past_120 = group.iloc[i-120:i]

        if current['close'] <= past_120['close'].max():
            continue

        if pd.isna(current['pre_close']) or current['pre_close'] <= 0:
            continue
        gain = (current['close'] - current['pre_close']) / current['pre_close'] * 100
        if gain <= 8:
            continue

        if pd.isna(current['amount']) or current['amount'] < 500000:
            continue

        min_close_120 = past_120['close'].min()
        max_close_120 = past_120['close'].max()
        if min_close_120 / max_close_120 <= 0.75:
            continue

        qualifying_dates.append(current['trade_date'])

    return len(qualifying_dates) > 0, qualifying_dates


def generate_kline_html(stock_name: str, ts_code: str, df: pd.DataFrame,
                         qualifying_dates: List[str], output_path: str):
    """生成单只股票的K线图HTML"""

    dates = df['trade_date'].tolist()
    opens = df['open'].tolist()
    closes = df['close'].tolist()
    highs = df['high'].tolist()
    lows = df['low'].tolist()
    volumes = df['vol'].tolist()

    kline_data = []
    for i in range(len(dates)):
        kline_data.append({
            "date": str(dates[i]),
            "open": opens[i],
            "close": closes[i],
            "high": highs[i],
            "low": lows[i],
            "vol": volumes[i]
        })

    kline_json = str(kline_data).replace("'", "")

    qualifying_set = set(qualifying_dates)
    mark_points = []
    for d in qualifying_dates:
        for i, date in enumerate(dates):
            if str(date) == d:
                mark_points.append({"coord": [i, closes[i]], "value": closes[i]})
                break

    mark_points_json = str(mark_points).replace("'", "")

    html_content = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{stock_name} ({ts_code}) - K线图</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{ margin: 0; padding: 20px; background: #1a1a2e; }}
        .header {{ color: #eee; font-family: Arial; margin-bottom: 20px; }}
        .chart-container {{ width: 100%; height: 600px; background: #16213e; border-radius: 10px; }}
        .legend {{ color: #eee; margin-top: 10px; font-family: Arial; }}
        .qualify-info {{ color: #4ade80; margin-top: 10px; font-family: Arial; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>{stock_name} ({ts_code})</h2>
        <p>选股策略满足次数: {len(qualifying_dates)} 次</p>
        <p>满足策略的日期: {', '.join(qualifying_dates)}</p>
    </div>
    <div id="chart" class="chart-container"></div>
    <div class="legend">
        <span style="color: #4ade80;">● 满足策略日期</span> |
        <span style="color: #ff6b6b;">● K线</span>
    </div>

    <script>
        var chart = echarts.init(document.getElementById('chart'));

        var dates = {str(dates).replace("'", '"')};
        var klineData = {kline_json};
        var markPoints = {mark_points_json};

        var option = {{
            backgroundColor: '#16213e',
            tooltip: {{
                trigger: 'axis',
                axisPointer: {{ type: 'cross' }}
            }},
            grid: {{
                left: '10%',
                right: '10%',
                top: '15%',
                bottom: '15%'
            }},
            xAxis: {{
                type: 'category',
                data: dates,
                axisLine: {{ lineStyle: {{ color: '#4ade80' }} }},
                axisLabel: {{ color: '#eee' }}
            }},
            yAxis: {{
                scale: true,
                axisLine: {{ lineStyle: {{ color: '#4ade80' }} }},
                axisLabel: {{ color: '#eee' }},
                splitLine: {{ color: '#333' }}
            }},
            dataZoom: [
                {{ type: 'inside', start: 0, end: 100 }},
                {{ type: 'slider', start: 0, end: 100 }}
            ],
            series: [
                {{
                    name: 'K线',
                    type: 'candlestick',
                    data: klineData.map(d => [d.open, d.close, d.low, d.high]),
                    itemStyle: {{
                        color: '#ff6b6b',
                        color0: '#4ade80',
                        borderColor: '#ff6b6b',
                        borderColor0: '#4ade80'
                    }},
                    markPoint: {{
                        symbol: 'circle',
                        symbolSize: 10,
                        label: {{
                            color: '#fff',
                            fontSize: 10
                        }},
                        data: markPoints,
                        itemStyle: {{
                            color: '#f59e0b',
                            borderColor: '#f59e0b',
                            borderWidth: 2
                        }}
                    }}
                }}
            ]
        }};

        chart.setOption(option);
        window.addEventListener('resize', function() {{
            chart.resize();
        }});
    </script>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)


def main():
    print("=" * 80)
    print("📊 120日新高策略 - K线图回测报告生成器")
    print("=" * 80)

    output_dir = "回测报告"
    if os.path.exists(output_dir):
        print(f"\n🗑️  删除旧目录: {output_dir}")
        shutil.rmtree(output_dir)

    print(f"\n📁 创建目录: {output_dir}")
    os.makedirs(output_dir)

    print("\n🔌 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    print("\n📋 获取股票基础信息...")
    stock_info = get_stock_basic_info(conn)
    if stock_info.empty:
        print("❌ 没有获取到股票信息")
        close_connection(conn)
        return
    print(f"   ✅ 获取到 {len(stock_info)} 只股票（市值>50亿，非ST）")

    print("\n📅 获取股票K线数据...")
    df = get_stock_data_with_info(conn)
    if df.empty:
        print("❌ 没有获取到K线数据")
        close_connection(conn)
        return
    print(f"   ✅ 获取到 {len(df)} 条记录")

    print("\n🔍 转换数据类型...")
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df['open'] = pd.to_numeric(df['open'], errors='coerce')
    df['high'] = pd.to_numeric(df['high'], errors='coerce')
    df['low'] = pd.to_numeric(df['low'], errors='coerce')
    df['vol'] = pd.to_numeric(df['vol'], errors='coerce')
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
    df['pre_close'] = pd.to_numeric(df['pre_close'], errors='coerce')

    print("\n🚀 开始分析选股策略...")
    qualified_count = 0
    total_analyzed = 0

    grouped = df.groupby('ts_code')

    for ts_code, group in grouped:
        total_analyzed += 1
        if total_analyzed % 200 == 0:
            print(f"   ⏳ 已分析 {total_analyzed} 只股票...")

        stock_name_row = stock_info[stock_info['ts_code'] == ts_code]
        if stock_name_row.empty:
            continue
        stock_name = stock_name_row.iloc[0]['stock_name']

        is_qualified, qualifying_dates = check_newhigh_strategy_with_dates(group)

        if not is_qualified:
            continue

        qualified_count += 1

        safe_stock_name = stock_name.replace('/', '_').replace('\\', '_').replace('<', '_').replace('>', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('|', '_')
        html_filename = f"{safe_stock_name}_{ts_code}.html"
        html_path = os.path.join(output_dir, html_filename)

        group_sorted = group.sort_values('trade_date')
        generate_kline_html(stock_name, ts_code, group_sorted, qualifying_dates, html_path)

    print(f"\n✅ 分析完成！共分析 {total_analyzed} 只股票，{qualified_count} 只满足策略")

    close_connection(conn)
    print(f"\n💾 HTML文件已保存到: {output_dir}/")
    print("\n🎉 全部完成！")


if __name__ == "__main__":
    main()