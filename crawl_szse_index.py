#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
爬取深交所首页主板和创业板的数据
数据源：
1. akshare: 获取总市值、流通市值、上市公司数、总成交金额
2. 乐咕乐股(legulegu.com): 获取平均市盈率
输出为CSV文件，并写入数据库表 szse_market_summary_t
"""

import os
import shutil
import pandas as pd
import requests
from datetime import datetime, timedelta
import re
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


def get_szse_data():
    """使用akshare获取深交所市场总貌数据"""
    import akshare as ak

    trade_date = get_trade_date()
    df = ak.stock_szse_summary(date=trade_date)
    return df


def get_pe_ratio_from_legulegu(board_type='创业板'):
    """从乐咕乐股获取平均市盈率数据"""
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    
    # 乐咕乐股的市盈率页面
    pe_urls = {
        '创业板': 'https://legulegu.com/stockdata/cybPE',
        '深证主板': 'https://legulegu.com/stockdata/shenzhenPE',
        '上证主板': 'https://legulegu.com/stockdata/shanghaiPE',
        '科创板': 'https://legulegu.com/stockdata/ke-chuang-ban-pe',
    }
    
    url = pe_urls.get(board_type)
    if not url:
        return None
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            # 解析页面获取平均市盈率
            # HTML格式: <span class="metric-label">平均市盈率</span> <span class="metric-value">54.14</span>
            pattern = r'<span class="metric-label[^>]*>平均市盈率</span>\s*<span class="metric-value">([\d.]+)</span>'
            match = re.search(pattern, response.text)
            if match:
                pe_ratio = float(match.group(1))
                return pe_ratio
    except Exception as e:
        print(f"   ❌ 获取{board_type}市盈率失败: {e}")
    
    return None


def parse_board_data(df, board_name, pe_ratio=None):
    """从总貌数据中提取指定板块的数据"""
    row = df[df['证券类别'] == board_name]
    if row.empty:
        return None

    row = row.iloc[0]
    data = {
        '总市值（亿元）': round(row['总市值'] / 1e8, 2) if pd.notna(row['总市值']) else None,
        '流通市值（亿元）': round(row['流通市值'] / 1e8, 2) if pd.notna(row['流通市值']) else None,
        '上市公司数': int(row['数量']) if pd.notna(row['数量']) else None,
        '总成交金额（亿元）': round(row['成交金额'] / 1e8, 2) if pd.notna(row['成交金额']) else None,
    }
    
    # 添加平均市盈率
    if pe_ratio:
        data['平均市盈率'] = pe_ratio
    
    return data


def create_table_if_not_exists(conn):
    """创建深交所市场总貌数据表（如果表不存在）"""
    cursor = conn.cursor()
    
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS szse_market_summary_t (
        trade_date VARCHAR(8) NOT NULL COMMENT '交易日期',
        board_type VARCHAR(20) NOT NULL COMMENT '板块类型（主板A股、创业板A股等）',
        total_mv DECIMAL(15,2) COMMENT '总市值（亿元）',
        float_mv DECIMAL(15,2) COMMENT '流通市值（亿元）',
        company_count INT COMMENT '上市公司数',
        total_amount DECIMAL(15,2) COMMENT '总成交金额（亿元）',
        avg_pe_ratio DECIMAL(10,2) COMMENT '平均市盈率',
        update_time DATETIME COMMENT '更新时间',
        PRIMARY KEY (trade_date, board_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='深交所市场总貌数据表'
    """
    
    cursor.execute(create_table_sql)
    conn.commit()
    cursor.close()
    print("   ✅ 数据表 szse_market_summary_t 已准备就绪")


