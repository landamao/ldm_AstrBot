"""验证 WebChat 重试按钮的 can_regenerate 判定与 llm_error 重试分支。

覆盖场景：
1. 正常 AI 回复（checkpoint 在 conversation history）→ can_regenerate=True
2. llm_error 报错消息（checkpoint 不在 history）→ can_regenerate=True（必须可重试）
3. 插件/指令消息（checkpoint 不在 history 且无 llm_error part）→ can_regenerate=False
4. prepare_regenerate_message_payload 对 llm_error 走 _prepare_llm_error_regenerate
5. _prepare_llm_error_regenerate 无 checkpoint 时退回最后一条用户消息

运行：~/ldmbot/.venv/bin/python tests/test_chat_regenerate.py
"""

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.dashboard.services.chat_service import ChatService  # noqa: E402


class FakeRecord:
    def __init__(self, content, checkpoint_id=None, rid=None):
        self.content = content
        self.llm_checkpoint_id = checkpoint_id
        self.id = rid
        self.platform_id = "webchat"
        self.user_id = "conv-1"


def make_bot_content(part_type="plain", text="hi"):
    return {"type": "bot", "message": [{"type": part_type, "text": text}]}


def make_llm_error_content():
    return {
        "type": "bot",
        "message": [{"type": "llm_error", "model": "m", "code": "", "detail": "失败"}],
    }


def build_history(checkpoint_id):
    """构造一条带 checkpoint 的 conversation history。"""
    return [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        {"role": "_checkpoint", "content": {"id": checkpoint_id}},
    ]


def make_service():
    service = ChatService.__new__(ChatService)
    service.db = MagicMock()
    service.conv_mgr = MagicMock()
    service.platform_history_mgr = MagicMock()
    service.umop_config_router = MagicMock()
    service.running_convs = {}
    return service


async def test_can_regenerate():
    service = make_service()
    ckpt = "ckpt-ai-1"
    history = build_history(ckpt)

    # 1. 正常 AI 回复：checkpoint 在历史中
    rec_ai = FakeRecord(make_bot_content(), checkpoint_id=ckpt)
    assert service._can_regenerate_message(rec_ai, history) is True

    # 2. llm_error 报错消息：checkpoint 不在历史中也必须可重试
    rec_err = FakeRecord(make_llm_error_content(), checkpoint_id="ckpt-lost-1")
    assert service._can_regenerate_message(rec_err, history) is True
    # 无 checkpoint 的 llm_error 也可重试
    rec_err2 = FakeRecord(make_llm_error_content(), checkpoint_id=None)
    assert service._can_regenerate_message(rec_err2, history) is True

    # 3. 插件消息：checkpoint 不在历史 → 不可重试
    rec_plugin = FakeRecord(make_bot_content(), checkpoint_id="ckpt-plugin-1")
    assert service._can_regenerate_message(rec_plugin, history) is False

    # 4. user 消息不可重试
    rec_user = FakeRecord({"type": "user", "message": []}, checkpoint_id=ckpt)
    assert service._can_regenerate_message(rec_user, history) is False

    print("test_can_regenerate: PASS (4 断言)")


