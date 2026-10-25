#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stock_graph_aoc.py —— 从图像中识别股票名称 / 股票代码

功能
----
1. 使用 RapidOCR（本地 ONNX 推理，无需联网）识别图片中的文字（支持中文）；
2. 从识别结果中提取 6 位股票代码（兼容 600519 / sh600519 / 600519.SH 形态）；
3. 将识别出的中文词条与 stock_info_t 表做精确 / 模糊匹配，得到股票名称与代码；
4. 输出结构化结果（默认 JSON，便于其他程序调用）。

用法
----
    python stock_graph_aoc.py <图片路径> [更多图片...] [选项]

选项
----
    --top N      每个图片最多返回 N 条股票结果（默认 5）
    --json       只输出 JSON（供程序调用）
    --verbose    额外打印 OCR 识别出的全部文本行
    --no-db      不连接数据库，仅返回 OCR 文本与代码

依赖
----
    .venv/bin/python3 环境已安装 rapidocr-onnxruntime、pymysql
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from typing import Any, Optional

# 允许以任意工作目录运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 6 位代码识别：600519 / sh600519 / 600519.SH / 600519SH
_CODE_RE = re.compile(r'(?<!\d)(?:SH|SZ|BJ)?(\d{6})(?:\.?(?:SH|SZ|BJ))?(?!\d)', re.I)
# 名称候选段：中文（含字母数字与 *，如 *ST国华 / ST星源）
_SEG_RE = re.compile(r'[\u4e00-\u9fffA-Za-z0-9*]+')

_ENGINE: Optional[Any] = None


def _norm(s: str) -> str:
    """归一化文本：全角转半角、去空白与标点、转大写，便于名称匹配。"""
    s = unicodedata.normalize('NFKC', s).upper()
    return ''.join(ch for ch in s if ch.isalnum() or ch == '*')


def get_ocr_engine() -> Any:
    """延迟加载 RapidOCR 引擎（首次调用约 1-2 秒）。"""
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            try:
                from rapidocr import RapidOCR
            except ImportError:
                sys.stderr.write(
                    "❌ 缺少 RapidOCR，请先执行: .venv/bin/pip install rapidocr-onnxruntime\n")
                sys.exit(1)
        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_image(image_path: str) -> list[dict]:
    """对单张图片做 OCR，返回 [{text, score}] 按置信度降序。"""
    engine = get_ocr_engine()
    result, _elapse = engine(image_path)
    items: list[dict] = []
    if not result:
        return items
    for _box, text, score in result:
        if text and str(text).strip():
            items.append({"text": str(text).strip(), "score": float(score or 0)})
    return items


def load_stock_index() -> list[tuple]:
    """加载 stock_info_t 全表到内存：[(ts_code, stock_name, 归一化名称, 6位代码)]。"""
    from module_mysql_connection import get_mysql_connection, close_connection
    # module_mysql_connection 内部 print 走 stderr，避免污染 --json 的 stdout
    real_out = sys.stdout
    sys.stdout = sys.stderr
    try:
        conn = get_mysql_connection()
        if not conn:
            return []
        try:
            cur = conn.cursor()
            cur.execute("SELECT ts_code, stock_name FROM stock_info_t")
            rows = cur.fetchall()
        finally:
            close_connection(conn)
    finally:
        sys.stdout = real_out
    idx = []
    for r in rows:
        code = r["ts_code"]
        name = r["stock_name"]
        idx.append((code, name, _norm(name), re.sub(r'\D', '', code)[:6]))
    return idx


def match_name(token: str, index: list[tuple]) -> Optional[tuple]:
    """名称词条与股票全量列表匹配，返回 (评分, ts_code, stock_name)。

    精确命中得分 100+，子串命中得分按长度计；返回最优一条。
    """
    n = _norm(token)
    if len(n) < 2:
        return None
    best = None
    for code, name, nname, _c6 in index:
        if n == nname:
            s = 1000 + len(n)
        elif n in nname:
            s = len(n)          # 子串匹配：词条越长越可信
        else:
            continue
        if best is None or s > best[0]:
            best = (s, code, name)
    return best


