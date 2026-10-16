"""数据层：行情快照等走 WeStock CLI，日K 走本地 stock_daily_t（MySQL）。

所有外部数据获取集中在此模块，上层分析逻辑不直接触碰 CLI/数据库。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

# stock_analyzer 位于项目根目录下，需把根目录加入 sys.path 以复用公共连接模块
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from module_mysql_connection import get_mysql_connection, close_connection
except ImportError:  # 数据库模块缺失时日K回退 WeStock CLI
    get_mysql_connection = None
    close_connection = None

CLI = "westock-data"
TIMEOUT = 60


class DataError(RuntimeError):
    """数据获取失败。"""


# ---------------------------------------------------------------- 代码归一化

def normalize_code(raw: str) -> str:
    """把用户输入的各种写法归一化为 westock 标准代码。

    支持：688213 / 688213.SH / sh688213 / SH688213
    """
    s = raw.strip().replace(" ", "")
    if not s:
        raise DataError("股票代码为空")

    # 已带市场前缀
    m = re.fullmatch(r"(sh|sz|bj|hk)(\d{4,6})", s, re.I)
    if m:
        pre, num = m.group(1).lower(), m.group(2)
        _validate(pre, num)
        return f"{pre}{num}"

    # 后缀形式 688213.SH
    m = re.fullmatch(r"(\d{4,6})\.(sh|sz|bj|hk)", s, re.I)
    if m:
        pre, num = m.group(2).lower(), m.group(1)
        _validate(pre, num)
        return f"{pre}{num}"

    # 纯数字，按板块规则推断
    if s.isdigit():
        if len(s) == 6:
            if s[0] in ("5", "6", "9") or s.startswith(("11", "13")):
                _validate("sh", s)
                return f"sh{s}"
            if s[0] in ("0", "2", "3"):
                _validate("sz", s)
                return f"sz{s}"
            if s[0] in ("4", "8"):
                _validate("bj", s)
                return f"bj{s}"
        if len(s) == 5:  # 港股
            num = s.zfill(5)
            _validate("hk", num)
            return f"hk{num}"

    raise DataError(f"无法识别的股票代码：{raw}（示例：688213 / 688213.SH / sh688213）")


def _validate(prefix: str, num: str) -> None:
    """校验代码与市场前缀是否自洽，避免生成必然查不到的代码。"""
    if prefix == "hk":
        if not (0 <= int(num) <= 9999):
            raise DataError(f"港股代码超出范围：{prefix}{num}")
        return
    if not num.isdigit() or len(num) != 6:
        raise DataError(f"代码位数不正确：{prefix}{num}（A 股应为 6 位）")
    head = num[0]
    expect = {
        "sh": ("5", "6", "9"),
        "sz": ("0", "2", "3"),
        "bj": ("4", "8"),
    }.get(prefix)
    if expect and head not in expect:
        raise DataError(
            f"代码与市场前缀不匹配：{prefix}{num}（{prefix} 应以 {'/'.join(expect)} 开头）"
        )


# ---------------------------------------------------------------- CLI 调用

def _run(args: list[str]) -> str:
    if shutil.which(CLI) is None:
        raise DataError(f"未找到 {CLI}，请先安装 WeStock 行情工具")
    try:
        r = subprocess.run(
            [CLI] + args, capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired as e:
        raise DataError(f"{CLI} {' '.join(args)} 超时") from e
    if r.returncode != 0:
        raise DataError(f"{CLI} 失败：{(r.stderr or r.stdout).strip()[:200]}")
    out = r.stdout.strip()
    if not out:
        raise DataError(f"{CLI} 返回空数据")
    return out


# ---------------------------------------------------------------- 表格解析

def parse_tables(text: str) -> list[dict[str, Any]]:
    """从 CLI 输出中提取所有 Markdown 表格为 dict 列表。"""
    tables: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:\-|]+\|$", lines[i + 1].strip()
        ):
            header = _split_row(line)
            i += 2
            rows: list[dict[str, Any]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i])
                if len(cells) == len(header):
                    rows.append(dict(zip(header, cells)))
                i += 1
            tables.append({"header": header, "rows": rows})
        else:
            i += 1
    return tables


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _f(v: Any) -> float | None:
    """安全转 float。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "--", "None", "null", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _first_table(text: str) -> list[dict[str, Any]]:
    tables = parse_tables(text)
    if not tables or not tables[0]["rows"]:
        raise DataError("未解析到数据表格")
    return tables[0]["rows"]


