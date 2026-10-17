"""数据层：封装 westock-data CLI，统一返回 pandas 结构。

所有外部数据入口都在这里，上层分析模块不直接碰 CLI。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

# ---------------------------------------------------------------- 缓存

_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "astock_analyzer")
_MEM_CACHE: Dict[str, str] = {}


class DataUnavailable(RuntimeError):
    """数据源不可用或返回为空。"""


def _cache_path(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
    return os.path.join(_CACHE_DIR, safe)


def _read_cache(key: str, ttl_days: int) -> Optional[str]:
    if key in _MEM_CACHE:
        return _MEM_CACHE[key]
    p = _cache_path(key)
    if os.path.exists(p) and (time_now() - os.path.getmtime(p)) < ttl_days * 86400:
        with open(p, "r", encoding="utf-8") as f:
            _MEM_CACHE[key] = f.read()
        return _MEM_CACHE[key]
    return None


def time_now() -> float:
    import time

    return time.time()


def _write_cache(key: str, text: str) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w", encoding="utf-8") as f:
        f.write(text)
    _MEM_CACHE[key] = text


def run_cli(args: List[str], ttl_days: int = 0) -> str:
    """执行 westock-data 命令；ttl_days>0 时走本地缓存。"""
    exe = _which_westock()
    cmd = [exe] + args
    key = "_".join(args)

    if ttl_days > 0:
        cached = _read_cache(key, ttl_days)
        if cached is not None:
            return cached

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except Exception as exc:  # noqa: BLE001
        raise DataUnavailable(f"westock-data 执行失败：{exc}") from exc

    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        raise DataUnavailable(f"westock-data 返回空：{' '.join(args)}")
    if "数据为空" in out or "未找到匹配" in out:
        raise DataUnavailable(f"数据源无覆盖：{' '.join(args)}")
    if proc.returncode != 0 and "Traceback" in out:
        raise DataUnavailable(f"westock-data 报错：{out[:300]}")

    if ttl_days > 0:
        _write_cache(key, out)
    return out


def _which_westock() -> str:
    exe = os.environ.get("WESTOCK_BIN") or shutil.which("westock-data") or shutil.which("westock")
    if not exe:
        raise DataUnavailable(
            "未找到 westock-data。请安装 WeStock CLI，或用环境变量 WESTOCK_BIN 指定路径。"
        )
    return exe


# ---------------------------------------------------------------- Markdown 解析

_DEF_LINE = re.compile(r"^\|\s*-+(\s*\|[-]+)*\s*\|$")
_BATCH = re.compile(r"^\[Batch\]", re.M)


def parse_tables(text: str) -> List[pd.DataFrame]:
    """把 CLI 输出的一个或多个 Markdown 表格解析为 DataFrame 列表。"""
    lines = [ln.rstrip() for ln in text.splitlines()]
    tables: List[pd.DataFrame] = []
    buf: List[str] = []

    for ln in lines:
        if ln.lstrip().startswith("|"):
            buf.append(ln)
            continue
        if buf:
            tables.append(_parse_one(buf))
            buf = []
    if buf:
        tables.append(_parse_one(buf))
    return [t for t in tables if t is not None and not t.empty]


def _parse_one(block: List[str]) -> Optional[pd.DataFrame]:
    if len(block) < 2:
        return None
    header = _split_row(block[0])
    if not header:
        return None
    sep_idx = next((i for i, ln in enumerate(block) if _DEF_LINE.match(ln.strip())), None)
    body = block[(sep_idx + 1) :] if sep_idx is not None else block[1:]
    rows = [_split_row(ln) for ln in body]
    rows = [r for r in rows if r and len(r) == len(header)]
    if not rows:
        return pd.DataFrame(columns=header)
    return pd.DataFrame(rows, columns=header)


def _split_row(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _num(df: pd.DataFrame) -> pd.DataFrame:
    """尽力把数值列转成 float，无法转换的置 NaN。"""
    out = df.copy()
    for c in out.columns:
        conv = pd.to_numeric(out[c], errors="coerce")
        if conv.notna().sum() >= max(1, int(len(out) * 0.5)):
            out[c] = conv
    return out


# ---------------------------------------------------------------- 取数

@dataclass
class Quote:
    code: str
    name: str
    data: Dict[str, Any]


def normalize_code(code: str) -> str:
    """600577 / sh600577 / 600577.SH → sh600577。"""
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if "." in c:  # 600577.SH
        num, suf = c.split(".", 1)
        suf = suf.lower()
        return f"{suf}{num}" if suf in ("sh", "sz", "bj") else f"sh{num}"
    if c.startswith(("6", "9", "5")):
        return f"sh{c}"
    if c.startswith(("4", "8")):
        return f"bj{c}"
    return f"sz{c}"


def get_quote(code: str, date: Optional[str] = None) -> Quote:
    args = ["quote", normalize_code(code)]
    if date:
        args += ["--date", date]
    text = run_cli(args, ttl_days=1)
    tables = parse_tables(text)
    if not tables:
        raise DataUnavailable(f"行情数据为空：{code}")
    df = _num(tables[0]).iloc[-1]
    return Quote(code=normalize_code(code), name=str(df.get("name", "")), data=df.to_dict())


def get_kline(code: str, start: Optional[str] = None, end: Optional[str] = None,
              limit: int = 300, fq: Optional[str] = None) -> pd.DataFrame:
    """日K。列：date, open, close, high, low, volume, amount, turnover_rate, change_pct"""
    code = normalize_code(code)
    args = ["kline", code, "--period", "day", "--limit", str(limit)]
    if start:
        args += ["--start", start]
    if end:
        args += ["--end", end]
    if fq:
        args += ["--fq", fq]
    tables = parse_tables(run_cli(args))
    if not tables:
        raise DataUnavailable(f"K线数据为空：{code}")
    df = _num(tables[0])
    df = df.rename(columns={"last": "close", "exchange": "turnover_rate"})
    keep = [c for c in ["date", "open", "close", "high", "low", "volume",
                        "amount", "turnover_rate", "change_pct"] if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def get_technical(code: str, date: Optional[str] = None) -> pd.DataFrame:
    """单日技术指标快照（BOLL/MACD/KDJ/RSI/MA）。"""
    args = ["technical", normalize_code(code)]
    if date:
        args += ["--date", date]
    tables = parse_tables(run_cli(args))
    if not tables:
        raise DataUnavailable(f"技术指标为空：{code}")
    return _num(tables[0]).iloc[-1].to_dict()


def get_chip(code: str) -> Optional[Dict[str, Any]]:
    """筹码成本与获利盘。非致命，失败返回 None。"""
    try:
        tables = parse_tables(run_cli(["chip", normalize_code(code)], ttl_days=1))
        if not tables:
            return None
        return _num(tables[0]).iloc[-1].to_dict()
    except DataUnavailable:
        return None


def get_fund_flow(code: str, start: Optional[str] = None,
                  end: Optional[str] = None) -> pd.DataFrame:
    """资金流。列：date, main_net, jumbo_net, block_net, mid_net, small_net, close"""
    args = ["fund", "flow", normalize_code(code)]
    if start:
        args += ["--start", start]
    if end:
        args += ["--end", end]
    try:
        tables = parse_tables(run_cli(args, ttl_days=1))
    except DataUnavailable:
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    df = _num(tables[0])
    if "EndDate" in df.columns and "date" in df.columns:
        df = df.drop(columns=["EndDate"])
    ren = {
        "EndDate": "date", "MainNetFlow": "main_net", "JumboNetFlow": "jumbo_net",
        "BlockNetFlow": "block_net", "MidNetFlow": "mid_net", "SmallNetFlow": "small_net",
        "ClosePrice": "close",
    }
    df = df.rename(columns=ren)
    df = df.loc[:, ~df.columns.duplicated()]
    keep = [c for c in ren.values() if c in df.columns]
    df = df[keep].copy()
    if "date" not in df.columns:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def get_margin(code: str, start: Optional[str] = None,
               end: Optional[str] = None) -> pd.DataFrame:
    """两融。列：date, close, finance_balance, security_balance, finance_net_buy, finance_doD"""
    args = ["fund", "margin", normalize_code(code)]
    if start:
        args += ["--start", start]
    if end:
        args += ["--end", end]
    try:
        tables = parse_tables(run_cli(args, ttl_days=1))
    except DataUnavailable:
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    df = _num(tables[0])
    ren = {
        "date": "date", "closePrice": "close", "FinanceValue": "finance_balance",
        "SecurityValue": "security_balance", "FinanceBuyValue": "finance_buy",
        "FinanceRefundValue": "finance_refund", "FinanceValueDOD": "finance_doD",
    }
    df = df.rename(columns=ren)
    keep = [c for c in ren.values() if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="%Y-%m-%d")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if "finance_buy" in df and "finance_refund" in df:
        df["finance_net_buy"] = df["finance_buy"] - df["finance_refund"]
    return df


def get_news(code: str, limit: int = 10) -> pd.DataFrame:
    """个股资讯。非致命。"""
    try:
        tables = parse_tables(run_cli(["news", "list", normalize_code(code), "--limit", str(limit)]))
    except DataUnavailable:
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    return tables[0]


def last_trade_date(k: pd.DataFrame) -> str:
    return pd.Timestamp(k["date"].iloc[-1]).strftime("%Y-%m-%d")


def is_trading_day(code: str = "sh000001") -> tuple:
    """返回 (是否交易时段, 最近交易日, 最近收盘)。盘中判断按北京时 9:30-15:00。"""
    from datetime import datetime, timedelta, timezone

    bj = timezone(timedelta(hours=8))
    now = datetime.now(bj)
    try:
        q = get_quote("sh000001")
        prev_close = float(q.data.get("prev_close") or 0)
        last_px = float(q.data.get("price") or 0)
        qtime = str(q.data.get("time") or "")
        d = qtime[:10]
        if d and d != now.strftime("%Y-%m-%d"):
            return False, d, last_px or prev_close
        t = qtime[11:16] if len(qtime) >= 16 else ""
        in_session = ("09:30" <= t <= "11:30") or ("13:00" <= t <= "15:00")
        return in_session, d, last_px or prev_close
    except Exception:  # noqa: BLE001
        return False, now.strftime("%Y-%m-%d"), float("nan")


def eod_offset() -> int:
    """当日收盘前，建议回看多少个自然日才能取到完整的两融/资金流数据。"""
    from datetime import datetime, timedelta, timezone

    bj = timezone(timedelta(hours=8))
    return 1 if datetime.now(bj).hour < 18 else 0


def universe_list(market: str = "hs", utype: str = "stock", limit: int = 5000) -> List[tuple]:
    """证券池清单，返回 [(code, name), ...]。"""
    try:
        tables = parse_tables(run_cli(["universe", "--market", market, "--type", utype,
                                       "--limit", str(limit)], ttl_days=1))
    except DataUnavailable:
        return []
    if not tables:
        return []
    df = tables[0]
    cols = {c.lower(): c for c in df.columns}
    ccol = cols.get("code")
    ncol = cols.get("name")
    if not ccol:
        return []
    return [(str(r[ccol]), str(r[ncol]) if ncol else "") for _, r in df.iterrows()]


def resolve_input(text: str) -> List[tuple]:
    """把 '600577' 或 'sh600577,sz000651' 解析为 [(code, name), ...]。"""
    out = []
    for tok in str(text).replace("，", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        c = normalize_code(tok)
        try:
            out.append((c, get_quote(c).name))
        except DataUnavailable:
            out.append((c, tok))
    return out


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "600577"
    print(get_quote(code).name)
    print(get_kline(code, limit=5).to_string(index=False))