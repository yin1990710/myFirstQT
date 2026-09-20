#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
update_em_board_daily.py — 东方财富行业/概念板块当日快照入库

数据来源：em_boards.py（push2.eastmoney.com 官方接口）
  - get_industry_boards()  东财行业板块列表（约 86 个）
  - get_concept_boards()   东财概念板块列表（400+ 个）

写入 em_board_daily_t 表，按 (board_code, trade_date) 唯一键 upsert。
交易日口径：0-15 点取前一交易日，15 点后取当日（与 select 脚本一致）。
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from em_boards import get_industry_boards, get_concept_boards


def get_target_date():
    """目标交易日：0-15 点取前一交易日，15 点后取当日。"""
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS em_board_daily_t (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    board_code      VARCHAR(16)  NOT NULL COMMENT '东财板块代码(BK开头)',
    board_name      VARCHAR(64)  NOT NULL COMMENT '板块名称',
    board_type      VARCHAR(8)   NOT NULL COMMENT '板块类型: 行业/概念',
    trade_date      VARCHAR(8)   NOT NULL COMMENT '交易日期(YYYYMMDD)',
    pct_chg         DECIMAL(6,2)          COMMENT '涨跌幅(%)',
    turnover_rate   DECIMAL(10,4)        COMMENT '换手率(%)',
    main_net_inflow DECIMAL(20,2)        COMMENT '主力净流入(元)',
    up_count        INT                 COMMENT '上涨家数',
    down_count      INT                 COMMENT '下跌家数',
    leading_stock   VARCHAR(32)         COMMENT '领涨股名称',
    total_mv        DECIMAL(20,2)        COMMENT '总市值(元)',
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_board_date (board_code, trade_date),
    KEY idx_date_type (trade_date, board_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='东方财富行业/概念板块当日快照表';
"""

UPSERT_SQL = """
INSERT INTO em_board_daily_t
    (board_code, board_name, board_type, trade_date,
     pct_chg, turnover_rate, main_net_inflow,
     up_count, down_count, leading_stock, total_mv)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    board_name      = VALUES(board_name),
    pct_chg         = VALUES(pct_chg),
    turnover_rate   = VALUES(turnover_rate),
    main_net_inflow = VALUES(main_net_inflow),
    up_count        = VALUES(up_count),
    down_count      = VALUES(down_count),
    leading_stock   = VALUES(leading_stock),
    total_mv        = VALUES(total_mv)
"""


def _to_float(v):
    """安全转 float：None/空/非数值返回 None。"""
    if v is None or v == '' or (isinstance(v, float) and v != v):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_int(v):
    f = _to_float(v)
    return int(f) if f is not None else None


def fetch_boards(trade_date):
    """拉取行业 + 概念板块当日快照，返回 list[dict]。"""
    rows = []
    for board_type, fetch_fn in (('行业', get_industry_boards),
                                 ('概念', get_concept_boards)):
        print(f"📡 正在获取东财{board_type}板块列表...")
        try:
            df = fetch_fn()
        except Exception as e:
            print(f"⚠️ {board_type}板块获取失败，跳过: {e}")
            continue
        if df is None or df.empty:
            print(f"⚠️ {board_type}板块返回空")
            continue
        for _, r in df.iterrows():
            rows.append({
                'board_code': str(r.get('代码') or '').strip(),
                'board_name': str(r.get('名称') or '').strip(),
                'board_type': board_type,
                'trade_date': trade_date,
                'pct_chg':         _to_float(r.get('涨跌幅%')),
                'turnover_rate':    _to_float(r.get('换手率%')),
                'main_net_inflow':  _to_float(r.get('主力净流入(元)')),
                'up_count':         _to_int(r.get('上涨家数')),
                'down_count':       _to_int(r.get('下跌家数')),
                'leading_stock':    str(r.get('领涨股') or '').strip() or None,
                'total_mv':         _to_float(r.get('总市值(元)')),
            })
        print(f"✅ {board_type}板块取到 {len(df)} 个")
    return rows


def main():
    print("=" * 60)
    print("更新东财行业/概念板块当日快照")
    print("=" * 60)

    trade_date = get_target_date()
    print(f"目标交易日: {trade_date}")

    rows = fetch_boards(trade_date)
    if not rows:
        print("⚠️ 未取到任何板块数据，退出")
        return

    # 过滤掉无板块代码的脏行
    rows = [r for r in rows if r['board_code']]
    print(f"共 {len(rows)} 条待入库")

    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败，退出")
        return

    try:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            connection.commit()
            print("✅ em_board_daily_t 表创建成功（或已存在）")

            data = [(
                r['board_code'], r['board_name'], r['board_type'], r['trade_date'],
                r['pct_chg'], r['turnover_rate'], r['main_net_inflow'],
                r['up_count'], r['down_count'], r['leading_stock'], r['total_mv'],
            ) for r in rows]
            cursor.executemany(UPSERT_SQL, data)
        connection.commit()
        print(f"✅ 成功插入/更新 {len(rows)} 条板块数据")

        print("\n" + "=" * 60)
        print("🎉 东财板块数据更新完成！")
        print("=" * 60)
    except Exception as e:
        print(f"❌ 入库失败: {e}")
        connection.rollback()
    finally:
        close_connection(connection)


if __name__ == "__main__":
    main()
