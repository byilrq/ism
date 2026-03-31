#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/root/asset_manager"
APP_DIR="${APP_ROOT}/app"
BACKUP_DIR="${APP_ROOT}/backups"
SERVICE_NAME="asset_manager"
NGINX_SITE="asset_manager"
INTERNAL_PORT="5000"
PUBLIC_PORT="2083"
DB_NAME="asset_manager"
DB_USER="asset_user"
DB_PASS="by123"
DB_HOST="localhost"
RAW_BASE="https://raw.githubusercontent.com/byilrq/ism/main"
APP_RAR_URL="${RAW_BASE}/app.rar"
CONFIG_URL="${RAW_BASE}/config.py"
RUN_URL="${RAW_BASE}/run.py"
REQ_URL="${RAW_BASE}/requirements.txt"
SQL_URL="${RAW_BASE}/asset_manager.sql"
TMP_DIR="/tmp/asset_manager_install"
VENV_DIR="${APP_ROOT}/venv"
PY_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"
GUNICORN_BIN="${VENV_DIR}/bin/gunicorn"
BACKUP_SCRIPT="/usr/local/bin/asset_manager_backup.sh"
CRON_FILE="/etc/cron.d/asset_manager_backup"
STATE_FILE="/root/.asset_manager_install.conf"
DOMAIN=""
USE_SSL="0"

