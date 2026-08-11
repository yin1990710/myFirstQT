#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
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
    folder_name = f"2浪趋势{target_date}"
    return folder_name


def create_folder():
    folder_name = get_folder_name()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)

    if os.path.exists(folder_path):
        print(f"🗑️ 文件夹: {folder_name} 已存在")
    else:
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")

    return folder_path


def read_stock_data():
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=150)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.amount,
        d.ma5,
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


def find_T_date(records):
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


def check_10_days_decline(records, T_index):
    n = len(records)
    T_plus_10 = T_index + 10
    
    if T_plus_10 >= n:
        return False
    
    for i in range(T_index + 1, T_plus_10 + 1):
        if records[i]['turning_point'] != '下降':
            return False
    
    return True


def check_price_drop(records, T_index):
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
    
    ratio = min_price / max_price
    return ratio < 0.8


def check_turning_point_ratio(records, T_index):
    n = len(records)
    
    n1 = 0
    avg1_amounts = []
    i = T_index - 1
    while i >= 0:
        if records[i]['turning_point'] == '上升':
            n1 += 1
            amount = records[i]['amount']
            if amount is not None and amount > 0:
                avg1_amounts.append(float(amount))
            i -= 1
        else:
            break
    
    if n1 == 0:
        return False, 0, 0, 0, 0
    avg1 = sum(avg1_amounts) / len(avg1_amounts) if avg1_amounts else 0
    
    n2 = 0
    avg2_amounts = []
    i = T_index + 1
    while i < n:
        if records[i]['turning_point'] == '下降':
            n2 += 1
            amount = records[i]['amount']
            if amount is not None and amount > 0:
                avg2_amounts.append(float(amount))
            i += 1
        else:
            break
    
    if n2 == 0:
        return False, n1, n2, avg1, 0
    avg2 = sum(avg2_amounts) / len(avg2_amounts) if avg2_amounts else 0
    
    if avg2 == 0:
        return False, n1, n2, avg1, avg2
    
    n_ratio = n1 / n2
    avg_ratio = avg1 / avg2
    
    return (n_ratio > 1 and avg_ratio > 1.5), n1, n2, avg1, avg2


def check_close_above_ma5(records):
    if len(records) < 2:
        return False
    
    recent = records[-2:]
    for r in recent:
        close = float(r['close']) if r['close'] else None
        ma5 = float(r['ma5']) if r['ma5'] else None
        if close is None or ma5 is None:
            return False
        if close <= ma5:
            return False
    return True


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
    count_ratio = 0
    
    for ts_code, records in stock_data.items():
        if len(records) < 100:
            continue
        
        records.sort(key=lambda x: x['trade_date'])
        
        latest = records[-1]
        stock_name = latest['name']
        
        if 'ST' in stock_name:
            continue
        
        if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH')):
            continue
        
        total_mv = latest['total_mv']
        if total_mv < 10000000000:
            continue
        
        count_total += 1
        count_mv += 1
        
        if not check_close_above_ma5(records):
            continue
        
        count_close_ma5 += 1
        
        T_date = find_T_date(records)
        if not T_date:
            continue
        
        count_T += 1
        
        if not check_10_days_decline(records, T_date['index']):
            continue
        
        count_decline += 1
        
        if not check_price_drop(records, T_date['index']):
            continue
        
        count_price += 1
        
        ratio_result, n1, n2, avg1, avg2 = check_turning_point_ratio(records, T_date['index'])
        if not ratio_result:
            continue
        
        count_ratio += 1
        
        prices = []
        for i in range(T_date['index'], len(records)):
            if records[i]['close'] is not None:
                prices.append(float(records[i]['close']))
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        price_ratio = min_price / max_price if max_price > 0 else 0
        
        n_ratio = n1 / n2 if n2 > 0 else 0
        avg_ratio = avg1 / avg2 if avg2 > 0 else 0
        
        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv,
            'T_date': T_date['date'],
            'T_close': T_date['close'],
            'T_amount': T_date['amount'],
            'price_ratio': price_ratio,
            'n1': n1,
            'n2': n2,
            'n_ratio': n_ratio,
            'avg1': avg1,
            'avg2': avg2,
            'avg_ratio': avg_ratio
        })
    
    result.sort(key=lambda x: x['total_mv'], reverse=True)
    
    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件a(市值>100亿): {count_mv}")
    print(f"满足条件a+b(close>ma5): {count_close_ma5}")
    print(f"满足条件a+b+c(最近30日出现波峰): {count_T}")
    print(f"满足条件a+b+c+d(T日后10日下降): {count_decline}")
    print(f"满足条件a+b+c+d+e(最低/最高<80%): {count_price}")
    print(f"满足条件a+b+c+d+e+f(n1/n2>1且avg1/avg2>1.5): {count_ratio}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)
    
    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "2浪趋势.csv"
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
    print("🌊 2浪趋势选股策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  a. 总市值 > 100亿")
    print("  b. 最近2个交易日收盘价 > MA5")
    print("  c. 最近30个交易日内出现波峰(T日)，且T日成交量>1000000")
    print("  d. T日后出现至少10个交易日的下降")
    print("  e. T日至最近日，最低/最高收盘价 < 80%")
    print("  f. n1/n2 > 1 且 avg1/avg2 > 1.5")
    print("     (n1: T日前连续上升天数, avg1: 上升日平均成交量)")
    print("     (n2: T日后连续下降天数, avg2: 下降日平均成交量)")
    print("  g. 去除非A股股票")
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
        print(f"📄 CSV路径: {folder_path}/2浪趋势.csv")
        print("=" * 80)
        
        print("\n🔥 精选股票：")
        for i, stock in enumerate(stocks[:10], 1):
            mv_billion = stock['total_mv'] / 100000000
            price_drop_pct = (1 - stock['price_ratio']) * 100
            print(f"{i}. {stock['ts_code']} {stock['name']}")
            print(f"   └─ 市值: {mv_billion:.2f}亿")
            print(f"   └─ T日: {stock['T_date']}")
            print(f"   └─ 价格跌幅: {price_drop_pct:.2f}%")
            print(f"   └─ n1/n2: {stock['n1']}/{stock['n2']}={stock['n_ratio']:.2f}")
            print(f"   └─ avg1/avg2: {stock['avg_ratio']:.2f}")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()