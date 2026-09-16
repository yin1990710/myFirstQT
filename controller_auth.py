#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
用户登录认证模块 (controller_auth.py)

职责：
  1. 建表 user_login_t（若不存在），并初始化默认账号 luckboy；
  2. 验证账号密码；
  3. 更新登录/登出时间与在线状态。

供 app.py 的 /login、/logout、/api/current_user 接口调用，app.py 仅做请求转发与响应。
"""

import hashlib

from mysql_connection import get_mysql_connection, close_connection

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS user_login_t (
      id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
      account VARCHAR(50) NOT NULL COMMENT '账号',
      password VARCHAR(64) NOT NULL COMMENT '密码(SHA256)',
      login_status TINYINT NOT NULL DEFAULT 0 COMMENT '登录状态: 0=离线 1=在线',
      login_time DATETIME DEFAULT NULL COMMENT '最近登录时间',
      logout_time DATETIME DEFAULT NULL COMMENT '最近登出时间',
      valid_from_date DATE DEFAULT NULL COMMENT '账号有效期开始日期',
      create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
      update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
      UNIQUE KEY uk_account (account)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户登录信息表'
"""

DEFAULT_ACCOUNT = 'luckboy'
DEFAULT_PASSWORD = 'luckboy123'


def hash_password(pwd):
    """SHA256 加密密码。"""
    return hashlib.sha256(pwd.encode('utf-8')).hexdigest()


def init_user_table():
    """建表并初始化默认账号 luckboy（若不存在）。"""
    conn = get_mysql_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            cursor.execute(
                "SELECT id FROM user_login_t WHERE account = %s",
                (DEFAULT_ACCOUNT,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO user_login_t (account, password, valid_from_date) "
                    "VALUES (%s, %s, CURDATE())",
                    (DEFAULT_ACCOUNT, hash_password(DEFAULT_PASSWORD)))
            conn.commit()
    except Exception:
        pass
    finally:
        close_connection(conn)


def verify_user(account, password):
    """验证账号密码。

    返回：
      {'ok': True, 'account': ...} 成功
      {'ok': False, 'error': '...'} 失败
    """
    if not account or not password:
        return {'ok': False, 'error': '账号和密码不能为空'}
    conn = get_mysql_connection()
    if not conn:
        return {'ok': False, 'error': '数据库连接失败'}
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT account, password FROM user_login_t WHERE account = %s",
                (account,))
            row = cursor.fetchone()
            if not row:
                return {'ok': False, 'error': '账号不存在'}
            if row['password'] != hash_password(password):
                return {'ok': False, 'error': '密码错误'}
            return {'ok': True, 'account': row['account']}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    finally:
        close_connection(conn)


def update_login_status(account, online):
    """更新用户登录状态和时间。

    参数：
      account: 账号
      online: True=登录上线, False=登出离线
    """
    conn = get_mysql_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
            if online:
                cursor.execute(
                    "UPDATE user_login_t SET login_status=1, login_time=NOW() "
                    "WHERE account = %s",
                    (account,))
            else:
                cursor.execute(
                    "UPDATE user_login_t SET login_status=0, logout_time=NOW() "
                    "WHERE account = %s",
                    (account,))
            conn.commit()
    except Exception:
        pass
    finally:
        close_connection(conn)
