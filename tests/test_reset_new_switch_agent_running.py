"""/reset、/new、/switch 与运行中 Agent 的交互：

- /reset：强制停止（软停+立即打断 runner）+ 丢弃本轮历史写回 + 清空上下文
- /new、/switch、/groupnew：有 Agent 运行中时拒绝执行并提示 /stop

运行：python tests/test_reset_new_switch_agent_running.py
"""

import asyncio
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import astrbot.builtin_stars.builtin_commands.commands.conversation as conv_mod  # noqa: E402
from astrbot.builtin_stars.builtin_commands.commands.conversation import (  # noqa: E402
    ConversationCommands,
)

UMO = "test:FriendMessage:204676209"
REJECT_TEXT = "⚠️ 当前会话有Agent正在运行，请等待结束或发送/stop停止后重试"


@contextmanager
def _pinned_wake_prefix():
    """钉死唤醒词为 /，避免本机配置（如 ，，）影响文案断言。

    获取第一个唤醒词() 在函数内 from astrbot.core import astrbot_config，
    因此要钉 astrbot.core 模块属性本身。
    """
    import astrbot.core as core_mod

    with patch.object(core_mod, "astrbot_config", {"wake_prefix": ["/"]}):
        yield


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_wake_prefix():
    """pytest 与直跑两条入口都钉住唤醒词，文案断言不受本机配置影响。"""
    with _pinned_wake_prefix():
        yield


class FakeEvent:
    def __init__(self, umo: str = UMO) -> None:
        self.unified_msg_origin = umo
        self.result = None
        self.extras: dict = {}
        self.platform_meta = SimpleNamespace(id="test")

    def set_result(self, result) -> None:
        self.result = result

    def set_extra(self, key, value) -> None:
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    async def send(self, chain) -> None:
        pass

    def get_platform_id(self) -> str:
        return "test"

    def get_group_id(self) -> str | None:
        return None

    def get_sender_id(self) -> str:
        return "204676209"

    def result_text(self) -> str:
        return self.result.get_plain_text() if self.result else ""


def _context() -> MagicMock:
    context = MagicMock()
    context.get_config.return_value = {
        "provider_settings": {"agent_runner_type": "local"},
        "platform_settings": {"unique_session": False},
    }
    context.get_using_provider = MagicMock(return_value=MagicMock())
    mgr = context.conversation_manager
    mgr.get_curr_conversation_id = AsyncMock(return_value="cid-1")
    mgr.get_conversation = AsyncMock(return_value=None)
    mgr.update_conversation = AsyncMock()
    mgr.new_conversation = AsyncMock(return_value="cid-new-1234")
    mgr.switch_conversation = AsyncMock()
    mgr.get_conversations = AsyncMock(
        return_value=[SimpleNamespace(cid="cid-1abcd", title="旧对话")]
    )
    context.message_history_manager.delete_all = AsyncMock()
    return context


def _run(coro):
    return asyncio.run(coro)


def _patch_running(runner_active: bool, event_active: bool):
    """同时桩掉 runner 注册表与活跃事件注册表。"""
    return patch.multiple(
        conv_mod,
        get_active_runner=MagicMock(
            return_value=MagicMock(request_stop=MagicMock()) if runner_active else None
        ),
    )


# ==== /reset ====


def test_reset_运行中_强制停止并丢弃写回():
    """reset 必须软停事件+打断 runner+置丢弃标记，清空照常执行。"""
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    stop_all = MagicMock(return_value=1)
    request_agent_stop_all = MagicMock(return_value=1)
    runner = MagicMock(request_stop=MagicMock())
    with (
        patch.object(conv_mod.active_event_registry, "stop_all", stop_all),
        patch.object(
            conv_mod.active_event_registry,
            "request_agent_stop_all",
            request_agent_stop_all,
        ),
        patch.object(
            conv_mod.active_event_registry, "has_active", MagicMock(return_value=True)
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.get_active_runner",
            MagicMock(return_value=runner),
        ),
        patch.object(conv_mod.sp, "get_async", AsyncMock(return_value={})),
    ):
        _run(cmds.reset(event))

    # 软停而非终止传播：写入丢弃标记 + 强制停止标记
    request_agent_stop_all.assert_called_once_with(
        UMO,
        exclude=event,
        extra_updates={
            "agent_force_stop": True,
            "_discard_history_write": True,
        },
    )
    runner.request_stop.assert_called_once()
    # 清空照常
    context.conversation_manager.update_conversation.assert_awaited_once_with(
        UMO, "cid-1", []
    )
    assert "清除聊天历史成功" in event.result_text()


def test_reset_无运行任务_不触发停止():
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    request_agent_stop_all = MagicMock(return_value=0)
    with (
        patch.object(
            conv_mod.active_event_registry,
            "request_agent_stop_all",
            request_agent_stop_all,
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.get_active_runner",
            MagicMock(return_value=None),
        ),
        patch.object(conv_mod.sp, "get_async", AsyncMock(return_value={})),
    ):
        _run(cmds.reset(event))
    request_agent_stop_all.assert_called_once()
    context.conversation_manager.update_conversation.assert_awaited_once_with(
        UMO, "cid-1", []
    )
    assert "清除聊天历史成功" in event.result_text()


