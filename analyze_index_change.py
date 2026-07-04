#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from mysql_connection import get_mysql_connection, close_connection

def create_table(conn):
    cursor = conn.cursor()
    create_sql = """
    CREATE TABLE IF NOT EXISTS `stock_index_future_delta_t` (
      `id` bigint NOT NULL AUTO_INCREMENT,
      `trade_date` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期',
      `idx_fu_avg_before_fu_diff` decimal(10,2) DEFAULT NULL COMMENT '三大指数期前一日现差算术均值',
      `idx_fu_avg_fu_diff` decimal(10,2) DEFAULT NULL COMMENT '三大指数期现差当日算术均值',
      `idx_fu_avg_diff_delta` decimal(10,2) DEFAULT NULL COMMENT '三大指数期现差边际变化算术均值',
      `idx_nextdate_diff` decimal(10,2) DEFAULT NULL COMMENT '中证全指次日涨跌幅',
      PRIMARY KEY (`id`),
      UNIQUE KEY `uk_ts_date` (`trade_date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数期现差变化数据表';
    """
    cursor.execute(create_sql)
    conn.commit()
    print("✅ 表结构创建/检查完成")

def truncate_table(conn):
    cursor = conn.cursor()
    truncate_sql = "TRUNCATE TABLE stock_index_future_delta_t;"
    cursor.execute(truncate_sql)
    conn.commit()
    print("✅ 表数据已清空")

def insert_data(conn):
    cursor = conn.cursor()
    insert_sql = """
    INSERT INTO stock_index_future_delta_t (trade_date, idx_fu_avg_before_fu_diff, idx_fu_avg_fu_diff, idx_fu_avg_diff_delta, idx_nextdate_diff)
    SELECT
        t5.trade_date,
        t5.idx_fu_avg_before_fu_diff,
        t5.idx_fu_avg_fu_diff,
        t5.idx_fu_avg_diff_delta,
        t6.next_close - t6.next_open as idx_nextdate_diff
    FROM
    (SELECT
        trade_date,
        '三大期现差边际变化算术均值' as idx_fu_name,
        sum(before_fu_diff)/3 as idx_fu_avg_before_fu_diff,
        sum(fu_diff)/3 as idx_fu_avg_fu_diff,
        SUM(fu_diff - before_fu_diff) / 3 as idx_fu_avg_diff_delta
    FROM (
        SELECT
            trade_date,
            idx_name,
            fu_diff,
            LAG(fu_diff, 1, 0) OVER(PARTITION BY idx_name ORDER BY trade_date) as before_fu_diff
        FROM (
            SELECT
                t2.idx_name,
                t2.trade_date,
                t1.idx_fu_close - t2.idx_close as fu_diff
            FROM (
                SELECT
                    CASE WHEN ts_code='ICL.CFX' THEN '中证500'
                         WHEN ts_code='IML.CFX' THEN '中证1000'
                         WHEN ts_code='IFL.CFX' THEN '沪深300'
                         ELSE NULL END as idx_fu_name,
                    close as idx_fu_close,
                    trade_date
                FROM stock_index_future_daily_t
                WHERE trade_date >= '20241101'
            ) t1
            JOIN (
                SELECT
                    CASE WHEN ts_code='000905.SH' THEN '中证500'
                         WHEN ts_code='000852.SH' THEN '中证1000'
                         WHEN ts_code='000300.SH' THEN '沪深300'
                         ELSE NULL END as idx_name,
                    close as idx_close,
                    trade_date
                FROM stock_index_daily_t
                WHERE trade_date >= '20241101'
            ) t2 ON t1.trade_date = t2.trade_date AND t1.idx_fu_name = t2.idx_name
        ) t3
    ) t4
    GROUP BY trade_date
    ) t5
    JOIN (
        SELECT
            trade_date,
            lead(open, 1, 0) OVER(PARTITION BY ts_code ORDER BY trade_date) as next_open,
            lead(close, 1, 0) OVER(PARTITION BY ts_code ORDER BY trade_date) as next_close
        FROM stock_index_daily_t
        WHERE trade_date >= '20241101' AND ts_code='000985.CSI'
    ) t6 ON t5.trade_date = t6.trade_date;
    """
    cursor.execute(insert_sql)
    affected = cursor.rowcount
    conn.commit()
    print(f"✅ 成功插入 {affected} 条数据")

def verify_data(conn):
    cursor = conn.cursor()
    query = "SELECT COUNT(*) FROM stock_index_future_delta_t"
    cursor.execute(query)
    count = cursor.fetchone()
    print(f"\n📊 数据表统计：")
    print(f"   总记录数: {count['COUNT(*)']}")

    query = "SELECT MIN(trade_date), MAX(trade_date) FROM stock_index_future_delta_t"
    cursor.execute(query)
    date_range = cursor.fetchone()
    print(f"   日期范围: {date_range['MIN(trade_date)']} ~ {date_range['MAX(trade_date)']}")

    query = """
    SELECT trade_date, idx_fu_avg_before_fu_diff, idx_fu_avg_fu_diff, idx_fu_avg_diff_delta, idx_nextdate_diff
    FROM stock_index_future_delta_t
    ORDER BY trade_date DESC
    LIMIT 5
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    print(f"\n📋 最近5条数据：")
    print(f"{'日期':<12} {'前一日期现差':<16} {'当日期现差':<14} {'边际变化':<14} {'次日涨跌幅':<14}")
    print(f"{'----':<12} {'------------':<16} {'----------':<14} {'--------':<14} {'----------':<14}")
    for row in rows:
        print(f"{row['trade_date']:<12} {row['idx_fu_avg_before_fu_diff']:<16} {row['idx_fu_avg_fu_diff']:<14} {row['idx_fu_avg_diff_delta']:<14} {row['idx_nextdate_diff']:<14}")

def main():
    conn = get_mysql_connection()
    if not conn:
        print("数据库连接失败")
        sys.exit(1)

    try:
        print("📊 开始执行指数期现差变化分析...")
        print("-" * 50)

        print("\n1. 检查/创建表结构...")
        create_table(conn)

        print("\n2. 清空表数据...")
        truncate_table(conn)

        print("\n3. 插入数据...")
        insert_data(conn)

        print("\n4. 验证数据...")
        verify_data(conn)

        print("\n" + "=" * 50)
        print("✅ 分析完成！")

    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()