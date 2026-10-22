#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cffex_position.py —— 中金所股指期货（IF / IH / IC / IM）多空持仓变化抓取工具
================================================================================

数据来源
--------
唯一权威源：中国金融期货交易所（CFFEX）官网 www.cffex.com.cn，交易所盘后发布，
T 日结算完成后于当日 16:15 前后公布 T 日数据（非实时）。

两个官方 XML 接口（实测可用，无需 token、无需 API Key）：

1) 成交持仓排名（前 20 名结算会员）
   http://www.cffex.com.cn/sj/ccpm/{YYYYMM}/{DD}/{PRODUCT}.xml
   例：http://www.cffex.com.cn/sj/ccpm/202610/21/IF.xml

2) 每日行情（全市场合约级行情与持仓量）
   http://www.cffex.com.cn/sj/hqsj/rtj/{YYYYMM}/{DD}/index.xml
   例：http://www.cffex.com.cn/sj/hqsj/rtj/202610/21/index.xml

口径说明（务必阅读，避免误读）
----------------------------
* <datatypeid> 映射（已用官网网页数据逐条交叉校验，非推测）：

      datatypeid = 0  ->  成交量
      datatypeid = 1  ->  持买单量（多头持仓）
      datatypeid = 2  ->  持卖单量（空头持仓）

  校验样例（IF2611，2026-10-21 vs 2026-10-20）：
      持买 7843 + 723 = 8566  == 官网 10-20 持买单量 8566   ✔
      持卖 6676 + 602 = 7278  == 官网 10-20 持卖单量 7278   ✔
      成交 7450 + 2973 = 10423 == 官网 10-20 成交量 10423  ✔

* <varvolume> 是交易所直接给出的官方「比上交易日增减」字段。
  本工具一律采用官方字段做汇总，不用「当日 − 前日」自行相减，避免口径漂移。

* 「前 20 名会员」≠ 全市场。交易所只披露单边持仓 1 万手以上（含）的合约，
  及当月合约前 20 名结算会员。会员名为「XX期货(代客)」，期货公司当前无自营
  业务，披露的均为经纪业务(代客)数据，套保与投机混合。

* 净持仓 = 前 20 名持买单量合计 − 前 20 名持卖单量合计。
  同一客户可能在 A 会员持多、在 B 会员持空，因此该净持仓反映的是
  「席位集中度上的多空差」，不等于全市场净多空，更不等于看空观点
  （股指期货空单常用于对冲现货）。

* 持仓量（openinterest）为单边口径；每日行情中的 preopeninterest 为前一日持仓量，
  两者之差即当日全市场持仓量变化（合约级）。

用法
----
    # 1) 抓最近可用交易日的四大期指多空持仓汇总
    python cffex_position.py

    # 2) 指定日期 + 品种，打印会员明细
    python cffex_position.py --date 2026-10-21 --product IF --members

    # 3) 拉一段时间的主力合约多空变化时间序列
    python cffex_position.py --start 2026-10-01 --end 2026-10-21 --series

    # 4) 导出 Excel（多 sheet）
    python cffex_position.py --date 2026-10-21 --excel out.xlsx

    # 5) 口径自检：验证官方「比上交易日增减」与逐日持仓量自洽
    python cffex_position.py --verify --product IF

依赖：requests、pandas（导出 Excel 另需 openpyxl）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import requests
import numpy as np

# --------------------------------------------------------------------------- #
# 常量配置
# --------------------------------------------------------------------------- #

CFFEX_BASE = os.environ.get("CFFEX_BASE_URL", "http://www.cffex.com.cn").rstrip("/")
CACHE_DIR = os.environ.get(
    "CFFEX_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".cache", "cffex_position")
)

#: 股指期货品种 -> 中文名
PRODUCTS: Dict[str, str] = {
    "IF": "沪深300股指期货",
    "IH": "上证50股指期货",
    "IC": "中证500股指期货",
    "IM": "中证1000股指期货",
}

