#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calc_em_boards.py — 东方财富「行业板块 + 概念板块」数据获取（游资/短线语境口径）

能力清单（均基于 push2.eastmoney.com 官方接口，2026-09-20 实测）：
  1. industry  东财行业板块列表（约 86 个，含涨跌幅/上涨下跌家数/领涨股/主力净流入）
  2. concept   东财概念板块列表（400+ 个）
  3. cons      板块成分股（按 BK 代码，如 BK1036 半导体）
  4. stock     个股反查所属板块（行业 + 概念 + 风格，一次返回 30+ 条）★短线最实用
  5. build_map 全市场「个股 -> 板块」映射（供选股流水线批量使用，慎用，请求量大）

接口实测备注（2026-09-20，本机环境）：
  - slist（个股所属板块）与 stock/get（个股行业字段）实测通过；
  - clist（板块列表/成分批量）在部分网络/代理环境下可能被对端断连，
    代码已内置 3 次指数退避重试 + akshare 降级兜底；
  - 本机若设置了 http_proxy 代理导致 requests 报 ProxyError，加 --no-proxy。

用法示例：
  python3 calc_em_boards.py industry                 # 东财行业板块列表（含当日涨跌）
  python3 calc_em_boards.py concept                  # 东财概念板块列表
  python3 calc_em_boards.py cons BK1036              # 半导体成分股
  python3 calc_em_boards.py stock 688213             # 思特威所属的行业+概念板块
  python3 calc_em_boards.py stock 688213 --label     # 同上，并把板块标注为 行业/概念
  python3 calc_em_boards.py industry --out boards.csv
