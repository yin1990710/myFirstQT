#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calc_industry_index.py — 行业指数日K线计算

编制方法（见 prompt L460-469）：
  - 每个行业每个交易日，取该日总市值前 10 的股票编制为该行业指数
  - 指数开盘价 = 前 10 股票开盘价平均值
  - 指数收盘价 = 前 10 股票收盘价平均值
  - 指数最高价 = 前 10 股票最高价平均值
  - 指数最低价 = 前 10 股票最低价平均值
  - 指数名称 = 行业名称 + "指数"（如 "信息通信指数"）

数据来源：
  - stock_dfcf_industry_t       股票-东财行业映射（无日期、稳定）
  - stock_daily_t               当日 OHLC 行情
  - stock_daily_basic_info_t     当日 total_mv 总市值（万元）

白名单：东方财富行业板块/行业板块.csv（仅计算此文件列出的行业）

用法：
  python3 calc_industry_index.py                          # 计算最新交易日
  python3 calc_industry_index.py 20260918                 # 指定单日
  python3 calc_industry_index.py 20260901 20260918        # 指定起始与结束日期（含两端）
  python3 calc_industry_index.py 20260901                 # 从起始日期到最新交易日
  python3 calc_industry_index.py --all                    # 回填全部历史交易日（首次部署）

参数：
  start_date  起始日期 YYYYMMDD（可选，默认取最新交易日）
  end_date    结束日期 YYYYMMDD（可选，默认等于 start_date）
              仅传 start_date 时，end_date 自动取 stock_daily_t 最新交易日
  --all       回填全部历史交易日
"""
import os
import sys
import csv
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection


# 每个行业取总市值前 N 只股票编制指数
TOP_N = 10

# 板块白名单 CSV 路径（仅计算此文件中列出的板块）
BOARD_WHITELIST_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '东方财富行业板块', '行业板块.csv'
)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS industry_index_daily_t (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    board_name  VARCHAR(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '行业名称',
    index_name  VARCHAR(80) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指数名称(行业名+指数)',
    trade_date  VARCHAR(8)  COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期(YYYYMMDD)',
    `open`      DECIMAL(10,3)        COMMENT '开盘价(前10股票开盘均价)',
    high        DECIMAL(10,3)        COMMENT '最高价(前10股票最高均价)',
    low         DECIMAL(10,3)        COMMENT '最低价(前10股票最低均价)',
    `close`     DECIMAL(10,3)        COMMENT '收盘价(前10股票收盘均价)',
    stock_count INT NOT NULL COMMENT '实际纳入股票数(<=10)',
    created_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_board_date (board_name, trade_date),
    KEY idx_date (trade_date),
    KEY idx_board_date (board_name, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='行业指数日K线表(每行业总市值前10股票OHLC均值)';
"""

UPSERT_SQL = """
INSERT INTO industry_index_daily_t
    (board_name, index_name, trade_date, `open`, high, low, `close`, stock_count)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    index_name   = VALUES(index_name),
    `open`       = VALUES(`open`),
    high         = VALUES(high),
    low          = VALUES(low),
    `close`      = VALUES(`close`),
    stock_count  = VALUES(stock_count)
"""