def _pick(rows: list[dict[str, Any]], code: str) -> dict[str, Any]:
    """多标的时挑出目标代码那一行。"""
    for r in rows:
        if r.get("code") == code:
            return r
    return rows[0]


# ---------------------------------------------------------------- 数据结构

@dataclass
class Quote:
    """实时行情快照。"""
    code: str
    name: str = ""
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    vol_unit_shares: bool = True  # volume 字段是否已是「股」
    amount: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    range_pct: float | None = None
    pe_ttm: float | None = None
    pe_fwd: float | None = None
    pe_lyr: float | None = None
    pb: float | None = None
    dividend_ttm: float | None = None
    total_mcap: float | None = None
    float_mcap: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    chg_5d: float | None = None
    chg_10d: float | None = None
    chg_20d: float | None = None
    chg_60d: float | None = None
    chg_ytd: float | None = None
    date: str = ""

    @property
    def amplitude(self) -> float | None:
        if self.high is not None and self.low is not None and self.prev_close:
            return round((self.high - self.low) / self.prev_close * 100, 2)
        return None


@dataclass
class Bar:
    """单根 K 线。"""
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float = 0.0
    chg: float | None = None


@dataclass
class FundFlowDay:
    date: str
    close: float
    main: float
    jumbo: float
    big: float
    mid: float
    small: float


@dataclass
class MarginDay:
    date: str
    close: float
    finance_balance: float
    finance_net_buy: float
    finance_dod: float | None
    security_balance: float | None


@dataclass
class Bundle:
    """一只标的的全量数据包。任一环节失败不影响其余部分。"""
    code: str
    quote: Quote | None = None
    bars: list[Bar] = field(default_factory=list)
    tech: list[dict[str, Any]] = field(default_factory=list)
    flows: list[FundFlowDay] = field(default_factory=list)
    margins: list[MarginDay] = field(default_factory=list)
    chip: dict[str, float] = field(default_factory=dict)
    consensus: dict[str, Any] = field(default_factory=dict)
    score: dict[str, float] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    reports: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)
    weekly: list[Bar] = field(default_factory=list)
    mcap_date: str = ""  # 总市值取自 stock_daily_basic_info_t 的交易日(YYYYMMDD)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, tag: str, e: Exception) -> None:
        self.errors.append(f"{tag}：{e}")


# ---------------------------------------------------------------- 各接口封装

def fetch_quote(code: str) -> Quote:
    text = _run(["quote", code])
    if "未找到" in text:
        raise DataError(f"未找到 {code} 的行情数据，请确认代码是否正确")
    r = _pick(_first_table(text), code)
    price = _f(r.get("price"))
    vol = _f(r.get("volume"))
    amt = _f(r.get("amount"))
    # 成交量单位探测：实测科创板返回「股」，沪深主板/创业板返回「手」。
    # 用 amount / (volume × price) 反推，≈1 说明是股，≈100 说明是手。
    is_shares = True
    if price and vol and amt and vol > 0:
        ratio = amt / (vol * price)
        is_shares = ratio < 10
    return Quote(
        code=code,
        name=r.get("name", ""),
        price=price,
        change=_f(r.get("change")),
        change_pct=_f(r.get("change_percent")),
        open=_f(r.get("open")),
        high=_f(r.get("high")),
        low=_f(r.get("low")),
        prev_close=_f(r.get("prev_close")),
        volume=vol if is_shares else (vol * 100 if vol else None),
        vol_unit_shares=is_shares,
        amount=amt,
        turnover_rate=_f(r.get("turnover_rate")),
        volume_ratio=_f(r.get("volume_ratio")),
        range_pct=_f(r.get("range_pct")),
        pe_ttm=_f(r.get("pe_ratio")),
        pe_fwd=_f(r.get("pe_fwd")),
        pe_lyr=_f(r.get("pe_lyr")),
        pb=_f(r.get("pb_ratio")),
        dividend_ttm=_f(r.get("dividend_ratio_ttm")),
        total_mcap=_f(r.get("total_market_cap")),
        float_mcap=_f(r.get("circulating_market_cap")),
        high_52w=_f(r.get("high_52week")),
        low_52w=_f(r.get("low_52week")),
        chg_5d=_f(r.get("chg_5d")),
        chg_10d=_f(r.get("chg_10d")),
        chg_20d=_f(r.get("chg_20d")),
        chg_60d=_f(r.get("chg_60d")),
        chg_ytd=_f(r.get("chg_ytd")),
        date=r.get("time", ""),
    )


