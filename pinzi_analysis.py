#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
from typing import List, Dict, Tuple

def analyze_pinzi_pattern(data: pd.DataFrame) -> pd.DataFrame:
    """
    品字定向法分析主函数
    
    根据连续3个交易日的价格关系识别波峰、波谷、波中
    
    :param data: 包含股票日线数据的DataFrame，需包含 open, close 列
    :return: 添加了 'pattern' 列的DataFrame，值为 '波峰', '波谷', '波中'
    """
    # 复制数据以避免修改原数据
    result = data.copy().reset_index(drop=True)
    
    # 初始化 pattern 列
    result['pattern'] = '波中'
    
    # 定义波峰波谷类型
    PEAK = '波峰'
    VALLEY = '波谷'
    MIDDLE = '波中'
    
    n = len(result)
    
    for i in range(1, n - 1):
        # 获取连续三个交易日的数据
        prev = result.iloc[i - 1]   # 第1个交易日
        curr = result.iloc[i]       # 第2个交易日（待判断日）
        next_day = result.iloc[i + 1]  # 第3个交易日
        
        # 检查吸收规则
        # 如果第3个交易日的开盘价高于第2个且收盘价低于第2个，则顺延
        if next_day['open'] > curr['open'] and next_day['close'] < curr['close']:
            # 需要顺延，跳过当前判断
            continue
        
        # 判断阳线/阴线
        curr_is_up = curr['close'] >= curr['open']
        prev_is_up = prev['close'] >= prev['open']
        
        # 波峰判断：第2个交易日收盘价高于第1个，第3个低于第2个
        if curr['close'] > prev['close'] and next_day['close'] < curr['close']:
            # 根据阳线/阴线细化判断
            if curr_is_up:
                # 阳线情况：收盘价 > 开盘价
                if prev['close'] >= prev['open']:
                    # 连续阳线，第2个为波峰
                    result.loc[i, 'pattern'] = PEAK
                else:
                    # 前阴后阳，需进一步判断
                    if curr['close'] > prev['open']:
                        result.loc[i, 'pattern'] = PEAK
            else:
                # 阴线情况
                result.loc[i, 'pattern'] = PEAK
        
        # 波谷判断：第2个交易日收盘价低于第1个，第3个高于第2个
        elif curr['close'] < prev['close'] and next_day['close'] > curr['close']:
            if curr_is_up:
                # 阳线情况
                result.loc[i, 'pattern'] = VALLEY
            else:
                # 阴线情况
                if prev['close'] < prev['open']:
                    # 连续阴线，第2个为波谷
                    result.loc[i, 'pattern'] = VALLEY
                else:
                    # 前阳后阴，需进一步判断
                    if curr['close'] < prev['open']:
                        result.loc[i, 'pattern'] = VALLEY
    
    return result

def generate_trade_signals(data: pd.DataFrame) -> pd.DataFrame:
    """
    根据波峰波谷生成交易信号
    
    :param data: 包含 pattern 列的DataFrame
    :return: 添加了 'signal' 列的DataFrame，值为 'buy', 'sell', ''
    """
    result = data.copy()
    result['signal'] = ''
    
    n = len(result)
    
    for i in range(n):
        pattern = result.iloc[i]['pattern']
        
        if pattern == '波谷' and i < n - 1:
            # 波谷下一个交易日开盘买入
            result.loc[i + 1, 'signal'] = 'buy'
        elif pattern == '波峰' and i < n - 1:
            # 波峰下一个交易日开盘卖出
            result.loc[i + 1, 'signal'] = 'sell'
    
    return result

def calculate_profit(data: pd.DataFrame) -> float:
    """
    计算基于品字定向法的收益率
    
    :param data: 包含 signal 和 open 列的DataFrame
    :return: 累计收益率（百分比）
    """
    total_profit = 0.0
    buy_price = None
    in_position = False
    
    for _, row in data.iterrows():
        if row['signal'] == 'buy' and not in_position:
            buy_price = row['open']
            in_position = True
        elif row['signal'] == 'sell' and in_position:
            sell_price = row['open']
            profit = (sell_price - buy_price) / buy_price * 100
            total_profit += profit
            in_position = False
            buy_price = None
    
    return total_profit

def find_wave_pairs(data: pd.DataFrame) -> List[Dict]:
    """
    找出波峰波谷配对
    
    :param data: 包含 pattern 列的DataFrame
    :return: 波峰波谷配对列表
    """
    pairs = []
    valley_dates = data[data['pattern'] == '波谷'].index.tolist()
    peak_dates = data[data['pattern'] == '波峰'].index.tolist()
    
    v_idx = 0
    p_idx = 0
    
    while v_idx < len(valley_dates) and p_idx < len(peak_dates):
        valley_idx = valley_dates[v_idx]
        peak_idx = peak_dates[p_idx]
        
        if valley_idx < peak_idx:
            # 找到一对有效的波谷-波峰
            pairs.append({
                'valley_idx': valley_idx,
                'valley_date': data.iloc[valley_idx]['trade_date'],
                'valley_price': data.iloc[valley_idx]['close'],
                'peak_idx': peak_idx,
                'peak_date': data.iloc[peak_idx]['trade_date'],
                'peak_price': data.iloc[peak_idx]['close']
            })
            v_idx += 1
            p_idx += 1
        else:
            p_idx += 1
    
    return pairs

