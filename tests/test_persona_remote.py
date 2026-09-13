"""/persona 远程换人格：会话ID解析、set（规则/对话/自动创建）、unset、reset。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.builtin_stars.builtin_commands.commands.persona import (  # noqa: E402
    PersonaCommands,
)

MODULE = "astrbot.builtin_stars.builtin_commands.commands.persona"

UMO_A = "aiocqhttp:GroupMessage:111111"
UMO_B = "aiocqhttp:GroupMessage:222222"
UMO_WEBCHAT = "webchat:FriendMessage:webchat!user1!thread-9"

ALIAS_111 = SimpleNamespace(umo=UMO_A, user_alias="测试群", auto_name="")
ALIAS_WC = SimpleNamespace(umo=UMO_WEBCHAT, user_alias="", auto_name="网页聊天")


def _event(text: str, umo: str = UMO_A) -> MagicMock:
    event = MagicMock()
    event.message_str = text
    event.unified_msg_origin = umo
    event.get_platform_name = MagicMock(return_value="aiocqhttp")
    event.set_result = MagicMock()
    return event


def _cmds(umos: list[str] | None = None) -> tuple[PersonaCommands, MagicMock]:
    context = MagicMock()
    context.get_db = MagicMock(return_value=MagicMock())
    context.conversation_manager = MagicMock()
    context.persona_manager = MagicMock()
    context.provider_manager = MagicMock()
    cmds = PersonaCommands(context)
    return cmds, context


def _stub_known_umos(cmds: PersonaCommands, umos: list[str]) -> None:
    cmds._known_umos = AsyncMock(return_value=umos)  # type: ignore[method-assign]


def _stub_aliases(context: MagicMock, aliases: list) -> None:
    db = context.get_db.return_value
    db.get_umo_aliases = AsyncMock(return_value=aliases)


def _result_text(event: MagicMock) -> str:
    # mock MessageEventResult：message() 返回 self，文本拼接 chain 里的 Plain
    result = event.set_result.call_args[0][0]
    return "".join(c.text for c in result.chain)


def _stub_sp(
    configs: dict[str, dict],
):
    """按 umo 打桩 sp.get_async / put_async，记录写入。"""

    async def get_async(scope, scope_id, key, default=None):
        if scope == "umo" and key == "session_service_config":
            return configs.get(scope_id, {})
        return default

    writes: list[tuple[str, dict]] = []

    async def put_async(scope, scope_id, key, value):
        if scope == "umo" and key == "session_service_config":
            writes.append((scope_id, value))
            configs[scope_id] = value

    return patch(
        f"{MODULE}.sp",
        SimpleNamespace(
            get_async=get_async,
            put_async=put_async,
            remove_async=AsyncMock(),
        ),
    ), writes


def _stub_conv(context: MagicMock, curr_cid: str | None = None) -> MagicMock:
    cm = context.conversation_manager
    cm.get_curr_conversation_id = AsyncMock(return_value=curr_cid)
    cm.update_conversation_persona_id = AsyncMock()
    cm.new_conversation = AsyncMock(return_value="new-cid")
    cm.update_conversation = AsyncMock()
    return cm


# ==== 会话ID解析 ====


def test_umo_matches_分段精确():
    assert PersonaCommands._umo_matches(UMO_A, "111111")
    assert PersonaCommands._umo_matches(UMO_WEBCHAT, "webchat!user1!thread-9")
    assert PersonaCommands._umo_matches(UMO_WEBCHAT, "user1")
    assert PersonaCommands._umo_matches(UMO_WEBCHAT, "thread-9")
    # 数字部分匹配不做部分匹配：12 不能匹配 111111
    assert not PersonaCommands._umo_matches(UMO_A, "12")
    assert not PersonaCommands._umo_matches(UMO_A, "1111112")
    assert not PersonaCommands._umo_matches(UMO_WEBCHAT, "read")  # thread-9 的子串不算


@pytest.mark.asyncio
async def test_解析_按会话ID段唯一命中():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A, UMO_B, UMO_WEBCHAT])
    _stub_aliases(context, [ALIAS_111, ALIAS_WC])
    candidates, alias_map = await cmds._resolve_targets("222222")
    assert candidates == [UMO_B]
    assert UMO_A not in alias_map or alias_map  # alias_map 可用


@pytest.mark.asyncio
async def test_解析_按昵称命中():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A, UMO_WEBCHAT])
    _stub_aliases(context, [ALIAS_111, ALIAS_WC])
    candidates, _ = await cmds._resolve_targets("测试群")
    assert candidates == [UMO_A]


@pytest.mark.asyncio
async def test_解析_昵称命中但会话已死不生效():
    cmds, context = _cmds()
    # 333333 只剩昵称残留（无对话数据），不应命中
    _stub_known_umos(cmds, [UMO_A])
    dead = SimpleNamespace(umo=UMO_B, user_alias="测试群", auto_name="")
    _stub_aliases(context, [dead])
    candidates, _ = await cmds._resolve_targets("测试群")
    assert candidates == []


@pytest.mark.asyncio
async def test_解析_两个群同名昵称多候选拒绝():
    cmds, context = _cmds()
    # 两个群 auto_name 相同 → 双候选，入口应拒绝执行
    _stub_known_umos(cmds, [UMO_A, UMO_B])
    a1 = SimpleNamespace(umo=UMO_A, user_alias="", auto_name="测试群")
    a2 = SimpleNamespace(umo=UMO_B, user_alias="", auto_name="测试群")
    _stub_aliases(context, [a1, a2])
    candidates, _ = await cmds._resolve_targets("测试群")
    assert candidates == [UMO_A, UMO_B]
    # 走入口确认拒绝执行且不写数据
    sp_patch, writes = _stub_sp({})
    _stub_conv(context, curr_cid="cid-1")
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小助手 测试群")
    with sp_patch:
        await cmds.persona(event)
    assert "匹配到多个会话" in _result_text(event)
    assert writes == []
    context.conversation_manager.new_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_解析_未命中返回空():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    candidates, _ = await cmds._resolve_targets("999999")
    assert candidates == []


@pytest.mark.asyncio
async def test_设置_会话ID多候选拒绝执行():
    cmds, context = _cmds()
    # 线程分段 user1 同时被两个 UMO 包含 → 多候选
    _stub_known_umos(cmds, ["webchat:FriendMessage:webchat!user1!t1", "webchat:FriendMessage:webchat!user1!t2"])
    _stub_aliases(context, [])
    event = _event("/persona 小助手 user1")
    _stub_conv(context, curr_cid="cid-1")
    sp_patch, _ = _stub_sp({})
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    with sp_patch:
        await cmds.persona(event)
    text = _result_text(event)
    assert "匹配到多个会话" in text
    context.conversation_manager.update_conversation_persona_id.assert_not_called()


# ==== set：规则 / 对话 / 自动创建 ====


@pytest.mark.asyncio
async def test_远程set_有规则改规则():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    configs = {UMO_A: {"persona_id": "旧人格", "custom_name": "保留我"}}
    sp_patch, writes = _stub_sp(configs)
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小助手 111111")
    with sp_patch:
        await cmds.persona(event)
    assert writes == [(UMO_A, {"persona_id": "小助手", "custom_name": "保留我"})]
    text = _result_text(event)
    assert "自定义规则人格设为「小助手」" in text
    assert "persona reset 111111" in text
    context.conversation_manager.update_conversation_persona_id.assert_not_called()


@pytest.mark.asyncio
async def test_远程set_有对话改对话():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, writes = _stub_sp({})
    cm = _stub_conv(context, curr_cid="cid-9")
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小助手 111111")
    with sp_patch:
        await cmds.persona(event)
    cm.update_conversation_persona_id.assert_awaited_once_with(UMO_A, "小助手", "cid-9")
    assert writes == []
    text = _result_text(event)
    assert "当前对话的人格设为「小助手」" in text


@pytest.mark.asyncio
async def test_远程set_无对话自动创建带人格():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, _ = _stub_sp({})
    cm = _stub_conv(context, curr_cid=None)
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小助手 111111")
    with sp_patch:
        await cmds.persona(event)
    cm.new_conversation.assert_awaited_once_with(UMO_A, persona_id="小助手")
    text = _result_text(event)
    assert "已自动创建并设为人格「小助手」" in text
    assert "下一条消息即生效" in text


@pytest.mark.asyncio
async def test_本会话set_无对话也自动创建():
    cmds, context = _cmds()
    sp_patch, _ = _stub_sp({})
    cm = _stub_conv(context, curr_cid=None)
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小助手")
    with sp_patch:
        await cmds.persona(event)
    cm.new_conversation.assert_awaited_once_with(UMO_A, persona_id="小助手")
    text = _result_text(event)
    assert "已自动创建并设为人格「小助手」" in text


@pytest.mark.asyncio
async def test_设置_多段拼接是有效人格名按当前会话处理():
    cmds, context = _cmds()
    sp_patch, writes = _stub_sp({})
    cm = _stub_conv(context, curr_cid=None)
    # 旧行为：多段拼接不带空格（"".join）
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小 助 手")
    with sp_patch:
        await cmds.persona(event)
    cm.new_conversation.assert_awaited_once_with(UMO_A, persona_id="小助手")
    assert writes == []


@pytest.mark.asyncio
async def test_设置_人格名不存在不写任何数据():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, writes = _stub_sp({})
    _stub_conv(context, curr_cid=None)
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 不存在 111111")
    with sp_patch:
        await cmds.persona(event)
    assert writes == []
    text = _result_text(event)
    assert "人格「不存在」不存在" in text
    assert "persona list" in text


# ==== unset ====


@pytest.mark.asyncio
async def test_远程unset_有规则移除规则():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    configs = {UMO_A: {"persona_id": "旧人格", "custom_name": "保留我"}}
    sp_patch, writes = _stub_sp(configs)
    event = _event("/persona unset 111111")
    with sp_patch:
        await cmds.persona(event)
    assert writes == [(UMO_A, {"custom_name": "保留我"})]
    assert "已从" in _result_text(event)


@pytest.mark.asyncio
async def test_远程unset_无规则无对话提示():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, _ = _stub_sp({})
    _stub_conv(context, curr_cid=None)
    event = _event("/persona unset 111111")
    with sp_patch:
        await cmds.persona(event)
    text = _result_text(event)
    assert "没有自定义规则人格" in text
    assert "没有进行中的对话" in text


@pytest.mark.asyncio
async def test_本会话unset_无规则有对话写None():
    cmds, context = _cmds()
    sp_patch, _ = _stub_sp({})
    cm = _stub_conv(context, curr_cid="cid-1")
    event = _event("/persona unset")
    with sp_patch:
        await cmds.persona(event)
    cm.update_conversation_persona_id.assert_awaited_once_with(UMO_A, "[%None]", "cid-1")
    assert "取消人格成功" in _result_text(event)


# ==== reset ====


@pytest.mark.asyncio
async def test_远程reset_清空对话历史():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, _ = _stub_sp({})
    cm = _stub_conv(context, curr_cid="cid-5")
    context.get_config = MagicMock(
        return_value={"provider_settings": {"agent_runner_type": "agentic"}},
    )
    context.get_using_provider = MagicMock(return_value=MagicMock())
    context.message_history_manager = MagicMock()
    context.message_history_manager.delete_all = AsyncMock()
    event = _event("/persona reset 111111")
    with sp_patch:
        await cmds.persona(event)
    cm.update_conversation.assert_awaited_once_with(UMO_A, "cid-5", [])
    assert "已重置" in _result_text(event)


@pytest.mark.asyncio
async def test_远程reset_无对话提示无需重置():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, _ = _stub_sp({})
    _stub_conv(context, curr_cid=None)
    context.get_config = MagicMock(
        return_value={"provider_settings": {"agent_runner_type": "agentic"}},
    )
    context.get_using_provider = MagicMock(return_value=MagicMock())
    event = _event("/persona reset 111111")
    with sp_patch:
        await cmds.persona(event)
    assert "无需重置" in _result_text(event)


# ==== 入口分支保护 ====


@pytest.mark.asyncio
async def test_入口_无参数显示帮助():
    cmds, context = _cmds()
    _stub_conv(context, curr_cid=None)
    context.persona_manager.get_default_persona_v3 = AsyncMock(
        return_value={"name": "default"},
    )
    context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=(None, None, None, False),
    )
    sp_patch, _ = _stub_sp({})
    event = _event("/persona")
    with sp_patch:
        await cmds.persona(event)
    text = _result_text(event)
    assert "persona 人格 [会话ID]" in text
    assert "persona reset" in text


@pytest.mark.asyncio
async def test_入口_会话ID未匹配拒绝执行():
    cmds, context = _cmds()
    _stub_known_umos(cmds, [UMO_A])
    _stub_aliases(context, [ALIAS_111])
    sp_patch, writes = _stub_sp({})
    context.provider_manager.personas = [{"name": "小助手", "prompt": "x"}]
    event = _event("/persona 小助手 999999")
    with sp_patch:
        await cmds.persona(event)
    text = _result_text(event)
    assert "未找到会话" in text
    assert writes == []
    context.conversation_manager.new_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_入口_list不受影响():
    cmds, context = _cmds()
    context.persona_manager.get_folder_tree = AsyncMock(return_value=[])
    context.persona_manager.personas = []
    event = _event("/persona list")
    await cmds.persona(event)
    assert "人格列表" in _result_text(event)
