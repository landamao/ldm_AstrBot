"""验证手动停止必须写入英文 system_reminder。

运行：~/ldmbot/.venv/bin/python tests/test_interrupt_force_stop_note.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.agent.message import Message  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse  # noqa: E402


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


def test_force_stop_writes_english_note():
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = make_event(_delivered_llm_plain_text="hello")
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
    last = out[-1]
    text = last.content if isinstance(last.content, str) else ""
    assert "The user manually stopped this response." in text
    assert "<system_reminder>" in text
    print("test_force_stop_writes_english_note: PASS")


def test_force_stop_not_skipped_as_fully_delivered():
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    event = make_event(
        _delivered_llm_plain_text="hello",
        _llm_reply_send_completed=True,
    )
    assert (
        stage._is_llm_reply_fully_delivered(
            event,
            runner_aborted=False,
            original_text="hello",
            delivered_text="hello",
            force_stopped=True,
        )
        is False
    )
    print("test_force_stop_not_skipped_as_fully_delivered: PASS")


if __name__ == "__main__":
    test_force_stop_writes_english_note()
    test_force_stop_not_skipped_as_fully_delivered()
    print("\n全部通过 ✓")
