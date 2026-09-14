#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Flask 回测结果查询 Web 应用 (app.py)

核心功能：
  1. GET /                → 返回 pages/index.html 首页（选股策略导航）；
  2. GET /backtest        → 返回 pages/backtest_web.html 交互式回测查询页面；
  3. GET /stock?code=xxx  → 返回 pages/stock_detail.html 个股K线详情页；
  4. GET /market          → 返回 pages/market_monitor.html 大盘风险监测页
                            （由 report_market_monitor_html.py 定时任务生成）；
  5. GET /cron            → 返回 pages/cron_monitor.html 任务运行监控页，
                            /api/cron_tasks 实时解析 cron_logs 编排日志，
                            /api/cron_log 读取单个任务明细日志；
  6. GET /api/strategies  → 返回 strategy_selected_stock_daily_t 表中所有策略名
                            （strategy 字段为逗号串，拆分为独立策略名并去重排序）；
  7. GET /api/strategies_meta → AST 扫描所有 select_*.py，返回策略中文名、
                            STRATEGY_NAME 与选股条件说明（取自模块 docstring）；
  8. GET /api/results     → 按策略名称（FIND_IN_SET 匹配逗号串）+ 日期范围
                            （trade_date，YYYYMMDD）查询回测结果，返回 JSON；
  9. GET /api/kline       → 返回个股最近 200 个交易日的前复权日K数据
                            （OHLC/成交量）及策略选中日期标记；
  10. GET /api/selected_stocks → 去重返回所有被策略选中过的股票（详情页导航兜底）。

页面展示字段：股票代码、股票名称、交易日、策略、是否选中、10日/20日最大涨幅、10日/20日最大跌幅，
前端点击表头可按任意涨跌幅列（及交易日）升序/降序排序，空值始终排在最后。

用法：
  python app.py                       # 缺少 flask 时自动切换到项目 venv 解释器
  ${项目}/.venv/bin/python3 app.py    # 启动后浏览器访问 http://127.0.0.1:5000
"""

import ast
import difflib
import glob
import os
import re
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# 系统 Python 多为 PEP 668 外部管理环境，无法直接 pip 安装 flask；
# 若当前解释器缺少 flask，则自动切换到项目 venv 的 python 重新执行本脚本。
try:
    from flask import Flask, jsonify, request, send_from_directory
except ModuleNotFoundError:
    VENV_PYTHON = os.path.join(BASE_DIR, '.venv', 'bin', 'python3')
    if os.path.exists(VENV_PYTHON) and os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])
    raise

from mysql_connection import get_mysql_connection, close_connection

PAGE_DIR = os.path.join(BASE_DIR, 'pages')

KLINE_DAYS = 200   # 个股详情页展示的最近交易日数量

app = Flask(__name__)


@app.after_request
def no_cache_html(resp):
    """页面文件每次都校验最新内容，避免浏览器缓存旧版 HTML 导致改动不生效。"""
    if resp.content_type and resp.content_type.startswith('text/html'):
        resp.headers['Cache-Control'] = 'no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
    return resp


def query_db(sql, params=None):
    """执行查询并返回结果列表；连接失败返回 None。"""
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
    finally:
        close_connection(conn)


@app.route('/favicon.ico')
def favicon():
    """浏览器标签页小图标（默认从站点根路径请求 ico）。"""
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/x-icon')


@app.route('/')
def index():
    """首页：选股策略导航。"""
    return send_from_directory(PAGE_DIR, 'index.html')


@app.route('/backtest')
def backtest_page():
    """回测结果交互式查询页面。"""
    return send_from_directory(PAGE_DIR, 'backtest_web.html')


@app.route('/stock')
def stock_detail():
    """返回 pages/ 下的个股K线详情页（股票代码由 ?code= 提供，前端解析）。"""
    return send_from_directory(PAGE_DIR, 'stock_detail.html')


@app.route('/market')
def market_monitor():
    """大盘风险监测页，由 report_market_monitor_html.py 定时任务预先生成。"""
    report_path = os.path.join(PAGE_DIR, 'market_monitor.html')
    if not os.path.exists(report_path):
        return ('<!DOCTYPE html><meta charset="utf-8">'
                '<div style="font-family:sans-serif;text-align:center;margin-top:120px">'
                '<h2>大盘风险监测报告尚未生成</h2>'
                '<p>请在项目目录运行：<code>.venv/bin/python report_market_monitor_html.py</code></p>'
                '<p><a href="/">← 返回首页</a></p></div>'), 404
    return send_from_directory(PAGE_DIR, 'market_monitor.html')


@app.route('/cron')
def cron_monitor():
    """任务运行监控页：展示当天定时任务（run_daily_stock_tasks.sh）执行情况。"""
    return send_from_directory(PAGE_DIR, 'cron_monitor.html')


# ---------------------------------------------------------------------------
# 定时任务运行监控：解析 cron_logs/ 下 run_daily_stock_tasks.sh 的编排日志
# ---------------------------------------------------------------------------
CRON_LOG_DIR = os.path.join(BASE_DIR, 'cron_logs')
_CRON_TS_LINE = re.compile(r'^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s?(?P<body>.*)$')
_CRON_START = re.compile(r'^\[(?P<step>[^\]]+)\]\s?开始执行\s?(?P<script>[\w.]+\.py)\s?\.\.\.$')
_CRON_END = re.compile(r'^\[(?P<step>[^\]]+)\]\s?[✅❌]\s?(?P<script>[\w.]+\.py)\s?执行(?P<result>成功|失败)')


def _match_detail_logs(scripts, run_id):
    """把每个任务脚本名匹配到 cron_logs 中对应的明细日志文件。

    脚本里重定向文件名与脚本名存在个别不一致（如 select_newhigh_in_120d.py
    对应 select_newhigh_120d_<run_id>.log，部分文件名少下划线），先精确匹配，
    剩余任务按文件名相似度兜底。
    """
    try:
        pool = [f for f in os.listdir(CRON_LOG_DIR)
                if f.endswith('.log') and run_id in f and not f.startswith('daily_stock_')]
    except OSError:
        return {}

    mapping = {}

    def stem_of(fname):
        return fname.replace(run_id, '').replace('.log', '').rstrip('_')

    # 1) 精确匹配：<script_stem>_<run_id>.log 或 <script_stem><run_id>.log
    for script in scripts:
        stem = script[:-3]
        for cand in (f'{stem}_{run_id}.log', f'{stem}{run_id}.log'):
            if cand in pool:
                mapping[script] = cand
                pool.remove(cand)
                break

    # 2) 相似度兜底：全局贪心选最高分对
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


def _parse_cron_run():
    """解析 cron_logs 中最近一次 daily_stock_*.log 编排日志。

    返回 None 表示 cron_logs 目录不存在；返回 has_run=False 表示目录在但无运行日志。
    每次调用都重新读文件，便于页面轮询“运行中”的任务。
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
    pending = {}          # (步骤, 脚本) → tasks 下标
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


