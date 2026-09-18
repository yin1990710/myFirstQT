#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
数据完整性监控（数据计算层，不含 UI）。
架构：本脚本只负责查询数据库 → 计算 → 输出 JSON，前端页面通过 API 读取 JSON 动态渲染。

输出两张监控表：
1. stock_daily_t 分日期字段完整性：每个交易日 ts_code / ma5 / ma30 /
   qfq_adj_factor / short_strength_score（短线强弱得分）不为空的数量
2. 数据表监控：数据库全部表（含 stock_daily_t）的表名（英文）、表注释（中文）、
   截止基准日总数据量、基准日当日新增量；
   基准日 = 查询范围最后一个交易日（未指定范围时取 stock_daily_t 最新交易日）

用法：
  python monitor_stock_data.py                          # 输出到 pages/data_monitor.json
  python monitor_stock_data.py --json out.json          # 指定输出路径
  python monitor_stock_data.py --stdout                 # 输出到终端（供 API 调用）
"""

import argparse
import json
import os
import sys
from datetime import datetime, date
from decimal import Decimal

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection

# 表2 数据表监控：默认纳入数据库全部表（含 stock_daily_t，与其字段完整性表1互补）
EXCLUDE_TABLES = set()

# 各表「当日新增」统计字段及类型（未列出的表按 trade_date(update_time) 自动发现）：
#   ymd  → 字段为 YYYYMMDD 字符串
#   date → 字段为 DATE 类型（YYYY-MM-DD）
#   dt   → 字段为 DATETIME/TIMESTAMP，按 DATE(字段) 比较
TABLE_DATE_COL_OVERRIDES = {
    'task_run_log_t':    ('run_date', 'date'),
    'backtest_task_t':   ('start_time', 'dt'),
    'my_stock_t':        ('selected_date', 'ymd'),
    'etf_basic_t':       ('update_time', 'dt'),
    'stock_info_t':      ('update_time', 'dt'),
    'user_login_t':      ('login_time', 'dt'),
}

# information_schema 中表注释为空时的兜底中文名
TABLE_COMMENT_FALLBACK = {
    'rzrq_ye_t': '融资融券余额表',
    'stock_index_daily_t': '股指指数日线数据表',
    'stock_index_future_daily_t': '股指期货日线数据表',
    'stock_info_t': '股票基础信息表',
}


def _json_default(o):
    """JSON 序列化兜底：MySQL DECIMAL/datetime/date 统一转换，避免 TypeError。"""
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (datetime, date)):
        return o.strftime('%Y-%m-%d %H:%M:%S')
    raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')


def get_recent_dates(conn, days=10):
    """获取最近N个交易日（升序）"""
    sql = """
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        ORDER BY trade_date DESC
        LIMIT %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (days,))
        rows = cursor.fetchall()
    dates = [row['trade_date'] for row in rows]
    dates.reverse()
    return dates


def get_dates_in_range(conn, start_date, end_date):
    """获取指定日期范围内的交易日列表（含首尾，升序）"""
    sql = """
        SELECT DISTINCT trade_date
        FROM stock_daily_t
        WHERE trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date ASC
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, (start_date, end_date))
        rows = cursor.fetchall()
    return [row['trade_date'] for row in rows]


def get_stock_daily_field_counts(conn, dates):
    """表1：stock_daily_t 分日期各字段不为空的数量（一次 GROUP BY）。

    返回 {trade_date: {code, ma5, ma30, qfq, sss}}。
    """
    if not dates:
        return {}
    placeholders = ','.join(['%s'] * len(dates))
    sql = f"""
        SELECT trade_date,
               COUNT(ts_code) AS code_cnt,
               SUM(CASE WHEN ma5 IS NOT NULL THEN 1 ELSE 0 END) AS ma5_cnt,
               SUM(CASE WHEN ma30 IS NOT NULL THEN 1 ELSE 0 END) AS ma30_cnt,
               SUM(CASE WHEN qfq_adj_factor IS NOT NULL THEN 1 ELSE 0 END) AS qfq_cnt,
               SUM(CASE WHEN short_strength_score IS NOT NULL THEN 1 ELSE 0 END) AS sss_cnt
        FROM stock_daily_t
        WHERE trade_date IN ({placeholders})
        GROUP BY trade_date
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, dates)
        rows = cursor.fetchall()
    return {
        r['trade_date']: {
            'code': int(r['code_cnt'] or 0),
            'ma5': int(r['ma5_cnt'] or 0),
            'ma30': int(r['ma30_cnt'] or 0),
            'qfq': int(r['qfq_cnt'] or 0),
            'sss': int(r['sss_cnt'] or 0),
        }
        for r in rows
    }


