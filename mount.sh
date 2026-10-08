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
RCLONE_HEALTH_STATE_DIR="/var/lib/rclone-mount-health"

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
        if ! command -v fusermount3 >/dev/null 2>&1 && ! command -v fusermount >/dev/null 2>&1; then
            if command -v apt-get >/dev/null 2>&1; then
                info "补充安装 FUSE3 工具"
                apt-get update
                apt-get install -y fuse3
            fi
        fi
        return 0
    fi

    echo "[*] 开始安装 Rclone 及依赖..."

    export DEBIAN_FRONTEND=noninteractive

    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y rclone fuse3
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

    if command -v fusermount3 &> /dev/null || command -v fusermount &> /dev/null; then
        ok "FUSE 工具安装成功"
    else
        warn "FUSE 工具安装可能不完整"
    fi
}

configure_rclone_remote() {
    echo ""
    echo -e "${BLUE}[*] 开始配置 Rclone Pcloud（美区）...${NC}"
    echo ""

    if ! command -v rclone >/dev/null 2>&1; then
        err "Rclone 未安装，请先执行菜单 3.1 初始化安装"
        return 1
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        err "未检测到 python3，无法校验授权结果"
        return 1
    fi

    mkdir -p "$RCLONE_CONFIG_DIR"
    chmod 700 "$RCLONE_CONFIG_DIR" 2>/dev/null || true

    local config_name="rclone"
    local client_id client_secret oauth_json auth_code auth_response auth_url encoded_client_id
    local tmp_section tmp_config tmp_error
    local use_custom_api=0

    echo "========================================"
    echo -e "     ${BLUE}Pcloud 美区授权配置${NC}"
    echo "========================================"
    echo ""
    echo "说明："
    echo "  1. 仅配置 Pcloud 美区（api.pcloud.com）"
    echo "  2. 如果填写自己的 Client ID + Client Secret，脚本直接使用该 API 应用"
    echo "  3. 自有 API 模式不需要粘贴 OAuth JSON；浏览器授权后只需输入 Pcloud 显示的授权码"
    echo "  4. Client ID 留空时，才使用 Rclone 默认 OAuth，并要求粘贴完整 OAuth JSON"
    echo "  5. 新配置会先临时验证；验证失败不会覆盖当前可用配置"
    echo ""

    read -r -p "请输入 Pcloud Client ID（留空使用 Rclone 默认 OAuth）: " client_id

    if [ -n "${client_id:-}" ]; then
        use_custom_api=1
        # 用户要求可见输入，便于核对。配置写入后仍使用 chmod 600 保护。
        read -r -p "请输入 Pcloud Client Secret（输入可见）: " client_secret
        if [ -z "${client_secret:-}" ]; then
            err "已输入 Client ID 时，Client Secret 不能为空"
            return 1
        fi

        encoded_client_id="$(python3 - "$client_id" <<'PY_URL'
import sys
from urllib.parse import quote
print(quote(sys.argv[1], safe=''))
PY_URL
)"
        auth_url="https://my.pcloud.com/oauth2/authorize?client_id=${encoded_client_id}&response_type=code&force_reapprove=1"

        echo ""
        echo -e "${BLUE}[第 1 步]${NC} 在任意浏览器打开下面地址并登录你的 Pcloud 美区账号："
        echo ""
        echo "    ${auth_url}"
        echo ""
        echo "授权后 Pcloud 会直接显示一次性授权码（code）。"
        echo "不需要执行 rclone authorize，也不需要复制 OAuth JSON。"
        echo ""
        read -r -p "请输入 Pcloud 页面显示的授权码: " auth_code

        if [ -z "${auth_code:-}" ]; then
            err "授权码为空，配置取消"
            return 1
        fi

        info "使用 Client ID + Client Secret 向 Pcloud 美区交换访问 token"
        # 通过 stdin 把敏感参数交给 Python，避免 Client Secret 出现在 ps 进程参数中。
        auth_response="$(printf '%s\n%s\n%s\n' "$client_id" "$client_secret" "$auth_code" | python3 -c '
import sys
import urllib.parse
import urllib.request

