#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: W23二浪选股策略

对齐《W23波浪选股模型报告.html》及其配套实现 wave3_screener.py 的策略逻辑，
数据源由 AkShare 改为本项目 MySQL 数据库：
  Stage 1 一浪确认: ZigZag 识别 L0 -> H1 上升浪
          （涨幅>=15%、持续10~120日、期间出现过MA5上穿MA30金叉、峰值量>=1.2倍此前60日均量）
  Stage 2 二浪末尾: 自H1回撤38.2%~78.6%（黄金区50%~61.8%加分）且L2不破L0（铁律）、
          H1后至少8个交易日、末期地量衰竭、L2出现至少2日
  Stage 3 三浪启动: 100分制打分卡（最近一根K线及此前5日）
          ①回撤黄金区20/12分(必选) ②末期地量15/10分(必选) ③MACD底背离15/零下金叉10
          ④放量阳线突破15 ⑤MA5拐头+站上10 ⑥关键位支撑10 ⑦MA5再金叉MA30(或多头排列)10
          ⑧大盘环境5（沪深300>MA20，不满足时本项0分且触发门槛+10）
          总分>=70触发买入信号，60~69列入观察池

前置过滤（STOCK_FILTERS 注册式，参考 find_similar_ma5.py；新增条件只需追加一个过滤函数与一行注册）：
  A股板块（仅沪深）、总市值>100亿、最新交易日短线强弱得分>75、
  收盘价/成交量无缺失、历史K线不少于 78 个交易日。

数据: stock_daily_t 最近 260 个交易日（按 qfq_adj_factor 前复权）
      + stock_daily_basic_info_t 市值过滤 + stock_index_daily_t 沪深300大盘过滤
输出: CSV「W23二浪选股.csv」（csv.writer + utf-8-sig）
      文件夹「W23二浪选股+当日日期后缀」（已存在则复用）；无结果不生成文件/文件夹
"""

import os
import csv
from datetime import datetime, timedelta

import tushare as ts

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- W23 模型参数（对应报告"参数速查表"） ----------
ZIGZAG_PCT = 0.08          # ZigZag 摆动确认阈值（小盘/高波动股可调至 0.10~0.12）
WAVE1_MIN_GAIN = 0.15      # 1浪最小涨幅
WAVE1_MIN_DAYS = 30        # 1浪最小持续交易日数
WAVE1_MAX_DAYS = 120       # 1浪最大持续天数（过长的"1浪"多为旧趋势）
WAVE1_VOL_EXPAND = 1.2     # 1浪峰值量 / 此前60日均量 下限
FIB_LO = 0.382             # 2浪回撤下限
FIB_HI = 0.786             # 2浪回撤上限
WAVE2_MIN_DAYS = 8         # 2浪最短调整天数（H1后至少运行的交易日数）
WAVE2_MAX_DAYS = 40        # 2浪最长调整天数（参考实现未强制，保留参数便于校准）
VOL_SHRINK_RATIO = 0.60    # 地量判定：末期均量/1浪峰值量 的加分上限（<=0.40 得满分）
BREAKOUT_VOL_RATIO = 1.5   # 突破日成交量 / 5日均量
MA_SHORT = 5
MA_LONG = 30
SCORE_THRESHOLD = 70       # 信号触发分数
WATCH_THRESHOLD = 60       # 观察池分数
LOOKBACK_DAYS = 260        # 数据回看交易日数
MIN_MARKET_CAP_WAN = 1_000_000  # 总市值下限（万元）= 100亿
MIN_SHORT_STRENGTH = 75.0       # 最新交易日短线强弱得分下限（>75）
MIN_BARS = MA_LONG + WAVE1_MIN_DAYS + WAVE2_MIN_DAYS + 10  # 参与计算的最少交易日数（78）
TOP_N = 20

# ---------- 灵活过滤条件（STOCK_FILTERS 注册表，参考 find_similar_ma5.py） ----------
# 每个过滤函数入参为单只股票的记录列表（按日期升序），返回 True=通过；
# 通过前置过滤后才进入 Stage1~3 形态识别与打分卡计算。

def _filter_a_share(records):
    """仅保留沪深A股（.SH/.SZ），剔除北交所等。"""
    code = records[-1].get('ts_code') or ''
    return code.endswith('.SH') or code.endswith('.SZ')


def _filter_min_market_cap(records):
    """最近一个交易日总市值 total_mv（万元）> MIN_MARKET_CAP_WAN。"""
    mv = records[-1].get('total_mv')
    return mv is not None and mv > MIN_MARKET_CAP_WAN


def _filter_short_strength(records):
    """最新交易日短线强弱得分 > MIN_SHORT_STRENGTH（无得分视为不通过）。"""
    s = records[-1].get('short_strength_score')
    return s is not None and s > MIN_SHORT_STRENGTH


def _filter_close_complete(records):
    """全部交易日收盘价无缺失。"""
    return all(r.get('close') is not None for r in records)


def _filter_vol_complete(records):
    """全部交易日成交量无缺失。"""
    return all(r.get('vol') is not None for r in records)


def _filter_enough_bars(records):
    """历史K线不少于 MIN_BARS 个交易日，保证MA30/1浪/2浪窗口可计算。"""
    return len(records) >= MIN_BARS


STOCK_FILTERS = [
    ('非沪深A股', _filter_a_share),
    (f'总市值<={MIN_MARKET_CAP_WAN/10000:.0f}亿', _filter_min_market_cap),
    (f'短线强弱得分<={MIN_SHORT_STRENGTH:g}', _filter_short_strength),
    ('存在收盘价缺失日', _filter_close_complete),
    ('存在成交量缺失日', _filter_vol_complete),
    (f'历史K线不足{MIN_BARS}日', _filter_enough_bars),
]


def apply_filters(records):
    """依次执行 STOCK_FILTERS 全部条件，返回 (是否通过, 未通过条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(records)]
    return (len(reasons) == 0), reasons


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
    return f"W23二浪选股{get_target_date()}"


