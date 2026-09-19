"""决定性复现 v3：SSE 日志流 + BaseHTTPMiddleware，客户端 RST 后服务端是否无限泵送。

事故假设：SSE 是无限数据源（只要 bot 打日志就一直有），若断连后仍持续推送，
每次 write 产生一条 asyncio "socket.send() raised exception" → 持续刷屏直到系统冻死。

用法：python tests/_repro_sse_forever.py
"""
import asyncio
import contextlib
import logging
import socket
import struct

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config

PORT = 6189
SEND_RATE = 50  # 模拟 bot 日志速率：50 条/秒

warnings_count = {"n": 0}


class CountingHandler(logging.Handler):
    def emit(self, record):
        if "socket.send() raised exception" in record.getMessage():
            warnings_count["n"] += 1


logging.getLogger("asyncio").addHandler(CountingHandler())
logging.getLogger("asyncio").setLevel(logging.DEBUG)

app = FastAPI()
stream_state = {"yielding": True}


@app.middleware("http")
async def fake_auth_middleware(request: Request, call_next):
    return await call_next(request)


@app.get("/sse")
async def sse():
    async def gen():
        i = 0
        try:
            while True:
                i += 1
                yield f"id: {i}\ndata: log {i}\n\n"
                await asyncio.sleep(1 / SEND_RATE)
        except asyncio.CancelledError:
            stream_state["yielding"] = False
            raise

    return StreamingResponse(gen(), media_type="text/event-stream")


async def raw_client():
    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    writer.write(b"GET /sse HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    await asyncio.sleep(1.0)
    with contextlib.suppress(Exception):
        await reader.read(4096)
    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def main():
    config = Config()
    config.bind = [f"127.0.0.1:{PORT}"]
    server = asyncio.create_task(serve(app, config))
    await asyncio.sleep(0.8)

    await raw_client()
    print("客户端已 RST，观察 10 秒：")
    for sec in range(1, 11):
        await asyncio.sleep(1.0)
        print(f"  +{sec}s: 累计 send 告警 {warnings_count['n']} 条, 生成器仍存活: {stream_state['yielding']}")

    server.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server
    rate = warnings_count["n"] / 10
    print(f"结论: 断连后刷屏速率 {rate:.0f} 条/秒"
          f"{'（持续刷屏，速率≈日志速率）' if rate > 10 else '（已停止）'}")


if __name__ == "__main__":
    asyncio.run(main())
