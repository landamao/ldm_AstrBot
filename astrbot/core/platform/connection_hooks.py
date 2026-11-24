"""平台适配器连接状态钩子的派发。

`on_platform_loaded` 在适配器实例加载完成时就触发，此时平台侧可能还没有连上
（例如 OneBot 反向 WebSocket 尚未建立）。本模块负责派发 `on_platform_connected`：
由适配器在**真正收到平台侧的连接事件**后调用 `Platform.notify_connected()` 触发。
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from astrbot.core import logger
from astrbot.core.star.star_handler import EventType, star_handlers_registry, star_map

if TYPE_CHECKING:
    from .platform import Platform


async def dispatch_platform_connected(
    platform: Platform,
    platform_self_id: str | None = None,
) -> None:
    """依次执行所有 on_platform_connected 钩子。

    单个钩子抛错只记录日志，不影响其它钩子，也不影响适配器本身的连接流程。
    """
    handlers = star_handlers_registry.get_handlers_by_event_type(
        EventType.OnPlatformConnectedEvent,
    )
    platform_name = platform.meta().name
    for handler in handlers:
        want_platform = handler.extras_configs.get("platform_name")
        if want_platform and want_platform != platform_name:
            continue

        plugin = star_map.get(handler.handler_module_path)
        plugin_label = plugin.name if plugin else handler.handler_module_path
        try:
            logger.info(
                f"钩子(on_platform_connected) -> {plugin_label} - {handler.handler_name}",
            )
            await handler.handler(platform, platform_self_id)
        except Exception:
            logger.error(traceback.format_exc())
