"""「模型无正文时重新请求」与「开启主模型失败回退」配置验证。

验证行为：
- 仅返回思考内容、无正文也无工具调用时，按重试方式处理：
  retry_current=重试当前模型直到吐出正文/工具调用（上限 5 次尝试）；
  fallback=判定为失败，立即请求回退模型
- 开关关闭时保持旧行为：仅思考的响应被原样接受（静默无正文）
- 重试耗尽后走回退链；无回退模型时抛出中文错误
- 全局回退开关关闭（fallback_providers 为空）时，请求失败直接得到中文错误

运行：python tests/test_empty_content_retry.py
"""

import asyncio
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor  # noqa: E402
from astrbot.core.provider.entities import (  # noqa: E402
    LLMResponse,
    ProviderRequest,
    TokenUsage,
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
        raise AssertionError("测试不应触发工具执行")


class MockProvider:
    """按队列返回响应；队列耗尽后返回 default_factory 生成的响应。"""

    def __init__(self, provider_id, default_factory=None):
        self.provider_config = {"id": provider_id, "model": f"model-{provider_id}"}
        self.responses = []
        self.default_factory = default_factory
        self.call_count = 0

    async def text_chat(self, **kwargs):
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        if self.default_factory is not None:
            return self.default_factory()
        raise AssertionError(f"{self.provider_config['id']} 意外的额外请求")


def reasoning_only_response():
    return LLMResponse(
        role="assistant",
        completion_text="",
        reasoning_content="让我想一想……",
        usage=TokenUsage(input_other=10, output=5),
    )


def text_response(text="这是正文回复"):
    return LLMResponse(
        role="assistant",
        completion_text=text,
        usage=TokenUsage(input_other=10, output=5),
    )


def make_request():
    return ProviderRequest(
        prompt="你好",
        session_id="test-session",
        contexts=[{"role": "user", "content": "你好"}],
        func_tool=None,
    )


def make_runner(provider, fallback_providers=None, **kwargs):
    runner = ToolLoopAgentRunner()

    async def _reset():
        await runner.reset(
            provider=provider,
            request=make_request(),
            run_context=ContextWrapper(context=None),
            tool_executor=MockToolExecutor(),
            agent_hooks=MockHooks(),
            streaming=False,
            fallback_providers=fallback_providers or [],
            **kwargs,
        )

    asyncio.run(_reset())
    # 重试退避等待归零，避免测试拖慢
    runner.EMPTY_OUTPUT_RETRY_WAIT_MIN_S = 0
    runner.EMPTY_OUTPUT_RETRY_WAIT_MAX_S = 0
    return runner


async def run_one_step(runner):
    responses = []
    async for resp in runner.step():
        responses.append(resp)
    return responses


def final_text(responses):
    finals = [r for r in responses if r.type == "llm_result"]
    assert finals, "应收到最终 llm_result 回复"
    return finals[-1].data["chain"].get_plain_text()


def err_text(responses):
    errs = [r for r in responses if r.type == "err"]
    assert errs, "应收到 err 回复"
    return errs[-1].data["chain"].get_plain_text()


def test_reasoning_only_retries_current_model_until_content():
    """retry_current：仅思考的响应应触发同模型重试，直到吐出正文。"""
    provider = MockProvider("main")
    provider.responses = [reasoning_only_response(), reasoning_only_response()]
    provider.default_factory = lambda: text_response("重试后拿到正文")
    runner = make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
    )

    responses = asyncio.run(run_one_step(runner))

    assert provider.call_count == 3, (
        f"前两次仅思考 + 第三次正文，应共请求 3 次，实际 {provider.call_count}"
    )
    assert "重试后拿到正文" in final_text(responses)
    print("test_reasoning_only_retries_current_model_until_content: PASS")


def test_reasoning_only_fallback_mode_switches_immediately():
    """fallback：主模型仅思考即判定失败，立刻请求回退模型，不重试主模型。"""
    main = MockProvider("main")
    main.responses = [reasoning_only_response()]
    fallback = MockProvider("fallback")
    fallback.responses = [text_response("来自回退模型")]
    runner = make_runner(
        main,
        fallback_providers=[fallback],
        empty_content_retry_enabled=True,
        empty_content_retry_mode="fallback",
    )

    responses = asyncio.run(run_one_step(runner))

    assert main.call_count == 1, "主模型仅思考后不应被重试"
    assert fallback.call_count == 1, "应立即请求回退模型"
    assert "来自回退模型" in final_text(responses)
    print("test_reasoning_only_fallback_mode_switches_immediately: PASS")


