#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/root/asset_manager"
APP_DIR="${APP_ROOT}/app"
VENV_DIR="${APP_ROOT}/venv"
BACKUP_DIR="${APP_ROOT}/backups"
BACKUP_FILE="${BACKUP_DIR}/asset_manager_latest.sql"

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
OPENLIST_URL="http://127.0.0.1:5244"
OPENLIST_DAV_URL="${OPENLIST_URL}/dav/"
OPENLIST_DAV_MOUNT="/mnt/openlist_dav"
OPENLIST_REMOTE_DIR="asset_manager_images"
OPENLIST_UPLOAD_ROOT="${OPENLIST_DAV_MOUNT}/${OPENLIST_REMOTE_DIR}"
OPENLIST_DAV_USER="assetdav"
OPENLIST_DAV_PASS=""
OPENLIST_ADMIN_PASS=""

OPENLIST_MOUNT_SERVICE="/etc/systemd/system/openlist-webdav.service"

NC='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
CYAN='\033[36m'
BLUE='\033[34m'
MAGENTA='\033[35m'
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
        err "请使用 root 运行：sudo ./ism_openlist.sh"
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
OPENLIST_URL=${OPENLIST_URL@Q}
OPENLIST_DAV_URL=${OPENLIST_DAV_URL@Q}
OPENLIST_DAV_MOUNT=${OPENLIST_DAV_MOUNT@Q}
OPENLIST_REMOTE_DIR=${OPENLIST_REMOTE_DIR@Q}
OPENLIST_UPLOAD_ROOT=${OPENLIST_UPLOAD_ROOT@Q}
OPENLIST_DAV_USER=${OPENLIST_DAV_USER@Q}
OPENLIST_DAV_PASS=${OPENLIST_DAV_PASS@Q}
OPENLIST_ADMIN_PASS=${OPENLIST_ADMIN_PASS@Q}
EOF_STATE
}

