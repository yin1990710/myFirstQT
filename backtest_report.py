#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
回测结果查询页面生成 (backtest_report.py)

核心功能：
  读取 strategy_selected_stock_daily_t 表全部回测数据，生成自包含的交互式
  HTML 页面 backtest_report.html：
    1. 页面上可选择策略名称、起止日期范围（YYYYMMDD）进行查询；
    2. 展示该策略下所有股票的回测结果：股票代码、交易日、策略、是否选中、
       最大涨幅/最大跌幅（10日、20日窗口）；
    3. 点击表头可按任意涨跌幅列（以及交易日）升序/降序排序，空值始终排在最后。

用法：
  python3 backtest_report.py     # 生成 pages/backtest_report.html，用浏览器打开即可
"""

import sys
import os
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mysql_connection import get_mysql_connection, close_connection

# 输出目录：项目下 pages/，已存在则直接复用
PAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pages')
os.makedirs(PAGE_DIR, exist_ok=True)
OUTPUT_HTML = os.path.join(PAGE_DIR, 'backtest_report.html')

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>选股策略回测结果查询</title>
<style>
  body { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; background: #f7f8fa; color: #222; }
  h1 { font-size: 20px; margin: 0 0 16px; }
  .bar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; background: #fff; padding: 12px 16px;
         border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 14px; }
  .bar label { font-size: 13px; color: #555; }
  .bar select, .bar input { padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; }
  .bar input { width: 100px; }
  .bar button { padding: 6px 14px; border: none; border-radius: 6px; background: #1a73e8; color: #fff;
                font-size: 13px; cursor: pointer; }
  .bar button.reset { background: #888; }
  #count { font-size: 13px; color: #666; }
  table { border-collapse: collapse; width: 100%; background: #fff; border-radius: 8px; overflow: hidden;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); font-size: 13px; }
  th, td { padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right; white-space: nowrap; }
  th:nth-child(1), td:nth-child(1), th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) { text-align: left; }
  thead th { background: #1a73e8; color: #fff; cursor: pointer; user-select: none; position: sticky; top: 0; }
  thead th:hover { background: #1558b0; }
  thead th .arrow { font-size: 10px; margin-left: 4px; }
  tbody tr:hover { background: #eef4ff; }
  .up { color: #d93025; }
  .down { color: #0f9d58; }
  .null { color: #bbb; }
  .strat { color: #888; font-size: 12px; }
</style>
</head>
<body>
<h1>📊 选股策略回测结果查询</h1>
<div class="bar">
  <label>策略 <select id="strategy"></select></label>
  <label>起始日期 <input id="startDate" placeholder="YYYYMMDD"></label>
  <label>结束日期 <input id="endDate" placeholder="YYYYMMDD"></label>
  <button onclick="render()">查询</button>
  <button class="reset" onclick="resetFilters()">重置</button>
  <span id="count"></span>
</div>
<table>
  <thead><tr id="headRow"></tr></thead>
  <tbody id="body"></tbody>
</table>
<script>
const ROWS = __ROWS__;
const STRATEGIES = __STRATEGIES__;
const COLS = [
  {key: 'ts_code',      label: '股票代码',   type: 'str'},
  {key: 'trade_date',   label: '交易日',     type: 'str'},
  {key: 'strategy',     label: '策略',       type: 'str'},
  {key: 'selected',     label: '是否选中',   type: 'num'},
  {key: 'max_gain_10d', label: '10日最大涨幅%', type: 'pct'},
  {key: 'max_down_10d', label: '10日最大跌幅%', type: 'pct'},
  {key: 'max_down_20d', label: '20日最大跌幅%', type: 'pct'},
  {key: 'max_gain_20d', label: '20日最大涨幅%', type: 'pct'},
];
let sortKey = 'trade_date', sortDir = -1;

const sel = document.getElementById('strategy');
['全部策略', ...STRATEGIES].forEach(s => {
  const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o);
});
document.getElementById('headRow').innerHTML = COLS.map(c =>
  `<th data-key="${c.key}">${c.label}<span class="arrow"></span></th>`).join('');
document.querySelectorAll('th').forEach(th => th.onclick = () => {
  const k = th.dataset.key;
  if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = (k === 'ts_code' || k === 'trade_date') ? -1 : 1; }
  render();
});

function resetFilters() {
  sel.value = '全部策略';
  document.getElementById('startDate').value = '';
  document.getElementById('endDate').value = '';
  render();
}

function fmt(v, type) {
  if (v === null || v === undefined) return '<span class="null">—</span>';
  if (type === 'pct') {
    const cls = v > 0 ? 'up' : (v < 0 ? 'down' : '');
    return `<span class="${cls}">${v.toFixed(2)}</span>`;
  }
  return v;
}

function render() {
  const st = sel.value, s = document.getElementById('startDate').value.trim(),
        e = document.getElementById('endDate').value.trim();
  let rows = ROWS.filter(r =>
    (st === '全部策略' || r.strategy.split(',').includes(st)) &&
    (!s || r.trade_date >= s) && (!e || r.trade_date <= e));
  const col = COLS.find(c => c.key === sortKey);
  rows.sort((a, b) => {
    const va = a[sortKey], vb = b[sortKey];
    if (va === null || va === undefined) return 1;   // 空值排最后
    if (vb === null || vb === undefined) return -1;
    if (col.type === 'str') return va < vb ? -sortDir : (va > vb ? sortDir : 0);
    return (va - vb) * sortDir;
  });
  document.querySelectorAll('th .arrow').forEach(a => a.textContent = '');
  const th = document.querySelector(`th[data-key="${sortKey}"] .arrow`);
  if (th) th.textContent = sortDir === 1 ? '▲' : '▼';
  document.getElementById('count').textContent = `共 ${rows.length} 条记录`;
  document.getElementById('body').innerHTML = rows.map(r => '<tr>' + COLS.map(c => {
    if (c.key === 'strategy') return `<td class="strat">${r.strategy}</td>`;
    if (c.key === 'selected') return `<td>${r.selected === 1 ? '是' : '否'}</td>`;
    return `<td>${fmt(r[c.key], c.type)}</td>`;
  }).join('') + '</tr>').join('');
}
render();
</script>
</body>
</html>
"""


def main():
    print("=" * 80)
    print("📊 生成回测结果查询页面 (backtest_report.py)")
    print("=" * 80)

    conn = get_mysql_connection()
    if not conn:
        print("❌ 数据库连接失败")
        return
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT ts_code, trade_date, strategy, selected,
                       max_gain_10d, max_down_10d, max_down_20d, max_gain_20d
                FROM strategy_selected_stock_daily_t
                ORDER BY trade_date DESC, ts_code
            """)
            rows = cursor.fetchall()
    finally:
        close_connection(conn)

    # strategy 逗号串拆分为独立策略名清单
    strategies = sorted({s.strip() for r in rows for s in (r['strategy'] or '').split(',') if s.strip()})
    for r in rows:
        for k in ('max_gain_10d', 'max_down_10d', 'max_down_20d', 'max_gain_20d'):
            if r[k] is not None:
                r[k] = float(r[k])

    html = (HTML_TEMPLATE
            .replace('__ROWS__', json.dumps(rows, ensure_ascii=False))
            .replace('__STRATEGIES__', json.dumps(strategies, ensure_ascii=False)))

    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ 已生成 {OUTPUT_HTML}")
    print(f"   - 共 {len(rows)} 条记录，{len(strategies)} 个策略: {', '.join(strategies)}")
    print("   - 用浏览器打开该文件即可交互查询（选择策略/日期范围、点击表头排序）")


if __name__ == "__main__":
    main()
