import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
cursor = conn.cursor()

query = """
SELECT trade_date, close, high, low 
FROM stock_daily_t 
WHERE ts_code = '603399.SH' 
  AND trade_date <= '20260421' 
ORDER BY trade_date DESC 
LIMIT 200
"""
cursor.execute(query)
results = cursor.fetchall()

if results:
    target_data = None
    for r in results:
        if r['trade_date'] == '20260421':
            target_data = r
            break
    
    if target_data:
        last_120 = results[:120]
        if len(last_120) >= 120:
            # 找出最高价及其出现日期
            max_high = 0
            max_date = None
            for r in last_120:
                h = float(r['high'])
                if h > max_high:
                    max_high = h
                    max_date = r['trade_date']
            
            t_close = float(target_data['close'])
            t_high = float(target_data['high'])
            
            print('603399.SH 分析结果:')
            print('=' * 50)
            print('目标日期:', target_data['trade_date'])
            print('当日收盘价:', t_close)
            print('当日最高价:', t_high)
            print('=' * 50)
            print('120日最高价:', max_high)
            print('最高价出现日期:', max_date)
            print('=' * 50)
            
            if t_close >= max_high * 0.98:
                print('✅ 满足120日新高条件')
            else:
                print('❌ 不满足条件')
                print('差距:', max_high * 0.98 - t_close)
        else:
            print('数据不足，仅有', len(last_120), '天数据')
    else:
        print('未找到20260421的数据')
else:
    print('无数据')

close_connection(conn)