@app.route('/api/cron_tasks')
def api_cron_tasks():
    """当天定时任务运行列表（实时读 cron_logs，可轮询）。"""
    return jsonify(_parse_cron_run() or {'log_dir_exists': False})


@app.route('/api/cron_log')
def api_cron_log():
    """读取单个任务明细日志（默认末尾 200 行），仅允许 cron_logs 内的文件名。"""
    name = request.args.get('file', '')
    if not re.match(r'^[\w.\-]+\.log$', name) or name.startswith('daily_stock_'):
        return jsonify({'error': '非法日志文件名'}), 400
    path = os.path.join(CRON_LOG_DIR, name)
    log_dir = os.path.realpath(CRON_LOG_DIR)
    if not os.path.realpath(path).startswith(log_dir + os.sep) or not os.path.isfile(path):
        return jsonify({'error': '日志文件不存在'}), 404
    try:
        tail_n = max(1, min(int(request.args.get('tail', 200)), 5000))
    except ValueError:
        tail_n = 200
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    return jsonify({'file': name, 'content': ''.join(lines[-tail_n:]), 'truncated': len(lines) > tail_n})


# 标题形如 "二浪日线选股策略 (select_2wave_daily.py)"，去掉括号内文件名
_TITLE_FILE_SUFFIX = re.compile(r'[（(]\s*select_[^）)]*\.py\s*[）)]')
_strategy_meta_cache = {}   # {文件mtime: 元数据}，select 文件运行期不变，缓存一次即可


