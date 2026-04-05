#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/root/asset_manager"
APP_DIR="${APP_ROOT}/app"
VENV_DIR="${APP_ROOT}/venv"
BACKUP_DIR="${APP_ROOT}/backups"
BACKUP_FILE="${BACKUP_DIR}/asset_manager_latest.sql"
BACKUP_SCRIPT="${APP_ROOT}/ism_backup.sh"
CRON_BACKUP_FILE="/etc/cron.d/asset_manager_backup"
BACKUP_LOG_FILE="/var/log/asset_manager_backup.log"

SERVICE_NAME="asset_manager"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE_FILE="/etc/nginx/sites-available/${SERVICE_NAME}_2083.conf"
NGINX_SITE_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}_2083.conf"
STATE_FILE="/root/.asset_manager_install.conf"

RAW_BASE="https://raw.githubusercontent.com/byilrq/ism/main"
APP_ZIP_URL="${RAW_BASE}/app.zip"
CONFIG_URL="${RAW_BASE}/config.py"
RUN_URL="${RAW_BASE}/run.py"
REQ_URL="${RAW_BASE}/requirements.txt"
SQL_URL="${RAW_BASE}/asset_manager.sql"
ROUTES_INIT_URL="${RAW_BASE}/__init__.py"
OCR_URL="${RAW_BASE}/ocr_recognizer.py"

TMP_DIR="/tmp/asset_manager_install"
DB_NAME="asset_manager"
DB_USER="asset_user"
DB_PASS="by123"
DB_HOST="localhost"

INTERNAL_PORT="5000"
PUBLIC_PORT="2083"
GUNICORN_WORKERS="1"

ASSET_IMG_DIR="${APP_ROOT}/app/uploads/images/assets"
ACCESSORY_IMG_DIR="${APP_ROOT}/app/uploads/images/accessories"
LOCAL_UPLOAD_ROOT="${APP_ROOT}/app/uploads/images"

DOMAIN=""
DAV_URL=""
DAV_MOUNT="/mnt/webdav_mount"
DAV_REMOTE_ROOT="ism_images"
DAV_UPLOAD_ROOT="${DAV_MOUNT}/${DAV_REMOTE_ROOT}"
DAV_USER=""
DAV_PASS=""

WEBDAV_SERVICE_NAME="webdav-mount"
WEBDAV_MOUNT_SERVICE="/etc/systemd/system/${WEBDAV_SERVICE_NAME}.service"

NC='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
CYAN='\033[96m'
BLUE='\033[94m'
MAGENTA='\033[95m'
WHITE='\033[97m'

green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
cyan() { printf '\033[36m%s\033[0m\n' "$*"; }

info() { cyan "[INFO] $*"; }
ok() { green "[OK] $*"; }
warn() { yellow "[WARN] $*"; }
err() { red "[ERR] $*"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "请使用 root 运行：sudo ./ism_webdav.sh"
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
    cat > "$STATE_FILE" <<EOF_STATE
DOMAIN=${DOMAIN@Q}
DAV_URL=${DAV_URL@Q}
DAV_MOUNT=${DAV_MOUNT@Q}
DAV_REMOTE_ROOT=${DAV_REMOTE_ROOT@Q}
DAV_UPLOAD_ROOT=${DAV_UPLOAD_ROOT@Q}
DAV_USER=${DAV_USER@Q}
DAV_PASS=${DAV_PASS@Q}
EOF_STATE
}

recompute_dav_paths() {
    DAV_URL="${DAV_URL%/}/"
    DAV_MOUNT="${DAV_MOUNT%/}"
    [ -n "$DAV_MOUNT" ] || DAV_MOUNT="/mnt/webdav_mount"
    DAV_REMOTE_ROOT="${DAV_REMOTE_ROOT#/}"
    DAV_REMOTE_ROOT="${DAV_REMOTE_ROOT%/}"
    [ -n "$DAV_REMOTE_ROOT" ] || DAV_REMOTE_ROOT="ism_images"
    DAV_UPLOAD_ROOT="${DAV_MOUNT}/${DAV_REMOTE_ROOT}"
}

ensure_state_defaults() {
    : "${DAV_URL:=}"
    : "${DAV_MOUNT:=/mnt/webdav_mount}"
    : "${DAV_REMOTE_ROOT:=ism_images}"
    : "${DAV_USER:=}"
    : "${DAV_PASS:=}"
    if [ -n "$DAV_URL" ]; then
        recompute_dav_paths
    else
        DAV_MOUNT="${DAV_MOUNT%/}"
        [ -n "$DAV_MOUNT" ] || DAV_MOUNT="/mnt/webdav_mount"
        DAV_REMOTE_ROOT="${DAV_REMOTE_ROOT#/}"
        DAV_REMOTE_ROOT="${DAV_REMOTE_ROOT%/}"
        [ -n "$DAV_REMOTE_ROOT" ] || DAV_REMOTE_ROOT="ism_images"
        DAV_UPLOAD_ROOT="${DAV_MOUNT}/${DAV_REMOTE_ROOT}"
    fi
}

