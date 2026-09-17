#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘指标体系 —— 15指标计算与报告生成 (report_market_overview_metricx.py)

五层架构：
  ① 采集层   → 从现有 MySQL 表读取原始数据（stock_daily_t / stock_daily_basic_info_t /
                rzrq_ye_t / sse_market_summary_t / szse_market_summary_t）
  ② 储存层   → market_overview_metric_daily_t 指标快照表（每日一行×15指标）
  ③ 计算层   → 15 个指标标准化计算 + 派生比率
  ④ 请求响应层 → app.py 提供 /api/market_overview_metrics JSON 接口
  ⑤ 展示层   → pages/大盘指标概览.html 仪表盘页面

三大类 15 个指标：
  【A 短线技术】A1 收盘价>MA5占比 / A2 收盘价>MA30占比 / A3 涨幅中位数 / A4 涨平跌家数比
  【B 资金面 】B1 换手率中位数 / B2 成交额÷总市值 / B3 全市场成交额 /
              B4 融资余额 / B5 融资÷流通市值 / B6 融券余额 / B7 融券÷流通市值
  【C 估值  】C1 上证主板PE / C2 深证主板PE(自算) / C3 创业板PE(自算) / C4 科创板PE

用法：
  .venv/bin/python report_market_overview_metricx.py
"""

import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

PAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pages')
OUTPUT_JSON = os.path.join(PAGE_DIR, 'market_overview_metrics.json')

YI = 100000000          # 元 → 亿元
WAN = 10000             # 万元 → 亿元
QIAN = 100000           # 千元 → 亿元（1亿=10万元=100,000千元）


# ===============================================================
# 板块归属（按代码前缀）
# ===============================================================
def board_of(code: str) -> str:
    c = str(code).replace('.SZ', '').replace('.SH', '').replace('.BJ', '').zfill(6)
    if c.startswith(('600', '601', '603', '605')):
        return '沪主板'
    if c.startswith(('000', '001', '002', '003')):
        return '深主板'
    if c.startswith(('300', '301')):
        return '创业板'
    if c.startswith(('688', '689')):
        return '科创板'
    if c.startswith(('8', '4', '9')):
        return '北交所'
    return '其他'


# ===============================================================
# ① 采集层：从 MySQL 读取原始数据
# ===============================================================
def get_target_date(conn) -> str:
    """获取最新有数据的交易日（stock_daily_t 中最大 trade_date）。"""
    sql = "SELECT MAX(trade_date) AS latest FROM stock_daily_t"
    cursor = conn.cursor()
    cursor.execute(sql)
    row = cursor.fetchone()
    cursor.close()
    if row and row['latest']:
        return str(row['latest'])
    # 兜底：当前时间为0-15点取前一天
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def collect_stock_daily(conn, trade_date: str) -> pd.DataFrame:
    """采集层：读取个股日线（close/ma5/ma30/pct_chg/amount）。"""
    sql = """
    SELECT ts_code, close, ma5, ma30, pct_chg, amount
    FROM stock_daily_t
    WHERE trade_date = %s
    """
    cursor = conn.cursor()
    cursor.execute(sql, (trade_date,))
    rows = cursor.fetchall()
    cursor.close()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def collect_stock_basic(conn, trade_date: str) -> pd.DataFrame:
    """采集层：读取个股基本面（turnover_rate/pe/total_mv/circ_mv）。"""
    sql = """
    SELECT ts_code, turnover_rate, pe, pe_ttm, total_mv, circ_mv
    FROM stock_daily_basic_info_t
    WHERE trade_date = %s
    """
    cursor = conn.cursor()
    cursor.execute(sql, (trade_date,))
    rows = cursor.fetchall()
    cursor.close()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def collect_rzrq(conn, trade_date: str, days: int = 60) -> pd.DataFrame:
    """采集层：读取两融余额（rzye/rqye），取最近 N 日用于趋势计算。"""
    start = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=days * 2)).strftime('%Y%m%d')
    sql = """
    SELECT trade_date, exchange_id, rzye, rqye
    FROM rzrq_ye_t
    WHERE trade_date >= %s AND trade_date <= %s
    ORDER BY trade_date
    """
    cursor = conn.cursor()
    cursor.execute(sql, (start, trade_date))
    rows = cursor.fetchall()
    cursor.close()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fetch_exchange_rows(conn, exchange: str, trade_date: str) -> dict:
    """采集层：从新表 exchange_market_overview_t 读取某交易所分板块记录。

    返回 {board_type: row} 精确映射；目标日无数据时回退到该交易所最新交易日，
    并在 row 字典中标记实际交易日（actual_date）。
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT trade_date, board_type, total_mv, float_mv, total_amount, "
        "total_shares, float_shares, avg_pe, company_count, listed_count "
        "FROM exchange_market_overview_t WHERE exchange = %s AND trade_date = %s",
        (exchange, trade_date))
    rows = cursor.fetchall()
    actual_date = trade_date
    if not rows:
        # 兜底：取该交易所最新一个交易日
        cursor.execute(
            "SELECT MAX(trade_date) AS d FROM exchange_market_overview_t WHERE exchange = %s",
            (exchange,))
        latest = cursor.fetchone()
        if latest and latest.get('d'):
            actual_date = str(latest['d'])
            cursor.execute(
                "SELECT trade_date, board_type, total_mv, float_mv, total_amount, "
                "total_shares, float_shares, avg_pe, company_count, listed_count "
                "FROM exchange_market_overview_t WHERE exchange = %s AND trade_date = %s",
                (exchange, actual_date))
            rows = cursor.fetchall()
    cursor.close()
    mapped = {str(r['board_type']): r for r in rows}
    mapped['_actual_date'] = actual_date if rows else None
    return mapped


