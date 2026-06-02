#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import pandas as pd
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

import tushare as ts

def read_stock_index_codes():
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '股指代码', 'stock_index.csv')
    if not os.path.exists(csv_path):
        print(f"❌ 未找到文件: {csv_path}")
        return None
    
    try:
        df = pd.read_csv(csv_path)
        codes = df.iloc[:, 0].tolist()
        print(f"✅ 成功读取 {len(codes)} 个股指代码")
        return codes
    except Exception as e:
        print(f"❌ 读取CSV文件失败: {e}")
        return None

def create_table(connection):
    create_sql = """
    CREATE TABLE IF NOT EXISTS stock_index_daily_t (
        ts_code VARCHAR(20) NOT NULL,
        trade_date VARCHAR(8) NOT NULL,
        close FLOAT,
        open FLOAT,
        high FLOAT,
        low FLOAT,
        pre_close FLOAT,
        `change` FLOAT,
        pct_chg FLOAT,
        vol FLOAT,
        amount FLOAT,
        PRIMARY KEY (ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(create_sql)
        connection.commit()
        print("✅ 表结构创建成功")
        return True
    except Exception as e:
        print(f"❌ 创建表失败: {e}")
        return False

def truncate_table(connection):
    truncate_sql = "TRUNCATE TABLE stock_index_daily_t"
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(truncate_sql)
        connection.commit()
        print("✅ 表数据已清空")
        return True
    except Exception as e:
        print(f"❌ 清空表失败: {e}")
        return False

def fetch_index_data(codes):
    all_data = []
    pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')
    
    for code in codes:
        print(f"🔄 正在获取 {code} 的数据...")
        try:
            df = pro.index_daily(ts_code=code)
            if df is not None and not df.empty:
                all_data.append(df)
                print(f"✅ 获取 {code} 数据成功，共 {len(df)} 条")
            else:
                print(f"⚠️ {code} 没有数据")
        except Exception as e:
            print(f"❌ 获取 {code} 数据失败: {e}")
    
    if all_data:
        return pd.concat(all_data, ignore_index=True)
    return None

def insert_data(connection, df):
    insert_sql = """
    INSERT INTO stock_index_daily_t (
        ts_code, trade_date, close, open, high, low, pre_close, `change`, pct_chg, vol, amount
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        close=VALUES(close),
        open=VALUES(open),
        high=VALUES(high),
        low=VALUES(low),
        pre_close=VALUES(pre_close),
        `change`=VALUES(`change`),
        pct_chg=VALUES(pct_chg),
        vol=VALUES(vol),
        amount=VALUES(amount)
    """
    
    try:
        with connection.cursor() as cursor:
            for _, row in df.iterrows():
                cursor.execute(insert_sql, (
                    row['ts_code'],
                    row['trade_date'],
                    row['close'],
                    row['open'],
                    row['high'],
                    row['low'],
                    row['pre_close'],
                    row['change'],
                    row['pct_chg'],
                    row['vol'],
                    row['amount']
                ))
        connection.commit()
        print(f"✅ 成功插入 {len(df)} 条数据")
        return True
    except Exception as e:
        print(f"❌ 插入数据失败: {e}")
        return False

def main():
    print("=" * 80)
    print("股指日数据更新")
    print("=" * 80)
    
    codes = read_stock_index_codes()
    if not codes:
        print("❌ 没有获取到股指代码，退出程序")
        return
    
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败，退出程序")
        return
    
    try:
        if not create_table(connection):
            return
        
        if not truncate_table(connection):
            return
        
        df = fetch_index_data(codes)
        if df is None or df.empty:
            print("❌ 没有获取到任何数据")
            return
        
        insert_data(connection, df)
        
    finally:
        close_connection(connection)
    
    print("\n" + "=" * 80)
    print("🎉 股指日数据更新完成!")
    print("=" * 80)

if __name__ == "__main__":
    main()