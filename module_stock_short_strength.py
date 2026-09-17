#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短期强弱评分（Short-term Strength Score, SSS）—— 5 维指标体系
================================================================
解决"哪些股票当下买/卖力量占优，未来 5-20 日更可能跑赢市场"。
与 W23 互补：W23 抓"形态（2 浪末/3 浪初）"，SSS 抓"当下强度"。

5 个指标（每个标准化到 0-100，加权合成综合分）：
  1) RPS 相对强弱     收益维度      权重 30%  —— 涨幅 vs 沪深 300
  2) VPVR 量价共振    量价维度      权重 20%  —— 涨日均量 / 跌日均量
  3) TREND 趋势动量   趋势维度      权重 20%  —— 均线斜率 + 价格站位 + ADX
  4) MSR 动量夏普     稳定性维度    权重 15%  —— 收益/波动（区分脉冲与稳健上行）
  5) MFI 主力资金     资金维度      权重 15%  —— 5 日主力净额 / 5 日成交额

模块分层设计（指标计算与数据获取完全解耦，供外部调用）：
  ┌──────────────────────────────────────────────┐
  │ 第 1 层：纯指标计算（无 DB 依赖，可独立调用）  │
  │   calc_rps(df, bm_ret)        → 0-100        │
  │   calc_vpvr(df)               → 0-100        │
  │   calc_trend(df)              → 0-100        │
  │   calc_msr(df)                → 0-100        │
  │   calc_mfi(df)                → 0-100        │
  │   calc_composite(scores)      → 0-100        │
  │   calc_all_scores(df, bm_ret) → ScoreResult   │
  ├──────────────────────────────────────────────┤
  │ 第 2 层：数据获取（MySQL）                     │
  │   fetch_one(code, days)      → DataFrame     │
  │   fetch_benchmark(symbol)    → Series         │
  │   fetch_stock_name(code)     → str            │
  │   fetch_active_stocks(n)    → list[str]      │
  ├──────────────────────────────────────────────┤
  │ 第 3 层：编排（fetch + calc）                  │
  │   evaluate_one(code)         → ScoreResult   │
  │   run() / main()             → CLI/任务入口   │
  └──────────────────────────────────────────────┘

外部调用示例：
  from module_stock_short_strength import calc_all_scores, fetch_one, fetch_benchmark, safe_pct_change

  df = fetch_one("000001.SZ", 80)
  bm = fetch_benchmark("000300.SH", 80)
  bm_ret = safe_pct_change(bm, 20)
  result = calc_all_scores(df, bm_ret)
  print(result.composite, result.level)

  # 或单独调用某项指标
  from module_stock_short_strength import calc_vpvr
  vpvr = calc_vpvr(df)

数据源：项目 MySQL 数据库
  - 个股日线：stock_daily_t（ts_code, trade_date, open, high, low, close, vol, amount）
  - 基准指数：stock_index_daily_t（ts_code='000300.SH' 沪深 300）
  - 股票名称：stock_info_t（ts_code, stock_name）

用法：
  python module_stock_short_strength.py --code 000001.SZ    # 单标的诊断
  python module_stock_short_strength.py --top 30             # 全市场 Top30
  python module_stock_short_strength.py --selftest          # 离线自检（4 项断言）

