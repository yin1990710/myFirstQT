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
from module_mysql_connection import get_mysql_connection, close_connection


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


# 回测窗口天数
FUTURE_DAYS_10 = 10
FUTURE_DAYS_20 = 20


def _compute_stock_metrics(cursor, ts_code, trade_date):
    """从 stock_daily_t 实时计算个股 T 日后 10/20 日窗口最大涨跌幅（%）。

    返回 dict: {max_gain_10d, max_down_10d, max_gain_20d, max_down_20d}
    数据不足时对应字段为 None；T 日无收盘价或无未来数据时返回 None。
    """
    # T 日收盘价
    cursor.execute(
        "SELECT close FROM stock_daily_t WHERE ts_code=%s AND trade_date=%s",
        (ts_code, trade_date))
    row = cursor.fetchone()
    if not row or not row['close'] or float(row['close']) <= 0:
        return None
    base_close = float(row['close'])
    # T 日后收盘价序列
    cursor.execute("""
        SELECT close FROM stock_daily_t
        WHERE ts_code=%s AND trade_date>%s AND close>0
        ORDER BY trade_date ASC
    """, (ts_code, trade_date))
    closes = [float(r['close']) for r in cursor.fetchall()]
    if not closes:
        return None
    result = {}
    # 10 日窗口
    if len(closes) >= FUTURE_DAYS_10:
        w10 = closes[:FUTURE_DAYS_10]
        result['max_gain_10d'] = (max(w10) / base_close - 1) * 100
        result['max_down_10d'] = (min(w10) / base_close - 1) * 100
    else:
        result['max_gain_10d'] = None
        result['max_down_10d'] = None
    # 20 日窗口
    if len(closes) >= FUTURE_DAYS_20:
        w20 = closes[:FUTURE_DAYS_20]
        result['max_gain_20d'] = (max(w20) / base_close - 1) * 100
        result['max_down_20d'] = (min(w20) / base_close - 1) * 100
    else:
        result['max_gain_20d'] = None
        result['max_down_20d'] = None
    return result


def _fetch_selected_records(cursor, strategy, start_date, end_date):
    """从 strategy_selected_stock_daily_t 读取选股记录（不含预计算指标，指标由 _compute_stock_metrics 实时计算）。"""
    sql = """
        SELECT ts_code, stock_name, selected_date, strategy, selected
        FROM strategy_selected_stock_daily_t
        WHERE selected = 1
    """
    params = []
    if strategy:
        sql += " AND FIND_IN_SET(%s, strategy)"
        params.append(strategy)
    if start_date:
        sql += " AND selected_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND selected_date <= %s"
        params.append(end_date)
    sql += " ORDER BY selected_date, ts_code"
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


def run_backtest(strategy=None, start_date=None, end_date=None, records=None):
    """执行策略回测，返回聚合评价指标 dict。

    参数：
      strategy:   策略名（对应 strategy_selected_stock_daily_t.strategy 字段）
      start_date: 起始日期 YYYYMMDD
      end_date:   结束日期 YYYYMMDD
      records:    可选，预扫描的选股记录列表（跳过 DB 查询），每条需含
                  ts_code, stock_name, trade_date, strategy 字段
    """
    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}

    try:
        with conn.cursor() as cursor:
            # 1. 读取选股记录（优先使用传入的 records，否则查 DB）
            if records is not None:
                # 扫描模式：传入的 records 已经是 list[dict]
                records = [dict(r) for r in records]  # 确保是 dict
            else:
                records = _fetch_selected_records(cursor, strategy, start_date, end_date)

            # 统一键名：DB 模式返回 selected_date，扫描模式返回 trade_date
            # 归一化后后续统一用 r['trade_date']
            for r in records:
                if 'trade_date' not in r and 'selected_date' in r:
                    r['trade_date'] = r.pop('selected_date')
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

            # 1b. 从 stock_daily_t 实时计算每条记录的回测指标（不依赖预计算值）
            for r in records:
                m = _compute_stock_metrics(cursor, r['ts_code'], r['trade_date'])
                if m:
                    r['max_gain_10d'] = m['max_gain_10d']
                    r['max_down_10d'] = m['max_down_10d']
                    r['max_gain_20d'] = m['max_gain_20d']
                    r['max_down_20d'] = m['max_down_20d']
                else:
                    r['max_gain_10d'] = r['max_down_10d'] = r['max_gain_20d'] = r['max_down_20d'] = None

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
            for r in records:
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


