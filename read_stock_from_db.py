#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
from mysql_connection import get_mysql_connection, close_connection

def read_stock_data_from_db(
    ts_code: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = None
) -> pd.DataFrame:
    """
    从数据库读取股票日线数据
    
    :param ts_code: 股票代码，如 '002801.SZ'，不传则查询所有
    :param start_date: 开始日期，格式 'YYYYMMDD'
    :param end_date: 结束日期，格式 'YYYYMMDD'
    :param limit: 返回数量限制
    :return: 包含股票数据的DataFrame
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库")
        return pd.DataFrame()
    
    try:
        cursor = conn.cursor()
        
        #读取每个股票最近30天的数据
        sql = "select * from (select  *, ROW_NUMBER() over(PARTITION by ts_code ORDER BY trade_date desc)  as rn from stock_daily_t ) t where rn < 31"
        params = []
        
        if ts_code:
            sql += " WHERE ts_code = %s"
            params.append(ts_code)
        
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        
        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        data = cursor.fetchall()
        
        df = pd.DataFrame(data, columns=columns)
        
        print(f"✅ 成功读取 {len(df)} 条记录")
        return df
    
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return pd.DataFrame()
    
    finally:
        close_connection(conn)

def get_stock_codes_from_db() -> list:
    """
    获取数据库中所有股票代码
    
    :return: 股票代码列表
    """
    conn = get_mysql_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor()
        sql = "SELECT DISTINCT ts_code FROM stock_daily_t ORDER BY ts_code"
        cursor.execute(sql)
        result = cursor.fetchall()
        
        # 提取股票代码列表
        codes = [row[0] for row in result]
        return codes
    
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return []
    
    finally:
        close_connection(conn)

def get_stock_summary(ts_code: str = None) -> pd.DataFrame:
    """
    获取股票统计摘要
    
    :param ts_code: 股票代码，不传则统计所有
    :return: 统计摘要DataFrame
    """
    conn = get_mysql_connection()
    if not conn:
        return pd.DataFrame()
    
    try:
        cursor = conn.cursor()
        
        sql = """
            SELECT 
                ts_code,
                COUNT(*) as total_days,
                MIN(trade_date) as first_date,
                MAX(trade_date) as last_date,
                AVG(close) as avg_close,
                MIN(low) as min_low,
                MAX(high) as max_high
            FROM stock_daily_t
        """
        
        params = []
        if ts_code:
            sql += " WHERE ts_code = %s"
            params.append(ts_code)
        
        sql += " GROUP BY ts_code ORDER BY ts_code"
        
        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        data = cursor.fetchall()
        
        df = pd.DataFrame(data, columns=columns)
        return df
    
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return pd.DataFrame()
    
    finally:
        close_connection(conn)

def main():
    """
    主函数：查询 stock_daily_t 表中所有记录
    """
    print("📊 查询 stock_daily_t 表中所有记录")
    print("=" * 120)

    # 查询所有记录
    print("\n📋 正在读取 stock_daily_t 表...")
    df = read_stock_data_from_db()

    if not df.empty:
        # 转换数值类型
        numeric_cols = ['open', 'high', 'low', 'close', 'vol', 'amount']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 按日期排序（从早到晚）
        df = df.sort_values('trade_date').reset_index(drop=True)

        print(f"\n✅ 成功读取 {len(df)} 条记录")
        print("\n📊 查询结果（前20条）:")
        print("=" * 120)

        # 显示表头
        header = f"{'序号':>4} {'交易日期':>10} {'股票代码':>12} {'开盘价':>10} {'最高价':>10} {'最低价':>10} {'收盘价':>10} {'涨跌幅':>10} {'成交量':>12} {'成交额':>15}"
        print(header)
        print("-" * 120)

        # 显示前20条数据
        display_df = df.head(20)
        for idx, row in display_df.iterrows():
            line = f"{idx + 1:>4} {row['trade_date']:>10} {row['ts_code']:>12} {row['open']:>10.2f} {row['high']:>10.2f} {row['low']:>10.2f} {row['close']:>10.2f} {row['pct_chg']:>10.2f} {row['vol']:>12.0f} {row['amount']:>15.2f}"
            print(line)

        if len(df) > 20:
            print(f"\n... 还有 {len(df) - 20} 条记录")

        # 显示数据统计
        print("\n📈 数据统计:")
        print("-" * 60)
        print(f"   股票代码数量: {df['ts_code'].nunique()}")
        print(f"   总记录数量: {len(df)}")
        print(f"   日期范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
        print(f"   收盘价范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
        print(f"   总成交额: {df['amount'].sum():,.2f}")
    else:
        print("⚠️ 表中没有数据或查询失败")

if __name__ == "__main__":
    main()
