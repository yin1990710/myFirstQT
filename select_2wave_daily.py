#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: 二浪日线选股策略

识别「第一波放量上涨 → 缩量回调 → 再次启动」的二浪结构。
选股条件：
1. 第一波涨幅（波段末日收盘价相对首日收盘价）> 15%
2. 回调至少 2 个交易日，且回调期间收盘价未有效跌破 MA30
   （允许 10% 误差，即收盘价不低于当日 MA30 × 0.9）
3. 缩量回调：调整期间平均成交额萎缩至放量上涨阶段平均成交额的 60% 以下
   （放量上涨阶段指成交额超过整个上涨期平均成交额 1.5 倍的交易日）
4. 最近一个交易日涨幅 > 5%，或最近 3 个交易日内 turning_point 出现「波谷」
5. 最近一个交易日总市值 total_mv（万元）> 2亿（20,000 万元）
6. 最近一个交易日成交额 amount × 1000 > 5亿
7. 最近一个交易日短线强弱得分 short_strength_score > 60

基础门槛：流通市值 circ_mv ≥ 50亿、有效交易日数据 ≥ 45 日。
过滤条件采用 STOCK_FILTERS 注册表方式，新增条件只需追加一个过滤函数与一行注册。

输出：CSV「二浪日线选股策略.csv」（utf-8-sig），文件夹「二浪日线选股策略+当日日期后缀」（已存在则复用）；
结果为 0 时不生成文件/文件夹。
"""

import os
import sys
import csv
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks


def get_target_date():
    now = datetime.now()
    current_hour = now.hour
    if current_hour < 15:
        target_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        target_date = now.strftime('%Y%m%d')
    return target_date


def get_folder_name():
    target_date = get_target_date()
    folder_name = f"二浪日线选股策略{target_date}"
    return folder_name


def get_folder_path():
    folder_name = get_folder_name()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_path = os.path.join(script_dir, folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"� 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


def read_stock_data(days=120):
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return []

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 30)).strftime('%Y%m%d')

    query_sql = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.open,
        d.close,
        d.high,
        d.low,
        d.amount,
        d.pct_chg,
        d.short_strength_score,
        b.total_mv,
        b.circ_mv
    FROM stock_daily_t d
    LEFT JOIN stock_daily_basic_info_t b ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
    WHERE d.trade_date >= %s AND d.trade_date <= %s
    ORDER BY d.ts_code, d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query_sql, (start_date, target_date))
            results = cursor.fetchall()
        connection.commit()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {target_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(connection)


def calculate_ma(close_prices, period=30):
    if len(close_prices) < period:
        return None
    return sum(close_prices[-period:]) / period


def find_turning_points(records):
    """识别波峰和波谷"""
    turning_points = []
    if len(records) < 5:
        return turning_points
    
    close_prices = [r['close'] for r in records]
    
    for i in range(2, len(records) - 2):
        prev_prev = close_prices[i-2]
        prev = close_prices[i-1]
        curr = close_prices[i]
        next_ = close_prices[i+1]
        next_next = close_prices[i+2]
        
        # 判断波谷：当前价格低于前后两天
        if curr < prev and curr < next_ and curr <= prev_prev and curr <= next_next:
            turning_points.append({
                'index': i,
                'date': records[i]['trade_date'],
                'type': 'trough',
                'price': curr
            })
    
    return turning_points


# ---------- 灵活过滤条件（新增条件只需在 STOCK_FILTERS 中追加一行） ----------
MIN_BARS = 45                  # 参与计算的最少有效交易日数
WAVE_WINDOW = 40               # 二浪结构识别窗口（近40个交易日）
MIN_FIRST_WAVE_DAYS = 8        # 第一波至少8个交易日
MIN_ADJUST_DAYS = 2            # 回调至少2个交易日
MIN_CIRC_MV_WAN = 500000       # 流通市值≥50亿（circ_mv单位万元）
MIN_TOTAL_MV_WAN = 20000       # 总市值≥2亿（total_mv单位万元）
MIN_AMOUNT_YI = 5.0            # 最新日成交额>5亿
MIN_FIRST_WAVE_GAIN = 15.0     # 第一波涨幅≥15%
MA30_BREAK_TOLERANCE = 0.90    # 回调收盘价不低于当日MA30×0.9（允许10%误差）
MAX_ADJUST_VOL_RATIO = 0.60    # 调整期均量≤放量上涨阶段均量的60%
MIN_TODAY_GAIN = 5.0           # 最新日涨幅≥5%
MIN_SHORT_STRENGTH = 60.0      # 最新日短线强弱得分>60


def _filter_circ_mv(ctx):
    """流通市值≥50亿。"""
    return (ctx['circ_mv'] or 0) >= MIN_CIRC_MV_WAN


def _filter_wave_structure(ctx):
    """近40日存在有效二浪结构（第一波≥8日、回调≥2日、MA30可覆盖回调段）。"""
    return ctx['wave_ok']


def _filter_first_wave_gain(ctx):
    """第一波涨幅≥15%。"""
    return (ctx['wave_ok']
            and ctx['first_wave_gain'] is not None
            and ctx['first_wave_gain'] >= MIN_FIRST_WAVE_GAIN)


def _filter_ma30_support(ctx):
    """回调期间收盘价未有效跌破MA30（允许10%误差）。"""
    return ctx['wave_ok'] and ctx['broke_ma30'] is False


def _filter_shrink_volume(ctx):
    """调整期平均成交额萎缩至放量上涨阶段的60%以下。"""
    return (ctx['wave_ok']
            and ctx['adjust_ratio'] is not None
            and ctx['adjust_ratio'] <= MAX_ADJUST_VOL_RATIO)


def _filter_start_signal(ctx):
    """最新日涨幅≥5%，或近3个交易日出现波谷。"""
    g = ctx['today_gain']
    return (g is not None and g >= MIN_TODAY_GAIN) or ctx['has_recent_trough']


def _filter_total_mv(ctx):
    """总市值≥2亿。"""
    return (ctx['total_mv'] or 0) >= MIN_TOTAL_MV_WAN


def _filter_amount(ctx):
    """最新日成交额>5亿（amount单位千元，amount×1000为元）。"""
    return (ctx['amount'] or 0) * 1000 > MIN_AMOUNT_YI * 1e8


def _filter_short_strength(ctx):
    """最新日短线强弱得分>75（无得分视为不通过）。"""
    s = ctx['short_strength_score']
    return s is not None and s > MIN_SHORT_STRENGTH


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)；函数入参为单只股票的特征上下文 ctx，返回 True=通过
STOCK_FILTERS = [
    (f'流通市值<{MIN_CIRC_MV_WAN / 10000:g}亿', _filter_circ_mv),
    ('近40日无有效二浪结构', _filter_wave_structure),
    (f'第一波涨幅<{MIN_FIRST_WAVE_GAIN:g}%', _filter_first_wave_gain),
    ('回调收盘价有效跌破MA30(容差10%)', _filter_ma30_support),
    ('调整期量能未萎缩至放量期60%以下', _filter_shrink_volume),
    (f'今日涨幅<{MIN_TODAY_GAIN:g}%且近3日无波谷', _filter_start_signal),
    (f'总市值<{MIN_TOTAL_MV_WAN / 10000:g}亿', _filter_total_mv),
    (f'最新日成交额<={MIN_AMOUNT_YI:g}亿', _filter_amount),
    (f'短线强弱得分<={MIN_SHORT_STRENGTH:g}', _filter_short_strength),
]


def apply_filters(ctx):
    """依次执行 STOCK_FILTERS 中的全部过滤条件，返回 (是否通过, 未通过的条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(ctx)]
    return (len(reasons) == 0), reasons