def _parse_strategy_file(path):
    """AST 解析单个 select_*.py，提取策略名/中文名/docstring 选股条件。"""
    filename = os.path.basename(path)
    try:
        with open(path, encoding='utf-8') as f:
            source = f.read()
    except OSError:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    strategy_name = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'STRATEGY_NAME':
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        strategy_name = node.value.value
        # 兜底：部分文件未定义 STRATEGY_NAME 常量，直接把字面量传给入库函数
        if (strategy_name is None and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == 'record_selected_stocks'
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            strategy_name = node.args[0].value

    doc = ast.get_docstring(tree) or ''
    lines = [ln.rstrip() for ln in doc.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    title = _TITLE_FILE_SUFFIX.sub('', lines[0]).strip() if lines else filename
    # 标题行之后的全部内容作为展开区文案（含选股条件、输出约定等）
    body = '\n'.join(lines[1:]).strip()
    return {
        'file': filename,
        'strategy': strategy_name,
        'title': title,
        'doc': body,
    }


def _load_strategies_meta():
    """扫描全部 select_*.py（按文件名排序），结果按文件 mtime 缓存。"""
    paths = sorted(glob.glob(os.path.join(BASE_DIR, 'select_*.py')))
    sig = tuple((p, os.path.getmtime(p)) for p in paths)
    if _strategy_meta_cache.get('sig') != sig:
        metas = [m for m in (_parse_strategy_file(p) for p in paths) if m]
        _strategy_meta_cache.clear()
        _strategy_meta_cache['sig'] = sig
        _strategy_meta_cache['data'] = metas
    return _strategy_meta_cache['data']


@app.route('/api/strategies')
def api_strategies():
    """返回所有策略名（strategy 逗号串拆分去重）。"""
    rows = query_db("SELECT strategy FROM strategy_selected_stock_daily_t")
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500
    names = sorted({s.strip() for r in rows
                    for s in (r['strategy'] or '').split(',') if s.strip()})
    return jsonify({'strategies': names})


@app.route('/api/strategies_meta')
def api_strategies_meta():
    """扫描所有 select_*.py，返回策略中文名、入库策略名及选股条件说明。"""
    return jsonify({'strategies': _load_strategies_meta()})


@app.route('/api/results')
def api_results():
    """按策略名称 + 日期范围查询回测结果。"""
    strategy = request.args.get('strategy', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()

    sql = """
        SELECT ts_code, stock_name, trade_date, strategy, selected,
               max_gain_10d, max_down_10d, max_gain_20d, max_down_20d
        FROM strategy_selected_stock_daily_t
        WHERE 1=1
    """
    params = []
    if strategy:
        sql += " AND FIND_IN_SET(%s, strategy)"
        params.append(strategy)
    if start:
        sql += " AND trade_date >= %s"
        params.append(start)
    if end:
        sql += " AND trade_date <= %s"
        params.append(end)
    sql += " ORDER BY trade_date DESC, ts_code"

    rows = query_db(sql, params)
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500

    # Decimal → float，便于前端 JSON 序列化
    for r in rows:
        for k in ('max_gain_10d', 'max_down_10d', 'max_gain_20d', 'max_down_20d'):
            if r[k] is not None:
                r[k] = float(r[k])
    return jsonify({'rows': rows, 'total': len(rows)})


def _fmt_date(d):
    """YYYYMMDD → YYYY-MM-DD（供前端 x 轴与 markLine 对齐使用）。"""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else d


@app.route('/api/kline')
def api_kline():
    """返回个股最近 KLINE_DAYS 个交易日前复权日K及策略选中标记。"""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'error': '缺少 code 参数'}), 400

    # 最近 200 个交易日（子查询取日期后正序返回）
    rows = query_db("""
        SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol
        FROM (
            SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol
            FROM stock_daily_t
            WHERE ts_code = %s
            ORDER BY trade_date DESC
            LIMIT %s
        ) t ORDER BY trade_date
    """, (code, KLINE_DAYS))
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500
    if not rows:
        return jsonify({'error': f'未找到 {code} 的行情数据'}), 404

    earliest = rows[0]['trade_date']

    # 该区间内的策略选中记录（含股票名称）
    marks = query_db("""
        SELECT trade_date, strategy, stock_name
        FROM strategy_selected_stock_daily_t
        WHERE ts_code = %s AND trade_date >= %s
        ORDER BY trade_date
    """, (code, earliest))
    if marks is None:
        marks = []

    stock_name = next((m['stock_name'] for m in marks if m.get('stock_name')), None)
    klines, vols = [], []
    for r in rows:
        klines.append({
            'date': _fmt_date(r['trade_date']),
            # ECharts 蜡烛图约定：[open, close, low, high]
            'ohlc': [r['open'], r['close'], r['low'], r['high']],
            'pct_chg': r['pct_chg'],
        })
        vols.append(r['vol'])
    return jsonify({
        'ts_code': code,
        'stock_name': stock_name,
        'klines': klines,
        'vols': vols,
        'marks': [{'date': _fmt_date(m['trade_date']),
                   'strategy': m['strategy']} for m in marks],
    })


@app.route('/api/selected_stocks')
def api_selected_stocks():
    """去重返回所有被策略选中过的股票（详情页无导航上下文时的兜底列表）。"""
    rows = query_db("""
        SELECT ts_code, MAX(stock_name) AS stock_name
        FROM strategy_selected_stock_daily_t
        GROUP BY ts_code
        ORDER BY ts_code
    """)
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500
    return jsonify({'stocks': [{'ts_code': r['ts_code'],
                                'stock_name': r['stock_name']} for r in rows]})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
