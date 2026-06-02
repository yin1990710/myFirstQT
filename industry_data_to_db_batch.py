#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从同花顺获取行业板块日线数据并写入数据库
"""

import os
import sys
import pandas as pd
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import tushare as ts
from mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

CSV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "同花顺行业概念板块", "板块名称及代码.csv")

def read_industry_codes():
    """
    读取CSV文件获取板块代码

    :return: 板块名称和代码的字典列表
    """
    print(f"📂 正在读取CSV文件: {CSV_FILE}")
    df = pd.read_csv(CSV_FILE)
    industries = df.to_dict('records')
    print(f"✅ 成功读取 {len(industries)} 个板块")
    return industries

def get_industry_daily(ts_code, start_date=None, end_date=None, limit=300):
    """
    获取行业板块日线数据

    :param ts_code: 板块代码
    :param start_date: 开始日期，格式 'YYYYMMDD'
    :param end_date: 结束日期，格式 'YYYYMMDD'
    :param limit: 返回数量限制
    :return: 包含日线数据的DataFrame
    """
    try:
        df = pro.moneyflow_cnt_ths(**{
            "ts_code": ts_code,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit
        }, fields=[
            "trade_date",
            "ts_code",
            "name",
            "lead_stock",
            "close_price",
            "pct_change",
            "industry_index",
            "company_num",
            "pct_change_stock",
            "net_buy_amount",
            "net_sell_amount",
            "net_amount"
        ])
        return df
    except Exception as e:
        print(f"❌ 获取板块 {ts_code} 数据失败: {e}")
        return pd.DataFrame()

def create_table(connection):
    """
    创建行业板块日线数据表（如果不存在）

    :param connection: MySQL连接对象
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS ths_industry_daily_t (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        trade_date VARCHAR(8) NOT NULL COMMENT '交易日期',
        ts_code VARCHAR(20) NOT NULL COMMENT '板块代码',
        name VARCHAR(100) COMMENT '板块名称',
        lead_stock VARCHAR(100) COMMENT '领涨股票名称',
        close_price DECIMAL(10,2) COMMENT '最新价',
        pct_change DECIMAL(10,2) COMMENT '行业涨跌幅',
        industry_index DECIMAL(12,2) COMMENT '行业指数',
        company_num INT COMMENT '公司数量',
        pct_change_stock DECIMAL(10,2) COMMENT '领涨股涨跌幅',
        net_buy_amount DECIMAL(20,2) COMMENT '流入资金(元)',
        net_sell_amount DECIMAL(20,2) COMMENT '流出资金(元)',
        net_amount DECIMAL(20,2) COMMENT '净额(元)',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        UNIQUE KEY uk_ts_date (ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='同花顺行业板块日线数据表';
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(create_table_sql)
        connection.commit()
        print("✅ ths_industry_daily_t 表创建成功（或已存在）")
    except Exception as e:
        print(f"❌ 创建表失败: {e}")
        connection.rollback()

def insert_data(connection, df, industry_name):
    """
    插入板块数据到数据库

    :param connection: MySQL连接对象
    :param df: 包含板块数据的DataFrame
    :param industry_name: 板块名称
    """
    if df.empty:
        return 0

    insert_sql = """
    INSERT INTO ths_industry_daily_t (
        trade_date, ts_code, name, lead_stock, close_price,
        pct_change, industry_index, company_num, pct_change_stock,
        net_buy_amount, net_sell_amount, net_amount
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    ) ON DUPLICATE KEY UPDATE
        name = VALUES(name),
        lead_stock = VALUES(lead_stock),
        close_price = VALUES(close_price),
        pct_change = VALUES(pct_change),
        industry_index = VALUES(industry_index),
        company_num = VALUES(company_num),
        pct_change_stock = VALUES(pct_change_stock),
        net_buy_amount = VALUES(net_buy_amount),
        net_sell_amount = VALUES(net_sell_amount),
        net_amount = VALUES(net_amount)
    """

    try:
        with connection.cursor() as cursor:
            data = df[['trade_date', 'ts_code', 'name', 'lead_stock', 'close_price',
                      'pct_change', 'industry_index', 'company_num', 'pct_change_stock',
                      'net_buy_amount', 'net_sell_amount', 'net_amount']].values.tolist()
            cursor.executemany(insert_sql, data)
        connection.commit()
        count = len(df)
        print(f"✅ [{industry_name}] 成功插入/更新 {count} 条数据")
        return count
    except Exception as e:
        print(f"❌ 插入数据失败: {e}")
        connection.rollback()
        return 0

def main():
    """
    主函数：获取行业板块数据并写入数据库
    """
    print("=" * 60)
    print("获取同花顺行业板块日线数据")
    print("=" * 60)

    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=320)).strftime('%Y%m%d')

    print(f"日期范围: {start_date} ~ {end_date}")

    industries = read_industry_codes()

    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败，退出程序")
        return

    try:
        create_table(connection)

        total_count = 0
        success_count = 0

        for industry in industries:
            industry_name = industry.get('概念名称', '')
            ts_code = industry.get('板块代码', '')

            if not ts_code:
                continue

            print(f"\n[{success_count + 1}/{len(industries)}] 正在获取: {industry_name} ({ts_code})")

            df = get_industry_daily(ts_code, start_date, end_date, limit=300)

            if not df.empty:
                count = insert_data(connection, df, industry_name)
                total_count += count
                success_count += 1
            else:
                print(f"⚠️ [{industry_name}] 未获取到数据")

        print("\n" + "=" * 60)
        print(f"✅ 数据更新完成！成功处理 {success_count}/{len(industries)} 个板块，共 {total_count} 条记录")
        print("=" * 60)

    finally:
        close_connection(connection)

if __name__ == "__main__":
    main()
