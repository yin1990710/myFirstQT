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
    folder_name = f"30天放量低波{target_date}"
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

def read_stock_data(days=45):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 5)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.amount,
        i.stock_name,
        i.total_mv,
        i.circ_mv
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
            'amount': float(record['amount'] or 0),
            'stock_name': record.get('stock_name', ''),
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0,
            'circ_mv': float(record['circ_mv'] or 0) if record['circ_mv'] else 0
        })

    result = []
    count_total = 0
    count_condition0 = 0  # 新增：振幅条件
    count_condition1 = 0
    count_condition2 = 0
    count_condition3 = 0
    count_condition4 = 0
    count_condition5 = 0

    fifty_hundred_million_yuan = 5000000000

    for ts_code, records in stock_data.items():
        if len(records) < 30:
            continue

        records.sort(key=lambda x: x['trade_date'])

        last_30_days = records[-30:]

        if len(last_30_days) < 30:
            continue

        count_total += 1

        # 检查最近30日振幅 (新增条件)
        max_close_30 = max([r['close'] for r in last_30_days])
        min_close_30 = min([r['close'] for r in last_30_days])
        if min_close_30 > 0:
            amplitude_30 = (max_close_30 - min_close_30) / min_close_30 * 100
        else:
            amplitude_30 = 0

        if amplitude_30 <= 0 or amplitude_30 >= 15:
            continue

        count_condition0 += 1  # 振幅条件满足

        up_days = []
        for r in last_30_days:
            if r['open'] < r['close']:
                up_days.append(r)

        if len(up_days) < 7:
            continue

        count_condition1 += 1

        up_days_with_high_ratio = []
        for r in up_days:
            if r['circ_mv'] > 0:
                ratio = (r['amount'] * 1000) / r['circ_mv']
                if ratio > 0.1:
                    up_days_with_high_ratio.append(r)

        if len(up_days_with_high_ratio) < 2:
            continue

        count_condition2 += 1

        close_prices = [r['close'] for r in last_30_days]
        min_close = min(close_prices)
        max_close = max(close_prices)

        if max_close == 0:
            continue

        close_ratio = min_close / max_close

        if close_ratio <= 0.8:
            continue

        count_condition3 += 1

        gain_over_5_count = 0
        for i in range(1, len(last_30_days)):
            prev_close = last_30_days[i-1]['close']
            curr_close = last_30_days[i]['close']
            if prev_close > 0:
                gain = (curr_close - prev_close) / prev_close * 100
                if gain > 5:
                    gain_over_5_count += 1

        if gain_over_5_count < 2:
            continue

        count_condition4 += 1

        total_mv = last_30_days[-1].get('total_mv', 0)
        if total_mv < fifty_hundred_million_yuan:
            continue

        count_condition5 += 1

        result.append({
            'ts_code': ts_code,
            'stock_name': last_30_days[-1].get('stock_name', '')
        })

    result.sort(key=lambda x: x['ts_code'])

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整30天): {count_total}")
    print(f"满足条件0(最近30日振幅0%-15%): {count_condition0}")
    print(f"满足条件0+1(至少7天阳线): {count_condition1}")
    print(f"满足条件0+1+2(阳线中2天成交额/流动市值>10%): {count_condition2}")
    print(f"满足条件0+1+2+3(最低收盘价/最高收盘价>80%): {count_condition3}")
    print(f"满足条件0+1+2+3+4(至少2天涨幅>5%): {count_condition4}")
    print(f"满足条件0+1+2+3+4+5(市值>50亿): {len(result)}")
    print("=" * 60)

    return result

def generate_csv_file(stocks, folder_path):
    target_date = get_target_date()
    csv_filename = f"30天放量低波{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称\n")
        for stock in stocks:
            f.write(f"{stock['ts_code']},{stock['stock_name']}\n")

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("30天放量低波选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data(days=45)

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

        for stock in selected_stocks:
            print(f"• {stock['ts_code']} - {stock['stock_name']}")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()