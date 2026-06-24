#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
爬取深交所首页主板和创业板的数据
数据源：https://www.szse.cn/index/index.html
使用akshare库获取数据，输出为CSV文件
"""

import os
import shutil
import pandas as pd
from datetime import datetime, timedelta


def get_trade_date() -> str:
    """获取交易日期，如果当前时间在0-15点之间则取前一天日期"""
    now = datetime.now()
    hour = now.hour
    if 0 <= hour < 15:
        target_date = now - timedelta(days=1)
        return target_date.strftime('%Y%m%d')
    else:
        return now.strftime('%Y%m%d')


def get_szse_data():
    """使用akshare获取深交所市场总貌数据"""
    import akshare as ak

    trade_date = get_trade_date()
    df = ak.stock_szse_summary(date=trade_date)
    return df


def parse_board_data(df, board_name):
    """从总貌数据中提取指定板块的数据"""
    row = df[df['证券类别'] == board_name]
    if row.empty:
        return None

    row = row.iloc[0]
    return {
        '总市值（亿元）': round(row['总市值'] / 1e8, 2) if pd.notna(row['总市值']) else None,
        '流通市值（亿元）': round(row['流通市值'] / 1e8, 2) if pd.notna(row['流通市值']) else None,
        '上市公司数': int(row['数量']) if pd.notna(row['数量']) else None,
        '总成交金额（亿元）': round(row['成交金额'] / 1e8, 2) if pd.notna(row['成交金额']) else None,
    }


def main():
    print("=" * 60)
    print("📊 深交所首页数据爬虫")
    print("=" * 60)

    trade_date = get_trade_date()
    print(f"\n 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 交易日期: {trade_date}")

    print("\n 正在获取深交所市场总貌数据...")
    df = get_szse_data()
    print(f"✅ 获取到 {len(df)} 条记录")

    # 提取主板和创业板数据
    main_board = parse_board_data(df, '主板A股')
    gem_board = parse_board_data(df, '创业板A股')

    if main_board:
        print("\n 主板数据:")
        for k, v in main_board.items():
            print(f"   {k}: {v}")

    if gem_board:
        print("\n📊 创业板数据:")
        for k, v in gem_board.items():
            print(f"   {k}: {v}")

    # 保存为CSV
    folder_name = f"深交所首页数据{trade_date}"
    output_dir = os.path.join(os.getcwd(), folder_name)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir)

    # 保存主板数据
    if main_board:
        df_main = pd.DataFrame([main_board], index=['主板A股'])
        csv_path = os.path.join(output_dir, "主板.csv")
        df_main.to_csv(csv_path, encoding='utf-8-sig')
        print(f"\n💾 主板数据已保存: {csv_path}")

    # 保存创业板数据
    if gem_board:
        df_gem = pd.DataFrame([gem_board], index=['创业板A股'])
        csv_path = os.path.join(output_dir, "创业板.csv")
        df_gem.to_csv(csv_path, encoding='utf-8-sig')
        print(f"💾 创业板数据已保存: {csv_path}")

    # 保存完整数据
    full_csv_path = os.path.join(output_dir, "市场总貌.csv")
    df_save = df.copy()
    df_save['总市值（亿元）'] = df_save['总市值'].apply(lambda x: round(x / 1e8, 2) if pd.notna(x) else None)
    df_save['流通市值（亿元）'] = df_save['流通市值'].apply(lambda x: round(x / 1e8, 2) if pd.notna(x) else None)
    df_save['成交金额（亿元）'] = df_save['成交金额'].apply(lambda x: round(x / 1e8, 2) if pd.notna(x) else None)
    df_save.to_csv(full_csv_path, index=False, encoding='utf-8-sig')
    print(f"💾 完整市场总貌数据已保存: {full_csv_path}")

    print(f"\n🎉 爬取完成！数据保存在: {output_dir}")


if __name__ == "__main__":
    main()