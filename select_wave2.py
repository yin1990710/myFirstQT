#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
波浪理论二浪选股 (select_wave2.py)

对齐豆包对话《波浪理论：筛选处于第2浪的量化选股策略》：
  1浪 = 底部启动的第一波显著上涨（L1 → H1）
  2浪 = 从 H1 开始的回撤调整（至 L2），缩量、不破 1 浪起点、酝酿第 3 浪主升

硬性条件：
  a. L2 > L1          （2浪低点不跌破浪1起点，跌破即结构失效剔除）
  b. 回撤幅度 0.382 <= (H1-L2)/(H1-L1) <= 0.618   （黄金分割区间）
  c. 2浪调整阶段平均成交量 < 浪1上涨阶段平均成交量（缩量回调）
  d. 长期均线向上、现价站上长期均线、浪1高点与2浪低点均守住长期均线
     （DB 仅 214 个交易日，不足以计算 MA250 年线，暂用 MA120 替代，
       数据积累足够后把 LONG_MA_DAYS 改回 250 即可）
  e. MA30 向上（中期均线保持多头）
  f. 时间约束：浪2周期 >= 0.5×浪1周期 且 <= 2.618×浪1周期
  g. 浪1为显著上升波段：涨幅 >= MIN_WAVE1_PCT，且持续 >= MIN_WAVE1_DAYS

辅助打分（100分制，用于排序，>= MIN_SCORE 才输出）：
  - 回撤接近黄金分割中心0.5（30分）
  - 缩量程度：浪2/浪1 平均量比越低越好（25分）
  - 浪2末端地量：最近5日最低量 < 0.6×浪1均量（10分）
  - 重新站上MA5（入场时机，10分）
  - MACD：DIF>0（不破0轴，10分）+ DIF>DEA（5分）
  - RSI(14) 回落至40附近（30~50最佳，不低于20，10分）

波段识别：order=10 的局部极值（收盘价），数值越大浪级越大（短线可降到6）。

其他约定：
  - 基础过滤：仅A股(.SH/.SZ)、最新交易日有数据（剔除停牌）、
    最新日总市值 total_mv >= 100亿（1,000,000万元）
  - 数据读取 stock_daily_t 最近 250 个交易日（DB 现有 214 个），
    LEFT JOIN stock_daily_basic_info_t 取最新日 total_mv
  - 输出：文件夹「二浪选股+当日日期后缀」（已存在则复用），
    CSV「二浪选股.csv」单列 股票代码（csv.writer + utf-8-sig）
  - 结果为0时不创建文件夹/CSV
  - 风控参考：止损=2浪低点L2下方3%；目标=3浪启动，第一目标位 H1+(H1-L1)

用法：
  python3 select_wave2.py
  python3 select_wave2.py --min-score 70
