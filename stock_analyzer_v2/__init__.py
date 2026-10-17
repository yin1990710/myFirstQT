"""astock_analyzer —— A 股个股走势分析工具包。

对任意 A 股标的输出：走势结构切分、技术状态、斐波那契位、主力资金脉冲、
杠杆资金行为、筹码结构、估值与关键价位观察框架，支持文本与 HTML 两种输出。

用法：
    from astock import analyze, save_html
    res = analyze("600577")
    print(res.to_text())
    save_html(res, "./reports")
"""

from .analyzer import AnalysisResult, analyze, normalize, render_text
from .report import render_html, save_html

__version__ = "1.0.0"
__all__ = [
    "analyze", "save_html", "render_html", "render_text",
    "AnalysisResult", "normalize", "__version__",
]