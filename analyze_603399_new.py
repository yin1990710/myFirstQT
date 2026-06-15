import sys
sys.path.append('.')
from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
cursor = conn.cursor()

query = """
SELECT trade_date, close, high, low, amount 
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
            # 取前119天的数据（不含当天）
            previous_119 = last_120[1:]
            
            # 计算119天收盘价的最高价和最低价
            close_prices = [float(r['close']) for r in previous_119]
            max_close_119 = max(close_prices)
            min_close_119 = min(close_prices)
            
            t_close = float(target_data['close'])
            t_amount = float(target_data['amount'])
            
            # 计算涨幅
            prev_close = float(results[1]['close'])
            gain = (t_close - prev_close) / prev_close * 100
            
            # 计算波动幅度
            amplitude = (max_close_119 - min_close_119) / min_close_119 * 100
            
            print('603399.SH 分析结果 (使用最新逻辑):')
            print('=' * 60)
            print('目标日期:', target_data['trade_date'])
            print('当日收盘价:', t_close)
            print('前119天收盘价最高价:', max_close_119)
            print('前119天收盘价最低价:', min_close_119)
            print('前119天波动幅度:', amplitude)
            print('当日涨幅:', gain)
            print('当日成交额(元):', t_amount * 1000)
            print('=' * 60)
            
            # 条件判断
            condition1 = t_close > max_close_119  # 收盘价突破
            condition2 = amplitude <= 30  # 波动幅度<=30%
            condition3 = gain > 5  # 涨幅>5%
            condition4 = t_amount * 1000 > 500000000  # 成交额>5亿
            
            print('条件1: 收盘价突破前119天收盘价最高价:', '✅' if condition1 else '❌')
            print('条件2: 前119天波动幅度<=30%:', '✅' if condition2 else '❌')
            print('条件3: 涨幅>5%:', '✅' if condition3 else '❌')
            print('条件4: 成交额>5亿元:', '✅' if condition4 else '❌')
            
            if condition1 and condition2 and condition3 and condition4:
                print('=' * 60)
                print('🎉 满足选股条件！')
            else:
                print('=' * 60)
                print('❌ 不满足选股条件')
        else:
            print('数据不足，仅有', len(last_120), '天数据')
    else:
        print('未找到20260421的数据')
else:
    print('无数据')

close_connection(conn)
