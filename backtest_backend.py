#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
策略回测后端模块 (backtest_backend.py)

从 strategy_selected_stock_daily_t 表读取指定策略 + 日期范围内的选股记录，
结合 stock_daily_t 和 index_daily_t 数据，计算聚合回测评价指标：

收益类指标：
  - 10日最大收益率：选中股票在 T+1~T+10 窗口最大涨幅的均值
  - 20日最大收益率：选中股票在 T+1~T+20 窗口最大涨幅的均值
  - 相对上证指数超额收益率：股票收益 - 上证指数同期收益
  - 相对创业板指数超额收益率：股票收益 - 创业板指同期收益

风险类指标：
  - 10日最大跌幅：选中股票在 T+1~T+10 窗口最大跌幅的均值
  - 20日最大跌幅：选中股票在 T+1~T+20 窗口最大跌幅的均值
  - 夏普比率（核心评分）：基于 10 日最大收益序列的均值/标准差

用法（被 app.py 导入）：
  from backtest_backend import run_backtest
  result = run_backtest(strategy='涨停选股', start_date='20260801', end_date='20260910')
"""

import os
import sys
import math

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


# 指数代码
SH_INDEX = '000001.SH'   # 上证指数
SZ_INDEX = '399006.SZ'    # 创业板指


def _safe_round(val, n=2):
    """安全四舍五入，None 原样返回。"""
    if val is None:
        return None
    return round(float(val), n)


def _avg(values):
    """计算均值，空列表返回 None。"""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _std(values):
    """计算样本标准差，不足 2 个返回 None。"""
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _fetch_selected_records(cursor, strategy, start_date, end_date):
    """从 strategy_selected_stock_daily_t 读取选股记录。"""
    sql = """
        SELECT ts_code, stock_name, trade_date, strategy, selected,
               max_gain_10d, max_down_10d, max_gain_20d, max_down_20d
        FROM strategy_selected_stock_daily_t
        WHERE selected = 1
    """
    params = []
    if strategy:
        sql += " AND FIND_IN_SET(%s, strategy)"
        params.append(strategy)
    if start_date:
        sql += " AND trade_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date <= %s"
        params.append(end_date)
    sql += " ORDER BY trade_date, ts_code"
    cursor.execute(sql, params)
    return cursor.fetchall()


def _fetch_index_closes(cursor, ts_code, start_date, end_date):
    """读取指数收盘价，返回 {trade_date: close} 字典。"""
    # 向前扩展 30 天确保能取到 T 日收盘价
    sql = """
        SELECT trade_date, close FROM index_daily_t
        WHERE ts_code = %s AND close > 0
    """
    params = [ts_code]
    if start_date:
        sql += " AND trade_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND trade_date <= %s"
        params.append(end_date)
    sql += " ORDER BY trade_date"
    cursor.execute(sql, params)
    return {r['trade_date']: float(r['close']) for r in cursor.fetchall()}


def _compute_index_return(index_closes, trade_dates_sorted, base_date, window):
    """计算指数在 base_date 后 window 个交易日的收益率（%）。

    用指数自身的交易日序列找 T 日和 T+window 日收盘价。
    """
    dates = trade_dates_sorted
    if base_date not in index_closes:
        return None
    base_close = index_closes[base_date]
    if base_close <= 0:
        return None
    # 找 base_date 在 dates 中的位置
    try:
        idx = dates.index(base_date)
    except ValueError:
        return None
    future_idx = idx + window
    if future_idx >= len(dates):
        return None
    future_date = dates[future_idx]
    if future_date not in index_closes:
        return None
    future_close = index_closes[future_date]
    if future_close <= 0:
        return None
    return (future_close / base_close - 1) * 100


def run_backtest(strategy=None, start_date=None, end_date=None):
    """执行策略回测，返回聚合评价指标 dict。

    参数：
      strategy:  策略名（对应 strategy_selected_stock_daily_t.strategy 字段）
      start_date: 起始日期 YYYYMMDD
      end_date:   结束日期 YYYYMMDD
    """
    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}

    try:
        with conn.cursor() as cursor:
            # 1. 读取选股记录
            records = _fetch_selected_records(cursor, strategy, start_date, end_date)
            if not records:
                return {
                    'strategy': strategy,
                    'start_date': start_date,
                    'end_date': end_date,
                    'total_stocks': 0,
                    'total_dates': 0,
                    'metrics': {},
                    'detail': [],
                    'message': '未找到符合条件的选股记录'
                }

            # 过滤出有回测数据的记录
            valid_records = [r for r in records
                             if r['max_gain_10d'] is not None
                             or r['max_down_10d'] is not None
                             or r['max_gain_20d'] is not None
                             or r['max_down_20d'] is not None]

            all_dates = sorted(set(r['trade_date'] for r in records))
            valid_dates = sorted(set(r['trade_date'] for r in valid_records))

            # 2. 读取指数数据
            idx_start = None
            idx_end = None
            if all_dates:
                idx_start = all_dates[0]
                idx_end = all_dates[-1]
            sh_closes = _fetch_index_closes(cursor, SH_INDEX, idx_start, idx_end)
            sz_closes = _fetch_index_closes(cursor, SZ_INDEX, idx_start, idx_end)
            sh_dates = sorted(sh_closes.keys())
            sz_dates = sorted(sz_closes.keys())

            # 3. 计算聚合指标
            gains_10d = [float(r['max_gain_10d']) for r in valid_records
                         if r['max_gain_10d'] is not None]
            down_10d = [float(r['max_down_10d']) for r in valid_records
                        if r['max_down_10d'] is not None]
            gains_20d = [float(r['max_gain_20d']) for r in valid_records
                         if r['max_gain_20d'] is not None]
            down_20d = [float(r['max_down_20d']) for r in valid_records
                        if r['max_down_20d'] is not None]

            avg_gain_10d = _safe_round(_avg(gains_10d))
            avg_gain_20d = _safe_round(_avg(gains_20d))
            avg_down_10d = _safe_round(_avg(down_10d))
            avg_down_20d = _safe_round(_avg(down_20d))

            # 指数收益：对每个选股日期计算 T+10 / T+20 收益率
            sh_returns_10d = []
            sz_returns_10d = []
            for d in valid_dates:
                r = _compute_index_return(sh_closes, sh_dates, d, 10)
                if r is not None:
                    sh_returns_10d.append(r)
                r = _compute_index_return(sz_closes, sz_dates, d, 10)
                if r is not None:
                    sz_returns_10d.append(r)

            avg_sh_10d = _safe_round(_avg(sh_returns_10d))
            avg_sz_10d = _safe_round(_avg(sz_returns_10d))

            # 超额收益 = 股票平均收益 - 指数平均收益
            excess_sh = None
            if avg_gain_10d is not None and avg_sh_10d is not None:
                excess_sh = round(avg_gain_10d - avg_sh_10d, 2)
            excess_sz = None
            if avg_gain_10d is not None and avg_sz_10d is not None:
                excess_sz = round(avg_gain_10d - avg_sz_10d, 2)

            # 夏普比率：10日最大收益序列的均值/标准差 * sqrt(252)（年化）
            sharpe = None
            if len(gains_10d) >= 2:
                m = _avg(gains_10d)
                s = _std(gains_10d)
                if m is not None and s is not None and s > 0:
                    sharpe = round(m / s * math.sqrt(252), 2)

            metrics = {
                'avg_gain_10d': avg_gain_10d,
                'avg_gain_20d': avg_gain_20d,
                'avg_down_10d': avg_down_10d,
                'avg_down_20d': avg_down_20d,
                'excess_sh': excess_sh,
                'excess_sz': excess_sz,
                'sharpe': sharpe,
                'sh_index_10d': avg_sh_10d,
                'sz_index_10d': avg_sz_10d,
            }

            # 构建明细列表（每条记录一行）
            detail = []
            for r in valid_records:
                detail.append({
                    'ts_code': r['ts_code'],
                    'stock_name': r['stock_name'],
                    'trade_date': r['trade_date'],
                    'strategy': r['strategy'],
                    'max_gain_10d': _safe_round(r['max_gain_10d']),
                    'max_down_10d': _safe_round(r['max_down_10d']),
                    'max_gain_20d': _safe_round(r['max_gain_20d']),
                    'max_down_20d': _safe_round(r['max_down_20d']),
                })

            return {
                'strategy': strategy,
                'start_date': start_date,
                'end_date': end_date,
                'total_stocks': len(records),
                'valid_stocks': len(valid_records),
                'total_dates': len(all_dates),
                'valid_dates': len(valid_dates),
                'metrics': metrics,
                'detail': detail,
            }
    except Exception as e:
        return {'error': f'回测计算失败: {e}'}
    finally:
        close_connection(conn)


if __name__ == '__main__':
    # 命令行测试
    import argparse
    parser = argparse.ArgumentParser(description='策略回测')
    parser.add_argument('strategy', nargs='?', default=None, help='策略名')
    parser.add_argument('start_date', nargs='?', default=None, help='起始日期 YYYYMMDD')
    parser.add_argument('end_date', nargs='?', default=None, help='结束日期 YYYYMMDD')
    args = parser.parse_args()

    result = run_backtest(args.strategy, args.start_date, args.end_date)
    import json
    # detail 太长时只打印前 5 条
    if 'detail' in result and len(result['detail']) > 5:
        detail = result['detail']
        result['detail'] = detail[:5]
        result['detail_total'] = len(detail)
    print(json.dumps(result, ensure_ascii=False, indent=2))
