#!/bin/bash
# 每周一到周五17点定时执行股票分析脚本
# 第一批任务：数据更新（按顺序执行）
# 第二批任务：选股分析（待第一批全部完成后按顺序执行）

set -e

SCRIPT_DIR="/Users/luckboy/Documents/trae_projects/myFirstQT"
VENV_PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
LOG_DIR="${SCRIPT_DIR}/cron_logs"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "${LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "${LOG_DIR}/daily_stock_${DATE}.log"
}

log "=========================================="
log "开始执行每日股票分析任务"
log "=========================================="

cd "${SCRIPT_DIR}"

log "========== 第一批任务：数据更新 =========="

# 步骤1: 执行 update_stock_info.py
log "[步骤1/17] 开始执行 update_stock_info_daily.py..."
if ${VENV_PYTHON} update_stock_info_daily.py >> "${LOG_DIR}/update_stock_info_daily_${DATE}.log" 2>&1; then
    log "[步骤1/17] ✅ update_stock_info_daily.py 执行成功"
else
    log "[步骤1/17] ❌ update_stock_info_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤2: 执行 update_industry_daily.py
log "[步骤2/17] 开始执行 update_industry_daily.py..."
if ${VENV_PYTHON} update_industry_daily.py >> "${LOG_DIR}/update_industry_daily_${DATE}.log" 2>&1; then
    log "[步骤2/17] ✅ update_industry_daily.py 执行成功"
else
    log "[步骤2/17] ❌ update_industry_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤3: 执行 update_stock_index_daily.py
log "[步骤3/17] 开始执行 update_stock_index_daily.py..."
if ${VENV_PYTHON} update_stock_index_daily.py >> "${LOG_DIR}/update_stock_index_daily_${DATE}.log" 2>&1; then
    log "[步骤3/17] ✅ update_stock_index_daily.py 执行成功"
else
    log "[步骤3/17] ❌ update_stock_index_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤4: 执行 update_stock_index_future_daily.py
log "[步骤4/17] 开始执行 update_stock_index_future_daily.py..."
if ${VENV_PYTHON} update_stock_index_future_daily.py >> "${LOG_DIR}/update_stock_index_future_daily_${DATE}.log" 2>&1; then
    log "[步骤4/17] ✅ update_stock_index_future_daily.py 执行成功"
else
    log "[步骤4/17] ❌ update_stock_index_future_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤5: 执行 update_rzrq_daily.py
log "[步骤5/17] 开始执行 update_rzrq_daily.py..."
if ${VENV_PYTHON} update_rzrq_daily.py >> "${LOG_DIR}/update_rzrq_daily_${DATE}.log" 2>&1; then
    log "[步骤5/17] ✅ update_rzrq_daily.py 执行成功"
else
    log "[步骤5/17] ❌ update_rzrq_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤6: 执行 update_stock_daily.py
log "[步骤6/17] 开始执行 update_stock_daily.py..."
if ${VENV_PYTHON} update_stock_daily.py >> "${LOG_DIR}/update_stock_daily_${DATE}.log" 2>&1; then
    log "[步骤6/17] ✅ update_stock_daily.py 执行成功"
else
    log "[步骤6/17] ❌ update_stock_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤6: 执行 update_stock_ma5_ma30.py
log "[步骤7/17] 开始执行 update_stock_ma5_ma30.py..."
if ${VENV_PYTHON} update_stock_ma5_ma30.py >> "${LOG_DIR}/update_stock_ma5_ma30${DATE}.log" 2>&1; then
    log "[步骤6/17] ✅ update_stock_ma5_ma30.py 执行成功"
else
    log "[步骤6/17] ❌ update_stock_ma5_ma30.py 执行失败，停止任务"
    exit 1
fi

# 步骤6: 执行 update_stock_ma5_ma30.py
log "[步骤8/17] 开始执行 update_stock_turning_point_tag.py..."
if ${VENV_PYTHON} update_stock_turning_point_tag.py >> "${LOG_DIR}/update_stock_turning_point_tag${DATE}.log" 2>&1; then
    log "[步骤6/17] ✅ update_stock_turning_point_tag.py 执行成功"
else
    log "[步骤6/17] ❌ update_stock_turning_point_tag_v2.py 执行失败，停止任务"
    exit 1
