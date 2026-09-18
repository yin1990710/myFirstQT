#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Flask 回测结果查询 Web 应用 (app.py)

核心功能：
  1. GET /                → 返回 pages/index.html 首页（选股策略导航）；
  2. GET /about           → 返回 pages/关于我们.html 项目介绍页；
  3. GET /backtest        → 返回 pages/策略选股结果.html 策略选股结果查询页面；
     GET /backtest_analysis → 返回 pages/策略回测.html 策略回测页面（开发中）；
  4. GET /market-overview  → 返回 pages/大盘指标概览.html 大盘指标概览页；
     GET /mri               → 返回 pages/A股大盘风险指数MRI.html；
  5. GET /cron            → 返回 pages/任务运行监控.html 任务运行监控页，
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
  10. GET /api/selected_stocks → 去重返回所有被策略选中过的股票（详情页导航兜底）；
  11. POST /api/strategies_run + GET /api/strategies_status → 选股策略手动执行与状态查询。

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
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# 系统 Python 多为 PEP 668 外部管理环境，无法直接 pip 安装 flask；
# 若当前解释器缺少 flask，则自动切换到项目 venv 的 python 重新执行本脚本。
try:
    from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for
except ModuleNotFoundError:
    VENV_PYTHON = os.path.join(BASE_DIR, '.venv', 'bin', 'python3')
    if os.path.exists(VENV_PYTHON) and os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        os.execv(VENV_PYTHON, [VENV_PYTHON, os.path.abspath(__file__)] + sys.argv[1:])
    raise

from module_mysql_connection import get_mysql_connection, close_connection
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
app.secret_key = 'ljjh_quant_secret_2026'
app.permanent_session_lifetime = timedelta(hours=3)   # session 有效时长 3 小时


@app.after_request
def no_cache_html(resp):
    """页面与 JSON 接口都不缓存，避免浏览器拿到旧版 HTML 或过期任务状态。"""
    ctype = resp.content_type or ''
    if ctype.startswith('text/html') or 'application/json' in ctype:
        resp.headers['Cache-Control'] = 'no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
    return resp


@app.after_request
def inject_auth_script(resp):
    """向所有 HTML 页面注入登录账号展示脚本（登录页除外），在导航栏右侧显示账号和退出链接。"""
    if (resp.content_type and resp.content_type.startswith('text/html')
            and request.path != '/login'):
        # send_from_directory 返回 direct_passthrough 响应，需关闭才能读取 body
        resp.direct_passthrough = False
        html = resp.get_data(as_text=True)
        if '</body>' in html:
            script = (
                '<script>(function(){'
                "fetch('/api/current_user').then(r=>r.json()).then(d=>{"
                "if(!d.account) return;"
                "var nb=document.querySelector('.navbar');"
                "if(!nb) return;"
                "var el=document.createElement('span');"
                "el.style.cssText='margin-left:auto;display:flex;align-items:center;gap:6px;"
                "color:rgba(255,255,255,.9);font-size:14px;white-space:nowrap;';"
                "el.innerHTML=d.account+' <a href=\"/logout\" style=\"color:#fff;"
                "font-size:12px;text-decoration:none;margin-left:6px;opacity:.8;\">退出</a>';"
                'nb.appendChild(el);});'
                '})();</script>'
            )
            html = html.replace('</body>', script + '</body>')
            resp.set_data(html)
    return resp


# 不需要登录的公开路径
PUBLIC_PATHS = {'/login', '/logout', '/api/current_user', '/favicon.ico'}


@app.before_request
def require_login():
    """全局登录拦截：除公开路径外，所有请求都需要登录，session 超时（3小时）需重新登录。"""
    path = request.path
    if path in PUBLIC_PATHS or path.startswith('/static/'):
        return None
    account = session.get('account')
    login_time = session.get('login_time')
    if account and login_time:
        try:
            elapsed = datetime.now() - datetime.fromisoformat(login_time)
            if elapsed > timedelta(hours=3):
                session.clear()
                if path.startswith('/api/'):
                    return jsonify({'error': '登录已超时，请重新登录', 'need_login': True}), 401
                return redirect(url_for('login_page', next=request.url))
            return None   # 已登录且未超时
        except (ValueError, TypeError):
            session.clear()
    # 未登录
    if path.startswith('/api/'):
        return jsonify({'error': '请先登录', 'need_login': True}), 401
    return redirect(url_for('login_page', next=request.url))


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


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """登录页面：GET 返回登录页，POST 验证账号密码并设置 session。"""
    if request.method == 'GET':
        return send_from_directory(PAGE_DIR, '登录.html')
    data = request.get_json(silent=True) or {}
    account = (data.get('account') or '').strip()
    password = data.get('password') or ''
    from controller_auth import verify_user, update_login_status
    result = verify_user(account, password)
    if result.get('ok'):
        session.permanent = True
        session['account'] = result['account']
        session['login_time'] = datetime.now().isoformat()
        update_login_status(result['account'], online=True)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': result.get('error', '登录失败')}), 401


@app.route('/logout')
def logout():
    """退出登录：清除 session 并重定向到登录页。"""
    account = session.get('account')
    if account:
        from controller_auth import update_login_status
        update_login_status(account, online=False)
    session.clear()
    return redirect(url_for('login_page'))


@app.route('/api/current_user')
def api_current_user():
    """返回当前登录账号（供前端导航栏展示）。"""
    account = session.get('account')
    return jsonify({'account': account})


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
    """返回 ETF 基础信息 + 最新交易日日线行情，支持按交易所/管理人/名称筛选。

    查询参数：
      exchange：交易所筛选（SH / SZ / 空为全部）
      manager ：管理人名称模糊搜索
      keyword ：ETF 简称/跟踪指数名模糊搜索
    响应：
      {trade_date, count, etfs: [{ts_code, csname, index_name, mgr_name,
                                  list_date, etf_type, close, pct_chg,
                                  vol, amount, pre_close, change}]}
    """
    exchange = (request.args.get('exchange') or '').strip().upper()
    keyword = (request.args.get('keyword') or '').strip()
    manager = (request.args.get('manager') or '').strip()

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
            if manager:
                sql += " AND b.mgr_name LIKE %s"
                params.append(f'%{manager}%')
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