wait_for_port() {
    local port="$1"
    local tries="${2:-10}"
    local i
    for i in $(seq 1 "$tries"); do
        if ss -lnt "( sport = :${port} )" 2>/dev/null | grep -q ":${port}"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

patch_config_upload_folder() {
    local mount_path="$1"
    local config_file="${APP_ROOT}/config.py"
    if [ ! -f "$config_file" ]; then
        err "未找到 ${config_file}"
        return 1
    fi

    python3 - "$config_file" "$mount_path" <<'PY'
from pathlib import Path
import sys, re
config_file = Path(sys.argv[1])
mount_path = sys.argv[2]
text = config_file.read_text(encoding="utf-8")
new_line = f'    UPLOAD_FOLDER = "{mount_path}"'
text2, n = re.subn(r'^\s*UPLOAD_FOLDER\s*=.*$', new_line, text, count=1, flags=re.MULTILINE)
if n != 1:
    raise SystemExit("未找到 UPLOAD_FOLDER 配置项")
config_file.write_text(text2, encoding="utf-8")
print("patched", config_file)
PY
}

patch_systemd_workers_and_dependencies() {
    if [ ! -f "$SYSTEMD_SERVICE_FILE" ]; then
        warn "未找到 ${SYSTEMD_SERVICE_FILE}，将跳过 systemd 补丁"
        return 0
    fi

    python3 - "$SYSTEMD_SERVICE_FILE" "$VENV_DIR" "$INTERNAL_PORT" "$GUNICORN_WORKERS" "${WEBDAV_SERVICE_NAME}.service" <<'PY'
from pathlib import Path
import sys, re
svc = Path(sys.argv[1])
venv = sys.argv[2]
port = sys.argv[3]
workers = sys.argv[4]
webdav_unit = sys.argv[5]
text = svc.read_text(encoding='utf-8')
after_line = f'After=network-online.target mariadb.service {webdav_unit}'
text = re.sub(r'^After=.*$', after_line, text, flags=re.MULTILINE)
text = re.sub(r'^Wants=.*$', 'Wants=network-online.target', text, flags=re.MULTILINE)
if 'Wants=network-online.target' not in text:
    text = text.replace(after_line + '\n', after_line + '\nWants=network-online.target\n', 1)
text = re.sub(r'^Requires=.*$', '', text, flags=re.MULTILINE)
if f'Requires={webdav_unit}' not in text:
    text = text.replace('[Service]\n', f'Requires={webdav_unit}\n\n[Service]\n', 1)
text = re.sub(r'\n{3,}', '\n\n', text)
exec_line = f'ExecStart={venv}/bin/gunicorn --workers {workers} --bind 127.0.0.1:{port} run:app'
text = re.sub(r'^ExecStart=.*$', exec_line, text, flags=re.MULTILINE)
svc.write_text(text, encoding='utf-8')
print('patched', svc)
PY

    systemctl daemon-reload
    ok "asset_manager systemd 已切换为 WebDAV 依赖，并将 gunicorn workers 调整为 ${GUNICORN_WORKERS}"
}

install_packages() {
    export DEBIAN_FRONTEND=noninteractive
    info "安装依赖（含 OCR 系统包）"
    apt-get update
    apt-get install -y \
        nginx mariadb-server cron curl unzip \
        python3 python3-venv python3-pip python3-dev \
        build-essential default-libmysqlclient-dev pkg-config \
        tesseract-ocr tesseract-ocr-chi-sim
    systemctl enable --now mariadb
    systemctl enable --now nginx
    systemctl enable --now cron
    ok "依赖安装完成"
}

prepare_dirs() {
    mkdir -p "$APP_ROOT" "$BACKUP_DIR" "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR" "$TMP_DIR"
}

download_files() {
    info "下载项目文件"
    rm -rf "$TMP_DIR"
    mkdir -p "$TMP_DIR"

    curl -L --fail --retry 3 -o "$TMP_DIR/app.zip" "$APP_ZIP_URL"
    curl -L --fail --retry 3 -o "$TMP_DIR/config.py" "$CONFIG_URL"
    curl -L --fail --retry 3 -o "$TMP_DIR/run.py" "$RUN_URL"
    curl -L --fail --retry 3 -o "$TMP_DIR/requirements.txt" "$REQ_URL"
    curl -L --fail --retry 3 -o "$TMP_DIR/asset_manager.sql" "$SQL_URL"
    curl -L --fail --retry 3 -o "$TMP_DIR/routes___init__.py" "$ROUTES_INIT_URL"
    curl -L --fail --retry 3 -o "$TMP_DIR/ocr_recognizer.py" "$OCR_URL"

    ok "项目文件下载完成"
}

deploy_files() {
    info "部署应用文件"
    if [ -d "$APP_ROOT" ] && [ -n "$(find "$APP_ROOT" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
        ts="$(date +%Y%m%d_%H%M%S)"
        mv "$APP_ROOT" "${APP_ROOT}.bak.${ts}"
        warn "检测到已有 ${APP_ROOT}，已备份为 ${APP_ROOT}.bak.${ts}"
    fi

    mkdir -p "$APP_ROOT" "$APP_DIR" "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR" "$BACKUP_DIR"
    rm -rf "$TMP_DIR/app_extract"
    mkdir -p "$TMP_DIR/app_extract"
    unzip -oq "$TMP_DIR/app.zip" -d "$TMP_DIR/app_extract"

    if [ -d "$TMP_DIR/app_extract/app" ]; then
        cp -a "$TMP_DIR/app_extract/app/." "$APP_DIR/"
    else
        cp -a "$TMP_DIR/app_extract/." "$APP_DIR/"
    fi

    cp -f "$TMP_DIR/config.py" "$APP_ROOT/config.py"
    cp -f "$TMP_DIR/run.py" "$APP_ROOT/run.py"
    cp -f "$TMP_DIR/requirements.txt" "$APP_ROOT/requirements.txt"
    cp -f "$TMP_DIR/asset_manager.sql" "$APP_ROOT/asset_manager.sql"

    mkdir -p "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR"
    ok "应用文件已部署"
}

sync_custom_files() {
    info "同步你维护的三个核心文件：routes/__init__.py、routes/ocr_recognizer.py 和 asset_manager.sql"

    mkdir -p "${APP_DIR}/routes"

    if [ -f "$TMP_DIR/routes___init__.py" ]; then
        cp -f "$TMP_DIR/routes___init__.py" "${APP_DIR}/routes/__init__.py"
    else
        err "未找到 $TMP_DIR/routes___init__.py"
        return 1
    fi

    if [ -f "$TMP_DIR/ocr_recognizer.py" ]; then
        cp -f "$TMP_DIR/ocr_recognizer.py" "${APP_DIR}/routes/ocr_recognizer.py"
    else
        err "未找到 $TMP_DIR/ocr_recognizer.py"
        return 1
    fi

    if [ -f "$TMP_DIR/asset_manager.sql" ]; then
        cp -f "$TMP_DIR/asset_manager.sql" "$APP_ROOT/asset_manager.sql"
    else
        err "未找到 $TMP_DIR/asset_manager.sql"
        return 1
    fi

    ok "已覆盖 routes/__init__.py、routes/ocr_recognizer.py 和 asset_manager.sql"
}

setup_python_env() {
    info "创建 Python 虚拟环境并安装 Python 依赖（含 OCR Python 包）"
    python3 -m venv "$VENV_DIR"
    # shellcheck disable=SC1091
    . "$VENV_DIR/bin/activate"
    pip install --upgrade pip wheel setuptools
    pip install -r "$APP_ROOT/requirements.txt"
    pip install pymysql gunicorn Pillow pytesseract
    ok "Python 环境准备完成"
}

setup_database() {
    info "初始化数据库和账号"
    mysql <<EOF_DB
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
EOF_DB

    info "导入数据库备份"
    mysql "$DB_NAME" < "$APP_ROOT/asset_manager.sql"
    ok "数据库已导入"
}

write_systemd() {
    info "写入 systemd 服务"
    cat > "$SYSTEMD_SERVICE_FILE" <<EOF_SYSTEMD
[Unit]
Description=Asset Manager Gunicorn Service
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${APP_ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/gunicorn --workers ${GUNICORN_WORKERS} --bind 127.0.0.1:${INTERNAL_PORT} run:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    if wait_for_port "${INTERNAL_PORT}" 15; then
        ok "systemd 服务已启动，Gunicorn 已监听 127.0.0.1:${INTERNAL_PORT}"
    else
        warn "systemd 服务已启动，但暂未检测到 ${INTERNAL_PORT} 端口监听，请执行：journalctl -u ${SERVICE_NAME} -n 80 --no-pager"
    fi
}

write_nginx_http() {
    cat > "$NGINX_SITE_FILE" <<EOF_NGINX_HTTP
server {
    listen ${PUBLIC_PORT};
    server_name ${DOMAIN:-_};

    client_max_body_size 30m;

    location / {
        proxy_pass http://127.0.0.1:${INTERNAL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 60;
        proxy_send_timeout 300;
    }
}
EOF_NGINX_HTTP
}

write_nginx_https() {
    local cert_dir="/etc/letsencrypt/live/${DOMAIN}"
    cat > "$NGINX_SITE_FILE" <<EOF_NGINX_HTTPS
server {
    listen ${PUBLIC_PORT} ssl;
    server_name ${DOMAIN};

    ssl_certificate ${cert_dir}/fullchain.pem;
    ssl_certificate_key ${cert_dir}/privkey.pem;

    client_max_body_size 30m;

    location / {
        proxy_pass http://127.0.0.1:${INTERNAL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 60;
        proxy_send_timeout 300;
    }
}
EOF_NGINX_HTTPS
}

configure_nginx() {
    load_state
    read -r -p "请输入域名（可留空，直接用 IP:2083 访问）: " domain_input
    if [ -n "${domain_input:-}" ]; then
        DOMAIN="$domain_input"
    fi
    save_state

    info "配置 Nginx 反向代理"
    if [ -n "$DOMAIN" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]; then
        write_nginx_https
        ok "检测到证书，已配置 https://${DOMAIN}:${PUBLIC_PORT}"
    else
        write_nginx_http
        if [ -n "$DOMAIN" ]; then
            warn "未找到该域名证书，已配置为 http://${DOMAIN}:${PUBLIC_PORT}"
        else
            warn "未填写域名，已配置为 http://服务器IP:${PUBLIC_PORT}"
        fi
    fi

    ln -sf "$NGINX_SITE_FILE" "$NGINX_SITE_LINK"
    nginx -t
    systemctl restart nginx
    sleep 1

    if ss -lnt "( sport = :${PUBLIC_PORT} )" 2>/dev/null | grep -q ":${PUBLIC_PORT}"; then
        ok "Nginx 配置完成，已监听端口 ${PUBLIC_PORT}"
    else
        warn "Nginx 已重启，但暂未检测到 ${PUBLIC_PORT} 端口监听，请执行：systemctl status nginx --no-pager"
    fi
}

write_webdav_mount_service() {
    cat > "$WEBDAV_MOUNT_SERVICE" <<EOF_SYSTEMD
[Unit]
Description=Mount Generic WebDAV
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p ${DAV_MOUNT}
ExecStart=/usr/bin/mount -t davfs ${DAV_URL} ${DAV_MOUNT}
ExecStop=/bin/umount -l ${DAV_MOUNT}
TimeoutStartSec=60

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
}

write_davfs_mount_config() {
    info "写入 /etc/davfs2/davfs2.conf"
    python3 - "$DAV_MOUNT" <<'PY'
from pathlib import Path
import sys, re
mount_path = sys.argv[1]
p = Path('/etc/davfs2/davfs2.conf')
text = p.read_text(encoding='utf-8') if p.exists() else ''
block = f'[{mount_path}]\nuse_locks 0\nbuf_size 64\n'
pattern = re.compile(rf'^\[{re.escape(mount_path)}\]\n(?:.*\n)*?(?=^\[|\Z)', re.MULTILINE)
if pattern.search(text):
    text = pattern.sub(block, text).rstrip() + '\n'
else:
    if text and not text.endswith('\n'):
        text += '\n'
    text += '\n' + block
p.write_text(text, encoding='utf-8')
PY
}

write_davfs_secrets_entry() {
    info "写入 /etc/davfs2/secrets"
    python3 - "$DAV_MOUNT" "$DAV_USER" "$DAV_PASS" <<'PY'
from pathlib import Path
import sys
mount_path, user, passwd = sys.argv[1:4]
p = Path('/etc/davfs2/secrets')
lines = p.read_text(encoding='utf-8').splitlines() if p.exists() else []
lines = [line for line in lines if not line.startswith(mount_path + ' ')]
lines.append(f'{mount_path} {user} {passwd}')
p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
    chmod 600 /etc/davfs2/secrets
}

show_reset_backup_cron_notice() {
    printf "\n${BOLD}${YELLOW}=================================================================${NC}\n"
    printf "${BOLD}${RED} ★ WebDAV 配置已变化，请立即重新执行菜单 6 重置 cron 备份任务 ★ ${NC}\n"
    printf "${BOLD}${WHITE} 当前备份脚本：${BACKUP_SCRIPT}${NC}\n"
    printf "${BOLD}${WHITE} 当前日志文件：${BACKUP_LOG_FILE}${NC}\n"
    printf "${BOLD}${YELLOW}=================================================================${NC}\n\n"
}

apply_webdav_settings() {
    local mode="$1"

    if [ "$mode" = "install" ]; then
        info "安装 WebDAV 依赖"
        apt-get update
        apt-get install -y davfs2
    else
        if ! command -v mount.davfs >/dev/null 2>&1; then
            err "未检测到 davfs2，请先执行菜单 4 并选择 y 安装 WebDAV"
            return 1
        fi
    fi

    mkdir -p "$DAV_MOUNT"
    write_davfs_mount_config
    write_davfs_secrets_entry

    info "写入 ${WEBDAV_SERVICE_NAME}.service"
    write_webdav_mount_service
    systemctl daemon-reload

    info "重新挂载 WebDAV"
    systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    systemctl enable --now "${WEBDAV_SERVICE_NAME}.service"

    info "测试 WebDAV 目录读写并创建程序目录"
    ls -lah "$DAV_MOUNT"
    mkdir -p "$DAV_UPLOAD_ROOT/assets"
    mkdir -p "$DAV_UPLOAD_ROOT/accessories"
    touch "$DAV_UPLOAD_ROOT/test_write.txt"

    info "修改程序图片保存目录"
    if [ -f "${APP_ROOT}/config.py" ]; then
        cp -f "${APP_ROOT}/config.py" "${APP_ROOT}/config.py.bak_webdav_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$DAV_UPLOAD_ROOT"
    else
        warn "未发现 ${APP_ROOT}/config.py，跳过程序配置修改"
    fi

    patch_systemd_workers_and_dependencies
    save_state
    write_backup_script

    if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    if [ "$mode" = "install" ]; then
        ok "WebDAV 已安装并接入程序"
    else
        ok "WebDAV 参数已重置并完成切换"
    fi
    echo "当前 WebDAV 挂载点：$DAV_MOUNT"
    echo "当前程序图片目录：$DAV_UPLOAD_ROOT"
    echo "远端业务目录：/${DAV_REMOTE_ROOT}/assets 和 /${DAV_REMOTE_ROOT}/accessories"
    echo "备份脚本已重建：$BACKUP_SCRIPT"
    show_reset_backup_cron_notice
}

prompt_webdav_install() {
    echo "WebDAV 安装说明："
    echo "1) 这里是直连网盘或存储提供的 WebDAV，不再使用 OpenList。"
    echo "2) 请从你的网盘或存储后台获取 WebDAV Connection URL、Connection ID（或用户名）、Password。"
    echo "3) 程序远端默认目录固定为：/ism_images/assets 和 /ism_images/accessories。"
    echo "4) 数据库备份会同步到：/ism_images/asset_manager_latest.sql。"
    echo

    read -r -p "请输入 WebDAV Connection URL [${DAV_URL:-请从网盘后台复制}]: " input_dav_url
    read -r -p "请输入 Connection ID / 用户名 [${DAV_USER:-按网盘后台显示填写}]: " input_user
    read -r -p "请输入 Password: " input_pass
    read -r -p "请输入本机挂载目录 [${DAV_MOUNT}]: " input_mount

    if [ -n "${input_dav_url:-}" ]; then DAV_URL="$input_dav_url"; fi
    if [ -n "${input_user:-}" ]; then DAV_USER="$input_user"; fi
    if [ -n "${input_pass:-}" ]; then DAV_PASS="$input_pass"; fi
    if [ -n "${input_mount:-}" ]; then DAV_MOUNT="$input_mount"; fi

    if [ -z "$DAV_URL" ] || [ -z "$DAV_USER" ] || [ -z "$DAV_PASS" ]; then
        err "WebDAV Connection URL、Connection ID、Password 不能为空"
        return 1
    fi

    recompute_dav_paths
    echo
    echo "当前 WebDAV Connection URL：${DAV_URL}"
    echo "当前本机挂载点：${DAV_MOUNT}"
    echo "当前程序图片目录：${DAV_UPLOAD_ROOT}"
    echo "远端业务目录：/${DAV_REMOTE_ROOT}/assets 和 /${DAV_REMOTE_ROOT}/accessories"
    echo

    apply_webdav_settings install
}

prompt_webdav_reset() {
    load_state
    ensure_state_defaults

    if [ -z "$DAV_MOUNT" ]; then
        err "未找到现有挂载目录，请先执行菜单 4 并选择 y 安装 WebDAV"
        return 1
    fi

    echo "WebDAV 重置说明："
    echo "1) 仅重置 WebDAV 连接参数并重新挂载。"
    echo "2) 不安装新软件，不修改 Nginx / MariaDB / Python 环境。"
    echo "3) 使用当前本机挂载目录：${DAV_MOUNT}"
    echo "4) 远端业务目录保持为：/${DAV_REMOTE_ROOT}/assets 和 /${DAV_REMOTE_ROOT}/accessories"
    echo

    read -r -p "请输入新的 WebDAV Connection URL [${DAV_URL:-请从网盘后台复制}]: " input_dav_url
    read -r -p "请输入新的 Connection ID / 用户名 [${DAV_USER:-按网盘后台显示填写}]: " input_user
    read -r -p "请输入新的 Password [直接回车则保持当前密码]: " input_pass

    if [ -n "${input_dav_url:-}" ]; then DAV_URL="$input_dav_url"; fi
    if [ -n "${input_user:-}" ]; then DAV_USER="$input_user"; fi
    if [ -n "${input_pass:-}" ]; then DAV_PASS="$input_pass"; fi

    if [ -z "$DAV_URL" ] || [ -z "$DAV_USER" ] || [ -z "$DAV_PASS" ]; then
        err "WebDAV Connection URL、Connection ID、Password 不能为空"
        return 1
    fi

    recompute_dav_paths
    echo
    echo "将重置为新的 WebDAV Connection URL：${DAV_URL}"
    echo "当前本机挂载点保持：${DAV_MOUNT}"
    echo "当前程序图片目录：${DAV_UPLOAD_ROOT}"
    echo "远端业务目录保持：/${DAV_REMOTE_ROOT}/assets 和 /${DAV_REMOTE_ROOT}/accessories"
    echo

    apply_webdav_settings reset
}

install_webdav() {
    load_state
    ensure_state_defaults

    echo "菜单 4：安装/重置 WebDAV"
    echo "  y = 安装 WebDAV"
    echo "  c = 重置 WebDAV 参数并切换网盘"
    echo "  n = 跳过，返回主菜单"
    read -r -p "请选择 [y/c/n]: " webdav_action

    case "${webdav_action:-n}" in
        y|Y)
            prompt_webdav_install
            ;;
        c|C)
            prompt_webdav_reset
            ;;
        n|N|"")
            warn "已跳过 WebDAV 配置，返回主菜单"
            return 0
            ;;
        *)
            warn "无效选项，返回主菜单"
            return 0
            ;;
    esac
}

