#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/root/asset_manager"
APP_DIR="/root/asset_manager/app"
VENV_DIR="/root/asset_manager/venv"
BACKUP_DIR="/root/asset_manager/backups"
SYSTEMD_SERVICE="/etc/systemd/system/asset_manager.service"
NGINX_SITE="/etc/nginx/sites-available/asset_manager_2083.conf"
NGINX_SITE_LINK="/etc/nginx/sites-enabled/asset_manager_2083.conf"

APP_ZIP_URL="https://raw.githubusercontent.com/byilrq/ism/main/app.zip"
CONFIG_URL="https://raw.githubusercontent.com/byilrq/ism/main/config.py"
RUN_URL="https://raw.githubusercontent.com/byilrq/ism/main/run.py"
REQ_URL="https://raw.githubusercontent.com/byilrq/ism/main/requirements.txt"
SQL_URL="https://raw.githubusercontent.com/byilrq/ism/main/asset_manager.sql"

DB_NAME="asset_manager"
DB_USER="asset_user"
DB_PASS="by123"
DB_HOST="localhost"

INTERNAL_PORT="5000"
EXTERNAL_PORT="2083"

ASSET_IMG_DIR="/root/asset_manager/app/uploads/images/assets"
ACCESSORY_IMG_DIR="/root/asset_manager/app/uploads/images/accessories"

TMP_DIR="/tmp/asset_manager_install"
PROFILE_FILE="/root/.asset_manager_install.conf"

green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
cyan() { printf '\033[36m%s\033[0m\n' "$*"; }

info() { cyan "[INFO] $*"; }
ok() { green "[OK] $*"; }
warn() { yellow "[WARN] $*"; }
err() { red "[ERR] $*"; }

need_root() {
  if [ "$(id -u)" != "0" ]; then
    err "请使用 root 运行：sudo ./ism.sh"
    exit 1
  fi
}

save_profile() {
  local domain="${1:-}"
  cat > "$PROFILE_FILE" <<EOC
DOMAIN=${domain@Q}
EOC
}

load_profile() {
  DOMAIN=""
  if [ -f "$PROFILE_FILE" ]; then
    . "$PROFILE_FILE"
  fi
}

ensure_dirs() {
  mkdir -p "$APP_ROOT" "$BACKUP_DIR" "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR" "$TMP_DIR"
}

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  info "安装系统依赖"
  apt-get update
  apt-get install -y \
    nginx mariadb-server curl unzip python3 python3-venv python3-pip \
    python3-dev build-essential default-libmysqlclient-dev cron
  systemctl enable --now mariadb
  systemctl enable --now nginx
  systemctl enable --now cron
  ok "系统依赖安装完成"
}

download_file() {
  local url="$1"
  local out="$2"
  info "下载 $(basename "$out")"
  curl -L --fail --retry 3 --connect-timeout 15 -o "$out" "$url"
}

download_sources() {
  rm -rf "$TMP_DIR"
  mkdir -p "$TMP_DIR"
  download_file "$APP_ZIP_URL" "$TMP_DIR/app.zip"
  download_file "$CONFIG_URL" "$TMP_DIR/config.py"
  download_file "$RUN_URL" "$TMP_DIR/run.py"
  download_file "$REQ_URL" "$TMP_DIR/requirements.txt"
  download_file "$SQL_URL" "$TMP_DIR/asset_manager.sql"
  ok "程序文件下载完成"
}

deploy_app_files() {
  info "部署应用文件到 $APP_ROOT"
  mkdir -p "$APP_ROOT"
  mkdir -p "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR"

  rm -rf "$TMP_DIR/unzip"
  mkdir -p "$TMP_DIR/unzip"
  unzip -oq "$TMP_DIR/app.zip" -d "$TMP_DIR/unzip"

  if [ -d "$TMP_DIR/unzip/app" ]; then
    rm -rf "$APP_DIR"
    cp -a "$TMP_DIR/unzip/app" "$APP_DIR"
  else
    mkdir -p "$APP_DIR"
    find "$TMP_DIR/unzip" -mindepth 1 -maxdepth 1 -exec cp -a {} "$APP_DIR/" \;
  fi

  cp -f "$TMP_DIR/config.py" "$APP_ROOT/config.py"
  cp -f "$TMP_DIR/run.py" "$APP_ROOT/run.py"
  cp -f "$TMP_DIR/requirements.txt" "$APP_ROOT/requirements.txt"
  cp -f "$TMP_DIR/asset_manager.sql" "$APP_ROOT/asset_manager.sql"

  mkdir -p "$ASSET_IMG_DIR" "$ACCESSORY_IMG_DIR"
  ok "应用文件已部署"
}

setup_python_env() {
  info "创建虚拟环境并安装依赖"
  python3 -m venv "$VENV_DIR"
  . "$VENV_DIR/bin/activate"
  pip install --upgrade pip wheel setuptools
  pip install -r "$APP_ROOT/requirements.txt"
  pip install pymysql gunicorn
  ok "Python 环境准备完成"
}

