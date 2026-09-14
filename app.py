#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Flask 回测结果查询 Web 应用 (app.py)

核心功能：
  1. GET /                → 返回 pages/backtest_web.html 交互式查询页面；
  2. GET /api/strategies  → 返回 strategy_selected_stock_daily_t 表中所有策略名
                            （strategy 字段为逗号串，拆分为独立策略名并去重排序）；
  3. GET /api/results     → 按策略名称（FIND_IN_SET 匹配逗号串）+ 日期范围
                            （trade_date，YYYYMMDD）查询回测结果，返回 JSON。

页面展示字段：股票代码、交易日、策略、是否选中、10日/20日最大涨幅、10日/20日最大跌幅，
前端点击表头可按任意涨跌幅列（及交易日）升序/降序排序，空值始终排在最后。

用法：
  ${项目}/.venv/bin/python3 app.py    # 启动后浏览器访问 http://127.0.0.1:5000
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, send_from_directory

from mysql_connection import get_mysql_connection, close_connection

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_DIR = os.path.join(BASE_DIR, 'pages')

app = Flask(__name__)


def query_db(sql, params=None):
    """执行查询并返回结果列表；连接失败返回 None。"""
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        close_connection(conn)


@app.route('/')
def index():
    """返回 pages/ 下的交互式查询页面。"""
    return send_from_directory(PAGE_DIR, 'backtest_web.html')


@app.route('/api/strategies')
def api_strategies():
    """返回所有策略名（strategy 逗号串拆分去重）。"""
    rows = query_db("SELECT strategy FROM strategy_selected_stock_daily_t")
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500
    names = sorted({s.strip() for r in rows
                    for s in (r['strategy'] or '').split(',') if s.strip()})
    return jsonify({'strategies': names})


@app.route('/api/results')
def api_results():
    """按策略名称 + 日期范围查询回测结果。"""
    strategy = request.args.get('strategy', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()

    sql = """
        SELECT ts_code, trade_date, strategy, selected,
               max_gain_10d, max_down_10d, max_gain_20d, max_down_20d
        FROM strategy_selected_stock_daily_t
        WHERE 1=1
    """
    params = []
    if strategy:
        sql += " AND FIND_IN_SET(%s, strategy)"
        params.append(strategy)
    if start:
        sql += " AND trade_date >= %s"
        params.append(start)
    if end:
        sql += " AND trade_date <= %s"
        params.append(end)
    sql += " ORDER BY trade_date DESC, ts_code"

    rows = query_db(sql, params)
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500

    # Decimal → float，便于前端 JSON 序列化
    for r in rows:
        for k in ('max_gain_10d', 'max_down_10d', 'max_gain_20d', 'max_down_20d'):
            if r[k] is not None:
                r[k] = float(r[k])
    return jsonify({'rows': rows, 'total': len(rows)})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