# ---------------------------------------------------------------------------
# 回测结果持久化
# ---------------------------------------------------------------------------

CREATE_BACKTEST_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS strategy_backtest_result_t (
      id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
      strategy_name VARCHAR(128) NOT NULL COMMENT '策略名称',
      strategy_file VARCHAR(100) DEFAULT NULL COMMENT '对应策略py文件名',
      start_date VARCHAR(8) NOT NULL COMMENT '回测起始日期YYYYMMDD',
      end_date VARCHAR(8) NOT NULL COMMENT '回测结束日期YYYYMMDD',
      ts_code VARCHAR(12) NOT NULL COMMENT '股票代码',
      stock_name VARCHAR(50) DEFAULT NULL COMMENT '股票名称',
      trade_date VARCHAR(8) NOT NULL COMMENT '选股日期YYYYMMDD',
      max_gain_10d DECIMAL(8,2) DEFAULT NULL COMMENT '10日最大涨幅(%)',
      max_down_10d DECIMAL(8,2) DEFAULT NULL COMMENT '10日最大跌幅(%)',
      max_gain_20d DECIMAL(8,2) DEFAULT NULL COMMENT '20日最大涨幅(%)',
      max_down_20d DECIMAL(8,2) DEFAULT NULL COMMENT '20日最大跌幅(%)',
      avg_gain_10d DECIMAL(8,2) DEFAULT NULL COMMENT '平均10日最大涨幅(%)',
      avg_gain_20d DECIMAL(8,2) DEFAULT NULL COMMENT '平均20日最大涨幅(%)',
      avg_down_10d DECIMAL(8,2) DEFAULT NULL COMMENT '平均10日最大跌幅(%)',
      avg_down_20d DECIMAL(8,2) DEFAULT NULL COMMENT '平均20日最大跌幅(%)',
      excess_sh DECIMAL(8,2) DEFAULT NULL COMMENT '相对上证指数超额收益(%)',
      excess_sz DECIMAL(8,2) DEFAULT NULL COMMENT '相对创业板指超额收益(%)',
      sharpe DECIMAL(10,2) DEFAULT NULL COMMENT '夏普比率(年化)',
      sh_index_10d DECIMAL(8,2) DEFAULT NULL COMMENT '上证指数10日收益(%)',
      sz_index_10d DECIMAL(8,2) DEFAULT NULL COMMENT '创业板指10日收益(%)',
      total_stocks INT DEFAULT NULL COMMENT '选股总数',
      valid_stocks INT DEFAULT NULL COMMENT '有效回测数',
      total_dates INT DEFAULT NULL COMMENT '涉及交易日数',
      run_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '回测执行时间',
      update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      UNIQUE KEY uk_run_stock (strategy_name, start_date, end_date, ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='策略回测结果表'
"""

INSERT_BACKTEST_SQL = """
    INSERT INTO strategy_backtest_result_t
      (strategy_name, strategy_file, start_date, end_date,
       ts_code, stock_name, trade_date,
       max_gain_10d, max_down_10d, max_gain_20d, max_down_20d,
       avg_gain_10d, avg_gain_20d, avg_down_10d, avg_down_20d,
       excess_sh, excess_sz, sharpe, sh_index_10d, sz_index_10d,
       total_stocks, valid_stocks, total_dates)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

DELETE_BACKTEST_SQL = """
    DELETE FROM strategy_backtest_result_t
    WHERE strategy_name = %s AND start_date = %s AND end_date = %s
"""


def save_backtest_result(result, strategy_file=None):
    """将回测结果保存到 strategy_backtest_result_t 表。

    参数：
      result: run_backtest() 返回的 dict
      strategy_file: 对应的策略 .py 文件名（可空）
    返回：
      {'ok': True, 'rows': N} 或 {'ok': False, 'error': '...'}
    """
    if not result or result.get('error'):
        return {'ok': False, 'error': '回测结果为空或出错'}

    detail = result.get('detail') or []
    if not detail:
        return {'ok': False, 'error': '回测无明细数据，跳过保存'}

    strategy = result.get('strategy') or ''
    start_date = result.get('start_date') or ''
    end_date = result.get('end_date') or ''
    m = result.get('metrics') or {}

    conn = get_mysql_connection()
    if not conn:
        return {'ok': False, 'error': '数据库连接失败'}

    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_BACKTEST_TABLE_SQL)
            # 删除同策略+日期范围的旧结果，实现覆盖
            cursor.execute(DELETE_BACKTEST_SQL, (strategy, start_date, end_date))
            # 批量插入明细行（聚合指标冗余存储到每行）
            rows = []
            for d in detail:
                rows.append((
                    strategy, strategy_file, start_date, end_date,
                    d.get('ts_code'), d.get('stock_name'), d.get('trade_date'),
                    d.get('max_gain_10d'), d.get('max_down_10d'),
                    d.get('max_gain_20d'), d.get('max_down_20d'),
                    m.get('avg_gain_10d'), m.get('avg_gain_20d'),
                    m.get('avg_down_10d'), m.get('avg_down_20d'),
                    m.get('excess_sh'), m.get('excess_sz'),
                    m.get('sharpe'), m.get('sh_index_10d'), m.get('sz_index_10d'),
                    result.get('total_stocks'), result.get('valid_stocks'),
                    result.get('total_dates'),
                ))
            cursor.executemany(INSERT_BACKTEST_SQL, rows)
        conn.commit()
        return {'ok': True, 'rows': len(rows)}
    except Exception as e:
        conn.rollback()
        return {'ok': False, 'error': f'保存失败: {e}'}
    finally:
        close_connection(conn)


# ---------------------------------------------------------------------------
# 回测任务注册表（每个用户提交的回测任务落库，服务重启后历史任务仍可查询）
# ---------------------------------------------------------------------------

CREATE_BACKTEST_TASK_SQL = """
    CREATE TABLE IF NOT EXISTS backtest_task_t (
      task_id VARCHAR(40) PRIMARY KEY COMMENT '任务ID',
      account VARCHAR(50) NOT NULL COMMENT '提交账号',
      task_name VARCHAR(200) NOT NULL COMMENT '任务名称 账号-策略_日期范围',
      strategy VARCHAR(128) NOT NULL COMMENT '策略名称',
      strategy_file VARCHAR(100) DEFAULT NULL COMMENT '对应策略py文件名',
      start_date VARCHAR(8) NOT NULL COMMENT '回测起始日期YYYYMMDD',
      end_date VARCHAR(8) NOT NULL COMMENT '回测结束日期YYYYMMDD',
      status VARCHAR(16) NOT NULL DEFAULT 'running' COMMENT 'running/done/failed',
      stage VARCHAR(16) DEFAULT 'queued' COMMENT 'queued/scanning/computing/done/failed',
      progress_done INT DEFAULT NULL COMMENT '已扫描交易日数',
      progress_total INT DEFAULT NULL COMMENT '总交易日数',
      progress_current_date VARCHAR(8) DEFAULT NULL COMMENT '当前扫描交易日',
      error TEXT DEFAULT NULL COMMENT '失败原因',
      saved TINYINT NOT NULL DEFAULT 0 COMMENT '结果是否已写入结果表',
      start_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '任务开始时间',
      end_time DATETIME DEFAULT NULL COMMENT '任务结束时间',
      duration DECIMAL(10,1) DEFAULT NULL COMMENT '耗时秒',
      update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      KEY idx_account (account, start_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='策略回测任务注册表'
"""

INSERT_BACKTEST_TASK_SQL = """
    INSERT INTO backtest_task_t
      (task_id, account, task_name, strategy, strategy_file, start_date, end_date, status, stage)
    VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', 'queued')
"""

UPDATE_TASK_PROGRESS_SQL = """
    UPDATE backtest_task_t
    SET stage = %s, progress_done = %s, progress_total = %s, progress_current_date = %s
    WHERE task_id = %s
"""

UPDATE_TASK_STAGE_SQL = "UPDATE backtest_task_t SET stage = %s WHERE task_id = %s"

FINISH_TASK_SQL = """
    UPDATE backtest_task_t
    SET status = %s, stage = %s, end_time = NOW(),
        duration = TIMESTAMPDIFF(SECOND, start_time, NOW()),
        error = %s, saved = %s
    WHERE task_id = %s
"""

LIST_TASKS_SQL = """
    SELECT task_id, account, task_name, strategy, strategy_file,
           start_date, end_date, status, stage,
           progress_done, progress_total, progress_current_date,
           error, saved, start_time, end_time, duration
    FROM backtest_task_t
    WHERE account = %s
"""


def create_backtest_task(task_id, account, task_name, strategy,
                       strategy_file, start_date, end_date):
    """创建回测任务记录（状态 running/queued）。"""
    conn = get_mysql_connection()
    if not conn:
        return {'ok': False, 'error': '数据库连接失败'}
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_BACKTEST_TASK_SQL)
            cursor.execute(INSERT_BACKTEST_TASK_SQL,
                         (task_id, account, task_name, strategy,
                          strategy_file, start_date, end_date))
        conn.commit()
        return {'ok': True}
    except Exception as e:
        conn.rollback()
        return {'ok': False, 'error': f'创建任务失败: {e}'}
    finally:
        close_connection(conn)


