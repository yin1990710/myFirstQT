#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股结果回测指标回填 (update_strategy_selected_stock_daily.py)

功能：读取strategy_selected_stock_daily_t表中10个交易日前的T日(selected_date)的数据，
  回填 T+1～T+10 个交易日内回测指标。
  回填 strategy_selected_stock_daily_t 表中的回测指标：
    - max_gain_10d：T+1～T+10 个交易日内最高 close 相对 T 日 close 的最大涨幅（%）
    - max_down_10d：T+1～T+10 个交易日内最低 close 相对 T 日 close 的最大跌幅（%）
    - sse_index_same_inc：T+1～T+10个交易日内上证指数涨幅（%，T+10日相对选股日T的上证指数 000001.SH 收盘涨跌幅）
    - chinext_index_same_inc：T+1～T+10个交易日内创业板指数涨幅（%，同上口径，创业板指 399006.SZ）
    - compute_date：结果回填日期（YYYYMMDD），记录本次回写指标的实际执行日期

字段说明：
    - selected_date：选股日（原 trade_date），即策略选出该股的交易日
    - compute_date：结果回填日期，本次回写 max_gain_10d 等指标时的实际执行日期

日期范围：
  python3 update_strategy_selected_stock_daily.py                          # 默认：最新开市日往前第10个交易日（T+10恰为最新日）
  python3 update_strategy_selected_stock_daily.py --start-date 20260901     # 指定起始日（含），结束日同起始日
  python3 update_strategy_selected_stock_daily.py --start-date 20260901 --end-date 20260918  # 指定起止日

回填规则：
  - T 日之后须有完整 10 个已收盘交易日，不足 10 日的 4 个字段一律保持 NULL，
    后续交易日数据更新后再次运行本脚本即可自动补齐。
  - 已回填的字段跳过，脚本可重复执行（幂等）。
  - 每次回填时同步写入 compute_date 为当天日期（datetime.now().strftime('%Y%m%d')）。
  - 个股涨幅 =（T+1~T+10 窗口内最高/最低收盘价 / T日收盘价 - 1）× 100，正负值均保留原值。
  - 指数涨幅 =（T+10 交易日指数收盘 / T日指数收盘 - 1）× 100，同一选股日全部记录取值相同，
    数据源 index_daily_t（上证指数 000001.SH、创业板指 399006.SZ）。
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
SSE_INDEX_CODE = '000001.SH'      # 上证指数
CHINEXT_INDEX_CODE = '399006.SZ'  # 创业板指


# ---------- 日期工具 ----------

def get_target_trade_date():
    """获取默认目标选股日 T：最新开市日往前第 10 个开市日。

    该日的 T+10 恰好是最新开市日，保证回填窗口为完整的 10 个已收盘交易日。
    当前时间 0-15 点视为盘前，最新开市日取前一交易日。
    """
    now = datetime.now()
    if 0 <= now.hour < 15:
        end = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        end = now.strftime('%Y%m%d')
    start = (now - timedelta(days=30)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return end
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) <= FUTURE_DAYS_10:
        return opens[0] if opens else end
    return opens[-(FUTURE_DAYS_10 + 1)]


def parse_args():
    """解析命令行参数：位置参数 start_date / end_date，缺省取最新开市日往前第10个交易日。

    用法：
      python3 update_strategy_selected_stock_daily.py                          # 默认：最新开市日往前第10个交易日
      python3 update_strategy_selected_stock_daily.py 20260901                 # 从起始日到最新交易日
      python3 update_strategy_selected_stock_daily.py 20260901 20260918        # 指定起止日（含两端）
    """
    parser = argparse.ArgumentParser(description='选股结果 T+10 回测指标回填')
    parser.add_argument('start_date', nargs='?', default=None,
                        help='起始入选日 YYYYMMDD（含），缺省取最新开市日往前第10个交易日')
    parser.add_argument('end_date', nargs='?', default=None,
                        help='结束入选日 YYYYMMDD（含），缺省取最新开市日往前第10个交易日')
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
    """查询 strategy 表中落在 [start_date, end_date] 区间内的入选日集合（升序）。

    end_date 为 None 时表示不设上界（到表内最新日）。
    """
    if end_date:
        cursor.execute("""
            SELECT DISTINCT selected_date FROM strategy_selected_stock_daily_t
            WHERE selected_date >= %s AND selected_date <= %s
            ORDER BY selected_date
        """, (start_date, end_date))
    else:
        cursor.execute("""
            SELECT DISTINCT selected_date FROM strategy_selected_stock_daily_t
            WHERE selected_date >= %s
            ORDER BY selected_date
        """, (start_date,))
    return [r['selected_date'] for r in cursor.fetchall()]


