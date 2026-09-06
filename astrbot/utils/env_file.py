"""启动期 .env 文件支持：读取环境变量，缺失时自动写入示例。

与 main.py 同目录（项目根）的 .env 必须在启动最早期加载，早于任何
模块读取环境变量（astrbot.core 在导入期就会读取 LDMBOT_DATA_DIR /
LDMBOT_DEMO_MODE 等变量）。

本模块只允许依赖标准库与 python-dotenv，禁止 import astrbot.core，
否则会先于 .env 加载触发 core 初始化。
"""

import unicodedata
from pathlib import Path

from dotenv import load_dotenv

# 示例模板条目: (变量=<占位值>, 说明)。全部保持注释状态，取消行首 "# " 才生效。
# 条目与 main.py --help 中的环境变量清单保持一致。
_ENV_EXAMPLE_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "路径与运行模式",
        [
            ("LDMBOT_DATA_DIR=<路径>", "直接指定 data 目录路径"),
            ("LDMBOT_ROOT=<路径>", "根目录，data 目录 = $LDMBOT_ROOT/data（LDMBOT_DATA_DIR 优先级更高）"),
            ("LDMBOT_CLI=1", "标记由 CLI 启动（内部使用）"),
            ("LDMBOT_RELOAD=1", "启用插件热重载"),
            ("LDMBOT_LAUNCHER=1", "标记由 Launcher 启动（内部使用）"),
            ("LDMBOT_WEBUI_DIR=<路径>", "自定义 WebUI 静态文件目录"),
        ],
    ),
    (
        "Dashboard / WebUI",
        [
            ("LDMBOT_DASHBOARD_PORT=<端口>", "WebUI 监听端口（默认 6185）"),
            ("LDMBOT_DASHBOARD_HOST=<地址>", "WebUI 监听地址（默认 0.0.0.0）"),
            ("LDMBOT_DASHBOARD_SSL_ENABLE=1", "启用 HTTPS"),
            ("LDMBOT_DASHBOARD_SSL_CERT=<路径>", "SSL 证书文件路径"),
            ("LDMBOT_DASHBOARD_SSL_KEY=<路径>", "SSL 私钥文件路径"),
            ("LDMBOT_DASHBOARD_SSL_CA_CERTS=<路径>", "SSL CA 证书路径"),
            ("LDMBOT_DASHBOARD_INITIAL_PASSWORD=<密码>", "重置密码使用的新密码（配合下一项使用，不设默认 \"ldm\"）"),
            ("LDMBOT_RESET_DASHBOARD_PASSWORD=1", "启动时触发重置 Dashboard 密码（配合上一项使用）"),
            ("LDMBOT_DASHBOARD_SKIP_DEFAULT_PASSWORD_AUTH=1", "跳过默认密码认证（仅限本地）"),
            ("LDMBOT_TEST_MODE=true", "测试模式（跳过部分初始化）"),
        ],
    ),
    (
        "Desktop 客户端",
        [
            ("LDMBOT_DESKTOP_CLIENT=1", "打包 Desktop 运行时（内部使用）"),
            ("LDMBOT_DESKTOP_MANAGED=1", "Desktop 托管模式（内部使用）"),
            ("LDMBOT_DESKTOP_CORE_LOCK_PATH=<路径>", "Desktop 核心锁文件路径（内部使用）"),
        ],
    ),
    (
        "MCP",
        [
            ("LDMBOT_MCP_INIT_TIMEOUT=<秒>", "MCP 初始化超时秒数（默认 180，上限 300）"),
            ("LDMBOT_MCP_ENABLE_TIMEOUT=<秒>", "MCP 动态启用超时秒数（默认 180）"),
            ("LDMBOT_MCP_STDIO_ALLOWED_COMMANDS=<cmd1,cmd2,...>", "MCP stdio 启动命令白名单（逗号分隔）"),
        ],
    ),
    (
        "启动行为",
        [
            ("LDMBOT_NO_BANNER=1", "跳过启动横幅动画"),
            ("LDMBOT_PAUSE_CONSOLE=1", "暂停控制台日志输出（内部使用）"),
        ],
    ),
    (
        "更新器",
        [
            ("LDMBOT_REPO_OWNER=<所有者>", "GitHub 仓库所有者（默认 landamao）"),
            ("LDMBOT_REPO_NAME=<仓库名>", "GitHub 仓库名（默认 ldm_AstrBot）"),
            ("LDMBOT_UPDATE_CACHE_TTL=<秒>", "远端版本信息缓存秒数（默认 300）"),
            ("LDMBOT_GITHUB_TOKEN=<token>", "GitHub API Token，提高限流配额"),
            ("LDMBOT_CORE_PACKAGE_BASE_URL=<URL>", "核心包下载基础 URL"),
        ],
    ),
    (
        "Provider / 代理",
        [
            ("LDMBOT_DASHSCOPE_API_KEY=<key>", "阿里云百炼 API Key（Embedding/Rerank 回退）"),
            ("https_proxy=<URL>", "标准 HTTP(S) 代理（Provider 默认读取）"),
            ("http_proxy=<URL>", "标准 HTTP 代理（Provider 默认读取）"),
        ],
    ),
    (
        "第三方平台",
        [
            ("LDMBOT_COZE_API_KEY=<key>", "Coze API 密钥"),
            ("LDMBOT_COZE_BOT_ID=<id>", "Coze Bot ID"),
            ("LDMBOT_DINGTALK_REGISTRATION_BASE_URL=<URL>", "钉钉注册基础 URL"),
            ("LDMBOT_DINGTALK_REGISTRATION_SOURCE=<来源>", "钉钉注册来源标识"),
        ],
    ),
    (
        "其他",
        [
            ("LDMBOT_DEMO_MODE=true", "演示模式"),
            ("LDMBOT_NO_PLUGINS=true", "跳过第三方插件加载（仅内置插件，可用于排除插件故障调试）"),
            ("LDMBOT_BAY_DATA_DIR=<路径>", "Bay 凭据目录"),
            ("LDMBOT_DISABLE_METRICS=1", "禁用指标上传"),
            ("LDMBOT_PLATFORM_STATS_INVALID_COUNT_WARN_LIMIT=<数量>", "备份导入失效平台告警阈值（默认 5）"),
            ("LDMBOT_BUILD_DASHBOARD=1", "构建时编译 Dashboard 前端"),
        ],
    ),
]