client_id = sys.stdin.readline().rstrip("\n")
client_secret = sys.stdin.readline().rstrip("\n")
code = sys.stdin.readline().rstrip("\n")
data = urllib.parse.urlencode({
    "client_id": client_id,
    "client_secret": client_secret,
    "code": code,
}).encode("utf-8")
req = urllib.request.Request(
    "https://api.pcloud.com/oauth2_token",
    data=data,
    method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
try:
    with urllib.request.urlopen(req, timeout=25) as resp:
        sys.stdout.write(resp.read().decode("utf-8", "replace"))
except Exception as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
' 2>/tmp/pcloud-http-error.$$)" || {
            err "连接 Pcloud OAuth 接口失败"
            if [ -s /tmp/pcloud-http-error.$$ ]; then
                cat /tmp/pcloud-http-error.$$
            fi
            rm -f /tmp/pcloud-http-error.$$
            return 1
        }
        rm -f /tmp/pcloud-http-error.$$

        oauth_json="$(printf '%s' "$auth_response" | python3 -c '
import json, sys
try:
    obj = json.load(sys.stdin)
except Exception:
    raise SystemExit(2)
if obj.get("result", 0) not in (0, None):
    print(obj.get("error") or ("Pcloud error %s" % obj.get("result")), file=sys.stderr)
    raise SystemExit(3)
token = obj.get("access_token")
if not isinstance(token, str) or not token.strip():
    print(obj.get("error") or "Pcloud did not return access_token", file=sys.stderr)
    raise SystemExit(4)
print(json.dumps({
    "access_token": token.strip(),
    "token_type": obj.get("token_type") or "bearer",
    "expiry": "0001-01-01T00:00:00Z",
}, separators=(",", ":")))
' 2>/tmp/pcloud-oauth-error.$$)" || {
            err "Pcloud 授权码交换失败"
            if [ -s /tmp/pcloud-oauth-error.$$ ]; then
                cat /tmp/pcloud-oauth-error.$$
            else
                printf '%s\n' "$auth_response"
            fi
            rm -f /tmp/pcloud-oauth-error.$$
            return 1
        }
        rm -f /tmp/pcloud-oauth-error.$$
        ok "Pcloud 授权码交换成功"
    else
        echo ""
        echo -e "${BLUE}[默认 OAuth 模式]${NC} Client ID 为空，使用 Rclone 自带 Pcloud OAuth 应用。"
        echo "请在有浏览器的电脑执行："
        echo ""
        echo '    rclone authorize "pcloud"'
        echo ""
        echo "浏览器授权完成后，请复制 rclone 输出的完整 JSON。"
        echo ""
        read -r -p "请粘贴完整 OAuth JSON: " oauth_json

        if [ -z "${oauth_json:-}" ]; then
            err "OAuth JSON 为空，配置取消"
            return 1
        fi

        if ! printf '%s' "$oauth_json" | python3 -c '
import json, sys
try:
    obj = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if not isinstance(obj, dict) or not isinstance(obj.get("access_token"), str) or not obj["access_token"].strip():
    raise SystemExit(1)
' >/dev/null 2>&1; then
            err "OAuth JSON 格式无效，必须粘贴 rclone authorize 返回的完整 JSON"
            return 1
        fi
    fi

    tmp_section="$(mktemp /tmp/rclone-pcloud-section.XXXXXX)"
    tmp_config="$(mktemp "${RCLONE_CONFIG_FILE}.new.XXXXXX")"
    tmp_error="$(mktemp /tmp/rclone-pcloud-verify.XXXXXX)"
    chmod 600 "$tmp_section" "$tmp_config" "$tmp_error"

    {
        printf '[%s]\n' "$config_name"
        printf 'type = pcloud\n'
        if [ "$use_custom_api" -eq 1 ]; then
            printf 'client_id = %s\n' "$client_id"
            printf 'client_secret = %s\n' "$client_secret"
        fi
        printf 'hostname = api.pcloud.com\n'
        printf 'token = %s\n' "$oauth_json"
    } > "$tmp_section"

    # 保留其他 rclone remote，仅替换 [rclone] 这一节。
    if ! python3 - "$RCLONE_CONFIG_FILE" "$tmp_section" "$tmp_config" "$config_name" <<'PY_MERGE'