def get_folder_path():
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


# ---------- 数据读取 ----------

def read_stock_data(start_date, end_date):
    """读取 stock_daily_t 全市场 [start, end]，LEFT JOIN basic 取 total_mv、stock_info_t 取名称。"""
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT d.ts_code, d.trade_date, d.open, d.close, d.vol,
               d.qfq_adj_factor, d.short_strength_score, b.total_mv, i.stock_name
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        LEFT JOIN stock_info_t i
               ON d.ts_code COLLATE utf8mb4_unicode_ci = i.ts_code
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


def read_market_filter_ok():
    """大盘环境过滤：沪深300 收盘 > MA20。取不到数据时默认放行。"""
    conn = get_mysql_connection()
    if not conn:
        return True
    try:
        with conn.cursor() as cursor:
            for code in ('000300.SH', '000300.CSI'):
                cursor.execute(
                    "SELECT trade_date, close FROM stock_index_daily_t "
                    "WHERE ts_code = %s ORDER BY trade_date DESC LIMIT 30", (code,))
                rows = cursor.fetchall()
                if not rows:
                    continue
                closes = [float(r['close']) for r in rows if r['close'] is not None]
                closes.reverse()
                if len(closes) < 20:
                    return True
                ma20 = sum(closes[-20:]) / 20
                ok = closes[-1] > ma20
                print(f"📊 大盘过滤: 沪深300({code}) 收盘{closes[-1]:.2f} "
                      f"{'>' if ok else '<='} MA20={ma20:.2f} → {'通过' if ok else '不通过(门槛+10)'}")
                return ok
        print("📊 大盘过滤: 无沪深300数据，默认放行")
        return True
    except Exception as e:
        print(f"📊 大盘过滤查询失败({e})，默认放行")
        return True
    finally:
        close_connection(conn)


# ---------- 选股结果入库（便于回测） ----------


# ---------- 指标计算（收盘价/成交量序列 → MA / 量均线 / MACD） ----------

