#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股结果回测指标回填 (update_strategy_selected_stock_daily.py)

功能：
  回填 strategy_selected_stock_daily_t 表中的回测指标：
    - max_gain_10d：T+1～T+10 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - max_down_10d：T+1～T+10 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - max_gain_20d：T+1～T+20 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - max_down_20d：T+1～T+20 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - max_gain_to_date：T+1～最新交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - max_down_to_date：T+1～最新交易日内最低 close 相对 T 日 close 的最大跌幅（%）

日期范围：
  python3 update_strategy_selected_stock_daily.py                          # 默认：最近一个交易日
  python3 update_strategy_selected_stock_daily.py --start-date 20260901     # 指定起始日（含），结束日默认最近交易日
  python3 update_strategy_selected_stock_daily.py --start-date 20260901 --end-date 20260918  # 指定起止日

回填规则：
  - 未来数据不足（T+10 / T+20 未完整落在已入库数据内）的字段保持 NULL，
    后续交易日数据更新后再次运行本脚本即可自动补齐。
  - 已回填的字段跳过，脚本可重复执行（幂等）。
  - 涨幅 =（窗口内收盘价 / T日收盘价 - 1）× 100，正负值均保留原值。
"""

import sys
import os
import argparse
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- 常量 ----------
FUTURE_DAYS_10 = 10   # T+1～T+10
FUTURE_DAYS_20 = 20   # T+1～T+20


# ---------- 日期工具 ----------

def get_latest_trade_date():
    """获取最近一个交易日（tushare 交易日历）。

    当前时间 0-15 点取前一日，再通过交易日历向前回溯到最近一个开市日。
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


def parse_args():
    """解析命令行参数：--start-date / --end-date，缺省取最近一个交易日。"""
    parser = argparse.ArgumentParser(description='选股结果回测指标回填')
    parser.add_argument('--start-date', type=str, default=None,
                        help='起始交易日 YYYYMMDD（含），缺省取最近一个交易日')
    parser.add_argument('--end-date', type=str, default=None,
                        help='结束交易日 YYYYMMDD（含），缺省取最近一个交易日')
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


# ---------- 数据查询 ----------

def fetch_target_dates(cursor, start_date, end_date):
    """查询 strategy 表中落在 [start_date, end_date] 区间内的交易日集合（升序）。"""
    cursor.execute("""
        SELECT DISTINCT trade_date FROM strategy_selected_stock_daily_t
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    """, (start_date, end_date))
    return [r['trade_date'] for r in cursor.fetchall()]


def fetch_records_by_dates(cursor, dates):
    """读取指定交易日集合的选股记录。"""
    if not dates:
        return []
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
    """T 日之后的全部收盘价序列（升序，不限窗口）。"""
    cursor.execute("""
        SELECT close FROM stock_daily_t
        WHERE ts_code=%s AND trade_date>%s AND close>0
        ORDER BY trade_date ASC
    """, (ts_code, trade_date))
    return [float(r['close']) for r in cursor.fetchall()]


# ---------- 核心回填 ----------

def backfill_record(cursor, record, closes, base_close):
    """对单条记录计算各窗口指标并写回，返回被更新的字段列表。"""
    sets, params = [], []

    # 10日窗口（T+1~T+10，用已有未来日最多取 10 个；不足 10 日时用实际已有日数，
    # 后续数据补齐后再次运行会刷新为完整 10 日窗口结果）
    if len(closes) >= 1:
        w10 = closes[:FUTURE_DAYS_10]
        sets.append("max_gain_10d=%s")
        params.append((max(w10) / base_close - 1) * 100)
        sets.append("max_down_10d=%s")
        params.append((min(w10) / base_close - 1) * 100)

    # 20日窗口（T+1~T+20）
    if (record['max_down_20d'] is None or record['max_gain_20d'] is None) \
            and len(closes) >= FUTURE_DAYS_20:
        w20 = closes[:FUTURE_DAYS_20]
        if record['max_down_20d'] is None:
            sets.append("max_down_20d=%s")
            params.append((min(w20) / base_close - 1) * 100)
        if record['max_gain_20d'] is None:
            sets.append("max_gain_20d=%s")
            params.append((max(w20) / base_close - 1) * 100)

    # 至今窗口（T+1~最新）
    if (record['max_gain_to_date'] is None or record['max_down_to_date'] is None) \
            and len(closes) >= 1:
        if record['max_gain_to_date'] is None:
            sets.append("max_gain_to_date=%s")
            params.append((max(closes) / base_close - 1) * 100)
        if record['max_down_to_date'] is None:
            sets.append("max_down_to_date=%s")
            params.append((min(closes) / base_close - 1) * 100)

    if not sets:
        return []

    params += [record['ts_code'], record['trade_date']]
    cursor.execute(
        f"UPDATE strategy_selected_stock_daily_t SET {', '.join(sets)} "
        "WHERE ts_code=%s AND trade_date=%s", params)
    return sets


def main():
    args = parse_args()

    default_date = get_latest_trade_date()
    start_date = args.start_date or default_date
    end_date = args.end_date or default_date

    print("=" * 80)
    print("📈 选股结果回测指标回填 (update_strategy_selected_stock_daily.py)")
    print("=" * 80)
    print(f"📅 回填区间: {start_date} ~ {end_date}")

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    try:
        with conn.cursor() as cursor:
            dates = fetch_target_dates(cursor, start_date, end_date)
            if not dates:
                print("⚠️ 该区间内无选股记录，退出")
                return
            print(f"📅 命中交易日: {dates[0]} ~ {dates[-1]}（共{len(dates)}个交易日）")

            records = fetch_records_by_dates(cursor, dates)
            print(f"📊 共 {len(records)} 条记录")

            # 10日窗口每次都刷新（随未来数据增多而更新）；20日/至今窗口仅在 NULL 时回填
            to_process = [r for r in records
                          if (r['max_down_20d'] is None or r['max_gain_20d'] is None
                              or r['max_gain_to_date'] is None
                              or r['max_down_to_date'] is None
                              or True)]  # 始终包含，用于刷新 10 日窗口
            done = 0  # 10日窗口每次都刷新，无"已回填"概念
            print(f"📊 待处理 {len(to_process)} 条（10日窗口每次刷新，20日/至今仅补 NULL）")

            upd_10 = upd_20 = upd_td = 0
            skip_no_data = 0
            for r in to_process:
                base_close = fetch_base_close(cursor, r['ts_code'], r['trade_date'])
                if base_close is None:
                    skip_no_data += 1
                    continue
                closes = fetch_future_closes(cursor, r['ts_code'], r['trade_date'])

                sets = backfill_record(cursor, r, closes, base_close)
                if not sets:
                    skip_no_data += 1
                    continue

                if 'max_gain_10d=%s' in sets or 'max_down_10d=%s' in sets:
                    upd_10 += 1
                if 'max_gain_20d=%s' in sets or 'max_down_20d=%s' in sets:
                    upd_20 += 1
                if 'max_gain_to_date=%s' in sets or 'max_down_to_date=%s' in sets:
                    upd_td += 1

        conn.commit()
        print("\n" + "=" * 80)
        print("🎉 回测指标回填完成！")
        print(f"   - 10日窗口（T+1~T+10）: {upd_10} 条")
        print(f"   - 20日窗口（T+1~T+20）: {upd_20} 条")
        print(f"   - 至今窗口（T+1~最新）: {upd_td} 条")
        print(f"   - 未来数据不足暂跳过: {skip_no_data} 条（后续运行自动补齐）")
        print("=" * 80)
    except Exception as e:
        print(f"❌ 回测指标回填失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)


if __name__ == '__main__':
    main()