ensure_state_defaults() {
    : "${OPENLIST_URL:=http://127.0.0.1:5244}"
    : "${OPENLIST_DAV_URL:=${OPENLIST_URL}/dav/}"
    : "${OPENLIST_DAV_MOUNT:=/mnt/openlist_dav}"
    : "${OPENLIST_REMOTE_DIR:=asset_manager_images}"
    : "${OPENLIST_UPLOAD_ROOT:=${OPENLIST_DAV_MOUNT}/${OPENLIST_REMOTE_DIR}}"
    : "${OPENLIST_DAV_USER:=assetdav}"
    : "${OPENLIST_DAV_PASS:=}"
    : "${OPENLIST_ADMIN_PASS:=}"
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

print_section() {
    printf "\n${BOLD}${BLUE}== %s ==${NC}\n" "$1"
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

    python3 - "$SYSTEMD_SERVICE_FILE" "$VENV_DIR" "$INTERNAL_PORT" "$GUNICORN_WORKERS" <<'PY'
from pathlib import Path
import sys, re
svc = Path(sys.argv[1])
venv = sys.argv[2]
port = sys.argv[3]
workers = sys.argv[4]
text = svc.read_text(encoding='utf-8')
text = re.sub(r'^After=.*$', 'After=network-online.target mariadb.service openlist.service openlist-webdav.service', text, flags=re.MULTILINE)
text = re.sub(r'^Wants=.*$', 'Wants=network-online.target', text, flags=re.MULTILINE)
if 'Wants=network-online.target' not in text:
    text = text.replace('After=network-online.target mariadb.service openlist.service openlist-webdav.service\n', 'After=network-online.target mariadb.service openlist.service openlist-webdav.service\nWants=network-online.target\n', 1)
if 'Requires=openlist-webdav.service' not in text:
    text = text.replace('[Service]\n', 'Requires=openlist-webdav.service\n\n[Service]\n', 1)
exec_line = f'ExecStart={venv}/bin/gunicorn --workers {workers} --bind 127.0.0.1:{port} run:app'
text = re.sub(r'^ExecStart=.*$', exec_line, text, flags=re.MULTILINE)
svc.write_text(text, encoding='utf-8')
print('patched', svc)
PY

    systemctl daemon-reload
    ok "asset_manager systemd 已切换为 WebDAV 依赖，并将 gunicorn workers 调整为 ${GUNICORN_WORKERS}"
}

install_openlist() {
    load_state
    ensure_state_defaults

    info "安装 OpenList 官方 APT 仓库"
    apt-get update
    apt-get install -y curl ca-certificates gnupg
    curl -fsSL https://github.com/OpenListTeam/OpenList-APT/releases/latest/download/install-apt.sh | bash

    info "安装 OpenList"
    apt-get update
    apt-get install -y openlist

    systemctl enable --now openlist
    systemctl status openlist --no-pager || true

    read -r -p "请输入要设置的 OpenList 管理员密码（可留空稍后手工设置）: " input_admin_pass
    if [ -n "${input_admin_pass:-}" ]; then
        OPENLIST_ADMIN_PASS="$input_admin_pass"
        if command -v openlist >/dev/null 2>&1; then
            openlist admin set "$OPENLIST_ADMIN_PASS" || true
            systemctl restart openlist || true
        fi
    fi

    save_state

    ok "OpenList 已安装"
    echo "后台地址：${OPENLIST_URL}"
    echo "WebDAV 地址：${OPENLIST_DAV_URL}"
    echo "请登录 OpenList 后台，新增天翼云盘客户端或天翼云盘TV存储，并在根目录下创建 ${OPENLIST_REMOTE_DIR}/assets 和 ${OPENLIST_REMOTE_DIR}/accessories"
}

write_openlist_mount_service() {
    cat > "$OPENLIST_MOUNT_SERVICE" <<EOF_SYSTEMD
[Unit]
Description=Mount OpenList WebDAV
After=network-online.target openlist.service
Wants=network-online.target
Requires=openlist.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p ${OPENLIST_DAV_MOUNT}
ExecStart=/usr/bin/mount -t davfs ${OPENLIST_DAV_URL} ${OPENLIST_DAV_MOUNT}
ExecStop=/bin/umount -l ${OPENLIST_DAV_MOUNT}
TimeoutStartSec=60

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
}

install_openlist_webdav() {
    load_state
    ensure_state_defaults

    echo "OpenList WebDAV 接入说明："
    echo "1) OpenList 服务地址默认：${OPENLIST_URL}"
    echo "2) WebDAV 地址默认：${OPENLIST_DAV_URL}"
    echo "3) 本机挂载目录默认：${OPENLIST_DAV_MOUNT}"
    echo "4) 程序图片目录默认：${OPENLIST_UPLOAD_ROOT}"
    echo "5) 程序会在该目录下使用 assets / accessories 两个子目录"
    echo "6) 这里的账号密码请填写你在 OpenList 后台新建的普通 WebDAV 用户"
    echo

    read -r -p "请输入 OpenList WebDAV 用户名 [${OPENLIST_DAV_USER}]: " input_user
    read -r -p "请输入 OpenList WebDAV 密码: " input_pass
    echo

    if [ -n "${input_user:-}" ]; then OPENLIST_DAV_USER="$input_user"; fi
    if [ -n "${input_pass:-}" ]; then OPENLIST_DAV_PASS="$input_pass"; fi

    if [ -z "$OPENLIST_DAV_USER" ] || [ -z "$OPENLIST_DAV_PASS" ]; then
        err "OpenList WebDAV 用户名和密码不能为空"
        return 1
    fi

    info "安装 WebDAV 依赖"
    apt-get update
    apt-get install -y davfs2

    mkdir -p "$OPENLIST_DAV_MOUNT"

    info "写入 /etc/davfs2/davfs2.conf"
    python3 - "$OPENLIST_DAV_MOUNT" <<'PY'
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

    info "写入 /etc/davfs2/secrets"
    python3 - "$OPENLIST_DAV_MOUNT" "$OPENLIST_DAV_USER" "$OPENLIST_DAV_PASS" <<'PY'
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

    info "写入 openlist-webdav.service"
    write_openlist_mount_service
    systemctl daemon-reload

    info "重新挂载 OpenList WebDAV"
    systemctl stop openlist-webdav.service >/dev/null 2>&1 || true
    umount "$OPENLIST_DAV_MOUNT" >/dev/null 2>&1 || umount -l "$OPENLIST_DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$OPENLIST_DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    systemctl enable --now openlist-webdav.service

    info "测试 OpenList WebDAV 目录读写并创建程序目录"
    ls -lah "$OPENLIST_DAV_MOUNT"
    mkdir -p "$OPENLIST_UPLOAD_ROOT"
    mkdir -p "$OPENLIST_UPLOAD_ROOT/assets"
    mkdir -p "$OPENLIST_UPLOAD_ROOT/accessories"
    touch "$OPENLIST_UPLOAD_ROOT/test_write.txt"

    info "修改程序图片保存目录"
    if [ -f "${APP_ROOT}/config.py" ]; then
        cp -f "${APP_ROOT}/config.py" "${APP_ROOT}/config.py.bak_openlist_$(date +%Y%m%d_%H%M%S)"
        patch_config_upload_folder "$OPENLIST_UPLOAD_ROOT"
    else
        warn "未发现 ${APP_ROOT}/config.py，跳过程序配置修改"
    fi

    patch_systemd_workers_and_dependencies
    save_state

    if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    ok "OpenList WebDAV 已安装并接入程序"
    echo "当前 WebDAV 挂载点：$OPENLIST_DAV_MOUNT"
    echo "当前 WebDAV 地址：$OPENLIST_DAV_URL"
    echo "当前程序图片目录：$OPENLIST_UPLOAD_ROOT"
    echo "当前 OpenList 远端目录：${OPENLIST_REMOTE_DIR}（其下会使用 assets / accessories）"
}

check_openlist_webdav_connectivity() {
    load_state
    ensure_state_defaults

    if [ -z "$OPENLIST_DAV_MOUNT" ] || [ -z "$OPENLIST_DAV_URL" ]; then
        err "未找到 OpenList WebDAV 配置，请先执行“安装 WebDAV”"
        return 1
    fi

    info "检测 OpenList WebDAV 连通性"
    mkdir -p "$OPENLIST_DAV_MOUNT"
    systemctl stop openlist-webdav.service >/dev/null 2>&1 || true
    umount "$OPENLIST_DAV_MOUNT" >/dev/null 2>&1 || umount -l "$OPENLIST_DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$OPENLIST_DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    systemctl start openlist-webdav.service

    ls -lah "$OPENLIST_DAV_MOUNT"
    mkdir -p "$OPENLIST_UPLOAD_ROOT/assets" "$OPENLIST_UPLOAD_ROOT/accessories"
    touch "$OPENLIST_UPLOAD_ROOT/.openlist_probe_$(date +%Y%m%d_%H%M%S)"
    ok "OpenList WebDAV 连通性检测通过"
}

install_packages() {
    export DEBIAN_FRONTEND=noninteractive
    info "安装系统环境依赖（含 OCR 系统包）"
    apt-get update
    apt-get install -y \
        nginx mariadb-server cron curl unzip \
        python3 python3-venv python3-pip python3-dev \
        build-essential default-libmysqlclient-dev pkg-config \
        tesseract-ocr tesseract-ocr-chi-sim
    systemctl enable --now mariadb
    systemctl enable --now nginx
    systemctl enable --now cron
    ok "系统环境依赖安装完成"
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

write_backup_script() {
    cat > /usr/local/bin/asset_manager_backup.sh <<EOF_BACKUP
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR}"
BACKUP_FILE="${BACKUP_FILE}"
DB_NAME="${DB_NAME}"
DB_USER="${DB_USER}"
DB_PASS="${DB_PASS}"
OPENLIST_DAV_MOUNT="${OPENLIST_DAV_MOUNT}"
OPENLIST_UPLOAD_ROOT="${OPENLIST_UPLOAD_ROOT}"
REMOTE_BACKUP_FILE="${OPENLIST_UPLOAD_ROOT}/asset_manager_latest.sql"

mkdir -p "\$BACKUP_DIR"
rm -f "\$BACKUP_DIR"/*.sql
mysqldump -u"\$DB_USER" -p"\$DB_PASS" "\$DB_NAME" > "\$BACKUP_FILE"

if systemctl list-unit-files 2>/dev/null | grep -q '^openlist-webdav\.service'; then
    systemctl start openlist-webdav.service >/dev/null 2>&1 || true
fi

if mountpoint -q "\$OPENLIST_DAV_MOUNT" && [ -d "\$OPENLIST_UPLOAD_ROOT" ]; then
    find "\$OPENLIST_UPLOAD_ROOT" -maxdepth 1 -type f -name '*.sql' -delete || true
    cp -f "\$BACKUP_FILE" "\$REMOTE_BACKUP_FILE"
    echo "[OK] 云盘备份已同步到 \$REMOTE_BACKUP_FILE"
else
    echo "[WARN] OpenList WebDAV 未挂载或远端目录不存在，仅保留本地备份：\$BACKUP_FILE"
fi
EOF_BACKUP
    chmod +x /usr/local/bin/asset_manager_backup.sh
}

setup_backup() {
    info "配置每天自动备份数据库：本地仅保留最新一份；若已接入 OpenList WebDAV，则自动同步到云盘 ${OPENLIST_REMOTE_DIR} 根目录并仅保留最新一份"
    mkdir -p "$BACKUP_DIR"
    write_backup_script
    cat > /etc/cron.d/asset_manager_backup <<EOF_CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 2 * * * root /usr/local/bin/asset_manager_backup.sh >> /var/log/asset_manager_backup.log 2>&1
EOF_CRON
    chmod 644 /etc/cron.d/asset_manager_backup
    systemctl restart cron
    ok "自动备份已开启。本地备份：$BACKUP_FILE；云盘备份文件名：asset_manager_latest.sql"
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

restart_service() {
    info "重启程序"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager || true
}

install_asset_system() {
    prepare_dirs
    download_files
    deploy_files
    setup_python_env
    setup_database
    write_systemd
    configure_nginx

    ok "资产管理系统安装完成"
    echo
    echo "内部 Gunicorn 端口：127.0.0.1:${INTERNAL_PORT}"
    echo "外部 Nginx 端口：${PUBLIC_PORT}"
    echo "当前默认图片目录（未接云前）：${ASSET_IMG_DIR} 和 ${ACCESSORY_IMG_DIR}"
    echo "若要启用 OCR，当前脚本已安装系统包与 Python 包，重启服务后即可生效。"
    echo "建议下一步顺序：4. 安装 OpenList  ->  5. 安装 WebDAV  ->  7. 设置cron备份数据库"
}

show_menu() {
    clear
    printf "${BOLD}${BLUE}================ asset_manager + OpenList 菜单 ================${NC}\n"
    printf "${GREEN} 1.${NC} ${WHITE}安装依赖${NC} ${DIM}(含 OCR 系统包)${NC}\n"
    printf "${GREEN} 2.${NC} ${WHITE}安装系统${NC}\n"
    printf "${GREEN} 3.${NC} ${WHITE}重启系统${NC}\n"
    printf "${CYAN} 4.${NC} ${WHITE}安装 OpenList${NC}\n"
    printf "${CYAN} 5.${NC} ${WHITE}安装 WebDAV${NC}\n"
    printf "${CYAN} 6.${NC} ${WHITE}WebDAV 连通性检测${NC}\n"
    printf "${MAGENTA} 7.${NC} ${WHITE}设置cron备份数据库${NC}\n"
    printf "${MAGENTA} 8.${NC} ${WHITE}恢复数据库${NC}\n"
    printf "${RED} 0.${NC} ${WHITE}退出${NC}\n"
    printf "${BOLD}${BLUE}===============================================================${NC}\n"
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
            2) install_asset_system ;;
            3) restart_service ;;
            4) install_openlist ;;
            5) install_openlist_webdav ;;
            6) check_openlist_webdav_connectivity ;;
            7) setup_backup ;;
            8) restore_database ;;
            0) exit 0 ;;
            *) warn "无效选项" ;;
        esac
        echo
        read -r -p "按回车继续..." _
    done
}

main "$@"
