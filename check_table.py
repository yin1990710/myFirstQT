#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
cursor = conn.cursor()
try:
    cursor.execute("SHOW CREATE TABLE stock_daily_t")
    row = cursor.fetchone()
    print(row['Create Table'] if isinstance(row, dict) else row[1])
except Exception as e:
    print(f"错误: {e}")
close_connection(conn)
