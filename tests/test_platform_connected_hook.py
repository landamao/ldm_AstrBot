"""验证平台适配器连接钩子 on_platform_connected。

覆盖：
- @filter.on_platform_connected 注册到 EventType.OnPlatformConnectedEvent
- Platform.notify_connected 派发钩子，钩子拿到适配器实例与 self_id
- platform_name 过滤只命中指定适配器
- 单个钩子抛错被隔离，不影响其它钩子与适配器
- aiocqhttp 适配器收到 OneBot lifecycle.connect 时触发钩子
- aiocqhttp.is_connected / connected_self_id 读的是真实反向 WS 连接表

运行：ldmbot/.venv/Scripts/python.exe tests/test_platform_connected_hook.py
也可用 pytest 收集（async 用例已带 pytest.mark.asyncio 标记）。
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from aiocqhttp import Event  # noqa: E402

from astrbot.api.event import filter  # noqa: E402
from astrbot.core.platform.platform import Platform, PlatformStatus  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (  # noqa: E402
    AiocqhttpAdapter,
)
from astrbot.core.star.star import star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    star_handlers_registry,
)

# (平台实例, self_id) 的调用记录
调用记录: list[tuple[Platform, str | None]] = []


@filter.on_platform_connected()
async def 通用连接处理器(platform: Platform, self_id: str | None) -> None:
    """测试用：接收所有适配器的连接事件。"""
    调用记录.append((platform, self_id))


@filter.on_platform_connected(platform_name="aiocqhttp")
async def OneBot连接处理器(platform: Platform, self_id: str | None) -> None:
    """测试用：只接收 aiocqhttp 的连接事件。"""
    调用记录.append((platform, self_id))


@filter.on_platform_connected(priority=10)
async def 会抛错的处理器(platform: Platform, self_id: str | None) -> None:
    """测试用：故意抛错，验证抛错被隔离且高优先级先执行。"""
    raise RuntimeError("故意抛错")


class _插件上下文:
    """临时挂上假插件元数据，让 only_activated=True 的查找能命中。"""

    def __enter__(self):
        调用记录.clear()
        self._old_star = star_map.get(__name__)
        star_map[__name__] = SimpleNamespace(
            name="连接钩子测试插件",
            display_name="连接钩子测试插件",
            activated=True,
            reserved=False,
        )
        return self

    def __exit__(self, *exc):
        if self._old_star is None:
            star_map.pop(__name__, None)
        else:
            star_map[__name__] = self._old_star
        return False


class _测试平台(Platform):
    """最小可用的平台适配器，名称可控，用来模拟不同适配器。"""

    def __init__(self, name: str = "test") -> None:
        super().__init__({"id": name}, asyncio.Queue())
        self._meta = PlatformMetadata(name=name, description="测试用平台", id=name)

    def meta(self) -> PlatformMetadata:
        return self._meta

    async def run(self) -> None:
        return


def _获取处理器(name: str):
    return star_handlers_registry.get_handler_by_full_name(f"{__name__}_{name}")


def test_decorator_registers_event_type_and_filter():
    """注册器写入正确的事件类型与 platform_name 过滤参数。"""
    通用 = _获取处理器("通用连接处理器")
    过滤 = _获取处理器("OneBot连接处理器")
    assert 通用 is not None
    assert 通用.event_type == EventType.OnPlatformConnectedEvent
    assert 通用.extras_configs.get("platform_name") is None

    assert 过滤 is not None
    assert 过滤.event_type == EventType.OnPlatformConnectedEvent
    assert 过滤.extras_configs.get("platform_name") == "aiocqhttp"
    print("test_decorator_registers_event_type_and_filter: PASS")


@pytest.mark.asyncio
async def test_notify_connected_dispatches_hook():
    """notify_connected 派发钩子：抛错的钩子不阻断后续钩子。"""
    with _插件上下文():
        platform = _测试平台("aiocqhttp")
        await platform.notify_connected("10001")

    # 高优先级的抛错钩子先跑且未阻断，其余两个各记录一次
    assert len(调用记录) == 2
    assert all(inst is platform for inst, _ in 调用记录)
    assert {self_id for _, self_id in 调用记录} == {"10001"}
    print("test_notify_connected_dispatches_hook: PASS")


@pytest.mark.asyncio
async def test_platform_name_filter_skips_other_adapter():
    """platform_name 过滤：telegram 只命中通用钩子。"""
    with _插件上下文():
        platform = _测试平台("telegram")
        await platform.notify_connected("bot-1")

    assert len(调用记录) == 1
    assert 调用记录[0] == (platform, "bot-1")
    print("test_platform_name_filter_skips_other_adapter: PASS")


@pytest.mark.asyncio
async def test_base_platform_connection_state():
    """基类默认以运行中为已连接，且 stats 输出 connected 字段。"""
    platform = _测试平台("demo")
    assert platform.is_connected is False
    assert platform.connected_self_id is None
    assert platform.get_stats()["connected"] is False

    platform.status = PlatformStatus.RUNNING
    assert platform.is_connected is True
    assert platform.get_stats()["connected"] is True
    print("test_base_platform_connection_state: PASS")


def _make_adapter() -> AiocqhttpAdapter:
    return AiocqhttpAdapter(
        {
            "id": "onebot-test",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "0.0.0.0",
            "ws_reverse_port": 6199,
            "ws_reverse_token": "",
        },
        {},
        asyncio.Queue(),
    )


@pytest.mark.asyncio
async def test_aiocqhttp_connection_state_tracks_ws_clients():
    """aiocqhttp 的连接状态跟随库内反向 WS 连接表，不缓存。"""
    adapter = _make_adapter()
    assert adapter.is_connected is False
    assert adapter.connected_self_id is None

    adapter.bot._wsr_api_clients["10001"] = object()
    assert adapter.is_connected is True
    assert adapter.connected_self_id == "10001"

    adapter.bot._wsr_api_clients.clear()
    assert adapter.is_connected is False
    assert adapter.connected_self_id is None
    print("test_aiocqhttp_connection_state_tracks_ws_clients: PASS")


@pytest.mark.asyncio
async def test_aiocqhttp_lifecycle_connect_fires_hook():
    """OneBot 的 lifecycle.connect 元事件会走真实事件总线触发钩子。"""
    adapter = _make_adapter()
    adapter.bot._wsr_api_clients["10001"] = object()

    event = Event.from_payload(
        {
            "post_type": "meta_event",
            "meta_event_type": "lifecycle",
            "sub_type": "connect",
            "self_id": 10001,
            "time": 0,
        },
    )
    assert event is not None

    with _插件上下文():
        await adapter.bot._bus.emit("meta_event.lifecycle.connect", event)

    # 通用钩子 + OneBot 专用钩子各一次，抛错钩子未阻断
    assert len(调用记录) == 2
    assert all(inst is adapter for inst, _ in 调用记录)
    assert {self_id for _, self_id in 调用记录} == {"10001"}
    print("test_aiocqhttp_lifecycle_connect_fires_hook: PASS")


async def main():
    test_decorator_registers_event_type_and_filter()
    await test_notify_connected_dispatches_hook()
    await test_platform_name_filter_skips_other_adapter()
    await test_base_platform_connection_state()
    await test_aiocqhttp_connection_state_tracks_ws_clients()
    await test_aiocqhttp_lifecycle_connect_fires_hook()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
