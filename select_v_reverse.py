#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
V形反转 / 底部放量突破选股 (select_v_reverse.py)

参考 vp_pattern_detector.py 的五大量价特征，识别「长期下跌 → 底部缩量双底 → 放量V型突破」形态：
  F1 长期下跌趋势      —— 近60日中 >70% 时间收于 MA60 下方，且 MA60 斜率为负
  F2 底部缩量横盘      —— 近20日振幅 < 20% + 量能处于近250日低分位(<35%) + 长均线走平
  F3 双底/低点抬高     —— 近60日内第二低点不破第一低点(±5%)，且已脱离第二低点
  F4 放量突破大阳线    —— 量创60日新高 & 量 >= 前日均量 2.5 倍 & 涨幅 >= 5% & 光尾大阳
  F5 收复长期均线      —— 收盘上穿 MA60，或 MA5 上穿 MA10 且站上 MA60
  F6 连续放量上攻      —— 近5日内 >= 3 天连续上涨且量能递增

复合信号（底部放量突破）：
  bottom_struct = (近45日出现 F2 或 F3) & (近90日出现 F1)
  vol_conf      = (量能 vol_z60 >= 3.0) 或 F4
  trend_conf    = F5 或 F6
  signal        = bottom_struct & vol_conf & trend_conf

基础门槛：沪深A股、最新日总市值 > 100亿、短线强弱得分 > 60、有效交易日 >= 250日。
全部条件以 STOCK_FILTERS 注册表实现，新增条件只需追加一行注册。

