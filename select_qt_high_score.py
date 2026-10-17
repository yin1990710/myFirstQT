#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
选股策略: QT高分选股策略（智能分析评分选股）

扫描 stock_daily_basic_info_t 最新交易日总市值 > 50亿 的全部股票，
先按 stock_daily_t 最近一个交易日成交额 >= 5亿、短线强弱得分 >= 60 前置过滤
（减少无效分析），再逐只调用 stock_analyzer_v2.analyzer.analyze() 做完整
走势分析，选出综合评分 verdict.score >= 1（中性偏多/偏强）的股票。

选股结果写入 strategy_selected_stock_daily_t 表（便于回测）。

用法：
    python select_qt_high_score.py                 # 全量扫描（约1小时）
    python select_qt_high_score.py --limit 20      # 只扫描前20只（调试）
    python select_qt_high_score.py --code 600577   # 单只调试
"""

import argparse
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from module_mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks
from stock_analyzer_v2.analyzer import analyze


# ---------- 策略参数 ----------
MIN_TOTAL_MV_WAN = 500_000   # 最新交易日总市值下限（万元）= 50亿
MIN_AMOUNT_QIAN = 500_000    # 最新交易日成交额下限（千元）= 5亿（前置过滤）
MIN_SHORT_STRENGTH = 60.0    # 最新交易日短线强弱得分下限（无得分不通过，前置过滤）
MIN_SCORE = 1.0              # 智能分析综合评分下限（>=1 为中性偏多/偏强）
PROGRESS_EVERY = 50          # 每分析多少只打印一次进度


# ---------- 灵活过滤条件（STOCK_FILTERS 注册表，参考 select_limitup_1d.py） ----------
# 过滤函数入参为 build_context(...) 生成的单只股票特征上下文，返回 True=通过。

def build_context(ts_code, stock_name):
    """调用 stock_analyzer_v2 完整分析单只股票，返回评分上下文。"""
    try:
        res = analyze(ts_code)
        return {
            'ts_code': ts_code,
            'stock_name': stock_name,
            'res': res,
            'score': res.verdict['score'],
            'label': res.verdict['label'],
            'tone': res.verdict['tone'],
            'as_of': res.as_of.replace('-', ''),
            'error': None,
        }
    except Exception as e:
        return {
            'ts_code': ts_code,
            'stock_name': stock_name,
            'res': None,
            'score': None,
            'label': None,
            'tone': None,
            'as_of': None,
            'error': str(e),
        }


def _filter_analyze_ok(ctx):
    """智能分析成功（行情/K线等数据完整）。"""
    return ctx['res'] is not None


def _filter_high_score(ctx):
    """综合评分 >= MIN_SCORE（1.0）。"""
    return ctx['score'] is not None and ctx['score'] >= MIN_SCORE


# 过滤条件注册表：每个条件为 (淘汰原因名称, 函数)
STOCK_FILTERS = [
    ('智能分析失败或数据缺失', _filter_analyze_ok),
    (f'综合评分<{MIN_SCORE:g}', _filter_high_score),
]


def apply_filters(ctx):
    """依次执行 STOCK_FILTERS 全部条件，返回 (是否通过, 未通过条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(ctx)]
    return (len(reasons) == 0), reasons


# ---------- 核心逻辑 ----------

