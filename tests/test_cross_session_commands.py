"""status、compact 跨会话参数、权限与目标隔离回归。"""
import re
from contextlib import asynccontextmanager
from itertools import permutations
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_compact_command import (
    HISTORY,
    ConversationCommands,
    FakeEvent,
    _context,
    _result_text,
    _run,
)

MODULE = "astrbot.builtin_stars.builtin_commands.commands.conversation"
TARGET = "OtherBot:GroupMessage:123:456"


@pytest.mark.parametrize("command", ["status", "compact"])
@pytest.mark.parametrize("role", [None, "member"])
def test_跨会话拒绝非管理员且不访问目标(command, role):
    event = FakeEvent()
    event.role = role
    context = _context()
    _run(getattr(ConversationCommands(context), command)(event, TARGET))
    assert "管理员权限" in _result_text(event)
    context.get_config.assert_not_called()
    context.conversation_manager.get_curr_conversation_id.assert_not_awaited()
    context.conversation_manager.update_conversation.assert_not_awaited()


@pytest.mark.parametrize("command", ["status", "compact"])
@pytest.mark.parametrize("target", ["x:Invalid:1", ":GroupMessage:1", "x:GroupMessage:", "x:GroupMessage"])
def test_非法会话不执行(command, target):
    event = FakeEvent()
    event.role = "admin"
    context = _context()
    _run(getattr(ConversationCommands(context), command)(event, target))
    assert "会话 ID 格式错误" in _result_text(event)
    context.conversation_manager.get_curr_conversation_id.assert_not_awaited()


@pytest.mark.parametrize("args", list(permutations([TARGET, "YES", "1"])))
def test_compact_参数顺序任意且保留会话大小写(args):
    event = FakeEvent()
    event.role = "admin"
    context = _context()
    _run(ConversationCommands(context).compact(event, *args))
    assert "上下文压缩完成" in _result_text(event)
    assert context.conversation_manager.update_conversation.await_args.args[:2] == (TARGET, "cid-1")


def test_compact_截断重试提示携带目标会话():
    event = FakeEvent()
    event.role = "admin"
    context = _context()
    _run(ConversationCommands(context).compact(event, TARGET, "1"))
    assert f"/compact yes 1 {TARGET}" in _result_text(event)
    context.conversation_manager.update_conversation.assert_not_awaited()


@pytest.mark.parametrize("target", [None, "test:FriendMessage:204676209", TARGET])
def test_status_全部状态和统计来自目标会话(target):
    event = FakeEvent()
    event.role = "admin" if target == TARGET else "member"
    umo = target or event.unified_msg_origin
    context = _context()
    context.conversation_manager.get_curr_conversation_id.return_value = "target-cid"
    stats = SimpleNamespace(record_count=1, total_input_other=100, total_input_cached=20, total_output=30)
    query = AsyncMock(side_effect=[
        SimpleNamespace(one=lambda: stats),
        SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(current_context_tokens=500)),
    ])

    @asynccontextmanager
    async def get_db():
        yield SimpleNamespace(execute=query)

    context.get_db.return_value.get_db = get_db
    with patch(f"{MODULE}.active_event_registry.count", return_value=0) as count:
        _run(ConversationCommands(context).status(event, target))
    count.assert_called_once_with(umo, exclude=event)
    context.conversation_manager.get_curr_conversation_id.assert_awaited_once_with(umo)
    context.conversation_manager.get_conversation.assert_awaited_once_with(umo, "target-cid")
    for call in query.await_args_list:
        assert "target-cid" in call.args[0].compile().params.values()
    assert "当前上下文: 500" in _result_text(event)
    assert "历史消息轮数: 2" in _result_text(event)
    assert "150" in _result_text(event)
    assert event.unified_msg_origin == "test:FriendMessage:204676209"


@pytest.mark.parametrize("command", ["compact", "status"])
def test_目标没有当前对话不回退本会话(command):
    event = FakeEvent()
    event.role = "admin"
    context = _context()
    context.conversation_manager.get_curr_conversation_id.return_value = None
    _run(getattr(ConversationCommands(context), command)(event, TARGET))
    assert TARGET in _result_text(event)
    context.conversation_manager.get_curr_conversation_id.assert_awaited_once_with(TARGET)
    context.conversation_manager.get_conversation.assert_not_awaited()
    context.conversation_manager.update_conversation.assert_not_awaited()


def _status_context(conv) -> MagicMock:
    """构造能走完 status 全流程的 context：一次统计查询 + 一次最近记录查询。"""
    context = _context()
    context.conversation_manager.get_conversation = AsyncMock(return_value=conv)
    stats = SimpleNamespace(record_count=0, total_input_other=0, total_input_cached=0, total_output=0)
    query = AsyncMock(side_effect=[
        SimpleNamespace(one=lambda: stats),
        SimpleNamespace(scalar_one_or_none=lambda: None),
    ])

    @asynccontextmanager
    async def get_db():
        yield SimpleNamespace(execute=query)

    context.get_db.return_value.get_db = get_db
    return context


def test_status_最后活跃时间取自对话更新时间():
    event = FakeEvent()
    event.role = "admin"
    conv = SimpleNamespace(history=HISTORY, updated_at=1758249000)
    with patch(f"{MODULE}.active_event_registry.count", return_value=0):
        _run(ConversationCommands(_status_context(conv)).status(event, None))
    # 时间显示随本地时区变化，只断言行存在与格式
    assert re.search(r"最后活跃时间: \d{2}-\d{2} \d{2}:\d{2}", _result_text(event))


def test_status_无更新时间显示未知():
    event = FakeEvent()
    event.role = "admin"
    conv = SimpleNamespace(history=HISTORY, updated_at=0)
    with patch(f"{MODULE}.active_event_registry.count", return_value=0):
        _run(ConversationCommands(_status_context(conv)).status(event, None))
    assert "最后活跃时间: 未知" in _result_text(event)
