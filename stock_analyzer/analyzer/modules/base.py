"""分析模块统一返回结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Metric:
    """一条指标：名称 / 展示值 / 评价（good/neutral/bad 用于着色）/ 备注。"""
    label: str
    value: str
    tone: str = "neutral"          # good | neutral | bad | warn
    note: str = ""


@dataclass
class Signal:
    """一条结论：文本 + 方向（bullish/bearish/neutral）+ 重要度（1-3）。"""
    text: str
    direction: str = "neutral"
    weight: int = 1


@dataclass
class ModuleResult:
    key: str
    name: str
    score: float = 0.0
    max_score: float = 0.0
    available: bool = True
    headline: str = ""
    metrics: list[Metric] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    tables: list[dict[str, Any]] = field(default_factory=list)

    @property
    def score_ratio(self) -> float:
        return 0.0 if self.max_score <= 0 else max(0.0, min(1.0, self.score / self.max_score))

    @property
    def grade(self) -> str:
        r = self.score_ratio
        for cut, label in ((0.8, "优"), (0.65, "良"), (0.5, "中"), (0.35, "偏弱"), (0.0, "差")):
            if r >= cut:
                return label
        return "差"

    def add(self, label: str, value: str, tone: str = "neutral", note: str = "") -> None:
        self.metrics.append(Metric(label, value, tone, note))

    def sig(self, text: str, direction: str = "neutral", weight: int = 1) -> None:
        self.signals.append(Signal(text, direction, weight))

    def table(self, title: str, headers: list[str], rows: list[list[str]], note: str = "") -> None:
        self.tables.append({"title": title, "headers": headers, "rows": rows, "note": note})


def tone_of(value: float | None, good_above: float | None = None,
            bad_below: float | None = None) -> str:
    """按阈值给指标着色。"""
    if value is None:
        return "neutral"
    if good_above is not None and value >= good_above:
        return "good"
    if bad_below is not None and value <= bad_below:
        return "bad"
    return "neutral"
