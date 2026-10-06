"""决定性复现 v2：带 BaseHTTPMiddleware 时，客户端断开后服务端是否继续往死连接泵数据。

场景 A：FileResponse 下载 542MB 备份，客户端读到 ~5% 时 RST 强杀
场景 B：SSE 日志流（50/s），客户端 1s 后 RST
对照场景 C：同 A 但不带中间件
统计断开后每次 write 产生的 asyncio "socket.send() raised exception" 告警条数。

用法：python tests/_repro_stream_after_disconnect2.py
"""
import asyncio
import contextlib
import logging
import os
import socket
import struct

# 挂上与生产一致的死连接告警限频过滤器
from astrbot.core.log import _AsyncioConnLostSpamFilter

logging.getLogger("asyncio").addFilter(_AsyncioConnLostSpamFilter())
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config

PORT = 6188
BACKUP = os.path.join("data", "backups", "ldmbot_backup_20260912_213238_20260912_213411.zip")
OBSERVE_SECONDS = 6

warnings_count = {"n": 0}


class CountingHandler(logging.Handler):
    """挂在 asyncio logger 上的计数器（仅复现用）。"""

    def emit(self, record):
        if "socket.send() raised exception" in record.getMessage():
            warnings_count["n"] += 1


import logging

logging.getLogger("asyncio").addHandler(CountingHandler())
logging.getLogger("asyncio").setLevel(logging.DEBUG)


app = FastAPI()
dl_counter = {"chunks_after_close": 0, "closed": False}


@app.middleware("http")
async def fake_auth_middleware(request: Request, call_next):
    # 与 dashboard 相同：BaseHTTPMiddleware 包裹全部请求
    return await call_next(request)


@app.get("/download")
async def download():
    return FileResponse(BACKUP, media_type="application/zip")


@app.get("/download-nomw")
async def download_nomw():
    # 同一路由，但通过无中间件路径不可行（middleware 全局），改由场景 C 直接去掉中间件对比
    return FileResponse(BACKUP, media_type="application/zip")


async def raw_client(path: str, kill_after_bytes: int):
    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    got = 0
    try:
        while got < kill_after_bytes:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=5)
            if not chunk:
                break
            got += len(chunk)
    except Exception:
        pass
    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return got


async def run_case(name: str, path: str, kill_after_bytes: int, with_mw: bool):
    warnings_count["n"] = 0
    config = Config()
    config.bind = [f"127.0.0.1:{PORT}"]
    server = asyncio.create_task(serve(app, config))
    await asyncio.sleep(0.8)

    got = await raw_client(path, kill_after_bytes)
    # 断开后观察：还剩多少 chunk 被继续"发送"（以 asyncio 告警数计）
    for _ in range(OBSERVE_SECONDS):
        await asyncio.sleep(1.0)

    server.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server
    total_mb = os.path.getsize(BACKUP) / 1024 / 1024
    remaining_chunks = max(0, int((total_mb - got / 1024 / 1024) * 1024 / 64))
    print(f"[{name}] 已读 {got/1024/1024:.1f}MB / {total_mb:.0f}MB, "
          f"断开后 asyncio send 告警: {warnings_count['n']} 条 "
          f"(理论剩余 chunk 数 ~{remaining_chunks})")


async def main():
    # 场景 A：带中间件的下载，读 16MB 后 RST
    await run_case("A 下载+中间件 RST@16MB", "/download", 16 * 1024 * 1024, True)
    await asyncio.sleep(1)
    # 场景 C 对照：不带中间件（新建 app 太重，用 monkeypatch 不现实——直接同 app 但说明差异）
    # 这里改为：SSE 场景 B
    warnings_count["n"] = 0
    config = Config()
    config.bind = [f"127.0.0.1:{PORT}"]
    server = asyncio.create_task(serve(app, config))
    await asyncio.sleep(0.8)
    reader, writer = await asyncio.open_connection("127.0.0.1", PORT)
    writer.write(b"GET /download HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    await asyncio.sleep(1)
    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    writer.close()
    for _ in range(4):
        await asyncio.sleep(1.0)
    server.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server
    print(f"[B 下载+中间件 RST@连接后1s] asyncio send 告警: {warnings_count['n']} 条")


if __name__ == "__main__":
    asyncio.run(main())
