"""LLMSummaryCompressor 透出压缩请求的模型返回 usage。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.core.agent.context.compressor import (  # noqa: E402
    LLMSummaryCompressor,
)
from astrbot.core.agent.message import Message  # noqa: E402
from astrbot.core.provider.entities import TokenUsage  # noqa: E402


def _history_messages() -> list[Message]:
    return [
        Message(role="user", content="第一轮问题"),
        Message(role="assistant", content="第一轮回答"),
        Message(role="user", content="第二轮问题"),
        Message(role="assistant", content="第二轮回答"),
        Message(role="user", content="第三轮问题"),
        Message(role="assistant", content="第三轮回答"),
    ]


def _provider(usage: TokenUsage | None) -> MagicMock:
    provider = MagicMock()
    provider.provider_config = {"modalities": None}
    provider.text_chat = AsyncMock(
        return_value=SimpleNamespace(completion_text="这是摘要", usage=usage)
    )
    return provider


def _run(coro):
    return asyncio.run(coro)


def test_压缩后透出摘要请求usage():
    provider = _provider(TokenUsage(input_other=10, input_cached=2, output=5))
    compressor = LLMSummaryCompressor(provider=provider, keep_recent_rounds=1)

    result = _run(compressor(_history_messages()))

    assert result != _history_messages()  # 确实发生了压缩
    assert compressor.last_usage == TokenUsage(input_other=10, input_cached=2, output=5)


def test_压缩失败时usage为None():
    provider = _provider(None)
    provider.text_chat = AsyncMock(side_effect=RuntimeError("网络错误"))
    compressor = LLMSummaryCompressor(provider=provider, keep_recent_rounds=1)

    result = _run(compressor(_history_messages()))

    assert result == _history_messages()
    assert compressor.last_usage is None


def test_模型未返回usage时保持None():
    provider = _provider(None)
    compressor = LLMSummaryCompressor(provider=provider, keep_recent_rounds=1)

    _run(compressor(_history_messages()))

    assert compressor.last_usage is None
