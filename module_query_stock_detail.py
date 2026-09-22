#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
个股数据详情 · 数据查询层（第 1 层）
====================================

供 app.py 的 /api/stock_detail、/api/stock_search、/api/stock_latest_date 路由调用。
仅负责 SQL 查询与类型清洗（Decimal→float），单位换算由 app 层完成。

模块分层：
  ┌──────────────────────────────────────────────┐
  │ 数据获取（MySQL）                              │
  │   search_stocks(keyword, limit)  → 联想补全    │
  │   get_latest_trade_date(code)    → 最近交易日  │
  │   get_stock_detail(code, date)  → 单日快照     │
  └──────────────────────────────────────────────┘

数据来源：
  - stock_daily_t          行情 + ma5/ma30/short_strength_score
  - stock_daily_basic_info_t  turnover_rate_f/pe/pe_ttm/pb/total_mv/circ_mv/dv_ttm
  - stock_info_t           stock_name（注意与 stock_daily_t 排序规则不同，JOIN 需 COLLATE）
"""

from decimal import Decimal

from module_mysql_connection import get_mysql_connection, close_connection


def _to_float(v):
    """Decimal/数值 → float；None 保留。"""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def search_stocks(keyword, limit=20):
    """股票代码/名称联想补全。

    :param keyword: 关键词（代码或名称片段）
    :param limit: 最多返回条数
    :return: [{'ts_code': str, 'stock_name': str}, ...]；关键词为空或过短返回 []
    """
    kw = (keyword or '').strip()
    if len(kw) < 2:
        return []
    pattern = f"%{kw}%"
    conn = get_mysql_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT ts_code, stock_name
                   FROM stock_info_t
                   WHERE ts_code LIKE %s OR stock_name LIKE %s
                   LIMIT %s""",
                (pattern, pattern, limit),
            )
            rows = cur.fetchall()
        return [{'ts_code': r['ts_code'], 'stock_name': r['stock_name']} for r in rows]
    except Exception as e:
        print(f"❌ search_stocks 查询失败: {e}")
        return []
    finally:
        close_connection(conn)


def get_latest_trade_date(code):
    """该股最近一个交易日（YYYYMMDD 字符串）。无数据返回 None。"""
    code = (code or '').strip()
    if not code:
        return None
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(trade_date) AS latest FROM stock_daily_t WHERE ts_code = %s",
                (code,),
            )
            row = cur.fetchone()
        if not row:
            return None
        latest = row.get('latest')
        return latest if latest else None
    except Exception as e:
        print(f"❌ get_latest_trade_date 查询失败: {e}")
        return None
    finally:
        close_connection(conn)


