#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股结果入库公共模块（insert_strategy_selected_record.py）

功能：
  将各选股程序（select_*.py / find_*.py）的选股结果统一写入
  strategy_selected_stock_daily_t 表，便于后续回测。

写入规则：
  1. 主键为 (ts_code, trade_date)。
  2. 主键冲突时，不新增行，而是把新策略追加到已有行的 strategy 字段，
     多个策略用逗号分隔（同一策略已存在则不重复追加）。
  3. selected 语义：任一策略选中即为 1；原行为 0 且新策略 selected=1 时更新为 1。
  4. max_gain_10d / max_down_10d / max_down_20d / max_gain_20d 字段预留
     （回测程序 backtest.py 另行回填），本模块不计算。

用法：
  from insert_strategy_selected_record import record_selected_stocks
  record_selected_stocks('turn_bottom',
                         [{'ts_code': '600000.SH', 'selected': 1}, ...],
                         '20260914')
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

# 建表语句：已存在则直接复用
CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS strategy_selected_stock_daily_t (
      ts_code VARCHAR(12) NOT NULL COMMENT '股票代码',
      trade_date VARCHAR(8) NOT NULL COMMENT '交易日(选股目标日)',
      strategy VARCHAR(128) NOT NULL COMMENT '选股策略，多个用逗号分隔(如2wave_daily,2wave_w23)',
      selected TINYINT NOT NULL DEFAULT 1 COMMENT '是否被选中(0:否,1:是)，任一策略选中即为1',
      max_gain_10d FLOAT NULL COMMENT '10个交易日中最大涨幅(%)',
      max_down_10d FLOAT NULL COMMENT '10个交易日中最大跌幅(%)',
      max_down_20d FLOAT NULL COMMENT '20个交易日中最大跌幅(%)',
      max_gain_20d FLOAT NULL COMMENT '20个交易日中最大涨幅(%)',
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      PRIMARY KEY (ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
      COMMENT='每日选股结果记录表(便于回测)'
"""

# 写入语句：主键冲突时追加策略（逗号分隔，不重复追加），selected 取"任一选中即为1"
UPSERT_SQL = """
    INSERT INTO strategy_selected_stock_daily_t
      (ts_code, trade_date, strategy, selected)
    VALUES (%s, %s, %s, %s) AS new
    ON DUPLICATE KEY UPDATE
      strategy_selected_stock_daily_t.strategy = IF(
          FIND_IN_SET(new.strategy, strategy_selected_stock_daily_t.strategy) > 0,
          strategy_selected_stock_daily_t.strategy,
          CONCAT(strategy_selected_stock_daily_t.strategy, ',', new.strategy)),
      strategy_selected_stock_daily_t.selected = IF(
          strategy_selected_stock_daily_t.selected = 1, 1, new.selected)
"""


def record_selected_stocks(strategy, rows, selection_date):
    """选股结果入库 strategy_selected_stock_daily_t（便于回测）。

    参数：
      strategy       策略名，如 '2wave_daily'、'turn_bottom'
      rows           [{'ts_code': '600000.SH', 'selected': 1}, ...]
      selection_date 选股目标日 'YYYYMMDD'（取数据中最新交易日）

    主键冲突时将该策略追加到 strategy 字段（逗号分隔，已存在不重复追加）。
    """
    if not rows or not strategy or not selection_date:
        return
    conn = get_mysql_connection()
    if not conn:
        print("❌ 选股结果入库失败：数据库连接失败")
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            cursor.executemany(UPSERT_SQL,
                               [(r['ts_code'], selection_date, strategy,
                                 int(r.get('selected', 1))) for r in rows])
        conn.commit()
        n1 = sum(1 for r in rows if r.get('selected', 1))
        print(f"✅ 选股结果已入库: strategy={strategy} trade_date={selection_date} "
              f"共{len(rows)}条(selected=1共{n1}条)")
    except Exception as e:
        print(f"❌ 选股结果入库失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)