def analyze_wave_characteristics(data: pd.DataFrame) -> Dict:
    """
    分析波峰波谷特征统计
    
    :param data: 包含 pattern 列的DataFrame
    :return: 统计字典
    """
    peaks = data[data['pattern'] == '波峰']
    valleys = data[data['pattern'] == '波谷']
    middles = data[data['pattern'] == '波中']
    
    stats = {
        'total_days': len(data),
        'peak_count': len(peaks),
        'valley_count': len(valleys),
        'middle_count': len(middles),
        'peak_ratio': len(peaks) / len(data) * 100,
        'valley_ratio': len(valleys) / len(data) * 100,
        'middle_ratio': len(middles) / len(data) * 100
    }
    
    return stats

def plot_pinzi_analysis(data: pd.DataFrame, output_file: str = None):
    """
    可视化品字定向法分析结果（生成HTML图表）
    
    :param data: 包含 pattern 和 signal 列的DataFrame
    :param output_file: 输出HTML文件名，默认为 None（不保存）
    :return: HTML内容
    """
    html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>品字定向法分析结果</title>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ color: #333; text-align: center; }}
        .stats {{ display: flex; gap: 20px; justify-content: center; margin-bottom: 30px; }}
        .stat-card {{ background: #f5f5f5; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #333; }}
        .stat-label {{ font-size: 14px; color: #666; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: center; }}
        th {{ background: #f5f5f5; font-weight: bold; }}
        .peak {{ background: #ffeaea; color: #d32f2f; }}
        .valley {{ background: #e8f5e9; color: #388e3c; }}
        .buy {{ background: #e3f2fd; color: #1976d2; }}
        .sell {{ background: #fff3e0; color: #f57c00; }}
        .chart-container {{ margin-top: 30px; }}
        .candle {{ display: flex; align-items: center; gap: 2px; }}
        .candle-body {{ width: 20px; height: 30px; border: 1px solid #333; }}
        .candle-up {{ background: #ef5350; }}
        .candle-down {{ background: #66bb6a; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 品字定向法分析结果</h1>
        
        <!-- 统计卡片 -->
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{len(data)}</div>
                <div class="stat-label">总交易日</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(data[data['pattern']=='波峰'])}</div>
                <div class="stat-label">波峰数量</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(data[data['pattern']=='波谷'])}</div>
                <div class="stat-label">波谷数量</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{calculate_profit(data):.2f}%</div>
                <div class="stat-label">累计收益率</div>
            </div>
        </div>
        
        <!-- 数据表格 -->
        <table>
            <thead>
                <tr>
                    <th>日期</th>
                    <th>开盘</th>
                    <th>最高</th>
                    <th>最低</th>
                    <th>收盘</th>
                    <th>涨跌幅</th>
                    <th>走势类型</th>
                    <th>交易信号</th>
                </tr>
            </thead>
            <tbody>
    """
    
    for _, row in data.iterrows():
        pattern_class = {
            '波峰': 'peak',
            '波谷': 'valley',
            '波中': ''
        }[row['pattern']]
        
        signal_class = {
            'buy': 'buy',
            'sell': 'sell',
            '': ''
        }[row['signal']]
        
        signal_icon = {
            'buy': '🔵 买入',
            'sell': '🔴 卖出',
            '': ''
        }[row['signal']]
        
        html_content += f"""
                <tr>
                    <td>{row['trade_date']}</td>
                    <td>{row['open']:.2f}</td>
                    <td>{row['high']:.2f}</td>
                    <td>{row['low']:.2f}</td>
                    <td>{row['close']:.2f}</td>
                    <td>{row['pct_chg']:.2f}%</td>
                    <td class="{pattern_class}">{row['pattern']}</td>
                    <td class="{signal_class}">{signal_icon}</td>
                </tr>
        """
    
    html_content += """
            </tbody>
        </table>
    </div>
</body>
</html>
    """
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"✅ 分析报告已保存到: {output_file}")
    
    return html_content

def main():
    """
    示例：品字定向法分析流程
    """
    # 创建示例数据
    sample_data = pd.DataFrame({
        'trade_date': ['20260401', '20260402', '20260403', '20260404', '20260407',
                       '20260408', '20260409', '20260410', '20260411', '20260412'],
        'open': [30.0, 30.5, 31.0, 30.8, 30.2, 29.8, 30.1, 30.5, 31.0, 31.2],
        'high': [30.8, 31.2, 31.5, 31.0, 30.5, 30.2, 30.8, 31.0, 31.5, 31.8],
        'low': [29.8, 30.3, 30.8, 30.5, 29.8, 29.5, 29.9, 30.2, 30.8, 31.0],
        'close': [30.5, 31.0, 30.9, 30.3, 30.0, 30.0, 30.5, 30.8, 31.2, 31.5],
        'pct_chg': [0.0, 1.64, -0.32, -1.94, -1.0, 0.0, 1.67, 0.98, 1.29, 0.96]
    })
    
    print("📊 品字定向法分析示例")
    print("=" * 50)
    
    # 执行品字定向法分析
    analyzed_data = analyze_pinzi_pattern(sample_data)
    
    # 生成交易信号
    analyzed_data = generate_trade_signals(analyzed_data)
    
    # 计算收益率
    profit = calculate_profit(analyzed_data)
    
    # 分析波峰波谷特征
    stats = analyze_wave_characteristics(analyzed_data)
    
    # 打印统计结果
    print(f"总交易日: {stats['total_days']}")
    print(f"波峰数量: {stats['peak_count']}")
    print(f"波谷数量: {stats['valley_count']}")
    print(f"累计收益率: {profit:.2f}%")
    
    # 打印详细结果
    print("\n📋 分析结果:")
    print(analyzed_data[['trade_date', 'close', 'pattern', 'signal']].to_string(index=False))
    
    # 生成HTML报告
    plot_pinzi_analysis(analyzed_data, 'pinzi_analysis_report.html')

if __name__ == "__main__":
    main()