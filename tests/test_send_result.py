"""验证 event.send() 透传发送结果（MessageSendResult）。

覆盖：
- aiocqhttp 群聊/私聊 send 返回协议端响应包装的 MessageSendResult（message_id 统一字符串）
- 空消息链未发送时返回 None
- 合并转发（Node）经 call_action 的响应透传
- 合并转发与普通消息混排拆分发送时返回最后一次的响应
- WebChat send 返回本地生成的 message_id
- 基类 send 不真正发送，返回 None

运行：python tests/test_send_result.py
也可用 pytest 收集（async 用例已带 pytest.mark.asyncio 标记）。
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.message.components import Node, Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.platform.astr_message_event import AstrMessageEvent  # noqa: E402
from astrbot.core.platform.message_send_result import MessageSendResult  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    AiocqhttpMessageEvent,
)
from astrbot.core.platform.sources.webchat.webchat_event import (  # noqa: E402
    WebChatMessageEvent,
)


def _make_aiocqhttp_event(group_id: str = "", sender_id: str = "10001") -> AiocqhttpMessageEvent:
    platform_meta = PlatformMetadata(name="aiocqhttp", description="t", id="qq")
    message_obj = SimpleNamespace(
        type=MessageType.GROUP_MESSAGE if group_id else MessageType.FRIEND_MESSAGE,
        group_id=group_id,
        sender=SimpleNamespace(user_id=sender_id, nickname="测试用户"),
        raw_message=MagicMock(self_id="10086"),
        self_id="10086",
    )
    bot = MagicMock()
    return AiocqhttpMessageEvent(
        "hi", message_obj, platform_meta, group_id or sender_id, bot
    )


@pytest.mark.asyncio
async def test_aiocqhttp_group_send_returns_message_id():
    event = _make_aiocqhttp_event(group_id="123456")
    event.bot.send_group_msg = AsyncMock(return_value={"message_id": 987654})

    result = await event.send(MessageChain([Plain("hello")]))

    assert isinstance(result, MessageSendResult)
    assert result.message_id == "987654"
    assert result.raw == {"message_id": 987654}
    event.bot.send_group_msg.assert_awaited_once()


@pytest.mark.asyncio
async def test_aiocqhttp_private_send_returns_message_id():
    event = _make_aiocqhttp_event(sender_id="10001")
    event.bot.send_private_msg = AsyncMock(return_value={"message_id": "abc-123"})

    result = await event.send(MessageChain([Plain("hello")]))

    assert result is not None
    assert result.message_id == "abc-123"
    event.bot.send_private_msg.assert_awaited_once()


@pytest.mark.asyncio
async def test_aiocqhttp_empty_chain_returns_none():
    event = _make_aiocqhttp_event(group_id="123456")
    event.bot.send_group_msg = AsyncMock(return_value={"message_id": 1})

    # 全空白 Plain 会被过滤，未发生任何发送
    result = await event.send(MessageChain([Plain("  \n")]))

    assert result is None
    event.bot.send_group_msg.assert_not_awaited()


@pytest.mark.asyncio
async def test_aiocqhttp_forward_msg_returns_message_id():
    event = _make_aiocqhttp_event(group_id="123456")
    event.bot.call_action = AsyncMock(
        return_value={"message_id": 555, "forward_id": "fwd-1"}
    )

    node = Node(content=[Plain("转发内容")], uin="10086", name="机器人")
    result = await event.send(MessageChain([node]))

    assert result is not None
    assert result.message_id == "555"
    assert result.raw == {"message_id": 555, "forward_id": "fwd-1"}
    event.bot.call_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_aiocqhttp_mixed_chain_returns_last_result():
    event = _make_aiocqhttp_event(group_id="123456")
    event.bot.call_action = AsyncMock(return_value={"message_id": 111})
    event.bot.send_group_msg = AsyncMock(return_value={"message_id": 222})

    node = Node(content=[Plain("转发内容")], uin="10086", name="机器人")
    result = await event.send(MessageChain([node, Plain("普通消息")]))

    # 拆分两次发送（先转发后普通），返回最后一次的响应
    assert result is not None
    assert result.message_id == "222"
    event.bot.call_action.assert_awaited_once()
    event.bot.send_group_msg.assert_awaited_once()


@pytest.mark.asyncio
async def test_webchat_send_returns_local_message_id():
    platform_meta = PlatformMetadata(name="webchat", description="t", id="webchat")
    message_obj = SimpleNamespace(message_id="web-777", type=None, message=[])
    event = WebChatMessageEvent("hi", message_obj, platform_meta, "webchat!Default!c1")

    result = await event.send(MessageChain([Plain("hello")]))

    assert isinstance(result, MessageSendResult)
    assert result.message_id == "web-777"
    assert isinstance(result.raw, str)


@pytest.mark.asyncio
async def test_base_send_returns_none():
    platform_meta = PlatformMetadata(name="test", description="t", id="test")
    message_obj = SimpleNamespace(
        type=MessageType.FRIEND_MESSAGE,
        sender=SimpleNamespace(user_id="u1", nickname="测试用户"),
    )
    event = AstrMessageEvent("hi", message_obj, platform_meta, "u1")

    result = await event.send(MessageChain([Plain("hello")]))

    assert result is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