check_webdav_connectivity() {
    load_state
    ensure_state_defaults

    if [ -z "$DAV_MOUNT" ] || [ -z "$DAV_URL" ]; then
        err "未找到 WebDAV 配置，请先执行“安装 WebDAV”"
        return 1
    fi

    info "检测 WebDAV 连通性"
    mkdir -p "$DAV_MOUNT"
    systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    systemctl start "${WEBDAV_SERVICE_NAME}.service"

    ls -lah "$DAV_MOUNT"
    mkdir -p "$DAV_UPLOAD_ROOT/assets" "$DAV_UPLOAD_ROOT/accessories"
    touch "$DAV_UPLOAD_ROOT/.webdav_probe_$(date +%Y%m%d_%H%M%S)"
    ok "WebDAV 连通性检测通过"
}

write_backup_script() {
    mkdir -p "$APP_ROOT"
    cat > "$BACKUP_SCRIPT" <<EOF_BACKUP
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR}"
BACKUP_FILE="${BACKUP_FILE}"
DB_NAME="${DB_NAME}"
DB_USER="${DB_USER}"
DB_PASS="${DB_PASS}"
DAV_MOUNT="${DAV_MOUNT}"
DAV_UPLOAD_ROOT="${DAV_UPLOAD_ROOT}"
REMOTE_BACKUP_FILE="${DAV_UPLOAD_ROOT}/asset_manager_latest.sql"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE}"

