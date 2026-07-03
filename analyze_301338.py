#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""分析股票301338.SZ为什么没有被select_2wave_daily.py选出来"""

from mysql_connection import get_mysql_connection
import pandas as pd

def analyze_stock():
    conn = get_mysql_connection()
    cursor = conn.cursor()
    
    # 1. 股票基本信息
    cursor.execute("""
        SELECT ts_code, stock_name, total_mv, circ_mv 
        FROM stock_info_t 
        WHERE ts_code = '301338.SZ'
    """)
    info = cursor.fetchone()
    
    print("=" * 80)
    print("301338.SZ 选股条件分析")
    print("=" * 80)
    
    print(f"\n【股票基本信息】")
    print(f"  代码: {info['ts_code']}")
    print(f"  名称: {info['stock_name']}")
    print(f"  总市值: {info['total_mv']/10000:.2f}亿元")
    print(f"  流通市值: {info['circ_mv']/10000:.2f}亿元")
    
    # 条件1: 流通市值>50亿
    circ_mv = float(info['circ_mv'])
    cond1 = circ_mv >= 5000000000
    print(f"\n【条件1】流通市值>50亿")
    print(f"  当前: {circ_mv/10000:.2f}亿")
    print(f"  结果: {'✓ 通过' if cond1 else '❌ 未通过'}")
    
    # 2. 获取最近40天数据
    cursor.execute("""
        SELECT trade_date, open, close, high, low, amount, pct_chg
        FROM stock_daily_t
        WHERE ts_code = '301338.SZ'
        ORDER BY trade_date DESC
        LIMIT 40
    """)
    rows = cursor.fetchall()
    df = pd.DataFrame(rows)
    df = df.sort_values('trade_date')
    df['close'] = df['close'].astype(float)
    df['amount'] = df['amount'].astype(float)
    df['pct_chg'] = df['pct_chg'].astype(float)
    
    print(f"\n【数据范围】最近40天: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
    
    # 条件2: 第一波涨幅>15%
    max_idx = df['close'].idxmax()
    max_price = df.loc[max_idx, 'close']
    max_date = df.loc[max_idx, 'trade_date']
    
    first_wave = df.loc[:max_idx]
    first_wave_gain = (first_wave.iloc[-1]['close'] - first_wave.iloc[0]['close']) / first_wave.iloc[0]['close'] * 100
    
    print(f"\n【条件2】第一波涨幅>15%")
    print(f"  最高点: {max_date}, 价格{max_price:.2f}")
    print(f"  第一波天数: {len(first_wave)}")
    print(f"  涨幅: {first_wave_gain:.2f}%")
    cond2 = first_wave_gain >= 15
    print(f"  结果: {'✓ 通过' if cond2 else '❌ 未通过'}")
    
    # 条件3: 调整未跌破MA30 (允许10%误差)
    adjustment = df.loc[max_idx+1:]
    
    if len(adjustment) == 0:
        cond3 = False
        print(f"\n【条件3】调整未跌破MA30")
        print(f"  ❌ 没有调整阶段数据")
    else:
        # 获取更多数据计算MA30
        cursor.execute("""
            SELECT trade_date, close
            FROM stock_daily_t
            WHERE ts_code = '301338.SZ'
            ORDER BY trade_date DESC
            LIMIT 60
        """)
        rows2 = cursor.fetchall()
        close_prices = [float(r['close']) for r in reversed(rows2)]
        
        # 计算MA30
        ma30_values = []
        for i in range(30, len(close_prices)+1):
            ma30 = sum(close_prices[i-30:i]) / 30
            ma30_values.append(ma30)
        
        adjustment_start_idx = len(ma30_values) - len(adjustment)
        
        print(f"\n【条件3】调整未跌破MA30 (允许10%误差)")
        print(f"  调整天数: {len(adjustment)}")
        
        below_ma30 = False
        for i in range(len(adjustment)):
            close_val = adjustment.iloc[i]['close']
            ma30_val = ma30_values[adjustment_start_idx + i]
            threshold = ma30_val * 0.90
            
            if close_val < threshold:
                below_ma30 = True
                print(f"  ❌ {adjustment.iloc[i]['trade_date']}: 收盘{close_val:.2f} < MA30{ma30_val:.2f}*0.90={threshold:.2f}")
        
        cond3 = not below_ma30
        if cond3:
            print(f"  ✓ 调整期间未跌破MA30")
    
    # 条件4: 调整量能萎缩<60%
    if len(adjustment) == 0:
        cond4 = False
        print(f"\n【条件4】调整量能萎缩<60%")
        print(f"  ❌ 没有调整阶段数据")
    else:
        first_wave_amount = first_wave['amount'].tolist()
        adjustment_amount = adjustment['amount'].tolist()
        
        first_wave_avg_all = sum(first_wave_amount) / len(first_wave_amount)
        boom_threshold = first_wave_avg_all * 1.5
        boom_days = [a for a in first_wave_amount if a > boom_threshold]
        
        if boom_days:
            first_wave_avg = sum(boom_days) / len(boom_days)
        else:
            first_wave_avg = first_wave_avg_all
        
        adjustment_avg = sum(adjustment_amount) / len(adjustment_amount)
        ratio = adjustment_avg / first_wave_avg
        
        print(f"\n【条件4】调整量能萎缩<60%")
        print(f"  第一波放量平均成交额: {first_wave_avg:.2f}千元")
        print(f"  调整期间平均成交额: {adjustment_avg:.2f}千元")
        print(f"  调整量/上涨量: {ratio:.2f}")
        cond4 = ratio <= 0.60
        print(f"  结果: {'✓ 通过' if cond4 else '❌ 未通过'}")
    
    # 条件5: 启动信号 - 今日涨幅>1.5% OR 近3日波谷
    today_gain = df.iloc[-1]['pct_chg']
    
    print(f"\n【条件5】启动信号: 今日涨幅>1.5% OR 近3日波谷")
    print(f"  今日涨跌幅: {today_gain:.2f}%")
    
    if today_gain >= 1.5:
        cond5 = True
        print(f"  ✓ 今日涨幅满足条件")
    else:
        # 检查近3日波谷
        recent_15 = df.iloc[-15:].copy()
        turning_points = []
        close_list = recent_15['close'].tolist()
        
        for i in range(2, len(close_list)-2):
            if close_list[i] < close_list[i-1] and close_list[i] < close_list[i+1] and \
               close_list[i] <= close_list[i-2] and close_list[i] <= close_list[i+2]:
                turning_points.append(i)
        
        has_recent_trough = any(tp >= len(close_list) - 3 for tp in turning_points)
        cond5 = has_recent_trough
        
        if has_recent_trough:
            print(f"  ✓ 近3个交易日出现波谷")
        else:
            print(f"  ❌ 近3个交易日没有波谷")
    
    # 条件6: 成交额>5亿
    latest_amount = df.iloc[-1]['amount']
    amount_yuan = latest_amount * 1000  # amount单位为千元
    
    print(f"\n【条件6】成交额>5亿")
    print(f"  今日成交额: {amount_yuan/100000000:.2f}亿元")
    cond6 = amount_yuan > 500000000
    print(f"  结果: {'✓ 通过' if cond6 else '❌ 未通过'}")
    
    # 总结
    print("\n" + "=" * 80)
    print("【分析结论】")
    print("=" * 80)
    
    all_conditions = {
        '流通市值>50亿': cond1,
        '第一波涨幅>15%': cond2,
        '调整未破MA30': cond3,
        '量能萎缩<60%': cond4,
        '启动信号': cond5,
        '成交额>5亿': cond6
    }
    
    for cond_name, result in all_conditions.items():
        status = '✓' if result else '❌'
        print(f"  {status} {cond_name}")
    
    print("\n" + "=" * 80)
    
    failed = [k for k, v in all_conditions.items() if not v]
    if failed:
        print(f"未通过条件: {', '.join(failed)}")
        print("这就是该股票没有被选出的原因！")
    else:
        print("所有条件都通过，应该被选出！")
    
    print("=" * 80)
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    analyze_stock()