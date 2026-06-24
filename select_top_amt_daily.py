#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
主要功能是从stock_daily_t表中查出最近一个交易日的数据，选出成交额(amount*1000)超过50亿的股票。
将股票代码保存到CSV文件中，文件名为50亿成额.csv。
新建一个名称为50亿成额加当日日期（如果当前时间为15点前则取前一天日期）的文件夹，
如果文件夹已存在则先删除再新建，将CSV文件放在该文件夹下。
"""

import os
import shutil
from datetime import datetime, timedelta

from mysql_connection import get_mysql_connection, close_connection


def get_trade_date() -> str:
    """获取交易日期，如果当前时间在0-15点之间则取前一天日期"""
    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 15:
        target_date = now - timedelta(days=1)
        return target_date.strftime('%Y%m%d')
    else:
        return now.strftime('%Y%m%d')


def get_top_amount_stocks(conn) -> list:
    """获取最近一个交易日成交额超过50亿的股票"""
    cursor = conn.cursor()
    
    # 查询最近一个交易日的数据
    query = """
    SELECT DISTINCT ts_code
    FROM stock_daily_t
    WHERE trade_date = (
        SELECT MAX(trade_date) FROM stock_daily_t
    )
    AND amount * 1000 > 5000000000
    ORDER BY ts_code
    """
    
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    
    return rows


def save_results_to_csv(results: list, output_dir: str):
    """将结果保存到CSV文件"""
    csv_path = os.path.join(output_dir, "50亿成额.csv")
    
    with open(csv_path, 'w', encoding='utf-8') as f:
        ts_codes = [row['ts_code'] for row in results]
        f.write(','.join(ts_codes))
    
    print(f"   ✅ 已保存 {len(results)} 只股票到: {csv_path}")


def main():
    print("=" * 60)
    print("📊 高成交额选股模型")
    print("=" * 60)
    
    trade_date = get_trade_date()
    print(f"\n⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 输出日期: {trade_date}")
    
    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return
    
    print("🔌 正在连接数据库: root@localhost:3306/stock_daily_db")
    print("✅ 数据库连接成功！")
    
    print("\n📋 步骤2: 查询高成交额股票...")
    results = get_top_amount_stocks(conn)
    print(f"   ✅ 找到 {len(results)} 只成交额超过50亿的股票")
    
    if results:
        print("\n📊 股票代码列表:")
        print("-" * 60)
        for i, row in enumerate(results, 1):
            print(f"{i:3d}. {row['ts_code']}")
    
    print("\n💾 步骤3: 保存结果到CSV文件...")
    folder_name = f"50亿成额{trade_date}"
    output_dir = os.path.join(os.getcwd(), folder_name)
    
    # 如果文件夹已存在则删除再新建
    if os.path.exists(output_dir):
        print(f"   📁 文件夹已存在，删除重建: {output_dir}")
        shutil.rmtree(output_dir)
    
    os.makedirs(output_dir)
    save_results_to_csv(results, output_dir)
    print(f"   ✅ 已保存到: {output_dir}")
    
    print(f"\n🎉 选股完成！共选出 {len(results)} 只股票")
    close_connection(conn)


if __name__ == "__main__":
    main()