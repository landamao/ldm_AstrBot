#!/bin/bash

# ------------------------------------------------------------
# 提示：安装程序将从公网镜像服务器自动下载 ldmbot.zip
# ------------------------------------------------------------

set -euo pipefail

# ------------------------------------------------------------
# ldmbot 安装脚本
# ------------------------------------------------------------

# 下载源：公网镜像服务器（nginx，/api/ldmbot/ 目录下 latest.zip 始终为最新版）
MIRROR_BASE="http://39.106.102.162:9200/api/ldmbot"
DOWNLOAD_URL="${MIRROR_BASE}/latest.zip"

# （原 GitHub Releases 下载地址，保留备查）
# DOWNLOAD_URL="https://github.com/landamao/ldm_AstrBot/releases/latest/download/ldmbot.zip"

# ==================== 代理相关 ====================
# 说明：下载压缩包已改用公网镜像服务器，下载阶段的代理逻辑已注释；
#       但同步依赖（uv / pip 装依赖）时的代理设置保留，国内网络装依赖可能仍需要代理。
# 自动检测代理的预设端口（非交互模式时使用）
PROXY_PORTS=(7890 7897)

# 公共 GitHub 代理列表（参考 NapCat 安装脚本 network_test，已注释，不再用于下载压缩包）
# GH_PROXY_LIST=(
#     "https://ghfast.top"
#     "https://gh.wuliya.xin"
#     "https://gh-proxy.com"
#     "https://github.moeyy.xyz"
# )
# ====================================================================

# 颜色定义
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
BLUE=$'\033[0;1;34;94m'
NC=$'\033[0m'

# ---------- 统一日志输出（仿 NapCat 风格：时间戳 + 按关键词自动配色）----------
log() {
    local msg
    msg="[$(date +"%Y-%m-%d %H:%M:%S")]: $1"
    case "$1" in
        *"失败"*|*"错误"*|*"无法"*|*"无效"*|*"不存在"*|*"已退出"*|*"中止"*|*"未知"*|*"未找到包含"*|*"解压后未找到"*)
            echo -e "${RED}${msg}${NC}"
            ;;
        *"成功"*|*"完成"*|*"检测到"*|*"使用"*|*"启动"*|*"开始"*)
            echo -e "${GREEN}${msg}${NC}"
            ;;
        *"忽略"*|*"跳过"*|*"默认"*|*"警告"*|*"未找到"*|*"未检测到"*|*"已取消"*|*"不更新"*|*"提示"*|*"请"*)
            echo -e "${YELLOW}${msg}${NC}"
            ;;
        *)
            echo -e "${BLUE}${msg}${NC}"
            ;;
    esac
}