def collect_sse_summary(conn, trade_date: str) -> dict:
    """采集层：上交所市场总貌（新表）：全市场市值 + 主板/科创板官方PE。"""
    rows = _fetch_exchange_rows(conn, 'SSE', trade_date)
    all_row = rows.get('全市场') or {}
    main_row = rows.get('主板') or {}
    star_row = rows.get('科创板') or {}
    return {
        'sse_pe_main':  _float(main_row.get('avg_pe')),
        'sse_pe_star':  _float(star_row.get('avg_pe')),
        'sse_float_mv': _float(all_row.get('float_mv')),
        'sse_total_mv': _float(all_row.get('total_mv')),
        'sse_date':     rows.get('_actual_date'),
    }


def collect_szse_summary(conn, trade_date: str) -> dict:
    """采集层：深交所市场总貌（新表）：全市场市值/成交额 + 主板/创业板官方PE。"""
    rows = _fetch_exchange_rows(conn, 'SZSE', trade_date)
    all_row = rows.get('全市场') or {}
    main_row = rows.get('主板') or {}
    cyb_row = rows.get('创业板') or {}
    return {
        'szse_pe_main':  _float(main_row.get('avg_pe')),
        'szse_pe_cyb':   _float(cyb_row.get('avg_pe')),
        'szse_float_mv': _float(all_row.get('float_mv')),
        'szse_total_mv': _float(all_row.get('total_mv')),
        'szse_amount':   _float(all_row.get('total_amount')),
        'szse_date':     rows.get('_actual_date'),
    }


# ===============================================================
# ② 储存层：指标快照表
# ===============================================================
def ensure_metric_table(conn):
    """储存层：创建指标快照表（如不存在）。"""
    sql = """
    CREATE TABLE IF NOT EXISTS `market_overview_metric_daily_t` (
      `trade_date` varchar(8) NOT NULL COMMENT '交易日期YYYYMMDD',
      `metric_key` varchar(10) NOT NULL COMMENT '指标编号(A1-A4,B1-B7,C1-C4)',
      `metric_group` varchar(20) NOT NULL COMMENT '指标分类(短线技术/资金面/估值)',
      `metric_name` varchar(100) NOT NULL COMMENT '指标名称',
      `metric_value` decimal(20,4) DEFAULT NULL COMMENT '指标值',
      `metric_unit` varchar(10) DEFAULT NULL COMMENT '单位(%,亿元,倍,家)',
      `metric_source` varchar(100) DEFAULT NULL COMMENT '数据来源',
      `metric_status` varchar(20) DEFAULT 'ok' COMMENT '状态(ok/approx/missing)',
      `metric_note` varchar(500) DEFAULT NULL COMMENT '备注',
      `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      PRIMARY KEY (`trade_date`,`metric_key`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='大盘指标每日快照表'
    """
    cursor = conn.cursor()
    cursor.execute(sql)
    conn.commit()
    cursor.close()