def _discover_date_columns(conn, tables):
    """发现各表可用的日期字段（用于当日新增统计）。

    优先 TABLE_DATE_COL_OVERRIDES；否则取 trade_date（varchar YYYYMMDD）；
    再退化为 update_time（TIMESTAMP/DATETIME）；都没有则返回 None（不统计当日新增）。
    """
    if not tables:
        return {}
    placeholders = ','.join(['%s'] * len(tables))
    sql = f"""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME IN ({placeholders})
          AND COLUMN_NAME IN ('trade_date', 'update_time')
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, tables)
        rows = cursor.fetchall()
    avail = {}
    for r in rows:
        avail.setdefault(r['TABLE_NAME'], {})[r['COLUMN_NAME']] = r['DATA_TYPE']

    result = {}
    for t in tables:
        if t in TABLE_DATE_COL_OVERRIDES:
            result[t] = TABLE_DATE_COL_OVERRIDES[t]
        elif 'trade_date' in avail.get(t, {}):
            result[t] = ('trade_date', 'ymd')
        elif 'update_time' in avail.get(t, {}):
            result[t] = ('update_time', 'dt')
        else:
            result[t] = None
    return result


def _new_count_where(col, kind, ref_date):
    """生成「当日新增」WHERE 片段与参数（标识符来自内部白名单，非用户输入）。"""
    if kind == 'ymd':
        return f"`{col}` = %s", ref_date
    if kind == 'date':
        return f"`{col}` = %s", f"{ref_date[0:4]}-{ref_date[4:6]}-{ref_date[6:8]}"
    # dt：DATETIME/TIMESTAMP
    return f"DATE(`{col}`) = %s", f"{ref_date[0:4]}-{ref_date[4:6]}-{ref_date[6:8]}"


def get_table_stats(conn, ref_date):
    """表2：数据库全部表的 表名/注释/截止基准日总量/基准日当日新增量。

    返回 list[dict]，按表名升序。
    """
    sql = """
        SELECT TABLE_NAME, TABLE_COMMENT
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
        tables_rows = cursor.fetchall()

    tables = [r['TABLE_NAME'] for r in tables_rows if r['TABLE_NAME'] not in EXCLUDE_TABLES]
    comments = {r['TABLE_NAME']: (r['TABLE_COMMENT'] or '').strip() for r in tables_rows}
    date_cols = _discover_date_columns(conn, tables)

    result = []
    with conn.cursor() as cursor:
        for t in tables:
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM `{t}`")
            total = int(cursor.fetchone()['cnt'] or 0)

            new_cnt = None
            dc = date_cols.get(t)
            if dc:
                col, kind = dc
                where_sql, param = _new_count_where(col, kind, ref_date)
                try:
                    cursor.execute(
                        f"SELECT COUNT(*) AS cnt FROM `{t}` WHERE {where_sql}", (param,))
                    new_cnt = int(cursor.fetchone()['cnt'] or 0)
                except Exception:
                    new_cnt = None

            comment = comments.get(t) or TABLE_COMMENT_FALLBACK.get(t) or '-'
            result.append({
                'table': t,
                'comment': comment,
                'total': total,
                'new': new_cnt,
                'date_col': dc[0] if dc else None,
            })
    return result