mkdir -p "\$BACKUP_DIR"
rm -f "\$BACKUP_DIR"/*.sql
mysqldump -u"\$DB_USER" -p"\$DB_PASS" "\$DB_NAME" > "\$BACKUP_FILE"

if systemctl list-unit-files 2>/dev/null | grep -q '^${WEBDAV_SERVICE_NAME}\.service'; then
    if ! mountpoint -q "\$DAV_MOUNT"; then
        systemctl restart "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        sleep 2
    fi
fi

if mountpoint -q "\$DAV_MOUNT"; then
    mkdir -p "\$DAV_UPLOAD_ROOT"
    find "\$DAV_UPLOAD_ROOT" -maxdepth 1 -type f -name '*.sql' -delete || true
    cp -f "\$BACKUP_FILE" "\$REMOTE_BACKUP_FILE"
    echo "[OK] 云盘备份已同步到 \$REMOTE_BACKUP_FILE"
else
    echo "[WARN] WebDAV 未挂载，仅保留本地备份：\$BACKUP_FILE"
fi
EOF_BACKUP
    chmod +x "$BACKUP_SCRIPT"
    ok "数据库备份脚本已生成：$BACKUP_SCRIPT"
}

install_backup_cron() {
    if [ ! -f "$BACKUP_SCRIPT" ]; then
        err "未找到备份脚本：$BACKUP_SCRIPT，请先执行菜单 4 安装或重置 WebDAV 以自动生成脚本"
        return 1
    fi

    info "生成 cron 自动备份任务"
    cat > "$CRON_BACKUP_FILE" <<EOF_CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 2 * * * root ${BACKUP_SCRIPT} >> ${BACKUP_LOG_FILE} 2>&1
EOF_CRON
    chmod 644 "$CRON_BACKUP_FILE"
    systemctl restart cron
    ok "cron 自动备份已开启：每天 02:00 执行 ${BACKUP_SCRIPT}"
}

show_backup_cron_status() {
    info "查看 cron 备份任务配置"
    if [ -f "$CRON_BACKUP_FILE" ]; then
        cat "$CRON_BACKUP_FILE"
    else
        warn "当前未发现 cron 备份任务：$CRON_BACKUP_FILE"
    fi

    echo
    info "查看备份脚本"
    if [ -f "$BACKUP_SCRIPT" ]; then
        ls -lah "$BACKUP_SCRIPT"
    else
        warn "当前未发现备份脚本：$BACKUP_SCRIPT"
    fi

    echo
    info "查看 cron 服务状态"
    systemctl status cron --no-pager || true

    echo
    info "查看最近备份日志"
    if [ -f "$BACKUP_LOG_FILE" ]; then
        tail -n 50 "$BACKUP_LOG_FILE"
    else
        warn "当前还没有备份日志：$BACKUP_LOG_FILE"
    fi
}

delete_backup_cron() {
    if [ -f "$CRON_BACKUP_FILE" ]; then
        rm -f "$CRON_BACKUP_FILE"
        systemctl restart cron
        ok "cron 自动备份任务已删除：$CRON_BACKUP_FILE"
    else
        warn "未找到 cron 备份任务，无需删除"
    fi
}

setup_backup() {
    load_state
    ensure_state_defaults

    echo "菜单 6：管理 cron 数据库备份任务"
    echo "  1 = 生成/重置 cron 备份任务"
    echo "  2 = 查看 cron 任务和运行情况"
    echo "  3 = 删除 cron 备份任务"
    echo "  0 = 返回主菜单"
    read -r -p "请选择 [1/2/3/0]: " backup_choice

    case "${backup_choice:-0}" in
        1)
            install_backup_cron
            ;;
        2)
            show_backup_cron_status
            ;;
        3)
            delete_backup_cron
            ;;
        0|"")
            warn "已返回主菜单"
            return 0
            ;;
        *)
            warn "无效选项，返回主菜单"
            return 0
            ;;
    esac
}

restore_database() {
    if [ ! -f "$BACKUP_FILE" ]; then
        err "未找到备份文件：$BACKUP_FILE"
        exit 1
    fi

    warn "即将使用备份文件恢复数据库：$BACKUP_FILE"
    read -r -p "输入 YES 确认恢复： " confirm_text
    if [ "$confirm_text" != "YES" ]; then
        warn "已取消恢复"
        return 0
    fi

    info "恢复数据库"
    mysql "$DB_NAME" < "$BACKUP_FILE"
    ok "数据库恢复完成"
}

uninstall_webdav() {
    load_state
    ensure_state_defaults

    warn "该操作会卸载本机 WebDAV 挂载，并把程序图片目录切回本地：${LOCAL_UPLOAD_ROOT}"
    warn "不会删除你在云盘上已存在的业务文件"
    read -r -p "输入 YES 确认卸载 WebDAV: " confirm_text
    if [ "${confirm_text:-}" != "YES" ]; then
        warn "已取消卸载"
        return 0
    fi

    info "停止并卸载 ${WEBDAV_SERVICE_NAME}.service"
    systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl disable "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    rm -f "$WEBDAV_MOUNT_SERVICE"

    info "清理 davfs 配置"
    python3 - "$DAV_MOUNT" <<'PY'
from pathlib import Path
import sys, re
mount_path = sys.argv[1]
conf = Path('/etc/davfs2/davfs2.conf')
if conf.exists():
    text = conf.read_text(encoding='utf-8')
    pattern = re.compile(rf'^\[{re.escape(mount_path)}\]\n(?:.*\n)*?(?=^\[|\Z)', re.MULTILINE)
    text = pattern.sub('', text).strip()
    conf.write_text((text + '\n') if text else '', encoding='utf-8')
secrets = Path('/etc/davfs2/secrets')
if secrets.exists():
    lines = [line for line in secrets.read_text(encoding='utf-8').splitlines() if not line.startswith(mount_path + ' ')]
    secrets.write_text(('\n'.join(lines).rstrip() + '\n') if lines else '', encoding='utf-8')
PY

    info "卸载 davfs2 软件包"
    apt-get remove -y davfs2 || true
    apt-get autoremove -y || true

    info "将程序图片目录切回本地"
    mkdir -p "$LOCAL_UPLOAD_ROOT/assets" "$LOCAL_UPLOAD_ROOT/accessories"
    if [ -f "${APP_ROOT}/config.py" ]; then
        cp -f "${APP_ROOT}/config.py" "${APP_ROOT}/config.py.bak_local_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$LOCAL_UPLOAD_ROOT"
    fi

    if [ -f "$SYSTEMD_SERVICE_FILE" ]; then
        write_systemd
    fi

    systemctl daemon-reload
    ok "WebDAV 已卸载，本地上传目录已恢复为：${LOCAL_UPLOAD_ROOT}"
}

restart_service() {
    info "重启系统"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager || true
}

install_asset_system() {
    prepare_dirs
    download_files
    deploy_files
    sync_custom_files
    setup_python_env
    setup_database
    write_systemd
    configure_nginx

    ok "系统安装完成"
    echo
    echo "内部 Gunicorn 端口：127.0.0.1:${INTERNAL_PORT}"
    echo "外部 Nginx 端口：${PUBLIC_PORT}"
    echo "当前默认图片目录（未接云前）：${ASSET_IMG_DIR} 和 ${ACCESSORY_IMG_DIR}"
    echo "本次安装最后已固定覆盖三个核心文件："
    echo "1) ${APP_DIR}/routes/__init__.py"
    echo "2) ${APP_DIR}/routes/ocr_recognizer.py"
    echo "3) ${APP_ROOT}/asset_manager.sql"
    echo "建议下一步顺序：4. 安装 WebDAV -> 5. WebDAV 连通性检测 -> 6. 管理cron备份任务"
}

confirm_install_asset_system() {
    echo "你选择了【2 安装系统】。"
    echo "该操作会部署程序、初始化数据库、配置 systemd 和 Nginx。"
    read -r -p "确认安装吗？输入 y/Y 继续，n/N 取消并返回主菜单: " confirm_install

    case "${confirm_install:-n}" in
        y|Y)
            install_asset_system
            ;;
        n|N|"")
            warn "已取消安装，返回主菜单"
            return 0
            ;;
        *)
            warn "无效输入，已取消安装，返回主菜单"
            return 0
            ;;
    esac
}

show_menu() {
    clear
    printf "\n"
    printf "${BOLD}${BLUE}=========================================================================${NC}\n"
    printf "${BOLD}${WHITE}              asset_manager WebDAV 管理菜单                             ${NC}\n"
    printf "${BOLD}${BLUE}=========================================================================${NC}\n"

    printf "${BOLD}${GREEN} [1] 安装依赖${NC}              ${WHITE}安装基础环境：Nginx / MariaDB / Python / OCR${NC}\n"
    printf "${BOLD}${RED} [2] 安装系统${NC}              ${WHITE}部署程序、初始化数据库、配置服务${NC}\n"
    printf "${BOLD}${BLUE}-------------------------------------------------------------------------${NC}\n"
	
    printf "${BOLD}${CYAN} [3] 重启系统${NC}              ${WHITE}重启 asset_manager 服务${NC}\n"
    printf "${BOLD}${YELLOW} [4] 安装/重置 WebDAV${NC}      ${WHITE}首次挂载或切换新的 WebDAV 网盘${NC}\n"
    printf "${BOLD}${YELLOW} [5] WebDAV 连通性检测${NC}     ${WHITE}检测挂载、目录、写入是否正常${NC}\n"

    printf "${BOLD}${YELLOW} [6] 管理cron备份任务${NC}      ${WHITE}生成、查看、删除数据库备份 cron${NC}\n"
    printf "${BOLD}${MAGENTA} [7] 恢复数据库${NC}            ${WHITE}从本地最新备份恢复数据库${NC}\n"

    printf "${BOLD}${RED} [8] 卸载WebDAV${NC}            ${YELLOW}移除挂载并恢复本地上传目录${NC}\n"
    printf "${BOLD}${RED} [0] 退出${NC}                  ${WHITE}退出当前脚本${NC}\n"

    printf "${BOLD}${BLUE}-------------------------------------------------------------------------${NC}\n"
    printf "${BOLD}${YELLOW} ★ 推荐顺序：${NC}${GREEN}1 -> 2 -> 4 -> 5 -> 6${NC}\n"
    printf "${BOLD}${RED} ★ 注意：${NC}${WHITE}菜单 4 会重建 ${BOLD}${YELLOW}ism_backup.sh${NC}${WHITE}，完成后请再执行菜单 6 重置 cron${NC}\n"
    printf "${BOLD}${BLUE}=========================================================================${NC}\n"
    printf "\n"
}

main() {
    require_root
    prepare_dirs
    load_state
    ensure_state_defaults

    while true; do
        show_menu
        read -r -p "请输入菜单编号: " choice
        echo
        case "${choice:-}" in
            1) install_packages ;;
            2) confirm_install_asset_system ;;
            3) restart_service ;;
            4) install_webdav ;;
            5) check_webdav_connectivity ;;
            6) setup_backup ;;
            7) restore_database ;;
            8) uninstall_webdav ;;
            0) exit 0 ;;
            *) warn "无效选项" ;;
        esac
        echo
        read -r -p "按回车继续..." _
    done
}

main "$@"
