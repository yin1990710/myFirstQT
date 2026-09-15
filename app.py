#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Flask 回测结果查询 Web 应用 (app.py)

核心功能：
  1. GET /                → 返回 pages/index.html 首页（选股策略导航）；
  2. GET /about           → 返回 pages/关于我们.html 项目介绍页；
  3. GET /backtest        → 返回 pages/选股策略回测.html 交互式回测查询页面；
  4. GET /stock?code=xxx  → 返回 pages/个股详情.html 个股K线详情页；
  5. GET /market          → 返回 pages/大盘整体情况.html 大盘整体情况页（静态模板），
                            GET /api/market_data 返回 JSON 数据
                            （由 report_market_overall_html.py 生成 market_overall_data.json）；
  6. GET /cron            → 返回 pages/任务调度监控.html 任务运行监控页，
                            /api/cron_tasks 实时解析 cron_logs 编排日志，
                            /api/cron_log 读取单个任务明细日志；
  7. GET /api/strategies  → 返回 strategy_selected_stock_daily_t 表中所有策略名
                            （strategy 字段为逗号串，拆分为独立策略名并去重排序）；
  8. GET /api/strategies_meta → AST 扫描所有 select_*.py，返回策略中文名、
                            STRATEGY_NAME 与选股条件说明（取自模块 docstring）；
  9. GET /api/results     → 按策略名称（FIND_IN_SET 匹配逗号串）+ 日期范围
                            （trade_date，YYYYMMDD）查询回测结果，返回 JSON；
  10. GET /api/kline       → 返回个股最近 200 个交易日的前复权日K数据
                            （OHLC/成交量）及策略选中日期标记；
  11. GET /api/selected_stocks → 去重返回所有被策略选中过的股票（详情页导航兜底）；
  12. POST /api/strategies_run + GET /api/strategies_status → 选股策略手动执行与状态查询。

页面展示字段：股票代码、股票名称、交易日、策略、是否选中、10日/20日最大涨幅、10日/20日最大跌幅，
前端点击表头可按任意涨跌幅列（及交易日）升序/降序排序，空值始终排在最后。

用法：
  python app.py                       # 缺少 flask 时自动切换到项目 venv 解释器
  ${项目}/.venv/bin/python3 app.py    # 启动后浏览器访问 http://127.0.0.1:5000