def get_stock_detail(code, trade_date):
    """单股单日完整字段快照。

    关联 stock_daily_t + stock_daily_basic_info_t + stock_info_t，
    返回 17 字段原始值（数值已转 float，未做单位换算）或 None。

    :param code: 股票代码（如 000001.SZ）
    :param trade_date: 交易日期（YYYYMMDD）
    :return: dict 或 None
    """
    code = (code or '').strip()
    trade_date = (trade_date or '').strip()
    if not code or not trade_date:
        return None

    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT d.ts_code, d.trade_date, d.short_strength_score,
                          d.open, d.close, d.pct_chg, d.amount, d.ma5, d.ma30,
                          b.turnover_rate_f, b.pe, b.pe_ttm, b.pb,
                          b.total_mv, b.circ_mv, b.dv_ttm,
                          i.stock_name
                   FROM stock_daily_t d
                   LEFT JOIN stock_daily_basic_info_t b
                          ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
                   LEFT JOIN stock_info_t i
                          ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
                   WHERE d.ts_code = %s AND d.trade_date = %s
                   LIMIT 1""",
                (code, trade_date),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            'ts_code':             row.get('ts_code'),
            'stock_name':          row.get('stock_name'),
            'trade_date':          row.get('trade_date'),
            'short_strength_score': _to_float(row.get('short_strength_score')),
            'open':                _to_float(row.get('open')),
            'close':               _to_float(row.get('close')),
            'pct_chg':             _to_float(row.get('pct_chg')),
            'amount':              _to_float(row.get('amount')),       # 千元
            'turnover_rate_f':     _to_float(row.get('turnover_rate_f')),
            'pe':                  _to_float(row.get('pe')),
            'pe_ttm':              _to_float(row.get('pe_ttm')),
            'pb':                  _to_float(row.get('pb')),
            'total_mv':            _to_float(row.get('total_mv')),     # 万元
            'circ_mv':             _to_float(row.get('circ_mv')),      # 万元
            'dv_ttm':              _to_float(row.get('dv_ttm')),
            'ma5':                 _to_float(row.get('ma5')),
            'ma30':                _to_float(row.get('ma30')),
        }
    except Exception as e:
        print(f"❌ get_stock_detail 查询失败: {e}")
        return None
    finally:
        close_connection(conn)


def get_stock_list(trade_date):
    """某交易日全市场股票列表（数值已转 float，未做单位换算）。

    字段含 industries（逗号分隔的东财行业名，取自 stock_dfcf_industry_t）。
    前端按总市值/股息率/换手率/成交额区间过滤、按字段排序、分页展示。

    :param trade_date: 交易日期（YYYYMMDD）
    :return: [dict, ...]；连接失败返回 None
    """
    trade_date = (trade_date or '').strip()
    if not trade_date:
        return None
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT d.ts_code, d.trade_date, d.short_strength_score,
                          d.open, d.close, d.pct_chg, d.amount,
                          b.turnover_rate_f, b.pe, b.pe_ttm, b.pb,
                          b.total_mv, b.circ_mv, b.dv_ttm,
                          i.stock_name,
                          ind.industries
                   FROM stock_daily_t d
                   LEFT JOIN stock_daily_basic_info_t b
                          ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
                   LEFT JOIN stock_info_t i
                          ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
                   LEFT JOIN (
                       SELECT ts_code,
                              GROUP_CONCAT(board_name SEPARATOR ',') AS industries
                       FROM stock_dfcf_industry_t
                       GROUP BY ts_code
                   ) ind ON d.ts_code COLLATE utf8mb4_unicode_ci
                          = ind.ts_code COLLATE utf8mb4_unicode_ci
                   WHERE d.trade_date = %s
                   ORDER BY d.ts_code""",
                (trade_date,),
            )
            rows = cur.fetchall()
        return [{
            'ts_code':              r.get('ts_code'),
            'stock_name':           r.get('stock_name'),
            'trade_date':           r.get('trade_date'),
            'short_strength_score': _to_float(r.get('short_strength_score')),
            'open':                 _to_float(r.get('open')),
            'close':                _to_float(r.get('close')),
            'pct_chg':              _to_float(r.get('pct_chg')),
            'amount':               _to_float(r.get('amount')),        # 千元
            'turnover_rate_f':      _to_float(r.get('turnover_rate_f')),
            'pe':                   _to_float(r.get('pe')),
            'pe_ttm':               _to_float(r.get('pe_ttm')),
            'pb':                   _to_float(r.get('pb')),
            'total_mv':             _to_float(r.get('total_mv')),      # 万元
            'circ_mv':              _to_float(r.get('circ_mv')),       # 万元
            'dv_ttm':               _to_float(r.get('dv_ttm')),
            'industries':           r.get('industries') or '',          # 逗号分隔的行业名
        } for r in rows]
    except Exception as e:
        print(f"❌ get_stock_list 查询失败: {e}")
        return None
    finally:
        close_connection(conn)


def get_global_latest_trade_date():
    """全市场最近一个交易日（YYYYMMDD），用于列表页日期框初始值。无数据返回 None。"""
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(trade_date) AS latest FROM stock_daily_t")
            row = cur.fetchone()
        return (row or {}).get('latest') or None
    except Exception as e:
        print(f"❌ get_global_latest_trade_date 查询失败: {e}")
        return None
    finally:
        close_connection(conn)
