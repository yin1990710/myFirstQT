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
        close - open as diff
    FROM stock_index_daily_t 
    WHERE trade_date >='20241101' AND ts_code='000985.CSI'
    ORDER BY trade_date
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    return rows

def get_labels(data):
    return str([row['trade_date'] for row in data])

def get_values(data):
    vals = []
    for row in data:
        if row['diff'] is None:
            vals.append('null')
        else:
            vals.append(str(float(row['diff'])))
    return '[' + ', '.join(vals) + ']'

def get_avg_diff(data, days):
    valid = [float(row['diff']) for row in data[-days:] if row['diff'] is not None]
    if not valid:
        return 0
    return sum(valid) / len(valid)

def generate_html(future_data, csi_data):
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
        .chart-container {{ position: relative; height: 600px; margin-bottom: 30px; }}
        .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .stat-card {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat-card .label {{ font-size: 14px; opacity: 0.9; }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; }}
        .stat-card.csi {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }}
        .stat-card.diff {{ background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }}
        .date-range {{ text-align: center; color: #666; margin-bottom: 20px; }}
        .info-box {{ background: #fff3cd; border: 1px solid #ffeeba; border-radius: 8px; padding: 15px; text-align: center; color: #856404; margin-bottom: 30px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 三大指数期现差与中证全指涨跌幅关系</h1>
        <div class="date-range">数据日期：2024年11月1日 ~ {datetime.now().strftime('%Y年%m月%d日')}</div>
        
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
                <div class="value">{get_avg_diff(csi_data, 30):.2f}</div>
            </div>
            <div class="stat-card csi">
                <div class="label">中证全指涨跌幅(近10日)</div>
                <div class="value">{get_avg_diff(csi_data, 10):.2f}</div>
            </div>
        </div>
        
        <div class="info-box">
            <strong>指标说明</strong>：三大期现差算术均值 = (沪深300期现差 + 中证500期现差 + 中证1000期现差) / 3；中证全指涨跌幅 = close - open（当日收盘价 - 开盘价）
        </div>
        
        <div class="chart-container">
            <canvas id="mainChart"></canvas>
        </div>
    </div>
    
    <script>
        const ctx = document.getElementById('mainChart').getContext('2d');
        
        const labels = {get_labels(future_data)};
        const futureSpread = {get_values(future_data)};
        const csiReturn = {get_values(csi_data)};
        
        new Chart(ctx, {{
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
                        tension: 0.4,
                        yAxisID: 'y'
                    }},
                    {{
                        label: '中证全指涨跌幅(close-open)',
                        data: csiReturn,
                        borderColor: 'rgba(245, 87, 108, 1)',
                        backgroundColor: 'rgba(245, 87, 108, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        yAxisID: 'y1'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{
                    mode: 'index',
                    intersect: false,
                }},
                plugins: {{
                    legend: {{
                        position: 'top',
                        labels: {{
                            font: {{ size: 14 }},
                            padding: 20
                        }}
                    }},
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
                    x: {{
                        title: {{ display: true, text: '日期', font: {{ size: 14 }} }},
                        ticks: {{ maxRotation: 45, minRotation: 0, font: {{ size: 10 }} }}
                    }},
                    y: {{
                        type: 'linear',
                        display: true,
                        position: 'left',
                        title: {{ display: true, text: '三大期现差算术均值', font: {{ size: 14 }}, color: 'rgba(79, 172, 254, 1)' }},
                        grid: {{ color: 'rgba(0,0,0,0.05)' }}
                    }},
                    y1: {{
                        type: 'linear',
                        display: true,
                        position: 'right',
                        title: {{ display: true, text: '中证全指涨跌幅(close-open)', font: {{ size: 14 }}, color: 'rgba(245, 87, 108, 1)' }},
                        grid: {{ drawOnChartArea: false }}
                    }}
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
        
        print("📊 正在生成HTML图表...")
        html_content = generate_html(future_data, csi_data)
        
        output_file = 'analyze_idx_report.html'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"✅ HTML文件已保存: {output_file}")
        
    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()