"""

import ast
import glob
import json
import os
import re
import subprocess
import sys
import threading
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
from monitor_run_daily_tasks import (
    parse_cron_run,
    write_run_log,
    write_manual_run_log,
    get_allowed_scripts,
    CRON_LOG_DIR,
    MANUAL_LOG_DIR,
)

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


@app.route('/about')
def about_page():
    """关于我们：项目介绍页面。"""
    return send_from_directory(PAGE_DIR, '关于我们.html')


@app.route('/etf')
def etf_page():
    """ETF 专区：展示全市场 ETF 基础信息与最新日线行情。"""
    return send_from_directory(PAGE_DIR, 'ETF专区.html')


@app.route('/api/etf_data')
def api_etf_data():
    """返回 ETF 基础信息 + 最新交易日日线行情，支持按交易所/名称筛选。

    查询参数：
      exchange：交易所筛选（SH / SZ / 空为全部）
      keyword ：ETF 简称/跟踪指数名模糊搜索
    响应：
      {trade_date, count, etfs: [{ts_code, csname, index_name, mgr_name,
                                  list_date, etf_type, close, pct_chg,
                                  vol, amount, pre_close, change}]}
    """
    exchange = (request.args.get('exchange') or '').strip().upper()
    keyword = (request.args.get('keyword') or '').strip()

    conn = get_mysql_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败'}), 503
    try:
        with conn.cursor() as cur:
            # 最新交易日
            cur.execute("SELECT MAX(trade_date) AS d FROM etf_daily_t")
            row = cur.fetchone() or {}
            trade_date = row.get('d')
            if not trade_date:
                return jsonify({'trade_date': None, 'count': 0, 'etfs': []})

            # 联表查询基础信息 + 最新日线
            sql = """
                SELECT b.ts_code, b.csname, b.cname, b.index_code, b.index_name,
                       b.list_date, b.exchange, b.mgr_name, b.mgt_fee, b.etf_type,
                       d.pre_close, d.open, d.high, d.low, d.close,
                       d.`change`, d.pct_chg, d.vol, d.amount
                FROM etf_basic_t b
                LEFT JOIN etf_daily_t d
                  ON b.ts_code = d.ts_code AND d.trade_date = %s
                WHERE 1=1
            """
            params = [trade_date]
            if exchange:
                sql += " AND b.exchange = %s"
                params.append(exchange)
            if keyword:
                sql += " AND (b.csname LIKE %s OR b.cname LIKE %s OR b.index_name LIKE %s)"
                kw = f'%{keyword}%'
                params += [kw, kw, kw]
            sql += " ORDER BY (d.amount IS NULL) ASC, d.amount DESC LIMIT 500"
            cur.execute(sql, params)
            rows = cur.fetchall() or []
            # amount/vol 字段 DECIMAL → float
            for r in rows:
                for k in ('pre_close', 'open', 'high', 'low', 'close', 'change',
                          'pct_chg', 'vol', 'amount', 'mgt_fee'):
                    if r.get(k) is not None:
                        r[k] = float(r[k])
            return jsonify({'trade_date': trade_date, 'count': len(rows), 'etfs': rows})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        close_connection(conn)


@app.route('/backtest')
def backtest_page():
    """回测结果交互式查询页面。"""
    return send_from_directory(PAGE_DIR, '选股策略回测.html')


@app.route('/stock')
def stock_detail():
    """返回 pages/ 下的个股K线详情页（股票代码由 ?code= 提供，前端解析）。"""
    return send_from_directory(PAGE_DIR, '个股详情.html')


@app.route('/market')
def market_monitor():
    """大盘整体情况页（静态模板），由前端 JS 调用 /api/market_data 拉取数据渲染。"""
    report_path = os.path.join(PAGE_DIR, '大盘整体情况.html')
    if not os.path.exists(report_path):
        return ('<!DOCTYPE html><meta charset="utf-8">'
                '<div style="font-family:sans-serif;text-align:center;margin-top:120px">'
                '<h2>大盘整体情况页面缺失</h2>'
                '<p>pages/大盘整体情况.html 不存在，请检查项目文件。</p>'
                '<p><a href="/">← 返回首页</a></p></div>'), 404
    return send_from_directory(PAGE_DIR, '大盘整体情况.html')


@app.route('/api/market_data')
def api_market_data():
    """返回大盘风险监测数据 JSON（由 report_market_overall_html.py 生成到 pages/market_overall_data.json）。"""
    path = os.path.join(PAGE_DIR, 'market_overall_data.json')
    if not os.path.exists(path):
        return jsonify({'error': '数据尚未生成，请先运行 .venv/bin/python report_market_overall_html.py'}), 404
    with open(path, encoding='utf-8') as f:
        return jsonify(json.load(f))


@app.route('/cron')
def cron_monitor():
    """任务运行监控页：展示当天定时任务（run_daily_stock_tasks.sh）执行情况。"""
    return send_from_directory(PAGE_DIR, '任务调度监控.html')


@app.route('/data-monitor')
def data_monitor():
    """数据完整性监控页：展示 stock_daily_t 表数据更新情况。"""
    return send_from_directory(PAGE_DIR, '数据完整性监控.html')


@app.route('/api/stock_data_monitor')
def api_stock_data_monitor():
    """数据完整性监控 API：实时查询数据库返回监控数据 JSON。

    可选参数 start_date / end_date（YYYYMMDD），指定时查询日期范围内的交易日。
    """
    try:
        from monitor_stock_data import collect_data
        start_date = (request.args.get('start_date') or '').strip()
        end_date = (request.args.get('end_date') or '').strip()
        if start_date and end_date:
            data = collect_data(start_date=start_date, end_date=end_date)
        else:
            data = collect_data(days=10)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/mri')
def mri_dashboard():
    """大盘风险分析报告（MRI 实时仪表盘），由 report_market_risk.py 生成。"""
    path = os.path.join(PAGE_DIR, 'MRI实时仪表盘.html')
    if not os.path.exists(path):
        return ('<!DOCTYPE html><meta charset="utf-8">'
                '<title>MRI 仪表盘未生成</title><body style="font-family:sans-serif;padding:40px">'
                '<h2>MRI 实时仪表盘尚未生成</h2>'
                '<p>请先运行 <code>python report_market_risk.py</code> 生成报告。</p>'
                '<p><a href="/">返回首页</a></p></body>'), 404
    return send_from_directory(PAGE_DIR, 'MRI实时仪表盘.html')


@app.route('/mri/framework')
def mri_framework():
    """A股大盘风险指标体系说明页。"""
    return send_from_directory(PAGE_DIR, 'A股大盘风险指标体系.html')


@app.route('/api/mri_data')
def api_mri_data():
    """返回 MRI 风险指数数据 JSON（由 report_market_risk.py 生成到 pages/mri_data.json）。"""
    path = os.path.join(PAGE_DIR, 'mri_data.json')
    if not os.path.exists(path):
        return jsonify({'error': '数据尚未生成，请先点击「重新生成报告」'}), 404
    with open(path, encoding='utf-8') as f:
        return jsonify(json.load(f))


# MRI 报告重新生成（单任务，复用手动执行状态管理）
_MRI_SCRIPT = 'report_market_risk.py'
_mri_state = {'status': 'idle'}  # idle | running | success | failed
_mri_lock = threading.Lock()


def _reap_mri_process(proc, started):
    rc = proc.wait()
    ended = datetime.now()
    with _mri_lock:
        _mri_state.update(status='success' if rc == 0 else 'failed',
                          end=ended.strftime('%Y-%m-%d %H:%M:%S'), rc=rc,
                          duration=round((ended - started).total_seconds(), 1))


@app.route('/api/mri_regenerate', methods=['POST'])
def api_mri_regenerate():
    """后台重新生成 MRI 报告（运行 report_market_risk.py）。"""
    with _mri_lock:
        if _mri_state.get('status') == 'running':
            return jsonify({'error': '报告正在生成中，请勿重复触发',
                            'state': dict(_mri_state)}), 409
        script_path = os.path.join(BASE_DIR, _MRI_SCRIPT)
        if not os.path.isfile(script_path):
            return jsonify({'error': '脚本不存在'}), 404
        started = datetime.now()
        try:
            proc = subprocess.Popen([sys.executable, script_path], cwd=BASE_DIR)
        except OSError as e:
            return jsonify({'error': f'启动失败: {e}'}), 500
        _mri_state.update(status='running', start=started.strftime('%Y-%m-%d %H:%M:%S'),
                          end=None, rc=None, duration=None, pid=proc.pid)
    threading.Thread(target=_reap_mri_process, args=(proc, started), daemon=True).start()
    return jsonify({'ok': True, 'state': dict(_mri_state)}), 202


@app.route('/api/mri_status')
def api_mri_status():
    """查询 MRI 报告生成状态。"""
    with _mri_lock:
        return jsonify(dict(_mri_state))


# ---------------------------------------------------------------------------
# 定时任务运行监控：日志解析+数据库写入逻辑在 monitor_run_daily_tasks.py
# ---------------------------------------------------------------------------

# 手动执行的在途/最近状态（进程内存保存，Flask 重启后重置）
_manual_state = {}   # {脚本名: {status,start,end,rc,duration,log,pid}}
_manual_lock = threading.Lock()


def _manual_snapshot(script):
    st = _manual_state.get(script)
    return dict(st) if st else None


def _reap_manual_process(script, proc, started, log_fp):
    """后台等待子进程结束并回写状态。"""
    rc = proc.wait()
    log_fp.close()
    ended = datetime.now()
    status = 'success' if rc == 0 else 'failed'
    end_str = ended.strftime('%Y-%m-%d %H:%M:%S')
    duration = round((ended - started).total_seconds(), 1)
    with _manual_lock:
        st = _manual_state.get(script)
        if st:
            st.update(status=status, end=end_str, rc=rc, duration=duration)
            # 写入 task_run_log_t
            try:
                write_manual_run_log(script, status,
                                     st.get('start'), end_str, duration,
                                     rc=rc, log=st.get('log'))
            except Exception:
                pass

@app.route('/api/cron_tasks')
def api_cron_tasks():
    """当天定时任务运行列表（实时读 cron_logs，可轮询），并附带手动执行状态。
    解析后将结果写入 task_run_log_t 表持久化。
    """
    data = parse_cron_run() or {'log_dir_exists': False}
    if data.get('has_run'):
        try:
            write_run_log(data)
        except Exception:
            pass
        for task in data['tasks']:
            task['manual'] = _manual_snapshot(task['name'])
    return jsonify(data)



@app.route('/api/cron_run', methods=['POST'])
def api_cron_run():
    """手动触发单个任务脚本：后台执行，输出重定向到 manual_logs/。

    可选 start_date / end_date（YYYYMMDD），作为命令行参数传给脚本。
    """
    payload = request.get_json(silent=True) or {}
    script = (payload.get('script') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_]+\.py', script):
        return jsonify({'error': '任务名不合法'}), 400
    if script not in get_allowed_scripts():
        return jsonify({'error': '该脚本不在每日定时任务清单中，禁止执行'}), 403
    script_path = os.path.join(BASE_DIR, script)
    if not os.path.isfile(script_path):
        return jsonify({'error': '任务脚本不存在'}), 404

    # 可选日期参数（YYYYMMDD 格式校验）
    start_date = (payload.get('start_date') or '').strip()
    end_date = (payload.get('end_date') or '').strip()
    cmd_args = []
    if start_date:
        if not re.fullmatch(r'\d{8}', start_date):
            return jsonify({'error': 'start_date 格式应为 YYYYMMDD'}), 400
        cmd_args.append(start_date)
    if end_date:
        if not re.fullmatch(r'\d{8}', end_date):
            return jsonify({'error': 'end_date 格式应为 YYYYMMDD'}), 400
        if start_date and end_date < start_date:
            return jsonify({'error': '结束日期不能早于开始日期'}), 400
        if not start_date:
            cmd_args.append(end_date)  # 只传了一个日期
        else:
            cmd_args.append(end_date)

    with _manual_lock:
        current = _manual_state.get(script)
        if current and current.get('status') == 'running':
            return jsonify({'error': '该任务正在执行中，请勿重复触发',
                            'state': dict(current)}), 409

        os.makedirs(MANUAL_LOG_DIR, exist_ok=True)
        started = datetime.now()
        log_name = 'manual_%s_%s.log' % (script[:-3], started.strftime('%Y%m%d_%H%M%S'))
        log_fp = open(os.path.join(MANUAL_LOG_DIR, log_name), 'a', encoding='utf-8')
        try:
            proc = subprocess.Popen([sys.executable, script_path] + cmd_args,
                                    cwd=BASE_DIR,
                                    stdout=log_fp, stderr=subprocess.STDOUT)
        except OSError as e:
            log_fp.close()
            return jsonify({'error': f'任务启动失败: {e}'}), 500

        state = {'status': 'running', 'start': started.strftime('%Y-%m-%d %H:%M:%S'),
                 'end': None, 'rc': None, 'duration': None,
                 'log': log_name, 'pid': proc.pid}
        _manual_state[script] = state

    threading.Thread(target=_reap_manual_process,
                     args=(script, proc, started, log_fp), daemon=True).start()
    # 写入 running 状态到 task_run_log_t
    try:
        write_manual_run_log(script, 'running', state['start'], None, None, log=log_name)
    except Exception:
        pass
    return jsonify({'ok': True, 'state': state}), 202


@app.route('/api/cron_run_batch', methods=['POST'])
def api_cron_run_batch():
    """批量手动触发多个任务脚本：逐个在后台执行，返回每个脚本的启动结果。

    请求体：{scripts: ['a.py', 'b.py'], start_date?: 'YYYYMMDD', end_date?: 'YYYYMMDD'}
    响应：{results: [{script, ok, state?, error?}]}
    """
    payload = request.get_json(silent=True) or {}
    scripts = payload.get('scripts') or []
    if not isinstance(scripts, list) or not scripts:
        return jsonify({'error': 'scripts 参数不能为空'}), 400

    allowed = get_allowed_scripts()
    start_date = (payload.get('start_date') or '').strip()
    end_date = (payload.get('end_date') or '').strip()
    cmd_args = []
    if start_date:
        if not re.fullmatch(r'\d{8}', start_date):
            return jsonify({'error': 'start_date 格式应为 YYYYMMDD'}), 400
        cmd_args.append(start_date)
    if end_date:
        if not re.fullmatch(r'\d{8}', end_date):
            return jsonify({'error': 'end_date 格式应为 YYYYMMDD'}), 400
        if start_date and end_date < start_date:
            return jsonify({'error': '结束日期不能早于开始日期'}), 400
        cmd_args.append(end_date)

    results = []
    for script in scripts:
        script = (script or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_]+\.py', script):
            results.append({'script': script, 'ok': False, 'error': '任务名不合法'})
            continue
        if script not in allowed:
            results.append({'script': script, 'ok': False, 'error': '不在定时任务清单中'})
            continue
        script_path = os.path.join(BASE_DIR, script)
        if not os.path.isfile(script_path):
            results.append({'script': script, 'ok': False, 'error': '脚本不存在'})
            continue

        with _manual_lock:
            current = _manual_state.get(script)
            if current and current.get('status') == 'running':
                results.append({'script': script, 'ok': False,
                                'error': '正在执行中，已跳过', 'state': dict(current)})
                continue

            os.makedirs(MANUAL_LOG_DIR, exist_ok=True)
            started = datetime.now()
            log_name = 'manual_%s_%s.log' % (script[:-3], started.strftime('%Y%m%d_%H%M%S'))
            log_fp = open(os.path.join(MANUAL_LOG_DIR, log_name), 'a', encoding='utf-8')
            try:
                proc = subprocess.Popen([sys.executable, script_path] + cmd_args,
                                        cwd=BASE_DIR,
                                        stdout=log_fp, stderr=subprocess.STDOUT)
            except OSError as e:
                log_fp.close()
                results.append({'script': script, 'ok': False, 'error': f'启动失败: {e}'})
                continue

            state = {'status': 'running', 'start': started.strftime('%Y-%m-%d %H:%M:%S'),
                     'end': None, 'rc': None, 'duration': None,
                     'log': log_name, 'pid': proc.pid}
            _manual_state[script] = state

        threading.Thread(target=_reap_manual_process,
                         args=(script, proc, started, log_fp), daemon=True).start()
        # 写入 running 状态到 task_run_log_t
        try:
            write_manual_run_log(script, 'running', state['start'], None, None, log=log_name)
        except Exception:
            pass
        results.append({'script': script, 'ok': True, 'state': state})

    return jsonify({'results': results}), 202


# ---------------------------------------------------------------------------
# 选股策略手动执行（独立于 cron 任务清单，按 select_*.py 文件系统扫描）
# ---------------------------------------------------------------------------

@app.route('/api/strategies_run', methods=['POST'])
def api_strategies_run():
    """批量手动执行选股策略脚本：逐个在后台执行，返回每个脚本的启动结果。

    请求体：{scripts: ['select_limitup_1d.py', ...], target_date?: 'YYYYMMDD'}
    响应：{results: [{script, ok, state?, error?}]}
    """
    payload = request.get_json(silent=True) or {}
    scripts = payload.get('scripts') or []
    if not isinstance(scripts, list) or not scripts:
        return jsonify({'error': 'scripts 参数不能为空'}), 400

    # 校验脚本名：必须是 select_*.py 且文件存在
    valid_files = {os.path.basename(p) for p in glob.glob(os.path.join(BASE_DIR, 'select_*.py'))}
    target_date = (payload.get('target_date') or '').strip()
    cmd_args = []
    if target_date:
        if not re.fullmatch(r'\d{8}', target_date):
            return jsonify({'error': 'target_date 格式应为 YYYYMMDD'}), 400
        cmd_args.append(target_date)

    results = []
    for script in scripts:
        script = (script or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_]+\.py', script):
            results.append({'script': script, 'ok': False, 'error': '脚本名不合法'})
            continue
        if script not in valid_files:
            results.append({'script': script, 'ok': False, 'error': '不是有效的选股策略脚本'})
            continue
        script_path = os.path.join(BASE_DIR, script)
        if not os.path.isfile(script_path):
            results.append({'script': script, 'ok': False, 'error': '脚本不存在'})
            continue

        with _manual_lock:
            current = _manual_state.get(script)
            if current and current.get('status') == 'running':
                results.append({'script': script, 'ok': False,
                                'error': '正在执行中，已跳过', 'state': dict(current)})
                continue

            os.makedirs(MANUAL_LOG_DIR, exist_ok=True)
            started = datetime.now()
            log_name = 'manual_%s_%s.log' % (script[:-3], started.strftime('%Y%m%d_%H%M%S'))
            log_fp = open(os.path.join(MANUAL_LOG_DIR, log_name), 'a', encoding='utf-8')
            try:
                proc = subprocess.Popen([sys.executable, script_path] + cmd_args,
                                        cwd=BASE_DIR,
                                        stdout=log_fp, stderr=subprocess.STDOUT)
            except OSError as e:
                log_fp.close()
                results.append({'script': script, 'ok': False, 'error': f'启动失败: {e}'})
                continue

            state = {'status': 'running', 'start': started.strftime('%Y-%m-%d %H:%M:%S'),
                     'end': None, 'rc': None, 'duration': None,
                     'log': log_name, 'pid': proc.pid}
            _manual_state[script] = state

        threading.Thread(target=_reap_manual_process,
                         args=(script, proc, started, log_fp), daemon=True).start()
        try:
            write_manual_run_log(script, 'running', state['start'], None, None, log=log_name)
        except Exception:
            pass
        results.append({'script': script, 'ok': True, 'state': state})

    return jsonify({'results': results}), 202


@app.route('/api/strategies_status')
def api_strategies_status():
    """查询选股策略脚本执行状态（scripts 参数为逗号分隔的脚本名列表）。

    响应：{statuses: {select_xxx.py: {status, start, end, rc, duration, log, pid}}}
    """
    scripts = request.args.get('scripts', '').strip()
    if not scripts:
        return jsonify({'statuses': {}})
    names = [s.strip() for s in scripts.split(',') if s.strip()]
    with _manual_lock:
        statuses = {n: dict(_manual_state[n]) for n in names if n in _manual_state}
    return jsonify({'statuses': statuses})


@app.route('/api/cron_log')
def api_cron_log():
    """读取单个任务明细日志（默认末尾 200 行）。

    src=cron（默认）读 cron_logs/ 定时编排内的任务日志；
    src=manual 读 manual_logs/ 手动触发产生的日志。
    """
    name = request.args.get('file', '')
    src = request.args.get('src', 'cron')
    if not re.match(r'^[\w.\-]+\.log$', name):
        return jsonify({'error': '非法日志文件名'}), 400
    if src not in ('cron', 'manual'):
        return jsonify({'error': '非法日志来源'}), 400
    base_dir = MANUAL_LOG_DIR if src == 'manual' else CRON_LOG_DIR
    if src == 'cron' and name.startswith('daily_stock_'):
        return jsonify({'error': '编排日志不支持明细查看'}), 400
    if src == 'manual' and not name.startswith('manual_'):
        return jsonify({'error': '非法手动日志文件名'}), 400

    path = os.path.join(base_dir, name)
    if not os.path.realpath(path).startswith(os.path.realpath(base_dir) + os.sep) \
            or not os.path.isfile(path):
        return jsonify({'error': '日志文件不存在'}), 404
    try:
        tail_n = max(1, min(int(request.args.get('tail', 200)), 5000))
    except ValueError:
        tail_n = 200
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    return jsonify({'file': name, 'content': ''.join(lines[-tail_n:]),
                    'truncated': len(lines) > tail_n})


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
