#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
访问tushare的index_daily接口，查询指数日线数据并写入数据库
"""

import os
import sys
import tushare as ts
import pandas as pd
from datetime import datetime, timedelta

# 添加项目路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

# 初始化pro接口
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# 定义指数代码列表
INDEX_CODES = [
    '000001.SH',  # 上证指数
    '399001.SZ',  # 深证成指
    '399006.SZ',   # 创业板指
    '000985.CSI', #中证全指
    #申万行业指数
    '801750.SI' , #电力设备
    '801050.SI‌ ' #半导体
    
]

def get_index_daily(ts_code, start_date=None, end_date=None, limit=100):
    """
    获取指数日线数据
    
    :param ts_code: 指数代码
    :param start_date: 开始日期，格式 'YYYYMMDD'
    :param end_date: 结束日期，格式 'YYYYMMDD'
    :param limit: 返回数量限制
    :return: 包含日线数据的DataFrame
    """
    print(f"📡 正在获取指数 {ts_code} 的日线数据...")
    
    try:
        df = pro.index_daily(**{
            "ts_code": ts_code,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit
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
        
        if not df.empty:
            print(f"✅ 获取到 {len(df)} 条数据")
        else:
            print(f"⚠️ 未获取到 {ts_code} 的数据")
        
        return df
    
    except Exception as e:
        print(f"❌ 获取指数数据失败: {e}")
        return pd.DataFrame()

def create_index_daily_table(connection):
    """
    创建指数日线数据表（如果不存在）
    
    :param connection: MySQL连接对象
    """
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS index_daily_t (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        ts_code VARCHAR(10) NOT NULL COMMENT '指数代码',
        trade_date VARCHAR(8) NOT NULL COMMENT '交易日期',
        open DECIMAL(10,2) COMMENT '开盘价',
        high DECIMAL(10,2) COMMENT '最高价',
        low DECIMAL(10,2) COMMENT '最低价',
        close DECIMAL(10,2) COMMENT '收盘价',
        pre_close DECIMAL(10,2) COMMENT '前收盘价',
        `change` DECIMAL(10,2) COMMENT '涨跌额',
        pct_chg DECIMAL(6,2) COMMENT '涨跌幅(%)',
        vol BIGINT COMMENT '成交量(手)',
        amount DECIMAL(18,2) COMMENT '成交额(千元)',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        UNIQUE KEY uk_ts_date (ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数日线数据表';
    """
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(create_table_sql)
        connection.commit()
        print("✅ index_daily_t 表创建成功（或已存在）")
    except Exception as e:
        print(f"❌ 创建表失败: {e}")
        connection.rollback()

def insert_index_data(connection, df):
    """
    插入指数数据到数据库
    
    :param connection: MySQL连接对象
    :param df: 包含指数数据的DataFrame
    """
    if df.empty:
        return
    
    insert_sql = """
    INSERT INTO index_daily_t (
        ts_code, trade_date, open, high, low, close,
        pre_close, `change`, pct_chg, vol, amount
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    ) ON DUPLICATE KEY UPDATE
        open = VALUES(open),
        high = VALUES(high),
        low = VALUES(low),
        close = VALUES(close),
        pre_close = VALUES(pre_close),
        `change` = VALUES(`change`),
        pct_chg = VALUES(pct_chg),
        vol = VALUES(vol),
        amount = VALUES(amount)
    """
    
    try:
        with connection.cursor() as cursor:
            # 将DataFrame转换为元组列表
            data = df[['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
                      'pre_close', 'change', 'pct_chg', 'vol', 'amount']].values.tolist()
            # 批量插入
            cursor.executemany(insert_sql, data)
        connection.commit()
        print(f"✅ 成功插入/更新 {len(df)} 条指数数据")
    except Exception as e:
        print(f"❌ 插入数据失败: {e}")
        connection.rollback()

def main():
    """
    主函数：获取指数日线数据并写入数据库
    """
    print("=" * 60)
    print("更新指数日线数据")
    print("=" * 60)
    
    # 计算日期范围（最近100天）
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=120)).strftime('%Y%m%d')  # 多取20天确保足够数据
    
    print(f"日期范围: {start_date} ~ {end_date}")
    print(f"指数列表: {INDEX_CODES}")
    
    # 连接数据库
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败，退出程序")
        return
    
    try:
        # 创建表
        create_index_daily_table(connection)
        
        # 遍历指数列表，获取数据并写入数据库
        for ts_code in INDEX_CODES:
            df = get_index_daily(ts_code, start_date, end_date, limit=100)
            if not df.empty:
                insert_index_data(connection, df)
        
        print("\n" + "=" * 60)
        print("✅ 所有指数数据更新完成！")
        print("=" * 60)
        
    finally:
        close_connection(connection)

if __name__ == "__main__":
    main()
