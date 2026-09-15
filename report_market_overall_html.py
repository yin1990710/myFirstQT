#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
大盘风险监测数据生成任务 (report_market_overall_html.py)

参照 report_stock_overall.py（matplotlib 版大盘趋势报告）的统计口径，
计算页面所需数据并输出 JSON 文件 pages/market_overall_data.json，
由 Flask /api/market_data 接口读取后供前端 pages/大盘整体情况.html 渲染。

数据内容与 PNG 版报告一致：
  1. 指数统计表：三大指数期现差边际变化均值、上证指数/中证全指最新收盘/30日均价/偏离度；
  2. 期现差走势：沪深300(IF)、中证500(IC)、中证1000(IM)；
  3. 股指近期最大震幅统计表：上证/沪深300/中证500/中证1000 近30/20/10/5 日；
  4. 融资额走势（融资余额/融资买入额/融资偿还额，亿元）；
  5. 融券额走势（融券余额，亿元）；
  6. 融券量走势（融券卖出量/融券余量）；
  7. 每日涨幅>8%/跌幅<-8% 股票数量走势。

用法：
  .venv/bin/python report_market_overall_html.py
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

# 直接复用 PNG 版报告的数据查询函数，保证两边统计口径一致
from report_stock_overall import (
    get_target_date,
    get_index_stats,
    get_future_delta,
    get_index_data,
    get_future_data,
    get_rzrq_data,
    get_stock_daily_data,
)

PAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pages')
OUTPUT_PATH = os.path.join(PAGE_DIR, 'market_overall_data.json')

YI = 100000000  # 元 → 亿元


def _fmt_date(d):
    """YYYYMMDD → YYYY-MM-DD。"""
    s = str(d)
    return f'{s[0:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 else s


def build_basis_series(index_df, future_df, index_code, future_prefix):
    """计算单个股指的期现差序列（口径同 plot_basis：主力合约取每日最后一条）。"""
    idx = index_df[index_df['ts_code'] == index_code].copy()
    fut = future_df[future_df['ts_code'].str.startswith(future_prefix)].copy()
    if idx.empty or fut.empty:
        return []
    fut = fut.sort_values('trade_date').groupby('trade_date').last().reset_index()
    merged = pd.merge(idx, fut, on='trade_date', suffixes=('_index', '_future'))
    merged['basis'] = merged['close_future'] - merged['close_index']
    return [[_fmt_date(r['trade_date']), round(float(r['basis']), 2)]
            for _, r in merged.sort_values('trade_date').iterrows()]


def build_amplitude_table(index_df):
    """股指近30/20/10/5日最大震幅表，口径同 plot_index_table。"""
    indices = {
        '000001.SH': '上证指数',
        '000300.SH': '沪深300',
        '000905.SH': '中证500',
        '000852.SH': '中证1000',
    }
    rows = []
    for ts_code, name in indices.items():
        d = index_df[index_df['ts_code'] == ts_code].sort_values('trade_date').reset_index(drop=True)
        row = {'name': name, 'p30': None, 'p20': None, 'p10': None, 'p5': None}
        if not d.empty:
            today_low = float(d.iloc[-1]['low'])

            def amp(prev_n):
                if len(d) >= prev_n + 1:
                    high_max = float(d.iloc[-prev_n - 1:-1]['high'].max())
                    return round((today_low - high_max) / today_low * 100, 2)
                return None

            row.update(p30=amp(30), p20=amp(20), p10=amp(10), p5=amp(5))
        rows.append(row)
    return rows


