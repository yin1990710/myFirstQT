#!/bin/bash
# ============================================================================
# 力矩量化选股平台 - Linux 一键部署脚本
# 适配系统: Ubuntu 20.04+ / Debian 11+ (apt 包管理器)
# 使用方式: sudo bash deploy.sh
# ============================================================================
set -e

# ---------------------------- 配置区(可按需修改) ----------------------------
APP_NAME="myFirstQT"
APP_USER="myfirstqt"                       # 运行应用的系统用户(自动创建)
INSTALL_DIR="/opt/${APP_NAME}"             # 项目部署目录
MYSQL_ROOT_PASSWORD="12345678"             # MySQL root 密码(需与 module_mysql_connection.py 一致)
MYSQL_DB_NAME="stock_daily_db"             # 数据库名(需与 module_mysql_connection.py 一致)
APP_HOST="0.0.0.0"                         # app.py 监听地址(0.0.0.0 对外可访问)
APP_PORT=5000                               # app.py 监听端口
CRON_SCHEDULE="0 17 * * 1-5"               # 每周一到周五 17:00 执行例行任务
PYTHON_BIN="python3"

# 颜色输出
C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_BLUE='\033[0;34m'; C_NC='\033[0m'
log()  { echo -e "${C_BLUE}[$(date '+%H:%M:%S')]${C_NC} $*"; }
ok()   { echo -e "${C_GREEN}[OK]${C_NC} $*"; }
warn() { echo -e "${C_YELLOW}[WARN]${C_NC} $*"; }
err()  { echo -e "${C_RED}[ERR]${C_NC} $*" >&2; }

# ---------------------------- 前置检查 ----------------------------
if [[ $EUID -ne 0 ]]; then
    err "本脚本需要 root 权限运行, 请使用: sudo bash $0"
    exit 1
fi

if [[ ! -f "app.py" ]]; then
    err "未在项目根目录发现 app.py, 请在项目根目录运行本脚本"
    exit 1
fi

SCRIPT_DIR_NOW="$(cd "$(dirname "$0")" && pwd)"
log "项目源目录: ${SCRIPT_DIR_NOW}"
log "目标部署目录: ${INSTALL_DIR}"

# ============================ 1. 安装系统依赖 ============================
echo
log "==================== 步骤 1/7: 安装系统依赖 ===================="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    build-essential \
    mysql-server mysql-client \
    libmysqlclient-dev \
    pkg-config \
    curl ca-certificates \
    fonts-wqy-zenhei fonts-wqy-microhei
ok "系统依赖安装完成"

# ============================ 2. 创建运行用户与目录 ============================
echo
log "==================== 步骤 2/7: 创建运行用户与部署目录 ===================="
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd -r -m -d "/home/${APP_USER}" -s /bin/bash "${APP_USER}"
    ok "创建系统用户: ${APP_USER}"
else
    warn "用户 ${APP_USER} 已存在, 跳过创建"
fi

# 拷贝项目文件到部署目录
if [[ "${SCRIPT_DIR_NOW}" != "${INSTALL_DIR}" ]]; then
    log "拷贝项目文件 ${SCRIPT_DIR_NOW} -> ${INSTALL_DIR}"
    mkdir -p "${INSTALL_DIR}"
    # 使用 rsync 同步, 排除虚拟环境与缓存
    if command -v rsync >/dev/null 2>&1; then
        apt-get install -y --no-install-recommends rsync
        rsync -a --delete \
            --exclude='.venv' --exclude='venv' \
            --exclude='__pycache__' --exclude='*.pyc' \
            --exclude='cron_logs' --exclude='manual_logs' \
            --exclude='.git' \
            "${SCRIPT_DIR_NOW}/" "${INSTALL_DIR}/"
    else
        cp -a "${SCRIPT_DIR_NOW}/." "${INSTALL_DIR}/"
        rm -rf "${INSTALL_DIR}/.venv" "${INSTALL_DIR}/__pycache__" "${INSTALL_DIR}/cron_logs" "${INSTALL_DIR}/manual_logs"
    fi
    ok "项目文件已拷贝到 ${INSTALL_DIR}"
else
    warn "源目录即部署目录, 跳过拷贝"
fi
chown -R "${APP_USER}:${APP_USER}" "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/cron_logs" "${INSTALL_DIR}/manual_logs"
chown -R "${APP_USER}:${APP_USER}" "${INSTALL_DIR}/cron_logs" "${INSTALL_DIR}/manual_logs"

# ============================ 3. 创建 Python 虚拟环境并安装依赖 ============================
echo
log "==================== 步骤 3/7: 创建 Python 虚拟环境并安装依赖 ===================="
cd "${INSTALL_DIR}"
if [[ ! -d ".venv" ]]; then
    sudo -u "${APP_USER}" "${PYTHON_BIN}" -m venv .venv
    ok "虚拟环境创建于 ${INSTALL_DIR}/.venv"
