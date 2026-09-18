#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
手动任务数据查询模块（数据查询层）

只负责「从 task_run_log_t 查询选股策略手动执行记录」，不涉及 HTTP 请求/响应。
app.py 的 /api/manual_tasks 接口调用本模块。

数据来源：策略选股结果页（条件选股）点击【运行策略】后，
app.py /api/strategies_run 后台执行 select_*.py / find_similar_*.py，
并通过 monitor_run_daily_tasks.write_manual_run_log(source='strategy') 写入
task_run_log_t（每次提交独立 run_id=strategy_YYYYMMDD_HHMMSS_<script>）。

提供：
  - query_strategy_tasks(limit=200)：最近的选股策略手动任务列表
  - query_strategy_today_summary()：今日选股策略手动任务 KPI 统计
  - resolve_strategy_titles(scripts)：脚本名 → 策略中文名（解析脚本 docstring 首行）
"""

import ast
import glob
import os
import re
from datetime import datetime

from module_mysql_connection import get_mysql_connection, close_connection

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# docstring 首行标题前缀（如「选股策略: 高换手率选股策略」→ 高换手率选股策略）
_TITLE_PREFIX = re.compile(r'^(?:选股策略|策略名称)\s*[:：]\s*')

_TITLE_CACHE = None


def _dt_str(v):
    """datetime → 'YYYY-MM-DD HH:MM:SS' 字符串，None 原样返回。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    return str(v)


def _load_strategy_titles():
    """扫描 select_*.py / find_similar_*.py，建立 脚本文件名 → 策略中文名 映射。

    优先取 STRATEGY_NAME 常量；其次取 record_selected_stocks 首参字面量；
    最后取模块 docstring 首行（去掉「选股策略:」等前缀）。
    """
    global _TITLE_CACHE
    paths = sorted(glob.glob(os.path.join(BASE_DIR, 'select_*.py'))) \
        + sorted(glob.glob(os.path.join(BASE_DIR, 'find_similar_*.py')))
    sig = tuple((p, os.path.getmtime(p)) for p in paths)
    if _TITLE_CACHE is not None and _TITLE_CACHE[0] == sig:
        return _TITLE_CACHE[1]

    mapping = {}
    for path in paths:
        name = os.path.basename(path)
        try:
            with open(path, encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            mapping[name] = name
            continue

        title = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Name) and target.id == 'STRATEGY_NAME'
                            and isinstance(node.value, ast.Constant)
                            and isinstance(node.value.value, str)):
                        title = node.value.value
            if (title is None and isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == 'record_selected_stocks'
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                title = node.args[0].value

        if not title:
            doc = ast.get_docstring(tree) or ''
            for line in doc.splitlines():
                line = line.strip()
                if line:
                    title = _TITLE_PREFIX.sub('', line).strip()
                    break
        mapping[name] = title or name

    _TITLE_CACHE = (sig, mapping)
    return mapping


def resolve_strategy_titles(scripts):
    """批量解析脚本名 → 策略中文名；未识别时返回脚本名本身。"""
    titles = _load_strategy_titles()
    return {s: titles.get(s, s) for s in scripts}


def query_strategy_tasks(limit=200):
    """查询最近的选股策略手动执行记录（按开始时间倒序）。

    返回 list[dict]，每项含：
      id / run_id / script / task_name / strategy_name / biz_date /
      status / start / end / duration / log
    """
    conn = get_mysql_connection()
    if not conn:
        return None

    sql = """
        SELECT id, run_id, script_name, task_name, biz_date, status,
               start_time, end_time, duration_sec, detail_log
        FROM task_run_log_t
        WHERE source = 'strategy'
        ORDER BY start_time DESC, id DESC
        LIMIT %s
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (int(limit),))
            rows = cursor.fetchall()
    except Exception as e:
        print(f"❌ 查询选股策略手动任务失败: {e}")
        return []
    finally:
        close_connection(conn)

    titles = resolve_strategy_titles({r['script_name'] for r in rows})
    result = []
    for r in rows:
        result.append({
            'id': r['id'],
            'run_id': r['run_id'],
            'script': r['script_name'],
            'task_name': r.get('task_name'),
            'strategy_name': titles.get(r['script_name'], r['script_name']),
            'biz_date': r.get('biz_date'),
            'status': r['status'],
            'start': _dt_str(r['start_time']),
            'end': _dt_str(r['end_time']),
            'duration': float(r['duration_sec']) if r['duration_sec'] is not None else None,
            'log': r.get('detail_log'),
        })
    return result


def query_strategy_today_summary():
    """今日选股策略手动任务 KPI 统计。

    返回 {'total', 'success', 'failed', 'running'}；连接失败返回 None。
    """
    conn = get_mysql_connection()
    if not conn:
        return None
    sql = """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running
        FROM task_run_log_t
        WHERE source = 'strategy' AND run_date = CURDATE()
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            r = cursor.fetchone()
        return {
            'total': int(r['total'] or 0),
            'success': int(r['success'] or 0),
            'failed': int(r['failed'] or 0),
            'running': int(r['running'] or 0),
        }
    except Exception as e:
        print(f"❌ 查询选股策略手动任务统计失败: {e}")
        return {'total': 0, 'success': 0, 'failed': 0, 'running': 0}
    finally:
        close_connection(conn)