def write_metrics_to_db(conn, trade_date: str, metrics: list):
    """储存层：将指标写入快照表（覆盖同日同指标）。"""
    sql = """
    INSERT INTO market_overview_metric_daily_t
      (trade_date, metric_key, metric_group, metric_name, metric_value,
       metric_unit, metric_source, metric_status, metric_note)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      metric_group=VALUES(metric_group), metric_name=VALUES(metric_name),
      metric_value=VALUES(metric_value), metric_unit=VALUES(metric_unit),
      metric_source=VALUES(metric_source), metric_status=VALUES(metric_status),
      metric_note=VALUES(metric_note)
    """
    cursor = conn.cursor()
    for m in metrics:
        cursor.execute(sql, (
            trade_date, m['key'], m['group'], m['name'],
            m['value'], m['unit'], m['source'], m['status'], m['note']
        ))
    conn.commit()
    cursor.close()


# ===============================================================
# ③ 计算层：15 指标标准化计算
# ===============================================================
def _float(v):
    """安全转 float，处理 Decimal/None。"""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def compute_a_class(stock_df: pd.DataFrame) -> dict:
    """计算层：A 类短线技术指标（A1-A4）。"""
    v = {}
    if stock_df.empty:
        return v

    # A1/A2：收盘价在 MA5/MA30 上方占比
    valid_ma5 = stock_df.dropna(subset=['close', 'ma5'])
    valid_ma5 = valid_ma5[valid_ma5['ma5'] > 0]
    if len(valid_ma5) > 0:
        v['a1_above_ma5'] = round(float((valid_ma5['close'] > valid_ma5['ma5']).mean()) * 100, 2)
        v['a1_sample'] = len(valid_ma5)

    valid_ma30 = stock_df.dropna(subset=['close', 'ma30'])
    valid_ma30 = valid_ma30[valid_ma30['ma30'] > 0]
    if len(valid_ma30) > 0:
        v['a2_above_ma30'] = round(float((valid_ma30['close'] > valid_ma30['ma30']).mean()) * 100, 2)
        v['a2_sample'] = len(valid_ma30)

    # A3：涨幅中位数
    pct = stock_df['pct_chg'].dropna()
    if len(pct) > 0:
        v['a3_pct_median'] = round(float(pct.median()), 3)

    # A4：涨/平/跌家数
    pct_valid = pct
    if len(pct_valid) > 0:
        v['a4_up'] = int((pct_valid > 0).sum())
        v['a4_flat'] = int((pct_valid == 0).sum())
        v['a4_down'] = int((pct_valid < 0).sum())

    return v


def compute_b_class(stock_df: pd.DataFrame, basic_df: pd.DataFrame,
                    rzrq_df: pd.DataFrame, sse: dict, szse: dict,
                    trade_date: str) -> dict:
    """计算层：B 类资金面指标（B1-B7）。"""
    v = {}

    # B1：换手率中位数
    if not basic_df.empty and 'turnover_rate' in basic_df.columns:
        turn = basic_df['turnover_rate'].dropna()
        if len(turn) > 0:
            v['b1_turnover_median'] = round(float(turn.median()), 3)

    # B3：全市场成交额（stock_daily_t.amount 单位千元 → 亿元）
    if not stock_df.empty and 'amount' in stock_df.columns:
        amt = stock_df['amount'].dropna()
        if len(amt) > 0:
            v['b3_amount_total'] = round(float(amt.sum()) / QIAN, 2)

    # 沪深流通/总市值（亿元）
    float_mv = (_float(sse.get('sse_float_mv')) or 0) + (_float(szse.get('szse_float_mv')) or 0)
    total_mv = (_float(sse.get('sse_total_mv')) or 0) + (_float(szse.get('szse_total_mv')) or 0)
    v['float_mv_total'] = float_mv
    v['total_mv_total'] = total_mv

    # B2：成交额 / 总市值
    if v.get('b3_amount_total') and total_mv > 0:
        v['b2_amount_to_mv'] = round(v['b3_amount_total'] / total_mv * 100, 3)

    # B4-B7：两融（rzrq_ye_t 单位元）
    if not rzrq_df.empty:
        latest = rzrq_df[rzrq_df['trade_date'] == trade_date]
        if latest.empty:
            latest = rzrq_df[rzrq_df['trade_date'] == rzrq_df['trade_date'].max()]
        if not latest.empty:
            fin = float(latest['rzye'].sum()) / YI     # 元→亿元
            sec = float(latest['rqye'].sum()) / YI
            v['b4_fin_balance'] = round(fin, 2)
            v['b6_sec_balance'] = round(sec, 2)

            # B5/B7：融资/融券 ÷ 流通市值
            if fin and float_mv > 0:
                v['b5_fin_to_mv'] = round(fin / float_mv * 100, 3)
            if sec and float_mv > 0:
                v['b7_sec_to_mv'] = round(sec / float_mv * 100, 4)

            # 趋势：5日/20日/60日变化率
            rzrq_daily = rzrq_df.groupby('trade_date').agg(
                rzye=('rzye', 'sum'), rqye=('rqye', 'sum')
            ).sort_index()
            fin_series = rzrq_daily['rzye'] / YI
            for n in [5, 20, 60]:
                if len(fin_series) > n:
                    chg = (float(fin_series.iloc[-1]) / float(fin_series.iloc[-1 - n]) - 1) * 100
                    v[f'fin_chg_{n}d'] = round(chg, 2)
            if len(fin_series) > 0:
                v['fin_peak'] = round(float(fin_series.max()), 2)
                v['fin_peak_date'] = str(fin_series.idxmax())

    return v


