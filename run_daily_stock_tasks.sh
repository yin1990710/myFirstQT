#!/bin/bash
# 每周一到周五17点定时执行股票分析脚本
# 第一批任务：数据更新（按顺序执行）
# 第二批任务：选股分析（待第一批全部完成后按顺序执行）

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


# 步骤1: 执行 update_industry_daily.py（行业日数据）
# log "[步骤1/18] 开始执行 update_industry_daily.py..."
# if ${VENV_PYTHON} update_industry_daily.py >> "${LOG_DIR}/update_industry_daily_${DATE}.log" 2>&1; then
#     log "[步骤1/18] ✅ update_industry_daily.py 执行成功"
# else
#     log "[步骤1/18] ❌ update_industry_daily.py 执行失败，停止任务"
#     exit 1
# fi

# 步骤2: 执行 update_stock_index_daily.py（指数日数据）
log "[步骤2/18] 开始执行 update_stock_index_daily.py..."
if ${VENV_PYTHON} update_stock_index_daily.py >> "${LOG_DIR}/update_stock_index_daily_${DATE}.log" 2>&1; then
    log "[步骤2/18] ✅ update_stock_index_daily.py 执行成功"
else
    log "[步骤2/18] ❌ update_stock_index_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤3: 执行 update_stock_index_future_daily.py（股指期货日数据）
log "[步骤3/18] 开始执行 update_stock_index_future_daily.py..."
if ${VENV_PYTHON} update_stock_index_future_daily.py >> "${LOG_DIR}/update_stock_index_future_daily_${DATE}.log" 2>&1; then
    log "[步骤3/18] ✅ update_stock_index_future_daily.py 执行成功"
else
    log "[步骤3/18] ❌ update_stock_index_future_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤4: 执行 update_rzrq_daily.py（融资融券数据）
log "[步骤4/18] 开始执行 update_rzrq_daily.py..."
if ${VENV_PYTHON} update_rzrq_daily.py >> "${LOG_DIR}/update_rzrq_daily_${DATE}.log" 2>&1; then
    log "[步骤4/18] ✅ update_rzrq_daily.py 执行成功"
else
    log "[步骤4/18] ❌ update_rzrq_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤5: 执行 update_stock_daily.py（股票日数据）
log "[步骤5/18] 开始执行 update_stock_daily.py..."
if ${VENV_PYTHON} update_stock_daily.py >> "${LOG_DIR}/update_stock_daily_${DATE}.log" 2>&1; then
    log "[步骤5/18] ✅ update_stock_daily.py 执行成功"
else
    log "[步骤5/18] ❌ update_stock_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤6: 执行 update_stock_info_daily.py（股票信息）
log "[步骤6/18] 开始执行 update_stock_info_daily.py..."
if ${VENV_PYTHON} update_stock_info_daily.py >> "${LOG_DIR}/update_stock_info_daily_${DATE}.log" 2>&1; then
    log "[步骤6/18] ✅ update_stock_info_daily.py 执行成功"
else
    log "[步骤6/18] ❌ update_stock_info_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤7: 执行 update_stock_daily_basic_info.py（日基本交易指标数据）
log "[步骤7/18] 开始执行 update_stock_daily_basic_info.py..."
if ${VENV_PYTHON} update_stock_daily_basic_info.py >> "${LOG_DIR}/update_stock_daily_basic_info_${DATE}.log" 2>&1; then
    log "[步骤7/18] ✅ update_stock_daily_basic_info.py 执行成功"
else
    log "[步骤7/18] ❌ update_stock_daily_basic_info.py 执行失败，停止任务"
    exit 1
fi

# 步骤8: 执行 update_stock_ma5_ma30.py（MA5/MA30 打标）
log "[步骤8/18] 开始执行 update_stock_ma5_ma30.py..."
if ${VENV_PYTHON} update_stock_ma5_ma30.py >> "${LOG_DIR}/update_stock_ma5_ma30_${DATE}.log" 2>&1; then
    log "[步骤8/18] ✅ update_stock_ma5_ma30.py 执行成功"
else
    log "[步骤8/18] ❌ update_stock_ma5_ma30.py 执行失败，停止任务"
    exit 1
fi

# 步骤9: 执行 update_stock_turning_point_tag.py（turning_point 打标）
log "[步骤9/18] 开始执行 update_stock_turning_point_tag.py..."
if ${VENV_PYTHON} update_stock_turning_point_tag.py >> "${LOG_DIR}/update_stock_turning_point_tag_${DATE}.log" 2>&1; then
    log "[步骤9/18] ✅ update_stock_turning_point_tag.py 执行成功"
else
    log "[步骤9/18] ❌ update_stock_turning_point_tag.py 执行失败，停止任务"
    exit 1