@app.route('/api/etf_managers')
def api_etf_managers():
    """返回 ETF 管理人去重列表（按旗下 ETF 数量降序），供筛选框下拉提示。"""
    conn = get_mysql_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败'}), 503
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT mgr_name AS name, COUNT(*) AS cnt
                FROM etf_basic_t
                WHERE mgr_name IS NOT NULL AND mgr_name != ''
                GROUP BY mgr_name ORDER BY cnt DESC, name ASC
            """)
            rows = cur.fetchall() or []
            return jsonify({'managers': rows})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        close_connection(conn)


@app.route('/backtest')
def backtest_page():
    """策略选股结果查询页面。"""
    return send_from_directory(PAGE_DIR, '策略选股结果.html')


@app.route('/backtest_analysis')
def backtest_analysis_page():
    """策略回测页面（开发中）。"""
    return send_from_directory(PAGE_DIR, '策略回测.html')


@app.route('/my_stocks')
def my_stocks_page():
    """我的股票池页面。"""
    return send_from_directory(PAGE_DIR, '我的股票池.html')


@app.route('/api/my_stocks')
def api_my_stocks():
    """查询用户股票池中所有股票的区间收益指标。

    仅做请求转发与响应，业务逻辑由 controller_my_stocks.get_account_stocks() 封装。
    """
    account = request.args.get('account') or session.get('account') or ''

    from controller_my_stocks import get_account_stocks
    result = get_account_stocks(account)

    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route('/api/my_stocks/add', methods=['POST'])
def api_my_stocks_add():
    """批量将股票加入用户股票池。

    仅做请求转发与响应，业务逻辑由 controller_my_stocks.add_to_my_stocks() 封装。

    请求体：{account: 'luckboy', stocks: [{ts_code, stock_name, selected_date, strategy_name}, ...]}
    """
    data = request.get_json(silent=True) or {}
    account = (data.get('account') or session.get('account') or '').strip()
    stocks = data.get('stocks') or []

    from controller_my_stocks import add_to_my_stocks
    result = add_to_my_stocks(account, stocks)

    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route('/api/my_stocks/remove', methods=['POST'])
def api_my_stocks_remove():
    """从用户股票池移除股票。仅做请求转发与响应。

    请求体：{ts_codes: [ts_code, ...]}（account 取当前登录 session）
    """
    data = request.get_json(silent=True) or {}
    account = (data.get('account') or session.get('account') or '').strip()
    ts_codes = data.get('ts_codes') or []

    from controller_my_stocks import remove_from_my_stocks
    result = remove_from_my_stocks(account, ts_codes)

    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route('/stock')
def stock_detail():
    """返回 pages/ 下的个股K线详情页（股票代码由 ?code= 提供，前端解析）。"""
    return send_from_directory(PAGE_DIR, '个股详情.html')


@app.route('/stock-detail')
def stock_detail_page():
    """个股数据详情页：展示单只股票某交易日的完整字段快照。"""
    return send_from_directory(PAGE_DIR, '个股数据详情.html')


@app.route('/api/stock_detail')
def api_stock_detail():
    """返回单股单日完整字段快照 JSON（成交额/总市值/流通市值已换算为亿元，保留1位小数）。"""
    code = request.args.get('code', '').strip()
    date = request.args.get('date', '').strip()
    if not code:
        return jsonify({'error': '缺少 code 参数'}), 400
    if not date:
        return jsonify({'error': '缺少 date 参数'}), 400
    from module_query_stock_detail import get_stock_detail
    row = get_stock_detail(code, date)
    if not row:
        return jsonify({'error': f'未找到 {code} 在 {date} 的数据'}), 404
    # 单位换算：amount 千元→亿元；total_mv/circ_mv 万元→亿元（均保留1位小数）
    def _yi_yi(kv, divisor):  # 通用：千元/万元 → 亿元
        return round(kv / divisor, 1) if kv is not None else None
    return jsonify({
        'ts_code':              row['ts_code'],
        'stock_name':           row['stock_name'],
        'trade_date':           row['trade_date'],
        'short_strength_score': row['short_strength_score'],
        'open':                 row['open'],
        'close':                row['close'],
        'pct_chg':              row['pct_chg'],
        'amount_yi':            _yi_yi(row['amount'], 100000),
        'turnover_rate_f':      row['turnover_rate_f'],
        'pe':                   row['pe'],
        'pe_ttm':               row['pe_ttm'],
        'pb':                   row['pb'],
        'total_mv_yi':          _yi_yi(row['total_mv'], 10000),
        'circ_mv_yi':           _yi_yi(row['circ_mv'], 10000),
        'dv_ttm':               row['dv_ttm'],
        'ma5':                  row['ma5'],
        'ma30':                 row['ma30'],
    })


@app.route('/api/stock_search')
def api_stock_search():
    """股票代码/名称联想补全，返回 {suggestions: [{ts_code, stock_name}, ...]}。"""
    q = request.args.get('q', '').strip()
    from module_query_stock_detail import search_stocks
    return jsonify({'suggestions': search_stocks(q)})


@app.route('/api/stock_latest_date')
def api_stock_latest_date():
    """返回该股最近一个交易日（YYYYMMDD），用于详情页日期框初始值/max。"""
    code = request.args.get('code', '').strip()
    if not code:
        return jsonify({'error': '缺少 code 参数'}), 400
    from module_query_stock_detail import get_latest_trade_date
    return jsonify({'latest_date': get_latest_trade_date(code)})


@app.route('/api/stock_list')
def api_stock_list():
    """返回某交易日全市场股票列表 JSON（成交额/总市值/流通市值已换算为亿元，保留1位小数）。
    前端按总市值/股息率/换手率/成交额区间过滤、按字段排序、分页展示（每页30条）。"""
    date = request.args.get('date', '').strip()
    if not date:
        # 未传 date 时自动取全市场最新交易日
        from module_query_stock_detail import get_global_latest_trade_date
        date = get_global_latest_trade_date()
        if not date:
            return jsonify({'error': '暂无行情数据'}), 404
    from module_query_stock_detail import get_stock_list
    rows = get_stock_list(date)
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500
    def _yi(v, divisor):  # 千元/万元 → 亿元（保留1位）
        return round(v / divisor, 1) if v is not None else None
    items = [{
        'ts_code':              r['ts_code'],
        'stock_name':           r['stock_name'],
        'trade_date':           r['trade_date'],
        'short_strength_score': r['short_strength_score'],
        'open':                 r['open'],
        'close':                r['close'],
        'pct_chg':              r['pct_chg'],
        'amount_yi':            _yi(r['amount'], 100000),
        'turnover_rate_f':      r['turnover_rate_f'],
        'pe':                   r['pe'],
        'pe_ttm':               r['pe_ttm'],
        'pb':                   r['pb'],
        'total_mv_yi':          _yi(r['total_mv'], 10000),
        'circ_mv_yi':           _yi(r['circ_mv'], 10000),
        'dv_ttm':               r['dv_ttm'],
        'ma5':                  r['ma5'],
        'ma30':                 r['ma30'],
    } for r in rows]
    return jsonify({'trade_date': date, 'count': len(items), 'items': items})



@app.route('/cron')
def cron_monitor():
    """任务运行监控页：展示当天定时任务（run_daily_stock_tasks.sh）执行情况。"""
    return send_from_directory(PAGE_DIR, '任务运行监控.html')


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
        from monitor_stock_data import collect_data, _json_default
        start_date = (request.args.get('start_date') or '').strip()
        end_date = (request.args.get('end_date') or '').strip()
        if start_date and end_date:
            data = collect_data(start_date=start_date, end_date=end_date)
        else:
            data = collect_data(days=10)
        # batch_duration_sec 等 DECIMAL 字段经 _json_default 转为 float
        return app.response_class(
            json.dumps(data, ensure_ascii=False, default=_json_default),
            mimetype='application/json')
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/mri')
def mri_dashboard():
    """A股大盘风险指数（MRI 实时仪表盘），由 report_market_risk_metricx.py 生成。"""
    path = os.path.join(PAGE_DIR, 'A股大盘风险指数MRI.html')
    if not os.path.exists(path):
        return ('<!DOCTYPE html><meta charset="utf-8">'
                '<title>MRI 仪表盘未生成</title><body style="font-family:sans-serif;padding:40px">'
                '<h2>MRI 实时仪表盘尚未生成</h2>'
                '<p>请先运行 <code>python report_market_risk_metricx.py</code> 生成报告。</p>'
                '<p><a href="/">返回首页</a></p></body>'), 404
    return send_from_directory(PAGE_DIR, 'A股大盘风险指数MRI.html')


@app.route('/sss/framework')
def sss_framework_page():
    """股票短期强弱指标体系说明页面。"""
    return send_from_directory(PAGE_DIR, '股票短期强弱指标体系说明.html')


@app.route('/mri/framework')
def mri_framework():
    """A股大盘风险指标体系说明页。"""
    return send_from_directory(PAGE_DIR, 'A股大盘风险指标体系说明.html')


@app.route('/market-overview')
def market_overview():
    """大盘指标概览页（15 指标仪表盘），由 report_market_overview_metricx.py 生成。"""
    path = os.path.join(PAGE_DIR, '大盘指标概览.html')
    if not os.path.exists(path):
        return ('<!DOCTYPE html><meta charset="utf-8">'
                '<title>大盘指标概览未生成</title><body style="font-family:sans-serif;padding:40px">'
                '<h2>大盘指标概览页面尚未生成</h2>'
                '<p>请先运行 <code>python report_market_overview_metricx.py</code> 生成数据。</p>'
                '<p><a href="/">返回首页</a></p></body>'), 404
    return send_from_directory(PAGE_DIR, '大盘指标概览.html')


@app.route('/api/market_overview_metrics')
def api_market_overview_metrics():
    """返回大盘指标体系数据 JSON。

    无 date 参数：返回最新一次计算生成的 market_overview_metrics.json；
    带 date=YYYYMMDD：从指标快照表 market_overview_metric_daily_t 查询指定交易日。
    """
    date = (request.args.get('date') or '').strip()
    if date:
        if not re.fullmatch(r'\d{8}', date):
            return jsonify({'error': '日期格式应为 YYYYMMDD'}), 400
        conn = get_mysql_connection()
        if not conn:
            return jsonify({'error': '数据库连接失败'}), 500
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT metric_key, metric_group, metric_name, metric_value, metric_unit, "
                "metric_source, metric_status, metric_note, update_time "
                "FROM market_overview_metric_daily_t WHERE trade_date = %s "
                "ORDER BY metric_key", (date,))
            rows = cur.fetchall()
            cur.close()
        finally:
            close_connection(conn)
        if not rows:
            return jsonify({'error': f'{date} 暂无指标数据（该日可能未运行计算或非交易日）'}), 404
        metrics = [{
            'key': r['metric_key'], 'group': r['metric_group'], 'name': r['metric_name'],
            'value': float(r['metric_value']) if r['metric_value'] is not None else None,
            'unit': r['metric_unit'], 'source': r['metric_source'] or '',
            'status': r['metric_status'] or 'ok', 'note': r['metric_note'] or '',
        } for r in rows]
        ok_count = sum(1 for m in metrics if m['status'] == 'ok')
        updated = rows[0]['update_time']
        return jsonify({
            'trade_date': date,
            'generated_at': updated.strftime('%Y-%m-%d %H:%M:%S') if updated else '',
            'ok_count': ok_count, 'total_count': len(metrics), 'metrics': metrics,
        })

    # 最新：读计算脚本生成的 JSON
    path = os.path.join(PAGE_DIR, 'market_overview_metrics.json')
    if not os.path.exists(path):
        return jsonify({'error': '数据尚未生成，请先点击「刷新数据」'}), 404
    with open(path, encoding='utf-8') as f:
        return jsonify(json.load(f))


@app.route('/api/market_overview_charts')
def api_market_overview_charts():
    """返回大盘指标概览页走势图数据：basis（期现差 IF/IC/IM）+ rzrq（融资融券时序）。

    数据来源：report_market_overview_metricx.py 写入的 market_overview_metrics.json，
    不再依赖旧的 market_overall_data.json / report_market_overall_html.py。
    """
    path = os.path.join(PAGE_DIR, 'market_overview_metrics.json')
    if not os.path.exists(path):
        return jsonify({'error': '数据尚未生成，请先运行 report_market_overview_metricx.py'}), 404
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return jsonify({
        'basis': data.get('basis', {}),
        'rzrq': data.get('rzrq', {}),
    })


@app.route('/api/market_overview_metrics_dates')
def api_market_overview_metrics_dates():
    """返回指标快照表中已有数据的交易日列表（降序，最多120个），供日期选择框限定范围。"""
    conn = get_mysql_connection()
    if not conn:
        return jsonify({'dates': []})
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT trade_date FROM market_overview_metric_daily_t "
            "ORDER BY trade_date DESC LIMIT 120")
        dates = [str(r['trade_date']) for r in cur.fetchall()]
        cur.close()
    finally:
        close_connection(conn)
    return jsonify({'dates': dates})


@app.route('/api/market_overview_metric_history')
def api_market_overview_metric_history():
    """返回单个指标最近 N 个交易日的历史值（指标行点击展开走势图用）。

    参数：key=指标编号（如 A1），days=交易日数（默认60，上限250）。
    """
    key = (request.args.get('key') or '').strip()
    if not re.fullmatch(r'[A-C]\d', key):
        return jsonify({'error': '指标编号不合法'}), 400
    try:
        days = min(max(int(request.args.get('days', 60)), 1), 250)
    except (TypeError, ValueError):
        days = 60

    conn = get_mysql_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败'}), 500
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT trade_date, metric_name, metric_unit, metric_value, metric_status "
            "FROM market_overview_metric_daily_t WHERE metric_key = %s "
            "ORDER BY trade_date DESC LIMIT %s", (key, days))
        rows = list(reversed(cur.fetchall()))
        cur.close()
    finally:
        close_connection(conn)
    if not rows:
        return jsonify({'error': f'{key} 暂无历史数据'}), 404
    points = [{
        'trade_date': str(r['trade_date']),
        'value': float(r['metric_value']) if r['metric_value'] is not None else None,
        'status': r['metric_status'] or 'ok',
    } for r in rows]
    return jsonify({
        'key': key,
        'name': rows[-1]['metric_name'],
        'unit': rows[-1]['metric_unit'] or '',
        'points': points,
    })


# 大盘指标概览刷新（后台运行 report_market_overview_metricx.py）
_MARKET_OVERVIEW_SCRIPT = 'report_market_overview_metricx.py'
_market_overview_state = {'status': 'idle'}
_market_overview_lock = threading.Lock()


def _reap_market_overview_process(proc, started):
    rc = proc.wait()
    ended = datetime.now()
    with _market_overview_lock:
        _market_overview_state.update(
            status='success' if rc == 0 else 'failed',
            end=ended.strftime('%Y-%m-%d %H:%M:%S'), rc=rc,
            duration=round((ended - started).total_seconds(), 1))


@app.route('/api/market_overview_refresh', methods=['POST'])
def api_market_overview_refresh():
    """立即刷新大盘指标体系：后台运行 report_market_overview_metricx.py 重算数据。"""
    with _market_overview_lock:
        if _market_overview_state.get('status') == 'running':
            return jsonify({'error': '数据正在计算中，请勿重复触发',
                            'state': dict(_market_overview_state)}), 409
        script_path = os.path.join(BASE_DIR, _MARKET_OVERVIEW_SCRIPT)
        if not os.path.isfile(script_path):
            return jsonify({'error': '脚本不存在'}), 404
        started = datetime.now()
        try:
            proc = subprocess.Popen([sys.executable, script_path], cwd=BASE_DIR)
        except OSError as e:
            return jsonify({'error': f'启动失败: {e}'}), 500
        _market_overview_state.update(status='running',
                                      start=started.strftime('%Y-%m-%d %H:%M:%S'),
                                      end=None, rc=None, duration=None, pid=proc.pid)
    threading.Thread(target=_reap_market_overview_process, args=(proc, started), daemon=True).start()
    return jsonify({'ok': True, 'state': dict(_market_overview_state)}), 202


@app.route('/api/market_overview_refresh_status')
def api_market_overview_refresh_status():
    """查询大盘指标体系计算状态。"""
    with _market_overview_lock:
        return jsonify(dict(_market_overview_state))


@app.route('/api/mri_data')
def api_mri_data():
    """返回 MRI 风险指数数据 JSON（由 report_market_risk_metricx.py 生成到 pages/mri_data.json）。"""
    path = os.path.join(PAGE_DIR, 'mri_data.json')
    if not os.path.exists(path):
        return jsonify({'error': '数据尚未生成，请先点击「重新生成报告」'}), 404
    with open(path, encoding='utf-8') as f:
        return jsonify(json.load(f))


# MRI 报告重新生成（单任务，复用手动执行状态管理）
_MRI_SCRIPT = 'report_market_risk_metricx.py'
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
    """后台重新生成 MRI 报告（运行 report_market_risk_metricx.py）。"""
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
# 整体单次执行：run_daily_stock_tasks.sh（19 步全流程编排，等同定时调度手动跑一次）
# ---------------------------------------------------------------------------

_RUN_ALL_SCRIPT = 'run_daily_stock_tasks.sh'
_run_all_state = {'status': 'idle'}   # idle | running | success | failed
_run_all_lock = threading.Lock()


def _reap_run_all_process(proc, started, log_fp):
    """后台等待全量编排脚本结束并回写状态。"""
    rc = proc.wait()
    log_fp.close()
    ended = datetime.now()
    with _run_all_lock:
        _run_all_state.update(status='success' if rc == 0 else 'failed',
                              end=ended.strftime('%Y-%m-%d %H:%M:%S'), rc=rc,
                              duration=round((ended - started).total_seconds(), 1))


@app.route('/api/cron_run_all', methods=['POST'])
def api_cron_run_all():
    """后台整体单次执行 run_daily_stock_tasks.sh（19 步全流程）。

    脚本自身将各步骤日志写入 cron_logs/，并清空重建该目录；
    包装层 stdout（编排日志 tee 输出）额外落盘到 manual_logs/ 便于在线查看。
    """
    with _run_all_lock:
        if _run_all_state.get('status') == 'running':
            return jsonify({'error': '全量任务正在执行中，请勿重复触发',
                            'state': dict(_run_all_state)}), 409
        script_path = os.path.join(BASE_DIR, _RUN_ALL_SCRIPT)
        if not os.path.isfile(script_path):
            return jsonify({'error': '编排脚本不存在'}), 404

        os.makedirs(MANUAL_LOG_DIR, exist_ok=True)
        started = datetime.now()
        log_name = 'manual_run_all_%s.log' % started.strftime('%Y%m%d_%H%M%S')
        log_fp = open(os.path.join(MANUAL_LOG_DIR, log_name), 'a', encoding='utf-8')
        try:
            proc = subprocess.Popen(['/bin/bash', script_path], cwd=BASE_DIR,
                                    stdout=log_fp, stderr=subprocess.STDOUT)
        except OSError as e:
            log_fp.close()
            return jsonify({'error': f'启动失败: {e}'}), 500
        _run_all_state.clear()
        _run_all_state.update(status='running',
                              start=started.strftime('%Y-%m-%d %H:%M:%S'),
                              end=None, rc=None, duration=None,
                              log=log_name, pid=proc.pid)

    threading.Thread(target=_reap_run_all_process,
                     args=(proc, started, log_fp), daemon=True).start()
    return jsonify({'ok': True, 'state': dict(_run_all_state)}), 202


@app.route('/api/cron_run_all_status')
def api_cron_run_all_status():
    """查询全量编排脚本的执行状态。"""
    with _run_all_lock:
        return jsonify(dict(_run_all_state))


# ---------------------------------------------------------------------------
# 选股策略手动执行（独立于 cron 任务清单，按 select_*.py 文件系统扫描）
# ---------------------------------------------------------------------------

@app.route('/api/strategies_run', methods=['POST'])
def api_strategies_run():
    """批量手动执行选股策略脚本：逐个在后台执行，返回每个脚本的启动结果。

    请求体：{scripts: ['select_limitup_1d.py', ...], target_date?: 'YYYYMMDD'}
    脚本白名单：select_*.py（条件选股）和 find_similar_*.py（以股选股）。
    响应：{results: [{script, ok, state?, error?}]}
    """
    payload = request.get_json(silent=True) or {}
    scripts = payload.get('scripts') or []
    if not isinstance(scripts, list) or not scripts:
        return jsonify({'error': 'scripts 参数不能为空'}), 400

    # 校验脚本名：必须是 select_*.py 或 find_similar_*.py 且文件存在
    valid_files = ({os.path.basename(p) for p in glob.glob(os.path.join(BASE_DIR, 'select_*.py'))}
                   | {os.path.basename(p) for p in glob.glob(os.path.join(BASE_DIR, 'find_similar_*.py'))})
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
_TITLE_FILE_SUFFIX = re.compile(r'[（(]\s*(?:select_|find_similar_)[^）)]*\.py\s*[）)]')
# docstring 首行形如 "选股策略: 当日涨停选股策略"，去掉前缀只留策略名
_TITLE_PREFIX = re.compile(r'^选股策略[：:]\s*')
_strategy_meta_cache = {}   # {文件mtime: 元数据}，select/find_similar 文件运行期不变，缓存一次即可


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
    title = _TITLE_PREFIX.sub('', _TITLE_FILE_SUFFIX.sub('', lines[0])).strip() if lines else filename
    # 标题行之后的全部内容作为展开区文案（含选股条件、输出约定等）
    body = '\n'.join(lines[1:]).strip()
    return {
        'file': filename,
        'strategy': strategy_name,
        'title': title,
        'doc': body,
    }


def _load_strategies_meta():
    """扫描全部 select_*.py 和 find_similar_*.py，按类别+文件名排序，结果按文件 mtime 缓存。"""
    select_paths = sorted(glob.glob(os.path.join(BASE_DIR, 'select_*.py')))
    find_paths = sorted(glob.glob(os.path.join(BASE_DIR, 'find_similar_*.py')))
    all_paths = select_paths + find_paths
    sig = tuple((p, os.path.getmtime(p)) for p in all_paths)
    if _strategy_meta_cache.get('sig') != sig:
        metas = []
        for p in select_paths:
            m = _parse_strategy_file(p)
            if m:
                m['category'] = '条件选股'
                metas.append(m)
        for p in find_paths:
            m = _parse_strategy_file(p)
            if m:
                m['category'] = '以股选股'
                metas.append(m)
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


@app.route('/api/backtest_strategies')
def api_backtest_strategies():
    """返回已注册的可回测策略列表（来自 backtest_scanner.STRATEGY_REGISTRY）。"""
    from backtest_scanner import available_strategies, STRATEGY_REGISTRY
    names = available_strategies()
    strategies = []
    for n in names:
        reg = STRATEGY_REGISTRY[n]
        strategies.append({
            'name': n,
            'module': reg['module'],
            'min_lookback': reg['min_lookback'],
        })
    return jsonify({'strategies': strategies})


@app.route('/api/results')
def api_results():
    """按策略名称 + 日期范围查询回测结果。"""
    strategy = request.args.get('strategy', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()

    sql = """
        SELECT ts_code, stock_name, trade_date, strategy, selected,
               max_gain_10d, max_down_10d, max_gain_20d, max_down_20d,
               max_gain_to_date, max_down_to_date
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
        for k in ('max_gain_10d', 'max_down_10d', 'max_gain_20d', 'max_down_20d',
                  'max_gain_to_date', 'max_down_to_date'):
            if r[k] is not None:
                r[k] = float(r[k])
    return jsonify({'rows': rows, 'total': len(rows)})


