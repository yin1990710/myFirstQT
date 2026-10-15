#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ETF 每日数据更新任务 (update_etf_daily.py)

功能：
  1. 拉取全市场 ETF 基础信息（etf_basic 接口），写入 etf_basic_t 表；
  2. 按交易日批量拉取全市场场内基金日线行情（fund_daily 按 trade_date 单次调用，
     不再逐只循环），过滤出 ETF 后批量写入 etf_daily_t 表。
  相比逐只循环模式：单交易日仅 1 次接口调用，全市场约 1 秒完成，
  避免逐只调用触发限频导致数据大面积缺失。

数据库表：
  - etf_basic_t：ETF 基础信息（代码、名称、跟踪指数、管理人、上市日期等）
  - etf_daily_t：ETF 日线行情（OHLC、成交量、成交额、涨跌幅）

用法：
  .venv/bin/python update_etf_daily.py                    # 默认最新交易日（15点前取上一交易日）
  .venv/bin/python update_etf_daily.py 20261015           # 指定单日
  .venv/bin/python update_etf_daily.py 20260924 20261015  # 补数：指定日期区间（含首尾）
"""

import math
import os
import sys
import time
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

# Tushare 接口初始化
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# 批量模式下相邻交易日调用之间的休眠秒数
API_SLEEP = 0.3


def create_tables(connection):
    """创建 etf_basic_t 与 etf_daily_t 表（如不存在）。"""
    ddl_basic = """
    CREATE TABLE IF NOT EXISTS etf_basic_t (
        ts_code        VARCHAR(10)  NOT NULL COMMENT 'ETF代码',
        csname         VARCHAR(60)  COMMENT 'ETF简称',
        extname        VARCHAR(60)  COMMENT 'ETF扩展简称',
        cname          VARCHAR(120) COMMENT 'ETF全称',
        index_code     VARCHAR(20)  COMMENT '跟踪指数代码',
        index_name     VARCHAR(60)  COMMENT '跟踪指数名称',
        setup_date     VARCHAR(8)   COMMENT '成立日期',
        list_date      VARCHAR(8)   COMMENT '上市日期',
        list_status    VARCHAR(4)   COMMENT '上市状态：L上市 D退市 P暂停',
        exchange       VARCHAR(4)   COMMENT '交易所：SH上交所 SZ深交所',
        mgr_name       VARCHAR(40)  COMMENT '管理人',
        custod_name    VARCHAR(80)  COMMENT '托管人',
        mgt_fee        DECIMAL(6,3) COMMENT '管理费(%)',
        etf_type       VARCHAR(20)  COMMENT 'ETF类型',
        update_time    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
        PRIMARY KEY (ts_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ETF基础信息表';
    """
    ddl_daily = """
    CREATE TABLE IF NOT EXISTS etf_daily_t (
        ts_code        VARCHAR(10)  NOT NULL COMMENT 'ETF代码',
        trade_date     VARCHAR(8)   NOT NULL COMMENT '交易日期',
        pre_close      DECIMAL(10,4) COMMENT '前收盘价',
        open           DECIMAL(10,4) COMMENT '开盘价',
        high           DECIMAL(10,4) COMMENT '最高价',
        low            DECIMAL(10,4) COMMENT '最低价',
        close          DECIMAL(10,4) COMMENT '收盘价',
        `change`       DECIMAL(10,4) COMMENT '涨跌额',
        pct_chg        DECIMAL(8,3)  COMMENT '涨跌幅(%)',
        vol            DECIMAL(20,4) COMMENT '成交量(手)',
        amount         DECIMAL(20,4) COMMENT '成交额(千元)',
        update_time    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
        PRIMARY KEY (ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ETF日线行情表';
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(ddl_basic)
            cursor.execute(ddl_daily)
        connection.commit()
        print("✅ 表 etf_basic_t / etf_daily_t 创建成功（或已存在）")
        return True
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        connection.rollback()
        return False


def fetch_etf_basic():
    """拉取全市场上市 ETF 基础信息。"""
    try:
        df = pro.etf_basic(list_status='L')
        if df is None or df.empty:
            print("⚠️ 未获取到 ETF 基础信息")
            return None
        print(f"✅ 获取到 {len(df)} 条 ETF 基础信息")
        return df
    except Exception as e:
        print(f"❌ 获取 ETF 基础信息失败: {e}")
        return None


def _s(v):
    """安全转 str（None/NaN → ''）。"""
    if v is None:
        return ''
    if isinstance(v, float) and math.isnan(v):
        return ''
    return str(v)


def insert_etf_basic(connection, df):
    """写入 ETF 基础信息（按主键 upsert）。"""
    if df is None or df.empty:
        return
    insert_sql = """
    INSERT INTO etf_basic_t (
        ts_code, csname, extname, cname, index_code, index_name,
        setup_date, list_date, list_status, exchange, mgr_name,
        custod_name, mgt_fee, etf_type
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    ) ON DUPLICATE KEY UPDATE
        csname=VALUES(csname), extname=VALUES(extname), cname=VALUES(cname),
        index_code=VALUES(index_code), index_name=VALUES(index_name),
        setup_date=VALUES(setup_date), list_date=VALUES(list_date),
        list_status=VALUES(list_status), exchange=VALUES(exchange),
        mgr_name=VALUES(mgr_name), custod_name=VALUES(custod_name),
        mgt_fee=VALUES(mgt_fee), etf_type=VALUES(etf_type)
    """
    try:
        with connection.cursor() as cursor:
            for _, row in df.iterrows():
                cursor.execute(insert_sql, (
                    _s(row.get('ts_code', '')),
                    _s(row.get('csname', '')),
                    _s(row.get('extname', '')),
                    _s(row.get('cname', '')),
                    _s(row.get('index_code', '')),
                    _s(row.get('index_name', '')),
                    _s(row.get('setup_date', '')),
                    _s(row.get('list_date', '')),
                    _s(row.get('list_status', '')),
                    _s(row.get('exchange', '')),
                    _s(row.get('mgr_name', '')),
                    _s(row.get('custod_name', '')),
                    _f(row.get('mgt_fee')),
                    _s(row.get('etf_type', '')),
                ))
        connection.commit()
        print(f"✅ etf_basic_t 写入完成: {len(df)} 条")
    except Exception as e:
        print(f"❌ 写入 etf_basic_t 失败: {e}")
        connection.rollback()


def _f(v):
    """安全转 float（None/NaN/非法值 → None）。"""
    try:
        f = float(v)
        return None if f != f else f   # NaN 检查
    except (TypeError, ValueError):
        return None


def get_etf_codes(conn) -> set:
    """读取 etf_basic_t 全部 ETF 代码，用于过滤 fund_daily 返回的非 ETF 场内基金。"""
    with conn.cursor() as cursor:
        cursor.execute("SELECT ts_code FROM etf_basic_t")
        return {r['ts_code'] for r in cursor.fetchall()}


def fetch_fund_daily_batch(trade_date):
    """批量拉取某交易日全市场场内基金日线（fund_daily 按 trade_date 单次调用）。"""
    try:
        return pro.fund_daily(trade_date=trade_date)
    except Exception as e:
        print(f"  ⚠️ {trade_date} 批量行情获取失败: {e}")
        return None


def insert_etf_daily_batch(conn, df, etf_codes) -> int:
    """批量写入某交易日 ETF 日线（executemany upsert，仅保留 etf_basic_t 中的 ETF）。"""
    if df is None or df.empty:
        return 0
    df = df[df['ts_code'].isin(etf_codes)]
    if df.empty:
        return 0
    insert_sql = """
    INSERT INTO etf_daily_t (
        ts_code, trade_date, pre_close, open, high, low, close,
        `change`, pct_chg, vol, amount
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    ) ON DUPLICATE KEY UPDATE
        pre_close=VALUES(pre_close), open=VALUES(open), high=VALUES(high),
        low=VALUES(low), close=VALUES(close), `change`=VALUES(`change`),
        pct_chg=VALUES(pct_chg), vol=VALUES(vol), amount=VALUES(amount)
    """
    data = [
        (str(r['ts_code']), str(r['trade_date']),
         _f(r.get('pre_close')), _f(r.get('open')), _f(r.get('high')),
         _f(r.get('low')), _f(r.get('close')), _f(r.get('change')),
         _f(r.get('pct_chg')), _f(r.get('vol')), _f(r.get('amount')))
        for _, r in df.iterrows()
    ]
    trade_date = str(df['trade_date'].iloc[0])
    try:
        with conn.cursor() as cursor:
            cursor.executemany(insert_sql, data)
        conn.commit()
        return len(data)
    except Exception as e:
        print(f"  ⚠️ {trade_date} 批量写入失败: {e}")
        conn.rollback()
        return 0


def get_trade_dates(start_date, end_date) -> list:
    """获取 [start_date, end_date] 内的交易日列表（SSE 交易日历）。"""
    df = pro.trade_cal(exchange='SSE', start_date=start_date,
                       end_date=end_date, is_open='1')
    if df is None or df.empty:
        return []
    return sorted(df['cal_date'].astype(str).tolist())


def get_default_target_date() -> str:
    """默认目标日：15点前取上一交易日（当日行情收盘后才发布）。"""
    today = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=15)).strftime('%Y%m%d')
    dates = get_trade_dates(start, today)
    if not dates:
        return today
    if datetime.now().hour < 15 and dates[-1] == today:
        dates = dates[:-1]
    return dates[-1] if dates else today


def main():
    print("=" * 80)
    print("📈 ETF 每日数据更新任务（批量模式）")
    print("=" * 80)

    # 解析参数：0个=默认最新交易日；1个=指定单日；2个=日期区间（含首尾，用于补数）
    if len(sys.argv) >= 3:
        start_date, end_date = sys.argv[1], sys.argv[2]
    elif len(sys.argv) == 2:
        start_date = end_date = sys.argv[1]
    else:
        start_date = end_date = get_default_target_date()

    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return

    try:
        if not create_tables(connection):
            return

        # 1. 更新 ETF 基础信息（1次接口调用）
        print("\n[1/2] 拉取 ETF 基础信息...")
        df_basic = fetch_etf_basic()
        insert_etf_basic(connection, df_basic)

        # 2. 按交易日批量拉取全市场基金日线并入库（每交易日1次接口调用）
        trade_dates = get_trade_dates(start_date, end_date)
        if not trade_dates:
            print(f"\n❌ {start_date} ~ {end_date} 内无交易日")
            return
        etf_codes = get_etf_codes(connection)
        print(f"\n[2/2] 批量拉取 ETF 日线行情: {start_date} ~ {end_date}，"
              f"共 {len(trade_dates)} 个交易日（ETF 基础表 {len(etf_codes)} 只）...")
        ok_days, total_rows = 0, 0
        for i, d in enumerate(trade_dates, 1):
            df_d = fetch_fund_daily_batch(d)
            n = insert_etf_daily_batch(connection, df_d, etf_codes)
            if n > 0:
                ok_days += 1
                total_rows += n
            print(f"  [{i}/{len(trade_dates)}] {d}: 写入 {n} 条")
            time.sleep(API_SLEEP)

        print(f"\n✅ ETF 日线批量更新完成: {ok_days}/{len(trade_dates)} 个交易日有数据, "
              f"共 {total_rows} 条记录")
    finally:
        close_connection(connection)

    print("\n" + "=" * 80)
    print("🎉 ETF 数据更新任务完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
