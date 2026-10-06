"""消息防抖验证。

验证行为：
- 窗口内第二条消息被吸收（返回 False），不触发打断、不请求 LLM
- 静默期满后赢家合并缓冲：message_str 按 [Message N] 标注，媒体组件并入赢家消息链
- 单条消息（窗口内无后续）原样放行，message_str 不变
- 关闭开关 / 群聊 / window=0 / 插件 provider_request 路径不受影响
- 状态表用完清理，不泄漏

运行：~/ldmbot/.venv/bin/python tests/test_message_debounce.py
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.message.components import Image, Plain  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
    _DEBOUNCE_STATE,
)


def make_stage(debounce_cfg: dict, private: bool = True):
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    conf = {
        "platform_settings": {"message_debounce": debounce_cfg},
    }
    ctx = MagicMock()
    ctx.plugin_manager.context.get_config.return_value = conf
    stage.ctx = ctx
    stage._is_private = private
    return stage


def make_event(umo: str, text: str, comps=None, private: bool = True):
    event = MagicMock()
    event.unified_msg_origin = umo
    event.message_str = text
    msg_obj = MagicMock()
    msg_obj.message = list(comps or [])
    event.message_obj = msg_obj
    event.get_messages.return_value = list(comps or [])
    event.is_private_chat = lambda: private
    event.get_message_outline = lambda: "[图片]" if (comps and not text) else text
    return event


def run(coro):
    return asyncio.run(coro)


# ---------- 吸收与合并 ----------


def test_absorb_and_merge():
    """窗口内三条连发：前两条被吸收，赢家合并成 [Message N] 标注的文本+组件。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0.3})

    img1, img3 = Image(file="http://x/1.jpg"), Image(file="http://x/3.jpg")

    async def scenario():
        ev1 = make_event("qq:FriendMessage:1", "第一条", [img1])
        t1 = asyncio.create_task(stage._message_debounce_wait(ev1))
        await asyncio.sleep(0.1)
        ev2 = make_event("qq:FriendMessage:1", "第二条")
        r2 = await stage._message_debounce_wait(ev2)
        assert r2 is False, "窗口内第二条应被吸收"
        await asyncio.sleep(0.1)
        ev3 = make_event("qq:FriendMessage:1", "第三条", [img3])
        r3 = await stage._message_debounce_wait(ev3)
        assert r3 is False, "窗口重置后第三条也应被吸收"
        r1 = await t1
        assert r1 is True, "首条静默期满后应放行"
        return ev1

    ev1 = run(scenario())
    assert ev1.message_str.startswith("[Message 1] 第一条\n[Message 2] 第二条\n[Message 3] 第三条\n"), (
        f"合并文本不符: {ev1.message_str!r}"
    )
    assert img1 in ev1.message_obj.message and img3 in ev1.message_obj.message, (
        "被吸收消息的图片组件应并入赢家消息链"
    )
    assert "sent 3 messages" in ev1.message_str, "合并文本末尾应有 system_reminder 提示"
    assert "qq:FriendMessage:1" not in _DEBOUNCE_STATE, "状态表应清理"
    print("test_absorb_and_merge: PASS")


def test_single_message_untouched():
    """窗口内无后续消息：原样放行，message_str 不加任何标记。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0.15})
    ev = make_event("qq:FriendMessage:2", "单独一条")
    assert run(stage._message_debounce_wait(ev)) is True
    assert ev.message_str == "单独一条", "单条消息不应被改写"
    assert "qq:FriendMessage:2" not in _DEBOUNCE_STATE
    print("test_single_message_untouched: PASS")


def test_media_only_absorbed_message():
    """被吸收的是纯图片消息（无文本）：合并用占位 outline。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0.3})

    async def scenario():
        ev1 = make_event("qq:FriendMessage:3", "看图", [])
        t1 = asyncio.create_task(stage._message_debounce_wait(ev1))
        await asyncio.sleep(0.1)
        img = Image(file="http://x/9.jpg")
        ev2 = make_event("qq:FriendMessage:3", "", [img])
        r2 = await stage._message_debounce_wait(ev2)
        assert r2 is False
        return await t1, ev1, img

    r1, ev1, img = run(scenario())
    assert r1 is True
    assert ev1.message_str.startswith("[Message 1] 看图\n[Message 2] [图片]\n"), (
        f"纯媒体消息应用占位 outline: {ev1.message_str!r}"
    )
    assert img in ev1.message_obj.message
    print("test_media_only_absorbed_message: PASS")


# ---------- 开关与边界 ----------


def test_disabled_passthrough():
    """开关关闭：直接放行，不开窗口。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": False, "window": 2.0})
    ev = make_event("qq:FriendMessage:4", "hi")
    assert run(stage._message_debounce_wait(ev)) is True
    assert ev.message_str == "hi"
    assert "qq:FriendMessage:4" not in _DEBOUNCE_STATE
    print("test_disabled_passthrough: PASS")


def test_group_chat_passthrough():
    """群聊：即使开关打开也不防抖（本期限仅私聊）。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 2.0})
    ev = make_event("qq:GroupMessage:5", "hi", private=False)
    assert run(stage._message_debounce_wait(ev)) is True
    assert "qq:FriendMessage:5" not in _DEBOUNCE_STATE
    print("test_group_chat_passthrough: PASS")


