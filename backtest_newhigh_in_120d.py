#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import random
from datetime import datetime
from typing import Dict, List, Tuple
from mysql_connection import get_mysql_connection, close_connection

# 固定随机种子，保证每次抽样结果可复现
random.seed(123456)
np.random.seed(123456)


def get_stock_basic_info(conn) -> pd.DataFrame:
    """
    筛选条件：
    1. 市值 > 50亿
    2. 股票名称 不含 ST、*ST
    """
    try:
        cursor = conn.cursor()
        sql = """
            SELECT ts_code, stock_name, total_mv
            FROM stock_info_t
            WHERE total_mv > 5000000000
              AND stock_name NOT LIKE '%ST%'
              AND stock_name NOT LIKE '%*ST%'
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"❌ 获取股票基础信息失败: {e}")
        return pd.DataFrame()


def get_historical_stock_data(conn, start_date='20250101') -> pd.DataFrame:
    """获取20250101以来日K数据"""
    try:
        cursor = conn.cursor()
        sql = """
            SELECT
                d.ts_code,
                d.trade_date,
                d.close,
                d.pre_close,
                d.amount
            FROM stock_daily_t d
            WHERE d.trade_date >= %s
            ORDER BY d.ts_code, d.trade_date
        """
        cursor.execute(sql, (start_date,))
        rows = cursor.fetchall()
        df = pd.DataFrame(rows)
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['pre_close'] = pd.to_numeric(df['pre_close'], errors='coerce')
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        return df
    except Exception as e:
        print(f"❌ 获取历史数据失败: {e}")
        return pd.DataFrame()


def check_newhigh_strategy(group: pd.DataFrame) -> Tuple[bool, str]:
    """
    选股策略：
    1. 收盘价创120日新高
    2. 当日涨幅 > 8%
    3. 成交额 > 5亿（500000千元）
    4. 120日波幅 < 25%（最低/最高 > 75%）
    返回：(是否满足, 第一个满足交易日)
    """
    group = group.reset_index(drop=True)
    for i in range(120, len(group)):
        current = group.iloc[i]
        past_120 = group.iloc[i-120:i]

        if current['close'] <= past_120['close'].max():
            continue

        if pd.isna(current['pre_close']) or current['pre_close'] <= 0:
            continue
        gain = (current['close'] - current['pre_close']) / current['pre_close'] * 100
        if gain <= 8:
            continue

        if pd.isna(current['amount']) or current['amount'] < 500000:
            continue

        min_close_120 = past_120['close'].min()
        max_close_120 = past_120['close'].max()
        if min_close_120 / max_close_120 <= 0.75:
            continue

        return True, current['trade_date']
    return False, ""


def calculate_10_metrics(group: pd.DataFrame, trigger_date: str) -> Dict[str, float]:
    """
    计算 10 个指标：每个区间 最高涨幅 + 最低跌幅
    1.  2-10日 最高涨幅
    2.  2-10日 最低跌幅
    3. 11-20日 最高涨幅
    4. 11-20日 最低跌幅
    5. 21-40日 最高涨幅
    6. 21-40日 最低跌幅
    7. 41-60日 最高涨幅
    8. 41-60日 最低跌幅
    9. 至今 最高涨幅
    10.至今 最低跌幅
    负数 100% 保留
    """
    trigger_idx = group[group['trade_date'] == trigger_date].index
    if len(trigger_idx) == 0:
        return None
    trigger_idx = trigger_idx[0]
    first_close = group.iloc[trigger_idx]['close']

    if first_close <= 0:
        return None

    res = {}

    # ========== 2～10日 ==========
    s2 = trigger_idx + 2
    e10 = min(trigger_idx + 10, len(group)-1)
    if s2 > e10:
        h1, l1 = np.nan, np.nan
    else:
        h1 = group.loc[s2:e10, 'close'].max()
        l1 = group.loc[s2:e10, 'close'].min()
    res['2-10日最高涨幅(%)'] = round((h1 - first_close)/first_close*100, 2) if not np.isnan(h1) else np.nan
    res['2-10日最低跌幅(%)'] = round((l1 - first_close)/first_close*100, 2) if not np.isnan(l1) else np.nan

    # ========== 11～20日 ==========
    s11 = trigger_idx + 11
    e20 = min(trigger_idx + 20, len(group)-1)
    if s11 > e20:
        h2, l2 = np.nan, np.nan
    else:
        h2 = group.loc[s11:e20, 'close'].max()
        l2 = group.loc[s11:e20, 'close'].min()
    res['11-20日最高涨幅(%)'] = round((h2 - first_close)/first_close*100, 2) if not np.isnan(h2) else np.nan
    res['11-20日最低跌幅(%)'] = round((l2 - first_close)/first_close*100, 2) if not np.isnan(l2) else np.nan

    # ========== 21～40日 ==========
    s21 = trigger_idx + 21
    e40 = min(trigger_idx + 40, len(group)-1)
    if s21 > e40:
        h3, l3 = np.nan, np.nan
    else:
        h3 = group.loc[s21:e40, 'close'].max()
        l3 = group.loc[s21:e40, 'close'].min()
    res['21-40日最高涨幅(%)'] = round((h3 - first_close)/first_close*100, 2) if not np.isnan(h3) else np.nan
    res['21-40日最低跌幅(%)'] = round((l3 - first_close)/first_close*100, 2) if not np.isnan(l3) else np.nan

    # ========== 41～60日 ==========
    s41 = trigger_idx + 41
    e60 = min(trigger_idx + 60, len(group)-1)
    if s41 > e60:
        h4, l4 = np.nan, np.nan
    else:
        h4 = group.loc[s41:e60, 'close'].max()
        l4 = group.loc[s41:e60, 'close'].min()
    res['41-60日最高涨幅(%)'] = round((h4 - first_close)/first_close*100, 2) if not np.isnan(h4) else np.nan
    res['41-60日最低跌幅(%)'] = round((l4 - first_close)/first_close*100, 2) if not np.isnan(l4) else np.nan

    # ========== 至今 ==========
    h5 = group.loc[trigger_idx:, 'close'].max()
    l5 = group.loc[trigger_idx:, 'close'].min()
    res['至今最高涨幅(%)'] = round((h5 - first_close)/first_close*100, 2)
    res['至今最低跌幅(%)'] = round((l5 - first_close)/first_close*100, 2)

    return res


def main():
    print("=" * 80)
    print("📊 120日新高策略 - 10指标完整版（随机抽300只）")
    print("=" * 80)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return

    # 1. 筛选股票池
    print("\n🔍 筛选股票：市值>50亿 + 非ST")
    basic_df = get_stock_basic_info(conn)
    if basic_df.empty:
        print("❌ 无符合条件股票")
        close_connection(conn)
        return

    # 2. 读取K线
    print("\n📅 读取20250101以来K线...")
    k_df = get_historical_stock_data(conn, '20250101')
    if k_df.empty:
        print("❌ 无K线数据")
        close_connection(conn)
        return

    # 3. 只保留符合条件股票
    valid_codes = set(basic_df['ts_code'])
    k_df = k_df[k_df['ts_code'].isin(valid_codes)]

    # 4. 股票名称映射
    name_map = dict(zip(basic_df['ts_code'], basic_df['stock_name']))

    # 5. 执行策略
    print("\n🚀 开始执行策略...")
    result = []

    for code, group in k_df.groupby('ts_code'):
        group = group.sort_values('trade_date').reset_index(drop=True)
        ok, date = check_newhigh_strategy(group)
        if not ok:
            continue

        metrics = calculate_10_metrics(group, date)
        if not metrics:
            continue

        result.append({
            '股票代码': code,
            '股票名称': name_map.get(code, ''),
            '触发交易日': date,
            '2-10日最高涨幅(%)': metrics['2-10日最高涨幅(%)'],
            '2-10日最低跌幅(%)': metrics['2-10日最低跌幅(%)'],
            '11-20日最高涨幅(%)': metrics['11-20日最高涨幅(%)'],
            '11-20日最低跌幅(%)': metrics['11-20日最低跌幅(%)'],
            '21-40日最高涨幅(%)': metrics['21-40日最高涨幅(%)'],
            '21-40日最低跌幅(%)': metrics['21-40日最低跌幅(%)'],
            '41-60日最高涨幅(%)': metrics['41-60日最高涨幅(%)'],
            '41-60日最低跌幅(%)': metrics['41-60日最低跌幅(%)'],
            '至今最高涨幅(%)': metrics['至今最高涨幅(%)'],
            '至今最低跌幅(%)': metrics['至今最低跌幅(%)']
        })

    # 6. 全量输出
    if len(result) == 0:
        print("\n❌ 无股票满足策略")
        close_connection(conn)
        return

    print(f"\n🎲 总满足条件：{len(result)} 只")

    # 7. 输出CSV
    out = pd.DataFrame(result)
    fn = f"120日新高_4条件_{datetime.now().strftime('%Y%m%d')}.csv"
    out.to_csv(fn, index=False, encoding='utf-8-sig')
    print(f"\n💾 结果已保存：{fn}")

    close_connection(conn)
    print("\n🎉 全部完成！")


if __name__ == "__main__":
    main()