else
    warn ".venv 已存在, 跳过创建"
fi

VENV_PY="${INSTALL_DIR}/.venv/bin/python3"
VENV_PIP="${INSTALL_DIR}/.venv/bin/pip"

log "升级 pip 并安装依赖..."
sudo -u "${APP_USER}" "${VENV_PY}" -m pip install --upgrade pip --quiet
sudo -u "${APP_USER}" "${VENV_PIP}" install -r requirements.txt --quiet
ok "Python 依赖安装完成"

# ============================ 4. 配置 MySQL 并启动 ============================
echo
log "==================== 步骤 4/7: 配置 MySQL ===================="
# 启动 MySQL 服务
systemctl start mysql 2>/dev/null || systemctl start mysqld 2>/dev/null || true
systemctl enable mysql 2>/dev/null || systemctl enable mysqld 2>/dev/null || true

# 等待 MySQL 就绪
log "等待 MySQL 服务就绪..."
for i in $(seq 1 30); do
    if mysqladmin ping -uroot --silent 2>/dev/null; then
        ok "MySQL 已启动"
        break
    fi
    sleep 1
    if [[ $i -eq 30 ]]; then
        err "MySQL 启动超时, 请检查: systemctl status mysql"
        exit 1
    fi
done

# 设置 root 密码(若未设置) + 创建数据库
log "配置 root 密码与数据库..."
# MySQL 8 默认使用 auth_socket, 需先通过 unix socket 登录设置密码
mysql -uroot <<EOF || true
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '${MYSQL_ROOT_PASSWORD}';
FLUSH PRIVILEGES;
EOF

# 验证密码登录
if ! mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -e "SELECT 1;" >/dev/null 2>&1; then
    warn "root 密码可能已设置或使用 caching_sha2_password, 尝试创建应用专用账号..."
    mysql -uroot <<EOF || true
ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';
FLUSH PRIVILEGES;
EOF
fi

# 创建数据库(允许失败, 数据库可能已存在)
log "创建数据库 ${MYSQL_DB_NAME}..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<EOF
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
EOF
ok "数据库 ${MYSQL_DB_NAME} 就绪"

# ============================ 5. 导入数据表 DDL ============================
echo
log "==================== 步骤 5/7: 导入数据表结构 ===================="
DDL_FILE="${INSTALL_DIR}/stock_db_SQL/stock_daily_db_ddl.sql"
if [[ ! -f "${DDL_FILE}" ]]; then
    err "DDL 文件不存在: ${DDL_FILE}"
    err "请确认 stock_db_SQL/stock_daily_db_ddl.sql 在项目中"
    exit 1
fi

# DDL 中含 GTID_PURGED 语句, 在部分 MySQL 上会报错, 预先移除
TMP_DDL="/tmp/stock_daily_db_ddl_filtered.sql"
grep -v -E "^SET @@GLOBAL\.GTID_PURGED|^SET @@SESSION\.SQL_LOG_BIN|^SET @MYSQLDUMP_TEMP_LOG_BIN" "${DDL_FILE}" > "${TMP_DDL}" || cp "${DDL_FILE}" "${TMP_DDL}"

log "导入 DDL: ${TMP_DDL} -> ${MYSQL_DB_NAME}"
# -f 容忍个别语句失败(如表已存在), 不阻断整体流程
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -f "${MYSQL_DB_NAME}" < "${TMP_DDL}"
rm -f "${TMP_DDL}"