def update_backtest_task_progress(task_id, done, total, current_date, stage='scanning'):
    """更新任务扫描进度（每个交易日回调时调用）。"""
    conn = get_mysql_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(UPDATE_TASK_PROGRESS_SQL,
                         (stage, done, total, current_date, task_id))
        conn.commit()
    except Exception:
        pass
    finally:
        close_connection(conn)


def set_backtest_task_stage(task_id, stage):
    """更新任务阶段（如 scanning → computing）。"""
    conn = get_mysql_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(UPDATE_TASK_STAGE_SQL, (stage, task_id))
        conn.commit()
    except Exception:
        pass
    finally:
        close_connection(conn)


def finish_backtest_task(task_id, status, error='', saved=False):
    """任务结束：status 为 done/failed，记录结束时间、耗时、失败原因及是否已落库。"""
    conn = get_mysql_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(FINISH_TASK_SQL,
                         (status, status, error[:2000] if error else None,
                          1 if saved else 0, task_id))
        conn.commit()
    except Exception:
        pass
    finally:
        close_connection(conn)


def _task_row_to_dict(r):
    """DB 行 → 前端任务视图（datetime/Decimal 转字符串/数字）。"""
    if not r:
        return None
    start = r.get('start_time')
    end = r.get('end_time')
    duration = r.get('duration')
    return {
        'task_id': r['task_id'],
        'account': r['account'],
        'task_name': r['task_name'],
        'strategy': r['strategy'],
        'strategy_file': r.get('strategy_file'),
        'start_date': r['start_date'],
        'end_date': r['end_date'],
        'status': r['status'],
        'stage': r.get('stage'),
        'progress': (None if r.get('progress_total') is None else {
            'done': r['progress_done'],
            'total': r['progress_total'],
            'current_date': r.get('progress_current_date'),
        }),
        'error': r.get('error'),
        'saved': bool(r.get('saved')),
        'start': start.strftime('%Y-%m-%d %H:%M:%S') if start else None,
        'end': end.strftime('%Y-%m-%d %H:%M:%S') if end else None,
        'duration': float(duration) if duration is not None else None,
    }