fi

log "========== 第一批任务完成，开始第二批任务 =========="


# 步骤10: 执行 select_newhigh_in_120d.py（近120日区间突破）
log "[步骤10/18] 开始执行 select_newhigh_in_120d.py..."
if ${VENV_PYTHON} select_newhigh_in_120d.py >> "${LOG_DIR}/select_newhigh_120d_${DATE}.log" 2>&1; then
    log "[步骤10/18] ✅ select_newhigh_in_120d.py 执行成功"
else
    log "[步骤10/18] ❌ select_newhigh_in_120d.py 执行失败，停止任务"
    exit 1
fi

# 步骤11: 执行 select_bottom_bounce.py（见底反弹策略）
log "[步骤11/18] 开始执行 select_bottom_bounce.py..."
if ${VENV_PYTHON} select_bottom_bounce.py >> "${LOG_DIR}/select_bottom_bounce_${DATE}.log" 2>&1; then
    log "[步骤11/18] ✅ select_bottom_bounce.py 执行成功"
else
    log "[步骤11/18] ❌ select_bottom_bounce.py 执行失败，停止任务"
    exit 1
fi

# 步骤12: 执行 select_high_exchange.py（高换手率策略）
log "[步骤12/18] 开始执行 select_high_exchange.py..."
if ${VENV_PYTHON} select_high_exchange.py >> "${LOG_DIR}/select_high_exchange_${DATE}.log" 2>&1; then
    log "[步骤12/18] ✅ select_high_exchange.py 执行成功"
else
    log "[步骤12/18] ❌ select_high_exchange.py 执行失败，停止任务"
    exit 1
fi

# 步骤13: 执行 select_2wave_up.py（二浪启动策略）
log "[步骤13/18] 开始执行 select_2wave_up.py..."
if ${VENV_PYTHON} select_2wave_up.py >> "${LOG_DIR}/select_2wave_up_${DATE}.log" 2>&1; then
    log "[步骤13/18] ✅ select_2wave_up.py 执行成功"
else
    log "[步骤13/18] ❌ select_2wave_up.py 执行失败，停止任务"
    exit 1
fi

# 步骤14: 执行 select_limitup_1d.py（当日涨停股票）
log "[步骤14/18] 开始执行 select_limitup_1d.py..."
if ${VENV_PYTHON} select_limitup_1d.py >> "${LOG_DIR}/select_limitup_1d_${DATE}.log" 2>&1; then
    log "[步骤14/18] ✅ select_limitup_1d.py 执行成功"
else
    log "[步骤14/18] ❌ select_limitup_1d.py 执行失败，停止任务"
    exit 1
fi

# 步骤15: 执行 select_2wave_daily.py（2浪趋势选股）
log "[步骤15/18] 开始执行 select_2wave_daily.py..."
if ${VENV_PYTHON} select_2wave_daily.py >> "${LOG_DIR}/select_2wave_daily_${DATE}.log" 2>&1; then
    log "[步骤15/18] ✅ select_2wave_daily.py 执行成功"
else
    log "[步骤15/18] ❌ select_2wave_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤16: 执行 report_stock_overall.py（大盘趋势报告）
log "[步骤16/18] 开始执行 report_stock_overall.py..."
if ${VENV_PYTHON} report_stock_overall.py >> "${LOG_DIR}/report_stock_overall_${DATE}.log" 2>&1; then
    log "[步骤16/18] ✅ report_stock_overall.py 执行成功"
else
    log "[步骤16/18] ❌ report_stock_overall.py 执行失败，停止任务"
    exit 1
fi

# 步骤17: 执行 report_industry_exchange.py（行业趋势报告）
log "[步骤17/18] 开始执行 report_industry_exchange.py..."
if ${VENV_PYTHON} report_industry_exchange.py >> "${LOG_DIR}/report_industry_exchange_${DATE}.log" 2>&1; then
    log "[步骤17/18] ✅ report_industry_exchange.py 执行成功"
else
    log "[步骤17/18] ❌ report_industry_exchange.py 执行失败，停止任务"
    exit 1
fi

# 步骤18: 执行 monitor_stock_data.py（数据更新监控）
log "[步骤18/18] 开始执行 monitor_stock_data.py..."
if ${VENV_PYTHON} monitor_stock_data.py >> "${LOG_DIR}/monitor_stock_data_${DATE}.log" 2>&1; then
    log "[步骤18/18] ✅ monitor_stock_data.py 执行成功"
else
    log "[步骤18/18] ❌ monitor_stock_data.py 执行失败，停止任务"
    exit 1
fi

log "=========================================="
log "🎉 所有任务执行完成！"
log "=========================================="
