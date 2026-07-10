#!/usr/bin/env bash
set -euo pipefail

# ================== 颜色和输出函数 ==================
NC='\033[0m'
BOLD='\033[1m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
CYAN='\033[36m'
BLUE='\033[34m'
MAGENTA='\033[35m'

green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
cyan() { printf '\033[36m%s\033[0m\n' "$*"; }

info() { cyan "[INFO] $*"; }
ok() { green "[OK] $*"; }
warn() { yellow "[WARN] $*"; }
err() { red "[ERR] $*"; }

# ================== 常量定义 ==================
APP_ROOT="/root/ism"
BACKUP_DIR="${APP_ROOT}/backups"
STATE_FILE="/root/.ism_install.conf"
RCLONE_CONFIG_DIR="$HOME/.config/rclone"
RCLONE_CONFIG_FILE="$RCLONE_CONFIG_DIR/rclone.conf"

DAV_MOUNT="/mnt/webdav_mount"
DAV_REMOTE_ROOT="ism_images"
DAV_UPLOAD_ROOT="${DAV_MOUNT}/${DAV_REMOTE_ROOT}"
WEBDAV_MOUNT_SERVICE="/etc/systemd/system/webdav-mount.service"
WEBDAV_SERVICE_NAME="webdav-mount"

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
ASSET_CLOUDDRIVE_BIND_SERVICE_NAME="asset-manager-clouddrive-bind"
ASSET_CLOUDDRIVE_BIND_SERVICE="/etc/systemd/system/${ASSET_CLOUDDRIVE_BIND_SERVICE_NAME}.service"
ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME="asset-manager-clouddrive-rebind"
ASSET_CLOUDDRIVE_REBIND_SERVICE="/etc/systemd/system/${ASSET_CLOUDDRIVE_REBIND_SERVICE_NAME}.service"
ASSET_CLOUDDRIVE_WAIT_SCRIPT="/usr/local/bin/asset-manager-clouddrive-wait.sh"
GITHUB_API_LATEST="https://api.github.com/repos/cloud-fs/cloud-fs.github.io/releases/latest"

# ================== 工具函数 ==================
submenu_pause() {
    echo
    read -r -p "按回车继续当前菜单..." _
}

download_with_retry() {
    local url="$1"
    local out="$2"
    local retry="${3:-3}"
    local i

    for ((i=1; i<=retry; i++)); do
        rm -f "$out" 2>/dev/null || true

        if command -v curl >/dev/null 2>&1; then
            curl -fsSL --connect-timeout 10 --retry 2 "$url" -o "$out" && return 0
        elif command -v wget >/dev/null 2>&1; then
            wget -q --timeout=10 --tries=2 -O "$out" "$url" && return 0
        else
            red "未检测到 curl 或 wget，无法下载文件"
            return 1
        fi

        yellow "下载失败，正在重试 (${i}/${retry})..."
        sleep 1
    done

    return 1
}

get_host_ip() {
    hostname -I 2>/dev/null | awk '{print $1}'
}

check_fuse() {
    if [ ! -e /dev/fuse ]; then
        warn "未检测到 /dev/fuse，尝试加载 fuse 内核模块"
        modprobe fuse >/dev/null 2>&1 || true
    fi

    if [ ! -e /dev/fuse ]; then
        err "当前系统没有可用的 /dev/fuse，无法挂载本地目录"
        return 1
    fi
    return 0
}

arch() {
    case "$(uname -m)" in
        x86_64 | x64 | amd64) echo 'amd64' ;;
        i*86 | x86) echo '386' ;;
        armv8* | armv8 | arm64 | aarch64) echo 'arm64' ;;
        armv7* | armv7 | arm) echo 'armv7' ;;
        armv6* | armv6) echo 'armv6' ;;
        armv5* | armv5) echo 'armv5' ;;
        s390x) echo 's390x' ;;
        *) echo "不支持的 CPU 架构！" && exit 1 ;;
    esac
}