def list_backtest_tasks(account, strategy=None, start_date=None, end_date=None, limit=100):
    """查询指定账号提交的回测任务（按开始时间倒序），支持策略+日期范围筛选。"""
    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_BACKTEST_TASK_SQL)
            sql = LIST_TASKS_SQL
            params = [account]
            if strategy:
                sql += " AND strategy = %s"
                params.append(strategy)
            if start_date:
                sql += " AND start_date >= %s"
                params.append(start_date)
            if end_date:
                sql += " AND end_date <= %s"
                params.append(end_date)
            sql += " ORDER BY start_time DESC LIMIT %s"
            params.append(limit)
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
            return {'tasks': [_task_row_to_dict(r) for r in rows]}
    except Exception as e:
        return {'error': f'查询任务列表失败: {e}'}
    finally:
        close_connection(conn)


def mark_backtest_task_stopped(task_id):
    """直接将运行中任务标记为已停止（服务重启后守护线程已不存在时使用）。

    仅对 running 状态任务生效，返回实际更新行数。
    """
    conn = get_mysql_connection()
    if not conn:
        return 0
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_BACKTEST_TASK_SQL)
            cursor.execute(
                "UPDATE backtest_task_t "
                "SET status='stopped', stage='stopped', end_time=NOW(), "
                "    duration=TIMESTAMPDIFF(SECOND, start_time, NOW()) "
                "WHERE task_id=%s AND status='running'",
                (task_id,))
            conn.commit()
            return cursor.rowcount
    except Exception:
        return 0
    finally:
        close_connection(conn)