setup_database() {
  info "初始化数据库和账号"
  mysql <<EOD
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
EOD

  info "导入数据库备份"
  mysql "$DB_NAME" < "$APP_ROOT/asset_manager.sql"
  ok "数据库已导入"
}

write_systemd() {
  info "写入 systemd 服务"
  cat > "$SYSTEMD_SERVICE" <<EOD
[Unit]
Description=Asset Manager Gunicorn Service
After=network.target mariadb.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$APP_ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV_DIR/bin/gunicorn --workers 2 --bind 127.0.0.1:${INTERNAL_PORT} run:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOD
  systemctl daemon-reload
  systemctl enable asset_manager
  systemctl restart asset_manager
  ok "systemd 服务已启动"
}

write_nginx_http() {
  local domain="$1"
  cat > "$NGINX_SITE" <<EOD
server {
    listen ${EXTERNAL_PORT};
    server_name ${domain:-_};

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
EOD
}

write_nginx_https() {
  local domain="$1"
  local cert_dir="/etc/letsencrypt/live/$domain"
  cat > "$NGINX_SITE" <<EOD
server {
    listen ${EXTERNAL_PORT} ssl;
    http2 on;
    server_name ${domain};

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
EOD
}

configure_nginx() {
  load_profile
  local domain_input=""
  read -r -p "请输入域名（可留空，直接用 IP:2083 访问）: " domain_input
  domain_input="${domain_input:-$DOMAIN}"
  save_profile "$domain_input"

  info "配置 Nginx 反向代理"
  if [ -n "$domain_input" ] && [ -f "/etc/letsencrypt/live/$domain_input/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/$domain_input/privkey.pem" ]; then
    write_nginx_https "$domain_input"
    ok "检测到现有证书，已配置 https://${domain_input}:${EXTERNAL_PORT}"
  else
    write_nginx_http "$domain_input"
    if [ -n "$domain_input" ]; then
      warn "未找到该域名证书，已改为 http://${domain_input}:${EXTERNAL_PORT}"
    else
      warn "未填写域名，已配置为 http://服务器IP:${EXTERNAL_PORT}"
    fi
  fi

  ln -sf "$NGINX_SITE" "$NGINX_SITE_LINK"
  nginx -t
  systemctl reload nginx
  ok "Nginx 配置完成"
}

setup_backup() {
  info "配置每天自动备份数据库，只保留最新一份"
  mkdir -p "$BACKUP_DIR"

  cat > /usr/local/bin/asset_manager_backup.sh <<'EOD'
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="/root/asset_manager/backups"
DB_NAME="asset_manager"
DB_USER="asset_user"
DB_PASS="by123"

mkdir -p "$BACKUP_DIR"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'asset_manager_*.sql' -delete
mysqldump -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" > "$BACKUP_DIR/asset_manager_$(date +%Y%m%d).sql"
cp -f "$BACKUP_DIR/asset_manager_$(date +%Y%m%d).sql" "$BACKUP_DIR/asset_manager_latest.sql"
EOD

  chmod +x /usr/local/bin/asset_manager_backup.sh

  cat > /etc/cron.d/asset_manager_backup <<'EOD'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 2 * * * root /usr/local/bin/asset_manager_backup.sh >> /var/log/asset_manager_backup.log 2>&1
EOD

  systemctl restart cron
  ok "自动备份已开启，目录：$BACKUP_DIR"
}

install_all() {
  install_packages
  ensure_dirs
  download_sources
  deploy_app_files
  setup_python_env
  setup_database
  write_systemd
  configure_nginx
  setup_backup

  ok "安装完成"
  echo
  echo "内部 Gunicorn 监听端口：127.0.0.1:${INTERNAL_PORT}"
  echo "外部 Nginx 访问端口：${EXTERNAL_PORT}"
  echo "主设备图片目录：${ASSET_IMG_DIR}"
  echo "配件图片目录：${ACCESSORY_IMG_DIR}"
  echo
  load_profile
  if [ -n "${DOMAIN:-}" ] && [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    echo "访问地址：https://${DOMAIN}:${EXTERNAL_PORT}"
  elif [ -n "${DOMAIN:-}" ]; then
    echo "访问地址：http://${DOMAIN}:${EXTERNAL_PORT}"
  else
    echo "访问地址：http://服务器IP:${EXTERNAL_PORT}"
  fi
}

restart_service() {
  info "重启程序"
  systemctl daemon-reload
  systemctl enable asset_manager
  systemctl restart asset_manager
  systemctl status asset_manager --no-pager || true
}

menu() {
  clear
  cat <<'EOD'
================ asset_manager 菜单 ================
1. 安装
2. 重启
3. 添加每天自动备份数据库（仅保留最新一份）
0. 退出
===================================================
EOD
}

main() {
  need_root
  ensure_dirs

  while true; do
    menu
    read -r -p "请输入菜单编号: " choice
    case "${choice:-}" in
      1) install_all ;;
      2) restart_service ;;
      3) setup_backup ;;
      0) exit 0 ;;
      *) warn "无效选项" ;;
    esac
    echo
    read -r -p "按回车继续..." _
  done
}

main "$@"
