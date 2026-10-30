"""验证事件收尾钩子 OnEventCompletedEvent。

覆盖：
- EventType / filter.on_event_completed 注册链路可用
- call_event_hook(allow_stopped=True) 对已终止事件照常执行钩子
- call_event_hook 默认行为不变：已终止事件不执行钩子，只补终止通知
- PipelineScheduler.execute 在事件被终止时仍派发收尾钩子
- _process_stages 抛异常时同样派发，且 active_event_registry 正常注销
- 生产同款 functools.partial 绑定（插件类方法）能被正常派发

运行：ldmbot/.venv/Scripts/python.exe tests/test_event_completed_hook.py
也可用 pytest 收集（async 用例已带 pytest.mark.asyncio 标记）。
"""

import asyncio
import functools
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.pipeline.context_utils import call_event_hook  # noqa: E402
from astrbot.core.pipeline.scheduler import PipelineScheduler  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.star.star import star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)
from astrbot.core.utils.active_event_registry import active_event_registry  # noqa: E402

# 收尾钩子是否被调用、拿到了哪些事件
收尾调用记录: list[AstrMessageEvent] = []


async def 收尾处理器(event: AstrMessageEvent) -> None:
    """测试用收尾钩子：只记录事件，不改变任何状态。"""
    收尾调用记录.append(event)


class _插件类:
    """模拟插件类：生产环境中 handler 会被重绑为 functools.partial(裸函数, 实例)。"""

    async def hook_handler(self, event: AstrMessageEvent) -> None:
        收尾调用记录.append(event)


def _make_event() -> AstrMessageEvent:
    platform_meta = PlatformMetadata(name="test", description="t", id="test")
    message_obj = SimpleNamespace(
        type=MessageType.FRIEND_MESSAGE,
        message=[Plain("你好")],
        sender=SimpleNamespace(user_id="u1", nickname="测试用户"),
        message_str="你好",
        raw_message={"message_id": 123456},
    )
    event = AstrMessageEvent("你好", message_obj, platform_meta, "u1")
    # 预置会话配置缓存，避免派发时查库
    event.set_extra("_sp_session_plugin_config", {})
    return event


def _handler_metadata(handler, name: str) -> StarHandlerMetadata:
    return StarHandlerMetadata(
        event_type=EventType.OnEventCompletedEvent,
        handler_full_name=f"{handler.__module__}_{name}",
        handler_name=name,
        handler_module_path=handler.__module__,
        handler=handler,
        event_filters=[],
    )


class _注册上下文:
    """临时注册收尾处理器 + 假插件元数据，退出时全部清理。"""

    def __init__(self, handler, name: str, module_path: str, display: str):
        self.md = _handler_metadata(handler, name)
        self.module_path = module_path
        self.display = display
        self._old_star = None

    def __enter__(self):
        收尾调用记录.clear()
        self._old_star = star_map.get(self.module_path)
        star_map[self.module_path] = SimpleNamespace(
            name=self.display,
            display_name=self.display,
            activated=True,
            reserved=False,
        )
        star_handlers_registry.append(self.md)
        return self.md

    def __exit__(self, *exc):
        if self.md in star_handlers_registry._handlers:
            star_handlers_registry._handlers.remove(self.md)
        star_handlers_registry.star_handlers_map.pop(self.md.handler_full_name, None)
        if self._old_star is None:
            star_map.pop(self.module_path, None)
        else:
            star_map[self.module_path] = self._old_star
        return False


@pytest.mark.asyncio
async def test_call_event_hook_allow_stopped_runs_handler():
    with _注册上下文(收尾处理器, "收尾处理器", 收尾处理器.__module__, "收尾测试插件"):
        event = _make_event()
        event.stop_event()

        # 默认行为不变：已终止事件不执行钩子
        assert await call_event_hook(event, EventType.OnEventCompletedEvent) is True
        assert 收尾调用记录 == []

        # allow_stopped=True：照常执行
        assert (
            await call_event_hook(
                event,
                EventType.OnEventCompletedEvent,
                allow_stopped=True,
            )
            is True
        )
        assert 收尾调用记录 == [event]
    print("test_call_event_hook_allow_stopped_runs_handler: PASS")