#: datatypeid -> 官方口径名称（已交叉校验）
DATATYPE_NAMES: Dict[int, str] = {
    0: "成交量",
    1: "持买单量(多头)",
    2: "持卖单量(空头)",
}

#: 排名表中的数据类别
DT_VOLUME, DT_LONG, DT_SHORT = 0, 1, 2

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# 中文列名映射（导出时使用）
COL_CN = {
    "trading_day": "交易日",
    "product": "品种",
    "product_cn": "品种名称",
    "instrument": "合约",
    "datatype": "数据类别",
    "datatype_name": "口径",
    "rank": "名次",
    "member": "会员简称",
    "volume": "持仓量/成交量(手)",
    "chg_vs_prev": "比上交易日增减(手)",
    "long_total": "多头持仓合计(手)",
    "long_chg": "多头增减合计(手)",
    "short_total": "空头持仓合计(手)",
    "short_chg": "空头增减合计(手)",
    "net_position": "净持仓(多-空,手)",
    "net_chg": "净持仓变化(手)",
    "long_ratio": "多头占比",
    "volume_total": "成交量合计(手)",
    "volume_chg": "成交量增减(手)",
    "net_chg_5d": "净持仓5日变化(手)",
    "net_ratio": "净持仓占双边比",
    "ls_score": "多空强弱评分",
    "net_streak": "净持仓同向连续天数",
    "oi": "持仓量(手)",
    "oi_prev": "前日持仓量(手)",
    "oi_chg": "持仓量变化(手)",
    "close": "收盘价",
    "settlement": "结算价",
    "turnover": "成交额(元)",
}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def normalize_date(value: str) -> str:
    """
    把多种日期写法统一成 YYYYMMDD。

    支持：20261022 / 2026-10-22 / 2026/10/22 / 2026.10.22
    """
    text = str(value).strip()
    for sep in ("-", "/", "."):
        text = text.replace(sep, "")
    if len(text) != 8 or not text.isdigit():
        raise CffexError(f"日期格式无法识别：{value}（请用 YYYYMMDD 或 YYYY-MM-DD）")
    # 简易合法性校验
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise CffexError(f"非法日期：{value}") from exc
    return text


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #


class CffexError(Exception):
    """基础异常"""


class NoDataError(CffexError):
    """该日期为非交易日，或数据尚未发布（交易所返回 302 → 404 错误页）"""


# --------------------------------------------------------------------------- #
# HTTP 客户端：禁用重定向 + 本地缓存
# --------------------------------------------------------------------------- #


class _NoRedirectSession:
    """会话封装：禁用重定向（交易所对非交易日/未发布日期会 302 到 200 的错误页）。"""

    def __init__(self, timeout: int = 30, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA, "Accept": "*/*"})

    def get(self, url: str) -> requests.Response:
        last_err: Optional[Exception] = None
        for attempt in range(self.retries):
            try:
                return self._session.get(url, timeout=self.timeout, allow_redirects=False)
            except requests.RequestException as exc:  # 网络抖动重试
                last_err = exc
                if attempt < self.retries - 1:
                    time.sleep(1.2 * (attempt + 1))
        raise CffexError(f"请求失败（重试 {self.retries} 次）: {url} | {last_err}")

    def close(self) -> None:
        self._session.close()


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class RankRow:
    """成交持仓排名表中的一行。"""

    trading_day: str
    product: str
    instrument: str
    datatype: int
    rank: int
    member: str
    volume: int
    chg_vs_prev: int
    party_id: str

    @property
    def datatype_name(self) -> str:
        return DATATYPE_NAMES.get(self.datatype, f"未知({self.datatype})")


# --------------------------------------------------------------------------- #
# 抓取器
# --------------------------------------------------------------------------- #


