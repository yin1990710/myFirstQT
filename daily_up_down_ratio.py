#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
计算20260101以来每天下跌股票数/上涨股票数，并生成走势图HTML文件
"""

import os
import sys
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_up_down_data():
    """获取20260101以来每天的上涨/下跌股票数"""
    conn = get_mysql_connection()
    if not conn:
        return None

    query = """
    SELECT 
        trade_date,
        SUM(CASE WHEN close > prev_close THEN 1 ELSE 0 END) AS up_count,
        SUM(CASE WHEN close < prev_close THEN 1 ELSE 0 END) AS down_count
    FROM (
        SELECT 
            trade_date,
            ts_code,
            close,
            LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close
        FROM stock_daily_t
        WHERE trade_date >= '20260101'
    ) AS t
    WHERE prev_close IS NOT NULL
    GROUP BY trade_date
    ORDER BY trade_date
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute(query)
            results = cursor.fetchall()
        print(f"✅ 成功获取 {len(results)} 条数据")
        return results
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return None
    finally:
        close_connection(conn)


def generate_html(data):
    """生成HTML走势图"""
    dates = [str(row['trade_date']) for row in data]
    up_counts = [int(row['up_count']) for row in data]
    down_counts = [int(row['down_count']) for row in data]
    
    ratios = []
    for up, down in zip(up_counts, down_counts):
        if up == 0:
            ratios.append(None)
        else:
            ratios.append(round(down / up, 4))

    max_ratio = max(r for r in ratios if r is not None) if ratios else 1

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>2026年以来每日下跌/上涨比值走势图</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; text-align: center; margin-bottom: 5px; }}
        h2 {{ color: #666; text-align: center; font-size: 14px; margin-bottom: 30px; }}
        .chart-container {{ position: relative; height: 500px; width: 100%; }}
        .stats {{ display: flex; justify-content: space-around; margin-top: 30px; padding: 20px; background: #f8f9fa; border-radius: 8px; }}
        .stat-item {{ text-align: center; }}
        .stat-label {{ color: #666; font-size: 14px; }}
        .stat-value {{ font-size: 24px; font-weight: bold; }}
        .stat-value.up {{ color: #ef5350; }}
        .stat-value.down {{ color: #26a69a; }}
        .stat-value.ratio {{ color: #7e57c2; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>2026年以来每日下跌/上涨比值走势图</h1>
        <h2>数据源: stock_daily_t | 更新时间: {data[-1]['trade_date'] if data else ''}</h2>
        
        <div class="chart-container">
            <canvas id="upDownChart"></canvas>
        </div>
        
        <div class="stats">
            <div class="stat-item">
                <div class="stat-label">最新上涨股票数</div>
                <div class="stat-value up">{up_counts[-1] if up_counts else 0}</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">最新下跌股票数</div>
                <div class="stat-value down">{down_counts[-1] if down_counts else 0}</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">下跌/上涨比值</div>
                <div class="stat-value ratio">{ratios[-1] if ratios and ratios[-1] else 0:.2f}</div>
            </div>
        </div>
    </div>

    <script>
        var ctx = document.getElementById('upDownChart').getContext('2d');
        
        var chart = new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {json.dumps(dates)},
                datasets: [
                    {{
                        label: '下跌/上涨比值',
                        data: {json.dumps(ratios)},
                        borderColor: '#7e57c2',
                        backgroundColor: 'rgba(126, 87, 194, 0.1)',
                        fill: true,
                        tension: 0.4
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
                            font: {{ size: 14 }}
                        }}
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                var label = context.dataset.label || '';
                                if (label) {{
                                    label += ': ';
                                }}
                                if (context.parsed.y !== null) {{
                                    label += context.parsed.y.toFixed(2);
                                }}
                                return label;
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        title: {{
                            display: true,
                            text: '日期',
                            font: {{ size: 14 }}
                        }},
                        ticks: {{
                            maxRotation: 45,
                            minRotation: 45,
                            font: {{ size: 10 }}
                        }}
                    }},
                    y: {{
                        type: 'linear',
                        display: true,
                        position: 'left',
                        title: {{
                            display: true,
                            text: '下跌/上涨比值',
                            font: {{ size: 14 }}
                        }},
                        min: 0,
                        max: {max_ratio * 1.2}
                    }}
                }},
                animation: {{
                    duration: 1500,
                    easing: 'easeOutQuart'
                }}
            }}
        }});
    </script>
</body>
</html>"""

    filename = "daily_up_down_ratio.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✅ HTML文件已生成: {os.path.abspath(filename)}")


def main():
    print("=" * 80)
    print("📊 每日涨跌股票数统计")
    print("=" * 80)

    print("\n🔍 查询20260101以来的涨跌数据...")
    data = get_up_down_data()
    
    if not data:
        print("❌ 没有获取到数据")
        return

    print("\n📈 最近5天数据:")
    print(f"{'日期':<12} {'上涨':<8} {'下跌':<8} {'下跌/上涨'}")
    print("-" * 40)
    for row in data[-5:]:
        ratio = row['down_count'] / row['up_count'] if row['up_count'] > 0 else 0
        print(f"{row['trade_date']:<12} {row['up_count']:<8} {row['down_count']:<8} {ratio:.2f}")

    print("\n🎨 生成走势图HTML...")
    generate_html(data)

    print("\n🎉 完成！")


if __name__ == "__main__":
    main()