def _to_ts_code(code: str) -> str | None:
    """westock 风格代码（sh688213）→ tushare 风格 ts_code（688213.SH）；非A股返回 None。"""
    m = re.fullmatch(r"(sh|sz|bj)(\d{6})", code, re.I)
    if not m:
        return None
    return f"{m.group(2)}.{m.group(1).upper()}"


def _iso_date(v: Any) -> str:
    """YYYYMMDD → YYYY-MM-DD（与 WeStock K线日期格式保持一致）。"""
    s = str(v or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def fetch_kline_from_db(code: str, limit: int = 250) -> list[Bar]:
    """从 stock_daily_t 读取最近 limit 根日K，前复权口径对齐 WeStock --fq qfq。

    单位换算（tushare 口径）：vol 手 → 股（×100）；amount 千元 → 元（×1000）。
    前复权：price × adj_factor / 最新 adj_factor。
    """
    if get_mysql_connection is None:
        raise DataError("module_mysql_connection 不可用")
    ts_code = _to_ts_code(code)
    conn = get_mysql_connection()
    if not conn:
        raise DataError("无法连接 MySQL（stock_daily_t）")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT trade_date, open, high, low, close, vol, amount,
                       pct_chg, qfq_adj_factor
                FROM stock_daily_t
                WHERE ts_code = %s
                ORDER BY trade_date DESC
                LIMIT %s
                """,
                (ts_code, int(limit)),
            )
            rows = cur.fetchall()
    finally:
        close_connection(conn)

    if not rows:
        raise DataError(f"stock_daily_t 中无 {ts_code} 日K数据")
    rows = list(reversed(rows))  # DESC 取出后还原为日期升序

    # 最新可用复权因子作为前复权基准
    latest_factor: float | None = None
    for r in reversed(rows):
        f = r.get("qfq_adj_factor")
        if f is not None:
            latest_factor = float(f)
            break

    bars: list[Bar] = []
    for r in rows:
        o, h, l, c = _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close"))
        if None in (o, h, l, c):
            continue
        factor = _f(r.get("qfq_adj_factor"))
        scale = factor / latest_factor if (factor and latest_factor) else 1.0
        vol = _f(r.get("vol"))
        vol = vol * 100.0 if vol else 0.0       # 手 → 股
        amt = _f(r.get("amount"))
        amt = amt * 1000.0 if amt else 0.0      # 千元 → 元
        bars.append(
            Bar(
                date=_iso_date(r.get("trade_date")),
                open=o * scale, close=c * scale, high=h * scale, low=l * scale,
                volume=vol, amount=amt,
                chg=_f(r.get("pct_chg")),
            )
        )
    if not bars:
        raise DataError(f"{ts_code} 日K数据无法解析")
    return bars


def fetch_market_cap_from_db(code: str, date_hint: str = "") -> dict[str, Any] | None:
    """从 stock_daily_basic_info_t 读取总市值/流通市值，返回元口径。

    date_hint 支持 YYYY-MM-DD / YYYYMMDD，取该日（含）之前最近一条记录。
    表中 total_mv/circ_mv 单位为万元，统一换算为元。
    """
    if get_mysql_connection is None:
        return None
    ts_code = _to_ts_code(code)
    d = re.sub(r"\D", "", date_hint or "")[:8]
    conn = get_mysql_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            if d:
                cur.execute(
                    """
                    SELECT trade_date, total_mv, circ_mv
                    FROM stock_daily_basic_info_t
                    WHERE ts_code = %s AND trade_date <= %s
                    ORDER BY trade_date DESC LIMIT 1
                    """,
                    (ts_code, d),
                )
            else:
                cur.execute(
                    """
                    SELECT trade_date, total_mv, circ_mv
                    FROM stock_daily_basic_info_t
                    WHERE ts_code = %s
                    ORDER BY trade_date DESC LIMIT 1
                    """,
                    (ts_code,),
                )
            r = cur.fetchone()
    finally:
        close_connection(conn)
    if not r:
        return None
    total_mv = _f(r.get("total_mv"))
    circ_mv = _f(r.get("circ_mv"))
    return {
        "trade_date": r.get("trade_date"),
        "total_mcap": total_mv * 1e4 if total_mv is not None else None,   # 万元 → 元
        "float_mcap": circ_mv * 1e4 if circ_mv is not None else None,
    }


def fetch_kline(
    code: str, period: str = "day", limit: int = 250, fq: str | None = "qfq"
) -> list[Bar]:
    # 日K 优先从本地 stock_daily_t 读取；周K/港股或数据库不可用时回退 WeStock CLI
    if period == "day" and _to_ts_code(code):
        try:
            return fetch_kline_from_db(code, limit)
        except DataError:
            if get_mysql_connection is None or shutil.which(CLI) is None:
                raise

    args = ["kline", code, "--period", period, "--limit", str(limit)]
    if fq and period in ("day", "week", "month"):
        args += ["--fq", fq]
    rows = _first_table(_run(args))
    # 成交量单位探测：科创板返回「股」，其余板块返回「手」。
    # 取成交量最大的那根做样本（噪声最小），用 amount / (volume × 均价) 反推。
    ratios = []
    for r in rows:
        v = _f(r.get("volume"))
        a = _f(r.get("amount"))
        h = _f(r.get("high"))
        l = _f(r.get("low"))
        c = _f(r.get("last") or r.get("close"))
        if v and a and h and l and c and v > 0:
            ratios.append(a / (v * (h + l + c) / 3))
    in_shares = True
    if ratios:
        med = sorted(ratios)[len(ratios) // 2]
        in_shares = med < 10

    bars: list[Bar] = []
    for r in rows:
        c = _f(r.get("last") or r.get("close"))
        o = _f(r.get("open"))
        h = _f(r.get("high"))
        low = _f(r.get("low"))
        if None in (o, c, h, low):
            continue
        vol = _f(r.get("volume")) or 0.0
        if vol and not in_shares:
            vol *= 100
        bars.append(
            Bar(
                date=r.get("date", ""),
                open=o, close=c, high=h, low=low,
                volume=vol,
                amount=_f(r.get("amount")) or 0.0,
                chg=_f(r.get("change_pct")),
            )
        )
    if not bars:
        raise DataError(f"{code} {period} K线为空")
    bars.sort(key=lambda b: b.date)
    return bars


def fetch_technical(code: str, limit: int = 90) -> list[dict[str, Any]]:
    """技术指标序列（含 MA/MACD/KDJ/RSI/BOLL）。"""
    rows = _first_table(_run(["technical", code, "--limit", str(limit)]))
    out: list[dict[str, Any]] = []
    for r in rows:
        rec: dict[str, Any] = {"date": r.get("date", "")}
        for k, v in r.items():
            if k == "date":
                continue
            rec[k.lower()] = _f(v)
        out.append(rec)
    out.sort(key=lambda x: x["date"])
    return out


def fetch_flow(code: str, start: str, end: str) -> list[FundFlowDay]:
    rows = _first_table(_run(["fund", "flow", code, "--start", start, "--end", end]))
    out = []
    for r in rows:
        out.append(
            FundFlowDay(
                date=r.get("date", ""),
                close=_f(r.get("ClosePrice")) or 0.0,
                main=_f(r.get("MainNetFlow")) or 0.0,
                jumbo=_f(r.get("JumboNetFlow")) or 0.0,
                big=_f(r.get("BlockNetFlow")) or 0.0,
                mid=_f(r.get("MidNetFlow")) or 0.0,
                small=_f(r.get("SmallNetFlow")) or 0.0,
            )
        )
    out.sort(key=lambda x: x.date)
    return out


def fetch_margin(code: str, start: str, end: str) -> list[MarginDay]:
    rows = _first_table(_run(["fund", "margin", code, "--start", start, "--end", end]))
    out = []
    for r in rows:
        out.append(
            MarginDay(
                date=r.get("date", ""),
                close=_f(r.get("closePrice")) or 0.0,
                finance_balance=_f(r.get("FinanceValue")) or 0.0,
                finance_net_buy=(
                    (_f(r.get("FinanceBuyValue")) or 0.0)
                    - (_f(r.get("FinanceRefundValue")) or 0.0)
                ),
                finance_dod=_f(r.get("FinanceValueDOD")),
                security_balance=_f(r.get("SecurityValue")),
            )
        )
    out.sort(key=lambda x: x.date)
    return out


def fetch_chip(code: str) -> dict[str, float]:
    rows = _first_table(_run(["chip", code]))
    r = rows[-1]
    return {
        "date": r.get("date", ""),
        "avg_cost": _f(r.get("chipAvgCost")) or 0.0,
        "conc70": _f(r.get("chipConcentration70")) or 0.0,
        "conc90": _f(r.get("chipConcentration90")) or 0.0,
        "profit_rate": _f(r.get("chipProfitRate")) or 0.0,
        "close": _f(r.get("closePrice")) or 0.0,
    }


def fetch_consensus(code: str) -> dict[str, Any]:
    text = _run(["consensus", code])
    rows = _first_table(text)
    target = None
    m = re.search(r"目标价:\s*([\d.]+)", text)
    if m:
        target = _f(m.group(1))
    years = []
    for r in rows:
        years.append(
            {
                "year": r.get("year", ""),
                "eps": _f(r.get("eps")),
                "revenue": _f(r.get("revenue")),
                "net_profit": _f(r.get("netProfit")),
                "pe": _f(r.get("pe")),
                "pb": _f(r.get("pb")),
                "revenue_yoy": _f(r.get("revenueYoy")),
                "profit_yoy": _f(r.get("netProfitYoy")),
                "institutions": _f(r.get("institutionCnt")),
            }
        )
    return {"target_price": target, "years": years}


def fetch_score(code: str) -> dict[str, float]:
    r = _pick(_first_table(_run(["score", code])), code)

    def g(k: str) -> float | None:
        v = _f(r.get(k))
        return v

    return {
        "cap": g("capScore"), "comp": g("compScore"), "fund": g("funmScore"),
        "risk": g("riskScore"), "tech": g("tecScore"),
    }


def fetch_profile(code: str) -> dict[str, Any]:
    r = _pick(_first_table(_run(["profile", code])), code)
    return {
        "name": r.get("name", ""),
        "industry": r.get("industry", ""),
        "sector": r.get("sector", ""),
        "listed": r.get("listedDate", ""),
        "chairman": r.get("chairman", ""),
        "business": r.get("business", ""),
    }


def fetch_reports(code: str, limit: int = 8) -> list[dict[str, Any]]:
    try:
        text = _run(["report", "list", code, "--limit", str(limit)])
    except DataError:
        return []
    tables = parse_tables(text)
    if not tables:
        return []
    return tables[0]["rows"]


def fetch_news(code: str, limit: int = 12) -> list[dict[str, Any]]:
    try:
        text = _run(["news", "list", code, "--limit", str(limit)])
    except DataError:
        return []
    tables = parse_tables(text)
    if not tables:
        return []
    return tables[0]["rows"]


# ---------------------------------------------------------------- 编排

def _daterange(days: int) -> tuple[str, str]:
    import datetime as _dt
    end = _dt.date.today()
    return (end - _dt.timedelta(days=days)).isoformat(), end.isoformat()


def load(code: str, *, days: int = 30, kline_bars: int = 250) -> Bundle:
    """一次性拉取全量数据。单点失败降级为记录，不中断整体流程。"""
    b = Bundle(code=code)

    for tag, fn in (
        ("行情", lambda: fetch_quote(code)),
        ("日K", lambda: fetch_kline(code, "day", kline_bars)),
        ("周K", lambda: fetch_kline(code, "week", 120)),
        ("技术指标", lambda: fetch_technical(code, 120)),
    ):
        try:
            v = fn()
            if tag == "行情":
                b.quote = v
            elif tag == "日K":
                b.bars = v
            elif tag == "周K":
                b.weekly = v
            else:
                b.tech = v
        except Exception as e:  # noqa: BLE001
            b.fail(tag, e)

    # 总市值/流通市值以本地 stock_daily_basic_info_t 为准（万元→元），覆盖行情接口快照
    if b.quote:
        try:
            mv = fetch_market_cap_from_db(code, b.quote.date)
            if mv:
                if mv["total_mcap"] is not None:
                    b.quote.total_mcap = mv["total_mcap"]
                if mv["float_mcap"] is not None:
                    b.quote.float_mcap = mv["float_mcap"]
                b.mcap_date = str(mv.get("trade_date") or "")
        except Exception as e:  # noqa: BLE001
            b.fail("总市值", e)

    s, e = _daterange(days)
    for tag, fn in (
        ("资金流向", lambda: fetch_flow(code, s, e)),
        ("融资融券", lambda: fetch_margin(code, s, e)),
        ("筹码分布", lambda: fetch_chip(code)),
        ("一致预期", lambda: fetch_consensus(code)),
        ("评分", lambda: fetch_score(code)),
        ("公司简况", lambda: fetch_profile(code)),
    ):
        try:
            v = fn()
            if tag == "资金流向":
                b.flows = v
            elif tag == "融资融券":
                b.margins = v
            elif tag == "筹码分布":
                b.chip = v
            elif tag == "一致预期":
                b.consensus = v
            elif tag == "评分":
                b.score = v
            else:
                b.profile = v
        except Exception as ex:  # noqa: BLE001
            b.fail(tag, ex)

    # 非核心数据，失败静默
    b.reports = fetch_reports(code)
    b.news = fetch_news(code)

    if not b.bars and b.quote and b.quote.date:
        b.notes.append("日K数据缺失，技术结构分析已跳过，仅输出行情与资金面。")
    return b