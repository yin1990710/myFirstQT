#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
品字交易法同行业股票报告生成器
从品字交易法选股结果中提取股票，查找同行业市值最大的5只股票
"""

import os
import sys
import shutil
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
    """读取品字交易法选股结果"""
    target_date = get_target_date()
    
    # 尝试从文件夹中读取
    folder_name = f"品字交易法{target_date}"
    csv_filename = f"品字交易法{target_date}.csv"
    csv_path = os.path.join(folder_name, csv_filename)
    
    # 如果文件夹中不存在，尝试从根目录读取
    if not os.path.exists(csv_path):
        csv_path = csv_filename
    
    if not os.path.exists(csv_path):
        print(f"❌ 品字交易法选股结果文件不存在: {csv_path}")
        return None
    
    stocks = []
    # 尝试多种编码
    encodings = ['utf-16-le', 'utf-8-sig', 'gbk', 'gb2312', 'gb18030']
    content = None
    
    for encoding in encodings:
        try:
            with open(csv_path, 'r', encoding=encoding) as f:
                content = f.read()
                break
        except:
            continue
    
    if content is None:
        print(f"❌ 无法读取文件，尝试了多种编码")
        return None
    
    # 使用csv读取（制表符分隔）
    import io
    f = io.StringIO(content)
    reader = csv.DictReader(f, delimiter='\t')
    for row in reader:
        ts_code = row.get('代码', '').strip("'")
        stock_name = row.get('名称', '').strip('"')
        # 补充市场后缀
        if ts_code and len(ts_code) == 6:
            if ts_code.startswith('6'):
                ts_code = ts_code + '.SH'
            else:
                ts_code = ts_code + '.SZ'
        stocks.append({
            'ts_code': ts_code,
            'stock_name': stock_name
        })
    
    print(f"✅ 读取到 {len(stocks)} 只品字交易法选股结果")
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
        
        if not ts_code:
            continue
        
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
        
        # 添加原股票信息
        report_data.append({
            'ts_code': ts_code,
            'stock_name': stock_name,
            'industry': industry,
            'total_mv': 0,
            'is_original': True
        })
        
        # 添加同行业股票信息
        for s in top5_stocks:
            report_data.append({
                'ts_code': s['ts_code'],
                'stock_name': s['stock_name'],
                'industry': s['industry'],
                'total_mv': float(s['total_mv']) / 100000000,
                'is_original': False
            })
    
    return report_data


def create_output_folder():
    """创建输出文件夹"""
    target_date = get_target_date()
    folder_name = f"品字交易法同行业{target_date}"
    
    if os.path.exists(folder_name):
        print(f"🗑️ 删除已存在的文件夹: {folder_name}")
        shutil.rmtree(folder_name)
    
    os.makedirs(folder_name, exist_ok=True)
    print(f"📁 创建文件夹: {folder_name}")
    
    return folder_name


def save_report(report_data, folder_path):
    """保存报告为CSV文件"""
    csv_path = os.path.join(folder_path, "select_pinzi_same_industry.csv")
    
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        f.write("股票代码,股票名称,行业,总市值(亿),是否原股票\n")
        for row in report_data:
            is_original = "是" if row['is_original'] else "否"
            total_mv_str = f"{row['total_mv']:.2f}" if row['total_mv'] > 0 else "-"
            f.write(f"{row['ts_code']},{row['stock_name']},{row['industry']},{total_mv_str},{is_original}\n")
    
    print(f"✅ 报告已保存: {csv_path}")
    return csv_path


def print_report(report_data):
    """打印报告"""
    print("\n" + "=" * 80)
    print("📊 品字交易法同行业股票报告")
    print("=" * 80)
    
    print(f"{'股票代码':<12} {'股票名称':<10} {'行业':<15} {'总市值(亿)':<12} {'是否原股票'}")
    print("-" * 80)
    
    for row in report_data:
        total_mv_str = f"{row['total_mv']:.2f}" if row['total_mv'] > 0 else "-"
        is_original = "是" if row['is_original'] else "否"
        print(f"{row['ts_code']:<12} {row['stock_name']:<10} {row['industry']:<15} {total_mv_str:<12} {is_original}")
    
    print(f"\n共 {len(report_data)} 条记录")


def main():
    print("=" * 80)
    print("📊 品字交易法同行业股票报告生成器")
    print("=" * 80)
    
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
    
    # 创建输出文件夹
    folder_path = create_output_folder()
    
    # 打印报告
    print_report(report_data)
    
    # 保存报告
    save_report(report_data, folder_path)
    
    print("\n" + "=" * 80)
    print("🎉 报告生成完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()