"""

import os
import csv
import argparse
from datetime import datetime, timedelta

import tushare as ts

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

# ---------- 策略参数 ----------
ORDER = 10                 # 局部极值识别窗口（浪级大小）
RETRACE_MIN = 0.382        # 黄金分割回撤下限
RETRACE_MAX = 0.618        # 黄金分割回撤上限（可放宽至0.764）
MIN_WAVE1_PCT = 20.0       # 浪1最小涨幅（%）
MIN_WAVE1_DAYS = 5         # 浪1最短交易日数
MIN_WAVE2_DAYS = 3         # 浪2最短交易日数（回调必须已展开）
TIME_RATIO_MIN = 0.5       # 浪2/浪1 时间比下限
TIME_RATIO_MAX = 2.618     # 浪2/浪1 时间比上限
LONG_MA_DAYS = 120         # 长期均线（DB仅214个交易日，MA250不可得，暂用MA120替代）
MID_MA_DAYS = 30           # 中期均线
SHORT_MA_DAYS = 5          # 短期均线
MIN_MARKET_CAP_WAN = 1_000_000  # 最新日总市值下限（万元）= 100亿
MIN_SCORE = 60             # 辅助打分输出阈值（100分制）
TOP_N = 30                 # 最多输出数量

LOOKBACK_DAYS = 250        # 读取交易日数（DB现有214个，请求留缓冲）


# ---------- 工具函数 ----------

def get_target_date():
    now = datetime.now()
    if 0 <= now.hour < 15:
        return (now - timedelta(days=1)).strftime('%Y%m%d')
    return now.strftime('%Y%m%d')


def get_last_n_trade_dates(target_date, n):
    """返回 target_date 前（含）最近 n 个交易日列表。"""
    end = target_date
    start = (datetime.strptime(target_date, '%Y%m%d')
             - timedelta(days=n * 2 + 15)).strftime('%Y%m%d')
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                       fields=['cal_date', 'is_open'])
    if df is None or df.empty:
        return [target_date]
    opens = sorted(df[df['is_open'] == 1]['cal_date'].tolist())
    if len(opens) > n:
        opens = opens[-n:]
    return opens


def get_folder_name():
    return f"二浪选股{get_target_date()}"


def get_folder_path():
    """文件夹「二浪选股+日期后缀」，已存在则复用。"""
    folder_name = get_folder_name()
    folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder_name)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"📁 创建文件夹: {folder_name}")
    else:
        print(f"📁 文件夹已存在: {folder_name}")
    return folder_path


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------- 数据读取 ----------

def read_stock_data(start_date, end_date):
    """读取 stock_daily_t 全市场 [start, end]，LEFT JOIN basic 取 total_mv。"""
    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return []
    query_sql = """
        SELECT d.ts_code, d.trade_date, d.close, d.vol, d.amount, b.total_mv
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


def add_mas(recs):
    """基于 close 就地计算 ma5 / ma30 / ma_long（窗口内有 None 或数据不足均为 None）。

    用前缀和实现：任意窗口若包含 None（close 缺失），该窗口均线记 None。
    """
    closes = [r['close'] for r in recs]
    n = len(closes)
    pref = [0.0] * (n + 1)
    cnt = [0] * (n + 1)
    for i, c in enumerate(closes):
        pref[i + 1] = pref[i] + (c if c is not None else 0.0)
        cnt[i + 1] = cnt[i] + (1 if c is not None else 0)

    for days, key in ((SHORT_MA_DAYS, 'ma5'), (MID_MA_DAYS, 'ma30'),
                      (LONG_MA_DAYS, 'ma_long')):
        for i, r in enumerate(recs):
            if i < days - 1:
                r[key] = None
                continue
            lo = i - days + 1
            if cnt[i + 1] - cnt[lo] != days:
                r[key] = None
            else:
                r[key] = (pref[i + 1] - pref[lo]) / days


# ---------- 波浪结构识别 ----------

def local_extremes(closes, order=ORDER):
    """局部极值下标列表（收盘价，严格极值）。返回 (highs, lows)。"""
    n = len(closes)
    highs, lows = [], []
    for i in range(order, n - order):
        win = closes[i - order: i + order + 1]
        c = closes[i]
        if c is None or any(v is None for v in win):
            continue
        if c > max(v for k, v in enumerate(win) if k != order) and c == max(win):
            highs.append(i)
        if c < min(v for k, v in enumerate(win) if k != order) and c == min(win):
            lows.append(i)
    return highs, lows


