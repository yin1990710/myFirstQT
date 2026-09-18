#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股结果回测回填 (backtest_strategy_results.py)

核心功能：
  1. 读取 strategy_selected_stock_daily_t 表中最近 22 个交易日的选股记录。
  2. 读取 stock_daily_t 表中的收盘价数据。
  3. 以 strategy_selected_stock_daily_t 的 trade_date 为基准日期（T日），计算
     T 日后 10 个交易日（T+1~T+10）内和后 20 个交易日（T+1~T+20）内的
     最大涨幅和最大跌幅，回填 4 个回测指标：
    - max_gain_10d：T+1～T+10 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - max_down_10d：T+1～T+10 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - max_down_20d：T+1～T+20 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - max_gain_20d：T+1～T+20 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
  4. 结果按主键（ts_code + trade_date）更新回 strategy_selected_stock_daily_t 表。

日期范围参数化：
  python3 backtest_strategy_results.py                          # 默认：表内最近 22 个交易日的记录
  python3 backtest_strategy_results.py 20260701                 # 指定起始日：回测该日期（含）之后所有记录
  python3 backtest_strategy_results.py 20260701 20260815        # 指定起止日：回测区间内所有记录

回填规则：
  - 未来数据不足（T+10 / T+20 未完整落在已入库数据内）的字段保持 NULL，
    后续交易日数据更新后再次运行本脚本即可自动补齐。
  - 4 个字段都已回填的记录跳过，脚本可重复执行（幂等）。
  - 涨幅 =（窗口内收盘价 / T日收盘价 - 1）× 100，正负值均保留原值。
