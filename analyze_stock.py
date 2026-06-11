#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection
from datetime import datetime, timedelta

def analyze_stock(ts_code):
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return
    
    cursor = conn.cursor()
    
    # 获取最近150天数据
    target_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=150)).strftime('%Y%m%d')
    
    cursor.execute('''
        SELECT trade_date, close, high, low, amount, pct_chg 
        FROM stock_daily_t 
        WHERE ts_code = %s AND trade_date >= %s AND trade_date <= %s
        ORDER BY trade_date
    ''', (ts_code, start_date, target_date))
    
    results = cursor.fetchall()
    close_connection(conn)
    
    if not results:
        print(f"❌ 未找到 {ts_code} 的数据")
        return
    
    print(f"📊 分析股票: {ts_code}")
    print("=" * 80)
    print(f"获取到 {len(results)} 条记录")
    
    # 提取数据
    close_prices = [float(r['close'] or 0) for r in results]
    amounts = [float(r['amount'] or 0) for r in results]
    trade_dates = [r['trade_date'] for r in results]
    
    # 检查数据完整性
    if len(results) < 45:
        print(f"❌ 数据不足45天，当前只有{len(results)}天")
        return
    
    # 取最近40个交易日
    recent_40 = results[-40:]
    close_40 = [float(r['close'] or 0) for r in recent_40]
    amount_40 = [float(r['amount'] or 0) for r in recent_40]
    
    # 找到最高价位置
    max_price = max(close_40)
    max_idx = close_40.index(max_price)
    
    first_wave = recent_40[:max_idx+1]
    adjustment = recent_40[max_idx+1:]
    
    print(f"\n--- 第一波上涨分析 ---")
    print(f"第一波起始日期: {first_wave[0]['trade_date']}")
    print(f"第一波结束日期: {first_wave[-1]['trade_date']}")
    print(f"第一波天数: {len(first_wave)}天")
    print(f"第一波日期列表 (收盘价/成交额):")
    
    first_wave_amounts = []
    for i, day in enumerate(first_wave, 1):
        amount = float(day['amount'] or 0)
        first_wave_amounts.append(amount)
        print(f"  {i:2d}. {day['trade_date']} - 收盘价: {float(day['close'] or 0):.2f} - 涨幅: {float(day['pct_chg'] or 0):.2f}% - 成交额: {amount:.0f} 千元")
    
    # 计算整个上涨期的平均值
    total_first_amount = sum(first_wave_amounts)
    avg_first_amount_all = total_first_amount / len(first_wave_amounts)
    
    # 方案一：只计算放量上涨阶段（成交额超过整个上涨期平均的1.5倍）
    boom_threshold = avg_first_amount_all * 1.5
    boom_days = [a for a in first_wave_amounts if a > boom_threshold]
    
    if boom_days:
        avg_first_amount = sum(boom_days) / len(boom_days)
        print(f"\n  上涨期总成交额: {total_first_amount:.0f} 千元")
        print(f"  上涨期日均成交额: {avg_first_amount_all:.0f} 千元")
        print(f"  放量阶段日均成交额: {avg_first_amount:.0f} 千元 (超过{boom_threshold:.0f}的天数: {len(boom_days)}/{len(first_wave_amounts)})")
    else:
        avg_first_amount = avg_first_amount_all
        print(f"\n  上涨期总成交额: {total_first_amount:.0f} 千元")
        print(f"  上涨期日均成交额: {avg_first_amount:.0f} 千元")
        print(f"  未发现明显放量阶段，使用全部天数计算")
    
    first_wave_close = [float(r['close'] or 0) for r in first_wave]
    first_wave_amount = [float(r['amount'] or 0) for r in first_wave]
    
    first_wave_gain = (first_wave_close[-1] - first_wave_close[0]) / first_wave_close[0] * 100
    print(f"第一波涨幅: {first_wave_gain:.2f}%")
    print(f"  → 条件要求: >15%")
    print(f"  → {'✅ 满足' if first_wave_gain >= 15 else '❌ 不满足'}")
    
    if len(first_wave) < 8:
        print(f"❌ 第一波天数不足8天，只有{len(first_wave)}天")
        return
    
    print(f"\n--- 调整阶段分析 ---")
    print(f"调整天数: {len(adjustment)}天")
    print(f"调整日期列表 (收盘价/成交额):")
    total_adj_amount = 0
    for i, day in enumerate(adjustment, 1):
        amount = float(day['amount'] or 0)
        total_adj_amount += amount
        print(f"  {i:2d}. {day['trade_date']} - 收盘价: {float(day['close'] or 0):.2f} - 涨幅: {float(day['pct_chg'] or 0):.2f}% - 成交额: {amount:.0f} 千元")
    
    if len(adjustment) < 2:
        print(f"❌ 调整天数不足2天")
        return
    
    avg_adj_amount = total_adj_amount / len(adjustment)
    amount_ratio = avg_adj_amount / avg_first_amount
    
    print(f"\n  调整期总成交额: {total_adj_amount:.0f} 千元")
    print(f"  调整期日均成交额: {avg_adj_amount:.0f} 千元")
    print(f"\n上涨期平均成交额: {avg_first_amount:.0f} 千元")
    print(f"调整期平均成交额: {avg_adj_amount:.0f} 千元")
    print(f"调整量/上涨量: {amount_ratio:.2f}")
    print(f"  → 条件要求: <0.90")
    print(f"  → {'✅ 满足' if amount_ratio < 0.90 else '❌ 不满足'}")
    
    # 检查30日均线
    ma30_list = []
    for i in range(30, len(close_prices)+1):
        ma30 = sum(close_prices[i-30:i]) / 30
        ma30_list.append(ma30)
    
    if len(ma30_list) >= len(adjustment):
        adj_ma30 = ma30_list[-len(adjustment):]
        adj_closes = [float(r['close'] or 0) for r in adjustment]
        
        below_ma30 = False
        for i in range(len(adjustment)):
            if adj_closes[i] < adj_ma30[i] * 0.90:
                below_ma30 = True
                break
        
        print(f"\n--- 30日均线分析 ---")
        print(f"调整期间是否跌破30日均线(90%): {'❌ 跌破' if below_ma30 else '✅ 未跌破'}")
    
    # 检查今日涨幅和波谷
    print(f"\n--- 启动信号分析 ---")
    today = results[-1]
    prev_day = results[-2] if len(results) >= 2 else None
    
    if prev_day:
        today_gain = (float(today['close'] or 0) - float(prev_day['close'] or 0)) / float(prev_day['close'] or 1) * 100
        print(f"今日涨幅: {today_gain:.2f}%")
        print(f"  → 条件要求: >1.5%")
        print(f"  → {'✅ 满足' if today_gain >= 1.5 else '❌ 不满足'}")
    
    # 检查波谷
    recent_15 = results[-15:]
    close_15 = [float(r['close'] or 0) for r in recent_15]
    has_trough = False
    
    for i in range(2, len(recent_15)-2):
        if (close_15[i] < close_15[i-1] and close_15[i] < close_15[i+1] and 
            close_15[i] <= close_15[i-2] and close_15[i] <= close_15[i+2]):
            if i >= len(recent_15) - 3:
                has_trough = True
                print(f"近3日出现波谷: ✅ 满足")
                break
    
    if not has_trough:
        print(f"近3日出现波谷: ❌ 不满足")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        analyze_stock(sys.argv[1])
    else:
        analyze_stock("600888.SH")