def calc_indicators(closes, vols):
    """纯 Python 计算滚动指标，窗口含当日（与 pandas rolling 对齐）。

    返回 dict(ma5, ma30, vol_ma5, dif, dea)，前缀不足处为 None。
    """
    n = len(closes)

    def rolling_mean(arr, w):
        out = [None] * n
        ps = [0.0] * (n + 1)
        for i in range(n):
            ps[i + 1] = ps[i] + arr[i]
        for i in range(w - 1, n):
            out[i] = (ps[i + 1] - ps[i + 1 - w]) / w
        return out

    ma5 = rolling_mean(closes, MA_SHORT)
    ma30 = rolling_mean(closes, MA_LONG)
    vol_ma5 = rolling_mean(vols, MA_SHORT)

    # MACD: EMA(adjust=False)，ema[0]=c[0]，与 pandas ewm 对齐
    dif = [0.0] * n
    dea = [0.0] * n
    k12, k26, k9 = 2 / 13, 2 / 27, 2 / 10
    ema12 = ema26 = closes[0]
    dif_prev = 0.0
    for i in range(n):
        if i > 0:
            ema12 = k12 * closes[i] + (1 - k12) * ema12
            ema26 = k26 * closes[i] + (1 - k26) * ema26
            dif[i] = ema12 - ema26
            dea[i] = k9 * dif[i] + (1 - k9) * dif_prev
            dif_prev = dif[i]

    return {'ma5': ma5, 'ma30': ma30, 'vol_ma5': vol_ma5, 'dif': dif, 'dea': dea}


# ---------- ZigZag 波形识别 ----------

def find_pivots(close, pct):
    """百分比 ZigZag：返回 [(i, 'H'|'L', price)]。pct 为确认反转所需最小回撤。"""
    pivots = []
    n = len(close)
    trend, ext = None, 0
    for i in range(1, n):
        c = close[i]
        if trend is None:
            if c >= close[ext] * (1 + pct):
                pivots.append((ext, 'L', close[ext])); trend = 'up'; ext = i
            elif c <= close[ext] * (1 - pct):
                pivots.append((ext, 'H', close[ext])); trend = 'down'; ext = i
        elif trend == 'up':
            if c > close[ext]:
                ext = i
            elif c <= close[ext] * (1 - pct):
                pivots.append((ext, 'H', close[ext])); trend = 'down'; ext = i
        else:
            if c < close[ext]:
                ext = i
            elif c >= close[ext] * (1 + pct):
                pivots.append((ext, 'L', close[ext])); trend = 'up'; ext = i
    if trend is not None:
        pivots.append((ext, 'H' if trend == 'up' else 'L', close[ext]))
    return pivots


# ---------- Stage 1 & 2：定位最近的 L0 -> H1 -> L2 结构 ----------

def locate_w12(recs, ind):
    """在尾部窗口内寻找最近一组满足 1 浪定义的 L0->H1，及其后的 2 浪低点 L2。

    返回 (dict, None) 或 (None, 失败原因)。
    """
    n = len(recs)
    close = [r['close'] for r in recs]
    vol = [r['vol'] for r in recs]
    ma5, ma30 = ind['ma5'], ind['ma30']

    pivots = find_pivots(close, ZIGZAG_PCT)
    if len(pivots) < 3:
        return None, '无有效ZigZag摆动结构'

    w = None
    # 候选结构：倒数出现过的 L->H（含最后一个未确认极值），只取最近一组
    for k in range(len(pivots) - 2, -1, -1):
        if pivots[k][1] != 'L':
            continue
        if k + 1 >= len(pivots) or pivots[k + 1][1] != 'H':
            continue
        i0, _, p0 = pivots[k]
        i1, _, p1 = pivots[k + 1]
        gain = p1 / p0 - 1
        days1 = i1 - i0
        if not (gain >= WAVE1_MIN_GAIN and
                WAVE1_MIN_DAYS <= days1 <= WAVE1_MAX_DAYS):
            continue

        # 1浪期间出现过 MA5 上穿 MA30 金叉
        seg_gold = False
        for t in range(max(1, i0), i1 + 1):
            if (ma5[t] is not None and ma30[t] is not None
                    and ma5[t - 1] is not None and ma30[t - 1] is not None
                    and ma5[t] > ma30[t] and ma5[t - 1] <= ma30[t - 1]):
                seg_gold = True
                break

        # 1浪峰值量相对此前60日均量温和放大
        peak_vol = max(vol[max(0, i0 - 5):i1 + 1])
        base = vol[max(0, i0 - 60):i0 + 1]
        base_mean = sum(base) / len(base) if base else 0.0
        vol_ok = peak_vol >= WAVE1_VOL_EXPAND * base_mean

        if not (seg_gold and vol_ok):
            continue

        # 2浪低点：H1 之后最低收盘（含未确认摆动低点）
        after = close[i1 + 1:]
        if len(after) < WAVE2_MIN_DAYS:
            continue
        i2_rel = after.index(min(after))
        i2 = i1 + 1 + i2_rel
        low2 = close[i2]
        days2 = n - 1 - i2                # 距今的天数（低点出现后已运行时间）
        retr = (p1 - low2) / (p1 - p0)    # 回撤比例
        w = dict(i0=i0, i1=i1, i2=i2, L0=p0, H1=p1, L2=low2,
                 gain=gain, days1=days1, days2=days2, retr=retr,
                 seg_gold=seg_gold, vol_ok=vol_ok)
        break   # 只取最近一组满足 1 浪定义的结构

    if w is None:
        return None, '未识别出满足1浪定义的L0→H1结构'

    # --- Stage 2 校验 ---
    if not (FIB_LO <= w['retr'] <= FIB_HI):
        return None, f"回撤{w['retr']*100:.1f}%不在{FIB_LO*100:.1f}%~{FIB_HI*100:.1f}%"
    if w['L2'] <= w['L0']:                # 铁律：2浪不破1浪起点
        return None, 'L2跌破L0(波浪计数失败)'
    if not (w['days2'] >= 2):             # 低点至少出现2日（留确认时间）
        return None, 'L2出现不足2日'
    return w, None