def collect_data(days=10, start_date=None, end_date=None):
    """执行全部查询，返回结构化 dict（可序列化为 JSON）。

    参数：
      days: 最近 N 个交易日（当 start_date/end_date 未指定时使用）
      start_date: 起始日期 YYYYMMDD（可选）
      end_date: 结束日期 YYYYMMDD（可选，仅传起始日时可取到最新交易日）

    返回结构：
      {
        "generated_at": "...", "days": 10, "ref_date": "20260917",
        "dates": ["20260912", ...], "date_range": {...} 或 null,
        "stock_daily": {"title", "rows": [[表头], [每日...]]},
        "other_tables": {"title", "ref_label", "rows": [[表名, 注释, 总量, 当日新增]]}
      }
    """
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")

    try:
        if start_date and end_date:
            dates = get_dates_in_range(conn, start_date, end_date)
            date_range = {"start": start_date, "end": end_date}
        elif start_date:
            dates = get_dates_in_range(conn, start_date,
                                       datetime.now().strftime('%Y%m%d'))
            date_range = {"start": start_date, "end": dates[-1] if dates else start_date}
        else:
            dates = get_recent_dates(conn, days=days)
            date_range = None

        if not dates:
            return {"generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    "days": days, "dates": [], "date_range": date_range,
                    "ref_date": None, "stock_daily": None, "other_tables": None}

        # 表1：stock_daily_t 分日期字段非空计数（默认按交易日期倒序，最新在前）
        counts = get_stock_daily_field_counts(conn, dates)
        daily_rows = [
            ['交易日期', '股票代码非空', 'ma5非空', 'ma30非空',
             'qfq_adj_factor非空', '短线强弱得分非空']
        ]
        for d in reversed(dates):
            c = counts.get(d, {'code': 0, 'ma5': 0, 'ma30': 0, 'qfq': 0, 'sss': 0})
            daily_rows.append([d, c['code'], c['ma5'], c['ma30'], c['qfq'], c['sss']])

        # 表2：数据表监控（基准日 = 范围内最后一个交易日）
        ref_date = dates[-1]
        others = get_table_stats(conn, ref_date)
        other_rows = [['表名（英文）', '表注释', f'截止{ref_date}总数据量',
                       f'{ref_date}当日新增量']]
        for o in others:
            other_rows.append([o['table'], o['comment'], o['total'], o['new']])

        return {
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "days": days,
            "dates": dates,
            "date_range": date_range,
            "ref_date": ref_date,
            "stock_daily": {
                "title": "stock_daily_t 分日期字段完整性（各字段不为空的记录数）",
                "rows": daily_rows,
            },
            "other_tables": {
                "title": "数据表监控",
                "ref_label": ref_date,
                "rows": other_rows,
            },
        }
    finally:
        close_connection(conn)


def main():
    parser = argparse.ArgumentParser(description="数据完整性监控（输出 JSON）")
    parser.add_argument("--json", default="pages/data_monitor.json",
                        help="JSON 输出路径（默认 pages/data_monitor.json）")
    parser.add_argument("--stdout", action="store_true", help="输出到终端而非文件")
    parser.add_argument("--days", type=int, default=10, help="最近 N 个交易日（默认 10）")
    args = parser.parse_args()

    print("=" * 60)
    print("📊 数据完整性监控")
    print("=" * 60)

    data = collect_data(days=args.days)

    if args.stdout:
        print(json.dumps(data, ensure_ascii=False, default=_json_default))
    else:
        out_dir = os.path.dirname(args.json)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
        print(f"✅ JSON 已保存: {args.json}")
        print(f"📅 交易日: {data['dates']}")
        if data.get('stock_daily'):
            print(f"  - {data['stock_daily']['title']}")
        if data.get('other_tables'):
            print(f"  - {data['other_tables']['title']}")

    print("\n🎉 监控完成！")


if __name__ == "__main__":
    main()
