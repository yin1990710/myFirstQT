#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
更新 stock_daily_factor_t 表中的股票技术面因子数据。

数据来源：tushare stk_factor 接口（按交易日批量获取全市场技术因子）。
表结构：以 ts_code + trade_date 为主键，字段对齐 stk_factor 默认返回字段。
更新策略：存在则更新（INSERT ... ON DUPLICATE KEY UPDATE），不存在则新增。
分页说明：stk_factor 单次最多返回约 2000 条记录，按 limit/offset 分页拉取。

用法:
    python update_stock_daily_factor.py              # 默认更新最近一个交易日
    python update_stock_daily_factor.py 20260901      # 更新指定交易日
    python update_stock_daily_factor.py 20260901 20260902  # 更新日期区间
"""

import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# stk_factor 单次拉取上限与分页大小
PAGE_SIZE = 2000
# 分页请求间隔（秒），避免触发接口限频
PAGE_SLEEP = 0.35

# stk_factor 默认返回字段，与表列保持一致
FACTOR_FIELDS = [
    "ts_code", "trade_date",
    "close", "open", "high", "low", "pre_close", "change", "pct_change",
    "vol", "amount", "adj_factor",
    "open_hfq", "open_qfq", "close_hfq", "close_qfq",
    "high_hfq", "high_qfq", "low_hfq", "low_qfq",
    "pre_close_hfq", "pre_close_qfq",
    "macd_dif", "macd_dea", "macd",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower",
    "cci"
]

# 表中与 FACTOR_FIELDS 对齐的列名（MySQL 关键字需反引号）
TABLE_COLUMNS = [
    "ts_code", "trade_date",
    "`close`", "`open`", "`high`", "`low`", "pre_close", "`change`", "pct_change",
    "vol", "amount", "adj_factor",
    "open_hfq", "open_qfq", "close_hfq", "close_qfq",
    "high_hfq", "high_qfq", "low_hfq", "low_qfq",
    "pre_close_hfq", "pre_close_qfq",
    "macd_dif", "macd_dea", "macd",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower",
    "cci"
]

# FACTOR_FIELDS 中的字段名 -> TABLE_COLUMNS 中的列名映射（用于拼装插入数据）
FIELD_TO_COLUMN = dict(zip(FACTOR_FIELDS, TABLE_COLUMNS))

# 数值列注释（建表用）
COLUMN_COMMENTS = {
    "close": "当日收盘价",
    "open": "当日开盘价",
    "high": "当日最高价",
    "low": "当日最低价",
    "pre_close": "前一日收盘价",
    "change": "当日涨跌额",
    "pct_change": "当日涨跌幅(%)",
    "vol": "成交量(手)",
    "amount": "成交额(千元)",
    "adj_factor": "复权因子",
    "open_hfq": "后复权开盘价",
    "open_qfq": "前复权开盘价",
    "close_hfq": "后复权收盘价",
    "close_qfq": "前复权收盘价",
    "high_hfq": "后复权最高价",
    "high_qfq": "前复权最高价",
    "low_hfq": "后复权最低价",
    "low_qfq": "前复权最低价",
    "pre_close_hfq": "后复权前收盘价",
    "pre_close_qfq": "前复权前收盘价",
    "macd_dif": "MACD-DIF",
    "macd_dea": "MACD-DEA",
    "macd": "MACD",
    "kdj_k": "KDJ-K",
    "kdj_d": "KDJ-D",
    "kdj_j": "KDJ-J",
    "rsi_6": "RSI-6",
    "rsi_12": "RSI-12",
    "rsi_24": "RSI-24",
    "boll_upper": "BOLL上轨",
    "boll_mid": "BOLL中轨",
    "boll_lower": "BOLL下轨",
    "cci": "CCI顺势指标",
}


def get_latest_trade_date():
    """
    获取最近一个交易日日期。
    当前时间 0-15 点取前一日，再通过交易日历向前回溯到最近一个开市日。

    :return: 日期字符串 YYYYMMDD
    """
    now = datetime.now()
    if 0 <= now.hour < 15:
        end = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        end = now.strftime('%Y%m%d')
    start = (now - timedelta(days=15)).strftime('%Y%m%d')

    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return end
    df = df[df['is_open'] == 1]
    if df.empty:
        return end
    return sorted(df['cal_date'].tolist())[-1]


def get_trade_dates(start_date, end_date):
    """获取日期范围内的交易日列表"""
    df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return []
    df = df[df['is_open'] == 1]
    return sorted(df['cal_date'].tolist())


def get_valid_stock_codes(conn):
    """
    从 stock_info_t 表读取所有有效股票代码（去除 ST 股和北证股）。

    :param conn: 数据库连接
    :return: 有效股票代码集合
    """
    sql = """
        SELECT ts_code
        FROM stock_info_t
        WHERE ts_code IS NOT NULL
          AND ts_code NOT LIKE '%.BJ'
          AND stock_name NOT LIKE '%ST%'
        GROUP BY ts_code
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    if rows and isinstance(rows[0], dict):
        return {row['ts_code'] for row in rows}
    return {row[0] for row in rows}


