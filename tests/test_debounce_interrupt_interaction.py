"""防抖等待中的事件不应被「打断旧回复」逻辑停止。

回归场景：私聊连发两条图片消息。首条进入防抖等待（agent_debounce_waiting），
第二条到达时触发 _maybe_interrupt_active_reply，若打断逻辑把防抖等待中的首条
标记为 agent_stop_requested，首条合并后请求 LLM 会被立即停止（日志表现为
「Agent execution was requested to stop by user」且用量全 0）。

修复：打断只针对真正在跑的回复；防抖等待中的事件通过
agent_debounce_waiting 标记被 has_active / request_agent_stop_all 跳过。

运行：uv run pytest tests/test_debounce_interrupt_interaction.py -q
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.utils.active_event_registry import (  # noqa: E402
    ActiveEventRegistry,
)


def make_event(umo: str, waiting: bool = False):
    event = MagicMock()
    event.unified_msg_origin = umo
    event.get_extra.return_value = waiting
    event.set_extra = MagicMock()
    return event


def test_request_agent_stop_skips_debounce_waiting():
    """打断请求停止时跳过防抖等待中的事件。"""
    reg = ActiveEventRegistry()
    umo = "qq:FriendMessage:100"

    winner = make_event(umo, waiting=True)  # 防抖等待中的首条（赢家）
    runner_ev = make_event(umo, waiting=False)  # 旧回复事件
    reg.register(winner)
    reg.register(runner_ev)

    count = reg.request_agent_stop_all(
        umo, exclude=None, extra_updates={"agent_user_aborted": True}
    )
    assert count == 1, f"只应停止旧回复事件，实际停止 {count}"
    # 赢家不能被标记停止
    assert not any(
        call[0][0] == "agent_stop_requested" for call in winner.set_extra.call_args_list
    ), "防抖等待中的事件不应被标记 agent_stop_requested"
    # 旧回复事件被标记
    assert any(
        call[0][0] == "agent_stop_requested" and call[0][1] is True
        for call in runner_ev.set_extra.call_args_list
    ), "旧回复事件应被标记 agent_stop_requested"
    print("test_request_agent_stop_skips_debounce_waiting: PASS")


def test_has_active_skips_debounce_waiting():
    """只有防抖等待事件时视为无活跃回复，不触发打断。"""
    reg = ActiveEventRegistry()
    umo = "qq:FriendMessage:101"
    winner = make_event(umo, waiting=True)
    reg.register(winner)
    assert reg.has_active(umo) is False, "仅防抖等待中的事件不应算作活跃回复"
    assert reg.count(umo) == 1, "count 应保留原始语义（含防抖等待）"
    print("test_has_active_skips_debounce_waiting: PASS")


def test_mixed_active_and_waiting():
    """旧回复 + 防抖等待并存：has_active 仍为 True（旧回复可被打断）。"""
    reg = ActiveEventRegistry()
    umo = "qq:FriendMessage:102"
    winner = make_event(umo, waiting=True)
    runner_ev = make_event(umo, waiting=False)
    reg.register(winner)
    reg.register(runner_ev)
    assert reg.has_active(umo) is True, "存在非防抖等待的活跃事件时应为 True"
    print("test_mixed_active_and_waiting: PASS")


def test_has_active_respects_exclude():
    """exclude 语义不受影响：被打断的新消息自身不算活跃。"""
    reg = ActiveEventRegistry()
    umo = "qq:FriendMessage:103"
    current = make_event(umo, waiting=False)
    reg.register(current)
    assert reg.has_active(umo, exclude=current) is False
    print("test_has_active_respects_exclude: PASS")


if __name__ == "__main__":
    test_request_agent_stop_skips_debounce_waiting()
    test_has_active_skips_debounce_waiting()
    test_mixed_active_and_waiting()
    test_has_active_respects_exclude()
    print("\n全部通过 ✓")
