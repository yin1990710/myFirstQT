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
    folder_name = f"步步高{target_date}"
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

def read_stock_data(days=20):
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
        d.turning_point,
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
            'turning_point': record['turning_point'],
            'circ_mv': float(record['circ_mv'] or 0) if record['circ_mv'] else 0
        })

    result = []
    count_total = 0
    count_condition1 = 0
    count_condition2 = 0
    count_condition3 = 0
    count_condition4 = 0
    count_condition5 = 0

    six_hundred_million = 600000000

    for ts_code, records in stock_data.items():
        if len(records) < 14:
            continue

        records.sort(key=lambda x: x['trade_date'])

        last_5_days = records[-5:]
        last_15_days = records[-15:]

        if len(last_5_days) < 5:
            continue

        count_total += 1

        has_wave_valley = False
        for r in last_15_days:
            if r['turning_point'] == '波谷':
                has_wave_valley = True
                break

        if not has_wave_valley:
            continue

        count_condition1 += 1

        open_increasing = True
        for i in range(1, len(last_5_days)):
            if last_5_days[i]['open'] <= last_5_days[i-1]['open']:
                open_increasing = False
                break

        if not open_increasing:
            continue

        count_condition2 += 1

        avg_amount_5day = sum(r['amount'] for r in last_5_days) / 5
        avg_amount_5day_yuan = avg_amount_5day * 1000

        if avg_amount_5day_yuan <= six_hundred_million:
            continue

        count_condition3 += 1

        has_high_turnover = False
        for r in last_5_days:
            circ_mv = r['circ_mv']
            amount = r['amount'] * 1000

            if circ_mv > 0:
                turnover_ratio = (amount / circ_mv) * 100
                if turnover_ratio > 10:
                    has_high_turnover = True
                    break

        if not has_high_turnover:
            continue

        count_condition4 += 1

        closes_5day = [r['close'] for r in last_5_days]
        min_close = min(closes_5day)
        max_close = max(closes_5day)
        if max_close == 0:
            continue
        close_ratio = min_close / max_close
        if close_ratio <= 0.8:
            continue

        count_condition5 += 1

        result.append({
            'ts_code': ts_code,
        })

    result.sort(key=lambda x: x['ts_code'])

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件1(近15天有波谷): {count_condition1}")
    print(f"满足条件1+2(5日开盘逐日提高): {count_condition2}")
    print(f"满足条件1+2+3(5日平均成交额>6亿): {count_condition3}")
    print(f"满足条件1+2+3+4(换手率>10%): {count_condition4}")
    print(f"满足条件1+2+3+4+5(振幅>80%): {len(result)}")
    print("=" * 60)

    return result

def generate_csv_file(stocks, folder_path):
    target_date = get_target_date()
    csv_filename = f"步步高{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    ts_codes = [stock['ts_code'] for stock in stocks]
    ts_codes_str = ','.join(ts_codes)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write(ts_codes_str)

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path

def main():
    print("=" * 80)
    print("步步高选股策略")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data(days=20)

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