from pathlib import Path
import re
import sys

current_path = Path(sys.argv[1])
section_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])
target = sys.argv[4]

old = current_path.read_text(encoding='utf-8') if current_path.exists() else ''
section = section_path.read_text(encoding='utf-8').strip()
lines = old.splitlines(keepends=True)
out = []
skip = False
header_re = re.compile(r'^\s*\[([^\]]+)\]\s*$')

for line in lines:
    m = header_re.match(line.rstrip('\r\n'))
    if m:
        if m.group(1) == target:
            skip = True
            continue
        if skip:
            skip = False
    if not skip:
        out.append(line)

text = ''.join(out).rstrip()
if text:
    text += '\n\n'
text += section + '\n'
out_path.write_text(text, encoding='utf-8')
PY_MERGE
    then
        rm -f "$tmp_section" "$tmp_config" "$tmp_error"
        err "生成临时 Rclone 配置失败，原配置未修改"
        return 1
    fi

    echo ""
    info "先用临时配置验证 Pcloud 美区授权，不覆盖现有配置"

    if timeout -k 3s 25s rclone lsd "${config_name}:" \
        --config "$tmp_config" \
        --max-depth 1 \
        >/dev/null 2>"$tmp_error"; then

        if ! timeout -k 3s 25s rclone about "${config_name}:" \
            --config "$tmp_config" \
            >/dev/null 2>>"$tmp_error"; then
            warn "目录访问已通过，但容量信息读取失败；目录访问验证结果仍视为有效"
        fi

        mv -f "$tmp_config" "$RCLONE_CONFIG_FILE"
        chmod 600 "$RCLONE_CONFIG_FILE"
        mkdir -p "$RCLONE_HEALTH_STATE_DIR"
        rm -f "$RCLONE_HEALTH_STATE_DIR/${config_name}.auth_error"
        tmp_config=""
        rm -f "$tmp_section" "$tmp_error"
        tmp_section=""
        tmp_error=""

        echo ""
        ok "Pcloud 美区授权验证成功，正式配置已更新"
        echo "配置名称：${config_name}"
        echo "API 区域：美国（api.pcloud.com）"
        if [ "$use_custom_api" -eq 1 ]; then
            echo "授权方式：自有 Client ID + Client Secret + Pcloud 授权码"
        else
            echo "授权方式：Rclone 默认 OAuth"
        fi
        echo "配置文件：${RCLONE_CONFIG_FILE}"
        echo ""
        echo -e "${CYAN}[安全说明]${NC} OAuth token 已写入配置文件；rclone.conf 权限已设为 600。"

        if systemctl list-unit-files "rclone-mount-${config_name}.service" >/dev/null 2>&1; then
            echo ""
            warn "检测到已有 Rclone 挂载服务。请返回菜单执行："
            echo "  3 = 启用/修复挂载"
            echo "让当前 FUSE 挂载立即使用新的授权配置。"
        fi
        return 0
    fi

    echo ""
    err "Pcloud 授权验证失败，原 rclone.conf 未修改"

    if grep -qiE 'revoked|2095' "$tmp_error"; then
        echo "原因：Pcloud 返回 token 已撤销，需要重新执行授权。"
    elif grep -qiE 'invalid.*client|client.*invalid|client_secret|unauthorized|401' "$tmp_error"; then
        echo "原因：Client ID / Client Secret 或授权 token 无效。"
    elif grep -qiE 'timeout|timed out|connection|network|no route|temporary failure|TLS|certificate' "$tmp_error"; then
        echo "原因：网络或 Pcloud API 连接异常/超时。"
    else
        echo "原因：远端验证未通过。"
    fi

    echo ""
    echo "本次失败不会覆盖当前配置。"
    rm -f "$tmp_section" "$tmp_config" "$tmp_error"
    return 1
}

configure_pcloud() {
    # 兼容旧调用：统一使用美区自有 API OAuth 配置流程。
    configure_rclone_remote
}

