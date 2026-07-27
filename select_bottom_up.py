#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
import shutil
import math
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
    folder_name = f"底部反转{target_date}"
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


def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    start_date = '20250101'
    target_date = get_target_date()

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.open,
        d.amount,
        d.ma5,
        d.ma30,
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
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)


def check_price_range(records):
    recent_200 = records[-200:] if len(records) >= 200 else records
    
    prices = []
    for r in recent_200:
        close = r['close']
        if close is not None:
            prices.append(float(close))
    
    if len(prices) < 2:
        return False
    
    min_price = min(prices)
    max_price = max(prices)
    
    if max_price == 0:
        return False
    
    ratio = min_price / max_price
    return ratio <= 0.5


def calculate_angle(records):
    n = len(records)
    if n < 5:
        return None, None, None
    
    T_minus_4 = records[-5]
    T = records[-1]
    
    ma5_T_4 = float(T_minus_4['ma5']) if T_minus_4['ma5'] else None
    ma5_T = float(T['ma5']) if T['ma5'] else None
    ma30_T_4 = float(T_minus_4['ma30']) if T_minus_4['ma30'] else None
    ma30_T = float(T['ma30']) if T['ma30'] else None
    
    if ma5_T_4 is None or ma5_T is None or ma30_T_4 is None or ma30_T is None:
        return None, None, None
    
    a1 = (ma5_T - ma5_T_4) / 4
    b1 = (ma30_T - ma30_T_4) / 4
    
    denominator = math.sqrt((a1**2 + 1) * (b1**2 + 1))
    if denominator == 0:
        return a1, b1, None
    
    cos_x = (a1 * b1 + 1) / denominator
    cos_x = max(-1, min(1, cos_x))
    x = math.acos(cos_x) * 180 / math.pi
    
    return a1, b1, x


def check_yang_line(records):
    recent_10 = records[-10:] if len(records) >= 10 else records
    
    yang_count = 0
    yang_amount = 0
    yin_amount = 0
    
    for i in range(len(recent_10)):
        r = recent_10[i]
        open_p = float(r['open']) if r['open'] else None
        close_p = float(r['close']) if r['close'] else None
        amount = float(r['amount']) if r['amount'] else 0
        
        if open_p is not None and close_p is not None:
            if close_p > open_p:
                yang_count += 1
                yang_amount += amount
            elif close_p < open_p:
                yin_amount += amount
    
    if yang_count < 5:
        return False, yang_count, yang_amount, yin_amount
    
    if yin_amount == 0:
        return True, yang_count, yang_amount, yin_amount
    
    ratio = yang_amount / yin_amount
    return ratio > 1.5, yang_count, yang_amount, yin_amount


def check_gain(records):
    recent_10 = records[-10:] if len(records) >= 10 else records
    
    for i in range(1, len(recent_10)):
        prev_close = float(recent_10[i-1]['close']) if recent_10[i-1]['close'] else None
        curr_close = float(recent_10[i]['close']) if recent_10[i]['close'] else None
        
        if prev_close is not None and curr_close is not None and prev_close > 0:
            gain = (curr_close - prev_close) / prev_close * 100
            if gain > 6:
                return True, gain
    
    return False, 0


def analyze_stocks(data):
    stock_data = {}
    
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] else None,
            'open': float(record['open']) if record['open'] else None,
            'amount': float(record['amount']) if record['amount'] else None,
            'ma5': float(record['ma5']) if record['ma5'] else None,
            'ma30': float(record['ma30']) if record['ma30'] else None,
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })
    
    result = []
    count_total = 0
    count_mv = 0
    count_price_range = 0
    count_angle = 0
    count_yang = 0
    count_gain = 0
    
    for ts_code, records in stock_data.items():
        if len(records) < 220:
            continue
        
        records.sort(key=lambda x: x['trade_date'])
        
        latest = records[-1]
        stock_name = latest['name']
        
        if 'ST' in stock_name:
            continue
        
        if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH')):
            continue
        
        total_mv = latest['total_mv']
        if total_mv < 8000000000:
            continue
        
        count_total += 1
        count_mv += 1
        
        if not check_price_range(records):
            continue
        
        count_price_range += 1
        
        a1, b1, x = calculate_angle(records)
        if a1 is None or b1 is None or x is None:
            continue
        
        if a1 < 0 or b1 < 0 or x < 0:
            continue
        
        count_angle += 1
        
        yang_result, yang_count, yang_amount, yin_amount = check_yang_line(records)
        if not yang_result:
            continue
        
        count_yang += 1
        
        gain_result, max_gain = check_gain(records)
        if not gain_result:
            continue
        
        count_gain += 1
        
        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv,
            'a1': a1,
            'b1': b1,
            'angle': x,
            'yang_count': yang_count,
            'yang_amount': yang_amount,
            'yin_amount': yin_amount,
            'max_gain': max_gain
        })
    
    result.sort(key=lambda x: x['total_mv'], reverse=True)
    
    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件a(市值>80亿): {count_mv}")
    print(f"满足条件a+b(最低/最高<50%): {count_price_range}")
    print(f"满足条件a+b+c(a1,b1,x>0): {count_angle}")
    print(f"满足条件a+b+c+d(阳线>5且成交额>1.5): {count_yang}")
    print(f"满足条件a+b+c+d+e(涨幅>6%): {count_gain}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)
    
    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "底部企稳.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['name']])
    
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("📈 底部企稳选股策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  a. 总市值 > 80亿")
    print("  b. 最近200日最低/最高收盘价 <= 50%")
    print("  c. T-4至T日，ma5斜率a1>0, ma30斜率b1>0, 夹角x>0")
    print("  d. 最近10日至少5个阳线，阳线成交额/阴线成交额>1.5")
    print("  e. 最近10日至少1日涨幅>6%")
    print("=" * 80)
    
    folder_path = create_folder()
    
    data = read_stock_data()
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return
    
    stocks = analyze_stocks(data)
    
    if stocks:
        generate_csv_file(stocks, folder_path)
        
        print("\n" + "=" * 80)
        print("🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {folder_path}/底部企稳.csv")
        print("=" * 80)
        
        print("\n🔥 精选股票：")
        for i, stock in enumerate(stocks[:10], 1):
            mv_billion = stock['total_mv'] / 100000000
            yang_ratio = stock['yang_amount'] / stock['yin_amount'] if stock['yin_amount'] > 0 else float('inf')
            print(f"{i}. {stock['ts_code']} {stock['name']}")
            print(f"   └─ 市值: {mv_billion:.2f}亿")
            print(f"   └─ a1: {stock['a1']:.4f}, b1: {stock['b1']:.4f}, 夹角: {stock['angle']:.2f}°")
            print(f"   └─ 阳线: {stock['yang_count']}/10, 成交额比: {yang_ratio:.2f}")
            print(f"   └─ 最大涨幅: {stock['max_gain']:.2f}%")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()