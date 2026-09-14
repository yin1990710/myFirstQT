#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
大盘风险监测 HTML 报告生成任务 (report_market_monitor_html.py)

参照 report_stock_overall.py（matplotlib 版大盘趋势报告）的统计口径，
生成可交互的 HTML 页面 pages/market_monitor.html，由 Flask /market 路由提供访问。

页面内容与 PNG 版报告一致：
  1. 指数统计表：三大指数期现差边际变化均值、上证指数/中证全指最新收盘/30日均价/偏离度；
  2. 期现差走势：沪深300(IF)、中证500(IC)、中证1000(IM)；
  3. 股指近期最大震幅统计表：上证/沪深300/中证500/中证1000 近30/20/10/5 日；
  4. 融资额走势（融资余额/融资买入额/融资偿还额，亿元）；
  5. 融券额走势（融券余额，亿元）；
  6. 融券量走势（融券卖出量/融券余量）；
  7. 每日涨幅>8%/跌幅<-8% 股票数量走势。

用法：
  .venv/bin/python report_market_monitor_html.py
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
OUTPUT_PATH = os.path.join(PAGE_DIR, 'market_monitor.html')

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


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<link rel="icon" type="image/png" href="/static/favicon.png">
<link rel="alternate icon" type="image/x-icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>大盘风险监测 - 力矩量化选股平台</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #f5f7fa; color: #1f2933; }
  /* 与首页一致的导航栏 */
  .navbar { position: sticky; top: 0; z-index: 100; display: flex; align-items: center;
            gap: 16px; padding: 0 20px; height: 56px; background: #1a73e8;
            box-shadow: 0 2px 6px rgba(0,0,0,.15); }
  .navbar .brand { display: flex; align-items: center; gap: 10px; margin-right: 8px; text-decoration: none; }
  .navbar .brand img { height: 40px; width: auto; border-radius: 7px;
                       box-shadow: 0 1px 4px rgba(0,0,0,.3); background: #0b2545; }
  .navbar .brand .brand-text { color: #fff; font-size: 17px; font-weight: 600; white-space: nowrap; }
  .navbar nav { display: flex; gap: 0; height: 100%; }
  .navbar nav a { display: flex; align-items: center; padding: 0 12px; height: 100%;
                  white-space: nowrap;
                  color: rgba(255,255,255,.88); font-size: 14px; text-decoration: none; }
  .navbar nav a:hover { background: #1558b0; color: #fff; }
  .navbar nav a.active { background: #1558b0; color: #fff; font-weight: 600; }

  .wrap { max-width: 1200px; margin: 24px auto 48px; padding: 0 24px; }
  .page-head { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
  .page-head h1 { font-size: 22px; margin: 0; }
  .page-head .meta { color: #6b7684; font-size: 13px; }

  .card { background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(16,42,72,.08);
          padding: 18px 20px; margin-top: 18px; }
  .card h2 { font-size: 16px; margin: 0 0 14px; padding-left: 10px;
             border-left: 4px solid #1a73e8; line-height: 1.2; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { padding: 10px 8px; text-align: center; border-bottom: 1px solid #edf0f4; }
  th { background: #f0f5ff; color: #1a4480; font-weight: 600; }
  tbody tr:hover { background: #f8fafd; }
  td.name, th.name { text-align: left; padding-left: 18px; font-weight: 500; }
  .up { color: #d83a3a; font-weight: 600; }
  .down { color: #159947; font-weight: 600; }
  .risk-deep { color: #d83a3a; font-weight: 700; }
  .risk-mid { color: #e8820c; font-weight: 600; }
  .dash { color: #b4bdc8; }

  .grid2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; margin-top: 18px; }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
  .chart { width: 100%; height: 340px; }
  .chart-note { color: #8a94a3; font-size: 12px; margin: 4px 0 0; }
  #chartErr { display: none; color: #d83a3a; background: #fdecec; padding: 10px 14px;
              border-radius: 8px; margin-top: 18px; }
</style>
</head>
<body>

<header class="navbar">
  <a class="brand" href="/" title="力矩量化选股平台">
    <img src="/static/logo.png" alt="力矩量化选股平台 logo">
    <span class="brand-text">力矩量化选股平台</span>
  </a>
  <nav>
    <a href="/#strategies">选股策略</a>
    <a href="/backtest">回测结果查询</a>
    <a href="/cron">任务运行监控</a>
    <a href="/market" class="active">大盘风险监控</a>
    <a href="/#about">关于我们</a>
  </nav>
</header>

<main class="wrap">
  <div class="page-head">
    <h1>大盘风险监测</h1>
    <div class="meta">报告日期：<b id="targetDate"></b>　｜　页面生成时间：<b id="generatedAt"></b></div>
  </div>

  <section class="card">
    <h2>指数统计</h2>
    <table>
      <thead><tr>
        <th class="name">指标名称</th><th>数值</th><th>近30日平均收盘价</th><th>偏离度</th>
      </tr></thead>
      <tbody id="statsBody"></tbody>
    </table>
    <p class="chart-note">偏离度 =（最新收盘价 − 近30日平均收盘价）/ 最新收盘价 ×100%；正值（红）表示高于均值，负值（绿）表示低于均值。</p>
  </section>

  <section class="card">
    <h2>股指近期最大震幅统计</h2>
    <table>
      <thead><tr>
        <th class="name">指数名称</th><th>近30天最大震幅</th><th>近20天最大震幅</th>
        <th>近10天最大震幅</th><th>近5天最大震幅</th>
      </tr></thead>
      <tbody id="ampBody"></tbody>
    </table>
    <p class="chart-note">最大震幅 =（当日最低价 − 前N日最高价）/ 当日最低价 ×100%；数值越负代表回撤风险越大（&le;−10% 深红，&le;−5% 橙色）。</p>
  </section>

  <div class="grid2">
    <section class="card"><h2>沪深300 期现差走势（IF）</h2><div id="chartIF" class="chart"></div></section>
    <section class="card"><h2>中证500 期现差走势（IC）</h2><div id="chartIC" class="chart"></div></section>
  </div>
  <div class="grid2">
    <section class="card"><h2>中证1000 期现差走势（IM）</h2><div id="chartIM" class="chart"></div></section>
    <section class="card"><h2>融资额走势（亿元）</h2><div id="chartRz" class="chart"></div></section>
  </div>
  <div class="grid2">
    <section class="card"><h2>融券额走势（亿元）</h2><div id="chartRqYe" class="chart"></div></section>
    <section class="card"><h2>融券量走势</h2><div id="chartRqVol" class="chart"></div></section>
  </div>
  <section class="card">
    <h2>每日涨跌超 8% 股票数量走势</h2>
    <div id="chartExtreme" class="chart" style="height:380px"></div>
    <p class="chart-note">红线：当日涨幅 &gt; 8% 家数；绿线：当日跌幅 &lt; −8% 家数（红涨绿跌）。</p>
  </section>

  <div id="chartErr">图表库(echarts)加载失败，请检查网络后刷新。</div>
</main>

<script id="reportData" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('reportData').textContent);
document.getElementById('targetDate').textContent = D.target_date;
document.getElementById('generatedAt').textContent = D.generated_at;

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function dash(v) { return (v === null || v === undefined || v === '') ? '<span class="dash">-</span>' : v; }
function signed(v, suffix) {
  if (v === null || v === undefined) return '<span class="dash">-</span>';
  const cls = v > 0 ? 'up' : (v < 0 ? 'down' : '');
  const txt = (v > 0 ? '+' : '') + v.toFixed(2) + suffix;
  return cls ? '<span class="' + cls + '">' + txt + '</span>' : txt;
}

/* ---- 指数统计表 ---- */
let rows = [];
if (D.future_delta !== null) {
  rows.push('<tr><td class="name">三大指数期现差边际变化均值（' + esc(D.future_delta_date || '') + '）</td>'
    + '<td colspan="3">' + signed(D.future_delta, '') + '</td></tr>');
}
D.index_stats.forEach(s => {
  rows.push('<tr><td class="name">' + esc(s.name) + '</td><td>' + s.latest_close.toFixed(2)
    + '</td><td>' + s.avg_close.toFixed(2) + '</td><td>' + signed(s.deviation, '%') + '</td></tr>');
});
document.getElementById('statsBody').innerHTML = rows.join('');

/* ---- 最大震幅表 ---- */
function ampCell(v) {
  if (v === null || v === undefined) return '<span class="dash">-</span>';
  let cls = '';
  if (v <= -10) cls = 'risk-deep';
  else if (v <= -5) cls = 'risk-mid';
  else if (v < 0) cls = 'down';
  return '<span class="' + cls + '">' + v.toFixed(2) + '%</span>';
}
document.getElementById('ampBody').innerHTML = D.amplitude.map(r =>
  '<tr><td class="name">' + esc(r.name) + '</td><td>' + ampCell(r.p30)
  + '</td><td>' + ampCell(r.p20) + '</td><td>' + ampCell(r.p10)
  + '</td><td>' + ampCell(r.p5) + '</td></tr>').join('');

/* ---- ECharts ---- */
if (typeof echarts === 'undefined') {
  document.getElementById('chartErr').style.display = 'block';
} else {
  const RED = '#d83a3a', GREEN = '#159947', BLUE = '#1a73e8';
  function baseOpt(dates, vals, color) {
    return {
      tooltip: { trigger: 'axis', valueFormatter: v => (v == null ? '-' : v) },
      grid: { left: 60, right: 24, top: 28, bottom: 58 },
      xAxis: { type: 'category', data: dates, boundaryGap: false,
               axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', scale: true },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 18 }],
      series: [{
        type: 'line', data: vals, showSymbol: false, lineStyle: { width: 2, color: color },
        markLine: { silent: true, symbol: 'none',
          data: [{ yAxis: 0 }], lineStyle: { color: RED, type: 'dashed' },
          label: { formatter: '0', color: RED } }
      }]
    };
  }
  function renderLine(id, pair, colors) {
    const dates = pair.dates;
    const series = pair.series.map((s, i) => ({
      name: s.name, type: 'line', data: s.data, showSymbol: false,
      lineStyle: { width: 2, color: colors[i] }
    }));
    echarts.init(document.getElementById(id)).setOption({
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { fontSize: 11 } },
      grid: { left: 66, right: 24, top: 34, bottom: 58 },
      xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', scale: true, name: pair.unit || '' },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 18 }],
      series: series
    });
  }
  const noData = id => echarts.init(document.getElementById(id)).setOption({
    title: { text: '暂无数据', left: 'center', top: 'center',
             textStyle: { color: '#9aa3af', fontSize: 14, fontWeight: 'normal' } }
  });

  [['chartIF','IF',BLUE], ['chartIC','IC',BLUE], ['chartIM','IM',BLUE]].forEach(([id, key, color]) => {
    const arr = D.basis[key];
    if (!arr.length) { noData(id); return; }
    echarts.init(document.getElementById(id)).setOption(
      baseOpt(arr.map(x => x[0]), arr.map(x => x[1]), color));
  });

  renderLine('chartRz', { unit: '亿元', dates: D.rzrq.dates, series: [
    { name: '融资余额', data: D.rzrq.rzye },
    { name: '融资买入额', data: D.rzrq.rzmre },
    { name: '融资偿还额', data: D.rzrq.rzche }
  ]}, [BLUE, RED, '#e8820c']);

  renderLine('chartRqYe', { unit: '亿元', dates: D.rzrq.dates, series: [
    { name: '融券余额', data: D.rzrq.rqye }
  ]}, [RED]);

  renderLine('chartRqVol', { dates: D.rzrq.dates, series: [
    { name: '融券卖出量', data: D.rzrq.rqmcl },
    { name: '融券余量', data: D.rzrq.rqyl }
  ]}, [BLUE, RED]);

  renderLine('chartExtreme', { unit: '家', dates: D.extreme.dates, series: [
    { name: '涨幅>8%', data: D.extreme.up },
    { name: '跌幅<-8%', data: D.extreme.down }
  ]}, [RED, GREEN]);

  window.addEventListener('resize', () => {
    ['chartIF','chartIC','chartIM','chartRz','chartRqYe','chartRqVol','chartExtreme']
      .forEach(id => echarts.getInstanceByDom(document.getElementById(id))?.resize());
  });
}
</script>
</body>
</html>
"""


def render_html(data):
    """把数据 JSON 嵌入模板并写出 HTML 文件。"""
    os.makedirs(PAGE_DIR, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    html = HTML_TEMPLATE.replace('__DATA__', payload)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
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

    path = render_html(data)
    print(f'\n✅ HTML 报告已生成: {path}')
    print('   访问地址: http://127.0.0.1:5000/market')
    print('\n' + '=' * 80)
    print('🎉 大盘风险监测 HTML 报告生成完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()
