#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
二浪早识别（三级预警）(select_wave2_early.py)

相对 select_wave2.py 的核心改进 —— 解决"识别到2浪时已接近浪3"的滞后问题：
  1. H1（浪1顶部）不再用 order=10 局部极值（右侧要等10天确认），
     改用 stock_daily_t.turning_point='波峰'（ma5斜率打标，右侧仅滞后3天），提前约7天
  2. L1 = H1 之前最近的 turning_point='波谷'
  3. 不要求"2浪已完整走出"，只要价格进入目标区域且出现右侧止跌K线即分级预警
  4. 输出三级状态：
       🔵 预警（左侧）：回调进入黄金分割区 0.382~0.764 + 缩量（回调均量<浪1均量）
       🟡 试仓（右侧初确认）：预警 + 时间比≥0.5 + 止跌信号（站回MA5 / 锤子线 / 看涨吞没）
       🟢 确认（浪3启动）：试仓 + 放量阳线/突破回调平台（+MA5>MA30 加分）

结构底线（任何级别都必须满足，违反即排除）：
  - 回撤最低价 > L1（不破浪1起点）
  - 长期均线向上、现价在长期均线上方、浪1高点站上长期均线
    （DB仅214个交易日，MA250不可得，暂用MA120，改 LONG_MA_DAYS 即可切换）
  - MA30 向上
  - 浪2/浪1 时间比 <= 2.618（调整不能无限拉长）
  - 浪1涨幅 >= MIN_WAVE1_PCT
  - 基础过滤：仅A股、最新日有数据、总市值>=100亿

输出：
  - 文件夹「二浪早识别+当日日期后缀」（已存在则复用）
  - CSV「二浪早识别.csv」：股票代码/信号级别/L1/H1日期/回调天数/回撤/量比/触发信号/市值
  - 排序：确认 > 试仓 > 预警，同级按打分降序；结果为0不生成文件/文件夹

用法：
  python3 select_wave2_early.py