# ---------------------------------------------------------------------------
# 策略回测：异步任务（后台 daemon 线程执行，切换页面不中断；完成后结果落库可直接查询）
# ---------------------------------------------------------------------------
# {task_id: {status, strategy, start_date, end_date, stage, progress,
#            start, end, duration, result, error, ...}}
_backtest_tasks = {}
_backtest_tasks_lock = threading.Lock()
_BACKTEST_TASKS_MAX = 20   # 内存中最多保留最近 20 个任务，超出清理最早的已结束任务
# task_id → threading.Event，用于通知守护线程中止扫描
_backtest_stop_events = {}


def _run_backtest_task(task_id, account, task_name, strategy,
                       start_date, end_date, strategy_file):
    """后台线程：扫描策略选股 → 计算回测指标 → 持久化到 strategy_backtest_result_t。

    任务状态/进度同步写入 backtest_task_t 任务注册表，服务重启后历史仍可查询。
    收到停止事件（Event）时在扫描交易日间隙退出，任务标记为 stopped。
    """
    from backtest_backend import (run_backtest, save_backtest_result,
                                update_backtest_task_progress,
                                set_backtest_task_stage,
                                finish_backtest_task)

    stop_event = _backtest_stop_events.get(task_id)

    def _progress_cb(done, total, current_date):
        with _backtest_tasks_lock:
            t = _backtest_tasks.get(task_id)
            if t:
                t['stage'] = 'scanning'
                t['progress'] = {'done': done, 'total': total,
                                 'current_date': current_date}
        update_backtest_task_progress(task_id, done, total, current_date, 'scanning')
        # 返回 True 通知扫描器中止
        return bool(stop_event and stop_event.is_set())

    started_dt = datetime.now()
    try:
        # 1. 扫描策略选股记录（直接扫 stock_daily_t，不依赖 strategy_selected_stock_daily_t）
        from backtest_scanner import scan_strategy
        scan_result = scan_strategy(strategy, start_date, end_date,
                                   progress_cb=_progress_cb)
        if isinstance(scan_result, dict) and scan_result.get('cancelled'):
            raise _TaskStopped()
        if isinstance(scan_result, dict) and scan_result.get('error'):
            raise RuntimeError(scan_result['error'])

        # 扫描结束后、开始计算前也响应停止
        if stop_event and stop_event.is_set():
            raise _TaskStopped()

        # 2. 计算回测指标
        with _backtest_tasks_lock:
            t = _backtest_tasks.get(task_id)
            if t:
                t['stage'] = 'computing'
        set_backtest_task_stage(task_id, 'computing')
        result = run_backtest(strategy, start_date, end_date, records=scan_result)
        if result.get('error'):
            raise RuntimeError(result['error'])

        # 3. 有明细数据时持久化到 strategy_backtest_result_t
        saved = False
        save_error = ''
        if result.get('detail'):
            save_info = save_backtest_result(result, strategy_file)
            saved = save_info.get('ok', False)
            if not saved:
                save_error = save_info.get('error', '')
        result['strategy_file'] = strategy_file
        result['saved'] = saved
        if save_error:
            result['save_error'] = save_error

        ended_dt = datetime.now()
        with _backtest_tasks_lock:
            t = _backtest_tasks.get(task_id)
            if t:
                t.update(status='done', stage='done',
                         end=ended_dt.strftime('%Y-%m-%d %H:%M:%S'),
                         duration=round((ended_dt - started_dt).total_seconds(), 1),
                         result=result)
        finish_backtest_task(task_id, 'done', saved=saved)
    except _TaskStopped:
        ended_dt = datetime.now()
        with _backtest_tasks_lock:
            t = _backtest_tasks.get(task_id)
            if t:
                t.update(status='stopped', stage='stopped',
                         end=ended_dt.strftime('%Y-%m-%d %H:%M:%S'),
                         duration=round((ended_dt - started_dt).total_seconds(), 1),
                         error='任务已停止')
        finish_backtest_task(task_id, 'stopped', error='任务已停止')
    except Exception as e:
        ended_dt = datetime.now()
        with _backtest_tasks_lock:
            t = _backtest_tasks.get(task_id)
            if t:
                t.update(status='failed', stage='failed',
                         end=ended_dt.strftime('%Y-%m-%d %H:%M:%S'),
                         duration=round((ended_dt - started_dt).total_seconds(), 1),
                         error=str(e))
        finish_backtest_task(task_id, 'failed', error=str(e))
    finally:
        _backtest_stop_events.pop(task_id, None)


