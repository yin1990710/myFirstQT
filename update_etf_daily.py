#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ETF 每日数据更新任务 (update_etf_daily.py)

功能：
  1. 拉取全市场 ETF 基础信息（etf_basic 接口），写入 etf_basic_t 表；
  2. 拉取每个上市 ETF 最近 N 个交易日的日 K 线行情（fund_daily 接口），
     写入 etf_daily_t 表。

数据库表：
  - etf_basic_t：ETF 基础信息（代码、名称、跟踪指数、管理人、上市日期等）
  - etf_daily_t：ETF 日线行情（OHLC、成交量、成交额、涨跌幅）

用法：
  .venv/bin/python update_etf_daily.py
"""

import os
import sys
import time
from datetime import datetime

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

# Tushare 接口初始化
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# 拉取日线的历史天数（最近 N 个交易日）
HISTORY_DAYS = 30
# Tushare 接口限频：每次调用之间的休眠秒数
API_SLEEP = 0.15


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
                    row.get('ts_code', ''),
                    row.get('csname', ''),
                    row.get('extname', ''),
                    row.get('cname', ''),
                    row.get('index_code', ''),
                    row.get('index_name', ''),
                    row.get('setup_date', ''),
                    row.get('list_date', ''),
                    row.get('list_status', ''),
                    row.get('exchange', ''),
                    row.get('mgr_name', ''),
                    row.get('custod_name', ''),
                    float(row['mgt_fee']) if row.get('mgt_fee') is not None else None,
                    row.get('etf_type', ''),
                ))
        connection.commit()
        print(f"✅ etf_basic_t 写入完成: {len(df)} 条")
    except Exception as e:
        print(f"❌ 写入 etf_basic_t 失败: {e}")
        connection.rollback()


def fetch_etf_daily(ts_code):
    """拉取单只 ETF 最近 N 个交易日的日 K 线。"""
    end_date = datetime.now().strftime('%Y%m%d')
    # 起始日向前多取 30 天，保证覆盖 N 个交易日
    start_dt = datetime.now()
    from datetime import timedelta
    start_date = (start_dt - timedelta(days=HISTORY_DAYS * 2 + 20)).strftime('%Y%m%d')
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        return df
    except Exception as e:
        print(f"  ⚠️ {ts_code} 日线获取失败: {e}")
        return None


def insert_etf_daily(connection, ts_code, df):
    """写入单只 ETF 日线数据。"""
    if df is None or df.empty:
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
    n = 0
    try:
        with connection.cursor() as cursor:
            for _, row in df.iterrows():
                cursor.execute(insert_sql, (
                    row.get('ts_code', ts_code),
                    str(row.get('trade_date', '')),
                    float(row['pre_close']) if row.get('pre_close') is not None else None,
                    float(row['open']) if row.get('open') is not None else None,
                    float(row['high']) if row.get('high') is not None else None,
                    float(row['low']) if row.get('low') is not None else None,
                    float(row['close']) if row.get('close') is not None else None,
                    float(row['change']) if row.get('change') is not None else None,
                    float(row['pct_chg']) if row.get('pct_chg') is not None else None,
                    float(row['vol']) if row.get('vol') is not None else None,
                    float(row['amount']) if row.get('amount') is not None else None,
                ))
                n += 1
        connection.commit()
    except Exception as e:
        print(f"  ⚠️ {ts_code} 写入失败: {e}")
        connection.rollback()
    return n


def main():
    print("=" * 80)
    print("📈 ETF 每日数据更新任务")
    print("=" * 80)

    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return

    try:
        if not create_tables(connection):
            return

        # 1. 更新 ETF 基础信息
        print("\n[1/2] 拉取 ETF 基础信息...")
        df_basic = fetch_etf_basic()
        insert_etf_basic(connection, df_basic)

        # 2. 更新每只 ETF 的日 K 线
        print(f"\n[2/2] 拉取 ETF 日线行情（最近 {HISTORY_DAYS} 个交易日）...")
        if df_basic is None or df_basic.empty:
            print("⚠️ 无基础信息，跳过日线更新")
            return

        codes = df_basic['ts_code'].tolist()
        total = len(codes)
        ok_count = 0
        total_rows = 0
        for i, code in enumerate(codes, 1):
            df_d = fetch_etf_daily(code)
            n = insert_etf_daily(connection, code, df_d)
            if n > 0:
                ok_count += 1
                total_rows += n
            if i % 50 == 0:
                print(f"  进度: {i}/{total}")
            time.sleep(API_SLEEP)

        print(f"\n✅ ETF 日线更新完成: {ok_count}/{total} 只成功, 共 {total_rows} 条记录")

    finally:
        close_connection(connection)

    print("\n" + "=" * 80)
    print("🎉 ETF 数据更新任务完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
