from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.platform import AstrMessageEvent


class ActiveEventRegistry:
    """维护 unified_msg_origin 到活跃事件的映射。

    用于在 reset 等场景下终止该会话正在处理的事件。
    """

    def __init__(self) -> None:
        self._events: dict[str, set[AstrMessageEvent]] = defaultdict(set)

    def register(self, event: AstrMessageEvent) -> None:
        self._events[event.unified_msg_origin].add(event)

    def unregister(self, event: AstrMessageEvent) -> None:
        umo = event.unified_msg_origin
        self._events[umo].discard(event)
        if not self._events[umo]:
            del self._events[umo]

    def has_active(self, umo: str, exclude: AstrMessageEvent | None = None) -> bool:
        """当前 UMO 是否仍有活跃事件。"""
        for event in list(self._events.get(umo, [])):
            if event is not exclude:
                return True
        return False

    def count(self, umo: str, exclude: AstrMessageEvent | None = None) -> int:
        """当前 UMO 活跃事件数量。"""
        return sum(1 for event in list(self._events.get(umo, [])) if event is not exclude)

    def stop_all(
        self,
        umo: str,
        exclude: AstrMessageEvent | None = None,
    ) -> int:
        """终止指定 UMO 的所有活跃事件。

        Args:
            umo: 统一消息来源标识符。
            exclude: 需要排除的事件（通常是发起 reset 的事件本身）。

        Returns:
            被终止的事件数量。
        """
        count = 0
        for event in list(self._events.get(umo, [])):
            if event is not exclude:
                event.stop_event()
                count += 1
        return count

    def request_agent_stop_all(
        self,
        umo: str,
        exclude: AstrMessageEvent | None = None,
        *,
        extra_updates: dict | None = None,
    ) -> int:
        """请求停止指定 UMO 的所有活跃事件中的 Agent 运行。

        与 stop_all 不同，这里不会调用 event.stop_event()，
        因此不会中断事件传播，后续流程（如历史记录保存）仍可继续。

        Args:
            extra_updates: 可选，额外写入被停止事件的 extras。
        """
        count = 0
        for event in list(self._events.get(umo, [])):
            if event is not exclude:
                event.set_extra("agent_stop_requested", True)
                if extra_updates:
                    for key, value in extra_updates.items():
                        event.set_extra(key, value)
                count += 1
        return count

    async def wait_until_idle(
        self,
        umo: str,
        *,
        exclude: AstrMessageEvent | None = None,
        timeout: float = 8.0,
        poll_interval: float = 0.1,
    ) -> bool:
        """等待指定 UMO 上的其他活跃事件结束。

        Returns:
            True 表示在超时前已空闲；False 表示超时仍有活跃事件。
        """
        if timeout <= 0:
            return not self.has_active(umo, exclude=exclude)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.has_active(umo, exclude=exclude):
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(poll_interval)
        return True


active_event_registry = ActiveEventRegistry()
