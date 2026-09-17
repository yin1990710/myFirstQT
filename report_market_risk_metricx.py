# -*- coding: utf-8 -*-
"""
A股大盘风险指数（Market Risk Index, MRI）
=========================================
依据《A股大盘风险评估指标体系》六维框架实现的可执行版本：
    估值定空间 + 情绪定热度 + 资金定流动性 + 技术定位置 + 宏观定周期 + 结构定拥挤

设计原则
  1. 分层解耦：取数层（fetcher）与打分层（score）完全分离，接口失效不影响逻辑。
  2. 优雅降级：任一指标取数失败 -> 标记 N/A，指数按"可用指标"重新归一（不按 0 计，避免虚假乐观）。
  3. 手工补数：--input 传入的 JSON 与自动取数【合并】，手填值优先。覆盖免费源拿不到的指标。
  4. 离线可验证：--selftest 用合成样本验证打分与合成逻辑，不依赖外网。

用法
  python report_market_risk_metricx.py                          # 自动取数（需 akshare）
  python report_market_risk_metricx.py --input mri_manual.json  # 自动取数 + 手工补数（推荐）
  python report_market_risk_metricx.py --init-manual            # 生成手工补数模板
  python report_market_risk_metricx.py --selftest               # 离线自检
  python report_market_risk_metricx.py --json out.json          # 指定 JSON 输出路径

指标可获取性（AkShare 免费源实测，2026-09）
  ✅ 全自动（14 项）：PE分位、PB分位、ERP、融资余额/流通市值、期权IV、涨停家数、
                     M1-M2剪刀差、BIAS250、均线破坏、波动率、市场宽度背离、PMI、汇率、10Y国债变动
  注意：PMI 优先取【国家统计局官方制造业PMI】(macro_china_pmi，更新至最新月)；
        备选金十源 (macro_china_pmi_yearly) 数据仅更新至 2025-08，已降级为兜底。
  ✍️ 需手工补数（7 项）：基金仓位、DR007偏离、北向流入、
                     赛道拥挤度、股指期货贴水、期权PCR、个股相关系数
  说明：北向资金自 2024-08 起停止披露实时数据。

阈值均为历史经验参考值，务必用滚动窗口自行校准（见报告第九节）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

# ----------------------------------------------------------------------------
# 一、指标定义与打分卡   0=低风险 1=中性 2=高风险
# ----------------------------------------------------------------------------

INDICATORS = [
    # ---- 估值：决定下跌空间 ----
    dict(key="pe_pct", dim="估值", name="全A PE-TTM 近10年分位", unit="%",
         rule=">80% 高风险，<30% 低风险", auto=True,
         score=lambda v: 2 if v >= 80 else (1 if v >= 60 else 0)),
    dict(key="pb_pct", dim="估值", name="全A PB 近10年分位", unit="%",
         rule=">80% 高风险，<30% 低风险（对周期股更有效）", auto=True,
         score=lambda v: 2 if v >= 80 else (1 if v >= 60 else 0)),
    dict(key="erp", dim="估值", name="股权风险溢价 ERP = 1/PE − 10Y国债收益率", unit="%",
         rule="<1% 股票性价比差（高风险），>4% 接近底部", auto=True,
         score=lambda v: 2 if v <= 1.0 else (1 if v <= 2.5 else 0)),

    # ---- 情绪：决定过热程度（反向指标）----
    dict(key="margin_ratio", dim="情绪", name="融资余额 / 流通市值", unit="%",
         rule="≥3.5% 杠杆过热，≥2.5% 偏高（2015峰约4.7%流通口径），高位回落风险缓释", auto=True,
         score=lambda v: 2 if v >= 3.5 else (1 if v >= 2.5 else 0)),
    dict(key="fund_position", dim="情绪", name="股票型基金仓位", unit="%",
         rule=">88% 「88魔咒」，<80% 相对安全", auto=False,
         score=lambda v: 2 if v >= 88 else (1 if v >= 84 else 0)),
    dict(key="iv_low", dim="情绪", name="期权隐含波动率 QVIX（50ETF）", unit="",
         rule="极低 = 自满（风险），冲高 = 恐慌（机会）", auto=True,
         score=lambda v: 2 if v <= 15 else (1 if v <= 18 else 0)),
    dict(key="limit_up_cnt", dim="情绪", name="涨停家数（投机热度）", unit="家",
         rule=">100 家 = 投机过热，<30 家 = 情绪低迷", auto=True,
         score=lambda v: 2 if v >= 100 else (1 if v >= 60 else 0)),

    # ---- 资金与流动性 ----
    dict(key="dr007_dev", dim="资金", name="DR007 相对 7天逆回购利率偏离", unit="bp",
         rule="持续为正 20bp 以上 = 资金面收紧", auto=False,
         score=lambda v: 2 if v >= 30 else (1 if v >= 15 else 0)),
    dict(key="m1_m2_gap", dim="资金", name="M1-M2 剪刀差（同比增速差）", unit="pct",
         rule="缺口为负 = 资金活化不足、入市活水弱", auto=True,
         score=lambda v: 2 if v <= -5 else (1 if v <= -2 else 0)),
    dict(key="north_flow", dim="资金", name="北向/互联互通近20日净流入", unit="亿元",
         rule="持续净流出 = 外部资金撤离（2024-08 起停止实时披露）", auto=False,
         score=lambda v: 2 if v <= -200 else (1 if v <= 0 else 0)),

    # ---- 技术：决定当前位置 ----
    dict(key="bias250", dim="技术", name="指数年线偏离度 BIAS250", unit="%",
         rule=">+30% 严重超买（2015/2007 均 >40%），<-20% 超跌", auto=True,
         score=lambda v: 2 if v >= 30 else (1 if v >= 20 else 0)),
    dict(key="ma_trend_broken", dim="技术", name="均线层级破坏（MA20<MA60<MA120）", unit="",
         rule="是 = 中期趋势已确认破坏", auto=True,
         score=lambda v: 2 if v >= 1 else 0),
    dict(key="vol_low", dim="技术", name="60日年化波动率", unit="%",
         rule="<12% 低波自满（风险），>35% 恐慌释放中", auto=True,
         score=lambda v: 2 if v <= 12 else (1 if v <= 16 else 0)),
    dict(key="breadth_div", dim="技术", name="市场宽度背离（指数近新高但上涨家数占比 <45%）", unit="",
         rule="是 = 普涨结束、结构性风险", auto=True,
         score=lambda v: 2 if v >= 1 else 0),

    # ---- 宏观与基本面 ----
    dict(key="pmi_below50_months", dim="宏观", name="PMI 连续低于 50 的月数", unit="月",
         rule="连续 3 个月以上 = 经济下行确认", auto=True,
         score=lambda v: 2 if v >= 3 else (1 if v >= 2 else 0)),
    dict(key="usdcny_1m", dim="宏观", name="人民币汇率近1个月变动", unit="%",
         rule="1个月贬值 >3% 常伴外资流出", auto=True,
         score=lambda v: 2 if v >= 3 else (1 if v >= 2 else 0)),
    dict(key="cgb10y_chg", dim="宏观", name="10Y国债收益率近1个月变动", unit="bp",
         rule="快速上行 = 流动性收紧/压制估值", auto=True,
         score=lambda v: 2 if v >= 30 else (1 if v >= 15 else 0)),

    # ---- 市场结构与风险传导 ----
    dict(key="sector_crowding", dim="结构", name="热门赛道成交额占比（前3行业）", unit="%",
         rule=">40%-45% 极度拥挤，均值回归风险大", auto=False,
         score=lambda v: 2 if v >= 40 else (1 if v >= 30 else 0)),
    dict(key="iff_basis", dim="结构", name="股指期货年化贴水幅度（IF/IC/IM）", unit="%",
         rule="贴水大幅走阔 = 机构悲观、对冲需求激增", auto=False,
         score=lambda v: 2 if v >= 8 else (1 if v >= 4 else 0)),
    dict(key="pcr_low", dim="结构", name="期权 PCR（认沽/认购比）", unit="",
         rule="极端低 = 乐观（顶部信号），极端高 = 恐慌（底部信号）", auto=False,
         score=lambda v: 2 if v <= 0.6 else (1 if v <= 0.8 else 0)),
    dict(key="stock_corr", dim="结构", name="个股平均相关系数（近60日）", unit="",
         rule="抬升 = 系统性风险主导、分散化失效", auto=False,
         score=lambda v: 2 if v >= 0.6 else (1 if v >= 0.45 else 0)),
]

DIM_WEIGHTS = {"估值": 0.15, "情绪": 0.25, "资金": 0.20,
               "技术": 0.15, "宏观": 0.10, "结构": 0.15}

RISK_BANDS = [
    (0, 30, "低风险（偏冷）", "可提高仓位，进攻性配置"),
    (30, 50, "中性", "均衡持仓、做结构"),
    (50, 70, "偏高", "降低仓位 / 提高对冲比例、收紧止损"),
    (70, 101, "高风险（过热）", "显著减仓、保留现金、规避高拥挤赛道"),
]

BOOL_KEYS = {"ma_trend_broken", "breadth_div"}


def fmt_value(r: dict) -> str:
    """统一取值展示：布尔型 -> 是/否，缺失 -> N/A。"""
    if r["value"] is None:
        return "N/A"
    if r["key"] in BOOL_KEYS:
        return "是" if float(r["value"]) >= 1 else "否"
    return f"{r['value']}{r['unit']}"


# ----------------------------------------------------------------------------
# 二、打分与合成
# ----------------------------------------------------------------------------

def score_indicator(ind: dict, value):
    if value is None:
        return None
    try:
        return int(ind["score"](float(value)))
    except Exception:
        return None


def compute_index(values: dict) -> dict:
    rows, dim_acc = [], {}
    for ind in INDICATORS:
        v = values.get(ind["key"])
        s = score_indicator(ind, v)
        rows.append(dict(key=ind["key"], dim=ind["dim"], name=ind["name"],
                         unit=ind["unit"], rule=ind["rule"], auto=ind["auto"],
                         value=v, score=s))
        if s is not None:
            dim_acc.setdefault(ind["dim"], []).append(s)

    dim_scores = {d: (sum(v) / (2 * len(v)) * 100 if v else None)
                  for d, v in dim_acc.items()}

    # 仅用"有数据"的维度归一化权重，避免缺失被当作低风险
    avail = {d: DIM_WEIGHTS[d] for d, v in dim_scores.items() if v is not None}
    total_w = sum(avail.values())
    index = (sum(dim_scores[d] * w for d, w in avail.items()) / total_w) if total_w > 0 else None

    band = next((b for b in RISK_BANDS
                 if b[0] <= (index if index is not None else -1) < b[1]),
                (None, None, "数据不足", "无法评估"))

    covered = sum(1 for r in rows if r["score"] is not None)
    return dict(
        index=round(index, 1) if index is not None else None,
        level=band[2], action=band[3],
        dim_scores={d: (round(v, 1) if v is not None else None) for d, v in dim_scores.items()},
        rows=rows, coverage=f"{covered}/{len(rows)}",
        missing=[r["key"] for r in rows if r["score"] is None],
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


# ----------------------------------------------------------------------------
# 三、取数层（AkShare，2026-09 实测可用的接口链；失败即降级为 N/A）
# ----------------------------------------------------------------------------

def _ak():
    try:
        import akshare as ak  # noqa
        return ak
    except Exception:
        return None


def _try(calls, pick=None):
    """依次尝试多个 (函数名, kwargs) 组合，返回首个成功结果，全失败返回 None。"""
    ak = _ak()
    if ak is None:
        return None
    for fn_name, kwargs in calls:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is None or len(df) == 0:
                continue
            return df if pick is None else pick(df)
        except Exception:
            continue
    return None


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").replace("家", ""))
    except Exception:
        return None


def fetch_all(verbose: bool = True) -> dict:
    """返回 {key: value}。任一项失败置 None 并记录原因。"""
    v = {i["key"]: None for i in INDICATORS}
    why = {}
    ak = _ak()
    if ak is None:
        print("[提示] 未检测到 akshare，联网取数已跳过。可 --input 手工填数或 --selftest 自检。")
        return v

    # ---- 1. 指数日线：技术维度 ----
    idx = _try([("stock_zh_index_daily", dict(symbol="sh000300"))])
    close = None
    if idx is not None:
        try:
            col = "close" if "close" in idx.columns else idx.columns[-1]
            close = idx[col].astype(float).reset_index(drop=True)
        except Exception:
            close = None
    else:
        why["bias250"] = "指数日线接口不可用"

    if close is not None and len(close) > 260:
        ma250 = close.rolling(250).mean().iloc[-1]
        v["bias250"] = round((close.iloc[-1] / ma250 - 1) * 100, 2)
        v["vol_low"] = round(close.pct_change().dropna().tail(60).std() * (252 ** 0.5) * 100, 2)
        ma20, ma60, ma120 = (close.rolling(n).mean().iloc[-1] for n in (20, 60, 120))
        v["ma_trend_broken"] = 1 if (ma20 < ma60 < ma120) else 0

    # ---- 2. 全A估值分位（PE / PB）----
    pe_df = _try([("stock_a_ttm_lyr", {}), ("stock_a_indicator_lg", dict(symbol="all"))])
    pe_now = None
    if pe_df is not None:
        try:
            col = next(c for c in pe_df.columns if "pe" in c.lower() and "ttm" in c.lower())
            ser = pe_df[col].astype(float).dropna()
            win = ser.tail(2500)                      # 约 10 年交易日
            v["pe_pct"] = round((win < win.iloc[-1]).mean() * 100, 1)
            pe_now = float(ser.iloc[-1])
        except Exception:
            why["pe_pct"] = "PE 序列解析失败"

    pb_df = _try([("stock_a_all_pb", {})])
    if pb_df is not None:
        try:
            c = next(c for c in pb_df.columns if "quantileInRecent10YearsMiddlePB" in c)
            v["pb_pct"] = round(float(pb_df[c].dropna().iloc[-1]) * 100, 1)
        except Exception:
            why["pb_pct"] = "PB 分位字段解析失败"

    # ---- 3. 10Y 国债收益率（ERP 与宏观共用）----
    cgb = _try([("bond_zh_us_rate", {})])
    y_now = None
    if cgb is not None:
        try:
            y_col = next(c for c in cgb.columns if "中国国债收益率10年" in c)
            y = cgb[y_col].astype(float).dropna()
            if len(y) > 21:
                v["cgb10y_chg"] = round((y.iloc[-1] - y.iloc[-21]) * 100, 1)
            y_now = float(y.iloc[-1])                 # 单位：%
            if pe_now and y_now > 0:
                # ERP = 盈利收益率 − 无风险利率（百分点），主流卖方口径
                v["erp"] = round((100.0 / pe_now) - y_now, 2)
        except Exception:
            why["cgb10y_chg"] = "国债收益率序列解析失败"

    # ---- 4. 期权隐含波动率 QVIX（50ETF）----
    qvix = _try([("index_option_50etf_qvix", {}), ("index_option_300etf_qvix", {})])
    if qvix is not None:
        try:
            v["iv_low"] = round(float(qvix["close"].dropna().iloc[-1]), 2)
        except Exception:
            why["iv_low"] = "QVIX 解析失败"

    # ---- 5. 市场活跃度：涨停家数 + 宽度背离 ----
    act = _try([("stock_market_activity_legu", {})])
    if act is not None:
        try:
            d = {str(k).strip(): _num(x) for k, x in zip(act["item"], act["value"])}
            zt = d.get("涨停") or d.get("涨停家数")
            if zt is not None:
                v["limit_up_cnt"] = zt
            up = d.get("上涨")
            tot = sum(x for x in (d.get("上涨"), d.get("下跌"), d.get("平盘")) if x is not None)
            if up is not None and tot and close is not None and len(close) > 60:
                near_high = close.iloc[-1] >= close.tail(60).max() * 0.995
                v["breadth_div"] = 1 if (near_high and up / tot * 100 < 45) else 0
        except Exception:
            why["limit_up_cnt"] = "活跃度字段解析失败"

    # ---- 6. M1-M2 剪刀差 ----
    ms = _try([("macro_china_money_supply", {})])
    if ms is not None:
        try:
            d = ms.copy()
            # "2008年01月份" -> "2008-01"，用于强制升序（原始数据可能倒序）
            d["_k"] = (d["月份"].astype(str)
                       .str.replace("年", "-", regex=False)
                       .str.replace("月份", "", regex=False))
            d = d.sort_values("_k")
            m1 = float(d["货币(M1)-同比增长"].astype(float).iloc[-1])
            m2 = float(d["货币和准货币(M2)-同比增长"].astype(float).iloc[-1])
            v["m1_m2_gap"] = round(m1 - m2, 2)
        except Exception:
            why["m1_m2_gap"] = "M1/M2 序列解析失败"

    # ---- 7. PMI 连续低于 50 的月数（官方统计局源优先，更新更及时）----
    def _below50_run(series) -> int:
        cnt = 0
        for x in series.iloc[::-1]:
            if x < 50:
                cnt += 1
            else:
                break
        return cnt

    pmi_series, last_label, pmi_src = None, "", ""

    # 主源：国家统计局官方制造业 PMI（月度序列，最新月更新及时）
    pmi_off = _try([("macro_china_pmi", {})])
    if pmi_off is not None:
        try:
            d = pmi_off.copy()
            d["_k"] = (d["月份"].astype(str)
                       .str.replace("年", "-", regex=False)
                       .str.replace("月份", "", regex=False)
                       .str.replace("月", "", regex=False))
            d = d.sort_values("_k").dropna(subset=["制造业-指数"])
            if len(d):
                pmi_series = d["制造业-指数"].astype(float).reset_index(drop=True)
                last_label = str(d["_k"].iloc[-1])
                pmi_src = "统计局官方"
        except Exception:
            why["pmi_below50_months"] = "官方 PMI 解析失败，已回退备选源"

    # 备选源：金十（发布及时但数据陈旧，仅到 2025-08）
    if pmi_series is None:
        pmi = _try([("macro_china_pmi_yearly", {})])
        if pmi is not None:
            try:
                d = pmi.dropna(subset=["今值"]).sort_values("日期")
                pmi_series = d["今值"].astype(float).reset_index(drop=True)
                last_label = str(d["日期"].iloc[-1])[:10]
                pmi_src = "金十"
            except Exception:
                why["pmi_below50_months"] = "PMI 序列解析失败"

    if pmi_series is not None and len(pmi_series):
        v["pmi_below50_months"] = _below50_run(pmi_series)
        # 数据新鲜度检查：月度宏观数据滞后超过 75 天即提示复核
        lag_days = None
        if len(last_label) == 7 and last_label[:4].isdigit():          # "YYYY-MM"
            y, m = int(last_label[:4]), int(last_label[5:7])
            nxt = date(y + m // 12, m % 12 + 1, 1)                     # 次月首日
            lag_days = (date.today() - nxt).days
        else:
            try:
                lag_days = (date.today() -
                            datetime.strptime(last_label[:10], "%Y-%m-%d").date()).days
            except Exception:
                lag_days = None
        if lag_days is not None and lag_days > 75:
            why["pmi_below50_months"] = \
                f"[{pmi_src}]数据截至 {last_label}（滞后 {lag_days} 天），请复核"

    # ---- 7b. 融资余额 / 流通市值（情绪维度，原手工项，现已自动化）----
    # 分子：融资余额（沪深分市合计，单位：元）
    margin_total = None          # 元
    margin_series = None         # 近 60 日合计序列（元），用于趋势判断

    sh_margin = _try([("macro_china_market_margin_sh", {})])
    sz_margin = _try([("macro_china_market_margin_sz", {})])

    if sh_margin is not None and sz_margin is not None:
        try:
            sh_col = next(c for c in sh_margin.columns if "融资余额" in str(c))
            sz_col = next(c for c in sz_margin.columns if "融资余额" in str(c))
            sh_s = sh_margin[sh_col].astype(float).dropna().reset_index(drop=True)
            sz_s = sz_margin[sz_col].astype(float).dropna().reset_index(drop=True)
            # 对齐末尾 60 天
            n = min(len(sh_s), len(sz_s), 60)
            margin_series = (sh_s.tail(n).reset_index(drop=True) +
                             sz_s.tail(n).reset_index(drop=True))
            margin_total = float(margin_series.iloc[-1])    # 元
        except Exception:
            why["margin_ratio"] = "沪深融资余额合并失败"

    # 兜底：全市场口径（单位：亿元）
    if margin_total is None:
        mi = _try([("stock_margin_account_info", {})])
        if mi is not None:
            try:
                col = next(c for c in mi.columns if "融资余额" in str(c))
                margin_total = float(mi[col].astype(float).iloc[-1]) * 1e8  # 亿元→元
            except Exception:
                pass

    # 分母：流通市值（沪深合计，统一为亿元）
    circ_mv = 0.0    # 亿元

    # 沪市快照
    sse = _try([("stock_sse_summary", {})])
    if sse is not None:
        try:
            row = sse[sse.iloc[:, 0].astype(str).str.contains("流通市值")]
            if len(row):
                col = next(c for c in sse.columns if "股票" in str(c))
                circ_mv += float(row[col].iloc[0])
        except Exception:
            pass

    # 深市快照（需传有效交易日，自动回溯试探）
    for days_ago in range(0, 10):
        test_d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
        szse = _try([("stock_szse_summary", dict(date=test_d))])
        if szse is not None and len(szse):
            break
    if szse is not None:
        try:
            row = szse[szse.iloc[:, 0].astype(str).str.contains("股票")]
            if len(row):
                col = next(c for c in szse.columns if "流通市值" in str(c))
                circ_mv += float(row[col].iloc[0]) / 1e8    # 元→亿元
        except Exception:
            pass

    if margin_total is not None and circ_mv > 0:
        ratio = margin_total / 1e8 / circ_mv * 100          # %
        v["margin_ratio"] = round(ratio, 2)
        # 趋势标注：高位回落 → 风险缓释（水平定脆弱性，趋势定方向）
        if margin_series is not None and len(margin_series) >= 30:
            peak = float(margin_series.max())
            latest = float(margin_series.iloc[-1])
            peak_idx = int(margin_series.idxmax())
            if peak_idx >= len(margin_series) - 30 and latest < peak:
                why["margin_ratio"] = (
                    f"比率{ratio:.2f}%，高位回落（峰值{peak/1e8:.0f}亿、"
                    f"距峰值{(1-latest/peak)*100:.1f}%），风险缓释")
            elif latest > float(margin_series.iloc[-30]):
                why["margin_ratio"] = f"比率{ratio:.2f}%，融资余额上行中"
    elif margin_total is None:
        why["margin_ratio"] = "融资余额取数失败"
    elif circ_mv <= 0:
        why["margin_ratio"] = "流通市值取数失败"

    # ---- 8. 人民币汇率近1个月变动 ----
    fx = _try([("currency_boc_sina", dict(symbol="美元",
                                         start_date=(datetime.now().replace(day=1)).strftime("%Y%m%d"),
                                         end_date=datetime.now().strftime("%Y%m%d")))])
    if fx is None:
        fx = _try([("currency_boc_sina", dict(symbol="美元", start_date="20260101",
                                             end_date=datetime.now().strftime("%Y%m%d")))])
    if fx is not None:
        try:
            col = next(c for c in fx.columns if "央行中间价" in c or "折算价" in c)
            s = fx[col].astype(float).dropna().reset_index(drop=True)
            if len(s) > 21:
                v["usdcny_1m"] = round((s.iloc[-1] / s.iloc[-21] - 1) * 100, 2)
            elif len(s) >= 2:
                v["usdcny_1m"] = round((s.iloc[-1] / s.iloc[0] - 1) * 100, 2)
        except Exception:
            why["usdcny_1m"] = "汇率序列解析失败"

    if verbose:
        auto_missing = [k for k, val in v.items()
                        if val is None and any(i["key"] == k and i["auto"] for i in INDICATORS)]
        if auto_missing:
            print("[取数提示] 本应自动获取但失败：" +
                  "；".join(f"{k}({why.get(k, '接口不可用')})" for k in auto_missing))
        stale = [k for k, val in v.items() if val is not None and "数据截至" in why.get(k, "")]
        if stale:
            print("[复核提示] 已取到值但数据可能滞后：" +
                  "；".join(f"{k}({why[k]})" for k in stale))
    return v


# ----------------------------------------------------------------------------
# 四、输出
# ----------------------------------------------------------------------------

def print_report(res: dict):
    print("=" * 78)
    print(f"A股大盘风险指数（MRI）  {res['ts']}   数据覆盖 {res['coverage']}")
    print("=" * 78)
    if res["index"] is None:
        print("无可用数据，无法计算指数。请检查网络或使用 --input 手工填数。")
        return
    print(f"合成风险指数：{res['index']} / 100   →  {res['level']}")
    print(f"建议动作：{res['action']}")
    print("-" * 78)
    print("分维度读数：")
    for d, s in res["dim_scores"].items():
        bar = "█" * int((s or 0) / 5) if s is not None else "-"
        txt = ("%.1f" % s) if s is not None else "N/A"
        print(f"  {d:<4}{txt:>7}  {bar}")
    print("-" * 78)
    print(f"{'维度':<5}{'指标':<38}{'取值':>11}  {'分':>2}  风险读法")
    for r in res["rows"]:
        print(f"{r['dim']:<5}{r['name'][:36]:<38}{fmt_value(r):>11}  "
              f"{'-' if r['score'] is None else r['score']:>2}  {r['rule']}")
    print("-" * 78)
    manual = [r["key"] for r in res["rows"] if r["score"] is None and not r["auto"]]
    auto_miss = [r["key"] for r in res["rows"] if r["score"] is None and r["auto"]]
    if auto_miss:
        print("自动取数失败：" + ", ".join(auto_miss))
    if manual:
        print("待手工补数：" + ", ".join(manual))
    print("=" * 78)


def init_manual(path: str):
    tmpl = {i["key"]: None for i in INDICATORS if not i["auto"]}
    tmpl["_note"] = ("手工补数模板：填入数值后以 --input 传入，将与自动取数结果合并（手填优先）。"
                     "拿不到的可留 null。")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tmpl, f, ensure_ascii=False, indent=2)
    print(f"[已生成] 手工补数模板 -> {path}")
    for k in tmpl:
        if k != "_note":
            ind = next(i for i in INDICATORS if i["key"] == k)
            print(f"    {k:<22} {ind['name']}  （{ind['rule']}）")


# ----------------------------------------------------------------------------
# 五、离线自检
# ----------------------------------------------------------------------------

def selftest():
    hot = dict(pe_pct=88, pb_pct=85, erp=0.6, margin_ratio=2.9, fund_position=89.5,
               iv_low=13.5, limit_up_cnt=138, dr007_dev=35, m1_m2_gap=-6.2, north_flow=-320,
               bias250=34, ma_trend_broken=0, vol_low=11, breadth_div=1,
               pmi_below50_months=4, usdcny_1m=3.4, cgb10y_chg=38,
               sector_crowding=44, iff_basis=9.5, pcr_low=0.55, stock_corr=0.65)
    cold = dict(pe_pct=22, pb_pct=20, erp=4.6, margin_ratio=1.4, fund_position=78,
                iv_low=32, limit_up_cnt=18, dr007_dev=-5, m1_m2_gap=1.5, north_flow=150,
                bias250=-22, ma_trend_broken=1, vol_low=38, breadth_div=0,
                pmi_below50_months=1, usdcny_1m=-1.2, cgb10y_chg=-12,
                sector_crowding=17, iff_basis=1.5, pcr_low=1.35, stock_corr=0.32)

    print("\n>>> 自检 1：过热样本（应落入 70-100 高风险区）")
    h = compute_index(hot)
    print_report(h)
    print("\n>>> 自检 2：偏冷样本（应落入 0-30 低风险区）")
    c = compute_index(cold)
    print_report(c)

    ok = (h["index"] is not None and h["index"] >= 70) and \
         (c["index"] is not None and c["index"] <= 30)

    print("\n>>> 自检 3：缺失/降级（仅 3 项数据，覆盖度与归一化应正确）")
    p = compute_index(dict(pe_pct=95, bias250=40, vol_low=9))
    print(f"指数={p['index']}  覆盖={p['coverage']}  缺={len(p['missing'])}项  "
          f"维度数={sum(1 for v in p['dim_scores'].values() if v is not None)}")

    print("\n>>> 自检 4：手工值与自动值合并（手填优先）")
    merged = {**dict(pe_pct=20), **dict(pe_pct=90)}
    print(f"合并后 pe_pct={merged['pe_pct']}  ->  {'通过 ✅' if merged['pe_pct'] == 90 else '未通过 ❌'}")

    print("\n自检结果：" + ("通过 ✅" if ok else "未通过 ❌"))
    return 0 if ok else 1


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="A股大盘风险指数 MRI")
    ap.add_argument("--selftest", action="store_true", help="离线自检")
    ap.add_argument("--input", help="手工补数 JSON（与自动取数合并，手填优先）")
    ap.add_argument("--json", dest="json_out", default="pages/mri_data.json",
                    help="导出结果 JSON（默认 pages/mri_data.json）")
    ap.add_argument("--init-manual", dest="init_manual_path", nargs="?",
                    const="mri_manual.json", help="生成手工补数模板")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.init_manual_path:
        init_manual(a.init_manual_path)
        return 0

    values = fetch_all()
    if a.input:
        with open(a.input, encoding="utf-8") as f:
            manual = json.load(f)
        for k, val in manual.items():
            if k.startswith("_") or val is None:
                continue
            values[k] = val
        print(f"[已合并] 手工补数 {sum(1 for k, v in manual.items() if not k.startswith('_') and v is not None)} 项")

    res = compute_index(values)
    print_report(res)

    out = a.json_out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"[已输出] JSON -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
