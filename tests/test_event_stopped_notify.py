"""验证 stop_event 终止日志的真实来源归因。

覆盖：
- stop_event() 调用栈留痕（首次停止为准）、continue_event() 清除留痕
- resolve_stop_source 按注册处理器的 code 对象匹配，匹配不到返回 (None, None)
- 已知/未知两种文案与消息概要
- WebChat 结构化卡片与同一事件去重、非 WebChat 不发送
- call_event_hook 对已死事件的入口预检（不执行任何钩子处理器）
- BotMessageAccumulator 对新卡片的落库

运行：~/ldmbot/.venv/bin/python tests/test_event_stopped_notify.py
也可用 pytest 收集（async 用例已带 pytest.mark.asyncio 标记）。
"""

import asyncio
import functools
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.pipeline.context_utils import (  # noqa: E402
    call_event_hook,
    call_handler,
    format_event_stopped_message,
    format_unknown_stopped_message,
    notify_event_stopped,
    resolve_stop_source,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.star.star import star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    star_handlers_registry,
)
from astrbot.dashboard.services.chat_service import BotMessageAccumulator  # noqa: E402


def _make_event(message: str = "你好，机器人") -> AstrMessageEvent:
    """构造可直接实例化的真实事件（AstrMessageEvent 无抽象方法）。"""
    platform_meta = PlatformMetadata(name="test", description="t", id="test")
    message_obj = SimpleNamespace(
        type=MessageType.FRIEND_MESSAGE,
        message=[Plain(message)] if message else [],
        sender=SimpleNamespace(user_id="u1", nickname="测试用户"),
        message_str=message,
    )
    return AstrMessageEvent(message, message_obj, platform_meta, "u1")


def _register_handler(module_path: str, display: str, name: str, func) -> None:
    """向 star_map 与处理器注册表注入桩，用于来源解析。"""
    star_map[module_path] = SimpleNamespace(name=module_path, display_name=display)
    star_handlers_registry.star_handlers_map[f"{module_path}/{name}"] = SimpleNamespace(
        handler=func,
        handler_module_path=module_path,
        handler_name=name,
    )


def _unregister_handler(module_path: str, name: str) -> None:
    star_map.pop(module_path, None)
    star_handlers_registry.star_handlers_map.pop(f"{module_path}/{name}", None)


def test_stop_event_captures_first_caller():
    def stopper_a(event):
        event.stop_event()

    def stopper_b(event):
        event.stop_event()

    _register_handler("test_plugin_stop", "归因测试插件", "stopper_a", stopper_a)
    try:
        event = _make_event()
        assert event._stopped_by_frames is None
        stopper_a(event)
        assert event.is_stopped()
        # 首次停止为准：后续 stop_event 不覆盖留痕
        stopper_b(event)
        md, handler_name = resolve_stop_source(event)
        assert handler_name == "stopper_a"
        assert md is not None and md.display_name == "归因测试插件"
        # continue_event 清除留痕与停止标志
        event.continue_event()
        assert not event.is_stopped()
        assert event._stopped_by_frames is None
    finally:
        _unregister_handler("test_plugin_stop", "stopper_a")


def test_resolve_unknown_when_no_match():
    event = _make_event()

    def not_registered_stopper(ev):
        ev.stop_event()

    not_registered_stopper(event)
    md, handler_name = resolve_stop_source(event)
    assert md is None and handler_name is None
    # 无留痕（如 result 级停止）同样归未知
    event2 = _make_event()
    assert resolve_stop_source(event2) == (None, None)


class _FakePlugin:
    """模拟插件类：生产中实例化后 handler 被绑成 functools.partial(裸函数, 实例)。"""

    async def hook_handler(self, event):
        event.stop_event()

    async def gen_handler(self, event):
        event.stop_event()
        yield


@pytest.mark.asyncio
async def test_resolve_partial_bound_handlers():
    plugin = _FakePlugin()
    # star_manager 同款绑定：functools.partial(裸函数, 实例)
    partial_hook = functools.partial(_FakePlugin.hook_handler, plugin)
    partial_gen = functools.partial(_FakePlugin.gen_handler, plugin)
    _register_handler(
        "test_plugin_partial", "partial 绑定插件", "hook_handler", partial_hook
    )
    _register_handler(
        "test_plugin_partial", "partial 绑定插件", "gen_handler", partial_gen
    )
    try:
        # 钩子路径：直接 await（call_event_hook 的调用方式）
        event = _make_event()
        await partial_hook(event)
        md, handler_name = resolve_stop_source(event)
        assert handler_name == "hook_handler"
        assert md is not None and md.display_name == "partial 绑定插件"

        # 指令路径：异步生成器 handler 走真实 call_handler 洋葱机制
        event2 = _make_event()
        wrapper = call_handler(event2, partial_gen)
        async for _ in wrapper:
            pass
        md2, handler_name2 = resolve_stop_source(event2)
        assert handler_name2 == "gen_handler"
        assert md2 is not None
    finally:
        _unregister_handler("test_plugin_partial", "hook_handler")
        _unregister_handler("test_plugin_partial", "gen_handler")
    print("test_resolve_partial_bound_handlers: PASS")


def test_format():
    assert (
        format_event_stopped_message("复读机", "on_llm_request", "你好")
        == "插件「复读机」（on_llm_request）终止了事件传播。消息概要「你好」"
    )
    assert (
        format_unknown_stopped_message("你好")
        == "事件被终止传播，未找到调用点。消息概要「你好」"
    )
    print("test_format: PASS")


