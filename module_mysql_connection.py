#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pymysql
from pymysql import OperationalError
from typing import Optional, Dict

def get_mysql_connection(
    host: str = 'localhost',
    port: int = 3306,
    user: str = 'root',
    password: str = '12345678',
    database: str = 'stock_daily_db',
    charset: str = 'utf8mb4'
) -> Optional[pymysql.connections.Connection]:
    """
    获取MySQL数据库连接
    
    :param host: 数据库主机地址，默认 localhost
    :param port: 数据库端口，默认 3306
    :param user: 数据库用户名，默认 root
    :param password: 数据库密码，默认 root
    :param database: 数据库名称，默认 stock_daily_db
    :param charset: 字符集，默认 utf8mb4
    :return: MySQL连接对象，如果连接失败返回 None
    """
    try:
        print(f"🔌 正在连接数据库: {user}@{host}:{port}/{database}")
        
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset=charset,
            cursorclass=pymysql.cursors.DictCursor
        )
        
        print("✅ 数据库连接成功！")
        return connection
    
    except OperationalError as e:
        print(f"❌ 数据库连接失败: {e}")
        if e.args[0] == 1049:
            print(f"提示：数据库 '{database}' 不存在，请先创建")
        elif e.args[0] == 1045:
            print("提示：用户名或密码错误")
        elif e.args[0] == 2003:
            print("提示：无法连接到MySQL服务器，请检查服务是否启动")
        return None
    except Exception as e:
        print(f"❌ 连接出错: {e}")
        return None

def close_connection(connection: pymysql.connections.Connection) -> None:
    """
    关闭数据库连接
    
    :param connection: MySQL连接对象
    """
    if connection and connection.open:
        connection.close()
        print("🔌 数据库连接已关闭")

def test_connection() -> None:
    """
    测试数据库连接
    """
    conn = get_mysql_connection()
    
    if conn:
        try:
            # 创建游标
            with conn.cursor() as cursor:
                # 执行查询
                cursor.execute("SELECT VERSION()")
                result = cursor.fetchone()
                print(f"📊 MySQL版本: {result['VERSION()']}")
                
                # 查询数据库表
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                if tables:
                    print("\n📋 数据库中的表:")
                    for table in tables:
                        print(f"  - {list(table.values())[0]}")
                else:
                    print("\n📋 数据库中没有表")
        finally:
            print("建表")
            close_connection(conn)

if __name__ == "__main__":
    test_connection()
    