#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
定时任务运行监控：解析 cron_logs 编排日志，并将运行记录写入 task_run_log_t 表。

架构说明：
  - 本模块负责「日志解析」+「数据库写入」，不涉及 HTTP 请求/响应。
  - app.py 的 /api/cron_tasks 接口调用 parse_cron_run() 获取解析结果。
  - write_run_log() 将解析结果持久化到 task_run_log_t 表。
  - app.py 的 /api/cron_run 手动触发接口调用 get_allowed_scripts() 做白名单校验。

用法：
  # 从 app.py 导入
  from monitor_run_daily_tasks import parse_cron_run, write_run_log, get_allowed_scripts

  # 独立运行（解析并写入数据库）
  python monitor_run_daily_tasks.py
"""

import difflib
import glob
import os
import re
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 路径与正则常量
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRON_LOG_DIR = os.path.join(BASE_DIR, 'cron_logs')
MANUAL_LOG_DIR = os.path.join(BASE_DIR, 'manual_logs')
DAILY_SH_PATH = os.path.join(BASE_DIR, 'run_daily_stock_tasks.sh')

_CRON_TS_LINE = re.compile(r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s?(?P<body>.*)$')
_CRON_START = re.compile(r'^\[(?P<step>[^\]]+)\]\s?开始执行\s?(?P<script>[\w.]+\.py)\s?\.\.\.$')
_CRON_END = re.compile(r'^\[(?P<step>[^\]]+)\]\s?[✅❌]\s?(?P<script>[\w.]+\.py)\s?执行(?P<result>成功|失败)')


# ---------------------------------------------------------------------------
# 一、白名单：从 run_daily_stock_tasks.sh 提取允许执行的脚本
# ---------------------------------------------------------------------------

def get_allowed_scripts():
    """允许手动触发的任务脚本白名单：从 run_daily_stock_tasks.sh 非注释行提取。"""
    allowed = set()
    try:
        with open(DAILY_SH_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#'):
                    continue
                allowed.update(re.findall(r'([A-Za-z0-9_]+\.py)', line))
    except OSError:
        pass
    return allowed


# ---------------------------------------------------------------------------
# 二、日志解析：从 cron_logs/daily_stock_*.log 提取任务运行记录
# ---------------------------------------------------------------------------

def _match_detail_logs(scripts, run_id):
    """把每个任务脚本名匹配到 cron_logs 中对应的明细日志文件。"""
    try:
        pool = [f for f in os.listdir(CRON_LOG_DIR)
                if f.endswith('.log') and run_id in f and not f.startswith('daily_stock_')]
    except OSError:
        return {}

    mapping = {}

    def stem_of(fname):
        return fname.replace(run_id, '').replace('.log', '').rstrip('_')

    # 1) 精确匹配
    for script in scripts:
        stem = script[:-3]
        for cand in (f'{stem}_{run_id}.log', f'{stem}{run_id}.log'):
            if cand in pool:
                mapping[script] = cand
                pool.remove(cand)
                break

    # 2) 相似度兜底
    pairs = []
    for script in scripts:
        if script in mapping:
            continue
        stem = script[:-3]
        for fname in pool:
            score = difflib.SequenceMatcher(None, stem, stem_of(fname)).ratio()
            if score >= 0.6:
                pairs.append((score, script, fname))
    for _, script, fname in sorted(pairs, key=lambda x: -x[0]):
        if script not in mapping and fname in pool:
            mapping[script] = fname
            pool.remove(fname)
    return mapping


def parse_cron_run():
    """解析 cron_logs 中最近一次 daily_stock_*.log 编排日志。

    返回 None 表示 cron_logs 目录不存在；
    返回 {'has_run': False} 表示目录在但无运行日志。
    每次调用都重新读文件，便于页面轮询"运行中"的任务。
    """
    if not os.path.isdir(CRON_LOG_DIR):
        return None

    mains = sorted(glob.glob(os.path.join(CRON_LOG_DIR, 'daily_stock_*.log')))
    if not mains:
        return {'has_run': False}

    path = mains[-1]
    fname = os.path.basename(path)
    m = re.match(r'daily_stock_(\d{4})(\d{2})(\d{2})_(\d{6})\.log$', fname)
    run_id = m.group(0).replace('daily_stock_', '').replace('.log', '') if m else ''
    run_date = f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else ''
    run_time = f'{m.group(4)[0:2]}:{m.group(4)[2:4]}:{m.group(4)[4:6]}' if m else ''

    tasks = []
    pending = {}
    phase = ''
    batch_start = batch_end = None
    batch_failed = False

    with open(path, encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.rstrip('\n')
            mm = _CRON_TS_LINE.match(line)
            if not mm:
                continue
            ts, body = mm.group('ts'), mm.group('body').strip()

            if '第一批任务：数据更新' in body:
                phase = '数据更新'
                continue
            if '第一批任务完成，开始第二批任务' in body:
                phase = '选股分析与报告'
                continue
            if body == '开始执行每日股票分析任务':
                batch_start = ts
                continue
            if '所有任务执行完成' in body:
                batch_end = ts
                continue

            ms = _CRON_START.match(body)
            if ms:
                idx = len(tasks)
                tasks.append({
                    'step': ms.group('step'),
                    'name': ms.group('script'),
                    'phase': phase,
                    'status': 'running',
                    'start': ts,
                    'end': None,
                    'duration': None,
                })
                pending[(ms.group('step'), ms.group('script'))] = idx
                continue

            me = _CRON_END.match(body)
            if me:
                idx = pending.pop((me.group('step'), me.group('script')), None)
                if idx is None:
                    continue
                ok = me.group('result') == '成功'
                if not ok:
                    batch_failed = True
                start_dt = datetime.strptime(tasks[idx]['start'], '%Y-%m-%d %H:%M:%S')
                end_dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
                tasks[idx].update(
                    status='success' if ok else 'failed',
                    end=ts,
                    duration=round((end_dt - start_dt).total_seconds(), 1),
                )

    detail_logs = _match_detail_logs([t['name'] for t in tasks], run_id)
    for t in tasks:
        t['detail_log'] = detail_logs.get(t['name'])

    if batch_end:
        batch_status = 'failed' if batch_failed else 'success'
    elif batch_failed:
        batch_status = 'failed'
    elif batch_start:
        batch_status = 'running'
    else:
        batch_status = 'unknown'

    batch_duration = None
    if batch_start and batch_end:
        batch_duration = round((
            datetime.strptime(batch_end, '%Y-%m-%d %H:%M:%S')
            - datetime.strptime(batch_start, '%Y-%m-%d %H:%M:%S')
        ).total_seconds(), 1)

    return {
        'has_run': True,
        'run_id': run_id,
        'run_date': run_date,
        'run_time': run_time,
        'is_today': run_date == datetime.now().strftime('%Y-%m-%d'),
        'log_file': fname,
        'batch_start': batch_start,
        'batch_end': batch_end,
        'batch_duration': batch_duration,
        'batch_status': batch_status,
        'tasks': tasks,
        'summary': {
            'total': len(tasks),
            'success': sum(1 for t in tasks if t['status'] == 'success'),
            'failed': sum(1 for t in tasks if t['status'] == 'failed'),
            'running': sum(1 for t in tasks if t['status'] == 'running'),
        },
    }


# ---------------------------------------------------------------------------
# 三、数据库写入：将解析结果持久化到 task_run_log_t
# ---------------------------------------------------------------------------

def write_run_log(data=None):
    """将解析结果写入 task_run_log_t 表。

    参数：
      data: parse_cron_run() 的返回值。为 None 时自动调用 parse_cron_run()。

    写入策略：
      - 使用 INSERT ... ON DUPLICATE KEY UPDATE（唯一键 uk_run_script = run_id + script_name）
      - 运行中的任务也会写入（status=running），下次轮询时 UPDATE 为最终状态
    """
    if data is None:
        data = parse_cron_run()
    if not data or not data.get('has_run') or not data.get('tasks'):
        return 0

    from mysql_connection import get_mysql_connection, close_connection

    run_id = data['run_id']
    run_date = data['run_date']
    batch_status = data.get('batch_status')
    batch_start = data.get('batch_start')
    batch_end = data.get('batch_end')
    batch_duration = data.get('batch_duration')
    summary = data.get('summary', {})
    total = summary.get('total')
    success = summary.get('success')
    failed = summary.get('failed')

    sql = """
        INSERT INTO task_run_log_t
            (run_id, run_date, batch_phase, step, script_name, status,
             start_time, end_time, duration_sec, detail_log,
             batch_status, batch_start, batch_end, batch_duration_sec,
             total_tasks, success_tasks, failed_tasks)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status=VALUES(status), start_time=VALUES(start_time),
            end_time=VALUES(end_time), duration_sec=VALUES(duration_sec),
            detail_log=VALUES(detail_log), batch_status=VALUES(batch_status),
            batch_start=VALUES(batch_start), batch_end=VALUES(batch_end),
            batch_duration_sec=VALUES(batch_duration_sec),
            total_tasks=VALUES(total_tasks), success_tasks=VALUES(success_tasks),
            failed_tasks=VALUES(failed_tasks)
    """

    conn = get_mysql_connection()
    if not conn:
        return 0
    rows_written = 0
    try:
        with conn.cursor() as cursor:
            for t in data['tasks']:
                cursor.execute(sql, (
                    run_id, run_date,
                    t.get('phase'), t.get('step'), t['name'], t['status'],
                    t.get('start'), t.get('end'), t.get('duration'), t.get('detail_log'),
                    batch_status, batch_start, batch_end, batch_duration,
                    total, success, failed,
                ))
                rows_written += cursor.rowcount
        conn.commit()
    finally:
        close_connection(conn)
    return rows_written


def write_manual_run_log(script, status, start, end, duration, rc=None, log=None):
    """将手动执行的任务记录写入 task_run_log_t 表。

    参数：
      script:   脚本名（如 'update_stock_daily.py'）
      status:   'running' | 'success' | 'failed'
      start:    开始时间字符串 'YYYY-MM-DD HH:MM:SS'
      end:      结束时间字符串（可 None）
      duration: 耗时秒（可 None）
      rc:       退出码（可 None）
      log:      手动日志文件名（可 None）

    使用 run_id='manual_<YYYYMMDD>_<script_stem>' 区分定时批次与手动执行。
    """
    from mysql_connection import get_mysql_connection, close_connection

    now = datetime.now()
    run_id = 'manual_' + now.strftime('%Y%m%d') + '_' + script[:-3]
    run_date = now.strftime('%Y-%m-%d')

    sql = """
        INSERT INTO task_run_log_t
            (run_id, run_date, batch_phase, step, script_name, status,
             start_time, end_time, duration_sec, detail_log,
             batch_status, batch_start, batch_end, batch_duration_sec,
             total_tasks, success_tasks, failed_tasks)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status=VALUES(status), start_time=VALUES(start_time),
            end_time=VALUES(end_time), duration_sec=VALUES(duration_sec),
            detail_log=VALUES(detail_log), batch_status=VALUES(batch_status),
            batch_start=VALUES(batch_start), batch_end=VALUES(batch_end),
            batch_duration_sec=VALUES(batch_duration_sec),
            total_tasks=VALUES(total_tasks), success_tasks=VALUES(success_tasks),
            failed_tasks=VALUES(failed_tasks)
    """
    batch_status = 'success' if status == 'success' else ('failed' if status == 'failed' else 'running')
    conn = get_mysql_connection()
    if not conn:
        return 0
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                run_id, run_date,
                '手动执行', '-', script, status,
                start, end, duration, log,
                batch_status, start, end, duration,
                1,
                1 if status == 'success' else 0,
                1 if status == 'failed' else 0,
            ))
            conn.commit()
            return cursor.rowcount
    except Exception:
        return 0
    finally:
        close_connection(conn)


# ---------------------------------------------------------------------------
# 四、独立运行入口
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("📋 定时任务运行监控 → 写入 task_run_log_t")
    print("=" * 60)

    result = parse_cron_run()
    if not result:
        print("❌ cron_logs 目录不存在")
        sys.exit(1)
    if not result.get('has_run'):
        print("❌ 未找到运行日志（daily_stock_*.log）")
        sys.exit(1)

    print(f"📅 运行编号: {result['run_id']}")
    print(f"📊 批次状态: {result['batch_status']}")
    print(f"📝 任务数: {result['summary']['total']}"
          f"（成功 {result['summary']['success']}, 失败 {result['summary']['failed']}）")

    n = write_run_log(result)
    print(f"✅ 已写入 task_run_log_t: {n} 条记录")