def test_zero_window_passthrough():
    """window=0：等同关闭。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0})
    ev = make_event("qq:FriendMessage:6", "hi")
    assert run(stage._message_debounce_wait(ev)) is True
    assert "qq:FriendMessage:6" not in _DEBOUNCE_STATE
    print("test_zero_window_passthrough: PASS")


def test_hard_deadline_caps_sliding():
    """累计等待上限：deadline 一旦触及 hard_deadline 就不再后移。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 50.0, "max_wait": 60.0})

    async def scenario():
        ev1 = make_event("qq:FriendMessage:7", "第一条")
        t1 = asyncio.create_task(stage._message_debounce_wait(ev1))
        await asyncio.sleep(0.05)
        # 把 hard_deadline 拉近，模拟已滑动等待很久
        state = _DEBOUNCE_STATE["qq:FriendMessage:7"]
        loop = asyncio.get_running_loop()
        fake_hard = loop.time() + 0.2
        state["hard_deadline"] = fake_hard
        ev2 = make_event("qq:FriendMessage:7", "第二条")
        assert await stage._message_debounce_wait(ev2) is False
        assert state["deadline"] == fake_hard, (
            "now+50 远超 hard_deadline，deadline 必须被钳在 hard_deadline"
        )
        t1.cancel()
        try:
            await t1
        except asyncio.CancelledError:
            pass

    run(scenario())
    _DEBOUNCE_STATE.clear()
    print("test_hard_deadline_caps_sliding: PASS")


def test_max_wait_forces_release():
    """max_wait 到期后即使仍在连发也强制放行（不被吸收）。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0.3, "max_wait": 1.0})

    async def scenario():
        ev1 = make_event("qq:FriendMessage:9", "第一条")
        t1 = asyncio.create_task(stage._message_debounce_wait(ev1))
        # 持续刷屏超过 max_wait=1s：每 0.2s 发一条
        for i in range(6):
            await asyncio.sleep(0.2)
            ev = make_event("qq:FriendMessage:9", f"刷屏{i}")
            await stage._message_debounce_wait(ev)
        r1 = await t1
        return r1, ev1

    r1, ev1 = run(scenario())
    assert r1 is True, "max_wait 到期后首条应强制放行"
    assert "[Message" in ev1.message_str, "放行时仍应合并已缓冲内容"
    assert "刷屏" in ev1.message_str, "刷屏期间被吸收的消息应在合并内容里"
    _DEBOUNCE_STATE.clear()
    print("test_max_wait_forces_release: PASS")


def test_config_read_per_call():
    """配置实时现读：窗口进行中开关被关掉，后续消息不再被吸收。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0.3})

    async def scenario():
        ev1 = make_event("qq:FriendMessage:8", "第一条")
        t1 = asyncio.create_task(stage._message_debounce_wait(ev1))
        await asyncio.sleep(0.1)
        # WebUI 关掉开关（实时生效，禁缓存）
        stage.ctx.plugin_manager.context.get_config.return_value = {
            "platform_settings": {"message_debounce": {"enable": False, "window": 0.3}}
        }
        ev2 = make_event("qq:FriendMessage:8", "第二条")
        r2 = await stage._message_debounce_wait(ev2)
        assert r2 is True, "开关关闭后新消息应直接放行"
        r1 = await t1
        assert r1 is True
        return ev1

    ev1 = run(scenario())
    assert ev1.message_str == "第一条", "关开关后缓冲不再增长，首条原样放行"
    print("test_config_read_per_call: PASS")


# ---------- 不同会话隔离 ----------


def test_different_umo_isolated():
    """两个会话各自防抖，互不吸收。"""
    _DEBOUNCE_STATE.clear()
    stage = make_stage({"enable": True, "window": 0.3})

    async def scenario():
        ev_a = make_event("qq:FriendMessage:A", "A1")
        ev_b = make_event("qq:FriendMessage:B", "B1")
        t_a = asyncio.create_task(stage._message_debounce_wait(ev_a))
        t_b = asyncio.create_task(stage._message_debounce_wait(ev_b))
        await asyncio.sleep(0.1)
        ev_a2 = make_event("qq:FriendMessage:A", "A2")
        assert await stage._message_debounce_wait(ev_a2) is False
        assert await t_b is True
        assert (await t_a) is True
        return ev_a

    ev_a = run(scenario())
    assert ev_a.message_str.startswith("[Message 1] A1\n[Message 2] A2\n")
    assert "sent 2 messages" in ev_a.message_str, "合并文本应含 system_reminder 提示"
    print("test_different_umo_isolated: PASS")


if __name__ == "__main__":
    test_absorb_and_merge()
    test_single_message_untouched()
    test_media_only_absorbed_message()
    test_disabled_passthrough()
    test_group_chat_passthrough()
    test_zero_window_passthrough()
    test_hard_deadline_caps_sliding()
    test_max_wait_forces_release()
    test_config_read_per_call()
    test_different_umo_isolated()
    print("\n全部通过 ✓")