def create_table_if_not_exists(conn):
    """
    创建 stock_daily_factor_t 表（如已存在则不重建）。
    主键为 (ts_code, trade_date)，字段对齐 stk_factor 默认返回字段。
    """
    col_defs = []
    for field in FACTOR_FIELDS:
        if field in ("ts_code", "trade_date"):
            continue
        comment = COLUMN_COMMENTS.get(field, field)
        col_defs.append(f"    {FIELD_TO_COLUMN[field]:<16} DOUBLE DEFAULT NULL COMMENT '{comment}'")

    col_defs_sql = ",\n".join(col_defs)

    create_sql = f"""
        CREATE TABLE IF NOT EXISTS stock_daily_factor_t (
            ts_code          VARCHAR(16)  NOT NULL COMMENT '股票代码',
            trade_date       VARCHAR(8)   NOT NULL COMMENT '交易日期',
{col_defs_sql},
            PRIMARY KEY (ts_code, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票每日技术面因子'
    """
    with conn.cursor() as cursor:
        cursor.execute(create_sql)
    conn.commit()


def fetch_stk_factor_page(trade_date, offset):
    """按交易日 + 偏移量分页获取全市场技术因子数据（单次 API 调用）"""
    return pro.stk_factor(trade_date=trade_date, limit=PAGE_SIZE, offset=offset)


