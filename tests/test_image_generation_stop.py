"""生图会话停止：cancel 独立 task，断线不取消。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.dashboard.services.chat_service import ChatService  # noqa: E402


def _service() -> ChatService:
    svc = ChatService.__new__(ChatService)
    svc.running_convs = {}
    svc.image_gen_tasks = {}
    svc.chat_runs = {}
    svc.chat_runs_by_session = {}
    return svc


def test_cancel_image_generation_cancels_task():
    async def _run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow():
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        svc = _service()
        task = asyncio.create_task(slow())
        svc.image_gen_tasks["sess"] = task
        await started.wait()
        await svc._cancel_image_generation("sess")
        assert cancelled.is_set()
        assert task.done()

    asyncio.run(_run())


def test_cancel_image_generation_noop_when_idle():
    async def _run():
        svc = _service()
        await svc._cancel_image_generation("missing")

    asyncio.run(_run())


def test_shield_keeps_job_when_waiter_cancelled():
    """HTTP 断线取消 waiter，shield 后生图 job 继续。"""

    async def _run():
        finished = asyncio.Event()

        async def job():
            await asyncio.sleep(0.05)
            finished.set()
            return {"ok": True}

        task = asyncio.create_task(job())

        async def waiter():
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled() or task.done():
                    return await task
                raise

        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        wait_task.cancel()
        try:
            await wait_task
        except asyncio.CancelledError:
            pass
        await task
        assert finished.is_set()
        assert task.result() == {"ok": True}

    asyncio.run(_run())


if __name__ == "__main__":
    test_cancel_image_generation_cancels_task()
    test_cancel_image_generation_noop_when_idle()
    test_shield_keeps_job_when_waiter_cancelled()
    print("\033[32mok\033[0m")
