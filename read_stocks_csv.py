#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import os

# 指定要读取的CSV文件列表（常量）
CSV_FILES = [
    '股票列表csv/上证A股.csv',
    '股票列表csv/深证A股.csv'
]

def read_multiple_stock_files(csv_files: list = CSV_FILES, add_suffix: bool = True) -> list:
    """
    读取多个CSV文件中的股票代码列，合并并去重
    
    :param csv_files: CSV文件列表，默认为CSV_FILES常量
    :param add_suffix: 是否自动添加股票后缀，默认True
    :return: 合并并去重后的股票代码列表
    """
    all_codes = []
    
    for csv_file in csv_files:
        print(f"📊 正在读取 {csv_file}...")
        codes = read_stock_codes(csv_file, add_suffix=False)
        all_codes.extend(codes)
    
    # 去重
    unique_codes = list(dict.fromkeys(all_codes))
    
    # 添加股票后缀
    if add_suffix:
        unique_codes = [add_stock_suffix(code) for code in unique_codes]
    
    print(f"\n✅ 共读取 {len(all_codes)} 个股票代码，去重后 {len(unique_codes)} 个")
    return unique_codes

def add_stock_suffix(stock_code: str) -> str:
    """
    根据股票代码规则添加后缀

    股票代码规则：
    - 上海证券交易所（SSE）：600、601、603、605、688开头，添加.SH
    - 深圳证券交易所（SZSE）：000、001、002、003、300开头，添加.SZ
    - 北京证券交易所（BSE）：4、8开头，添加.BJ

    :param stock_code: 股票代码（可能没有后缀）
    :return: 带后缀的股票代码，如 '002801.SZ'
    """
    stock_code = str(stock_code).strip()

    if stock_code.endswith('.SH') or stock_code.endswith('.SZ') or stock_code.endswith('.BJ'):
        return stock_code

    pure_code = ''.join(filter(str.isdigit, stock_code))

    if not pure_code:
        print(f"⚠️ 无法从 '{stock_code}' 提取股票代码")
        return stock_code

    if pure_code.startswith(('600', '601', '603', '605', '688')):
        return pure_code + '.SH'
    elif pure_code.startswith(('000', '001', '002', '003', '300')):
        return pure_code + '.SZ'
    elif pure_code.startswith(('4', '8')):
        return pure_code + '.BJ'
    else:
        print(f"⚠️ 未知股票代码规则 '{stock_code}'，默认添加 .SZ 后缀")
        return pure_code + '.SZ'

def read_stock_codes(csv_file: str, add_suffix: bool = True) -> list:
    """
    读取CSV文件中的股票代码列

    :param csv_file: CSV文件路径，默认为同目录下的stocks.csv
    :param add_suffix: 是否自动添加股票后缀，默认True
    :return: 股票代码列表
    """
    # 检查文件是否存在
    if not os.path.exists(csv_file):
        print(f"❌ 文件 {csv_file} 不存在")
        return []
    
    try:
        # 读取CSV文件
        df = pd.read_csv(csv_file, encoding='utf-8-sig')
        
        # 检查是否包含"代码"列
        if '代码' not in df.columns:
            print(f"❌ CSV文件中没有找到'代码'列")
            print(f"   文件中的列名: {df.columns.tolist()}")
            return []
        
        # 提取"代码"列数据
        codes = df['代码'].dropna().tolist()

        # 去除空白字符
        codes = [str(code).strip() for code in codes if str(code).strip()]

        # 添加股票后缀
        if add_suffix:
            codes = [add_stock_suffix(code) for code in codes]

        print(f"✅ 成功读取 {len(codes)} 个股票代码")
        return codes
    
    except Exception as e:
        print(f"❌ 读取文件失败: {e}")
        return []

def main():
    """
    主函数：读取股票代码并打印
    """
    print("📊 正在读取指定的CSV文件...")
    print(f"📁 文件列表: {CSV_FILES}")
    
    # 读取多个CSV文件中的股票代码
    codes = read_multiple_stock_files()
    
    # 打印股票代码
    if codes:
        print("\n📋 股票代码列表:")
        print("-" * 30)
        for i, code in enumerate(codes, 1):
            print(f"{i:2d}. {code}")
        print("-" * 30)
        print(f"共 {len(codes)} 个股票代码")
    else:
        print("⚠️ 没有找到有效的股票代码")

if __name__ == "__main__":
    main()