"""WebChat 落库：complete 后再 end 只剩 agent_stats 时，不能再插一条空 bot。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.dashboard.services.chat_service import (  # noqa: E402
    ChatRunState,
    ChatService,
)


class FakeRecord:
    def __init__(self, rid: int, content: dict):
        self.id = rid
        self.content = content
        self.created_at = None
        self.llm_checkpoint_id = "ckpt-1"


def _service() -> ChatService:
    svc = ChatService.__new__(ChatService)
    svc.running_convs = {}
    svc.image_gen_tasks = {}
    svc.chat_runs = {}
    svc.chat_runs_by_session = {}
    svc.db = MagicMock()
    svc.db.get_platform_session_by_id = AsyncMock(return_value=None)
    svc.platform_history_mgr = MagicMock()
    svc.platform_history_mgr.update = AsyncMock()
    inserted: list[FakeRecord] = []

    async def save_bot_message(*args, **kwargs):
        content = {
            "type": "bot",
            "message": args[1] if len(args) > 1 else kwargs.get("message_parts", []),
        }
        stats = args[2] if len(args) > 2 else kwargs.get("agent_stats", {})
        refs = args[3] if len(args) > 3 else kwargs.get("refs", {})
        if stats:
            content["agent_stats"] = stats
        if refs:
            content["refs"] = refs
        rec = FakeRecord(len(inserted) + 1, content)
        inserted.append(rec)
        return rec

    svc.save_bot_message = save_bot_message
    svc.load_current_conversation_history = AsyncMock(return_value=("", []))
    svc._inserted = inserted
    return svc


def test_end_with_only_agent_stats_merges_into_last_bot():
    async def _run():
        svc = _service()
        queue: asyncio.Queue = asyncio.Queue()
        run = ChatRunState(
            run_id="run-1",
            username="ldm",
            session_id="sess-1",
            llm_checkpoint_id="ckpt-1",
            platform_history_id="webchat",
            back_queue=queue,
        )
        svc.chat_runs[run.run_id] = run
        svc.chat_runs_by_session[run.session_id] = {run.run_id}

        await queue.put(
            {
                "type": "plain",
                "data": "你好",
                "streaming": True,
                "message_id": "run-1",
            }
        )
        await queue.put(
            {
                "type": "complete",
                "data": "你好",
                "streaming": True,
                "message_id": "run-1",
            }
        )
        await queue.put(
            {
                "type": "plain",
                "data": json.dumps({"input_tokens": 10, "output_tokens": 2}),
                "chain_type": "agent_stats",
                "message_id": "run-1",
            }
        )
        await queue.put({"type": "end", "data": "", "message_id": "run-1"})

        with patch(
            "astrbot.dashboard.services.chat_service.webchat_queue_mgr"
        ) as queue_mgr:
            queue_mgr.remove_back_queue = MagicMock()
            await svc._consume_chat_run(run)

        assert len(svc._inserted) == 1
        assert svc._inserted[0].content["message"] == [
            {"type": "plain", "text": "你好"}
        ]
        svc.platform_history_mgr.update.assert_awaited()
        updated_content = svc.platform_history_mgr.update.await_args.kwargs["content"]
        assert updated_content["agent_stats"]["input_tokens"] == 10
        assert updated_content["message"] == [{"type": "plain", "text": "你好"}]

    asyncio.run(_run())


def test_empty_stats_only_does_not_insert_bot():
    async def _run():
        svc = _service()
        queue: asyncio.Queue = asyncio.Queue()
        run = ChatRunState(
            run_id="run-2",
            username="ldm",
            session_id="sess-2",
            llm_checkpoint_id="ckpt-2",
            platform_history_id="webchat",
            back_queue=queue,
        )
        svc.chat_runs[run.run_id] = run
        svc.chat_runs_by_session[run.session_id] = {run.run_id}
        await queue.put(
            {
                "type": "plain",
                "data": json.dumps({"input_tokens": 1}),
                "chain_type": "agent_stats",
                "message_id": "run-2",
            }
        )
        await queue.put({"type": "end", "data": "", "message_id": "run-2"})

        with patch(
            "astrbot.dashboard.services.chat_service.webchat_queue_mgr"
        ) as queue_mgr:
            queue_mgr.remove_back_queue = MagicMock()
            await svc._consume_chat_run(run)

        assert svc._inserted == []
        svc.platform_history_mgr.update.assert_not_awaited()

    asyncio.run(_run())
