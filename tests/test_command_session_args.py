"""指令参数透传测试：main.py 委托层把原始 token 原样传给 commands 层。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot.builtin_stars.builtin_commands.main import Main


def _ready_main(monkeypatch):
    """绕过 Star 初始化，仅接好被测两个方法的委托。"""
    main = Main.__new__(Main)
    main.context = SimpleNamespace()
    main.conversation_c = SimpleNamespace(
        compact=AsyncMock(), status=AsyncMock()
    )
    return main


def _make_event():
    event = SimpleNamespace()
    event.should_call_llm = lambda *_: None
    return event


@pytest.mark.asyncio
async def test_会话ID含冒号不被参数转换改写(monkeypatch):
    """会话 ID 平台段含数字（如 12345:GroupMessage:1）时必须保持 str。"""
    event = _make_event()
    main = _ready_main(monkeypatch)

    raw = "12345:GroupMessage:789"
    await main.compact(event, raw, "yes", "3")
    main.conversation_c.compact.assert_awaited_once_with(event, raw, "yes", "3")
    await main.status(event, raw)
    main.conversation_c.status.assert_awaited_once_with(event, raw)


@pytest.mark.asyncio
async def test_compact_缺省参数传None():
    """main 委托层只透传，不做类型转换（commands 层负责解析）。"""
    event = _make_event()
    main = _ready_main(None)

    await main.compact(event, "3")
    main.conversation_c.compact.assert_awaited_once_with(event, "3", None, None)
