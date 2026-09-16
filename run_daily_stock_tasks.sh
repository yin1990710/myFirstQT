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
log "[步骤1/20] 开始执行 指数日交易数据 (update_stock_index_daily.py)..."
if ${VENV_PYTHON} update_stock_index_daily.py >> "${LOG_DIR}/update_stock_index_daily_${DATE}.log" 2>&1; then
    log "[步骤1/20] ✅ 指数日交易数据 (update_stock_index_daily.py) 执行成功"
else
    log "[步骤1/20] ❌ 指数日交易数据 (update_stock_index_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤2：股指期货日交易数据
log "[步骤2/20] 开始执行 股指期货日交易数据 (update_stock_index_future_daily.py)..."
if ${VENV_PYTHON} update_stock_index_future_daily.py >> "${LOG_DIR}/update_stock_index_future_daily_${DATE}.log" 2>&1; then
    log "[步骤2/20] ✅ 股指期货日交易数据 (update_stock_index_future_daily.py) 执行成功"
else
    log "[步骤2/20] ❌ 股指期货日交易数据 (update_stock_index_future_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤3：融资融券数据
log "[步骤3/20] 开始执行 融资融券数据 (update_rzrq_ye_daily.py)..."
if ${VENV_PYTHON} update_rzrq_ye_daily.py >> "${LOG_DIR}/update_rzrq_ye_daily_${DATE}.log" 2>&1; then
    log "[步骤3/20] ✅ 融资融券数据 (update_rzrq_ye_daily.py) 执行成功"
else
    log "[步骤3/20] ❌ 融资融券数据 (update_rzrq_ye_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤4：个股交易数据
log "[步骤4/20] 开始执行 个股交易数据 (update_stock_daily.py)..."
if ${VENV_PYTHON} update_stock_daily.py >> "${LOG_DIR}/update_stock_daily_${DATE}.log" 2>&1; then
    log "[步骤4/20] ✅ 个股交易数据 (update_stock_daily.py) 执行成功"
else
    log "[步骤4/20] ❌ 个股交易数据 (update_stock_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤5：股票基础信息
log "[步骤5/20] 开始执行 股票基础信息 (update_stock_info_daily.py)..."
if ${VENV_PYTHON} update_stock_info_daily.py >> "${LOG_DIR}/update_stock_info_daily_${DATE}.log" 2>&1; then
    log "[步骤5/20] ✅ 股票基础信息 (update_stock_info_daily.py) 执行成功"
else
    log "[步骤5/20] ❌ 股票基础信息 (update_stock_info_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤6：股票日基本交易指标数据
log "[步骤6/20] 开始执行 股票日基本交易指标数据 (update_stock_daily_basic_info_daily.py)..."
if ${VENV_PYTHON} update_stock_daily_basic_info_daily.py >> "${LOG_DIR}/update_stock_daily_basic_info_daily_${DATE}.log" 2>&1; then
    log "[步骤6/20] ✅ 股票日基本交易指标数据 (update_stock_daily_basic_info_daily.py) 执行成功"
else
    log "[步骤6/20] ❌ 股票日基本交易指标数据 (update_stock_daily_basic_info_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤7：个股MA5/MA30打标
log "[步骤7/20] 开始执行 个股MA5/MA30打标 (update_stock_daily_ma5_ma30.py)..."
if ${VENV_PYTHON} update_stock_daily_ma5_ma30.py >> "${LOG_DIR}/update_stock_daily_ma5_ma30_${DATE}.log" 2>&1; then
    log "[步骤7/20] ✅ 个股MA5/MA30打标 (update_stock_daily_ma5_ma30.py) 执行成功"
else
    log "[步骤7/20] ❌ 个股MA5/MA30打标 (update_stock_daily_ma5_ma30.py) 执行失败，停止任务"
    exit 1
fi

# 步骤8：个股turning_point打标
log "[步骤8/20] 开始执行 个股turning_point打标 (update_stock_daily_turning_point.py)..."
if ${VENV_PYTHON} update_stock_daily_turning_point.py >> "${LOG_DIR}/update_stock_daily_turning_point_${DATE}.log" 2>&1; then
    log "[步骤8/20] ✅ 个股turning_point打标 (update_stock_daily_turning_point.py) 执行成功"
else
    log "[步骤8/20] ❌ 个股turning_point打标 (update_stock_daily_turning_point.py) 执行失败，停止任务"
    exit 1
fi

# 步骤9：股票技术面指标数据
log "[步骤9/20] 开始执行 股票技术面指标数据 (update_stock_daily_factor.py)..."
if ${VENV_PYTHON} update_stock_daily_factor.py >> "${LOG_DIR}/update_stock_daily_factor_${DATE}.log" 2>&1; then
    log "[步骤9/20] ✅ 股票技术面指标数据 (update_stock_daily_factor.py) 执行成功"
else
    log "[步骤9/20] ❌ 股票技术面指标数据 (update_stock_daily_factor.py) 执行失败，停止任务"
    exit 1
fi

# 步骤10：ETF基础信息与日交易数据
log "[步骤10/20] 开始执行 ETF基础信息与日交易数据 (update_etf_daily.py)..."
if ${VENV_PYTHON} update_etf_daily.py >> "${LOG_DIR}/update_etf_daily_${DATE}.log" 2>&1; then
    log "[步骤10/20] ✅ ETF基础信息与日交易数据 (update_etf_daily.py) 执行成功"
else
    log "[步骤10/20] ❌ ETF基础信息与日交易数据 (update_etf_daily.py) 执行失败，停止任务"
    exit 1
fi


log "========== 第二批任务：选股策略 =========="