def fetch_stk_factor_all(trade_date):
    """
    分页拉取指定交易日全市场的技术因子数据，直到无更多记录。

    :param trade_date: 交易日 YYYYMMDD
    :return: 合并后的 DataFrame（可能为空）
    """
    frames = []
    offset = 0
    while True:
        df = fetch_stk_factor_page(trade_date, offset)
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(PAGE_SLEEP)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def upsert_stk_factor(conn, df):
    """
    按 (ts_code, trade_date) 主键更新入库。
    存在则更新，不存在则新增（INSERT ... ON DUPLICATE KEY UPDATE）。

    :param conn: 数据库连接
    :param df: 包含技术因子数据的 DataFrame
    :return: 受影响的记录数
    """
    if df is None or df.empty:
        return 0

    # NaN 替换为 None，避免插入 nan；先转 object 防止 None 被浮点列回转为 nan
    df = df.astype(object).where(pd.notnull(df), None)

    cols = ", ".join(TABLE_COLUMNS)
    placeholders = ", ".join(["%s"] * len(TABLE_COLUMNS))
    # ON DUPLICATE KEY UPDATE: 除主键外的所有列更新为新值
    update_cols = [c for c in TABLE_COLUMNS if c not in ("ts_code", "trade_date")]
    update_clause = ", ".join([f"{c}=VALUES({c})" for c in update_cols])

    insert_sql = (
        f"INSERT INTO stock_daily_factor_t ({cols}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )

    data = [
        tuple(row[field] for field in FACTOR_FIELDS)
        for row in df.to_dict('records')
    ]

    with conn.cursor() as cursor:
        affected = cursor.executemany(insert_sql, data)
    conn.commit()
    return affected if affected else 0


def process_one_day(conn, trade_date, valid_codes):
    """
    处理单个交易日：全市场分页拉取技术因子，按有效股票过滤后批量更新入库。

    :param conn: 数据库连接
    :param trade_date: 交易日 YYYYMMDD
    :param valid_codes: 有效股票代码集合（去 ST、去北证）
    :return: (过滤后记录数, 更新记录数)
    """
    df_factor = fetch_stk_factor_all(trade_date)
    if df_factor is None or df_factor.empty:
        print(f"   ⚠️ {trade_date} 无技术因子数据")
        return 0, 0

    # 过滤：仅保留有效股票（去 ST、去北证）
    df_factor = df_factor[df_factor['ts_code'].isin(valid_codes)]

    affected = upsert_stk_factor(conn, df_factor)
    return len(df_factor), affected


def main():
    """
    主函数：按最近一个交易日（或指定日期/区间）批量获取 stk_factor
    技术面因子数据并更新到 stock_daily_factor_t 表。
    """
    start_time = time.time()

    # 解析命令行参数
    if len(sys.argv) >= 3:
        start_date = sys.argv[1]
        end_date = sys.argv[2]
    elif len(sys.argv) == 2:
        start_date = sys.argv[1]
        end_date = sys.argv[1]
    else:
        today = get_latest_trade_date()
        start_date = today
        end_date = today

    print("=" * 60)
    print("📊 股票每日技术面因子更新程序")
    print("=" * 60)
    print(f"\n📅 日期范围: {start_date} ~ {end_date}")

    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库，程序退出")
        sys.exit(1)

    try:
        print("\n📋 步骤2: 从 stock_info_t 读取有效股票代码...")
        valid_codes = get_valid_stock_codes(conn)
        if not valid_codes:
            print("❌ 没有找到股票代码，程序退出")
            return
        print(f"   ✅ 共读取到 {len(valid_codes)} 个有效股票代码")

        print("\n🆕 步骤3: 创建/确认表 stock_daily_factor_t ...")
        create_table_if_not_exists(conn)
        print("   ✅ 表已就绪")

        print("\n📅 步骤4: 获取交易日列表...")
        trade_dates = get_trade_dates(start_date, end_date)
        if not trade_dates:
            print("❌ 日期范围内没有交易日，程序退出")
            return
        print(f"   ✅ 共 {len(trade_dates)} 个交易日: {trade_dates[0]} ~ {trade_dates[-1]}")

        total_rows = 0
        total_affected = 0
        error_count = 0

        print("\n🔄 步骤5: 获取 stk_factor 数据并更新入库...")
        for i, trade_date in enumerate(trade_dates, 1):
            try:
                rows, affected = process_one_day(conn, trade_date, valid_codes)
                total_rows += rows
                total_affected += affected
                print(f"   [{i}/{len(trade_dates)}] {trade_date}: 获取{rows}条, 更新{affected}条")
            except Exception as e:
                error_count += 1
                print(f"   ⚠️ 处理 {trade_date} 时出错: {e}")

        elapsed = time.time() - start_time
        print(f"\n📈 更新结果统计:")
        print("-" * 40)
        print(f"   处理交易日数: {len(trade_dates)}")
        print(f"   获取记录数: {total_rows}")
        print(f"   更新记录数: {total_affected}")
        print(f"   出错次数: {error_count}")
        print(f"   总耗时: {elapsed:.1f} 秒")

        print("\n🎉 股票每日技术面因子更新完成！")

    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