def load_board_whitelist():
    """从行业板块.csv 加载板块名集合，文件不存在时返回 None（不过滤）。"""
    if not os.path.exists(BOARD_WHITELIST_CSV):
        return None
    names = set()
    with open(BOARD_WHITELIST_CSV, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            nm = (row.get('board_name') or '').strip()
            if nm:
                names.add(nm)
    print(f"  板块白名单: {len(names)} 个（来源 {BOARD_WHITELIST_CSV}）")
    return names or None


def get_target_date():
    """目标交易日：0-15 点取前一交易日，15 点后取当日。"""
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_latest_trade_date():
    """从 stock_daily_t 取最新交易日（避免周末/假日取到非交易日）。"""
    conn = get_mysql_connection()
    if not conn:
        return get_target_date()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT MAX(trade_date) d FROM stock_daily_t')
            row = cur.fetchone()
            return row['d'] if row and row['d'] else get_target_date()
    finally:
        close_connection(conn)


def calc_for_date(conn, trade_date, whitelist):
    """计算单个交易日所有行业指数，返回 list[dict]。

    每个行业取当日 total_mv 前 10 的股票，对其 OHLC 求均值。
    使用窗口函数 ROW_NUMBER() PARTITION BY board_name ORDER BY total_mv DESC。
    三表 JOIN 存在 collation 冲突（utf8mb4_unicode_ci vs utf8mb4_0900_ai_ci），
    JOIN 条件统一 COLLATE utf8mb4_unicode_ci。
    """
    with conn.cursor() as cur:
        if whitelist is not None:
            placeholders = ','.join(['%s'] * len(whitelist))
            cur.execute(f"""
                SELECT
                    d.board_name,
                    k.`open`, k.high, k.low, k.`close`,
                    b.total_mv,
                    ROW_NUMBER() OVER (
                        PARTITION BY d.board_name
                        ORDER BY b.total_mv DESC, k.vol DESC
                    ) AS rn
                FROM stock_dfcf_industry_t d
                JOIN stock_daily_t k
                  ON d.ts_code COLLATE utf8mb4_unicode_ci = k.ts_code COLLATE utf8mb4_unicode_ci
                JOIN stock_daily_basic_info_t b
                  ON d.ts_code COLLATE utf8mb4_unicode_ci = b.ts_code COLLATE utf8mb4_unicode_ci
                 AND k.trade_date = b.trade_date
                WHERE k.trade_date = %s
                  AND d.board_name IN ({placeholders})
            """, (trade_date,) + tuple(whitelist))
        else:
            cur.execute("""
                SELECT
                    d.board_name,
                    k.`open`, k.high, k.low, k.`close`,
                    b.total_mv,
                    ROW_NUMBER() OVER (
                        PARTITION BY d.board_name
                        ORDER BY b.total_mv DESC, k.vol DESC
                    ) AS rn
                FROM stock_dfcf_industry_t d
                JOIN stock_daily_t k
                  ON d.ts_code COLLATE utf8mb4_unicode_ci = k.ts_code COLLATE utf8mb4_unicode_ci
                JOIN stock_daily_basic_info_t b
                  ON d.ts_code COLLATE utf8mb4_unicode_ci = b.ts_code COLLATE utf8mb4_unicode_ci
                 AND k.trade_date = b.trade_date
                WHERE k.trade_date = %s
            """, (trade_date,))
        rows = cur.fetchall()

    # 按行业聚合前 10
    agg = defaultdict(lambda: {'opens': [], 'highs': [], 'lows': [], 'closes': []})
    for r in rows:
        if r['rn'] > TOP_N:
            continue
        bn = r['board_name']
        if r['open'] is not None:
            agg[bn]['opens'].append(float(r['open']))
        if r['high'] is not None:
            agg[bn]['highs'].append(float(r['high']))
        if r['low'] is not None:
            agg[bn]['lows'].append(float(r['low']))
        if r['close'] is not None:
            agg[bn]['closes'].append(float(r['close']))

    results = []
    for bn, v in agg.items():
        # 实际纳入数：取 OHLC 都非空的最小家数，保证均价有效
        cnt = min(len(v['opens']), len(v['highs']), len(v['lows']), len(v['closes']))
        if cnt == 0:
            continue
        results.append({
            'board_name': bn,
            'index_name': bn + '指数',
            'trade_date': trade_date,
            'open': round(sum(v['opens']) / len(v['opens']), 3),
            'high': round(sum(v['highs']) / len(v['highs']), 3),
            'low': round(sum(v['lows']) / len(v['lows']), 3),
            'close': round(sum(v['closes']) / len(v['closes']), 3),
            'stock_count': cnt,
        })
    return results


def save_to_db(conn, results):
    """upsert 入库；results 为空时仅建表。"""
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, [
            (r['board_name'], r['index_name'], r['trade_date'],
             r['open'], r['high'], r['low'], r['close'], r['stock_count'])
            for r in results
        ])
    conn.commit()