⚠️ 阈值/权重为经验初值，建议先用 --selftest 通过后再以 3-6 个月实盘数据校准。
"""

import argparse
import math
import os
import sys
import warnings
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

from module_mysql_connection import get_mysql_connection, close_connection


# ===============================================================
# 可调参数（详见报告《股票短期强弱指标体系.html》）
# ===============================================================
PARAMS = dict(
    # 时间窗口
    lookback=20,                 # 主回看窗口（日）
    vol_lookback=5,              # 主力资金累计窗口
    benchmark="000300.SH",       # 基准：沪深 300

    # 权重（合计 100%）
    w_rps=0.30,
    w_vpvr=0.20,
    w_trend=0.20,
    w_msr=0.15,
    w_mfi=0.15,

    # VPVR 阈值（量比 log 化后）
    vpvr_lo=0.5,                 # → 10 分
    vpvr_mid=1.0,                # → 50 分
    vpvr_hi=2.0,                 # → 80 分
    vpvr_max=3.0,                # → 95 分

    # TREND 阈值
    trend_ma_short=5,
    trend_ma_mid=10,
    trend_ma_long=20,
    trend_adx_strong=25,         # ADX > 25 视作强趋势

    # MSR 阈值
    msr_strong=2.0,              # 动量夏普 > 2 → 满分档
    msr_weak=-1.0,               # 动量夏普 < -1 → 0 分档

    # 输出阈值
    score_top=80,                # 强势区间
    score_bot=30,                # 弱势区间
)

# 权重字典（供 calc_composite 使用）
DEFAULT_WEIGHTS: Dict[str, float] = {
    "rps": PARAMS["w_rps"],
    "vpvr": PARAMS["w_vpvr"],
    "trend": PARAMS["w_trend"],
    "msr": PARAMS["w_msr"],
    "mfi": PARAMS["w_mfi"],
}


# ===============================================================
# 第 1 层：纯指标计算（无 DB 依赖，可独立调用）
# ===============================================================

def safe_pct_change(s: pd.Series, n: int) -> float:
    """计算 N 日涨跌幅，数据不足或除零时返回 NaN。"""
    if len(s) < n + 1 or s.iloc[-n - 1] == 0:
        return np.nan
    return float(s.iloc[-1] / s.iloc[-n - 1] - 1)


def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """平均趋向指数 ADX（手写实现，避免引入 talib）"""
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([
        h - l,
        (h - c.shift(1)).abs(),
        (l - c.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(n, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(n, min_periods=n).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(n, min_periods=n).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(n, min_periods=n).mean()


def cross_sectional_rank(scores: pd.Series) -> pd.Series:
    """横截面百分位排名 → 0-100 分（NaN 保留 NaN）"""
    if scores.notna().sum() < 5:
        return pd.Series(np.nan, index=scores.index)
    return scores.rank(pct=True, na_option="keep") * 100


def calc_rps(df: pd.DataFrame, bm_ret: float,
             lookback: int = None) -> Optional[float]:
    """1) 相对强弱 RPS：超额收益 → 线性映射 0-100

    参数：
      df     : 个股日线 DataFrame，需含 close 列
      bm_ret : 基准指数 N 日涨跌幅
      lookback: 回看窗口（默认 PARAMS["lookback"]）
    """
    n = lookback or PARAMS["lookback"]
    stock_ret = safe_pct_change(df["close"], n)
    if pd.isna(stock_ret) or pd.isna(bm_ret):
        return None
    excess = stock_ret - bm_ret
    # 经验区间：超额 -10% ~ +10% → 0-100
    return float(np.clip((excess + 0.10) / 0.20 * 100, 0, 100))


def calc_vpvr(df: pd.DataFrame,
              lookback: int = None) -> Optional[float]:
    """2) 量价共振 VPVR：log(涨日均量/跌日均量) 分档打分

    参数：
      df      : 个股日线 DataFrame，需含 close、vol 列
      lookback: 回看窗口（默认 PARAMS["lookback"]）
    """
    n = lookback or PARAMS["lookback"]
    if len(df) < n:
        return None
    sub = df.tail(n).copy()
    sub["ret"] = sub["close"].pct_change()
    up_vol = sub.loc[sub["ret"] > 0, "vol"].mean()
    dn_vol = sub.loc[sub["ret"] < 0, "vol"].mean()
    if pd.isna(up_vol) or pd.isna(dn_vol) or dn_vol == 0:
        return None
    ratio = up_vol / dn_vol
    lr = math.log(max(ratio, 0.05))
    p = PARAMS
    if lr <= math.log(p["vpvr_lo"]):
        return 10.0
    if lr >= math.log(p["vpvr_max"]):
        return 95.0
    if lr <= math.log(p["vpvr_mid"]):
        t = (lr - math.log(p["vpvr_lo"])) / (math.log(p["vpvr_mid"]) - math.log(p["vpvr_lo"]))
        return 10 + 40 * t
    if lr <= math.log(p["vpvr_hi"]):
        t = (lr - math.log(p["vpvr_mid"])) / (math.log(p["vpvr_hi"]) - math.log(p["vpvr_mid"]))
        return 50 + 30 * t
    t = (lr - math.log(p["vpvr_hi"])) / (math.log(p["vpvr_max"]) - math.log(p["vpvr_hi"]))
    return 80 + 15 * t


def calc_trend(df: pd.DataFrame) -> Optional[float]:
    """3) 趋势动量：MA斜率（30%）+ 价格站位（40%）+ ADX（30%）

    参数：
      df: 个股日线 DataFrame，需含 high、low、close 列
    """
    if len(df) < max(PARAMS["trend_ma_long"], 14) + 5:
        return None
    p = PARAMS
    close = df["close"]
    ma_s = close.rolling(p["trend_ma_short"]).mean()
    ma_m = close.rolling(p["trend_ma_mid"]).mean()
    ma_l = close.rolling(p["trend_ma_long"]).mean()

    # 子项 1：MA5 5 日斜率年化
    s = ma_s.iloc[-1] - ma_s.iloc[-6]
    slope_pct = s / ma_s.iloc[-6] * (252 / 5) if ma_s.iloc[-6] else 0
    s_score = np.clip((slope_pct + 0.2) / 0.8 * 100, 0, 100)

    # 子项 2：价格站位
    above = sum([
        close.iloc[-1] > ma_s.iloc[-1],
        close.iloc[-1] > ma_m.iloc[-1],
        close.iloc[-1] > ma_l.iloc[-1],
    ])
    p_score = above / 3 * 100

    # 子项 3：ADX
    a = _adx(df, 14).iloc[-1]
    if pd.isna(a):
        a_score = 50.0
    else:
        a_score = np.clip((a - 15) / (p["trend_adx_strong"] - 15) * 100, 0, 100)

    return float(0.30 * s_score + 0.40 * p_score + 0.30 * a_score)


def calc_msr(df: pd.DataFrame,
             lookback: int = None) -> Optional[float]:
    """4) 动量夏普：(N日收益 - 无风险) / N日波动率

    参数：
      df      : 个股日线 DataFrame，需含 close 列
      lookback: 回看窗口（默认 PARAMS["lookback"]）
    """
    n = lookback or PARAMS["lookback"]
    if len(df) < n + 1:
        return None
    rets = df["close"].pct_change().tail(n).dropna()
    if len(rets) < n - 2:
        return None
    cum = (1 + rets).prod() - 1
    rf = 0.02 * n / 252
    vol = rets.std(ddof=1) * math.sqrt(252)
    if vol == 0 or pd.isna(vol):
        return None
    msr = (cum - rf) / vol
    p = PARAMS
    return float(np.clip((msr - p["msr_weak"]) / (p["msr_strong"] - p["msr_weak"]) * 100, 0, 100))


def calc_mfi(df: pd.DataFrame,
             vol_lookback: int = None) -> Optional[float]:
    """5) 主力资金净流入强度：5 日净额累计 / 5 日成交额累计

    粗口径：净额 = (close-open)*vol；实盘可对接 L2 主力净流入接口

    参数：
      df         : 个股日线 DataFrame，需含 open、close、vol 列
      vol_lookback: 资金累计窗口（默认 PARAMS["vol_lookback"]）
    """
    n = vol_lookback or PARAMS["vol_lookback"]
    if len(df) < n + 1:
        return None
    sub = df.tail(n).copy()
    sub["gross_flow"] = (sub["close"] - sub["open"]) * sub["vol"] / 1e8
    sub["turnover"] = sub["vol"] * sub["close"] / 1e8
    net = sub["gross_flow"].sum()
    turn = sub["turnover"].sum()
    if turn == 0 or pd.isna(turn):
        return None
    ratio = net / turn
    return float(np.clip((ratio + 0.02) / 0.04 * 100, 0, 100))


def calc_composite(scores: Dict[str, Optional[float]],
                   weights: Dict[str, float] = None) -> Optional[float]:
    """综合打分：加权合成（缺数据指标权重自动归零）

    参数：
      scores : {"rps": v, "vpvr": v, "trend": v, "msr": v, "mfi": v}
      weights: 权重字典（默认 DEFAULT_WEIGHTS）
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS
    valid = {k: v for k, v in scores.items()
             if v is not None and not pd.isna(v)}
    if not valid:
        return None
    w_sum = sum(weights[k] for k in valid)
    return sum(valid[k] * weights[k] for k in valid) / w_sum


