---
name: "dtw-stock-pattern-match"
description: "Build A-share stock screeners that match a target stock/template's price-volume pattern via MinMax normalization + band-constrained DTW over sliding windows. Invoke when user asks to find stocks with similar走势/形态/K线/量价 to a given stock, image, or date range in myFirstQT."
---

# DTW 量价形态相似选股（myFirstQT 工作区专用）

当用户要求「找出与某只股票 / 某段时间 / 某张走势图 形态相似的股票」时，按本技能标准流程实现，保证三个同类脚本（find_similar_pattern.py、find_similar_ma5.py、find_good_up.py）风格一致。

## 一、数据源约定（stock_daily_db）

- 连接：`sys.path.append(脚本目录)` 后 `from mysql_connection import get_mysql_connection, close_connection`，`with conn.cursor() as cursor` 查询，finally 关闭。
- 交易日历：tushare `pro.trade_cal(exchange='SSE', ...)`，按 `is_open==1` 取最近 N 个交易日；SQL 区间日历日缓冲用 `n*2+15`。
- 目标日期 `get_target_date()`：0-15 点取前一自然日，否则取当日，格式 `%Y%m%d`。
- 主表 `stock_daily_t`：字段 `ts_code, trade_date, close, vol, amount, ma5, ma30, turning_point`。
  - `vol` 单位是**手**（个股日成交仅几万~几十万手），不能用"几亿"做阈值。
  - `amount` 单位是**千元**，成交额金额换算：`amount*1000` 元；5亿 = `amount > 500000`。
  - **ma5/ma30 可能未打标（历史数据有 NULL）**：模板/窗口需要 MA5 时，优先用 close 自行滚动计算，不依赖 DB 的 ma5 字段；模板起点前多取 15 个自然日作 MA5 种子。
- 市值表 `stock_daily_basic_info_t`：`total_mv` 单位**万元**，100亿 = `1,000,000`。用 `LEFT JOIN ... ON ts_code AND trade_date` 关联，取最新交易日值。
- 最新交易日：`SELECT MAX(trade_date) FROM stock_daily_t`；候选股最新记录日 != 该日即停牌，过滤。
- A 股过滤：代码以 `.SH` / `.SZ` 结尾。

## 二、标准算法

### 1. MinMax 归一化（逐窗口独立做，只保留形态）
```python
def normalize_series(series):
    arr = np.array(series, dtype=float)
    lo, hi = np.min(arr), np.max(arr)
    if hi - lo < 1e-9:
        return np.full_like(arr, 0.5)   # 常数序列映射0.5，不能返回全0（DTW会误判高度相似）
    return (arr - lo) / (hi - lo)
```

### 2. DTW 距离（纯 numpy，带 Sakoe-Chiba 带状约束）
本工作区 venv **没有 scipy/fastdtw，禁止 import**，用以下实现（90点单次约2ms）：
```python
def dtw_distance(a, b, band=10):
    n, m = len(a), len(b)
    INF = float('inf')
    prev = np.full(m + 1, INF); prev[0] = 0.0
    for i in range(1, n + 1):
        cur = np.full(m + 1, INF)
        for j in range(max(1, i - band), min(m, i + band) + 1):
            cur[j] = abs(a[i-1] - b[j-1]) + min(cur[j-1], prev[j], prev[j-1])
        prev = cur
    return prev[m]
```
等长序列比对用带状约束（band 取窗口长度的 ~10%），既提速又防病态扭曲。

### 3. 相似度换算与融合
- 单维：`sim = max(0, 1 - dtw_dist / 窗口长度)`（平均逐点绝对偏差）。
- 双维量价：`final = 0.6*ma5_sim + 0.4*vol_sim`（权重做成常量，注释可调）。
- 固定等长窗口（如近10日）可直接比对；长模板（如90日）必须用**滑动窗口**：步长5、强制包含最新窗口，且只保留结束日在最近20个交易日内的窗口，每股取最高分。
- 结构硬约束做成「惩罚×0.3」而非直接淘汰（如结束日 close>ma30 且 ma30 较5日前上行的多头结构）。

## 三、工程与输出约定

- 文件头：`#!/usr/bin/env python3` + `# -*- coding: utf-8 -*-` + 模块 docstring 写明模板来源、算法、过滤、输出。
- 参数化：窗口长度、top N、阈值、权重、模板代码/日期全部用文件顶部大写常量或 argparse（如 `--window`、`--top`）。
- 漏斗统计必须打印：总数 → 各过滤条件分别淘汰数 → 参与排名数 → 最终数。
- CSV：`csv.writer(f, newline='', encoding='utf-8-sig')`；简单结果单列「股票代码」，对比结果可加分数/日期列。
- 文件夹：`<策略名><YYYYMMDD>`；按项目现行约定**已存在则复用**并打印「📁 文件夹已存在: 名称」（只有 prompt 明确要求"删除重建"时才用 shutil.rmtree）。
- 输出目录必须加入 `.gitignore`（格式 `策略名*/`），CSV/PNG 不入库。
- 结果为 0 时不生成空文件夹/CSV。
- tushare token 直接复用现有脚本中的常量，不要新建配置。

## 四、实现步骤清单

1. 确认模板：代码 + 日期区间，先 SQL 查交易日数、close 首尾与涨跌幅、ma5/vol 是否有 NULL。
2. 加载模板（含 MA5 种子）→ MinMax 归一化序列。
3. 一次性 SQL 读取候选区间全市场数据（JOIN 市值表），Python 内按 ts_code 分组，避免逐股查询。
4. 过滤：最新日有数据 → A股 → 市值 → 窗口长度/字段完整性。
5. 滑动窗口 DTW 打分（先写函数再 benchmark：单次应在毫秒级）。
6. 按 final 分降序，阈值过滤，取 Top N，打印漏斗 + Top 榜。
7. 写 CSV、更新 .gitignore、实跑验证并抽样核对榜首股票的关键数值。

## 五、参考实现

- `find_good_up.py`：长模板（90日）滑动窗口 + 量价双维 DTW 的完整范本。
- `find_similar_ma5.py`：固定短窗口 + argparse 参数化 + 灵活过滤注册表 `STOCK_FILTERS`（新增过滤条件只追加一行）的范本。
- `find_similar_pattern.py`：结构特征（波峰波谷/幅度/量能比）+ 轮廓余弦混合评分的范本，可与 DTW 结合。
