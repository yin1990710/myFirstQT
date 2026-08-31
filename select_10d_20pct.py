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
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_folder_path():
    target_date = get_target_date()
    folder_name = f"10天涨20个点{target_date}"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


def read_stock_data():
    """读取最近15个交易日的日线数据（最近10日 + T日前5日），并关联股票信息"""
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
        d.amount,
        i.stock_name,
        i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date >= (
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        ORDER BY trade_date DESC
        LIMIT 1 OFFSET 14
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
            'amount': float(record['amount']) if record['amount'] else None,
            'name': record['stock_name'] or '',
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0
        })

    result = []
    count_total = 0
    count_mv = 0
    count_T = 0
    count_amount = 0
    count_gain = 0

    for ts_code, records in stock_data.items():
        if len(records) < 10:
            continue

        records.sort(key=lambda x: x['trade_date'])
        latest = records[-1]
        stock_name = latest['name']

        count_total += 1

        # 条件2：去除总市值小于100亿的股票
        total_mv = latest['total_mv']
        if total_mv < 10000000000:
            continue
        count_mv += 1

        recent10 = records[-10:]

        # 条件3：最近10个交易日的最高收盘价出现的日期为T日
        valid_closes = [(i, float(r['close'])) for i, r in enumerate(recent10) if r['close'] is not None]
        if len(valid_closes) < 2:
            continue
        max_close = max(c for _, c in valid_closes)
        t_idx_local = next(i for i, c in valid_closes if c == max_close)
        t_idx_global = len(records) - len(recent10) + t_idx_local
        t_date = records[t_idx_global]['trade_date']
        count_T += 1

        # 条件4：T日前5个交易日至T日（含）的平均成交额 >= T日至最近一个交易日的平均成交额的2倍
        if t_idx_global < 5:
            continue
        before = records[t_idx_global - 5: t_idx_global + 1]
        after = records[t_idx_global:]
        amounts_before = [r['amount'] for r in before if r['amount'] is not None]
        amounts_after = [r['amount'] for r in after if r['amount'] is not None]
        if not amounts_before or not amounts_after:
            continue
        avg_before = sum(amounts_before) / len(amounts_before)
        avg_after = sum(amounts_after) / len(amounts_after)
        if avg_after <= 0 or avg_before < 2 * avg_after:
            continue
        amount_ratio = avg_before / avg_after
        count_amount += 1

        # 条件5：最近10个交易日的涨幅大于20%
        closes = [c for _, c in valid_closes]
        min_close = min(closes)
        if min_close <= 0:
            continue
        gain = (max_close - min_close) / min_close
        if gain <= 0.20:
            continue
        count_gain += 1

        result.append({
            'ts_code': ts_code,
            'name': stock_name,
            't_date': t_date,
            'min_close': min_close,
            'max_close': max_close,
            'avg_before': avg_before,
            'avg_after': avg_after,
            'amount_ratio': amount_ratio,
            'gain': gain
        })

    result.sort(key=lambda x: x['gain'], reverse=True)

    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"总股票数(数据完整): {count_total}")
    print(f"满足条件(市值>100亿): {count_mv}")
    print(f"满足条件(确定T日): {count_T}")
    print(f"满足条件(T前平均成交额>=2倍T后): {count_amount}")
    print(f"满足条件(10日涨幅>20%): {count_gain}")
    print(f"最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "10天涨幅大于20个点.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', 'T日', '最低收盘价', '最高收盘价', 'T前平均成交额(亿)', 'T后平均成交额(亿)', '缩量倍数', '涨幅'])
        for stock in stocks:
            writer.writerow([
                stock['ts_code'],
                stock['name'],
                stock['t_date'],
                f"{stock['min_close']:.2f}",
                f"{stock['max_close']:.2f}",
                f"{stock['avg_before'] * 1000 / 100000000:.2f}",
                f"{stock['avg_after'] * 1000 / 100000000:.2f}",
                f"{stock['amount_ratio']:.2f}",
                f"{stock['gain'] * 100:.2f}%"
            ])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("📈 10天涨幅大于20%选股策略")
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
        print(f"📄 CSV路径: {folder_path}/10天涨幅大于20个点.csv")
        print("=" * 80)
        print("\n🔥 精选股票（前10）：")
        for i, stock in enumerate(stocks[:10], 1):
            print(f"{i}. {stock['ts_code']} {stock['name']} T日: {stock['t_date']} 缩量倍数: {stock['amount_ratio']:.2f} 涨幅: {stock['gain']*100:.2f}%")
    else:
        print("\n❌ 没有选出符合条件的股票")


if __name__ == "__main__":
    main()