def test_save_to_history_丢弃标记_不写库():
    """_save_to_history 见到 _discard_history_write 直接返回，不写库。"""
    from astrbot.core.agent.message import Message
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
        InternalAgentSubStage,
    )

    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = FakeEvent()
    event.set_extra("_discard_history_write", True)
    req = SimpleNamespace(conversation=SimpleNamespace(cid="cid-1"))
    conv_mgr = MagicMock()
    conv_mgr.update_conversation = AsyncMock()
    stage.conv_manager = conv_mgr
    _run(
        stage._save_to_history(
            event,
            req,
            None,
            [Message(role="user", content="hi")],
            None,
        )
    )
    conv_mgr.update_conversation.assert_not_awaited()


def test_save_to_history_无标记_正常写库():
    from astrbot.core.agent.message import Message
    from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
        InternalAgentSubStage,
    )

    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = FakeEvent()
    req = SimpleNamespace(conversation=SimpleNamespace(cid="cid-1"))
    conv_mgr = MagicMock()
    conv_mgr.update_conversation = AsyncMock()
    stage.conv_manager = conv_mgr
    from astrbot.core.provider.entities import LLMResponse

    _run(
        stage._save_to_history(
            event,
            req,
            LLMResponse(role="assistant", completion_text="hi"),
            [Message(role="user", content="hi")],
            None,
        )
    )
    conv_mgr.update_conversation.assert_awaited_once()


# ==== /new ====


def test_new_运行中_拒绝执行():
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    with (
        patch.object(
            conv_mod.active_event_registry, "has_active", MagicMock(return_value=True)
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=False),
        ),
    ):
        _run(cmds.new_conv(event))
    assert REJECT_TEXT == event.result_text()
    context.conversation_manager.new_conversation.assert_not_awaited()


def test_new_runner活跃_同样拒绝():
    """活跃事件已注销但 runner 还没收尾（0.5s 窗口）时也要拒绝。"""
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    with (
        patch.object(
            conv_mod.active_event_registry, "has_active", MagicMock(return_value=False)
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=True),
        ),
    ):
        _run(cmds.new_conv(event))
    assert REJECT_TEXT == event.result_text()
    context.conversation_manager.new_conversation.assert_not_awaited()


def test_new_无运行_正常创建():
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    with (
        patch.object(
            conv_mod.active_event_registry, "has_active", MagicMock(return_value=False)
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=False),
        ),
    ):
        _run(cmds.new_conv(event))
    context.conversation_manager.new_conversation.assert_awaited_once()
    assert "切换到新对话" in event.result_text()


# ==== /switch ====


def test_switch_运行中_拒绝执行():
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    with (
        patch.object(
            conv_mod.active_event_registry, "has_active", MagicMock(return_value=True)
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=False),
        ),
    ):
        _run(cmds.switch_conv(event, 1))
    assert REJECT_TEXT == event.result_text()
    context.conversation_manager.switch_conversation.assert_not_awaited()


def test_switch_无运行_正常切换():
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)
    with (
        patch.object(
            conv_mod.active_event_registry, "has_active", MagicMock(return_value=False)
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=False),
        ),
    ):
        _run(cmds.switch_conv(event, 1))
    context.conversation_manager.switch_conversation.assert_awaited_once_with(
        UMO, "cid-1abcd"
    )
    assert "切换到对话" in event.result_text()


# ==== /groupnew ====


def test_groupnew_目标会话运行中_拒绝():
    group_umo = "test:GroupMessage:99999"
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)

    def _has_active(umo, exclude=None):
        return umo == group_umo

    with (
        patch.object(
            conv_mod.active_event_registry, "has_active", _has_active
        ),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=False),
        ),
    ):
        _run(cmds.groupnew_conv(event, "99999"))
    assert REJECT_TEXT == event.result_text()
    context.conversation_manager.new_conversation.assert_not_awaited()


def test_groupnew_当前会话运行_目标空闲_放行():
    """管理员的会话自己在跑不影响给别的群建新对话。"""
    event = FakeEvent()
    context = _context()
    cmds = ConversationCommands(context)

    def _has_active(umo, exclude=None):
        return umo == event.unified_msg_origin

    with (
        patch.object(conv_mod.active_event_registry, "has_active", _has_active),
        patch(
            "astrbot.core.pipeline.process_stage.follow_up.has_active_runner",
            MagicMock(return_value=False),
        ),
    ):
        _run(cmds.groupnew_conv(event, "99999"))
    context.conversation_manager.new_conversation.assert_awaited_once()
    assert "已切换到新对话" in event.result_text()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        with _pinned_wake_prefix():
            fn()
        print(f"{fn.__name__}: PASS")
    print(f"\n{len(fns)} tests passed")