"""

import argparse
import json
import sys
import time

import pandas as pd
import requests

BASE = "https://push2.eastmoney.com/api/qt"
UT = "bd1d9ddb04089700cf9c27f6f7426281"  # 东财公开 ut token
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

# clist 通用字段映射（东财板块/成分列表）
CLIST_FIELDS = ("f12,f14,f3,f6,f8,f62,f104,f105,f128,f20")
CLIST_COLS = {
    "f12": "代码", "f14": "名称", "f3": "涨跌幅%", "f6": "成交额(元)",
    "f8": "换手率%", "f62": "主力净流入(元)", "f104": "上涨家数",
    "f105": "下跌家数", "f128": "领涨股", "f20": "总市值(元)",
}

# slist（个股所属板块）字段映射
SLIST_COLS = {"f12": "板块代码", "f13": "市场", "f14": "板块名称"}


def make_session(no_proxy: bool = False) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if no_proxy:
        s.trust_env = False  # 忽略 http_proxy 等环境变量
    return s


def _get_json(session: requests.Session, url: str, params: dict,
              retries: int = 3, backoff: float = 1.5):
    """GET -> JSON，带指数退避重试；全部失败抛出最后异常。"""
    last = None
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries - 1:
                time.sleep(backoff * (2 ** i))
    raise last


def _diff_to_df(data: dict, cols: dict) -> pd.DataFrame:
    """东财 data.diff -> DataFrame（diff 可能是 list 或 dict{str:int}）。"""
    if not data or "diff" not in data:
        return pd.DataFrame()
    diff = data["diff"]
    if isinstance(diff, dict):  # 老接口偶发返回 {idx: {...}}
        diff = list(diff.values())
    df = pd.DataFrame(diff).rename(columns=cols)
    return df


def _secid(code: str) -> str:
    """A股 6 位代码 -> 东财 secid。6 开头=沪市(1)，其余=深市(0)。"""
    code = code.strip().zfill(6)
    return ("1." if code.startswith(("6", "9", "5")) else "0.") + code


def _fetch_clist(session, fs: str, max_pages: int = 30) -> pd.DataFrame:
    """分页抓取 clist 列表（板块列表 / 成分股通用）。"""
    out, page, total = [], 1, None
    while page <= max_pages:
        js = _get_json(session, f"{BASE}/clist/get", {
            "pn": page, "pz": 100, "po": 1, "np": 1, "ut": UT,
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": fs, "fields": CLIST_FIELDS,
        })
        data = js.get("data") or {}
        total = data.get("total", 0)
        df = _diff_to_df(data, CLIST_COLS)
        if df.empty:
            break
        out.append(df)
        if len(df) < 100 or sum(len(x) for x in out) >= total:
            break
        page += 1
        time.sleep(0.3)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _akshare_fallback(kind: str) -> pd.DataFrame:
    """clist 全挂时用 akshare 兜底（仅支持 industry / concept 列表）。

    akshare 内部 requests 会读环境代理；这里临时清掉代理环境变量，
    避免本机 http_proxy 导致 ProxyError（用完恢复）。
    """
    import os
    proxy_keys = [k for k in os.environ
                  if k.lower() in ("http_proxy", "https_proxy", "all_proxy")]
    saved = {k: os.environ.pop(k) for k in proxy_keys}
    try:
        import akshare as ak  # 延迟导入
        if kind == "industry":
            df = ak.stock_board_industry_name_em()
        elif kind == "concept":
            df = ak.stock_board_concept_name_em()
        else:
            raise RuntimeError(f"akshare 兜底不支持: {kind}")
        return df.rename(columns={"板块代码": "代码", "板块名称": "名称",
                                  "涨跌幅": "涨跌幅%", "领涨股票": "领涨股"})
    finally:
        os.environ.update(saved)


def _tushare_fallback(kind: str) -> pd.DataFrame:
    """东财 clist + akshare 全挂时，用 tushare 申万行业指数兜底（仅 industry）。

    申万一级（801010-801099）约 31 个行业，数据来自 sw_daily 接口，
    字段与东财口径差异：无主力净流入/上涨下跌家数/领涨股，有 PE/PB/总市值。
    """
    if kind != "industry":
        raise RuntimeError("tushare 降级仅支持 industry")
    import tushare as ts
    pro = ts.pro_api()
    # 申万一级行业指数代码：801010-801099（排除 801001-801009 等非行业指数）
    basic = pro.index_basic(market='SW')
    l1 = basic[basic['ts_code'].str.match(r'^8010[1-9]\d\.SI$')].copy()
    if l1.empty:
        raise RuntimeError("tushare 申万一级行业为空")

    from datetime import datetime, timedelta
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=15)).strftime('%Y%m%d')

    out = []
    for _, row in l1.iterrows():
        daily = pro.sw_daily(ts_code=row['ts_code'], start_date=start, end_date=end)
        if daily is not None and not daily.empty:
            latest = daily.sort_values('trade_date').iloc[-1]
            out.append({
                '代码': row['ts_code'],
                '名称': row['name'],
                '涨跌幅%': latest.get('pct_change'),
                '换手率%': None,
                '主力净流入(元)': None,
                '上涨家数': None,
                '下跌家数': None,
                '领涨股': None,
                '总市值(元)': latest.get('total_mv'),
            })
    if not out:
        raise RuntimeError("tushare sw_daily 全部为空")
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 四个能力

def get_industry_boards(no_proxy=False) -> pd.DataFrame:
    """东财行业板块列表（fs=m:90 t:2）。失败时 akshare 降级。"""
    s = make_session(no_proxy)
    try:
        df = _fetch_clist(s, "m:90+t:2")
        if df.empty:
            raise RuntimeError("clist 返回空")
        return df
    except Exception:
        print("[warn] clist 行业列表不可用，降级到 akshare...", file=sys.stderr)
        try:
            return _akshare_fallback("industry")
        except Exception as e1:  # noqa: BLE001
            print(f"[warn] akshare 也失败: {e1}，降级到 tushare 申万行业...", file=sys.stderr)
            try:
                return _tushare_fallback("industry")
            except Exception as e2:  # noqa: BLE001
                raise RuntimeError(
                    f"行业板块列表不可用（clist/akshare/tushare 均失败）。"
                    f"\n  akshare: {e1}"
                    f"\n  tushare: {e2}"
                    "\n请检查网络或 tushare token 配置") from e2


def get_concept_boards(no_proxy=False) -> pd.DataFrame:
    """东财概念板块列表（fs=m:90 t:3）。失败时 akshare 降级。"""
    s = make_session(no_proxy)
    try:
        df = _fetch_clist(s, "m:90+t:3")
        if df.empty:
            raise RuntimeError("clist 返回空")
        return df
    except Exception:
        print("[warn] clist 概念列表不可用，降级到 akshare...", file=sys.stderr)
        try:
            return _akshare_fallback("concept")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"概念板块列表不可用（clist 与 akshare 均失败: {e}）。"
                "请在正常网络环境运行，或加 --no-proxy 重试") from e


def get_board_constituents(bk_code: str, no_proxy=False) -> pd.DataFrame:
    """按 BK 代码取板块成分股，如 BK1036=半导体。"""
    bk_code = bk_code.upper()
    if not bk_code.startswith("BK"):
        raise ValueError("请传东财板块代码（BK 开头），如 BK1036")
    s = make_session(no_proxy)
    df = _fetch_clist(s, f"b:{bk_code}")
    if df.empty:
        raise RuntimeError(f"{bk_code} 成分为空或接口不可用（网络受限时本能力无降级）")
    return df


def get_stock_boards(code: str, no_proxy=False, label: bool = False) -> pd.DataFrame:
    """个股反查所属板块（行业 + 概念 + 风格）。

    slist spt=3 一次返回该股全部所属板块（实测 688213 返回 33 个：
    半导体/数字芯片设计/AI芯片/汽车芯片/国产芯片/机器视觉...）。
    label=True 时需先拉行业+概念全集做标注（clist 受限时自动放弃标注）。
    """
    s = make_session(no_proxy)
    secid = _secid(code)
    js = _get_json(s, f"{BASE}/slist/get", {
        "spt": 3, "fltt": 2, "invt": 2, "fields": "f12,f13,f14",
        "secid": secid, "pn": 1, "pz": 100, "po": 1, "np": 1,
        "ut": UT, "fid": "f3",
    })
    df = _diff_to_df(js.get("data"), SLIST_COLS)
    if df.empty:
        raise RuntimeError(f"{code} 未取到所属板块（接口返回空）")
    if label:
        try:
            ind = set(get_industry_boards(no_proxy)["代码"])
            con = set(get_concept_boards(no_proxy)["代码"])
            df["板块类型"] = df["板块代码"].map(
                lambda x: "行业" if x in ind else ("概念" if x in con else "风格/其他"))
        except Exception:
            print("[warn] 板块类型标注需要 clist，当前不可用，跳过标注", file=sys.stderr)
    return df


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="东财行业+概念板块数据获取")
    ap.add_argument("cmd", choices=["industry", "concept", "cons", "stock"])
    ap.add_argument("arg", nargs="?", help="cons: BK代码 / stock: 6位代码")
    ap.add_argument("--out", help="输出 CSV 路径")
    ap.add_argument("--label", action="store_true", help="stock 模式下标注板块类型")
    ap.add_argument("--no-proxy", action="store_true",
                    help="忽略 http_proxy 环境变量（requests 报 ProxyError 时使用）")
    args = ap.parse_args()

    if args.cmd == "industry":
        df = get_industry_boards(args.no_proxy)
    elif args.cmd == "concept":
        df = get_concept_boards(args.no_proxy)
    elif args.cmd == "cons":
        if not args.arg:
            ap.error("cons 需要 BK 代码，如: calc_em_boards.py cons BK1036")
        df = get_board_constituents(args.arg, args.no_proxy)
    else:  # stock
        if not args.arg:
            ap.error("stock 需要 6 位代码，如: calc_em_boards.py stock 688213")
        df = get_stock_boards(args.arg, args.no_proxy, label=args.label)

    pd.set_option("display.max_rows", 50, "display.width", 160,
                  "display.unicode.east_asian_width", True)
    print(f"共 {len(df)} 条")
    print(df.head(20).to_string(index=False))
    if args.out:
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"已保存 -> {args.out}")


if __name__ == "__main__":
    main()