def insert_data_to_db(conn, trade_date, board_type, data):
    """将数据插入或更新到数据库"""
    cursor = conn.cursor()
    
    # 使用 REPLACE INTO 或 INSERT ... ON DUPLICATE KEY UPDATE
    insert_sql = """
    INSERT INTO szse_market_summary_t 
    (trade_date, board_type, total_mv, float_mv, company_count, total_amount, avg_pe_ratio, update_time)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
    total_mv = VALUES(total_mv),
    float_mv = VALUES(float_mv),
    company_count = VALUES(company_count),
    total_amount = VALUES(total_amount),
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
        data.get('总成交金额（亿元）'),
        data.get('平均市盈率'),
        now
    ))
    
    conn.commit()
    cursor.close()
    print(f"   ✅ {board_type} 数据已写入数据库")


def main():
    print("=" * 60)
    print("📊 深交所首页数据爬虫")
    print("=" * 60)

    trade_date = get_trade_date()
    print(f"\n⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 交易日期: {trade_date}")

    print("\n📊 步骤1: 获取深交所市场总貌数据...")
    df = get_szse_data()
    print(f"   ✅ 获取到 {len(df)} 条记录")

    print("\n📊 步骤2: 获取平均市盈率数据...")
    gem_pe = get_pe_ratio_from_legulegu('创业板')
    main_pe = get_pe_ratio_from_legulegu('深证主板')
    
    if gem_pe:
        print(f"   ✅ 创业板平均市盈率: {gem_pe}")
    if main_pe:
        print(f"   ✅ 深证主板平均市盈率: {main_pe}")

    # 提取主板和创业板数据
    main_board = parse_board_data(df, '主板A股', main_pe)
    gem_board = parse_board_data(df, '创业板A股', gem_pe)

    if main_board:
        print("\n📊 主板数据:")
        for k, v in main_board.items():
            print(f"   {k}: {v}")

    if gem_board:
        print("\n📊 创业板数据:")
        for k, v in gem_board.items():
            print(f"   {k}: {v}")

    # 步骤3: 写入数据库
    print("\n📊 步骤3: 写入数据库...")
    conn = get_mysql_connection()
    if conn:
        create_table_if_not_exists(conn)
        
        if main_board:
            insert_data_to_db(conn, trade_date, '主板A股', main_board)
        if gem_board:
            insert_data_to_db(conn, trade_date, '创业板A股', gem_board)
        
        close_connection(conn)
    else:
        print("   ❌ 数据库连接失败，跳过数据库写入")

    # 步骤4: 保存为CSV
    folder_name = f"深交所首页数据{trade_date}"
    output_dir = os.path.join(os.getcwd(), folder_name)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # 保存主板数据
    if main_board:
        df_main = pd.DataFrame([main_board], index=['主板A股'])
        csv_path = os.path.join(output_dir, "主板.csv")
        df_main.to_csv(csv_path, encoding='utf-8-sig')
        print(f"\n💾 主板数据已保存: {csv_path}")

    # 保存创业板数据
    if gem_board:
        df_gem = pd.DataFrame([gem_board], index=['创业板A股'])
        csv_path = os.path.join(output_dir, "创业板.csv")
        df_gem.to_csv(csv_path, encoding='utf-8-sig')
        print(f"💾 创业板数据已保存: {csv_path}")

    # 保存完整数据
    full_csv_path = os.path.join(output_dir, "市场总貌.csv")
    df_save = df.copy()
    df_save['总市值（亿元）'] = df_save['总市值'].apply(lambda x: round(x / 1e8, 2) if pd.notna(x) else None)
    df_save['流通市值（亿元）'] = df_save['流通市值'].apply(lambda x: round(x / 1e8, 2) if pd.notna(x) else None)
    df_save['成交金额（亿元）'] = df_save['成交金额'].apply(lambda x: round(x / 1e8, 2) if pd.notna(x) else None)
    df_save.to_csv(full_csv_path, index=False, encoding='utf-8-sig')
    print(f"💾 完整市场总貌数据已保存: {full_csv_path}")

    print(f"\n🎉 爬取完成！数据保存在: {output_dir}")


if __name__ == "__main__":
    main()