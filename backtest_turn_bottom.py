#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
底部放量反转策略回测

功能：
1. 使用select_turn_bottom.py的4个选股条件
2. 从20250101以来找出所有满足策略的股票
3. 计算8个区间的涨跌幅
4. 随机选取200只保存CSV
"""

import random
import pandas as pd
from datetime import datetime
from typing import Tuple, List
from mysql_connection import get_mysql_connection, close_connection


def get_stock_data(conn, start_date='20250101') -> pd.DataFrame:
    """获取股票数据"""
    try:
        cursor = conn.cursor()
        sql = """
            SELECT 
                d.ts_code,
                d.trade_date,
                d.open,
                d.close,
                d.amount,
                i.stock_name,
                i.total_mv
            FROM stock_daily_t d
            LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
            WHERE d.trade_date >= %s
            ORDER BY d.ts_code, d.trade_date
        """
        cursor.execute(sql, (start_date,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        df = pd.DataFrame(rows, columns=columns)
        
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['open'] = pd.to_numeric(df['open'], errors='coerce')
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce')
        df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce')
        
        return df
    except Exception as e:
        print(f"❌ 获取股票数据失败: {e}")
        return pd.DataFrame()


def check_strategy(group: pd.DataFrame) -> Tuple[bool, str, int]:
    """
    检查股票是否满足选股策略
    选股条件：
    1. 最近200天最低收盘价/最高收盘价 < 50%
    2. 最低价出现在最近15个交易日内
    3. 市值 > 50亿
    4. 近10天至少4个阳线，阳线成交额均>5亿，阳线成交额是阴线1.5倍以上
    
    返回：(是否满足, 首个满足日期, 首个满足日索引)
    """
    group = group.reset_index(drop=True)
    
    for start_idx in range(200, len(group)):
        current_idx = start_idx
        current = group.iloc[current_idx]
        
        # 往前取200天数据（不含当天）
        past_200 = group.iloc[current_idx-200:current_idx]
        if len(past_200) < 200:
            continue
        
        # 条件3：市值>50亿
        total_mv = current.get('total_mv', 0)
        if total_mv <= 5000000000:
            continue
        
        # 条件1：最低收盘价/最高收盘价 < 50%
        min_close_200 = past_200['close'].min()
        max_close_200 = past_200['close'].max()
        if max_close_200 == 0:
            continue
        ratio = min_close_200 / max_close_200
        if ratio >= 0.5:
            continue
        
        # 条件2：最低价出现在最近15个交易日内
        past_200_reset = past_200.reset_index(drop=True)
        min_index = past_200_reset['close'].idxmin()
        days_since_min = len(past_200_reset) - 1 - min_index
        if days_since_min > 15:
            continue
        
        # 条件4：最近10个交易日分析（从当前，往前10天）
        recent_10 = group.iloc[current_idx-9:current_idx+1]
        if len(recent_10) < 10:
            continue
        
        # 阳线数量
        up_days = recent_10[recent_10['close'] > recent_10['open']]
        if len(up_days) < 4:
            continue
        
        # 阳线成交额均>5亿
        up_amounts = up_days['amount']
        if any(up_amounts < 500000):
            continue
        
        # 阳线平均成交额 >= 阴线平均成交额 × 1.5
        down_days = recent_10[recent_10['close'] < recent_10['open']]
        if len(down_days) == 0:
            continue
        avg_up_amount = up_amounts.mean()
        avg_down_amount = down_days['amount'].mean()
        if avg_down_amount == 0 or avg_up_amount < avg_down_amount * 1.5:
            continue
        
        return True, current['trade_date'], current_idx
    
    return False, "", -1


def calculate_returns(group: pd.DataFrame, first_idx: int) -> dict:
    """计算8个区间的涨跌幅"""
    group = group.reset_index(drop=True)
    first_close = group.iloc[first_idx]['close']
    
    result = {
        'first_date': group.iloc[first_idx]['trade_date'],
        'first_close': first_close,
        '2_10_max_gain': 0.0,
        '2_10_max_loss': 0.0,
        '11_20_max_gain': 0.0,
        '11_20_max_loss': 0.0,
        '21_40_max_gain': 0.0,
        '21_40_max_loss': 0.0,
        '41_60_max_gain': 0.0,
        '41_60_max_loss': 0.0
    }
    
    if first_close == 0:
        return result
    
    # 2-10日区间
    if first_idx + 10 < len(group):
        period_data = group.iloc[first_idx+1:first_idx+10]
        max_close = period_data['close'].max()
        min_close = period_data['close'].min()
        result['2_10_max_gain'] = (max_close - first_close) / first_close * 100
        result['2_10_max_loss'] = (min_close - first_close) / first_close * 100
    
    # 11-20日区间
    if first_idx + 20 < len(group):
        period_data = group.iloc[first_idx+11:first_idx+20]
        max_close = period_data['close'].max()
        min_close = period_data['close'].min()
        result['11_20_max_gain'] = (max_close - first_close) / first_close * 100
        result['11_20_max_loss'] = (min_close - first_close) / first_close * 100
    
    # 21-40日区间
    if first_idx + 40 < len(group):
        period_data = group.iloc[first_idx+21:first_idx+40]
        max_close = period_data['close'].max()
        min_close = period_data['close'].min()
        result['21_40_max_gain'] = (max_close - first_close) / first_close * 100
        result['21_40_max_loss'] = (min_close - first_close) / first_close * 100
    
    # 41-60日区间
    if first_idx + 60 < len(group):
        period_data = group.iloc[first_idx+41:first_idx+60]
        max_close = period_data['close'].max()
        min_close = period_data['close'].min()
        result['41_60_max_gain'] = (max_close - first_close) / first_close * 100
        result['41_60_max_loss'] = (min_close - first_close) / first_close * 100
    
    return result


def main():
    print("=" * 80)
    print("📊 底部放量反转策略回测")
    print("=" * 80)
    
    print("\n🔌 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        return
    
    print("\n📋 获取股票数据...")
    df = get_stock_data(conn)
    if df.empty:
        close_connection(conn)
        return
    print(f"   ✅ 获取到 {len(df)} 条记录")
    
    print("\n🚀 开始分析...")
    results = []
    analyzed = 0
    
    grouped = df.groupby('ts_code')
    for ts_code, group in grouped:
        analyzed += 1
        if analyzed % 500 == 0:
            print(f"   ⏳ 已分析 {analyzed} 只股票...")
        
        stock_name = group.iloc[-1].get('stock_name', '')
        is_qualified, first_date, first_idx = check_strategy(group)
        
        if not is_qualified:
            continue
        
        returns = calculate_returns(group, first_idx)
        
        results.append({
            'ts_code': ts_code,
            'stock_name': stock_name,
            'first_date': returns['first_date'],
            '2_10_max_gain': returns['2_10_max_gain'],
            '2_10_max_loss': returns['2_10_max_loss'],
            '11_20_max_gain': returns['11_20_max_gain'],
            '11_20_max_loss': returns['11_20_max_loss'],
            '21_40_max_gain': returns['21_40_max_gain'],
            '21_40_max_loss': returns['21_40_max_loss'],
            '41_60_max_gain': returns['41_60_max_gain'],
            '41_60_max_loss': returns['41_60_max_loss']
        })
    
    print(f"\n✅ 分析完成！共 {len(results)} 只股票满足策略")
    
    close_connection(conn)
    
    # 随机选取200只
    if len(results) > 200:
        results = random.sample(results, 200)
        print(f"🎲 随机选取 200 只股票")
    elif len(results) > 0:
        print(f"🎲 满足策略股票不足200只，选取全部 {len(results)} 只")
    
    # 保存CSV
    result_df = pd.DataFrame(results)
    fn = f"底部反转回测_{datetime.now().strftime('%Y%m%d')}.csv"
    result_df.to_csv(fn, index=False, encoding='utf-8-sig')
    
    print(f"\n💾 结果已保存: {fn}")
    print("\n🎉 回测完成！")


if __name__ == "__main__":
    main()