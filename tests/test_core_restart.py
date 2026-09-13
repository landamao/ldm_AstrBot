"""验证 core_lifecycle.restart() 能正确拉起后台重启任务。

回归：asyncio.create_task(asyncio.shield(coro)) 会 TypeError
（shield 返回 Future，create_task 只接受 coroutine）。
WebUI /system/restart 每次都会打「Dashboard API 未处理异常」。
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "/home/ldm/ldmbot_code")

from astrbot.core.core_lifecycle import AstrBotCoreLifecycle  # noqa: E402


def _make_lifecycle() -> AstrBotCoreLifecycle:
    lc = AstrBotCoreLifecycle.__new__(AstrBotCoreLifecycle)
    lc.event_bus = MagicMock()
    lc.event_bus.shutdown = AsyncMock()
    lc.plugin_manager = MagicMock()
    lc.plugin_manager.shutdown = AsyncMock()
    lc.provider_manager = MagicMock()
    lc.provider_manager.terminate = AsyncMock()
    lc.platform_manager = MagicMock()
    lc.platform_manager.terminate = AsyncMock()
    lc.kb_manager = MagicMock()
    lc.kb_manager.terminate = AsyncMock()
    lc.dashboard_shutdown_event = MagicMock()
    lc.astrbot_updator = MagicMock()
    return lc


async def _跑重启(lc: AstrBotCoreLifecycle) -> MagicMock:
    with (
        patch(
            "astrbot.core.core_lifecycle.shutdown_local_booter",
            new_callable=AsyncMock,
        ) as boot,
        patch("astrbot.core.core_lifecycle.threading.Thread") as Thread,
    ):
        await lc.restart()
        await asyncio.sleep(0.05)
        boot.assert_awaited()
        return Thread


def 测试重启不抛TypeError且会走reboot():
    lc = _make_lifecycle()

    async def _run():
        Thread = await _跑重启(lc)
        lc.event_bus.shutdown.assert_awaited()
        lc.plugin_manager.shutdown.assert_awaited()
        lc.provider_manager.terminate.assert_awaited()
        lc.platform_manager.terminate.assert_awaited()
        lc.kb_manager.terminate.assert_awaited()
        lc.dashboard_shutdown_event.set.assert_called_once()
        Thread.assert_called_once()
        kwargs = Thread.call_args.kwargs
        assert kwargs["target"] is lc.astrbot_updator._reboot
        assert kwargs["name"] == "restart"
        assert kwargs["daemon"] is True
        Thread.return_value.start.assert_called_once()

    asyncio.run(_run())


def 测试调用方被取消后台重启仍完成():
    """聊天 /restart 在 pipeline 任务里调用：event_bus.shutdown 会 cancel 调用方。"""
    lc = _make_lifecycle()

    async def _run():
        with (
            patch(
                "astrbot.core.core_lifecycle.shutdown_local_booter",
                new_callable=AsyncMock,
            ),
            patch("astrbot.core.core_lifecycle.threading.Thread") as Thread,
        ):

            async def 调用方():
                await lc.restart()
                await asyncio.sleep(10)

            task = asyncio.create_task(调用方())
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0.05)
            lc.dashboard_shutdown_event.set.assert_called_once()
            Thread.assert_called_once()
            Thread.return_value.start.assert_called_once()

    asyncio.run(_run())


if __name__ == "__main__":
    测试重启不抛TypeError且会走reboot()
    测试调用方被取消后台重启仍完成()
    print("\033[32m全部通过\033[0m")