def compute_c_class(basic_df: pd.DataFrame, sse: dict, szse: dict) -> dict:
    """计算层：C 类估值指标（C1-C4）。

    四个板块 PE 全部优先采用交易所官方值（exchange_market_overview_t）；
    深主板/创业板官方值缺失时，才用个股 PE 中位数自算兜底。
    """
    v = {}

    # C1/C4：上交所官方 PE
    if sse.get('sse_pe_main') is not None:
        v['c1_pe_sh_main'] = sse['sse_pe_main']
    if sse.get('sse_pe_star') is not None:
        v['c4_pe_star'] = sse['sse_pe_star']

    # C2/C3：深交所官方 PE 优先
    if szse.get('szse_pe_main') is not None:
        v['c2_pe_sz_main'] = szse['szse_pe_main']
        v['c2_pe_official'] = True
    if szse.get('szse_pe_cyb') is not None:
        v['c3_pe_cyb'] = szse['szse_pe_cyb']
        v['c3_pe_official'] = True

    # 官方值缺失时：用个股 PE 中位数自算兜底（剔除亏损与极端值）
    if not basic_df.empty and 'pe' in basic_df.columns:
        s = basic_df.copy()
        s['pe'] = pd.to_numeric(s['pe'], errors='coerce')
        s = s[(s['pe'] > 0) & (s['pe'] < 500)]
        s['board'] = s['ts_code'].astype(str).map(board_of)
        for board, key in [('深主板', 'c2_pe_sz_main'), ('创业板', 'c3_pe_cyb')]:
            if key in v:
                continue   # 已有官方值，不再自算
            sub = s[s['board'] == board]['pe']
            if len(sub) > 10:
                v[key] = round(float(sub.median()), 2)
                v[f'{key}_n'] = int(len(sub))

    return v


