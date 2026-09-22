#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: 120日区间新高选股策略

从 stock_daily_t 表查出最近 120 天股票交易数据，从 stock_info_t 表查询股票基本信息，
通过 ts_code 关联，选出符合以下条件的股票：

1. 最近1个交易日收盘价（close），超过最近120天（不含最近一天）的最高收盘价（close）。
2. 最近1个交易日的涨幅（(close-前一日收盘价)/前一日收盘价）超过5%。
3. 前119日收盘价不含0值（脏数据保护）。
4. 前119日区间振幅（最高收盘-最低收盘）/最低收盘 ≤ 30%。
5. 最近1个交易日成交额 amount × 1000 > 5亿。

过滤条件采用 STOCK_FILTERS 注册表方式（参考 find_similar_ma5.py），
新增条件只需追加一个 _filter_xxx 函数与一行注册；apply_filters 统一执行并返回未通过原因。

最后，将符合以上条件的股票的ts_code保存在生成一个名称为区间新高加当天日期（如果当前时间在0-15时之间，则取前一天日期）的csv文件，ts_code之间用英文逗号分隔，新建一个名称为区间新高加当天日期（如果当前时间在0-15时之间，则取前一天日期））的文件夹，将csv文件放在该文件夹下，如果文件夹已存在则先删除再新建。
"""

import os
import shutil
import pandas as pd
from datetime import datetime, timedelta

from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks


def get_date_170_days_ago() -> str:
    """获取200个自然日之前的日期，确保能取出120个交易日"""
    date_200_days_ago = datetime.now() - timedelta(days=200)
    return date_200_days_ago.strftime('%Y%m%d')


def get_trade_date() -> str:
    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 15:
        target_date = now - timedelta(days=1)
        return target_date.strftime('%Y%m%d')
    else:
        return now.strftime('%Y%m%d')


def get_stock_data(conn) -> pd.DataFrame:
    date_170_days_ago = get_date_170_days_ago()
    try:
        cursor = conn.cursor()
        sql = """
            SELECT
                d.ts_code,
                d.trade_date,
                d.open,
                d.high,
                d.low,
                d.close,
                d.vol,
                d.amount,
                d.short_strength_score,
                i.stock_name
            FROM stock_daily_t d
            LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
            WHERE d.trade_date >= %s
            ORDER BY d.ts_code, d.trade_date
        """
        cursor.execute(sql, (date_170_days_ago,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        if rows and isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
        else:
            df = pd.DataFrame(rows, columns=columns)

        return df
    except Exception as e:
        print(f"❌ 获取股票数据失败: {e}")
        return pd.DataFrame()


# ---------- 灵活过滤条件（新增条件只需在 STOCK_FILTERS 中追加一行） ----------
NEWHIGH_WINDOW = 120          # 区间窗口（最近120个交易日）
MAX_AMPLITUDE = 30.0          # 前119日收盘区间振幅上限（%）
MIN_GAIN = 5.0                # 最新日涨幅下限（%）
MIN_AMOUNT_YI = 5.0           # 最新日成交额下限（亿元）；amount单位千元，amount×1000为元
MIN_SHORT_STRENGTH = 70.0      # 最新日短线强弱得分>70


def build_context(group):
    """计算单只股票的区间新高特征上下文（group 为按日期升序、已取近120日的 DataFrame）。

    数值口径与原实现一致：max/min 由 pandas 计算（自动跳过 NaN），
    数据缺失产生的 NaN 在比较时一律不拦截，由各过滤函数按原语义判定。
    """
    latest = group.iloc[-1]
    previous = group.iloc[:-1]

    max_close_119 = previous['close'].max()
    min_close_119 = previous['close'].min()
    prev_close = group.iloc[-2]['close']
    gain = ((latest['close'] - prev_close) / prev_close * 100
            if prev_close and prev_close != 0 else float('nan'))
    amplitude = ((max_close_119 - min_close_119) / min_close_119 * 100
                 if min_close_119 and min_close_119 != 0 else float('nan'))

    return {
        'bars': len(group),
        'latest_close': latest['close'],
        'latest_amount': latest['amount'],
        'max_close_119': max_close_119,
        'min_close_119': min_close_119,
        'amplitude': amplitude,
        'gain': gain,
        'stock_name': group.iloc[0].get('stock_name', ''),
        'short_strength_score': (float(latest['short_strength_score'])
                                 if latest.get('short_strength_score') is not None else None),
    }


def _filter_enough_bars(ctx):
    """有效交易日不少于120日（不足120日无法定义120日新高）。"""
    return ctx['bars'] >= NEWHIGH_WINDOW


def _filter_close_nonzero(ctx):
    """前119日收盘价不含0值（最低收盘>0即保证全部为正）。"""
    return ctx['min_close_119'] is not None and ctx['min_close_119'] != 0 \
        and ctx['max_close_119'] is not None and ctx['max_close_119'] != 0


def _filter_amplitude(ctx):
    """前119日收盘区间振幅 ≤ 35%。"""
    return ctx['amplitude'] <= MAX_AMPLITUDE


def _filter_new_high(ctx):
    """最新收盘价严格突破前119日最高收盘价（创120日收盘新高）。"""
    return ctx['latest_close'] > ctx['max_close_119']


def _filter_gain(ctx):
    """最新日涨幅 > 5%。"""
    return ctx['gain'] > MIN_GAIN


def _filter_amount(ctx):
    """最新日成交额 > 5亿。"""
    return ctx['latest_amount'] * 1000 > MIN_AMOUNT_YI * 1e8


def _filter_short_strength(ctx):
    """最新一个交易日短线强弱得分 > MIN_SHORT_STRENGTH（无得分视为不通过）。"""
    s = ctx.get('short_strength_score')
    return s is not None and s > MIN_SHORT_STRENGTH


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)；函数入参为单只股票的特征上下文 ctx，返回 True=通过
STOCK_FILTERS = [
    (f'有效交易日不足{NEWHIGH_WINDOW}日', _filter_enough_bars),
    ('前119日收盘价存在0值', _filter_close_nonzero),
    (f'区间振幅>{MAX_AMPLITUDE:g}%', _filter_amplitude),
    ('最新收盘未创120日新高', _filter_new_high),
    (f'最新日涨幅<={MIN_GAIN:g}%', _filter_gain),
    (f'最新日成交额<={MIN_AMOUNT_YI:g}亿', _filter_amount),
    (f'短线强弱得分<={MIN_SHORT_STRENGTH:g}', _filter_short_strength),
]


def apply_filters(ctx):
    """依次执行 STOCK_FILTERS 中的全部过滤条件，返回 (是否通过, 未通过的条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(ctx)]
    return (len(reasons) == 0), reasons