def level_for(s: Optional[float]) -> str:
    """综合分 → 等级文字"""
    if s is None or pd.isna(s):
        return "数据缺失"
    if s >= 80:
        return "强势 ★★★★"
    if s >= 65:
        return "偏强 ★★★"
    if s >= 50:
        return "中性 ★★"
    if s >= 35:
        return "偏弱 ★"
    return "弱势"


# ---------------------------------------------------------------
# 数据结构 & 一站式计算入口
# ---------------------------------------------------------------
@dataclass
class ScoreResult:
    """单标的五维评分结果"""
    code: str = ""
    name: str = ""
    rps: Optional[float] = None
    vpvr: Optional[float] = None
    trend: Optional[float] = None
    msr: Optional[float] = None
    mfi: Optional[float] = None
    composite: Optional[float] = None
    level: str = ""
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典（便于序列化）"""
        return {
            "code": self.code, "name": self.name,
            "rps": self.rps, "vpvr": self.vpvr, "trend": self.trend,
            "msr": self.msr, "mfi": self.mfi,
            "composite": self.composite, "level": self.level,
        }


def calc_all_scores(df: pd.DataFrame, bm_ret: float,
                    code: str = "", name: str = "",
                    verbose: bool = False) -> ScoreResult:
    """一站式计算：传入个股 DataFrame + 基准涨跌幅，返回五维 ScoreResult

    这是供外部调用的主入口——无需查数据库，只要你有 OHLCV 数据即可评分。

    参数：
      df     : 个股日线 DataFrame，需含 open/high/low/close/vol 列
      bm_ret : 基准指数 N 日涨跌幅（float）
      code   : 股票代码（可选，用于结果标识）
      name   : 股票名称（可选）
      verbose: 是否生成背离提示 notes
    """
    r = ScoreResult(code=code, name=name)
    r.rps = calc_rps(df, bm_ret)
    r.vpvr = calc_vpvr(df)
    r.trend = calc_trend(df)
    r.msr = calc_msr(df)
    r.mfi = calc_mfi(df)
    r.composite = calc_composite({
        "rps": r.rps, "vpvr": r.vpvr, "trend": r.trend,
        "msr": r.msr, "mfi": r.mfi,
    })
    r.level = level_for(r.composite)

    if verbose:
        notes = []
        if r.composite is not None and r.composite >= PARAMS["score_top"]:
            notes.append("综合偏强，可作为 W23 入场候选的优先标的")
        elif r.composite is not None and r.composite <= PARAMS["score_bot"]:
            notes.append("综合偏弱，规避/止盈")
        if r.rps is not None and r.mfi is not None:
            if r.rps - r.mfi > 30:
                notes.append("⚠️ 量价背离：涨幅领先但资金净流出——警惕假突破")
            if r.mfi - r.rps > 30:
                notes.append("资金背离：资金抢筹但价格未启动——可左侧关注")
        if r.trend is not None and r.msr is not None and r.trend - r.msr > 30:
            notes.append("形态强但波动大——可能是脉冲式强势，回撤风险高")
        r.notes = notes
    return r


# ===============================================================
# 第 2 层：数据获取（MySQL）
# ===============================================================

def fetch_one(code: str, days: int = 180) -> pd.DataFrame:
    """从 stock_daily_t 取单只股票近 N 个交易日日线（含成交量）"""
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT ts_code, trade_date, open, high, low, close, vol, amount
                FROM stock_daily_t
                WHERE ts_code = %s
                ORDER BY trade_date DESC
                LIMIT %s
            """
            cursor.execute(sql, (code, days))
            rows = cursor.fetchall()
        if not rows:
            raise RuntimeError(f"{code} 无数据")
        df = pd.DataFrame(rows)
        df = df.sort_values("trade_date").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "vol", "amount"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.tail(days).reset_index(drop=True)
    finally:
        close_connection(conn)


