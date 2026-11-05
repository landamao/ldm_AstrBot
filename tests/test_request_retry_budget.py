"""request_max_retries 是全链路的总尝试预算：调成 1 就只发一次请求。

此前同一条逻辑请求叠了三层各自记账的重试：
1. OpenAI/Anthropic SDK 自带的隐式重试（默认 max_retries=2，不受配置控制）；
2. retry_provider_request 的传输重试（读配置）；
3. openai_source.text_chat/text_chat_stream 外层错误恢复循环（硬编码 10 次）；
4. tool_loop_agent_runner 的空输出/仅思考输出重试（内置 3/5 次，不读配置）。

因此用户在 WebUI 里把「请求重试次数」调成 1，失败后仍可能连发十几次请求。
本测试锁定修复后的语义：预算 = 单次逻辑请求最多发出的 HTTP 请求数。

运行：python tests/test_request_retry_budget.py
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import astrbot.core as astrbot_core  # noqa: E402
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
from astrbot.core.provider.sources.openai_source import (  # noqa: E402
    ProviderOpenAIOfficial,
)
from astrbot.core.provider.sources.request_retry import (  # noqa: E402
    resolve_request_max_retries,
    retry_provider_request,
)


# --------------------------------------------------------------------------
# 预算解析
# --------------------------------------------------------------------------


def test_explicit_value_wins():
    assert resolve_request_max_retries(1) == 1
    assert resolve_request_max_retries(4) == 4
    # 字符串数字按数字解析
    assert resolve_request_max_retries("3") == 3
    # 低于 1 时钳到 1（至少发一次）
    assert resolve_request_max_retries(0) == 1
    assert resolve_request_max_retries(-5) == 1


def test_unset_value_falls_back_to_global_config():
    """未显式传参的调用点（图片转述/压缩/插件调用）也读全局配置。"""
    original = astrbot_core.astrbot_config
    try:
        astrbot_core.astrbot_config = {"provider_settings": {"request_max_retries": 1}}
        assert resolve_request_max_retries(None) == 1

        astrbot_core.astrbot_config = {"provider_settings": {"request_max_retries": 4}}
        assert resolve_request_max_retries(None) == 4

        # 配置缺项时退回内置默认值而不是崩
        astrbot_core.astrbot_config = {"provider_settings": {}}
        assert resolve_request_max_retries(None) == 5
    finally:
        astrbot_core.astrbot_config = original


# --------------------------------------------------------------------------
# 传输重试
# --------------------------------------------------------------------------


def test_transport_retry_attempts_equal_budget():
    calls = {"n": 0}

    class FakeServerError(Exception):
        status_code = 500

    async def always_fail():
        calls["n"] += 1
        raise FakeServerError("boom")

    for budget in (1, 2, 3):
        calls["n"] = 0
        try:
            asyncio.run(
                retry_provider_request(
                    "test",
                    always_fail,
                    max_attempts=budget,
                )
            )
        except FakeServerError:
            pass
        assert calls["n"] == budget, f"预算 {budget} 应发 {budget} 次，实际 {calls['n']}"


# --------------------------------------------------------------------------
# 真实 HTTP：provider.text_chat 的总请求数
# --------------------------------------------------------------------------


def _start_failing_server():
    """起一个固定返回 500 的本地服务，记录收到的请求次数。"""
    counter = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            counter["n"] += 1
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            body = json.dumps(
                {
                    "error": {
                        "message": "upstream exploded",
                        "type": "server_error",
                        "code": 500,
                    }
                }
            ).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return port, counter, server


def _make_provider(port: int) -> ProviderOpenAIOfficial:
    config = {
        "id": "retry-test",
        "type": "openai_chat_completion",
        "model": "gpt-4o-mini",
        "key": ["sk-test"],
        "api_base": f"http://127.0.0.1:{port}/v1",
        "timeout": 10,
        "enable": True,
        "provider_type": "chat_completion",
    }
    return ProviderOpenAIOfficial(config, {})


def _chat_once(provider: ProviderOpenAIOfficial, budget: int) -> None:
    try:
        asyncio.run(provider.text_chat(prompt="hi", request_max_retries=budget))
    except Exception:  # noqa: BLE001 - 预期失败
        pass


def test_provider_sends_single_request_when_budget_is_one():
    """预算 1：即使上游持续 500，也只能发一次请求（SDK 不能偷偷补发）。"""
    port, counter, server = _start_failing_server()
    try:
        _chat_once(_make_provider(port), budget=1)
        assert counter["n"] == 1, f"预算 1 应只发 1 次，实际 {counter['n']}"
    finally:
        server.shutdown()


def test_provider_sends_budget_requests_when_budget_is_three():
    port, counter, server = _start_failing_server()
    try:
        _chat_once(_make_provider(port), budget=3)
        assert counter["n"] == 3, f"预算 3 应发 3 次，实际 {counter['n']}"
    finally:
        server.shutdown()


# --------------------------------------------------------------------------
# Agent runner：仅思考输出不再额外轰炸
# --------------------------------------------------------------------------


class MockHooks:
    async def on_agent_begin(self, run_context):
        pass

    async def on_tool_start(self, run_context, tool, tool_args):
        pass

    async def on_tool_end(self, run_context, tool, tool_result):
        pass

    async def on_agent_done(self, run_context, llm_response):
        pass


class MockToolExecutor(BaseFunctionToolExecutor):
    async def execute(self, tool, run_context, **tool_args):
        raise AssertionError("测试不应触发工具执行")


class MockProvider:
    def __init__(self, provider_id, response_factory):
        self.provider_config = {"id": provider_id, "model": f"model-{provider_id}"}
        self.response_factory = response_factory
        self.call_count = 0

    async def text_chat(self, **kwargs):
        self.call_count += 1
        return self.response_factory()


def _reasoning_only() -> LLMResponse:
    return LLMResponse(
        role="assistant",
        completion_text="",
        reasoning_content="想一下……",
        usage=TokenUsage(input_other=10, output=5),
    )


def _make_runner(provider, **kwargs) -> ToolLoopAgentRunner:
    runner = ToolLoopAgentRunner()

    async def _reset():
        await runner.reset(
            provider=provider,
            request=ProviderRequest(
                prompt="你好",
                session_id="test-session",
                contexts=[{"role": "user", "content": "你好"}],
                func_tool=None,
            ),
            run_context=ContextWrapper(context=None),
            tool_executor=MockToolExecutor(),
            agent_hooks=MockHooks(),
            streaming=False,
            **kwargs,
        )

    asyncio.run(_reset())
    runner.EMPTY_OUTPUT_RETRY_WAIT_MIN_S = 0
    runner.EMPTY_OUTPUT_RETRY_WAIT_MAX_S = 0
    return runner


async def _drain(runner):
    return [resp async for resp in runner.step()]


def test_reasoning_only_makes_single_attempt_when_budget_is_one():
    """预算 1 + retry_current：仅思考输出时不再对同一模型补发请求。"""
    provider = MockProvider("main", _reasoning_only)
    runner = _make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
        request_max_retries=1,
    )
    responses = asyncio.run(_drain(runner))

    assert provider.call_count == 1, (
        f"预算 1 时同一模型只应请求 1 次，实际 {provider.call_count}"
    )
    assert any(r.type == "err" for r in responses), "应得到错误回复而不是静默空输出"


def test_reasoning_only_respects_budget_above_one():
    provider = MockProvider("main", _reasoning_only)
    runner = _make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
        request_max_retries=2,
    )
    asyncio.run(_drain(runner))

    assert provider.call_count == 2, (
        f"预算 2 时最多请求 2 次，实际 {provider.call_count}"
    )


def test_budget_unset_keeps_builtin_attempts():
    """未显式传预算（单测/脚本直接构造 runner）时保留内置上限。"""
    provider = MockProvider("main", _reasoning_only)
    runner = _make_runner(
        provider,
        empty_content_retry_enabled=True,
        empty_content_retry_mode="retry_current",
    )
    asyncio.run(_drain(runner))

    assert provider.call_count == ToolLoopAgentRunner.EMPTY_CONTENT_RETRY_ATTEMPTS


def main() -> int:
    tests = [
        test_explicit_value_wins,
        test_unset_value_falls_back_to_global_config,
        test_transport_retry_attempts_equal_budget,
        test_provider_sends_single_request_when_budget_is_one,
        test_provider_sends_budget_requests_when_budget_is_three,
        test_reasoning_only_makes_single_attempt_when_budget_is_one,
        test_reasoning_only_respects_budget_above_one,
        test_budget_unset_keeps_builtin_attempts,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"{test.__name__}: FAIL - {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"{test.__name__}: ERROR - {type(exc).__name__}: {exc}")
        else:
            print(f"{test.__name__}: PASS")

    if failed:
        print(f"\n{failed} 项失败")
        return 1
    print("\n全部通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