fi

log "========== 第一批任务完成，开始第二批任务 =========="


# 步骤8: 执行 select_newhigh_in_120d.py 区间放量破新高
log "[步骤9/17] 开始执行 select_newhigh_in_120d.py..."
if ${VENV_PYTHON} select_newhigh_in_120d.py >> "${LOG_DIR}/select_newhigh_120d_${DATE}.log" 2>&1; then
    log "[步骤8/17] ✅ select_newhigh_in_120d.py 执行成功"
else
    log "[步骤8/17] ❌ select_newhigh_in_120d.py 执行失败，停止任务"
    exit 1
fi

# 步骤9: 执行 select_turn_bottom.py 底部反转
log "[步骤10/17] 开始执行 select_turn_bottom.py..."
if ${VENV_PYTHON} select_turn_bottom.py >> "${LOG_DIR}/select_turn_bottom_${DATE}.log" 2>&1; then
    log "[步骤9/17] ✅ select_turn_bottom.py 执行成功"
else
    log "[步骤9/17] ❌ select_turn_bottom.py 执行失败，停止任务"
    exit 1
fi


# 步骤14: 执行 select_limitup_1d.py 当日涨停股票
log "[步骤11/17] 开始执行 select_limitup_1d.py..."
if ${VENV_PYTHON} select_limitup_1d.py >> "${LOG_DIR}/select_limitup_1d${DATE}.log" 2>&1; then
    log "[步骤14/17] ✅ select_limitup_1d.py 执行成功"
else
    log "[步骤14/17] ❌ select_limitup_1d.py 执行失败，停止任务"
    exit 1
fi


# 步骤15: 执行 select_2wave_daily.py (2波上涨选股)
log "[步骤12/17] 开始执行 select_2wave_daily.py..."
if ${VENV_PYTHON} select_2wave_daily.py >> "${LOG_DIR}/select_2wave_daily_${DATE}.log" 2>&1; then
    log "[步骤15/17] ✅ select_2wave_daily.py 执行成功"
else
    log "[步骤15/17] ❌ select_2wave_daily.py 执行失败，停止任务"
    exit 1
fi


# 步骤15: 执行 select_3wave_up.py (2浪上涨选股)
log "[步骤13/17] 开始执行 select_2wave_up.py..."
if ${VENV_PYTHON} select_2wave_up.py >> "${LOG_DIR}/select_2wave_up${DATE}.log" 2>&1; then
    log "[步骤15/17] ✅ select_2wave_up.py 执行成功"
else
    log "[步骤15/17] ❌ select_2wave_up.py 执行失败，停止任务"
    exit 1
fi

# 步骤15: 执行 select_3wave_up.py (2浪上涨选股)
log "[步骤14/17] 开始执行 select_2wave_up_v2.py..."
if ${VENV_PYTHON} select_2wave_up_v2.py >> "${LOG_DIR}/select_2wave_up_v2${DATE}.log" 2>&1; then
    log "[步骤15/17] ✅ select_2wave_up_v2.py 执行成功"
else
    log "[步骤15/17] ❌ select_2wave_up_v2.py 执行失败，停止任务"
    exit 1
fi

# 步骤16: 执行 stock_overall_report.py
log "[步骤15/17] 开始执行 report_stock_overall.py..."
if ${VENV_PYTHON} report_stock_overall.py >> "${LOG_DIR}/report_stock_overall${DATE}.log" 2>&1; then
    log "[步骤16/17] ✅ report_stock_overall.py 执行成功"
else
    log "[步骤16/17] ❌ report_stock_overall.py 执行失败，停止任务"
    exit 1
fi

# 步骤16: 执行 monitor_stock_data.py
log "[步骤16/17] 开始执行 monitor_stock_data.py..."
if ${VENV_PYTHON} monitor_stock_data.py >> "${LOG_DIR}/monitor_stock_data${DATE}.log" 2>&1; then
    log "[步骤16/17] ✅ monitor_stock_data.py 执行成功"
else
    log "[步骤16/17] ❌ monitor_stock_data.py 执行失败，停止任务"
    exit 1
fi

log "=========================================="
log "🎉 所有任务执行完成！"
log "=========================================="