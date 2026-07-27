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
    folder_name = f"3浪启动{target_date}"
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


def find_3wave_pattern(records):
    n = len(records)
    if n < 30:
        return None

    turning_points = []
    for i in range(n):
        tp = records[i]['turning_point']
        ma5 = float(records[i]['ma5']) if records[i]['ma5'] else None
        if tp in ('波峰', '波谷') and ma5 is not None:
            turning_points.append({
                'index': i,
                'date': records[i]['trade_date'],
                'type': tp,
                'ma5': ma5
            })

    if len(turning_points) < 3:
        return None

    turning_points_reverse = list(reversed(turning_points))

    for i in range(len(turning_points_reverse) - 2):
        recent_tp = turning_points_reverse[i]
        middle_tp = turning_points_reverse[i + 1]
        far_tp = turning_points_reverse[i + 2]

        if recent_tp['type'] == '波峰' and middle_tp['type'] == '波谷' and far_tp['type'] == '波峰':
            dist1 = recent_tp['index'] - middle_tp['index']
            dist2 = middle_tp['index'] - far_tp['index']
            assert dist1 > 0, f"dist1 should be positive, got {dist1}"
            assert dist2 > 0, f"dist2 should be positive, got {dist2}"
            if dist1 >= 10 and dist2 >= 10:
                if recent_tp['ma5'] > far_tp['ma5']:
                    return {
                        'pattern': '波峰-波谷-波峰',
                        'recent_peak_date': recent_tp['date'],
                        'recent_peak_ma5': recent_tp['ma5'],
                        'trough_date': middle_tp['date'],
                        'trough_ma5': middle_tp['ma5'],
                        'far_peak_date': far_tp['date'],
                        'far_peak_ma5': far_tp['ma5'],
                        'dist1': dist1,
                        'dist2': dist2
                    }

        elif recent_tp['type'] == '波谷' and middle_tp['type'] == '波峰' and far_tp['type'] == '波谷':
            dist1 = recent_tp['index'] - middle_tp['index']
            dist2 = middle_tp['index'] - far_tp['index']
            assert dist1 > 0, f"dist1 should be positive, got {dist1}"
            assert dist2 > 0, f"dist2 should be positive, got {dist2}"
            if dist1 >= 10 and dist2 >= 10:
                if recent_tp['ma5'] > far_tp['ma5']:
                    return {
                        'pattern': '波谷-波峰-波谷',
                        'recent_trough_date': recent_tp['date'],
                        'recent_trough_ma5': recent_tp['ma5'],
                        'peak_date': middle_tp['date'],
                        'peak_ma5': middle_tp['ma5'],
                        'far_trough_date': far_tp['date'],
                        'far_trough_ma5': far_tp['ma5'],
                        'dist1': dist1,
                        'dist2': dist2
                    }

    return None


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


def check_ma5_increasing(records):
    if len(records) < 2:
        return False

    recent = records[-2:]
    ma5_1 = float(recent[0]['ma5']) if recent[0]['ma5'] else None
    ma5_2 = float(recent[1]['ma5']) if recent[1]['ma5'] else None

    if ma5_1 is None or ma5_2 is None:
        return False

    return ma5_2 > ma5_1


def check_gain_and_volume(records):
    if len(records) < 3:
        return False

    recent = records[-3:]
    for i in range(1, len(recent)):
        prev_close = float(recent[i - 1]['close']) if recent[i - 1]['close'] else None
        curr_close = float(recent[i]['close']) if recent[i]['close'] else None
        amount = float(recent[i]['amount']) if recent[i]['amount'] else None

        if prev_close is None or curr_close is None or amount is None:
            continue
        if prev_close <= 0:
            continue

        gain = (curr_close - prev_close) / prev_close * 100
        if gain > 5 and amount > 500000:
            return True

    return False


def analyze_stocks(data):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close': record['close'],
            'amount': record['amount'],
            'ma5': record['ma5'],
            'turning_point': record['turning_point'],
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_mv = 0
    count_pattern = 0
    count_close_ma5 = 0
    count_ma5_inc = 0
    count_gain_vol = 0

    for ts_code, records in stock_data.items():
        if len(records) < 100:
            continue

        records.sort(key=lambda x: x['trade_date'])

        latest = records[-1]
        stock_name = latest['name']

        if 'ST' in stock_name:
            continue

        total_mv = latest['total_mv']
        if total_mv < 8000000000:
            continue

        count_total += 1
        count_mv += 1

        pattern_info = find_3wave_pattern(records)
        if not pattern_info:
            continue

        count_pattern += 1

        if not check_close_above_ma5(records):
            continue

        count_close_ma5 += 1

        if not check_ma5_increasing(records):
            continue

        count_ma5_inc += 1

        if not check_gain_and_volume(records):
            continue

        count_gain_vol += 1

        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv,
            'pattern': pattern_info['pattern'],
            'recent_point_date': pattern_info.get('recent_peak_date', pattern_info.get('recent_trough_date', '')),
            'recent_point_ma5': pattern_info.get('recent_peak_ma5', pattern_info.get('recent_trough_ma5', 0)),
            'middle_point_date': pattern_info.get('trough_date', pattern_info.get('peak_date', '')),
            'middle_point_ma5': pattern_info.get('trough_ma5', pattern_info.get('peak_ma5', 0)),
            'far_point_date': pattern_info.get('far_peak_date', pattern_info.get('far_trough_date', '')),
            'far_point_ma5': pattern_info.get('far_peak_ma5', pattern_info.get('far_trough_ma5', 0)),
            'dist1': pattern_info['dist1'],
            'dist2': pattern_info['dist2']
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件a(市值>100亿): {count_mv}")
    print(f"满足条件a+b(3浪模式): {count_pattern}")
    print(f"满足条件a+b+c(close>ma5): {count_close_ma5}")
    print(f"满足条件a+b+c+d(ma5递增): {count_ma5_inc}")
    print(f"满足条件a+b+c+d+e(涨幅>5%且成交量>500000): {count_gain_vol}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "3浪启动.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,市值(亿),3浪模式,近期点日期,近期点MA5,中间点日期,中间点MA5,远期点日期,远期点MA5,间隔1,间隔2\n")
        for stock in stocks:
            mv_billion = stock['total_mv'] / 100000000
            f.write(f"{stock['ts_code']},{stock['name']},{mv_billion:.2f},")
            f.write(f"{stock['pattern']},{stock['recent_point_date']},{stock['recent_point_ma5']:.2f},")
            f.write(f"{stock['middle_point_date']},{stock['middle_point_ma5']:.2f},")
            f.write(f"{stock['far_point_date']},{stock['far_point_ma5']:.2f},")
            f.write(f"{stock['dist1']},{stock['dist2']}\n")

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("🌊 3浪启动选股策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  a. 总市值 > 80亿")
    print("  b. 最近100个交易日内出现3浪模式：")
    print("     - 波峰→波谷→波峰（间隔≥10日，近期波峰>远期波峰）")
    print("     - 波谷→波峰→波谷（间隔≥10日，近期波谷>远期波谷）")
    print("  c. 最近2个交易日收盘价close > MA5")
    print("  d. 最近2个交易日MA5单调递增")
    print("  e. 最近2个交易日至少有一天涨幅>5%且成交量amount>500000")
    print("=" * 80)

    folder_path = create_folder()

    data = read_stock_data()

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
            print(f"{i}. {stock['ts_code']} {stock['name']}")
            print(f"   └─ 模式: {stock['pattern']} | 市值: {stock['total_mv']/100000000:.1f}亿")
            print(f"   └─ 间隔: {stock['dist1']}日, {stock['dist2']}日")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()