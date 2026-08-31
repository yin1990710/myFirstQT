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
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_folder_path():
    """新建文件夹，已存在的删除重建"""
    target_date = get_target_date()
    folder_name = f"底部企稳{target_date}"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 删除已存在文件夹: {folder_name}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


def read_stock_data():
    """读取最近201个交易日的日线数据（200日条件 + 1日涨幅参考日），并关联股票信息"""
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.ma5,
        d.ma30,
        i.stock_name,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date >= (
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        ORDER BY trade_date DESC
        LIMIT 1 OFFSET 200
    )
    AND d.trade_date <= %s
    ORDER BY d.ts_code, d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (target_date,))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据")
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
            'close': float(record['close']) if record['close'] else None,
            'ma5': float(record['ma5']) if record['ma5'] else None,
            'ma30': float(record['ma30']) if record['ma30'] else None,
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_mv = 0
    count_range = 0
    count_gain = 0
    count_ma = 0

    for ts_code, records in stock_data.items():
        if len(records) < 200:
            continue

        records.sort(key=lambda x: x['trade_date'])
        latest = records[-1]
        stock_name = latest['name']

        # 条件1：根据股票代码去除非A股股票
        if not (ts_code.endswith('.SZ') or ts_code.endswith('.SH')):
            continue

        count_total += 1

        # 条件2：总市值 > 200亿
        total_mv = latest['total_mv']
        if total_mv < 20000000000:
            continue
        count_mv += 1

        # 最近200个交易日窗口
        recent200 = records[-200:]
        closes = [r['close'] for r in recent200 if r['close'] is not None]
        if len(closes) < 200:
            continue

        min_close = min(closes)
        max_close = max(closes)
        if min_close <= 0:
            continue

        # 条件3：最低收盘价/最高收盘价 < 50%，且最低收盘价出现在最近20个交易日内
        if min_close / max_close >= 0.50:
            continue
        recent20_closes = [r['close'] for r in records[-20:] if r['close'] is not None]
        if not recent20_closes or min(recent20_closes) > min_close:
            continue
        count_range += 1

        # 条件4：最近10个交易日至少有1日涨幅 > 8%
        has_gain = False
        for i in range(len(records) - 10, len(records)):
            prev_close = records[i - 1]['close']
            cur_close = records[i]['close']
            if prev_close is None or cur_close is None or prev_close <= 0:
                continue
            if (cur_close - prev_close) / prev_close > 0.08:
                has_gain = True
                break
        if not has_gain:
            continue
        count_gain += 1

        # 条件5：最近3个交易日 ma5 > ma30
        last3 = records[-3:]
        if not all(r['ma5'] is not None and r['ma30'] is not None and r['ma5'] > r['ma30'] for r in last3):
            continue
        count_ma += 1

        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            'total_mv': total_mv,
            'min_close': min_close,
            'max_close': max_close,
            'range_ratio': min_close / max_close
        })

    result.sort(key=lambda x: x['total_mv'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"A股股票数(数据完整): {count_total}")
    print(f"满足条件(市值>200亿): {count_mv}")
    print(f"满足条件(200日振幅<50%且最低价在近20日): {count_range}")
    print(f"满足条件(10日内有单日涨幅>8%): {count_gain}")
    print(f"满足条件(3日ma5>ma30): {count_ma}")
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
        print(f"📄 CSV路径: {folder_path}/底部企稳.csv")
        print("=" * 80)
        print("\n🔥 精选股票（前10）：")
        for i, stock in enumerate(stocks[:10], 1):
            mv_billion = stock['total_mv'] / 100000000
            print(f"{i}. {stock['ts_code']} {stock['name']} 市值: {mv_billion:.2f}亿 最低/最高: {stock['range_ratio']*100:.1f}%")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()