color_green() { printf '\033[32m%s\033[0m\n' "$*"; }
color_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
color_red() { printf '\033[31m%s\033[0m\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
ok() { color_green "[OK] $*"; }
warn() { color_yellow "[WARN] $*"; }
err() { color_red "[ERR] $*"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "请使用 root 运行此脚本"
        exit 1
    fi
}

load_state() {
    if [ -f "$STATE_FILE" ]; then
        # shellcheck disable=SC1090
        . "$STATE_FILE"
    fi
}

save_state() {
    cat > "$STATE_FILE" <<STATE
DOMAIN=${DOMAIN@Q}
USE_SSL=${USE_SSL@Q}
STATE
}

install_packages() {
    info "安装系统依赖"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y \
        nginx mariadb-server cron curl wget unar \
        python3 python3-venv python3-pip python3-dev \
        build-essential default-libmysqlclient-dev pkg-config
    systemctl enable --now mariadb
    systemctl enable --now nginx
    systemctl enable --now cron
    ok "系统依赖安装完成"
}

download_files() {
    info "下载项目文件"
    rm -rf "$TMP_DIR"
    mkdir -p "$TMP_DIR"

    curl -L "$APP_RAR_URL" -o "$TMP_DIR/app.rar"
    curl -L "$CONFIG_URL" -o "$TMP_DIR/config.py"
    curl -L "$RUN_URL" -o "$TMP_DIR/run.py"
    curl -L "$REQ_URL" -o "$TMP_DIR/requirements.txt"
    curl -L "$SQL_URL" -o "$TMP_DIR/asset_manager.sql"

    ok "项目文件下载完成"
}

prepare_directories() {
    info "创建目录"
    if [ -d "$APP_ROOT" ]; then
        local ts
        ts="$(date +%Y%m%d_%H%M%S)"
        mv "$APP_ROOT" "${APP_ROOT}.bak.${ts}"
        warn "检测到已有 ${APP_ROOT}，已备份为 ${APP_ROOT}.bak.${ts}"
    fi

    mkdir -p "$APP_ROOT"
    mkdir -p "$APP_DIR"
    mkdir -p "$APP_DIR/uploads/images/assets"
    mkdir -p "$APP_DIR/uploads/images/accessories"
    mkdir -p "$BACKUP_DIR"
    ok "目录创建完成"
}

extract_app() {
    info "解压 app.rar 到 ${APP_ROOT}"
    mkdir -p "$TMP_DIR/app_extract"
    unar -f -o "$TMP_DIR/app_extract" "$TMP_DIR/app.rar" >/dev/null

    if [ -d "$TMP_DIR/app_extract/app" ]; then
        cp -a "$TMP_DIR/app_extract/app/." "$APP_DIR/"
    else
        # 有些压缩包根目录可能不是 app/，尝试整体复制
        cp -a "$TMP_DIR/app_extract/." "$APP_DIR/"
    fi

    cp -f "$TMP_DIR/config.py" "$APP_ROOT/config.py"
    cp -f "$TMP_DIR/run.py" "$APP_ROOT/run.py"
    cp -f "$TMP_DIR/requirements.txt" "$APP_ROOT/requirements.txt"
    cp -f "$TMP_DIR/asset_manager.sql" "$APP_ROOT/asset_manager.sql"

    mkdir -p "$APP_DIR/uploads/images/assets"
    mkdir -p "$APP_DIR/uploads/images/accessories"
    ok "程序文件部署完成"
}

setup_python() {
    info "创建 Python 虚拟环境并安装依赖"
    python3 -m venv "$VENV_DIR"
    "$PIP_BIN" install --upgrade pip setuptools wheel
    "$PIP_BIN" install -r "$APP_ROOT/requirements.txt"
    ok "Python 环境安装完成"
}

setup_database() {
    info "初始化数据库并导入 asset_manager.sql"
    mysql <<SQL
CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

    mysql "$DB_NAME" < "$APP_ROOT/asset_manager.sql"
    ok "数据库导入完成"
}

write_systemd_service() {
    info "写入 systemd 服务"
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF2
[Unit]
Description=Asset Manager Gunicorn Service
After=network.target mariadb.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${APP_ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${GUNICORN_BIN} -w 2 -b 127.0.0.1:${INTERNAL_PORT} run:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF2
    ok "systemd 服务文件已生成"
}

prompt_domain() {
    load_state
    echo
    read -r -p "如需绑定域名，请输入域名（留空则仅使用服务器IP:${PUBLIC_PORT}访问） [${DOMAIN:-}]: " input_domain
    if [ -n "${input_domain}" ]; then
        DOMAIN="$input_domain"
    fi

    USE_SSL="0"
    if [ -n "${DOMAIN}" ]; then
        if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]; then
            USE_SSL="1"
            ok "检测到证书，将启用 HTTPS：https://${DOMAIN}:${PUBLIC_PORT}"
        else
            warn "未检测到 /etc/letsencrypt/live/${DOMAIN} 下的证书文件，将使用 http://${DOMAIN}:${PUBLIC_PORT}"
        fi
    fi
    save_state
}

write_nginx_config() {
    load_state
    info "配置 Nginx 反向代理"
    local server_name
    server_name="_"
    if [ -n "${DOMAIN}" ]; then
        server_name="${DOMAIN}"
    fi

    if [ "${USE_SSL}" = "1" ]; then
        cat > "/etc/nginx/sites-available/${NGINX_SITE}.conf" <<EOF2
server {
    listen ${PUBLIC_PORT} ssl;
    server_name ${server_name};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    client_max_body_size 30m;

    location / {
        proxy_pass http://127.0.0.1:${INTERNAL_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF2
    else
        cat > "/etc/nginx/sites-available/${NGINX_SITE}.conf" <<EOF2
server {
    listen ${PUBLIC_PORT};
    server_name ${server_name};

    client_max_body_size 30m;

    location / {
        proxy_pass http://127.0.0.1:${INTERNAL_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF2
    fi

    ln -sf "/etc/nginx/sites-available/${NGINX_SITE}.conf" "/etc/nginx/sites-enabled/${NGINX_SITE}.conf"
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl restart nginx
    ok "Nginx 配置完成"
}

restart_service() {
    info "重新下载程序并重启服务"
    download_files

    if [ ! -d "$APP_ROOT" ]; then
        err "未检测到 ${APP_ROOT}，请先执行安装"
        exit 1
    fi

    rm -rf "$APP_DIR"
    mkdir -p "$APP_DIR"
    extract_app

    if [ -d "$VENV_DIR" ]; then
        "$PIP_BIN" install -r "$APP_ROOT/requirements.txt"
    else
        warn "未检测到虚拟环境，自动重新安装 Python 环境"
        setup_python
    fi

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager || true
}

write_backup_files() {
    info "配置每日数据库备份，只保留最新一份"
    mkdir -p "$BACKUP_DIR"
    cat > "$BACKUP_SCRIPT" <<EOF2
#!/usr/bin/env bash
set -euo pipefail
mkdir -p "${BACKUP_DIR}"
rm -f "${BACKUP_DIR}"/asset_manager_*.sql
mysqldump -u ${DB_USER} -p'${DB_PASS}' ${DB_NAME} > "${BACKUP_DIR}/asset_manager_
	daily_latest.sql"
EOF2

    # 修正换行，避免 heredoc 中出现制表符问题
    sed -i 's/asset_manager_\n\tdaily_latest.sql/asset_manager_daily_latest.sql/g' "$BACKUP_SCRIPT"
    chmod +x "$BACKUP_SCRIPT"

    cat > "$CRON_FILE" <<EOF2
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 2 * * * root ${BACKUP_SCRIPT} >> /var/log/asset_manager_backup.log 2>&1
EOF2
    chmod 644 "$CRON_FILE"
    systemctl restart cron
    ok "自动备份已配置，每天 02:00 备份到 ${BACKUP_DIR}，仅保留最新一份"
}

install_all() {
    require_root
    prompt_domain
    install_packages
    download_files
    prepare_directories
    extract_app
    setup_python
    setup_database
    write_systemd_service
    write_nginx_config
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager || true
    ok "安装完成"
    if [ -n "${DOMAIN}" ]; then
        if [ "${USE_SSL}" = "1" ]; then
            info "访问地址：https://${DOMAIN}:${PUBLIC_PORT}"
        else
            info "访问地址：http://${DOMAIN}:${PUBLIC_PORT}"
        fi
    else
        info "访问地址：http://服务器IP:${PUBLIC_PORT}"
    fi
}

show_menu() {
    echo "========================================"
    echo "  资产管理系统一键安装脚本 ism.sh"
    echo "========================================"
    echo "1. 安装"
    echo "2. 重启"
    echo "3. 添加每天自动备份数据库"
    echo "0. 退出"
    echo "========================================"
}

main() {
    require_root
    while true; do
        show_menu
        read -r -p "请选择菜单编号: " choice
        case "$choice" in
            1)
                install_all
                ;;
            2)
                restart_service
                ;;
            3)
                write_backup_files
                ;;
            0)
                exit 0
                ;;
            *)
                warn "无效选项，请重新输入"
                ;;
        esac
        echo
        read -r -p "按回车继续..." _
        echo
    done
}

main "$@"
