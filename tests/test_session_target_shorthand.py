"""status、compact 与 persona 使用相同的会话简写。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from test_compact_command import ConversationCommands, FakeEvent, _context, _result_text, _run

TARGET = "qq:GroupMessage:123456"
WEB = "webchat:FriendMessage:webchat!user1!thread-9"


def context_with_sessions(umos=None):
    context = _context()
    umos = [TARGET, WEB] if umos is None else umos
    context.get_db.return_value.get_umo_aliases = AsyncMock(return_value=[
        SimpleNamespace(umo=TARGET, user_alias="测试群", auto_name="群名称"),
        SimpleNamespace(umo="qq:GroupMessage:deleted", user_alias="已删除群", auto_name=""),
    ])

    @asynccontextmanager
    async def get_db():
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            fetchall=lambda: [(umo,) for umo in umos],
        )))

    context.get_db.return_value.get_db = get_db
    return context


@pytest.mark.parametrize("command", ["status", "compact"])
@pytest.mark.parametrize("raw,target", [("123456", TARGET), ("测试群", TARGET), ("群名称", TARGET), ("thread-9", WEB), ("user1", WEB)])
def test_号码名称线程简写定位目标(command, raw, target):
    event = FakeEvent()
    event.role = "admin"
    context = context_with_sessions()
    context.conversation_manager.get_curr_conversation_id.return_value = None
    _run(getattr(ConversationCommands(context), command)(event, raw))
    context.conversation_manager.get_curr_conversation_id.assert_awaited_once_with(target)
    assert target in _result_text(event)


def test_compact_简写与保留轮数混用():
    event = FakeEvent()
    event.role = "admin"
    context = context_with_sessions()
    _run(ConversationCommands(context).compact(event, "123456", "yes", "1"))
    assert context.conversation_manager.update_conversation.await_args.args[:2] == (TARGET, "cid-1")
    assert "保留最近 1 轮" in _result_text(event)


@pytest.mark.parametrize("command", ["status", "compact"])
def test_同号多会话不能任选(command):
    event = FakeEvent()
    event.role = "admin"
    other = "qq2:FriendMessage:123456"
    context = context_with_sessions([TARGET, other])
    _run(getattr(ConversationCommands(context), command)(event, "123456"))
    text = _result_text(event)
    assert "多个会话" in text and TARGET in text and other in text
    context.conversation_manager.get_curr_conversation_id.assert_not_awaited()


@pytest.mark.parametrize("command", ["status", "compact"])
@pytest.mark.parametrize("raw", ["已删除群", "不存在的昵称"])
def test_未命中不回退当前会话(command, raw):
    event = FakeEvent()
    event.role = "admin"
    context = context_with_sessions()
    _run(getattr(ConversationCommands(context), command)(event, raw))
    assert "未找到会话" in _result_text(event)
    context.conversation_manager.get_curr_conversation_id.assert_not_awaited()


def test_compact_未命中号码仍作保留轮数():
    event = FakeEvent()
    event.role = "admin"
    context = context_with_sessions()
    _run(ConversationCommands(context).compact(event, "yes", "1"))
    assert context.conversation_manager.update_conversation.await_args.args[:2] == (event.unified_msg_origin, "cid-1")