# ================== WebDAV 函数 ==================
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

install_webdav() {
    echo ""
    echo -e "${CYAN}菜单 1：${BOLD}${BLUE}WebDAV 配置${NC}"
    echo -e "  ${BLUE}[1]${NC} ${BOLD}${BLUE}首次配置${NC}            (初始化 WebDAV)"
    echo -e "  ${YELLOW}[2]${NC} ${BOLD}${YELLOW}重新配置${NC}           (更换网盘)"
    echo -e "  ${CYAN}[0]${NC} ${BOLD}${CYAN}返回主菜单${NC}"
    read -r -p "请选择 [0-2]: " webdav_action
    echo

    case "${webdav_action:-0}" in
        1)
            prompt_webdav_install
            ;;
        2)
            prompt_webdav_reset
            ;;
        0|"")
            return 0
            ;;
        *)
            warn "无效选项"
            return 1
            ;;
    esac
}

prompt_webdav_install() {
    export DEBIAN_FRONTEND=noninteractive

    if ! command -v mount.davfs >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        info "安装 WebDAV 依赖 davfs2"
        apt-get update
        apt-get install -y davfs2
    fi

    echo "WebDAV 安装说明："
    echo "1) 这里是直连网盘或存储提供的 WebDAV。"
    echo "2) 请填写 WebDAV Connection URL、Connection ID（或用户名）、Password。"
    echo "3) 程序远端默认目录固定为：/ism_images/assets 和 /ism_images/accessories。"
    echo "4) 数据库备份会同步到：/ism_images/sql_backups/。"
    echo

    read -r -p "请输入 WebDAV Connection URL: " DAV_URL
    read -r -p "请输入 Connection ID / 用户名: " DAV_USER
    read -r -p "请输入 Password: " DAV_PASS
    read -r -p "请输入本机挂载目录 [${DAV_MOUNT}]: " input_mount

    if [ -n "${input_mount:-}" ]; then DAV_MOUNT="$input_mount"; fi

    if [ -z "$DAV_URL" ] || [ -z "$DAV_USER" ] || [ -z "$DAV_PASS" ]; then
        err "WebDAV 参数不能为空"
        return 1
    fi

    stop_webdav_mount_service
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

    ok "WebDAV 已安装并接入"
    echo "当前 WebDAV 挂载点：$DAV_MOUNT"
    echo "当前程序图片目录：$DAV_UPLOAD_ROOT"
}

prompt_webdav_reset() {
    echo "WebDAV 重置说明："
    echo "1) 仅重置 WebDAV 连接参数并重新挂载。"
    echo "2) 使用当前本机挂载目录：${DAV_MOUNT}"
    echo

    read -r -p "请输入新的 WebDAV Connection URL: " DAV_URL
    read -r -p "请输入新的 Connection ID / 用户名: " DAV_USER
    read -r -p "请输入新的 Password: " DAV_PASS

    if [ -z "$DAV_URL" ] || [ -z "$DAV_USER" ] || [ -z "$DAV_PASS" ]; then
        err "WebDAV 参数不能为空"
        return 1
    fi

    stop_webdav_mount_service
    mkdir -p "$DAV_MOUNT"
    write_davfs_mount_config
    write_davfs_secrets_entry
    write_webdav_mount_service
    systemctl daemon-reload

    systemctl stop "${WEBDAV_SERVICE_NAME}.service" >/dev/null 2>&1 || true
    umount "$DAV_MOUNT" >/dev/null 2>&1 || umount -l "$DAV_MOUNT" >/dev/null 2>&1 || true
    rm -f "/var/run/mount.davfs/$(echo "$DAV_MOUNT" | sed 's#/#-#g' | sed 's/^-//').pid"
    systemctl enable --now "${WEBDAV_SERVICE_NAME}.service"

    mkdir -p "$DAV_UPLOAD_ROOT/assets" "$DAV_UPLOAD_ROOT/accessories" "$DAV_UPLOAD_ROOT/sql_backups"
    touch "$DAV_UPLOAD_ROOT/test_write.txt"

    ok "WebDAV 已重置并接入"
    echo "当前 WebDAV 挂载点：$DAV_MOUNT"
    echo "当前程序图片目录：$DAV_UPLOAD_ROOT"
}

