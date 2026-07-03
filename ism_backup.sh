#!/usr/bin/env bash
set -euo pipefail

PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

BACKUP_DIR="/root/ism/backups"
BACKUP_FILE="/root/ism/backups/ism_latest.sql"
DB_NAME="ism"
DB_USER="asset_user"
DB_PASS="by123"
DAV_MOUNT="/mnt/webdav_mount"
DAV_UPLOAD_ROOT="/mnt/webdav_mount/ism_images"
DAV_BACKUP_ROOT="/mnt/CloudDrive/ism_images/sql_backups"
REMOTE_BACKUP_FILE="${DAV_BACKUP_ROOT}/ism_latest.sql"
BACKUP_DATE="$(date +%Y.%m.%d)"
REMOTE_CLOUDDRIVE_BACKUP_FILE="${DAV_BACKUP_ROOT}/ism_latest.${BACKUP_DATE}.sql"
BACKUP_LOG_FILE="/var/log/ism_backup.log"
STORAGE_BACKEND="clouddrive"
WEBDAV_SERVICE_NAME="webdav-mount"
ASSET_CLOUDDRIVE_BIND_SERVICE_NAME="asset-manager-clouddrive-bind"

mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$BACKUP_LOG_FILE")"
touch "$BACKUP_LOG_FILE"
exec >> "$BACKUP_LOG_FILE" 2>&1

echo "=================================================================="
echo "[INFO] $(date '+%F %T') 开始执行数据库备份，存储模式：$STORAGE_BACKEND"

on_error() {
    local exit_code="$1"
    local line_no="$2"
    echo "[ERR] $(date '+%F %T') 备份失败，退出码=${exit_code}，行号=${line_no}"
    exit "$exit_code"
}
trap 'on_error "0" "1287"' ERR

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

if [ "$STORAGE_BACKEND" = "webdav" ]; then
    if [ -f "/etc/systemd/system/${WEBDAV_SERVICE_NAME}.service" ] || [ -f "/lib/systemd/system/${WEBDAV_SERVICE_NAME}.service" ] || systemctl cat "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1; then
        if ! mountpoint -q "$DAV_MOUNT"; then
            echo "[INFO] WebDAV 当前未挂载，尝试重启服务：$WEBDAV_SERVICE_NAME.service"
            systemctl restart "$WEBDAV_SERVICE_NAME.service" >/dev/null 2>&1 || true
            sleep 3
        fi
    fi

    if mountpoint -q "$DAV_MOUNT"; then
        mkdir -p "$DAV_BACKUP_ROOT"
        find "$DAV_BACKUP_ROOT" -maxdepth 1 -type f -name '*.sql' -delete || true
        cp -f "$BACKUP_FILE" "$REMOTE_BACKUP_FILE"
        echo "[OK] WebDAV 备份已同步到 $REMOTE_BACKUP_FILE"
    else
        echo "[WARN] WebDAV 未挂载，仅保留本地备份：$BACKUP_FILE"
    fi
elif [ "$STORAGE_BACKEND" = "clouddrive" ]; then
    if [ -f "/etc/systemd/system/${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" ] || [ -f "/lib/systemd/system/${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" ] || systemctl cat "${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service" >/dev/null 2>&1; then
        if ! mountpoint -q "$DAV_UPLOAD_ROOT"; then
            echo "[INFO] CloudDrive 当前未接入，尝试重启服务：$ASSET_CLOUDDRIVE_BIND_SERVICE_NAME.service"
            systemctl restart "$ASSET_CLOUDDRIVE_BIND_SERVICE_NAME.service" >/dev/null 2>&1 || true
            sleep 3
        fi
    fi

    if mountpoint -q "$DAV_UPLOAD_ROOT"; then
        mkdir -p "$DAV_BACKUP_ROOT"
        cp -f "$BACKUP_FILE" "$REMOTE_CLOUDDRIVE_BACKUP_FILE"
        echo "[OK] CloudDrive 备份已同步到 $REMOTE_CLOUDDRIVE_BACKUP_FILE"
    else
        echo "[WARN] CloudDrive 未接入，仅保留本地备份：$BACKUP_FILE"
    fi
else
    echo "[OK] 当前为本地存储模式，仅保留本地备份：$BACKUP_FILE"
fi

echo "[OK] $(date '+%F %T') 数据库备份流程结束"