# 解析脚本绝对路径
[[ "$0" = /* ]] && SCRIPT_PATH="$0" || SCRIPT_PATH="$PWD/$0"

# ---------- 记录脚本启动时的原始目录 ----------
ORIG_DIR="$(pwd)"

# ---------- 选项解析 ----------
AUTO_YES=false
AUTO_UPDATE=false
DIRECT_OPTION=""   # -数字 直接选择菜单选项
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes)
            AUTO_YES=true
            shift
            ;;
        -up|--update)
            AUTO_UPDATE=true
            AUTO_YES=true
            shift
            ;;
        -[1-7])
            DIRECT_OPTION="${1#-}"
            shift
            ;;
        *)
            log "未知选项: $1"
            log "可用选项：-y / -up / -1~-7（直接选择菜单选项）"
            exit 1
            ;;
    esac
done

# ---------- 辅助函数 ----------
# 清空目录下所有内容（含隐藏文件），但保留目录本身；
# 兼容 astrbot / dashboard/dist 为软链接的情况（软链接保留，只清空链接目标内容）
clear_dir_contents() {
    local d="$1"
    if [[ -e "$d" ]] || [[ -L "$d" ]]; then
        rm -rf "$d"/* "$d"/.[!.]* "$d"/..?* 2>/dev/null || true
    fi
}

ask_yes_no() {
    if $AUTO_YES; then return 0; fi
    local prompt="$1"
    local answer
    # 强制从终端读取，防止在子shell $() 中卡死
    echo >&2
    read -p "${YELLOW}${prompt} [Y/n] ${NC}" answer < /dev/tty
    [[ "$answer" =~ ^[Nn] ]] && return 1 || return 0
}

# ==================== 代理相关 ====================
# 说明：test_proxy 供「同步依赖时的代理设置」使用（下载压缩包阶段已不再使用代理）。
test_proxy() {
    local endpoint=$1 host port
    if [[ "$endpoint" == *:* ]]; then
        host="${endpoint%:*}"
        port="${endpoint##*:}"
    else
        host="127.0.0.1"
        port="$endpoint"
    fi
    curl -x "http://${host}:${port}" -s --connect-timeout 5 --max-time 10 \
        "http://httpbin.org/ip" > /dev/null 2>&1
}

# 测试公共 GitHub 代理可用性（参考 napcat.sh network_test，已注释，不再使用）
# 成功时设置 TARGET_GH_PROXY 为可用代理前缀，失败时置空
# network_test_github() {
#     local timeout=10
#     local status=0
#     local found=0
#     TARGET_GH_PROXY=""
#     log "开始测试公共 GitHub 代理..." >&2
#
#     local check_url="https://raw.githubusercontent.com/landamao/ldm_AstrBot/main/README.md"
#     local proxy
#     for proxy in "${GH_PROXY_LIST[@]}"; do
#         log "测试代理: ${proxy}" >&2
#         status=$(curl -k -L --connect-timeout ${timeout} --max-time $((timeout*2)) \
#             -o /dev/null -s -w "%{http_code}" "${proxy}/${check_url}" 2>/dev/null || true)
#         if [ "${status}" = "200" ]; then
#             found=1
#             TARGET_GH_PROXY="${proxy}"
#             log "将使用 GitHub 代理: ${proxy}" >&2
#             break
#         else
#             log "代理 ${proxy} 不可用 (HTTP ${status:-超时})" >&2
#         fi
#     done
#
#     if [ ${found} -eq 0 ]; then
#         log "警告: 未找到可用的公共 GitHub 代理，将直连下载..." >&2
#         TARGET_GH_PROXY=""
#     fi
# }
# ====================================================================

# 用 curl/wget 下载到临时文件；成功返回 0
# 参数: $1=最终下载 URL  $2=输出路径  [$3=可选 curl 额外参数，如 -x http://...]
_download_to_tmp() {
    local url="$1"
    local out="$2"
    shift 2
    # 剩余参数作为 curl 额外选项
    # --noproxy '*'：强制不走本机代理环境变量（代理/VPN 常导致连不上镜像 IP）
    # -f：HTTP 4xx/5xx（如 404 错误页）时返回非零，避免把错误页当成功下载
    if curl "$@" -k -L -f -o "$out" "$url" --connect-timeout 15 --max-time 300 --noproxy '*' 2>/dev/null; then
        # 简单校验：文件非空
        [[ -s "$out" ]] && return 0
    fi
    return 1
}

ensure_unzip() {
    if command -v unzip &>/dev/null; then return 0; fi
    log "未找到 unzip 命令，正在安装..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y unzip || { log "安装 unzip 失败。"; exit 1; }
    elif command -v yum &>/dev/null; then
        sudo yum install -y unzip || { log "安装 unzip 失败. "; exit 1; }
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y unzip || { log "安装 unzip 失败。"; exit 1; }
    elif command -v brew &>/dev/null; then
        brew install unzip || { log "安装 unzip 失败。"; exit 1; }
    else
        log "无法自动安装 unzip。请手动安装 unzip 后重试。"; exit 1
    fi
}

# ---------- 下载 ldmbot.zip 函数（始终操作原始目录）----------
# 下载源：公网镜像服务器（http://39.106.102.162:9200/api/ldmbot/latest.zip）
# 注意：本函数 stdout 用于返回 zip 路径，所有提示必须走 stderr（log ... >&2）
download_ldmbot_zip() {
    local target_zip="${ORIG_DIR}/ldmbot.zip"    # 固定使用原始目录下的文件
    local tmp_zip="${target_zip}.tmp"
    # 代理相关变量（已注释，不再使用）
    # local use_http_proxy=false
    # local http_proxy_port=""
    # local use_gh_proxy=false
    # TARGET_GH_PROXY=""

    log "开始下载 ldmbot 源码..." >&2
    # 清理可能残留的临时文件
    rm -f "$tmp_zip"

    # 检查原始目录下是否已有文件
    if [[ -f "$target_zip" ]]; then
        log "检测到脚本启动目录（${ORIG_DIR}）下已存在 ldmbot.zip。" >&2
        if ask_yes_no "是否直接使用已存在的 ldmbot.zip？"; then
            log "使用已存在的 ldmbot.zip (位于 ${ORIG_DIR})" >&2
            echo "$target_zip"
            return
        fi
        # 用户选择重新下载，暂不删除旧文件，等下载成功后再替换
    fi

    log "开始下载最新 ldmbot.zip 到目录：脚本启动目录 ${ORIG_DIR}" >&2
    log "下载地址：${DOWNLOAD_URL}" >&2
    log "如无法下载，可手动下载到该目录，然后重新执行脚本" >&2

    # ==================== 代理相关（已全部注释，不再使用）====================
    # ---------- 交互模式：先问 HTTP 代理，不用再问公共 GitHub 代理 ----------
    # if ! $AUTO_YES && [ -t 0 ]; then
    #     if ask_yes_no "是否使用 HTTP 代理下载？（如不懂该功能请输入 n 跳过）"; then
    #         echo >&2
    #         read -p "${YELLOW}请输入代理地址（例如 127.0.0.1:7890，或仅端口 7890；直接回车使用 127.0.0.1:7890）: ${NC}" proxy_port < /dev/tty
    #         proxy_port=${proxy_port:-127.0.0.1:7890}
    #         local pport="${proxy_port##*:}"
    #         if { [[ "$proxy_port" =~ ^[0-9]+$ ]] || [[ "$proxy_port" =~ ^[^:]+:[0-9]+$ ]]; } && [ "$pport" -ge 1 ] && [ "$pport" -le 65535 ]; then
    #             use_http_proxy=true
    #             http_proxy_port="$proxy_port"
    #         else
    #             log "无效代理地址，跳过 HTTP 代理。" >&2
    #         fi
    #     else
    #         # 不用 HTTP 代理时，再询问是否使用公共 GitHub 代理
    #         if ask_yes_no "是否使用公共 GitHub 代理下载？（国内网络环境推荐）"; then
    #             use_gh_proxy=true
    #         fi
    #     fi
    # ---------- 非交互模式：自动检测本地端口，失败再测公共代理 ----------
    # else
    #     for port in "${PROXY_PORTS[@]}"; do
    #         if test_proxy "$port"; then
    #             use_http_proxy=true
    #             http_proxy_port="$port"
    #             log "检测到代理端口 ${port}" >&2
    #             break
    #         fi
    #     done
    #     if ! $use_http_proxy; then
    #         use_gh_proxy=true
    #         log "未检测到 HTTP 代理，将使用公共 GitHub 代理..." >&2
    #     fi
    # fi

    # ---------- 1) HTTP 代理下载 ----------
    # if $use_http_proxy && [[ -n "$http_proxy_port" ]]; then
    #     # 统一为 host:port
    #     [[ "$http_proxy_port" == *:* ]] || http_proxy_port="127.0.0.1:${http_proxy_port}"
    #     log "通过代理 ${http_proxy_port} 下载..." >&2
    #     if _download_to_tmp "$DOWNLOAD_URL" "$tmp_zip" -x "http://${http_proxy_port}"; then
    #         log "下载完成（HTTP 代理）" >&2
    #         mv "$tmp_zip" "$target_zip"
    #         echo "$target_zip"
    #         return
    #     fi
    #     log "HTTP 代理下载失败。" >&2
    #     # 交互模式下 HTTP 失败后，再询问是否用公共代理
    #     if ! $AUTO_YES && [ -t 0 ]; then
    #         if ask_yes_no "HTTP 代理失败，是否改用公共 GitHub 代理？"; then
    #             use_gh_proxy=true
    #         fi
    #     else
    #         use_gh_proxy=true
    #     fi
    # fi

    # ---------- 2) 公共 GitHub 代理下载 ----------
    # if $use_gh_proxy; then
    #     network_test_github
    #     if [[ -n "$TARGET_GH_PROXY" ]]; then
    #         local gh_url="${TARGET_GH_PROXY}/${DOWNLOAD_URL}"
    #         log "通过公共代理下载: ${gh_url}" >&2
    #         if _download_to_tmp "$gh_url" "$tmp_zip"; then
    #             log "下载完成（公共 GitHub 代理: ${TARGET_GH_PROXY}）" >&2
    #             mv "$tmp_zip" "$target_zip"
    #             echo "$target_zip"
    #             return
    #         fi
    #         log "公共 GitHub 代理下载失败，将直连下载..." >&2
    #     fi
    # fi
    # ====================================================================

    # ---------- 3) 从公网镜像服务器下载 ----------
    log "使用公网镜像服务器下载..." >&2
    if _download_to_tmp "$DOWNLOAD_URL" "$tmp_zip"; then
        log "下载完成。" >&2
        mv "$tmp_zip" "$target_zip"
        echo "$target_zip"
        return
    elif command -v wget &>/dev/null && wget --no-proxy -O "$tmp_zip" "$DOWNLOAD_URL" --timeout=30 2>/dev/null && [[ -s "$tmp_zip" ]]; then
        log "下载完成（wget）" >&2
        mv "$tmp_zip" "$target_zip"
        echo "$target_zip"
        return
    else
        log "下载失败，请检查网络，关闭代理和/或VPN后重试。" >&2
        rm -f "$tmp_zip"
        exit 1
    fi
}

# ====== 公共函数：项目目录定位 / 统一启动 ======

# 判断目录是否为 ldmbot 项目根目录（需含 main.py 和 astrbot 目录）
is_project_root() {
    [[ -f "$1/main.py" && -d "$1/astrbot" ]]
}

# -y 自动模式的完整性判断：main.py + astrbot + .venv（无 .venv 视为不完整）
is_complete_project() {
    [[ -f "$1/main.py" && -d "$1/astrbot" && -d "$1/.venv" ]]
}

# 定位 ldmbot 项目目录：
#   1. 当前目录是项目根目录（main.py + astrbot 等）
#   2. 当前目录/ldmbot 是项目根目录
#   3. 搜索标志目录 astrbot/builtin_stars/astrbot/ldm（~ 和 /opt）
# 成功：设置全局 PROJECT_DIR 并返回 0；失败返回 1
find_project_dir() {
    PROJECT_DIR=""
    if is_project_root "$PWD"; then
        PROJECT_DIR="$PWD"
        log "检测到当前目录即 ldmbot 项目根目录：${PROJECT_DIR}"
        return 0
    fi
    if is_project_root "$PWD/ldmbot"; then
        PROJECT_DIR="$PWD/ldmbot"
        log "检测到当前目录下的 ldmbot 项目：${PROJECT_DIR}"
        return 0
    fi

    log "当前目录未检测到 ldmbot 项目，正在搜索标志目录 astrbot/builtin_stars/astrbot/ldm ..."
    local search_results
    search_results=$( { find ~/ /opt -type d -path "*astrbot/builtin_stars/astrbot/ldm" 2>/dev/null || true; } | sed 's|/astrbot/builtin_stars/astrbot/ldm$||' | sort -u )
    if [[ -z "$search_results" ]]; then
        log "未找到包含 astrbot/builtin_stars/astrbot/ldm 的目录。"
        return 1
    fi

    local -a candidates=()
    readarray -t candidates <<< "$search_results"
    if [[ ${#candidates[@]} -eq 1 ]]; then
        PROJECT_DIR="${candidates[0]}"
        log "检测到唯一 ldmbot 项目目录：${PROJECT_DIR}"
        return 0
    fi

    if $AUTO_YES; then
        log "检测到多个可能的 ldmbot 项目目录，非交互模式下无法选择。"
        local d
        for d in "${candidates[@]}"; do
            echo "  - $d"
        done
        return 1
    fi

    echo "找到以下可能的 ldmbot 项目目录："
    local i=1 c
    for d in "${candidates[@]}"; do
        echo "$i) $d"
        ((i++))
    done
    while true; do
        echo
        read -p "${YELLOW}请选择序号（或输入 0 退出）: ${NC}" c < /dev/tty
        if [[ "$c" == "0" ]]; then
            log "已取消。"
            return 1
        elif [[ "$c" =~ ^[0-9]+$ ]] && (( c >= 1 && c <= ${#candidates[@]} )); then
            PROJECT_DIR="${candidates[$((c-1))]}"
            return 0
        else
            log "无效选择。"
        fi
    done
}

# 统一启动 ldmbot：$1=项目根目录（默认当前目录）
# 优先 ./.venv/bin/python3，其次 ./.venv/bin/python，最后系统 python3
# 启动后捕获退出码：Ctrl+C（SIGINT）是用户主动退出，脚本随信号终止，无需处理；
# 其他退出码视为启动/运行失败，提示重新安装
start_ldmbot() {
    local dir="${1:-$PWD}"
    cd "$dir" || { log "无法进入目录：$dir"; exit 1; }
    local ret=0
    # set -e 下须先关闭再开启，才能拿到 ldmbot 的退出码并继续判断
    set +e
    if [[ -x ./.venv/bin/python3 ]]; then
        log "使用虚拟环境启动：./.venv/bin/python3 main.py"
        ./.venv/bin/python3 main.py
        ret=$?
    elif [[ -x ./.venv/bin/python ]]; then
        log "使用虚拟环境启动：./.venv/bin/python main.py"
        ./.venv/bin/python main.py
        ret=$?
    elif command -v python3 &>/dev/null; then
        log "未找到 .venv，使用系统 python3 启动（可能缺少依赖）..."
        python3 main.py
        ret=$?
    else
        set -e
        log "无法找到可用的启动方式，请先执行安装。"
        exit 1
    fi
    set -e

    if [[ "$ret" -eq 0 ]] || [[ "$ret" -eq 130 ]]; then
        # 0 = 正常退出；130 = Ctrl+C（SIGINT）用户主动退出，均属正常结束
        log "ldmbot 已正常退出。"
        exit 0
    fi
    log "ldmbot 退出码：${ret}，似乎启动失败了，请尝试运行 bash ${SCRIPT_PATH} -1 -y 重新安装。"
    exit "$ret"
}

# ====== 更新 ldmbot 函数 ======
apply_update() {
    local target_dir="$1"
    log "目标目录：${target_dir}"
    log "当前目录内容："
    ls "$target_dir"
    if ! $AUTO_YES && ! $AUTO_UPDATE; then
        if ! ask_yes_no "请检查文件内容，应包含 astrbot、main.py 等文件，是否确认更新？"; then
            log "已取消。"
            exit 0
        fi
    fi
    log "开始自动更新..."

    # ---------- 在原始目录下获取 ZIP（仍在原目录） ----------
    local downloaded_zip
    downloaded_zip=$(download_ldmbot_zip)

    # ---------- 进入目标目录进行替换 ----------
    cd "$target_dir" || { log "无法进入目录。"; exit 1; }

    local tmp_extract=$(mktemp -d)
    ensure_unzip

    log "正在解压并验证文件结构..."
    if ! unzip -o "$downloaded_zip" -d "$tmp_extract" >/dev/null; then
        log "解压 ldmbot.zip 失败，可能是文件损坏或磁盘空间不足。"
        rm -rf "$tmp_extract"
        exit 1
    fi

    # 验证解压结构
    if [[ ! -d "$tmp_extract/ldmbot" ]]; then
        log "解压后未找到 ldmbot 目录，失败，请重新执行脚本"
        rm -rf "$tmp_extract"
        exit 1
    fi

    # 删除旧文件并替换
    log "清理旧程序文件（astrbot 和 dashboard/dist）..."
    if [[ -e astrbot ]] || [[ -L astrbot ]]; then
        clear_dir_contents "astrbot"
    fi
    if [[ -e dashboard/dist ]] || [[ -L dashboard/dist ]]; then
        clear_dir_contents "dashboard/dist"
    fi
    # 旧版本 WebUI 产物在 data/dist，已迁移至 dashboard/dist，删除残留
    if [[ -e data/dist ]] || [[ -L data/dist ]]; then
        rm -rf data/dist
    fi

    log "正在覆盖更新文件..."
    cp -rf "$tmp_extract/ldmbot/." .

    # 清理临时解压目录（保留原始目录下的 zip 文件）
    rm -rf "$tmp_extract"

    # 依赖同步：优先使用虚拟环境 pip，没有虚拟环境 pip 才用 uv（uv 不可用则回退系统 pip）
    local vpy=""
    if [[ -x .venv/bin/python3 ]]; then
        vpy=".venv/bin/python3"
    elif [[ -x .venv/bin/python ]]; then
        vpy=".venv/bin/python"
    fi
    if [[ -n "$vpy" ]] && "$vpy" -m pip --version &>/dev/null 2>&1; then
        log "使用虚拟环境 pip 同步依赖：${vpy} -m pip install -r requirements.txt"
        "$vpy" -m pip install -r requirements.txt
    elif command -v uv &>/dev/null; then
        log "未检测到虚拟环境 pip，使用 uv 同步依赖..."
        if ! uv sync; then
            log "uv sync 失败，回退到系统 pip 同步依赖..."
            python3 -m pip install -r requirements.txt
        fi
    else
        log "未检测到虚拟环境 pip 与 uv，使用系统 pip 同步依赖..."
        python3 -m pip install -r requirements.txt
    fi

    log "ldmbot 更新完成！"
    log "提示：文件已更新，请手动重启 ldmbot 使其生效。"
    log "例如: systemctl restart ldmbot 或通过 WebUI 面板设置页面重启"
    log "或使用 bash ${SCRIPT_PATH} -2 直接启动。"
    exit 0
}

update_ldmbot() {
    log "开始更新 ldmbot ..."
    if ! find_project_dir; then
        log "未找到 ldmbot 项目目录，无法更新。"
        exit 1
    fi
    apply_update "$PROJECT_DIR"
}

# 若为更新模式，直接定位并更新后退出
if $AUTO_UPDATE; then
    update_ldmbot
    exit 0
fi

install_python3_12() {
    log "正在自动安装 Python 3.12..."
    if command -v apt-get &>/dev/null; then
        sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
        sudo apt-get update -qq
        sudo apt-get install -y python3.12 python3.12-venv python3.12-distutils
    elif command -v yum &>/dev/null; then
        sudo yum install -y python3.12 || true
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3.12 || true
    elif command -v brew &>/dev/null; then
        brew install python@3.12
    else
        log "无法自动安装 Python，请手动安装 Python 3.12+。"
        exit 1
    fi
}

# pip 同步依赖（无 uv 或 uv sync 失败时使用）
# 优先直接使用已有虚拟环境；没有可用虚拟环境时才检测系统 Python 创建
sync_pip_deps() {
    log "准备 pip 环境..."
    local venv_py=""
    if [[ -x .venv/bin/python3 ]]; then
        venv_py=".venv/bin/python3"
    elif [[ -x .venv/bin/python ]]; then
        venv_py=".venv/bin/python"
    fi

    if [[ -n "$venv_py" ]] && "$venv_py" -m pip --version &>/dev/null 2>&1; then
        # 虚拟环境已可用：直接用虚拟环境的 pip 同步依赖，不再依赖系统 Python
        log "虚拟环境可用：${venv_py}，直接使用虚拟环境 pip 同步依赖..."
        # shellcheck disable=SC1091
        source .venv/bin/activate
        [[ -f requirements.txt ]] && pip install -r requirements.txt || pip install .
        return
    fi

    # 无可用虚拟环境：检测系统 Python 3.12+ 来创建虚拟环境
    PYTHON_CMD=""
    if command -v python3.12 &>/dev/null; then
        PYTHON_CMD="python3.12"
    elif command -v python3 &>/dev/null; then
        python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' && PYTHON_CMD="python3"
    fi

    if [[ -z "$PYTHON_CMD" ]]; then
        log "未找到 Python 3.12+。"
        install_python3_12
        command -v python3.12 &>/dev/null && PYTHON_CMD="python3.12" || {
            command -v python3 &>/dev/null && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' && PYTHON_CMD="python3"
        }
        [[ -z "$PYTHON_CMD" ]] && { log "无法获取 Python 3.12，中止。"; exit 1; }
    fi

    log "Python 解释器：$PYTHON_CMD"
    if [[ ! -d .venv ]]; then
        log "创建虚拟环境..."
        $PYTHON_CMD -m venv .venv
    elif [[ ! -f .venv/bin/activate ]] || { [[ ! -x .venv/bin/python3 ]] && [[ ! -x .venv/bin/python ]]; }; then
        log "检测到 .venv 损坏，重新创建虚拟环境..."
        rm -rf .venv
        $PYTHON_CMD -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    [[ -f requirements.txt ]] && pip install -r requirements.txt || pip install .
}

# ====== 全新安装 ldmbot（ZIP 从 GitHub 下载） ======
install_ldmbot() {
    log "开始安装 ldmbot ..."

    # 始终在脚本启动目录安装
    cd "$ORIG_DIR" || { log "无法进入启动目录：${ORIG_DIR}"; exit 1; }

    # 覆盖判断：-1 -y 强制重装（像更新一样只删源码部分、保留用户数据，强制走完整安装流程）；完整项目不覆盖，其余覆盖（不论是否已安装，都进行依赖同步）
    local NEED_EXTRACT=true
    if [[ "$DIRECT_OPTION" == "1" ]] && $AUTO_YES; then
        log "检测到 -1 -y 强制重装模式，将强制走完整安装流程（只更新源码，保留用户数据）。"
        # 删除旧源码与前端产物，确保解压后为全新源码；用户数据目录（data/）保留
        if [[ -d ldmbot ]]; then
            if [[ -e ldmbot/astrbot ]] || [[ -L ldmbot/astrbot ]]; then
                log "清空旧源码 astrbot 目录..."
                clear_dir_contents "ldmbot/astrbot"
            fi
            if [[ -e ldmbot/dashboard/dist ]] || [[ -L ldmbot/dashboard/dist ]]; then
                log "清空旧前端 dashboard/dist 目录..."
                clear_dir_contents "ldmbot/dashboard/dist"
            fi
            # 旧版本 WebUI 产物在 data/dist，已迁移至 dashboard/dist，删除残留
            if [[ -e ldmbot/data/dist ]] || [[ -L ldmbot/data/dist ]]; then
                rm -rf ldmbot/data/dist
            fi
        else
            log "未检测到已存在的 ldmbot 目录，将全新安装。"
        fi
    elif [[ -d ldmbot ]]; then
        log "检测到已存在的 ldmbot 目录。"
        if $AUTO_YES && ! $AUTO_UPDATE; then
            if is_project_root "ldmbot"; then
                log "检测到已安装的完整 ldmbot，不覆盖。"
                NEED_EXTRACT=false
            else
                log "未找到完整 ldmbot 项目（缺少 main.py 或 astrbot），将覆盖安装..."
            fi
        elif ! ask_yes_no "是否覆盖安装（解压覆盖现有文件）？"; then
            log "已取消安装。"
            exit 0
        fi
    fi

    if $NEED_EXTRACT; then
        # 下载并解压
        log "正在获取并解压 ldmbot 压缩包..."
        local downloaded_zip
        downloaded_zip=$(download_ldmbot_zip)
        ensure_unzip
        if ! unzip -o "$downloaded_zip" -d "$ORIG_DIR" >/dev/null; then
            log "解压 ldmbot.zip 失败。"
            exit 1
        fi
        if [[ ! -d "$ORIG_DIR/ldmbot" ]]; then
            log "解压后未找到 ldmbot 目录，请检查压缩包。"
            exit 1
        fi
    fi

    # 代理设置（用于后续 uv / pip 下载依赖，保留：国内网络装依赖可能仍需要代理）
    log "代理设置..."
    PROXY_PORT=""

    if $AUTO_YES; then
        for port in "${PROXY_PORTS[@]}"; do
            if test_proxy "$port"; then
                PROXY_PORT="$port"
                break
            fi
        done
        if [[ -n "$PROXY_PORT" ]]; then
            log "检测到可用代理端口：${PROXY_PORT}"
            if ask_yes_no "是否启用代理？"; then
                [[ "$PROXY_PORT" == *:* ]] || PROXY_PORT="127.0.0.1:${PROXY_PORT}"
                export http_proxy="http://${PROXY_PORT}"
                export https_proxy="http://${PROXY_PORT}"
                export all_proxy="http://${PROXY_PORT}"
                log "已启用代理。"
            else
                log "已选择不启用代理。"
            fi
        else
            log "警告：未检测到可用代理，下载可能较慢。"
        fi
    else
        if ask_yes_no "是否使用代理？（用于加速依赖下载，如不懂该功能请输入 n 跳过）"; then
            while true; do
                echo
                read -p "${YELLOW}请输入代理地址（例如 127.0.0.1:7890，或仅端口 7890；直接回车使用 127.0.0.1:7890）: ${NC}" custom_port < /dev/tty
                custom_port=${custom_port:-127.0.0.1:7890}
                local port_part="${custom_port##*:}"
                if { [[ "$custom_port" =~ ^[0-9]+$ ]] || [[ "$custom_port" =~ ^[^:]+:[0-9]+$ ]]; } && [ "$port_part" -ge 1 ] && [ "$port_part" -le 65535 ]; then
                    if test_proxy "$custom_port"; then
                        PROXY_PORT="$custom_port"
                        log "代理地址 ${PROXY_PORT} 连接成功。"
                        break
                    else
                        log "代理地址 ${custom_port} 不可用，请检查代理是否已开启。"
                        if ! ask_yes_no "是否重新输入？"; then
                            log "跳过代理设置。"
                            PROXY_PORT=""
                            break
                        fi
                    fi
                else
                    log "无效代理地址。"
                    if ! ask_yes_no "是否重新输入？"; then
                        log "跳过代理设置。"
                        PROXY_PORT=""
                        break
                    fi
                fi
            done
            if [[ -n "$PROXY_PORT" ]]; then
                # 统一为 host:port 再导出
                [[ "$PROXY_PORT" == *:* ]] || PROXY_PORT="127.0.0.1:${PROXY_PORT}"
                export http_proxy="http://${PROXY_PORT}"
                export https_proxy="http://${PROXY_PORT}"
                export all_proxy="http://${PROXY_PORT}"
                log "已启用代理。"
            fi
        else
            log "已选择不使用代理。"
        fi
    fi

    # 进入项目目录
    cd "$ORIG_DIR/ldmbot" || { log "无法进入 ldmbot 目录。"; exit 1; }

    # 依赖同步：优先使用虚拟环境 pip，没有虚拟环境 pip 才用 uv（uv 不可用则回退 pip）
    USE_UV=false
    VENV_PIP=false
    if [[ -x .venv/bin/python3 ]] || [[ -x .venv/bin/python ]]; then
        local vpy=""
        [[ -x .venv/bin/python3 ]] && vpy=".venv/bin/python3" || vpy=".venv/bin/python"
        if "$vpy" -m pip --version &>/dev/null 2>&1; then
            VENV_PIP=true
        fi
    fi

    if $VENV_PIP; then
        log "检测到虚拟环境 pip，使用 pip 同步依赖..."
        sync_pip_deps
    else
        log "未检测到虚拟环境 pip，改用 uv 同步依赖..."
        # 检查 uv 包管理器（仅在没有虚拟环境 pip 时才需要）
        if command -v uv &>/dev/null; then
            USE_UV=true
            log "已找到 uv：$(which uv)"
        else
            log "未找到 uv，正在安装..."
            if curl -LsSf https://astral.sh/uv/install.sh | sh; then
                export PATH="$HOME/.local/bin:$PATH"
                command -v uv &>/dev/null && USE_UV=true && log "uv 安装成功。" || log "uv 不在 PATH 中。"
            else
                log "uv 安装失败。"
            fi
        fi
        if $USE_UV; then
            log "uv sync 同步依赖..."
            if ! uv sync; then
                log "uv sync 失败，回退到 pip。"
                sync_pip_deps
            fi
        else
            log "未找到可用 uv，使用 pip 同步依赖..."
            sync_pip_deps
        fi
    fi

    log "安装完成！"
    if $AUTO_YES && ! $AUTO_UPDATE; then
        log "正在启动 ldmbot ..."
        start_ldmbot "$PWD"
    fi
    log "可使用 bash ${SCRIPT_PATH} -2 直接启动。"
}
# ==========================

install_napcat() {
    log "开始安装 NapCat ..."

    # 固定安装到当前工作目录下的 NapCat（绝对路径）
    # 注意：子 shell / bash 脚本里的 cd 不会改变「用户当前交互终端」的目录；
    # NapCat 官方 launcher.sh 又依赖相对路径 ./libnapcat_launcher.so，
    # 所以安装结束后必须明确提示用户先 cd 到该绝对路径再启动。
    local napcat_dir="$HOME/NapCat"
    mkdir -p "$napcat_dir" || { log "无法创建 NapCat 目录：${napcat_dir}"; exit 1; }
    cd "$napcat_dir" || { log "无法进入目录：${napcat_dir}"; exit 1; }

    log "安装目录（绝对路径）：${napcat_dir}"
    log "说明：安装脚本内部的 cd 不会改变你当前终端所在目录。"
    log "安装完成后请务必先进入上述目录，再执行启动命令。"

    echo -e "请选择安装线路：\n"
    echo -e "${GREEN}1)${NC} 线路1（国内）：${YELLOW}curl -o napcat.sh https://jiashu.1win.eu.org/https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh && sudo bash napcat.sh${NC}\n"
    echo -e "${GREEN}2)${NC} 线路2（国内）：${YELLOW}curl -o napcat.sh https://github.moeyy.xyz/https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh && sudo bash napcat.sh${NC}\n"
    echo -e "${GREEN}3)${NC} 线路3（国外）：${YELLOW}curl -o napcat.sh https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh && sudo bash napcat.sh${NC}\n"
    echo -e "${GREEN}4)${NC} 线路4（懒大猫提供）：${YELLOW}curl -o http://39.106.102.162:9800/napcat%E8%B5%84%E6%BA%90/ldm_napcat_install.sh > napcat.sh && bash napcat.sh${NC}\n"

    local choice
    echo
    read -p "${YELLOW}请输入选择 [1-4]: ${NC}" choice
    local url=""
    case $choice in
        1) url="https://jiashu.1win.eu.org/https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh" ;;
        2) url="https://github.moeyy.xyz/https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh" ;;
        3) url="https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh" ;;
        4) url="http://39.106.102.162:9800/napcat%E8%B5%84%E6%BA%90/ldm_napcat_install.sh" ;;
        *) log "无效选择"; exit 1 ;;
    esac

    log "正在下载并执行安装脚本..."
    # 不用 exec：exec 会替换本进程，结束后无法再打印「请 cd 到哪里」的提示
    curl -o napcat.sh "$url" || { log "下载失败"; exit 1; }
    # set -e 下必须先关再开，才能拿到官方安装脚本的退出码并继续打印 cd 提示
    set +e
    sudo bash napcat.sh
    local rc=$?
    set -e

    echo
    if [[ $rc -eq 0 ]]; then
        log "NapCat 安装流程已结束。"
    else
        log "NapCat 安装脚本退出码：${rc}"
    fi
    log "重要：你当前终端仍在「运行本安装脚本时」的目录，不会自动进入 NapCat。"
    log "官方 launcher.sh 使用相对路径（./libnapcat_launcher.so），必须先 cd 再启动。"
    echo
    log "请复制执行以下命令启动 NapCat："
    echo -e "  ${YELLOW}cd \"${napcat_dir}\"${NC}"
    echo -e "  ${YELLOW}sudo bash ./launcher.sh${NC}"
    echo
    echo -e "或一行：${YELLOW}cd \"${napcat_dir}\" && sudo bash ./launcher.sh${NC}"
    echo

    # 可选：直接帮用户在本脚本进程内启动（仍无法改用户 shell 的 cwd，但可省一次手敲）
    if [[ $rc -eq 0 ]] && [[ -f "${napcat_dir}/launcher.sh" ]]; then
        if ask_yes_no "是否现在就在本脚本内启动 NapCat？（Ctrl+C 可结束）"; then
            cd "$napcat_dir" || exit 1
            log "正在启动：cd ${napcat_dir} && sudo bash ./launcher.sh"
            exec sudo bash ./launcher.sh
        fi
    elif [[ ! -f "${napcat_dir}/launcher.sh" ]]; then
        log "未在 ${napcat_dir} 找到 launcher.sh，请检查上方安装日志。"
        log "若文件在其他目录，请先 find 定位后再 cd 过去启动。"
    fi
}

migrate_official() {
    log "开始从官方迁移（替换官方源码为 ldmbot）..."
    log "此操作将删除目标 AstrBot 目录下的 astrbot 和 dashboard/dist 目录，并替换为 ldmbot 源码。"
    log "请确保目标目录是 AstrBot 的根目录（包含 astrbot, data, main.py 等）。"
    echo

    local target_dir=""
    while true; do
        echo
        read -p "${YELLOW}请输入目标 AstrBot 目录路径（输入 0 进行自动搜索）: ${NC}" input < /dev/tty
        if [[ "$input" == "0" ]]; then
            log "正在搜索可能的目标目录..."
            local search_results
            search_results=$( { find ~/ -type f -path "*astrbot/api/event/filter*" 2>/dev/null || true; } | sed 's|/astrbot/api/event/filter/.*||' | sort -u )
            if [[ -z "$search_results" ]]; then
                log "未找到匹配的目录。"
                continue
            fi
            echo "找到以下可能的目录："
            echo "$search_results" | nl
            echo
            read -p "${YELLOW}请选择序号或直接输入路径: ${NC}" choice < /dev/tty
            if [[ "$choice" =~ ^[0-9]+$ ]]; then
                target_dir=$(echo "$search_results" | sed -n "${choice}p")
            else
                target_dir="$choice"
            fi
            if [[ -z "$target_dir" ]]; then
                log "无效选择。"
                continue
            fi
        else
            target_dir="$input"
        fi

        if [[ ! -d "$target_dir" ]]; then
            log "目录不存在：$target_dir"
            continue
        fi

        if [[ ! -d "$target_dir/astrbot" ]] || [[ ! -f "$target_dir/main.py" ]]; then
            log "警告：该目录下未找到 astrbot 目录或 main.py，可能不是正确的 AstrBot 根目录。"
            if ! ask_yes_no "是否仍然强制继续？"; then
                echo
                continue
            fi
        fi
        break
    done

    # 展示目标目录内容，确认
    log "目标目录：${target_dir}"
    ls "$target_dir"
    echo

    if ! ask_yes_no "请检查文件内容，应包含 astrbot、main.py 等文件，是否确认？"; then
        log "已取消迁移。"
        return
    fi

    # ---------- 在原始目录下获取 ZIP ----------
    local downloaded_zip
    downloaded_zip=$(download_ldmbot_zip)

    # ---------- 进入目标目录进行替换 ----------
    cd "$target_dir" || { log "无法进入目录。"; exit 1; }

    local tmp_extract=$(mktemp -d)
    ensure_unzip

    log "正在解压文件以准备覆盖..."
    if ! unzip -o "$downloaded_zip" -d "$tmp_extract" >/dev/null; then
        log "解压 ldmbot.zip 失败，迁移中止。"
        rm -rf "$tmp_extract"
        exit 1
    fi

    if [[ ! -d "$tmp_extract/ldmbot" ]]; then
        log "解压后未找到 ldmbot 目录，失败，请重新执行脚本"
        rm -rf "$tmp_extract"
        exit 1
    fi

    log "删除旧官方程序文件（astrbot 和 dashboard/dist）..."
    if [[ -e astrbot ]] || [[ -L astrbot ]]; then
        clear_dir_contents "astrbot"
    fi
    if [[ -e dashboard/dist ]] || [[ -L dashboard/dist ]]; then
        clear_dir_contents "dashboard/dist"
    fi
    # 官方版 WebUI 产物在 data/dist，新版本已迁移至 dashboard/dist，删除残留
    if [[ -e data/dist ]] || [[ -L data/dist ]]; then
        rm -rf data/dist
    fi

    log "正在注入 ldmbot 文件..."
    cp -rf "$tmp_extract/ldmbot/." .

    rm -rf "$tmp_extract"

    log "迁移完成！"
    log "请手动重启 AstrBot（例如执行重启命令或通过 WebUI 面板设置页面重启）。"
    log "提示：如果使用 systemctl 管理，可执行 systemctl restart astrbot 等。"
}

configure_napcat_connection() {
    if ! command -v python3 &>/dev/null; then
        log "未找到 python3，无法运行配置脚本。"
        exit 1
    fi

    local tmp_py
    tmp_py=$(mktemp).py
    cat > "$tmp_py" <<'LDMBOT_NAPCAT_CONFIG_PY'
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Callable, Optional, Set

# ---------- 常量 ----------
ANCHOR = "astrbot/builtin_stars/astrbot/ldm"        # 用来定位 ldmbot 的路径片段
CONFIG_REL = Path("data") / "cmd_config.json"       # ldmbot 配置文件相对路径
DEFAULT_PORT = 6199                                 # 起始端口
DEFAULT_ID_PREFIX = "NapCat"                        # ldmbot 平台 id 前缀
DEFAULT_WS_CLIENT_NAME = "ldmbot"                   # NapCat 反向 ws 客户端名称（不带QQ，因为文件已分QQ）

# 全局搜索时跳过的目录名（加速 + 避免权限噪声）
_跳过目录名 = {
    "proc", "sys", "dev", "run", "snap", "boot",
    ".git", "node_modules", "__pycache__", ".cache",
    "lost+found", "tmp", "var", "media", "cdrom",
}

# 家目录搜索额外跳过的目录（体积大、且基本不会存放 NapCat/ldmbot 配置；. 开头的已由隐藏规则统一跳过）
_家目录跳过目录名 = {
    # Windows 用户/系统目录
    "AppData", "Application Data", "Local Settings",
    "Program Files", "Program Files (x86)", "ProgramData", "Windows",
    "$Recycle.Bin", "System Volume Information", "Library",
    # 开发工具链环境
    "miniconda3", "anaconda3", "miniforge3", "mambaforge",
}

_跳过目录名_小写 = {d.lower() for d in _跳过目录名}
_家目录跳过目录名_小写 = {d.lower() for d in _家目录跳过目录名}


def _目录应跳过(name: str, 家目录模式: bool = False) -> bool:
    """判断目录名是否应被搜索跳过；含 . 开头隐藏目录及 *venv* 通配匹配（venv/.venv/xxvenv 均命中）。"""
    if name.startswith("."):
        return True
    lower = name.lower()
    if "venv" in lower:
        return True
    if lower in _跳过目录名_小写:
        return True
    if 家目录模式 and lower in _家目录跳过目录名_小写:
        return True
    return False


# ---------- 未找到配置时的回退 ----------
def 询问未找到时的处理方式(配置名: str) -> str:
    """未搜索到配置时，让用户选择后续动作。返回 '1'/'2'/'0'。"""
    print(f"\n❌ 未找到 {配置名}。")
    print("请选择：")
    print("  [1] 在全局搜索（耗时可能很长）")
    print("  [2] 手动输入绝对路径")
    print("  [0] 退出")
    while True:
        choice = input("请输入选项（默认 1）：").strip() or "1"
        if choice in ("0", "1", "2"):
            return choice
        print("无效选项，请输入 0、1 或 2。")


def 手动输入绝对路径(
    描述: str,
    校验: Optional[Callable[[Path], Optional[str]]] = None,
) -> Path:
    """循环提示用户输入绝对路径，直到文件存在且通过可选校验。

    校验函数返回 None 表示通过；返回字符串表示错误信息。
    """
    print(f"\n请输入 {描述} 的绝对路径。")
    print("提示：可直接粘贴完整路径；路径两端的引号会自动去掉。")
    while True:
        raw = input("绝对路径：").strip().strip('"').strip("'")
        if not raw:
            print("路径不能为空，请重新输入。")
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            print("请输入绝对路径（以 / 开头，Windows 盘符请用 /mnt/c/... 等形式）。")
            continue
        if not p.exists():
            print(f"文件不存在：{p}")
            continue
        if not p.is_file():
            print(f"不是文件：{p}")
            continue
        if 校验 is not None:
            err = 校验(p)
            if err:
                print(err)
                continue
        return p


def _全局遍历收集(
    匹配: Callable[[str, list[str]], list[Path]],
    进度提示: str,
) -> list[Path]:
    """从常见根目录 os.walk 搜索，忽略权限错误。"""
    print(f"\n⏳ {进度提示}")
    print("   （可能需要较长时间，按 Ctrl+C 可中断）")
    roots: list[Path] = []
    for r in (Path("/"), Path.home()):
        if r.exists() and r not in roots:
            roots.append(r)
    # WSL 常见 Windows 挂载
    mnt = Path("/mnt")
    if mnt.is_dir():
        for child in sorted(mnt.iterdir()):
            if child.is_dir() and child.name.isalpha() and len(child.name) == 1:
                roots.append(child)

    found: list[Path] = []
    seen: Set[str] = set()
    home = Path.home()
    try:
        for root in roots:
            print(f"   … 扫描 {root}")
            # 家目录按家目录规则额外裁剪（AppData、conda 等）
            家目录模式 = root == home
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
                # 原地裁剪：隐藏目录、venv、黑名单等一律不进入
                dirnames[:] = [d for d in dirnames if not _目录应跳过(d, 家目录模式)]
                try:
                    hits = 匹配(dirpath, filenames)
                except (PermissionError, OSError):
                    continue
                for p in hits:
                    key = str(p.resolve()) if p.exists() else str(p)
                    if key not in seen:
                        seen.add(key)
                        found.append(p)
    except KeyboardInterrupt:
        print("\n⚠️ 全局搜索已被中断，将使用目前已找到的结果。")
    print(f"   共找到 {len(found)} 个候选。")
    return found


def 校验NapCat配置文件(path: Path) -> Optional[str]:
    """校验是否像可用的 NapCat onebot 配置；通过返回 None。"""
    name = path.name
    if not name.endswith(".json"):
        return "文件扩展名应为 .json"
    if name.endswith("_.json"):
        return "这是模板文件（*_ .json），请选择带 QQ 号的正式配置"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"无法解析 JSON：{e}"
    if not isinstance(data, dict):
        return "配置根节点应为 JSON 对象"
    return None


def 校验ldmbot配置文件(path: Path) -> Optional[str]:
    """校验是否像可用的 ldmbot cmd_config.json；通过返回 None。"""
    if path.name != "cmd_config.json":
        # 不强制文件名，但给出提醒并仍允许
        pass
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"无法解析 JSON：{e}"
    if not isinstance(data, dict):
        return "配置根节点应为 JSON 对象"
    return None


def 从NapCat路径提取QQ(path: Path) -> str:
    """从 onebot11_QQ.json 这类文件名提取 QQ；失败则询问用户。"""
    parts = path.stem.split("_")
    if len(parts) >= 2:
        candidate = parts[-1]
        if candidate.isdigit():
            return candidate
    while True:
        qq = input("无法从文件名识别 QQ 号，请手动输入：").strip()
        if qq:
            return qq
        print("QQ 号不能为空。")


# ---------- NapCat 配置查找 ----------
def _匹配NapCat配置(dirpath: str, filenames: list[str]) -> list[Path]:
    """匹配 .../napcat/config/onebot*_*.json（排除模板 *_ .json）。"""
    base = Path(dirpath)
    # 路径形如 .../napcat/config/
    if base.name != "config":
        return []
    if base.parent.name.lower() != "napcat":
        return []
    hits = []
    for name in filenames:
        if not name.startswith("onebot"):
            continue
        if not name.endswith(".json") or name.endswith("_.json"):
            continue
        if "_" not in name:
            continue
        hits.append(base / name)
    return hits


def 查找NapCat配置文件(搜索根: Optional[Path] = None) -> list[Path]:
    """在指定根（默认家目录）下查找 NapCat 的 onebot 配置文件（跳过 venv 等无关目录）。"""
    root = 搜索根 or Path.home()
    found: list[Path] = []
    seen: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        dirnames[:] = [d for d in dirnames if not _目录应跳过(d, 家目录模式=True)]
        for p in _匹配NapCat配置(dirpath, filenames):
            key = str(p)
            if key not in seen:
                seen.add(key)
                found.append(p)
    return found


def 全局搜索NapCat配置文件() -> list[Path]:
    """全盘搜索 NapCat onebot 配置。"""
    return _全局遍历收集(_匹配NapCat配置, "正在全局搜索 NapCat 配置…")


def 获取NapCat配置路径() -> Path:
    """查找 / 回退选择 NapCat 配置文件，返回最终路径。"""
    napcat_configs = 查找NapCat配置文件()
    if not napcat_configs:
        while True:
            choice = 询问未找到时的处理方式("NapCat 的 onebot 配置文件（请确保已安装并登录一次QQ）")
            if choice == "0":
                print("已退出。请先安装并启动一次 NapCat 并登录 QQ 后再试。")
                sys.exit(1)
            if choice == "1":
                napcat_configs = 全局搜索NapCat配置文件()
                if napcat_configs:
                    break
                print("全局搜索仍未找到，可改选手动输入路径，或再试一次。")
                continue
            # choice == "2"
            return 手动输入绝对路径("NapCat onebot 配置文件", 校验NapCat配置文件)

    if len(napcat_configs) == 1:
        return napcat_configs[0]

    print("\n找到多个 NapCat 配置文件：")
    for idx, p in enumerate(napcat_configs, 1):
        qq_guess = p.stem.split("_")[-1] if "_" in p.stem else "?"
        print(f"  [{idx}] {p}  (QQ: {qq_guess})")
    choice = input("\n请选择编号（默认 1）：").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(napcat_configs):
        return napcat_configs[int(choice) - 1]
    return napcat_configs[0]


# ---------- 工具：从配置中提取已用端口 ----------
def extract_ports(config: Any, used_ports: Set[int] = None) -> Set[int]:
    """递归遍历配置对象，收集所有端口号"""
    if used_ports is None:
        used_ports = set()
    if isinstance(config, dict):
        for key, value in config.items():
            if key == "port" and isinstance(value, int):
                used_ports.add(value)
            elif key == "url" and isinstance(value, str):
                port = _extract_port_from_url(value)
                if port is not None:
                    used_ports.add(port)
            else:
                extract_ports(value, used_ports)
    elif isinstance(config, list):
        for item in config:
            extract_ports(item, used_ports)
    return used_ports


def _extract_port_from_url(url: str) -> Optional[int]:
    """从 URL 中解析端口号"""
    try:
        parsed = urlparse(url)
        if parsed.port is not None:
            return parsed.port
    except Exception:
        pass
    return None


# ---------- 工具：从配置中提取已用名称 ----------
def extract_names(config, names_set=None):
    """递归提取所有 "name" 键的值"""
    if names_set is None:
        names_set = set()
    if isinstance(config, dict):
        for key, value in config.items():
            if key == "name" and isinstance(value, str):
                names_set.add(value)
            else:
                extract_names(value, names_set)
    elif isinstance(config, list):
        for item in config:
            extract_names(item, names_set)
    return names_set


# ---------- 端口占用检测 ----------
def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检测本机端口是否被占用"""
    if not (1 <= port <= 65535):
        raise ValueError("端口号必须在 1~65535 之间")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return False
        except OSError:
            return True


def 询问是否(提示: str, 默认: Optional[bool] = None) -> bool:
    """询问 y/n；默认为 None 时必须显式输入 y 或 n。"""
    suffix = {True: "（默认 y）", False: "（默认 n）"}.get(默认, "")
    while True:
        ans = input(f"{提示}{suffix}").strip().lower()
        if not ans:
            if 默认 is None:
                print("请输入 y 或 n。")
                continue
            return 默认
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("无效输入，请输入 y 或 n。")


def _windows监听进程pid(port: int, netstat_cmd: str = "netstat") -> set[str]:
    """解析 netstat -ano 输出，返回监听指定端口的 PID 集合。"""
    try:
        out = subprocess.run([netstat_cmd, "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        print(f"⚠️ 无法查询端口占用进程（{netstat_cmd}）：{e}")
        return set()
    pids: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        # 形如：TCP  127.0.0.1:6099  0.0.0.0:0  LISTENING  1234
        if (len(parts) >= 5 and parts[0] == "TCP"
                and parts[3].upper() == "LISTENING"
                and parts[1].rsplit(":", 1)[-1] == str(port)):
            pids.add(parts[4])
    return pids


def _结束占用端口的进程(port: int) -> bool:
    """尽力结束监听指定端口的进程，是否成功以端口复测为准。

    Windows 用 netstat+taskkill；Linux/WSL 用 fuser、lsof，
    WSL 下若两者不可用再回退到 Windows 互操作（netstat.exe/taskkill.exe）。
    """
    try:
        if sys.platform == "win32":
            pids = _windows监听进程pid(port)
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                               capture_output=True, text=True, timeout=20)
            return bool(pids)
        # Linux / WSL
        try:
            r = subprocess.run(["fuser", "-k", f"{port}/tcp"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                return True
        except FileNotFoundError:
            pass
        try:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                                 capture_output=True, text=True, timeout=20).stdout
            pids = {x.strip() for x in out.split() if x.strip()}
            for pid in pids:
                subprocess.run(["kill", "-9", pid], timeout=10)
            if pids:
                return True
        except FileNotFoundError:
            pass
        # WSL 互操作回退：尝试结束 Windows 侧进程
        if shutil.which("netstat.exe"):
            pids = _windows监听进程pid(port, netstat_cmd="netstat.exe")
            for pid in pids:
                subprocess.run(["taskkill.exe", "/PID", pid, "/T", "/F"],
                               capture_output=True, text=True, timeout=20)
            return bool(pids)
    except Exception as e:
        print(f"⚠️ 自动结束进程失败：{e}")
    return False


def 检测并处理运行中实例(名称: str, port: Optional[int]) -> None:
    """按配置端口检测实例是否正在运行；在运行则警告并询问是否结束。

    y → 尝试结束进程；n → 继续写入（有被运行实例覆盖修改的风险）。
    """
    if port is None:
        return
    try:
        running = is_port_in_use(port)
    except ValueError:
        return
    if not running:
        print(f"✅ {名称} 未在运行（端口 {port} 空闲）。")
        return
    print(f"\n⚠️ 检测到 {名称} 可能正在运行（端口 {port} 被占用）。")
    print(f"   请在 {名称} 未运行时继续，以防写入后被正在运行的实例覆盖修改。")
    if 询问是否(f"是否尝试结束 {名称} 的运行？(y/n)"):
        print(f"⏳ 正在尝试结束 {名称} …")
        _结束占用端口的进程(port)
        time.sleep(1)
        if not is_port_in_use(port):
            print(f"✅ {名称} 已停止。")
        else:
            print(f"⚠️ 端口 {port} 仍被占用，未能结束 {名称}，请手动关闭后重试。")
            print("   本次将继续写入，配置可能被运行中的实例覆盖。")
    else:
        print(f"➡️ 选择继续，{名称} 仍在运行，写入后配置可能被其覆盖。")


# ---------- NapCat 配置写入 ----------
def 写入NapCat反向WS配置(path: Path, port: int, client_name: str):
    """在 NapCat 的 onebot 配置中添加一条反向 websocket 客户端"""
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 确保 network 和 websocketClients 存在
    if "network" not in cfg or not isinstance(cfg.get("network"), dict):
        cfg["network"] = {}
    if "websocketClients" not in cfg["network"] or not isinstance(cfg["network"]["websocketClients"], list):
        cfg["network"]["websocketClients"] = []

    # 生成不重复的 name
    existing_names = extract_names(cfg["network"]["websocketClients"])
    final_name = client_name
    counter = 1
    while final_name in existing_names:
        final_name = f"{client_name}_{counter}"
        counter += 1

    new_client = {
        "enable": True,
        "name": final_name,
        "url": f"ws://127.0.0.1:{port}/ws",
        "reportSelfMessage": False,
        "messagePostFormat": "array",
        "token": "",
        "debug": False,
        "heartInterval": 5000,
        "reconnectInterval": 5000,
        "verifyCertificate": False
    }
    cfg["network"]["websocketClients"].append(new_client)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    print(f"✅ 已向 NapCat 配置添加反向 WS 客户端：name={final_name}, port={port}")
    return final_name


# ---------- ldmbot 配置相关 ----------
def find_ldmbot_config_dirs(home: Path) -> list[Path]:
    """搜索 home 下所有包含 ANCHOR 的路径，返回对应的项目根目录列表（跳过 venv 等无关目录）。"""
    anchor_parts = tuple(ANCHOR.split("/"))
    depth = len(anchor_parts)
    dynamic_dirs = set()
    for dirpath, dirnames, _filenames in os.walk(home, onerror=lambda _e: None):
        dirnames[:] = [d for d in dirnames if not _目录应跳过(d, 家目录模式=True)]
        try:
            parts = Path(dirpath).relative_to(home).parts
        except ValueError:
            continue
        if len(parts) >= depth and parts[-depth:] == anchor_parts:
            first_dir = home / parts[0] if len(parts) > depth else home
            dynamic_dirs.add(first_dir)
    return sorted(dynamic_dirs)


def 全局搜索ldmbot配置文件() -> list[Path]:
    """全盘搜索 ldmbot 的 data/cmd_config.json（要求同树内存在 ANCHOR）。"""
    anchor_parts = ANCHOR.split("/")

    def 匹配(dirpath: str, filenames: list[str]) -> list[Path]:
        if "cmd_config.json" not in filenames:
            return []
        base = Path(dirpath)
        if base.name != "data":
            return []
        # 项目根 = data 的父目录；要求其下存在 ANCHOR
        project_root = base.parent
        if not (project_root.joinpath(*anchor_parts)).exists():
            return []
        return [base / "cmd_config.json"]

    return _全局遍历收集(匹配, "正在全局搜索 ldmbot 配置 (cmd_config.json)…")


def get_ldmbot_config_paths(base_dirs: list[Path]) -> list[Path]:
    """返回所有可能存在的 ldmbot 配置文件路径"""
    return [d / CONFIG_REL for d in base_dirs]


def filter_existing(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """分离存在与不存在的配置文件路径"""
    valid, invalid = [], []
    for p in paths:
        (valid if p.exists() else invalid).append(p)
    return valid, invalid


def 获取ldmbot配置路径() -> Path:
    """查找 / 回退选择 ldmbot 配置文件，返回最终路径。"""
    print("\n--- 正在搜索 ldmbot 配置 ---")
    home = Path.home()
    dirs = find_ldmbot_config_dirs(home)
    valid_ldm: list[Path] = []

    if dirs:
        config_paths = get_ldmbot_config_paths(dirs)
        valid_ldm, invalid_ldm = filter_existing(config_paths)
        if invalid_ldm:
            print("⚠️ 以下 ldmbot 配置路径不存在，已忽略：")
            for p in invalid_ldm:
                print(f"   • {p}")

    if not valid_ldm:
        while True:
            choice = 询问未找到时的处理方式("ldmbot 的配置文件 (cmd_config.json)")
            if choice == "0":
                print("已退出。请先安装并启动一次 ldmbot 后再试。")
                sys.exit(1)
            if choice == "1":
                valid_ldm = 全局搜索ldmbot配置文件()
                if valid_ldm:
                    break
                print("全局搜索仍未找到，可改选手动输入路径，或再试一次。")
                continue
            # choice == "2"
            return 手动输入绝对路径("ldmbot 配置文件 (cmd_config.json)", 校验ldmbot配置文件)

    if len(valid_ldm) == 1:
        return valid_ldm[0]

    print("\n找到多个 ldmbot 配置文件：")
    for idx, p in enumerate(valid_ldm, 1):
        print(f"  [{idx}] {p}")
    choice = input("\n请选择编号（默认 1）：").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(valid_ldm):
        return valid_ldm[int(choice) - 1]
    return valid_ldm[0]


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def 检测ldmbot运行使用的端口号(arg: str | Path | dict) -> Optional[int]:
    """
    用来检测该配置文件的ldmbot运行使用的端口号
    :param arg: 文件路径或配置文件的字典
    :return: 使用的端口号
    """
    if isinstance(arg, (Path, str)):
        if not Path(arg).is_file():
            raise ValueError(f"文件路径不是有效的配置文件")
        try:
            with open(arg, "r", encoding="utf-8") as f:
                info = json.load(f)
        except json.decoder.JSONDecodeError as e:
            raise ValueError(f"配置文件损坏或不是有效的配置文件", e)
    elif isinstance(arg, dict):
        info = arg
    else:
        raise ValueError(f"不支持的参数类型：{type(arg)}，{arg}")

    try:
        return int(info['dashboard']["port"])
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"配置文件格式错误", e)

def 检测NapCat运行使用的端口号(path: str | Path) -> Optional[int]:
    """
    用来检测该配置文件的NapCat运行使用的端口号
    :param arg: 文件路径或配置文件的字典
    :return: 使用的端口号
    """
    if not Path(path).is_dir():
        raise ValueError("请传入NapCat配置文件的文件夹路径")
    config_path = Path(path) / "webui.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"配置文件不存在{config_path}")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except json.decoder.JSONDecodeError as e:
        raise ValueError(f"配置文件损坏或不是有效的配置文件", e)
    try:
        return int(info['port'])
    except (KeyError, ValueError, TypeError) as e:
        raise ValueError(f"配置文件格式错误", e)

def 写入快捷登录账号信息(path: str | Path, value:str|int) -> bool:
    """写入QQ号到自动登录，方便下次自动登录"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            info = json.load(f)
        info['autoLoginAccount'] = str(value)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=4)
        return True
    except Exception as e:
        raise RuntimeError(f"写入配置文件失败: {path}") from e

def 向ldmbot添加NapCat平台(config: dict, port: int, platform_id: str):
    """在 ldmbot 的 cmd_config.json 中添加 aiocqhttp 平台配置"""
    if "platform" not in config or not isinstance(config.get("platform"), list):
        config["platform"] = []

    platforms = config["platform"]

    # 生成不重复的 id
    existing_ids = set()
    for item in platforms:
        if isinstance(item, dict) and "id" in item:
            existing_ids.add(item["id"])

    final_id = platform_id
    counter = 1
    while final_id in existing_ids:
        final_id = f"{platform_id}_{counter}"
        counter += 1

    platforms.append({
        "id": final_id,
        "type": "aiocqhttp",
        "enable": True,
        "ws_reverse_host": "127.0.0.1",
        "ws_reverse_port": port,
        "ws_reverse_token": ""
    })
    return final_id


# ---------- 已有连接配置检测 / 覆盖 ----------
def 查找已有NapCat连接(clients: list) -> list[dict]:
    """找出 websocketClients 里由本脚本创建的 ldmbot 连接（name 为 ldmbot / ldmbot_N）。"""
    pattern = re.compile(rf"^{re.escape(DEFAULT_WS_CLIENT_NAME)}(_\d+)?$")
    return [
        c for c in clients
        if isinstance(c, dict) and pattern.match(str(c.get("name", "")))
    ]


def 查找已有ldmbot平台(platforms: list, qq: str) -> list[dict]:
    """找出 platform 里由本脚本创建的 NapCat 平台（id 为 NapCat_{qq} / NapCat_{qq}_N）。"""
    pattern = re.compile(rf"^{re.escape(DEFAULT_ID_PREFIX)}_{re.escape(qq)}(_\d+)?$")
    matched = []
    for p in platforms:
        if not isinstance(p, dict):
            continue
        if "type" in p and p.get("type") != "aiocqhttp":
            continue
        if pattern.match(str(p.get("id", ""))):
            matched.append(p)
    return matched


def 已有连接的一致端口(existing_napcat: list[dict], existing_ldm: list[dict]) -> Optional[int]:
    """若已有连接两侧端口一致则返回该端口，否则返回 None。"""
    ports = set()
    for c in existing_napcat:
        p = _extract_port_from_url(str(c.get("url", "")))
        if p is not None:
            ports.add(p)
    for plat in existing_ldm:
        pv = plat.get("ws_reverse_port")
        if isinstance(pv, int):
            ports.add(pv)
    if len(ports) == 1:
        return ports.pop()
    return None


def 询问覆盖或新增() -> str:
    """检测到已有连接配置时，询问是覆盖还是另新增。返回 'overwrite' / 'add' / 'exit'。"""
    print("\n请选择处理方式：")
    print("  [1] 覆盖已有配置（更新为新端口）")
    print("  [2] 保留已有配置，另新增一条")
    print("  [0] 退出，不做任何修改")
    while True:
        choice = input("请输入选项（默认 1）：").strip() or "1"
        if choice == "0":
            return "exit"
        if choice == "1":
            return "overwrite"
        if choice == "2":
            return "add"
        print("无效选项，请输入 0、1 或 2。")


# ---------- 主流程 ----------
def main():
    print("=" * 50)
    print("🔧 NapCat ↔ ldmbot 自动连接配置工具")
    print("=" * 50)

    # 1. 查找 NapCat 配置
    target_napcat = 获取NapCat配置路径()
    qq = 从NapCat路径提取QQ(target_napcat)
    print(f"\n📌 使用 NapCat 配置：{target_napcat}")
    print(f"   绑定 QQ：{qq}")

    # 2. 查找 ldmbot 配置
    target_ldm = 获取ldmbot配置路径()
    print(f"\n📌 使用 ldmbot 配置：{target_ldm}")

    # 3. 收集已占用端口、检测已有连接配置
    print("\n--- 正在检测端口占用 ---")
    napcat_cfg = load_json(target_napcat)
    ldm_cfg = load_json(target_ldm)

    used_ports = set()
    # NapCat 中 network.websocketClients 的端口
    network = napcat_cfg.get("network", {})
    clients = network.get("websocketClients", [])
    if not isinstance(clients, list):
        clients = []
    for client in clients:
        if isinstance(client, dict) and "url" in client:
            p = _extract_port_from_url(client["url"])
            if p:
                used_ports.add(p)
    # ldmbot 中平台的 ws_reverse_port
    platforms = ldm_cfg.get("platform", [])
    if not isinstance(platforms, list):
        platforms = []
    for plat in platforms:
        if isinstance(plat, dict) and "ws_reverse_port" in plat:
            port_val = plat["ws_reverse_port"]
            if isinstance(port_val, int):
                used_ports.add(port_val)

    # 已有本脚本创建的连接配置时，询问覆盖还是另新增（不再自动追加）
    existing_napcat = 查找已有NapCat连接(clients)
    existing_ldm = 查找已有ldmbot平台(platforms, qq)
    mode = "add"
    if existing_napcat or existing_ldm:
        print("\n--- 检测到已有连接配置 ---")
        for c in existing_napcat:
            print(f"   • NapCat 反向 WS：name={c.get('name')}, url={c.get('url')}")
        for plat in existing_ldm:
            print(f"   • ldmbot 平台：id={plat.get('id')}, port={plat.get('ws_reverse_port')}")
        mode = 询问覆盖或新增()
        if mode == "exit":
            print("已退出，未做任何修改。")
            sys.exit(0)

    # 选择端口：覆盖时若原端口两侧一致且仍空闲则沿用，否则另找空闲端口
    port: Optional[int] = None
    if mode == "overwrite":
        old_port = 已有连接的一致端口(existing_napcat, existing_ldm)
        if old_port is not None and 1 <= old_port <= 65535 and not is_port_in_use(old_port):
            port = old_port
    if port is None:
        port = DEFAULT_PORT
        while port in used_ports or is_port_in_use(port):
            port += 1
    print(f"🔌 选定通信端口：{port}")

    # 4. 生成名称
    # NapCat 客户端名称不带 QQ，因为配置文件已区分 QQ
    ws_client_name = DEFAULT_WS_CLIENT_NAME
    # ldmbot 平台 ID 可以带上 QQ 便于区分（按需，这里仍带）
    ldm_platform_id = f"{DEFAULT_ID_PREFIX}_{qq}"

    # 5. 检测 NapCat / ldmbot 是否正在运行，正在运行则警告并询问是否结束
    print("\n--- 正在检测运行状态 ---")
    try:
        napcat_run_port = 检测NapCat运行使用的端口号(target_napcat.parent)
    except (ValueError, OSError) as e:
        print(f"ℹ️ 跳过 NapCat 运行检测：{e.args[0] if e.args else e}")
        napcat_run_port = None
    检测并处理运行中实例("NapCat", napcat_run_port)

    try:
        ldm_run_port = 检测ldmbot运行使用的端口号(target_ldm)
    except (ValueError, OSError) as e:
        print(f"ℹ️ 跳过 ldmbot 运行检测：{e.args[0] if e.args else e}")
        ldm_run_port = None
    检测并处理运行中实例("ldmbot", ldm_run_port)

    # 6. 写入配置
    print("\n--- 正在写入配置 ---")
    if mode == "overwrite":
        # 覆盖：保留原 name/id，只更新地址/端口/启用状态
        if existing_napcat:
            for c in existing_napcat:
                c["enable"] = True
                c["url"] = f"ws://127.0.0.1:{port}/ws"
            save_json(target_napcat, napcat_cfg)
            ws_client_name = "、".join(str(c.get("name")) for c in existing_napcat)
            print(f"✅ 已覆盖 NapCat 反向 WS 客户端（{len(existing_napcat)} 条），端口={port}")
        else:
            写入NapCat反向WS配置(target_napcat, port, ws_client_name)

        if existing_ldm:
            for plat in existing_ldm:
                plat["enable"] = True
                plat["ws_reverse_host"] = "127.0.0.1"
                plat["ws_reverse_port"] = port
            save_json(target_ldm, ldm_cfg)
            final_ldm_id = "、".join(str(plat.get("id")) for plat in existing_ldm)
            print(f"✅ 已覆盖 ldmbot 平台配置（{len(existing_ldm)} 条），端口={port}")
        else:
            final_ldm_id = 向ldmbot添加NapCat平台(ldm_cfg, port, ldm_platform_id)
            save_json(target_ldm, ldm_cfg)
            print(f"✅ 已向 ldmbot 添加平台配置：id={final_ldm_id}, port={port}")
    else:
        写入NapCat反向WS配置(target_napcat, port, ws_client_name)
        final_ldm_id = 向ldmbot添加NapCat平台(ldm_cfg, port, ldm_platform_id)
        save_json(target_ldm, ldm_cfg)
        print(f"✅ 已向 ldmbot 添加平台配置：id={final_ldm_id}, port={port}")

    # 7. 询问是否写入 NapCat 自动登录（快捷登录）账号
    webui_path = target_napcat.parent / "webui.json"
    if not webui_path.is_file():
        print("\nℹ️ 未找到 webui.json，跳过 NapCat 自动登录账号写入。")
    elif 询问是否(f"\n是否将当前账号 {qq} 添加到 NapCat 自动登录？(y/n)", 默认=False):
        try:
            写入快捷登录账号信息(webui_path, qq)
            print(f"✅ 已写入 NapCat 自动登录账号：{qq}（{webui_path}）")
        except Exception as e:
            print(f"⚠️ 写入自动登录账号失败：{e}")

    print("\n" + "=" * 50)
    print("🎉 配置完成！")
    print(f"   NapCat 反向 WS 客户端：{ws_client_name} (端口 {port})")
    print(f"   ldmbot 平台 ID：{final_ldm_id} (端口 {port})")
    print("请分别重启 NapCat 和 ldmbot 以使设置生效。")
    print("=" * 50)


if __name__ == "__main__":
    main()
LDMBOT_NAPCAT_CONFIG_PY

    set +e
    python3 "$tmp_py"
    local rc=$?
    set -e
    rm -f "$tmp_py"
    if [[ $rc -ne 0 ]]; then
        log "配置脚本执行失败（退出码 $rc）。"
        exit $rc
    fi
}


show_menu() {
    if [[ -n "$DIRECT_OPTION" ]]; then
        echo "$DIRECT_OPTION"   # -数字 直接选择
        return
    fi
    if $AUTO_YES; then
        echo "y"   # 非交互默认：-y 自动检测（完整则启动，不完整则安装）
        return
    fi
    cat >&2 <<EOF

${YELLOW}请选择操作：${NC}
----------------------------------------
${GREEN}1) 安装 ldmbot${NC}
${GREEN}2) 启动 ldmbot（使用现有文件）${NC}
${GREEN}3) 更新 ldmbot${NC}
${RED}4) 退出${NC}
${GREEN}5) 从官方迁移（替换官方源码）${NC}
${GREEN}6) 安装 NapCat${NC}
${GREEN}7) 配置 ldmbot 与 NapCat 连接${NC}
----------------------------------------
EOF
    echo >&2
    read -p "${YELLOW}请选择 [1]: ${NC}" choice < /dev/tty
    choice=${choice:-1}
    echo "$choice"
}

choice=$(show_menu)
case $choice in
    1)
        # 安装：只负责安装（含依赖同步），不启动
        install_ldmbot
        ;;
    2)
        log "直接启动..."
        # 启动：只启动不自动安装；当前目录没有时询问是否搜索其他安装目录
        if is_project_root "$PWD"; then
            start_ldmbot "$PWD"
        elif is_project_root "$PWD/ldmbot"; then
            start_ldmbot "$PWD/ldmbot"
        elif $AUTO_YES || ask_yes_no "当前目录未找到 ldmbot 项目，是否搜索其他安装目录？"; then
            if find_project_dir; then
                start_ldmbot "$PROJECT_DIR"
            else
                log "未找到 ldmbot 项目目录，无法启动。"
                exit 1
            fi
        else
            log "已取消。"
            exit 0
        fi
        ;;
    3)
        update_ldmbot
        ;;
    y)
        AUTO_YES=true   # 菜单选 y 等同 -y 参数：全自动，无任何询问
        # -y 自动模式：只检测当前目录，完整（含 .venv）则直接启动（不判断版本），不完整则安装
        if is_complete_project "$PWD"; then
            log "检测到当前目录即完整 ldmbot 项目，直接启动。"
            start_ldmbot "$PWD"
        elif is_complete_project "$PWD/ldmbot"; then
            log "检测到当前目录下的完整 ldmbot 项目，直接启动。"
            start_ldmbot "$PWD/ldmbot"
        else
            log "未检测到完整 ldmbot 项目（缺少 main.py、astrbot 或 .venv），开始安装..."
            install_ldmbot
        fi
        ;;
    4)
        log "已退出。"
        exit 0
        ;;
    5)
        migrate_official
        exit 0
        ;;
    6)
        install_napcat
        ;;
    7)
        configure_napcat_connection
        ;;
    *)
        log "无效选项，已退出。"
        exit 1
        ;;
esac

exit 0