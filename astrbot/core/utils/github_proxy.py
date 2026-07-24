"""GitHub 加速（URL 前缀镜像）读写辅助。

服务端配置键：``github_proxy``（存于 data/cmd_config.json）。
WebUI 网络设置里的 ProxySelector 会同步写入；
聊天指令 /plugin update、/plugin get 以及后端安装/更新在未显式传 proxy 时回落此值。
"""

from __future__ import annotations

from typing import Any

from astrbot.core import logger


def normalize_github_proxy(proxy: str | None) -> str:
    """规范化加速地址：去空白、去尾斜杠；空则返回空串。"""
    if not proxy:
        return ""
    return str(proxy).strip().rstrip("/")


def get_configured_github_proxy(config: Any | None = None) -> str:
    """从配置对象读取 github_proxy；config 为 None 时读全局 astrbot_config。"""
    if config is None:
        from astrbot.core import astrbot_config

        config = astrbot_config
    if config is None:
        return ""
    try:
        value = config.get("github_proxy", "")
    except Exception:
        value = getattr(config, "github_proxy", "") if config else ""
    return normalize_github_proxy(value)


def resolve_github_proxy(
    proxy: str | None = None,
    config: Any | None = None,
) -> str:
    """优先使用调用方显式传入的 proxy；为空时回落服务端 github_proxy。"""
    explicit = normalize_github_proxy(proxy)
    if explicit:
        return explicit
    return get_configured_github_proxy(config)


def log_github_proxy_usage(
    proxy: str | None,
    *,
    action: str,
    target: str = "",
    source: str = "",
) -> str:
    """记录 GitHub 加速使用情况（中文 INFO），返回规范化后的 proxy。

    Args:
        proxy: 已解析的加速地址（可为空）。
        action: 动作描述，如「安装插件」「更新插件」「下载更新包」。
        target: 目标名称/仓库。
        source: 来源说明，如「请求参数」「服务端配置」「指令」。
    """
    normalized = normalize_github_proxy(proxy)
    target_part = f" 目标={target}" if target else ""
    source_part = f" 来源={source}" if source else ""
    if normalized:
        logger.info(
            f"GitHub 加速: 使用中 动作={action}{target_part} "
            f"代理={normalized}{source_part}"
        )
    else:
        logger.info(
            f"GitHub 加速: 未使用 动作={action}{target_part}{source_part}"
        )
    return normalized
