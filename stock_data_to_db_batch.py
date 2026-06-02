#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
from datetime import datetime, timedelta
from tracemalloc import start
""" 取文件夹股票列表csv中的股票代码， 批量从tushare.pro网站调取日线级别的交易数据，保存到stock_daily_t表里 """

# Tushare调用频率限制配置
CALL_INTERVAL = 2  # 每次调用间隔（秒）

# 上次调用时间戳
last_call_time = 0

def rate_limit():
    """
    Tushare调用频率限制器
    确保每次调用间隔至少CALL_INTERVAL秒
    """
    global last_call_time
    
    current_time = time.time()
    elapsed = current_time - last_call_time
    
    # 如果距离上次调用不足CALL_INTERVAL秒，等待
    if elapsed < CALL_INTERVAL:
        wait_time = CALL_INTERVAL - elapsed
        print(f"      ⏳ 频率限制，等待 {wait_time:.1f} 秒...")
        time.sleep(wait_time)
    
    # 更新上次调用时间
    last_call_time = time.time()

# 引用自定义模块
from mysql_connection import get_mysql_connection, close_connection
from read_stocks_csv import read_multiple_stock_files
from tushare_get_daily import get_stock_daily

def get_stock_filter_info(conn, stock_codes: list) -> dict:
    """
    从stock_info_t表获取股票过滤信息（名称和总市值）

    :param conn: 数据库连接
    :param stock_codes: 股票代码列表
    :return: 字典，key为ts_code，value为包含stock_name和total_mv的字典
    """
    filter_info = {}
    if not stock_codes:
        return filter_info

    try:
        cursor = conn.cursor()
        placeholders = ','.join(['%s'] * len(stock_codes))
        sql = f"""
            SELECT ts_code, stock_name, total_mv
            FROM stock_info_t
            WHERE ts_code IN ({placeholders})
        """
        cursor.execute(sql, tuple(stock_codes))
        results = cursor.fetchall()

        for row in results:
            filter_info[row['ts_code']] = {
                'stock_name': row.get('stock_name', ''),
                'total_mv': row.get('total_mv', 0) or 0
            }
    except Exception as e:
        print(f"⚠️ 获取股票过滤信息失败: {e}")

    return filter_info

def should_filter_stock(ts_code: str, stock_filter_info: dict) -> tuple:
    """
    检查股票是否应该被过滤

    :param ts_code: 股票代码
    :param stock_filter_info: 股票过滤信息字典
    :return: (是否过滤, 过滤原因)
    """
    if ts_code not in stock_filter_info:
        return (False, '')

    info = stock_filter_info[ts_code]
    stock_name = info.get('stock_name', '')
    total_mv = float(info.get('total_mv', 0) or 0)

    if 'ST' in stock_name or '*ST' in stock_name or 'S*ST' in stock_name or 'SST' in stock_name:
        return (True, f'ST股票 ({stock_name})')

    if total_mv < 5e9:
        return (True, f'总市值 {total_mv/1e8:.2f}亿 < 50亿')

    return (False, '')

def insert_stock_data(connection, df):
    print(f"      📡 插入数据参数: {df}")
    
    """
    将股票数据插入数据库
    
    :param connection: 数据库连接对象
    :param df: 包含股票数据的DataFrame
    :return: 成功插入的记录数
    """
    if df.empty:
        return 0
    
    # 构建插入SQL
    insert_sql = """
    INSERT INTO stock_daily_t (
        ts_code, trade_date, open, high, low, close, 
        pre_close, `change`, pct_chg, vol, amount
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        open = VALUES(open),
        high = VALUES(high),
        low = VALUES(low),
        close = VALUES(close),
        pre_close = VALUES(pre_close),
        `change` = VALUES(`change`),
        pct_chg = VALUES(pct_chg),
        vol = VALUES(vol),
        amount = VALUES(amount),
        updated_at = CURRENT_TIMESTAMP
    """
    
    try:
        with connection.cursor() as cursor:
            # 将DataFrame转换为元组列表
            data = df[[
                'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
                'pre_close', 'change', 'pct_chg', 'vol', 'amount'
            ]].values.tolist()
            
            # 批量插入
            cursor.executemany(insert_sql, data)
            connection.commit()
            
            return cursor.rowcount
    
    except Exception as e:
        print(f"❌ 插入数据失败: {e}")
        connection.rollback()
        return 0