# ---------- Stage 3：三浪启动打分卡 ----------

def score_signal(recs, ind, w, market_ok):
    """对最近一根 K 线按 W23 打分卡评分。返回 (score, detail dict)。"""
    i = len(recs) - 1
    close = [r['close'] for r in recs]
    open_ = [r['open'] for r in recs]
    vol = [r['vol'] for r in recs]
    ma5, ma30 = ind['ma5'], ind['ma30']
    vol_ma5, dif, dea = ind['vol_ma5'], ind['dif'], ind['dea']
    d = {}

    # 必要条件①：回撤黄金区（locate_w12 已保证 38.2~78.6，这里细分加分）
    retr = w['retr']
    d['①回撤黄金区'] = 20 if 0.50 <= retr <= 0.618 else 12

    # 必要条件②：2浪末期地量（L2 前后各3日窗口均量 vs 1浪峰值量）
    v_peak = max(vol[max(0, w['i0'] - 5):w['i1'] + 1])
    lo, hi = max(0, w['i2'] - 3), min(i + 1, w['i2'] + 4)
    v_trough = sum(vol[lo:hi]) / (hi - lo)
    ratio = v_trough / v_peak if v_peak > 0 else 1.0
    d['②地量衰竭'] = 15 if ratio <= 0.40 else (10 if ratio <= VOL_SHRINK_RATIO else 0)
    d['_vol_ratio'] = ratio

    # 必要条件不达标则直接出局
    if d['②地量衰竭'] == 0:
        d['_pass'] = False
        d['_watch'] = False
        d['_threshold'] = SCORE_THRESHOLD + (10 if not market_ok else 0)
        return 0, d

    # ③ MACD：底背离（L2价创新低而DIF未创新低）或近5日零下金叉
    dif_seg = dif[max(0, w['i0']):w['i2'] + 1]
    divergence = (w['L2'] < min(close[max(0, w['i0']):w['i2'] + 1]) * 1.0001
                  and dif[w['i2']] > min(dif_seg))
    golden = any(dif[t] > dea[t] and dif[t - 1] <= dea[t - 1]
                 for t in range(max(1, i - 4), i + 1))
    d['③MACD背离/金叉'] = 15 if divergence else (10 if golden else 0)

    # ④ 放量阳线突破（收阳 + 收盘创近10日新高 + 量>=1.5倍5日均量）
    hi10 = max(close[max(0, i - 10):i]) if i > 0 else close[i]
    breakout = (close[i] > open_[i] and close[i] > hi10
                and vol_ma5[i] is not None
                and vol[i] >= BREAKOUT_VOL_RATIO * vol_ma5[i])
    d['④放量突破'] = 15 if breakout else 0

    # ⑤ MA5 拐头 + 站上 MA5
    d['⑤MA5拐头站上'] = 10 if (ma5[i] is not None and ma5[i - 1] is not None
                                and close[i] > ma5[i] and ma5[i] > ma5[i - 1]) else 0

    # ⑥ 关键位支撑：L2 靠近 MA30 或 61.8% 回撤位（±3%）后不创新低
    fib618 = w['H1'] - 0.618 * (w['H1'] - w['L0'])
    near_sup = (ma30[w['i2']] is not None and
                min(abs(w['L2'] - ma30[w['i2']]) / w['L2'],
                    abs(w['L2'] - fib618) / w['L2']) <= 0.03)
    hold = min(close[w['i2']:]) >= w['L2'] * 0.997
    d['⑥关键位支撑'] = 10 if (near_sup and hold) else 0

    # ⑦ MA5 再度金叉 MA30（或已多头排列且MA30斜率>=0）
    cross = any(ma5[t] is not None and ma30[t] is not None
                and ma5[t - 1] is not None and ma30[t - 1] is not None
                and ma5[t] > ma30[t] and ma5[t - 1] <= ma30[t - 1]
                for t in range(max(1, i - 4), i + 1))
    bull = (ma5[i] is not None and ma30[i] is not None and ma30[i - 3] is not None
            and ma5[i] > ma30[i] and (ma30[i] - ma30[i - 3]) >= 0)
    d['⑦均线再金叉'] = 10 if (cross or bull) else 0

    # ⑧ 大盘环境
    d['⑧大盘环境'] = 5 if market_ok else 0
    threshold = SCORE_THRESHOLD + (10 if not market_ok else 0)
    d['_threshold'] = threshold

    score = sum(v for k, v in d.items() if not k.startswith('_'))
    d['_pass'] = score >= threshold
    d['_watch'] = (not d['_pass']) and WATCH_THRESHOLD <= score < threshold
    return score, d


