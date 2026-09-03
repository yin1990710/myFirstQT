#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
高换手率选股策略 (select_high_exchange.py)

选股条件（对齐 prompt#L302-314）：
1. 读取 stock_daily_t + stock_daily_basic_info_t 最近 15 个交易日，按 ts_code+trade_date 关联
   （实际读 16 个交易日：补前 1 个交易日 close，用于计算 15 天完整的日涨幅）
2. 去除最新一个交易日 total_mv < 100亿 的股票
3. 近 15 个交易日内，至少有 3 个交易日满足：
   当日涨幅 > 5%   AND   当日换手率 turnover_rate_f ≥ 对应市值分档阈值
   分档（基于最新一个交易日的 total_mv，单位万元）：
     - 100亿 ~ 300亿：换手率 > 10%
     - 300亿 ~ 500亿：换手率 >  8%
     -        > 500亿：换手率 >  6%
4. 近 10 个交易日内，**所有「阴线」交易日的换手率都 < 5%**
   （阴线 = close < open；非阴线交易日对此条件无约束；换手率缺失不视为满足 <5%）
5. 均线条件（三者 全部满足）：
   a) 最近一个交易日 收盘价 close > ma5（close 必须站上 5 日线）
   b) 最近 3 个交易日，全部满足 ma5 > ma30（多头排列）
   c) 最近 3 个交易日的 ma30 严格单调递增（ma30_day1 < ma30_day2 < ma30_day3）
6. 根据股票代码去除非A股（仅保留 .SH / .SZ）
7. 输出 CSV "高换手.csv"，格式对齐 select_2wave_up_v2.py（csv.writer + utf-8-sig + 表头 股票代码）
8. 放入「高换手+当日日期后缀」文件夹（已存在则删除重建）
"""

import os
import sys
import csv
import shutil
from datetime import datetime, timedelta

import tushare as ts

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    """
    返回 target_date 前（含）最近 n 个交易日的 (start_date, end_date, list)。
    预留 3 天日历日缓冲保证凑够开市日。
    """
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 10)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return target_date, target_date, [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) > n:
        opens = opens[-n:]
    return opens[0], opens[-1], opens


def create_folder(target_date):
    """新建（已存在的删除重建）高换手加日期后缀的文件夹，返回路径"""
    folder_name = f"高换手{target_date}"
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ 已删除旧文件夹: {folder_name}")
    os.makedirs(folder_path)
    print(f"📁 创建文件夹: {folder_name}")
    return folder_path


# ---------- 市值分档换手率阈值 ----------

def get_turnover_threshold(total_mv_wan):
    """基于最新总市值（万元）返回涨幅>5%同日换手率阈值（%）。<100亿返回 None。"""
    if total_mv_wan < 1000000:                        # <100亿
        return None
    if 1000000 <= total_mv_wan < 3000000:               # 100亿 ~ 300亿
        return 10.0
    if 3000000 <= total_mv_wan < 5000000:               # 300亿 ~ 500亿
        return 8.0
    return 6.0                                           # >500亿


def get_mv_tier_name(total_mv_wan):
    if total_mv_wan < 1000000:
        return "<100亿"
    if total_mv_wan < 3000000:
        return "100-300亿"
    if total_mv_wan < 5000000:
        return "300-500亿"
    return ">500亿"


# ---------- 核心逻辑 ----------

def read_stock_data(start_date, end_date):
    """
    读取 stock_daily_t + stock_daily_basic_info_t 在 [start_date, end_date] 区间，
    JOIN 列: ts_code, trade_date, open, close, ma5, ma30, total_mv, turnover_rate_f
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
            d.ma5,
            d.ma30,
            b.total_mv,
            b.turnover_rate_f
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


