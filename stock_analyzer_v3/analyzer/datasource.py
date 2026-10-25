"""数据源层：所有取数逻辑集中在此。

统一策略
--------
1. 直连优先（trust_env=False），失败后自动回落到系统代理，兼容有/无代理环境。
2. 指数退避重试 3 次，失败返回 None，由上层模块优雅降级（不中断整份报告）。
3. 全部结果走磁盘缓存，TTL 见 config.CACHE_TTL。

数据源实测可用性（2026-10 校验）
------------------------------
可用：腾讯财经(qt.gtimg.cn / web.ifzq.gtimg.cn)、新浪(pandas_datareader 系 财务/K线)、
      同花顺(行业成分/资金流/板块指数)、东财 datacenter-web & emweb & 公告接口。
不可用：东财 push2* 系列（实时盘口、板块成分、个股资金流东财版）——已绕开。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from typing import Any

import pandas as pd
import requests

from .config import CACHE_TTL, PRODUCT_FUTURES
from .utils import CACHE, fmt_num, market_prefix, to_num, to_secid

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_DIRECT = requests.Session()
_DIRECT.trust_env = False          # 绕过本机代理，直连更快
_PROXY = requests.Session()        # 直连失败时的回落通道


def _http(url: str, *, params: dict | None = None, headers: dict | None = None,
          encoding: str | None = None, retries: int = 3, timeout: int = 15) -> str | None:
    """带重试与双通道回落的 GET，返回文本。"""
    hdr = {**UA, **(headers or {})}
    last_err = None
    for attempt in range(retries):
        for sess in (_DIRECT, _PROXY):
            try:
                r = sess.get(url, params=params, headers=hdr, timeout=timeout)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                if encoding:
                    r.encoding = encoding
                return r.text
            except Exception as e:      # noqa: BLE001 网络异常统一降级
                last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.4 * (attempt + 1))
    return None


def _http_json(url: str, **kw) -> Any | None:
    txt = _http(url, **kw)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None


def _ak():
    """延迟导入 akshare（首次导入约 1-2 秒）。"""
    import akshare as ak
    return ak


# ==================================================================== 1. 行情
def realtime_quotes(codes: list[str]) -> pd.DataFrame:
    """腾讯批量实时行情。60 只/批单次请求，返回含价格、涨跌、PE/PB、市值、量比。

    字段位说明（腾讯 88 段格式）：3现价 4昨收 5今开 6成交量(手) 31涨跌 32涨跌幅
    33最高 34最低 37成交额(万) 38换手率 39PE(TTM) 43振幅 44流通市值(亿) 45总市值(亿)
    46PB 47涨停价 48跌停价 49量比
    """
    codes = [str(c).zfill(6) for c in codes]
    out: list[dict] = []
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        q = ",".join(to_secid(c) for c in chunk)
        txt = _http(f"https://qt.gtimg.cn/q={q}", encoding="gbk")
        if not txt:
            continue
        for line in txt.strip().split("\n"):
            if "=" not in line:
                continue
            f = line.split("=", 1)[1].strip('";\n').split("~")
            if len(f) < 50:
                continue
            out.append({
                "code": f[2],
                "name": f[1].replace(" ", ""),
                "price": to_num(f[3]),
                "prev_close": to_num(f[4]),
                "open": to_num(f[5]),
                "volume_hand": to_num(f[6]),
                "change": to_num(f[31]),
                "change_pct": to_num(f[32]),
                "high": to_num(f[33]),
                "low": to_num(f[34]),
                "amount_wan": to_num(f[37]),
                "turnover": to_num(f[38]),
                "pe_ttm": to_num(f[39]),
                "amplitude": to_num(f[43]),
                "float_mv": (to_num(f[44]) or 0) * 1e8,
                "total_mv": (to_num(f[45]) or 0) * 1e8,
                "pb": to_num(f[46]),
                "limit_up": to_num(f[47]),
                "limit_down": to_num(f[48]),
                "vol_ratio": to_num(f[49]),
            })
    return pd.DataFrame(out)


def quote(code: str) -> dict:
    df = realtime_quotes([code])
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def kline_daily(code: str, days: int = 420, adjust: str = "qfq") -> pd.DataFrame:
    """日线。优先新浪（字段规范、含换手率），失败回落腾讯。

    返回列：date open high low close volume amount turnover
    """
    ck = f"kline::{code}::{days}::{adjust}"
    hit = CACHE.get(ck, CACHE_TTL["kline"])
    if hit is not None:
        return hit

    df = _kline_sina(code, adjust)
    if df is None or df.empty:
        df = _kline_tx(code, adjust)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.dropna(subset=["close"]).sort_values("date").tail(days).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


def _kline_sina(code: str, adjust: str) -> pd.DataFrame | None:
    today = dt.date.today()
    start = (today - dt.timedelta(days=900)).strftime("%Y%m%d")
    sym = to_secid(code)
    try:
        df = _ak().stock_zh_a_daily(symbol=sym, start_date=start,
                                    end_date=today.strftime("%Y%m%d"), adjust=adjust)
    except Exception:      # noqa: BLE001
        return None
    if df is None or df.empty:
        return None
    df = df.rename(columns={"date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
    for c in keep:
        if c not in df.columns:
            df[c] = None
    if "turnover" not in df.columns:
        df["turnover"] = None
    # 新浪 turnover 为小数（0.0277 = 2.77%），统一换成百分数
    df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce") * 100
    return df[keep + ["turnover"]]


def _kline_tx(code: str, adjust: str) -> pd.DataFrame | None:
    today = dt.date.today()
    start = (today - dt.timedelta(days=900)).strftime("%Y-%m-%d")
    param = f"{to_secid(code)},day,{start},{today:%Y-%m-%d},640,{adjust}"
    js = _http_json(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}")
    if not js:
        return None
    sym = to_secid(code)
    node = (js.get("data") or {}).get(sym) or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        return None
    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = df["volume"] * 100      # 手 -> 股
    df["amount"] = None
    df["turnover"] = None
    return df


# ==================================================================== 2. 基本面
def company_survey(code: str) -> dict:
    """东财 F10 公司概况：全称、EM2016 三级行业、证监会行业、主营、员工数、上市日期。"""
    ck = f"survey::{code}"
    hit = CACHE.get(ck, CACHE_TTL["survey"])
    if hit is not None:
        return hit
    sym = to_secid(code).upper()
    js = _http_json(f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={sym}")
    out: dict = {}
    if js and js.get("jbzl"):
        j = js["jbzl"][0]
        out = {
            "code": j.get("SECURITY_CODE"),
            "name": j.get("SECURITY_NAME_ABBR"),
            "full_name": j.get("ORG_NAME"),
            "em2016": j.get("EM2016") or "",
            "csrc_industry": j.get("INDUSTRYCSRC1") or "",
            "market": j.get("TRADE_MARKET") or "",
            "profile": (j.get("ORG_PROFILE") or "")[:600],
            "business_scope": (j.get("BUSINESS_SCOPE") or "")[:400],
            "employees": to_num(j.get("EMP_NUM")),
            "chairman": j.get("CHAIRMAN"),
            "website": j.get("ORG_WEB"),
            "region": j.get("PROVINCE"),
        }
    if js and js.get("fxxg"):
        out["list_date"] = (js["fxxg"][0] or {}).get("LISTING_DATE")
    CACHE.set(ck, out)
    return out


def financial_abstract(code: str) -> pd.DataFrame:
    """新浪财务摘要（多报告期，行=指标，列=报告期）。"""
    ck = f"fin_abs::{code}"
    hit = CACHE.get(ck, CACHE_TTL["financial"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_financial_abstract(symbol=str(code).zfill(6))
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    CACHE.set(ck, df)
    return df


def financial_indicators(code: str, start_year: str) -> pd.DataFrame:
    """新浪财务分析指标（86 项，含 ROE/周转/现金流质量）。"""
    ck = f"fin_ind::{code}::{start_year}"
    hit = CACHE.get(ck, CACHE_TTL["financial"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_financial_analysis_indicator(symbol=str(code).zfill(6), start_year=start_year)
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    CACHE.set(ck, df)
    return df


def main_business(code: str) -> pd.DataFrame:
    """东财主营构成（按产品/按地区，含毛利率）。"""
    ck = f"zygc::{code}"
    hit = CACHE.get(ck, CACHE_TTL["financial"])
    if hit is not None:
        return hit
    sym = to_secid(code).upper()
    try:
        df = _ak().stock_zygc_em(symbol=sym)
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    CACHE.set(ck, df)
    return df


def business_profile(code: str) -> dict:
    """同花顺主营业务简介：主营描述、产品类型、产品名称。"""
    ck = f"zyjs::{code}"
    hit = CACHE.get(ck, CACHE_TTL["survey"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_zyjs_ths(symbol=str(code).zfill(6))
    except Exception:      # noqa: BLE001
        return {}
    if df is None or df.empty:
        return {}
    row = df.iloc[0].to_dict()
    out = {
        "main_business": str(row.get("主营业务") or "")[:400],
        "product_type": str(row.get("产品类型") or "")[:200],
        "product_name": str(row.get("产品名称") or "")[:300],
    }
    CACHE.set(ck, out)
    return out


# ==================================================================== 3. 行业 / 板块
def industry_list() -> pd.DataFrame:
    """同花顺 90 个行业板块（name / code）。"""
    ck = "ths_industry_list"
    hit = CACHE.get(ck, CACHE_TTL["industry"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_board_industry_name_ths()
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    CACHE.set(ck, df)
    return df


def industry_constituents(board_code: str = "", name: str = "", max_pages: int = 12) -> pd.DataFrame:
    """行业成分股（完整名单 + 实时市值/PE/成交额）。

    主通道：东财 datacenter RPT_BOARD_CONSTITUENT（1 次请求拿全名单），再批量取腾讯行情；
    备通道：同花顺行业详情页（匿名仅开放前 5 页/100 只，且默认按涨幅排序，仅作兜底）。

    参数 board_code 兼容旧调用（同花顺板块代码），name 为板块名（东财与同花顺命名基本一致）。
    """
    ck = f"cons::{name}::{board_code}"
    hit = CACHE.get(ck, CACHE_TTL["industry"])
    if hit is not None:
        return hit

    df = _cons_em(name) if name else pd.DataFrame()
    if df.empty:
        df = _cons_ths(board_code, max_pages) if board_code else pd.DataFrame()
    if not df.empty:
        CACHE.set(ck, df)
    return df


def _cons_em(board_name: str) -> pd.DataFrame:
    """东财板块成分 -> 腾讯批量行情（市值/PE/涨跌幅/成交额）。"""
    from urllib.parse import quote as _q
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_BOARD_CONSTITUENT"
           f"&columns=ALL&pageSize=600&pageNumber=1&filter={_q(f'(BOARD_NAME=\"{board_name}\")')}")
    js = _http_json(url)
    if not js or not js.get("result") or not js["result"].get("data"):
        return pd.DataFrame()
    codes = []
    for it in js["result"]["data"]:
        sec = str(it.get("SECUCODE") or "")
        c = sec.split(".")[0] if "." in sec else str(it.get("SECURITY_CODE") or "")
        if re.fullmatch(r"\d{6}", c):
            codes.append(c)
    if not codes:
        return pd.DataFrame()
    quotes = realtime_quotes(sorted(set(codes)))
    if quotes.empty:
        return pd.DataFrame()
    df = pd.DataFrame({
        "code": quotes["code"],
        "name": quotes["name"],
        "price": quotes["price"],
        "change_pct": quotes["change_pct"],
        "turnover": quotes["turnover"],
        "vol_ratio": quotes["vol_ratio"],
        "amount": quotes["amount_wan"] * 1e4,      # 万元 -> 元
        "float_mv": quotes["float_mv"],
        "pe": quotes["pe_ttm"],
    })
    df = df[df["float_mv"].notna() & (df["float_mv"] > 0)]
    return df.drop_duplicates(subset=["code"]).reset_index(drop=True)


def _cons_ths(board_code: str, max_pages: int = 12) -> pd.DataFrame:
    """同花顺行业详情页兜底解析（匿名限前 5 页）。"""
    rows: list[list[str]] = []
    total_pages = 1
    for page in range(1, max_pages + 1):
        url = (f"http://q.10jqka.com.cn/thshy/detail/code/{board_code}/" if page == 1
               else f"http://q.10jqka.com.cn/thshy/detail/code/{board_code}/page/{page}/")
        html = _http(url, encoding="gbk", headers={"Referer": "http://q.10jqka.com.cn/thshy/"})
        if not html or len(html) < 1000:
            break
        if page == 1:
            m = re.search(r'page_info">\s*\d+/(\d+)', html)
            if m:
                total_pages = min(int(m.group(1)), max_pages)
        for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S):
            tds = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(tds) >= 13 and re.fullmatch(r"\d{6}", tds[1]):
                rows.append(tds[:14])
        if page >= total_pages:
            break
        time.sleep(0.15)

    if not rows:
        return pd.DataFrame()

    cols = ["序号", "code", "name", "price", "change_pct", "change", "speed",
            "turnover", "vol_ratio", "amplitude", "amount", "float_shares", "float_mv", "pe"]
    df = pd.DataFrame([r[:len(cols)] for r in rows], columns=cols)
    for c in ["price", "change_pct", "change", "speed", "turnover", "vol_ratio",
              "amplitude", "amount", "float_shares", "float_mv", "pe"]:
        df[c] = df[c].map(to_num)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["amount"] = df["amount"] * 1e8
    df["float_mv"] = df["float_mv"] * 1e8
    return df.drop_duplicates(subset=["code"]).reset_index(drop=True)


def industry_fund_flow() -> pd.DataFrame:
    """同花顺行业资金流（即时）：行业、涨跌幅、流入/流出/净额(亿)、公司家数、领涨股。"""
    ck = "ths_ind_flow"
    hit = CACHE.get(ck, CACHE_TTL["flow"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_fund_flow_industry(symbol="即时")
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["净额"] = df["净额"].map(to_num)
    df["流入资金"] = df["流入资金"].map(to_num)
    df["流出资金"] = df["流出资金"].map(to_num)
    df["行业-涨跌幅"] = df["行业-涨跌幅"].map(to_num)
    df["公司家数"] = df["公司家数"].map(to_num)
    CACHE.set(ck, df)
    return df


def concept_fund_flow() -> pd.DataFrame:
    ck = "ths_con_flow"
    hit = CACHE.get(ck, CACHE_TTL["flow"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_fund_flow_concept(symbol="即时")
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df["净额"] = df["净额"].map(to_num)
    df["行业-涨跌幅"] = df["行业-涨跌幅"].map(to_num)
    CACHE.set(ck, df)
    return df


def stock_fund_flow_all() -> pd.DataFrame:
    """同花顺全市场个股资金流（约 5200 行，耗时 ~20s，缓存 30 分钟）。"""
    ck = "ths_stock_flow_all"
    hit = CACHE.get(ck, CACHE_TTL["flow_all"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_fund_flow_individual(symbol="即时")
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    for c in ["最新价", "涨跌幅", "换手率", "流入资金", "流出资金", "净额", "成交额"]:
        df[c] = df[c].map(to_num)
    CACHE.set(ck, df)
    return df


def industry_index_hist(name: str, days: int = 120) -> pd.DataFrame:
    """同花顺板块指数日线（用于行业相对强度、板块趋势）。"""
    ck = f"ths_idx::{name}::{days}"
    hit = CACHE.get(ck, CACHE_TTL["industry"])
    if hit is not None:
        return hit
    today = dt.date.today()
    start = (today - dt.timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = _ak().stock_board_industry_index_ths(symbol=name, start_date=start,
                                                  end_date=today.strftime("%Y%m%d"))
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "收盘价": "close", "成交额": "amount", "成交量": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    for c in ["close", "amount", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").tail(days).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


# ==================================================================== 4. 消息面
def stock_news(code: str, limit: int = 30) -> pd.DataFrame:
    """东财个股新闻。"""
    ck = f"news::{code}::{limit}"
    hit = CACHE.get(ck, CACHE_TTL["news"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_news_em(symbol=str(code).zfill(6))
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"新闻标题": "title", "发布时间": "time", "文章来源": "source",
                            "新闻内容": "content", "新闻链接": "url"})
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.sort_values("time", ascending=False).head(limit).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


def stock_announcements(code: str, days: int = 120, page_size: int = 50) -> pd.DataFrame:
    """东财个股公告（含公告类型标签），按日期回溯 days 天。"""
    ck = f"ann::{code}::{days}"
    hit = CACHE.get(ck, CACHE_TTL["announce"])
    if hit is not None:
        return hit
    cutoff = dt.date.today() - dt.timedelta(days=days)
    rows: list[dict] = []
    for page in range(1, 4):
        url = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
               f"?sr=-1&page_size={page_size}&page_index={page}&ann_type=A"
               f"&client_source=web&stock_list={str(code).zfill(6)}&f_node=0&s_node=0")
        js = _http_json(url)
        if not js or not js.get("data") or not js["data"].get("list"):
            break
        stop = False
        for it in js["data"]["list"]:
            date_str = (it.get("notice_date") or "")[:10]
            try:
                d = dt.date.fromisoformat(date_str)
            except ValueError:
                continue
            if d < cutoff:
                stop = True
                break
            rows.append({
                "date": d,
                "title": re.sub(r"\s+", " ", it.get("title") or ""),
                "types": ",".join(c.get("column_name", "") for c in (it.get("columns") or [])),
                "url": f"https://data.eastmoney.com/notices/detail/{str(code).zfill(6)}/{it.get('art_code')}.html",
            })
        if stop:
            break
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("date", ascending=False).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


def research_reports(code: str) -> pd.DataFrame:
    """东财个股研报：评级、机构、盈利预测、目标价。"""
    ck = f"research::{code}"
    hit = CACHE.get(ck, CACHE_TTL["research"])
    if hit is not None:
        return hit
    try:
        df = _ak().stock_research_report_em(symbol=str(code).zfill(6))
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.sort_values("日期", ascending=False).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


def lhb_records(code: str, days: int = 90) -> pd.DataFrame:
    """龙虎榜记录（按代码过滤）。"""
    ck = f"lhb::{code}::{days}"
    hit = CACHE.get(ck, CACHE_TTL["lhb"])
    if hit is not None:
        return hit
    today = dt.date.today()
    start = (today - dt.timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = _ak().stock_lhb_detail_em(start_date=start, end_date=today.strftime("%Y%m%d"))
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df["代码"].astype(str).str.zfill(6) == str(code).zfill(6)]
    if not df.empty:
        df["上榜日"] = pd.to_datetime(df["上榜日"], errors="coerce")
        df = df.sort_values("上榜日", ascending=False).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


def futures_price(symbol: str, days: int = 120) -> pd.DataFrame:
    """新浪期货主力连续价格（主要产品价格代理指标）。"""
    ck = f"fut::{symbol}::{days}"
    hit = CACHE.get(ck, CACHE_TTL["futures"])
    if hit is not None:
        return hit
    today = dt.date.today()
    start = (today - dt.timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = _ak().futures_main_sina(symbol=symbol, start_date=start, end_date=today.strftime("%Y%m%d"))
    except Exception:      # noqa: BLE001
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"日期": "date", "收盘价": "close"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").tail(days).reset_index(drop=True)
    CACHE.set(ck, df)
    return df


def pick_futures(industry: str, product_text: str = "") -> tuple[str, str] | None:
    """按行业/产品关键词挑选可用的期货代理品种。"""
    hay = f"{industry} {product_text}"
    for kw, (sym, label) in PRODUCT_FUTURES.items():
        if kw in hay:
            return sym, label
    return None


# ==================================================================== 5. 行业解析
def stock_boards(code: str) -> dict:
    """反查个股所属的全部东财板块，按类型分组。

    东财板块类型（BOARD_TYPE_NEW）：1=地域 2=行业（含多级） 3=概念 4=风格/资金。
    返回 {"industry": [...], "concept": [...], "region": [...], "style": [...]}
    """
    ck = f"boards::{code}"
    hit = CACHE.get(ck, CACHE_TTL["industry"])
    if hit is not None:
        return hit
    c = str(code).zfill(6)
    suffix = "SH" if c.startswith("6") else ("BJ" if c.startswith(("4", "8", "9")) else "SZ")
    from urllib.parse import quote as _q
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_BOARD_CONSTITUENT"
           f"&columns=ALL&pageSize=120&pageNumber=1&filter={_q(f'(SECUCODE=\"{c}.{suffix}\")')}")
    js = _http_json(url)
    out: dict[str, list[dict]] = {"industry": [], "concept": [], "region": [], "style": []}
    if js and js.get("result") and js["result"].get("data"):
        for it in js["result"]["data"]:
            t = str(it.get("BOARD_TYPE_NEW") or "")
            key = {"2": "industry", "3": "concept", "1": "region", "4": "style"}.get(t)
            if key:
                out[key].append({"name": it.get("BOARD_NAME"), "code": it.get("BOARD_CODE")})
    CACHE.set(ck, out)
    return out


def resolve_industry(code: str, max_candidates: int = 3) -> dict:
    """确定个股所属的同花顺行业板块（与行业资金流/板块指数同一套口径）。

    主通道：东财板块反查（type=2 行业层级）与同花顺 90 个行业求交集，
            多级命中时取成分股最少的那个（最细分），再用成分股名单完整性校验。
    备通道：东财 EM2016 三级行业字符串 + 别名/模糊匹配。
    """
    target = str(code).zfill(6)
    ilist = industry_list()
    names = ilist["name"].tolist() if not ilist.empty else []
    code_map = dict(zip(ilist["name"], ilist["code"])) if not ilist.empty else {}
    survey = company_survey(code)
    em2016 = survey.get("em2016", "")

    # ---- 主通道：东财板块反查
    boards = stock_boards(code)
    ind_boards = [b["name"] for b in boards.get("industry", []) if b.get("name")]
    hits = [n for n in ind_boards if n in code_map]
    if not hits and ind_boards and names:
        import difflib
        for n in ind_boards:
            hits.extend([m for m in difflib.get_close_matches(n, names, n=1, cutoff=0.6) if m not in hits])

    # 多级命中时，取成分股最少（最细分）的那个
    best = None
    for name in hits[:max_candidates + 2]:
        board = code_map.get(name, "")
        cons = industry_constituents(board, name)
        if cons.empty:
            continue
        if best is None or len(cons) < len(best[2]):
            best = (name, board, cons)
        if target in set(cons["code"]):
            break

    if best:
        return {"industry": best[0], "board_code": best[1], "verified": True,
                "em2016": em2016, "constituents": best[2],
                "boards": boards, "candidates": hits, "source": "eastmoney-board"}

    # ---- 备通道：EM2016 三级行业
    from .config import INDUSTRY_ALIAS
    import difflib

    segs = [s for s in em2016.split("-") if s]
    cands: list[str] = []
    for seg in reversed(segs):
        if seg in code_map and seg not in cands:
            cands.append(seg)
    for seg in reversed(segs):
        a = INDUSTRY_ALIAS.get(seg)
        if a and a in code_map and a not in cands:
            cands.append(a)
    if segs:
        for m in difflib.get_close_matches(segs[-1], names, n=2, cutoff=0.4):
            if m not in cands:
                cands.append(m)

    for name in cands[:max_candidates]:
        board = code_map.get(name, "")
        cons = industry_constituents(board, name)
        if not cons.empty and target in set(cons["code"]):
            return {"industry": name, "board_code": board, "verified": True,
                    "em2016": em2016, "constituents": cons,
                    "boards": boards, "candidates": cands, "source": "em2016"}

    fallback = cands[0] if cands else (names[0] if names else "")
    return {"industry": fallback, "board_code": code_map.get(fallback, ""), "verified": False,
            "em2016": em2016, "constituents": pd.DataFrame(),
            "boards": boards, "candidates": cands, "source": "fallback"}