输出：CSV「V形反转.csv」（utf-8-sig，含命中特征与特征得分），
文件夹「V形反转+当日日期后缀」（已存在则复用）；结果为0不生成文件/文件夹。
"""

import os
import csv
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import tushare as ts

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


# ---------- 策略参数（与 vp_pattern_detector.py 对齐） ----------
LOOKBACK_DAYS = 250        # 数据读取窗口（需覆盖 vma5_pctile 的 250 日分位）
MIN_BARS = 250             # 最少有效交易日（保证 vma5_pctile 等长窗口可用）

# F1 长期下跌
F1_DOWN_LOOKBACK = 60      # F1 回看窗口
F1_BELOW_RATIO = 0.70      # >70% 时间收于 MA60 下方
# F2 底部缩量横盘
F2_BASE_LOOKBACK = 20      # 横盘窗口
F2_RANGE_MAX = 0.20        # 横盘区间最大振幅 20%
F2_VOL_PCTILE = 0.35       # 量能低分位阈值
F2_SLOPE_TOL = 0.004       # 长均线走平容忍度
# F3 双底/低点抬高
F3_LOW_LOOKBACK = 60       # 双底回看窗口
F3_LOW_TOL = 0.05          # 第二低点相对第一低点容忍度 ±5%
# F4 放量突破大阳线
F4_VOL_MULT = 2.5          # 量 / 前一日 vma5 的倍数阈值
F4_VOL_HIGH_N = 60         # 量创 N 日新高
F4_BIG_UP_PCT = 5.0        # 大阳线最小涨幅（%，百分比点数）
F4_TAIL_RATIO = 0.97       # 光尾：收盘 >= 最高 × 0.97
# F6 连续放量上攻
F6_CONSEC_UP = 3           # 近5日内连续放量上涨天数阈值
# 复合信号窗口
BOTTOM_STRUCT_WIN = 45     # 底部结构回看（F2/F3）
F1_RECENT_WIN = 90         # 长期下跌回看（F1）
VOL_Z_THRESHOLD = 3.0      # 量能 z-score 阈值

# 基础门槛
MIN_MARKET_CAP_WAN = 1_000_000  # 总市值下限（万元）= 100亿
MIN_SHORT_STRENGTH = 60.0       # 最新日短线强弱得分 > 60
TOP_N = 50


# ---------- 量价特征计算（对齐 vp_pattern_detector.detect_features） ----------

def compute_features(records):
    """对单只股票的 records（按日期升序 list[dict]）计算 F1~F6 及复合信号。

    返回最新一日的特征 dict，含各 F 标志、复合信号、窗口级标志、vol_z60、特征得分。
    数据不足导致指标为 NaN 时，对应特征视为 False。
    """
    df = pd.DataFrame(records)
    df = df.sort_values('trade_date').reset_index(drop=True)
    close = df['close']
    vol = df['vol']
    high = df['high']
    low = df['low']
    opn = df['open']

    # 涨跌幅（百分比点数，与 DB pct_chg 同口径）
    df['pct_chg'] = close.pct_change() * 100

    # 均线（MA5/MA10/MA60 由 close 自行计算，不依赖 DB 打标，与 vp_pattern_detector 一致）
    for n in (5, 10, 60):
        df[f'ma{n}'] = close.rolling(n).mean()
    df['vma5'] = vol.rolling(5).mean()

    # MA60 5 日归一化斜率
    df['ma60_slope'] = df['ma60'].diff(5) / df['ma60'].shift(5)

    # 量能指标
    df['vol_ratio'] = vol / df['vma5'].shift(1)
    df['vol_rank60'] = vol.rolling(F4_VOL_HIGH_N).apply(
        lambda x: (x[-1] >= x).all(), raw=True)
    vol_mean60 = vol.rolling(60).mean()
    vol_std60 = vol.rolling(60).std()
    df['vol_z60'] = (vol - vol_mean60) / vol_std60
    df['vma5_pctile'] = df['vma5'].rolling(250, min_periods=60).rank(pct=True)

    # ----- F1 长期下跌趋势 -----
    below = (close < df['ma60']).rolling(F1_DOWN_LOOKBACK).mean() > F1_BELOW_RATIO
    df['F1'] = (below & (df['ma60_slope'] < 0)).fillna(False).astype(bool)

    # ----- F2 底部缩量横盘 -----
    rng = close.rolling(F2_BASE_LOOKBACK).max() / close.rolling(F2_BASE_LOOKBACK).min() - 1
    df['F2'] = (
        (rng < F2_RANGE_MAX)
        & (df['vma5_pctile'] < F2_VOL_PCTILE)
        & (df['ma60_slope'].abs() < F2_SLOPE_TOL)
    ).fillna(False).astype(bool)

    # ----- F3 双底/低点抬高 -----
    def _higher_low(win):
        seg = pd.Series(win).reset_index(drop=True)
        if len(seg) < 30:
            return False
        i1 = int(seg.idxmin())
        first_low = seg.iloc[i1]
        after = seg.iloc[i1 + 10:]
        if after.empty:
            return False
        second_low = after.min()
        return (second_low >= first_low * (1 - F3_LOW_TOL)) and (seg.iloc[-1] > second_low * 1.05)

    df['F3'] = low.rolling(F3_LOW_LOOKBACK).apply(
        lambda x: _higher_low(x), raw=False).fillna(0).astype(bool)

    # ----- F4 放量突破大阳线 -----
    body_ok = (df['pct_chg'] >= F4_BIG_UP_PCT) & (close > opn)
    tail_ok = close >= high * F4_TAIL_RATIO
    df['F4'] = (
        df['vol_rank60'].astype(bool)
        & (df['vol_ratio'] >= F4_VOL_MULT)
        & body_ok & tail_ok
    ).fillna(False).astype(bool)

    # ----- F5 收复长期均线 -----
    cross_up = (close > df['ma60']) & (close.shift(1) <= df['ma60'].shift(1))
    golden = (
        (df['ma5'] > df['ma10'])
        & (df['ma5'].shift(1) <= df['ma10'].shift(1))
        & (close > df['ma60'])
    )
    df['F5'] = (cross_up | golden).fillna(False).astype(bool)

    # ----- F6 连续放量上攻 -----
    up = df['pct_chg'] > 0
    vol_up = vol > vol.shift(1)
    df['F6'] = ((up & vol_up).rolling(5).sum() >= F6_CONSEC_UP).fillna(False).astype(bool)

    # ----- 复合信号：底部结构 + 量能爆发 + 趋势确认 -----
    fcols = ['F1', 'F2', 'F3', 'F4', 'F5', 'F6']
    has_f1_recent = df['F1'].iloc[-F1_RECENT_WIN:].max()
    has_bottom_recent = (df['F2'].iloc[-BOTTOM_STRUCT_WIN:].max()
                         or df['F3'].iloc[-BOTTOM_STRUCT_WIN:].max())
    bottom_struct = bool(has_bottom_recent and has_f1_recent)

    last = df.iloc[-1]
    vol_conf = bool((pd.notna(last['vol_z60']) and last['vol_z60'] >= VOL_Z_THRESHOLD)
                    or bool(last['F4']))
    trend_conf = bool(last['F5'] or last['F6'])
    signal = bottom_struct and vol_conf and trend_conf

    return {
        'F1': bool(last['F1']),
        'F2': bool(last['F2']),
        'F3': bool(last['F3']),
        'F4': bool(last['F4']),
        'F5': bool(last['F5']),
        'F6': bool(last['F6']),
        'bottom_struct': bottom_struct,
        'vol_conf': vol_conf,
        'trend_conf': trend_conf,
        'signal': signal,
        'vol_z60': float(last['vol_z60']) if pd.notna(last['vol_z60']) else None,
        'feature_score': sum(1 for c in fcols if bool(last[c])),
        'hit_features': [c for c in fcols if bool(last[c])],
    }


# ---------- 上下文 & 过滤条件（STOCK_FILTERS 注册表） ----------

def build_context(code, records):
    """计算单只股票的 V 形反转特征上下文。"""
    latest = records[-1]
    feat = compute_features(records)
    return {
        'ts_code': code,
        'bars': len(records),
        'total_mv': latest.get('total_mv'),
        'short_strength_score': (float(latest.get('short_strength_score'))
                                 if latest.get('short_strength_score') is not None else None),
        'close': latest.get('close'),
        'pct_chg': latest.get('pct_chg'),
        **feat,
    }


def _filter_a_share(ctx):
    """仅保留沪深A股（.SH/.SZ），剔除北交所等。"""
    code = ctx['ts_code'] or ''
    return code.endswith('.SH') or code.endswith('.SZ')


def _filter_min_market_cap(ctx):
    """最新日总市值 > 100亿。"""
    mv = ctx['total_mv']
    return mv is not None and mv > MIN_MARKET_CAP_WAN


def _filter_enough_bars(ctx):
    """有效交易日 >= MIN_BARS（250日），保证量能分位等长窗口可用。"""
    return ctx['bars'] >= MIN_BARS


def _filter_short_strength(ctx):
    """最新日短线强弱得分 > 60（无得分视为不通过）。"""
    s = ctx.get('short_strength_score')
    return s is not None and s > MIN_SHORT_STRENGTH


def _filter_bottom_struct(ctx):
    """近45日存在底部结构(F2 或 F3) 且 近90日存在长期下跌(F1)。"""
    return ctx['bottom_struct']


def _filter_vol_conf(ctx):
    """量能爆发：vol_z60 >= 3 或 F4 放量突破大阳线。"""
    return ctx['vol_conf']


def _filter_trend_conf(ctx):
    """趋势确认：F5 收复长期均线 或 F6 连续放量上攻。"""
    return ctx['trend_conf']


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)
STOCK_FILTERS = [
    ('非沪深A股', _filter_a_share),
    (f'最新日总市值<={MIN_MARKET_CAP_WAN / 10000:.0f}亿', _filter_min_market_cap),
    (f'有效交易日不足{MIN_BARS}日', _filter_enough_bars),
    (f'短线强弱得分<={MIN_SHORT_STRENGTH:g}', _filter_short_strength),
    ('近期无底部结构(F2/F3)或无长期下跌(F1)', _filter_bottom_struct),
    ('量能未爆发(vol_z60<3且无F4放量突破)', _filter_vol_conf),
    ('未收复长期均线且无连续放量上攻(F5/F6均无)', _filter_trend_conf),
]


def apply_filters(ctx):
    """依次执行 STOCK_FILTERS 全部条件，返回 (是否通过, 未通过条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(ctx)]
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


