#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
全市场短线强弱得分回填任务
================================================================
调用 module_stock_short_strength 模块，计算 stock_daily_t 中全部股票
的短线强弱得分（SSS，五维加权综合分 0-100），并回填到 stock_daily_t
的 short_strength_score 字段。

处理范围与口径：
  - 默认：只计算最新交易日（stock_daily_t 的 MAX(trade_date)）；
  - 指定 --start/--end 时：对时间段内（按 stock_daily_t 实际存在的
    交易日）的每一天 D 逐日计算——每只股票用「截至 D 的近 80 个交易
    日行情」评分，并回填 D 当日的行，支持历史补算；
  - 计算窗口与模块保持一致：近 80 个交易日（lookback 20 + 60 种子）；
  - 基准：沪深300（stock_index_daily_t 000300.SH）截至当日的近 20 日
    涨跌幅。

表结构（首次运行自动添加，幂等）：
  ALTER TABLE stock_daily_t ADD COLUMN short_strength_score DECIMAL(5,1)
  DEFAULT NULL COMMENT '短线强弱得分SSS(0-100,综合五维加权)'

用法：
  python3 update_stock_daily_short_strength.py            # 全市场评分回填（最新交易日）
  python3 update_stock_daily_short_strength.py --start 20260901 --end 20260917
                                                          # 回填时间段内每个交易日
  python3 update_stock_daily_short_strength.py --code 000001.SZ
                                                          # 单只诊断（不写库）
依赖：
  module_stock_short_strength.py  —— SSS 五维评分模块（纯计算层）
  module_mysql_connection.py             —— 数据库连接

⚠️ 全市场逐日评分约需 40~50 秒/交易日，时间段越长耗时越长。
"""

import argparse
import os
import re
import sys
import time

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from module_stock_short_strength import (
    PARAMS, calc_all_scores, safe_pct_change, fetch_benchmark,
)
from module_mysql_connection import get_mysql_connection, close_connection

DAYS = PARAMS["lookback"] + 60     # 与模块 fetch_one 一致：近 80 个交易日
BATCH_SIZE = 1000                  # 回填写入批次


# ---------------- 表结构 ----------------

def ensure_score_column(conn):
    """确保 stock_daily_t 存在 short_strength_score 列（幂等，缺失才加）。"""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'stock_daily_t'
              AND COLUMN_NAME = 'short_strength_score'
        """)
        if cursor.fetchone()["n"] > 0:
            print("✅ 列 short_strength_score 已存在")
            return
        cursor.execute("""
            ALTER TABLE stock_daily_t
            ADD COLUMN short_strength_score DECIMAL(5,1) DEFAULT NULL
            COMMENT '短线强弱得分SSS(0-100,综合五维加权)'
            AFTER qfq_adj_factor
        """)
    print("✅ 已新增列 short_strength_score DECIMAL(5,1)")


# ---------------- 数据读取 ----------------

def load_market_data(conn, start, end):
    """一次性读取评分所需数据。

    参数 start/end 为评分目标区间（None 表示默认最新交易日）。
    返回 (target_dates, grouped, bm_dates_need, latest)：
      target_dates  —— 待计算的交易日列表（升序）
      grouped       —— {ts_code: DataFrame} 全量日线（窗口起点到最后目标日）
      latest        —— stock_daily_t 最新交易日
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT MAX(trade_date) AS d FROM stock_daily_t")
        latest = cursor.fetchone()["d"]
        if not latest:
            print("❌ stock_daily_t 无数据")
            return [], {}, None

        # 目标交易日区间：默认只算最新一天
        if start and end:
            cursor.execute("""
                SELECT DISTINCT trade_date FROM stock_daily_t
                WHERE trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (start, end))
            target_dates = [r["trade_date"] for r in cursor.fetchall()]
            if not target_dates:
                print(f"❌ 区间 {start}~{end} 内无交易日数据")
                return [], {}, latest
        else:
            target_dates = [latest]

        # 窗口起点：第一个目标日之前（含）的近 DAYS 个交易日
        cursor.execute("""
            SELECT DISTINCT trade_date FROM stock_daily_t
            WHERE trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT %s
        """, (target_dates[0], DAYS))
        window_dates = [r["trade_date"] for r in cursor.fetchall()]
        window_start = window_dates[-1] if window_dates else target_dates[0]

        print(f"📦 读取 {window_start} ~ {target_dates[-1]} 全市场日线"
              f"（窗口 {len(window_dates)} 个交易日 + 目标 {len(target_dates)} 天）…")
        cursor.execute("""
            SELECT ts_code, trade_date, open, high, low, close, vol, amount
            FROM stock_daily_t
            WHERE trade_date >= %s AND trade_date <= %s
            ORDER BY ts_code, trade_date
        """, (window_start, target_dates[-1]))
        rows = cursor.fetchall()

    df_all = pd.DataFrame(rows)
    for col in ("open", "high", "low", "close", "vol", "amount"):
        df_all[col] = pd.to_numeric(df_all[col], errors="coerce")
    grouped = {code: g for code, g in df_all.groupby("ts_code", sort=False)}
    return target_dates, grouped, latest