def get_trade_dates_in_range(conn, start_date, end_date):
    """从 stock_daily_t 取 [start_date, end_date] 区间内的交易日列表（升序）。"""
    with conn.cursor() as cur:
        if start_date and end_date:
            cur.execute(
                "SELECT DISTINCT trade_date FROM stock_daily_t "
                "WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date",
                (start_date, end_date))
        elif start_date:
            cur.execute(
                "SELECT DISTINCT trade_date FROM stock_daily_t "
                "WHERE trade_date >= %s ORDER BY trade_date",
                (start_date,))
        elif end_date:
            cur.execute(
                "SELECT DISTINCT trade_date FROM stock_daily_t "
                "WHERE trade_date <= %s ORDER BY trade_date",
                (end_date,))
        else:
            cur.execute(
                "SELECT DISTINCT trade_date FROM stock_daily_t ORDER BY trade_date")
        return [r['trade_date'] for r in cur.fetchall()]


def main():
    args = sys.argv[1:]
    whitelist = load_board_whitelist()

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()

        if args and args[0] == '--all':
            # 回填全部历史交易日
            dates = get_trade_dates_in_range(conn, None, None)
            print(f"回填 {len(dates)} 个交易日...")
            total = 0
            for i, td in enumerate(dates, 1):
                res = calc_for_date(conn, td, whitelist)
                if res:
                    save_to_db(conn, res)
                    total += len(res)
                if i % 50 == 0:
                    print(f"  进度 {i}/{len(dates)}，已入库 {total} 条")
            print(f"✅ 回填完成，共入库 {total} 条")
        else:
            # 解析参数：0 或 1-2 个日期
            start_date = args[0] if len(args) >= 1 else None
            end_date = args[1] if len(args) >= 2 else None

            if start_date is None:
                # 无参数：默认取最新交易日
                dates = [get_latest_trade_date()]
            elif end_date is None:
                # 仅传起始日期：从该日期到最新交易日
                dates = get_trade_dates_in_range(conn, start_date, None)
            else:
                # 起始与结束日期都指定
                if start_date > end_date:
                    start_date, end_date = end_date, start_date  # 自动交换
                dates = get_trade_dates_in_range(conn, start_date, end_date)

            print("=" * 60)
            if len(dates) == 1:
                print(f"行业指数日K线计算 — 交易日 {dates[0]}")
            else:
                print(f"行业指数日K线计算 — 区间 {dates[0]} ~ {dates[-1]}（共 {len(dates)} 个交易日）")
            print("=" * 60)
            total = 0
            for i, td in enumerate(dates, 1):
                res = calc_for_date(conn, td, whitelist)
                if len(dates) == 1:
                    # 单日展示明细
                    print(f"共 {len(res)} 个行业指数（每行业总市值前 {TOP_N} 股票 OHLC 均值）")
                    print(f"{'指数名称':20} {'开盘':>8} {'最高':>8} {'最低':>8} {'收盘':>8} {'家数':>4}")
                    print("-" * 60)
                    for r in res[:15]:
                        print(f"{r['index_name']:20} {r['open']:>8} {r['high']:>8} "
                              f"{r['low']:>8} {r['close']:>8} {r['stock_count']:>4}")
                if res:
                    save_to_db(conn, res)
                    total += len(res)
                if len(dates) > 1 and (i % 50 == 0 or i == len(dates)):
                    print(f"  进度 {i}/{len(dates)}，已入库 {total} 条")
            if total:
                print(f"\n✅ 入库 {total} 条")
            else:
                print("\n⚠️ 无数据，跳过入库")
    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