class _TaskStopped(Exception):
    """内部信号：任务被用户停止。"""
    pass


def _backtest_task_public(t, include_result=False):
    """任务状态对外视图（去掉内部字段，明细结果按需携带）。"""
    if not t:
        return None
    view = {k: v for k, v in t.items() if k != 'result'}
    if include_result and t.get('result'):
        view['result'] = t['result']
    return view


@app.route('/api/backtest_analysis', methods=['POST'])
def api_backtest_analysis():
    """策略回测分析（异步）：按指定策略 + 日期范围扫描 stock_daily_t 找出符合条件的股票，
    后台计算回测指标并持久化到 strategy_backtest_result_t。

    立即返回 202 + task_id，前端通过 GET /api/backtest_task_status?task_id= 轮询。
    相同策略+日期范围的任务正在执行时直接复用，不重复启动。
    """
    data = request.get_json(silent=True) or {}
    strategy = (data.get('strategy') or '').strip() or None
    start_date = (data.get('start_date') or '').strip() or None
    end_date = (data.get('end_date') or '').strip() or None
    task_name = (data.get('task_name') or '').strip() or None

    if not strategy or not start_date or not end_date:
        return jsonify({'error': '请指定策略、起始日期和结束日期'}), 400
    if not task_name:
        return jsonify({'error': '请填写回测任务名称'}), 400

    # 任务归属当前登录账号，名称格式：账号-策略名_起始~结束
    account = session.get('account') or ''

    # 按策略名查找对应的策略 .py 文件
    strategy_file = None
    for m in _load_strategies_meta():
        if m.get('strategy') == strategy:
            strategy_file = m.get('file')
            break

    import uuid
    with _backtest_tasks_lock:
        # 相同参数的在途任务直接复用（防止重复扫描）
        for tid, t in _backtest_tasks.items():
            if (t.get('status') == 'running' and t.get('strategy') == strategy
                    and t.get('start_date') == start_date
                    and t.get('end_date') == end_date
                    and t.get('account') == account):
                return jsonify({'ok': True, 'task_id': tid,
                                'message': '相同参数的回测任务正在执行中'}), 202

        task_id = 'bt_' + datetime.now().strftime('%Y%m%d%H%M%S') + '_' + uuid.uuid4().hex[:8]
        started = datetime.now()
        _backtest_tasks[task_id] = {
            'task_id': task_id,
            'account': account,
            'task_name': task_name,
            'status': 'running',
            'stage': 'queued',
            'strategy': strategy,
            'start_date': start_date,
            'end_date': end_date,
            'strategy_file': strategy_file,
            'progress': None,
            'start': started.strftime('%Y-%m-%d %H:%M:%S'),
            'end': None,
            'duration': None,
            'result': None,
            'error': None,
        }
        _backtest_stop_events[task_id] = threading.Event()
        # 清理超出上限的最早已结束任务（done/failed/stopped）
        finished = [(tid, t) for tid, t in _backtest_tasks.items()
                    if t.get('status') in ('done', 'failed', 'stopped')]
        finished.sort(key=lambda x: x[1].get('end') or '')
        if len(finished) >= _BACKTEST_TASKS_MAX:
            for tid, _ in finished[:-_BACKTEST_TASKS_MAX + 1]:
                _backtest_tasks.pop(tid, None)
                _backtest_stop_events.pop(tid, None)

    # 任务注册落库（失败不阻断内存中的执行，仅记录提示）
    from backtest_backend import create_backtest_task
    create_backtest_task(task_id, account, task_name, strategy,
                       strategy_file, start_date, end_date)

    threading.Thread(target=_run_backtest_task,
                     args=(task_id, account, task_name, strategy,
                           start_date, end_date, strategy_file),
                     daemon=True).start()
    return jsonify({'ok': True, 'task_id': task_id}), 202


