#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""分析选股条件的严格程度"""

import pandas as pd
from mysql_connection import get_mysql_connection, close_connection


def main():
    conn = get_mysql_connection()
    if not conn:
        return
    
    cursor = conn.cursor()
    
    # 1. 统计满足成交额>5亿的股票数
    query = """
    SELECT COUNT(DISTINCT d.ts_code) as cnt
    FROM stock_daily_t d
    WHERE d.trade_date = '20260612' AND d.amount * 1000 > 500000000
    """
    cursor.execute(query)
    result = cursor.fetchone()
    print(f"满足成交额>5亿的股票数: {result['cnt']}")
    
    # 2. 统计满足涨幅>5%的股票数
    query2 = """
    SELECT COUNT(DISTINCT d.ts_code) as cnt
    FROM (
        SELECT ts_code, close, trade_date,
               LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) as prev_close
        FROM stock_daily_t
        WHERE trade_date >= '20260610'
    ) t
    WHERE trade_date = '20260612' AND prev_close > 0
      AND (close - prev_close) / prev_close > 0.05
    """
    cursor.execute(query2)
    result2 = cursor.fetchone()
    print(f"满足涨幅>5%的股票数: {result2['cnt']}")
    
    # 3. 统计满足120天波动幅度<=35%的股票数
    query3 = """
    SELECT COUNT(*) as cnt
    FROM (
        SELECT ts_code, 
               MAX(close) as max_close, 
               MIN(close) as min_close
        FROM stock_daily_t
        WHERE trade_date >= '20260105'
        GROUP BY ts_code
        HAVING COUNT(*) >= 120
    ) t
    WHERE min_close > 0 AND (max_close - min_close) / min_close <= 0.35
    """
    cursor.execute(query3)
    result3 = cursor.fetchone()
    print(f"满足120天波动幅度<=35%的股票数: {result3['cnt']}")
    
    # 4. 统计满足收盘价突破前119天最高收盘价的股票数
    query4 = """
    SELECT COUNT(DISTINCT ts_code) as cnt
    FROM (
        SELECT ts_code, trade_date, close,
               MAX(close) OVER (PARTITION BY ts_code ORDER BY trade_date 
                               ROWS BETWEEN 119 PRECEDING AND 1 PRECEDING) as max_prev_119
        FROM stock_daily_t
        WHERE trade_date >= '20260105'
    ) t
    WHERE trade_date = '20260612' AND max_prev_119 IS NOT NULL
      AND close > max_prev_119
    """
    cursor.execute(query4)
    result4 = cursor.fetchone()
    print(f"满足收盘价突破前119天最高收盘价的股票数: {result4['cnt']}")
    
    close_connection(conn)


if __name__ == "__main__":
    main()
