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
    WHERE d.ts_code = '000636.SZ' COLLATE utf8mb4_unicode_ci AND d.trade_date >= '20260401' AND d.trade_date <= '20260531'
    ORDER BY d.trade_date
'''

cursor.execute(sql)
rows = cursor.fetchall()

print('风华高科 (000636.SZ) 近期数据')
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
print('='*70)
print('选股条件分析 (20260416-20260429区间):')
print('-'*70)

period_data = [d for d in data if '20260416' <= d['trade_date'] <= '20260429']
print(f'\n该区间共 {len(period_data)} 个交易日:')
for d in period_data:
    is_up = 'Y' if d['close'] > d['open'] else 'N'
    print(f"  {d['trade_date']} 开盘:{d['open']:.2f} 收盘:{d['close']:.2f} 成交额:{d['amount']:.2f}千 阳线:{is_up}")

up_days = [d for d in period_data if d['close'] > d['open']]
down_days = [d for d in period_data if d['close'] <= d['open']]

print()
print(f'条件1: 阳线数量 >= 4')
print(f'  实际阳线数: {len(up_days)}')
cond1 = len(up_days) >= 4
print(f'  结果: {"通过" if cond1 else "未通过"}')

cond2 = False
cond3 = False
if len(up_days) > 0 and len(down_days) > 0:
    avg_up = sum(d['amount'] for d in up_days) / len(up_days)
    avg_down = sum(d['amount'] for d in down_days) / len(down_days)
    ratio = avg_up / avg_down if avg_down > 0 else 0
    print(f'条件2: 阳线平均成交额 >= 1.2倍阴线平均成交额')
    print(f'  阳线平均: {avg_up:.2f}千元, 阴线平均: {avg_down:.2f}千元, 倍数: {ratio:.2f}x')
    cond2 = ratio >= 1.2
    print(f'  结果: {"通过" if cond2 else "未通过"}')

    print(f'条件3: 阳线平均成交额*1000 > 5亿')
    print(f'  值: {avg_up*1000/1e8:.2f}亿')
    cond3 = avg_up*1000 > 5e8
    print(f'  结果: {"通过" if cond3 else "未通过"}')

total_mv = period_data[-1]['total_mv'] if period_data else 0
cond4 = False
if total_mv > 0:
    print(f'条件4: 总市值 > 50亿')
    print(f'  值: {total_mv/1e9:.2f}亿')
    cond4 = total_mv > 5e8
    print(f'  结果: {"通过" if cond4 else "未通过"}')

print()
print('='*70)
if cond1 and cond2 and cond3 and cond4:
    print('✅ 结论: 风华高科 (000636.SZ) 符合选股条件')
else:
    print('❌ 结论: 风华高科 (000636.SZ) 不符合选股条件')
    failed = []
    if not cond1:
        failed.append('条件1(阳线数量>=4)')
    if not cond2:
        failed.append('条件2(阳线成交额>=1.2倍阴线)')
    if not cond3:
        failed.append('条件3(阳线平均成交额>5亿)')
    if not cond4:
        failed.append('条件4(市值>50亿)')
    print(f'   未通过条件: {", ".join(failed)}')

cursor.close()
close_connection(conn)