def test_accumulator():
    acc = BotMessageAccumulator()
    payload = json.dumps(
        {
            "text": "插件「复读机」（on_llm_request）终止了事件传播。消息概要「你好」",
            "plugin": "复读机",
            "method": "on_llm_request",
        },
        ensure_ascii=False,
    )
    acc.add_plain(payload, chain_type="event_stopped", streaming=False)
    parts = acc.build_message_parts()
    assert len(parts) == 1
    assert parts[0]["type"] == "event_stopped"
    assert (
        parts[0]["text"]
        == "插件「复读机」（on_llm_request）终止了事件传播。消息概要「你好」"
    )
    assert parts[0]["plugin"] == "复读机"
    assert parts[0]["method"] == "on_llm_request"

    # 未知来源：plugin/method 为空也能落库
    acc2 = BotMessageAccumulator()
    payload2 = json.dumps(
        {
            "text": "事件被终止传播，未找到调用点。消息概要「你好」",
            "plugin": None,
            "method": None,
        },
        ensure_ascii=False,
    )
    acc2.add_plain(payload2, chain_type="event_stopped", streaming=False)
    parts2 = acc2.build_message_parts()
    assert parts2[0]["plugin"] == "" and parts2[0]["method"] == ""
    print("test_accumulator: PASS")


def _webchat_event() -> MagicMock:
    event = MagicMock()
    event.get_extra.return_value = None
    event.get_platform_name.return_value = "webchat"
    event.send = AsyncMock()
    return event


@pytest.mark.asyncio
async def test_notify_known_source_webchat():
    event = _webchat_event()
    event.get_message_outline.return_value = " 你好 "

    def stopper(ev):
        pass  # 不实际调用，仅用其 code 对象伪造留痕

    _register_handler("test_plugin_notify", "通知测试插件", "my_handler", stopper)
    try:
        event._stopped_by_frames = [(stopper.__code__, 10)]
        await notify_event_stopped(event)
        event.send.assert_awaited_once()
        chain = event.send.await_args.args[0]
        assert chain.type == "event_stopped"
        payload = chain.chain[0].data
        assert payload["plugin"] == "通知测试插件"
        assert payload["method"] == "my_handler"
        assert (
            payload["text"]
            == "插件「通知测试插件」（my_handler）终止了事件传播。消息概要「你好」"
        )
        # 同一事件第二次通知应被去重
        event.get_extra.return_value = True
        await notify_event_stopped(event)
        assert event.send.await_count == 1
    finally:
        _unregister_handler("test_plugin_notify", "my_handler")
    print("test_notify_known_source_webchat: PASS")


@pytest.mark.asyncio
async def test_notify_unknown_source_webchat():
    event = _webchat_event()
    event.get_message_outline.return_value = ""
    event._stopped_by_frames = None
    await notify_event_stopped(event)
    chain = event.send.await_args.args[0]
    payload = chain.chain[0].data
    assert payload["text"] == "事件被终止传播，未找到调用点。消息概要「（空）」"
    assert payload["plugin"] == "" and payload["method"] == ""
    print("test_notify_unknown_source_webchat: PASS")


@pytest.mark.asyncio
async def test_notify_non_webchat_no_send():
    event = MagicMock()
    event.get_extra.return_value = None
    event.get_platform_name.return_value = "aiocqhttp"
    event.get_message_outline.return_value = "你好"
    event._stopped_by_frames = None
    event.send = AsyncMock()
    await notify_event_stopped(event)
    event.send.assert_not_awaited()
    print("test_notify_non_webchat_no_send: PASS")


@pytest.mark.asyncio
async def test_notify_with_real_event_capture():
    event = _make_event()

    def stopper(ev):
        ev.stop_event()

    _register_handler("test_plugin_real", "真实捕获插件", "real_stopper", stopper)
    try:
        stopper(event)
        await notify_event_stopped(event)
        # 真实事件：留痕解析命中注册处理器；非 webchat 平台不发送卡片
        assert event.get_extra("_event_stopped_notified") is True
        # 再通知一次验证去重不重复落日志路径
        await notify_event_stopped(event)
    finally:
        _unregister_handler("test_plugin_real", "real_stopper")
    print("test_notify_with_real_event_capture: PASS")


@pytest.mark.asyncio
async def test_call_event_hook_skips_dead_event():
    event = _webchat_event()
    event.is_stopped.return_value = True
    event.get_message_outline.return_value = "已终止的消息"
    event._stopped_by_frames = None
    result = await call_event_hook(event, EventType.OnLLMRequestEvent)
    # 预检即返回：事件已死不执行任何钩子处理器，只补终止通知
    assert result is True
    event.send.assert_awaited_once()
    chain = event.send.await_args.args[0]
    assert chain.type == "event_stopped"
    print("test_call_event_hook_skips_dead_event: PASS")


async def main():
    test_stop_event_captures_first_caller()
    test_resolve_unknown_when_no_match()
    await test_resolve_partial_bound_handlers()
    test_format()
    test_accumulator()
    await test_notify_known_source_webchat()
    await test_notify_unknown_source_webchat()
    await test_notify_non_webchat_no_send()
    await test_notify_with_real_event_capture()
    await test_call_event_hook_skips_dead_event()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
