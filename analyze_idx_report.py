#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from mysql_connection import get_mysql_connection, close_connection
from datetime import datetime

def calculate_future_spread(conn):
    cursor = conn.cursor()
    query = """
    SELECT
        trade_date,
        '三大期现差算术均值' as idx_name,
        sum(fu_diff)/3 as diff
    FROM 
    (SELECT
        t2.idx_name,
        t2.trade_date,
        t1.idx_fu_close - t2.idx_close as fu_diff
    FROM 
    (SELECT 
        case when ts_code='ICL.CFX' then '中证500' 
             when ts_code='IML.CFX' then '中证1000' 
             when ts_code='IFL.CFX' then '沪深300'  
             else null end as idx_fu_name,  
        close as idx_fu_close, 
        trade_date 
    FROM stock_index_future_daily_t 
    WHERE trade_date >='20241101'
    ) t1 
    JOIN (
        SELECT 
            case when ts_code='000905.SH' then '中证500' 
                 when ts_code='000852.SH' then '中证1000' 
                 when ts_code='000300.SH' then '沪深300'  
                 else null end as idx_name, 
            close as idx_close, 
            trade_date 
        FROM stock_index_daily_t 
        WHERE trade_date >='20241101'
    ) t2 ON t1.trade_date = t2.trade_date AND t1.idx_fu_name = t2.idx_name
    ) t3 
    GROUP BY trade_date
    ORDER BY trade_date
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    return rows

def calculate_csi_return(conn):
    cursor = conn.cursor()
    query = """
    SELECT 
        trade_date,
        '中证全指' as idx_name,
        (close - LAG(close, 1) OVER(ORDER BY trade_date)) / LAG(close, 1) OVER(ORDER BY trade_date) * 100 as diff
    FROM stock_index_daily_t 
    WHERE trade_date >='20241101' AND ts_code='000985.CSI'
    ORDER BY trade_date
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    return rows

def calculate_correlation(future_data, csi_data):
    future_dict = {row['trade_date']: float(row['diff']) for row in future_data}
    csi_dict = {row['trade_date']: float(row['diff']) for row in csi_data if row['diff'] is not None}
    
    common_dates = set(future_dict.keys()) & set(csi_dict.keys())
    if len(common_dates) < 2:
        return 0, 0
    
    dates = sorted(common_dates)
    x = [future_dict[d] for d in dates]
    y = [csi_dict[d] for d in dates]
    
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(a * b for a, b in zip(x, y))
    sum_x2 = sum(a * a for a in x)
    sum_y2 = sum(a * a for a in y)
    
    numerator = n * sum_xy - sum_x * sum_y
    denominator = ((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y)) ** 0.5
    
    if denominator == 0:
        return 0, len(dates)
    
    r = numerator / denominator
    return r, len(dates)

def get_scatter_data(future_data, csi_data):
    future_dict = {row['trade_date']: float(row['diff']) for row in future_data}
    csi_dict = {row['trade_date']: float(row['diff']) for row in csi_data if row['diff'] is not None}
    
    common_dates = sorted(set(future_dict.keys()) & set(csi_dict.keys()))
    return str([[future_dict[d], csi_dict[d]] for d in common_dates])

def get_labels(data):
    return str([row['trade_date'] for row in data])

def get_values(data):
    return str([float(row['diff']) if row['diff'] is not None else None for row in data])

def get_avg_diff(data, days):
    valid = [float(row['diff']) for row in data[-days:] if row['diff'] is not None]
    if not valid:
        return 0
    return sum(valid) / len(valid)

