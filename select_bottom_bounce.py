#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: 底部反弹选股策略

选股条件采用 STOCK_FILTERS 注册表方式（参考 find_similar_ma5.py），
新增条件只需追加一个 _filter_xxx 函数与一行注册；apply_filters 统一执行并返回未通过原因。

选股条件（对齐 prompt#L360-366）：
1. 读取 stock_daily_t 最近 200 个交易日 + stock_daily_basic_info_t（LEFT JOIN 按 ts_code+trade_date 取 total_mv）
2. 取满足如下条件的股票：
   a. 最近一个交易日总市值 total_mv（万元）> 50亿（500,000 万元）
   b. 过去 200 个交易日 最低收盘价/最高收盘价 < 50%，且最低收盘价出现在最近 30 个交易日内（空间足够）
   c. 最近 10 个交易日 ma30 逐渐走平：T日与T-10日的 ma30 连线斜率 > 0（筑底时间足够）
   d. 最近 3 个交易日 ma5 均高于 ma30
   e. 最新一个交易日短线强弱得分 short_strength_score > 75
3. 根据股票代码去除非 A 股（仅保留 .SH / .SZ）
4. CSV 输出对齐 select_2wave_up_v2.py（csv.writer + utf-8-sig）
5. 文件夹「底部反弹+当日日期后缀」（已存在则删除重建）
"""

import os
import sys
import csv
import shutil
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


# ---------- 策略常量 ----------
MIN_BARS = 200               # 有效交易日下限（近200个交易日）
MIN_TOTAL_MV_WAN = 500_000   # 最新日总市值下限（万元）= 50亿
RANGE_MAX_RATIO = 0.50       # 近200日 最低收盘/最高收盘 上限
RECENT_DAYS = 30             # 最低收盘价须出现在最近N个交易日内
MA30_SLOPE_DAYS = 10         # ma30斜率观察期：T日 vs T-N日
RECENT_MA_DAYS = 3           # 最近N个交易日 ma5 均须高于 ma30
MIN_SHORT_STRENGTH = 70      # 最新日短线强弱得分下限


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    """返回 target_date 前（含）最近 n 个交易日列表，预留日历日缓冲。"""
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


def create_folder(target_date):
    """新建（已存在的删除重建）底部反弹+日期后缀的文件夹，返回路径"""
    folder_name = f"底部反弹{target_date}"
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


# ---------- 核心逻辑 ----------

def read_stock_data(start_date, end_date):
    """
    读取 stock_daily_t + stock_daily_basic_info_t 在 [start_date, end_date] 区间，
    LEFT JOIN 按 ts_code + trade_date，获取 close/total_mv/short_strength_score
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []

    query_sql = """
        SELECT
            d.ts_code,
            d.trade_date,
            d.close,
            d.short_strength_score,
            b.total_mv
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code  = b.ts_code
              AND d.trade_date = b.trade_date
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


# ---------- 灵活过滤条件（新增条件只需在 STOCK_FILTERS 中追加一行） ----------

def _ma30(closes, end_idx):
    """closes 中以 end_idx 为末日（含）的30日简单均线，需 end_idx >= 29。"""
    return sum(closes[end_idx - 29:end_idx + 1]) / 30.0


def _ma5(closes, end_idx):
    """closes 中以 end_idx 为末日（含）的5日简单均线，需 end_idx >= 4。"""
    return sum(closes[end_idx - 4:end_idx + 1]) / 5.0


def _filter_enough_bars(records):
    """有效交易日不少于200日。"""
    return len(records) >= MIN_BARS


def _filter_total_mv(records):
    """最近一个有市值记录的交易日总市值 > 50亿。"""
    for r in reversed(records):
        if r['total_mv'] > 0:
            return r['total_mv'] >= MIN_TOTAL_MV_WAN
    return False


def _filter_200d_range(records):
    """近200个交易日 最低收盘价/最高收盘价 < 50%（空间足够）。"""
    window = records[-MIN_BARS:]
    closes = [r['close'] for r in window]
    max_close = max(closes)
    if max_close <= 0:
        return False
    return (min(closes) / max_close) < RANGE_MAX_RATIO


def _filter_min_in_recent(records):
    """近200日最低收盘价出现在最近30个交易日内。"""
    window = records[-MIN_BARS:]
    closes = [r['close'] for r in window]
    min_close = min(closes)
    return any(r['close'] == min_close for r in window[-RECENT_DAYS:])


def _filter_ma30_slope(records):
    """T日与T-10日的ma30连线斜率>0（ma30走平回升，筑底时间足够）。

    ma30 由窗口内收盘价自算（30日简单均线），避免依赖表中 ma30 的历史回填。
    """
    if len(records) < MA30_SLOPE_DAYS + 30:
        return False
    closes = [r['close'] for r in records]
    ma30_t = _ma30(closes, len(closes) - 1)
    ma30_t10 = _ma30(closes, len(closes) - 1 - MA30_SLOPE_DAYS)
    return ma30_t > ma30_t10


def _filter_ma5_above_ma30(records):
    """最近3个交易日 ma5 均高于 ma30（均线多头确认）。"""
    if len(records) < RECENT_MA_DAYS + 30:
        return False
    closes = [r['close'] for r in records]
    for k in range(RECENT_MA_DAYS):
        end_idx = len(closes) - 1 - k
        if _ma5(closes, end_idx) <= _ma30(closes, end_idx):
            return False
    return True


def _filter_short_strength(records):
    """最新一个交易日短线强弱得分 > MIN_SHORT_STRENGTH（无得分视为不通过）。"""
    s = records[-1].get('short_strength_score')
    return s is not None and s > MIN_SHORT_STRENGTH


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)；函数入参为该股票记录（按日期升序、已剔除close<=0脏数据），返回 True=通过
STOCK_FILTERS = [
    (f'有效交易日不足{MIN_BARS}日', _filter_enough_bars),
    (f'最新日总市值<{MIN_TOTAL_MV_WAN/10000:.0f}亿', _filter_total_mv),
    (f'近{MIN_BARS}日最低/最高收盘>={RANGE_MAX_RATIO*100:.0f}%', _filter_200d_range),
    (f'最低收盘价不在近{RECENT_DAYS}个交易日内', _filter_min_in_recent),
    (f'T日与T-{MA30_SLOPE_DAYS}日ma30连线斜率<=0', _filter_ma30_slope),
    (f'近{RECENT_MA_DAYS}日存在ma5<=ma30', _filter_ma5_above_ma30),
    (f'短线强弱得分<={MIN_SHORT_STRENGTH:g}', _filter_short_strength),
]


def apply_filters(records):
    """依次执行 STOCK_FILTERS 中的全部过滤条件，返回 (是否通过, 未通过的条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(records)]
    return (len(reasons) == 0), reasons


