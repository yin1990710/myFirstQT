#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
基于波浪理论的选股模型 v2
选出处于上升二浪或三浪中的股票

特点：不依赖wave_tag表，直接通过价格数据识别波浪形态
"""

import os
import sys
from datetime import datetime, timedelta
import shutil

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    """获取目标日期（15点前用昨天，15点后用今天）"""
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def identify_local_extremes(prices, window=5):
    """
    识别局部极值点（波谷和波峰）
    
    参数:
        prices: 价格列表
        window: 窗口大小（前后window天的极值）
    
    返回:
        troughs: 波谷索引列表
        peaks: 波峰索引列表
    """
    troughs = []
    peaks = []
    
    for i in range(window, len(prices) - window):
        # 判断是否是局部波谷
        is_trough = all(prices[i] <= prices[i+j] for j in range(-window, window+1) if j != 0)
        if is_trough:
            troughs.append(i)
        
        # 判断是否是局部波峰
        is_peak = all(prices[i] >= prices[i+j] for j in range(-window, window+1) if j != 0)
        if is_peak:
            peaks.append(i)
    
    return troughs, peaks


def detect_wave_stage(prices, amounts):
    """
    识别波浪阶段
    
    参数:
        prices: 价格列表（从早到晚）
        amounts: 成交额列表
    
    返回:
        wave_stage: 波浪阶段 ('wave2', 'wave3', 'wave1', 'unknown')
        confidence: 置信度 (0-1)
        details: 详细信息
    """
    
    if len(prices) < 60:
        return 'unknown', 0, {'reason': '数据不足60天'}
    
    # 识别局部极值点
    troughs, peaks = identify_local_extremes(prices, window=3)
    
    if not troughs or not peaks:
        return 'unknown', 0, {'reason': '未识别到极值点'}
    
    # 获取最近的数据
    current_price = prices[-1]
    current_idx = len(prices) - 1
    
    # 找到最近的有效波谷和波峰
    recent_trough = None
    recent_peak = None
    prev_trough = None
    prev_peak = None
    
    for i in range(len(troughs) - 1, -1, -1):
        if recent_trough is None:
            recent_trough = troughs[i]
        if troughs[i] < recent_trough and prev_trough is None:
            prev_trough = troughs[i]
            break
    
    for i in range(len(peaks) - 1, -1, -1):
        if recent_peak is None:
            recent_peak = peaks[i]
        if peaks[i] < recent_peak and prev_peak is None:
            prev_peak = peaks[i]
            break
    
    # 如果最近的是波峰，说明刚从峰值下跌，可能是二浪回调
    if recent_peak > recent_trough:
        # 当前处于回调中（二浪）
        trough_price = prices[recent_trough]
        peak_price = prices[recent_peak]
        
        # 计算回调幅度
        decline_pct = (peak_price - current_price) / peak_price * 100
        rise_pct = (peak_price - trough_price) / trough_price * 100
        
        # 一浪涨幅需要足够大（>10%）
        # 二浪回调幅度通常在23.6%-78.6%
        
        if rise_pct >= 8:  # 一浪涨幅至少8%
            # 计算成交额变化
            recent_5_avg = sum(amounts[-5:]) / 5
            peak_period_amounts = amounts[max(0, recent_peak-5):recent_peak+1]
            peak_avg = sum(peak_period_amounts) / len(peak_period_amounts) if peak_period_amounts else recent_5_avg
            amount_ratio = recent_5_avg / peak_avg if peak_avg > 0 else 1
            
            details = {
                'trough_price': trough_price,
                'peak_price': peak_price,
                'current_price': current_price,
                'decline_pct': decline_pct,
                'rise_pct': rise_pct,
                'amount_ratio': amount_ratio
            }
            
            # 二浪特征：回调幅度在20%-70%之间，且回调时缩量
            if 20 <= decline_pct <= 70:
                confidence = 0.7 + (0.1 if amount_ratio < 0.8 else 0)
                return 'wave2', min(0.9, confidence), details
            
            # 或者回调时明显缩量
            if amount_ratio < 0.7 and decline_pct > 10:
                return 'wave2', 0.65, details
    
    # 如果最近的是波谷，说明可能是一浪或三浪
    else:
        trough_price = prices[recent_trough]
        rise_pct = (current_price - trough_price) / trough_price * 100
        
        # 计算近期成交额
        recent_5_avg = sum(amounts[-5:]) / 5
        prev_5_avg = sum(amounts[-10:-5]) / 5 if len(amounts) >= 10 else recent_5_avg
        amount_ratio = recent_5_avg / prev_5_avg if prev_5_avg > 0 else 1
        
        # 找到前一个波峰
        if prev_peak:
            prev_peak_price = prices[prev_peak]
            
            # 如果当前价格突破前高，可能是三浪
            if current_price > prev_peak_price * 0.98 and rise_pct > 10:
                details = {
                    'trough_price': trough_price,
                    'prev_peak_price': prev_peak_price,
                    'current_price': current_price,
                    'rise_pct': rise_pct,
                    'amount_ratio': amount_ratio
                }
                
                # 三浪特征：突破前高，成交额放大
                if amount_ratio > 1.2:
                    return 'wave3', 0.85, details
                
                elif amount_ratio > 1.0:
                    return 'wave3', 0.75, details
            
            # 如果还没有突破前高，可能是一浪
            elif rise_pct > 5:
                details = {
                    'trough_price': trough_price,
                    'current_price': current_price,
                    'rise_pct': rise_pct,
                    'amount_ratio': amount_ratio
                }
                return 'wave1', 0.6, details
    
    return 'unknown', 0, {'reason': '不符合二浪或三浪特征'}


def read_stock_data(days=90):
    """读取股票数据"""
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return {}

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days + 20)).strftime('%Y%m%d')

    query = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.amount,
        d.high,
        d.low,
        d.pct_chg,
        i.total_mv,
        i.stock_name
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.trade_date >= %s AND d.trade_date <= %s
    ORDER BY d.ts_code, d.trade_date
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (start_date, target_date))
            results = cursor.fetchall()

        # 按股票代码分组
        stock_data = {}
        for record in results:
            ts_code = record['ts_code']
            if ts_code not in stock_data:
                stock_data[ts_code] = {
                    'stock_name': record.get('stock_name', ''),
                    'total_mv': float(record['total_mv'] or 0),
                    'records': []
                }
            
            stock_data[ts_code]['records'].append({
                'trade_date': record['trade_date'],
                'close': float(record['close'] or 0),
                'amount': float(record['amount'] or 0),
                'high': float(record['high'] or 0),
                'low': float(record['low'] or 0),
                'pct_chg': float(record['pct_chg'] or 0)
            })

        return stock_data

    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return {}
    finally:
        close_connection(connection)


def filter_wave_stocks(stock_data, min_mv=5e8):
    """筛选波浪股票"""
    
    wave2_stocks = []
    wave3_stocks = []
    wave1_stocks = []
    
    for ts_code, data in stock_data.items():
        # 市值过滤
        if data['total_mv'] < min_mv:
            continue
        
        records = data['records']
        if len(records) < 60:
            continue
        
        # 检查数据完整性（至少60天）
        recent_60 = records[-60:]
        if len(recent_60) < 60:
            continue
        
        # 提取数据
        prices = [r['close'] for r in records]
        amounts = [r['amount'] for r in records]
        
        # 识别波浪阶段
        wave_stage, confidence, details = detect_wave_stage(prices, amounts)
        
        stock_info = {
            'ts_code': ts_code,
            'stock_name': data['stock_name'],
            'total_mv': data['total_mv'],
            'wave_stage': wave_stage,
            'confidence': confidence,
            'current_price': prices[-1],
            'details': details
        }
        
        if wave_stage == 'wave2':
            wave2_stocks.append(stock_info)
        elif wave_stage == 'wave3':
            wave3_stocks.append(stock_info)
        elif wave_stage == 'wave1':
            wave1_stocks.append(stock_info)
    
    # 按置信度排序
    wave2_stocks.sort(key=lambda x: x['confidence'], reverse=True)
    wave3_stocks.sort(key=lambda x: x['confidence'], reverse=True)
    wave1_stocks.sort(key=lambda x: x['confidence'], reverse=True)
    
    return wave2_stocks, wave3_stocks, wave1_stocks


def save_results(wave2_stocks, wave3_stocks, wave1_stocks, output_dir):
    """保存选股结果"""
    
    # 保存二浪股票
    wave2_file = os.path.join(output_dir, '波浪选股_二浪回调.csv')
    with open(wave2_file, 'w', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,当前价格,总市值(亿),置信度,回调幅度(%),一浪涨幅(%)\n")
        for stock in wave2_stocks:
            details = stock['details']
            f.write(f"{stock['ts_code']},{stock['stock_name']},{stock['current_price']:.2f},")
            f.write(f"{stock['total_mv']/100000000:.2f},{stock['confidence']:.2f},")
            f.write(f"{details.get('decline_pct', 0):.2f},{details.get('rise_pct', 0):.2f}\n")
    
    print(f"✅ 二浪股票已保存至: {wave2_file}")
    
    # 保存三浪股票
    wave3_file = os.path.join(output_dir, '波浪选股_三浪主升.csv')
    with open(wave3_file, 'w', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,当前价格,总市值(亿),置信度,三浪涨幅(%)\n")
        for stock in wave3_stocks:
            details = stock['details']
            f.write(f"{stock['ts_code']},{stock['stock_name']},{stock['current_price']:.2f},")
            f.write(f"{stock['total_mv']/100000000:.2f},{stock['confidence']:.2f},")
            f.write(f"{details.get('rise_pct', 0):.2f}\n")
    
    print(f"✅ 三浪股票已保存至: {wave3_file}")
    
    # 保存一浪股票
    wave1_file = os.path.join(output_dir, '波浪选股_一浪启动.csv')
    with open(wave1_file, 'w', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,当前价格,总市值(亿),置信度,一浪涨幅(%)\n")
        for stock in wave1_stocks:
            details = stock['details']
            f.write(f"{stock['ts_code']},{stock['stock_name']},{stock['current_price']:.2f},")
            f.write(f"{stock['total_mv']/100000000:.2f},{stock['confidence']:.2f},")
            f.write(f"{details.get('rise_pct', 0):.2f}\n")
    
    print(f"✅ 一浪股票已保存至: {wave1_file}")


def print_results(wave2_stocks, wave3_stocks, wave1_stocks):
    """打印选股结果"""
    
    print("\n" + "=" * 80)
    print("🌊 波浪理论选股结果")
    print("=" * 80)
    
    print(f"\n📊 选股统计:")
    print(f"   二浪回调股票: {len(wave2_stocks)} 只")
    print(f"   三浪主升股票: {len(wave3_stocks)} 只")
    print(f"   一浪启动股票: {len(wave1_stocks)} 只")
    
    print(f"\n🌊 二浪回调股票 (共 {len(wave2_stocks)} 只):")
    print("-" * 80)
    print(f"{'股票代码':<12} {'股票名称':<10} {'价格':<8} {'市值(亿)':<10} {'置信度':<8} {'回调%':<10} {'一浪涨幅%':<10}")
    print("-" * 80)
    
    for i, stock in enumerate(wave2_stocks[:15]):  # 最多显示15只
        details = stock['details']
        print(f"{stock['ts_code']:<12} {stock['stock_name']:<10} {stock['current_price']:<8.2f} "
              f"{stock['total_mv']/100000000:<10.2f} {stock['confidence']:<8.2f} "
              f"{details.get('decline_pct', 0):<10.2f} {details.get('rise_pct', 0):<10.2f}")
    
    if len(wave2_stocks) > 15:
        print(f"... 还有 {len(wave2_stocks) - 15} 只")
    
    print(f"\n🚀 三浪主升股票 (共 {len(wave3_stocks)} 只):")
    print("-" * 80)
    print(f"{'股票代码':<12} {'股票名称':<10} {'价格':<8} {'市值(亿)':<10} {'置信度':<8} {'三浪涨幅%':<12}")
    print("-" * 80)
    
    for i, stock in enumerate(wave3_stocks[:15]):  # 最多显示15只
        details = stock['details']
        print(f"{stock['ts_code']:<12} {stock['stock_name']:<10} {stock['current_price']:<8.2f} "
              f"{stock['total_mv']/100000000:<10.2f} {stock['confidence']:<8.2f} "
              f"{details.get('rise_pct', 0):<12.2f}")
    
    if len(wave3_stocks) > 15:
        print(f"... 还有 {len(wave3_stocks) - 15} 只")
    
    print(f"\n🔥 一浪启动股票 (共 {len(wave1_stocks)} 只):")
    print("-" * 80)
    print(f"{'股票代码':<12} {'股票名称':<10} {'价格':<8} {'市值(亿)':<10} {'置信度':<8} {'一浪涨幅%':<12}")
    print("-" * 80)
    
    for i, stock in enumerate(wave1_stocks[:15]):  # 最多显示15只
        details = stock['details']
        print(f"{stock['ts_code']:<12} {stock['stock_name']:<10} {stock['current_price']:<8.2f} "
              f"{stock['total_mv']/100000000:<10.2f} {stock['confidence']:<8.2f} "
              f"{details.get('rise_pct', 0):<12.2f}")
    
    if len(wave1_stocks) > 15:
        print(f"... 还有 {len(wave1_stocks) - 15} 只")


def print_strategy_intro():
    """打印策略说明"""
    print("\n" + "=" * 80)
    print("📖 波浪理论选股策略说明")
    print("=" * 80)
    print("""
