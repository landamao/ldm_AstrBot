"""上下文占用快照验证（对应上游 issue AstrBot#9864 / PR #9865 的本地实现）。

验证行为：
- conversation.token_usage 是含缓存命中和输出 token 的计费累计值，不再被当作
  当前上下文大小：历史值巨大（如带缓存命中的 3,879,963）而真实上下文很小时，
  不应误触发压缩导致整段对话被清空（"失忆"）
- 工具循环内：上一次真实请求的 prompt 占用（usage.input）按消息指纹复用，
  前缀被压缩/截断、hook 原地改消息、工具 schema 变更时快照失效，退回本地估算

运行：python tests/test_context_usage_snapshot.py
"""

import asyncio
import sys
from pathlib import Path

from mcp.types import CallToolResult, TextContent

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor  # noqa: E402
from astrbot.core.db.po import Conversation  # noqa: E402
from astrbot.core.provider.entities import (  # noqa: E402
    LLMResponse,
    ProviderRequest,
    TokenUsage,
)

PREVIOUS_QUESTION = "previous question"
HISTORICAL_ANSWER = "important historical answer"
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
    """所有钩子默认 no-op；on_tool_end 可被子类覆写做原地修改。"""

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
    def __init__(self, max_context_tokens=0):
        self.provider_config = {
            "id": "mock-provider",
            "model": "mock-model",
            "max_context_tokens": max_context_tokens,
        }
        self.responses = []
        self.received_contexts = []

    async def text_chat(self, **kwargs):
        self.received_contexts.append(kwargs["contexts"])
        return self.responses.pop(0)


def tool_call_response(usage):
    return LLMResponse(
        role="assistant",
        completion_text="",
        tools_call_name=["test_tool"],
        tools_call_args=[{"query": "test"}],
        tools_call_ids=["call_snapshot"],
        usage=usage,
    )


def make_request(contexts, prompt, conversation=None, func_tool=None):
    return ProviderRequest(
        prompt=prompt,
        session_id="test-session",
        contexts=contexts,
        conversation=conversation,
        func_tool=func_tool,
    )


def make_runner(provider, request, hooks=None):
    runner = ToolLoopAgentRunner()
    asyncio.run(
        runner.reset(
            provider=provider,
            request=request,
            run_context=ContextWrapper(context=None),
            tool_executor=MockToolExecutor(),
            agent_hooks=hooks or MockHooks(),
            streaming=False,
        )
    )
    return runner


async def run_steps(runner, max_step):
    async for _ in runner.step_until_done(max_step):
        pass


def str_contents(messages):
    return [m.content for m in messages if isinstance(m.content, str)]


# ---------- 核心回归：历史计费值不再误触发压缩（issue #9864 场景） ----------


def test_stale_conversation_token_usage_no_early_compression():
    """conversation.token_usage=3,879,963（含缓存命中的历史计费值）时，
    真实约 97K 的上下文不应被压缩清空。"""
    provider = MockProvider(max_context_tokens=1_000_000)
    provider.responses.append(
        LLMResponse(
            role="assistant",
            completion_text="好的",
            usage=TokenUsage(input_other=54_000, output=1_000),
        )
    )
    conversation = Conversation(
        platform_id="webchat",
        user_id="user",
        cid="conversation-id",
        token_usage=3_879_963,
    )
    request = make_request(
        contexts=[
            {"role": "user", "content": "x" * 320_000},
            {"role": "assistant", "content": HISTORICAL_ANSWER},
        ],
        prompt=CURRENT_QUESTION,
        conversation=conversation,
    )
    runner = make_runner(provider, request)

    # 旧行为对照：把这个计费值当当前占用，压缩器会清空整个上下文
    original_messages = list(runner.run_context.messages)
    legacy_messages = asyncio.run(
        runner.request_context_manager.process(
            list(original_messages),
            trusted_token_usage=conversation.token_usage,
        )
    )
    assert CURRENT_QUESTION not in str_contents(legacy_messages), (
        "旧行为对照失败：大 trusted 值应触发压缩丢掉当前问题"
    )

    asyncio.run(run_steps(runner, 1))
    contents = str_contents(runner.run_context.messages)
    assert HISTORICAL_ANSWER in contents, "历史回答不应被压缩丢弃"
    assert CURRENT_QUESTION in contents, "当前问题不应被压缩丢弃"
    print("test_stale_conversation_token_usage_no_early_compression: PASS")