def analyze_stocks(data):
    """逐只股票执行底部反弹选股逻辑（STOCK_FILTERS 注册式过滤）"""

    # ---------- 组装 ----------
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        total_mv = record['total_mv']
        score = record.get('short_strength_score')
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'close':      float(record['close'] or 0),
            'total_mv':   float(total_mv) if total_mv is not None else 0.0,
            'short_strength_score': float(score) if score is not None else None,
        })

    result = []
    cnt_not_a = 0       # 非A股
    count_empty = 0     # 清理脏数据后无有效行情
    cnt_filtered = {}   # 各过滤条件淘汰数（一只股票可同时计入多个条件）

    for ts_code, records in stock_data.items():

        # ---------- 非A股过滤 ----------
        if not (ts_code.endswith('.SH') or ts_code.endswith('.SZ')):
            cnt_not_a += 1
            continue

        # 清理停牌等脏数据（close<=0），按日期排序
        records = [r for r in records if r['close'] > 0]
        if not records:
            count_empty += 1
            continue
        records.sort(key=lambda x: x['trade_date'])

        # 注册式过滤：统一执行全部条件，返回未通过原因列表
        ok, reasons = apply_filters(records)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
            continue

        # ---------- 结果明细 ----------
        all_closes = [r['close'] for r in records]
        win_closes = all_closes[-MIN_BARS:]
        min_close = min(win_closes)
        max_close = max(win_closes)
        ma30_t = _ma30(all_closes, len(all_closes) - 1)
        ma30_t10 = _ma30(all_closes, len(all_closes) - 1 - MA30_SLOPE_DAYS)

        latest_mv = 0.0
        for r in reversed(records):
            if r['total_mv'] > 0:
                latest_mv = r['total_mv']
                break

        result.append({
            'ts_code':    ts_code,
            'close':      records[-1]['close'],
            'ma30':       ma30_t,
            'ma30_slope': (ma30_t - ma30_t10) / MA30_SLOPE_DAYS,
            'total_mv':   latest_mv,
            'min_close':  min_close,
            'max_close':  max_close,
            'range_pct':  min_close / max_close * 100 if max_close > 0 else 0,
        })

    # 按市值从大到小排序
    result.sort(key=lambda x: x['total_mv'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print("底部反弹选股策略 · 过滤漏斗")
    print("-" * 40)
    print(f"  股票总数量:                 {len(stock_data)}")
    if cnt_not_a:
        print(f"  - 非A股过滤:                {cnt_not_a}")
    if count_empty:
        print(f"  - 无有效行情(close<=0) 淘汰: {count_empty}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print("-" * 40)
    print(f"  最终选出:                   {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    """CSV 输出对齐 select_2wave_up_v2.py：csv.writer + utf-8-sig + 表头 股票代码"""
    csv_filename = "底部反弹.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for stock in stocks:
            writer.writerow([stock['ts_code']])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


# ---------- 主入口 ----------

def main():
    target_date = get_target_date()

    print("=" * 80)
    print("📊 底部反弹选股策略 (200日深调空间+最低点近30日+ma30走平回升+ma5多头+短线得分)")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 基础过滤：A股上市，最新日总市值 > 50亿")
    print("  2. 近200个交易日 最低收盘价/最高收盘价 < 50%")
    print("  3. 最低收盘价出现在最近30个交易日内")
    print("  4. T日与T-10日的ma30连线斜率 > 0（ma30走平回升）")
    print(f"  5. 最近3个交易日 ma5 均高于 ma30")
    print(f"  6. 最新交易日短线强弱得分 > {MIN_SHORT_STRENGTH}")
    print("=" * 80)

    # ---------- 步骤A：获取最近 200 个交易日 ----------
    print(f"\n📅 目标日期: {target_date}")
    trade_dates = get_last_n_trade_dates(target_date, 200)
    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    print(f"   查询区间: {start_date} ~ {end_date}（共{len(trade_dates)}个交易日）")

    # ---------- 步骤B：读取 ----------
    data = read_stock_data(start_date, end_date)
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    # ---------- 步骤C：选股 ----------
    selected = analyze_stocks(data)
    print(f"\n✅ 共选出 {len(selected)} 只满足条件的股票")

    if selected:
        folder_path = create_folder(target_date)
        csv_path = generate_csv_file(selected, folder_path)
        print("\n" + "=" * 80)
        print("🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n🔥 精选股票（按市值降序，前20只）：")
        for i, s in enumerate(selected[:20], 1):
            print(
                f"{i:>2}. {s['ts_code']:<11} "
                f"收{s['close']:>8.2f} MA30{s['ma30']:>8.2f} | "
                f"市值{s['total_mv']/10000:>8.1f}亿 | "
                f"200日振幅: 低{s['min_close']:>8.2f}/高{s['max_close']:>8.2f}"
                f"={s['range_pct']:>5.1f}% | "
                f"ma30斜率{s['ma30_slope']:.4f}/日"
            )

        # 选股结果入库（便于回测）
        record_selected_stocks(
            '底部反弹选股策略',
            [{'ts_code': s['ts_code'], 'selected': 1} for s in selected],
            end_date)
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