def list_backtest_accounts():
    """返回任务表中出现过的账号去重列表（按最近提交时间倒序），供任务列表账号筛选。"""
    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_BACKTEST_TASK_SQL)
            cursor.execute(
                "SELECT account, MAX(start_time) AS latest "
                "FROM backtest_task_t GROUP BY account ORDER BY latest DESC")
            return {'accounts': [r['account'] for r in (cursor.fetchall() or [])]}
    except Exception as e:
        return {'error': f'查询账号列表失败: {e}'}
    finally:
        close_connection(conn)


def get_backtest_task(task_id):
    """按 task_id 查询单个任务（不含回测结果明细）。"""
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_BACKTEST_TASK_SQL)
            cursor.execute(
                "SELECT * FROM backtest_task_t WHERE task_id = %s", (task_id,))
            return _task_row_to_dict(cursor.fetchone())
    except Exception:
        return None
    finally:
        close_connection(conn)


LOAD_SAVED_RESULT_SQL = """
    SELECT ts_code, stock_name, trade_date,
           max_gain_10d, max_down_10d, max_gain_20d, max_down_20d,
           avg_gain_10d, avg_gain_20d, avg_down_10d, avg_down_20d,
           excess_sh, excess_sz, sharpe, sh_index_10d, sz_index_10d,
           total_stocks, valid_stocks, total_dates, strategy_file, run_time
    FROM strategy_backtest_result_t
    WHERE strategy_name = %s AND start_date = %s AND end_date = %s
    ORDER BY trade_date, ts_code
"""


def load_saved_backtest_result(strategy, start_date, end_date):
    """从 strategy_backtest_result_t 还原 run_backtest() 同构结果，供任务完成后（含服务重启）查看。"""
    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}
    try:
        with conn.cursor() as cursor:
            cursor.execute(LOAD_SAVED_RESULT_SQL,
                         (strategy, start_date, end_date))
            rows = cursor.fetchall() or []
            if not rows:
                return None
            first = rows[0]
            detail = []
            for r in rows:
                detail.append({
                    'ts_code': r['ts_code'],
                    'stock_name': r.get('stock_name'),
                    'trade_date': r['trade_date'],
                    'max_gain_10d': _safe_round(r['max_gain_10d']),
                    'max_down_10d': _safe_round(r['max_down_10d']),
                    'max_gain_20d': _safe_round(r['max_gain_20d']),
                    'max_down_20d': _safe_round(r['max_down_20d']),
                })
            metrics = {
                'avg_gain_10d': _safe_round(first['avg_gain_10d']),
                'avg_gain_20d': _safe_round(first['avg_gain_20d']),
                'avg_down_10d': _safe_round(first['avg_down_10d']),
                'avg_down_20d': _safe_round(first['avg_down_20d']),
                'excess_sh': _safe_round(first['excess_sh']),
                'excess_sz': _safe_round(first['excess_sz']),
                'sharpe': _safe_round(first['sharpe']),
                'sh_index_10d': _safe_round(first['sh_index_10d']),
                'sz_index_10d': _safe_round(first['sz_index_10d']),
            }
            return {
                'strategy': strategy,
                'start_date': start_date,
                'end_date': end_date,
                'strategy_file': first.get('strategy_file'),
                'total_stocks': first.get('total_stocks') or 0,
                'valid_stocks': first.get('valid_stocks') or 0,
                'total_dates': first.get('total_dates') or 0,
                'metrics': metrics,
                'detail': detail,
                'saved': True,
            }
    except Exception as e:
        return {'error': f'读取已保存结果失败: {e}'}
    finally:
        close_connection(conn)
