#!/bin/bash
# 每周一到周五17点定时执行股票分析脚本
# 第一批任务：数据更新（按顺序执行）
# 第二批任务：选股分析（待第一批全部完成后按顺序执行）
# 第三批任务：选股结果回测回填
# 第四批任务：报告生成
# 第五批任务：数据与任务监控

set -e

SCRIPT_DIR="/Users/luckboy/Documents/trae_projects/myFirstQT"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
LOG_DIR="${SCRIPT_DIR}/cron_logs"
DATE=$(date +%Y%m%d_%H%M%S)

rm -rf "${LOG_DIR}"
mkdir -p "${LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_DIR}/daily_stock_${DATE}.log"
}

log "=========================================="
log "开始执行每日股票分析任务"
log "=========================================="

cd "${SCRIPT_DIR}"

log "========== 第一批任务：数据更新 =========="


# 步骤1：指数日交易数据
log "[步骤1/24] 开始执行 指数日交易数据 (update_stock_index_daily.py)..."
if ${VENV_PYTHON} update_stock_index_daily.py >> "${LOG_DIR}/update_stock_index_daily_${DATE}.log" 2>&1; then
    log "[步骤1/24] ✅ 指数日交易数据 (update_stock_index_daily.py) 执行成功"
else
    log "[步骤1/24] ❌ 指数日交易数据 (update_stock_index_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤2：股指期货日交易数据
log "[步骤2/24] 开始执行 股指期货日交易数据 (update_stock_index_future_daily.py)..."
if ${VENV_PYTHON} update_stock_index_future_daily.py >> "${LOG_DIR}/update_stock_index_future_daily_${DATE}.log" 2>&1; then
    log "[步骤2/24] ✅ 股指期货日交易数据 (update_stock_index_future_daily.py) 执行成功"
else
    log "[步骤2/24] ❌ 股指期货日交易数据 (update_stock_index_future_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤3：融资融券数据
log "[步骤3/24] 开始执行 融资融券数据 (update_rzrq_ye_daily.py)..."
if ${VENV_PYTHON} update_rzrq_ye_daily.py >> "${LOG_DIR}/update_rzrq_ye_daily_${DATE}.log" 2>&1; then
    log "[步骤3/24] ✅ 融资融券数据 (update_rzrq_ye_daily.py) 执行成功"
else
    log "[步骤3/24] ❌ 融资融券数据 (update_rzrq_ye_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤4：个股交易数据
log "[步骤4/24] 开始执行 个股交易数据 (update_stock_daily.py)..."
if ${VENV_PYTHON} update_stock_daily.py >> "${LOG_DIR}/update_stock_daily_${DATE}.log" 2>&1; then
    log "[步骤4/24] ✅ 个股交易数据 (update_stock_daily.py) 执行成功"
else
    log "[步骤4/24] ❌ 个股交易数据 (update_stock_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤5：股票基础信息
log "[步骤5/24] 开始执行 股票基础信息 (update_stock_info_daily.py)..."
if ${VENV_PYTHON} update_stock_info_daily.py >> "${LOG_DIR}/update_stock_info_daily_${DATE}.log" 2>&1; then
    log "[步骤5/24] ✅ 股票基础信息 (update_stock_info_daily.py) 执行成功"
else
    log "[步骤5/24] ❌ 股票基础信息 (update_stock_info_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤6：股票日基本交易指标数据
log "[步骤6/24] 开始执行 股票日基本交易指标数据 (update_stock_daily_basic_info_daily.py)..."
if ${VENV_PYTHON} update_stock_daily_basic_info_daily.py >> "${LOG_DIR}/update_stock_daily_basic_info_daily_${DATE}.log" 2>&1; then
    log "[步骤6/24] ✅ 股票日基本交易指标数据 (update_stock_daily_basic_info_daily.py) 执行成功"
else
    log "[步骤6/24] ❌ 股票日基本交易指标数据 (update_stock_daily_basic_info_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤7：个股MA5/MA30打标
log "[步骤7/24] 开始执行 个股MA5/MA30打标 (update_stock_daily_ma5_ma30.py)..."
if ${VENV_PYTHON} update_stock_daily_ma5_ma30.py >> "${LOG_DIR}/update_stock_daily_ma5_ma30_${DATE}.log" 2>&1; then
    log "[步骤7/24] ✅ 个股MA5/MA30打标 (update_stock_daily_ma5_ma30.py) 执行成功"
else
    log "[步骤7/24] ❌ 个股MA5/MA30打标 (update_stock_daily_ma5_ma30.py) 执行失败，停止任务"
    exit 1
fi

# 步骤8：个股turning_point打标
log "[步骤8/24] 开始执行 个股turning_point打标 (update_stock_daily_turning_point.py)..."
if ${VENV_PYTHON} update_stock_daily_turning_point.py >> "${LOG_DIR}/update_stock_daily_turning_point_${DATE}.log" 2>&1; then
    log "[步骤8/24] ✅ 个股turning_point打标 (update_stock_daily_turning_point.py) 执行成功"