@app.route('/api/backtest_task_status')
def api_backtest_task_status():
    """查询单个回测异步任务状态（含完成后的完整回测结果）。

    优先读进程内任务（刚完成的内存任务携带完整结果）；
    进程内没有（如服务重启后）则读 backtest_task_t 注册表，
    已完成任务从 strategy_backtest_result_t 还原完整结果。
    """
    task_id = (request.args.get('task_id') or '').strip()
    if not task_id:
        return jsonify({'error': '缺少 task_id'}), 400
    with _backtest_tasks_lock:
        t = _backtest_tasks.get(task_id)
        if t:
            return jsonify(_backtest_task_public(t, include_result=True))

    # 进程内无任务 → 查数据库注册表
    from backtest_backend import get_backtest_task, load_saved_backtest_result
    task = get_backtest_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404

    if task['status'] == 'done':
        if task.get('saved'):
            result = load_saved_backtest_result(
                task['strategy'], task['start_date'], task['end_date'])
            if isinstance(result, dict) and not result.get('error'):
                task['result'] = result
        if 'result' not in task:
            # 完成但区间内无选股（无结果明细），给出空结果占位
            task['result'] = {
                'strategy': task['strategy'],
                'start_date': task['start_date'],
                'end_date': task['end_date'],
                'strategy_file': task.get('strategy_file'),
                'total_stocks': 0,
                'message': '回测区间未选出符合条件的股票，无明细数据',
                'saved': task.get('saved', False),
            }
    return jsonify(task)