"""

import sys
import os
import argparse
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

RECENT_TABLE_DAYS = 22   # 默认：strategy 表最近 22 个交易日
FUTURE_DAYS_10 = 10      # T+1～T+10
FUTURE_DAYS_20 = 20      # T+1～T+20


def parse_args():
    parser = argparse.ArgumentParser(description='选股结果回测回填')
    parser.add_argument('start_date', nargs='?', default=None,
                        help='起始交易日 YYYYMMDD（含），缺省则取表内最近20个交易日')
    parser.add_argument('end_date', nargs='?', default=None,
                        help='结束交易日 YYYYMMDD（含），可选')
    args = parser.parse_args()
    for d in (args.start_date, args.end_date):
        if d is not None:
            try:
                datetime.strptime(d, '%Y%m%d')
            except ValueError:
                parser.error(f"日期格式应为 YYYYMMDD: {d}")
    if (args.start_date and args.end_date
            and args.start_date > args.end_date):
        parser.error("起始日期不能晚于结束日期")
    return args


def fetch_target_dates(cursor, start_date=None, end_date=None):
    """确定要回测的 trade_date 集合（升序）。缺省取表内最近 20 个交易日。"""
    if start_date is None:
        cursor.execute("""
            SELECT DISTINCT trade_date FROM strategy_selected_stock_daily_t
            ORDER BY trade_date DESC LIMIT %s
        """, (RECENT_TABLE_DAYS,))
    else:
        sql = ("SELECT DISTINCT trade_date FROM strategy_selected_stock_daily_t "
               "WHERE trade_date >= %s")
        params = [start_date]
        if end_date is not None:
            sql += " AND trade_date <= %s"
            params.append(end_date)
        sql += " ORDER BY trade_date"
        cursor.execute(sql, params)
    return sorted(r['trade_date'] for r in cursor.fetchall())


def fetch_records_by_dates(cursor, dates):
    """读取指定 trade_date 集合的选股记录。"""
    placeholders = ','.join(['%s'] * len(dates))
    cursor.execute(f"""
        SELECT ts_code, trade_date, strategy, selected,
               max_gain_10d, max_down_10d, max_down_20d, max_gain_20d,
               max_gain_to_date, max_down_to_date
        FROM strategy_selected_stock_daily_t
        WHERE trade_date IN ({placeholders})
        ORDER BY trade_date, ts_code
    """, dates)
    return cursor.fetchall()


def fetch_base_close(cursor, ts_code, trade_date):
    """T 日收盘价。"""
    cursor.execute(
        "SELECT close FROM stock_daily_t WHERE ts_code=%s AND trade_date=%s",
        (ts_code, trade_date))
    row = cursor.fetchone()
    if not row or not row['close'] or float(row['close']) <= 0:
        return None
    return float(row['close'])


def fetch_future_closes(cursor, ts_code, trade_date):
    """T 日之后的全部收盘价序列（升序，不限窗口，用于「至今」计算）。"""
    cursor.execute("""
        SELECT close FROM stock_daily_t
        WHERE ts_code=%s AND trade_date>%s AND close>0
        ORDER BY trade_date ASC
    """, (ts_code, trade_date))
    return [float(r['close']) for r in cursor.fetchall()]


def main():
    args = parse_args()

    print("=" * 80)
    print("📈 选股结果回测回填 (backtest_strategy_results.py)")
    print("=" * 80)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    try:
        with conn.cursor() as cursor:
            dates = fetch_target_dates(cursor, args.start_date, args.end_date)
            if not dates:
                print("⚠️ 未找到符合条件的记录，退出")
                return

            if args.start_date is None:
                print(f"\n📅 模式: 默认（表内最近{RECENT_TABLE_DAYS}个交易日）")
            else:
                end_txt = args.end_date if args.end_date else "至今"
                print(f"\n📅 模式: 指定日期范围 {args.start_date} ~ {end_txt}")
            a_day = dates[-1]
            print(f"📅 回测区间: {dates[0]} ~ {a_day}（共{len(dates)}个交易日）")

            records = fetch_records_by_dates(cursor, dates)
            print(f"📊 共 {len(records)} 条记录")

            to_process = [r for r in records
                          if (r['max_gain_10d'] is None or r['max_down_10d'] is None
                              or r['max_down_20d'] is None or r['max_gain_20d'] is None
                              or r['max_gain_to_date'] is None
                              or r['max_down_to_date'] is None)]
            done = len(records) - len(to_process)
            print(f"📊 已回填 {done} 条，待回填 {len(to_process)} 条")

            upd_10 = upd_20 = upd_td = 0
            skip_no_data = 0
            for r in to_process:
                base_close = fetch_base_close(cursor, r['ts_code'], r['trade_date'])
                if base_close is None:
                    skip_no_data += 1
                    continue
                closes = fetch_future_closes(cursor, r['ts_code'], r['trade_date'])

                sets, params = [], []
                # 10日窗口（T+1~T+10）：最大涨幅 + 最大跌幅
                if (r['max_gain_10d'] is None or r['max_down_10d'] is None) \
                        and len(closes) >= FUTURE_DAYS_10:
                    w10 = closes[:FUTURE_DAYS_10]
                    if r['max_gain_10d'] is None:
                        sets.append("max_gain_10d=%s")
                        params.append((max(w10) / base_close - 1) * 100)
                    if r['max_down_10d'] is None:
                        sets.append("max_down_10d=%s")
                        params.append((min(w10) / base_close - 1) * 100)
                # 20日窗口（T+1~T+20）：最大跌幅 + 最大涨幅
                if (r['max_down_20d'] is None or r['max_gain_20d'] is None) \
                        and len(closes) >= FUTURE_DAYS_20:
                    w20 = closes[:FUTURE_DAYS_20]
                    if r['max_down_20d'] is None:
                        sets.append("max_down_20d=%s")
                        params.append((min(w20) / base_close - 1) * 100)
                    if r['max_gain_20d'] is None:
                        sets.append("max_gain_20d=%s")
                        params.append((max(w20) / base_close - 1) * 100)
                # 至今窗口（T+1~最新）：全部未来收盘价的最大涨幅 + 最大跌幅
                if (r['max_gain_to_date'] is None or r['max_down_to_date'] is None) \
                        and len(closes) >= 1:
                    if r['max_gain_to_date'] is None:
                        sets.append("max_gain_to_date=%s")
                        params.append((max(closes) / base_close - 1) * 100)
                    if r['max_down_to_date'] is None:
                        sets.append("max_down_to_date=%s")
                        params.append((min(closes) / base_close - 1) * 100)
                if not sets:
                    skip_no_data += 1
                    continue

                params += [r['ts_code'], r['trade_date']]
                cursor.execute(
                    f"UPDATE strategy_selected_stock_daily_t SET {', '.join(sets)} "
                    "WHERE ts_code=%s AND trade_date=%s", params)
                if 'max_gain_10d=%s' in sets or 'max_down_10d=%s' in sets:
                    upd_10 += 1
                if 'max_gain_20d=%s' in sets or 'max_down_20d=%s' in sets:
                    upd_20 += 1
                if 'max_gain_to_date=%s' in sets or 'max_down_to_date=%s' in sets:
                    upd_td += 1

        conn.commit()
        print("\n" + "=" * 80)
        print("🎉 回测回填完成！")
        print(f"   - 10日窗口（max_gain_10d/max_down_10d，T+1~T+10）: {upd_10} 条")
        print(f"   - 20日窗口（max_down_20d/max_gain_20d，T+1~T+20）: {upd_20} 条")
        print(f"   - 至今窗口（max_gain_to_date/max_down_to_date，T+1~最新）: {upd_td} 条")
        print(f"   - 未来数据不足暂跳过: {skip_no_data} 条（后续运行自动补齐）")
        print("=" * 80)
    except Exception as e:
        print(f"❌ 回测回填失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