def analyze_stocks(data):
    """逐只股票执行完整选股逻辑（近 10 交易日版本）"""

    # ---------- 组装 ----------
    stock_data = {}
    for record in data:
        ts_code = record['ts_code']
        if ts_code not in stock_data:
            stock_data[ts_code] = []
        total_mv = record['total_mv']
        total_mv = float(total_mv) if total_mv is not None else 0.0
        turnover = record['turnover_rate_f']
        turnover = float(turnover) if turnover is not None else None
        stock_data[ts_code].append({
            'trade_date':       record['trade_date'],
            'open':             float(record['open']  or 0),
            'close':            float(record['close'] or 0),
            'ma5':              float(record['ma5']   or 0),
            'ma30':             float(record['ma30']  or 0),
            'total_mv':         total_mv,
            'turnover_rate_f':  turnover,
        })

    result = []
    cnt_valid            = 0     # 非A股 + total_mv>=100亿 + 数据完整
    cnt_mv_under         = 0     # total_mv < 100亿
    cnt_not_a            = 0     # 非A股
    cnt_need_more        = 0     # 交易天数不足 16
    cnt_fail_gain_turn   = 0     # 涨幅>5%+换手达标天数<3
    cnt_fail_yinxian_tr  = 0     # 近10日存在阴线换手率≥5%（条件4失败）
    cnt_fail_close_ma5   = 0     # 最新收盘价 <= ma5  (条件 5a 失败)
    cnt_fail_ma_trend    = 0     # ma5>ma30 或 ma30递增失败 (条件 5b/5c 失败)

    tier_counts = {
        "100-300亿": 0,
        "300-500亿": 0,
        ">500亿":    0,
    }

    for ts_code, records in stock_data.items():

        # ---------- 先非A股过滤 ----------
        if not (ts_code.endswith('.SH') or ts_code.endswith('.SZ')):
            cnt_not_a += 1
            continue

        # 清理停牌等脏数据（close<=0），按日期排序
        records = [r for r in records if r['close'] > 0]
        if not records:
            continue
        records.sort(key=lambda x: x['trade_date'])

        latest = records[-1]

        # ---------- total_mv < 100亿 过滤 ----------
        if latest['total_mv'] < 1000000:
            cnt_mv_under += 1
            continue

        # 保证至少 16 天（补前一日close，算近15天完整涨幅）
        if len(records) < 16:
            cnt_need_more += 1
            continue

        cnt_valid += 1

        # ---------- 窗口切分：近 16 条 = 近 15 个交易日 + 前置 1 日 ----------
        last_16 = records[-16:]             # 16 条 → 15 天完整涨幅可算
        last_10 = records[-10:]             # 近 10 个交易日 = 阴线换手率约束窗口

        threshold = get_turnover_threshold(latest['total_mv'])
        tier_name = get_mv_tier_name(latest['total_mv'])

        # ---------- 条件3：近15日「涨幅>5% 且 换手率>阈值」≥3 天 ----------
        hit_days = 0
        for idx in range(15):           # idx 0..14 → last_16[1..16]
            curr = last_16[idx + 1]
            prev = last_16[idx]
            if prev['close'] <= 0 or curr['close'] <= 0:
                continue
            gain_pct = (curr['close'] - prev['close']) / prev['close'] * 100
            tr = curr['turnover_rate_f']
            if gain_pct > 5 and tr is not None and tr > threshold:
                hit_days += 1

        if hit_days < 3:
            cnt_fail_gain_turn += 1
            continue

        # ---------- 条件4：近 10 日内所有阴线的换手率 < 5% ----------
        yinxian_bad = False
        for rec in last_10:
            if rec['open'] <= 0:             # 开盘价缺失，无法判定阴阳 → 容错跳过
                continue
            if rec['close'] < rec['open']:   # 阴线
                tr = rec['turnover_rate_f']
                if tr is None or tr >= 5:    # 换手率缺失或 ≥5% → 不满足
                    yinxian_bad = True
                    break
        if yinxian_bad:
            cnt_fail_yinxian_tr += 1
            continue

        # ---------- 条件5a：最近一日 close > ma5 ----------
        if not (latest['ma5'] > 0 and latest['close'] > latest['ma5']):
            cnt_fail_close_ma5 += 1
            continue

        # ---------- 条件5b/5c：近 3 日 ma5>ma30 且 ma30 严格单调递增 ----------
        last3 = records[-3:]
        ma5_gt_ma30 = all(
            r['ma5'] > 0 and r['ma30'] > 0 and r['ma5'] > r['ma30']
            for r in last3
        )
        ma30_vals = [r['ma30'] for r in last3]
        ma30_inc = (ma30_vals[0] < ma30_vals[1] < ma30_vals[2])
        if not (ma5_gt_ma30 and ma30_inc):
            cnt_fail_ma_trend += 1
            continue

        if tier_name in tier_counts:
            tier_counts[tier_name] += 1

        result.append({
            'ts_code':      ts_code,
            'close':        latest['close'],
            'ma5':          latest['ma5'],
            'total_mv_yi':  latest['total_mv'] / 10000,
            'mv_tier':      tier_name,
            'threshold':    threshold,
            'hit_days':     hit_days,
            'ma30_last3':   ma30_vals,
        })

    # 按市值从大到小排序
    result.sort(key=lambda x: x['total_mv_yi'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 60)
    print(f"满足条件统计：")
    print(f"  股票总数量:                        {len(stock_data)}")
    print(f"  - 非A股过滤:                       {cnt_not_a}")
    print(f"  - 最新日总市值<100亿过滤:           {cnt_mv_under}")
    print(f"  - 交易天数不足16过滤:               {cnt_need_more}")
    print(f"  市值>=100亿 + A股 + 数据完整:       {cnt_valid}")
    print(f"  涨幅>5%+换手达标天数<3淘汰(15日窗): {cnt_fail_gain_turn}")
    print(f"  阴线换手率≥5% 淘汰(10日窗条件4):   {cnt_fail_yinxian_tr}")
    print(f"  最新日 close ≤ ma5 淘汰:            {cnt_fail_close_ma5}")
    print(f"  ma5>ma30 或 ma30递增不通过淘汰:     {cnt_fail_ma_trend}")
    print("-" * 40)
    print(f"  市值分档入选数（满足全部条件）:")
    tier_thresholds = [
        ("100-300亿", 10.0),
        ("300-500亿",  8.0),
        (">500亿",     6.0),
    ]
    for k, thr in tier_thresholds:
        print(f"    {k:<9} (涨幅>5%+换手>{thr:>4}% ×≥3天): {tier_counts.get(k, 0)}")
    print(f"\n最终选出: {len(result)}")
    print("=" * 60)

    return result


def generate_csv_file(stocks, folder_path):
    """CSV 输出对齐 select_2wave_up_v2.py：csv.writer + utf-8-sig + 表头 股票代码"""
    csv_filename = "高换手.csv"
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
    print("📊 高换手率选股策略 (近15日涨幅窗口+近10日阴线缩量+均线多头版)")
    print("=" * 80)
    print("\n📊 选股逻辑：")
    print("  1. 基础过滤：A股上市，最新日总市值≥100亿")
    print("  2. 近15个交易日内至少有3天满足「当日涨幅>5% 且 换手率达标」:")
    print("     - 100-300亿 → 换手>10% 且涨幅>5%")
    print("     - 300-500亿 → 换手> 8% 且涨幅>5%")
    print("     -   >500亿 → 换手> 6% 且涨幅>5%")
    print("  3. 近10个交易日内，所有「阴线」(close<open)换手率都 < 5% (上涨放量、回调缩量)")
    print("  4. 均线多头三重奏：")
    print("     a) 最新日 close > ma5 (站上5日线)")
    print("     b) 近3日 ma5 > ma30 全部满足")
    print("     c) 近3日 ma30 严格单调递增")
    print("=" * 80)

    # ---------- 步骤A：获取最近 16 个交易日（补 1 日前置算 15 天涨幅） ----------
    print(f"\n📅 目标日期: {target_date}")
    s16, e16, t16 = get_last_n_trade_dates(target_date, 16)
    # 近 15 个交易日（涨幅校验窗口）= t16 末 15 个
    t15 = t16[-15:] if len(t16) >= 15 else t16
    # 近 10 个交易日（阴线换手率窗口）
    t10 = t16[-10:] if len(t16) >= 10 else t16
    print(f"   涨幅校验 15 日窗口: {t15[0]} ~ {t15[-1]}（共{len(t15)}天）")
    print(f"   阴线约束 10 日窗口: {t10[0]} ~ {t10[-1]}（共{len(t10)}天）")
    print(f"   实际查询区间(含1日前置close): {s16} ~ {e16}（共{len(t16)}天）")

    # ---------- 步骤B：文件夹 ----------
    folder_path = create_folder(target_date)

    # ---------- 步骤C：读取 ----------
    data = read_stock_data(s16, e16)
    if not data:
        print("❌ 没有获取到数据，退出程序")
        return

    # ---------- 步骤D：选股 ----------
    selected = analyze_stocks(data)
    print(f"\n✅ 共选出 {len(selected)} 只满足条件的股票")

    if selected:
        csv_path = generate_csv_file(selected, folder_path)
        print("\n" + "=" * 80)
        print("🎉 选股完成！")
        print(f"📁 文件夹路径: {folder_path}")
        print(f"📄 CSV路径: {csv_path}")
        print("=" * 80)

        print("\n🔥 精选股票（按市值降序，前20只）：")
        for i, s in enumerate(selected[:20], 1):
            ma = s['ma30_last3']
            print(
                f"{i:>2}. {s['ts_code']:<11} "
                f"收{s['close']:>8.2f} MA5{s['ma5']:>8.2f} | "
                f"市值{s['total_mv_yi']:>8.1f}亿({s['mv_tier']:<8}) | "
                f"达标{s['hit_days']:>2}天(换手>{s['threshold']:>4}%+涨幅>5%) | "
                f"MA30:[{ma[0]:.2f}<{ma[1]:.2f}<{ma[2]:.2f}]"
            )
    else:
        print("\n" + "=" * 80)
        print("⚠️ 没有满足条件的股票")
        print("=" * 80)


if __name__ == "__main__":
    main()