def test_reasoning_only_retry_disabled_keeps_old_behavior():
    """开关关闭：仅思考的响应被原样接受，不发起重试，也不回退。"""
    provider = MockProvider("main")
    provider.responses = [reasoning_only_response()]
    fallback = MockProvider("fallback")
    runner = make_runner(
        provider,
        fallback_providers=[fallback],
        empty_content_retry_enabled=False,
        empty_content_retry_mode="retry_current",
    )

    responses = asyncio.run(run_one_step(runner))

    assert provider.call_count == 1, "开关关闭时不应重试"
    assert fallback.call_count == 0, "开关关闭时不应回退"
    assert runner.done(), "仅思考响应按旧行为收尾"
    assert not [r for r in responses if r.type == "err"], "不应产生错误回复"
    print("test_reasoning_only_retry_disabled_keeps_old_behavior: PASS")


def test_reasoning_only_exhausted_then_fallback():
    """retry_current：重试耗尽后应切换到回退模型。"""
    main = MockProvider("main", default_factory=reasoning_only_response)
    fallback = MockProvider("fallback")
    fallback.responses = [text_response("回退成功")]
    runner = make_runner(
        main,
        fallback_providers=[fallback],
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
    )

    responses = asyncio.run(run_one_step(runner))

    assert main.call_count == ToolLoopAgentRunner.EMPTY_CONTENT_RETRY_ATTEMPTS, (
        f"主模型应尝试 {ToolLoopAgentRunner.EMPTY_CONTENT_RETRY_ATTEMPTS} 次，"
        f"实际 {main.call_count}"
    )
    assert fallback.call_count == 1
    assert "回退成功" in final_text(responses)
    print("test_reasoning_only_exhausted_then_fallback: PASS")


def test_reasoning_only_exhausted_no_fallback_yields_chinese_error():
    """retry_current：重试耗尽且无回退模型时，用户应收到中文错误。"""
    provider = MockProvider("main", default_factory=reasoning_only_response)
    runner = make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
    )

    responses = asyncio.run(run_one_step(runner))

    text = err_text(responses)
    assert "仅返回思考内容" in text, f"错误信息应说明原因，实际: {text}"
    assert "所有对话模型均请求失败" in text
    assert "ReasoningOnlyOutputError" not in text, "框架异常类名不应暴露给用户"
    print("test_reasoning_only_exhausted_no_fallback_yields_chinese_error: PASS")


def test_exception_no_fallback_yields_chinese_error():
    """主模型请求抛异常且无回退模型（全局回退关闭的等价场景）：
    直接得到中文错误，且不暴露异常类名以外的英文框架文案。"""
    provider = MockProvider("main")

    async def boom(**kwargs):
        raise RuntimeError("connection reset")

    provider.text_chat = boom
    runner = make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
    )

    responses = asyncio.run(run_one_step(runner))

    text = err_text(responses)
    assert "所有对话模型均请求失败" in text, f"实际: {text}"
    assert "RuntimeError" in text and "connection reset" in text
    print("test_exception_no_fallback_yields_chinese_error: PASS")


def test_tool_call_output_not_treated_as_reasoning_only():
    """带工具调用的响应（无正文）是正常输出，不应触发无正文重试。"""
    provider = MockProvider("main")
    provider.responses = [
        LLMResponse(
            role="assistant",
            completion_text="",
            reasoning_content="需要调用工具",
            tools_call_name=["test_tool"],
            tools_call_args=[{"query": "test"}],
            tools_call_ids=["call_1"],
            usage=TokenUsage(input_other=10, output=5),
        ),
        text_response("工具结果之后的回复"),
    ]
    runner = make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
    )

    asyncio.run(run_one_step(runner))

    assert provider.call_count == 1, "工具调用响应不应被判为无正文"
    print("test_tool_call_output_not_treated_as_reasoning_only: PASS")


if __name__ == "__main__":
    test_reasoning_only_retries_current_model_until_content()
    test_reasoning_only_fallback_mode_switches_immediately()
    test_reasoning_only_retry_disabled_keeps_old_behavior()
    test_reasoning_only_exhausted_then_fallback()
    test_reasoning_only_exhausted_no_fallback_yields_chinese_error()
    test_exception_no_fallback_yields_chinese_error()
    test_tool_call_output_not_treated_as_reasoning_only()
    print("\n全部通过 ✓")
