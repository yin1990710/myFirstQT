#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shutil
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    now = datetime.now()
    current_hour = now.hour
    if current_hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date


def get_folder_name():
    target_date = get_target_date()
    folder_name = f"二浪启动{target_date}"
    return folder_name


def create_folder():
    folder_name = get_folder_name()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)

    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")

    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")

    return folder_path


def read_stock_data(days=120):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.high,
        d.low,
        d.amount,
        d.pct_chg,
        i.stock_name,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date >= %s AND d.trade_date <= %s
    ORDER BY d.ts_code, d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)


def calculate_ma(close_prices, period=30):
    if len(close_prices) < period:
        return None
    return sum(close_prices[-period:]) / period


def analyze_stocks(data):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'open': float(record['open'] or 0),
            'close': float(record['close'] or 0),
            'high': float(record['high'] or 0),
            'low': float(record['low'] or 0),
            'amount': float(record['amount'] or 0),
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_condition1 = 0
    count_condition2 = 0
    count_condition3 = 0
    count_condition4 = 0

    for ts_code, records in stock_data.items():
        if len(records) < 60:
            continue

        records.sort(key=lambda x: x['trade_date'])

        count_total += 1

        close_prices = [r['close'] for r in records]
        amount_list = [r['amount'] for r in records]

        recent_60_days = records[-60:]
        close_60 = [r['close'] for r in recent_60_days]
        amount_60 = [r['amount'] for r in recent_60_days]

        min_price = min(close_60)
        max_price = max(close_60)
        max_index = close_60.index(max_price)

        first_wave = recent_60_days[:max_index + 1]
        adjustment = recent_60_days[max_index + 1:]

        if len(first_wave) < 10:
            continue

        first_wave_amount = [r['amount'] for r in first_wave]
        first_wave_avg_amount = sum(first_wave_amount) / len(first_wave_amount)

        first_wave_gain = (first_wave[-1]['close'] - first_wave[0]['close']) / first_wave[0]['close'] * 100

        if first_wave_gain < 20:
            continue

        count_condition1 += 1

        if len(adjustment) < 3:
            continue

        adjustment_low = min([r['low'] for r in adjustment])
        adjustment_close = [r['close'] for r in adjustment]
        ma30_list = []
        for i in range(30, len(close_prices) + 1):
            ma30 = calculate_ma(close_prices[:i], 30)
            ma30_list.append(ma30)

        if len(ma30_list) < len(adjustment):
            continue

        adjustment_ma30 = ma30_list[-len(adjustment):]

        below_ma30 = False
        for i in range(len(adjustment)):
            if adjustment[i]['close'] < adjustment_ma30[i] * 0.95:
                below_ma30 = True
                break

        if below_ma30:
            continue

        count_condition2 += 1

        adjustment_avg_amount = sum([r['amount'] for r in adjustment]) / len(adjustment)

        if adjustment_avg_amount > first_wave_avg_amount * 0.85:
            continue

        count_condition3 += 1

        latest = records[-1]
        prev = records[-2]

        today_gain = (latest['close'] - prev['close']) / prev['close'] * 100

        if today_gain < 2:
            continue

        count_condition4 += 1

        if latest['total_mv'] < 300000000:
            continue

        if latest['amount'] < 300000:
            continue

        result.append({
            'ts_code': ts_code,
            'name': latest['name'],
            'close': latest['close'],
            'total_mv': latest['total_mv'],
            'first_wave_gain': first_wave_gain,
            'today_gain': today_gain,
            'amount_ratio': adjustment_avg_amount / first_wave_avg_amount if first_wave_avg_amount > 0 else 0
        })

    result.sort(key=lambda x: x['first_wave_gain'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件1(第一波涨幅>30%): {count_condition1}")
    print(f"满足条件1+2(调整未破30日均线): {count_condition2}")
    print(f"满足条件1+2+3(调整期间成交量萎缩): {count_condition3}")
    print(f"满足条件1+2+3+4(今日启动涨幅>3%): {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    target_date = get_target_date()
    csv_filename = f"二浪启动{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,收盘价,第一波涨幅(%),今日涨幅(%),调整量/上涨量,市值(亿)\n")
        for stock in stocks:
            f.write(f"{stock['ts_code']},{stock['name']},{stock['close']:.2f},")
            f.write(f"{stock['first_wave_gain']:.2f},{stock['today_gain']:.2f},")
            f.write(f"{stock['amount_ratio']:.2f},{stock['total_mv']/10000:.2f}\n")

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("🌊 二浪启动选股策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 第一波上涨：涨幅超过20%")
    print("  2. 调整阶段：未跌破30日均线（允许5%的误差）")
    print("  3. 量能特征：调整期间成交量较上涨阶段萎缩85%以下")
    print("  4. 启动信号：今日涨幅超过2%，成交额超过3亿，市值超过30亿")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data(days=120)

    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    selected_stocks = analyze_stocks(data)

    print(f"\n✅ 共选出 {len(selected_stocks)} 只满足条件的股票")

    if selected_stocks:
        csv_path = generate_csv_file(selected_stocks, folder_path)
        print("\n" + "=" * 80)
        print(f"🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n🔥 精选股票：")
        for i, stock in enumerate(selected_stocks[:20], 1):
            print(f"{i}. {stock['ts_code']} {stock['name']} - 第一波涨{stock['first_wave_gain']:.1f}% 今日涨{stock['today_gain']:.1f}% 市值{stock['total_mv']/10000:.1f}亿")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