def _display_width(text: str) -> int:
    """按终端显示宽度计算文本宽度（中日韩全角字符按 2 列计）。"""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


def _render_env_example() -> str:
    """渲染 .env 示例模板，同一分节内说明文字按显示宽度对齐。"""
    lines = [
        "# ============================================================",
        "# LDMBot 环境变量配置（示例）",
        "#",
        "# 使用说明:",
        "#   1. 本文件与 main.py 同目录，启动时自动读取",
        '#   2. 去掉行首的 "# " 即可启用对应变量',
        "#   3. 修改后重启 LDMBot 生效",
        "#   4. 系统中已存在的同名环境变量优先于本文件",
        "#   5. 路径建议写成正斜杠形式（如 D:/ldmbot/data）",
        "# ============================================================",
        "",
    ]
    for title, entries in _ENV_EXAMPLE_SECTIONS:
        align_width = max(_display_width(f"# {name}") for name, _ in entries)
        lines.append(f"# ---- {title} ----")
        for name, desc in entries:
            line = f"# {name}"
            pad = " " * (align_width - _display_width(line) + 2)
            lines.append(f"{line}{pad}{desc}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _ensure_env_example(env_path: Path) -> bool:
    """不存在 .env 时写入示例模板，返回是否实际创建。"""
    if env_path.exists():
        return False
    try:
        env_path.write_text(_render_env_example(), encoding="utf-8")
        return True
    except OSError as exc:
        print(f"警告: 无法写入 .env 示例文件: {exc}")
        return False


def _load_env_file(env_path: Path) -> None:
    """加载 .env 中的环境变量，已存在的真实环境变量优先。"""
    if not env_path.exists():
        return
    try:
        load_dotenv(env_path, override=False)
    except Exception as exc:
        print(f"警告: 读取 .env 文件失败: {exc}")


def bootstrap_env(project_root: str | Path) -> None:
    """加载与 main.py 同目录的 .env；文件缺失时自动生成一份示例。"""
    env_path = Path(project_root) / ".env"
    created = _ensure_env_example(env_path)
    _load_env_file(env_path)
    if created:
        print(f"已生成环境变量示例文件: {env_path}（取消注释后重启生效）")
