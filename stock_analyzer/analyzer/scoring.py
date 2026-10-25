"""评分汇总：加权总分、评级、核心结论与风险提示。"""
from __future__ import annotations

from .config import DIMENSION_LABELS, RATING_BANDS, WEIGHTS
from .modules.base import ModuleResult


def aggregate(results: list[ModuleResult]) -> dict:
    """按权重汇总。某维度不可用时自动按剩余维度权重归一化。"""
    usable = [r for r in results if r.available and r.max_score > 0]
    total_weight = sum(WEIGHTS[r.key] for r in usable)

    rows, total = [], 0.0
    for r in results:
        w = WEIGHTS[r.key]
        if r in usable and total_weight > 0:
            eff_w = w / total_weight * 100          # 归一化后的实际权重
            contrib = r.score_ratio * eff_w
            total += contrib
            rows.append({
                "key": r.key,
                "name": r.name,
                "weight": w,
                "eff_weight": round(eff_w, 1),
                "score": round(r.score, 1),
                "max": r.max_score,
                "ratio": round(r.score_ratio * 100, 1),
                "contrib": round(contrib, 1),
                "grade": r.grade,
                "available": True,
            })
        else:
            rows.append({
                "key": r.key, "name": r.name, "weight": w, "eff_weight": 0.0,
                "score": 0.0, "max": r.max_score, "ratio": 0.0, "contrib": 0.0,
                "grade": "缺数据", "available": False,
            })

    total = round(total, 1)
    rating, rating_desc = "D", "弱势或风险偏多，规避"
    for threshold, label, desc in RATING_BANDS:
        if total >= threshold:
            rating, rating_desc = label, desc
            break

    # 强项 / 弱项
    ranked = sorted([r for r in rows if r["available"]], key=lambda x: x["ratio"], reverse=True)
    strong = ranked[:2]
    weak = ranked[-2:] if len(ranked) >= 2 else []

    # 汇总信号
    bullish = [s for r in results for s in r.signals if s.direction == "bullish"]
    bearish = [s for r in results for s in r.signals if s.direction == "bearish"]
    bullish.sort(key=lambda s: -s.weight)
    bearish.sort(key=lambda s: -s.weight)

    return {
        "total": total,
        "rating": rating,
        "rating_desc": rating_desc,
        "rows": rows,
        "strong": strong,
        "weak": weak,
        "bullish": bullish,
        "bearish": bearish,
        "available_count": len(usable),
    }


def verdict(agg: dict, results: list[ModuleResult]) -> dict:
    """综合操作建议：技术形态 + 总分 + 资金/消息共振度。"""
    by_key = {r.key: r for r in results}
    tech = by_key.get("technical")
    w23 = (tech.details.get("w23") if tech else None)
    total = agg["total"]

    if total >= 70 and w23 and w23.stage_code in ("W2_END", "W3_START"):
        stance = "积极关注"
        action = ("多维共振且 W23 形态处于 2 浪末/3 浪初，属于模型内的重点观察窗口。"
                  "建议在放量突破前高或回踩 MA30 不破时分批建仓，跌破最近摆动低点止损。")
        risk_level = "中"
    elif total >= 65:
        stance = "逢回调关注"
        action = ("综合得分偏强，但形态/资金尚未完全共振。建议等待缩量回踩 MA20-MA30 区间企稳，"
                  "或放量突破关键压力位后再介入，避免追高。")
        risk_level = "中"
    elif total >= 50:
        stance = "观察"
        action = ("多空因素交织，缺乏明确右侧信号。建议保持观察，等待基本面催化（业绩/订单）"
                  "或资金面转向（板块资金连续净流入）后再评估。")
        risk_level = "中高"
    else:
        stance = "回避/减仓"
        action = ("综合得分偏弱，趋势与资金均未给出正向信号。若持有，反弹至 MA20/MA30 附近"
                  "可考虑减仓；空仓者暂不介入，等待基本面拐点。")
        risk_level = "高"

    # 风险点
    risks: list[str] = []
    if agg["bearish"]:
        risks.extend([s.text for s in agg["bearish"][:4]])
    fund = by_key.get("fundamental")
    if fund and fund.available and fund.score_ratio < 0.5:
        risks.append("基本面得分偏低，业绩兑现能力是核心风险")
    if tech and tech.available and tech.score_ratio < 0.5:
        risks.append("技术面弱势，趋势性下跌中反弹易失败")

    return {"stance": stance, "action": action, "risk_level": risk_level,
            "risks": risks[:6], "w23_stage": w23.stage if w23 else "—"}