# ---------------- 评分与回填 ----------------

def score_day(codes_today, grouped, day, bm_ret):
    """计算某一交易日的全市场评分，返回 [(score, ts_code), ...]（仅有效分）。

    每只股票取「截至 day 的近 DAYS 个交易日」切片（与模块 fetch_one
    的取数口径一致），保证任意历史日得分可复算。
    """
    updates = []
    for code in codes_today:
        g = grouped.get(code)
        if g is None:
            continue
        sub = g[g["trade_date"] <= day].tail(DAYS)
        if len(sub) < 2:
            continue
        try:
            r = calc_all_scores(sub, bm_ret, code=code)
        except Exception:
            continue
        if r.composite is not None and not pd.isna(r.composite):
            updates.append((round(float(r.composite), 1), code))
    return updates


def write_scores(updates, day):
    """将某交易日的得分批量回填（按 ts_code + trade_date 定位）。"""
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return False
    try:
        with conn.cursor() as cursor:
            sql = """
                UPDATE stock_daily_t
                SET short_strength_score = %s
                WHERE ts_code = %s AND trade_date = %s
            """
            params = [(s, c, day) for s, c in updates]
            for i in range(0, len(params), BATCH_SIZE):
                cursor.executemany(sql, params[i:i + BATCH_SIZE])
                conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"❌ 回填失败({day}): {e}")
        return False
    finally:
        close_connection(conn)
    return True


# ---------------- 主流程 ----------------

def main():
    parser = argparse.ArgumentParser(description="全市场短线强弱得分回填")
    parser.add_argument("--start", default=None,
                        help="开始日期 YYYYMMDD（含），与--end搭配计算时间段")
    parser.add_argument("--end", default=None,
                        help="结束日期 YYYYMMDD（含），默认最新交易日")
    parser.add_argument("--code", default=None,
                        help="仅诊断单只股票（不写库），如 000001.SZ")
    args = parser.parse_args()

    print("=" * 72)
    print("📊 stock_daily_t 短线强弱得分（SSS）全市场回填任务")
    print("=" * 72)

    # 单股诊断模式：不走回填
    if args.code:
        from module_stock_short_strength import evaluate_one, print_one
        r = evaluate_one(args.code, verbose=True)
        print_one(r)
        return 0

    # 日期参数校验（支持只给 --start：单日补算；只给 --end 视为无效）
    start, end = args.start, args.end
    for label, d in (("--start", start), ("--end", end)):
        if d and not re.match(r"^\d{8}$", d):
            print(f"❌ {label} 日期格式应为 YYYYMMDD，收到：{d}")
            return 1
    if start and not end:
        end = start
    if end and not start:
        print("❌ 只给了 --end，请同时给 --start（或都不给表示仅最新交易日）")
        return 1
    if start and end and start > end:
        print(f"❌ 开始日期 {start} 晚于结束日期 {end}")
        return 1

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return 1
    try:
        ensure_score_column(conn)
        target_dates, grouped, latest = load_market_data(conn, start, end)
    finally:
        close_connection(conn)
    if not target_dates:
        return 1
    span_desc = (f"{target_dates[0]} ~ {target_dates[-1]} 共 {len(target_dates)} 个交易日"
                 if len(target_dates) > 1 else f"最新交易日 {latest}")
    print(f"✅ 待评分股票：{len(grouped)} 只（{span_desc}），"
          f"约需 {40 * len(target_dates)}~{50 * len(target_dates)} 秒")

    # 基准：沪深300 收盘序列（一次性取足整个区间 + 窗口缓冲）
    bm_days = DAYS + len(target_dates) + 10
    bm = fetch_benchmark(PARAMS["benchmark"], days=bm_days)
    print(f"📈 基准 {PARAMS['benchmark']}：取 {len(bm)} 个交易日收盘")

    t0 = time.time()
    total_updates = 0
    # 预构建每个交易日实际有行情的股票列表（停牌股当日不评分）
    codes_by_day = {}
    for code, g in grouped.items():
        for d in g["trade_date"]:
            codes_by_day.setdefault(d, []).append(code)

    for idx, day in enumerate(target_dates, 1):
        t1 = time.time()
        codes_today = codes_by_day.get(day, [])
        if not codes_today:
            print(f"⚠️ {day} 无行情数据，跳过")
            continue
        bm_ret = safe_pct_change(bm[bm.index <= day], PARAMS["lookback"])
        if pd.isna(bm_ret):
            print(f"⚠️ {day} 基准数据不足，跳过该日")
            continue
        updates = score_day(codes_today, grouped, day, bm_ret)
        if not updates:
            print(f"⚠️ {day} 无有效评分，跳过回填")
            continue
        if not write_scores(updates, day):
            return 1
        total_updates += len(updates)
        top1 = max(updates)
        print(f"📅 [{idx}/{len(target_dates)}] {day}：评分回填 {len(updates)} 只，"
              f"最高分 {top1[0]:.1f}（{top1[1]}），耗时 {time.time()-t1:.0f} 秒")

    print(f"\n✅ 全部完成：{len(target_dates)} 个交易日，累计回填 "
          f"{total_updates} 条，总耗时 {time.time()-t0:.0f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
