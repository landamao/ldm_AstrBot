"""最小复现：备份下载是否会卡死事件循环。

模拟 dashboard 的完整链路：FastAPI + BaseHTTPMiddleware(auth) + FileResponse，
用 hypercorn serve 338MB 的真实备份，同时用后台任务测量事件循环延迟。
用法：python tests/_repro_download_freeze.py
"""
import asyncio
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from hypercorn.asyncio import serve
from hypercorn.config import Config

BACKUP = os.path.join("data", "backups", "ldmbot_backup_20260912_213238_20260912_213411.zip")
PORT = 6186
DURATION = 25.0

app = FastAPI()


@app.middleware("http")
async def fake_auth_middleware(request: Request, call_next):
    # 与 dashboard 一致：BaseHTTPMiddleware 包一层
    return await call_next(request)


@app.get("/download")
async def download():
    return FileResponse(
        BACKUP,
        filename=os.path.basename(BACKUP),
        media_type="application/zip",
    )


loop_lags: list[float] = []


async def loop_monitor():
    while True:
        start = time.perf_counter()
        await asyncio.sleep(0.05)
        lag = time.perf_counter() - start - 0.05
        loop_lags.append(lag)


async def pinger(stop: asyncio.Event, results: list[float]):
    """下载期间持续发小 JSON 请求，模拟 WebUI 其他接口的响应延迟。"""
    import urllib.request
    while not stop.is_set():
        start = time.perf_counter()
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/ping", timeout=10).read()
        except Exception:
            pass
        results.append(time.perf_counter() - start)
        await asyncio.sleep(0.3)


@app.get("/ping")
async def ping():
    return {"ok": True}


async def main():
    monitor = asyncio.create_task(loop_monitor())
    stop = asyncio.Event()
    ping_results: list[float] = []
    pinger_task = asyncio.create_task(pinger(stop, ping_results))
    config = Config()
    config.bind = [f"127.0.0.1:{PORT}"]
    server = asyncio.create_task(serve(app, config))
    await asyncio.sleep(1.0)

    curl = await asyncio.create_subprocess_exec(
        "curl", "-s", "-o", "NUL", "--limit-rate", "5M",
        f"http://127.0.0.1:{PORT}/download",
        stdout=asyncio.subprocess.PIPE,
    )
    start = time.perf_counter()
    out, _ = await curl.communicate()
    elapsed = time.perf_counter() - start
    speed = int(out.decode() or 0)

    monitor.cancel()
    server.cancel()
    lags_ms = [l * 1000 for l in loop_lags]
    lags_ms.sort()
    size_mb = os.path.getsize(BACKUP) / 1024 / 1024
    print(f"文件: {size_mb:.0f}MB, 下载耗时 {elapsed:.1f}s, 速度 {speed/1024/1024:.1f}MB/s")
    print(f"采样 {len(lags_ms)} 次 (每 50ms 一次)")
    print(f"事件循环延迟: p50={lags_ms[len(lags_ms)//2]:.1f}ms "
          f"p95={lags_ms[int(len(lags_ms)*0.95)]:.1f}ms max={lags_ms[-1]:.1f}ms")
    worst = sorted(lags_ms[-10:], reverse=True)
    print(f"最差 10 次: {[f'{x:.0f}ms' for x in worst]}")


if __name__ == "__main__":
    asyncio.run(main())