@app.route('/api/backtest_task_stop', methods=['POST'])
def api_backtest_task_stop():
    """停止运行中的回测任务（仅任务所属账号可停止）。

    守护线程在扫描交易日间隙收到停止信号后退出，任务标记为 stopped；
    若服务已重启（线程不存在），直接将数据库中运行中任务标记为 stopped。
    """
    data = request.get_json(silent=True) or {}
    task_id = (data.get('task_id') or '').strip()
    if not task_id:
        return jsonify({'error': '缺少 task_id'}), 400

    account = session.get('account') or ''
    with _backtest_tasks_lock:
        t = _backtest_tasks.get(task_id)
        if t:
            if t.get('account') != account:
                return jsonify({'error': '无权停止他人任务'}), 403
            if t.get('status') != 'running':
                return jsonify({'ok': False, 'message': '任务不在运行中'}), 200
            ev = _backtest_stop_events.get(task_id)
            if ev:
                ev.set()
            t['stage'] = 'stopping'
            return jsonify({'ok': True, 'status': 'stopping'})

    # 进程内无任务（服务重启过）→ 校验账号归属后直接标记数据库
    from backtest_backend import get_backtest_task, mark_backtest_task_stopped
    task = get_backtest_task(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    if task.get('account') != account:
        return jsonify({'error': '无权停止他人任务'}), 403
    if task.get('status') != 'running':
        return jsonify({'ok': False, 'message': '任务不在运行中'}), 200
    affected = mark_backtest_task_stopped(task_id)
    return jsonify({'ok': bool(affected), 'status': 'stopped'})


@app.route('/api/backtest_tasks_recent')
def api_backtest_tasks_recent():
    """返回回测任务列表（读 backtest_task_t 注册表，重启不丢失）。

    查询参数：
      account     ：账号筛选，缺省为当前登录账号
      strategy/start_date/end_date：策略与日期范围筛选
    """
    from backtest_backend import list_backtest_tasks
    account = (request.args.get('account') or '').strip() \
        or session.get('account') or ''
    result = list_backtest_tasks(
        account=account,
        strategy=(request.args.get('strategy') or '').strip() or None,
        start_date=(request.args.get('start_date') or '').strip() or None,
        end_date=(request.args.get('end_date') or '').strip() or None,
    )
    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)


@app.route('/api/backtest_accounts')
def api_backtest_accounts():
    """返回提交过回测任务的账号去重列表，供任务列表账号筛选下拉。"""
    from backtest_backend import list_backtest_accounts
    result = list_backtest_accounts()
    if 'error' in result:
        return jsonify(result), 500
    result['current'] = session.get('account') or ''
    return jsonify(result)


def _fmt_date(d):
    """YYYYMMDD → YYYY-MM-DD（供前端 x 轴与 markLine 对齐使用）。"""
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else d