def read_candidates():
    """读取最新交易日 total_mv>50亿、成交额>=5亿、短线强弱得分>=60 的股票清单（前置过滤）。"""
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return None, []

    query_sql = """
        SELECT b.ts_code, b.total_mv, d.amount, d.short_strength_score, i.stock_name
        FROM stock_daily_basic_info_t b
        JOIN stock_daily_t d
               ON b.ts_code = d.ts_code AND b.trade_date = d.trade_date
        LEFT JOIN stock_info_t i
               ON b.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
        WHERE b.trade_date = (SELECT MAX(trade_date) FROM stock_daily_basic_info_t)
          AND b.total_mv > %s
          AND d.amount >= %s
          AND d.short_strength_score >= %s
        ORDER BY b.total_mv DESC
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT MAX(trade_date) AS d FROM stock_daily_basic_info_t")
            target_date = cursor.fetchone()['d']
            cursor.execute(query_sql, (MIN_TOTAL_MV_WAN, MIN_AMOUNT_QIAN, MIN_SHORT_STRENGTH))
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM stock_daily_basic_info_t "
                "WHERE trade_date = %s AND total_mv > %s",
                (target_date, MIN_TOTAL_MV_WAN))
            total_all = cursor.fetchone()['n']
        print(f"✅ 最新交易日 {target_date}，总市值>{MIN_TOTAL_MV_WAN/10000:.0f}亿 "
              f"共 {total_all} 只；前置过滤（成交额>={MIN_AMOUNT_QIAN/100000:.0f}亿 + "
              f"短线强弱得分>={MIN_SHORT_STRENGTH:g}）后候选 {len(rows)} 只")
        return target_date, rows
    except Exception as e:
        print(f"❌ 查询候选股票失败: {e}")
        return None, []
    finally:
        close_connection(conn)


def analyze_stocks(candidates):
    """逐只调用智能分析并按过滤注册表筛选。"""
    result = []
    cnt_filtered = {}  # 各过滤条件淘汰数（一只股票可同时计入多个条件）
    total = len(candidates)
    t0 = time.time()

    for idx, row in enumerate(candidates, 1):
        ts_code = row['ts_code']
        ctx = build_context(ts_code, row.get('stock_name') or '')
        ok, reasons = apply_filters(ctx)
        if not ok:
            for name in reasons:
                cnt_filtered[name] = cnt_filtered.get(name, 0) + 1
        else:
            result.append(ctx)

        if idx % PROGRESS_EVERY == 0 or idx == total:
            elapsed = time.time() - t0
            print(f"  进度 {idx}/{total}（{idx/total*100:.1f}%）"
                  f"选出 {len(result)} | 失败 {cnt_filtered.get('智能分析失败或数据缺失', 0)}"
                  f" | 已耗时 {elapsed/60:.1f} 分钟")

    # 按综合评分从大到小排序
    result.sort(key=lambda x: x['score'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print("QT高分选股策略 · 过滤漏斗")
    print("-" * 40)
    print(f"  股票总数量:                      {total}")
    for name, cnt in cnt_filtered.items():
        print(f"  - 过滤[{name}] 淘汰: {cnt}")
    print("-" * 40)
    print(f"  最终选出: {len(result)}")
    print("=" * 60)

    return result


# ---------- 主入口 ----------

def main():
    parser = argparse.ArgumentParser(description='QT高分选股策略（智能分析评分>=1）')
    parser.add_argument('--limit', type=int, default=0,
                        help='只扫描总市值最大的前N只（调试用，0=全量）')
    parser.add_argument('--code', help='只分析指定股票代码（调试用）')
    args = parser.parse_args()

    print("=" * 80)
    print(f"📊 QT高分选股策略（总市值>{MIN_TOTAL_MV_WAN/10000:.0f}亿 + 最新日成交额"
          f">={MIN_AMOUNT_QIAN/100000:.0f}亿 + 短线强弱得分>={MIN_SHORT_STRENGTH:g}"
          f" + stock_analyzer_v2 综合评分>={MIN_SCORE:g}）")
    print("=" * 80)

    target_date, candidates = read_candidates()
    if target_date is None or not candidates:
        print("❌ 没有获取到候选股票，退出程序")
        return

    if args.code:
        candidates = [c for c in candidates if c['ts_code'].startswith(args.code)]
        print(f"🎯 指定单只调试模式，匹配到 {len(candidates)} 只")
    if args.limit > 0:
        candidates = candidates[:args.limit]
        print(f"🎯 调试模式：只扫描前 {len(candidates)} 只")

    selected = analyze_stocks(candidates)
    print(f"\n✅ 共选出 {len(selected)} 只满足条件的股票")

    if not selected:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)
        return

    print("\n" + "=" * 80)
    print("🎉 选股完成！")
    print("=" * 80)

    print("\n🔥 精选股票（按综合评分降序，前20只）：")
    for i, s in enumerate(selected[:20], 1):
        print(
            f"{i:>2}. {s['ts_code']:<11} {s['stock_name']:<8} "
            f"评分{s['score']:>5.1f} | {s['label']} | {s['tone']}"
        )

    # 选股结果入库（便于回测）
    record_selected_stocks(
        'QT高分选股策略',
        [{'ts_code': s['ts_code'], 'stock_name': s['stock_name'], 'selected': 1}
         for s in selected],
        target_date)


if __name__ == "__main__":
    main()