def _name_runs(text: str) -> list[str]:
    """从一行文本提取中文名称候选段（连续中文，容忍 *ST / ST 前缀）。"""
    t = unicodedata.normalize('NFKC', text)
    runs: list[str] = []
    for m in re.finditer(r'(?:[*A-Za-z]{0,4})?([\u4e00-\u9fff]{2,})', t):
        seg = m.group(0)
        # 去掉混在其中的数字（如 第22位 -> 第位）
        runs.append(re.sub(r'\d', '', seg))
    return runs


def recognize_one(image_path: str, index: list[tuple],
                  top: int = 5, verbose: bool = False) -> dict:
    """识别单张图片，返回结构化结果。"""
    out: dict = {
        "image": image_path,
        "ocr_lines": [],
        "codes": [],
        "stocks": [],
        "unmatched_codes": [],
    }
    items = ocr_image(image_path)
    if verbose:
        out["ocr_lines"] = items
    if not index:
        # 无库环境：仅返回 OCR 代码
        code_hits: dict[str, float] = {}
        for it in items:
            for m in _CODE_RE.finditer(_norm(it["text"])):
                code_hits.setdefault(m.group(1), it["score"])
        out["codes"] = sorted(code_hits)
        out["unmatched_codes"] = out["codes"]
        return out

    code2stock = {c6: (code, name) for code, name, _nn, c6 in index}

    # ---- 逐行识别：代码 + 名称
    best: dict[str, dict] = {}        # ts_code -> 最优命中

    def add_hit(ts_code: str, stock_name: str, raw: str, source: str, score: float) -> None:
        if ts_code not in best or score > best[ts_code]["score"]:
            best[ts_code] = {
                "ts_code": ts_code, "stock_name": stock_name,
                "raw": raw, "source": source, "score": score,
            }

    for it in items:
        line = it["text"]
        n = _norm(line)

        # 1) 6 位代码（支持 600519 / sh600519 / 600519.SH）
        for m in _CODE_RE.finditer(n):
            c6 = m.group(1)
            if c6 in code2stock:
                code, name = code2stock[c6]
                add_hit(code, name, c6, "code", 900.0)

        # 2) 整行包含完整股票名（如标题「贵州茅台600519」）
        for code, name, nname, _c6 in index:
            if nname in n:
                add_hit(code, name, nname, "name", 1000.0 + len(nname))

        # 3) 中文词条子串匹配（如短词「茅台」→ 贵州茅台）
        for run in _name_runs(line):
            rn = _norm(run)
            if len(rn) < 2 or rn.isdigit():
                continue
            hit = match_name(rn, index)
            if hit:
                add_hit(hit[1], hit[2], rn, "name", float(hit[0]))

    stocks = sorted(best.values(), key=lambda x: -x["score"])[:top]
    out["stocks"] = stocks
    out["codes"] = sorted({m.group(1) for it in items
                           for m in _CODE_RE.finditer(_norm(it["text"]))})
    out["unmatched_codes"] = [c for c in out["codes"] if c not in code2stock]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="图像识别股票名称 / 股票代码")
    ap.add_argument("images", nargs="+", help="图片路径（支持多张）")
    ap.add_argument("--top", type=int, default=5, help="每个图片最多返回 N 条（默认 5）")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    ap.add_argument("--verbose", action="store_true", help="打印全部 OCR 文本行")
    ap.add_argument("--no-db", action="store_true", help="不连接数据库")
    args = ap.parse_args()

    index = [] if args.no_db else load_stock_index()

    results = []
    for img in args.images:
        if not os.path.exists(img):
            print(f"❌ 图片不存在: {img}", file=sys.stderr)
            continue
        results.append(recognize_one(img, index, top=args.top, verbose=args.verbose))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    for r in results:
        print(f"\n📷 图片: {r['image']}")
        if not r["stocks"] and not r["codes"]:
            print("   未识别到股票代码或名称")
            continue
        for s in r["stocks"]:
            src = "名称" if s["source"] == "name" else "代码"
            print(f"   ✅ {s['stock_name']}（{s['ts_code']}）—— 由{src}「{s['raw']}」识别")
        for c in r["unmatched_codes"]:
            print(f"   ⚠️ 识别到代码 {c}，但 stock_info_t 中无对应股票")


if __name__ == "__main__":
    main()
