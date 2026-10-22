#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_cffex_position.py —— 中金所股指期货（IF/IH/IC/IM）多空持仓采集入库
================================================================================

数据来源：中国金融期货交易所官网（盘后约 16:15 发布 T 日数据），
抓取逻辑复用同目录 cffex_position.py（前 20 名结算会员成交持仓排名，官方口径）。

入库表：cffex_position_daily_t（每合约一行，主力合约 is_main=1）。
关键口径：
  - long/short 为前 20 名会员持买/卖单量合计，增减取交易所官方 varvolume 字段；
  - net_position = 多头合计 - 空头合计；long_ratio = 多头 /（多头 + 空头）；
  - 主力合约 = 当日该品种前 20 名会员成交量合计最大的合约；
  - 「前 20 名会员」≠ 全市场，股指期货空单常用于对冲现货，净持仓不代表看空观点。

用法：
    python update_cffex_position.py                     # 抓最近已发布交易日
    python update_cffex_position.py --start 20261001    # 回填至最近
    python update_cffex_position.py --start 20261001 --end 20261022  # 指定区间
"""

import argparse
import sys
from datetime import datetime

from module_mysql_connection import get_mysql_connection, close_connection
from cffex_position import (
    CffexClient, CffexError, NoDataError,
    PRODUCTS, rank_to_frame, summarize_positions,
)

# ===============================================================
# 储存层：自动建表 + upsert
# ===============================================================
def ensure_table(conn):
    """自动创建股指期货多空持仓表。"""
    sql = """
    CREATE TABLE IF NOT EXISTS `cffex_position_daily_t` (
      `trading_day` varchar(8) NOT NULL COMMENT '交易日期YYYYMMDD',
      `instrument` varchar(16) NOT NULL COMMENT '合约代码，如IF2611',
      `product` varchar(8) NOT NULL COMMENT '品种代码：IF/IH/IC/IM',
      `product_cn` varchar(40) DEFAULT NULL COMMENT '品种中文名',
      `is_main` tinyint NOT NULL DEFAULT 0 COMMENT '是否主力合约（当日成交量合计最大）：1是/0否',
      `long_total` int DEFAULT NULL COMMENT '前20名会员持买单量（多头）合计（手）',
      `long_chg` int DEFAULT NULL COMMENT '多头比上交易日增减合计（手，官方口径）',
      `short_total` int DEFAULT NULL COMMENT '前20名会员持卖单量（空头）合计（手）',
      `short_chg` int DEFAULT NULL COMMENT '空头比上交易日增减合计（手，官方口径）',
      `net_position` int DEFAULT NULL COMMENT '净持仓=多头合计-空头合计（手）',
      `net_chg` int DEFAULT NULL COMMENT '净持仓变化=多头增减-空头增减（手）',
      `long_ratio` decimal(6,4) DEFAULT NULL COMMENT '多头占比=多头/(多头+空头)',
      `volume_total` int DEFAULT NULL COMMENT '前20名会员成交量合计（手）',
      `volume_chg` int DEFAULT NULL COMMENT '成交量比上交易日增减合计（手）',
      `data_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
      `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      PRIMARY KEY (`trading_day`,`instrument`),
      KEY `idx_product_day` (`product`,`trading_day`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='中金所股指期货多空持仓（前20名会员）数据表'
    """
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    cur.close()


def _to_int(v):
    try:
        f = float(v)
        return None if f != f else int(f)   # NaN -> None
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        f = float(v)
        return None if f != f else f        # NaN -> None
    except (TypeError, ValueError):
        return None


def fetch_day_records(client: CffexClient, day: str) -> list:
    """抓取某交易日在库的品种多空持仓汇总，每合约一行，主力合约打标。"""
    records = []
    for product in PRODUCTS:
        try:
            rows = client.fetch_rank(product, day)
        except NoDataError:
            print(f'  [{product}] {day} 无数据（非交易日或尚未发布）')
            continue
        summ = summarize_positions(rank_to_frame(rows), top_n=20)
        if summ.empty:
            continue
        # 主力合约：成交量合计最大者
        main_instrument = summ.sort_values('volume_total', ascending=False).iloc[0]['instrument']
        for _, r in summ.iterrows():
            records.append({
                'trading_day': str(r['trading_day']),
                'instrument': str(r['instrument']),
                'product': str(r['product']),
                'product_cn': str(r['product_cn'] or ''),
                'is_main': 1 if str(r['instrument']) == str(main_instrument) else 0,
                'long_total': _to_int(r['long_total']),
                'long_chg': _to_int(r['long_chg']),
                'short_total': _to_int(r['short_total']),
                'short_chg': _to_int(r['short_chg']),
                'net_position': _to_int(r['net_position']),
                'net_chg': _to_int(r['net_chg']),
                'long_ratio': _to_float(r['long_ratio']),
                'volume_total': _to_int(r['volume_total']),
                'volume_chg': _to_int(r['volume_chg']),
                'data_source': 'cffex.com.cn(前20名会员,官方varvolume)',
            })
    return records


def upsert_records(conn, records: list):
    """按主键 (trading_day, instrument) upsert。"""
    if not records:
        return 0
    sql = """
    INSERT INTO cffex_position_daily_t
      (trading_day, instrument, product, product_cn, is_main,
       long_total, long_chg, short_total, short_chg,
       net_position, net_chg, long_ratio, volume_total, volume_chg, data_source)
    VALUES (%(trading_day)s, %(instrument)s, %(product)s, %(product_cn)s, %(is_main)s,
            %(long_total)s, %(long_chg)s, %(short_total)s, %(short_chg)s,
            %(net_position)s, %(net_chg)s, %(long_ratio)s, %(volume_total)s, %(volume_chg)s,
            %(data_source)s)
    ON DUPLICATE KEY UPDATE
      product=VALUES(product), product_cn=VALUES(product_cn), is_main=VALUES(is_main),
      long_total=VALUES(long_total), long_chg=VALUES(long_chg),
      short_total=VALUES(short_total), short_chg=VALUES(short_chg),
      net_position=VALUES(net_position), net_chg=VALUES(net_chg),
      long_ratio=VALUES(long_ratio),
      volume_total=VALUES(volume_total), volume_chg=VALUES(volume_chg),
      data_source=VALUES(data_source)
    """
    cur = conn.cursor()
    n = cur.executemany(sql, records)
    conn.commit()
    cur.close()
    return n


# ===============================================================
# 主流程
# ===============================================================
def main():
    parser = argparse.ArgumentParser(description='中金所股指期货多空持仓采集入库')
    parser.add_argument('--start', help='回填起始日 YYYYMMDD（含），可单独使用，回填至最近已发布日')
    parser.add_argument('--end', help='回填结束日 YYYYMMDD（含），需与 --start 同用')
    args = parser.parse_args()

    print('=' * 80)
    print('中金所股指期货多空持仓采集')
    print('运行时间:', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 80)

    client = CffexClient()
    conn = get_mysql_connection()
    if not conn:
        print('❌ 数据库连接失败')
        return 1
    try:
        ensure_table(conn)

        if args.start:
            end = args.end if args.end else client.latest_available_date('IF')
            days = client.trading_days(args.start, end)
            print(f'回填区间 {args.start} ~ {end}：共 {len(days)} 个已发布交易日')
        else:
            days = [client.latest_available_date('IF')]
            print(f'未指定日期，使用最近已发布交易日：{days[0]}')

        total = 0
        for day in days:
            records = fetch_day_records(client, day)
            if records:
                upsert_records(conn, records)
            total += len(records)
            mains = [r for r in records if r['is_main']]
            preview = '  '.join(
                f"{r['product']}{r['instrument']} 多{r['long_total']:+d} 空{r['short_total']:+d} 净{r['net_position']:+d}"
                if r['long_total'] is not None else f"{r['product']}{r['instrument']} N/A"
                for r in mains[:4])
            print(f'  {day}：入库 {len(records)} 行（主力合约：{preview}）')

        print(f'✅ 共写入 {total} 行（表 cffex_position_daily_t）')
        return 0
    except (CffexError, NoDataError) as e:
        print(f'❌ 采集失败：{e}')
        return 2
    finally:
        close_connection(conn)
        client.close()


if __name__ == '__main__':
    sys.exit(main())