async def test_regenerate_llm_error_branch():
    """prepare_regenerate_message_payload 对 llm_error 走 _prepare_llm_error_regenerate。"""
    service = make_service()

    session = MagicMock()
    session.session_id = "conv-1"
    session.platform_id = "webchat"
    session.creator = "ldm"

    # llm_error 消息，checkpoint 存在，但 conversation history 为空（conversations 表无记录）
    target = FakeRecord(make_llm_error_content(), checkpoint_id="ckpt-err-1", rid=100)
    service.db.get_platform_session_by_id = AsyncMock(return_value=session)
    service.db.get_platform_message_history_by_id = AsyncMock(return_value=target)

    # 同 checkpoint 的 user 消息
    user_rec = FakeRecord({"type": "user", "message": [{"type": "plain", "text": "hi"}]}, checkpoint_id="ckpt-err-1", rid=99)
    service.get_sorted_platform_history = AsyncMock(return_value=[user_rec, target])
    service.load_current_conversation_history = AsyncMock(return_value=("", []))
    service.db.delete_webchat_threads_by_parent_message_ids = AsyncMock(return_value=[])
    service.delete_threads_by_ids = AsyncMock()
    service.platform_history_mgr.delete_by_id = AsyncMock()
    service.platform_history_mgr.update = AsyncMock()

    # 构造 payload 输入
    data = {"session_id": "conv-1", "message_id": 100}

    payload = await service.prepare_regenerate_message_payload("ldm", data)
    assert payload["_skip_user_history"] is True
    assert payload["_llm_checkpoint_id"] != "ckpt-err-1"
    assert payload["session_id"] == "conv-1"
    assert payload["message"] == [{"type": "plain", "text": "hi"}]
    service.platform_history_mgr.delete_by_id.assert_awaited_once_with(100)
    service.platform_history_mgr.update.assert_awaited_once()
    print("test_regenerate_llm_error_branch: PASS")


async def test_regenerate_llm_error_no_checkpoint():
    """无 checkpoint 的 llm_error：退回最后一条用户消息。"""
    service = make_service()

    session = MagicMock()
    session.session_id = "conv-1"
    session.platform_id = "webchat"
    session.creator = "ldm"

    target = FakeRecord(make_llm_error_content(), checkpoint_id=None, rid=200)
    service.db.get_platform_session_by_id = AsyncMock(return_value=session)
    service.db.get_platform_message_history_by_id = AsyncMock(return_value=target)

    user_rec = FakeRecord({"type": "user", "message": [{"type": "plain", "text": "last"}]}, checkpoint_id=None, rid=199)
    service.get_sorted_platform_history = AsyncMock(return_value=[user_rec, target])
    service.load_current_conversation_history = AsyncMock(return_value=("", []))
    service.db.delete_webchat_threads_by_parent_message_ids = AsyncMock(return_value=[])
    service.delete_threads_by_ids = AsyncMock()
    service.platform_history_mgr.delete_by_id = AsyncMock()
    service.platform_history_mgr.update = AsyncMock()

    payload = await service.prepare_regenerate_message_payload("ldm", {"session_id": "conv-1", "message_id": 200})
    assert payload["_skip_user_history"] is True
    assert payload["message"] == [{"type": "plain", "text": "last"}]
    service.platform_history_mgr.delete_by_id.assert_awaited_once_with(200)
    print("test_regenerate_llm_error_no_checkpoint: PASS")


async def test_regenerate_plugin_message_raises():
    """插件消息（checkpoint 不在历史、非 llm_error）重试仍报 Linked checkpoint not found。"""
    service = make_service()

    session = MagicMock()
    session.session_id = "conv-1"
    session.platform_id = "webchat"
    session.creator = "ldm"

    target = FakeRecord(make_bot_content(), checkpoint_id="ckpt-plugin-1", rid=300)
    service.db.get_platform_session_by_id = AsyncMock(return_value=session)
    service.db.get_platform_message_history_by_id = AsyncMock(return_value=target)
    service.load_current_conversation_history = AsyncMock(return_value=("", []))

    from astrbot.dashboard.services.chat_service import ChatServiceError

    try:
        await service.prepare_regenerate_message_payload("ldm", {"session_id": "conv-1", "message_id": 300})
    except ChatServiceError as exc:
        assert "Linked checkpoint not found" in str(exc)
        print("test_regenerate_plugin_message_raises: PASS")
        return
    raise AssertionError("插件消息重试应当报 Linked checkpoint not found")


async def main():
    await test_can_regenerate()
    await test_regenerate_llm_error_branch()
    await test_regenerate_llm_error_no_checkpoint()
    await test_regenerate_plugin_message_raises()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
