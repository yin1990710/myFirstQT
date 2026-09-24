#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
策略结果分析 · 数据计算层 (calc_strategy_result_analysis.py)

功能：
  按策略聚合 strategy_selected_stock_daily_t 中的 max_gain_10d，
  计算每种策略选出的股票在选入交易日后 10 个交易日内的算术平均涨幅，
  结果 UPSERT 写入 strategy_result_analysis_t 表。

策略字段说明：
  strategy_selected_stock_daily_t.strategy 是逗号分隔的多策略名
  （如「当日涨停选股策略,高换手率选股策略」），同一行记录的 max_gain_10d
  会按 FIND_IN_SET 归属到其包含的每一个策略。

用法：
  python3 calc_strategy_result_analysis.py        # 全量重算（默认）
  python3 calc_strategy_result_analysis.py -v     # 详细输出

输出表 strategy_result_analysis_t 字段：
  - strategy        策略名称（唯一键）
  - avg_gain_10d    10日算术平均涨幅（%）
  - stock_count     样本股票数（max_gain_10d 非空的记录数）
  - latest_trade_date  该策略最新选入交易日
  - update_time     更新时间（自动）
"""

import sys
import os
import argparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection


def aggregate_strategy_gains(cursor):
    """从 strategy_selected_stock_daily_t 聚合各策略的 10 日平均涨幅。

    返回：list[dict]，每个 dict 含 strategy / avg_gain_10d / stock_count / latest_trade_date。
    """
    cursor.execute("""
        SELECT strategy, max_gain_10d, selected_date
        FROM strategy_selected_stock_daily_t
        WHERE max_gain_10d IS NOT NULL
    """)
    rows = cursor.fetchall()

    # strategy 逗号串拆分 + 涨幅归属
    agg = {}  # {strategy_name: {'gains': [], 'latest_date': ''}}
    for r in rows:
        strat_field = (r.get('strategy') or '').strip()
        gain = r.get('max_gain_10d')
        selected_date = r.get('selected_date') or ''
        if gain is None:
            continue
        for s in strat_field.split(','):
            s = s.strip()
            if not s:
                continue
            if s not in agg:
                agg[s] = {'gains': [], 'latest_date': ''}
            agg[s]['gains'].append(float(gain))
            if selected_date > agg[s]['latest_date']:
                agg[s]['latest_date'] = selected_date

    result = []
    for name, info in agg.items():
        gains = info['gains']
        result.append({
            'strategy': name,
            'avg_gain_10d': round(sum(gains) / len(gains), 4),
            'stock_count': len(gains),
            'latest_trade_date': info['latest_date'],
        })
    result.sort(key=lambda x: x['avg_gain_10d'], reverse=True)
    return result


def upsert_analysis(cursor, records):
    """将聚合结果 UPSERT 写入 strategy_result_analysis_t。"""
    if not records:
        return 0
    sql = """
        INSERT INTO strategy_result_analysis_t
            (strategy, avg_gain_10d, stock_count, latest_trade_date)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            avg_gain_10d = VALUES(avg_gain_10d),
            stock_count = VALUES(stock_count),
            latest_trade_date = VALUES(latest_trade_date)
    """
    params = [(r['strategy'], r['avg_gain_10d'], r['stock_count'],
               r['latest_trade_date']) for r in records]
    cursor.executemany(sql, params)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description='策略结果分析数据计算')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    args = parser.parse_args()

    print("=" * 70)
    print("📊 策略结果分析 · 数据计算 (calc_strategy_result_analysis.py)")
    print("=" * 70)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    try:
        with conn.cursor() as cursor:
            records = aggregate_strategy_gains(cursor)
            if not records:
                print("⚠️ 无可用的 max_gain_10d 数据（需先运行 update_strategy_selected_stock_daily.py 回填）")
                return
            upserted = upsert_analysis(cursor, records)
        conn.commit()
        print(f"✅ 计算完成，共 {upserted} 个策略写入 strategy_result_analysis_t")
        if args.verbose:
            print("\n策略明细（按平均涨幅降序）：")
            for r in records:
                print(f"  {r['strategy']:<20} avg={r['avg_gain_10d']:>8.4f}%  count={r['stock_count']:>4}  latest={r['latest_trade_date']}")
    except Exception as e:
        print(f"❌ 计算失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)


if __name__ == '__main__':
    main()
