#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

def create_table(connection):
    create_sql = """
    CREATE TABLE IF NOT EXISTS rzrq_ye_t (
        trade_date VARCHAR(8) NOT NULL,
        exchange_id VARCHAR(10) NOT NULL,
        rzye DECIMAL(20, 2),
        rzmre DECIMAL(20, 2),
        rzche DECIMAL(20, 2),
        rqye DECIMAL(20, 2),
        rqmcl DECIMAL(20, 2),
        rzrqye DECIMAL(20, 2),
        rqyl DECIMAL(20, 2),
        PRIMARY KEY (trade_date, exchange_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(create_sql)
        connection.commit()
        print("✅ 表 rzrq_ye_t 创建成功")
    except Exception as e:
        print(f"❌ 创建表失败: {e}")
        return False
    return True

def fetch_margin_data(days=600):
    try:
        pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')
        df = pro.margin(trade_date='', start_date='', end_date='', limit=days)
        print(f"✅ 成功获取 {len(df)} 条融资融券数据")
        return df
    except Exception as e:
        print(f"❌ 获取融资融券数据失败: {e}")
        return None

def insert_data(connection, df):
    if df is None or df.empty:
        print("❌ 没有数据需要插入")
        return

    insert_sql = """
    INSERT INTO rzrq_ye_t (
        trade_date, exchange_id, rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s
    ) ON DUPLICATE KEY UPDATE
        rzye = VALUES(rzye),
        rzmre = VALUES(rzmre),
        rzche = VALUES(rzche),
        rqye = VALUES(rqye),
        rqmcl = VALUES(rqmcl),
        rzrqye = VALUES(rzrqye),
        rqyl = VALUES(rqyl)
    """

    count_insert = 0
    count_update = 0

    try:
        with connection.cursor() as cursor:
            for _, row in df.iterrows():
                trade_date = str(row['trade_date']) if row['trade_date'] else ''
                exchange_id = str(row['exchange_id']) if row['exchange_id'] else ''
                rzye = float(row['rzye']) if row['rzye'] else 0
                rzmre = float(row['rzmre']) if row['rzmre'] else 0
                rzche = float(row['rzche']) if row['rzche'] else 0
                rqye = float(row['rqye']) if row['rqye'] else 0
                rqmcl = float(row['rqmcl']) if row['rqmcl'] else 0
                rzrqye = float(row['rzrqye']) if row['rzrqye'] else 0
                rqyl = float(row['rqyl']) if row['rqyl'] else 0

                cursor.execute(insert_sql, (
                    trade_date, exchange_id, rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl
                ))

                if cursor.lastrowid > 0:
                    count_insert += 1
                else:
                    count_update += 1

        connection.commit()
        print(f"✅ 数据写入完成: 新增 {count_insert} 条, 更新 {count_update} 条")
    except Exception as e:
        print(f"❌ 插入数据失败: {e}")
        connection.rollback()

def main():
    print("=" * 80)
    print("获取融资融券余额数据")
    print("=" * 80)

    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return

    try:
        if not create_table(connection):
            return

        df = fetch_margin_data(days=600)
        if df is not None:
            insert_data(connection, df)

    finally:
        close_connection(connection)

    print("\n" + "=" * 80)
    print("🎉 任务完成")
    print("=" * 80)

if __name__ == "__main__":
    main()