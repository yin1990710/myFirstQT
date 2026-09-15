#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
V形反转选股 (select_v_reverse.py)

对齐 prompt#L408-411：
  1. 读取 stock_daily_t 最近 120 个交易日 + stock_daily_basic_info_t 最近 1 个交易日（ts_code 关联）
  2. 选股条件：
     0. 最近一个交易日总市值 total_mv（万元）> 100亿
     1. 过去 120 个交易日内 最低收盘价 close / 最高收盘价 close < 50%
     2. 最低收盘价出现在最近 30 个交易日内
     3. 最近 30 个交易日 turning_point 出现至少 10 个「上升」
     4. 最近一个交易日 ma5 > ma30
  3. 仅 A 股（.SH / .SZ），最新交易日有数据（剔除停牌）
  4. STOCK_FILTERS 灵活过滤（当前：最新日市值>100亿，可追加）
  5. CSV「V形反转.csv」（csv.writer + utf-8-sig，含极值信息）
  6. 文件夹「V形反转+当日日期后缀」（已存在则复用）；结果为0不生成文件/文件夹
"""

import os
import csv
from datetime import datetime, timedelta

import tushare as ts

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection
from module_insert_strategy_selected_record import record_selected_stocks

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- 策略参数 ----------
LOOKBACK_DAYS = 120        # 回看交易日数（用于最低/最高收盘价）
RECENT_DAYS = 30           # 「最近N个交易日」窗口：最低点必须落在该窗口内、上升数统计窗口
AMP_RATIO_MAX = 0.50       # 最低收盘价 / 最高收盘价 < 此值
MIN_RISE_DAYS = 10         # 最近30日 turning_point="上升" 的最少天数
MIN_MARKET_CAP_WAN = 1_000_000  # 总市值下限（万元）= 100亿
TOP_N = 50

# ---------- 灵活过滤条件（STOCK_FILTERS 注册表，参考 find_similar_ma5.py） ----------

def _filter_min_market_cap(records):
    """最近一个交易日总市值 total_mv（万元）> MIN_MARKET_CAP_WAN。"""
    mv = records[-1].get('total_mv')
    return mv is not None and mv > MIN_MARKET_CAP_WAN


STOCK_FILTERS = [
    (f'市值<={MIN_MARKET_CAP_WAN/10000:.0f}亿', _filter_min_market_cap),
]


def apply_filters(records):
    """依次执行 STOCK_FILTERS 全部条件，返回 (是否通过, 未通过条件名列表)。"""
    reasons = [name for name, fn in STOCK_FILTERS if not fn(records)]
    return (len(reasons) == 0), reasons


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 15)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    return opens[-n:] if len(opens) > n else opens


def get_folder_name():
    return f"V形反转{get_target_date()}"


def get_folder_path():
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


# ---------- 数据读取 ----------

def read_stock_data(start_date, end_date):
    """读取 stock_daily_t 全市场 [start, end]，LEFT JOIN basic 取最新日 total_mv。

    basic 用最近1个交易日关联即可（prompt：stock_daily_basic_info_t 最近1个交易日）。
    """
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT d.ts_code, d.trade_date, d.close, d.ma5, d.ma30,
               d.turning_point, b.total_mv
        FROM stock_daily_t d
        LEFT JOIN stock_daily_basic_info_t b
               ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        WHERE d.trade_date >= %s AND d.trade_date <= %s
        ORDER BY d.ts_code, d.trade_date
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(query_sql, (start_date, end_date))
            results = cursor.fetchall()
        print(f"✅ 成功读取 {len(results)} 条数据 ({start_date} ~ {end_date})")
        return results
    except Exception as e:
        print(f"❌ 查询数据失败: {e}")
        return []
    finally:
        close_connection(conn)


# ---------- V形反转识别 ----------

def detect_v_reverse(recs):
    """对齐 prompt 条件1~4，返回 (info, 失败原因)。"""
    n = len(recs)
    if n < LOOKBACK_DAYS:
        return None, f'历史不足{LOOKBACK_DAYS}日'

    closes = [r['close'] for r in recs]
    if any(c is None for c in closes):
        return None, '收盘价缺失'

    # 条件1：过去120日 最低收盘/最高收盘 < 50%
    lo, hi = min(closes), max(closes)
    if lo is None or hi is None or hi <= 0:
        return None, '极值异常'
    amp_ratio = lo / hi
    if amp_ratio >= AMP_RATIO_MAX:
        return None, f'振幅比{amp_ratio*100:.1f}%≥50%'

    # 条件2：最低收盘价出现在最近30个交易日内
    min_idx = closes.index(lo)
    recent_start = n - RECENT_DAYS
    if min_idx < recent_start:
        return None, f'最低收盘不在最近{RECENT_DAYS}日内'

    # 条件3：最近30个交易日 turning_point 出现至少10个「上升」
    recent = recs[-RECENT_DAYS:]
    rise_cnt = sum(1 for r in recent if r['turning_point'] == '上升')
    if rise_cnt < MIN_RISE_DAYS:
        return None, f'近{RECENT_DAYS}日上升仅{rise_cnt}天<{MIN_RISE_DAYS}'

    # 条件4：最近一个交易日 ma5 > ma30
    latest = recs[-1]
    ma5, ma30 = latest.get('ma5'), latest.get('ma30')
    if ma5 is None or ma30 is None or ma30 <= 0:
        return None, 'ma5/ma30缺失'
    if ma5 <= ma30:
        return None, 'ma5未大于ma30'

    info = {
        'min_idx': min_idx,
        'min_date': recs[min_idx]['trade_date'],
        'min_close': round(lo, 2),
        'max_close': round(hi, 2),
        'amp_ratio': round(amp_ratio, 3),
        'rise_cnt': rise_cnt,
        'ma5': round(ma5, 2),
        'ma30': round(ma30, 2),
    }
    return info, None


# ---------- 主流程 ----------

def main():
    target_date = get_target_date()
    print("=" * 80)
    print(f"📐 V形反转选股 — 目标日期 {target_date}")
    print(f"   回看{LOOKBACK_DAYS}日 | 最低/最高<{AMP_RATIO_MAX*100:.0f}% | "
          f"最低收盘在最近{RECENT_DAYS}日内 | 近{RECENT_DAYS}日上升≥{MIN_RISE_DAYS}天 | ma5>ma30")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    stock_data = {}
    for r in data:
        stock_data.setdefault(r['ts_code'], []).append({
            'trade_date': r['trade_date'],
            'close': float(r['close']) if r['close'] is not None else None,
            'ma5': float(r['ma5']) if r['ma5'] is not None else None,
            'ma30': float(r['ma30']) if r['ma30'] is not None else None,
            'turning_point': r['turning_point'],
            'total_mv': float(r['total_mv']) if r['total_mv'] is not None else None,
        })

    latest_date = trade_dates[-1]
    cnt_no_latest = cnt_not_a = 0
    cnt_filter = {name: 0 for name, _ in STOCK_FILTERS}
    cnt_fail = {}
    result = []

    for code, recs in stock_data.items():
        if recs[-1]['trade_date'] != latest_date:
            cnt_no_latest += 1
            continue
        if not (code.endswith('.SH') or code.endswith('.SZ')):
            cnt_not_a += 1
            continue
        ok, reasons = apply_filters(recs)
        if not ok:
            for name in reasons:
                cnt_filter[name] += 1
            continue

        info, reason = detect_v_reverse(recs)
        if info is None:
            cnt_fail[reason] = cnt_fail.get(reason, 0) + 1
            continue

        info['ts_code'] = code
        info['total_mv'] = recs[-1]['total_mv']
        info['close'] = recs[-1]['close']
        result.append(info)

    result.sort(key=lambda x: x['amp_ratio'])  # 振幅比越小（跌幅越深）排越前

    # ---------- 漏斗 ----------
    print("\n" + "=" * 72)
    print(f"  股票总数: {len(stock_data)}")
    print(f"  - 最新日停牌无数据: {cnt_no_latest}")
    print(f"  - 非A股: {cnt_not_a}")
    for name, cnt in cnt_filter.items():
        print(f"  - 过滤[{name}]: {cnt}")
    for name, cnt in sorted(cnt_fail.items(), key=lambda x: -x[1])[:10]:
        print(f"  - {name}: {cnt}")
    print("-" * 72)
    print(f"  入选: {len(result)}（按振幅比升序）")
    print("=" * 72)

    if not result:
        print("⚠️ 无符合V形反转的股票，不生成文件夹/CSV")
        return

    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, 'V形反转.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码', '最低收盘日', '最低收盘', '最高收盘', '最低/最高%',
                         f'近{RECENT_DAYS}日上升天数', 'ma5', 'ma30',
                         '总市值(万)', '最新收盘'])
        for s in result[:TOP_N]:
            writer.writerow([
                s['ts_code'], s['min_date'], s['min_close'], s['max_close'],
                f"{s['amp_ratio']*100:.1f}", s['rise_cnt'], s['ma5'], s['ma30'],
                f"{s['total_mv']:.0f}", s['close'],
            ])
    print(f"✅ CSV已生成（{min(TOP_N, len(result))}只）: {csv_path}")

    # 选股结果入库（便于回测，与CSV内容一致取Top N）
    record_selected_stocks(
        'V形反转选股',
        [{'ts_code': s['ts_code'], 'selected': 1} for s in result[:TOP_N]],
        latest_date)

    print(f"\n🔥 V形反转前{min(TOP_N, len(result))}（按振幅比升序）：")
    for i, s in enumerate(result[:TOP_N], 1):
        print(f"{i:>2}. {s['ts_code']:<11} 最低={s['min_close']:.2f}({s['min_date']}) "
              f"最高={s['max_close']:.2f} 最低/最高={s['amp_ratio']*100:.1f}% "
              f"近{RECENT_DAYS}日上升={s['rise_cnt']}天 ma5={s['ma5']:.2f}>ma30={s['ma30']:.2f} "
              f"市值={s['total_mv']/10000:.0f}亿")


if __name__ == '__main__':
    main()
