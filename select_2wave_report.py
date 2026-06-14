#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
二浪启动选股报告生成器
从二浪启动选股结果中提取股票，查找同行业市值最大的5只股票
"""

import os
import sys
from datetime import datetime, timedelta
import csv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection


def get_target_date():
    """获取目标日期（15点前用昨天，15点后用今天）"""
    now = datetime.now()
    if now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def read_selected_stocks():
    """读取二浪启动选股结果"""
    target_date = get_target_date()
    folder_name = f"二浪启动{target_date}"
    csv_filename = f"二浪启动{target_date}.csv"
    csv_path = os.path.join(folder_name, csv_filename)
    
    if not os.path.exists(csv_path):
        print(f"❌ 选股结果文件不存在: {csv_path}")
        return None
    
    stocks = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            stocks.append({
                'ts_code': row['股票代码'],
                'stock_name': row['股票名称']
            })
    
    print(f"✅ 读取到 {len(stocks)} 只选股结果")
    return stocks


def get_stock_industry(ts_code):
    """获取股票的行业信息"""
    connection = get_mysql_connection()
    if not connection:
        return None
    
    query = """
    SELECT industry
    FROM stock_info_t
    WHERE ts_code = %s
    LIMIT 1
    """
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (ts_code))
            result = cursor.fetchone()
            if result:
                return result['industry']
        return None
    except Exception as e:
        print(f"❌ 查询股票行业失败: {e}")
        return None
    finally:
        close_connection(connection)


def get_top5_stocks_by_industry(industry):
    """获取同行业市值最大的5只股票"""
    connection = get_mysql_connection()
    if not connection:
        return []
    
    query = """
    SELECT ts_code, stock_name, industry, total_mv
    FROM stock_info_t
    WHERE industry = %s
    AND total_mv IS NOT NULL
    AND total_mv > 0
    ORDER BY total_mv DESC
    LIMIT 5
    """
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, (industry))
            results = cursor.fetchall()
            return results
    except Exception as e:
        print(f"❌ 查询同行业股票失败: {e}")
        return []
    finally:
        close_connection(connection)


def generate_report(selected_stocks):
    """生成报告"""
    report_data = []
    
    for stock in selected_stocks:
        ts_code = stock['ts_code']
        stock_name = stock['stock_name']
        
        # 获取股票行业
        industry = get_stock_industry(ts_code)
        if not industry:
            print(f"⚠️ {ts_code} {stock_name} 无法获取行业信息")
            continue
        
        print(f"🔍 {ts_code} {stock_name} - 行业: {industry}")
        
        # 获取同行业市值最大的5只股票
        top5_stocks = get_top5_stocks_by_industry(industry)
        
        if not top5_stocks:
            print(f"  ⚠️ 该行业无其他股票数据")
            continue
        
        print(f"  ✅ 找到 {len(top5_stocks)} 只同行业股票")
        
        for s in top5_stocks:
            report_data.append({
                'ts_code': s['ts_code'],
                'stock_name': s['stock_name'],
                'industry': s['industry'],
                'total_mv': float(s['total_mv']) / 100000000  # 转换为亿元
            })
    
    return report_data


def save_report(report_data, folder_path):
    """保存报告为CSV文件"""
    csv_path = os.path.join(folder_path, "select_2wave_report.csv")
    
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,行业,总市值(亿)\n")
        for row in report_data:
            f.write(f"{row['ts_code']},{row['stock_name']},{row['industry']},{row['total_mv']:.2f}\n")
    
    print(f"✅ 报告已保存: {csv_path}")
    return csv_path


def print_report(report_data):
    """打印报告"""
    print("\n" + "=" * 80)
    print("📊 二浪启动选股报告 - 同行业市值Top5")
    print("=" * 80)
    
    print(f"{'股票代码':<12} {'股票名称':<10} {'行业':<15} {'总市值(亿)':<12}")
    print("-" * 80)
    
    for row in report_data:
        print(f"{row['ts_code']:<12} {row['stock_name']:<10} {row['industry']:<15} {row['total_mv']:<12.2f}")
    
    print(f"\n共 {len(report_data)} 条记录")


def main():
    print("=" * 80)
    print("📊 二浪启动选股报告生成器")
    print("=" * 80)
    
    # 获取目标日期和文件夹路径
    target_date = get_target_date()
    folder_path = f"二浪启动{target_date}"
    
    # 读取选股结果
    selected_stocks = read_selected_stocks()
    if not selected_stocks:
        print("❌ 无选股结果，退出程序")
        return
    
    # 生成报告
    print("\n🔍 正在查询同行业股票...")
    report_data = generate_report(selected_stocks)
    
    if not report_data:
        print("❌ 无报告数据，退出程序")
        return
    
    # 打印报告
    print_report(report_data)
    
    # 保存报告到选股结果目录
    save_report(report_data, folder_path)
    
    print("\n" + "=" * 80)
    print("🎉 报告生成完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()