def evaluate_wave2(recs):
    """在单只股票上枚举 (L1, H1) 组合，返回满足全部硬条件、得分最高的二浪结构，否则 None。

    结构字段：l1_idx/h1_idx/l2_idx、retrace、wave1_days/wave2_days、
              vol_ratio（浪2/浪1均量比）、score
    失败时返回 (None, 失败原因) —— 取条件通过数最多的一组尝试的原因，便于漏斗归因。
    """
    n = len(recs)
    closes = [r['close'] for r in recs]
    highs, lows = local_extremes(closes)
    if not highs or not lows:
        return None, '无局部极值'

    best, best_pass_cnt, best_reason = None, -1, '无满足硬条件的(L1,H1)组合'
    # 枚举浪1：低点 L1 → 其后的高点 H1
    for i in lows:
        for j in highs:
            if j <= i or j - i < MIN_WAVE1_DAYS:
                continue
            c_i, c_j = closes[i], closes[j]
            if c_i is None or c_j is None or c_i <= 0:
                continue
            wave1_days = j - i
            wave1_gain = (c_j / c_i - 1) * 100
            if wave1_gain < MIN_WAVE1_PCT:
                continue

            # 浪2：H1 之后最低收盘
            if any(closes[t] is None for t in range(j + 1, n)):
                continue
            if n - 1 - j < MIN_WAVE2_DAYS:
                continue
            k2 = min(range(j + 1, n), key=lambda t: closes[t])
            c_k = closes[k2]
            wave2_days = (n - 1) - j

            # ---- 硬条件逐项校验，统计通过数用于失败归因 ----
            checks = []
            # a. L2 > L1
            checks.append(('2浪破浪1起点(L2<=L1)', c_k > c_i))
            # b. 黄金分割回撤
            retrace = (c_j - c_k) / (c_j - c_i) if c_j > c_i else -1
            checks.append((f'回撤超出{RETRACE_MIN}~{RETRACE_MAX}', RETRACE_MIN <= retrace <= RETRACE_MAX))
            # c. 缩量回调
            avg_vol1 = mean([recs[t]['vol'] for t in range(i, j + 1)])
            avg_vol2 = mean([recs[t]['vol'] for t in range(j + 1, n)])
            checks.append(('浪2未缩量(均量>=浪1)',
                           avg_vol1 and avg_vol2 and avg_vol2 < avg_vol1))
            # d. 长期均线（现价站上 / 浪1高点上方 / 2浪低点守住 / 均线向上）
            ma_l_now = recs[-1]['ma_long']
            ma_l_ref = recs[n - 6]['ma_long'] if n >= 6 and recs[n - 6]['ma_long'] is not None else None
            checks.append(('长期均线数据不足',
                           ma_l_now is not None and ma_l_ref is not None
                           and recs[j]['ma_long'] is not None and recs[k2]['ma_long'] is not None))
            ma_l_ok = (ma_l_now is not None and ma_l_ref is not None
                       and recs[j]['ma_long'] is not None and recs[k2]['ma_long'] is not None)
            checks.append(('现价在长期均线下方', ma_l_ok and closes[-1] > ma_l_now))
            checks.append(('浪1高点未站上长期均线', ma_l_ok and c_j > recs[j]['ma_long']))
            checks.append(('2浪低点跌破长期均线', ma_l_ok and c_k >= recs[k2]['ma_long']))
            checks.append(('长期均线未向上', ma_l_ok and ma_l_now > ma_l_ref))
            # e. MA30 向上
            ma30_now, ma30_ref = recs[-1]['ma30'], (recs[n - 6]['ma30'] if n >= 6 else None)
            checks.append(('MA30未向上',
                           ma30_now is not None and ma30_ref is not None and ma30_now > ma30_ref))
            # f. 时间比例
            ratio = wave2_days / wave1_days
            checks.append((f'浪2/浪1时间比{ratio:.2f}超出{TIME_RATIO_MIN}~{TIME_RATIO_MAX}',
                           TIME_RATIO_MIN <= ratio <= TIME_RATIO_MAX))

            pass_cnt = sum(1 for _, ok in checks if ok)
            failed = [name for name, ok in checks if not ok]
            if failed:
                if pass_cnt > best_pass_cnt:
                    best_pass_cnt, best_reason = pass_cnt, failed[0]
                continue

            # ---- 辅助打分（100分制） ----
            score = 0.0
            # 回撤接近0.5（30分）
            score += 30 * max(0.0, 1 - abs(retrace - 0.5) / 0.118)
            # 缩量程度（25分）：浪2/浪1量比越低越好
            vol_ratio = avg_vol2 / avg_vol1 if avg_vol1 else 1.0
            score += 25 * max(0.0, 1 - vol_ratio)
            # 末端地量（10分）：最近5日最低量 / 浪1均量
            last5_min_vol = min((recs[t]['vol'] for t in range(max(0, n - 5), n)
                                 if recs[t]['vol'] is not None), default=None)
            if last5_min_vol is not None and avg_vol1:
                score += 10 * max(0.0, 1 - last5_min_vol / avg_vol1 / 0.6)
            # 重新站上MA5（10分）
            if recs[-1]['ma5'] is not None and closes[-1] > recs[-1]['ma5']:
                score += 10
            # MACD（15分）：DIF>0 不破0轴 10分 + DIF>DEA 5分
            dif, dea = calc_macd(closes)
            if dif is not None:
                if dif > 0:
                    score += 10
                if dea is not None and dif > dea:
                    score += 5
            # RSI(14) 40附近（10分）
            rsi = calc_rsi(closes)
            if rsi is not None and rsi >= 20:
                score += 10 * max(0.0, 1 - abs(rsi - 40) / 15)

            if best is None or score > best['score']:
                best = {
                    'l1_idx': i, 'h1_idx': j, 'l2_idx': k2,
                    'l1_date': recs[i]['trade_date'], 'h1_date': recs[j]['trade_date'],
                    'l2_date': recs[k2]['trade_date'],
                    'l1_close': c_i, 'h1_close': c_j, 'l2_close': c_k,
                    'retrace': retrace, 'wave1_days': wave1_days,
                    'wave2_days': wave2_days, 'vol_ratio': vol_ratio,
                    'rsi': rsi, 'dif': dif, 'dea': dea,
                    'score': round(score, 1),
                }

    return (best, None) if best else (None, best_reason)