def build_context(records):
    """计算单只股票的二浪结构特征上下文。

    records 已按日期升序、剔除 close<=0 的脏数据、长度≥MIN_BARS。
    结构类前置条件不满足时 wave_ok=False，相关特征取 None，由对应过滤条件拦截。
    """
    latest = records[-1]
    prev = records[-2]

    ctx = {
        'latest': latest,
        'circ_mv': latest.get('circ_mv') or 0,
        'total_mv': latest.get('total_mv') or 0,
        'amount': latest.get('amount') or 0,
        'short_strength_score': latest.get('short_strength_score'),
        'today_gain': ((latest['close'] - prev['close']) / prev['close'] * 100
                       if prev['close'] > 0 else None),
        'has_recent_trough': False,
        'wave_ok': False,
        'first_wave_gain': None,
        'broke_ma30': None,
        'adjust_ratio': None,
    }

    # 近3个交易日是否出现波谷（在近15日序列内识别 turning_point）
    recent_records = records[-15:]
    for tp in find_turning_points(recent_records):
        if tp['index'] >= len(recent_records) - 3:
            ctx['has_recent_trough'] = True
            break

    # 二浪结构：以近40日最高价切分第一波 / 回调段
    recent_window = records[-WAVE_WINDOW:]
    close_window = [r['close'] for r in recent_window]
    max_index = close_window.index(max(close_window))
    first_wave = recent_window[:max_index + 1]
    adjustment = recent_window[max_index + 1:]
    if len(first_wave) < MIN_FIRST_WAVE_DAYS or len(adjustment) < MIN_ADJUST_DAYS:
        return ctx

    close_prices = [r['close'] for r in records]
    ma30_list = [calculate_ma(close_prices[:i], 30)
                 for i in range(30, len(close_prices) + 1)]
    if len(ma30_list) < len(adjustment):
        return ctx

    # 第一波涨幅（波段末日收盘价相对首日收盘价）
    first_wave_gain = ((first_wave[-1]['close'] - first_wave[0]['close'])
                       / first_wave[0]['close'] * 100)

    # 放量上涨阶段均量：成交额超过整个上涨期均值1.5倍的交易日
    first_wave_amounts = [r['amount'] for r in first_wave]
    avg_all = sum(first_wave_amounts) / len(first_wave_amounts)
    boom_days = [a for a in first_wave_amounts if a > avg_all * 1.5]
    boom_avg = sum(boom_days) / len(boom_days) if boom_days else avg_all

    # 回调段是否有效跌破MA30（允许10%误差）
    adjustment_ma30 = ma30_list[-len(adjustment):]
    broke_ma30 = any(
        adjustment[i]['close'] < adjustment_ma30[i] * MA30_BREAK_TOLERANCE
        for i in range(len(adjustment))
    )

    # 调整期均量 / 放量上涨阶段均量
    adjust_avg = sum(r['amount'] for r in adjustment) / len(adjustment)
    adjust_ratio = adjust_avg / boom_avg if boom_avg > 0 else None

    ctx.update({
        'wave_ok': True,
        'first_wave_gain': first_wave_gain,
        'broke_ma30': broke_ma30,
        'adjust_ratio': adjust_ratio,
    })
    return ctx


