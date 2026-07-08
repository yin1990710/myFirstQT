#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
爆量上涨选股策略
选出近3个交易日中有爆量上涨特征的股票
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


def read_stock_data(days=40):
    """读取股票数据"""
    connection = get_mysql_connection()
    if not connection:
        print("❌ 数据库连接失败")
        return {}

    target_date = get_target_date()
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    query = """
    SELECT
        d.ts_code,
        d.trade_date,
        d.close,
        d.amount,
        i.stock_name,
        i.total_mv
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
                'amount': float(record['amount'] or 0)
            })

        return stock_data

    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return {}
    finally:
        close_connection(connection)


def has_boom_volume(records):
    """
    判断是否有爆量上涨特征
    
    参数:
        records: 股票记录列表
    
    返回:
        result: 是否符合条件
        details: 详细信息
    """
    
    if len(records) < 10:
        return False, {'reason': '数据不足'}
    
    # 获取最近3个交易日数据
    recent_3 = records[-3:]
    if len(recent_3) < 3:
        return False, {'reason': '最近3天数据不足'}
    
    # 提取金额数据
    amounts = [r['amount'] for r in recent_3]
    trade_dates = [r['trade_date'] for r in recent_3]
    
    # 计算每日涨幅（需要前一天收盘价）
    pct_chg_list = []
    for i in range(len(records)-1, len(records)-4, -1):
        if i >= 1:
            prev_close = records[i-1]['close']
            if prev_close == 0 or prev_close is None:
                continue
            pct_chg = (records[i]['close'] - prev_close) / prev_close * 100
            pct_chg_list.insert(0, pct_chg)
    
    if len(pct_chg_list) < 3:
        return False, {'reason': '无法计算涨幅'}
    
    # 检查是否有爆量上涨日
    for i in range(3):
        current_amount = amounts[i]
        other_amounts = amounts[:i] + amounts[i+1:]
        
        if len(other_amounts) < 2:
            continue
        
        # 计算其他交易日平均金额
        avg_other_amount = sum(other_amounts) / len(other_amounts)
        
        # 条件1：当日交易额是其他交易日平均的2.5倍以上
        # 条件2：成交额*1000大于5亿（即amount > 500000）
        # 条件3：涨幅大于6%
        if current_amount >= avg_other_amount * 2.5 and \
           current_amount * 1000 > 500000000 and \
           pct_chg_list[i] > 6:
            details = {
                'boom_date': trade_dates[i],
                'boom_amount': current_amount,
                'avg_other_amount': avg_other_amount,
                'amount_ratio': current_amount / avg_other_amount,
                'pct_chg': pct_chg_list[i]
            }
            return True, details
    
    return False, {'reason': '未满足爆量上涨条件'}


def filter_stocks(stock_data):
    """筛选爆量上涨股票"""
    
    boom_stocks = []
    
    for ts_code, data in stock_data.items():
        records = data['records']
        
        # 检查是否符合爆量上涨条件
        is_boom, details = has_boom_volume(records)
        
        if is_boom:
            stock_info = {
                'ts_code': ts_code,
                'stock_name': data['stock_name'],
                'total_mv': data['total_mv'],
                'details': details
            }
            boom_stocks.append(stock_info)
    
    # 按爆量倍数排序
    boom_stocks.sort(key=lambda x: x['details']['amount_ratio'], reverse=True)
    
    return boom_stocks


def save_results(boom_stocks, output_dir):
    """保存选股结果"""
    
    csv_file = os.path.join(output_dir, '爆量上涨股票.csv')
    
    with open(csv_file, 'w', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,总市值(亿),爆量日期,爆量倍数,涨幅(%),爆量金额(万元)\n")
        for stock in boom_stocks:
            details = stock['details']
            f.write(f"{stock['ts_code']},{stock['stock_name']},")
            f.write(f"{stock['total_mv']/100000000:.2f},")
            f.write(f"{details['boom_date']},")
            f.write(f"{details['amount_ratio']:.2f},")
            f.write(f"{details['pct_chg']:.2f},")
            f.write(f"{details['boom_amount']/10000:.2f}\n")
    
    print(f"✅ 结果已保存至: {csv_file}")


def print_results(boom_stocks):
    """打印选股结果"""
    
    print("\n" + "=" * 80)
    print("💥 爆量上涨选股结果")
    print("=" * 80)
    
    print(f"{'股票代码':<12} {'股票名称':<10} {'市值(亿)':<10} {'爆量日期':<12} {'爆量倍数':<10} {'涨幅(%)':<10}")
    print("-" * 80)
    
    for stock in boom_stocks:
        details = stock['details']
        print(f"{stock['ts_code']:<12} {stock['stock_name']:<10} "
              f"{stock['total_mv']/100000000:<10.2f} {details['boom_date']:<12} "
              f"{details['amount_ratio']:<10.2f} {details['pct_chg']:<10.2f}")
    
    print(f"\n共选出 {len(boom_stocks)} 只爆量上涨股票")


def main():
    print("=" * 80)
    print("💥 爆量上涨选股策略")
    print("   选出近3个交易日中有爆量上涨特征的股票")
    print("=" * 80)
    
    # 创建输出目录
    target_date = get_target_date()
    output_dir = f"爆量上涨{target_date}"
    
    if os.path.exists(output_dir):
        print(f"🗑️ 已删除旧文件夹: {output_dir}")
        shutil.rmtree(output_dir)
    
    os.makedirs(output_dir, exist_ok=True)
    print(f"📁 创建文件夹: {output_dir}")
    
    # 读取数据
    print("\n🔌 正在连接数据库...")
    stock_data = read_stock_data(days=40)
    
    if not stock_data:
        print("❌ 未读取到数据")
        return
    
    print(f"✅ 成功读取 {len(stock_data)} 只股票的数据")
    
    # 筛选爆量上涨股票
    print("\n🔍 正在筛选爆量上涨股票...")
    boom_stocks = filter_stocks(stock_data)
    
    # 打印结果
    print_results(boom_stocks)
    
    # 保存结果
    save_results(boom_stocks, output_dir)
    
    print("\n" + "=" * 80)
    print(f"✅ 选股完成！共选出 {len(boom_stocks)} 只爆量上涨股票")
    print("=" * 80)


if __name__ == "__main__":
    main()