# ===============================================================
# 指标组装：原始数值 → 标准化 Metric 列表
# ===============================================================
def build_metrics(v: dict) -> list:
    """将计算结果字典组装为 15 个标准化指标。"""
    M = []
    add = M.append

    def status(key):
        return 'ok' if v.get(key) is not None else 'missing'

    # ---- A 短线技术 ----
    add({'key': 'A1', 'group': '短线技术', 'name': '收盘价 > MA5 个股占比',
         'value': v.get('a1_above_ma5'), 'unit': '%',
         'source': 'stock_daily_t', 'status': status('a1_above_ma5'),
         'note': f"样本 {v.get('a1_sample', 0)} 只"})
    add({'key': 'A2', 'group': '短线技术', 'name': '收盘价 > MA30 个股占比',
         'value': v.get('a2_above_ma30'), 'unit': '%',
         'source': 'stock_daily_t', 'status': status('a2_above_ma30'),
         'note': f"样本 {v.get('a2_sample', 0)} 只"})
    add({'key': 'A3', 'group': '短线技术', 'name': '全市场涨幅中位数',
         'value': v.get('a3_pct_median'), 'unit': '%',
         'source': 'stock_daily_t', 'status': status('a3_pct_median'),
         'note': ''})
    if v.get('a4_up') is not None:
        tot = v['a4_up'] + v['a4_flat'] + v['a4_down']
        up_pct = round(v['a4_up'] / tot * 100, 1) if tot else None
        add({'key': 'A4', 'group': '短线技术', 'name': '涨/平/跌 家数比',
             'value': up_pct, 'unit': '%',
             'source': 'stock_daily_t', 'status': 'ok',
             'note': f"涨{v['a4_up']} / 平{v['a4_flat']} / 跌{v['a4_down']}"})
    else:
        add({'key': 'A4', 'group': '短线技术', 'name': '涨/平/跌 家数比',
             'value': None, 'unit': '%', 'source': '', 'status': 'missing', 'note': ''})

    # ---- B 资金面 ----
    add({'key': 'B1', 'group': '资金面', 'name': '全市场换手率中位数',
         'value': v.get('b1_turnover_median'), 'unit': '%',
         'source': 'stock_daily_basic_info_t', 'status': status('b1_turnover_median'),
         'note': ''})
    add({'key': 'B2', 'group': '资金面', 'name': '全市场成交额 / 总市值',
         'value': v.get('b2_amount_to_mv'), 'unit': '%',
         'source': '个股日线 ÷ exchange_market_overview_t', 'status': status('b2_amount_to_mv'),
         'note': ''})
    add({'key': 'B3', 'group': '资金面', 'name': '全市场成交额',
         'value': v.get('b3_amount_total'), 'unit': '亿元',
         'source': 'stock_daily_t', 'status': status('b3_amount_total'),
         'note': ''})
    add({'key': 'B4', 'group': '资金面', 'name': '融资余额',
         'value': v.get('b4_fin_balance'), 'unit': '亿元',
         'source': 'rzrq_ye_t(沪深合计)', 'status': status('b4_fin_balance'),
         'note': _trend_note(v)})
    add({'key': 'B5', 'group': '资金面', 'name': '融资余额 / 流通市值',
         'value': v.get('b5_fin_to_mv'), 'unit': '%',
         'source': 'rzrq_ye_t ÷ exchange_market_overview_t', 'status': status('b5_fin_to_mv'),
         'note': ''})
    add({'key': 'B6', 'group': '资金面', 'name': '融券余额',
         'value': v.get('b6_sec_balance'), 'unit': '亿元',
         'source': 'rzrq_ye_t(沪深合计)', 'status': status('b6_sec_balance'),
         'note': ''})
    add({'key': 'B7', 'group': '资金面', 'name': '融券余额 / 流通市值',
         'value': v.get('b7_sec_to_mv'), 'unit': '%',
         'source': 'rzrq_ye_t ÷ exchange_market_overview_t', 'status': status('b7_sec_to_mv'),
         'note': ''})

    # ---- C 估值（四板块 PE 均取自 exchange_market_overview_t 交易所官方值）----
    add({'key': 'C1', 'group': '估值', 'name': '上证主板平均市盈率',
         'value': v.get('c1_pe_sh_main'), 'unit': '倍',
         'source': 'exchange_market_overview_t(上交所官方)', 'status': status('c1_pe_sh_main'),
         'note': ''})
    add({'key': 'C2', 'group': '估值', 'name': '深证主板平均市盈率',
         'value': v.get('c2_pe_sz_main'), 'unit': '倍',
         'source': ('exchange_market_overview_t(深交所官方)' if v.get('c2_pe_official')
                    else 'stock_daily_basic_info_t(自算中位数)'),
         'status': status('c2_pe_sz_main'),
         'note': ('' if v.get('c2_pe_official')
                  else f"官方值缺失，自算兜底，样本 {v.get('c2_pe_sz_main_n', '-')} 只")})
    add({'key': 'C3', 'group': '估值', 'name': '创业板平均市盈率',
         'value': v.get('c3_pe_cyb'), 'unit': '倍',
         'source': ('exchange_market_overview_t(深交所官方)' if v.get('c3_pe_official')
                    else 'stock_daily_basic_info_t(自算中位数)'),
         'status': status('c3_pe_cyb'),
         'note': ('' if v.get('c3_pe_official')
                  else f"官方值缺失，自算兜底，样本 {v.get('c3_pe_cyb_n', '-')} 只")})
    add({'key': 'C4', 'group': '估值', 'name': '科创板平均市盈率',
         'value': v.get('c4_pe_star'), 'unit': '倍',
         'source': 'exchange_market_overview_t(上交所官方)', 'status': status('c4_pe_star'),
         'note': '口径含高PE与未盈利企业'})

    return M