def analyze_newhigh_stocks(df: pd.DataFrame) -> list:
    qualified_stocks = []
    cnt_filtered = {}    # 各过滤条件淘汰数（一只股票可同时计入多个条件）

    grouped = df.groupby('ts_code')

    for ts_code, group in grouped:
        group = group.sort_values('trade_date').reset_index(drop=True)

        # 取最近120个交易日
        if len(group) > NEWHIGH_WINDOW:
            group = group.tail(NEWHIGH_WINDOW).reset_index(drop=True)

        for col in ('close', 'open', 'high', 'vol', 'amount'):
            group[col] = pd.to_numeric(group[col], errors='coerce')

        # 特征上下文 + 注册式过滤
        ctx = build_context(group)
        ok, reasons = apply_filters(ctx)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
            continue

        break_ratio = (ctx['latest_close'] - ctx['max_close_119']) \
            / ctx['max_close_119'] * 100

        qualified_stocks.append({
            'ts_code': ts_code,
            'stock_name': ctx['stock_name'],
            'latest_close': ctx['latest_close'],
            'max_close_119': ctx['max_close_119'],
            'break_ratio': break_ratio,
            'gain': ctx['gain']
        })

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print("120日区间新高选股策略 · 过滤漏斗")
    print("-" * 40)
    print(f"  股票总数量:                 {grouped.ngroups}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print("-" * 40)
    print(f"  最终选出:                   {len(qualified_stocks)}")
    print("=" * 60)

    return qualified_stocks


def save_results_to_csv(results: list, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)

    csv_path = os.path.join(output_dir, f"120日区间新高{get_trade_date()}.csv")
    with open(csv_path, 'w') as f:
        ts_codes = [r['ts_code'] for r in results]
        f.write(','.join(ts_codes))

    detail_csv_path = os.path.join(output_dir, f"区间新高详情{get_trade_date()}.csv")
    df = pd.DataFrame(results)
    if not df.empty:
        df.to_csv(detail_csv_path, index=False, encoding='utf-8-sig')


def main():
    print("=" * 60)
    print("📊 区间新高选股模型")
    print("=" * 60)

    trade_date = get_trade_date()
    print(f"\n⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 输出日期: {trade_date} (前一日)")

    print("\n🔌 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    print("🔌 正在连接数据库: root@localhost:3306/stock_daily_db")
    print("✅ 数据库连接成功！")

    print("\n📋 步骤2: 获取股票数据...")
    df = get_stock_data(conn)
    if df.empty:
        print("❌ 没有获取到数据")
        close_connection(conn)
        return

    print(f"   ✅ 获取到 {len(df)} 条记录")

    print("\n📈 步骤3: 分析区间新高股票...")
    results = analyze_newhigh_stocks(df)
    print(f"   ✅ 找到 {len(results)} 只符合条件的股票")

    if results:
        print("\n📊 符合条件的股票列表:")
        print("-" * 80)
        print(f"{'股票代码':<12} {'股票名称':<15} {'最新收盘':<10} {'120日收盘最高':<12} {'突破幅度':<10} {'涨幅(%)':<10}")
        print("-" * 80)

        for r in results[:20]:
            print(f"{r['ts_code']:<12} {r['stock_name']:<15} "
                  f"{r['latest_close']:>8.2f}   "
                  f"{r['max_close_119']:>10.2f}   "
                  f"{r['break_ratio']:>7.2f}%   "
                  f"{r['gain']:>7.2f}%")

    print("\n💾 步骤4: 保存结果到CSV文件...")
    folder_name = f"120日区间新高{trade_date}"
    output_dir = os.path.join(os.getcwd(), folder_name)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)

    save_results_to_csv(results, output_dir)
    print(f"   ✅ 已保存到: {output_dir}")

    # 步骤5: 选股结果入库（便于回测）
    if results:
        record_selected_stocks(
            '120日区间新高选股策略',
            [{'ts_code': r['ts_code'], 'selected': 1} for r in results],
            str(df['trade_date'].max()))

    print(f"\n🎉 选股完成！共选出 {len(results)} 只股票")
    close_connection(conn)


if __name__ == "__main__":
    main()