@pytest.mark.asyncio
async def test_scheduler_dispatches_when_stopped():
    with _注册上下文(收尾处理器, "收尾处理器", 收尾处理器.__module__, "收尾测试插件"):
        event = _make_event()

        scheduler = PipelineScheduler(MagicMock())

        async def fake_process(ev, from_stage=0):
            ev.stop_event()

        scheduler._process_stages = fake_process
        await scheduler.execute(event)

        assert 收尾调用记录 == [event]
        assert event.is_stopped()
        # 收尾后必须已从活跃事件表注销
        assert not active_event_registry.has_active(event.unified_msg_origin)
    print("test_scheduler_dispatches_when_stopped: PASS")


@pytest.mark.asyncio
async def test_scheduler_dispatches_when_exception():
    with _注册上下文(收尾处理器, "收尾处理器", 收尾处理器.__module__, "收尾测试插件"):
        event = _make_event()

        scheduler = PipelineScheduler(MagicMock())

        async def fake_process(ev, from_stage=0):
            raise RuntimeError("pipeline boom")

        scheduler._process_stages = fake_process
        with pytest.raises(RuntimeError):
            await scheduler.execute(event)

        # 异常路径同样派发收尾钩子，且注销未被跳过
        assert 收尾调用记录 == [event]
        assert not active_event_registry.has_active(event.unified_msg_origin)
    print("test_scheduler_dispatches_when_exception: PASS")


@pytest.mark.asyncio
async def test_scheduler_dispatches_on_normal_completion():
    with _注册上下文(收尾处理器, "收尾处理器", 收尾处理器.__module__, "收尾测试插件"):
        event = _make_event()

        scheduler = PipelineScheduler(MagicMock())

        async def fake_process(ev, from_stage=0):
            return

        scheduler._process_stages = fake_process
        await scheduler.execute(event)

        assert 收尾调用记录 == [event]
        assert not event.is_stopped()
        assert not active_event_registry.has_active(event.unified_msg_origin)
    print("test_scheduler_dispatches_on_normal_completion: PASS")


@pytest.mark.asyncio
async def test_partial_bound_handler_dispatched():
    """生产绑定方式：functools.partial(裸函数, 实例) 必须能收到收尾钩子。"""
    plugin = _插件类()
    partial_handler = functools.partial(_插件类.hook_handler, plugin)
    md = _handler_metadata(partial_handler, "hook_handler")
    md.handler_module_path = _插件类.hook_handler.__module__

    star_map_key = md.handler_module_path
    old_star = star_map.get(star_map_key)
    star_map[star_map_key] = SimpleNamespace(
        name="partial 收尾插件",
        display_name="partial 收尾插件",
        activated=True,
        reserved=False,
    )
    star_handlers_registry.append(md)
    try:
        收尾调用记录.clear()
        event = _make_event()
        event.stop_event()
        await call_event_hook(
            event,
            EventType.OnEventCompletedEvent,
            allow_stopped=True,
        )
        assert 收尾调用记录 == [event]
    finally:
        if md in star_handlers_registry._handlers:
            star_handlers_registry._handlers.remove(md)
        star_handlers_registry.star_handlers_map.pop(md.handler_full_name, None)
        if old_star is None:
            star_map.pop(star_map_key, None)
        else:
            star_map[star_map_key] = old_star
    print("test_partial_bound_handler_dispatched: PASS")


async def main():
    await test_call_event_hook_allow_stopped_runs_handler()
    await test_scheduler_dispatches_when_stopped()
    await test_scheduler_dispatches_when_exception()
    await test_scheduler_dispatches_on_normal_completion()
    await test_partial_bound_handler_dispatched()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