else
    log "[步骤8/24] ❌ 个股turning_point打标 (update_stock_daily_turning_point.py) 执行失败，停止任务"
    exit 1
fi

# 步骤9：股票技术面指标数据
log "[步骤9/24] 开始执行 股票技术面指标数据 (update_stock_daily_factor.py)..."
if ${VENV_PYTHON} update_stock_daily_factor.py >> "${LOG_DIR}/update_stock_daily_factor_${DATE}.log" 2>&1; then
    log "[步骤9/24] ✅ 股票技术面指标数据 (update_stock_daily_factor.py) 执行成功"
else
    log "[步骤9/24] ❌ 股票技术面指标数据 (update_stock_daily_factor.py) 执行失败，停止任务"
    exit 1
fi

# 步骤10：全市场短线强弱得分回填
log "[步骤10/24] 开始执行 全市场短线强弱得分回填 (update_stock_daily_short_strength.py)..."
if ${VENV_PYTHON} update_stock_daily_short_strength.py >> "${LOG_DIR}/update_stock_daily_short_strength_${DATE}.log" 2>&1; then
    log "[步骤10/24] ✅ 全市场短线强弱得分回填 (update_stock_daily_short_strength.py) 执行成功"
else
    log "[步骤10/24] ❌ 全市场短线强弱得分回填 (update_stock_daily_short_strength.py) 执行失败，停止任务"
    exit 1
fi

# 步骤11：ETF基础信息与日交易数据
log "[步骤11/24] 开始执行 ETF基础信息与日交易数据 (update_etf_daily.py)..."
if ${VENV_PYTHON} update_etf_daily.py >> "${LOG_DIR}/update_etf_daily_${DATE}.log" 2>&1; then
    log "[步骤11/24] ✅ ETF基础信息与日交易数据 (update_etf_daily.py) 执行成功"
else
    log "[步骤11/24] ❌ ETF基础信息与日交易数据 (update_etf_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤12：沪深交易所市场总貌数据
log "[步骤12/24] 开始执行 沪深交易所市场总貌数据 (update_exchange_market_overview.py)..."
if ${VENV_PYTHON} update_exchange_market_overview.py >> "${LOG_DIR}/update_exchange_market_overview_${DATE}.log" 2>&1; then
    log "[步骤12/24] ✅ 沪深交易所市场总貌数据 (update_exchange_market_overview.py) 执行成功"
else
    log "[步骤12/24] ❌ 沪深交易所市场总貌数据 (update_exchange_market_overview.py) 执行失败，停止任务"
    exit 1
fi


log "========== 第二批任务：选股策略 =========="


# 步骤13：近120日区间突破策略
log "[步骤13/24] 开始执行 近120日区间突破策略 (select_newhigh_in_120d.py)..."
if ${VENV_PYTHON} select_newhigh_in_120d.py >> "${LOG_DIR}/select_newhigh_in_120d_${DATE}.log" 2>&1; then
    log "[步骤13/24] ✅ 近120日区间突破策略 (select_newhigh_in_120d.py) 执行成功"
else
    log "[步骤13/24] ❌ 近120日区间突破策略 (select_newhigh_in_120d.py) 执行失败，停止任务"
    exit 1
fi

# 步骤14：V形反转策略
log "[步骤14/24] 开始执行 V形反转策略 (select_v_reverse.py)..."
if ${VENV_PYTHON} select_v_reverse.py >> "${LOG_DIR}/select_v_reverse_${DATE}.log" 2>&1; then
    log "[步骤14/24] ✅ V形反转策略 (select_v_reverse.py) 执行成功"
else
    log "[步骤14/24] ❌ V形反转策略 (select_v_reverse.py) 执行失败，停止任务"
    exit 1
fi

# 步骤15：二浪启动策略
log "[步骤15/24] 开始执行 二浪启动策略 (select_2wave_up.py)..."
if ${VENV_PYTHON} select_2wave_up.py >> "${LOG_DIR}/select_2wave_up_${DATE}.log" 2>&1; then
    log "[步骤15/24] ✅ 二浪启动策略 (select_2wave_up.py) 执行成功"
else
    log "[步骤15/24] ❌ 二浪启动策略 (select_2wave_up.py) 执行失败，停止任务"
    exit 1
fi

# 步骤16：当日涨停股票策略
log "[步骤16/24] 开始执行 当日涨停股票策略 (select_limitup_1d.py)..."
if ${VENV_PYTHON} select_limitup_1d.py >> "${LOG_DIR}/select_limitup_1d_${DATE}.log" 2>&1; then
    log "[步骤16/24] ✅ 当日涨停股票策略 (select_limitup_1d.py) 执行成功"
