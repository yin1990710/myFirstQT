import sys
sys.path.insert(0, '.')
from mysql_connection import get_mysql_connection, close_connection

conn = get_mysql_connection()
if not conn:
    print('数据库连接失败')
    exit()

cursor = conn.cursor()

sql = '''
    SELECT
        d.trade_date, d.open, d.close, d.amount, i.total_mv
    FROM stock_daily_t d
    LEFT JOIN stock_info_t i ON d.ts_code = i.ts_code COLLATE utf8mb4_unicode_ci
    WHERE d.ts_code = '603303.SH' COLLATE utf8mb4_unicode_ci AND d.trade_date >= '20260401' AND d.trade_date <= '20260531'
    ORDER BY d.trade_date
'''

cursor.execute(sql)
rows = cursor.fetchall()

print('得邦照明 (603303.SH) 近期数据')
print('='*70)

data = []
for row in rows:
    d = {
        'trade_date': row['trade_date'],
        'open': float(row['open']) if row['open'] else 0,
        'close': float(row['close']) if row['close'] else 0,
        'amount': float(row['amount']) if row['amount'] else 0,
        'total_mv': float(str(row['total_mv']).replace(',', '')) if row['total_mv'] else 0
    }
    data.append(d)

for d in data:
    is_up = 'Y' if d['close'] > d['open'] else 'N'
    print(f"{d['trade_date']} 开盘:{d['open']:.2f} 收盘:{d['close']:.2f} 成交额:{d['amount']:.2f}千 阳线:{is_up}")

print()
print('选股条件分析 (最近10个交易日):')
print('-'*70)

recent_10 = data[-10:]
up_days = [d for d in recent_10 if d['close'] > d['open']]
down_days = [d for d in recent_10 if d['close'] <= d['open']]

print(f'条件1: 阳线数量 >= 4')
print(f'  实际阳线数: {len(up_days)}')
print(f'  结果: {"通过" if len(up_days) >= 4 else "未通过"}')

if len(up_days) > 0 and len(down_days) > 0:
    avg_up = sum(d['amount'] for d in up_days) / len(up_days)
    avg_down = sum(d['amount'] for d in down_days) / len(down_days)
    ratio = avg_up / avg_down if avg_down > 0 else 0
    print(f'条件2: 阳线平均成交额 >= 2倍阴线平均成交额')
    print(f'  阳线平均: {avg_up:.2f}千元, 阴线平均: {avg_down:.2f}千元, 倍数: {ratio:.2f}x')
    print(f'  结果: {"通过" if ratio >= 2 else "未通过"}')

    print(f'条件3: 阳线平均成交额*1000 > 5亿')
    print(f'  值: {avg_up*1000/1e8:.2f}亿')
    print(f'  结果: {"通过" if avg_up*1000 > 5e8 else "未通过"}')

total_mv = data[-1]['total_mv'] if data else 0
if total_mv > 0:
    print(f'条件4: 总市值 > 50亿')
    print(f'  值: {total_mv/1e9:.2f}亿')
    print(f'  结果: {"通过" if total_mv > 5e8 else "未通过"}')

cursor.close()
close_connection(conn)