def fetch_records_by_dates(cursor, dates):
    """读取指定交易日集合的选股记录。"""
    if not dates:
        return []
    placeholders = ','.join(['%s'] * len(dates))
    cursor.execute(f"""
        SELECT ts_code, selected_date, strategy, selected,
               max_gain_10d, max_down_10d,
               sse_index_same_inc, chinext_index_same_inc,
               compute_date
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
    """T 日之后的全部收盘价序列（升序，不限窗口）。"""
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


# ---------- 核心回填 ----------

def backfill_record(cursor, record, closes, base_close, index_inc):
    """对单条记录计算 T+1~T+10 窗口 4 个指标并写回，返回被更新的字段列表。

    个股 2 个指标要求 T 日后有完整 10 个个股收盘价；指数 2 个指标取按日预算的
    同期指数涨幅（要求指数侧 T+10 已入库）。两组互不阻塞，均仅在 NULL 时回填。
    """
    sets, params = [], []

    # 个股 T+1~T+10：最高 close 最大涨幅 + 最低 close 最大跌幅
    if base_close is not None and len(closes) >= FUTURE_DAYS_10:
        w10 = closes[:FUTURE_DAYS_10]
        if record['max_gain_10d'] is None:
            sets.append("max_gain_10d=%s")
            params.append((max(w10) / base_close - 1) * 100)
        if record['max_down_10d'] is None:
            sets.append("max_down_10d=%s")
            params.append((min(w10) / base_close - 1) * 100)

    # 指数 T+1~T+10 同期涨幅（与个股行情无关，按 selected_date 取值）
    if index_inc is not None:
        sse_inc, chinext_inc = index_inc
        if record['sse_index_same_inc'] is None:
            sets.append("sse_index_same_inc=%s")
            params.append(sse_inc)
        if record['chinext_index_same_inc'] is None:
            sets.append("chinext_index_same_inc=%s")
            params.append(chinext_inc)

    if not sets and record.get('compute_date') is not None:
        return []

    today = datetime.now().strftime('%Y%m%d')
    sets.insert(0, "compute_date=%s")
    params.insert(0, today)

    params += [record['ts_code'], record['selected_date']]
    cursor.execute(
        f"UPDATE strategy_selected_stock_daily_t SET {', '.join(sets)} "
        "WHERE ts_code=%s AND selected_date=%s", params)
    return sets


def main():
    args = parse_args()

    default_date = get_target_trade_date()
    # 无参数：默认单日（T+10恰为最新日，定时任务用）
    # 仅传起始日：从起始日到表内最新日
    # 传起止日：指定区间
    if not args.start_date and not args.end_date:
        start_date = end_date = default_date
    else:
        start_date = args.start_date or default_date
        end_date = args.end_date or None  # None 表示到表内最新日

    print("=" * 80)
    print("📈 选股结果 T+10 回测指标回填 (update_strategy_selected_stock_daily.py)")
    print("=" * 80)
    print(f"📅 回填区间: {start_date} ~ {end_date or '最新日'}")

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

            # 同期指数涨幅（与个股无关，按选股日批量计算一次；不满T+10的选股日不在map中）
            index_inc_map = fetch_index_inc_map(cursor, dates)
            print(f"📅 指数侧已满足 T+10 完整窗口的选股日 {len(index_inc_map)} 个"
                  f"（其余保持 NULL，后续运行自动补齐）")

            upd_stock = upd_index = 0
            skip_no_data = 0
            for r in records:
                base_close = fetch_base_close(cursor, r['ts_code'], r['selected_date'])
                # 个股T日数据缺失时仍可回填同期指数涨幅（该指标与个股行情无关）
                closes = (fetch_future_closes(cursor, r['ts_code'], r['selected_date'])
                          if base_close is not None else [])

                sets = backfill_record(cursor, r, closes, base_close,
                                       index_inc_map.get(r['selected_date']))
                if not sets:
                    skip_no_data += 1
                    continue

                if 'max_gain_10d=%s' in sets or 'max_down_10d=%s' in sets:
                    upd_stock += 1
                if ('sse_index_same_inc=%s' in sets
                        or 'chinext_index_same_inc=%s' in sets):
                    upd_index += 1

        conn.commit()
        print("\n" + "=" * 80)
        print("🎉 T+10 回测指标回填完成！")
        print(f"   - 个股10日窗口（max_gain_10d/max_down_10d）: {upd_stock} 条")
        print(f"   - 同期指数涨幅（sse/chinext_index_same_inc）: {upd_index} 条")
        print(f"   - 窗口不足10日暂跳过: {skip_no_data} 条（后续运行自动补齐）")
        print("=" * 80)
    except Exception as e:
        print(f"❌ 回测指标回填失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)


if __name__ == '__main__':
    main()
