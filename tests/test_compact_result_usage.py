"""/compact 回执的占用数据来源：模型返回优先，估算显式标注。"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrbot.builtin_stars.builtin_commands.commands.conversation import (
    ConversationCommands,
)
from astrbot.core.provider.entities import TokenUsage
from test_compact_command import FakeEvent, HISTORY, _context, _result_text, _run


def _context_with_stats(context: MagicMock, last_tokens: int | None) -> AsyncMock:
    last = (
        SimpleNamespace(current_context_tokens=last_tokens) if last_tokens else None
    )
    execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: last)
    )

    @asynccontextmanager
    async def get_db():
        yield SimpleNamespace(execute=execute)

    db = MagicMock()
    db.get_db = get_db
    context.get_db = MagicMock(return_value=db)
    return execute


def _run_llm_compact(last_tokens: int | None, usage: TokenUsage | None):
    event = FakeEvent()
    context = _context(provider=MagicMock())
    _context_with_stats(context, last_tokens)

    compressor_usage = usage

    class FakeCompressor:
        def __init__(self, **kwargs) -> None:
            pass

        async def __call__(self, messages):
            self.last_usage = compressor_usage
            return messages[:2]

    from unittest.mock import patch as mock_patch

    with mock_patch(
        "astrbot.builtin_stars.builtin_commands.commands.conversation.LLMSummaryCompressor",
        FakeCompressor,
    ):
        _run(ConversationCommands(context).compact(event))

    assert json.loads(  # 确认确实压缩落库
        json.dumps(context.conversation_manager.update_conversation.await_args.args[2])
    )
    return _result_text(event)


def test_压缩前占用用模型返回():
    text = _run_llm_compact(45_600, None)
    assert "压缩前占用: 45.60k（模型返回）" in text
    assert "压缩前占用: " in text
    # 有模型数据时不得再显示本地估算值
    assert "估算占用" not in text


def test_无模型数据时标注估算():
    text = _run_llm_compact(0, None)
    assert "压缩前占用:" in text and "（估算）" in text


def test_摘要输出用模型返回():
    text = _run_llm_compact(45_600, TokenUsage(input_other=100, output=1250))
    assert "摘要输出: 1.25k（模型返回）" in text


def test_模型未返回usage时不显示摘要输出():
    text = _run_llm_compact(45_600, None)
    assert "摘要输出" not in text
    assert "压缩后占用:" in text and "（估算）" in text