def _trend_note(v: dict) -> str:
    """融资余额趋势备注。"""
    parts = []
    for n, lab in [(5, '5日'), (20, '20日'), (60, '60日')]:
        k = f'fin_chg_{n}d'
        if v.get(k) is not None:
            parts.append(f"{lab}{v[k]:+.1f}%")
    if v.get('fin_peak'):
        parts.append(f"历史峰值 {v['fin_peak']:,.0f}亿({v.get('fin_peak_date', '')})")
    return ' · '.join(parts)


# ===============================================================
# JSON 输出
# ===============================================================
def write_json(data: dict, path: str):
    """计算层结果 → JSON 文件，供 app.py /api/market_overview_metrics 读取。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, default=_json_default)
    return path


def _json_default(obj):
    """JSON 序列化兜底：Decimal → float。"""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ===============================================================
# 主流程
# ===============================================================
def main():
    print('=' * 80)
    print('大盘指标体系 15 指标计算任务')
    print('=' * 80)

    print('\n[采集层] 连接数据库...')
    conn = get_mysql_connection()
    if not conn:
        print('❌ 数据库连接失败')
        return 1
    print('✅ 数据库连接成功')

    # 建表（储存层）
    print('\n[储存层] 确保指标快照表存在...')
    ensure_metric_table(conn)

    # 确定目标日期
    trade_date = get_target_date(conn)
    print(f'\n[采集层] 目标交易日: {trade_date}')

    # 采集原始数据
    print('  读取个股日线...')
    stock_df = collect_stock_daily(conn, trade_date)
    print(f'  → {len(stock_df)} 条个股日线')

    print('  读取个股基本面...')
    basic_df = collect_stock_basic(conn, trade_date)
    print(f'  → {len(basic_df)} 条基本面数据')

    print('  读取两融数据...')
    rzrq_df = collect_rzrq(conn, trade_date)
    print(f'  → {len(rzrq_df)} 条两融记录')

    print('  读取上交所总貌（exchange_market_overview_t）...')
    sse = collect_sse_summary(conn, trade_date)
    if sse.get('sse_date') and sse['sse_date'] != trade_date:
        print(f'  ⚠️ 目标日无上交所数据，回退至 {sse["sse_date"]}')
    print(f'  → 主板PE={sse.get("sse_pe_main")}, 科创板PE={sse.get("sse_pe_star")}, '
          f'全市场总市值={sse.get("sse_total_mv")}亿, 流通市值={sse.get("sse_float_mv")}亿')

    print('  读取深交所总貌（exchange_market_overview_t）...')
    szse = collect_szse_summary(conn, trade_date)
    if szse.get('szse_date') and szse['szse_date'] != trade_date:
        print(f'  ⚠️ 目标日无深交所数据，回退至 {szse["szse_date"]}')
    print(f'  → 主板PE={szse.get("szse_pe_main")}, 创业板PE={szse.get("szse_pe_cyb")}, '
          f'全市场总市值={szse.get("szse_total_mv")}亿, 流通市值={szse.get("szse_float_mv")}亿')

    close_connection(conn)

    # 计算层：15 指标
    print('\n[计算层] 计算 15 指标...')
    v = {}
    v.update(compute_a_class(stock_df))
    v.update(compute_b_class(stock_df, basic_df, rzrq_df, sse, szse, trade_date))
    v.update(compute_c_class(basic_df, sse, szse))

    metrics = build_metrics(v)
    ok_count = sum(1 for m in metrics if m['status'] == 'ok')
    print(f'  → {ok_count}/15 指标计算成功')

    # 储存层：写入快照表
    print('\n[储存层] 写入指标快照表...')
    conn = get_mysql_connection()
    if conn:
        write_metrics_to_db(conn, trade_date, metrics)
        close_connection(conn)
        print(f'  → {len(metrics)} 条指标已写入 market_overview_metric_daily_t')

    # 输出 JSON
    print('\n[输出] 生成 JSON 数据文件...')
    payload = {
        'trade_date': trade_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ok_count': ok_count,
        'total_count': len(metrics),
        'metrics': metrics,
    }
    path = write_json(payload, OUTPUT_JSON)
    print(f'  → {path}')
    print(f'\n访问地址: http://127.0.0.1:5000/market-overview')
    print('\n' + '=' * 80)
    print('🎉 大盘指标体系计算完成！')
    print('=' * 80)
    return 0


if __name__ == '__main__':
    sys.exit(main())
