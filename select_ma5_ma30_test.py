#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import math
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import pandas as pd

plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return None

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        i.stock_name
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.ts_code = '300260.SZ' AND d.trade_date <= '20260513'
    ORDER BY d.trade_date DESC
    LIMIT 80
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql)
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return None
    finally:
        close_connection(connection)

def calculate_angle(line1_start, line1_end, line2_start, line2_end):
    x1, y1 = line1_start
    x2, y2 = line1_end
    x3, y3 = line2_start
    x4, y4 = line2_end

    vector1 = (x2 - x1, y2 - y1)
    vector2 = (x4 - x3, y4 - y3)

    dot_product = vector1[0] * vector2[0] + vector1[1] * vector2[1]
    magnitude1 = math.sqrt(vector1[0] ** 2 + vector1[1] ** 2)
    magnitude2 = math.sqrt(vector2[0] ** 2 + vector2[1] ** 2)

    if magnitude1 == 0 or magnitude2 == 0:
        return 0

    cos_theta = dot_product / (magnitude1 * magnitude2)
    cos_theta = max(min(cos_theta, 1.0), -1.0)
    angle = math.acos(cos_theta) * (180 / math.pi)

    return angle

def main():
    print("=" * 80)
    print("MA5/MA30角度计算分析")
    print("股票: 300260.SZ")
    print("=" * 80)

    data = read_stock_data()
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    df = pd.DataFrame(data)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values('trade_date').reset_index(drop=True)

    if len(df) < 35:
        print(f"❌ 数据不足，需要至少35个交易日，当前只有{len(df)}条")
        return

    close_prices = df['close'].values
    trade_dates = df['trade_date'].values

    ma5_list = []
    ma30_list = []

    for i in range(len(close_prices)):
        if i >= 4:
            ma5 = close_prices[i-4:i+1].mean()
            ma5_list.append(ma5)
        else:
            ma5_list.append(None)

        if i >= 29:
            ma30 = close_prices[i-29:i+1].mean()
            ma30_list.append(ma30)
        else:
            ma30_list.append(None)

    valid_start = 29
    valid_ma5 = ma5_list[valid_start:]
    valid_ma30 = ma30_list[valid_start:]
    valid_dates = trade_dates[valid_start:]

    if len(valid_ma5) < 10:
        print(f"❌ 有效数据不足，需要至少10个交易日，当前只有{len(valid_ma5)}条")
        return

    recent_ma5 = valid_ma5[-10:]
    recent_ma30 = valid_ma30[-10:]
    recent_dates = valid_dates[-10:]

    print("\n📊 最近10个交易日的MA5和MA30:")
    for i in range(len(recent_dates)):
        date_str = pd.to_datetime(recent_dates[i]).strftime('%Y-%m-%d')
        print(f"  {date_str}: MA5={recent_ma5[i]:.4f}, MA30={recent_ma30[i]:.4f}")

    a1_start = (9, recent_ma5[9])
    a1_end = (5, recent_ma5[5])
    a2_start = (9, recent_ma30[9])
    a2_end = (5, recent_ma30[5])

    b1_start = (4, recent_ma5[4])
    b1_end = (0, recent_ma5[0])
    b2_start = (4, recent_ma30[4])
    b2_end = (0, recent_ma30[0])

    x1 = calculate_angle(a1_start, a1_end, a2_start, a2_end)
    x2 = calculate_angle(b1_start, b1_end, b2_start, b2_end)

    print(f"\n📐 角度计算结果:")
    print(f"  x1 (第10日-第6日 MA5与MA30夹角): {x1:.2f}°")
    print(f"  x2 (第5日-第1日 MA5与MA30夹角): {x2:.2f}°")

    fig, ax = plt.subplots(figsize=(14, 8))

    x_indices = list(range(len(recent_dates)))
    ax.plot(x_indices, recent_ma5, label='MA5', color='red', linewidth=2)
    ax.plot(x_indices, recent_ma30, label='MA30', color='blue', linewidth=2)

    ax.plot([a1_start[0], a1_end[0]], [a1_start[1], a1_end[1]], 
            label='a1 (MA5: 第10日→第6日)', color='green', linestyle='--', linewidth=2, marker='o')
    ax.plot([a2_start[0], a2_end[0]], [a2_start[1], a2_end[1]], 
            label='a2 (MA30: 第10日→第6日)', color='orange', linestyle='--', linewidth=2, marker='s')

    ax.plot([b1_start[0], b1_end[0]], [b1_start[1], b1_end[1]], 
            label='b1 (MA5: 第5日→第1日)', color='cyan', linestyle='-.', linewidth=2, marker='^')
    ax.plot([b2_start[0], b2_end[0]], [b2_start[1], b2_end[1]], 
            label='b2 (MA30: 第5日→第1日)', color='magenta', linestyle='-.', linewidth=2, marker='v')

    ax.annotate(f'x1={x1:.1f}°', xy=(7, (recent_ma5[7] + recent_ma30[7])/2), 
                xytext=(7.2, (recent_ma5[7] + recent_ma30[7])/2 + 0.1),
                arrowprops=dict(arrowstyle='->', color='green'))
    
    ax.annotate(f'x2={x2:.1f}°', xy=(2, (recent_ma5[2] + recent_ma30[2])/2), 
                xytext=(2.2, (recent_ma5[2] + recent_ma30[2])/2 + 0.1),
                arrowprops=dict(arrowstyle='->', color='cyan'))

    ax.set_xticks(x_indices)
    ax.set_xticklabels([pd.to_datetime(d).strftime('%m-%d') for d in recent_dates], rotation=45)
    ax.set_xlabel('日期')
    ax.set_ylabel('价格')
    start_date = pd.to_datetime(recent_dates[0]).strftime('%Y-%m-%d')
    end_date = pd.to_datetime(recent_dates[-1]).strftime('%Y-%m-%d')
    ax.set_title(f'MA5/MA30角度分析 - 300260.SZ ({start_date} ~ {end_date})')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, 'ship.png')
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"\n✅ 图表已保存: {output_path}")

    plt.show()

if __name__ == "__main__":
    main()