#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
from read_stock_from_db import read_stock_data_from_db
from pinzi_analysis import analyze_pinzi_pattern
from mysql_connection import get_mysql_connection, close_connection

""" 用品字定向法分析股票的波谷、波峰标签写回数据表stock_daliy_t """

def write_analysis_result_to_db(data: pd.DataFrame) -> bool:
    """
    将品字定向法分析结果写回数据库
    
    :param data: 包含 turning_point 字段的股票数据
    :return: 写入是否成功
    """
    conn = get_mysql_connection()
    if not conn:
        return False
    
    try:
        cursor = conn.cursor()
        
        update_sql = """
            UPDATE stock_daily_t 
            SET turning_point = %s 
            WHERE ts_code = %s AND trade_date = %s
        """
        
        update_count = 0
        for _, row in data.iterrows():
            try:
                tp_value = str(row['turning_point'])[:10]  # 确保不超过varchar(10)
                cursor.execute(update_sql, (tp_value, row['ts_code'], row['trade_date']))
                update_count += cursor.rowcount
            except Exception as e:
                print(f"⚠️ 更新失败 {row['ts_code']} {row['trade_date']}: {e}")
        
        conn.commit()
        print(f"✅ 成功更新 {update_count} 条记录")
        return True
    
    except Exception as e:
        print(f"❌ 写入数据库失败: {e}")
        conn.rollback()
        return False
    
    finally:
        close_connection(conn)

def analyze_and_write_to_db() -> bool:
    """
    主流程：读取数据 -> 品字定向法分析 -> 写回数据库
    """
    print("📊 品字定向法分析并写入数据库")
    print("=" * 50)
    
    print("\n📁 步骤1: 读取股票交易数据...")
    df = read_stock_data_from_db()
    if df.empty:
        print("❌ 没有读取到数据")
        return False
    
    numeric_cols = ['open', 'high', 'low', 'close']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    stock_codes = df['ts_code'].unique().tolist()
    print(f"✅ 找到 {len(stock_codes)} 个股票代码")
    
    print("\n📁 步骤2: 执行品字定向法分析...")
    
    results = []
    for ts_code in stock_codes:
        print(f"   正在分析: {ts_code}")
        
        stock_data = df[df['ts_code'] == ts_code].copy()
        
        if len(stock_data) < 3:
            print(f"   ⚠️ 数据不足3天，跳过")
            stock_data['pattern'] = '波中'
            results.append(stock_data)
            continue
        
        stock_data = stock_data.sort_values('trade_date').reset_index(drop=True)
        
        # 执行分析
        analyzed_data = analyze_pinzi_pattern(stock_data)
        
        # 如果原来有 turning_point 列，先删除
        if 'turning_point' in analyzed_data.columns:
            analyzed_data = analyzed_data.drop('turning_point', axis=1)
        
        # 重命名 pattern 列为 turning_point
        analyzed_data = analyzed_data.rename(columns={'pattern': 'turning_point'})
        
        results.append(analyzed_data)
    
    final_df = pd.concat(results, ignore_index=True)
    print(f"\n✅ 分析完成，共处理 {len(final_df)} 条记录")
    
    # 打印调试信息
    print("\n调试信息 - turning_point列:")
    print(f"  列数: {len(final_df.columns)}")
    print(f"  列名: {final_df.columns.tolist()}")
    if 'turning_point' in final_df.columns:
        print(f"  turning_point唯一值: {final_df['turning_point'].dropna().unique()}")
    
    print("\n📁 步骤3: 将分析结果写回数据库...")
    success = write_analysis_result_to_db(final_df)
    
    if success:
        print("\n📈 分析结果统计:")
        print("-" * 40)
        # 确保 turning_point 是一维的
        tp_series = final_df['turning_point']
        if hasattr(tp_series, 'value_counts'):
            stats = tp_series.value_counts()
            total = len(final_df)
            for pattern, count in stats.items():
                ratio = count / total * 100
                print(f"   {pattern}: {count} 条 ({ratio:.1f}%)")
        else:
            print("   无法统计，turning_point列有问题")
    
    return success

def main():
    success = analyze_and_write_to_db()
    
    if success:
        print("\n🎉 品字定向法分析并写入数据库完成！")
    else:
        print("\n❌ 品字定向法分析并写入数据库失败！")

if __name__ == "__main__":
    main()
