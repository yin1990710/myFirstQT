#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import os
import numpy as np
from mysql_connection import get_mysql_connection, close_connection

""" 读取文件夹《股票列表csv》下的基本股票基本信息保存到stock_info_t表里 """


def create_stock_info_table(conn) -> bool:
    """
    创建 stock_info_t 表
    
    :param conn: 数据库连接
    :return: 是否创建成功
    """
    try:
        cursor = conn.cursor()
        
        create_sql = """
            CREATE TABLE IF NOT EXISTS stock_info_t (
                ts_code VARCHAR(15) PRIMARY KEY,
                stock_name VARCHAR(50) NOT NULL,
                industry VARCHAR(50),
                area VARCHAR(30),
                total_share DECIMAL(20,6),
                float_share DECIMAL(20,6),
                total_mv DECIMAL(20,2),
                circ_mv DECIMAL(20,2),
                list_date VARCHAR(8),
                exchange VARCHAR(10),
                market VARCHAR(20),
                is_hs VARCHAR(5),
                update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        
        cursor.execute(create_sql)
        conn.commit()
        print("✅ stock_info_t 表已准备就绪")
        return True
    
    except Exception as e:
        print(f"❌ 创建表失败: {e}")
        return False

def read_csv_files(folder_path: str = "股票列表csv") -> pd.DataFrame:
    """
    读取指定文件夹下所有CSV文件
    
    :param folder_path: CSV文件所在文件夹路径
    :return: 合并后的股票信息DataFrame
    """
    if not os.path.exists(folder_path):
        print(f"❌ 文件夹 '{folder_path}' 不存在")
        return pd.DataFrame()
    
    csv_files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
    
    if not csv_files:
        print(f"❌ 文件夹 '{folder_path}' 中没有CSV文件")
        return pd.DataFrame()
    
    print(f"📁 找到 {len(csv_files)} 个CSV文件")
    
    dfs = []
    for csv_file in csv_files:
        file_path = os.path.join(folder_path, csv_file)
        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig')
            print(f"   ✅ 读取 {csv_file}: {len(df)} 条记录")
            dfs.append(df)
        except Exception as e:
            print(f"   ❌ 读取 {csv_file} 失败: {e}")
    
    if not dfs:
        print("❌ 没有成功读取任何CSV文件")
        return pd.DataFrame()
    
    combined_df = pd.concat(dfs, ignore_index=True)
    print(f"✅ 合并完成，共 {len(combined_df)} 条记录")
    
    return combined_df

def parse_stock_code(stock_code: str) -> str:
    """
    解析并格式化股票代码
    
    :param stock_code: 原始股票代码
    :return: 带后缀的股票代码
    """
    stock_code = str(stock_code).strip()
    
    if stock_code.endswith('.SH') or stock_code.endswith('.SZ'):
        return stock_code
    
    # 去除可能的引号
    stock_code = stock_code.strip("'\"")
    
    # 提取纯数字部分
    pure_code = ''.join(filter(str.isdigit, stock_code))
    
    if pure_code.startswith(('600', '601', '603', '605', '688')):
        return pure_code + '.SH'
    elif pure_code.startswith(('000', '001', '002', '003', '300')):
        return pure_code + '.SZ'
    elif pure_code.startswith(('8', '4')):
        return pure_code + '.BJ'
    else:
        return pure_code + '.SZ'

def parse_numeric_value(value):
    """
    解析包含中文单位的数值
    
    :param value: 原始值
    :return: 转换后的数值或None
    """
    if pd.isna(value) or value is None:
        return None
    
    if isinstance(value, (int, float)):
        if np.isnan(value) or np.isinf(value):
            return None
        return float(value)
    
    value = str(value).strip()
    
    if not value or value.lower() in ['nan', 'none', 'null']:
        return None
    
    # 处理百分比
    if value.endswith('%'):
        try:
            num_str = ''.join(filter(lambda x: x.isdigit() or x == '.', value))
            return float(num_str) / 100
        except:
            return None
    
    # 处理"亿"单位
    if '亿' in value:
        try:
            num_str = ''.join(filter(lambda x: x.isdigit() or x == '.', value))
            return float(num_str) * 100000000
        except:
            return None
    
    # 处理"万"单位
    if '万' in value:
        try:
            num_str = ''.join(filter(lambda x: x.isdigit() or x == '.', value))
            return float(num_str) * 10000
        except:
            return None
    
    # 处理普通数字
    try:
        return float(value)
    except:
        return None

def process_stock_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    处理股票数据，提取需要的字段
    
    :param df: 原始股票数据
    :return: 处理后的DataFrame
    """
    if df.empty:
        return df
    
    field_mapping = {
        '代码': 'ts_code',
        '股票代码': 'ts_code',
        '证券代码': 'ts_code',
        'code': 'ts_code',
        'ts_code': 'ts_code',
        
        '名称': 'stock_name',
        '股票名称': 'stock_name',
        '证券简称': 'stock_name',
        'name': 'stock_name',
        
        '行业': 'industry',
        '所属行业': 'industry',
        'industry': 'industry',
        
        '地区': 'area',
        '所属地区': 'area',
        'area': 'area',
        
        '总股本': 'total_share',
        '总股本(万股)': 'total_share',
        'total_share': 'total_share',
        
        '流通股本': 'float_share',
        '流通股(万股)': 'float_share',
        'float_share': 'float_share',
        
        '总市值': 'total_mv',
        '总市值(亿元)': 'total_mv',
        'total_mv': 'total_mv',
        
        '流通市值': 'circ_mv',
        '流通市值(亿元)': 'circ_mv',
        'circ_mv': 'circ_mv',
        
        '上市日期': 'list_date',
        'list_date': 'list_date'
    }
    
    # 只保留需要的列
    available_cols = [k for k in field_mapping.keys() if k in df.columns]
    df = df[available_cols]
    
    # 重命名列
    df = df.rename(columns=field_mapping)
    
    # 确保必要字段存在
    if 'ts_code' not in df.columns or 'stock_name' not in df.columns:
        print("❌ 缺少必要字段: ts_code 或 stock_name")
        return pd.DataFrame()
    
    # 格式化股票代码
    df['ts_code'] = df['ts_code'].apply(parse_stock_code)
    
    # 过滤无效代码
    df = df[df['ts_code'].str.len() >= 6]
    
    # 转换数值字段
    numeric_fields = ['total_share', 'float_share', 'total_mv', 'circ_mv']
    for field in numeric_fields:
        if field in df.columns:
            df[field] = df[field].apply(parse_numeric_value)
    
    # 去重（按股票代码）
    df = df.drop_duplicates(subset='ts_code', keep='first')
    print(f"✅ 去重后剩余 {len(df)} 条记录")
    
    return df