mount_rclone() {
    echo ""
    echo -e "${BLUE}[*] 开始挂载 / 修复 Rclone...${NC}"
    echo ""

    if ! command -v rclone &> /dev/null; then
        err "Rclone 未安装，请先执行菜单 3.1 安装"
        return 1
    fi

    if ! check_fuse; then
        err "FUSE 工具未安装"
        return 1
    fi

    local remotes
    remotes="$(rclone listremotes 2>/dev/null || true)"
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

    read -r -p "请输入挂载路径 (默认: /mnt/rclone): " mount_path
    mount_path=${mount_path:-/mnt/rclone}

    if [[ "$mount_path" != /* ]]; then
        err "挂载路径必须是绝对路径"
        return 1
    fi

    info "先验证远端 ${config_name}: 是否可访问"
    local verify_error verify_rc auth_error_file
    auth_error_file="${RCLONE_HEALTH_STATE_DIR}/${config_name}.auth_error"
    mkdir -p "$RCLONE_HEALTH_STATE_DIR"
    verify_error="$(mktemp /tmp/rclone-mount-verify.XXXXXX)"
    if timeout -k 2s 15s rclone lsd "${config_name}:" --config "$RCLONE_CONFIG_FILE" --max-depth 1 >/dev/null 2>"$verify_error"; then
        rm -f "$auth_error_file"
    else
        verify_rc=$?
        if grep -qiE 'revoked|result[^0-9]*2095|2095|invalid[^[:alnum:]]*(access[_ -]?token|token)|access[_ -]?token[^[:alnum:]]*(invalid|revoked)|invalid[^[:alnum:]]*client|client[^[:alnum:]]*invalid|client_secret|unauthori[sz]ed|invalid_grant|oauth[^[:alnum:]]*(invalid|denied)' "$verify_error"; then
            date -Is > "$auth_error_file"
            err "Pcloud 授权已失效/被撤销，本次不会启动或反复重启挂载。"
            echo "请执行菜单 3.2 重新授权，成功后再执行菜单 3.3 启用/修复挂载。"
        else
            err "Pcloud 远端当前不可达（rc=${verify_rc}），更可能是网络或服务异常。"
            echo "为避免生成假挂载，本次不启动 FUSE；网络恢复后可重新执行菜单 3.3。"
        fi
        rm -f "$verify_error"
        return 1
    fi
    rm -f "$verify_error"

    mkdir -p "$mount_path"
    write_rclone_mount_service "$config_name" "$mount_path"
    write_rclone_health_monitor "$config_name" "$mount_path"
    systemctl daemon-reload

    # 旧版脚本可能留下一个不受 systemd 管理的后台 rclone mount。
    # 无论挂载当前是否健康，都先安全卸载，再由 systemd 统一接管。
    if mountpoint -q "$mount_path" 2>/dev/null; then
        info "检测到现有挂载，先卸载并切换为 systemd 托管"
        systemctl stop "rclone-mount-${config_name}.service" >/dev/null 2>&1 || true
        if command -v fusermount3 >/dev/null 2>&1; then
            fusermount3 -uz "$mount_path" >/dev/null 2>&1 || true
        elif command -v fusermount >/dev/null 2>&1; then
            fusermount -uz "$mount_path" >/dev/null 2>&1 || true
        fi
        umount -l "$mount_path" >/dev/null 2>&1 || true
        sleep 1
    fi

    info "启动并启用 Rclone systemd 服务"
    systemctl enable --now "rclone-mount-${config_name}.service"
    systemctl enable --now "rclone-mount-health-${config_name}.timer"

    sleep 4
    if rclone_mount_probe "$mount_path"; then
        ok "Rclone 挂载成功且目录读取正常"
        echo "配置名称: $config_name"
        echo "挂载路径: $mount_path"
        echo "systemd 服务: rclone-mount-${config_name}.service"
        echo "健康监控: 每 1 分钟检查一次，发现 EIO/失效挂载后自动恢复"
        df -h "$mount_path" 2>/dev/null || true
    else
        err "Rclone 服务已启动，但挂载读测试失败"
        systemctl --no-pager --full status "rclone-mount-${config_name}.service" 2>/dev/null | tail -20 || true
        journalctl -u "rclone-mount-${config_name}.service" -n 20 --no-pager 2>/dev/null || true
        return 1
    fi
}

rclone_probe_path() {
    local mount_path="$1"
    local configured_path=""
    if [ -r "${APP_ROOT}/config.yaml" ]; then
        configured_path="$(sed -n 's/^[[:space:]]*upload_folder:[[:space:]]*//p' "${APP_ROOT}/config.yaml" | head -n 1 | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//" || true)"
    fi
    case "$configured_path" in
        "$mount_path"|"$mount_path"/*) printf '%s\n' "$configured_path" ;;
        *) printf '%s\n' "$mount_path" ;;
    esac
}

rclone_mount_probe() {
    local mount_path="$1"
    local probe_path="${2:-}"
    [ -n "$probe_path" ] || probe_path="$(rclone_probe_path "$mount_path")"
    mountpoint -q "$mount_path" 2>/dev/null || return 1
    timeout -k 2s 8s /bin/ls -A -- "$probe_path" >/dev/null 2>&1
}

write_rclone_mount_service() {
    local config_name="$1"
    local mount_path="$2"
    local service_file="/etc/systemd/system/rclone-mount-${config_name}.service"
    local log_file="/var/log/rclone-mount-${config_name}.log"
    local fuse_unmount fuse_unmount_args
    if command -v fusermount3 >/dev/null 2>&1; then
        fuse_unmount="$(command -v fusermount3)"
        fuse_unmount_args="-uz"
    elif command -v fusermount >/dev/null 2>&1; then
        fuse_unmount="$(command -v fusermount)"
        fuse_unmount_args="-uz"
    else
        fuse_unmount="$(command -v umount 2>/dev/null || echo /bin/umount)"
        fuse_unmount_args="-l"
    fi

    cat > "$service_file" <<EOF_SYSTEMD
[Unit]
Description=Rclone Mount ${config_name}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
User=root
Group=root
ExecStartPre=/bin/mkdir -p ${mount_path}
ExecStart=/usr/bin/rclone mount ${config_name}: ${mount_path} \\
  --config ${RCLONE_CONFIG_FILE} \\
  --allow-other \\
  --vfs-cache-mode=full \\
  --vfs-cache-max-age=24h \\
  --vfs-write-back=5s \\
  --dir-cache-time=5m \\
  --contimeout=10s \\
  --timeout=30s \\
  --log-level INFO \\
  --log-file ${log_file}
ExecStop=-${fuse_unmount} ${fuse_unmount_args} ${mount_path}
Restart=on-failure
RestartSec=15
TimeoutStopSec=20
KillMode=mixed

[Install]
WantedBy=multi-user.target
EOF_SYSTEMD

    chmod 644 "$service_file"
    ok "已创建 systemd 服务: $service_file"
}

write_rclone_health_monitor() {
    local config_name="$1"
    local mount_path="$2"
    local service_name="rclone-mount-${config_name}.service"
    local health_script="/usr/local/sbin/rclone-mount-health-${config_name}.sh"
    local health_service="/etc/systemd/system/rclone-mount-health-${config_name}.service"
    local health_timer="/etc/systemd/system/rclone-mount-health-${config_name}.timer"

    mkdir -p "$RCLONE_HEALTH_STATE_DIR"
    chmod 700 "$RCLONE_HEALTH_STATE_DIR" 2>/dev/null || true

    cat > "$health_script" <<EOF_HEALTH
#!/usr/bin/env bash
set -u

SERVICE='${service_name}'
MOUNT_PATH='${mount_path}'
REMOTE='${config_name}:'
RCLONE_CONFIG='${RCLONE_CONFIG_FILE}'
ISM_CONFIG='${APP_ROOT}/config.yaml'
STATE_DIR='${RCLONE_HEALTH_STATE_DIR}'
AUTH_ERROR_FILE='${RCLONE_HEALTH_STATE_DIR}/${config_name}.auth_error'
TAG='rclone-mount-health-${config_name}'

log_msg() { logger -t "\$TAG" -- "\$*"; }

mkdir -p "\$STATE_DIR"
chmod 700 "\$STATE_DIR" 2>/dev/null || true

probe_path="\$MOUNT_PATH"
if [ -r "\$ISM_CONFIG" ]; then
    configured_path="\$(sed -n 's/^[[:space:]]*upload_folder:[[:space:]]*//p' "\$ISM_CONFIG" | head -n 1 | sed -e 's/^["'\'' ]*//' -e 's/["'\'' ]*\$//' || true)"
    case "\$configured_path" in
        "\$MOUNT_PATH"|"\$MOUNT_PATH"/*) probe_path="\$configured_path" ;;
    esac
fi

is_auth_error() {
    printf '%s' "\$1" | grep -qiE \
        'revoked|result[^0-9]*2095|2095|invalid[^[:alnum:]]*(access[_ -]?token|token)|access[_ -]?token[^[:alnum:]]*(invalid|revoked)|invalid[^[:alnum:]]*client|client[^[:alnum:]]*invalid|client_secret|unauthori[sz]ed|invalid_grant|oauth[^[:alnum:]]*(invalid|denied)'
}

mark_auth_error() {
    printf '%s\n' "\$(date -Is)" > "\$AUTH_ERROR_FILE"
    log_msg "[AUTH ERROR] Pcloud authorization invalid/revoked; manual re-authorization required"
}

clear_auth_error() {
    rm -f "\$AUTH_ERROR_FILE"
}

remote_check() {
    local err_file err_text rc
    err_file="\$(mktemp /tmp/rclone-health-remote.XXXXXX)" || return 1
    if timeout -k 2s 15s /usr/bin/rclone lsd "\$REMOTE" --config "\$RCLONE_CONFIG" --max-depth 1 >/dev/null 2>"\$err_file"; then
        clear_auth_error
        rm -f "\$err_file"
        return 0
    fi
    rc=\$?
    err_text="\$(cat "\$err_file" 2>/dev/null || true)"
    rm -f "\$err_file"
    if is_auth_error "\$err_text"; then
        mark_auth_error
        return 2
    fi
    return "\$rc"
}

stop_bad_mount_for_auth() {
    if systemctl is-active --quiet "\$SERVICE"; then
        log_msg "authorization failed; stopping stale mount service \$SERVICE"
        systemctl stop "\$SERVICE" >/dev/null 2>&1 || true
    fi
}

restart_mount() {
    log_msg "restarting \$SERVICE (probe=\$probe_path)"
    systemctl reset-failed "\$SERVICE" >/dev/null 2>&1 || true
    systemctl restart "\$SERVICE" || return 1
    sleep 5
    mountpoint -q "\$MOUNT_PATH" || return 1
    timeout -k 2s 8s /bin/ls -A -- "\$probe_path" >/dev/null 2>&1
}

if ! systemctl is-active --quiet "\$SERVICE"; then
    remote_check
    remote_rc=\$?
    if [ "\$remote_rc" -eq 0 ]; then
        restart_mount && log_msg "service recovered" || log_msg "service recovery failed"
    elif [ "\$remote_rc" -eq 2 ]; then
        log_msg "service remains stopped because Pcloud authorization requires manual renewal"
    else
        log_msg "remote unavailable; skip restart until backend recovers"
    fi
    exit 0
fi

if ! mountpoint -q "\$MOUNT_PATH"; then
    remote_check
    remote_rc=\$?
    if [ "\$remote_rc" -eq 0 ]; then
        restart_mount && log_msg "missing mount recovered" || log_msg "missing mount recovery failed"
    elif [ "\$remote_rc" -eq 2 ]; then
        stop_bad_mount_for_auth
        log_msg "mount not restarted because Pcloud authorization requires manual renewal"
    else
        log_msg "mount missing and remote unavailable"
    fi
    exit 0
fi

probe_err="\$(timeout -k 2s 8s /bin/ls -A -- "\$probe_path" 2>&1 >/dev/null)"
probe_rc=\$?
if [ "\$probe_rc" -eq 0 ]; then
    exit 0
fi

# Missing ISM subdirectory is a configuration problem, not a stale FUSE mount.
if printf '%s' "\$probe_err" | grep -qiE 'No such file|not found'; then
    log_msg "configured path missing: \$probe_path"
    exit 0
fi

# Only recycle the FUSE mount when the backend itself is reachable. Authentication
# failures are kept separate from network/backend outages so a revoked token does
# not cause a restart loop.
remote_check
remote_rc=\$?
if [ "\$remote_rc" -eq 0 ]; then
    log_msg "mount unhealthy (rc=\$probe_rc, error=\$probe_err); backend reachable"
    restart_mount && log_msg "stale/EIO mount recovered" || log_msg "stale/EIO mount recovery failed"
elif [ "\$remote_rc" -eq 2 ]; then
    stop_bad_mount_for_auth
    log_msg "mount unhealthy and Pcloud authorization invalid; waiting for manual re-authorization"
else
    log_msg "mount unhealthy but backend unavailable; waiting for backend recovery"
fi
EOF_HEALTH
    chmod 755 "$health_script"

    cat > "$health_service" <<EOF_HEALTH_SERVICE
[Unit]
Description=Health check for Rclone mount ${config_name}
After=network-online.target rclone-mount-${config_name}.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${health_script}
TimeoutStartSec=40
EOF_HEALTH_SERVICE

    cat > "$health_timer" <<EOF_HEALTH_TIMER
[Unit]
Description=Periodic health check for Rclone mount ${config_name}

[Timer]
OnBootSec=60s
OnUnitActiveSec=60s
AccuracySec=10s
Persistent=true
Unit=rclone-mount-health-${config_name}.service

[Install]
WantedBy=timers.target
EOF_HEALTH_TIMER

    chmod 644 "$health_service" "$health_timer"
    ok "已创建 Rclone 挂载健康监控（每 1 分钟，区分授权故障与网络/EIO）"
}

show_rclone_status() {
    local config_name="rclone"
    local mount_path="/mnt/rclone"
    local service_name="rclone-mount-${config_name}.service"
    local timer_name="rclone-mount-health-${config_name}.timer"
    local auth_error_file="${RCLONE_HEALTH_STATE_DIR}/${config_name}.auth_error"
    local probe_path remote_error remote_rc

    probe_path="$(rclone_probe_path "$mount_path")"
    echo "Rclone 服务状态：$(systemctl is-active "$service_name" 2>/dev/null || true)"
    echo "健康监控状态：$(systemctl is-active "$timer_name" 2>/dev/null || true)"
    echo "实际检测目录：$probe_path"

    mkdir -p "$RCLONE_HEALTH_STATE_DIR"
    remote_error="$(mktemp /tmp/rclone-status-remote.XXXXXX)"
    if timeout -k 2s 15s rclone lsd "${config_name}:" --config "$RCLONE_CONFIG_FILE" --max-depth 1 >/dev/null 2>"$remote_error"; then
        rm -f "$auth_error_file"
        ok "Pcloud 远端验证正常"
    else
        remote_rc=$?
        if grep -qiE 'revoked|result[^0-9]*2095|2095|invalid[^[:alnum:]]*(access[_ -]?token|token)|access[_ -]?token[^[:alnum:]]*(invalid|revoked)|invalid[^[:alnum:]]*client|client[^[:alnum:]]*invalid|client_secret|unauthori[sz]ed|invalid_grant|oauth[^[:alnum:]]*(invalid|denied)' "$remote_error"; then
            date -Is > "$auth_error_file"
            err "Pcloud 授权异常：Token / API 授权已失效，需要重新执行授权配置"
        else
            warn "Pcloud 远端暂不可达（更可能是网络或服务异常，rc=${remote_rc}）"
        fi
    fi
    rm -f "$remote_error"

    if [ -f "$auth_error_file" ]; then
        echo "授权状态：异常（需要人工重新授权）"
        echo "最近检测：$(head -n 1 "$auth_error_file" 2>/dev/null || true)"
    else
        echo "授权状态：未检测到授权异常"
    fi

    if mountpoint -q "$mount_path" 2>/dev/null; then
        echo "挂载点：$mount_path（已挂载）"
        if rclone_mount_probe "$mount_path" "$probe_path"; then
            ok "挂载目录读测试正常"
        else
            err "挂载目录读测试失败，可能是 EIO / stale FUSE"
        fi
    else
        err "$mount_path 未挂载"
    fi
    echo ""
    echo "最近健康检查日志："
    journalctl -t "rclone-mount-health-${config_name}" -n 12 --no-pager 2>/dev/null || true
}

unmount_rclone() {
    echo ""
    echo "[*] 开始卸载 Rclone..."
    echo ""

    echo "[*] 当前 Rclone 挂载点:"
    mount | grep rclone | awk '{print $3}' | nl || true

    echo ""
    echo -n "请输入卸载路径 (默认: /mnt/rclone): "
    read -r mount_path
    mount_path=${mount_path:-/mnt/rclone}

    # 停止本脚本创建、且绑定到该挂载路径的 systemd 服务和健康监控。
    local sf svc config_name
    for sf in /etc/systemd/system/rclone-mount-*.service; do
        [ -f "$sf" ] || continue
        case "$sf" in *rclone-mount-health-*) continue ;; esac
        if grep -Fq " ${mount_path} " "$sf" 2>/dev/null; then
            svc="$(basename "$sf")"
            config_name="${svc#rclone-mount-}"
            config_name="${config_name%.service}"
            systemctl disable --now "rclone-mount-health-${config_name}.timer" >/dev/null 2>&1 || true
            systemctl stop "rclone-mount-health-${config_name}.service" >/dev/null 2>&1 || true
            systemctl disable --now "$svc" >/dev/null 2>&1 || true
        fi
    done

    if mountpoint -q "$mount_path" 2>/dev/null; then
        echo "[*] 正在卸载 $mount_path..."
        if command -v fusermount3 >/dev/null 2>&1; then
            fusermount3 -uz "$mount_path" 2>/dev/null || true
        elif command -v fusermount >/dev/null 2>&1; then
            fusermount -uz "$mount_path" 2>/dev/null || true
        fi
        umount -l "$mount_path" 2>/dev/null || true
        sleep 1
    fi

    if ! mountpoint -q "$mount_path" 2>/dev/null; then
        ok "卸载成功，自动挂载和健康监控已停止"
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
                    if command -v fusermount3 >/dev/null 2>&1; then
                        fusermount3 -uz "$mount_path" 2>/dev/null || true
                    elif command -v fusermount >/dev/null 2>&1; then
                        fusermount -uz "$mount_path" 2>/dev/null || true
                    fi
                    umount -l "$mount_path" 2>/dev/null || true
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

    info "停止并清理 Rclone systemd 挂载与健康监控"
    for unit in /etc/systemd/system/rclone-mount-health-*.timer /etc/systemd/system/rclone-mount-*.service; do
        [ -f "$unit" ] || continue
        systemctl disable --now "$(basename "$unit")" >/dev/null 2>&1 || true
    done
    rm -f /etc/systemd/system/rclone-mount-health-*.timer \
          /etc/systemd/system/rclone-mount-health-*.service \
          /etc/systemd/system/rclone-mount-*.service \
          /usr/local/sbin/rclone-mount-health-*.sh
    rm -rf "$RCLONE_HEALTH_STATE_DIR"
    systemctl daemon-reload

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
                    echo -e "  ${BLUE}[2]${NC} ${BOLD}${BLUE}授权配置${NC}           (Pcloud 美区 / 自有 API)"
                    echo -e "  ${MAGENTA}[3]${NC} ${BOLD}${MAGENTA}启用/修复挂载${NC}      (systemd 托管 + 自动健康恢复)"
                    echo -e "  ${GREEN}[4]${NC} ${BOLD}${GREEN}查看挂载状态${NC}        (读测试 + 健康监控日志)"
                    echo -e "  ${CYAN}[0]${NC} ${BOLD}${CYAN}返回主菜单${NC}"
                    read -r -p "请选择 [0-4]: " rclone_choice
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
                        4)
                            show_rclone_status
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