【波浪理论简介】
上升趋势由五浪组成：1浪(启动)→2浪(回调)→3浪(主升)→4浪(调整)→5浪(冲刺)
调整趋势由三浪组成：A浪→B浪→C浪

【选股目标】
- 二浪回调：从1浪高点回调20%-70%，为一浪涨幅的回撤
- 三浪主升：突破前高，强劲上涨，通常是最大的推动浪

【二浪特征】
- 从近期高点回调20%-70%
- 一浪涨幅至少8%
- 回调时成交额萎缩（<上涨时的80%）
- 回调幅度在38.2%-61.8%区间最佳

【三浪特征】
- 突破前一波段高点
- 从波谷上涨超过10%
- 成交额明显放大（>前5天的1.2倍）
- 通常是最强的推动浪

【一浪特征】
- 从波谷开始上涨
- 涨幅超过5%
- 尚未突破前高
    """)


def main():
    print_strategy_intro()
    
    print("\n" + "=" * 80)
    print("🌊 开始执行波浪理论选股策略")
    print("=" * 80)
    
    # 创建输出目录
    target_date = get_target_date()
    output_dir = f"波浪选股{target_date}"
    
    if os.path.exists(output_dir):
        print(f"🗑️ 已删除旧文件夹: {output_dir}")
        shutil.rmtree(output_dir)
    
    os.makedirs(output_dir, exist_ok=True)
    print(f"📁 创建文件夹: {output_dir}")
    
    # 读取数据
    print("\n🔌 正在连接数据库...")
    stock_data = read_stock_data(days=90)
    
    if not stock_data:
        print("❌ 未读取到数据")
        return
    
    print(f"✅ 成功读取 {len(stock_data)} 只股票的数据")
    
    # 筛选波浪股票
    print("\n🔍 正在分析波浪形态...")
    wave2_stocks, wave3_stocks, wave1_stocks = filter_wave_stocks(stock_data, min_mv=5e8)
    
    # 打印结果
    print_results(wave2_stocks, wave3_stocks, wave1_stocks)
    
    # 保存结果
    save_results(wave2_stocks, wave3_stocks, wave1_stocks, output_dir)
    
    print("\n" + "=" * 80)
    print(f"✅ 选股完成！")
    print(f"   二浪回调股票: {len(wave2_stocks)} 只")
    print(f"   三浪主升股票: {len(wave3_stocks)} 只")
    print(f"   一浪启动股票: {len(wave1_stocks)} 只")
    print("=" * 80)


if __name__ == "__main__":
    main()