uninstall_webdav() {
    warn "该操作会卸载本机 WebDAV 挂载"
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

    systemctl daemon-reload
    ok "WebDAV 已卸载"
}

# ================== CloudDrive 函数 ==================
cd_mount_is_active() {
    mountpoint -q "$CD_MOUNT_DIR"
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

cd_detect_arch() {
    local raw_arch
    raw_arch="$(uname -m)"
    case "$raw_arch" in
        x86_64|amd64) CD_ARCH="x86_64" ;;
        aarch64|arm64) CD_ARCH="aarch64" ;;
        armv7l|armv7) CD_ARCH="armv7" ;;
        *)
            err "暂不支持当前架构：${raw_arch}"
            return 1
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
        return 1
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
        return 1
    fi

    CD_BIN_FILE="$candidate"
    chmod +x "$CD_BIN_FILE"
    ok "已识别可执行文件：${CD_BIN_FILE}"
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
        ok "已清理 CloudDrive 挂载点残留内容"
    fi
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

    sleep 3
    if systemctl is-active --quiet "$CD_SERVICE_NAME"; then
        ok "CloudDrive 已启动"
    else
        warn "CloudDrive 服务已启动，但可能需要配置"
    fi

    print_clouddrive_access_addresses
    echo "注意：本脚本任何时候都不会删除 ${CD_MOUNT_DIR}"
}

show_clouddrive_status() {
    echo "CloudDrive 服务状态：$(systemctl is-active ${CD_SERVICE_NAME} 2>/dev/null || true)"

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
    while true; do
        echo ""
        echo -e "${CYAN}菜单 2：${BOLD}${MAGENTA}CloudDrive 配置${NC}"
        echo -e "  ${MAGENTA}[1]${NC} ${BOLD}${MAGENTA}初始化安装${NC}         (首次启用)"
        echo -e "  ${YELLOW}[2]${NC} ${BOLD}${YELLOW}恢复挂载${NC}           (重新挂载)"
        echo -e "  ${BLUE}[3]${NC} ${BOLD}${BLUE}查看状态${NC}           (检查配置)"
        echo -e "  ${CYAN}[0]${NC} ${BOLD}${CYAN}返回主菜单${NC}"
        read -r -p "请选择 [0-3]: " cd_choice

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
                return 0
                ;;
            *)
                warn "无效选项"
                submenu_pause
                ;;
        esac
        echo
    done
}

recover_clouddrive_mount_and_restart() {
    if [ ! -f "$CD_SERVICE_FILE" ] && [ ! -x "$CD_BIN_FILE" ]; then
        err "未检测到 CloudDrive 安装"
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
    sleep 3

    if cd_mount_is_active; then
        ok "CloudDrive 挂载已恢复：${CD_MOUNT_DIR}"
    else
        warn "服务已重启，但暂未检测到 ${CD_MOUNT_DIR} 挂载成功"
    fi
}

uninstall_clouddrive_app() {
    warn "该操作会卸载 CloudDrive，并清理本脚本创建的服务与程序文件。"
    warn "不会删除 ${CD_MOUNT_DIR} 文件夹。"
    read -r -p "输入 YES 确认继续： " confirm_text

    if [ "${confirm_text:-}" != "YES" ]; then
        warn "已取消卸载"
        return 0
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

    ok "CloudDrive 已卸载"
    echo "保留目录：${CD_MOUNT_DIR}"
}