else
    log "[步骤16/24] ❌ 当日涨停股票策略 (select_limitup_1d.py) 执行失败，停止任务"
    exit 1
fi

# 步骤17：二浪日线选股策略
log "[步骤17/24] 开始执行 二浪日线选股策略 (select_2wave_daily.py)..."
if ${VENV_PYTHON} select_2wave_daily.py >> "${LOG_DIR}/select_2wave_daily_${DATE}.log" 2>&1; then
    log "[步骤17/24] ✅ 二浪日线选股策略 (select_2wave_daily.py) 执行成功"
else
    log "[步骤17/24] ❌ 二浪日线选股策略 (select_2wave_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤18：W23二浪选股策略
log "[步骤18/24] 开始执行 W23二浪选股策略 (select_2wave_w23.py)..."
if ${VENV_PYTHON} select_2wave_w23.py >> "${LOG_DIR}/select_2wave_w23_${DATE}.log" 2>&1; then
    log "[步骤18/24] ✅ W23二浪选股策略 (select_2wave_w23.py) 执行成功"
else
    log "[步骤18/24] ❌ W23二浪选股策略 (select_2wave_w23.py) 执行失败，停止任务"
    exit 1
fi

log "========== 第三批任务：选股结果回测回填 =========="

# 步骤19：选股结果回测回填
log "[步骤19/24] 开始执行 选股结果回测回填 (strategy_results.py)..."
if ${VENV_PYTHON} strategy_results.py >> "${LOG_DIR}/strategy_results_${DATE}.log" 2>&1; then
    log "[步骤19/24] ✅ 选股结果回测回填 (strategy_results.py) 执行成功"
else
    log "[步骤19/24] ❌ 选股结果回测回填 (strategy_results.py) 执行失败，停止任务"
    exit 1
fi

log "========== 第四批任务：报告生成 =========="

# 步骤20：大盘整体情况报告
log "[步骤20/24] 开始执行 大盘整体情况报告 (report_stock_overall.py)..."
if ${VENV_PYTHON} report_stock_overall.py >> "${LOG_DIR}/report_stock_overall_${DATE}.log" 2>&1; then
    log "[步骤20/24] ✅ 大盘整体情况报告 (report_stock_overall.py) 执行成功"
else
    log "[步骤20/24] ❌ 大盘整体情况报告 (report_stock_overall.py) 执行失败，停止任务"
    exit 1
fi

# 步骤21：大盘指标体系
log "[步骤21/24] 开始执行 大盘指标体系 (report_market_overview_metricx.py)..."
if ${VENV_PYTHON} report_market_overview_metricx.py >> "${LOG_DIR}/report_market_overview_metricx_${DATE}.log" 2>&1; then
    log "[步骤21/24] ✅ 大盘指标体系 (report_market_overview_metricx.py) 执行成功"
else
    log "[步骤21/24] ❌ 大盘指标体系 (report_market_overview_metricx.py) 执行失败，停止任务"
    exit 1
fi

# 步骤22：A股大盘风险指数MRI
log "[步骤22/24] 开始执行 A股大盘风险指数MRI (report_market_risk_metricx.py)..."
if ${VENV_PYTHON} report_market_risk_metricx.py >> "${LOG_DIR}/report_market_risk_metricx_${DATE}.log" 2>&1; then
    log "[步骤22/24] ✅ A股大盘风险指数MRI (report_market_risk_metricx.py) 执行成功"
else
    log "[步骤22/24] ❌ A股大盘风险指数MRI (report_market_risk_metricx.py) 执行失败，停止任务"
    exit 1
fi

log "========== 第五批任务：任务与数据监控 =========="

# 步骤23：数据更新监控
log "[步骤23/24] 开始执行 数据更新监控 (monitor_stock_data.py)..."
if ${VENV_PYTHON} monitor_stock_data.py >> "${LOG_DIR}/monitor_stock_data_${DATE}.log" 2>&1; then
    log "[步骤23/24] ✅ 数据更新监控 (monitor_stock_data.py) 执行成功"
else
    log "[步骤23/24] ❌ 数据更新监控 (monitor_stock_data.py) 执行失败，停止任务"
    exit 1
fi

# 步骤24：任务运行监控
log "[步骤24/24] 开始执行 任务运行监控 (monitor_run_daily_tasks.py)..."
if ${VENV_PYTHON} monitor_run_daily_tasks.py >> "${LOG_DIR}/monitor_run_daily_tasks_${DATE}.log" 2>&1; then
    log "[步骤24/24] ✅ 任务运行监控 (monitor_run_daily_tasks.py) 执行成功"
else
    log "[步骤24/24] ❌ 任务运行监控 (monitor_run_daily_tasks.py) 执行失败，停止任务"
    exit 1
fi

log "=========================================="
log "🎉 所有任务执行完成！"
log "=========================================="
