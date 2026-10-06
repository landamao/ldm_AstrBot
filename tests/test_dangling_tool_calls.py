"""max_step 强制收尾悬空工具调用修复验证（对应上游 issue AstrBot#9912 / PR #9913）。

验证行为：
- 工具集已被移除（强制收尾步）时，模型幻觉返回的工具调用被剥离并按
  普通回复收尾：运行进入 DONE、用户能收到最终回复，上下文与
  req.tool_calls_result 中不出现悬空的 assistant(tool_calls) 消息
- 任何 tool_call_id 缺少配对的 tool 结果时，自动补占位结果块（逐个 id
  配对检查，数量相等不代表配对），上下文永不出现协议非法的悬空消息
- _save_to_history 落库前丢弃历史中已中毒的悬空 assistant(tool_calls) 组

运行：python tests/test_dangling_tool_calls.py
"""

import asyncio
import sys
from pathlib import Path

from mcp.types import CallToolResult, TextContent

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.agent.message import Message, ToolCallMessageSegment  # noqa: E402
from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
    _HandleFunctionToolsResult,
)
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import (  # noqa: E402
    LLMResponse,
    ProviderRequest,
    TokenUsage,
)

CURRENT_QUESTION = "current question"


def make_tool():
    return FunctionTool(
        name="test_tool",
        description="a test tool",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


class MockHooks:
    async def on_agent_begin(self, run_context):
        pass

    async def on_tool_start(self, run_context, tool, tool_args):
        pass

    async def on_tool_end(self, run_context, tool, tool_args, tool_result):
        pass

    async def on_agent_done(self, run_context, llm_response):
        pass


class MockToolExecutor(BaseFunctionToolExecutor):
    async def execute(self, tool, run_context, **tool_args):
        yield CallToolResult(content=[TextContent(type="text", text="工具执行结果")])


class MockProvider:
    def __init__(self):
        self.provider_config = {
            "id": "mock-provider",
            "model": "mock-model",
        }
        self.responses = []
        self.received_contexts = []

    async def text_chat(self, **kwargs):
        self.received_contexts.append(kwargs["contexts"])
        return self.responses.pop(0)


def tool_call_response(ids, names=None, args=None):
    return LLMResponse(
        role="assistant",
        completion_text="",
        tools_call_name=names or ["test_tool"] * len(ids),
        tools_call_args=args or [{"query": "test"}] * len(ids),
        tools_call_ids=ids,
        usage=TokenUsage(input_other=10, output=5),
    )


def make_request(contexts, prompt, func_tool=None):
    return ProviderRequest(
        prompt=prompt,
        session_id="test-session",
        contexts=contexts,
        func_tool=func_tool,
    )


def make_runner(provider, request):
    runner = ToolLoopAgentRunner()
    asyncio.run(
        runner.reset(
            provider=provider,
            request=request,
            run_context=ContextWrapper(context=None),
            tool_executor=MockToolExecutor(),
            agent_hooks=MockHooks(),
            streaming=False,
        )
    )
    return runner


async def run_one_step(runner):
    responses = []
    async for resp in runner.step():
        responses.append(resp)
    return responses


# ---------- 层 1：幻觉调用按普通回复收尾 ----------


def test_hallucinated_tool_call_finalizes_as_plain_response():
    """模拟 run_agent 强制收尾步：func_tool 被置空后模型仍返回工具调用，
    应剥离并按普通回复收尾，而不是静默无回复 + 悬空消息。"""
    provider = MockProvider()
    provider.responses.append(
        LLMResponse(
            role="assistant",
            completion_text="我会使用工具来完成这个任务",
            tools_call_name=["non_existent_tool"],
            tools_call_args=[{"query": "test"}],
            tools_call_ids=["call_hallucinated"],
            usage=TokenUsage(input_other=10, output=5),
        )
    )
    request = make_request(
        contexts=[{"role": "user", "content": CURRENT_QUESTION}],
        prompt=None,
        func_tool=ToolSet(tools=[make_tool()]),
    )
    runner = make_runner(provider, request)
    # 复现强制收尾：工具被移除
    runner.req.func_tool = None

    responses = asyncio.run(run_one_step(runner))

    assert runner.done(), "运行应进入 DONE 而不是停留在 RUNNING"
    final = [r for r in responses if r.type == "llm_result"]
    assert final, "用户应收到最终 llm_result 回复"
    dangling = [
        m
        for m in runner.run_context.messages
        if getattr(m, "tool_calls", None) is not None
    ]
    assert dangling == [], "上下文不应包含 assistant(tool_calls) 消息"
    assert not runner.req.tool_calls_result, (
        "不应追加悬空的 ToolCallsResult 记录"
    )
    last = runner.run_context.messages[-1]
    assert last.role == "assistant" and last.tool_calls is None
    print("test_hallucinated_tool_call_finalizes_as_plain_response: PASS")


def test_pure_hallucination_without_text_gets_limit_notice():
    """幻觉调用剥离后若没有任何可见文本，应补一条上限提示，
    保证用户不会完全收不到回复。"""
    provider = MockProvider()
    provider.responses.append(
        LLMResponse(
            role="assistant",
            completion_text="",
            tools_call_name=["non_existent_tool"],
            tools_call_args=[{}],
            tools_call_ids=["call_hallucinated"],
            usage=TokenUsage(input_other=10, output=5),
        )
    )
    request = make_request(
        contexts=[{"role": "user", "content": CURRENT_QUESTION}],
        prompt=None,
        func_tool=ToolSet(tools=[make_tool()]),
    )
    runner = make_runner(provider, request)
    runner.req.func_tool = None

    responses = asyncio.run(run_one_step(runner))

    assert runner.done(), "运行应进入 DONE 而不是停留在 RUNNING"
    final = [r for r in responses if r.type == "llm_result"]
    assert final, "纯幻觉调用剥离后也应有可见回复"
    assert "上限" in final[0].data["chain"].get_plain_text()
    print("test_pure_hallucination_without_text_gets_limit_notice: PASS")


# ---------- 层 2：缺失的 tool 结果补占位块 ----------


def test_missing_tool_results_get_placeholder_blocks():
    """工具调用执行后没有产生任何结果块时，应为缺失的 id 补占位 tool
    结果，上下文不出现悬空的 assistant(tool_calls) 消息。"""
    provider = MockProvider()
    provider.responses.append(tool_call_response(["call_123"]))
    provider.responses.append(
        LLMResponse(
            role="assistant",
            completion_text="完成",
            usage=TokenUsage(input_other=10, output=5),
        )
    )
    request = make_request(
        contexts=[{"role": "user", "content": CURRENT_QUESTION}],
        prompt=None,
        func_tool=ToolSet(tools=[make_tool()]),
    )
    runner = make_runner(provider, request)

    async def fake_handle_function_tools(req, llm_resp):
        # 模拟执行路径没有产出任何结果块
        if False:
            yield  # pragma: no cover — 保持异步生成器形态

    runner._handle_function_tools = fake_handle_function_tools

    asyncio.run(run_one_step(runner))

    messages = runner.run_context.messages
    assistants = [
        m for m in messages if getattr(m, "tool_calls", None) is not None
    ]
    tools = [m for m in messages if m.role == "tool"]
    assert len(assistants) == 1, "应恰好一条 assistant(tool_calls) 消息"
    assert len(tools) == 1, "缺失的结果应被占位块补齐"
    assert tools[0].tool_call_id == "call_123"
    assert "error" in str(tools[0].content).lower()
    print("test_missing_tool_results_get_placeholder_blocks: PASS")


def test_duplicate_result_ids_still_fill_missing():
    """结果块数量与调用数量相等但 id 重复（一个调用多个块、另一个没有）
    时，仍应按 id 配对补齐缺失的那个。"""
    provider = MockProvider()
    provider.responses.append(tool_call_response(["call_a", "call_b"]))
    request = make_request(
        contexts=[{"role": "user", "content": CURRENT_QUESTION}],
        prompt=None,
        func_tool=ToolSet(tools=[make_tool()]),
    )
    runner = make_runner(provider, request)

    async def fake_handle_function_tools(req, llm_resp):
        yield _HandleFunctionToolsResult.from_tool_call_result_blocks(
            [
                ToolCallMessageSegment(
                    role="tool", tool_call_id="call_a", content="result A"
                ),
                ToolCallMessageSegment(
                    role="tool", tool_call_id="call_a", content="result A2"
                ),
            ]
        )

    runner._handle_function_tools = fake_handle_function_tools

    asyncio.run(run_one_step(runner))

    tools = [
        m for m in runner.run_context.messages if m.role == "tool"
    ]
    result_ids = [m.tool_call_id for m in tools]
    assert result_ids == ["call_a", "call_a", "call_b"], (
        f"应保留 call_a 的两个结果块并补齐 call_b，实际 {result_ids}"
    )
    placeholder = tools[-1]
    assert placeholder.tool_call_id == "call_b"
    assert "error" in str(placeholder.content).lower()
    print("test_duplicate_result_ids_still_fill_missing: PASS")


# ---------- 层 4：落库前丢弃悬空历史 ----------


def _call(cid):
    return {
        "type": "function",
        "id": cid,
        "function": {"name": "test_tool", "arguments": "{}"},
    }


def test_save_history_drops_dangling_tool_calls():
    """_save_to_history 落库前应丢弃悬空的 assistant(tool_calls) 组
    （含只有部分结果配对的组），正常配对的消息不受影响。"""
    drop = InternalAgentSubStage._drop_dangling_tool_call_messages

    # 正常配对：原样保留
    paired = [
        Message(role="user", content="q"),
        Message(role="assistant", tool_calls=[_call("c1")]),
        Message(role="tool", tool_call_id="c1", content="r"),
        Message(role="assistant", content="done"),
    ]
    assert drop(list(paired)) == paired, "正常配对的历史不应被改动"

    # 悬空（无任何结果）：整条丢弃
    zero_result = [
        Message(role="user", content="q"),
        Message(role="assistant", tool_calls=[_call("c1")]),
        Message(role="assistant", content="done"),
    ]
    cleaned = drop(list(zero_result))
    assert [m.role for m in cleaned] == ["user", "assistant"], (
        "无结果的悬空 assistant(tool_calls) 应被丢弃"
    )

    # 部分配对：assistant 与其残留 tool 结果整组丢弃
    partial = [
        Message(role="user", content="q"),
        Message(role="assistant", tool_calls=[_call("c1"), _call("c2")]),
        Message(role="tool", tool_call_id="c1", content="r"),
        Message(role="user", content="next"),
    ]
    cleaned = drop(list(partial))
    assert [m.role for m in cleaned] == ["user", "user"], (
        "部分配对的组应整组丢弃（残留 tool 结果不能脱离 assistant 存在）"
    )

    # 混合：悬空组夹在正常组之间，只删悬空组
    mixed = [
        Message(role="assistant", tool_calls=[_call("a")]),
        Message(role="tool", tool_call_id="a", content="r"),
        Message(role="assistant", tool_calls=[_call("b")]),
        Message(role="user", content="x"),
        Message(role="assistant", tool_calls=[_call("c")]),
        Message(role="tool", tool_call_id="c", content="r"),
    ]
    cleaned = drop(list(mixed))
    assert len(cleaned) == 5, "只应丢弃中间的悬空组"
    assert not any(
        m.tool_calls and getattr(m.tool_calls[0], "id", None) == "b" for m in cleaned
    )
    print("test_save_history_drops_dangling_tool_calls: PASS")


if __name__ == "__main__":
    test_hallucinated_tool_call_finalizes_as_plain_response()
    test_pure_hallucination_without_text_gets_limit_notice()
    test_missing_tool_results_get_placeholder_blocks()
    test_duplicate_result_ids_still_fill_missing()
    test_save_history_drops_dangling_tool_calls()
    print("\n全部通过 ✓")
