#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/root/ism"
APP_DIR="${APP_ROOT}/app"
VENV_DIR="${APP_ROOT}/venv"
BACKUP_DIR="${APP_ROOT}/backups"
BACKUP_FILE="${BACKUP_DIR}/ism_latest.sql"
BACKUP_SCRIPT="${APP_ROOT}/ism_backup.sh"
CRON_BACKUP_FILE="/etc/cron.d/ism_backup"
BACKUP_LOG_FILE="/var/log/ism_backup.log"

SERVICE_NAME="ism"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE_FILE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_SITE_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"
STATE_FILE="/root/.ism_install.conf"

REPO_ZIP_URL="https://github.com/byilrq/ism/archive/main.zip"

TMP_DIR="/tmp/ism_install"
DB_NAME="ism"
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

STORAGE_BACKEND="local"
CLOUDDRIVE_SOURCE=""
RCLONE_MOUNT="/mnt/rclone"
RCLONE_REMOTE=""
ASSET_CLOUDDRIVE_BIND_SERVICE_NAME="asset-manager-clouddrive-bind"
ASSET_CLOUDDRIVE_BIND_SERVICE="/etc/systemd/system/${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service"
ASSET_CLOUDDRIVE_WAIT_SCRIPT="/usr/local/bin/asset-manager-clouddrive-wait.sh"
ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME="asset-manager-clouddrive-rebind"
ASSET_CLOUDDRIVE_REBIND_SERVICE="/etc/systemd/system/${ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME}.service"

CD_INSTALL_DIR="/opt/clouddrive"
CD_BIN_FILE="${CD_INSTALL_DIR}/clouddrive"
CD_BIN_LINK="/usr/local/bin/clouddrive"
CD_SERVICE_NAME="clouddrive"
CD_SERVICE_FILE="/etc/systemd/system/${CD_SERVICE_NAME}.service"
CD_HOME="/var/lib/clouddrive"
CD_MOUNT_DIR="/mnt/CloudDrive"
CD_WEB_PORT="19798"
CD_MOUNT_RECOVERY_DIR="/var/lib/clouddrive-mount-recovery"
CD_MOUNT_GUARD_SCRIPT="/usr/local/bin/clouddrive-mount-guard.sh"
GITHUB_API_LATEST="https://api.github.com/repos/cloud-fs/cloud-fs.github.io/releases/latest"
CD_ARCH=""
CD_PACKAGE_URL=""
CD_PACKAGE_NAME=""
CD_DOWNLOAD_FILE=""

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
        err "请使用 root 运行：sudo bash ISM.sh"
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
PUBLIC_PORT=${PUBLIC_PORT@Q}
DAV_URL=${DAV_URL@Q}
DAV_MOUNT=${DAV_MOUNT@Q}
DAV_REMOTE_ROOT=${DAV_REMOTE_ROOT@Q}
DAV_UPLOAD_ROOT=${DAV_UPLOAD_ROOT@Q}
DAV_USER=${DAV_USER@Q}
DAV_PASS=${DAV_PASS@Q}
STORAGE_BACKEND=${STORAGE_BACKEND@Q}
CLOUDDRIVE_SOURCE=${CLOUDDRIVE_SOURCE@Q}
RCLONE_MOUNT=${RCLONE_MOUNT@Q}
RCLONE_REMOTE=${RCLONE_REMOTE@Q}
CD_INSTALL_DIR=${CD_INSTALL_DIR@Q}
CD_BIN_FILE=${CD_BIN_FILE@Q}
CD_HOME=${CD_HOME@Q}
CD_MOUNT_DIR=${CD_MOUNT_DIR@Q}
CD_WEB_PORT=${CD_WEB_PORT@Q}
CD_MOUNT_RECOVERY_DIR=${CD_MOUNT_RECOVERY_DIR@Q}
CD_MOUNT_GUARD_SCRIPT=${CD_MOUNT_GUARD_SCRIPT@Q}
EOF_STATE
}

recompute_dav_paths() {
    if [ -n "${DAV_URL:-}" ]; then
        DAV_URL="${DAV_URL%/}/"
    fi
    DAV_MOUNT="${DAV_MOUNT%/}"
    [ -n "$DAV_MOUNT" ] || DAV_MOUNT="/mnt/webdav_mount"
    DAV_REMOTE_ROOT="${DAV_REMOTE_ROOT#/}"
    DAV_REMOTE_ROOT="${DAV_REMOTE_ROOT%/}"
    [ -n "$DAV_REMOTE_ROOT" ] || DAV_REMOTE_ROOT="ism_images"
    DAV_UPLOAD_ROOT="${DAV_MOUNT}/${DAV_REMOTE_ROOT}"
}

ensure_state_defaults() {
    : "${PUBLIC_PORT:=2083}"
    : "${DAV_URL:=}"
    : "${DAV_MOUNT:=/mnt/webdav_mount}"
    : "${DAV_REMOTE_ROOT:=ism_images}"
    : "${DAV_USER:=}"
    : "${DAV_PASS:=}"
    : "${STORAGE_BACKEND:=local}"
    : "${CLOUDDRIVE_SOURCE:=}"
    : "${RCLONE_MOUNT:=/mnt/rclone}"
    : "${RCLONE_REMOTE:=}"
    : "${CD_INSTALL_DIR:=/opt/clouddrive}"
    : "${CD_BIN_FILE:=${CD_INSTALL_DIR}/clouddrive}"
    : "${CD_HOME:=/var/lib/clouddrive}"
    : "${CD_MOUNT_DIR:=/mnt/CloudDrive}"
    : "${CD_WEB_PORT:=19798}"
    : "${CD_MOUNT_RECOVERY_DIR:=/var/lib/clouddrive-mount-recovery}"
    : "${CD_MOUNT_GUARD_SCRIPT:=/usr/local/bin/clouddrive-mount-guard.sh}"
    recompute_dav_paths
}

prepare_dirs() {
    mkdir -p "$TMP_DIR" "$DAV_MOUNT" "$CD_MOUNT_DIR"
}

submenu_pause() {
    echo
    read -r -p "按回车继续当前菜单..." _
}

