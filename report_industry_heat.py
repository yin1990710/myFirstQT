#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
report_industry_heat.py — 股票行业短线热度分析

数据来源：
  - stock_dfcf_industry_t（股票-东财板块映射）
  - stock_daily_t（当日行情：close/pct_chg/amount/short_strength_score）
  - stock_info_t（总市值 total_mv）

计算指标（按 trade_date × board_name 聚合）：
  1. stock_count        行业股票家数
  2. turnover_ratio     行业总成交额/总市值
  3. up_down_ratio      涨/跌比例（上涨家数/下跌家数）
  4. up_over_8pct       涨幅>8%的只数
  5. down_over_5pct     跌幅>5%的只数
  6. pct_median         涨幅中位数
  7. up_ratio           上涨只数/总家数
  8. avg_short_strength 平均短线强弱得分
  9. heat_score         综合打分 = up_down_ratio × pct_median × avg_short_strength

默认按 heat_score 倒序排序。
"""

import os
import sys
import statistics
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    """目标交易日：0-15 点取前一交易日，15 点后取当日。"""
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS industry_heat_daily_t (
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date        VARCHAR(8)   NOT NULL COMMENT '交易日期(YYYYMMDD)',
    board_name        VARCHAR(64)  NOT NULL COMMENT '板块名称',
    stock_count       INT          NOT NULL COMMENT '行业股票家数',
    turnover_ratio    DECIMAL(12,6)        COMMENT '总成交额/总市值',
    up_down_ratio     DECIMAL(12,4)        COMMENT '涨/跌比例',
    up_over_8pct      INT                 COMMENT '涨幅>8%只数',
    down_over_5pct    INT                 COMMENT '跌幅>5%只数',
    pct_median        DECIMAL(10,4)        COMMENT '涨幅中位数(%)',
    up_ratio          DECIMAL(8,4)         COMMENT '上涨只数/总家数',
    avg_short_strength DECIMAL(10,4)       COMMENT '平均短线强弱得分',
    heat_score        DECIMAL(14,4)        COMMENT '综合打分',
    created_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time       TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
                      ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_date_board (trade_date, board_name),
    KEY idx_date_score (trade_date, heat_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='行业短线热度分析表';
"""

UPSERT_SQL = """
INSERT INTO industry_heat_daily_t
    (trade_date, board_name, stock_count, turnover_ratio,
     up_down_ratio, up_over_8pct, down_over_5pct,
     pct_median, up_ratio, avg_short_strength, heat_score)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    stock_count       = VALUES(stock_count),
    turnover_ratio    = VALUES(turnover_ratio),
    up_down_ratio     = VALUES(up_down_ratio),
    up_over_8pct      = VALUES(up_over_8pct),
    down_over_5pct    = VALUES(down_over_5pct),
    pct_median        = VALUES(pct_median),
    up_ratio          = VALUES(up_ratio),
    avg_short_strength= VALUES(avg_short_strength),
    heat_score        = VALUES(heat_score)
"""

# 最小行业家数门槛（低于此数的板块不入库，过滤噪音）
MIN_STOCK_COUNT = 3

# 板块白名单 CSV 路径（仅计算此文件中列出的板块）
BOARD_WHITELIST_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '东方财富行业板块', '行业板块.csv'
)


