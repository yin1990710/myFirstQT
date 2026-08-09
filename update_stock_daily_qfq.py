#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
from datetime import datetime, timedelta

from mysql_connection import get_mysql_connection, close_connection

import tushare as ts
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

CALL_INTERVAL = 0.5

def get_stock_codes_from_db(conn):
    try:
        cursor = conn.cursor()
        sql = """
            SELECT ts_code 
            FROM stock_info_t 
            WHERE ts_code IS NOT NULL 
              AND ts_code NOT LIKE '%.BJ'
              AND stock_name NOT LIKE '%ST%'
            GROUP BY ts_code
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        if isinstance(rows[0], dict):
            codes = [row['ts_code'] for row in rows]
        else:
            codes = [row[0] for row in rows]
        return codes
    except Exception as e:
        print(f"❌ 读取股票代码失败: {e}")
        return []

def get_stock_daily_qfq(ts_code, start_date=None, end_date=None):
    """获取前复权日线数据"""
    df = ts.pro_bar(**{
        "ts_code": ts_code,
        "start_date": start_date,
        "end_date": end_date,
        "adj": "qfq"
    }, fields=[
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount"
    ])
    return df

def get_adj_factor(ts_code, start_date=None, end_date=None):
    """获取前复权因子"""
    df = pro.adj_factor(**{
        "ts_code": ts_code,
        "start_date": start_date,
        "end_date": end_date
    }, fields=[
        "ts_code",
        "trade_date",
        "adj_factor"
    ])
    return df

def get_latest_trade_date():
    now = datetime.now()
    if now.hour < 15:
        target_date = now - timedelta(days=1)
        return target_date.strftime('%Y%m%d')
    else:
        return now.strftime('%Y%m%d')

def main():
    print("=" * 60)
    print("📊 股票日线数据更新程序（前复权+复权因子）")
    print("=" * 60)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库，程序退出")
        sys.exit(1)

    try:
        stock_codes = get_stock_codes_from_db(conn)
        if not stock_codes:
            print("❌ 没有找到股票代码，程序退出")
            return

        print(f"   ✅ 共读取到 {len(stock_codes)} 个股票代码")

        start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
        end_date = datetime.now().strftime('%Y%m%d')
        print(f"   📅 日期范围: {start_date} ~ {end_date}")

        cursor = conn.cursor()
        total_inserted = 0
        total_updated = 0
        error_count = 0

        for i, ts_code in enumerate(stock_codes, 1):
            try:
                if i % 10 == 0:
                    print(f"   进度: {i}/{len(stock_codes)} ({i*100//len(stock_codes)}%), 已插入: {total_inserted}, 已更新: {total_updated}")

                # 1. 获取前复权日线数据
                df_daily = get_stock_daily_qfq(ts_code, start_date=start_date, end_date=end_date)

                if df_daily is None or df_daily.empty:
                    continue

                # 2. 获取前复权因子
                df_factor = get_adj_factor(ts_code, start_date=start_date, end_date=end_date)

                # 3. 关联前复权数据和复权因子
                if df_factor is not None and not df_factor.empty:
                    df_merged = df_daily.merge(df_factor[['trade_date', 'adj_factor']], on='trade_date', how='left')
                else:
                    df_merged = df_daily.copy()
                    df_merged['adj_factor'] = None

                # 4. 写入stock_daily_qfq_t表
                df_merged = df_merged.sort_values('trade_date')
                for _, row in df_merged.iterrows():
                    insert_sql = """
                        INSERT INTO stock_daily_qfq_t (
                            ts_code, trade_date, open, high, low, close, pre_close,
                            `change`, pct_chg, vol, amount, qfq_adj_factor
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            open=VALUES(open), high=VALUES(high), low=VALUES(low),
                            close=VALUES(close), pre_close=VALUES(pre_close),
                            `change`=VALUES(`change`), pct_chg=VALUES(pct_chg),
                            vol=VALUES(vol), amount=VALUES(amount),
                            qfq_adj_factor=VALUES(qfq_adj_factor)
                    """
                    cursor.execute(insert_sql, (
                        row['ts_code'],
                        row['trade_date'],
                        row.get('open'),
                        row.get('high'),
                        row.get('low'),
                        row.get('close'),
                        row.get('pre_close'),
                        row.get('change'),
                        row.get('pct_chg'),
                        row.get('vol'),
                        row.get('amount'),
                        row.get('adj_factor')
                    ))
                    total_inserted += 1

                conn.commit()
                time.sleep(CALL_INTERVAL)

            except Exception as e:
                error_count += 1
                print(f"   ⚠️ 处理 {ts_code} 时出错: {e}")

        cursor.close()

        print(f"\n📈 更新结果统计:")
        print("-" * 40)
        print(f"   处理股票数量: {len(stock_codes)}")
        print(f"   新增/更新记录数: {total_inserted}")
        print(f"   出错次数: {error_count}")

        print("\n🎉 股票日线数据更新完成！")

    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()
