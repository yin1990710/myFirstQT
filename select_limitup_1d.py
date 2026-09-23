#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: 放量涨停回调选股策略

读取 stock_daily_t 最近 10 个交易日数据和 stock_daily_basic_info_t 最近 10 个交易日数据，
用 ts_code 和 trade_date 关联，选出满足以下条件的股票：
1. 记最近一个交易日为A日，要求A日总市值 total_mv >= 100亿
2. 最近10个交易日至少有1个交易日涨幅 (close - 前一日close) / 前一日close × 100% > 9.5%，记录为B日
3. B日距离A日至少有4个交易日（不含B日）；且B日次日至A日（含A日）每个交易日涨跌幅绝对值 < 5%
4. 最近3个交易日 ma5 > ma30
5. 最近10个交易日中，阳线数 >= 4，且阳线平均成交量大于阴线平均成交量的1.5倍
   （阳线=close>open，阴线=close<open，平盘不计入；无阴线时量比条件自动满足）

输出：CSV「放量大涨回调.csv」（股票代码、股票名称、涨幅，utf-8-sig），
文件夹「当日涨停+当日日期后缀」（已存在则复用）；无结果不生成文件/文件夹。
"""

import os
import sys
import csv
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


# ---------- 策略参数 ----------
LOOKBACK_DAYS = 10              # 数据读取窗口（最近交易日数）
MIN_MARKET_CAP_WAN = 1_000_000  # A日总市值下限（万元）= 100亿
SURGE_PCT = 9.5                 # B日单日涨幅阈值（%）
MIN_GAP_DAYS = 4                # B日距A日最少交易日数（不含B日）
PULLBACK_ABS_PCT = 5.0          # B日次日~A日单日涨跌幅绝对值上限（%）
RECENT_MA_DAYS = 3              # 最近需满足 ma5 > ma30 的交易日数
MIN_YANG_DAYS = 4               # 窗口内阳线最少根数
YANG_VOL_RATIO = 1.5            # 阳线平均成交量 / 阴线平均成交量 下限


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    """返回 target_date 前（含）最近 n 个交易日列表。"""
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 10)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) > n:
        opens = opens[-n:]
    return opens


def get_folder_path():
    """文件夹「当日涨停+日期后缀」，已存在则复用。"""
    folder_name = f"当日涨停{get_target_date()}"
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    os.makedirs(folder_path, exist_ok=True)
    print(f"📁 文件夹: {folder_name}")
    return folder_path


# ---------- 灵活过滤条件（STOCK_FILTERS 注册表，参考 find_similar_ma5.py） ----------
# 过滤函数入参为 build_context(...) 生成的单只股票特征上下文，返回 True=通过。

def build_context(ts_code, stock_name, records, target_date):
    """计算单只股票的放量涨停回调特征上下文。records 已按日期升序、剔除 close<=0 脏数据。"""
    latest_mv = 0.0
    for r in reversed(records):
        if r['total_mv'] > 0:
            latest_mv = r['total_mv']
            break

    is_target = bool(records) and records[-1]['trade_date'] == target_date
    window = records[-LOOKBACK_DAYS:]

    # 逐日涨跌幅：严格按 (close - 前一日close) / 前一日close × 100%
    for r in window:
        pc = r['pre_close']
        r['pct_chg'] = ((r['close'] - pc) / pc * 100) if pc and pc > 0 else None

    # B日：窗口内位置距A日（窗口最后一日）至少 MIN_GAP_DAYS 个交易日（不含B日），
    # 且B日次日至A日（含A日）每个交易日涨跌幅绝对值 < PULLBACK_ABS_PCT%
    b_date = None
    b_pct = None
    last_idx = len(window) - 1
    for idx, r in enumerate(window):
        if last_idx - idx < MIN_GAP_DAYS:
            break
        pct = r['pct_chg']
        if pct is None or pct <= SURGE_PCT:
            continue
        later = window[idx + 1:]
        if all(x['pct_chg'] is not None and abs(x['pct_chg']) < PULLBACK_ABS_PCT
               for x in later):
            b_date, b_pct = r['trade_date'], pct
            break

    # 最近3日 ma5 > ma30（均线需为正）
    ma_bull = all(
        r['ma5'] > 0 and r['ma30'] > 0 and r['ma5'] > r['ma30']
        for r in window[-RECENT_MA_DAYS:]
    ) if len(window) >= RECENT_MA_DAYS else False

    # 阳线/阴线统计（成交量单位：手）
    yang_vols, yin_vols = [], []
    for r in window:
        if r['close'] > r['open']:
            if r['vol'] and r['vol'] > 0:
                yang_vols.append(r['vol'])
        elif r['close'] < r['open']:
            if r['vol'] and r['vol'] > 0:
                yin_vols.append(r['vol'])
    yang_days = sum(1 for r in window if r['close'] > r['open'])
    yang_avg_vol = sum(yang_vols) / len(yang_vols) if yang_vols else 0.0
    yin_avg_vol = sum(yin_vols) / len(yin_vols) if yin_vols else 0.0

    return {
        'ts_code': ts_code,
        'stock_name': stock_name,
        'records': records,
        'bars': len(records),
        'is_target': is_target,
        'latest_mv': latest_mv,
        'a_pct': window[-1]['pct_chg'] if window else None,
        'b_date': b_date,
        'b_pct': b_pct,
        'ma_bull': ma_bull,
        'yang_days': yang_days,
        'yang_avg_vol': yang_avg_vol,
        'yin_avg_vol': yin_avg_vol,
    }


def _filter_target_date_and_bars(ctx):
    """A日有行情（最新记录为目标交易日）且有效交易日 >= LOOKBACK_DAYS（10日）。"""
    return ctx['is_target'] and ctx['bars'] >= LOOKBACK_DAYS


def _filter_min_market_cap(ctx):
    """A日总市值 >= 100亿。"""
    return ctx['latest_mv'] >= MIN_MARKET_CAP_WAN


def _filter_surge_pullback(ctx):
    """存在B日：涨幅>9.5%、距A日>=4个交易日，且B日次日至A日涨跌幅绝对值均<5%。"""
    return ctx['b_date'] is not None


def _filter_ma5_above_ma30(ctx):
    """最近 RECENT_MA_DAYS（3）个交易日 ma5 > ma30。"""
    return ctx['ma_bull']


def _filter_yang_count(ctx):
    """近10日阳线数 >= MIN_YANG_DAYS（4根）。"""
    return ctx['yang_days'] >= MIN_YANG_DAYS


def _filter_yang_volume(ctx):
    """阳线平均成交量 > 阴线平均成交量的 YANG_VOL_RATIO（1.5）倍（无阴线自动满足）。"""
    if ctx['yin_avg_vol'] <= 0:
        return True
    return ctx['yang_avg_vol'] > YANG_VOL_RATIO * ctx['yin_avg_vol']


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)
STOCK_FILTERS = [
    (f'非A日行情或有效交易日不足{LOOKBACK_DAYS}日', _filter_target_date_and_bars),
    (f'A日总市值<{MIN_MARKET_CAP_WAN/10000:.0f}亿', _filter_min_market_cap),
    (f'无涨幅>{SURGE_PCT:g}%且距A日>={MIN_GAP_DAYS}日、随后回调<{PULLBACK_ABS_PCT:g}%的B日', _filter_surge_pullback),
    (f'近{RECENT_MA_DAYS}日存在ma5<=ma30', _filter_ma5_above_ma30),
    (f'近{LOOKBACK_DAYS}日阳线<{MIN_YANG_DAYS}根', _filter_yang_count),
    (f'阳线平均量未超阴线{YANG_VOL_RATIO:g}倍', _filter_yang_volume),
]


def apply_filters(ctx):
    """依次执行 STOCK_FILTERS 全部条件，返回 (是否通过, 未通过条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(ctx)]
    return (len(reasons) == 0), reasons


