#!/usr/bin/env python3
import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
if conn:
    try:
        cursor = conn.cursor()
        cursor.execute('SHOW COLUMNS FROM stock_daily_t')
        columns = cursor.fetchall()
        existing_cols = [col['Field'] for col in columns]
        
        if 'ma5' not in existing_cols:
            cursor.execute('ALTER TABLE stock_daily_t ADD COLUMN ma5 FLOAT DEFAULT NULL')
            print('成功添加ma5列')
        
        if 'ma30' not in existing_cols:
            cursor.execute('ALTER TABLE stock_daily_t ADD COLUMN ma30 FLOAT DEFAULT NULL')
            print('成功添加ma30列')
        
        conn.commit()
        
        cursor.execute('SHOW COLUMNS FROM stock_daily_t')
        columns = cursor.fetchall()
        print('\n更新后表结构:')
        for col in columns:
            print(f"  {col['Field']}: {col['Type']}")
    finally:
        close_connection(conn)