def get_folder_path():
    folder_name = f"V形反转{get_target_date()}"
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


# ---------- 数据读取 ----------

def read_stock_data(start_date, end_date):
    """读取 stock_daily_t 全市场 [start, end]，LEFT JOIN basic 取 total_mv。

    需含 open/high/low/close/vol/pct_chg 用于量价形态计算，以及 short_strength_score。
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT d.ts_code, d.trade_date,
               d.open, d.high, d.low, d.close, d.vol, d.pct_chg,
               d.short_strength_score, b.total_mv
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


# ---------- 核心选股逻辑（供 main 与 backtest_scanner 扫描模式共用） ----------

def analyze_stocks(data):
    """对全市场日线数据执行 V形反转/底部放量突破选股。

    data 为 list[dict]，每行含 ts_code, trade_date, open, high, low, close, vol,
    pct_chg, short_strength_score, total_mv（可选 stock_name）。
    返回入选股票 list[dict]，每条含 ts_code, stock_name, close, pct_chg,
    total_mv, vol_z60, feature_score, hit_features。
    """
    stock_data = {}
    for r in data:
        rec = {
            'ts_code': r['ts_code'],
            'trade_date': r['trade_date'],
            'open':  float(r['open']) if r['open'] is not None else None,
            'high':  float(r['high']) if r['high'] is not None else None,
            'low':   float(r['low']) if r['low'] is not None else None,
            'close': float(r['close']) if r['close'] is not None else None,
            'vol':   float(r['vol']) if r['vol'] is not None else 0.0,
            'pct_chg': (float(r['pct_chg']) if r['pct_chg'] is not None else None),
            'short_strength_score': (float(r['short_strength_score'])
                                     if r.get('short_strength_score') is not None else None),
            'total_mv': float(r['total_mv']) if r['total_mv'] is not None else None,
        }
        stock_data.setdefault(r['ts_code'], {
            'records': [],
            'stock_name': r.get('stock_name') or '',
        })
        stock_data[r['ts_code']]['records'].append(rec)
        if not stock_data[r['ts_code']]['stock_name'] and r.get('stock_name'):
            stock_data[r['ts_code']]['stock_name'] = r['stock_name']

    # 最新交易日取自数据内最大 trade_date（扫描模式下即 target_date）
    latest_date = max(r['trade_date'] for r in data) if data else None
    cnt_no_latest = 0
    cnt_filter = {name: 0 for name, _ in STOCK_FILTERS}
    result = []

    for code, info in stock_data.items():
        recs = info['records']
        # 最新交易日无行情（停牌等）不参与
        if not recs or recs[-1]['trade_date'] != latest_date:
            cnt_no_latest += 1
            continue
        # 剔除 close<=0 脏数据
        recs = [r for r in recs if r['close'] is not None and r['close'] > 0]
        if len(recs) < MIN_BARS:
            cnt_filter[f'有效交易日不足{MIN_BARS}日'] += 1
            continue

        ctx = build_context(code, recs)
        ok, reasons = apply_filters(ctx)
        if not ok:
            for name in reasons:
                cnt_filter[name] += 1
            continue

        result.append({
            'ts_code': code,
            'stock_name': info['stock_name'],
            'close': ctx['close'],
            'pct_chg': ctx['pct_chg'],
            'total_mv': ctx['total_mv'],
            'vol_z60': ctx['vol_z60'],
            'feature_score': ctx['feature_score'],
            'hit_features': '、'.join(ctx['hit_features']),
        })

    # 按特征得分降序（命中特征越多信号越强），同分按 vol_z60 降序
    result.sort(key=lambda x: (x['feature_score'],
                               x['vol_z60'] if x['vol_z60'] is not None else -999),
                reverse=True)
    return result, latest_date, len(stock_data), cnt_no_latest, cnt_filter


# ---------- 主流程 ----------

def main():
    target_date = get_target_date()
    print("=" * 80)
    print(f"🌊 V形反转 / 底部放量突破选股 — 目标日期 {target_date}")
    print(f"   F1长期下跌 | F2底部缩量横盘 | F3双底抬高 | F4放量突破大阳 | F5收复MA60 | F6连续放量上攻")
    print(f"   信号 = (近45日F2∨F3) ∧ (近90日F1) ∧ (vol_z60≥3∨F4) ∧ (F5∨F6)")
    print(f"   门槛：市值>{MIN_MARKET_CAP_WAN/10000:.0f}亿 | SSS>{MIN_SHORT_STRENGTH:g} | 交易日≥{MIN_BARS}")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    result, latest_date, total, cnt_no_latest, cnt_filter = analyze_stocks(data)

    # ---------- 漏斗 ----------
    print("\n" + "=" * 72)
    print(f"  股票总数: {total}")
    print(f"  - 最新日停牌无数据: {cnt_no_latest}")
    for name, cnt in cnt_filter.items():
        print(f"  - 过滤[{name}]: {cnt}")
    print("-" * 72)
    print(f"  入选: {len(result)}（按特征得分降序）")
    print("=" * 72)

    if not result:
        print("⚠️ 无符合V形反转/底部放量突破的股票，不生成文件夹/CSV")
        return

    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, 'V形反转.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '最新收盘', '当日涨幅%', 'vol_z60',
                         '特征得分', '命中特征', '总市值(万)'])
        for s in result[:TOP_N]:
            writer.writerow([
                s['ts_code'], s['stock_name'],
                f"{s['close']:.2f}" if s['close'] is not None else '',
                f"{s['pct_chg']:.2f}" if s['pct_chg'] is not None else '',
                f"{s['vol_z60']:.2f}" if s['vol_z60'] is not None else '',
                s['feature_score'], s['hit_features'],
                f"{s['total_mv']:.0f}" if s['total_mv'] is not None else '',
            ])
    print(f"✅ CSV已生成（{min(TOP_N, len(result))}只）: {csv_path}")

    # 选股结果入库（便于回测，取 Top N）
    record_selected_stocks(
        'V形反转选股',
        [{'ts_code': s['ts_code'], 'selected': 1} for s in result[:TOP_N]],
        latest_date)

    print(f"\n🔥 V形反转 / 底部放量突破前{min(TOP_N, len(result))}：")
    for i, s in enumerate(result[:TOP_N], 1):
        vz = f"{s['vol_z60']:.1f}" if s['vol_z60'] is not None else '-'
        pc = f"{s['pct_chg']:.1f}%" if s['pct_chg'] is not None else '-'
        print(f"{i:>2}. {s['ts_code']:<11} {s['stock_name']:<7} "
              f"收{s['close']:.2f}({pc}) "
              f"特征得分{s['feature_score']} vol_z60={vz} "
              f"市值{s['total_mv']/10000:.0f}亿 [{s['hit_features']}]")


if __name__ == '__main__':
    main()
