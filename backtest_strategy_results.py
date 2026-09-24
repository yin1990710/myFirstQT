#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股结果回测回填 (backtest_strategy_results.py)

核心功能：
  1. 读取 strategy_selected_stock_daily_t 表中最近 22 个交易日的选股记录。
  2. 读取 stock_daily_t 表中的收盘价数据。
  3. 以 strategy_selected_stock_daily_t 的 selected_date 为基准日期（T日），计算
     T 日后 10 个交易日（T+1~T+10）内和后 20 个交易日（T+1~T+20）内的
     最大涨幅和最大跌幅，回填 6 个回测指标：
    - max_gain_10d：T+1～T+10 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - max_down_10d：T+1～T+10 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - max_down_20d：T+1～T+20 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - max_gain_20d：T+1～T+20 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - sse_index_same_inc：T+1～T+10个交易日内上证指数涨幅（%，T+10日相对选股日T的上证指数 000001.SH 收盘涨跌幅）
    - chinext_index_same_inc：T+1～T+10个交易日内创业板指数涨幅（%，同上口径，创业板指 399006.SZ）
  4. 结果按主键（ts_code + selected_date）更新回 strategy_selected_stock_daily_t 表，并回填 compute_date（结果计算日期）。

日期范围参数化：
  python3 backtest_strategy_results.py                          # 默认：表内最近 22 个交易日的记录
  python3 backtest_strategy_results.py 20260701                 # 指定起始日：回测该日期（含）之后所有记录
  python3 backtest_strategy_results.py 20260701 20260815        # 指定起止日：回测区间内所有记录

回填规则：
  - 未来数据不足（T+10 / T+20 未完整落在已入库数据内）的字段保持 NULL，
    后续交易日数据更新后再次运行本脚本即可自动补齐。
  - 6 个字段都已回填的记录跳过，脚本可重复执行（幂等）。
  - 涨幅 =（窗口内收盘价 / T日收盘价 - 1）× 100，正负值均保留原值。
  - 同期指数涨幅 =（T+10 交易日指数收盘 / T日指数收盘 - 1）× 100，同一选股日全部记录取值相同，
    T 日后不足 10 个指数交易日时保持 NULL；数据源 index_daily_t（上证指数 000001.SH、创业板指 399006.SZ）。
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
SSE_INDEX_CODE = '000001.SH'      # 上证指数
CHINEXT_INDEX_CODE = '399006.SZ'  # 创业板指


def parse_args():
    parser = argparse.ArgumentParser(description='选股结果回测回填')
    parser.add_argument('start_date', nargs='?', default=None,
                        help='起始交易日 YYYYMMDD（含），缺省则取表内最近22个交易日')
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
    """确定要回测的 selected_date 集合（升序）。缺省取表内最近 22 个交易日（覆盖 T+20 刚满窗的日期）。"""
    if start_date is None:
        cursor.execute("""
            SELECT DISTINCT selected_date FROM strategy_selected_stock_daily_t
            ORDER BY selected_date DESC LIMIT %s
        """, (RECENT_TABLE_DAYS,))
    else:
        sql = ("SELECT DISTINCT selected_date FROM strategy_selected_stock_daily_t "
               "WHERE selected_date >= %s")
        params = [start_date]
        if end_date is not None:
            sql += " AND selected_date <= %s"
            params.append(end_date)
        sql += " ORDER BY selected_date"
        cursor.execute(sql, params)
    return sorted(r['selected_date'] for r in cursor.fetchall())