# ================== Rclone 函数 ==================
install_rclone() {
    echo "[*] 检查 rclone 是否已安装..."

    if command -v rclone &> /dev/null; then
        echo "[✓] Rclone 已安装"
        rclone version | head -1
        return 0
    fi

    echo "[*] 开始安装 Rclone 及依赖..."

    export DEBIAN_FRONTEND=noninteractive

    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y rclone fuse
    elif command -v yum >/dev/null 2>&1; then
        yum install -y rclone fuse
    elif command -v pacman >/dev/null 2>&1; then
        pacman -S --noconfirm rclone fuse2
    else
        err "不支持的系统，请手动安装 rclone"
        return 1
    fi

    if command -v rclone &> /dev/null; then
        ok "Rclone 安装成功"
        rclone version | head -1
    else
        err "Rclone 安装失败"
        return 1
    fi

    if command -v fusermount &> /dev/null; then
        ok "FUSE 工具安装成功"
    else
        warn "FUSE 工具安装可能不完整"
    fi
}

configure_rclone_remote() {
    echo ""
    echo -e "${BLUE}[*] 开始配置 Rclone Pcloud...${NC}"
    echo ""

    mkdir -p "$RCLONE_CONFIG_DIR"

    local config_name="rclone"
    echo "[配置名称已固定为: $config_name]"
    echo ""

    echo "========================================"
    echo -e "     ${BLUE}Pcloud 授权配置${NC}"
    echo "========================================"
    echo ""
    echo -e "${BLUE}[第 1 步]${NC} 在本地有浏览器的电脑上执行:"
    echo ""
    echo "    rclone authorize \"pcloud\""
    echo ""
    echo -e "${BLUE}[第 2 步]${NC} 完成浏览器登录和授权后，会看到:"
    echo ""
    echo "    Paste the following into your remote machine --->"
    echo "    {\"access_token\":\"gfOx7ZDn...\",\"token_type\":\"bearer\",\"expiry\":\"0001-01-01T00:00:00Z\"}"
    echo "    <---End paste"
    echo ""
    echo -e "${BLUE}[第 3 步]${NC} 从上面的 JSON 中只复制 access_token 部分"
    echo ""
    echo "    删除这些内容: {\"access_token\":\""
    echo "    删除这些内容: \",\"token_type\":\"bearer\",\"expiry\":\"0001-01-01T00:00:00Z\"}"
    echo ""
    echo "========================================"
    echo ""

    echo -n "请粘贴 access_token 的值: "
    read -r access_token

    if [ -z "$access_token" ]; then
        echo -e "${RED}[!] token 为空，配置取消${NC}"
        return 1
    fi

    mkdir -p "$RCLONE_CONFIG_DIR"

    if [ -f "$RCLONE_CONFIG_FILE" ]; then
        if grep -q "^\[$config_name\]" "$RCLONE_CONFIG_FILE"; then
            sed -i "/^\[$config_name\]/,/^\[/d" "$RCLONE_CONFIG_FILE"
        fi
    fi

    token="{\"access_token\":\"$access_token\",\"token_type\":\"bearer\",\"expiry\":\"0001-01-01T00:00:00Z\"}"

    cat >> "$RCLONE_CONFIG_FILE" <<EOF
[$config_name]
type = pcloud
token = $token
EOF

    echo ""
    echo -e "${GREEN}[✓] 配置已保存${NC}"
    echo ""
    echo -e "${BLUE}[*] 验证配置中...${NC}"
    sleep 2

    if rclone lsd "${config_name}:" --max-depth 0 &>/dev/null; then
        echo -e "${GREEN}[✓] Pcloud 授权成功 ✓${NC}"
        echo ""
        echo -e "${CYAN}[配置信息]${NC}"
        rclone about "${config_name}:" 2>/dev/null | head -5
        return 0
    else
        echo -e "${RED}[!] 配置验证失败${NC}"
        echo ""
        echo -e "${YELLOW}[可能原因]${NC}"
        echo "  1. token 已过期或无效"
        echo "  2. 网络连接失败"
        echo "  3. 粘贴时多复制了空格"
        echo ""
        return 1
    fi
}

