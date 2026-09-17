"""/compact 指令：参数解析、压缩前提示、压缩结果。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.builtin_stars.builtin_commands.commands.conversation import (  # noqa: E402
    ConversationCommands,
)

HISTORY = json.dumps(
    [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你？"},
        {"role": "user", "content": "讲个笑话"},
        {"role": "assistant", "content": "……"},
    ],
    ensure_ascii=False,
)


class FakeEvent:
    def __init__(self) -> None:
        self.unified_msg_origin = "test:FriendMessage:204676209"
        self.result = None
        self.sent = []

    def set_result(self, result) -> None:
        self.result = result

    async def send(self, chain) -> None:
        self.sent.append(chain)

    def sent_text(self) -> str:
        return "".join(c.text for m in self.sent for c in m.chain if hasattr(c, "text"))


def _result_text(event: FakeEvent) -> str:
    return event.result.get_plain_text() if event.result else ""


def _context(history: str | None = HISTORY, provider=None) -> MagicMock:
    context = MagicMock()
    context.get_config.return_value = {
        "provider_settings": {
            "agent_runner_type": "local",
            "llm_compress_provider_id": "",
        }
    }
    mgr = context.conversation_manager
    mgr.get_curr_conversation_id = AsyncMock(return_value="cid-1")
    if history is None:
        mgr.get_conversation = AsyncMock(return_value=None)
    else:
        mgr.get_conversation = AsyncMock(
            return_value=SimpleNamespace(history=history)
        )
    mgr.update_conversation = AsyncMock()
    if provider is not None:
        context.get_using_provider = MagicMock(return_value=provider)
    else:
        context.get_using_provider = MagicMock(side_effect=ValueError("no provider"))
    return context


def _run(coro):
    return asyncio.run(coro)


def test_compact_非数字参数返回用法提示():
    """回归：/compact abc 曾因 转整数或None 返回 tuple 直接崩溃。"""
    event = FakeEvent()
    cmds = ConversationCommands(_context())
    _run(cmds.compact(event, "abc"))
    assert "未找到会话" in _result_text(event)
    assert "/compact" not in _result_text(event)


def test_compact_非正数轮数返回用法提示():
    event = FakeEvent()
    cmds = ConversationCommands(_context())
    for bad in ("0", "-2"):
        event = FakeEvent()
        _run(cmds.compact(event, bad))
        assert "参数无法识别" in _result_text(event), bad


def test_compact_无法解析且不是yes的混合参数报错():
    event = FakeEvent()
    cmds = ConversationCommands(_context())
    _run(cmds.compact(event, "yes", "abc"))
    assert "未找到会话" in _result_text(event)


def test_compact_无历史对话提示():
    event = FakeEvent()
    cmds = ConversationCommands(_context(history=None))
    _run(cmds.compact(event))
    assert "没有可压缩的历史" in _result_text(event)


def test_compact_llm摘要_先发提示再发结果():
    """回归：压缩前没有先发送「正在压缩」提示。"""
    event = FakeEvent()
    cmds = ConversationCommands(_context(provider=MagicMock()))

    captured = {}

    class FakeCompressor:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def __call__(self, messages):
            return messages[:1]

    with patch(
        "astrbot.builtin_stars.builtin_commands.commands.conversation.LLMSummaryCompressor",
        FakeCompressor,
    ):
        _run(cmds.compact(event, "4"))

    # 先收到「正在压缩」提示，且标注保留轮数
    assert "正在压缩上下文" in event.sent_text()
    assert "保留最近 4 轮" in event.sent_text()
    # 结果落库 + 完成消息
    cmds.context.conversation_manager.update_conversation.assert_awaited_once()
    assert "上下文压缩完成" in _result_text(event)
    assert "保留最近 4 轮" in _result_text(event)
    assert captured["keep_recent_rounds"] == 4


def test_compact_未指定轮数沿用配置默认():
    event = FakeEvent()
    context = _context(provider=MagicMock())
    context.get_config.return_value["provider_settings"][
        "llm_compress_keep_recent_rounds"
    ] = 5
    cmds = ConversationCommands(context)

    captured = {}

    class FakeCompressor:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def __call__(self, messages):
            return messages[:1]

    with patch(
        "astrbot.builtin_stars.builtin_commands.commands.conversation.LLMSummaryCompressor",
        FakeCompressor,
    ):
        _run(cmds.compact(event))

    assert captured["keep_recent_rounds"] == 5


def test_compact_管理员跨会话只压缩目标():
    event = FakeEvent()
    event.role = "admin"
    target = "other:GroupMessage:12345"
    context = _context()
    with patch(
        "astrbot.builtin_stars.builtin_commands.commands.conversation.active_event_registry.stop_all"
    ) as stop:
        _run(ConversationCommands(context).compact(event, "yes", "1", target))
    assert "上下文压缩完成" in _result_text(event)
    context.get_config.assert_called_once_with(umo=target)
    context.get_using_provider.assert_called_once_with(umo=target)
    context.conversation_manager.get_curr_conversation_id.assert_awaited_once_with(target)
    assert context.conversation_manager.update_conversation.await_args.args[:2] == (target, "cid-1")
    stop.assert_called_once_with(target, exclude=event)
    assert event.unified_msg_origin == "test:FriendMessage:204676209"