# ---------- 主流程 ----------

def main():
    target_date = get_target_date()
    print("=" * 80)
    print(f"🌊 W23二浪选股 — 目标日期 {target_date}")
    print(f"   Stage1: 1浪涨幅>={WAVE1_MIN_GAIN*100:.0f}%且{WAVE1_MIN_DAYS}~{WAVE1_MAX_DAYS}日(金叉+放量)"
          f" | Stage2: 回撤{FIB_LO*100:.1f}%~{FIB_HI*100:.1f}%不破L0+H1后{WAVE2_MIN_DAYS}日+"
          f" | Stage3: 打分>={SCORE_THRESHOLD}触发/{WATCH_THRESHOLD}观察")
    print(f"   前置过滤: 仅沪深A股 | 总市值>{MIN_MARKET_CAP_WAN/10000:.0f}亿 | 短线强弱得分>{MIN_SHORT_STRENGTH:g}")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    market_ok = read_market_filter_ok()

    # 组装每只股票的记录（qfq 前复权处理：前复权价 = 原始价 × 当日因子 / 最新交易日因子）
    stock_data = {}
    stock_names = {}
    for r in data:
        code = r['ts_code']
        rec = {
            'ts_code': code,
            'trade_date': r['trade_date'],
            'open': float(r['open']) if r['open'] is not None else None,
            'close': float(r['close']) if r['close'] is not None else None,
            'vol': float(r['vol']) if r['vol'] is not None else None,
            'qfq_adj_factor': float(r['qfq_adj_factor']) if r['qfq_adj_factor'] is not None else None,
            'short_strength_score': (float(r['short_strength_score'])
                                     if r.get('short_strength_score') is not None else None),
            'total_mv': float(r['total_mv']) if r['total_mv'] is not None else None,
        }
        stock_data.setdefault(code, []).append(rec)
        if code not in stock_names and r.get('stock_name'):
            stock_names[code] = r['stock_name']

    latest_date = trade_dates[-1]
    cnt_no_latest = 0
    cnt_filter = {name: 0 for name, _ in STOCK_FILTERS}
    cnt_fail = {}
    result = []

    for code, recs in stock_data.items():
        # 最新交易日无行情（停牌等）不参与，与 find_similar_ma5 的日期对齐口径一致
        if recs[-1]['trade_date'] != latest_date:
            cnt_no_latest += 1
            continue

        # 前复权调整（前复权价 = 原始价 × 当日因子 / 最新交易日因子）
        f_latest = recs[-1]['qfq_adj_factor']
        if f_latest and f_latest > 0:
            for rec in recs:
                f = rec['qfq_adj_factor']
                if f and f > 0 and f != f_latest:
                    k = f / f_latest
                    if rec['open'] is not None:
                        rec['open'] *= k
                    if rec['close'] is not None:
                        rec['close'] *= k

        # 注册式前置过滤（板块/市值/强弱分/数据完整性/历史长度）
        ok, reasons = apply_filters(recs)
        if not ok:
            for name in reasons:
                cnt_filter[name] += 1
            continue

        # 通过前置过滤后，进入 Stage1~3 形态识别与打分卡计算
        ind = calc_indicators([r['close'] for r in recs], [r['vol'] for r in recs])
        w, reason = locate_w12(recs, ind)
        if w is None:
            cnt_fail[reason] = cnt_fail.get(reason, 0) + 1
            continue

        score, detail = score_signal(recs, ind, w, market_ok)
        if not (detail['_pass'] or detail['_watch']):
            cnt_fail[f'得分{score}低于观察线{WATCH_THRESHOLD}'] = \
                cnt_fail.get(f'得分{score}低于观察线{WATCH_THRESHOLD}', 0) + 1
            continue

        result.append({
            'ts_code': code,
            'stock_name': stock_names.get(code, ''),
            'signal': '★买入' if detail['_pass'] else '观察',
            'score': score,
            'threshold': detail['_threshold'],
            'retr': w['retr'],
            'vol_ratio': detail['_vol_ratio'],
            'L0': w['L0'], 'H1': w['H1'], 'L2': w['L2'],
            'days2': w['days2'],
            'stop_loss': w['L2'] * 0.97,
            'close': recs[-1]['close'],
            'total_mv': recs[-1]['total_mv'],
            'short_strength_score': recs[-1]['short_strength_score'],
            'detail': {k: v for k, v in detail.items() if not k.startswith('_')},
        })

    result.sort(key=lambda x: -x['score'])

    # ---------- 漏斗 ----------
    print("\n" + "=" * 72)
    print(f"  股票总数: {len(stock_data)}")
    print(f"  - 最新日停牌无数据: {cnt_no_latest}")
    for name, cnt in cnt_filter.items():
        print(f"  - 过滤[{name}]: {cnt}")
    for name, cnt in sorted(cnt_fail.items(), key=lambda x: -x[1])[:10]:
        print(f"  - {name}: {cnt}")
    n_buy = sum(1 for s in result if s['signal'] == '★买入')
    print("-" * 72)
    print(f"  入选: {len(result)}（★买入{n_buy}，观察{len(result)-n_buy}，按得分降序）")
    print("=" * 72)

    if not result:
        print("⚠️ 无符合W23二浪形态的股票，不生成文件夹/CSV")
        return

    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, 'W23二浪选股.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '信号', '得分', '触发线',
                         '回撤比例%', '地量比', 'L0', 'H1', 'L2', '距L2天数',
                         '止损位(L2-3%)', '最新收盘', '总市值(万)'])
        for s in result[:TOP_N]:
            writer.writerow([
                s['ts_code'], s['stock_name'], s['signal'], s['score'],
                s['threshold'], f"{s['retr']*100:.1f}", f"{s['vol_ratio']:.2f}",
                round(s['L0'], 2), round(s['H1'], 2), round(s['L2'], 2),
                s['days2'], round(s['stop_loss'], 2), round(s['close'], 2),
                f"{s['total_mv']:.0f}",
            ])
    print(f"✅ CSV已生成（{min(TOP_N, len(result))}只）: {csv_path}")

    print(f"\n🔥 W23信号前{min(TOP_N, len(result))}（按得分降序）：")
    for i, s in enumerate(result[:TOP_N], 1):
        sss = s.get('short_strength_score')
        sss_mark = f" 强弱={sss:.1f}" if sss is not None else ""
        print(f"{i:>2}. {s['ts_code']:<11}{s['stock_name']:<6} [{s['signal']}] "
              f"得分={s['score']}{sss_mark} 回撤={s['retr']*100:.1f}% 地量比={s['vol_ratio']:.2f} "
              f"L0={s['L0']:.2f} H1={s['H1']:.2f} L2={s['L2']:.2f} 距L2={s['days2']}日 "
              f"止损={s['stop_loss']:.2f}")

    buys = [s for s in result if s['signal'] == '★买入']
    if buys:
        print("\n买入信号打分明细：")
        for s in buys[:10]:
            print(f"  {s['ts_code']}（得分 {s['score']}）: "
                  + ", ".join(f"{k}:{v}" for k, v in s['detail'].items()))

    # 选股结果入库（★买入=1，观察=0）
    record_selected_stocks(
        'W23二浪选股策略',
        [{'ts_code': s['ts_code'], 'selected': 1 if s['signal'] == '★买入' else 0}
         for s in result],
        latest_date)


if __name__ == '__main__':
    main()