def fetch_benchmark(symbol: str = "000300.SH", days: int = 180) -> pd.Series:
    """从 stock_index_daily_t 取基准指数日线收盘"""
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT trade_date, close
                FROM stock_index_daily_t
                WHERE ts_code = %s
                ORDER BY trade_date DESC
                LIMIT %s
            """
            cursor.execute(sql, (symbol, days))
            rows = cursor.fetchall()
        if not rows:
            raise RuntimeError(f"基准 {symbol} 无数据")
        df = pd.DataFrame(rows)
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df.set_index("trade_date")["close"].tail(days)
    finally:
        close_connection(conn)


def fetch_stock_name(code: str) -> str:
    """从 stock_info_t 取股票名称"""
    conn = get_mysql_connection()
    if not conn:
        return ""
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT stock_name FROM stock_info_t WHERE ts_code = %s LIMIT 1",
                (code,)
            )
            row = cursor.fetchone()
            return row["stock_name"] if row else ""
    except Exception:
        return ""
    finally:
        close_connection(conn)


def fetch_active_stocks(top_n: int = 300) -> list:
    """取最近交易日成交额最大的前 N 只股票（ts_code 列表）"""
    conn = get_mysql_connection()
    if not conn:
        raise RuntimeError("数据库连接失败")
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT MAX(trade_date) AS d FROM stock_daily_t")
            row = cursor.fetchone()
            if not row or not row.get("d"):
                return []
            latest = row["d"]
            cursor.execute("""
                SELECT ts_code FROM stock_daily_t
                WHERE trade_date = %s AND amount IS NOT NULL AND amount > 0
                ORDER BY amount DESC
                LIMIT %s
            """, (latest, top_n))
            return [r["ts_code"] for r in cursor.fetchall()]
    finally:
        close_connection(conn)


# ===============================================================
# 第 3 层：编排（fetch + calc）
# ===============================================================

def evaluate_one(code: str, name: str = "", bm: Optional[pd.Series] = None,
                verbose: bool = False) -> ScoreResult:
    """单标的五维评估：自动取数 + 评分

    便捷入口——内部调用 fetch_one + fetch_benchmark + calc_all_scores。
    如已有 DataFrame 数据，请直接调用 calc_all_scores 避免重复查库。
    """
    df = fetch_one(code, days=PARAMS["lookback"] + 60)
    if not name:
        name = fetch_stock_name(code)
    if bm is None:
        bm = fetch_benchmark(PARAMS["benchmark"], days=PARAMS["lookback"] + 60)

    bm_ret = safe_pct_change(bm, PARAMS["lookback"])
    return calc_all_scores(df, bm_ret, code=code, name=name, verbose=verbose)


# ---------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------
def _fmt(x):
    return "-" if x is None or pd.isna(x) else f"{x:.1f}"


def print_one(r: ScoreResult):
    """打印单标的评分报告"""
    print(f"\n=== {r.code} {r.name} ===")
    print(f"  综合强弱分：{'-' if r.composite is None else f'{r.composite:.1f}'}  ({r.level})")
    print(f"  ├ RPS 相对强弱  : {_fmt(r.rps)}")
    print(f"  ├ VPVR 量价共振 : {_fmt(r.vpvr)}")
    print(f"  ├ TREND 趋势    : {_fmt(r.trend)}")
    print(f"  ├ MSR 动量夏普  : {_fmt(r.msr)}")
    print(f"  └ MFI 主力资金  : {_fmt(r.mfi)}")
    for n in r.notes:
        print(f"  💡 {n}")


# ===============================================================
# 离线自检（不依赖数据库）
# ===============================================================

def _make_synthetic(n=60, vpvr_scenario=None, trend_scenario=None):
    """生成合成行情用于自检"""
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
    np.random.seed(42)
    if trend_scenario == "up":
        close = 10 * np.cumprod(1 + np.random.normal(0.003, 0.012, n))
    elif trend_scenario == "down":
        close = 10 * np.cumprod(1 + np.random.normal(-0.003, 0.012, n))
    else:
        close = 10 + np.cumsum(np.random.normal(0, 0.15, n))
    close = np.maximum(close, 1)
    df = pd.DataFrame({
        "date": dates,
        "open": close * (1 + np.random.normal(0, 0.005, n)),
        "high": close * (1 + np.abs(np.random.normal(0, 0.008, n))),
        "low": close * (1 - np.abs(np.random.normal(0, 0.008, n))),
        "close": close,
        "vol": np.random.randint(1_000_000, 5_000_000, n).astype(float),
    })
    if vpvr_scenario == "up":
        rets = df["close"].pct_change().fillna(0).values
        df["vol"] = np.where(rets > 0, df["vol"] * 3, df["vol"] * 1)
    elif vpvr_scenario == "down":
        rets = df["close"].pct_change().fillna(0).values
        df["vol"] = np.where(rets > 0, df["vol"] * 1, df["vol"] * 3)
    return df


def _check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  | {detail}")


def selftest():
    """4 项自检，验证打分逻辑与边界"""
    print("========== SSS 短期强弱 离线自检 ==========")
    pass_cnt = 0
    total = 4

    # 1) 量价共振：放量上涨高分
    df = _make_synthetic(vpvr_scenario="up")
    v = calc_vpvr(df)
    _check("① 量价共振：放量上涨分 ≥ 50", v >= 50, f"放量场景={v:.1f}")
    if v >= 50: pass_cnt += 1

    # 2) 量价共振：缩量上涨低分
    df = _make_synthetic(vpvr_scenario="down")
    v = calc_vpvr(df)
    _check("② 量价共振：缩量上涨分 ≤ 50", v <= 50, f"缩量场景={v:.1f}")
    if v <= 50: pass_cnt += 1

    # 3) 趋势：上升 > 下降
    df = _make_synthetic(trend_scenario="up")
    t = calc_trend(df)
    df2 = _make_synthetic(trend_scenario="down")
    t2 = calc_trend(df2)
    _check("③ 趋势：上升分 > 下降分 + 10", t - t2 > 10,
           f"上升={t:.1f}, 下降={t2:.1f}, 差={t-t2:.1f}")
    if t - t2 > 10: pass_cnt += 1

    # 4) 综合分边界
    s100 = calc_composite({"rps": 100, "vpvr": 100, "trend": 100, "msr": 100, "mfi": 100})
    s0 = calc_composite({"rps": 0, "vpvr": 0, "trend": 0, "msr": 0, "mfi": 0})
    _check("④ 综合分：全 100 = 100，全 0 = 0",
           abs(s100 - 100) < 0.1 and abs(s0 - 0) < 0.1,
           f"100→{s100:.1f}, 0→{s0:.1f}")
    if abs(s100 - 100) < 0.1 and abs(s0 - 0) < 0.1: pass_cnt += 1

    print(f"\n[PASS] {pass_cnt}/{total} 项通过")
    return pass_cnt == total


# ===============================================================
# 任务入口（供 monitor_run_daily_tasks 调度）
# ===============================================================

def run():
    """任务入口：扫描全市场 Top30 短期强势股并导出 CSV。"""
    print("📊 短期强弱评分（SSS）—— 全市场扫描 Top30")
    top_n = 30
    try:
        codes = fetch_active_stocks(300)
    except Exception as e:
        print(f"❌ 股票池取数失败：{e}")
        return
    if not codes:
        print("❌ 未获取到活跃股票列表")
        return

    print(f"共 {len(codes)} 只活跃股票，开始评分...")
    try:
        bm = fetch_benchmark(PARAMS["benchmark"], days=PARAMS["lookback"] + 60)
    except Exception as e:
        print(f"❌ 基准取数失败：{e}")
        return

    bm_ret = safe_pct_change(bm, PARAMS["lookback"])
    results = []
    for i, c in enumerate(codes, 1):
        try:
            df = fetch_one(c, days=PARAMS["lookback"] + 60)
            r = calc_all_scores(df, bm_ret, code=c, name=fetch_stock_name(c))
            results.append(r)
        except Exception:
            continue
        if i % 50 == 0:
            print(f"  进度：{i}/{len(codes)}")

    if not results:
        print("❌ 无有效评分结果")
        return

    _export_results(results, top_n)


def _export_results(results: list, top_n: int):
    """结果导出为 CSV + 控制台打印"""
    df = pd.DataFrame([{
        "代码": r.code, "名称": r.name,
        "综合分": round(r.composite, 1) if r.composite else None,
        "等级": r.level,
        "RPS": round(r.rps, 1) if r.rps else None,
        "VPVR": round(r.vpvr, 1) if r.vpvr else None,
        "TREND": round(r.trend, 1) if r.trend else None,
        "MSR": round(r.msr, 1) if r.msr else None,
        "MFI": round(r.mfi, 1) if r.mfi else None,
    } for r in results])
    df = df.sort_values("综合分", ascending=False, na_position="last")
    print(f"\n=== 全市场 Top{top_n} 短期强势股 ===")
    print(df.head(top_n).to_string(index=False))
    out_path = os.path.join(BASE_DIR, f"sss_top{top_n}.csv")
    df.head(top_n).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n已导出 → {out_path}")


# ===============================================================
# CLI
# ===============================================================

def main():
    ap = argparse.ArgumentParser(description="短期强弱评分（5 维）")
    ap.add_argument("--code", help="单标的诊断，如 000001.SZ")
    ap.add_argument("--name", default="", help="标的名称（可选）")
    ap.add_argument("--top", type=int, default=0, help="全市场扫描取 Top N")
    ap.add_argument("--selftest", action="store_true", help="离线自检（4 项断言）")
    args = ap.parse_args()

    if args.selftest:
        return 0 if selftest() else 1

    if args.code:
        r = evaluate_one(args.code, args.name, verbose=True)
        print_one(r)
        return 0

    if args.top > 0:
        print(f"📊 全市场 Top{args.top} 短期强势股扫描")
        codes = fetch_active_stocks(300)
        if not codes:
            print("❌ 未获取到活跃股票列表")
            return 1
        print(f"共 {len(codes)} 只活跃股票，开始评分...")
        bm = fetch_benchmark(PARAMS["benchmark"], days=PARAMS["lookback"] + 60)
        bm_ret = safe_pct_change(bm, PARAMS["lookback"])
        results = []
        for i, c in enumerate(codes, 1):
            try:
                df = fetch_one(c, days=PARAMS["lookback"] + 60)
                r = calc_all_scores(df, bm_ret, code=c, name=fetch_stock_name(c))
                results.append(r)
            except Exception:
                continue
            if i % 50 == 0:
                print(f"  进度：{i}/{len(codes)}")
        if not results:
            print("❌ 无有效评分结果")
            return 1
        _export_results(results, args.top)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