configure_pcloud() {
    local config_name="$1"

    echo "[*] Pcloud 授权配置"
    echo ""
    echo "[第 1 步] 在本地有浏览器的电脑上执行:"
    echo "    rclone authorize \"pcloud\""
    echo ""
    echo "[第 2 步] 完成浏览器登录和授权后，复制 access_token"
    echo ""

    echo -n "请粘贴 access_token 的值: "
    read -r access_token

    if [ -z "$access_token" ]; then
        echo "[!] token 为空，配置取消"
        return 1
    fi

    token="{\"access_token\":\"$access_token\",\"token_type\":\"bearer\",\"expiry\":\"0001-01-01T00:00:00Z\"}"

    cat >> "$RCLONE_CONFIG_FILE" <<EOF
[$config_name]
type = pcloud
token = $token
EOF

    echo "[✓] 配置已保存"
}

mount_rclone() {
    echo ""
    echo -e "${BLUE}[*] 开始挂载 Rclone...${NC}"
    echo ""

    if ! command -v rclone &> /dev/null; then
        err "Rclone 未安装，请先执行菜单 3.1 安装"
        return 1
    fi

    if ! check_fuse; then
        err "FUSE 工具未安装"
        return 1
    fi

    local remotes=$(rclone listremotes)
    if [ -z "$remotes" ]; then
        warn "未找到 Rclone 配置，请先执行菜单 3.2 配置"
        return 1
    fi

    local config_name="rclone"

    if ! rclone listremotes | grep -q "^${config_name}:$"; then
        echo -e "${YELLOW}[!] 未找到默认配置 'rclone'${NC}"
        echo -e "${BLUE}[*] 已配置的 Remote:${NC}"
        rclone listremotes | nl
        echo ""

        read -r -p "请输入要挂载的 Remote 名称: " config_name

        if ! rclone listremotes | grep -q "^${config_name}:$"; then
            err "配置不存在: $config_name"
            return 1
        fi
    else
        echo -e "${GREEN}[✓] 使用默认配置: $config_name${NC}"
    fi

    echo -n "请输入挂载路径 (默认: /mnt/rclone): "
    read -r mount_path
    mount_path=${mount_path:-/mnt/rclone}

    if [ ! -d "$mount_path" ]; then
        mkdir -p "$mount_path"
    fi

    if mountpoint -q "$mount_path" 2>/dev/null; then
        ok "Rclone 已挂载"
        echo "配置名称: $config_name"
        echo "挂载路径: $mount_path"
        df -h "$mount_path"
        return 0
    fi

    echo "[*] 正在挂载 $config_name 到 $mount_path..."

    rclone mount "${config_name}:" "$mount_path" \
        --daemon \
        --allow-other \
        --vfs-cache-mode=full \
        --vfs-cache-max-age=24h \
        2>/dev/null

    sleep 2

    if mountpoint -q "$mount_path" 2>/dev/null; then
        ok "Rclone 挂载成功"
        echo "配置名称: $config_name"
        echo "挂载路径: $mount_path"
        df -h "$mount_path"

        write_rclone_mount_service "$config_name" "$mount_path"
        systemctl daemon-reload
        systemctl enable "rclone-mount-${config_name}.service"
        ok "已启用开机自动挂载"
    else
        err "Rclone 挂载失败"
        return 1
    fi
}

