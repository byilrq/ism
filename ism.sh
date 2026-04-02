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

ASSET_IMG_DIR="${APP_ROOT}/app/uploads/images/assets"
ACCESSORY_IMG_DIR="${APP_ROOT}/app/uploads/images/accessories"

DOMAIN=""
WEB_DAV_URL="https://app.koofr.net/dav/Koofr"
WEB_DAV_MOUNT="/mnt/koofr_webdav/Koofr"
WEB_DAV_REMOTE_DIR="asset_manager_images"
WEB_DAV_UPLOAD_ROOT="${WEB_DAV_MOUNT}/${WEB_DAV_REMOTE_DIR}"
WEB_DAV_USER=""

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
        err "请使用 root 运行：sudo ./ism.sh"
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
WEB_DAV_USER=${WEB_DAV_USER@Q}
EOF_STATE
}

ensure_state_defaults() {
    WEB_DAV_URL="https://app.koofr.net/dav/Koofr"
    WEB_DAV_MOUNT="/mnt/koofr_webdav/Koofr"
    WEB_DAV_REMOTE_DIR="asset_manager_images"
    WEB_DAV_UPLOAD_ROOT="${WEB_DAV_MOUNT}/${WEB_DAV_REMOTE_DIR}"
    : "${WEB_DAV_USER:=}"
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

install_webdav() {
    load_state
    ensure_state_defaults

    echo "Koofr WebDAV 连接说明："
    echo "1) WebDAV 地址已固定：${WEB_DAV_URL}"
    echo "2) 本机挂载目录已固定：${WEB_DAV_MOUNT}"
    echo "3) 程序图片目录已固定：${WEB_DAV_UPLOAD_ROOT}"
    echo "4) 程序会在该目录下自动使用 assets / accessories 两个子目录"
    echo "5) 账号填写 Koofr 登录邮箱"
    echo "6) 密码填写 Koofr 应用专用密码，不是登录密码"
    echo

    read -r -p "请输入 Koofr 账号（登录邮箱） [${WEB_DAV_USER}]: " input_user
    read -r -p "请输入 Koofr 应用专用密码: " input_pass
    echo

    if [ -n "${input_user:-}" ]; then WEB_DAV_USER="$input_user"; fi
    WEB_DAV_PASS="${input_pass:-}"

    if [ -z "$WEB_DAV_URL" ] || [ -z "$WEB_DAV_MOUNT" ] || [ -z "$WEB_DAV_USER" ] || [ -z "$WEB_DAV_PASS" ]; then
        err "Koofr WebDAV 地址、挂载目录、账号、应用专用密码都不能为空"
        return 1
    fi

    info "安装 WebDAV 依赖"
    apt-get update
    apt-get install -y davfs2 cadaver

    mkdir -p "$WEB_DAV_MOUNT"

    info "写入 /etc/davfs2/davfs2.conf"
    python3 - "$WEB_DAV_MOUNT" <<'PY'
from pathlib import Path
import sys, re
mount_path = sys.argv[1]
p = Path("/etc/davfs2/davfs2.conf")
text = p.read_text(encoding="utf-8") if p.exists() else ""
block = f"[{mount_path}]\nuse_locks 0\nn_cookies 1\nignore_dav_header 1\nbuf_size 64\n"
pattern = re.compile(rf'^\[{re.escape(mount_path)}\]\n(?:.*\n)*?(?=^\[|\Z)', re.MULTILINE)
if pattern.search(text):
    text = pattern.sub(block, text).rstrip() + "\n"
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n" + block
p.write_text(text, encoding="utf-8")
PY

    info "写入 /etc/davfs2/secrets"
    python3 - "$WEB_DAV_MOUNT" "$WEB_DAV_USER" "$WEB_DAV_PASS" <<'PY'
from pathlib import Path
import sys
mount_path, user, passwd = sys.argv[1:4]
p = Path("/etc/davfs2/secrets")
lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
lines = [line for line in lines if not line.startswith(mount_path + " ")]
lines.append(f"{mount_path} {user} {passwd}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    chmod 600 /etc/davfs2/secrets

    info "写入 /etc/fstab"
    python3 - "$WEB_DAV_URL" "$WEB_DAV_MOUNT" <<'PY'
from pathlib import Path
import sys
url, mount_path = sys.argv[1:3]
p = Path("/etc/fstab")
lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
lines = [line for line in lines if mount_path not in line]
lines.append(f"{url} {mount_path} davfs rw,_netdev,uid=root,gid=root,dir_mode=0775,file_mode=0664 0 0")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

    info "重新挂载 WebDAV"
    umount "$WEB_DAV_MOUNT" || umount -l "$WEB_DAV_MOUNT" || true
    rm -f "/var/run/mount.davfs/$(echo "$WEB_DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    mount -t davfs "$WEB_DAV_URL" "$WEB_DAV_MOUNT"

    info "测试 Koofr 目录读写并创建程序目录"
    ls -lah "$WEB_DAV_MOUNT"
    mkdir -p "$WEB_DAV_UPLOAD_ROOT"
    touch "$WEB_DAV_UPLOAD_ROOT/test_write.txt"
    mkdir -p "$WEB_DAV_UPLOAD_ROOT/assets"
    mkdir -p "$WEB_DAV_UPLOAD_ROOT/accessories"

    info "修改程序图片保存目录"
    cp -f "${APP_ROOT}/config.py" "${APP_ROOT}/config.py.bak_webdav_$(date +%Y%m%d_%H%M%S)"
    patch_config_upload_folder "$WEB_DAV_UPLOAD_ROOT"

    save_state

    if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\.service"; then
        systemctl restart "$SERVICE_NAME"
    fi

    ok "Koofr WebDAV 已安装并接入程序"
    echo "当前 WebDAV 挂载点：$WEB_DAV_MOUNT"
    echo "当前 WebDAV 地址：$WEB_DAV_URL"
    echo "当前程序图片目录：$WEB_DAV_UPLOAD_ROOT"
    echo "当前 Koofr 图片目录：${WEB_DAV_REMOTE_DIR}（其下会创建 assets / accessories）"
}

check_webdav_connectivity() {
    load_state
    ensure_state_defaults
    if [ -z "$WEB_DAV_MOUNT" ] || [ -z "$WEB_DAV_URL" ]; then
        err "未找到 Koofr WebDAV 配置，请先执行“安装webdav”"
        return 1
    fi

    info "检测 Koofr WebDAV 连通性"
    mkdir -p "$WEB_DAV_MOUNT"
    umount "$WEB_DAV_MOUNT" || umount -l "$WEB_DAV_MOUNT" || true
    rm -f "/var/run/mount.davfs/$(echo "$WEB_DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    mount -t davfs "$WEB_DAV_URL" "$WEB_DAV_MOUNT"

    ls -lah "$WEB_DAV_MOUNT"
    mkdir -p "$WEB_DAV_UPLOAD_ROOT/assets" "$WEB_DAV_UPLOAD_ROOT/accessories"
    touch "$WEB_DAV_UPLOAD_ROOT/.webdav_probe_$(date +%Y%m%d_%H%M%S)"
    ok "Koofr WebDAV 连通性检测通过"
}

install_packages() {
    export DEBIAN_FRONTEND=noninteractive
    info "安装系统依赖"
    apt-get update
    apt-get install -y \
        nginx mariadb-server cron curl unzip \
        python3 python3-venv python3-pip python3-dev \
        build-essential default-libmysqlclient-dev pkg-config \
        tesseract-ocr tesseract-ocr-chi-sim
    systemctl enable --now mariadb
    systemctl enable --now nginx
    systemctl enable --now cron
    ok "系统依赖安装完成（已包含OCR依赖）"
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
    info "创建 Python 虚拟环境"
    python3 -m venv "$VENV_DIR"
    # shellcheck disable=SC1091
    . "$VENV_DIR/bin/activate"
    pip install --upgrade pip wheel setuptools
    pip install -r "$APP_ROOT/requirements.txt"
    pip install pymysql gunicorn Pillow pytesseract
    ok "Python 环境准备完成（已包含OCR Python依赖）"
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
After=network.target mariadb.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${APP_ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/gunicorn --workers 2 --bind 127.0.0.1:${INTERNAL_PORT} run:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    ok "systemd 服务已启动"
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
    http2 on;
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
    systemctl reload nginx
    ok "Nginx 配置完成"
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

mkdir -p "\$BACKUP_DIR"
rm -f "\$BACKUP_DIR"/*.sql
mysqldump -u"\$DB_USER" -p"\$DB_PASS" "\$DB_NAME" > "\$BACKUP_FILE"
EOF_BACKUP
    chmod +x /usr/local/bin/asset_manager_backup.sh
}

setup_backup() {
    info "配置每天自动备份数据库，只保留一份最新备份"
    mkdir -p "$BACKUP_DIR"
    write_backup_script
    cat > /etc/cron.d/asset_manager_backup <<EOF_CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 2 * * * root /usr/local/bin/asset_manager_backup.sh >> /var/log/asset_manager_backup.log 2>&1
EOF_CRON
    chmod 644 /etc/cron.d/asset_manager_backup
    systemctl restart cron
    ok "自动备份已开启，备份文件：$BACKUP_FILE"
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

install_all() {
    install_packages
    prepare_dirs
    download_files
    deploy_files
    setup_python_env
    setup_database
    write_systemd
    configure_nginx
    setup_backup

    ok "安装完成"
    echo
    echo "内部 Gunicorn 端口：127.0.0.1:${INTERNAL_PORT}"
    echo "外部 Nginx 端口：${PUBLIC_PORT}"
    echo "主设备图片目录：${ASSET_IMG_DIR}"
    echo "配件图片目录：${ACCESSORY_IMG_DIR}"
    if [ -n "${DOMAIN:-}" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
        echo "访问地址：https://${DOMAIN}:${PUBLIC_PORT}"
    elif [ -n "${DOMAIN:-}" ]; then
        echo "访问地址：http://${DOMAIN}:${PUBLIC_PORT}"
    else
        echo "访问地址：http://服务器IP:${PUBLIC_PORT}"
    fi
}

show_menu() {
    clear
    cat <<'EOF_MENU'
================ asset_manager 菜单 ================
1. 安装
2. 重启
3. 添加每天自动备份数据库（仅保留最新一份）
4. 恢复数据库
5. 安装Koofr WebDAV
6. Koofr WebDAV连通性检测
0. 退出
===================================================
EOF_MENU
}

main() {
    require_root
    prepare_dirs
    load_state

    while true; do
        show_menu
        read -r -p "请输入菜单编号: " choice
        echo
        case "${choice:-}" in
            1) install_all ;;
            2) restart_service ;;
            3) setup_backup ;;
            4) restore_database ;;
            5) install_webdav ;;
            6) check_webdav_connectivity ;;
            0) exit 0 ;;
            *) warn "无效选项" ;;
        esac
        echo
        read -r -p "按回车继续..." _
    done
}

main "$@"