"""

import os
import csv
from datetime import datetime, timedelta

import tushare as ts

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection
from insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- 策略参数 ----------
RETRACE_GOLDEN_MIN = 0.382   # 黄金分割回撤下限（预警区起点）
RETRACE_GOLDEN_MAX = 0.618   # 标准黄金分割上限
RETRACE_DEEP_MAX = 0.764     # 极端深调上限（>此值结构失效）
MIN_WAVE1_PCT = 20.0         # 浪1最小涨幅（%）
MIN_WAVE1_DAYS = 5           # 浪1最短交易日数
MIN_WAVE2_DAYS = 3           # 浪2最少已运行交易日数
TIME_RATIO_MIN_TRIAL = 0.5   # 试仓级别：浪2/浪1 时间比下限
TIME_RATIO_MAX = 2.618       # 浪2/浪1 时间比上限（超过结构失效）
SHRINK_RATIO = 1.0           # 缩量：回调段均量 / 浪1均量 须 < 此值
VOL_BURST_RATIO = 1.5        # 浪3确认：最新日量 / 回调段均量（剔除最新日）
BREAKOUT_PCT = 3.0           # 浪3确认：最新日涨幅门槛（%）
H1_LOOKBACK_DAYS = 70        # H1（波峰）须出现在最近N个交易日内
LONG_MA_DAYS = 120           # 长期均线（数据足够后可改250）
SHORT_MA_DAYS = 5
MID_MA_DAYS = 30
MIN_MARKET_CAP_WAN = 1_000_000  # 100亿
LOOKBACK_DAYS = 250
TOP_N = 50

LEVEL_WARN, LEVEL_TRIAL, LEVEL_CONFIRM = '预警', '试仓', '确认'
LEVEL_RANK = {LEVEL_CONFIRM: 0, LEVEL_TRIAL: 1, LEVEL_WARN: 2}


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 15)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    return opens[-n:] if len(opens) > n else opens


def get_folder_name():
    return f"二浪早识别{get_target_date()}"


def get_folder_path():
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------- 数据读取与均线 ----------

def read_stock_data(start_date, end_date):
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
               d.vol, d.amount, d.turning_point, b.total_mv
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        WHERE d.trade_date >= %s AND d.trade_date <= %s
        ORDER BY d.ts_code, d.trade_date
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query_sql, (start_date, end_date))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {end_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(conn)


def add_mas(recs):
    """前缀和计算 ma5/ma30/ma_long，窗口内有缺失则该均线为 None。"""
    closes = [r['close'] for r in recs]
    n = len(closes)
    pref, cnt = [0.0] * (n + 1), [0] * (n + 1)
    for i, c in enumerate(closes):
        pref[i + 1] = pref[i] + (c if c is not None else 0.0)
        cnt[i + 1] = cnt[i] + (1 if c is not None else 0)
    for days, key in ((SHORT_MA_DAYS, 'ma5'), (MID_MA_DAYS, 'ma30'),
                      (LONG_MA_DAYS, 'ma_long')):
        for i in range(n):
            if i < days - 1:
                recs[i][key] = None
                continue
            lo = i - days + 1
            recs[i][key] = ((pref[i + 1] - pref[lo]) / days
                            if cnt[i + 1] - cnt[lo] == days else None)


# ---------- 右侧止跌 / 启动信号 ----------

def is_hammer(r):
    """锤子线：下影线 >= 2倍实体，上影线 <= 实体，实体位于K线上端（阴阳均可）。"""
    o, h, l, c = r['open'], r['high'], r['low'], r['close']
    if None in (o, h, l, c) or h <= l:
        return False
    body = abs(c - o)
    lower = min(o, c) - l
    upper = h - max(o, c)
    rng = h - l
    return (rng > 0 and lower >= 2 * max(body, rng * 0.05)
            and upper <= max(body, rng * 0.1) and min(o, c) >= l + rng * 0.6)


def is_bullish_engulfing(prev, r):
    """看涨吞没：昨阴今阳，今实体完全包住昨实体。"""
    if None in (prev['open'], prev['close'], r['open'], r['close']):
        return False
    return (prev['close'] < prev['open'] and r['close'] > r['open']
            and r['close'] >= prev['open'] and r['open'] <= prev['close'])


def bottom_signal(recs):
    """最新1~2根K线的右侧止跌信号列表。"""
    sig = []
    last = recs[-1]
    if last['ma5'] is not None and last['close'] is not None and last['close'] > last['ma5']:
        sig.append('站回MA5')
    if is_hammer(last):
        sig.append('锤子线')
    if len(recs) >= 2 and is_bullish_engulfing(recs[-2], last):
        sig.append('看涨吞没')
    if (last['close'] is not None and last['open'] is not None
            and last['close'] > last['open']):
        sig.append('阳线')
    return sig


def wave3_signal(recs, j, pullback_avg_vol):
    """浪3启动确认信号：放量阳线 或 突破H1后回调平台；MA5>MA30作附加确认。"""
    last = recs[-1]
    prev = recs[-2] if len(recs) >= 2 else None
    sig = []
    gain = None
    if prev and prev['close'] and last['close']:
        gain = (last['close'] / prev['close'] - 1) * 100
    # 放量阳线
    if (last['vol'] and pullback_avg_vol and last['vol'] > VOL_BURST_RATIO * pullback_avg_vol
            and gain is not None and gain > BREAKOUT_PCT):
        sig.append(f'放量+{gain:.1f}%')
    # 突破H1之后（不含最新日）的最高收盘
    after_high = max((r['close'] for r in recs[j + 1:-1] if r['close'] is not None), default=None)
    if after_high is not None and last['close'] and last['close'] > after_high:
        if last['vol'] and pullback_avg_vol and last['vol'] > pullback_avg_vol:
            sig.append('放量突破回调平台')
    # 均线多头
    if last['ma5'] is not None and last['ma30'] is not None and last['ma5'] > last['ma30']:
        sig.append('MA5>MA30')
    return sig


# ---------- 单股二浪早识别 ----------

def detect(recs):
    """返回 (入选dict, 失败原因)；用 turning_point 波峰/波谷识别浪1。"""
    n = len(recs)
    if n < LONG_MA_DAYS + 20:
        return None, '数据不足'
    last = recs[-1]
    for key in ('close', 'ma_long', 'ma30', 'ma5'):
        if last.get(key) is None:
            return None, '最新日均线/价格缺失'

    # H1：最近 H1_LOOKBACK_DAYS 日内、且距最新日>=3（打标滞后）的最近一个波峰
    h1_idx = None
    for t in range(n - 4, max(n - 1 - H1_LOOKBACK_DAYS, 0) - 1, -1):
        if recs[t]['turning_point'] == '波峰':
            h1_idx = t
            break
    if h1_idx is None:
        return None, '近期无turning_point波峰'

    # L1：H1 之前最近的波谷
    l1_idx = None
    for t in range(h1_idx - 1, -1, -1):
        if recs[t]['turning_point'] == '波谷':
            l1_idx = t
            break
    if l1_idx is None:
        return None, 'H1前无波谷'

    c_l1, c_h1 = recs[l1_idx]['close'], recs[h1_idx]['close']
    if not c_l1 or not c_h1 or c_l1 <= 0:
        return None, 'L1/H1价格缺失'
    wave1_days = h1_idx - l1_idx
    if wave1_days < MIN_WAVE1_DAYS:
        return None, '浪1周期过短'
    wave1_gain = (c_h1 / c_l1 - 1) * 100
    if wave1_gain < MIN_WAVE1_PCT:
        return None, f'浪1涨幅不足{MIN_WAVE1_PCT:g}%'

    # 浪2：H1 之后（含最新日）
    wave2_days = n - 1 - h1_idx
    if wave2_days < MIN_WAVE2_DAYS:
        return None, '浪2刚启动(不足3日)'
    after = recs[h1_idx + 1:]
    if any(r['close'] is None for r in after):
        return None, '浪2段价格缺失'
    l2_idx = min(range(h1_idx + 1, n), key=lambda t: recs[t]['close'])
    c_l2 = recs[l2_idx]['close']

    # ---------- 结构底线 ----------
    if c_l2 <= c_l1:
        return None, '回撤已破浪1起点'
    retrace = (c_h1 - c_l2) / (c_h1 - c_l1)
    if retrace > RETRACE_DEEP_MAX:
        return None, f'回撤>{RETRACE_DEEP_MAX}结构失效'
    if retrace < RETRACE_GOLDEN_MIN:
        return None, f'回撤<{RETRACE_GOLDEN_MIN}未到位'
    ma_l_now, ma_l_ref = last['ma_long'], recs[n - 6]['ma_long']
    if ma_l_ref is None or not (ma_l_now > ma_l_ref):
        return None, '长期均线未向上'
    if last['close'] <= ma_l_now:
        return None, '现价在长期均线下方'
    if recs[h1_idx]['ma_long'] is None or c_h1 <= recs[h1_idx]['ma_long']:
        return None, '浪1高点未站上长期均线'
    ma30_now, ma30_ref = last['ma30'], recs[n - 6]['ma30']
    if ma30_ref is None or not (ma30_now > ma30_ref):
        return None, 'MA30未向上'
    time_ratio = wave2_days / wave1_days
    if time_ratio > TIME_RATIO_MAX:
        return None, '浪2时间超2.618倍结构失效'

    # 量能：浪1均量 vs 浪2均量（浪3确认时剔除最新日单独算回调均量）
    avg_vol1 = mean([recs[t]['vol'] for t in range(l1_idx, h1_idx + 1)])
    avg_vol2_all = mean([r['vol'] for r in after])
    avg_vol2_pull = mean([recs[t]['vol'] for t in range(h1_idx + 1, n - 1)])
    if not avg_vol1 or not avg_vol2_all:
        return None, '量能数据缺失'
    vol_ratio = avg_vol2_all / avg_vol1
    if vol_ratio >= SHRINK_RATIO:
        return None, '回调未缩量'

    # ---------- 三级信号 ----------
    signals = []
    deep = retrace > RETRACE_GOLDEN_MAX
    level = LEVEL_WARN
    signals.append(f"回撤{retrace:.3f}{'(深调)' if deep else ''}")

    b_sig = bottom_signal(recs)
    w3_sig = wave3_signal(recs, h1_idx, avg_vol2_pull or avg_vol2_all)

    if time_ratio >= TIME_RATIO_MIN_TRIAL and b_sig:
        level = LEVEL_TRIAL
        signals += b_sig
    if level == LEVEL_TRIAL and w3_sig:
        level = LEVEL_CONFIRM
        signals += w3_sig

    # 打分（同级排序用）：空间30 + 缩量25 + 时间15 + 右侧信号30
    score = 30 * max(0.0, 1 - abs(retrace - 0.5) / 0.264)
    score += 25 * max(0.0, 1 - vol_ratio)
    score += 15 * min(time_ratio / 1.0, 1.0)
    score += min(30, 8 * len(set(b_sig)) + 10 * (level == LEVEL_CONFIRM))

    return {
        'level': level, 'score': round(score, 1),
        'l1_date': recs[l1_idx]['trade_date'], 'h1_date': recs[h1_idx]['trade_date'],
        'l2_date': recs[l2_idx]['trade_date'],
        'l1_close': c_l1, 'h1_close': c_h1, 'l2_close': c_l2,
        'retrace': retrace, 'wave1_days': wave1_days, 'wave2_days': wave2_days,
        'vol_ratio': vol_ratio, 'signals': '、'.join(dict.fromkeys(signals)),
        'close': last['close'],
    }, None


# ---------- 主流程 ----------

def main():
    target_date = get_target_date()
    print("=" * 80)
    print(f"🌊 二浪早识别（三级预警）— 目标日期 {target_date}")
    print(f"   H1=turning_point波峰(滞后3日) | 回撤{RETRACE_GOLDEN_MIN}~{RETRACE_DEEP_MAX} "
          f"| 长期MA{LONG_MA_DAYS} | 预警→试仓→确认")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    stock_data = {}
    for r in data:
        stock_data.setdefault(r['ts_code'], []).append({
            'trade_date': r['trade_date'],
            'open': float(r['open']) if r['open'] is not None else None,
            'high': float(r['high']) if r['high'] is not None else None,
            'low': float(r['low']) if r['low'] is not None else None,
            'close': float(r['close']) if r['close'] is not None else None,
            'vol': float(r['vol']) if r['vol'] is not None else None,
            'amount': float(r['amount']) if r['amount'] is not None else None,
            'turning_point': r['turning_point'],
            'total_mv': float(r['total_mv']) if r['total_mv'] is not None else 0.0,
        })
    for recs in stock_data.values():
        add_mas(recs)

    latest_date = trade_dates[-1]
    cnt = {'停牌': 0, '非A股': 0, '市值不足': 0}
    fail_cnt = {}
    result = []
    for code, recs in stock_data.items():
        if recs[-1]['trade_date'] != latest_date:
            cnt['停牌'] += 1
            continue
        if not (code.endswith('.SH') or code.endswith('.SZ')):
            cnt['非A股'] += 1
            continue
        if recs[-1]['total_mv'] < MIN_MARKET_CAP_WAN:
            cnt['市值不足'] += 1
            continue
        hit, reason = detect(recs)
        if hit is None:
            fail_cnt[reason] = fail_cnt.get(reason, 0) + 1
            continue
        hit['ts_code'] = code
        hit['total_mv'] = recs[-1]['total_mv']
        result.append(hit)

    result.sort(key=lambda x: (LEVEL_RANK[x['level']], -x['score']))

    print("\n" + "=" * 72)
    print(f"  股票总数: {len(stock_data)} | 停牌 {cnt['停牌']} | 非A股 {cnt['非A股']} "
          f"| 市值不足 {cnt['市值不足']}")
    for name, c in sorted(fail_cnt.items(), key=lambda x: -x[1])[:10]:
        print(f"  - {name}: {c}")
    n_confirm = sum(1 for x in result if x['level'] == LEVEL_CONFIRM)
    n_trial = sum(1 for x in result if x['level'] == LEVEL_TRIAL)
    n_warn = sum(1 for x in result if x['level'] == LEVEL_WARN)
    print("-" * 72)
    print(f"  🟢 确认(浪3启动): {n_confirm} | 🟡 试仓(右侧止跌): {n_trial} | 🔵 预警(左侧): {n_warn}")
    print("=" * 72)

    if not result:
        print("⚠️ 三级预警结果均为0，不生成文件夹/CSV")
        return

    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, '二浪早识别.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '信号级别', '打分', 'L1日期', 'H1日期', 'L2日期',
                         '回撤幅度', '浪1天数', '浪2天数', '回调/浪1量比',
                         '触发信号', '总市值(万)', '最新收盘'])
        for s in result[:TOP_N]:
            writer.writerow([
                s['ts_code'], s['level'], s['score'], s['l1_date'], s['h1_date'],
                s['l2_date'], f"{s['retrace']:.3f}", s['wave1_days'], s['wave2_days'],
                f"{s['vol_ratio']:.2f}", s['signals'], f"{s['total_mv']:.0f}", s['close'],
            ])
    print(f"✅ CSV已生成（{min(TOP_N, len(result))}只）: {csv_path}")

    # 选股结果入库（便于回测，与CSV内容一致取Top N；确认=1，试仓/预警=0）
    record_selected_stocks(
        'wave2_early',
        [{'ts_code': s['ts_code'],
          'selected': 1 if s['level'] == LEVEL_CONFIRM else 0}
         for s in result[:TOP_N]],
        latest_date)

    icon = {LEVEL_CONFIRM: '🟢', LEVEL_TRIAL: '🟡', LEVEL_WARN: '🔵'}
    print(f"\n🔥 三级预警名单（前{min(TOP_N, len(result))}）：")
    for i, s in enumerate(result[:TOP_N], 1):
        print(f"{i:>2}. {icon[s['level']]} {s['ts_code']:<11} [{s['level']}] 打分={s['score']:>5} "
              f"L1={s['l1_date']} H1={s['h1_date']} L2={s['l2_date']} "
              f"回撤={s['retrace']:.3f} 量比={s['vol_ratio']:.2f} "
              f"时比=1:{s['wave2_days'] / s['wave1_days']:.2f} | {s['signals']}")


if __name__ == '__main__':
    main()
