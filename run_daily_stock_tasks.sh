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

log "========== 第一批任务完成，开始第二批任务 =========="

# 步骤7: 执行 pinzi_analysis_tag_to_db.py
log "[步骤7/17] 开始执行 pinzi_analysis_tag_to_db.py..."
if ${VENV_PYTHON} pinzi_analysis_tag_to_db.py >> "${LOG_DIR}/pinzi_analysis_tag_${DATE}.log" 2>&1; then
    log "[步骤7/17] ✅ pinzi_analysis_tag_to_db.py 执行成功"
else
    log "[步骤7/17] ❌ pinzi_analysis_tag_to_db.py 执行失败，停止任务"
    exit 1
fi

# 步骤8: 执行 select_newhigh_in_120d.py
log "[步骤8/17] 开始执行 select_newhigh_in_120d.py..."
if ${VENV_PYTHON} select_newhigh_in_120d.py >> "${LOG_DIR}/select_newhigh_120d_${DATE}.log" 2>&1; then
    log "[步骤8/17] ✅ select_newhigh_in_120d.py 执行成功"
else
    log "[步骤8/17] ❌ select_newhigh_in_120d.py 执行失败，停止任务"
    exit 1
fi

# 步骤9: 执行 select_turn_bottom.py
log "[步骤9/17] 开始执行 select_turn_bottom.py..."
if ${VENV_PYTHON} select_turn_bottom.py >> "${LOG_DIR}/select_turn_bottom_${DATE}.log" 2>&1; then
    log "[步骤9/17] ✅ select_turn_bottom.py 执行成功"
else
    log "[步骤9/17] ❌ select_turn_bottom.py 执行失败，停止任务"
    exit 1
fi

# 步骤10: 执行 select_lowwave_in10day.py
log "[步骤10/17] 开始执行 select_lowwave_in10day.py..."
if ${VENV_PYTHON} select_lowwave_in10day.py >> "${LOG_DIR}/select_lowwave_in10day_${DATE}.log" 2>&1; then
    log "[步骤10/17] ✅ select_lowwave_in10day.py 执行成功"
else
    log "[步骤10/17] ❌ select_lowwave_in10day.py 执行失败，停止任务"
    exit 1
fi

# 步骤11: 执行 select_lowwave_in30day.py
log "[步骤11/17] 开始执行 select_lowwave.py..."
if ${VENV_PYTHON} select_lowwave.py >> "${LOG_DIR}/select_lowwave_${DATE}.log" 2>&1; then
    log "[步骤11/17] ✅ select_lowwave.py 执行成功"
else
    log "[步骤11/17] ❌ select_lowwave.py 执行失败，停止任务"
    exit 1
fi

# 步骤12: 执行 select_daydayup_in5day.py
log "[步骤12/17] 开始执行 select_daydayup_in5day.py..."
if ${VENV_PYTHON} select_daydayup_in5day.py >> "${LOG_DIR}/select_daydayup_in5day_${DATE}.log" 2>&1; then
    log "[步骤12/17] ✅ select_daydayup_in5day.py 执行成功"
else
    log "[步骤12/17] ❌ select_daydayup_in5day.py 执行失败，停止任务"
    exit 1
fi

# 步骤13: 执行 select_industry_in5day.py
log "[步骤13/17] 开始执行 select_industry_in5day.py..."
if ${VENV_PYTHON} select_industry_in5day.py >> "${LOG_DIR}/select_industry_in5day_${DATE}.log" 2>&1; then
    log "[步骤13/17] ✅ select_industry_in5day.py 执行成功"
else
    log "[步骤13/17] ❌ select_industry_in5day.py 执行失败，停止任务"
    exit 1
fi

# 步骤14: 执行 select_limitup_daily.py
log "[步骤14/17] 开始执行 select_limitup_daily.py..."
if ${VENV_PYTHON} select_limitup_daily.py >> "${LOG_DIR}/select_limitup_daily_${DATE}.log" 2>&1; then
    log "[步骤14/17] ✅ select_limitup_daily.py 执行成功"
else
    log "[步骤14/17] ❌ select_limitup_daily.py 执行失败，停止任务"
    exit 1
fi

# 步骤15: 执行 select_boomvol.py (爆量上涨选股)
log "[步骤15/17] 开始执行 select_boomvol.py..."
if ${VENV_PYTHON} select_boomvol.py >> "${LOG_DIR}/select_boomvol_${DATE}.log" 2>&1; then
    log "[步骤15/17] ✅ select_boomvol.py 执行成功"
else
    log "[步骤15/17] ❌ select_boomvol.py 执行失败，停止任务"
    exit 1
fi

# 步骤15: 执行 select_boomvol.py (爆量上涨选股)
log "[步骤15/17] 开始执行 select_2wave_daily.py..."
if ${VENV_PYTHON} select_2wave_daily.py >> "${LOG_DIR}/select_2wave_daily_${DATE}.log" 2>&1; then
    log "[步骤15/17] ✅ select_2wave_daily.py 执行成功"
else
    log "[步骤15/17] ❌ select_2wave_daily.py 执行失败，停止任务"
    exit 1
fi


# 步骤16: 执行 stock_overall_report_v2.py
log "[步骤16/17] 开始执行 stock_overall_report_v2.py..."
if ${VENV_PYTHON} stock_overall_report_v2.py >> "${LOG_DIR}/stock_overall_report_v2_${DATE}.log" 2>&1; then
    log "[步骤16/17] ✅ stock_overall_report_v2.py 执行成功"
else
    log "[步骤16/17] ❌ stock_overall_report_v2.py 执行失败，停止任务"
    exit 1
fi

# 步骤17: 执行 industry_exchange_report.py
log "[步骤17/18] 开始执行 industry_exchange_report.py..."
if ${VENV_PYTHON} industry_exchange_report.py >> "${LOG_DIR}/industry_exchange_report_${DATE}.log" 2>&1; then
    log "[步骤17/18] ✅ industry_exchange_report.py 执行成功"
else
    log "[步骤17/18] ❌ industry_exchange_report.py 执行失败，停止任务"
    exit 1
fi

# 步骤18: 执行 analyze_index_change.py
log "[步骤18/18] 开始执行 analyze_index_change.py..."
if ${VENV_PYTHON} analyze_index_change.py >> "${LOG_DIR}/analyze_index_change_${DATE}.log" 2>&1; then
    log "[步骤18/18] ✅ analyze_index_change.py 执行成功"
else
    log "[步骤18/18] ❌ analyze_index_change.py 执行失败，停止任务"
    exit 1
fi

log "=========================================="
log "🎉 所有任务执行完成！"
log "=========================================="