def main():
    """
    主函数：读取股票代码，获取日线数据，写入数据库
    """
    print("=" * 60)
    print("📊 股票数据批量导入程序")
    print("=" * 60)
    
    # 1. 获取数据库连接
    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库，程序退出")
        sys.exit(1)
    
    try:
        # 2. 读取股票代码
        print("\n📋 步骤2: 读取股票代码...")
        stock_codes = read_multiple_stock_files()
        if not stock_codes:
            print("❌ 没有找到股票代码，程序退出")
            return
        
        print(f"   共读取到 {len(stock_codes)} 个股票代码")

        # 3. 获取股票过滤信息（名称和总市值）
        print("\n🔍 步骤3: 获取股票过滤信息...")
        stock_filter_info = get_stock_filter_info(conn, stock_codes)
        print(f"   从数据库获取到 {len(stock_filter_info)} 只股票的过滤信息")

        # 4. 计算日期范围（最近30个交易日）
        #end_date = datetime.now().strftime('%Y%m%d')
        #start_date = (datetime.now() - timedelta(days=100)).strftime('%Y%m%d')  # 取100天确保包含30个交易日
        
        end_date='20260522'
        start_date='20250101'

        print(f"\n📅 查询日期范围: {start_date} ~ {end_date}")

        # 5. 清空数据表
        print("\n🗑️ 步骤4: 清空数据表...")
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM stock_daily_t")
                deleted_count = cursor.rowcount
                conn.commit()
                print(f"   ✅ 已清空数据表，删除了 {deleted_count} 条记录")
        except Exception as e:
            print(f"   ❌ 清空数据表失败: {e}")
            print("   程序退出")
            sys.exit()
        
        # 6. 遍历股票代码，获取数据并写入数据库
        total_inserted = 0
        total_updated = 0
        total_filtered = 0

        print("\n📈 步骤5: 批量获取股票数据并写入数据库...")
        for i, ts_code in enumerate(stock_codes, 1):
            should_filter, filter_reason = should_filter_stock(ts_code, stock_filter_info)
            if should_filter:
                print(f"\n   [{i}/{len(stock_codes)}] 跳过 {ts_code}: {filter_reason}")
                total_filtered += 1
                continue

            print(f"\n   [{i}/{len(stock_codes)}] 正在处理: {ts_code}")
            
            try:
                # 频率限制检查
                rate_limit()
                
                # 获取日线数据
             
                df = get_stock_daily(ts_code, start_date, end_date)
                
                if df.empty:
                    print(f"      ⚠️ 未获取到数据")
                    continue
                
                # 确保股票代码格式正确
                df['ts_code'] = ts_code
                
                # 插入数据库
                rowcount = insert_stock_data(conn, df)
                
                if rowcount > 0:
                    print(f"      ✅ 成功处理 {len(df)} 条记录")
                    total_inserted += len(df)
                else:
                    print(f"      ⚠️ 数据未变化")
                    total_updated += len(df)
            
            except Exception as e:
                print(f"      ❌ 处理失败: {e}")
        
        # 7. 输出统计结果
        print("\n" + "=" * 60)
        print("📊 导入完成！")
        print("=" * 60)
        print(f"📈 处理股票数量: {len(stock_codes)}")
        print(f"🚫 过滤股票数量: {total_filtered}")
        print(f"✅ 新增/更新记录: {total_inserted}")
        print(f"🔄 未变化记录: {total_updated}")
        print("=" * 60)
        
    finally:
        # 关闭数据库连接
        close_connection(conn)

if __name__ == "__main__":
    main()