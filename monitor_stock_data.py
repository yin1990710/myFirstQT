#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
监控 stock_daily_t 表中数据的更新情况（数据计算层，不含 UI）。
架构：本脚本只负责查询数据库 → 计算 → 输出 JSON，前端页面通过 API 读取 JSON 动态渲染。

5 张监控表：
1. 最近10个交易日，每日 ts_code 数量
2. 最近10个交易日，每日 turning_point 分布
3. 最近10个交易日，每日 ma5>0 和 ma30>0 的记录数
4. 最近10个交易日，qfq_adj_factor>0 的记录数
5. 最近10个交易日，stock_daily_basic_info_t 每日记录数

用法：
  python monitor_stock_data.py                          # 输出到 pages/data_monitor.json
  python monitor_stock_data.py --json out.json          # 指定输出路径
  python monitor_stock_data.py --stdout                 # 输出到终端（供 API 调用）
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_recent_dates(conn, days=10):
    """获取最近N个交易日"""
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
    """获取指定日期范围内的交易日列表（含首尾）"""
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


def get_ts_code_count(conn, dates):
    """每日 ts_code 数量"""
    results = []
    for d in dates:
        sql = """
            SELECT COUNT(DISTINCT ts_code) AS cnt
            FROM stock_daily_t
            WHERE trade_date = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            row = cursor.fetchone()
        results.append(row['cnt'] if row else 0)
    return results


def get_turning_point_distribution(conn, dates):
    """每日 turning_point 分布"""
    all_tags = []
    data = {}
    for d in dates:
        sql = """
            SELECT turning_point, COUNT(*) AS cnt
            FROM stock_daily_t
            WHERE trade_date = %s
            GROUP BY turning_point
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            rows = cursor.fetchall()
        dist = {}
        for row in rows:
            tag = row['turning_point'] if row['turning_point'] else '(空)'
            dist[tag] = row['cnt']
            if tag not in all_tags:
                all_tags.append(tag)
        data[d] = dist

    all_tags.sort()
    return all_tags, data


def get_ma_count(conn, dates):
    """每日 ma5>0 和 ma30>0 的记录数"""
    ma5_counts = []
    ma30_counts = []
    for d in dates:
        sql = """
            SELECT
                SUM(CASE WHEN ma5 IS NOT NULL AND ma5 > 0 THEN 1 ELSE 0 END) AS ma5_cnt,
                SUM(CASE WHEN ma30 IS NOT NULL AND ma30 > 0 THEN 1 ELSE 0 END) AS ma30_cnt
            FROM stock_daily_t
            WHERE trade_date = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            row = cursor.fetchone()
        ma5_counts.append(row['ma5_cnt'] if row and row['ma5_cnt'] else 0)
        ma30_counts.append(row['ma30_cnt'] if row and row['ma30_cnt'] else 0)
    return ma5_counts, ma30_counts


def get_qfq_factor_count(conn, dates):
    """每日 qfq_adj_factor>0 的记录数"""
    qfq_counts = []
    for d in dates:
        sql = """
            SELECT
                SUM(CASE WHEN qfq_adj_factor IS NOT NULL AND qfq_adj_factor > 0 THEN 1 ELSE 0 END) AS qfq_cnt
            FROM stock_daily_t
            WHERE trade_date = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            row = cursor.fetchone()
        qfq_counts.append(row['qfq_cnt'] if row and row['qfq_cnt'] else 0)
    return qfq_counts


def get_basic_info_count(conn, dates):
    """stock_daily_basic_info_t 每日记录数"""
    counts = []
    for d in dates:
        sql = """
            SELECT COUNT(*) AS cnt
            FROM stock_daily_basic_info_t
            WHERE trade_date = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, (d,))
            row = cursor.fetchone()
        counts.append(row['cnt'] if row and row['cnt'] else 0)
    return counts


def get_task_run_history(conn, dates):
    """查询 task_run_log_t 表，返回每个交易日的任务运行汇总。

    返回 dict: {date: {total, success, failed, running, batch_status, batch_duration}}
    """
    if not dates:
        return {}
    placeholders = ','.join(['%s'] * len(dates))
    sql = f"""
        SELECT run_date,
               MAX(batch_status)   AS batch_status,
               MAX(batch_duration_sec) AS batch_duration,
               MAX(total_tasks)    AS total,
               MAX(success_tasks)  AS success,
               MAX(failed_tasks)   AS failed
        FROM task_run_log_t
        WHERE run_date IN ({placeholders})
        GROUP BY run_date
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, dates)
        rows = cursor.fetchall()
    result = {}
    for row in rows:
        d = str(row['run_date']).replace('-', '')
        result[d] = {
            'total': row['total'] or 0,
            'success': row['success'] or 0,
            'failed': row['failed'] or 0,
            'batch_status': row['batch_status'] or '-',
            'batch_duration': float(row['batch_duration']) if row['batch_duration'] else None,
        }
    return result