# 初始化登录用户表(app.py 启动时 controller_auth.init_user_table() 也会自动建表)
log "初始化登录用户表..."
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" -f "${MYSQL_DB_NAME}" <<'EOF' || true
CREATE TABLE IF NOT EXISTS `user_t` (
  `id` int NOT NULL AUTO_INCREMENT,
  `account` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password_hash` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `salt` varchar(64) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`), UNIQUE KEY `uk_account` (`account`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='登录用户表';
EOF
ok "数据表导入完成"

# ============================ 6. 修改定时脚本路径并创建 cron 任务 ============================
echo
log "==================== 步骤 6/7: 配置定时任务 ===================="
CRON_SH="${INSTALL_DIR}/run_daily_stock_tasks.sh"

# 修改 SCRIPT_DIR 为部署目录
if grep -q '^SCRIPT_DIR=' "${CRON_SH}"; then
    # 备份原文件
    cp -p "${CRON_SH}" "${CRON_SH}.bak.$(date +%s)" 2>/dev/null || true
    # 替换 SCRIPT_DIR 行
    sed -i "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"${INSTALL_DIR}\"|" "${CRON_SH}"
    # 替换 VENV_PYTHON 行(若存在)
    sed -i "s|^VENV_PYTHON=.*|VENV_PYTHON=\"\${SCRIPT_DIR}/.venv/bin/python3\"|" "${CRON_SH}"
    chmod +x "${CRON_SH}"
    chown "${APP_USER}:${APP_USER}" "${CRON_SH}"
    ok "已修改 ${CRON_SH} 中的 SCRIPT_DIR -> ${INSTALL_DIR}"
else
    warn "未在 ${CRON_SH} 中找到 SCRIPT_DIR, 请手动确认路径配置"
fi

# 写入 crontab(保留 APP_USER 已有的其他任务)
CRON_LINE="${CRON_SCHEDULE} ${CRON_SH} >> ${INSTALL_DIR}/cron_logs/cron_main_\$(date +\%Y\%m\%d).log 2>&1"

TEMP_CRON="/tmp/crontab_${APP_USER}.txt"
sudo -u "${APP_USER}" crontab -l > "${TEMP_CRON}" 2>/dev/null || echo "" > "${TEMP_CRON}"

# 删除旧的同类任务(避免重复)
grep -v -F "${CRON_SH}" "${TEMP_CRON}" > "${TEMP_CRON}.new" || true
echo "${CRON_LINE}" >> "${TEMP_CRON}.new"

sudo -u "${APP_USER}" crontab "${TEMP_CRON}.new"
rm -f "${TEMP_CRON}" "${TEMP_CRON}.new"
ok "定时任务已创建: ${CRON_SCHEDULE} ${CRON_SH}"
warn "查看: sudo -u ${APP_USER} crontab -l"

# ============================ 7. 启动 app.py (systemd) ============================
echo
log "==================== 步骤 7/7: 配置 systemd 并启动 app.py ===================="
SERVICE_NAME="myfirstqt-app.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=力矩量化选股平台 Flask App (app.py)
After=network.target mysql.service

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_PY} ${INSTALL_DIR}/app.py
Restart=on-failure
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/cron_logs/app_stdout.log
StandardError=append:${INSTALL_DIR}/cron_logs/app_stderr.log

[Install]
WantedBy=multi-user.target
EOF

# 注意: app.py 内部硬编码 host=127.0.0.1, 若需对外开放请修改 app.py 末尾的 app.run() 行
if [[ "${APP_HOST}" != "127.0.0.1" ]]; then
    warn "app.py 末尾硬编码 host='127.0.0.1', 对外访问需手动修改 app.py 末尾的 app.run 行"
    warn "  或在 nginx/caddy 反向代理 127.0.0.1:5000 端口"
fi

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# 等待启动
log "等待 app.py 启动..."
sleep 3
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    ok "app.py 已启动并设置为开机自启"
else
    warn "app.py 启动可能失败, 查看日志: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi

# ============================ 部署完成总结 ============================
echo
echo -e "${C_GREEN}==================== 部署完成 ====================${C_NC}"
echo
echo "项目目录:        ${INSTALL_DIR}"
echo "运行用户:        ${APP_USER}"
echo "Python 虚拟环境: ${INSTALL_DIR}/.venv"
echo "数据库:          ${MYSQL_DB_NAME} (root/${MYSQL_ROOT_PASSWORD})"
echo "Web 访问:        http://127.0.0.1:${APP_PORT}  (或服务器IP)"
echo "定时任务:        ${CRON_SCHEDULE} (周一到周五 17:00)"
echo "systemd 服务:    ${SERVICE_NAME}"
echo
echo -e "${C_YELLOW}常用命令:${C_NC}"
echo "  查看应用状态:    systemctl status ${SERVICE_NAME}"
echo "  重启应用:        systemctl restart ${SERVICE_NAME}"
echo "  查看应用日志:    journalctl -u ${SERVICE_NAME} -f"
echo "  查看应用错误日志: tail -f ${INSTALL_DIR}/cron_logs/app_stderr.log"
echo "  查看定时任务:    sudo -u ${APP_USER} crontab -l"
echo "  手动运行例行任务: sudo -u ${APP_USER} ${CRON_SH}"
echo "  进入项目目录:    cd ${INSTALL_DIR} && source .venv/bin/activate"
echo
echo -e "${C_YELLOW}注意事项:${C_NC}"
echo "  1. tushare token 已硬编码在各 update_*.py / select_*.py 脚本中, 无需单独配置"
echo "  2. 若需外部网络访问, 请修改 ${INSTALL_DIR}/app.py 末尾的 app.run(host=...) 或配置反向代理"
echo "  3. 定时任务首次执行需联网拉取 tushare/akshare 数据, 建议在交易日 17:00 后观察 cron_logs 目录"
echo "  4. 首次登录平台账号请查看 controller_auth.py 中的 init_user_table() 默认账号密码"