def write_to_db(conn, df: pd.DataFrame) -> bool:
    """
    将股票信息写入数据库
    
    :param conn: 数据库连接
    :param df: 股票信息DataFrame
    :return: 是否写入成功
    """
    if df.empty:
        print("⚠️ 没有数据需要写入")
        return True
    
    try:
        cursor = conn.cursor()
        
        insert_sql = """
            INSERT INTO stock_info_t (
                ts_code, stock_name, industry, area, total_share, float_share,
                total_mv, circ_mv, list_date
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                stock_name = VALUES(stock_name),
                industry = VALUES(industry),
                area = VALUES(area),
                total_share = VALUES(total_share),
                float_share = VALUES(float_share),
                total_mv = VALUES(total_mv),
                circ_mv = VALUES(circ_mv),
                list_date = VALUES(list_date),
                update_time = CURRENT_TIMESTAMP
        """
        
        insert_count = 0
        for _, row in df.iterrows():
            # 将NaN转换为None
            params = []
            for field in ['ts_code', 'stock_name', 'industry', 'area', 
                         'total_share', 'float_share', 'total_mv', 'circ_mv', 'list_date']:
                value = row.get(field)
                if pd.isna(value) or (isinstance(value, float) and np.isnan(value)):
                    params.append(None)
                else:
                    params.append(value)
            
            try:
                cursor.execute(insert_sql, params)
                insert_count += 1
            except Exception as e:
                print(f"⚠️ 写入 {row.get('ts_code', '未知')} 失败: {e}")
        
        conn.commit()
        print(f"✅ 成功写入 {insert_count} 条记录")
        return True
    
    except Exception as e:
        print(f"❌ 写入失败: {e}")
        conn.rollback()
        return False

def main():
    """
    主函数：读取股票信息CSV文件并写入数据库
    """
    print("📊 股票信息导入工具")
    print("=" * 50)
    
    print("\n📁 步骤1: 连接数据库...")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库")
        return
    
    try:
        print("\n📁 步骤2: 创建 stock_info_t 表...")
        if not create_stock_info_table(conn):
            return
        
        print("\n📁 步骤3: 读取股票列表CSV文件夹...")
        df = read_csv_files("股票列表csv")
        if df.empty:
            print("❌ 没有读取到数据")
            return
        
        print("\n📁 步骤4: 处理股票数据...")
        processed_df = process_stock_data(df)
        if processed_df.empty:
            print("❌ 数据处理失败")
            return
        
        print("\n📁 步骤5: 写入数据库...")
        if not write_to_db(conn, processed_df):
            return
        
        print("\n📈 导入结果统计:")
        print("-" * 40)
        print(f"   导入股票数量: {len(processed_df)}")
        
        if 'industry' in processed_df.columns:
            industry_counts = processed_df['industry'].value_counts().head(5)
            print("\n   行业分布（前5位）:")
            for industry, count in industry_counts.items():
                print(f"     {industry}: {count} 只")
        
        print("\n🎉 股票信息导入完成！")
    
    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()
