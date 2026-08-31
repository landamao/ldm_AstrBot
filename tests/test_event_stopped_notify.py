"""验证 stop_event 文案格式与 WebChat 落库 part。

运行：~/ldmbot/.venv/bin/python tests/test_event_stopped_notify.py
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.pipeline.context_utils import (  # noqa: E402
    format_event_stopped_message,
    notify_event_stopped,
    plugin_display_name,
)
from astrbot.dashboard.services.chat_service import BotMessageAccumulator  # noqa: E402


def test_format():
    md = SimpleNamespace(name="astrbot_plugin_foo", display_name="复读机")
    assert plugin_display_name(md) == "复读机"
    assert (
        format_event_stopped_message(md, "on_llm_request")
        == "插件「复读机」（on_llm_request）终止了事件传播"
    )
    md2 = SimpleNamespace(name="builtin_commands", display_name=None)
    assert (
        format_event_stopped_message(md2, "help")
        == "插件「builtin_commands」（help）终止了事件传播"
    )
    assert format_event_stopped_message(None, None) == "插件「未知」（未知）终止了事件传播"
    print("test_format: PASS")


def test_accumulator():
    acc = BotMessageAccumulator()
    payload = json.dumps(
        {"text": "插件「复读机」（on_llm_request）终止了事件传播", "plugin": "复读机", "method": "on_llm_request"},
        ensure_ascii=False,
    )
    acc.add_plain(payload, chain_type="event_stopped", streaming=False)
    parts = acc.build_message_parts()
    assert len(parts) == 1
    assert parts[0]["type"] == "event_stopped"
    assert parts[0]["text"] == "插件「复读机」（on_llm_request）终止了事件传播"
    assert parts[0]["plugin"] == "复读机"
    assert parts[0]["method"] == "on_llm_request"
    print("test_accumulator: PASS")


async def test_notify_webchat_once():
    event = MagicMock()
    event.get_extra.return_value = None
    event.get_platform_name.return_value = "webchat"
    event.send = AsyncMock()
    md = SimpleNamespace(name="foo", display_name="测试插件")
    await notify_event_stopped(event, md, "bar")
    event.send.assert_awaited_once()
    chain = event.send.await_args.args[0]
    assert chain.type == "event_stopped"
    # 第二次应被去重
    event.get_extra.return_value = True
    await notify_event_stopped(event, md, "bar")
    assert event.send.await_count == 1
    print("test_notify_webchat_once: PASS")


async def test_notify_non_webchat_no_send():
    event = MagicMock()
    event.get_extra.return_value = None
    event.get_platform_name.return_value = "aiocqhttp"
    event.send = AsyncMock()
    md = SimpleNamespace(name="foo", display_name="测试插件")
    await notify_event_stopped(event, md, "bar")
    event.send.assert_not_awaited()
    print("test_notify_non_webchat_no_send: PASS")


async def main():
    test_format()
    test_accumulator()
    await test_notify_webchat_once()
    await test_notify_non_webchat_no_send()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
