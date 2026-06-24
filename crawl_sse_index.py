#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
爬取上交所首页主板和科创板的数据
数据源：akshare (stock_sse_summary)
输出为CSV文件，并写入数据库表 sse_market_summary_t
"""

import os
import shutil
import pandas as pd
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


def get_sse_data():
    """使用akshare获取上交所市场总貌数据"""
    import akshare as ak
    
    df = ak.stock_sse_summary()
    return df


def parse_board_data(df, board_type):
    """从总貌数据中提取指定板块的数据"""
    # 数据格式: 项目, 股票, 主板, 科创板
    # board_type可以是: 主板, 科创板, 股票(总貌)
    
    data = {}
    
    # 提取各项数据
    for row in df.itertuples():
        item = row.项目
        
        # 根据板块类型选择对应的列
        if board_type == '主板':
            number = row.主板
        elif board_type == '科创板':
            number = row.科创板
        elif board_type == '总貌':
            number = row.股票
        else:
            continue
        
        # 映射数据项
        if '上市公司' in item:
            data['上市公司数'] = int(number) if pd.notna(number) else None
        elif '总市值' in item:
            data['总市值（亿元）'] = float(number) if pd.notna(number) else None
        elif '流通市值' in item:
            data['流通市值（亿元）'] = float(number) if pd.notna(number) else None
        elif '平均市盈率' in item:
            data['平均市盈率'] = float(number) if pd.notna(number) else None
        elif '总股本' in item:
            data['总股本（亿股）'] = float(number) if pd.notna(number) else None
        elif '流通股本' in item:
            data['流通股本（亿股）'] = float(number) if pd.notna(number) else None
        elif '上市股票' in item:
            data['上市股票数'] = int(number) if pd.notna(number) else None
    
    return data


def create_table_if_not_exists(conn):
    """创建上交所市场总貌数据表（如果表不存在）"""
    cursor = conn.cursor()
    
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS sse_market_summary_t (
        trade_date VARCHAR(8) NOT NULL COMMENT '交易日期',
        board_type VARCHAR(20) NOT NULL COMMENT '板块类型（主板、科创板等）',
        total_mv DECIMAL(15,2) COMMENT '总市值（亿元）',
        float_mv DECIMAL(15,2) COMMENT '流通市值（亿元）',
        company_count INT COMMENT '上市公司数',
        stock_count INT COMMENT '上市股票数',
        total_shares DECIMAL(15,2) COMMENT '总股本（亿股）',
        float_shares DECIMAL(15,2) COMMENT '流通股本（亿股）',
        avg_pe_ratio DECIMAL(10,2) COMMENT '平均市盈率',
        update_time DATETIME COMMENT '更新时间',
        PRIMARY KEY (trade_date, board_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='上交所市场总貌数据表'
    """
    
    cursor.execute(create_table_sql)
    conn.commit()
    cursor.close()
    print("   ✅ 数据表 sse_market_summary_t 已准备就绪")


def insert_data_to_db(conn, trade_date, board_type, data):
    """将数据插入或更新到数据库"""
    cursor = conn.cursor()
    
    # 使用 INSERT ... ON DUPLICATE KEY UPDATE
    insert_sql = """
    INSERT INTO sse_market_summary_t 
    (trade_date, board_type, total_mv, float_mv, company_count, stock_count, 
     total_shares, float_shares, avg_pe_ratio, update_time)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
    total_mv = VALUES(total_mv),
    float_mv = VALUES(float_mv),
    company_count = VALUES(company_count),
    stock_count = VALUES(stock_count),
    total_shares = VALUES(total_shares),
    float_shares = VALUES(float_shares),
    avg_pe_ratio = VALUES(avg_pe_ratio),
    update_time = VALUES(update_time)
    """
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute(insert_sql, (
        trade_date,
        board_type,
        data.get('总市值（亿元）'),
        data.get('流通市值（亿元）'),
        data.get('上市公司数'),
        data.get('上市股票数'),
        data.get('总股本（亿股）'),
        data.get('流通股本（亿股）'),
        data.get('平均市盈率'),
        now
    ))
    
    conn.commit()
    cursor.close()
    print(f"   ✅ {board_type} 数据已写入数据库")


def main():
    print("=" * 60)
    print("📊 上交所首页数据爬虫")
    print("=" * 60)

    trade_date = get_trade_date()
    print(f"\n⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 交易日期: {trade_date}")

    print("\n📊 步骤1: 获取上交所市场总貌数据...")
    df = get_sse_data()
    print(f"   ✅ 获取到 {len(df)} 条记录")
    print(f"   数据列: {df.columns.tolist()}")

    # 提取主板和科创板数据
    main_board = parse_board_data(df, '主板')
    gem_board = parse_board_data(df, '科创板')  # 科创板
    total_board = parse_board_data(df, '总貌')

    if main_board:
        print("\n📊 主板数据:")
        for k, v in main_board.items():
            print(f"   {k}: {v}")

    if gem_board:
        print("\n📊 科创板数据:")
        for k, v in gem_board.items():
            print(f"   {k}: {v}")

    if total_board:
        print("\n📊 总貌数据:")
        for k, v in total_board.items():
            print(f"   {k}: {v}")

    # 步骤2: 写入数据库
    print("\n📊 步骤2: 写入数据库...")
    conn = get_mysql_connection()
    if conn:
        create_table_if_not_exists(conn)
        
        if main_board:
            insert_data_to_db(conn, trade_date, '主板', main_board)
        if gem_board:
            insert_data_to_db(conn, trade_date, '科创板', gem_board)
        if total_board:
            insert_data_to_db(conn, trade_date, '总貌', total_board)
        
        close_connection(conn)
    else:
        print("   ❌ 数据库连接失败，跳过数据库写入")

    # 步骤3: 保存为CSV
    print("\n📊 步骤3: 保存为CSV...")
    folder_name = f"上交所首页数据{trade_date}"
    output_dir = os.path.join(os.getcwd(), folder_name)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # 保存主板数据
    if main_board:
        df_main = pd.DataFrame([main_board], index=['主板'])
        csv_path = os.path.join(output_dir, "主板.csv")
        df_main.to_csv(csv_path, encoding='utf-8-sig')
        print(f"   💾 主板数据已保存: {csv_path}")

    # 保存科创板数据
    if gem_board:
        df_gem = pd.DataFrame([gem_board], index=['科创板'])
        csv_path = os.path.join(output_dir, "科创板.csv")
        df_gem.to_csv(csv_path, encoding='utf-8-sig')
        print(f"   💾 科创板数据已保存: {csv_path}")

    # 保存总貌数据
    if total_board:
        df_total = pd.DataFrame([total_board], index=['总貌'])
        csv_path = os.path.join(output_dir, "总貌.csv")
        df_total.to_csv(csv_path, encoding='utf-8-sig')
        print(f"   💾 总貌数据已保存: {csv_path}")

    # 保存完整数据
    full_csv_path = os.path.join(output_dir, "市场总貌.csv")
    df.to_csv(full_csv_path, index=False, encoding='utf-8-sig')
    print(f"   💾 完整市场总貌数据已保存: {full_csv_path}")

    print(f"\n🎉 爬取完成！数据保存在: {output_dir}")


if __name__ == "__main__":
    main()