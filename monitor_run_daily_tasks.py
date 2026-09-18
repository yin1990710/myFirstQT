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
# 新格式：[步骤1/20] 开始执行 指数日交易数据 (update_stock_index_daily.py)...
_CRON_START = re.compile(
    r'^\[(?P<step>[^\]]+)\]\s?开始执行\s?(?P<task_name>[^\(]+?)\s?\((?P<script>[\w.]+\.py)\)\s?\.\.\.$'
)
# [步骤1/20] ✅ 指数日交易数据 (update_stock_index_daily.py) 执行成功
_CRON_END = re.compile(
    r'^\[(?P<step>[^\]]+)\]\s?[✅❌]\s?(?P<task_name>[^\(]+?)\s?\((?P<script>[\w.]+\.py)\)\s?执行(?P<result>成功|失败)'
)
# 批次横幅：========== 第一批任务：数据更新 ==========
# 兼容 log "..." 源码行（结尾带引号）与日志正文（结尾为 = 号）两种形式
_BATCH_MARK = re.compile(
    r'第(?P<num>[一二三四五六七八九十\d]+)批任务[：:]\s*(?P<name>[^=\n]+?)\s*(?:=+\s*)?"?\s*$'
)
# 批次切换：========== 第一批任务完成，开始第二批任务 ==========
_BATCH_TRANSITION = re.compile(
    r'第[一二三四五六七八九十\d]+批任务完成，\s*开始第(?P<next>[一二三四五六七八九十\d]+)批任务'
)


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


# 从 shell 脚本注释中提取的 脚本名 → 任务名 映射缓存
_task_name_cache = None