def load_board_whitelist():
    """从行业板块.csv 加载板块名称集合，文件不存在时返回 None（不过滤）。"""
    if not os.path.exists(BOARD_WHITELIST_CSV):
        return None
    import csv
    names = set()
    with open(BOARD_WHITELIST_CSV, encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            nm = (row.get('board_name') or '').strip()
            if nm:
                names.add(nm)
    print(f"  板块白名单: {len(names)} 个（来源 {BOARD_WHITELIST_CSV}）")
    return names


def compute_industry_heat(trade_date):
    """计算指定交易日的行业热度指标，返回 list[dict]。"""
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    try:
        # 加载板块白名单（CSV 中列出的板块才计算）
        whitelist = load_board_whitelist()

        with connection.cursor() as cursor:
            # 1. 拉取当日全部行情数据（pct_chg/amount/short_strength_score）
            cursor.execute("""
                SELECT ts_code, pct_chg, amount, short_strength_score
                FROM stock_daily_t
                WHERE trade_date = %s
            """, (trade_date,))
            daily = {r['ts_code']: r for r in cursor.fetchall()}
            print(f"  当日行情: {len(daily)} 只")

            # 2. 拉取股票-板块映射 + 总市值（仅白名单板块）
            if whitelist is not None:
                placeholders = ','.join(['%s'] * len(whitelist))
                cursor.execute(f"""
                    SELECT d.ts_code, d.board_name, i.total_mv
                    FROM stock_dfcf_industry_t d
                    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code
                    WHERE d.board_name IN ({placeholders})
                """, tuple(whitelist))
            else:
                cursor.execute("""
                    SELECT d.ts_code, d.board_name, i.total_mv
                    FROM stock_dfcf_industry_t d
                    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code
                """)
            mapping = cursor.fetchall()
            print(f"  板块映射: {len(mapping)} 条")

            # 3. 按板块分组聚合
            from collections import defaultdict
            groups = defaultdict(list)
            for m in mapping:
                ts_code = m['ts_code']
                if ts_code not in daily:
                    continue
                row = daily[ts_code]
                groups[m['board_name']].append({
                    'pct_chg': float(row['pct_chg']) if row.get('pct_chg') is not None else None,
                    'amount': float(row['amount'] or 0),
                    'total_mv': float(m['total_mv'] or 0),
                    'short_strength': (float(row['short_strength_score'])
                                        if row.get('short_strength_score') is not None else None),
                })

            results = []
            for board_name, stocks in groups.items():
                n = len(stocks)
                if n < MIN_STOCK_COUNT:
                    continue

                pcts = [s['pct_chg'] for s in stocks if s['pct_chg'] is not None]
                strengths = [s['short_strength'] for s in stocks if s['short_strength'] is not None]
                total_amount = sum(s['amount'] for s in stocks)
                total_mv = sum(s['total_mv'] for s in stocks)

                up_count = sum(1 for p in pcts if p > 0)
                down_count = sum(1 for p in pcts if p < 0)
                up_over_8 = sum(1 for p in pcts if p > 8)
                down_over_5 = sum(1 for p in pcts if p < -5)

                pct_median = statistics.median(pcts) if pcts else None
                avg_strength = (sum(strengths) / len(strengths)) if strengths else None

                # 真实涨跌比用于热度分计算；无下跌股票时展示值约定为100（不参与热度分，避免分值失真）
                calc_ratio = (up_count / down_count) if down_count > 0 else None
                if down_count > 0:
                    up_down_ratio = up_count / down_count
                elif up_count > 0:
                    up_down_ratio = 100
                else:
                    up_down_ratio = None
                up_ratio = up_count / n if n > 0 else None
                turnover_ratio = (total_amount / total_mv) if total_mv > 0 else None

                # 综合打分 = 涨/跌比例 × 涨幅中位数 × 平均短线强弱得分（使用真实比值，全涨时不打分）
                heat_score = None
                if calc_ratio is not None and pct_median is not None and avg_strength is not None:
                    heat_score = calc_ratio * pct_median * avg_strength

                results.append({
                    'trade_date': trade_date,
                    'board_name': board_name,
                    'stock_count': n,
                    'turnover_ratio': round(turnover_ratio, 6) if turnover_ratio else None,
                    'up_down_ratio': round(up_down_ratio, 4) if up_down_ratio else None,
                    'up_over_8pct': up_over_8,
                    'down_over_5pct': down_over_5,
                    'pct_median': round(pct_median, 4) if pct_median is not None else None,
                    'up_ratio': round(up_ratio, 4) if up_ratio else None,
                    'avg_short_strength': round(avg_strength, 4) if avg_strength else None,
                    'heat_score': round(heat_score, 4) if heat_score is not None else None,
                })

            # 默认按 heat_score 倒序
            results.sort(key=lambda x: (x['heat_score'] or 0), reverse=True)
            return results
    finally:
        close_connection(connection)


def save_to_db(results):
    """批量 upsert 入库。"""
    if not results:
        return
    connection = get_mysql_connection()
    if not connection:
        return
    try:
        data = [(
            r['trade_date'], r['board_name'], r['stock_count'],
            r['turnover_ratio'], r['up_down_ratio'],
            r['up_over_8pct'], r['down_over_5pct'],
            r['pct_median'], r['up_ratio'],
            r['avg_short_strength'], r['heat_score'],
        ) for r in results]
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            cursor.executemany(UPSERT_SQL, data)
            # 清理该交易日不在白名单内的旧记录（upsert 只更新不删除，旧行需显式清理）
            whitelist = load_board_whitelist()
            if whitelist is not None:
                trade_date = results[0]['trade_date']
                names = list(whitelist)
                placeholders = ','.join(['%s'] * len(names))
                cursor.execute(
                    f"DELETE FROM industry_heat_daily_t WHERE trade_date=%s "
                    f"AND board_name NOT IN ({placeholders})",
                    [trade_date] + names)
                deleted = cursor.rowcount
                if deleted:
                    print(f"  清理非白名单旧记录: {deleted} 条")
        connection.commit()
        print(f"✅ 入库 {len(results)} 条行业热度记录")
    except Exception as e:
        print(f"❌ 入库失败: {e}")
        connection.rollback()
    finally:
        close_connection(connection)


def get_latest_trade_date():
    """从 stock_daily_t 取最新交易日（避免周末/假日 get_target_date 取到非交易日）。"""
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


def main():
    trade_date = sys.argv[1] if len(sys.argv) > 1 else get_latest_trade_date()
    print("=" * 60)
    print(f"股票行业短线热度分析 — 交易日 {trade_date}")
    print("=" * 60)

    results = compute_industry_heat(trade_date)
    if not results:
        print("⚠️ 无数据，退出")
        return

    print(f"\n共 {len(results)} 个板块（家数≥{MIN_STOCK_COUNT}）")
    print(f"{'板块':16} {'家数':>4} {'涨跌比':>6} {'中位%':>7} {'短线分':>7} {'热度分':>10}")
    print("-" * 60)
    for r in results[:15]:
        print(f"{r['board_name']:16} {r['stock_count']:>4} "
              f"{r['up_down_ratio'] or 0:>6.2f} "
              f"{r['pct_median'] or 0:>7.2f} "
              f"{r['avg_short_strength'] or 0:>7.1f} "
              f"{r['heat_score'] or 0:>10.1f}")

    save_to_db(results)
    print("\n🎉 完成")


if __name__ == "__main__":
    main()