class CffexClient:
    """中金所数据抓取客户端。"""

    def __init__(self, cache_dir: str = CACHE_DIR, use_cache: bool = True, verbose: bool = False):
        self.cache_dir = os.path.abspath(os.path.expanduser(cache_dir))
        self.use_cache = use_cache
        self.verbose = verbose
        self.http = _NoRedirectSession()
        os.makedirs(self.cache_dir, exist_ok=True)

    # ---------------------------- 缓存 ---------------------------- #

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, key)

    def _read_cache(self, key: str) -> Optional[bytes]:
        path = self._cache_path(key)
        if self.use_cache and os.path.exists(path):
            with open(path, "rb") as fh:
                return fh.read()
        return None

    def _write_cache(self, key: str, payload: bytes) -> None:
        path = self._cache_path(key)
        try:
            with open(path, "wb") as fh:
                fh.write(payload)
        except OSError:
            pass  # 缓存失败不影响主流程

    # ---------------------------- 底层抓取 ---------------------------- #

    def _fetch_xml(self, url: str, cache_key: str) -> bytes:
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        resp = self.http.get(url)
        if resp.status_code in (301, 302, 303, 307, 308):
            # 中金所对「非交易日 / 数据未发布」返回 302 → 错误页，禁用重定向后可准确识别
            raise NoDataError(f"无数据（302）：{url}")
        if resp.status_code == 404:
            raise NoDataError(f"无数据（404）：{url}")
        if resp.status_code != 200:
            raise CffexError(f"HTTP {resp.status_code}：{url}")

        body = resp.content
        if not body.lstrip().startswith(b"<?xml") and b"<positionRank" not in body[:400]:
            raise NoDataError(f"返回内容非排名 XML（可能为错误页）：{url}")

        self._write_cache(cache_key, body)
        return body

    # ---------------------------- 公开接口 ---------------------------- #

    def fetch_rank(self, product: str, day: str) -> List[RankRow]:
        """
        抓取某品种某交易日的成交持仓排名（前 20 名会员）。

        :param product: IF / IH / IC / IM
        :param day: YYYYMMDD
        """
        product = product.upper()
        if product not in PRODUCTS:
            raise CffexError(f"不支持的品种 {product}，可选：{', '.join(PRODUCTS)}")

        day = normalize_date(day)
        y, m, d = day[:4], day[4:6], day[6:8]
        url = f"{CFFEX_BASE}/sj/ccpm/{y}{m}/{d}/{product}.xml"
        body = self._fetch_xml(url, f"rank_{product}_{day}.xml")
        return self._parse_rank(body, day)

    @staticmethod
    def _parse_rank(body: bytes, day: str) -> List[RankRow]:
        root = ET.fromstring(body)
        rows: List[RankRow] = []
        for node in root.findall("data"):
            get = node.findtext

            def as_int(value: Optional[str], default: int = 0) -> int:
                if value is None or str(value).strip() == "":
                    return default
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    return default

            rows.append(
                RankRow(
                    trading_day=(get("tradingday") or day).strip(),
                    product=(get("productid") or "").strip(),
                    instrument=(get("instrumentid") or "").strip(),
                    datatype=as_int(get("datatypeid"), -1),
                    rank=as_int(get("rank")),
                    member=(get("shortname") or "").strip(),
                    volume=as_int(get("volume")),
                    chg_vs_prev=as_int(get("varvolume")),
                    party_id=(get("partyid") or "").strip(),
                )
            )
        return rows

    def fetch_daily_quote(self, day: str) -> pd.DataFrame:
        """
        抓取某交易日全部品种的每日行情（含全市场持仓量与持仓量变化）。

        :param day: YYYYMMDD
        """
        day = normalize_date(day)
        y, m, d = day[:4], day[4:6], day[6:8]
        url = f"{CFFEX_BASE}/sj/hqsj/rtj/{y}{m}/{d}/index.xml"
        body = self._fetch_xml(url, f"daily_{day}.xml")

        root = ET.fromstring(body)
        records = []
        for node in root.findall("dailydata"):
            get = node.findtext

            def as_float(value: Optional[str]) -> Optional[float]:
                if value is None or str(value).strip() == "":
                    return None
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            records.append(
                {
                    "trading_day": (get("tradingday") or day).strip(),
                    "instrument": (get("instrumentid") or "").strip(),
                    "product": (get("productid") or "").strip(),
                    "open": as_float(get("openprice")),
                    "high": as_float(get("highestprice")),
                    "low": as_float(get("lowestprice")),
                    "close": as_float(get("closeprice")),
                    "settlement": as_float(get("settlementprice")),
                    "pre_settlement": as_float(get("presettlementprice")),
                    "oi_prev": as_float(get("preopeninterest")),
                    "oi": as_float(get("openinterest")),
                    "volume": as_float(get("volume")),
                    "turnover": as_float(get("turnover")),
                }
            )

        df = pd.DataFrame(records)
        if df.empty:
            return df
        df["oi_chg"] = df["oi"] - df["oi_prev"]
        return df

    def latest_available_date(self, product: str = "IF", lookback_days: int = 15) -> str:
        """从今天往前回溯，找到最近一个「排名数据已发布」��交易日。"""
        today = datetime.now()
        for offset in range(lookback_days):
            candidate = (today - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                self.fetch_rank(product, candidate)
                return candidate
            except NoDataError:
                continue
        raise CffexError(f"最近 {lookback_days} 天内未找到 {product} 的已发布持仓排名数据")

    def trading_days(self, start: str, end: str, product: str = "IF") -> List[str]:
        """列出 [start, end] 区间内实际有数据发布的交易日（升序）。"""
        start = normalize_date(start)
        end = normalize_date(end)
        s = datetime.strptime(start, "%Y%m%d")
        e = datetime.strptime(end, "%Y%m%d")
        if e < s:
            raise CffexError("end 不能早于 start")
        days: List[str] = []
        cur = s
        while cur <= e:
            key = cur.strftime("%Y%m%d")
            try:
                self.fetch_rank(product, key)
                days.append(key)
            except NoDataError:
                pass
            cur += timedelta(days=1)
        return days

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "CffexClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# 汇总计算
# --------------------------------------------------------------------------- #


def rank_to_frame(rows: Sequence[RankRow]) -> pd.DataFrame:
    """RankRow 列表 -> DataFrame。"""
    if not rows:
        return pd.DataFrame(
            columns=[
                "trading_day", "product", "instrument", "datatype", "datatype_name",
                "rank", "member", "volume", "chg_vs_prev", "party_id",
            ]
        )
    df = pd.DataFrame([r.__dict__ for r in rows])
    df["datatype_name"] = df["datatype"].map(DATATYPE_NAMES).fillna(df["datatype"].astype(str))
    df["product_cn"] = df["product"].map(PRODUCTS).fillna("")
    return df


def summarize_positions(rank_df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """
    汇总每个合约的多空持仓与变化。

    多空增减合计直接取官方 varvolume 求和，不用「当日−前日」自行相减。
    """
    if rank_df.empty:
        return pd.DataFrame()

    df = rank_df[rank_df["rank"] <= top_n].copy()

    def _sum(sub: pd.DataFrame, col: str) -> int:
        if sub.empty:
            return 0
        return int(sub[col].sum())

    out: List[Dict] = []
    for (day, product, instrument), sub in df.groupby(
        ["trading_day", "product", "instrument"], sort=True
    ):
        longs = sub[sub["datatype"] == DT_LONG]
        shorts = sub[sub["datatype"] == DT_SHORT]
        vols = sub[sub["datatype"] == DT_VOLUME]

        long_total = _sum(longs, "volume")
        long_chg = _sum(longs, "chg_vs_prev")
        short_total = _sum(shorts, "volume")
        short_chg = _sum(shorts, "chg_vs_prev")
        gross = long_total + short_total

        out.append(
            {
                "trading_day": day,
                "product": product,
                "product_cn": PRODUCTS.get(product, ""),
                "instrument": instrument,
                "top_n": int(top_n),
                "long_total": long_total,
                "long_chg": long_chg,
                "short_total": short_total,
                "short_chg": short_chg,
                "net_position": long_total - short_total,
                "net_chg": long_chg - short_chg,
                "long_ratio": round(long_total / gross, 4) if gross else None,
                "volume_total": _sum(vols, "volume"),
                "volume_chg": _sum(vols, "chg_vs_prev"),
            }
        )

    return pd.DataFrame(out)


def build_series(
    client: CffexClient,
    start: str,
    end: str,
    products: Sequence[str] = ("IF", "IH", "IC", "IM"),
    top_n: int = 20,
) -> pd.DataFrame:
    """
    构建主力合约（按前 20 名会员成交量合计最大）的多空持仓变化时间序列。
    """
    all_sum: List[pd.DataFrame] = []
    for product in products:
        try:
            days = client.trading_days(start, end, product=product)
        except CffexError:
            days = []
        for day in days:
            try:
                rows = client.fetch_rank(product, day)
            except NoDataError:
                continue
            summ = summarize_positions(rank_to_frame(rows), top_n=top_n)
            if summ.empty:
                continue
            # 主力合约：成交量合计最大者
            main = summ.sort_values("volume_total", ascending=False).iloc[0].copy()
            main["is_main"] = True
            all_sum.append(main.to_frame().T)

    if not all_sum:
        return pd.DataFrame()

    series = pd.concat(all_sum, ignore_index=True)
    for col in ("long_total", "long_chg", "short_total", "short_chg",
                "net_position", "net_chg", "volume_total", "volume_chg"):
        series[col] = pd.to_numeric(series[col], errors="coerce").astype("Int64")
    series["long_ratio"] = pd.to_numeric(series["long_ratio"], errors="coerce")
    series = series.sort_values(["product", "trading_day"]).reset_index(drop=True)

    # ---- 衍生情绪指标：仅按品种在时间轴上计算，避免跨品种串扰 ----
    frames = []
    for product, grp in series.groupby("product", sort=False):
        g = grp.copy()
        net = g["net_position"].astype("float")
        net_chg = g["net_chg"].astype("float")

        # 净持仓 5 日变化（衡量多空力量边际转向速度）
        g["net_chg_5d"] = net.diff(5).round(0)
        # 净持仓占双边持仓比（-1 ~ +1，正为偏多）
        gross = (g["long_total"].astype("float") + g["short_total"].astype("float"))
        g["net_ratio"] = (net / gross.replace(0, pd.NA)).astype("float")
        # 多空强弱评分：多头增减相对空头增减的强度，[-1, 1]
        abs_chg = (g["long_chg"].astype("float").abs() + g["short_chg"].astype("float").abs())
        g["ls_score"] = (
            (g["long_chg"].astype("float") - g["short_chg"].astype("float"))
            / abs_chg.replace(0, pd.NA)
        ).astype("float")
        # 连续同向天数（净持仓增加为正方向）
        sign = np.sign(net_chg.fillna(0))
        streak = []
        cur = 0
        prev_sign = 0
        for s in sign:
            if s == 0:
                cur = 0
            elif s == prev_sign:
                cur += 1
            else:
                cur = 1
            prev_sign = s
            streak.append(cur)
        g["net_streak"] = streak
        frames.append(g)

    series = pd.concat(frames, ignore_index=True)
    return series.sort_values(["product", "trading_day"]).reset_index(drop=True)


def verify_varvolume(
    client: CffexClient, product: str = "IF", days: int = 5
) -> pd.DataFrame:
    """
    口径自检：逐日核对「官方比上交易日增减」是否等于「当日值 − 前日值」。

    这同时验证了 datatypeid 映射是否正确（多头/空头/成交量各跑一遍）。
    """
    end = client.latest_available_date(product)
    end_dt = datetime.strptime(end, "%Y%m%d")
    candidates: List[str] = []
    cur = end_dt
    while len(candidates) < days and (end_dt - cur).days < 20:
        try:
            client.fetch_rank(product, cur.strftime("%Y%m%d"))
            candidates.append(cur.strftime("%Y%m%d"))
        except NoDataError:
            pass
        cur -= timedelta(days=1)
    candidates.sort()

    results: List[Dict] = []
    for prev_day, cur_day in zip(candidates[:-1], candidates[1:]):
        prev = rank_to_frame(client.fetch_rank(product, prev_day))
        cur = rank_to_frame(client.fetch_rank(product, cur_day))
        key = ["instrument", "datatype", "member"]
        merged = cur.merge(prev, on=key, suffixes=("_cur", "_prev"), how="inner")
        if merged.empty:
            continue
        merged["diff"] = merged["volume_cur"] - merged["volume_prev"]
        merged["mismatch"] = merged["diff"] != merged["chg_vs_prev_cur"]
        for dt, grp in merged.groupby("datatype"):
            results.append(
                {
                    "品种": product,
                    "口径": DATATYPE_NAMES.get(int(dt), str(dt)),
                    "对比日": f"{prev_day} → {cur_day}",
                    "样本数": int(len(grp)),
                    "不一致数": int(grp["mismatch"].sum()),
                    "通过率": round(1 - grp["mismatch"].mean(), 4),
                }
            )
    return pd.DataFrame(results)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def _to_cn(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={k: v for k, v in COL_CN.items() if k in df.columns})


def print_summary(summary: pd.DataFrame, title: str = "多空持仓汇总（前 20 名会员）") -> None:
    if summary.empty:
        print("（无数据）")
        return
    print(f"\n===== {title} =====")
    cols = [
        "trading_day", "product", "instrument", "long_total", "long_chg",
        "short_total", "short_chg", "net_position", "net_chg", "long_ratio",
    ]
    show = summary[[c for c in cols if c in summary.columns]].copy()
    for c in ("long_ratio",):
        if c in show.columns:
            show[c] = show[c].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
    widths = {
        "trading_day": 10, "product": 6, "instrument": 9, "long_total": 10,
        "long_chg": 10, "short_total": 10, "short_chg": 10, "net_position": 10,
        "net_chg": 10, "long_ratio": 8,
    }
    header = "".join(
        f"{c:<{widths.get(c, 12)}}" for c in show.columns
    )
    print(header)
    print("-" * len(header))
    for _, row in show.iterrows():
        print(
            "".join(
                f"{str(row[c]):<{widths.get(c, 12)}}" for c in show.columns
            )
        )


def print_members(rank_df: pd.DataFrame, product: str, instrument: Optional[str] = None) -> None:
    if rank_df.empty:
        print("（无数据）")
        return
    df = rank_df[rank_df["product"] == product]
    if instrument:
        df = df[df["instrument"] == instrument]
    for dt in (DT_LONG, DT_SHORT):
        sub = df[df["datatype"] == dt].sort_values(["instrument", "rank"])
        if sub.empty:
            continue
        print(f"\n----- {DATATYPE_NAMES[dt]} -----")
        for inst, g in sub.groupby("instrument"):
            print(f"[{inst}]")
            g = g.sort_values("rank")
            for _, r in g.iterrows():
                print(
                    f"  {int(r['rank']):>2}. {r['member']:<14}"
                    f" 持仓 {int(r['volume']):>8} 手   比上日 {int(r['chg_vs_prev']):>+8} 手"
                )
            print(
                f"  合计  {'':14} 持仓 {int(g['volume'].sum()):>8} 手"
                f"   比上日 {int(g['chg_vs_prev'].sum()):>+8} 手"
            )


def export_excel(
    path: str,
    summary: pd.DataFrame,
    rank_df: pd.DataFrame,
    daily: Optional[pd.DataFrame] = None,
    series: Optional[pd.DataFrame] = None,
    products: Optional[Sequence[str]] = None,
) -> str:
    """导出多 sheet Excel。"""
    path = os.path.abspath(path)
    if path.lower().endswith(".csv"):
        summary.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if summary is not None and not summary.empty:
            _to_cn(summary).to_excel(writer, sheet_name="多空持仓汇总", index=False)
        if rank_df is not None and not rank_df.empty:
            _to_cn(rank_df).to_excel(writer, sheet_name="会员持仓明细", index=False)
        if daily is not None and not daily.empty:
            d = daily.copy()
            # 只保留股指期货品种，过滤掉每日行情里的期权合约
            if products:
                d = d[d["product"].isin(list(products))]
            d = d.drop(columns=["product_cn"], errors="ignore")
            if "oi_chg" in d.columns:
                d["oi_chg"] = d["oi_chg"].astype("Int64")
            d = d.rename(columns={
                "trading_day": "交易日", "instrument": "合约", "product": "品种",
                "open": "开盘价", "high": "最高价", "low": "最低价",
                "close": "收盘价", "settlement": "结算价", "pre_settlement": "前结算价",
                "oi": COL_CN["oi"], "oi_prev": COL_CN["oi_prev"], "oi_chg": COL_CN["oi_chg"],
                "volume": "成交量(手)", "turnover": COL_CN["turnover"],
            })
            d.to_excel(writer, sheet_name="每日行情与持仓量", index=False)
        if series is not None and not series.empty:
            _to_cn(series).to_excel(writer, sheet_name="主力合约时间序列", index=False)
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="中金所股指期货（IF/IH/IC/IM）多空持仓变化抓取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python cffex_position.py\n"
            "  python cffex_position.py --date 2026-10-21 --product IF --members\n"
            "  python cffex_position.py --start 2026-10-01 --end 2026-10-21 --series\n"
            "  python cffex_position.py --date 2026-10-21 --excel out.xlsx\n"
        ),
    )
    p.add_argument("--date", "-d", help="交易日 YYYYMMDD，默认取最近已发布日")
    p.add_argument("--start", help="序列起始日 YYYYMMDD")
    p.add_argument("--end", help="序列结束日 YYYYMMDD")
    p.add_argument(
        "--product", "-p", default="all",
        help="品种 IF/IH/IC/IM 或 all（默认 all）",
    )
    p.add_argument("--top", type=int, default=20, help="取前 N 名会员合计，默认 20")
    p.add_argument("--members", action="store_true", help="打印会员级明细")
    p.add_argument("--instrument", help="只打印指定合约，如 IF2611")
    p.add_argument("--series", action="store_true", help="输出主力合约时间序列")
    p.add_argument("--verify", action="store_true", help="口径自检（校验 varvolume 与逐日差值自洽）")
    p.add_argument("--excel", metavar="PATH", help="导出 Excel/CSV 到指定路径")
    p.add_argument("--no-cache", action="store_true", help="不使用本地缓存")
    p.add_argument("--cache-dir", help=f"缓存目录，默认 {CACHE_DIR}")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argparser().parse_args(argv)

    client = CffexClient(
        cache_dir=args.cache_dir or CACHE_DIR,
        use_cache=not args.no_cache,
    )

    try:
        # ---------- 自检模式 ----------
        if args.verify:
            products = [args.product.upper()] if args.product != "all" else list(PRODUCTS)
            frames = []
            for prod in products:
                df = verify_varvolume(client, product=prod, days=5)
                if not df.empty:
                    frames.append(df)
            result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            print("\n===== 口径自检：官方「比上交易日增减」 vs 逐日差值 =====")
            if result.empty:
                print("（数据不足，无法校验）")
            else:
                print(result.to_string(index=False))
                total_mismatch = int(result["不一致数"].sum())
                print(
                    f"\n结论：累计不一致 {total_mismatch} 条 —— "
                    + ("通过，datatypeid 映射与口径理解正确。"
                       if total_mismatch == 0 else "存在不一致，请复核映射。")
                )
            return 0

        # ---------- 序列模式 ----------
        if args.series:
            start = normalize_date(
                args.start or (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            )
            end = normalize_date(args.end or datetime.now().strftime("%Y%m%d"))
            products = list(PRODUCTS) if args.product == "all" else [args.product.upper()]
            series = build_series(client, start, end, products=products, top_n=args.top)
            if series.empty:
                print("区间内无可用数据")
                return 1
            print(f"\n===== 主力合约多空持仓变化序列 {start} ~ {end} =====")
            cols = [
                "trading_day", "product", "instrument", "long_total", "long_chg",
                "short_total", "short_chg", "net_position", "net_chg",
                "net_chg_5d", "net_ratio", "ls_score", "net_streak",
            ]
            show = series[[c for c in cols if c in series.columns]].copy()
            for c in ("long_ratio",):
                if c in show.columns:
                    show[c] = show[c].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
            for c in ("net_ratio", "ls_score"):
                if c in show.columns:
                    show[c] = show[c].map(lambda v: f"{v:+.3f}" if pd.notna(v) else "-")
            for prod, grp in show.groupby("product"):
                print(f"\n--- {prod} {PRODUCTS.get(prod, '')} ---")
                print(grp.to_string(index=False))
            print(
                "\n注：net_ratio=净持仓/双边持仓（正偏多）；ls_score=(多头增减-空头增减)/增减绝对值之和（+1全增多头，-1全增多空）；"
                "net_streak=净持仓同向连续变动天数。均为前20名会员合计口径。"
            )
            if args.excel:
                path = export_excel(args.excel, series, pd.DataFrame(), None, series, products)
                print(f"\n已导出：{path}")
            return 0

        # ---------- 单日模式 ----------
        if args.date:
            day = normalize_date(args.date)
        else:
            day = client.latest_available_date()
            print(f"（未指定日期，使用最近已发布交易日：{day}）")

        products = list(PRODUCTS) if args.product == "all" else [args.product.upper()]

        all_rows: List[RankRow] = []
        summaries: List[pd.DataFrame] = []
        for prod in products:
            try:
                rows = client.fetch_rank(prod, day)
            except NoDataError:
                print(f"[{prod}] {day} 无数据（非交易日或尚未发布）")
                continue
            all_rows.extend(rows)
            summ = summarize_positions(rank_to_frame(rows), top_n=args.top)
            if not summ.empty:
                summaries.append(summ)

        rank_df = rank_to_frame(all_rows)
        summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()

        if summary.empty:
            print("未取得任何数据")
            return 1

        print_summary(summary, f"中金所股指期货多空持仓 {day}（前 {args.top} 名会员）")
        print(
            "\n注：净持仓 = 前20名持买单量合计 − 持卖单量合计；"
            "增减为交易所官方「比上交易日增减」字段合计；数据为交易所盘后发布，非实时。"
        )

        if args.members:
            for prod in products:
                if prod in set(rank_df["product"]):
                    print_members(rank_df, prod, args.instrument)

        daily = None
        try:
            daily = client.fetch_daily_quote(day)
            if not daily.empty:
                d = daily[daily["product"].isin(products)].copy()
                for col in ("oi", "oi_prev", "oi_chg"):
                    if col in d.columns:
                        d[col] = d[col].astype("Int64")
                print(f"\n===== 全市场持仓量变化（合约级，{day}）=====")
                show = d[["trading_day", "product", "instrument", "oi", "oi_prev", "oi_chg"]]
                print(show.to_string(index=False))
        except (NoDataError, CffexError) as exc:
            print(f"（每日行情未取得：{exc}）")

        if args.excel:
            path = export_excel(args.excel, summary, rank_df, daily, None, products)
            print(f"\n已导出：{path}")

        return 0

    except CffexError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
