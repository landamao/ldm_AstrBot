"""「关于」信息收集：系统概况 + ldm 运行信息。

供 WebUI 关于弹窗（/api/v1/stats/about）与内置指令 /about 共用，
只依赖标准库与 astrbot.core.utils 的路径/版本工具。
"""

from __future__ import annotations

import os
import platform
import sys
import time
from datetime import datetime

import psutil

from astrbot.core.config import VERSION
from astrbot.core.dashboard_assets import resolve_dashboard_dist
from astrbot.core.utils.astrbot_path import (
    get_astrbot_backups_path,
    get_astrbot_data_path,
    get_astrbot_plugin_data_path,
    get_astrbot_plugin_path,
)
from astrbot.core.utils.io import get_dashboard_version
from astrbot.core.utils.update_rollback import get_rollback_dir

PROJECT_URL = "https://github.com/landamao/ldm_AstrBot"
AUTHOR_URL = "https://github.com/landamao"


def _read_linux_pretty_name() -> str | None:
    """读取 /etc/os-release 中的 PRETTY_NAME（仅 Linux）。"""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def _describe_os() -> tuple[str, str]:
    """返回 (系统展示名, 版本号)。

    Windows 11 的部分 Python 版本 platform.release() 仍返回 "10"，
    按 build 号（>= 22000）修正；Linux 优先用发行版名称，版本用内核号。
    """
    system = platform.system() or "Unknown"
    if system == "Windows":
        version = platform.version()
        release = platform.release()
        try:
            build = int(version.split(".")[-1])
            if build >= 22000 and release in ("10", ""):
                release = "11"
        except (ValueError, AttributeError):
            pass
        return f"Windows {release}".strip(), version
    if system == "Linux":
        return _read_linux_pretty_name() or "Linux", platform.release()
    return system, platform.release()


def _startup_command() -> str:
    """当前进程的启动命令（解释器 + 参数）。"""
    parts = [sys.executable, *(sys.argv or [])]
    return " ".join(p for p in parts if p).strip()


def _format_duration(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}天{hours}小时{minutes}分"
    if hours:
        return f"{hours}小时{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _startup_time_info() -> tuple[str, str]:
    """当前进程的启动时间（本地时区）与已运行时长。"""
    try:
        create_time = psutil.Process(os.getpid()).create_time()
    except Exception:
        return ("未知", "未知")
    uptime = max(0, int(time.time() - create_time))
    return (
        datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S"),
        _format_duration(uptime),
    )


def _global_proxy_status() -> str:
    """全局代理使用情况（以当前进程环境变量为准）。

    代理规则仅在启动时应用一次（core_lifecycle.__init__）：
    1. 配置了 http_proxy → 写入环境变量；
    2. 未配置且开启「未配置代理时清除系统代理」→ 清除环境变量，
       并设 no_proxy=localhost,127.0.0.1,::1；
    3. 否则保留启动前 shell 导出的代理变量。

    WebUI 改代理配置只落盘，当前进程环境不变，重启才重新应用，
    故配置与进程环境不一致时如实标注「重启后生效」。
    """
    from astrbot.core import astrbot_config

    配置值 = (astrbot_config.get("http_proxy", "") or "").strip()
    清除开关 = bool(astrbot_config.get("clear_system_proxy_when_unset", False))
    环境值 = (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
        or ""
    ).strip()

    if 环境值:
        if 配置值 and 配置值 == 环境值:
            return f"{环境值}（来自配置文件）"
        if 配置值:
            return f"{环境值}（来自启动环境变量；配置文件为 {配置值}，重启后生效）"
        if 清除开关:
            return f"{环境值}（来自启动环境变量；重启后将清除）"
        return f"{环境值}（来自启动环境变量）"
    if 配置值:
        return f"未使用代理，直连（配置文件为 {配置值}，重启后生效）"
    if os.environ.get("no_proxy") == "localhost,127.0.0.1,::1":
        # 启动时清除行为留下的特征值，说明当前进程的代理变量已被清空
        return "已清除系统代理，localhost 直连"
    return "未使用代理，直连"


async def get_about_info(
    dashboard_static_folder: str | None = None,
) -> dict:
    """收集关于信息。

    Args:
        dashboard_static_folder: 面板实际在用的 WebUI dist 目录；
            为空时委托 resolve_dashboard_dist 按统一优先级解析，
            与 WebUI 服务端口径一致。
    """
    os_name, os_version = _describe_os()
    try:
        webui_version = await get_dashboard_version()
    except Exception:
        webui_version = None
    startup_time, uptime = _startup_time_info()

    webui_dir = dashboard_static_folder or resolve_dashboard_dist()

    return {
        "system": {
            "os": os_name,
            "platform": platform.system() or "Unknown",
            "version": os_version,
            "arch": platform.machine(),
            "python": platform.python_version(),
        },
        "ldm": {
            "version": VERSION,
            "webui_version": webui_version,
            "startup_command": _startup_command(),
            "startup_dir": os.getcwd(),
            "startup_time": startup_time,
            "uptime": uptime,
            "global_proxy": _global_proxy_status(),
            "data_dir": get_astrbot_data_path(),
            "webui_dir": os.path.realpath(webui_dir) if webui_dir else None,
            "plugin_dir": get_astrbot_plugin_path(),
            "plugin_data_dir": get_astrbot_plugin_data_path(),
            "backup_dir": get_astrbot_backups_path(),
            "rollback_dir": str(get_rollback_dir()),
        },
        "project_url": PROJECT_URL,
        "author_url": AUTHOR_URL,
    }
