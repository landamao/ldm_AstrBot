"""验证 handler 产出裸字符串时按纯文本结果发送。

覆盖：
- 异步生成器 yield "文本" → 等价于 event.plain_result("文本")，结果落 event
- 协程 return "文本" → 同上（非生成器 handler 的对称行为）
- yield / return MessageEventResult 与 None 的既有行为不受影响
- 其他类型（如 ProviderRequest）照旧原样上抛，不被误包
- 多次 yield 时后者覆盖前者（与连续 plain_result 一致）

运行：.venv/Scripts/python.exe tests/test_yield_plain_str.py
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

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.message.message_event_result import (  # noqa: E402
    MessageEventResult,
)
from astrbot.core.pipeline.context_utils import call_handler  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.provider.entities import ProviderRequest  # noqa: E402


def _make_event() -> AstrMessageEvent:
    """构造可直接实例化的真实事件（AstrMessageEvent 无抽象方法）。"""
    platform_meta = PlatformMetadata(name="test", description="t", id="test")
    message_obj = SimpleNamespace(
        type=MessageType.FRIEND_MESSAGE,
        message=[Plain("你好")],
        sender=SimpleNamespace(user_id="u1", nickname="测试用户"),
        message_str="你好",
    )
    return AstrMessageEvent(message_obj, message_obj, platform_meta, "u1")


def _plain_texts(event: AstrMessageEvent) -> list[str]:
    result = event.get_result()
    assert result is not None
    return [c.text for c in result.chain if isinstance(c, Plain)]


async def _drive(event: AstrMessageEvent, handler) -> list:
    """驱动 call_handler，收集它向上抛出的透传值。"""
    passed = []
    async for out in call_handler(event, handler):
        if out is not None:
            passed.append(out)
    return passed


@pytest.mark.asyncio
async def test_yield_bare_str_becomes_plain_result():
    event = _make_event()

    async def handler(event):
        yield "屏蔽成功"

    passed = await _drive(event, handler)
    assert passed == []  # 被消费为结果，不向上透传
    assert _plain_texts(event) == ["屏蔽成功"]


@pytest.mark.asyncio
async def test_return_bare_str_becomes_plain_result():
    event = _make_event()

    async def handler(event):
        return "已完成"

    passed = await _drive(event, handler)
    assert passed == []
    assert _plain_texts(event) == ["已完成"]


@pytest.mark.asyncio
async def test_yield_none_still_no_result():
    event = _make_event()

    async def handler(event):
        yield None

    await _drive(event, handler)
    assert event.get_result() is None


@pytest.mark.asyncio
async def test_message_event_result_still_works():
    event = _make_event()

    async def handler(event):
        yield event.plain_result("老写法")

    await _drive(event, handler)
    assert _plain_texts(event) == ["老写法"]


@pytest.mark.asyncio
async def test_return_message_event_result_still_works():
    event = _make_event()

    async def handler(event):
        return MessageEventResult().message("返回值老写法")

    await _drive(event, handler)
    assert _plain_texts(event) == ["返回值老写法"]


@pytest.mark.asyncio
async def test_other_types_pass_through_untouched():
    event = _make_event()
    req = ProviderRequest()

    async def handler(event):
        yield req

    passed = await _drive(event, handler)
    assert passed == [req]  # 原样上抛（ProcessStage 按 ProviderRequest 处理）
    assert event.get_result() is None


@pytest.mark.asyncio
async def test_multiple_yields_last_wins():
    event = _make_event()

    async def handler(event):
        yield "第一条"
        yield "第二条"

    await _drive(event, handler)
    assert _plain_texts(event) == ["第二条"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run_all()))


async def _run_all() -> int:
    import inspect

    failed = 0
    for name, obj in list(globals().items()):
        if name.startswith("test_") and inspect.iscoroutinefunction(obj):
            try:
                await obj()
                print(f"通过: {name}")
            except AssertionError as e:
                failed += 1
                print(f"失败: {name}: {e}")
    return 1 if failed else 0