def generate_html(future_data, csi_data, correlation, sample_size):
    r = correlation
    r_squared = r ** 2
    corr_level = "极强正相关" if r >= 0.7 else "强正相关" if r >= 0.5 else "中等正相关" if r >= 0.3 else "弱正相关" if r >= 0.1 else "几乎无相关" if r > -0.1 else "弱负相关" if r >= -0.3 else "中等负相关" if r >= -0.5 else "强负相关" if r >= -0.7 else "极强负相关"
    
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>三大指数期现差与中证全指涨跌幅关系</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }}
        h1 {{ text-align: center; color: #333; margin-bottom: 30px; }}
        .chart-container {{ position: relative; height: 500px; margin-bottom: 30px; }}
        .stats {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 20px; margin-bottom: 30px; }}
        .stat-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat-card .label {{ font-size: 14px; opacity: 0.9; }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; }}
        .stat-card.csi {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }}
        .stat-card.diff {{ background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }}
        .stat-card.correlation {{ background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); }}
        .date-range {{ text-align: center; color: #666; margin-bottom: 20px; }}
        .correlation-box {{ background: #fff3cd; border: 1px solid #ffeeba; border-radius: 8px; padding: 20px; text-align: center; color: #856404; margin-bottom: 30px; }}
        .charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
        .section-title {{ font-size: 18px; font-weight: bold; color: #333; margin-bottom: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 三大指数期现差与中证全指涨跌幅关系</h1>
        <div class="date-range">数据日期：2024年11月1日 ~ {datetime.now().strftime('%Y年%m月%d日')}，样本数：{sample_size} 个交易日</div>
        
        <div class="stats">
            <div class="stat-card diff">
                <div class="label">期现差均值(近30日)</div>
                <div class="value">{get_avg_diff(future_data, 30):.2f}</div>
            </div>
            <div class="stat-card diff">
                <div class="label">期现差均值(近10日)</div>
                <div class="value">{get_avg_diff(future_data, 10):.2f}</div>
            </div>
            <div class="stat-card csi">
                <div class="label">中证全指涨跌幅(近30日)</div>
                <div class="value">{get_avg_diff(csi_data, 30):.2f}%</div>
            </div>
            <div class="stat-card csi">
                <div class="label">中证全指涨跌幅(近10日)</div>
                <div class="value">{get_avg_diff(csi_data, 10):.2f}%</div>
            </div>
            <div class="stat-card correlation">
                <div class="label">皮尔逊相关系数</div>
                <div class="value">{r:.4f}</div>
            </div>
        </div>
        
        <div class="correlation-box">
            <strong>相关分析结果</strong>：皮尔逊相关系数 r = {r:.4f}（{corr_level}），R² = {r_squared:.4f}。
            期现差反映市场对未来的预期，当期现差为正时，说明期货价格高于现货价格，市场预期上涨；反之则预期下跌。中证全指涨跌幅反映市场整体走势。
        </div>
        
        <div class="section-title">📈 趋势图：期现差与中证全指涨跌幅走势</div>
        <div class="chart-container">
            <canvas id="lineChart"></canvas>
        </div>
        
        <div class="section-title">📊 散点图：期现差与中证全指涨跌幅相关性</div>
        <div class="chart-container">
            <canvas id="scatterChart"></canvas>
        </div>
    </div>
    
    <script>
        const lineCtx = document.getElementById('lineChart').getContext('2d');
        const labels = {get_labels(future_data)};
        const futureSpread = {get_values(future_data)};
        const csiReturn = {get_values(csi_data)};
        
        new Chart(lineCtx, {{
            type: 'line',
            data: {{
                labels: labels,
                datasets: [
                    {{
                        label: '三大期现差算术均值',
                        data: futureSpread,
                        borderColor: 'rgba(79, 172, 254, 1)',
                        backgroundColor: 'rgba(79, 172, 254, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        yAxisID: 'y'
                    }},
                    {{
                        label: '中证全指涨跌幅(%)',
                        data: csiReturn,
                        borderColor: 'rgba(245, 87, 108, 1)',
                        backgroundColor: 'rgba(245, 87, 108, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        yAxisID: 'y1'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{ mode: 'index', intersect: false }},
                plugins: {{
                    legend: {{ position: 'top', labels: {{ font: {{ size: 14 }}, padding: 20 }} }},
                    tooltip: {{
                        backgroundColor: 'rgba(0,0,0,0.8)',
                        titleFont: {{ size: 14 }},
                        bodyFont: {{ size: 13 }},
                        padding: 12,
                        callbacks: {{
                            label: function(context) {{
                                let label = context.dataset.label || '';
                                if (label) label += ': ';
                                if (context.parsed.y !== null) label += context.parsed.y.toFixed(2);
                                return label;
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{ title: {{ display: true, text: '日期', font: {{ size: 14 }} }}, ticks: {{ maxRotation: 45, minRotation: 0, font: {{ size: 10 }} }} }},
                    y: {{ type: 'linear', display: true, position: 'left', title: {{ display: true, text: '期现差', font: {{ size: 14 }}, color: 'rgba(79, 172, 254, 1)' }}, grid: {{ color: 'rgba(0,0,0,0.05)' }} }},
                    y1: {{ type: 'linear', display: true, position: 'right', title: {{ display: true, text: '中证全指涨跌幅(%)', font: {{ size: 14 }}, color: 'rgba(245, 87, 108, 1)' }}, grid: {{ drawOnChartArea: false }} }}
                }}
            }}
        }});
        
        const scatterCtx = document.getElementById('scatterChart').getContext('2d');
        const scatterData = {get_scatter_data(future_data, csi_data)};
        
        new Chart(scatterCtx, {{
            type: 'scatter',
            data: {{
                datasets: [{{
                    label: '期现差 vs 中证全指涨跌幅',
                    data: scatterData,
                    backgroundColor: 'rgba(79, 172, 254, 0.6)',
                    borderColor: 'rgba(79, 172, 254, 1)',
                    borderWidth: 1,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        backgroundColor: 'rgba(0,0,0,0.8)',
                        callbacks: {{
                            label: function(context) {{
                                return "期现差: " + context.parsed.x.toFixed(2) + " | 涨跌幅: " + context.parsed.y.toFixed(2) + "%";
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{ title: {{ display: true, text: '三大期现差算术均值', font: {{ size: 14 }} }}, grid: {{ color: 'rgba(0,0,0,0.05)' }} }},
                    y: {{ title: {{ display: true, text: '中证全指涨跌幅(%)', font: {{ size: 14 }} }}, grid: {{ color: 'rgba(0,0,0,0.05)' }} }}
                }}
            }}
        }});
    </script>
</body>
</html>"""
    return html_content

def main():
    conn = get_mysql_connection()
    if not conn:
        print("数据库连接失败")
        sys.exit(1)
    
    try:
        print("📊 正在计算三大指数期现差...")
        future_data = calculate_future_spread(conn)
        print(f"   ✅ 获取到 {len(future_data)} 条期现差数据")
        
        print("📊 正在计算中证全指涨跌幅...")
        csi_data = calculate_csi_return(conn)
        print(f"   ✅ 获取到 {len(csi_data)} 条中证全指数据")
        
        print("📊 正在计算相关系数...")
        correlation, sample_size = calculate_correlation(future_data, csi_data)
        print(f"   ✅ 皮尔逊相关系数 r = {correlation:.4f}")
        
        print("📊 正在生成HTML图表...")
        html_content = generate_html(future_data, csi_data, correlation, sample_size)
        
        output_file = 'analyze_idx_report.html'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"✅ HTML文件已保存: {output_file}")
        
    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()