def fetch_records_by_dates(cursor, dates):
    """读取指定 selected_date 集合的选股记录。"""
    placeholders = ','.join(['%s'] * len(dates))
    cursor.execute(f"""
        SELECT ts_code, selected_date, compute_date, strategy, selected,
               max_gain_10d, max_down_10d, max_down_20d, max_gain_20d,
               sse_index_same_inc, chinext_index_same_inc
        FROM strategy_selected_stock_daily_t
        WHERE selected_date IN ({placeholders})
        ORDER BY selected_date, ts_code
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
    """T 日之后的全部收盘价序列（升序，不限窗口，由调用方截取前 10 / 20 日）。"""
    cursor.execute("""
        SELECT close FROM stock_daily_t
        WHERE ts_code=%s AND trade_date>%s AND close>0
        ORDER BY trade_date ASC
    """, (ts_code, trade_date))
    return [float(r['close']) for r in cursor.fetchall()]


def fetch_index_inc_map(cursor, dates):
    """计算各选股日 T 到 T+10 交易日的同期指数涨幅（%）。

    以 index_daily_t 上证指数交易日序列为日历（ROW_NUMBER 对齐），
    T+10 = T 在日历中后第 10 个交易日；返回 {selected_date: (sse_inc, chinext_inc)}。
    T 之后不足 10 个已入库指数交易日时该日不入表（对应字段保持 NULL）。
    """
    if not dates:
        return {}
    placeholders = ','.join(['%s'] * len(dates))
    cursor.execute(f"""
        WITH cal AS (
            SELECT trade_date,
                   ROW_NUMBER() OVER (ORDER BY trade_date) AS rn
            FROM (
                SELECT DISTINCT trade_date FROM index_daily_t
                WHERE ts_code = %s
            ) x
        )
        SELECT d.selected_date,
               (s10.close / s0.close - 1) * 100 AS sse_inc,
               (c10.close / c0.close - 1) * 100 AS chinext_inc
        FROM (
            SELECT DISTINCT selected_date FROM strategy_selected_stock_daily_t
            WHERE selected_date IN ({placeholders})
        ) d
        JOIN cal t0  ON t0.trade_date = d.selected_date COLLATE utf8mb4_unicode_ci
        JOIN cal t10 ON t10.rn = t0.rn + {FUTURE_DAYS_10}
        JOIN index_daily_t s0  ON s0.ts_code  = %s AND s0.trade_date  = t0.trade_date
        JOIN index_daily_t s10 ON s10.ts_code = %s AND s10.trade_date = t10.trade_date
        JOIN index_daily_t c0  ON c0.ts_code  = %s AND c0.trade_date  = t0.trade_date
        JOIN index_daily_t c10 ON c10.ts_code = %s AND c10.trade_date = t10.trade_date
    """, (SSE_INDEX_CODE, *dates,
          SSE_INDEX_CODE, SSE_INDEX_CODE,
          CHINEXT_INDEX_CODE, CHINEXT_INDEX_CODE))
    return {r['selected_date']: (float(r['sse_inc']), float(r['chinext_inc']))
            for r in cursor.fetchall()}


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

            # 同期指数涨幅 T+1~T+10（与个股无关，按选股日批量计算一次；不满T+10不在map中）
            index_inc_map = fetch_index_inc_map(cursor, dates)
            print(f"📅 指数侧已满足 T+10 完整窗口的选股日 {len(index_inc_map)} 个"
                  f"（其余保持 NULL，后续运行自动补齐）")

            to_process = [r for r in records
                          if (r['max_gain_10d'] is None or r['max_down_10d'] is None
                              or r['max_down_20d'] is None or r['max_gain_20d'] is None
                              or r['sse_index_same_inc'] is None
                              or r['chinext_index_same_inc'] is None)]
            done = len(records) - len(to_process)
            print(f"📊 已回填 {done} 条，待回填 {len(to_process)} 条")

            upd_10 = upd_20 = upd_td = 0
            skip_no_data = 0
            for r in to_process:
                base_close = fetch_base_close(cursor, r['ts_code'], r['selected_date'])
                # 个股T日数据缺失时仍可回填同期指数涨幅（该指标与个股行情无关）
                closes = (fetch_future_closes(cursor, r['ts_code'], r['selected_date'])
                          if base_close is not None else [])

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
                # 同期指数涨幅（T+1~T+10，按 selected_date 取值；与个股行情无关）
                index_inc = index_inc_map.get(r['selected_date'])
                if index_inc is not None:
                    sse_inc, chinext_inc = index_inc
                    if r['sse_index_same_inc'] is None:
                        sets.append("sse_index_same_inc=%s")
                        params.append(sse_inc)
                    if r['chinext_index_same_inc'] is None:
                        sets.append("chinext_index_same_inc=%s")
                        params.append(chinext_inc)
                if not sets and r.get('compute_date') is not None:
                    skip_no_data += 1
                    continue

                # 结果回填日期（本次 UPDATE 计算日）
                sets.append("compute_date=%s")
                params.append(datetime.now().strftime('%Y%m%d'))
                params += [r['ts_code'], r['selected_date']]
                cursor.execute(
                    f"UPDATE strategy_selected_stock_daily_t SET {', '.join(sets)} "
                    "WHERE ts_code=%s AND selected_date=%s", params)
                if 'max_gain_10d=%s' in sets or 'max_down_10d=%s' in sets:
                    upd_10 += 1
                if 'max_gain_20d=%s' in sets or 'max_down_20d=%s' in sets:
                    upd_20 += 1
                if ('sse_index_same_inc=%s' in sets
                        or 'chinext_index_same_inc=%s' in sets):
                    upd_td += 1

        conn.commit()
        print("\n" + "=" * 80)
        print("🎉 回测回填完成！")
        print(f"   - 10日窗口（max_gain_10d/max_down_10d，T+1~T+10）: {upd_10} 条")
        print(f"   - 20日窗口（max_down_20d/max_gain_20d，T+1~T+20）: {upd_20} 条")
        print(f"   - 同期指数涨幅（sse/chinext_index_same_inc，T+1~T+10）: {upd_td} 条")
        print(f"   - 未来数据不足暂跳过: {skip_no_data} 条（后续运行自动补齐）")
        print("=" * 80)
    except Exception as e:
        print(f"❌ 回测回填失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