wait_for_port() {
    local port="$1"
    local tries="${2:-10}"
    local i
    for i in $(seq 1 "$tries"); do
        if ss -lnt 2>/dev/null | awk '{print $4}' | grep -q ":${port}$"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

systemd_unit_exists() {
    local unit_name="$1"

    if [ -f "/etc/systemd/system/${unit_name}" ] || [ -f "/lib/systemd/system/${unit_name}" ] || [ -f "/usr/lib/systemd/system/${unit_name}" ]; then
        return 0
    fi

    if systemctl cat "$unit_name" >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

get_host_ip() {
    hostname -I 2>/dev/null | awk '{print $1}'
}

normalize_clouddrive_source() {
    local input_path="$1"
    input_path="${input_path%/}"
    [ -n "$input_path" ] || input_path="${CD_MOUNT_DIR}/ism_images"
    CLOUDDRIVE_SOURCE="$input_path"
}

check_fuse() {
    if [ ! -e /dev/fuse ]; then
        warn "未检测到 /dev/fuse，尝试加载 fuse 内核模块"
        modprobe fuse >/dev/null 2>&1 || true
    fi

    if [ ! -e /dev/fuse ]; then
        err "当前系统没有可用的 /dev/fuse，CloudDrive 无法挂载本地目录"
        return 1
    fi
    return 0
}

install_dependencies() {
    export DEBIAN_FRONTEND=noninteractive
    info "安装依赖：MariaDB / Python / OCR / WebDAV / CloudDrive 运行库"

    local pkgs="mariadb-server cron curl unzip ca-certificates jq tar fuse3 davfs2 \
        python3 python3-venv python3-pip python3-dev \
        build-essential default-libmysqlclient-dev pkg-config \
        tesseract-ocr tesseract-ocr-chi-sim"

    if command -v nginx >/dev/null 2>&1; then
        warn "检测到系统已安装 nginx，跳过 nginx 安装以保护现有业务"
    else
        info "系统未安装 nginx，将一并安装"
        pkgs="nginx ${pkgs}"
    fi

    apt-get update
    apt-get install -y $pkgs

    systemctl enable --now mariadb
    if command -v nginx >/dev/null 2>&1; then
        systemctl enable --now nginx 2>/dev/null || true
    fi
    systemctl enable --now cron
    check_fuse
    save_state
    ok "依赖安装完成"
}

patch_config_upload_folder() {
    local mount_path="$1"
    local config_file="${APP_ROOT}/config.yaml"
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
new_line = f'upload_folder: {mount_path}'
text2, n = re.subn(r'^upload_folder:.*$', new_line, text, count=1, flags=re.MULTILINE)
if n != 1:
    raise SystemExit("未找到 upload_folder 配置项")
config_file.write_text(text2, encoding="utf-8")
print("patched", config_file)
PY
}

patch_systemd_workers_and_dependencies() {
    local dep_unit="$1"
    if [ ! -f "$SYSTEMD_SERVICE_FILE" ]; then
        warn "未找到 ${SYSTEMD_SERVICE_FILE}，将跳过 systemd 补丁"
        return 0
    fi

    python3 - "$SYSTEMD_SERVICE_FILE" "$VENV_DIR" "$INTERNAL_PORT" "$GUNICORN_WORKERS" "$dep_unit" <<'PY'
from pathlib import Path
import sys, re
svc = Path(sys.argv[1])
venv = sys.argv[2]
port = sys.argv[3]
workers = sys.argv[4]
dep_unit = sys.argv[5]
text = svc.read_text(encoding='utf-8')
after_line = f'After=network-online.target mariadb.service {dep_unit}'
text = re.sub(r'^After=.*$', after_line, text, flags=re.MULTILINE)
text = re.sub(r'^Wants=.*$', 'Wants=network-online.target', text, flags=re.MULTILINE)
if 'Wants=network-online.target' not in text:
    text = text.replace(after_line + '\n', after_line + '\nWants=network-online.target\n', 1)
text = re.sub(r'^Requires=.*$', '', text, flags=re.MULTILINE)
if f'Requires={dep_unit}' not in text:
    text = text.replace('[Service]\n', f'Requires={dep_unit}\n\n[Service]\n', 1)
text = re.sub(r'\n{3,}', '\n\n', text)
exec_line = f'ExecStart={venv}/bin/gunicorn --workers {workers} --bind 127.0.0.1:{port} run:app'
text = re.sub(r'^ExecStart=.*$', exec_line, text, flags=re.MULTILINE)
svc.write_text(text, encoding='utf-8')
print('patched', svc)
PY

    systemctl daemon-reload
    ok "ism systemd 已设置依赖：${dep_unit}"
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

reset_asset_systemd_to_plain() {
    if [ ! -f "$SYSTEMD_SERVICE_FILE" ]; then
        warn "未找到 ${SYSTEMD_SERVICE_FILE}，将跳过 systemd 还原"
        return 0
    fi
    write_systemd
    ok "ism systemd 已恢复为本地/CloudDrive 普通模式"
}

download_files() {
    info "下载项目文件"
    rm -rf "$TMP_DIR"
    mkdir -p "$TMP_DIR"

    local REPO_ZIP_URL="https://github.com/byilrq/ism/archive/main.zip"
    curl -L --fail --retry 3 -o "$TMP_DIR/repo.zip" "$REPO_ZIP_URL"

    mkdir -p "$TMP_DIR/repo_extract"
    unzip -oq "$TMP_DIR/repo.zip" -d "$TMP_DIR/repo_extract"

    local EXTRACTED_DIR=""
    for d in "$TMP_DIR/repo_extract"/*/; do
        EXTRACTED_DIR="$d"
        break
    done

    mkdir -p "$TMP_DIR/app_extract"
    cp -a "${EXTRACTED_DIR}app/." "$TMP_DIR/app_extract/"
    cp -f "${EXTRACTED_DIR}config.yaml" "$TMP_DIR/config.yaml"
    cp -f "${EXTRACTED_DIR}run.py" "$TMP_DIR/run.py"
    cp -f "${EXTRACTED_DIR}requirements.txt" "$TMP_DIR/requirements.txt"
    cp -f "${EXTRACTED_DIR}ism.sql" "$TMP_DIR/ism.sql"
    cp -f "${EXTRACTED_DIR}ism_backup.sh" "$TMP_DIR/ism_backup.sh"

    ok "项目文件下载完成（直接从 GitHub 仓库同步）"
}

deploy_files() {
    local ts
    info "部署应用文件"
    if [ -d "$APP_ROOT" ] && [ -n "$(find "$APP_ROOT" -mindepth 1 -maxdepth 1 2>/dev/null)" ]; then
        ts="$(date +%Y%m%d_%H%M%S)"
        mv "$APP_ROOT" "${APP_ROOT}.bak.${ts}"
        warn "检测到已有 ${APP_ROOT}，已备份为 ${APP_ROOT}.bak.${ts}"
    fi

    mkdir -p "$APP_ROOT" "$APP_DIR" "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR" "$BACKUP_DIR"
    cp -a "$TMP_DIR/app_extract/." "$APP_DIR/"

    cp -f "$TMP_DIR/config.yaml" "$APP_ROOT/config.yaml"
    cp -f "$TMP_DIR/run.py" "$APP_ROOT/run.py"
    cp -f "$TMP_DIR/requirements.txt" "$APP_ROOT/requirements.txt"
    cp -f "$TMP_DIR/ism.sql" "$APP_ROOT/ism.sql"
    cp -f "$TMP_DIR/ism_backup.sh" "$APP_ROOT/ism_backup.sh"
    chmod +x "$APP_ROOT/ism_backup.sh"

    mkdir -p "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR"
    ok "应用文件已部署"
}

sync_custom_files() {
    info "同步仓库主文件"

    ok "app 目录已全部从 GitHub 仓库部署完成"
}

setup_python_env() {
    info "创建 Python 虚拟环境并安装 Python 依赖（含 OCR Python 包）"
    python3 -m venv "$VENV_DIR"
    # shellcheck disable=SC1091
    . "$VENV_DIR/bin/activate"
    pip install --upgrade pip wheel setuptools
    pip install -r "$APP_ROOT/requirements.txt"
    pip install pymysql gunicorn Pillow pytesseract pyyaml
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
    mysql "$DB_NAME" < "$APP_ROOT/ism.sql"
    ok "数据库已导入"
}

setup_admin_user() {
    local admin_user="$1"
    local admin_pass="$2"
    info "写入管理员账号到数据库"
    mysql "$DB_NAME" <<EOF_ADMIN
DELETE FROM users WHERE username='${admin_user}';
INSERT INTO users (username, password) VALUES ('${admin_user}', '${admin_pass}');
EOF_ADMIN
    ok "管理员账号已创建：${admin_user}"
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
    read -r -p "请输入域名（可留空，直接用 IP 访问）: " domain_input
    if [ -n "${domain_input:-}" ]; then
        DOMAIN="$domain_input"
    fi

    echo
    echo "请选择 HTTPS 访问端口："
    echo "  1 = 2083（默认，不影响服务器上现有 nginx 443 业务）"
    echo "  2 = 443（标准 HTTPS 端口）"
    read -r -p "请选择 [1/2] (默认 1): " port_choice

    local use_proxy_mode=0
    local internal_https_port="2443"

    case "${port_choice:-1}" in
        2)
            use_proxy_mode=1
            PUBLIC_PORT="$internal_https_port"
            info "使用端口: 443（代理模式，ISM 监听内部端口 ${internal_https_port}）"
            ;;
        *)
            PUBLIC_PORT="2083"
            info "使用端口: 2083"
            ;;
    esac
    save_state

    info "配置 Nginx 反向代理"

    # 生成 nginx 配置
    if [ -n "$DOMAIN" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]; then
        if [ "$use_proxy_mode" = "1" ]; then
            # 代理模式：只监听内部 HTTPS
            cat > "$NGINX_SITE_FILE" <<EOF_NGINX_HTTPS
server {
    listen 127.0.0.1:${internal_https_port} ssl http2;
    listen [::1]:${internal_https_port} ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    client_max_body_size 30m;

    location / {
        proxy_pass http://127.0.0.1:${INTERNAL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300;
        proxy_connect_timeout 60;
        proxy_send_timeout 300;
    }
}
EOF_NGINX_HTTPS
            ok "代理模式已启用，ISM 监听内部端口 ${internal_https_port}"
            echo -e "${YELLOW}请在代理工具中为 ${DOMAIN}:443 添加以下转发规则：${NC}"
            echo ""
            echo -e "${BOLD}location / {${NC}"
            echo -e "    proxy_pass https://127.0.0.1:${internal_https_port};"
            echo -e "    proxy_ssl_verify off;"
            echo -e "}${NC}"
            echo ""
        else
            # 标准模式：监听 PUBLIC_PORT
            write_nginx_https
            if [ "$PUBLIC_PORT" = "443" ]; then
                ok "检测到证书，已配置 https://${DOMAIN}:${PUBLIC_PORT}"
            else
                ok "检测到证书，已配置 https://${DOMAIN}:${PUBLIC_PORT}"
            fi
        fi
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

    if wait_for_port "${PUBLIC_PORT}" 10; then
        ok "Nginx 配置完成，已监听端口 ${PUBLIC_PORT}"
    else
        warn "Nginx 已重启，但暂未检测到 ${PUBLIC_PORT} 端口监听，请执行：systemctl status nginx --no-pager"
    fi

    save_state
}

stop_asset_clouddrive_bind_service() {
    systemctl stop "${ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl disable "${ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl stop "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl disable "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_UPLOAD_ROOT" >/dev/null 2>&1 || umount -l "$DAV_UPLOAD_ROOT" >/dev/null 2>&1 || true
}

stop_webdav_mount_service() {
    systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl disable "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
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

apply_webdav_settings() {
    local mode="$1"

    if ! command -v mount.davfs >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        info "安装 WebDAV 依赖 davfs2"
        apt-get update
        apt-get install -y davfs2
    fi

    if [ -z "$DAV_URL" ] || [ -z "$DAV_USER" ] || [ -z "$DAV_PASS" ]; then
        err "WebDAV Connection URL、Connection ID、Password 不能为空"
        return 1
    fi

    stop_asset_clouddrive_bind_service

    mkdir -p "$DAV_MOUNT"
    write_davfs_mount_config
    write_davfs_secrets_entry
    write_webdav_mount_service
    systemctl daemon-reload

    info "重新挂载 WebDAV"
    systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    systemctl enable --now "${WEBDAV_SERVICE_NAME}.service"

    info "测试 WebDAV 目录读写并创建程序目录"
    ls -lah "$DAV_MOUNT" || true
    mkdir -p "$DAV_UPLOAD_ROOT/assets" "$DAV_UPLOAD_ROOT/accessories" "$DAV_UPLOAD_ROOT/sql_backups"
    touch "$DAV_UPLOAD_ROOT/test_write.txt"

    if [ -f "${APP_ROOT}/config.yaml" ]; then
        cp -f "${APP_ROOT}/config.yaml" "${APP_ROOT}/config.yaml.bak_webdav_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$DAV_MOUNT"
    else
        warn "未发现 ${APP_ROOT}/config.yaml，跳过程序配置修改"
    fi

    patch_systemd_workers_and_dependencies "${WEBDAV_SERVICE_NAME}.service"
    STORAGE_BACKEND="webdav"
    save_state
    write_backup_script

    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    ok "WebDAV 已${mode}并接入程序"
    echo "当前 WebDAV 挂载点：$DAV_MOUNT"
    echo "当前程序图片目录：$DAV_UPLOAD_ROOT"
    echo "远端业务目录：/${DAV_REMOTE_ROOT}/assets 和 /${DAV_REMOTE_ROOT}/accessories"
    echo "备份脚本已重建：$BACKUP_SCRIPT"
}

prompt_webdav_install() {
    echo "WebDAV 安装说明："
    echo "1) 这里是直连网盘或存储提供的 WebDAV。"
    echo "2) 请填写 WebDAV Connection URL、Connection ID（或用户名）、Password。"
    echo "3) 程序远端默认目录固定为：/ism_images/assets 和 /ism_images/accessories。"
    echo "4) 数据库备份会同步到：/ism_images/sql_backups/。"
    echo

    read -r -p "请输入 WebDAV Connection URL [${DAV_URL:-请从网盘后台复制}]: " input_dav_url
    read -r -p "请输入 Connection ID / 用户名 [${DAV_USER:-按网盘后台显示填写}]: " input_user
    read -r -p "请输入 Password: " input_pass
    read -r -p "请输入本机挂载目录 [${DAV_MOUNT}]: " input_mount

    if [ -n "${input_dav_url:-}" ]; then DAV_URL="$input_dav_url"; fi
    if [ -n "${input_user:-}" ]; then DAV_USER="$input_user"; fi
    if [ -n "${input_pass:-}" ]; then DAV_PASS="$input_pass"; fi
    if [ -n "${input_mount:-}" ]; then DAV_MOUNT="$input_mount"; fi

    recompute_dav_paths
    apply_webdav_settings "安装"
}

prompt_webdav_reset() {
    load_state
    ensure_state_defaults

    echo "WebDAV 重置说明："
    echo "1) 仅重置 WebDAV 连接参数并重新挂载。"
    echo "2) 使用当前本机挂载目录：${DAV_MOUNT}"
    echo "3) 远端业务目录保持为：/${DAV_REMOTE_ROOT}/assets 和 /${DAV_REMOTE_ROOT}/accessories"
    echo

    read -r -p "请输入新的 WebDAV Connection URL [${DAV_URL:-请从网盘后台复制}]: " input_dav_url
    read -r -p "请输入新的 Connection ID / 用户名 [${DAV_USER:-按网盘后台显示填写}]: " input_user
    read -r -p "请输入新的 Password [直接回车则保持当前密码]: " input_pass

    if [ -n "${input_dav_url:-}" ]; then DAV_URL="$input_dav_url"; fi
    if [ -n "${input_user:-}" ]; then DAV_USER="$input_user"; fi
    if [ -n "${input_pass:-}" ]; then DAV_PASS="$input_pass"; fi

    recompute_dav_paths
    apply_webdav_settings "重置"
}

install_webdav() {
    load_state
    ensure_state_defaults

    while true; do
        echo "菜单 4：安装/重置 WebDAV"
        echo "  1 = 安装 WebDAV"
        echo "  2 = 重置 WebDAV 参数并切换网盘"
        echo "  0 = 返回主菜单"
        read -r -p "请选择 [1/2/0]: " webdav_action

        case "${webdav_action:-0}" in
            1)
                prompt_webdav_install
                submenu_pause
                ;;
            2)
                prompt_webdav_reset
                submenu_pause
                ;;
            0|"")
                warn "已返回主菜单"
                return 0
                ;;
            *)
                warn "无效选项，请重新输入"
                submenu_pause
                ;;
        esac
        echo
    done
}

write_asset_clouddrive_wait_script() {
    cat > "$ASSET_CLOUDDRIVE_WAIT_SCRIPT" <<'EOF_WAIT'
#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR='__CLOUDDRIVE_SOURCE__'
ROOT_MOUNT='__CLOUDDRIVE_ROOT__'
MAX_TRIES=60
SLEEP_SECS=1

for _ in $(seq 1 "$MAX_TRIES"); do
    if [ -d "$SOURCE_DIR" ]; then
        if [[ "$SOURCE_DIR" == "$ROOT_MOUNT"* ]]; then
            if mountpoint -q "$ROOT_MOUNT" && ls -ld "$SOURCE_DIR" >/dev/null 2>&1; then
                exit 0
            fi
        else
            if ls -ld "$SOURCE_DIR" >/dev/null 2>&1; then
                exit 0
            fi
        fi
    fi
    sleep "$SLEEP_SECS"
done

echo "[ERR] CloudDrive 源目录在等待 $MAX_TRIES 秒后仍不可用: $SOURCE_DIR" >&2
exit 1
EOF_WAIT
    python3 - "$ASSET_CLOUDDRIVE_WAIT_SCRIPT" "$CLOUDDRIVE_SOURCE" "$CD_MOUNT_DIR" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source_dir = sys.argv[2]
root_dir = sys.argv[3]
text = p.read_text(encoding='utf-8')
text = text.replace('__CLOUDDRIVE_SOURCE__', source_dir)
text = text.replace('__CLOUDDRIVE_ROOT__', root_dir)
p.write_text(text, encoding='utf-8')
PY
    chmod +x "$ASSET_CLOUDDRIVE_WAIT_SCRIPT"
}

write_asset_clouddrive_bind_service() {
    cat > "$ASSET_CLOUDDRIVE_BIND_SERVICE" <<EOF_SYSTEMD
[Unit]
Description=Bind CloudDrive storage into Asset Manager upload root
After=network-online.target ${CD_SERVICE_NAME}.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=${ASSET_CLOUDDRIVE_WAIT_SCRIPT}
ExecStartPre=/bin/mkdir -p ${DAV_UPLOAD_ROOT}
ExecStartPre=/bin/bash -lc 'mountpoint -q "${DAV_UPLOAD_ROOT}" && umount -l "${DAV_UPLOAD_ROOT}" || true'
ExecStart=/bin/mount --bind ${CLOUDDRIVE_SOURCE} ${DAV_UPLOAD_ROOT}
ExecStop=/bin/umount -l ${DAV_UPLOAD_ROOT}
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
}

write_asset_clouddrive_rebind_service() {
    cat > "$ASSET_CLOUDDRIVE_REBIND_SERVICE" <<EOF_SYSTEMD
[Unit]
Description=Delayed rebind CloudDrive storage into Asset Manager upload root
After=multi-user.target ${CD_SERVICE_NAME}.service ${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -lc 'sleep 15; systemctl restart ${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service'
TimeoutStartSec=90

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
}

switch_to_webdav_storage() {
    load_state
    ensure_state_defaults

    if [ -z "$DAV_URL" ] || [ -z "$DAV_USER" ] || [ -z "$DAV_PASS" ]; then
        err "当前没有已保存的 WebDAV 配置，请先执行菜单 4"
        return 1
    fi

    apply_webdav_settings "切换"
}

switch_to_local_storage() {
    load_state
    ensure_state_defaults

    stop_asset_clouddrive_bind_service
    stop_webdav_mount_service

    mkdir -p "$LOCAL_UPLOAD_ROOT/assets" "$LOCAL_UPLOAD_ROOT/accessories" "$LOCAL_UPLOAD_ROOT/sql_backups"
    if [ -f "${APP_ROOT}/config.yaml" ]; then
        cp -f "${APP_ROOT}/config.yaml" "${APP_ROOT}/config.yaml.bak_local_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$LOCAL_UPLOAD_ROOT"
    fi

    reset_asset_systemd_to_plain
    STORAGE_BACKEND="local"
    save_state
    write_backup_script

    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    ok "系统写入路径已恢复为本地目录：${LOCAL_UPLOAD_ROOT}"
    echo "备份脚本已更新：$BACKUP_SCRIPT"
}

set_storage_mount_path() {
    load_state
    ensure_state_defaults

    echo "菜单 6：设置系统写入路径"
    echo "=========================================="
    echo ""
    echo "说明："
    echo "  1. 输入一个挂载点路径（如 /mnt/mount）"
    echo "  2. 系统将自动设置为该路径下的 /ism_images 子目录"
    echo "  3. 例如：输入 /mnt/mount → 实际路径为 /mnt/mount/ism_images"
    echo ""
    echo "提示："
    echo "  - 如果输入的是 WebDAV/CloudDrive/rclone 挂载点，请确保已通过"
    echo "    mount.sh 脚本完成挂载和目录创建"
    echo "  - 输入的路径必须已存在且可写"
    echo "  - 程序会在该路径下自动创建 assets/accessories/sql_backups 目录"
    echo ""
    echo "=========================================="
    echo ""

    read -r -p "请输入挂载点路径: " mount_input

    mount_input=$(echo "$mount_input" | tr -d '[:space:]')

    if [ -z "$mount_input" ]; then
        warn "路径不能为空"
        return 1
    fi

    # 移除末尾的斜杠
    mount_input="${mount_input%/}"

    if [ ! -d "$mount_input" ]; then
        err "路径不存在或无法访问：${mount_input}"
        return 1
    fi

    # 检查路径是否可写
    if ! touch "$mount_input/.write_test" 2>/dev/null; then
        err "路径无写权限：${mount_input}"
        return 1
    fi
    rm -f "$mount_input/.write_test"

    local upload_path="${mount_input}/ism_images"

    echo ""
    echo "配置信息："
    echo "  挂载点：${mount_input}"
    echo "  实际路径：${upload_path}"
    echo ""
    read -r -p "确认配置？(y/n): " confirm

    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        warn "已取消配置"
        return 0
    fi

    # 停止所有挂载相关的服务
    stop_webdav_mount_service
    stop_asset_clouddrive_bind_service

    # 创建目录结构
    mkdir -p "$upload_path/assets" "$upload_path/accessories" "$upload_path/sql_backups"

    # 更新配置文件
    if [ -f "${APP_ROOT}/config.yaml" ]; then
        cp -f "${APP_ROOT}/config.yaml" "${APP_ROOT}/config.yaml.bak_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$upload_path"
    else
        warn "未发现 ${APP_ROOT}/config.yaml，跳过程序配置修改"
    fi

    reset_asset_systemd_to_plain
    STORAGE_BACKEND="custom"
    save_state
    write_backup_script

    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    ok "系统写入路径已设置"
    echo "挂载点：${mount_input}"
    echo "实际路径：${upload_path}"
    echo "备份脚本已更新：$BACKUP_SCRIPT"
}

switch_storage_backend_menu() {
    load_state
    ensure_state_defaults

    while true; do
        echo "菜单 6：设置系统写入路径"
        echo "  1 = 切换到 WebDAV"
        echo "  2 = 切换到 CloudDrive"
        echo "  3 = 切换到 rclone"
        echo "  4 = 切换到本地目录"
        echo "  0 = 返回主菜单"
        read -r -p "请选择 [1/2/3/4/0]: " storage_choice

        case "${storage_choice:-0}" in
            1)
                switch_to_webdav_storage
                submenu_pause
                ;;
            2)
                switch_to_clouddrive_storage
                submenu_pause
                ;;
            3)
                switch_to_rclone_storage
                submenu_pause
                ;;
            4)
                switch_to_local_storage
                submenu_pause
                ;;
            0|"")
                warn "已返回主菜单"
                return 0
                ;;
            *)
                warn "无效选项，请重新输入"
                submenu_pause
                ;;
        esac
        echo
    done
}

switch_to_rclone_storage() {
    load_state
    ensure_state_defaults

    echo "菜单 6 -> rclone：设置系统写入路径"
    echo "rclone 挂载目录：${RCLONE_MOUNT}"
    echo

    stop_webdav_mount_service
    stop_asset_clouddrive_bind_service

    mkdir -p "$RCLONE_MOUNT"

    if ! mountpoint -q "$RCLONE_MOUNT"; then
        err "rclone 挂载点不可用：${RCLONE_MOUNT}，请先通过外部脚本挂载"
        return 1
    fi

    info "检测到 ${RCLONE_MOUNT} 已挂载"

    local RCLONE_UPLOAD_ROOT="${RCLONE_MOUNT}/ism_images"
    mkdir -p "$RCLONE_UPLOAD_ROOT/assets" "$RCLONE_UPLOAD_ROOT/accessories" "$RCLONE_UPLOAD_ROOT/sql_backups"

    if [ -f "${APP_ROOT}/config.yaml" ]; then
        cp -f "${APP_ROOT}/config.yaml" "${APP_ROOT}/config.yaml.bak_rclone_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$RCLONE_UPLOAD_ROOT"
    else
        warn "未发现 ${APP_ROOT}/config.yaml，跳过程序配置修改"
    fi

    reset_asset_systemd_to_plain
    STORAGE_BACKEND="rclone"
    save_state
    write_backup_script

    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    ok "系统写入路径已切换到 rclone：${RCLONE_UPLOAD_ROOT}"
    echo "备份脚本已更新：$BACKUP_SCRIPT"
}

switch_to_clouddrive_storage() {
    load_state
    ensure_state_defaults

    if [ ! -f "$CD_SERVICE_FILE" ] && [ ! -x "$CD_BIN_FILE" ]; then
        err "未检测到 CloudDrive 安装，请先执行菜单 5"
        return 1
    fi

    echo "菜单 6 -> CloudDrive：设置系统写入路径"
    echo "CloudDrive 实际挂载根目录：${CD_MOUNT_DIR}"
    echo
    read -r -p "请输入 CloudDrive 现有本地目录（回车默认 ${CD_MOUNT_DIR}/ism_images）: " input_clouddrive_path
    normalize_clouddrive_source "${input_clouddrive_path:-${CLOUDDRIVE_SOURCE:-${CD_MOUNT_DIR}/ism_images}}"

    if [ "${CLOUDDRIVE_SOURCE}" = "${DAV_UPLOAD_ROOT}" ]; then
        err "CloudDrive 源目录不能等于程序接入目录：${DAV_UPLOAD_ROOT}"
        return 1
    fi

    if [[ "${CLOUDDRIVE_SOURCE}" == "${DAV_MOUNT}"* ]]; then
        err "CloudDrive 源目录不能位于程序接入根目录 ${DAV_MOUNT} 下面"
        return 1
    fi

    stop_webdav_mount_service
    stop_asset_clouddrive_bind_service

    if [ ! -d "${CLOUDDRIVE_SOURCE}" ]; then
        err "CloudDrive 源目录不存在：${CLOUDDRIVE_SOURCE}"
        err "请先确认 CloudDrive 已挂载成功，并且该目录已经存在"
        return 1
    fi

    if [ -f "${APP_ROOT}/config.yaml" ]; then
        cp -f "${APP_ROOT}/config.yaml" "${APP_ROOT}/config.yaml.bak_clouddrive_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$CLOUDDRIVE_SOURCE"
    else
        warn "未发现 ${APP_ROOT}/config.yaml，跳过程序配置修改"
    fi

    reset_asset_systemd_to_plain
    STORAGE_BACKEND="clouddrive"
    save_state
    write_backup_script

    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    if [ -d "${CLOUDDRIVE_SOURCE}" ]; then
        ok "系统写入路径已切换到 CloudDrive：${CLOUDDRIVE_SOURCE}"
    else
        warn "暂未检测到 ${DAV_UPLOAD_ROOT} 为挂载点，请检查：systemctl status ${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME} --no-pager"
    fi

    echo "当前 CloudDrive 源目录：$CLOUDDRIVE_SOURCE"
    echo "当前程序图片目录：$DAV_UPLOAD_ROOT"
    echo "备份脚本已更新：$BACKUP_SCRIPT"
}

switch_storage_backend_menu() {
    load_state
    ensure_state_defaults

    while true; do
        echo "菜单 6：设置系统写入路径"
        echo "  1 = 切换到 WebDAV"
        echo "  2 = 切换到 CloudDrive"
        echo "  3 = 切换到 rclone"
        echo "  4 = 切换到本地目录"
        echo "  0 = 返回主菜单"
        read -r -p "请选择 [1/2/3/4/0]: " storage_choice

        case "${storage_choice:-0}" in
            1)
                switch_to_webdav_storage
                submenu_pause
                ;;
            2)
                switch_to_clouddrive_storage
                submenu_pause
                ;;
            3)
                switch_to_rclone_storage
                submenu_pause
                ;;
            4)
                switch_to_local_storage
                submenu_pause
                ;;
            0|"")
                warn "已返回主菜单"
                return 0
                ;;
            *)
                warn "无效选项，请重新输入"
                submenu_pause
                ;;
        esac
        echo
    done
}

cd_mount_is_active() {
    mountpoint -q "$CD_MOUNT_DIR"
}

install_cd_mount_guard_script() {
    mkdir -p "$CD_MOUNT_RECOVERY_DIR"
    cat > "$CD_MOUNT_GUARD_SCRIPT" <<EOF_GUARD
#!/usr/bin/env bash
set -euo pipefail

MOUNT_DIR='${CD_MOUNT_DIR}'
RECOVERY_BASE='${CD_MOUNT_RECOVERY_DIR}'

mkdir -p "\$MOUNT_DIR" "\$RECOVERY_BASE"

if mountpoint -q "\$MOUNT_DIR"; then
    exit 0
fi

if [ -n "\$(find "\$MOUNT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)" ]; then
    ts="\$(date +%Y%m%d_%H%M%S)"
    recovery_dir="\$RECOVERY_BASE/\$ts"
    mkdir -p "\$recovery_dir"
    shopt -s dotglob nullglob
    mv "\$MOUNT_DIR"/* "\$recovery_dir"/ 2>/dev/null || true
    shopt -u dotglob nullglob
    echo "[INFO] moved stray files from \$MOUNT_DIR to \$recovery_dir" >&2
fi
EOF_GUARD
    chmod +x "$CD_MOUNT_GUARD_SCRIPT"
}

prepare_cd_mount_dir() {
    mkdir -p "$CD_MOUNT_DIR" "$CD_MOUNT_RECOVERY_DIR"

    if cd_mount_is_active; then
        return 0
    fi

    if [ -n "$(find "$CD_MOUNT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)" ]; then
        local ts recovery_dir
        ts="$(date +%Y%m%d_%H%M%S)"
        recovery_dir="$CD_MOUNT_RECOVERY_DIR/$ts"
        mkdir -p "$recovery_dir"
        info "检测到 ${CD_MOUNT_DIR} 里有残留文件，先迁移到 ${recovery_dir}"
        bash -lc 'shopt -s dotglob nullglob; mv "$1"/* "$2"/ 2>/dev/null || true' _ "$CD_MOUNT_DIR" "$recovery_dir"
        ok "已清理 CloudDrive 挂载点残留内容（已迁移备份）"
    fi
}

cd_detect_arch() {
    local raw_arch
    raw_arch="$(uname -m)"
    case "$raw_arch" in
        x86_64|amd64) CD_ARCH="x86_64" ;;
        aarch64|arm64) CD_ARCH="aarch64" ;;
        armv7l|armv7) CD_ARCH="armv7" ;;
        *)
            err "暂不支持当前架构：${raw_arch}"
            exit 1
            ;;
    esac
}

cd_fetch_latest_package_info() {
    cd_detect_arch
    info "获取 CloudDrive 最新 Linux 安装包信息（架构：${CD_ARCH}）"

    local json
    json="$(curl -fsSL "$GITHUB_API_LATEST")"

    CD_PACKAGE_URL="$(printf '%s' "$json" | jq -r --arg arch "$CD_ARCH" '
        .assets[]
        | select(.name | test("^clouddrive-2-linux-" + $arch + "-.*\\.tgz$"))
        | .browser_download_url
    ' | head -n 1)"

    CD_PACKAGE_NAME="$(printf '%s' "$json" | jq -r --arg arch "$CD_ARCH" '
        .assets[]
        | select(.name | test("^clouddrive-2-linux-" + $arch + "-.*\\.tgz$"))
        | .name
    ' | head -n 1)"

    if [ -z "${CD_PACKAGE_URL:-}" ] || [ "${CD_PACKAGE_URL}" = "null" ]; then
        err "未能获取 CloudDrive Linux 安装包地址"
        exit 1
    fi

    CD_DOWNLOAD_FILE="/tmp/${CD_PACKAGE_NAME}"
    ok "安装包：${CD_PACKAGE_NAME}"
}

find_clouddrive_binary() {
    local candidate

    candidate="$(find "$CD_INSTALL_DIR" -type f \( -name 'clouddrive' -o -name 'CloudDrive' -o -name 'cloud-fs' -o -name 'cloudfs' \) 2>/dev/null | head -n 1 || true)"

    if [ -z "${candidate:-}" ]; then
        candidate="$(find "$CD_INSTALL_DIR" -type f -perm /111 \
            ! -name '*.so' \
            ! -name '*.dll' \
            ! -name '*.json' \
            ! -name '*.yaml' \
            ! -name '*.yml' \
            ! -name '*.txt' \
            ! -name '*.md' \
            ! -name '*.html' \
            ! -name '*.css' \
            ! -name '*.js' \
            ! -path '*/resources/*' \
            ! -path '*/webview/*' \
            2>/dev/null | head -n 1 || true)"
    fi

    if [ -z "${candidate:-}" ]; then
        err "未能在安装目录内自动识别 CloudDrive 可执行文件"
        find "$CD_INSTALL_DIR" -maxdepth 3 -mindepth 1 | sed -n '1,50p' || true
        return 1
    fi

    CD_BIN_FILE="$candidate"
    chmod +x "$CD_BIN_FILE"
    ok "已识别可执行文件：${CD_BIN_FILE}"
    return 0
}

write_clouddrive_service() {
    mkdir -p "$(dirname "$CD_SERVICE_FILE")" "$CD_HOME"
    install_cd_mount_guard_script

    cat > "$CD_SERVICE_FILE" <<EOF_SYSTEMD
[Unit]
Description=CloudDrive Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$(dirname "$CD_BIN_FILE")
Environment=CLOUDDRIVE_HOME=${CD_HOME}
ExecStartPre=${CD_MOUNT_GUARD_SCRIPT}
ExecStart=${CD_BIN_FILE}
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
}

print_clouddrive_access_addresses() {
    local ip
    ip="$(get_host_ip || true)"
    echo
    echo "CloudDrive 管理地址："
    echo "  本机: http://127.0.0.1:${CD_WEB_PORT}"
    if [ -n "${ip:-}" ]; then
        echo "  局域网: http://${ip}:${CD_WEB_PORT}"
    fi
    echo "固定挂载目录：${CD_MOUNT_DIR}"
}

install_clouddrive() {
    export DEBIAN_FRONTEND=noninteractive
    info "确保 CloudDrive 运行所需依赖已安装"
    apt-get update
    apt-get install -y curl ca-certificates jq tar fuse3
    check_fuse

    cd_fetch_latest_package_info

    info "下载 CloudDrive 安装包"
    curl -L --fail --retry 3 -o "$CD_DOWNLOAD_FILE" "$CD_PACKAGE_URL"

    info "安装 CloudDrive 到 ${CD_INSTALL_DIR}"
    rm -rf "$CD_INSTALL_DIR"
    mkdir -p "$CD_INSTALL_DIR"
    tar -xzf "$CD_DOWNLOAD_FILE" -C "$CD_INSTALL_DIR"

    find_clouddrive_binary
    ln -sf "$CD_BIN_FILE" "$CD_BIN_LINK"

    write_clouddrive_service
    systemctl daemon-reload
    systemctl enable --now "$CD_SERVICE_NAME"

    if wait_for_port "$CD_WEB_PORT" 30; then
        ok "CloudDrive 已启动"
    else
        warn "CloudDrive 服务已启动，但暂未检测到 ${CD_WEB_PORT} 端口监听，请检查：systemctl status ${CD_SERVICE_NAME} --no-pager"
    fi

    save_state
    print_clouddrive_access_addresses
    echo "注意：本脚本任何时候都不会删除 ${CD_MOUNT_DIR}"
}

show_clouddrive_status() {
    load_state
    ensure_state_defaults

    echo "CloudDrive 服务状态：$(systemctl is-active ${CD_SERVICE_NAME} 2>/dev/null || true)"
    if wait_for_port "$CD_WEB_PORT" 1; then
        echo "Web 管理端口：${CD_WEB_PORT} 已监听"
    else
        echo "Web 管理端口：${CD_WEB_PORT} 未监听"
    fi

    if cd_mount_is_active; then
        echo "CloudDrive 挂载状态：${CD_MOUNT_DIR} 已挂载"
        findmnt "$CD_MOUNT_DIR" || true
    else
        echo "CloudDrive 挂载状态：${CD_MOUNT_DIR} 未挂载"
    fi

    echo "程序路径：${CD_BIN_FILE}"
    echo "配置目录：${CD_HOME}"
}

manage_clouddrive_menu() {
    load_state
    ensure_state_defaults

    while true; do
        echo "菜单 5：安装 CloudDrive"
        echo "  1 = 安装/重装 CloudDrive"
        echo "  2 = 恢复 CloudDrive 挂载"
        echo "  3 = 查看 CloudDrive 状态"
        echo "  0 = 返回主菜单"
        read -r -p "请选择 [1/2/3/0]: " cd_choice

        case "${cd_choice:-0}" in
            1)
                install_clouddrive
                submenu_pause
                ;;
            2)
                recover_clouddrive_mount_and_restart
                submenu_pause
                ;;
            3)
                show_clouddrive_status
                submenu_pause
                ;;
            0|"")
                warn "已返回主菜单"
                return 0
                ;;
            *)
                warn "无效选项，请重新输入"
                submenu_pause
                ;;
        esac
        echo
    done
}

recover_clouddrive_mount_and_restart() {
    if [ ! -f "$CD_SERVICE_FILE" ] && [ ! -x "$CD_BIN_FILE" ]; then
        err "未检测到 CloudDrive 安装，请先执行菜单 5"
        return 1
    fi

    mkdir -p "$CD_MOUNT_DIR" "$CD_MOUNT_RECOVERY_DIR"
    install_cd_mount_guard_script

    info "停止 CloudDrive 服务"
    systemctl stop "$CD_SERVICE_NAME" >/dev/null 2>&1 || true

    if cd_mount_is_active; then
        info "卸载当前挂载点 ${CD_MOUNT_DIR}"
        umount "$CD_MOUNT_DIR" >/dev/null 2>&1 || umount -l "$CD_MOUNT_DIR" >/dev/null 2>&1 || true
        fusermount3 -u "$CD_MOUNT_DIR" >/dev/null 2>&1 || true
    fi

    prepare_cd_mount_dir

    info "重新启动 CloudDrive 服务"
    systemctl start "$CD_SERVICE_NAME"
    wait_for_port "$CD_WEB_PORT" 20 || true

    if cd_mount_is_active; then
        ok "CloudDrive 挂载已恢复：${CD_MOUNT_DIR}"
        findmnt "$CD_MOUNT_DIR" || true
    else
        warn "服务已重启，但暂未检测到 ${CD_MOUNT_DIR} 挂载成功"
    fi
}

uninstall_clouddrive_app() {
    load_state
    ensure_state_defaults

    warn "该操作会卸载 CloudDrive，并清理本脚本创建的服务与程序文件。"
    warn "不会删除 ${CD_MOUNT_DIR} 文件夹。"
    warn "如果当前写入路径正在使用 CloudDrive，会先恢复为本地目录。"
    read -r -p "输入 YES 确认继续： " confirm_text

    if [ "${confirm_text:-}" != "YES" ]; then
        warn "已取消卸载"
        return 0
    fi

    if [ "${STORAGE_BACKEND}" = "clouddrive" ]; then
        switch_to_local_storage
    fi

    if cd_mount_is_active; then
        info "卸载 ${CD_MOUNT_DIR}"
        umount "$CD_MOUNT_DIR" >/dev/null 2>&1 || umount -l "$CD_MOUNT_DIR" >/dev/null 2>&1 || true
        fusermount3 -u "$CD_MOUNT_DIR" >/dev/null 2>&1 || true
    fi

    systemctl stop "$CD_SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl disable "$CD_SERVICE_NAME" >/dev/null 2>&1 || true
    rm -f "$CD_SERVICE_FILE"
    systemctl daemon-reload

    rm -f "$CD_MOUNT_GUARD_SCRIPT"
    rm -rf "$CD_MOUNT_RECOVERY_DIR"
    rm -f "$CD_BIN_LINK"
    rm -rf "$CD_INSTALL_DIR"
    rm -rf "$CD_HOME"
    rm -f /tmp/clouddrive-2-linux-*.tgz >/dev/null 2>&1 || true

    save_state
    ok "CloudDrive 已卸载"
    echo "保留目录：${CD_MOUNT_DIR}"
    echo "说明：本脚本没有删除 ${CD_MOUNT_DIR}"
}

write_backup_script() {
    if [ -f "$BACKUP_SCRIPT" ]; then
        chmod +x "$BACKUP_SCRIPT"
        ok "备份脚本已就绪：$BACKUP_SCRIPT"
    else
        warn "未找到备份脚本：$BACKUP_SCRIPT，请重新安装系统"
    fi
}

install_backup_cron() {
    if [ ! -f "$BACKUP_SCRIPT" ]; then
        warn "未找到备份脚本：$BACKUP_SCRIPT，正在按当前配置自动重建"
        write_backup_script
    fi

    info "生成 cron 自动备份任务"
    cat > "$CRON_BACKUP_FILE" <<EOF_CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""
0 20 * * * root flock -n /var/run/ism_backup.lock ${BACKUP_SCRIPT}
EOF_CRON
    chmod 644 "$CRON_BACKUP_FILE"
    systemctl restart cron
    ok "cron 自动备份已开启：每天 20:00 执行 ${BACKUP_SCRIPT}"
}

show_backup_cron_status() {
    local GREEN='\033[92m' RED='\033[91m' NC='\033[0m'
    local ok="${GREEN}✓${NC}" err="${RED}✗${NC}"

    if [ -f "$CRON_BACKUP_FILE" ]; then
        echo -e "${ok} 备份 cron 任务：$(grep -E '^[0-9*]' "$CRON_BACKUP_FILE" | head -1)"
    else
        echo -e "${err} 未发现 cron 备份任务：$CRON_BACKUP_FILE"
    fi

    if [ -f "$BACKUP_SCRIPT" ]; then
        echo -e "${ok} 备份脚本：$(ls -lh "$BACKUP_SCRIPT" | awk '{print $5, $NF}')"
    else
        echo -e "${err} 未发现备份脚本：$BACKUP_SCRIPT"
    fi

    if journalctl -u cron --since "today" --no-pager 2>/dev/null | grep -F "$BACKUP_SCRIPT" >/dev/null 2>&1; then
        local run_count
        run_count=$(journalctl -u cron --since "today" --no-pager 2>/dev/null | grep -cF "$BACKUP_SCRIPT" || true)
        echo -e "${ok} 今日已执行 ${run_count} 次"
    else
        echo -e "${err} 今日尚未执行"
    fi

    if [ -f "$BACKUP_LOG_FILE" ] && [ -s "$BACKUP_LOG_FILE" ]; then
        echo -e "${ok} 最近备份日志（末5行）："
        tail -n 5 "$BACKUP_LOG_FILE"
    else
        echo -e "${err} 暂无备份日志"
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

    if crontab -l -u root 2>/dev/null | grep -F "$BACKUP_SCRIPT" >/dev/null 2>&1; then
        warn "检测到 root 用户 crontab 中仍存在旧的备份任务，请手动执行：crontab -e -u root"
        crontab -l -u root 2>/dev/null | grep -F "$BACKUP_SCRIPT" || true
    fi
}

setup_backup() {
    load_state
    ensure_state_defaults

    while true; do
        echo "菜单 8：管理 cron 数据库备份任务"
        echo "  1 = 生成/重置 cron 备份任务"
        echo "  2 = 查看 cron 任务和运行情况"
        echo "  3 = 删除 cron 备份任务"
        echo "  0 = 返回主菜单"
        read -r -p "请选择 [1/2/3/0]: " backup_choice

        case "${backup_choice:-0}" in
            1)
                install_backup_cron
                submenu_pause
                ;;
            2)
                show_backup_cron_status
                submenu_pause
                ;;
            3)
                delete_backup_cron
                submenu_pause
                ;;
            0|"")
                warn "已返回主菜单"
                return 0
                ;;
            *)
                warn "无效选项，请重新输入"
                submenu_pause
                ;;
        esac
        echo
    done
}

restore_database() {
    if [ ! -f "$BACKUP_FILE" ]; then
        err "未找到备份文件：$BACKUP_FILE"
        return 1
    fi

    warn "即将使用备份文件恢复数据库：$BACKUP_FILE"
    read -r -p "输入 YES 确认恢复： " confirm_text
    if [ "${confirm_text:-}" != "YES" ]; then
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

    switch_to_local_storage
    STORAGE_BACKEND="local"
    save_state
    write_backup_script
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

uninstall_system() {
    warn "========== 卸载 ISM 系统 =========="
    warn "该操作将："
    warn "  1. 停止并删除 ism systemd 服务"
    warn "  2. 删除 ism 的 Nginx 站点配置（不会删除 nginx 本身）"
    warn "  3. 删除 /root/ism 程序目录和虚拟环境"
    warn "  4. 删除数据库和用户"
    warn "  5. 删除备份 cron 任务"
    warn "  6. 清理状态文件"
    warn ""
    warn "不会影响：nginx / MariaDB / CloudDrive 等现有业务"
    read -r -p "输入 YES 确认卸载: " confirm_text
    if [ "${confirm_text:-}" != "YES" ]; then
        warn "已取消卸载"
        return 0
    fi

    info "停止 ism 服务"
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    rm -f "$SYSTEMD_SERVICE_FILE"
    systemctl daemon-reload

    info "删除 Nginx 站点配置"
    rm -f "$NGINX_SITE_LINK"
    rm -f "$NGINX_SITE_FILE"
    if command -v nginx >/dev/null 2>&1; then
        nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
    fi

    info "删除备份 cron 和脚本"
    rm -f "$CRON_BACKUP_FILE"
    rm -f "$BACKUP_SCRIPT"
    systemctl restart cron 2>/dev/null || true

    if [ -f "$WEBDAV_MOUNT_SERVICE" ]; then
        info "删除 WebDAV 挂载服务"
        systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        systemctl disable "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        rm -f "$WEBDAV_MOUNT_SERVICE"
    fi

    if [ -f "$ASSET_CLOUDDRIVE_BIND_SERVICE" ]; then
        info "删除 CloudDrive bind 服务"
        systemctl stop "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        systemctl disable "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        rm -f "$ASSET_CLOUDDRIVE_BIND_SERVICE"
    fi
    if [ -f "$ASSET_CLOUDDRIVE_REBIND_SERVICE" ]; then
        systemctl stop "${ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        systemctl disable "${ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
        rm -f "$ASSET_CLOUDDRIVE_REBIND_SERVICE"
    fi
    rm -f "$ASSET_CLOUDDRIVE_WAIT_SCRIPT"
    systemctl daemon-reload

    info "删除数据库和用户"
    mysql <<EOF_DB_UNINSTALL
DROP DATABASE IF EXISTS \`${DB_NAME}\`;
DROP USER IF EXISTS '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
EOF_DB_UNINSTALL

    info "删除程序目录 ${APP_ROOT}"
    rm -rf "$APP_ROOT"
    rm -rf "$TMP_DIR"

    rm -f "$STATE_FILE"

    ok "ISM 系统已卸载完成"
    echo "保留项：nginx（如有）、MariaDB、CloudDrive（如有）"
}

install_asset_system() {
    local ADMIN_USER="$1"
    local ADMIN_PASS="$2"
    prepare_dirs
    download_files
    deploy_files
    sync_custom_files
    setup_python_env
    setup_database
    setup_admin_user "$ADMIN_USER" "$ADMIN_PASS"

    if [ -f "${APP_ROOT}/config.yaml" ]; then
        python3 - "${APP_ROOT}/config.yaml" "$ADMIN_USER" "$ADMIN_PASS" <<'PY'
from pathlib import Path
import sys, re
config_file = Path(sys.argv[1])
admin_user = sys.argv[2]
admin_pass = sys.argv[3]
text = config_file.read_text(encoding='utf-8')
text = re.sub(r'^admin_user:.*$', f'admin_user: {admin_user}', text, flags=re.MULTILINE)
text = re.sub(r'^admin_password:.*$', f'admin_password: {admin_pass}', text, flags=re.MULTILINE)
if 'admin_user:' not in text:
    text += f'\nadmin_user: {admin_user}\n'
if 'admin_password:' not in text:
    text += f'admin_password: {admin_pass}\n'
config_file.write_text(text, encoding='utf-8')
PY
    fi

    write_systemd
    configure_nginx
    write_backup_script
    save_state

    ok "系统安装完成"
    echo
    echo "内部 Gunicorn 端口：127.0.0.1:${INTERNAL_PORT}"
    echo "外部 Nginx 端口：${PUBLIC_PORT}"
    echo "当前默认图片目录（未接云前）：${ASSET_IMG_DIR} 和 ${ACCESSORY_IMG_DIR}"
    echo "建议下一步顺序：4/5 -> 6 -> 7 -> 8"
}

confirm_install_asset_system() {
    echo "你选择了【2 安装系统】。"
    echo "该操作会部署程序、初始化数据库、配置 systemd 和 Nginx。"
    echo

    local existing_creds
    local existing_user=""
    local existing_pass=""

    if [ -f "${APP_ROOT}/config.yaml" ]; then
        existing_user=$(grep -E "^admin_user:" "${APP_ROOT}/config.yaml" 2>/dev/null | awk -F': ' '{print $2}' | tr -d "'" | tr -d '"' || true)
        existing_pass=$(grep -E "^admin_password:" "${APP_ROOT}/config.yaml" 2>/dev/null | awk -F': ' '{print $2}' | tr -d "'" | tr -d '"' || true)
    fi

    local ADMIN_USER=""
    local ADMIN_PASS=""

    if [ -n "$existing_user" ]; then
        echo "检测到已有的管理员账号："
        echo "  当前用户名: $existing_user"
        echo "  当前密码: $existing_pass"
        echo
        read -r -p "新用户名 (回车保留现有: $existing_user): " ADMIN_USER
        if [ -z "$ADMIN_USER" ]; then
            ADMIN_USER="$existing_user"
        fi

        read -r -p "新密码 (回车保留现有: $existing_pass): " ADMIN_PASS
        if [ -z "$ADMIN_PASS" ]; then
            ADMIN_PASS="$existing_pass"
        fi
    else
        echo "请设置管理员账号和密码（明文保存到 /root/ism/config.yaml）："
        echo
        while [ -z "$ADMIN_USER" ]; do
            read -r -p "管理员用户名: " ADMIN_USER
            if [ -z "$ADMIN_USER" ]; then
                warn "用户名不能为空，请重新输入"
            fi
        done

        while [ -z "$ADMIN_PASS" ]; do
            read -r -p "管理员密码: " ADMIN_PASS
            if [ -z "$ADMIN_PASS" ]; then
                warn "密码不能为空，请重新输入"
            elif [ ${#ADMIN_PASS} -lt 6 ]; then
                warn "密码长度至少6位，请重新输入"
                ADMIN_PASS=""
            fi
        done
    fi

    echo
    read -r -p "确认安装吗？输入 YES 继续，其他任意键取消: " confirm_install

    case "${confirm_install:-n}" in
        YES)
            install_asset_system "$ADMIN_USER" "$ADMIN_PASS"
            ;;
        *)
            warn "已取消安装，返回主菜单"
            return 0
            ;;
    esac
}

check_connectivity() {
    load_state
    ensure_state_defaults

    local probe_ts
    probe_ts="$(date +%Y%m%d_%H%M%S)"

    echo "当前存储模式：${STORAGE_BACKEND}"
    echo

    case "${STORAGE_BACKEND:-local}" in
        webdav)
            if [ -z "${DAV_URL:-}" ]; then
                err "当前存储模式为 WebDAV，但未配置 DAV_URL，请先执行菜单 4"
                return 1
            fi

            if ! command -v mount.davfs >/dev/null 2>&1; then
                err "未检测到 davfs2 / mount.davfs，请先执行菜单 1 或菜单 4"
                return 1
            fi

            if ! systemd_unit_exists "${WEBDAV_SERVICE_NAME}.service"; then
                err "当前存储模式为 WebDAV，但未找到 ${WEBDAV_SERVICE_NAME}.service"
                err "请先执行菜单 4 重新安装/重置 WebDAV"
                return 1
            fi

            info "检测 WebDAV 连通性"
            mkdir -p "$DAV_MOUNT"
            systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
            umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
            rm -f "/var/run/mount.davfs/$(echo "$DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"

            if ! systemctl start "${WEBDAV_SERVICE_NAME}.service"; then
                err "启动 ${WEBDAV_SERVICE_NAME}.service 失败"
                return 1
            fi

            sleep 2
            if ! mountpoint -q "$DAV_MOUNT"; then
                err "WebDAV 挂载失败：${DAV_MOUNT}"
                return 1
            fi

            mkdir -p "$DAV_UPLOAD_ROOT/assets" "$DAV_UPLOAD_ROOT/accessories" "$DAV_UPLOAD_ROOT/sql_backups"
            touch "$DAV_UPLOAD_ROOT/.webdav_probe_${probe_ts}"
            touch "$DAV_UPLOAD_ROOT/sql_backups/.sql_backup_probe_${probe_ts}"

            ok "WebDAV 连通性检测通过"
            echo "挂载点：$DAV_MOUNT"
            echo "程序图片目录：$DAV_UPLOAD_ROOT"
            echo "SQL 备份目录：$DAV_UPLOAD_ROOT/sql_backups"
            ;;

        clouddrive)
            if [ -z "${CLOUDDRIVE_SOURCE:-}" ]; then
                err "当前存储模式为 CloudDrive，但未配置 CLOUDDRIVE_SOURCE，请先执行菜单 6"
                return 1
            fi

            info "检测 CloudDrive 连通性"
            echo "CloudDrive 根挂载目录：$CD_MOUNT_DIR"
            echo "CloudDrive 源目录：$CLOUDDRIVE_SOURCE"
            echo "程序接入目录：$DAV_UPLOAD_ROOT"
            echo "CloudDrive 服务状态：$(systemctl is-active ${CD_SERVICE_NAME} 2>/dev/null || true)"

            if [ ! -d "$CLOUDDRIVE_SOURCE" ]; then
                warn "CloudDrive 源目录当前不存在：$CLOUDDRIVE_SOURCE"
                warn "尝试恢复 CloudDrive 挂载一次"
                recover_clouddrive_mount_and_restart || true
            fi

            if [ ! -d "$CLOUDDRIVE_SOURCE" ]; then
                err "CloudDrive 源目录仍不存在：$CLOUDDRIVE_SOURCE"
                return 1
            fi

            mkdir -p "$CLOUDDRIVE_SOURCE/assets" "$CLOUDDRIVE_SOURCE/accessories" "$CLOUDDRIVE_SOURCE/sql_backups"
            touch "$CLOUDDRIVE_SOURCE/.clouddrive_source_probe_${probe_ts}"
            touch "$CLOUDDRIVE_SOURCE/sql_backups/.sql_backup_probe_${probe_ts}"

            if systemd_unit_exists "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service"; then
                systemctl restart "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
                sleep 2
            fi

            mkdir -p "$DAV_UPLOAD_ROOT"
            if mountpoint -q "$DAV_UPLOAD_ROOT"; then
                mkdir -p "$DAV_UPLOAD_ROOT/assets" "$DAV_UPLOAD_ROOT/accessories" "$DAV_UPLOAD_ROOT/sql_backups"
                touch "$DAV_UPLOAD_ROOT/.clouddrive_bind_probe_${probe_ts}"
                touch "$DAV_UPLOAD_ROOT/sql_backups/.sql_backup_probe_${probe_ts}"
                ok "CloudDrive 连通性检测通过"
                echo "CloudDrive 源目录：$CLOUDDRIVE_SOURCE"
                echo "程序接入目录：$DAV_UPLOAD_ROOT"
                echo "SQL 备份目录：$DAV_UPLOAD_ROOT/sql_backups"
            else
                warn "CloudDrive 源目录存在且可写，但程序接入目录当前不是挂载点：$DAV_UPLOAD_ROOT"
                warn "请检查：systemctl status ${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service --no-pager"
                return 1
            fi
            ;;

        rclone)
            info "检测 rclone 挂载目录"
            mkdir -p "$RCLONE_MOUNT"

            if ! mountpoint -q "$RCLONE_MOUNT"; then
                err "rclone 挂载点未挂载：${RCLONE_MOUNT}"
                return 1
            fi

            local RCLONE_UPLOAD_ROOT="${RCLONE_MOUNT}/ism_images"
            mkdir -p "$RCLONE_UPLOAD_ROOT/assets" "$RCLONE_UPLOAD_ROOT/accessories" "$RCLONE_UPLOAD_ROOT/sql_backups"
            touch "$RCLONE_UPLOAD_ROOT/.rclone_probe_${probe_ts}"
            touch "$RCLONE_UPLOAD_ROOT/sql_backups/.sql_backup_probe_${probe_ts}"
            ok "rclone 存储检测通过"
            echo "rclone 挂载点：$RCLONE_MOUNT"
            echo "程序图片目录：$RCLONE_UPLOAD_ROOT"
            echo "SQL 备份目录：$RCLONE_UPLOAD_ROOT/sql_backups"
            ;;

        custom)
            info "检测自定义存储路径"
            # 从 config.yaml 读取 upload_folder
            local custom_path=$(grep -E "^upload_folder:" "${APP_ROOT}/config.yaml" 2>/dev/null | awk '{print $2}' | tr -d "'" | tr -d '"' || true)

            if [ -z "$custom_path" ]; then
                err "未能读取自定义存储路径，请检查配置"
                return 1
            fi

            if [ ! -d "$custom_path" ]; then
                err "自定义存储路径不存在：${custom_path}"
                return 1
            fi

            mkdir -p "$custom_path/assets" "$custom_path/accessories" "$custom_path/sql_backups"
            touch "$custom_path/.custom_probe_${probe_ts}"
            touch "$custom_path/sql_backups/.sql_backup_probe_${probe_ts}"
            ok "自定义存储路径检测通过"
            echo "存储路径：$custom_path"
            echo "SQL 备份目录：$custom_path/sql_backups"
            ;;

        local)
            info "检测本地存储目录"
            mkdir -p "$LOCAL_UPLOAD_ROOT/assets" "$LOCAL_UPLOAD_ROOT/accessories" "$LOCAL_UPLOAD_ROOT/sql_backups"
            touch "$LOCAL_UPLOAD_ROOT/.local_probe_${probe_ts}"
            touch "$LOCAL_UPLOAD_ROOT/sql_backups/.sql_backup_probe_${probe_ts}"
            ok "本地存储检测通过"
            echo "本地图片目录：$LOCAL_UPLOAD_ROOT"
            echo "SQL 备份目录：$LOCAL_UPLOAD_ROOT/sql_backups"
            ;;

        *)
            err "未知存储模式：${STORAGE_BACKEND}"
            return 1
            ;;
    esac
}

# ==================== Let's Encrypt 证书处理 ====================
get_public_ipv4() {
    local ip=""
    ip="$(ip route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
    if [ -z "$ip" ] && command -v curl >/dev/null 2>&1; then
        ip="$(curl -4fsS --max-time 5 https://ipv4.icanhazip.com 2>/dev/null | tr -d '[:space:]' || true)"
    fi
    echo "$ip"
}

find_cert_name_by_domain() {
    local cert_domain="$1"
    local d=""
    if [ -f "/etc/letsencrypt/live/${cert_domain}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${cert_domain}/privkey.pem" ]; then
        echo "$cert_domain"
        return 0
    fi
    for d in /etc/letsencrypt/live/"${cert_domain}"*; do
        [ -d "$d" ] || continue
        if [ -f "$d/fullchain.pem" ] && [ -f "$d/privkey.pem" ]; then
            basename "$d"
            return 0
        fi
    done
    return 1
}

get_cert_paths() {
    local cert_domain="$1"
    local cert_name=""
    cert_name="$(find_cert_name_by_domain "$cert_domain" 2>/dev/null || true)"
    [ -n "$cert_name" ] || return 1
    [ -f "/etc/letsencrypt/live/${cert_name}/fullchain.pem" ] || return 1
    [ -f "/etc/letsencrypt/live/${cert_name}/privkey.pem" ] || return 1
    echo "${cert_name}|/etc/letsencrypt/live/${cert_name}/fullchain.pem|/etc/letsencrypt/live/${cert_name}/privkey.pem"
}

cert_files_exist() {
    get_cert_paths "$1" >/dev/null 2>&1
}

cert_is_valid() {
    local cert_domain="$1"
    local cert_info="" cert_file=""
    cert_info="$(get_cert_paths "$cert_domain" 2>/dev/null || true)"
    [ -n "$cert_info" ] || return 1
    cert_file="$(echo "$cert_info" | cut -d'|' -f2)"
    [ -f "$cert_file" ] || return 1
    openssl x509 -checkend 0 -noout -in "$cert_file" >/dev/null 2>&1 || return 1
    if openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep -A1 "Subject Alternative Name" | grep -qw "DNS:${cert_domain}"; then
        return 0
    fi
    openssl x509 -in "$cert_file" -noout -subject 2>/dev/null | grep -Eq "CN[[:space:]]*=[[:space:]]*${cert_domain}([,/]|$)"
}

cert_key_matches() {
    local cert_domain="$1"
    local cert_info="" cert_file="" key_file="" cert_pub="" key_pub=""
    cert_info="$(get_cert_paths "$cert_domain" 2>/dev/null || true)"
    [ -n "$cert_info" ] || return 1
    cert_file="$(echo "$cert_info" | cut -d'|' -f2)"
    key_file="$(echo "$cert_info" | cut -d'|' -f3)"
    [ -f "$cert_file" ] && [ -f "$key_file" ] || return 1
    cert_pub="$(openssl x509 -in "$cert_file" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform pem 2>/dev/null || true)"
    key_pub="$(openssl pkey -in "$key_file" -pubout -outform pem 2>/dev/null || true)"
    [ -n "$cert_pub" ] && [ -n "$key_pub" ] && [ "$cert_pub" = "$key_pub" ]
}

show_local_cert_info() {
    local cert_domain="$1"
    local cert_info="" cert_file=""
    cert_info="$(get_cert_paths "$cert_domain" 2>/dev/null || true)"
    cert_file="$(echo "$cert_info" | cut -d'|' -f2)"
    if [ -f "$cert_file" ]; then
        echo "域名: ${cert_domain}"
        openssl x509 -noout -subject -issuer -dates -in "$cert_file" 2>/dev/null || true
    fi
}

ensure_certbot_installed() {
    if command -v certbot >/dev/null 2>&1; then
        return 0
    fi
    echo "未检测到 certbot，正在安装..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -y
        apt-get install -y certbot
    elif command -v yum >/dev/null 2>&1; then
        yum install -y certbot
    else
        echo "❌ 未检测到 apt-get/yum，无法自动安装 certbot。"
        return 1
    fi
}

prepare_web_cert_for_domain() {
    local cert_domain="$1"
    local server_ip="" resolved_ip=""
    echo "检查域名证书：${cert_domain}"

    if cert_files_exist "$cert_domain" && cert_is_valid "$cert_domain" && cert_key_matches "$cert_domain"; then
        echo "检测到有效 Let's Encrypt 证书，直接复用。"
        show_local_cert_info "$cert_domain"
        return 0
    fi

    echo "未找到可用正式证书，将自动申请 Let's Encrypt 证书。"
    server_ip="$(get_public_ipv4)"
    resolved_ip="$(getent ahostsv4 "$cert_domain" 2>/dev/null | awk 'NR==1{print $1}' || true)"
    if [ -n "$server_ip" ] && [ -n "$resolved_ip" ] && [ "$server_ip" != "$resolved_ip" ]; then
        echo "⚠️ 域名解析 IP 与本机公网 IP 可能不一致："
        echo "   域名解析: $resolved_ip"
        echo "   本机公网: $server_ip"
        echo "   证书申请可能失败，请确认 DNS 已指向本机。"
    fi

    ensure_certbot_installed || return 1

    # 临时停止占用 80 端口的服务（nginx / apache2）
    systemctl stop nginx 2>/dev/null || true
    systemctl stop apache2 2>/dev/null || true
    sleep 2

    # 使用 standalone 方式申请证书
    if ! certbot certonly --standalone --non-interactive --agree-tos --register-unsafely-without-email -d "$cert_domain"; then
        echo "❌ Let's Encrypt 证书申请失败。请检查域名解析、80端口、防火墙/安全组。"
        systemctl start nginx 2>/dev/null || true
        return 1
    fi

    systemctl start nginx 2>/dev/null || true

    if cert_files_exist "$cert_domain" && cert_is_valid "$cert_domain" && cert_key_matches "$cert_domain"; then
        echo "✅ Let's Encrypt 证书已就绪。"
        show_local_cert_info "$cert_domain"
        return 0
    fi

    echo "❌ 证书文件存在性/有效性/私钥匹配校验未通过。"
    return 1
}

# ==================== 更新域名/HTTPS ====================
update_domain() {
    echo -e "${BLUE}=============== 更新 Web 管理端域名 ===============${NC}"
    load_state
    ensure_state_defaults

    local current_domain="${DOMAIN:-}"
    [ -n "$current_domain" ] && echo "当前域名: $current_domain" || echo "当前未配置域名（HTTP）"

    local new_domain=""
    while [ -z "$new_domain" ]; do
        read -rp "请输入新域名（留空则关闭 HTTPS 并回退到 HTTP）: " new_domain
        new_domain="$(echo "$new_domain" | tr -d '[:space:]')"
    done

    if [ -n "$current_domain" ] && [ "$new_domain" = "$current_domain" ]; then
        echo "域名相同，无需更新。"
        return 0
    fi

    # 如果输入了域名，确保证书就绪
    if [ -n "$new_domain" ]; then
        echo "准备 SSL 证书..."
        if ! prepare_web_cert_for_domain "$new_domain"; then
            echo -e "${RED}❌ 证书准备失败，域名更新终止。${NC}"
            return 1
        fi
    fi

    # 检测端口冲突
    local use_proxy_mode=0
    local internal_https_port="2443"

    if [ -n "$new_domain" ]; then
        if grep -r "listen.*443" /etc/nginx/sites-enabled/*.conf 2>/dev/null | grep -q "server_name.*${new_domain}"; then
            echo -e "${YELLOW}⚠️ 检测到 443 端口已被其他应用占用（server_name: ${new_domain}）${NC}"
            echo -e "${YELLOW}将自动启用代理模式：ISM 监听内部端口 ${internal_https_port}${NC}"
            use_proxy_mode=1
        fi
    fi

    # 备份状态文件
    local bak_suffix=".bak.$(date '+%Y%m%d_%H%M%S')"
    cp -a "$STATE_FILE" "${STATE_FILE}${bak_suffix}" 2>/dev/null || true

    # 更新 DOMAIN
    DOMAIN="$new_domain"
    save_state

    # 重新生成 nginx 配置
    if [ "$use_proxy_mode" = "1" ]; then
        # 代理模式：只监听内部端口
        cat > "$NGINX_SITE_FILE" <<EOF
server {
    listen 127.0.0.1:${internal_https_port} ssl;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
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
    }
}
EOF
        echo -e "${GREEN}✅ 代理模式配置已生成${NC}"
        echo -e "${YELLOW}请在代理工具的 ${new_domain}:443 配置中添加以下转发规则：${NC}"
        echo ""
        echo -e "${BOLD}location /ism {${NC}"
        echo -e "    proxy_pass https://127.0.0.1:${internal_https_port};"
        echo -e "    proxy_ssl_verify off;"
        echo -e "}${NC}"
        echo ""
    elif [ -n "$DOMAIN" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
        # 标准模式：监听 PUBLIC_PORT（2083）
        cat > "$NGINX_SITE_FILE" <<EOF
server {
    listen ${PUBLIC_PORT} ssl;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
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
    }
}
EOF
        echo -e "${GREEN}✅ 已配置 HTTPS: https://${DOMAIN}:${PUBLIC_PORT}${NC}"
    else
        # HTTP 模式
        cat > "$NGINX_SITE_FILE" <<EOF
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
    }
}
EOF
        if [ -n "$DOMAIN" ]; then
            echo -e "${YELLOW}⚠️ 未找到证书，已配置 HTTP: http://${DOMAIN}:${PUBLIC_PORT}${NC}"
        else
            local ip
            ip="$(get_host_ip)"
            echo -e "${YELLOW}⚠️ 未配置域名，访问: http://${ip:-服务器IP}:${PUBLIC_PORT}${NC}"
        fi
    fi

    # 确保符号链接存在
    ln -sf "$NGINX_SITE_FILE" "$NGINX_SITE_LINK"

    # 测试配置
    if ! nginx -t; then
        echo -e "${RED}❌ nginx 配置测试失败，已恢复备份。${NC}"
        cp -a "${STATE_FILE}${bak_suffix}" "$STATE_FILE" 2>/dev/null || true
        load_state
        return 1
    fi

    systemctl restart nginx

    # 重启 ISM 服务
    if systemctl is-enabled "$SERVICE_NAME" >/dev/null 2>&1; then
        systemctl restart "$SERVICE_NAME"
    fi

    echo "============================================="
    echo -e "${GREEN}✅ ISM 域名更新完成${NC}"
    if [ "$use_proxy_mode" = "0" ]; then
        [ -n "$DOMAIN" ] && echo "ISM 访问地址: https://${DOMAIN}:${PUBLIC_PORT}/"
    else
        echo "ISM 已接入代理工具，通过代理工具访问"
    fi
    echo "旧状态备份: ${STATE_FILE}${bak_suffix}"
    echo "============================================="
}


show_menu() {
    clear
    printf "\n"
    printf "${BOLD}${BLUE}=========================================================================${NC}\n"
    printf "${BOLD}${WHITE}         ISM 系统管理菜单                                               ${NC}\n"
    printf "${BOLD}${BLUE}=========================================================================${NC}\n"

    printf "${BOLD}${GREEN} [1] 安装依赖${NC}              ${WHITE}安装基础环境：MariaDB / Python / OCR${NC}\n"
    printf "${BOLD}${RED} [2] 安装系统${NC}              ${WHITE}部署程序、初始化数据库、配置服务${NC}\n"
    printf "${BOLD}${BLUE}-------------------------------------------------------------------------${NC}\n"

    printf "${BOLD}${CYAN} [3] 重启系统${NC}              ${WHITE}重启 ism 服务${NC}\n"
    printf "${BOLD}${CYAN} [6] 设置存储路径${NC}     ${WHITE}手动输入挂载路径（如 /mnt/mount）${NC}\n"
    printf "${BOLD}${YELLOW} [7] 连通性检测${NC}           ${WHITE}检测挂载、目录、写入是否正常${NC}\n"
    printf "${BOLD}${YELLOW} [8] 管理cron备份任务${NC}      ${WHITE}生成、查看、删除数据库备份 cron${NC}\n"
    printf "${BOLD}${MAGENTA} [9] 恢复数据库${NC}            ${WHITE}从本地最新备份恢复数据库${NC}\n"
	printf "${BOLD}${GREEN} [10] 更新域名/HTTPS${NC}        ${WHITE}更换域名并自动申请/更新 Let's Encrypt 证书${NC}\n"
    printf "${BOLD}${RED} [11] 卸载系统${NC}              ${YELLOW}卸载整个 ISM 系统（保留 nginx/MariaDB）${NC}\n"
    printf "${BOLD}${RED} [0] 退出${NC}                  ${WHITE}退出当前脚本${NC}\n"

    printf "${BOLD}${BLUE}-------------------------------------------------------------------------${NC}\n"
    printf "${BOLD}${YELLOW} ★ 推荐顺序：${NC}${GREEN}1 -> 2 -> 6 -> 7 -> 8${NC}\n"
    printf "${BOLD}${CYAN} ★ 说明：${NC}${WHITE}存储挂载由独立脚本 mount.sh 管理${NC}\n"
    printf "${BOLD}${BLUE}=========================================================================${NC}\n"
    printf "\n"
}

main() {
    require_root
    load_state
    ensure_state_defaults

    while true; do
        local_need_pause=1
        show_menu
        read -r -p "请输入菜单编号: " choice
        echo
        case "${choice:-}" in
            1) install_dependencies ;;
            2) confirm_install_asset_system ;;
            3) restart_service ;;
            6) set_storage_mount_path ;;
            7) check_connectivity ;;
            8) setup_backup; local_need_pause=0 ;;
            9) restore_database ;;
            10) update_domain ;;
            11) uninstall_system ;;
            0) exit 0 ;;
            *) warn "无效选项" ;;
        esac
        if [ "${local_need_pause}" = "1" ]; then
            echo
            read -r -p "按回车继续..." _
        fi
    done
}

main "$@"