def _load_task_names():
    """从 run_daily_stock_tasks.sh 注释行提取 脚本名 → 任务名 的映射。

    注释格式：# 步骤N：任务名称
    后续非注释行包含脚本名：... script_name.py ...
    """
    global _task_name_cache
    if _task_name_cache is not None:
        return _task_name_cache

    _task_name_cache = {}
    current_name = None
    try:
        with open(DAILY_SH_PATH, encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                # 注释行：# 步骤N：任务名称
                m = re.match(r'^#\s*步骤\d+[：:]\s*(.+)$', stripped)
                if m:
                    current_name = m.group(1).strip()
                    continue
                # 非注释行：查找脚本名
                if current_name:
                    scripts = re.findall(r'([A-Za-z0-9_]+\.py)', stripped)
                    for s in scripts:
                        if s not in _task_name_cache:
                            _task_name_cache[s] = current_name
    except OSError:
        pass
    return _task_name_cache


def _lookup_task_name(script):
    """根据脚本名查任务名，未找到时返回 None。"""
    return _load_task_names().get(script)


# 从 shell 脚本中提取的 批次序号 → 批次名称 映射缓存
_batch_name_cache = None


def _load_batch_names():
    """从 run_daily_stock_tasks.sh 提取 批次序号 → 批次名称 的映射。

    优先取脚本中 log 行的批次横幅（实际运行时输出到日志，如
    log "========== 第一批任务：数据更新 =========="），
    注释行（如 `# 第一批任务：数据更新（按顺序执行）`）仅作补充，
    并去掉名称尾部的（...）说明后缀；log 行的优先级高于注释行。
    """
    global _batch_name_cache
    if _batch_name_cache is not None:
        return _batch_name_cache

    _batch_name_cache = {}
    try:
        with open(DAILY_SH_PATH, encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                m = _BATCH_MARK.search(stripped)
                if not m:
                    continue
                num = m.group('num')
                if stripped.startswith('#'):
                    name = re.sub(r'（[^）]*）\s*$', '', m.group('name').strip()).strip()
                    _batch_name_cache.setdefault(num, name)
                else:
                    _batch_name_cache[num] = m.group('name').strip()
    except OSError:
        pass
    return _batch_name_cache


def _batch_name(num):
    """根据批次序号（如 '二'）查批次名称，未找到时返回 None。"""
    return _load_batch_names().get(num)


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

            # 批次横幅：========== 第N批任务：名称 ==========（名称直接取自日志）
            mb = _BATCH_MARK.search(body)
            if mb:
                phase = mb.group('name').strip()
                continue
            # 批次切换行不带名称（第N批任务完成，开始第M批任务），名称从 shell 脚本映射取
            mt = _BATCH_TRANSITION.search(body)
            if mt:
                phase = _batch_name(mt.group('next')) or f'第{mt.group("next")}批任务'
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
                    'task_name': ms.group('task_name').strip(),
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

    from module_mysql_connection import get_mysql_connection, close_connection

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
            (source, run_id, run_date, batch_phase, step, script_name, task_name, status,
             start_time, end_time, duration_sec, detail_log,
             batch_status, batch_start, batch_end, batch_duration_sec,
             total_tasks, success_tasks, failed_tasks)
        VALUES ('cron', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            source='cron',
            task_name=VALUES(task_name), batch_phase=VALUES(batch_phase),
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
                    t.get('phase'), t.get('step'), t['name'], t.get('task_name'),
                    t['status'],
                    t.get('start'), t.get('end'), t.get('duration'), t.get('detail_log'),
                    batch_status, batch_start, batch_end, batch_duration,
                    total, success, failed,
                ))
                rows_written += cursor.rowcount
        conn.commit()
    finally:
        close_connection(conn)
    return rows_written


def write_manual_run_log(script, status, start, end, duration, rc=None, log=None,
                         task_name=None, source='manual', biz_date=None, run_id=None):
    """将手动执行的任务记录写入 task_run_log_t 表。

    参数：
      script:    脚本名（如 'update_stock_daily.py'）
      status:    'running' | 'success' | 'failed'
      start:     开始时间字符串 'YYYY-MM-DD HH:MM:SS'
      end:       结束时间字符串（可 None）
      duration:  耗时秒（可 None）
      rc:        退出码（可 None）
      log:       手动日志文件名（可 None）
      task_name: 任务名称（如 '指数日交易数据'，可 None 时从 shell 脚本提取）
      source:    任务来源：'manual'（例行任务监控页手动触发，默认）或
                 'strategy'（策略选股结果页运行选股策略）
      biz_date:  业务目标日 'YYYYMMDD'（选股策略提交的目标交易日，可 None）
      run_id:    显式运行编号（可 None）。例行手动触发用
                 'manual_<YYYYMMDD>_<script_stem>'（同日同脚本覆盖更新）；
                 选股策略每次提交独立编号
                 'strategy_<YYYYMMDD_HHMMSS>_<script_stem>'（由调用方生成并透传，
                 保证 running→success 两次写入命中同一行）。

    使用 run_id 区分定时批次（YYYYMMDD_HHMMSS）与手动执行。
    """
    from module_mysql_connection import get_mysql_connection, close_connection

    now = datetime.now()
    if run_id is None:
        if source == 'strategy':
            run_id = 'strategy_' + now.strftime('%Y%m%d_%H%M%S') + '_' + script[:-3]
        else:
            run_id = 'manual_' + now.strftime('%Y%m%d') + '_' + script[:-3]
    run_date = now.strftime('%Y-%m-%d')

    # 若未传入 task_name，尝试从 shell 脚本注释中提取
    if not task_name:
        task_name = _lookup_task_name(script)

    sql = """
        INSERT INTO task_run_log_t
            (source, biz_date, run_id, run_date, batch_phase, step, script_name,
             task_name, status,
             start_time, end_time, duration_sec, detail_log,
             batch_status, batch_start, batch_end, batch_duration_sec,
             total_tasks, success_tasks, failed_tasks)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            source=VALUES(source), biz_date=VALUES(biz_date),
            task_name=VALUES(task_name), batch_phase=VALUES(batch_phase),
            status=VALUES(status), start_time=VALUES(start_time),
            end_time=VALUES(end_time), duration_sec=VALUES(duration_sec),
            detail_log=VALUES(detail_log), batch_status=VALUES(batch_status),
            batch_start=VALUES(batch_start), batch_end=VALUES(batch_end),
            batch_duration_sec=VALUES(batch_duration_sec),
            total_tasks=VALUES(total_tasks), success_tasks=VALUES(success_tasks),
            failed_tasks=VALUES(failed_tasks)
    """
    phase = '选股策略手动执行' if source == 'strategy' else '手动执行'
    batch_status = 'success' if status == 'success' else ('failed' if status == 'failed' else 'running')
    conn = get_mysql_connection()
    if not conn:
        return 0
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                source, biz_date,
                run_id, run_date,
                phase, '-', script, task_name, status,
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