@app.route('/api/backtest_results')
def api_backtest_results():
    """查询已保存的回测结果（strategy_backtest_result_t），按策略名 + 日期筛选。

    参数：
      strategy:  策略名称（精确匹配 strategy_name，空则全部）
      start_date: 起始日期 YYYYMMDD（筛选回测的 start_date >= 该值）
      end_date:   结束日期 YYYYMMDD（筛选回测的 end_date <= 该值）

    返回：
      {runs: [{strategy_name, strategy_file, start_date, end_date, total_stocks,
              valid_stocks, total_dates, avg_gain_10d, ... sharpe, run_time, ...}],
       total: N}
    """
    strategy = request.args.get('strategy', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    sql = """
        SELECT DISTINCT
          strategy_name, strategy_file, start_date, end_date,
          total_stocks, valid_stocks, total_dates,
          avg_gain_10d, avg_gain_20d, avg_down_10d, avg_down_20d,
          excess_sh, excess_sz, sharpe, sh_index_10d, sz_index_10d,
          run_time, update_time
        FROM strategy_backtest_result_t
        WHERE 1=1
    """
    params = []
    if strategy:
        sql += " AND strategy_name = %s"
        params.append(strategy)
    if start_date:
        sql += " AND start_date >= %s"
        params.append(start_date)
    if end_date:
        sql += " AND end_date <= %s"
        params.append(end_date)
    sql += " ORDER BY run_time DESC"

    rows = query_db(sql, params)
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500

    # Decimal/datetime → 可 JSON 序列化
    for r in rows:
        for k in ('avg_gain_10d', 'avg_gain_20d', 'avg_down_10d', 'avg_down_20d',
                  'excess_sh', 'excess_sz', 'sharpe', 'sh_index_10d', 'sz_index_10d'):
            if r.get(k) is not None:
                r[k] = float(r[k])
        if r.get('run_time'):
            r['run_time'] = str(r['run_time'])
        if r.get('update_time'):
            r['update_time'] = str(r['update_time'])

    return jsonify({'runs': rows, 'total': len(rows)})


@app.route('/api/backtest_results_detail')
def api_backtest_results_detail():
    """查询某次回测的逐股明细。

    参数：
      strategy:  策略名称
      start_date: 回测起始日期
      end_date:   回测结束日期
    """
    strategy = request.args.get('strategy', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()

    sql = """
        SELECT ts_code, stock_name, trade_date,
               max_gain_10d, max_down_10d, max_gain_20d, max_down_20d
        FROM strategy_backtest_result_t
        WHERE strategy_name = %s AND start_date = %s AND end_date = %s
        ORDER BY trade_date DESC, ts_code
    """
    rows = query_db(sql, [strategy, start_date, end_date])
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500

    for r in rows:
        for k in ('max_gain_10d', 'max_down_10d', 'max_gain_20d', 'max_down_20d'):
            if r.get(k) is not None:
                r[k] = float(r[k])

    return jsonify({'rows': rows, 'total': len(rows)})


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


@app.route('/api/sss_batch', methods=['POST'])
def api_sss_batch():
    """批量计算股票短期强弱评分（SSS 5 维指标）。

    请求体：{"codes": ["000001.SZ", ...]}
    响应：{"scores": {"000001.SZ": {"composite": 56.8, "rps": 83.1, ...}, ...}}

    仅做请求转发，业务逻辑由 module_stock_short_strength 封装。
    """
    data = request.get_json(silent=True) or {}
    codes = data.get('codes') or []
    if not codes or not isinstance(codes, list):
        return jsonify({'scores': {}})

    from module_stock_short_strength import (
        fetch_one, fetch_benchmark, safe_pct_change, calc_all_scores,
        PARAMS,
    )

    # 基准只取一次
    try:
        bm = fetch_benchmark(PARAMS['benchmark'], days=PARAMS['lookback'] + 60)
        bm_ret = safe_pct_change(bm, PARAMS['lookback'])
    except Exception:
        bm_ret = float('nan')

    scores = {}
    for code in codes[:200]:   # 上限 200 只，防止过大请求
        code = (code or '').strip()
        if not code:
            continue
        try:
            df = fetch_one(code, days=PARAMS['lookback'] + 60)
            r = calc_all_scores(df, bm_ret, code=code)
            scores[code] = {
                'composite': round(r.composite, 1) if r.composite is not None else None,
                'rps': round(r.rps, 1) if r.rps is not None else None,
                'vpvr': round(r.vpvr, 1) if r.vpvr is not None else None,
                'trend': round(r.trend, 1) if r.trend is not None else None,
                'msr': round(r.msr, 1) if r.msr is not None else None,
                'mfi': round(r.mfi, 1) if r.mfi is not None else None,
                'level': r.level,
            }
        except Exception:
            scores[code] = None
    return jsonify({'scores': scores})


# ---------------------------------------------------------------------------
# 新建策略页面与提交接口
# ---------------------------------------------------------------------------

STRATEGY_DESC_DIR = os.path.join(BASE_DIR, 'pages', 'strategy_description')


@app.route('/strategy/new')
def page_strategy_new():
    """新建策略页面：输入策略名称+描述并提交。"""
    return send_from_directory(PAGE_DIR, '新建策略.html')


@app.route('/strategy/my')
def page_strategy_my():
    """我的策略页面：展示当前账号提交的策略列表，支持查看与编辑回填。"""
    return send_from_directory(PAGE_DIR, '我的策略.html')


@app.route('/api/strategy_submit', methods=['POST'])
def api_strategy_submit():
    """接收策略类型+名称+描述，保存为 Markdown 文件到 pages/strategy_description/ 目录。

    请求体：{type: '条件选股'|'以股选股', name: '策略名称', description: '策略描述Markdown'}
    文件名：账号-策略类型-策略名称-YYYYMMDD.md；同账号同类型同名策略重新提交时覆盖旧文件
    """
    import re as _re
    import glob as _glob
    payload = request.get_json(silent=True) or {}
    # 策略类型白名单，默认条件选股
    ALLOWED_TYPES = ('条件选股', '以股选股')
    strategy_type = (payload.get('type') or '条件选股').strip()
    if strategy_type not in ALLOWED_TYPES:
        return jsonify({'error': '策略类型不合法，仅支持：条件选股、以股选股'}), 400
    name = (payload.get('name') or '').strip()
    description = payload.get('description') or ''
    if not name:
        return jsonify({'error': '策略名称不能为空'}), 400
    if not description.strip():
        return jsonify({'error': '策略描述不能为空'}), 400

    def _safe(seg, default):
        # 清理文件名片段：只保留中文、字母、数字、下划线、连字符
        seg = _re.sub(r'[^\w一-鿿\-]', '_', seg).strip('_')
        return seg or default

    account = _safe(session.get('account') or '', 'unknown')
    safe_type = _safe(strategy_type, '条件选股')
    safe_name = _safe(name, 'strategy')
    date_str = datetime.now().strftime('%Y%m%d')
    filename = f'{account}-{safe_type}-{safe_name}-{date_str}.md'
    os.makedirs(STRATEGY_DESC_DIR, exist_ok=True)
    # 删除同账号-同类型-同名称的旧文件，实现同名策略覆盖
    old_pattern = os.path.join(STRATEGY_DESC_DIR, f'{account}-{safe_type}-{safe_name}-*.md')
    for old_file in _glob.glob(old_pattern):
        try:
            os.remove(old_file)
        except OSError:
            pass
    filepath = os.path.join(STRATEGY_DESC_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f'# {name}\n\n'
                f'> 提交账号：{session.get("account") or account}\n'
                f'> 策略类型：{strategy_type}\n'
                f'> 提交日期：{datetime.now().strftime("%Y-%m-%d")}\n\n'
                f'{description}\n')
    return jsonify({'ok': True, 'filename': filename}), 201


def _safe_filename_segment(seg, default):
    """文件名片段清理：只保留中文、字母、数字、下划线、连字符。"""
    seg = re.sub(r'[^\w一-鿿\-]', '_', seg).strip('_')
    return seg or default


def _parse_strategy_filename(account, filename):
    """解析「账号-策略类型-策略名称-YYYYMMDD.md」文件名。

    返回 dict(type, name, date) 或 None（不属于该账号/格式不符）。
    """
    import os as _os
    base = _os.path.basename(filename)
    if not base.endswith('.md'):
        return None
    stem = base[:-3]
    prefix = account + '-'
    if not stem.startswith(prefix):
        return None
    rest = stem[len(prefix):]
    # 末尾固定 -YYYYMMDD
    m = re.match(r'^(.+)-(\d{8})$', rest)
    if not m:
        return None
    middle, date_str = m.group(1), m.group(2)
    # 类型只有两种，按前缀匹配
    stype = None
    for t in ('条件选股', '以股选股'):
        if middle == t or middle.startswith(t + '-'):
            stype = t
            break
    if stype is None:
        return None
    name = middle[len(stype) + 1:] if len(middle) > len(stype) + 1 else ''
    return {'type': stype, 'name': name, 'date': date_str}


@app.route('/api/my_strategies')
def api_my_strategies():
    """列出当前登录账号提交的全部策略（文件名以「账号-」前缀过滤），按日期降序。"""
    import glob as _glob
    import os as _os
    account = (session.get('account') or '').strip()
    if not account:
        return jsonify({'error': '请先登录'}), 401
    safe_account = _safe_filename_segment(account, '')
    if not safe_account:
        return jsonify({'strategies': []})
    items = []
    for fp in _glob.glob(_os.path.join(STRATEGY_DESC_DIR, safe_account + '-*.md')):
        base = _os.path.basename(fp)
        info = _parse_strategy_filename(safe_account, base)
        if not info:
            continue
        items.append({
            'filename': base,
            'type': info['type'],
            'name': info['name'],
            'date': info['date'],
        })
    items.sort(key=lambda x: (x['date'], x['type'], x['name']), reverse=True)
    return jsonify({'strategies': items})


@app.route('/api/my_strategy')
def api_my_strategy():
    """读取当前账号名下单个策略文件内容（供编辑回填）。

    参数：filename=账号-类型-名称-日期.md（basename，强制账号前缀校验防穿越）。
    返回：{type, name, date, description}，description 已剥离自动生成的文件头。
    """
    import os as _os
    account = (session.get('account') or '').strip()
    if not account:
        return jsonify({'error': '请先登录'}), 401
    safe_account = _safe_filename_segment(account, '')
    filename = (request.args.get('filename') or '').strip()
    if not re.fullmatch(r'[\w一-鿿\-]+\.md', filename):
        return jsonify({'error': '文件名不合法'}), 400
    info = _parse_strategy_filename(safe_account, filename)
    if not info:
        return jsonify({'error': '策略不存在或无权访问'}), 404
    filepath = _os.path.join(STRATEGY_DESC_DIR, _os.path.basename(filename))
    if not _os.path.isfile(filepath):
        return jsonify({'error': '策略文件不存在'}), 404
    with open(filepath, encoding='utf-8') as f:
        content = f.read()
    # 剥离自动生成的头部：# 标题 与若干 > 元信息行，保留正文描述
    body = re.sub(r'^#[^\n]*\n+(?:>[^\n]*\n+)*', '', content, count=1).strip()
    return jsonify({
        'filename': filename,
        'type': info['type'],
        'name': info['name'],
        'date': info['date'],
        'description': body,
    })


# ---------------------------------------------------------------------------
# 以股选股（find_similar_*.py）页面与执行接口
# ---------------------------------------------------------------------------

_find_similar_state = {}   # {script: {status, start, end, rc, duration, log, pid, params}}


@app.route('/find_similar')
def find_similar_page():
    """以股选股页面：参数输入 + 模板/相似股票 K 线展示。"""
    script = request.args.get('script', '').strip()
    return send_from_directory(PAGE_DIR, '以股选股.html')


@app.route('/api/find_similar_run', methods=['POST'])
def api_find_similar_run():
    """后台执行 find_similar_*.py 脚本，返回启动结果。

    请求体：{script: 'find_similar_ma5.py', args: {target: '301171.SZ', ...}}
    """
    payload = request.get_json(silent=True) or {}
    script = (payload.get('script') or '').strip()
    if not re.fullmatch(r'find_similar_[A-Za-z0-9_]+\.py', script):
        return jsonify({'error': '脚本名不合法'}), 400
    script_path = os.path.join(BASE_DIR, script)
    if not os.path.isfile(script_path):
        return jsonify({'error': '脚本不存在'}), 400

    with _manual_lock:
        current = _find_similar_state.get(script)
        if current and current.get('status') == 'running':
            return jsonify({'error': '正在执行中，请等待完成', 'state': dict(current)}), 409

        # 构造命令行参数
        args_map = payload.get('args') or {}
        cmd_args = []
        # find_similar_ma5.py 的 target 是位置参数
        if script == 'find_similar_ma5.py':
            target = (args_map.get('target') or '').strip()
            if not target:
                return jsonify({'error': '缺少目标股票代码 target 参数'}), 400
            cmd_args.append(target)
        else:
            # find_similar_wave2.py / find_similar_price_vol.py 用 --code/--start/--end
            code = (args_map.get('code') or '').strip()
            if code:
                cmd_args.extend(['--code', code])
            start = (args_map.get('start') or '').strip()
            if start:
                cmd_args.extend(['--start', start])
            end = (args_map.get('end') or '').strip()
            if end:
                cmd_args.extend(['--end', end])

        # 通用可选参数
        min_score = args_map.get('min_score')
        if min_score:
            cmd_args.extend(['--min-score', str(min_score)])
        top = args_map.get('top')
        if top:
            cmd_args.extend(['--top', str(top)])
        window = args_map.get('window')
        if window:
            cmd_args.extend(['--window', str(window)])

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
            return jsonify({'error': f'启动失败: {e}'}), 500

        state = {'status': 'running', 'start': started.strftime('%Y-%m-%d %H:%M:%S'),
                 'end': None, 'rc': None, 'duration': None,
                 'log': log_name, 'pid': proc.pid,
                 'params': args_map}
        _find_similar_state[script] = state

    # 后台收割进程
    def _reap_find_similar(script, proc, started, log_fp):
        proc.wait()
        ended = datetime.now()
        rc = proc.returncode
        duration = round((ended - started).total_seconds(), 1)
        log_fp.close()
        with _manual_lock:
            s = _find_similar_state.get(script)
            if s:
                s.update(status='success' if rc == 0 else 'failed',
                         end=ended.strftime('%Y-%m-%d %H:%M:%S'),
                         rc=rc, duration=duration)

    threading.Thread(target=_reap_find_similar,
                     args=(script, proc, started, log_fp), daemon=True).start()
    return jsonify({'state': dict(state)}), 202


@app.route('/api/find_similar_status')
def api_find_similar_status():
    """查询以股选股脚本执行状态。"""
    script = request.args.get('script', '').strip()
    if not script:
        return jsonify({'statuses': {}})
    with _manual_lock:
        s = _find_similar_state.get(script)
    return jsonify({'statuses': {script: dict(s) if s else None}})


@app.route('/api/find_similar_results')
def api_find_similar_results():
    """从日志中解析以股选股执行结果（相似股票列表）。

    解析日志中 "🔥 相似度排名前N：" 之后的股票列表行。
    """
    script = request.args.get('script', '').strip()
    with _manual_lock:
        s = _find_similar_state.get(script)
    if not s or s.get('status') != 'success':
        return jsonify({'error': '脚本未成功执行'}), 400

    log_path = os.path.join(MANUAL_LOG_DIR, s.get('log') or '')
    stocks = []
    template_code = None
    try:
        with open(log_path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return jsonify({'error': '日志文件不存在'}), 500

    # 从参数中提取模板股票代码
    params = s.get('params') or {}
    if params.get('target'):
        template_code = params['target'].upper().strip()
    elif params.get('code'):
        template_code = params['code'].upper().strip()

    # 解析计算结果日期（结果列表的交易日）：
    # ma5/wave2 日志为"目标日期[: ]YYYYMMDD"，price_vol 取"候选扫描区间"终点日
    result_date = None
    for line in lines:
        m = re.search(r'目标日期[:：]?\s*(\d{8})', line)
        if m:
            result_date = m.group(1)
            break
    if not result_date:
        for line in lines:
            if '扫描区间' in line:
                m = re.search(r'~\s*(\d{8})', line)
                if m:
                    result_date = m.group(1)
                break

    # 解析 "🔥 相似度排名" 后的股票列表
    in_results = False
    for line in lines:
        stripped = line.strip()
        if '相似度排名' in stripped:
            in_results = True
            continue
        if not in_results:
            continue
        # 匹配 " 1. 301171.SZ 最终=..." 或 " 1. 301171.SZ 综合=..."
        m = re.match(r'\d+\.\s+([A-Za-z0-9.]+)', stripped)
        if m:
            stocks.append(m.group(1))
        elif stripped and not stripped.startswith('=') and not stripped.startswith('🎉'):
            break  # 结果列表结束

    # 补充股票名称
    if stocks:
        placeholders = ','.join(['%s'] * len(stocks))
        rows = query_db(f"""
            SELECT ts_code, stock_name FROM stock_info_t
            WHERE ts_code IN ({placeholders})
        """, tuple(stocks))
        name_map = {r['ts_code']: r['stock_name'] for r in rows} if rows else {}
    else:
        name_map = {}

    # 模板股票名称
    template_name = None
    if template_code:
        rows = query_db("SELECT stock_name FROM stock_info_t WHERE ts_code = %s",
                        (template_code,))
        if rows:
            template_name = rows[0]['stock_name']

    result_list = [{'ts_code': s, 'stock_name': name_map.get(s)} for s in stocks]
    return jsonify({
        'template_code': template_code,
        'template_name': template_name,
        'result_date': result_date,
        'stocks': result_list,
    })


@app.route('/api/kline_range')
def api_kline_range():
    """返回指定股票在指定日期范围内的日K数据。"""
    code = request.args.get('code', '').strip()
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()
    if not code:
        return jsonify({'error': '缺少 code 参数'}), 400

    sql = """SELECT trade_date, open, high, low, close, vol, pct_chg
             FROM stock_daily_t WHERE ts_code = %s"""
    params = [code]
    if start:
        sql += " AND trade_date >= %s"
        params.append(start)
    if end:
        sql += " AND trade_date <= %s"
        params.append(end)
    sql += " ORDER BY trade_date"
    rows = query_db(sql, tuple(params))
    if rows is None:
        return jsonify({'error': '数据库连接失败'}), 500
    if not rows:
        return jsonify({'error': f'未找到 {code} 的行情数据'}), 404

    klines = []
    for r in rows:
        klines.append({
            'date': _fmt_date(r['trade_date']),
            'ohlc': [r['open'], r['close'], r['low'], r['high']],
            'vol': r['vol'],
            'pct_chg': r['pct_chg'],
        })

    # 查股票名称
    name_rows = query_db("SELECT stock_name FROM stock_info_t WHERE ts_code = %s", (code,))
    stock_name = name_rows[0]['stock_name'] if name_rows else None

    return jsonify({
        'ts_code': code,
        'stock_name': stock_name,
        'klines': klines,
    })


if __name__ == '__main__':
    from controller_auth import init_user_table
    init_user_table()   # 建表并初始化默认账号
    app.run(host='127.0.0.1', port=5000, debug=False)