# ---------- 核心逻辑 ----------

def read_stock_data(start_date, end_date):
    """
    读取 stock_daily_t + stock_daily_basic_info_t + stock_info_t 在 [start_date, end_date] 区间，
    按 ts_code + trade_date 关联，获取 open/close/pre_close/vol/ma5/ma30/total_mv/stock_name
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []

    query_sql = """
        SELECT
            d.ts_code,
            d.trade_date,
            d.open,
            d.close,
            d.pre_close,
            d.vol,
            d.ma5,
            d.ma30,
            b.total_mv,
            i.stock_name
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        LEFT JOIN stock_info_t i
               ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
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


def analyze_stocks(data, target_date):
    """逐只股票执行选股逻辑"""

    # ---------- 组装 ----------
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = {
                'records': [],
                'stock_name': record['stock_name'] or '',
            }
        total_mv = record['total_mv']
        total_mv = float(total_mv) if total_mv is not None else 0.0
        stock_data[ts_code]['records'].append({
            'trade_date': record['trade_date'],
            'open':       float(record['open'] or 0),
            'close':      float(record['close'] or 0),
            'pre_close':  float(record['pre_close'] or 0),
            'vol':        (float(record['vol']) if record['vol'] is not None else 0.0),
            'ma5':        (float(record['ma5']) if record['ma5'] is not None else 0.0),
            'ma30':       (float(record['ma30']) if record['ma30'] is not None else 0.0),
            'total_mv':   total_mv,
        })

    result = []
    cnt_empty = 0      # 清理停牌等脏数据（close<=0）后无有效行情
    cnt_filtered = {}  # 各过滤条件淘汰数（一只股票可同时计入多个条件）

    for ts_code, info in stock_data.items():
        records = [r for r in info['records'] if r['close'] > 0]
        if not records:
            cnt_empty += 1
            continue
        records.sort(key=lambda x: x['trade_date'])

        ctx = build_context(ts_code, info['stock_name'], records, target_date)
        ok, reasons = apply_filters(ctx)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
            continue

        result.append({
            'ts_code':     ts_code,
            'stock_name':  info['stock_name'],
            'pct_chg':     ctx['a_pct'] if ctx['a_pct'] is not None else 0.0,
            'b_date':      ctx['b_date'],
            'b_pct':       ctx['b_pct'],
            'total_mv':    ctx['latest_mv'],
        })

    # 按A日涨幅从大到小排序
    result.sort(key=lambda x: x['pct_chg'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print("放量涨停回调选股策略 · 过滤漏斗")
    print("-" * 40)
    print(f"  股票总数量:                      {len(stock_data)}")
    if cnt_empty:
        print(f"  - 无有效行情(close<=0) 淘汰:     {cnt_empty}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print("-" * 40)
    print(f"  最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    """CSV「放量大涨回调.csv」：股票代码、股票名称、涨幅（A日），utf-8-sig"""
    csv_filename = "放量大涨回调.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '股票名称', '涨幅'])
        for stock in stocks:
            writer.writerow([stock['ts_code'], stock['stock_name'],
                             f"{stock['pct_chg']:.2f}%"])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


# ---------- 主入口 ----------

def main():
    target_date = get_target_date()

    print("=" * 80)
    print("📊 放量涨停回调选股策略 (B日大涨>9.5%+回调<5%+近3日ma5>ma30+阳线放量)")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print(f"  1. A日（最近交易日）总市值 >= {MIN_MARKET_CAP_WAN/10000:.0f}亿")
    print(f"  2. 近{LOOKBACK_DAYS}日存在B日：涨幅>{SURGE_PCT:g}%，距A日>={MIN_GAP_DAYS}个交易日")
    print(f"  3. B日次日至A日单日涨跌幅绝对值 < {PULLBACK_ABS_PCT:g}%")
    print(f"  4. 近{RECENT_MA_DAYS}日 ma5 > ma30")
    print(f"  5. 近{LOOKBACK_DAYS}日阳线>={MIN_YANG_DAYS}根，阳线平均量>阴线{YANG_VOL_RATIO:g}倍")
    print("=" * 80)

    # ---------- 步骤A：获取最近 10 个交易日 ----------
    print(f"\n📅 目标日期: {target_date}")
    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    start_date, end_date = trade_dates[0], trade_dates[-1]
    print(f"   查询区间: {start_date} ~ {end_date}（共{len(trade_dates)}个交易日）")

    # ---------- 步骤B：读取 ----------
    data = read_stock_data(start_date, end_date)
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    # ---------- 步骤C：选股 ----------
    selected_stocks = analyze_stocks(data, target_date)
    print(f"\n✅ 共选出 {len(selected_stocks)} 只满足条件的股票")

    if not selected_stocks:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票，不生成文件/文件夹")
        print("=" * 80)
        return

    # ---------- 步骤D：有结果才创建文件夹并输出 ----------
    folder_path = get_folder_path()
    csv_path = generate_csv_file(selected_stocks, folder_path)

    print("\n" + "=" * 80)
    print("🎉 选股完成！")
    print(f"📁 文件夹路径: {folder_path}")
    print(f"📄 CSV路径: {csv_path}")
    print("=" * 80)

    print("\n🔥 精选股票（按A日涨幅降序，前20只）：")
    for i, s in enumerate(selected_stocks[:20], 1):
        print(
            f"{i:>2}. {s['ts_code']:<11} {s['stock_name']:<8} "
            f"A日{s['pct_chg']:>6.2f}% | "
            f"B日{s['b_date']}({s['b_pct']:.2f}%) | "
            f"市值{s['total_mv']/10000:>8.1f}亿"
        )

    # 选股结果入库（便于回测）
    record_selected_stocks(
        '放量涨停回调选股策略',
        [{'ts_code': s['ts_code'], 'selected': 1} for s in selected_stocks],
        end_date)


if __name__ == "__main__":
    main()