write_rclone_mount_service() {
    local config_name="$1"
    local mount_path="$2"
    local service_file="/etc/systemd/system/rclone-mount-${config_name}.service"

    cat > "$service_file" <<EOF_SYSTEMD
[Unit]
Description=Rclone Mount ${config_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStartPre=/bin/mkdir -p ${mount_path}
ExecStart=/usr/bin/rclone mount ${config_name}: ${mount_path} \\
  --allow-other \\
  --vfs-cache-mode=full \\
  --vfs-cache-max-age=24h \\
  --log-level INFO
ExecStop=/bin/fusermount -u ${mount_path}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD

    chmod 644 "$service_file"
    ok "已创建 systemd 服务: $service_file"
}

unmount_rclone() {
    echo ""
    echo "[*] 开始卸载 Rclone..."
    echo ""

    echo "[*] 当前 Rclone 挂载点:"
    mount | grep rclone | awk '{print $3}' | nl

    echo ""
    echo -n "请输入卸载路径 (默认: /mnt/rclone): "
    read -r mount_path
    mount_path=${mount_path:-/mnt/rclone}

    if ! mountpoint -q "$mount_path" 2>/dev/null; then
        err "$mount_path 未挂载"
        return 1
    fi

    echo "[*] 正在卸载 $mount_path..."

    fusermount -u "$mount_path" 2>/dev/null || umount "$mount_path" 2>/dev/null

    sleep 1

    if mountpoint -q "$mount_path" 2>/dev/null; then
        echo "[*] 尝试强制卸载..."
        umount -l "$mount_path"
    fi

    if ! mountpoint -q "$mount_path" 2>/dev/null; then
        ok "卸载成功"
    else
        err "卸载失败"
        return 1
    fi
}

uninstall_rclone() {
    echo ""
    echo "[!] 警告：将卸载 Rclone"
    echo ""

    if ! command -v rclone &> /dev/null; then
        err "Rclone 未安装"
        return 0
    fi

    echo "[*] 检查挂载的 Rclone..."
    local mounts=$(mount | grep rclone | awk '{print $3}')

    if [ -n "$mounts" ]; then
        echo "[!] 检测到以下挂载点，需要先卸载:"
        echo "$mounts" | nl
        echo ""

        read -r -p "是否卸载这些挂载点？ (y/n): " unmount_confirm

        if [ "$unmount_confirm" = "y" ] || [ "$unmount_confirm" = "Y" ]; then
            while IFS= read -r mount_path; do
                if [ -n "$mount_path" ]; then
                    echo "[*] 卸载 $mount_path..."
                    fusermount -u "$mount_path" 2>/dev/null || umount "$mount_path" 2>/dev/null
                    sleep 1
                    if mountpoint -q "$mount_path" 2>/dev/null; then
                        umount -l "$mount_path"
                    fi
                fi
            done <<< "$mounts"
        else
            warn "未卸载挂载点，取消卸载 Rclone"
            return 0
        fi
        echo ""
    fi

    read -r -p "确认卸载 Rclone？ (y/n): " confirm

    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        warn "已取消卸载"
        return 0
    fi

    echo "[*] 正在卸载 Rclone..."

    export DEBIAN_FRONTEND=noninteractive

    if command -v apt-get >/dev/null 2>&1; then
        apt-get remove -y rclone 2>/dev/null || true
        apt-get autoremove -y 2>/dev/null || true
    elif command -v yum >/dev/null 2>&1; then
        yum remove -y rclone 2>/dev/null || true
    elif command -v pacman >/dev/null 2>&1; then
        pacman -R --noconfirm rclone 2>/dev/null || true
    fi

    if ! command -v rclone &> /dev/null; then
        ok "Rclone 卸载成功"
        echo ""
        echo "[注意]"
        echo "  - 挂载目录已保留"
        echo "  - 配置文件已保留"
        echo "  - 云盘文件不受影响"
    else
        err "Rclone 卸载失败"
    fi
}

# ================== 主菜单 ==================
show_menu() {
    clear
    echo ""
    echo -e "${CYAN}===============================================${NC}"
    echo -e "${CYAN}        挂载存储方式管理菜单${NC}"
    echo -e "${CYAN}===============================================${NC}"
    echo ""
    echo -e "${BOLD}${BLUE}配置存储方式:${NC}"
    echo -e "  ${BLUE}[1]${NC} ${BOLD}${BLUE}WebDAV 配置${NC}              (NAS/网盘)"
    echo -e "  ${MAGENTA}[2]${NC} ${BOLD}${MAGENTA}CloudDrive 配置${NC}         (本地挂载)"
    echo -e "  ${YELLOW}[3]${NC} ${BOLD}${YELLOW}Rclone 配置${NC}              (Pcloud)"
    echo ""
    echo -e "${BOLD}${RED}卸载存储方式:${NC}"
    echo -e "  ${RED}[4]${NC} ${BOLD}${RED}卸载 WebDAV${NC}"
    echo -e "  ${RED}[5]${NC} ${BOLD}${RED}卸载 CloudDrive${NC}"
    echo -e "  ${RED}[6]${NC} ${BOLD}${RED}卸载 Rclone${NC}"
    echo ""
    echo -e "  ${CYAN}[0]${NC} ${BOLD}${CYAN}返回/退出${NC}"
    echo ""
    echo -e "${CYAN}===============================================${NC}"
}

main() {
    require_root() {
        if [ "$(id -u)" -ne 0 ]; then
            err "请使用 root 运行：sudo bash mount.sh"
            exit 1
        fi
    }

    require_root

    while true; do
        show_menu
        read -r -p "请输入菜单编号: " choice
        echo

        case "${choice:-0}" in
            1)
                install_webdav
                submenu_pause
                ;;
            2)
                manage_clouddrive_menu
                ;;
            3)
                while true; do
                    echo ""
                    echo -e "${CYAN}菜单 3：${BOLD}${YELLOW}Rclone 配置${NC}"
                    echo -e "  ${YELLOW}[1]${NC} ${BOLD}${YELLOW}初始化安装${NC}          (安装依赖)"
                    echo -e "  ${BLUE}[2]${NC} ${BOLD}${BLUE}授权配置${NC}           (Pcloud 授权)"
                    echo -e "  ${MAGENTA}[3]${NC} ${BOLD}${MAGENTA}启用挂载${NC}           (开始挂载)"
                    echo -e "  ${CYAN}[0]${NC} ${BOLD}${CYAN}返回主菜单${NC}"
                    read -r -p "请选择 [0-3]: " rclone_choice
                    echo

                    case "${rclone_choice:-0}" in
                        1)
                            install_rclone
                            submenu_pause
                            ;;
                        2)
                            configure_rclone_remote
                            submenu_pause
                            ;;
                        3)
                            mount_rclone
                            submenu_pause
                            ;;
                        0|"")
                            break
                            ;;
                        *)
                            warn "无效选项"
                            ;;
                    esac
                done
                ;;
            4)
                uninstall_webdav
                submenu_pause
                ;;
            5)
                uninstall_clouddrive_app
                submenu_pause
                ;;
            6)
                while true; do
                    echo ""
                    echo -e "${CYAN}菜单 6：卸载 Rclone${NC}"
                    echo -e "  ${YELLOW}1${NC} = ${BOLD}卸载挂载点${NC}          (只卸载不删除配置)"
                    echo -e "  ${RED}2${NC} = ${BOLD}卸载 Rclone${NC}         (完全卸载)"
                    echo -e "  ${CYAN}0${NC} = 返回主菜单"
                    read -r -p "请选择 [0-2]: " unmount_choice
                    echo

                    case "${unmount_choice:-0}" in
                        1)
                            unmount_rclone
                            submenu_pause
                            ;;
                        2)
                            uninstall_rclone
                            submenu_pause
                            ;;
                        0|"")
                            break
                            ;;
                        *)
                            warn "无效选项"
                            ;;
                    esac
                done
                ;;
            0)
                ok "已退出"
                exit 0
                ;;
            *)
                warn "无效选项，请重新输入"
                submenu_pause
                ;;
        esac
    done
}

main "$@"
