"""复现导出阻塞：模拟 exporter 的同步 zipfile 压缩写盘，测量事件循环延迟。

对照两种模式：
A. 现状——在事件循环里直接 zf.write（exporter.py 的写法）
B. 修复方向——zip 写入放进线程池
用法：python tests/_repro_export_freeze.py
"""
import asyncio
import io
import os
import time
import zipfile

DATA_DIRS = [
    ("data/attachments", "attachments"),
    ("data/plugins", "plugins"),
    ("data/config", "config"),
]
TOTAL_BYTES = 100 * 1024 * 1024  # 每轮压缩约 100MB 数据即止

loop_lags: list[float] = []


async def loop_monitor(stop: asyncio.Event):
    while not stop.is_set():
        start = time.perf_counter()
        await asyncio.sleep(0.05)
        lag = time.perf_counter() - start - 0.05
        loop_lags.append(lag)


def collect_files():
    files = []
    for root_dir, _tag in DATA_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for root, _dirs, names in os.walk(root_dir):
            for n in names:
                p = os.path.join(root, n)
                try:
                    files.append((p, os.path.getsize(p)))
                except OSError:
                    pass
    files.sort(key=lambda x: -x[1])
    return files


def sync_zip_write(files, out_path):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        written = 0
        for p, sz in files:
            zf.write(p, os.path.basename(p))
            written += sz
            if written >= TOTAL_BYTES:
                break


async def run_mode(mode: str, files):
    loop_lags.clear()
    stop = asyncio.Event()
    monitor = asyncio.create_task(loop_monitor(stop))
    await asyncio.sleep(0.2)

    out = f"data/backups/.repro_{mode}.zip"
    start = time.perf_counter()
    if mode == "A":
        sync_zip_write(files, out)  # 现状：事件循环上直接写
        await asyncio.sleep(0)
    else:
        await asyncio.to_thread(sync_zip_write, files, out)  # 线程池
    elapsed = time.perf_counter() - start

    stop.set()
    await monitor
    os.remove(out)

    lags_ms = sorted(l * 1000 for l in loop_lags)
    print(f"模式{mode}: 耗时 {elapsed:.1f}s | 循环延迟 p50={lags_ms[len(lags_ms)//2]:.0f}ms "
          f"p95={lags_ms[int(len(lags_ms)*0.95)]:.0f}ms max={lags_ms[-1]:.0f}ms")
    if lags_ms:
        print(f"  最差5次: {[f'{x:.0f}ms' for x in sorted(lags_ms[-5:], reverse=True)]}")


async def main():
    files = collect_files()
    total = sum(s for _, s in files[:40])
    print(f"取最大的 40 个文件共 {total/1024/1024:.0f}MB 参与压缩")
    await run_mode("A", files[:40])
    await run_mode("B", files[:40])


if __name__ == "__main__":
    asyncio.run(main())
