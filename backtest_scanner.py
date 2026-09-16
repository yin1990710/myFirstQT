#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
策略回测扫描器 (backtest_scanner.py)

不依赖 strategy_selected_stock_daily_t 表，直接从 stock_daily_t 扫描历史数据。
对日期范围内每个交易日 D，截取 trade_date <= D 的数据切片传给策略 analyze 函数，
模拟"当日执行"的行为，收集符合条件的 (ts_code, trade_date, stock_name, strategy) 记录。

用法：
  from backtest_scanner import scan_strategy
  records = scan_strategy('120日区间新高选股策略', '20260201', '20260331')
  # → [{'ts_code': '000001.SZ', 'stock_name': '平安银行', 'trade_date': '20260205', ...}, ...]
"""

import importlib
import math
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from mysql_connection import get_mysql_connection, close_connection


# ===============================================================
# 策略注册表：策略中文名 → {模块名, analyze 函数名, 所需最小回看天数, 策略标签}
# 每个策略 analyze 函数输入 list[dict]（DB 行记录），输出 list[dict]（含 ts_code, stock_name）
# ===============================================================
STRATEGY_REGISTRY = {
    '120日区间新高选股策略': {
        'module': 'select_newhigh_in_120d',
        'analyze': 'analyze_newhigh_stocks',
        'min_lookback': 170,
        'type': 'df',   # 输入 DataFrame
        'label': '120日区间新高选股策略',
    },
    '当日涨停选股策略': {
        'module': 'select_limitup_1d',
        'analyze': 'analyze_stocks',
        'min_lookback': 5,
        'type': 'list',  # 输入 list[dict]
        'label': '当日涨停选股策略',
    },
    '底部反弹选股策略': {
        'module': 'select_bottom_bounce',
        'analyze': 'analyze_stocks',
        'min_lookback': 170,
        'type': 'list',
        'label': '底部反弹选股策略',
    },
    '二浪启动选股策略': {
        'module': 'select_2wave_up',
        'analyze': 'analyze_stocks',
        'min_lookback': 170,
        'type': 'list',
        'label': '二浪启动选股策略',
    },
    '二浪日线选股策略': {
        'module': 'select_2wave_daily',
        'analyze': 'analyze_stocks',
        'min_lookback': 170,
        'type': 'list',
        'label': '二浪日线选股策略',
    },
    '高换手率选股策略': {
        'module': 'select_high_exchange',
        'analyze': 'analyze_stocks',
        'min_lookback': 60,
        'type': 'list',
        'label': '高换手率选股策略',
    },
    'V型反转选股策略': {
        'module': 'select_v_reverse',
        'analyze': 'analyze_stocks',
        'min_lookback': 170,
        'type': 'list',
        'label': 'V型反转选股策略',
    },
    'W23二浪选股策略': {
        'module': 'select_2wave_w23',
        'analyze': 'analyze_stocks',
        'min_lookback': 200,
        'type': 'list',
        'label': 'W23二浪选股策略',
    },
}


def get_trade_dates(start_date, end_date):
    """从 stock_daily_t 取 [start_date, end_date] 范围内的交易日列表。"""
    conn = get_mysql_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT trade_date FROM stock_daily_t
                WHERE trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (start_date, end_date))
            return [r['trade_date'] for r in cur.fetchall()]
    finally:
        close_connection(conn)


def _load_full_data(end_date, lookback_days):
    """一次性取出 [end_date - lookback, end_date] 区间全部股票日线 + 市值 + 名称。

    返回 list[dict]，每行含：ts_code, trade_date, open, high, low, close, pre_close,
    vol, amount, pct_chg, ma5, ma30, qfq_adj_factor, turning_point, total_mv, circ_mv,
    turnover_rate_f, stock_name
    """
    # 用自然日粗估起始日期，确保能覆盖足够交易日
    start_approx = (datetime.strptime(end_date, '%Y%m%d')
                    - timedelta(days=int(lookback_days * 1.8))).strftime('%Y%m%d')

    conn = get_mysql_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    d.ts_code, d.trade_date,
                    d.open, d.high, d.low, d.close, d.pre_close,
                    d.vol, d.amount, d.pct_chg,
                    d.ma5, d.ma30, d.qfq_adj_factor, d.turning_point,
                    b.total_mv, b.circ_mv, b.turnover_rate_f,
                    i.stock_name
                FROM stock_daily_t d
                LEFT JOIN stock_daily_basic_info_t b
                  ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
                LEFT JOIN stock_info_t i
                  ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
                WHERE d.trade_date >= %s AND d.trade_date <= %s
                ORDER BY d.ts_code, d.trade_date
            """, (start_approx, end_date))
            return cur.fetchall()
    finally:
        close_connection(conn)


