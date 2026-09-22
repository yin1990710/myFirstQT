#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
update_stock_dfcf_industry.py — 更新股票东财行业分类

数据来源：calc_em_boards.get_stock_boards()（东财 slist 个股反查所属板块接口）
  - 入参为 6 位股票代码（不带交易所后缀，如 688213）
  - 返回该股全部所属板块（行业+概念+风格，如半导体/AI芯片/国产芯片…）

写入 stock_dfcf_industry_t 表，以 (ts_code, board_code) 为唯一键 upsert。
全市场遍历，每只股票每个板块一条记录，入表时 ts_code 保存带后缀完整代码。
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from calc_em_boards import get_stock_boards

# 每次请求间隔（秒），避免被东财限流
REQUEST_INTERVAL = 0.15

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_dfcf_industry_t (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    ts_code     VARCHAR(16)  NOT NULL COMMENT '股票代码(带后缀,如688213.SH)',
    board_code  VARCHAR(16)  NOT NULL COMMENT '东财板块代码(BK开头)',
    board_name  VARCHAR(64)  NOT NULL COMMENT '板块名称',
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_code_board (ts_code, board_code),
    KEY idx_board (board_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='股票东财行业/板块分类表';
"""

UPSERT_SQL = """
INSERT INTO stock_dfcf_industry_t (ts_code, board_code, board_name)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE board_name = VALUES(board_name)
"""


def read_all_stocks():
    """从 stock_info_t 读取全部股票代码（带后缀）。"""
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ts_code FROM stock_info_t ORDER BY ts_code")
            rows = cursor.fetchall()
        return [r['ts_code'] for r in rows]
    finally:
        close_connection(connection)


def fetch_boards_for_stock(ts_code):
    """调用 calc_em_boards.get_stock_boards 获取个股所属板块。

    入参 ts_code 带后缀（如 688213.SH），调用时去掉后缀传 6 位代码。
    返回 list[(board_code, board_name)]，失败返回空列表。
    """
    code = ts_code.split('.')[0]
    try:
        df = get_stock_boards(code)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    out = []
    for _, row in df.iterrows():
        bc = str(row.get('板块代码') or '').strip()
        bn = str(row.get('板块名称') or '').strip()
        if bc and bn:
            out.append((bc, bn))
    return out


def main():
    print("=" * 60)
    print("更新股票东财行业分类（stock_dfcf_industry_t）")
    print("=" * 60)

    # 1. 建表
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败，退出")
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
        connection.commit()
        print("✅ stock_dfcf_industry_t 表创建成功（或已存在）")
    finally:
        close_connection(connection)

    # 2. 读取全部股票
    all_codes = read_all_stocks()
    print(f"待处理股票: {len(all_codes)} 只")

    total_boards = 0
    failed = 0
    batch = []
    BATCH_SIZE = 200

    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败，退出")
        return

    try:
        for i, ts_code in enumerate(all_codes, 1):
            boards = fetch_boards_for_stock(ts_code)
            if not boards:
                failed += 1
            else:
                for bc, bn in boards:
                    batch.append((ts_code, bc, bn))
                    total_boards += 1

            # 批量入库
            if len(batch) >= BATCH_SIZE:
                with connection.cursor() as cursor:
                    cursor.executemany(UPSERT_SQL, batch)
                connection.commit()
                batch.clear()

            if i % 500 == 0:
                print(f"  进度: {i}/{len(all_codes)} | 已入库 {total_boards} 条板块 | 失败 {failed} 只")

            time.sleep(REQUEST_INTERVAL)

        # 剩余数据入库
        if batch:
            with connection.cursor() as cursor:
                cursor.executemany(UPSERT_SQL, batch)
            connection.commit()

        print("\n" + "=" * 60)
        print(f"🎉 完成！共 {len(all_codes)} 只股票，{total_boards} 条板块记录，{failed} 只获取失败")
        print("=" * 60)
    except Exception as e:
        print(f"❌ 入库失败: {e}")
        connection.rollback()
    finally:
        close_connection(connection)


if __name__ == "__main__":
    main()
