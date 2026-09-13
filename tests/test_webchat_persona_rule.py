"""WebChat set/get_session_persona 与自定义规则联动：

- 有规则人格 → set 直接改规则值（[%None] = 从规则移除，其余字段保留），不写对话
- 无规则 → 原逻辑写对话（无对话自动创建）
- get 规则优先返回并带 from_rule 标记，无规则回落对话 persona_id
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.dashboard.services.chat_service import (  # noqa: E402
    ChatService,
    ChatServiceError,
)

MODULE = "astrbot.dashboard.services.chat_service"

UMO = "webchat:FriendMessage:webchat!ldm!sess1"

SESSION = SimpleNamespace(
    creator="ldm",
    platform_id="webchat",
    is_group=False,
    creator_id="ldm",
    session_id="sess1",
)


class FakeConvMgr:
    """模拟 conversation_manager：带当前对话 cid1（persona_id=old_persona）。"""

    def __init__(self) -> None:
        self.convs = {UMO: {"cid1": SimpleNamespace(persona_id="old_persona")}}
        self.curr = {UMO: "cid1"}
        self.updated: list[tuple[str, str, str | None]] = []
        self.created: list[tuple[str, str | None]] = []

    async def get_curr_conversation_id(self, umo: str):
        return self.curr.get(umo)

    async def get_conversation(self, unified_msg_origin: str, conversation_id: str):
        return self.convs.get(unified_msg_origin, {}).get(conversation_id)

    async def update_conversation(
        self,
        unified_msg_origin: str,
        conversation_id: str,
        persona_id: str | None = None,
        **kwargs,
    ):
        self.updated.append((unified_msg_origin, conversation_id, persona_id))
        # 模拟真实行为：同步变更存储的对话对象
        if (
            persona_id is not None
            and unified_msg_origin in self.convs
            and conversation_id in self.convs[unified_msg_origin]
        ):
            self.convs[unified_msg_origin][conversation_id].persona_id = persona_id

    async def new_conversation(
        self, umo: str, platform_id: str | None = None, persona_id: str | None = None
    ):
        self.created.append((umo, persona_id))
        return "new_cid"


def _service() -> tuple[ChatService, FakeConvMgr]:
    svc = ChatService.__new__(ChatService)
    svc.db = MagicMock()
    svc.db.get_platform_session_by_id = AsyncMock(return_value=SESSION)
    svc.conv_mgr = FakeConvMgr()
    return svc, svc.conv_mgr


def _stub_sp(configs: dict[str, dict]):
    """按 umo 打桩 sp.get_async / put_async，返回 (patcher, writes)。"""

    async def get_async(scope, scope_id, key, default=None):
        if scope == "umo" and key == "session_service_config":
            return configs.get(scope_id, {})
        return default

    writes: list[tuple[str, str, dict]] = []

    async def put_async(scope, scope_id, key, value):
        if scope == "umo" and key == "session_service_config":
            writes.append((scope_id, key, dict(value)))
            configs[scope_id] = dict(value)

    fake = SimpleNamespace(get_async=get_async, put_async=put_async)
    return patch(f"{MODULE}.sp", fake), writes


# ==== set_session_persona ====


def test_set_with_rule_updates_rule_value():
    async def _run():
        svc, conv = _service()
        cfg = {"persona_id": "rule_persona", "custom_name": "测试"}
        sp_patch, writes = _stub_sp({UMO: cfg})
        with sp_patch:
            r = await svc.set_session_persona("ldm", "sess1", "new_persona")

        assert r["from_rule"] is True
        assert r["persona_id"] == "new_persona"
        assert cfg["persona_id"] == "new_persona"
        assert cfg["custom_name"] == "测试"  # 其余规则字段保留
        assert conv.updated == []  # 不写对话
        assert conv.created == []
        assert [w for w in writes if w[0] == UMO]  # 规则已写回

    asyncio.run(_run())


def test_set_with_rule_unset_removes_persona_only():
    async def _run():
        svc, conv = _service()
        cfg = {"persona_id": "rule_persona", "custom_name": "测试"}
        sp_patch, writes = _stub_sp({UMO: cfg})
        with sp_patch:
            r = await svc.set_session_persona("ldm", "sess1", "[%None]")

        assert r["from_rule"] is True
        assert "persona_id" not in cfg
        assert cfg["custom_name"] == "测试"
        assert conv.updated == []
        assert writes  # 有写回

    asyncio.run(_run())


def test_set_without_rule_writes_conversation():
    async def _run():
        svc, conv = _service()
        sp_patch, writes = _stub_sp({UMO: {"custom_name": "测试"}})  # 规则无 persona_id
        with sp_patch:
            r = await svc.set_session_persona("ldm", "sess1", "conv_persona")

        assert r["from_rule"] is False
        assert conv.updated == [(UMO, "cid1", "conv_persona")]
        assert conv.created == []
        assert writes == []  # 不写规则

    asyncio.run(_run())


def test_set_without_rule_and_conversation_creates_one():
    async def _run():
        svc, conv = _service()
        conv.curr = {}  # 无当前对话
        sp_patch, _ = _stub_sp({})
        with sp_patch:
            r = await svc.set_session_persona("ldm", "sess1", "fresh_persona")

        assert r["from_rule"] is False
        assert conv.created == [(UMO, "fresh_persona")]

    asyncio.run(_run())


# ==== get_session_persona ====


def test_get_with_rule_returns_rule_value():
    async def _run():
        svc, _ = _service()
        sp_patch, _ = _stub_sp({UMO: {"persona_id": "rule_persona", "custom_name": "测试"}})
        with sp_patch:
            r = await svc.get_session_persona("ldm", "sess1")

        assert r["persona_id"] == "rule_persona"
        assert r["from_rule"] is True

    asyncio.run(_run())


def test_get_after_rule_removed_falls_back_to_conversation():
    async def _run():
        svc, conv = _service()
        conv.convs[UMO]["cid1"].persona_id = "conv_persona"
        sp_patch, _ = _stub_sp({UMO: {"custom_name": "测试"}})  # 规则无 persona_id
        with sp_patch:
            r = await svc.get_session_persona("ldm", "sess1")

        assert r["persona_id"] == "conv_persona"
        assert r["from_rule"] is False

    asyncio.run(_run())


def test_get_without_conversation_returns_none():
    async def _run():
        svc, conv = _service()
        conv.curr = {}
        sp_patch, _ = _stub_sp({})
        with sp_patch:
            r = await svc.get_session_persona("ldm", "sess1")

        assert r["persona_id"] is None
        assert r["from_rule"] is False

    asyncio.run(_run())


# ==== 权限/存在性 ====


def test_set_missing_session_raises():
    async def _run():
        svc, _ = _service()
        svc.db.get_platform_session_by_id = AsyncMock(return_value=None)
        sp_patch, _ = _stub_sp({})
        with sp_patch:
            with pytest.raises(ChatServiceError):
                await svc.set_session_persona("ldm", "sess1", "x")

    asyncio.run(_run())