# 步骤11：近120日区间突破策略
log "[步骤11/20] 开始执行 近120日区间突破策略 (select_newhigh_in_120d.py)..."
if ${VENV_PYTHON} select_newhigh_in_120d.py >> "${LOG_DIR}/select_newhigh_in_120d_${DATE}.log" 2>&1; then
    log "[步骤11/20] ✅ 近120日区间突破策略 (select_newhigh_in_120d.py) 执行成功"
else
    log "[步骤11/20] ❌ 近120日区间突破策略 (select_newhigh_in_120d.py) 执行失败，停止任务"
    exit 1
fi

# 步骤12：V形反转策略
log "[步骤12/20] 开始执行 V形反转策略 (select_v_reverse.py)..."
if ${VENV_PYTHON} select_v_reverse.py >> "${LOG_DIR}/select_v_reverse_${DATE}.log" 2>&1; then
    log "[步骤12/20] ✅ V形反转策略 (select_v_reverse.py) 执行成功"
else
    log "[步骤12/20] ❌ V形反转策略 (select_v_reverse.py) 执行失败，停止任务"
    exit 1
fi

# 步骤13：2浪启动策略
log "[步骤13/20] 开始执行 2浪启动策略 (select_2wave_up.py)..."
if ${VENV_PYTHON} select_2wave_up.py >> "${LOG_DIR}/select_2wave_up_${DATE}.log" 2>&1; then
    log "[步骤13/20] ✅ 2浪启动策略 (select_2wave_up.py) 执行成功"
else
    log "[步骤13/20] ❌ 2浪启动策略 (select_2wave_up.py) 执行失败，停止任务"
    exit 1
fi

# 步骤14：当日涨停股票策略
log "[步骤14/20] 开始执行 当日涨停股票策略 (select_limitup_1d.py)..."
if ${VENV_PYTHON} select_limitup_1d.py >> "${LOG_DIR}/select_limitup_1d_${DATE}.log" 2>&1; then
    log "[步骤14/20] ✅ 当日涨停股票策略 (select_limitup_1d.py) 执行成功"
else
    log "[步骤14/20] ❌ 当日涨停股票策略 (select_limitup_1d.py) 执行失败，停止任务"
    exit 1
fi

# 步骤15：2浪趋势选股策略
log "[步骤15/20] 开始执行 2浪趋势选股策略 (select_2wave_daily.py)..."
if ${VENV_PYTHON} select_2wave_daily.py >> "${LOG_DIR}/select_2wave_daily_${DATE}.log" 2>&1; then
    log "[步骤15/20] ✅ 2浪趋势选股策略 (select_2wave_daily.py) 执行成功"
else
    log "[步骤15/20] ❌ 2浪趋势选股策略 (select_2wave_daily.py) 执行失败，停止任务"
    exit 1
fi

# 步骤16：2浪趋势选股-23日策略
log "[步骤16/20] 开始执行 2浪趋势选股-23日策略 (select_2wave_w23.py)..."
if ${VENV_PYTHON} select_2wave_w23.py >> "${LOG_DIR}/select_2wave_w23_${DATE}.log" 2>&1; then
    log "[步骤16/20] ✅ 2浪趋势选股-23日策略 (select_2wave_w23.py) 执行成功"
else
    log "[步骤16/20] ❌ 2浪趋势选股-23日策略 (select_2wave_w23.py) 执行失败，停止任务"
    exit 1
fi

log "========== 第三批任务：选股结果回测回填 =========="

# 步骤17：选股结果回测回填
log "[步骤17/20] 开始执行 选股结果回测回填 (strategy_results.py)..."
if ${VENV_PYTHON} strategy_results.py >> "${LOG_DIR}/strategy_results_${DATE}.log" 2>&1; then
    log "[步骤17/20] ✅ 选股结果回测回填 (strategy_results.py) 执行成功"
else
    log "[步骤17/20] ❌ 选股结果回测回填 (strategy_results.py) 执行失败，停止任务"
    exit 1
fi

log "========== 第四批任务：大盘整体情况报告 =========="

# 步骤18：大盘整体情况报告
log "[步骤18/20] 开始执行 大盘整体情况报告 (report_stock_overall.py)..."
if ${VENV_PYTHON} report_stock_overall.py >> "${LOG_DIR}/report_stock_overall_${DATE}.log" 2>&1; then
    log "[步骤18/20] ✅ 大盘整体情况报告 (report_stock_overall.py) 执行成功"
else
    log "[步骤18/20] ❌ 大盘整体情况报告 (report_stock_overall.py) 执行失败，停止任务"
    exit 1
fi

log "========== 第五批任务：任务与数据监控 =========="

# 步骤19：数据更新监控
log "[步骤19/20] 开始执行 数据更新监控 (monitor_stock_data.py)..."
if ${VENV_PYTHON} monitor_stock_data.py >> "${LOG_DIR}/monitor_stock_data_${DATE}.log" 2>&1; then
    log "[步骤19/20] ✅ 数据更新监控 (monitor_stock_data.py) 执行成功"
else
    log "[步骤19/20] ❌ 数据更新监控 (monitor_stock_data.py) 执行失败，停止任务"
    exit 1
fi

# 步骤20：任务运行监控
log "[步骤20/20] 开始执行 任务运行监控 (monitor_run_daily_tasks.py)..."
if ${VENV_PYTHON} monitor_run_daily_tasks.py >> "${LOG_DIR}/monitor_run_daily_tasks_${DATE}.log" 2>&1; then
    log "[步骤20/20] ✅ 任务运行监控 (monitor_run_daily_tasks.py) 执行成功"
else
    log "[步骤20/20] ❌ 任务运行监控 (monitor_run_daily_tasks.py) 执行失败，停止任务"
    exit 1
fi

log "=========================================="
log "🎉 所有任务执行完成！"
log "=========================================="