def build_rzrq_series(rzrq_df):
    """融资/融券三组序列，金额单位转换为亿元，口径同 plot_rzrq_*。"""
    df = rzrq_df.copy()
    g = df.groupby('trade_date').agg({
        'rzye': 'sum', 'rzmre': 'sum', 'rzche': 'sum',
        'rqye': 'sum', 'rqmcl': 'sum', 'rqyl': 'sum',
    }).reset_index().sort_values('trade_date')

    # MySQL DECIMAL 字段返回 Decimal，需显式 float 转换后才能 JSON 序列化
    yi = lambda col: [round(float(v) / YI, 2) for v in g[col]]
    return {
        'dates': [_fmt_date(d) for d in g['trade_date']],
        'rzye': yi('rzye'),
        'rzmre': yi('rzmre'),
        'rzche': yi('rzche'),
        'rqye': yi('rqye'),
        'rqmcl': [round(float(v), 0) for v in g['rqmcl']],
        'rqyl': [round(float(v), 0) for v in g['rqyl']],
    }


def build_extreme_series(stock_df):
    """每日涨幅>8%/跌幅<-8% 股票数量，口径同 plot_stock_extreme_moves。"""
    df = stock_df.sort_values(['ts_code', 'trade_date']).copy()
    df['prev_close'] = df.groupby('ts_code')['close'].shift(1)
    df['pct_change'] = (df['close'] - df['prev_close']) / df['prev_close'] * 100
    stats = df.groupby('trade_date').apply(
        lambda x: pd.Series({
            'up': int((x['pct_change'] > 8).sum()),
            'down': int((x['pct_change'] < -8).sum()),
        })
    ).reset_index().sort_values('trade_date')
    return {
        'dates': [_fmt_date(d) for d in stats['trade_date']],
        'up': [int(v) for v in stats['up']],
        'down': [int(v) for v in stats['down']],
    }


def collect_data(conn):
    """汇总页面所需全部数据（字典，后续 JSON 序列化嵌入页面）。"""
    index_stats = get_index_stats(conn)
    future_delta = get_future_delta(conn)
    index_df = get_index_data(conn, days=90)
    future_df = get_future_data(conn, days=90)
    rzrq_df = get_rzrq_data(conn, days=400)
    stock_df = get_stock_daily_data(conn, days=30)

    return {
        'target_date': get_target_date(),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'index_stats': [
            {
                'name': s['name'],
                'latest_close': round(s['latest_close'], 2),
                'avg_close': round(s['avg_close'], 2),
                'deviation': round(s['deviation'], 2),
            } for s in index_stats
        ],
        'future_delta': (round(future_delta['delta'], 2) if future_delta else None),
        'future_delta_date': (_fmt_date(future_delta['trade_date']) if future_delta else None),
        'basis': {
            'IF': build_basis_series(index_df, future_df, '000300.SH', 'IF'),
            'IC': build_basis_series(index_df, future_df, '000905.SH', 'IC'),
            'IM': build_basis_series(index_df, future_df, '000852.SH', 'IM'),
        },
        'amplitude': build_amplitude_table(index_df),
        'rzrq': build_rzrq_series(rzrq_df),
        'extreme': build_extreme_series(stock_df),
    }


def write_json(data):
    """将计算结果写入 JSON 数据文件，供 app.py /api/market_data 接口读取。"""
    os.makedirs(PAGE_DIR, exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return OUTPUT_PATH


def main():
    print('=' * 80)
    print('大盘风险监测 HTML 报告生成任务')
    print('=' * 80)

    print('\n🔌 连接数据库...')
    conn = get_mysql_connection()
    if not conn:
        print('❌ 数据库连接失败')
        return
    print('✅ 数据库连接成功')

    print('\n📊 汇总指数/期现差/融资融券/个股数据...')
    data = collect_data(conn)
    close_connection(conn)
    print(f"✅ 报告日期 {data['target_date']}；"
          f"期现差序列 IF/IC/IM = {len(data['basis']['IF'])}/"
          f"{len(data['basis']['IC'])}/{len(data['basis']['IM'])} 个交易日；"
          f"融资融券 {len(data['rzrq']['dates'])} 日；极端涨跌 {len(data['extreme']['dates'])} 日")

    path = write_json(data)
    print(f'\n✅ 数据文件已生成: {path}')
    print('   访问地址: http://127.0.0.1:5000/market （页面自动拉取 /api/market_data）')
    print('\n' + '=' * 80)
    print('🎉 大盘风险监测数据生成完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()