def calc_macd(closes, fast=12, slow=26, sig=9):
    """返回 (最新DIF, 最新DEA)；数据不足返回 (None, None)。"""
    if len(closes) < slow + sig or any(c is None for c in closes):
        return None, None
    a_f, a_s = 2 / (fast + 1), 2 / (slow + 1)
    ema_f = ema_s = closes[0]
    difs = []
    for c in closes:
        ema_f = ema_f * (1 - a_f) + c * a_f
        ema_s = ema_s * (1 - a_s) + c * a_s
        difs.append(ema_f - ema_s)
    a_sig = 2 / (sig + 1)
    dea = difs[0]
    for d in difs:
        dea = dea * (1 - a_sig) + d * a_sig
    return difs[-1], dea


def calc_rsi(closes, period=14):
    """简单RSI；数据不足返回 None。"""
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - period + 1, len(closes))]
    gains = sum(d for d in diffs if d > 0) / period
    losses = sum(-d for d in diffs if d < 0) / period
    if gains + losses == 0:
        return 50.0
    return 100 * gains / (gains + losses)


# ---------- 核心选股 ----------

def run_wave2_selection(data, trade_dates):
    """全市场二浪选股。返回 (结果列表, stock_data缓存)；结果按 score 降序。"""
    stock_data = {}
    for record in data:
        stock_data.setdefault(record['ts_code'], []).append({
            'trade_date': record['trade_date'],
            'close': float(record['close']) if record['close'] is not None else None,
            'vol': float(record['vol']) if record['vol'] is not None else None,
            'amount': float(record['amount']) if record['amount'] is not None else None,
            'total_mv': float(record['total_mv']) if record['total_mv'] is not None else 0.0,
        })
    for recs in stock_data.values():
        add_mas(recs)

    latest_date = trade_dates[-1]
    min_history = LONG_MA_DAYS + 20   # 至少覆盖长期均线 + 波浪结构空间

    cnt_no_latest = cnt_not_a = cnt_mv = cnt_short = 0
    cnt_wave_fail = {}
    result = []
    for code, recs in stock_data.items():
        if recs[-1]['trade_date'] != latest_date:
            cnt_no_latest += 1
            continue
        if not (code.endswith('.SH') or code.endswith('.SZ')):
            cnt_not_a += 1
            continue
        if recs[-1]['total_mv'] < MIN_MARKET_CAP_WAN:
            cnt_mv += 1
            continue
        if len(recs) < min_history:
            cnt_short += 1
            continue

        best, fail_reason = evaluate_wave2(recs)
        if best is None:
            cnt_wave_fail[fail_reason] = cnt_wave_fail.get(fail_reason, 0) + 1
            continue
        if best['score'] < MIN_SCORE:
            cnt_low_score = getattr(run_wave2_selection, '_low', 0)
            run_wave2_selection._low = cnt_low_score + 1
            continue

        best['ts_code'] = code
        best['total_mv'] = recs[-1]['total_mv']
        best['close'] = recs[-1]['close']
        result.append(best)

    result.sort(key=lambda x: x['score'], reverse=True)

    # ---------- 漏斗统计 ----------
    print("\n" + "=" * 64)
    print(f"二浪选股漏斗（order={ORDER}，回撤{RETRACE_MIN}~{RETRACE_MAX}，"
          f"长期均线MA{LONG_MA_DAYS}，时间比{TIME_RATIO_MIN}~{TIME_RATIO_MAX}）")
    print("-" * 44)
    print(f"  股票总数:                 {len(stock_data)}")
    print(f"  - 最新日停牌无数据:       {cnt_no_latest}")
    print(f"  - 非A股:                  {cnt_not_a}")
    print(f"  - 市值<100亿:             {cnt_mv}")
    print(f"  - 上市/数据不足{min_history}日: {cnt_short}")
    for name, cnt in sorted(cnt_wave_fail.items(), key=lambda x: -x[1])[:8]:
        print(f"  - {name}: {cnt}")
    print(f"  - 打分<{MIN_SCORE}分:      {getattr(run_wave2_selection, '_low', 0)}")
    print("-" * 44)
    print(f"  入选: {len(result)}（score>={MIN_SCORE}，按打分降序）")
    print("=" * 64)

    run_wave2_selection._low = 0
    return result, stock_data


