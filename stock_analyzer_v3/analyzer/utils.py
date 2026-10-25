"""通用工具：数值解析、格式化、磁盘缓存、分档打分。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import re
import time
from typing import Any, Callable, Iterable

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


# ------------------------------------------------------------------ 数值解析
def to_num(value: Any) -> float | None:
    """把 '1.66亿' / '-6617.00万' / '12.73%' / 'False' / '' 统一转成 float。

    解析失败返回 None（而不是 0），避免把"缺失"误当成"零"。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    s = str(value).strip()
    if s in {"", "-", "--", "False", "false", "None", "nan", "NaN", "不适用"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("%", "").replace("元", "").replace("股", "")
    mult = 1.0
    if s.endswith("万亿"):
        mult, s = 1e12, s[:-2]
    elif s.endswith("亿"):
        mult, s = 1e8, s[:-1]
    elif s.endswith("万"):
        mult, s = 1e4, s[:-1]
    elif s.endswith("手"):
        mult, s = 100.0, s[:-1]
    # 处理 "1.66亿" 之外可能出现的 "≈" 等
    s = s.replace("≈", "").replace("↑", "").replace("↓", "").strip()
    try:
        v = float(s) * mult
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return None
        v = float(m.group()) * mult
    return -v if neg else v


def safe_div(a: float | None, b: float | None, default: float | None = None) -> float | None:
    if a is None or b is None or b == 0:
        return default
    return a / b


def pct(a: float | None, b: float | None, digits: int = 2) -> float | None:
    r = safe_div(a, b)
    return None if r is None else round(r * 100, digits)


# ------------------------------------------------------------------ 格式化
def fmt_num(v: float | None, digits: int = 2, dash: str = "—") -> str:
    if v is None:
        return dash
    return f"{v:,.{digits}f}"


def fmt_pct(v: float | None, digits: int = 2, sign: bool = False, dash: str = "—") -> str:
    if v is None:
        return dash
    s = f"{v:+.{digits}f}%" if sign else f"{v:.{digits}f}%"
    return s


def fmt_yi(v: float | None, digits: int = 2, unit: bool = True, dash: str = "—") -> str:
    """元 -> 亿元。"""
    if v is None:
        return dash
    s = f"{v / 1e8:,.{digits}f}"
    return f"{s}亿" if unit else s


def fmt_wan(v: float | None, digits: int = 2, dash: str = "—") -> str:
    if v is None:
        return dash
    return f"{v / 1e4:,.{digits}f}万"


def arrow(v: float | None) -> str:
    if v is None:
        return ""
    return "↑" if v > 0 else ("↓" if v < 0 else "→")


# ------------------------------------------------------------------ 分档打分
def band_score(value: float | None, bands: Iterable[tuple[float, float]], default: float = 0.0) -> float:
    """bands 形如 [(20, 8), (10, 6), (0, 4), (-1e9, 2)]，按顺序取第一个 value>=阈值 的档位。"""
    if value is None:
        return default
    for threshold, score in bands:
        if value >= threshold:
            return score
    return default


def scale_score(value: float | None, low: float, high: float, points: float) -> float:
    """线性映射到 [0, points]，超出范围截断。value 越高分越高。"""
    if value is None:
        return 0.0
    if high == low:
        return 0.0
    ratio = (value - low) / (high - low)
    return round(max(0.0, min(1.0, ratio)) * points, 2)


def inverse_scale_score(value: float | None, low: float, high: float, points: float) -> float:
    """value 越低分越高（用于估值分位、负债率等）。"""
    if value is None:
        return 0.0
    if high == low:
        return 0.0
    ratio = (high - value) / (high - low)
    return round(max(0.0, min(1.0, ratio)) * points, 2)


def percentile_rank(sorted_values: list[float], value: float) -> float:
    """value 在序列中的百分位（0~100，越大越靠前）。"""
    if not sorted_values or value is None:
        return 0.0
    n = len(sorted_values)
    cnt = sum(1 for v in sorted_values if v <= value)
    return round(cnt / n * 100, 1)


# ------------------------------------------------------------------ 缓存
class DiskCache:
    """极简磁盘缓存：按 key 存 pickle，带 TTL。避免反复打接口被限流。"""

    def __init__(self, directory: str = CACHE_DIR, enabled: bool = True):
        self.dir = directory
        self.enabled = enabled
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, key: str) -> str:
        h = hashlib.md5(key.encode("utf-8")).hexdigest()[:20]
        safe = re.sub(r"[^\w\-.]", "_", key)[:40]
        return os.path.join(self.dir, f"{safe}.{h}.pkl")

    def get(self, key: str, ttl: float) -> Any | None:
        if not self.enabled:
            return None
        p = self._path(key)
        if not os.path.exists(p):
            return None
        if time.time() - os.path.getmtime(p) > ttl:
            return None
        try:
            with open(p, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        try:
            with open(self._path(key), "wb") as f:
                pickle.dump(value, f)
        except Exception:
            pass

    def clear(self) -> int:
        n = 0
        for f in os.listdir(self.dir):
            if f.endswith(".pkl"):
                os.remove(os.path.join(self.dir, f))
                n += 1
        return n


CACHE = DiskCache()


def cached(key: str, ttl: float) -> Callable:
    """装饰器：把函数返回值写入磁盘缓存。"""
    def deco(fn: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            ck = f"{key}::{args}::{json.dumps(kwargs, sort_keys=True, default=str)}"
            hit = CACHE.get(ck, ttl)
            if hit is not None:
                return hit
            val = fn(*args, **kwargs)
            if val is not None:
                CACHE.set(ck, val)
            return val
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        return wrapper
    return deco


def market_prefix(code: str) -> str:
    """6位代码 -> 交易所前缀（腾讯/新浪用 sh/sz/bj）。"""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "90", "51", "58", "56", "11")):
        return "sh"
    if code.startswith(("00", "30", "20", "15", "12", "39")):
        return "sz"
    if code.startswith(("8", "43", "83", "87", "92")):
        return "bj"
    return "sh"


def to_secid(code: str) -> str:
    """腾讯格式：sh688213 / sz002129。"""
    return f"{market_prefix(code)}{str(code).zfill(6)}"


def guess_market_label(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("688"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith(("60",)):
        return "沪市主板"
    if code.startswith(("00",)):
        return "深市主板"
    if code.startswith(("8", "4", "9")):
        return "北交所"
    return "其他"


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
