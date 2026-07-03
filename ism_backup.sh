#!/usr/bin/env bash
set -euo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
CONFIG_FILE="/root/ism/config.yaml"
BACKUP_DIR="/root/ism/backups"
BACKUP_FILE="${BACKUP_DIR}/ism_latest.sql"
BACKUP_LOG_FILE="/var/log/ism_backup.log"
STATE_FILE="/root/.ism_install.conf"

# 从 config.yaml 读取数据库连接和上传目录
eval "$(
python3 -c "
import sys, yaml
with open('$CONFIG_FILE') as f:
    c = yaml.safe_load(f)
m = c.get('mysql', {})
print('DB_NAME=' + m.get('database', 'ism'))
print('DB_USER=' + m.get('user', 'asset_user'))
print('DB_PASS=' + m.get('password', 'by123'))
print('UPLOAD_FOLDER=' + c.get('upload_folder', '/root/ism/app/uploads'))
" 2>/dev/null
)" || { echo "[ERR] 无法读取 $CONFIG_FILE"; exit 1; }

BACKUP_DATE="$(date +%Y.%m.%d)"
REMOTE_BACKUP_ROOT="${UPLOAD_FOLDER}/sql_backups"
REMOTE_BACKUP_FILE="${REMOTE_BACKUP_ROOT}/ism_latest.sql"
REMOTE_BACKUP_DATED="${REMOTE_BACKUP_ROOT}/ism_latest.${BACKUP_DATE}.sql"

# 加载状态文件（获取服务名等辅助信息）
if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    . "$STATE_FILE"
fi

mkdir -p "$BACKUP_DIR" "$(dirname "$BACKUP_LOG_FILE")"
touch "$BACKUP_LOG_FILE"
exec >> "$BACKUP_LOG_FILE" 2>&1

echo "=================================================================="
echo "[INFO] $(date '+%F %T') 开始执行数据库备份，上传目录：${UPLOAD_FOLDER}"

on_error() {
    local exit_code="$1"
    local line_no="$2"
    echo "[ERR] $(date '+%F %T') 备份失败，退出码=${exit_code}，行号=${line_no}"
    exit "$exit_code"
}
trap 'on_error "$?" "$LINENO"' ERR

if ! command -v mysqldump >/dev/null 2>&1; then
    echo "[ERR] 未找到 mysqldump，请先安装 MariaDB/MySQL 客户端"
    exit 1
fi

TMP_BACKUP_FILE="${BACKUP_FILE}.tmp"
rm -f "$TMP_BACKUP_FILE"
mysqldump -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" > "$TMP_BACKUP_FILE"

if [ ! -s "$TMP_BACKUP_FILE" ]; then
    echo "[ERR] mysqldump 已执行，但未生成有效备份文件：$TMP_BACKUP_FILE"
    exit 1
fi

mv -f "$TMP_BACKUP_FILE" "$BACKUP_FILE"
echo "[OK] 本地数据库备份完成：$BACKUP_FILE"

# 根据 UPLOAD_FOLDER 路径判断存储模式并同步远程备份
if [[ "$UPLOAD_FOLDER" == "/mnt/webdav_mount"* ]]; then
    if [ -n "${WEBDAV_SERVICE_NAME:-}" ] && systemctl cat "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1; then
        if ! mountpoint -q "$UPLOAD_FOLDER"; then
            echo "[INFO] WebDAV 未挂载，尝试重启：$WEBDAV_SERVICE_NAME.service"
            systemctl restart "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
            sleep 3
        fi
    fi
    if mountpoint -q "$UPLOAD_FOLDER"; then
        mkdir -p "$REMOTE_BACKUP_ROOT"
        find "$REMOTE_BACKUP_ROOT" -maxdepth 1 -type f -name '*.sql' -delete || true
        cp -f "$BACKUP_FILE" "$REMOTE_BACKUP_FILE"
        echo "[OK] WebDAV 备份已同步到 $REMOTE_BACKUP_FILE"
    else
        echo "[WARN] WebDAV 未挂载，仅保留本地备份"
    fi

elif [[ "$UPLOAD_FOLDER" == "/mnt/CloudDrive"* ]]; then
    if [ -n "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME:-}" ] && systemctl cat "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1; then
        if ! mountpoint -q "$UPLOAD_FOLDER"; then
            echo "[INFO] CloudDrive 未接入，尝试重启：$ASSET_CLOUDDRIVE_BIND_SERVICE_NAME.service"
            systemctl restart "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1 || true
            sleep 3
        fi
    fi
    if mountpoint -q "$UPLOAD_FOLDER"; then
        mkdir -p "$REMOTE_BACKUP_ROOT"
        cp -f "$BACKUP_FILE" "$REMOTE_BACKUP_DATED"
        echo "[OK] CloudDrive 备份已同步到 $REMOTE_BACKUP_DATED"
    else
        echo "[WARN] CloudDrive 未接入，仅保留本地备份"
    fi

else
    echo "[OK] 本地存储模式，仅保留本地备份：$BACKUP_FILE"
fi

echo "[OK] $(date '+%F %T') 数据库备份流程结束"
