#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
V形反转选股 (select_v_reverse.py)

对齐 prompt#L408-411：
  1. 读取 stock_daily_t 最近 120 个交易日 + stock_daily_basic_info_t 最近 1 个交易日（ts_code 关联）
  2. 选股条件：
     0. 最近一个交易日总市值 total_mv（万元）> 100亿
     1. 过去 120 个交易日内 最低收盘价 close / 最高收盘价 close < 50%
     2. 最低收盘价出现在最近 30 个交易日内
     3. 最近 30 个交易日 turning_point 出现至少 10 个「上升」
     4. 最近一个交易日 ma5 > ma30
  3. 仅 A 股（.SH / .SZ），最新交易日有数据（剔除停牌）
  4. 全部条件以 STOCK_FILTERS 注册式过滤实现（参考 find_similar_ma5.py）：
     A股板块、最新日市值>100亿、历史≥120日、收盘价无缺失、最低/最高<50%、
     最低收盘在最近30日内、近30日上升≥10天、最新日ma5>ma30；新增条件只需追加一行注册
  5. CSV「V形反转.csv」（csv.writer + utf-8-sig，含极值信息）
  6. 文件夹「V形反转+当日日期后缀」（已存在则复用）；结果为0不生成文件/文件夹
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

# ---------- 策略参数 ----------
LOOKBACK_DAYS = 120        # 回看交易日数（用于最低/最高收盘价）
RECENT_DAYS = 30           # 「最近N个交易日」窗口：最低点必须落在该窗口内、上升数统计窗口
AMP_RATIO_MAX = 0.50       # 最低收盘价 / 最高收盘价 < 此值
MIN_RISE_DAYS = 10         # 最近30日 turning_point="上升" 的最少天数
MIN_MARKET_CAP_WAN = 1_000_000  # 总市值下限（万元）= 100亿
MIN_SHORT_STRENGTH = 60.0       # 最新日短线强弱得分>60
TOP_N = 50

# ---------- 灵活过滤条件（STOCK_FILTERS 注册表，参考 find_similar_ma5.py） ----------
# 过滤函数入参为 build_context(records) 生成的单只股票特征上下文，返回 True=通过；
# 通过全部前置过滤的股票即为V形反转入选标的。

def build_context(records):
    """计算单只股票的V形反转特征上下文。

    records 按日期升序。数据缺失/除零等异常时相关特征取 None，由对应过滤条件拦截，
    计算口径与原 detect_v_reverse 完全一致（min/max 基于全部收盘价、index 取首次最小值）。
    """
    n = len(records)
    latest = records[-1]
    closes = [r.get('close') for r in records]
    close_complete = n > 0 and all(c is not None for c in closes)

    lo = hi = min_idx = amp_ratio = None
    min_date = None
    if close_complete:
        lo = min(closes)
        hi = max(closes)
        min_idx = closes.index(lo)
        min_date = records[min_idx]['trade_date']
        if hi is not None and hi > 0:
            amp_ratio = lo / hi

    recent = records[-RECENT_DAYS:]
    rise_cnt = sum(1 for r in recent if r.get('turning_point') == '上升')

    return {
        'ts_code': latest.get('ts_code'),
        'bars': n,
        'close_complete': close_complete,
        'lo': lo,
        'hi': hi,
        'min_idx': min_idx,
        'min_date': min_date,
        'amp_ratio': amp_ratio,
        'rise_cnt': rise_cnt,
        'ma5': latest.get('ma5'),
        'ma30': latest.get('ma30'),
        'total_mv': latest.get('total_mv'),
        'close': latest.get('close'),
        'short_strength_score': (float(latest.get('short_strength_score'))
                                 if latest.get('short_strength_score') is not None else None),
    }


def _filter_a_share(ctx):
    """仅保留沪深A股（.SH/.SZ），剔除北交所等。"""
    code = ctx['ts_code'] or ''
    return code.endswith('.SH') or code.endswith('.SZ')


def _filter_min_market_cap(ctx):
    """最近一个交易日总市值 total_mv（万元）> MIN_MARKET_CAP_WAN。"""
    mv = ctx['total_mv']
    return mv is not None and mv > MIN_MARKET_CAP_WAN


def _filter_enough_bars(ctx):
    """有效交易日不少于 LOOKBACK_DAYS（120日）。"""
    return ctx['bars'] >= LOOKBACK_DAYS


def _filter_close_complete(ctx):
    """全部交易日收盘价无缺失。"""
    return ctx['close_complete']


def _filter_deep_drawdown(ctx):
    """过去120日 最低收盘价/最高收盘价 < 50%（极值异常时不通过）。"""
    return ctx['amp_ratio'] is not None and ctx['amp_ratio'] < AMP_RATIO_MAX


def _filter_low_in_recent(ctx):
    """最低收盘价出现在最近 RECENT_DAYS（30日）个交易日内。"""
    return ctx['min_idx'] is not None and ctx['min_idx'] >= ctx['bars'] - RECENT_DAYS


def _filter_rise_days(ctx):
    """最近30个交易日 turning_point='上升' 至少 MIN_RISE_DAYS（10）天。"""
    return ctx['rise_cnt'] >= MIN_RISE_DAYS


def _filter_ma_bull(ctx):
    """最近一个交易日 ma5 > ma30（缺失或ma30<=0不通过）。"""
    ma5, ma30 = ctx['ma5'], ctx['ma30']
    return ma5 is not None and ma30 is not None and ma30 > 0 and ma5 > ma30


