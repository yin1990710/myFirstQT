#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    now = datetime.now()
    if now.hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date


def get_folder_name():
    return f"2浪趋势{get_target_date()}"


def get_folder_path():
    folder_name = get_folder_name()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=200)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.amount,
        d.ma5,
        d.ma30,
        d.turning_point,
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


def check_close_above_ma5(records):
    """最近2个交易日收盘价均大于ma5，且最近1个交易日成交量>500000"""
    if len(records) < 2:
        return False
    for r in records[-2:]:
        close = float(r['close']) if r['close'] else None
        ma5 = float(r['ma5']) if r['ma5'] else None
        if close is None or ma5 is None:
            return False
        if close <= ma5:
            return False
    latest = records[-1]
    amount = float(latest['amount']) if latest['amount'] else 0
    if amount <= 500000:
        return False
    return True


def check_ma30_increasing(records):
    """最近2个交易日的ma30单调递增"""
    if len(records) < 2:
        return False
    r1 = records[-2]
    r2 = records[-1]
    ma30_1 = float(r1['ma30']) if r1['ma30'] else None
    ma30_2 = float(r2['ma30']) if r2['ma30'] else None
    if ma30_1 is None or ma30_2 is None:
        return False
    return ma30_2 > ma30_1


def find_T_date(records):
    """在最近45个交易日中找到波峰，T日成交量>1000000"""
    recent_30 = records[-45:] if len(records) >= 45 else records
    for i in range(len(recent_30)):
        if recent_30[i]['turning_point'] == '波峰':
            amount = float(recent_30[i]['amount']) if recent_30[i]['amount'] else 0
            if amount > 1000000:
                return {
                    'index': len(records) - len(recent_30) + i,
                    'date': recent_30[i]['trade_date'],
                    'close': recent_30[i]['close'],
                    'amount': recent_30[i]['amount']
                }
    return None


def check_decline_after_T(records, T_index):
    """T日后出现至少5个交易日的turning_point为"下降" """
    n = len(records)
    decline_count = 0
    for i in range(T_index + 1, n):
        if records[i]['turning_point'] == '下降':
            decline_count += 1
            if decline_count >= 5:
                return True
    return False


def check_price_drop(records, T_index):
    """从T日至最近一个交易日，最低收盘价/最高收盘价 < 80%"""
    prices = []
    for i in range(T_index, len(records)):
        close = records[i]['close']
        if close is not None:
            prices.append(float(close))
    if len(prices) < 2:
        return False
    min_price = min(prices)
    max_price = max(prices)
    if max_price == 0:
        return False
    return (min_price / max_price) < 0.8


def analyze_stocks(data):
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] else None,
            'amount': float(record['amount']) if record['amount'] else None,
            'ma5': float(record['ma5']) if record['ma5'] else None,
            'ma30': float(record['ma30']) if record['ma30'] else None,
            'turning_point': record['turning_point'],
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_mv = 0
    count_close_ma5 = 0
    count_T = 0
    count_decline = 0
    count_price = 0
    count_ma30 = 0

    for ts_code, records in stock_data.items():
        if len(records) < 100:
            continue

        records.sort(key=lambda x: x['trade_date'])
        latest = records[-1]
        stock_name = latest['name']

        # 去除非A股
        if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH')):
            continue

        # 去除ST股
        if 'ST' in stock_name:
            continue

        count_total += 1

        # 条件2：总市值 > 100亿
        total_mv = latest['total_mv']
        if total_mv < 10000000000:
            continue
        count_mv += 1

        # 条件3：最近2个交易日收盘价 > ma5
        if not check_close_above_ma5(records):
            continue
        count_close_ma5 += 1

        # 条件4：最近30个交易日内出现波峰
        T_date = find_T_date(records)
        if not T_date:
            continue
        count_T += 1

        # 条件4b：T日后至少5个交易日下降
        if not check_decline_after_T(records, T_date['index']):
            continue
        count_decline += 1

        # 条件4c：T日至最近日，最低/最高收盘价 < 80%
        if not check_price_drop(records, T_date['index']):
            continue
        count_price += 1

        # 条件4d：最近2个交易日的ma30单调递增
        if not check_ma30_increasing(records):
            continue
        count_ma30 += 1

        # 计算价格比例用于输出
        prices = [float(records[i]['close']) for i in range(T_date['index'], len(records)) if records[i]['close']]
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        price_ratio = min_price / max_price if max_price > 0 else 0

        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv,
            'T_date': T_date['date'],
            'T_close': T_date['close'],
            'T_amount': T_date['amount'],
            'price_ratio': price_ratio
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件(市值>100亿): {count_mv}")
    print(f"满足条件(close>ma5): {count_close_ma5}")
    print(f"满足条件(最近30日出现波峰): {count_T}")
    print(f"满足条件(T日后5日下降): {count_decline}")
    print(f"满足条件(最低/最高<80%): {count_price}")
    print(f"满足条件(ma30单调递增): {count_ma30}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "2浪趋势v2.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for stock in stocks:
            writer.writerow([stock['ts_code']])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("🌊 2浪趋势选股策略 v2")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 总市值 > 100亿")
    print("  2. 最近2个交易日收盘价 > MA5，且最近1个交易日成交量>500000")
    print("  3. 最近30个交易日内出现波峰(T日)，T日成交量>1000000")
    print("  4. T日后至少5个交易日turning_point为下降")
    print("  5. T日至最近日，最低/最高收盘价 < 80%")
    print("  6. 最近2个交易日ma30单调递增")
    print("  7. 去除非A股股票")
    print("=" * 80)

    folder_path = get_folder_path()

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
        print(f"📄 CSV路径: {folder_path}/2浪趋势v2.csv")
        print("=" * 80)
        print("\n🔥 精选股票：")
        for i, stock in enumerate(stocks[:10], 1):
            mv_billion = stock['total_mv'] / 100000000
            price_drop_pct = (1 - stock['price_ratio']) * 100
            print(f"{i}. {stock['ts_code']} {stock['name']}")
            print(f"   └─ 市值: {mv_billion:.2f}亿")
            print(f"   └─ T日: {stock['T_date']}")
            print(f"   └─ 价格跌幅: {price_drop_pct:.2f}%")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()
