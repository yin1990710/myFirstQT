#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易所市场总貌数据采集任务 (update_exchange_market_overview.py)

从上交所(sse.com.cn)、深交所(szse.cn)官网首页采集"市场总貌/数据总貌"数据，
按 全市场 / 主板 / 科创板 / 创业板 分板块，写入新建数据表 exchange_market_overview_t。

数据源（经首页实际请求确认，2026-09）：
  深交所：GET https://www.szse.cn/dynamicmodule/index/sczm.json
          basicmap.main = 全市场, .cdd = 主板, .nmk = 创业板
  上交所：静态 JS（无独立 JSON 接口），正则解析变量赋值
          data_1998.js = 数据总貌(全市场)
          data_1999.js = 主板
          data_2000.js = 科创板

统一字段口径（金额单位均为亿元，股本为亿股，成交量为万股）：
  trade_date(交易日) + exchange(SSE/SZSE) + board_type(全市场/主板/科创板/创业板) 为主键。

用法：
  .venv/bin/python update_exchange_market_overview.py
"""

import json
import os
import re
import ssl
import sys
import time
from datetime import datetime
from urllib.request import Request, urlopen

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

# ===============================================================
# 数据源配置
# ===============================================================
SZSE_URL = 'https://www.szse.cn/dynamicmodule/index/sczm.json'
SSE_JS = {
    '全市场': 'https://www.sse.com.cn/home/data_1998.js',
    '主板':   'https://www.sse.com.cn/home/data_1999.js',
    '科创板': 'https://www.sse.com.cn/home/data_2000.js',
}

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'),
    'Accept': '*/*',
}

# 不校验证书（交易所偶发证书链问题，数据为公开统计数据）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ===============================================================
# 通用 HTTP（标准库实现，超时 + 重试，无第三方依赖）
# ===============================================================
def http_get(url: str, referer: str, timeout: int = 20, retry: int = 3) -> str:
    headers = dict(HEADERS)
    headers['Referer'] = referer
    last_err = None
    for i in range(retry):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                raw = resp.read()
            # 交易所页面均为 utf-8
            return raw.decode('utf-8', errors='replace')
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f'  ⚠️ 第 {i + 1} 次请求失败: {e}')
            time.sleep(2 * (i + 1))
    raise RuntimeError(f'请求失败({retry}次): {url} -> {last_err}')


def _to_num(s):
    """安全转数值：空串/None → None，去掉逗号空白。"""
    if s is None:
        return None
    s = str(s).replace(',', '').strip()
    if s == '' or s == '--':
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def _date_compact(d: str) -> str:
    """2026-09-16 → 20260916。"""
    return d.replace('-', '').replace('/', '').strip()


# ===============================================================
# 深交所采集：sczm.json → 全市场/主板/创业板
# ===============================================================
def fetch_szse() -> list:
    """采集深交所市场总貌，返回标准化记录列表。"""
    print('[深交所] 请求 %s' % SZSE_URL)
    text = http_get(SZSE_URL, 'https://www.szse.cn/index/index.html')
    data = json.loads(text)
    bm = (data.get('result') or {}).get('basicmap') or {}
    trade_date = _date_compact(bm.get('time', ''))
    if not trade_date:
        raise RuntimeError('深交所返回缺少 basicmap.time')

    # 板块 → JSON 字段
    board_fields = {'全市场': 'main', '主板': 'cdd', '创业板': 'nmk'}
    records = []
    for board, fld in board_fields.items():
        items = bm.get(fld) or []
        kv = {it.get('name', ''): it.get('value') for it in items}
        rec = {
            'exchange': 'SZSE',
            'board_type': board,
            'trade_date': trade_date,
            'company_count':     _to_num(kv.get('上市公司数')),
            'listed_count':      _to_num(kv.get('上市证券数')),
            'total_mv':          _to_num(kv.get('股票总市值（亿元）') or kv.get('总市值（亿元）')),
            'float_mv':          _to_num(kv.get('股票流通市值（亿元）') or kv.get('流通市值（亿元）')),
            'total_amount':      _to_num(kv.get('股票成交金额（亿元）') or kv.get('总成交金额（亿元）')),
            'total_volume':      _to_num(kv.get('总成交量（万）')),
            'total_shares':      None,
            'float_shares':      None,
            'avg_pe':            _to_num(kv.get('股票平均市盈率') or kv.get('平均市盈率')),
            'data_source':       SZSE_URL + '#' + fld,
        }
        records.append(rec)
    print(f'[深交所] 交易日 {trade_date}，解析 {len(records)} 个板块')
    return records


# ===============================================================
# 上交所采集：3 个静态 JS → 全市场/主板/科创板
# ===============================================================
_JS_VAR_RE = re.compile(r"home_sjtj(?:_[a-z]+)?\.(\w+)\s*=\s*'([^']*)'")


def _parse_sse_js(text: str) -> dict:
    """从上交所静态 JS 中提取 var.field = 'value' 赋值。"""
    return {m.group(1): m.group(2) for m in _JS_VAR_RE.finditer(text)}


def fetch_sse() -> list:
    """采集上交所数据总貌，返回标准化记录列表。"""
    records = []
    for board, url in SSE_JS.items():
        print(f'[上交所] {board} 请求 {url}')
        text = http_get(url, 'https://www.sse.com.cn/')
        kv = _parse_sse_js(text)
        trade_date = _date_compact(kv.get('dataStatisticDate', ''))
        if not trade_date or 'mkt_value' not in kv:
            raise RuntimeError(f'上交所 {board} JS 解析失败: {sorted(kv.keys())}')
        rec = {
            'exchange': 'SSE',
            'board_type': board,
            'trade_date': trade_date,
            'company_count':     _to_num(kv.get('companyNumber')),
            'listed_count':      _to_num(kv.get('stockNumber')),
            'total_mv':          _to_num(kv.get('mkt_value')),
            'float_mv':          _to_num(kv.get('negotiable_value')),
            'total_amount':      None,   # 首页总貌不提供成交额
            'total_volume':      None,
            'total_shares':      _to_num(kv.get('iss_vol')),
            'float_shares':      _to_num(kv.get('ngt_vol')),
            'avg_pe':            _to_num(kv.get('ratioOfPe')),
            'data_source':       url,
        }
        records.append(rec)
    dates = {r['trade_date'] for r in records}
    print(f'[上交所] 交易日 {sorted(dates)}，解析 {len(records)} 个板块')
    return records


# ===============================================================
# 储存层：自动建表 + upsert
# ===============================================================
def ensure_table(conn):
    """自动创建交易所市场总貌统一宽表。"""
    sql = """
    CREATE TABLE IF NOT EXISTS `exchange_market_overview_t` (
      `trade_date` varchar(8) NOT NULL COMMENT '交易日期YYYYMMDD',
      `exchange` varchar(8) NOT NULL COMMENT '交易所：SSE上交所/SZSE深交所',
      `board_type` varchar(12) NOT NULL COMMENT '板块：全市场/主板/科创板/创业板',
      `company_count` int DEFAULT NULL COMMENT '上市公司数（家）',
      `listed_count` int DEFAULT NULL COMMENT '上市证券/股票数（只）',
      `total_mv` decimal(18,2) DEFAULT NULL COMMENT '总市值（亿元）',
      `float_mv` decimal(18,2) DEFAULT NULL COMMENT '流通市值（亿元）',
      `total_amount` decimal(18,2) DEFAULT NULL COMMENT '成交金额（亿元）',
      `total_volume` decimal(20,2) DEFAULT NULL COMMENT '成交量（万股，仅深交所）',
      `total_shares` decimal(18,2) DEFAULT NULL COMMENT '总股本（亿股，仅上交所）',
      `float_shares` decimal(18,2) DEFAULT NULL COMMENT '流通股本（亿股，仅上交所）',
      `avg_pe` decimal(12,4) DEFAULT NULL COMMENT '平均市盈率（倍）',
      `data_source` varchar(255) DEFAULT NULL COMMENT '数据来源URL',
      `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      PRIMARY KEY (`trade_date`,`exchange`,`board_type`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='沪深交易所市场总貌分板块数据表'
    """
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    cur.close()


def upsert_records(conn, records: list):
    """按主键 upsert（同日同板块重复运行则覆盖更新）。"""
    sql = """
    INSERT INTO exchange_market_overview_t
      (trade_date, exchange, board_type, company_count, listed_count,
       total_mv, float_mv, total_amount, total_volume,
       total_shares, float_shares, avg_pe, data_source)
    VALUES (%(trade_date)s, %(exchange)s, %(board_type)s, %(company_count)s, %(listed_count)s,
            %(total_mv)s, %(float_mv)s, %(total_amount)s, %(total_volume)s,
            %(total_shares)s, %(float_shares)s, %(avg_pe)s, %(data_source)s)
    ON DUPLICATE KEY UPDATE
      company_count=VALUES(company_count), listed_count=VALUES(listed_count),
      total_mv=VALUES(total_mv), float_mv=VALUES(float_mv),
      total_amount=VALUES(total_amount), total_volume=VALUES(total_volume),
      total_shares=VALUES(total_shares), float_shares=VALUES(float_shares),
      avg_pe=VALUES(avg_pe), data_source=VALUES(data_source)
    """
    cur = conn.cursor()
    cur.executemany(sql, records)
    conn.commit()
    cur.close()


# ===============================================================
# 主流程
# ===============================================================
def main():
    print('=' * 80)
    print('沪深交易所市场总貌数据采集')
    print('运行时间:', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 80)

    # 采集层
    records = []
    errors = []
    try:
        records.extend(fetch_szse())
    except Exception as e:  # noqa: BLE001
        errors.append(f'深交所: {e}')
        print('❌ 深交所采集失败:', e)
    try:
        records.extend(fetch_sse())
    except Exception as e:  # noqa: BLE001
        errors.append(f'上交所: {e}')
        print('❌ 上交所采集失败:', e)

    if not records:
        print('\n❌ 两个交易所均采集失败，任务结束。')
        for e in errors:
            print('  -', e)
        return 1

    # 储存层
    print('\n[储存层] 连接数据库并写入...')
    conn = get_mysql_connection()
    if not conn:
        print('❌ 数据库连接失败')
        return 1
    try:
        ensure_table(conn)
        print('  ✅ 表 exchange_market_overview_t 已就绪')
        upsert_records(conn, records)
        print(f'  ✅ 写入 {len(records)} 条记录')

        # 回读校验（按本次采集到的交易日回读）
        dates = sorted({r['trade_date'] for r in records}, reverse=True)
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_date, exchange, board_type, company_count,
                   total_mv, float_mv, avg_pe
            FROM exchange_market_overview_t
            WHERE trade_date IN (%s)
            ORDER BY trade_date DESC, exchange,
                     FIELD(board_type,'全市场','主板','科创板','创业板')
        """ % ','.join(['%s'] * len(dates)), dates)
        print('\n本次数据预览:')
        print(f"  {'交易日':<10}{'交易所':<7}{'板块':<7}{'公司数':>6}{'总市值(亿)':>14}{'流通市值(亿)':>14}{'平均PE':>9}")
        for r in cur.fetchall():
            print(f"  {r['trade_date']:<10}{r['exchange']:<7}{r['board_type']:<7}"
                  f"{str(r['company_count']):>6}{str(r['total_mv']):>14}"
                  f"{str(r['float_mv']):>14}{str(r['avg_pe']):>9}")
        cur.close()
    finally:
        close_connection(conn)

    print('\n' + '=' * 80)
    if errors:
        print('⚠️ 部分数据源失败:', '; '.join(errors))
        print('=' * 80)
        return 2
    print('🎉 采集完成，沪深两个交易所 × 3 板块共 6 条记录已落库')
    print('=' * 80)
    return 0


if __name__ == '__main__':
    sys.exit(main())