def _records_to_df(records):
    """list[dict] → pandas DataFrame。"""
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ('open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount',
                'pct_chg', 'ma5', 'ma30', 'qfq_adj_factor',
                'total_mv', 'circ_mv', 'turnover_rate_f'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df


def _call_analyze(strategy_name, data_slice, target_date=None):
    """动态调用策略 analyze 函数，兼容 DataFrame 和 list[dict] 两种输入。

    关键：策略 analyze 函数内部常调用 get_target_date() / get_trade_date()
    返回"今天"，扫描模式下需要替换为当前扫描日期 target_date。
    这里通过猴子补丁覆盖模块级函数。

    返回 list[dict]，每个元素至少含 ts_code, stock_name。
    """
    reg = STRATEGY_REGISTRY.get(strategy_name)
    if not reg:
        return []

    try:
        mod = importlib.import_module(reg['module'])
        func = getattr(mod, reg['analyze'])
    except (ImportError, AttributeError) as e:
        print(f"⚠️ 加载策略 {strategy_name} 失败: {e}")
        return []

    # 猴子补丁：覆盖模块中的日期函数，让策略以 target_date 为"今天"
    patched = []
    if target_date:
        for fn_name in ('get_target_date', 'get_trade_date'):
            if hasattr(mod, fn_name):
                original = getattr(mod, fn_name)
                setattr(mod, fn_name, lambda: target_date)
                patched.append((mod, fn_name, original))

    try:
        if reg['type'] == 'df':
            # DataFrame 输入
            df = _records_to_df(data_slice)
            if df.empty:
                return []
            result = func(df)
        else:
            # list[dict] 输入
            result = func(data_slice or [])
    except Exception as e:
        print(f"⚠️ 策略 {strategy_name} analyze 执行异常: {e}")
        return []
    finally:
        # 恢复被猴子补丁覆盖的日期函数
        for mod, fn_name, original in patched:
            setattr(mod, fn_name, original)

    if not result:
        return []

    # 统一输出格式：确保每条含 ts_code, stock_name
    normalized = []
    for r in result:
        if isinstance(r, dict) and r.get('ts_code'):
            normalized.append({
                'ts_code': r['ts_code'],
                'stock_name': r.get('stock_name', ''),
            })
    return normalized


def scan_strategy(strategy_name, start_date, end_date, progress_cb=None):
    """扫描指定策略在日期范围内的选股记录。

    参数：
      strategy_name: 策略中文名（需在 STRATEGY_REGISTRY 中注册）
      start_date:    起始日期 YYYYMMDD
      end_date:      结束日期 YYYYMMDD
      progress_cb:   可选回调 progress_cb(done, total, current_date)；
                    返回真值表示请求取消扫描，函数立即返回 {'cancelled': True}

    返回：
      [{'ts_code', 'stock_name', 'trade_date', 'strategy'}, ...]
      或 {'error': '...'} 或 {'cancelled': True}
    """
    reg = STRATEGY_REGISTRY.get(strategy_name)
    if not reg:
        return {'error': f'未注册的策略: {strategy_name}'}

    # 1. 获取交易日列表
    trade_dates = get_trade_dates(start_date, end_date)
    if not trade_dates:
        return {'error': f'日期范围内无交易日: {start_date}~{end_date}'}

    # 2. 一次性加载数据（覆盖 end_date + 所需回看天数）
    # 回看天数 = max(min_lookback, 最长策略回看)
    # 但由于是全量加载，直接用 min_lookback 即可（数据切片自然截断）
    print(f"📊 扫描策略 [{strategy_name}] {start_date} ~ {end_date}，共 {len(trade_dates)} 个交易日")
    print(f"  回看天数: {reg['min_lookback']}，正在加载数据...")

    # 为每个交易日 D，需要数据覆盖 [D - lookback, D]
    # 但我们直接加载 [first_trade_date - lookback, end_date]，然后切片
    # first_trade_date 就是 trade_dates[0]，但它本身也需要 lookback 天的数据
    first_date_approx = (datetime.strptime(trade_dates[0], '%Y%m%d')
                         - timedelta(days=int(reg['min_lookback'] * 1.8))).strftime('%Y%m%d')
    full_data = _load_full_data(end_date, reg['min_lookback'] + 30)
    if not full_data:
        return {'error': '数据库无股票日线数据'}
    print(f"  ✅ 已加载 {len(full_data)} 条记录，开始扫描...")

    # 按 trade_date 建索引，方便切片
    data_df = _records_to_df(full_data)
    if data_df.empty:
        return {'error': '数据为空'}

    all_records = []
    total = len(trade_dates)

    for i, d in enumerate(trade_dates):
        # 切片：trade_date <= d
        mask = data_df['trade_date'] <= d
        slice_df = data_df.loc[mask]
        # 截取每只股票最近 min_lookback 行（模拟"当日执行"时只看最近 N 天）
        # 但策略 analyze 函数内部已经自己处理了回看窗口
        # 这里直接把切片转成 list[dict]
        slice_records = slice_df.to_dict('records')

        qualified = _call_analyze(strategy_name, slice_records, d)
        for q in qualified:
            all_records.append({
                'ts_code': q['ts_code'],
                'stock_name': q.get('stock_name', ''),
                'trade_date': d,
                'strategy': strategy_name,
                })

        cancel = False
        if progress_cb:
            cancel = bool(progress_cb(i + 1, total, d))
        if cancel:
            print(f"  ⛔ 扫描已被取消，停止于 {d}，已扫描 {i+1}/{total}")
            return {'cancelled': True}
        if (i + 1) % 10 == 0 or i == total - 1:
            print(f"  进度: {i+1}/{total}，当前日 {d}，累计选股{len(all_records)} 条")

    print(f"  ✅ 扫描完成，共 {len(all_records)} 条选股记录")
    return all_records


def scan_multiple(strategy_names, start_date, end_date, progress_cb=None):
    """批量扫描多个策略，合并去重后返回。"""
    all_records = []
    for name in strategy_names:
        result = scan_strategy(name, start_date, end_date, progress_cb)
        if isinstance(result, dict) and result.get('error'):
            print(f"⚠️ {name}: {result['error']}")
            continue
        all_records.extend(result)
    # 去重（同一 ts_code + trade_date + strategy 只保留一条）
    seen = set()
    deduped = []
    for r in all_records:
        key = (r['ts_code'], r['trade_date'], r['strategy'])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def available_strategies():
    """返回已注册的策略列表。"""
    return list(STRATEGY_REGISTRY.keys())


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='策略回测扫描器')
    ap.add_argument('--strategy', required=True, help='策略名，如 "120日区间新高选股策略"')
    ap.add_argument('--start', required=True, help='起始日期 YYYYMMDD')
    ap.add_argument('--end', required=True, help='结束日期 YYYYMMDD')
    ap.add_argument('--list', action='store_true', help='列出已注册策略')
    args = ap.parse_args()

    if args.list:
        print("已注册策略:")
        for name in available_strategies():
            print(f"  - {name}")
        sys.exit(0)

    result = scan_strategy(args.strategy, args.start, args.end)
    if isinstance(result, dict) and result.get('error'):
        print(f"❌ {result['error']}")
        sys.exit(1)
    print(f"\n共 {len(result)} 条选股记录")
    for r in result[:10]:
        print(f"  {r['trade_date']} {r['ts_code']} {r['stock_name']} [{r['strategy']}]")