# ---------- 工具循环：按快照复用上一次真实 prompt 占用 ----------


def test_tool_loop_reuses_real_prompt_usage_snapshot():
    """第一次请求 usage.input=900（>820 阈值线），第二次请求按消息指纹
    复用该值并加上尾部增量，应触发压缩丢掉最老一轮。"""
    provider = MockProvider(max_context_tokens=1_000)
    provider.responses.append(
        tool_call_response(usage=TokenUsage(input_other=900, output=10))
    )
    provider.responses.append(
        LLMResponse(
            role="assistant",
            completion_text="完成",
            usage=TokenUsage(input_other=10, output=5),
        )
    )
    request = make_request(
        contexts=[
            {"role": "user", "content": PREVIOUS_QUESTION},
            {"role": "assistant", "content": HISTORICAL_ANSWER},
        ],
        prompt=CURRENT_QUESTION,
        func_tool=ToolSet(tools=[make_tool()]),
    )
    runner = make_runner(provider, request)

    asyncio.run(run_steps(runner, 3))

    assert len(provider.received_contexts) == 2, (
        f"应恰好发起两次 LLM 请求，实际 {len(provider.received_contexts)} 次"
    )
    second_contents = str_contents(provider.received_contexts[1])
    assert HISTORICAL_ANSWER not in second_contents, (
        "快照占用超过阈值应触发压缩，丢掉最老一轮"
    )
    assert CURRENT_QUESTION in second_contents, "当前问题应保留"
    print("test_tool_loop_reuses_real_prompt_usage_snapshot: PASS")


# ---------- 快照失效：hook 原地改消息 / 改工具 schema ----------


class MessageMutatingHooks(MockHooks):
    async def on_tool_end(self, run_context, tool, tool_args, tool_result):
        run_context.messages[0].content = "changed historical question"


class SchemaMutatingHooks(MockHooks):
    async def on_tool_end(self, run_context, tool, tool_args, tool_result):
        tool.parameters["properties"]["extra"] = {"type": "string"}


def _assert_reestimates_after_mutation(hooks):
    provider = MockProvider(max_context_tokens=1_000)
    provider.responses.append(
        tool_call_response(usage=TokenUsage(input_other=900, output=10))
    )
    provider.responses.append(
        LLMResponse(
            role="assistant",
            completion_text="完成",
            usage=TokenUsage(input_other=10, output=5),
        )
    )
    request = make_request(
        contexts=[
            {"role": "user", "content": PREVIOUS_QUESTION},
            {"role": "assistant", "content": HISTORICAL_ANSWER},
        ],
        prompt=CURRENT_QUESTION,
        func_tool=ToolSet(tools=[make_tool()]),
    )
    runner = make_runner(provider, request, hooks=hooks)

    captured = []
    original_process = runner.request_context_manager.process

    async def capture_process(messages, trusted_token_usage=0):
        captured.append(trusted_token_usage)
        return await original_process(messages, trusted_token_usage)

    runner.request_context_manager.process = capture_process

    asyncio.run(run_steps(runner, 3))

    assert len(provider.received_contexts) == 2, (
        f"应恰好发起两次 LLM 请求，实际 {len(provider.received_contexts)} 次"
    )
    assert captured == [0, 0], (
        f"快照应失效并退回本地估算，实际 trusted_token_usage={captured}"
    )


def test_reestimates_after_in_place_message_mutation():
    """hook 原地修改历史消息后，快照指纹不匹配，退回本地估算。"""
    _assert_reestimates_after_mutation(MessageMutatingHooks())
    print("test_reestimates_after_in_place_message_mutation: PASS")


def test_reestimates_after_in_place_schema_mutation():
    """hook 原地修改工具 schema 后，快照 schema 指纹不匹配，退回本地估算。"""
    _assert_reestimates_after_mutation(SchemaMutatingHooks())
    print("test_reestimates_after_in_place_schema_mutation: PASS")


if __name__ == "__main__":
    test_stale_conversation_token_usage_no_early_compression()
    test_tool_loop_reuses_real_prompt_usage_snapshot()
    test_reestimates_after_in_place_message_mutation()
    test_reestimates_after_in_place_schema_mutation()
    print("\n全部通过 ✓")
