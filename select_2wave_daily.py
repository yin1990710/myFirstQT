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


def calculate_ma(close_prices, period=30):
    if len(close_prices) < period:
        return None
    return sum(close_prices[-period:]) / period


def find_turning_points(records):
    """识别波峰和波谷"""
    turning_points = []
    if len(records) < 5:
        return turning_points
    
    close_prices = [r['close'] for r in records]
    
    for i in range(2, len(records) - 2):
        prev_prev = close_prices[i-2]
        prev = close_prices[i-1]
        curr = close_prices[i]
        next_ = close_prices[i+1]
        next_next = close_prices[i+2]
        
        # 判断波谷：当前价格低于前后两天
        if curr < prev and curr < next_ and curr <= prev_prev and curr <= next_next:
            turning_points.append({
                'index': i,
                'date': records[i]['trade_date'],
                'type': 'trough',
                'price': curr
            })
    
    return turning_points


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
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0,
            'circ_mv': float(record['circ_mv'] or 0) if record['circ_mv'] else 0
        })

    result = []
    count_total = 0
    count_condition1 = 0
    count_condition2 = 0
    count_condition3 = 0
    count_condition4 = 0
    count_trough = 0

    for ts_code, records in stock_data.items():
        if len(records) < 45:
            continue

        records.sort(key=lambda x: x['trade_date'])

        # 获取最新记录用于过滤
        latest = records[-1]
        
        # 过滤条件1：去除ST股（股票名称包含ST）
        stock_name = latest['name']
        if 'ST' in stock_name:
            continue
        
        # 过滤条件2：去除流通市值小于50亿的股票（circ_mv单位为元）
        circ_mv = latest['circ_mv']
        if circ_mv < 5000000000:  # 50亿 = 5000000000元
            continue

        count_total += 1

        close_prices = [r['close'] for r in records]
        amount_list = [r['amount'] for r in records]

        recent_40_days = records[-40:] if len(records) >= 40 else records
        close_40 = [r['close'] for r in recent_40_days]
        amount_40 = [r['amount'] for r in recent_40_days]

        min_price = min(close_40)
        max_price = max(close_40)
        max_index = close_40.index(max_price)

        first_wave = recent_40_days[:max_index + 1]
        adjustment = recent_40_days[max_index + 1:]

        if len(first_wave) < 8:
            continue

        first_wave_amount = [r['amount'] for r in first_wave]
        
        # 计算整个上涨期的平均成交额
        first_wave_avg_amount_all = sum(first_wave_amount) / len(first_wave_amount)
        
        # 只计算放量上涨阶段（成交额超过整个上涨期平均的1.5倍）
        boom_threshold = first_wave_avg_amount_all * 1.5
        boom_days = [a for a in first_wave_amount if a > boom_threshold]
        
        if boom_days:
            first_wave_avg_amount = sum(boom_days) / len(boom_days)
        else:
            first_wave_avg_amount = first_wave_avg_amount_all

        first_wave_gain = (first_wave[-1]['close'] - first_wave[0]['close']) / first_wave[0]['close'] * 100

        # 放宽条件：第一波涨幅>15%
        if first_wave_gain < 15:
            continue

        count_condition1 += 1

        if len(adjustment) < 2:
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
            # 放宽条件：允许10%的误差
            if adjustment[i]['close'] < adjustment_ma30[i] * 0.90:
                below_ma30 = True
                break

        if below_ma30:
            continue

        count_condition2 += 1

        adjustment_avg_amount = sum([r['amount'] for r in adjustment]) / len(adjustment)

        # 量能萎缩：调整期间成交量萎缩至放量上涨阶段的60%以下
        if adjustment_avg_amount > first_wave_avg_amount * 0.60:
            continue

        count_condition3 += 1

        latest = records[-1]
        prev = records[-2]

        today_gain = (latest['close'] - prev['close']) / prev['close'] * 100

        # 检查近3个交易日是否出现波谷
        has_recent_trough = False
        recent_records = records[-15:]
        turning_points = find_turning_points(recent_records)
        
        for tp in turning_points:
            # 波谷出现在近3个交易日内
            if tp['index'] >= len(recent_records) - 3:
                has_recent_trough = True
                break

        # 放宽条件：今日涨幅>1.5% 或者 近3个交易日出现波谷
        if today_gain < 1.5 and not has_recent_trough:
            continue

        if has_recent_trough:
            count_trough += 1
        else:
            count_condition4 += 1

        # 放宽条件：市值>20亿
        if latest['total_mv'] < 200000000:
            continue

        # 放宽条件：成交额>2亿
        if latest['amount'] < 200000:
            continue

        result.append({
            'ts_code': ts_code,
            'name': latest['name'],
            'close': latest['close'],
            'total_mv': latest['total_mv'],
            'first_wave_gain': first_wave_gain,
            'today_gain': today_gain,
            'amount_ratio': adjustment_avg_amount / first_wave_avg_amount if first_wave_avg_amount > 0 else 0,
            'has_trough': has_recent_trough
        })

    result.sort(key=lambda x: x['first_wave_gain'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件1(第一波涨幅>15%): {count_condition1}")
    print(f"满足条件1+2(调整未破30日均线): {count_condition2}")
    print(f"满足条件1+2+3(调整期间成交量萎缩): {count_condition3}")
    print(f"满足条件1+2+3+4(今日涨幅>1.5%): {count_condition4}")
    print(f"满足条件1+2+3+T(近3日出现波谷): {count_trough}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    target_date = get_target_date()
    csv_filename = f"二浪启动{target_date}.csv"
    csv_path = os.path.join(folder_path, csv_filename)

    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,收盘价,第一波涨幅(%),今日涨幅(%),调整量/上涨量,市值(亿),是否波谷\n")
        for stock in stocks:
            trough_flag = "是" if stock.get('has_trough', False) else "否"
            f.write(f"{stock['ts_code']},{stock['name']},{stock['close']:.2f},")
            f.write(f"{stock['first_wave_gain']:.2f},{stock['today_gain']:.2f},")
            f.write(f"{stock['amount_ratio']:.2f},{stock['total_mv']/10000:.2f},{trough_flag}\n")

    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("🌊 二浪启动选股策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 基础过滤：非ST股，流通市值>50亿")
    print("  2. 第一波上涨：涨幅超过15%")
    print("  3. 调整阶段：未跌破30日均线（允许10%的误差）")
    print("  4. 量能特征：调整期间成交量较放量上涨阶段萎缩60%以下")
    print("  5. 启动信号：今日涨幅超过1.5% OR 近3个交易日出现波谷")
    print("  6. 流动性：成交额超过2亿")
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
            trough_mark = " 📉" if stock.get('has_trough', False) else ""
            print(f"{i}. {stock['ts_code']} {stock['name']}{trough_mark} - 第一波涨{stock['first_wave_gain']:.1f}% 今日涨{stock['today_gain']:.1f}% 市值{stock['total_mv']/10000:.1f}亿")
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
