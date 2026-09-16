#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
用户股票池业务模块 (my_account_stocks.py)

职责：
  1. 建表 my_stock_t（若不存在）；
  2. 查询指定账号下所有股票；
  3. 从 stock_daily_t 实时计算区间收益指标（最高价/最低价/最大涨幅/最大跌幅/至今涨幅）。

供 app.py 的 /api/my_stocks 接口调用，app.py 仅做请求转发与响应。
"""

from mysql_connection import get_mysql_connection, close_connection

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS my_stock_t (
      id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
      account VARCHAR(50) NOT NULL COMMENT '账号',
      ts_code VARCHAR(12) NOT NULL COMMENT '股票代码',
      stock_name VARCHAR(50) DEFAULT NULL COMMENT '股票名称',
      selected_date VARCHAR(8) NOT NULL COMMENT '入选日期YYYYMMDD',
      buy_price DECIMAL(10,3) DEFAULT NULL COMMENT '买入价格',
      strategy_name VARCHAR(128) DEFAULT NULL COMMENT '入选策略名称',
      note VARCHAR(255) DEFAULT NULL COMMENT '备注',
      create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
      update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      UNIQUE KEY uk_account_stock (account, ts_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户股票池表'
"""

# 兼容已建表：若 buy_price 列不存在则追加
ALTER_ADD_BUY_PRICE_SQL = """
    ALTER TABLE my_stock_t
    ADD COLUMN buy_price DECIMAL(10,3) DEFAULT NULL COMMENT '买入价格' AFTER selected_date
"""

SELECT_STOCKS_SQL = """
    SELECT account, ts_code, stock_name, selected_date, buy_price, strategy_name, note
    FROM my_stock_t WHERE account = %s
    ORDER BY selected_date DESC, ts_code
"""

# 批量加入股票池：buy_price 取入选日收盘价，已存在则更新
INSERT_STOCK_SQL = """
    INSERT INTO my_stock_t (account, ts_code, stock_name, selected_date, buy_price, strategy_name)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      stock_name = VALUES(stock_name),
      selected_date = VALUES(selected_date),
      buy_price = VALUES(buy_price),
      strategy_name = VALUES(strategy_name),
      update_time = CURRENT_TIMESTAMP
"""

BASE_CLOSE_SQL = "SELECT close FROM stock_daily_t WHERE ts_code=%s AND trade_date=%s"

FUTURE_CLOSES_SQL = """
    SELECT close FROM stock_daily_t
    WHERE ts_code=%s AND trade_date>=%s AND close>0
    ORDER BY trade_date ASC
"""


def get_account_stocks(account):
    """查询指定账号的股票池，并从 stock_daily_t 实时计算区间收益指标。

    参数：
      account: 账号

    返回：
      {'account': ..., 'total': N, 'rows': [...]} 或 {'error': '...'}
    """
    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}

    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            try:
                cursor.execute(ALTER_ADD_BUY_PRICE_SQL)
            except Exception:
                pass  # 列已存在，忽略

            cursor.execute(SELECT_STOCKS_SQL, (account,))
            stocks = list(cursor.fetchall())

            results = []
            for s in stocks:
                ts_code = s['ts_code']
                sd = s['selected_date']

                cursor.execute(BASE_CLOSE_SQL, (ts_code, sd))
                base_row = cursor.fetchone()
                base_close = float(base_row['close']) if base_row and base_row['close'] else None

                cursor.execute(FUTURE_CLOSES_SQL, (ts_code, sd))
                closes = [float(r['close']) for r in cursor.fetchall()]

                row = {
                    'account': s['account'],
                    'ts_code': ts_code,
                    'stock_name': s['stock_name'],
                    'selected_date': sd,
                    'buy_price': float(s['buy_price']) if s.get('buy_price') else None,
                    'strategy_name': s.get('strategy_name'),
                    'note': s.get('note'),
                    'base_close': round(base_close, 2) if base_close else None,
                    'latest_close': round(closes[-1], 2) if closes else None,
                    'max_close': round(max(closes), 2) if closes else None,
                    'min_close': round(min(closes), 2) if closes else None,
                }
                # 收益计算基准：优先用买入价，无则用入选日收盘价
                ref_price = row['buy_price'] or base_close
                if ref_price and closes:
                    row['max_gain'] = round((max(closes) / ref_price - 1) * 100, 2)
                    row['max_drop'] = round((min(closes) / ref_price - 1) * 100, 2)
                    row['gain_to_date'] = round((closes[-1] / ref_price - 1) * 100, 2)
                else:
                    row['max_gain'] = row['max_drop'] = row['gain_to_date'] = None
                results.append(row)

        return {'account': account, 'total': len(results), 'rows': results}
    except Exception as e:
        return {'error': str(e)}
    finally:
        close_connection(conn)


def add_to_my_stocks(account, stocks):
    """批量将股票加入指定账号的股票池。

    参数：
      account: 账号
      stocks: [{ts_code, stock_name, selected_date, strategy_name}, ...]

    返回：
      {'ok': True, 'count': N} 或 {'error': '...'}
    """
    if not account:
        return {'error': '账号不能为空'}
    if not stocks or not isinstance(stocks, list):
        return {'error': '股票列表为空'}

    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}

    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            try:
                cursor.execute(ALTER_ADD_BUY_PRICE_SQL)
            except Exception:
                pass  # 列已存在，忽略

            count = 0
            for s in stocks:
                ts_code = (s.get('ts_code') or '').strip()
                selected_date = (s.get('selected_date') or '').strip()
                if not ts_code or not selected_date:
                    continue
                stock_name = (s.get('stock_name') or '').strip() or None
                strategy_name = (s.get('strategy_name') or '').strip() or None
                # 查入选日收盘价作为买入价
                cursor.execute(BASE_CLOSE_SQL, (ts_code, selected_date))
                close_row = cursor.fetchone()
                buy_price = float(close_row['close']) if close_row and close_row['close'] else None
                cursor.execute(INSERT_STOCK_SQL,
                               (account, ts_code, stock_name, selected_date, buy_price, strategy_name))
                count += 1
            conn.commit()
        return {'ok': True, 'count': count}
    except Exception as e:
        return {'error': str(e)}
    finally:
        close_connection(conn)


DELETE_STOCK_SQL = "DELETE FROM my_stock_t WHERE account = %s AND ts_code = %s"


def remove_from_my_stocks(account, ts_codes):
    """从指定账号的股票池中移除股票。

    参数：
      account: 账号
      ts_codes: [ts_code, ...]

    返回：
      {'ok': True, 'count': N} 或 {'error': '...'}
    """
    if not account:
        return {'error': '账号不能为空'}
    if not ts_codes or not isinstance(ts_codes, list):
        return {'error': '股票代码列表为空'}

    conn = get_mysql_connection()
    if not conn:
        return {'error': '数据库连接失败'}

    try:
        with conn.cursor() as cursor:
            count = 0
            for code in ts_codes:
                code = (code or '').strip()
                if not code:
                    continue
                cursor.execute(DELETE_STOCK_SQL, (account, code))
                count += cursor.rowcount
            conn.commit()
        return {'ok': True, 'count': count}
    except Exception as e:
        return {'error': str(e)}
    finally:
        close_connection(conn)