def _filter_short_strength(ctx):
    """最新一个交易日短线强弱得分 > MIN_SHORT_STRENGTH（无得分视为不通过）。"""
    s = ctx.get('short_strength_score')
    return s is not None and s > MIN_SHORT_STRENGTH


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)
STOCK_FILTERS = [
    ('非沪深A股', _filter_a_share),
    (f'最新日总市值<={MIN_MARKET_CAP_WAN/10000:.0f}亿', _filter_min_market_cap),
    (f'有效交易日不足{LOOKBACK_DAYS}日', _filter_enough_bars),
    ('存在收盘价缺失日', _filter_close_complete),
    (f'最低/最高收盘>={AMP_RATIO_MAX*100:.0f}%(或极值异常)', _filter_deep_drawdown),
    (f'最低收盘不在最近{RECENT_DAYS}日内', _filter_low_in_recent),
    (f'近{RECENT_DAYS}日上升天数<{MIN_RISE_DAYS}', _filter_rise_days),
    ('最新日ma5未大于ma30', _filter_ma_bull),
    (f'短线强弱得分<={MIN_SHORT_STRENGTH:g}', _filter_short_strength),
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


def get_folder_name():
    return f"V形反转{get_target_date()}"


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
    """读取 stock_daily_t 全市场 [start, end]，LEFT JOIN basic 取最新日 total_mv。

    basic 用最近1个交易日关联即可（prompt：stock_daily_basic_info_t 最近1个交易日）。
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT d.ts_code, d.trade_date, d.close, d.ma5, d.ma30,
               d.short_strength_score, d.turning_point, b.total_mv
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


# ---------- 主流程 ----------

def main():
    target_date = get_target_date()
    print("=" * 80)
    print(f"📐 V形反转选股 — 目标日期 {target_date}")
    print(f"   回看{LOOKBACK_DAYS}日 | 最低/最高<{AMP_RATIO_MAX*100:.0f}% | "
          f"最低收盘在最近{RECENT_DAYS}日内 | 近{RECENT_DAYS}日上升≥{MIN_RISE_DAYS}天 | ma5>ma30")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    stock_data = {}
    for r in data:
        stock_data.setdefault(r['ts_code'], []).append({
            'ts_code': r['ts_code'],
            'trade_date': r['trade_date'],
            'close': float(r['close']) if r['close'] is not None else None,
            'ma5': float(r['ma5']) if r['ma5'] is not None else None,
            'ma30': float(r['ma30']) if r['ma30'] is not None else None,
            'turning_point': r['turning_point'],
            'total_mv': float(r['total_mv']) if r['total_mv'] is not None else None,
        })

    latest_date = trade_dates[-1]
    cnt_no_latest = 0
    cnt_filter = {name: 0 for name, _ in STOCK_FILTERS}
    result = []

    for code, recs in stock_data.items():
        # 最新交易日无行情（停牌等）不参与，与 find_similar_ma5 的日期对齐口径一致
        if recs[-1]['trade_date'] != latest_date:
            cnt_no_latest += 1
            continue

        # 特征上下文 + 注册式过滤（板块/市值/历史/收盘价/深度回撤/低点位置/上升天数/均线多头）
        ctx = build_context(recs)
        ok, reasons = apply_filters(ctx)
        if not ok:
            for name in reasons:
                cnt_filter[name] += 1
            continue

        result.append({
            'ts_code': code,
            'min_date': ctx['min_date'],
            'min_close': round(ctx['lo'], 2),
            'max_close': round(ctx['hi'], 2),
            'amp_ratio': round(ctx['amp_ratio'], 3),
            'rise_cnt': ctx['rise_cnt'],
            'ma5': round(ctx['ma5'], 2),
            'ma30': round(ctx['ma30'], 2),
            'total_mv': ctx['total_mv'],
            'close': ctx['close'],
        })

    result.sort(key=lambda x: x['amp_ratio'])  # 振幅比越小（跌幅越深）排越前

    # ---------- 漏斗 ----------
    print("\n" + "=" * 72)
    print(f"  股票总数: {len(stock_data)}")
    print(f"  - 最新日停牌无数据: {cnt_no_latest}")
    for name, cnt in cnt_filter.items():
        print(f"  - 过滤[{name}]: {cnt}")
    print("-" * 72)
    print(f"  入选: {len(result)}（按振幅比升序）")
    print("=" * 72)

    if not result:
        print("⚠️ 无符合V形反转的股票，不生成文件夹/CSV")
        return

    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, 'V形反转.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '最低收盘日', '最低收盘', '最高收盘', '最低/最高%',
                         f'近{RECENT_DAYS}日上升天数', 'ma5', 'ma30',
                         '总市值(万)', '最新收盘'])
        for s in result[:TOP_N]:
            writer.writerow([
                s['ts_code'], s['min_date'], s['min_close'], s['max_close'],
                f"{s['amp_ratio']*100:.1f}", s['rise_cnt'], s['ma5'], s['ma30'],
                f"{s['total_mv']:.0f}", s['close'],
            ])
    print(f"✅ CSV已生成（{min(TOP_N, len(result))}只）: {csv_path}")

    # 选股结果入库（便于回测，与CSV内容一致取Top N）
    record_selected_stocks(
        'V形反转选股',
        [{'ts_code': s['ts_code'], 'selected': 1} for s in result[:TOP_N]],
        latest_date)

    print(f"\n🔥 V形反转前{min(TOP_N, len(result))}（按振幅比升序）：")
    for i, s in enumerate(result[:TOP_N], 1):
        print(f"{i:>2}. {s['ts_code']:<11} 最低={s['min_close']:.2f}({s['min_date']}) "
              f"最高={s['max_close']:.2f} 最低/最高={s['amp_ratio']*100:.1f}% "
              f"近{RECENT_DAYS}日上升={s['rise_cnt']}天 ma5={s['ma5']:.2f}>ma30={s['ma30']:.2f} "
              f"市值={s['total_mv']/10000:.0f}亿")


if __name__ == '__main__':
    main()
