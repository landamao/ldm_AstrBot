"""决定性复现：客户端断开后，hypercorn + starlette 流式响应是否继续往死连接泵数据。

模拟事故链路：
- SSE 日志流端点（同 dashboard 的 stream_log_events 模式），50 条/秒持续推送
- 客户端连接 1 秒后断开（分别测 FIN 干净断开 / RST 强杀）
- 观察断开后服务端是否继续 write、asyncio 是否刷 "socket.send() raised exception"

用法：python tests/_repro_stream_after_disconnect.py
"""
import asyncio
import contextlib
import socket
import time

import uvicorn  # 仅用于对照；主测试走 hypercorn

del uvicorn  # noqa - 防误导入

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config

PORT = 6187
SEND_RATE = 50  # 每秒推送条数
OBSERVE_SECONDS = 8  # 客户端断开后观察时长

app = FastAPI()
pump_counter = {"after_close": 0, "total": 0, "task_done": False}


async def sse_stream(client_id: str):
    """与 dashboard stream_log_events 同构：无限生成器 + yield 每条日志。"""
    i = 0
    while True:
        i += 1
        pump_counter["total"] += 1
        yield f"id: {i}\ndata: log line {i}\n\n"
        await asyncio.sleep(1 / SEND_RATE)


@app.get("/sse")
async def sse():
    async def guarded():
        try:
            async for chunk in sse_stream("x"):
                pump_counter["after_close"] += 0  # 占位，真正的计数在 ASGI 层下方
                yield chunk
        except asyncio.CancelledError:
            pump_counter["task_done"] = True
            raise

    return StreamingResponse(guarded(), media_type="text/event-stream")


async def raw_client(mode: str):
    """原始 socket 客户端：读完响应头+几行后断开。mode=fin 干净断开 / rst 强杀。"""
    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    writer.write(b"GET /sse HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()
    await asyncio.sleep(1.0)
    try:
        await reader.read(4096)  # 读一点确认流在推
    except Exception:
        pass
    if mode == "rst":
        # SO_LINGER=0 → 关闭时发 RST
        raw = writer.get_extra_info("socket")
        import struct

        raw.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def monitor(snapshot_log: list):
    """每秒记录泵送计数，模拟 journald 侧观察。"""
    last_total = 0
    for _ in range(OBSERVE_SECONDS):
        await asyncio.sleep(1.0)
        snapshot_log.append(
            (pump_counter["total"] - last_total, pump_counter["task_done"])
        )
        last_total = pump_counter["total"]


async def run_case(mode: str):
    pump_counter.update({"after_close": 0, "total": 0, "task_done": False})
    config = Config()
    config.bind = [f"127.0.0.1:{PORT}"]
    server = asyncio.create_task(serve(app, config))
    await asyncio.sleep(0.8)

    snapshots: list = []
    mon = asyncio.create_task(monitor(snapshots))
    await raw_client(mode)
    await mon
    server.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server

    rates = [s[0] for s in snapshots]
    still = sum(rates) / len(rates)
    print(f"[{mode}] 断开后 {OBSERVE_SECONDS}s 泵送速率: "
          f"{rates} 条/秒 (均值 {still:.0f}/s)，"
          f"响应任务已结束: {pump_counter['task_done']}")


async def main():
    await run_case("fin")
    await asyncio.sleep(1)
    await run_case("rst")


if __name__ == "__main__":
    asyncio.run(main())