def main():
    global MIN_SCORE
    parser = argparse.ArgumentParser(description='波浪理论二浪选股')
    parser.add_argument('--min-score', type=float, default=MIN_SCORE,
                        help=f'辅助打分阈值（100分制），默认{MIN_SCORE}')
    args = parser.parse_args()
    MIN_SCORE = args.min_score

    target_date = get_target_date()
    print("=" * 80)
    print(f"🌊 波浪理论二浪选股 — 目标日期 {target_date}")
    print("=" * 80)

    trade_dates = get_last_n_trade_dates(target_date, LOOKBACK_DAYS)
    data = read_stock_data(trade_dates[0], trade_dates[-1])
    if not data:
        print("❌ 未获取到数据，退出")
        return

    result, _ = run_wave2_selection(data, trade_dates)
    if not result:
        print(f"\n⚠️ 二浪选股结果为0，不生成文件夹/CSV")
        return

    folder_path = get_folder_path()
    csv_path = os.path.join(folder_path, '二浪选股.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['股票代码'])
        for s in result[:TOP_N]:
            writer.writerow([s['ts_code']])
    print(f"✅ CSV已生成（{min(TOP_N, len(result))}只）: {csv_path}")

    print(f"\n🔥 二浪结构打分前{min(TOP_N, len(result))}：")
    for i, s in enumerate(result[:TOP_N], 1):
        print(f"{i:>2}. {s['ts_code']:<11} 打分={s['score']:>5} "
              f"L1={s['l1_date']}({s['l1_close']:.2f}) H1={s['h1_date']}({s['h1_close']:.2f}) "
              f"L2={s['l2_date']}({s['l2_close']:.2f}) 回撤={s['retrace']:.3f} "
              f"量比={s['vol_ratio']:.2f} 量/时=1:{s['wave2_days'] / s['wave1_days']:.2f} "
              f"RSI={s['rsi']:.0f} 市值={s['total_mv'] / 10000:.0f}亿")


if __name__ == '__main__':
    main()
