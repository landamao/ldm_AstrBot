#!/bin/bash
# ldm_AstrBot 安装 / 启动脚本
# 用法：
#   1) 已克隆仓库：在仓库根目录执行 bash scripts/install_or_run.sh
#   2) 也可配合 release 中的源码包使用

set -euo pipefail

PROXY_PORTS=(7890 7897)
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
NC=$'\033[0m'

prompt_continue() {
    local msg="${1:-按回车键继续，或按 Ctrl+C 取消...}"
    read -r -p "$msg"
}

ask_yes_no() {
    local prompt="$1"
    read -r -p "${prompt} [Y/n] " answer
    [[ "$answer" =~ ^[Nn] ]] && return 1 || return 0
}

test_proxy() {
    local port=$1
    curl -x "http://127.0.0.1:${port}" -s --connect-timeout 5 --max-time 10 \
        "http://httpbin.org/ip" > /dev/null 2>&1
}

install_python3_12() {
    echo "正在尝试自动安装 Python 3.12..."
    if command -v apt-get &>/dev/null; then
        sudo add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
        sudo apt-get update -qq
        sudo apt-get install -y python3.12 python3.12-venv python3.12-distutils || \
          sudo apt-get install -y python3.12 python3.12-venv
    elif command -v yum &>/dev/null; then
        sudo yum install -y python3.12 || true
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3.12 || true
    elif command -v brew &>/dev/null; then
        brew install python@3.12
    else
        echo -e "${RED}无法自动安装 Python，请手动安装 Python 3.12+。${NC}"
        exit 1
    fi
}

# 定位仓库根目录（脚本在 scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

echo -e "${GREEN}项目目录：${ROOT_DIR}${NC}"

# 1. 代理检测
echo -e "\n${GREEN}[步骤 1/4] 检测代理端口...${NC}\n"
PROXY_PORT=""
for port in "${PROXY_PORTS[@]}"; do
    if test_proxy "$port"; then
        PROXY_PORT="$port"
        break
    fi
done

if [[ -n "$PROXY_PORT" ]]; then
    echo -e "${GREEN}检测到可用代理端口：${PROXY_PORT}${NC}"
    if ask_yes_no "是否启用代理？"; then
        export http_proxy="http://127.0.0.1:${PROXY_PORT}"
        export https_proxy="http://127.0.0.1:${PROXY_PORT}"
        export all_proxy="http://127.0.0.1:${PROXY_PORT}"
        echo -e "${GREEN}已启用代理。${NC}\n"
    else
        echo -e "${YELLOW}已选择不启用代理。${NC}\n"
    fi
else
    echo -e "${YELLOW}警告：未检测到可用代理，下载可能较慢。${NC}"
fi

# 2. uv 检查
echo -e "\n${GREEN}[步骤 2/4] 检查 uv 包管理器...${NC}\n"
USE_UV=false
if command -v uv &>/dev/null; then
    USE_UV=true
    echo -e "${GREEN}已找到 uv：$(command -v uv)${NC}\n"
else
    echo -e "${YELLOW}未找到 uv。${NC}"
    if ask_yes_no "是否自动安装 uv？"; then
        if curl -LsSf https://astral.sh/uv/install.sh | sh; then
            export PATH="$HOME/.local/bin:$PATH"
            command -v uv &>/dev/null && USE_UV=true && echo -e "${GREEN}uv 安装成功。${NC}\n" || echo -e "${YELLOW}uv 不在 PATH 中。${NC}\n"
        else
            echo -e "${YELLOW}uv 安装失败，将回退到 pip。${NC}\n"
        fi
    fi
fi

# 3. uv sync / 启动
if $USE_UV; then
    echo -e "\n${GREEN}[步骤 3/4] uv sync 并启动...${NC}\n"
    if uv sync; then
        echo -e "${GREEN}通过 uv 启动...${NC}\n"
        exec uv run main.py
    fi
    echo -e "${YELLOW}uv sync 失败，回退到 pip。${NC}"
fi

# 4. 回退：pip + Python ≥ 3.12
echo -e "\n${GREEN}[步骤 4/4] 准备 pip 环境...${NC}\n"

PYTHON_CMD=""
if command -v python3.12 &>/dev/null; then
    PYTHON_CMD="python3.12"
elif command -v python3 &>/dev/null; then
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' && PYTHON_CMD="python3" || true
fi

if [[ -z "$PYTHON_CMD" ]]; then
    echo -e "${YELLOW}未找到 Python 3.12+。${NC}"
    if ask_yes_no "是否尝试自动安装 Python 3.12？"; then
        install_python3_12
        command -v python3.12 &>/dev/null && PYTHON_CMD="python3.12" || {
            command -v python3 &>/dev/null && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' && PYTHON_CMD="python3" || true
        }
    fi
    [[ -z "$PYTHON_CMD" ]] && { echo -e "${RED}无法获取 Python 3.12，中止。${NC}"; exit 1; }
fi

echo -e "${GREEN}Python 解释器：${PYTHON_CMD}${NC}\n"
$PYTHON_CMD -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip
if [[ -f requirements.txt ]]; then
    pip install -r requirements.txt
fi
pip install -e .

echo -e "\n${GREEN}[启动] 正在启动 ldm_AstrBot...${NC}\n"
exec python main.py
