#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股结果入库公共模块（module_insert_strategy_selected_record.py）

功能：
  将各选股程序（select_*.py / find_*.py）的选股结果统一写入
  strategy_selected_stock_daily_t 表，便于后续回测。

写入规则：
  1. 主键为 (ts_code, trade_date)。
  2. 主键冲突时，不新增行，而是把新策略追加到已有行的 strategy 字段，
     多个策略用逗号分隔（同一策略已存在则不重复追加）。
  3. selected 语义：任一策略选中即为 1；原行为 0 且新策略 selected=1 时更新为 1。
  4. stock_name 为股票名称：优先取 rows 中显式传入的 'stock_name'，
     未传时按 ts_code 从 stock_info_t 表自动解析；历史缺失名称会在迁移时回填。
  5. max_gain_10d / max_down_10d / max_down_20d / max_gain_20d 字段预留
     （回测程序 strategy_results.py 另行回填），本模块不计算。

用法：
  from module_insert_strategy_selected_record import record_selected_stocks
  record_selected_stocks('turn_bottom',
                         [{'ts_code': '600000.SH', 'selected': 1}, ...],
                         '20260914')
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

# 建表语句：已存在则直接复用
CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS strategy_selected_stock_daily_t (
      ts_code VARCHAR(12) NOT NULL COMMENT '股票代码',
      stock_name VARCHAR(50) NULL COMMENT '股票名称(来自stock_info_t，可空)',
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
# stock_name：已有值则保留，为空时用本次新值补齐
UPSERT_SQL = """
    INSERT INTO strategy_selected_stock_daily_t
      (ts_code, stock_name, trade_date, strategy, selected)
    VALUES (%s, %s, %s, %s, %s) AS new
    ON DUPLICATE KEY UPDATE
      strategy_selected_stock_daily_t.strategy = IF(
          FIND_IN_SET(new.strategy, strategy_selected_stock_daily_t.strategy) > 0,
          strategy_selected_stock_daily_t.strategy,
          CONCAT(strategy_selected_stock_daily_t.strategy, ',', new.strategy)),
      strategy_selected_stock_daily_t.selected = IF(
          strategy_selected_stock_daily_t.selected = 1, 1, new.selected),
      strategy_selected_stock_daily_t.stock_name = COALESCE(
          NULLIF(strategy_selected_stock_daily_t.stock_name, ''), new.stock_name)
"""

# 旧表增量迁移：缺 stock_name 列时 ADD COLUMN（不重建表，保留既有数据）
ENSURE_STOCK_NAME_COLUMN_SQL = """
    SELECT COUNT(*) AS cnt
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'strategy_selected_stock_daily_t'
      AND COLUMN_NAME = 'stock_name'
"""

ADD_STOCK_NAME_COLUMN_SQL = """
    ALTER TABLE strategy_selected_stock_daily_t
      ADD COLUMN stock_name VARCHAR(50) NULL
      COMMENT '股票名称(来自stock_info_t，可空)' AFTER ts_code
"""

# 历史数据回填：按 ts_code 从 stock_info_t 匹配股票名称
# 注意：本表为 utf8mb4_0900_ai_ci，stock_info_t 为 utf8mb4_unicode_ci，
# JOIN 比较需显式统一排序规则，否则报 Illegal mix of collations
BACKFILL_STOCK_NAME_SQL = """
    UPDATE strategy_selected_stock_daily_t s
    JOIN stock_info_t i
      ON s.ts_code = i.ts_code COLLATE utf8mb4_0900_ai_ci
    SET s.stock_name = i.stock_name
    WHERE s.stock_name IS NULL OR s.stock_name = ''
"""


def _ensure_stock_name_column(cursor):
    """旧表结构增量升级：缺 stock_name 列时加列并回填历史记录的股票名称。"""
    cursor.execute(ENSURE_STOCK_NAME_COLUMN_SQL)
    if cursor.fetchone()['cnt'] > 0:
        return
    cursor.execute(ADD_STOCK_NAME_COLUMN_SQL)
    try:
        cursor.execute(BACKFILL_STOCK_NAME_SQL)
        print(f"✅ 已新增 stock_name 列并回填历史记录股票名称 {cursor.rowcount} 条")
    except Exception as e:
        # stock_info_t 不存在等情况下不阻断主流程，后续写入时仍会按可用来源解析
        print(f"⚠️ 历史股票名称回填跳过: {e}")


def _fetch_stock_name_map(cursor, ts_codes):
    """按 ts_code 批量从 stock_info_t 解析股票名称，返回 {ts_code: stock_name}。"""
    name_map = {}
    codes = list(dict.fromkeys(c for c in ts_codes if c))
    if not codes:
        return name_map
    try:
        placeholders = ','.join(['%s'] * len(codes))
        cursor.execute(
            f"SELECT ts_code, stock_name FROM stock_info_t "
            f"WHERE ts_code IN ({placeholders})", codes)
        for row in cursor.fetchall():
            if row.get('stock_name'):
                name_map[row['ts_code']] = row['stock_name']
    except Exception as e:
        print(f"⚠️ 从 stock_info_t 解析股票名称失败: {e}")
    return name_map


def record_selected_stocks(strategy, rows, selection_date):
    """选股结果入库 strategy_selected_stock_daily_t（便于回测）。

    参数：
      strategy       策略名，如 '2wave_daily'、'turn_bottom'
      rows           [{'ts_code': '600000.SH', 'selected': 1,
                       'stock_name': '浦发银行'(可选)}, ...]
      selection_date 选股目标日 'YYYYMMDD'（取数据中最新交易日）

    股票名称解析优先级：rows 中显式传入的 'stock_name' > stock_info_t 按
    ts_code 自动解析；两者都没有时该字段写入 NULL。
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
            _ensure_stock_name_column(cursor)
            # 先清除同策略同交易日的旧记录，避免条件变更后残留
            # 注意：strategy 字段是逗号拼接的多策略名，需用 FIND_IN_SET 匹配包含
            cursor.execute(
                "DELETE FROM strategy_selected_stock_daily_t "
                "WHERE trade_date = %s AND FIND_IN_SET(%s, strategy) > 0",
                (selection_date, strategy))
            name_map = _fetch_stock_name_map(
                cursor, [r.get('ts_code') for r in rows])
            values = [
                (r['ts_code'],
                 r.get('stock_name') or name_map.get(r['ts_code']),
                 selection_date, strategy,
                 int(r.get('selected', 1)))
                for r in rows
            ]
            cursor.executemany(UPSERT_SQL, values)
        conn.commit()
        n1 = sum(1 for r in rows if r.get('selected', 1))
        nn = sum(1 for v in values if v[1])
        print(f"✅ 选股结果已入库: strategy={strategy} trade_date={selection_date} "
              f"共{len(rows)}条(selected=1共{n1}条, 含股票名称{nn}条)")
    except Exception as e:
        print(f"❌ 选股结果入库失败: {e}")
        conn.rollback()
    finally:
        close_connection(conn)
