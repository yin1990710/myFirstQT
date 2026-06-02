#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import pandas as pd
from datetime import datetime

from mysql_connection import get_mysql_connection, close_connection

CSV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '股票列表csv')
BUY_STOCK_FILE = os.path.join(CSV_DIR, '已买股.csv')

def add_is_buy_column(conn):
    """
    检查并添加is_buy列到stock_daily_t表（字符串类型）

    :param conn: 数据库连接
    :return: 是否成功
    """
    try:
        cursor = conn.cursor()

        cursor.execute("DESCRIBE stock_daily_t")
        columns = [row['Field'] for row in cursor.fetchall()]

        if 'is_buy' not in columns:
            cursor.execute("ALTER TABLE stock_daily_t ADD COLUMN is_buy VARCHAR(20) DEFAULT '未买' COMMENT '是否已买 未买/已买入'")
            conn.commit()
            print("   ✅ 已添加 is_buy 列到 stock_daily_t 表")
            return True
        else:
            print("   ℹ️  is_buy 列已存在")
            return True

    except Exception as e:
        print(f"   ❌ 添加is_buy列失败: {e}")
        return False

def normalize_stock_code(code_str: str) -> str:
    """
    将股票代码标准化为带后缀的格式

    :param code_str: 原始股票代码
    :return: 标准化后的股票代码，如 000725.SZ
    """
    code_str = str(code_str).strip()

    code_str = code_str.replace("'", "").replace('"', '').strip()

    if code_str.startswith('000') or code_str.startswith('001') or \
       code_str.startswith('002') or code_str.startswith('003'):
        return f"{code_str}.SZ"
    elif code_str.startswith('600') or code_str.startswith('601') or \
         code_str.startswith('603') or code_str.startswith('605'):
        return f"{code_str}.SH"
    elif code_str.startswith('300') or code_str.startswith('301'):
        return f"{code_str}.SZ"
    elif code_str.startswith('688'):
        return f"{code_str}.SH"
    elif code_str.startswith('430') or code_str.startswith('830'):
        return f"{code_str}.BJ"
    else:
        return f"{code_str}.SZ"

def read_bought_stocks() -> set:
    """
    从CSV文件读取已买入股票代码

    :return: 股票代码集合
    """
    if not os.path.exists(BUY_STOCK_FILE):
        print(f"❌ 文件不存在: {BUY_STOCK_FILE}")
        return set()

    try:
        df = pd.read_csv(BUY_STOCK_FILE, dtype=str, keep_default_na=False)

        if '代码' not in df.columns:
            print(f"❌ CSV文件中没有'代码'列")
            return set()

        stock_codes = set()
        for code in df['代码']:
            code = str(code).strip()
            if code and code != '':
                normalized_code = normalize_stock_code(code)
                stock_codes.add(normalized_code)

        return stock_codes

    except Exception as e:
        print(f"❌ 读取CSV文件失败: {e}")
        return set()

def reset_all_is_buy(conn):
    """
    先将所有记录的is_buy重置为'未买'

    :param conn: 数据库连接
    """
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE stock_daily_t SET is_buy = '未买'")
        conn.commit()
        print(f"   🔄 已重置所有记录的is_buy为'未买'")
    except Exception as e:
        print(f"   ❌ 重置is_buy失败: {e}")

def update_bought_status(conn, bought_stocks: set) -> int:
    """
    更新stock_daily_t表中已买入股票的状态

    :param conn: 数据库连接
    :param bought_stocks: 已买入股票代码集合
    :return: 更新的记录数
    """
    if not bought_stocks:
        print("   ℹ️ 没有需要标记的已买入股票")
        return 0

    try:
        cursor = conn.cursor()

        placeholders = ','.join(['%s'] * len(bought_stocks))
        update_sql = f"""
            UPDATE stock_daily_t
            SET is_buy = '已买入', updated_at = CURRENT_TIMESTAMP
            WHERE ts_code IN ({placeholders})
        """

        cursor.execute(update_sql, tuple(bought_stocks))
        conn.commit()

        return cursor.rowcount

    except Exception as e:
        print(f"   ❌ 更新已买入状态失败: {e}")
        conn.rollback()
        return 0

def main():
    """
    主函数：标记已买入股票
    """
    print("=" * 60)
    print("📊 股票已买入标记工具")
    print("=" * 60)

    print("\n🔌 步骤1: 连接数据库")
    conn = get_mysql_connection()
    if not conn:
        print("❌ 无法连接数据库，程序退出")
        return

    try:
        print("\n📋 步骤2: 检查并添加is_buy列")
        if not add_is_buy_column(conn):
            print("❌ 添加is_buy列失败，程序退出")
            return

        print("\n📖 步骤3: 读取已买入股票列表")
        bought_stocks = read_bought_stocks()
        if not bought_stocks:
            print("❌ 没有找到已买入股票，程序退出")
            return

        print(f"   ✅ 找到 {len(bought_stocks)} 只已买入股票:")
        for code in sorted(bought_stocks):
            print(f"      - {code}")

        print("\n🔄 步骤4: 重置所有is_buy为'未买'")
        reset_all_is_buy(conn)

        print("\n🏷️ 步骤5: 标记已买入股票")
        updated_count = update_bought_status(conn, bought_stocks)
        print(f"   ✅ 成功标记 {updated_count} 条记录为'已买入'")

        print("\n" + "=" * 60)
        print("🎉 标记完成！")
        print("=" * 60)

    finally:
        close_connection(conn)

if __name__ == "__main__":
    main()