def analyze_stocks(data):
    stock_data = {}

    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        stock_data[ts_code].append({
            'trade_date': record['trade_date'],
            'open': float(record['open'] or 0),
            'close': float(record['close'] or 0),
            'high': float(record['high'] or 0),
            'low': float(record['low'] or 0),
            'amount': float(record['amount'] or 0),
            'short_strength_score': (float(record['short_strength_score'])
                                     if record.get('short_strength_score') is not None else None),
            'total_mv': float(record['total_mv'] or 0) if record['total_mv'] else 0,
            'circ_mv': float(record['circ_mv'] or 0) if record['circ_mv'] else 0
        })

    result = []
    count_total = 0          # 有效数据完整、进入过滤的股票数
    count_data_missing = 0   # 有效交易日不足 MIN_BARS
    cnt_filtered = {}        # 各过滤条件淘汰数（按淘汰原因统计，一只股票可同时计入多个条件）

    for ts_code, records in stock_data.items():
        # 过滤无效数据（close<=0，如停牌日脏数据），避免后续计算除零
        records = [r for r in records if r['close'] > 0]
        if len(records) < MIN_BARS:
            count_data_missing += 1
            continue

        records.sort(key=lambda x: x['trade_date'])
        count_total += 1

        # 二浪结构特征 + 注册式过滤条件
        ctx = build_context(records)
        ok, reasons = apply_filters(ctx)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
            continue

        latest = ctx['latest']
        result.append({
            'ts_code': ts_code,
            'close': latest['close'],
            'total_mv': ctx['total_mv'],
            'short_strength_score': ctx['short_strength_score'],
            'first_wave_gain': ctx['first_wave_gain'],
            'today_gain': ctx['today_gain'],
            'amount_ratio': ctx['adjust_ratio'],
            'has_trough': ctx['has_recent_trough']
        })

    result.sort(key=lambda x: x['first_wave_gain'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print("二浪日线选股策略 · 过滤漏斗")
    print("-" * 40)
    print(f"  股票总数量:                 {len(stock_data)}")
    print(f"  - 有效数据不足{MIN_BARS}日 淘汰:     {count_data_missing}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print("-" * 40)
    print(f"  进入过滤股票数:             {count_total}")
    print(f"  最终选出:                   {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    csv_filename = "二浪日线选股策略.csv"
    csv_path = os.path.join(folder_path, csv_filename)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for stock in stocks:
            writer.writerow([stock['ts_code']])
    print(f"✅ CSV文件已生成: {csv_path}")
    return csv_path


def main():
    print("=" * 80)
    print("🌊 二浪日线选股策略")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  0. 基础门槛：流通市值≥50亿、有效交易日≥45日")
    print("  1. 第一波上涨：涨幅≥15%")
    print("  2. 调整阶段：未跌破30日均线（允许10%的误差）")
    print("  3. 量能特征：调整期间成交量较放量上涨阶段萎缩60%以下")
    print("  4. 启动信号：今日涨幅≥5% OR 近3个交易日出现波谷")
    print("  5. 市值门槛：总市值≥2亿")
    print("  6. 流动性：成交额超过5亿")
    print("  7. 强弱门槛：最新交易日短线强弱得分>75分")
    print("=" * 80)

    folder_path = get_folder_path()

    data = read_stock_data(days=120)

    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    selected_stocks = analyze_stocks(data)

    print(f"\n✅ 共选出 {len(selected_stocks)} 只满足条件的股票")

    if selected_stocks:
        csv_path = generate_csv_file(selected_stocks, folder_path)
        print("\n" + "=" * 80)
        print(f"🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n🔥 精选股票：")
        for i, stock in enumerate(selected_stocks[:20], 1):
            trough_mark = " 📉" if stock.get('has_trough', False) else ""
            sss = stock.get('short_strength_score')
            sss_mark = f" 强弱{sss:.1f}" if sss is not None else ""
            print(f"{i}. {stock['ts_code']}{trough_mark} - 第一波涨{stock['first_wave_gain']:.1f}% 今日涨{stock['today_gain']:.1f}%{sss_mark} 市值{stock['total_mv']/10000:.1f}亿")

        # 选股结果入库（便于回测）
        selection_date = max(r['trade_date'] for r in data)
        record_selected_stocks(
            '二浪日线选股策略',
            [{'ts_code': s['ts_code'], 'selected': 1} for s in selected_stocks],
            selection_date)

    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
