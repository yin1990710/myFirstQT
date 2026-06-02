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
    folder_name = f"低波放量{target_date}"
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

def read_stock_data(days=15):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.amount,
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
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_condition1 = 0
    count_condition2 = 0
    count_condition3 = 0
    count_condition4 = 0
    count_condition5 = 0
    count_condition6 = 0

    five_hundred_million_yuan = 500000000

    for ts_code, records in stock_data.items():
        if len(records) < 10:
            continue

        records.sort(key=lambda x: x['trade_date'])

        last_10_days = records[-10:]

        if len(last_10_days) < 10:
            continue

        count_total += 1

        up_days_amount = []
        down_days_amount = []

        for r in last_10_days:
            if r['open'] < r['close']:
                up_days_amount.append(r['amount'])
            elif r['open'] > r['close']:
                down_days_amount.append(r['amount'])

        if len(up_days_amount) < 4:
            continue

        count_condition1 += 1

        if len(down_days_amount) == 0:
            continue

        avg_up_amount = sum(up_days_amount) / len(up_days_amount)
        avg_down_amount = sum(down_days_amount) / len(down_days_amount)

        if avg_down_amount == 0:
            continue

        if avg_up_amount <= avg_down_amount * 1.3:
            continue

        count_condition2 += 1

        if avg_up_amount * 1000 < five_hundred_million_yuan:
            continue

        count_condition3 += 1

        # 条件4：最近10个交易日至少有2个交易日涨幅超过5%
        gain_over_5_count = 0
        for i in range(1, len(last_10_days)):
            prev_close = last_10_days[i-1]['close']
            curr_close = last_10_days[i]['close']
            if prev_close > 0:
                gain = (curr_close - prev_close) / prev_close * 100
                if gain > 5:
                    gain_over_5_count += 1
        if gain_over_5_count < 2:
            continue

        count_condition4 += 1

        # 条件5：最近10个交易日阴线跌幅不超过-5%
        has_big_drop = False
        for i in range(1, len(last_10_days)):
            prev_close = last_10_days[i-1]['close']
            curr_close = last_10_days[i]['close']
            curr_open = last_10_days[i]['open']
            if curr_close < curr_open:  # 阴线
                if prev_close > 0:
                    drop = (curr_close - prev_close) / prev_close * 100
                    if drop < -5:  # 跌幅超过5%
                        has_big_drop = True
                        break
        if has_big_drop:
            continue

        count_condition5 += 1

        total_mv = last_10_days[-1].get('total_mv', 0)
        if total_mv < five_hundred_million_yuan:
            continue

        count_condition6 += 1

        result.append({
            'ts_code': ts_code,
        })

    result.sort(key=lambda x: x['ts_code'])

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件1(至少4天阳线): {count_condition1}")
    print(f"满足条件1+2(阳线成交额>阴线1.3倍): {count_condition2}")
    print(f"满足条件1+2+3(阳线平均成交额>5亿): {count_condition3}")
    print(f"满足条件1+2+3+4(至少2天涨幅>5%): {count_condition4}")
    print(f"满足条件1+2+3+4+5(阴线跌幅不超过-5%): {count_condition5}")
    print(f"满足条件1+2+3+4+5+6(市值>50亿): {len(result)}")
    print("=" * 60)

    return result

def generate_csv_file(stocks, folder_path):
    target_date = get_target_date()
    csv_filename = f"低波放量{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    ts_codes = [stock['ts_code'] for stock in stocks]
    ts_codes_str = ','.join(ts_codes)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write(ts_codes_str)

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("低波放量选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data(days=15)

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
            print(f"• {stock['ts_code']}")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)

if __name__ == "__main__":
    main()