def collect_data(days=10, start_date=None, end_date=None):
    """执行全部查询，返回结构化 dict（可序列化为 JSON）。

    参数：
      days: 最近 N 个交易日（当 start_date/end_date 未指定时使用）
      start_date: 起始日期 YYYYMMDD（可选）
      end_date: 结束日期 YYYYMMDD（可选）

    返回结构：
      {
        "generated_at": "2026-09-15 10:30:00",
        "days": 10,
        "dates": ["20260912", ...],
        "date_range": {"start": "20260901", "end": "20260914"} 或 null,
        "tables": { ... }
      }
    """
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")

    try:
        if start_date and end_date:
            dates = get_dates_in_range(conn, start_date, end_date)
            date_range = {"start": start_date, "end": end_date}
        else:
            dates = get_recent_dates(conn, days=days)
            date_range = None

        if not dates:
            return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "days": days, "dates": [], "date_range": date_range, "tables": {}}

        # 表格1
        ts_counts = get_ts_code_count(conn, dates)
        # 表格2
        tp_tags, tp_data = get_turning_point_distribution(conn, dates)
        # 表格3
        ma5_counts, ma30_counts = get_ma_count(conn, dates)
        # 表格4
        qfq_counts = get_qfq_factor_count(conn, dates)
        # 表格5
        basic_counts = get_basic_info_count(conn, dates)
        # 表格6：任务运行监控
        task_history = get_task_run_history(conn, dates)

        # 构建 JSON 结构
        tp_rows = []
        for tag in tp_tags:
            row = [tag]
            for d in dates:
                row.append(tp_data.get(d, {}).get(tag, 0))
            tp_rows.append(row)

        return {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "days": days,
            "dates": dates,
            "date_range": date_range,
            "tables": {
                "ts_code": {
                    "title": "每日 ts_code 数量",
                    "rows": [
                        ["交易日期"] + dates,
                        ["ts_code数量"] + ts_counts,
                    ],
                },
                "turning_point": {
                    "title": "每日 turning_point 分布",
                    "tags": tp_tags,
                    "rows": tp_rows,
                },
                "ma": {
                    "title": "每日 ma5>0 和 ma30>0 记录数",
                    "rows": [
                        ["交易日期"] + dates,
                        ["ma5>0记录数"] + ma5_counts,
                        ["ma30>0记录数"] + ma30_counts,
                    ],
                },
                "qfq": {
                    "title": "每日 qfq_adj_factor>0 记录数",
                    "rows": [
                        ["交易日期"] + dates,
                        ["qfq_adj_factor>0记录数"] + qfq_counts,
                    ],
                },
                "basic_info": {
                    "title": "stock_daily_basic_info_t 每日记录数",
                    "rows": [
                        ["交易日期"] + dates,
                        ["basic_info记录数"] + basic_counts,
                    ],
                },
                "task_runs": {
                    "title": "每日任务运行监控",
                    "rows": [
                        ["交易日期"] + dates,
                        ["批次状态"] + [task_history.get(d, {}).get('batch_status', '-') for d in dates],
                        ["任务总数"] + [task_history.get(d, {}).get('total', 0) for d in dates],
                        ["成功"] + [task_history.get(d, {}).get('success', 0) for d in dates],
                        ["失败"] + [task_history.get(d, {}).get('failed', 0) for d in dates],
                        ["批次耗时(秒)"] + [task_history.get(d, {}).get('batch_duration', 0) for d in dates],
                    ],
                },
            },
        }
    finally:
        close_connection(conn)


def main():
    parser = argparse.ArgumentParser(description="stock_daily_t 数据完整性监控（输出 JSON）")
    parser.add_argument("--json", default="pages/data_monitor.json",
                        help="JSON 输出路径（默认 pages/data_monitor.json）")
    parser.add_argument("--stdout", action="store_true", help="输出到终端而非文件")
    parser.add_argument("--days", type=int, default=10, help="最近 N 个交易日（默认 10）")
    args = parser.parse_args()

    print("=" * 60)
    print("📊 stock_daily_t 数据完整性监控")
    print("=" * 60)

    data = collect_data(days=args.days)

    if args.stdout:
        print(json.dumps(data, ensure_ascii=False))
    else:
        out_dir = os.path.dirname(args.json)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ JSON 已保存: {args.json}")
        print(f"� 交易日: {data['dates']}")
        for k, t in data.get("tables", {}).items():
            print(f"  - {t['title']}")

    print("\n🎉 监控完成！")


if __name__ == "__main__":
    main()
