"""工具中断上下文落库验证。

验证行为：
- 工具被中断：串行执行下，已完成的工具保留真实结果，被中断的工具写中断提示，
  未执行的工具写未执行提示，不追加 assistant 停止标记（避免重复）
- 纯文本被停止：assistant 末尾仍写入英文停止标记

运行：~/ldmbot/.venv/bin/python tests/test_tool_interrupt_context.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.agent.message import Message, ToolCallMessageSegment  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
    _ToolExecutionInterrupted,
)
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse  # noqa: E402

INTERRUPT_REMINDER = "was interrupted because the user actively requested to stop"
NOT_EXECUTED_REMINDER = "was not executed because the user actively requested to stop"


def make_event(**extras):
    event = MagicMock()
    store = dict(extras)

    def get_extra(key, default=None):
        return store.get(key, default)

    def set_extra(key, value):
        store[key] = value

    event.get_extra.side_effect = get_extra
    event.set_extra.side_effect = set_extra
    event.unified_msg_origin = "webchat:FriendMessage:webchat!ldm!s1"
    return event


def _plain(msg) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        return "".join(getattr(p, "text", "") or "" for p in msg.content)
    return ""


def make_runner():
    runner = ToolLoopAgentRunner.__new__(ToolLoopAgentRunner)
    runner.run_context = SimpleNamespace(messages=[])
    runner.streaming = False
    runner._streamed_assistant_text = ""
    runner._aborted = False
    runner.stats = SimpleNamespace(end_time=0)
    runner.agent_hooks = SimpleNamespace(on_agent_done=AsyncMock())
    runner._transition_state = MagicMock()
    runner._resolve_unconsumed_follow_ups = MagicMock()
    return runner


def make_llm_resp(tool_ids, tool_names):
    return LLMResponse(
        role="assistant",
        completion_text="",
        tools_call_name=tool_names,
        tools_call_args=[{} for _ in tool_names],
        tools_call_ids=tool_ids,
    )


# ---------- internal.py：工具中断不追加 assistant 停止标记 ----------


def test_internal_preserves_tool_results_no_assistant_note():
    """internal：有 tool 结果时原样保留，不重写真实结果、不追加 assistant 停止标记。"""
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = make_event(agent_force_stop=True)
    tool_calls = [
        {
            "type": "function",
            "id": "call_1",
            "function": {"name": "test", "arguments": "{}"},
        },
    ]
    messages = [
        Message(role="user", content="调用工具"),
        Message(role="assistant", content=None, tool_calls=tool_calls),
        Message(role="tool", content="第一个工具的真实结果", tool_call_id="call_1"),
    ]
    out = stage._apply_interrupt_to_messages(
        event,
        messages,
        runner_aborted=True,
        force_stopped=True,
    )
    assert len(out) == 3, f"不应新增消息，实际 {len(out)} 条"
    assert _plain(out[2]) == "第一个工具的真实结果", "真实结果不应被重写"
    assert all("manually stopped" not in _plain(m) for m in out), (
        "不应追加 assistant 停止标记"
    )
    print("test_internal_preserves_tool_results_no_assistant_note: PASS")


# ---------- runner：收尾按工具逐个写结果 ----------


def test_runner_completed_kept_interrupted_marked():
    """两个工具：第一个已完成保留真实结果，第二个被中断写中断提示。"""
    runner = make_runner()
    llm_resp = make_llm_resp(["call_1", "call_2"], ["test", "file_read"])
    completed = [
        ToolCallMessageSegment(
            role="tool",
            tool_call_id="call_1",
            content="test 工具的真实结果",
        ),
    ]
    asyncio.run(
        runner._finalize_aborted_step(
            llm_resp,
            completed_blocks=completed,
            interrupted_tool_call_id="call_2",
        )
    )
    msgs = runner.run_context.messages
    assert msgs[0].role == "assistant" and msgs[0].tool_calls
    assert msgs[1].role == "tool" and msgs[1].tool_call_id == "call_1"
    assert _plain(msgs[1]) == "test 工具的真实结果", "已完成工具的真实结果应保留"
    assert msgs[2].role == "tool" and msgs[2].tool_call_id == "call_2"
    text2 = _plain(msgs[2])
    assert INTERRUPT_REMINDER in text2 and "The tool 'file_read'" in text2, (
        f"被中断的工具应写中断提示，实际: {text2}"
    )
    assert NOT_EXECUTED_REMINDER not in text2
    assert all("manually stopped" not in _plain(m) for m in msgs), (
        "不应追加 assistant 停止标记"
    )
    print("test_runner_completed_kept_interrupted_marked: PASS")


def test_runner_not_executed_tool_gets_note():
    """三个工具、中断在第二个：第三个未执行的工具写未执行提示。"""
    runner = make_runner()
    llm_resp = make_llm_resp(["call_1", "call_2", "call_3"], ["a", "b", "c"])
    completed = [
        ToolCallMessageSegment(
            role="tool",
            tool_call_id="call_1",
            content="a 的真实结果",
        ),
    ]
    asyncio.run(
        runner._finalize_aborted_step(
            llm_resp,
            completed_blocks=completed,
            interrupted_tool_call_id="call_2",
        )
    )
    msgs = runner.run_context.messages
    assert _plain(msgs[1]) == "a 的真实结果"
    assert INTERRUPT_REMINDER in _plain(msgs[2])
    text3 = _plain(msgs[3])
    assert NOT_EXECUTED_REMINDER in text3 and "The tool 'c'" in text3, (
        f"未执行的工具应写未执行提示，实际: {text3}"
    )
    print("test_runner_not_executed_tool_gets_note: PASS")


def test_runner_abort_before_any_tool():
    """LLM 响应拿到后、工具执行前中断：没有已完成结果，工具写未执行提示。"""
    runner = make_runner()
    llm_resp = make_llm_resp(["call_1"], ["test"])
    asyncio.run(runner._finalize_aborted_step(llm_resp))
    msgs = runner.run_context.messages
    assert msgs[0].role == "assistant" and msgs[0].tool_calls
    text1 = _plain(msgs[1])
    assert NOT_EXECUTED_REMINDER in text1, f"应写未执行提示，实际: {text1}"
    print("test_runner_abort_before_any_tool: PASS")


def test_interrupt_exception_carries_context():
    """中断异常能携带已完成结果与被中断工具 ID。"""
    completed = [
        ToolCallMessageSegment(role="tool", tool_call_id="call_1", content="结果"),
    ]
    exc = _ToolExecutionInterrupted(
        "msg",
        completed_blocks=completed,
        interrupted_tool_call_id="call_2",
    )
    assert exc.completed_blocks[0].tool_call_id == "call_1"
    assert exc.interrupted_tool_call_id == "call_2"
    print("test_interrupt_exception_carries_context: PASS")


# ---------- 纯文本停止路径（原有行为，不能丢） ----------


def test_text_stop_still_writes_assistant_note():
    """纯文本被停止（无工具调用）：assistant 末尾仍写入英文停止标记。"""
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = make_event(agent_force_stop=True, _delivered_llm_plain_text="hello")
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello world extra"),
    ]
    out = stage._apply_interrupt_to_messages(
        event,
        messages,
        runner_aborted=True,
        force_stopped=True,
        llm_response=LLMResponse(role="assistant", completion_text="hello world extra"),
    )
    text = _plain(out[-1])
    assert "The user manually stopped this response." in text
    assert "<system_reminder>" in text
    print("test_text_stop_still_writes_assistant_note: PASS")


# ---------- 工具后文本中断：文本末尾追加停止标记 ----------


def test_tool_then_text_interrupt_appends_note():
    """工具成功执行后进入文本生成阶段被中断：文本末尾追加停止标记，工具结果原样保留。

    对应实测场景（context.txt）：调用工具 → 工具返回「测试调用」→ assistant
    开始输出赤壁赋 → 用户在文本生成中点停止。
    """
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = make_event(agent_force_stop=True)
    tool_calls = [
        {
            "type": "function",
            "id": "call_test1",
            "function": {"name": "test", "arguments": '{"content": "测试调用", "sleep": 0}'},
        },
    ]
    messages = [
        Message(role="user", content="调用一次工具之后输出赤壁赋"),
        Message(role="assistant", content=None, tool_calls=tool_calls),
        Message(role="tool", content="测试调用", tool_call_id="call_test1"),
        Message(
            role="assistant",
            content=[{"type": "text", "text": "# 前赤壁赋\n苏轼\n\n壬戌之秋，七月既望，"}],
        ),
    ]
    out = stage._apply_interrupt_to_messages(
        event,
        messages,
        runner_aborted=True,
        force_stopped=True,
        llm_response=LLMResponse(
            role="assistant",
            completion_text="# 前赤壁赋\n苏轼\n\n壬戌之秋，七月既望，",
        ),
    )
    # 工具结果原样保留
    assert _plain(out[2]) == "测试调用", "工具真实结果不应被改动"
    # 文本回复末尾追加停止标记
    text = _plain(out[3])
    assert "The user manually stopped this response." in text, (
        f"文本被中断应在末尾追加停止标记，实际: {text}"
    )
    assert "壬戌之秋" in text, "已生成的文本应保留"
    assert len(out) == 4, "不应新增消息"
    print("test_tool_then_text_interrupt_appends_note: PASS")


def test_multi_turn_old_text_reply_not_touched():
    """多轮场景：本轮工具阶段被中断时，上一轮的旧文本回复不被误追加停止标记。"""
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = make_event(agent_force_stop=True)
    tool_calls = [
        {
            "type": "function",
            "id": "call_t2",
            "function": {"name": "test", "arguments": "{}"},
        },
    ]
    messages = [
        Message(role="user", content="第一轮问题"),
        Message(role="assistant", content="第一轮的旧回复"),
        Message(role="user", content="第二轮调用工具"),
        Message(role="assistant", content=None, tool_calls=tool_calls),
        Message(
            role="tool",
            content="<system_reminder>The tool 'test' was interrupted because the user actively requested to stop. The execution was incomplete.</system_reminder>",
            tool_call_id="call_t2",
        ),
    ]
    out = stage._apply_interrupt_to_messages(
        event,
        messages,
        runner_aborted=True,
        force_stopped=True,
    )
    assert len(out) == 5, "不应新增消息"
    assert _plain(out[1]) == "第一轮的旧回复", "上一轮旧回复不应被追加停止标记"
    assert all("manually stopped" not in _plain(m) for m in out), (
        "工具阶段被中断时不应出现 assistant 停止标记"
    )
    print("test_multi_turn_old_text_reply_not_touched: PASS")


if __name__ == "__main__":
    test_internal_preserves_tool_results_no_assistant_note()
    test_runner_completed_kept_interrupted_marked()
    test_runner_not_executed_tool_gets_note()
    test_runner_abort_before_any_tool()
    test_interrupt_exception_carries_context()
    test_text_stop_still_writes_assistant_note()
    test_tool_then_text_interrupt_appends_note()
    test_multi_turn_old_text_reply_not_touched()
    print("\n全部通过 ✓")
