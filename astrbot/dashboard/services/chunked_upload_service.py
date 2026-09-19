"""Generic chunked-upload session management.

Business services keep ownership of file validation, final naming and
post-merge handling; this service owns the session lifecycle, chunk storage,
streaming assembly and expiry cleanup. Every session is bound to an owner
(the authenticated username) and a purpose (the consuming business), so a
session created for one business cannot be consumed by another caller or
another upload flow.
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astrbot.core import logger
from astrbot.core.utils.upload import UploadTooLargeError

DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_EXPIRE_SECONDS = 3600
CLEANUP_INTERVAL_SECONDS = 300


class ChunkedUploadError(Exception):
    pass


@dataclass
class UploadSession:
    id: str
    owner: str
    purpose: str
    filename: str
    original_filename: str
    total_size: int
    total_chunks: int
    chunk_size: int
    chunk_dir: Path
    meta: dict = field(default_factory=dict)
    received_chunks: set[int] = field(default_factory=set)
    created_at: float = 0.0
    last_activity: float = 0.0


class ChunkedUploadService:
    """Manage chunked upload sessions on disk with bounded memory."""

    def __init__(
        self,
        chunks_root: str | Path,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        expire_seconds: int = DEFAULT_EXPIRE_SECONDS,
    ) -> None:
        self.chunks_root = Path(chunks_root)
        self.chunk_size = chunk_size
        self.expire_seconds = expire_seconds
        self.sessions: dict[str, UploadSession] = {}
        self._cleanup_task: asyncio.Task | None = None

    def init_session(
        self,
        *,
        owner: str,
        purpose: str,
        filename: str,
        original_filename: str,
        total_size: int,
        meta: dict | None = None,
    ) -> UploadSession:
        """创建会话及其分片目录。

        total_size 的业务上限（如备份 8 GiB）由调用方在进入前校验，
        这里只做基本的合法性检查。
        """
        if total_size <= 0:
            raise ChunkedUploadError("无效的文件大小")

        upload_id = str(uuid.uuid4())
        chunk_dir = self.chunks_root / upload_id
        chunk_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        session = UploadSession(
            id=upload_id,
            owner=owner,
            purpose=purpose,
            filename=filename,
            original_filename=original_filename,
            total_size=total_size,
            total_chunks=math.ceil(total_size / self.chunk_size),
            chunk_size=self.chunk_size,
            chunk_dir=chunk_dir,
            meta=meta or {},
            created_at=now,
            last_activity=now,
        )
        self.sessions[upload_id] = session
        logger.info(
            f"分片上传会话开始: id={upload_id}, owner={owner}, "
            f"purpose={purpose}, file={filename}, chunks={session.total_chunks}"
        )
        return session

    def get_session(self, upload_id: str, *, owner: str | None = None) -> UploadSession:
        """查找会话，可选择性校验归属。

        会话不存在、已过期与归属不符统一报同一错误，避免探测他人会话。
        后台清理任务负责回收过期的分片目录；本检查保证过期会话在
        被回收前也不再可用。
        """
        session = self.sessions.get(upload_id)
        if (
            session is None
            or (owner is not None and session.owner != owner)
            or time.time() - session.last_activity > self.expire_seconds
        ):
            raise ChunkedUploadError("上传会话不存在或已过期")
        return session

    async def save_chunk(
        self,
        upload_id: str,
        chunk_index: int,
        file: Any,
        *,
        owner: str | None = None,
    ) -> dict:
        """落盘单个分片；重复上传与乱序到达都是幂等的。

        Args:
            file: 暴露适配器 ``save()`` 契约的上传对象。

        Raises:
            ChunkedUploadError: 会话不存在、索引越界或分片超限。
        """
        session = self.get_session(upload_id, owner=owner)
        if chunk_index < 0 or chunk_index >= session.total_chunks:
            raise ChunkedUploadError("分片索引超出范围")

        # 先写唯一命名的临时文件再原子发布：同一分片的失败重试或并发
        # 重传不能覆盖已接收的分片。
        chunk_path = session.chunk_dir / f"{chunk_index}.part"
        temp_path = session.chunk_dir / f"{chunk_index}.{uuid.uuid4().hex}.tmp"
        try:
            written = await file.save(temp_path, max_bytes=session.chunk_size)
        except BaseException as exc:
            temp_path.unlink(missing_ok=True)
            if isinstance(exc, UploadTooLargeError):
                raise ChunkedUploadError("分片大小超过上限") from exc
            raise

        # save 契约返回写入字节数；短写或空写视为分片不可用，不得发布。
        expected = min(
            session.total_size - chunk_index * session.chunk_size,
            session.chunk_size,
        )
        if written != expected:
            temp_path.unlink(missing_ok=True)
            raise ChunkedUploadError(
                f"分片大小不匹配：收到 {written} 字节，预期 {expected} 字节"
            )

        try:
            await asyncio.to_thread(os.replace, temp_path, chunk_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        session.received_chunks.add(chunk_index)
        session.last_activity = time.time()

        logger.debug(
            f"接收分片: id={upload_id}, "
            f"chunk={chunk_index + 1}/{session.total_chunks}"
        )
        return {
            "received": len(session.received_chunks),
            "total": session.total_chunks,
            "chunk_index": chunk_index,
        }

    def session_status(self, upload_id: str, *, owner: str | None = None) -> dict:
        """返回可用于续传的会话进度。

        有意只读：不得刷新 ``last_activity``，否则轮询该端点会一直给
        会话（及其磁盘分片）续命，使过期机制失效。
        """
        session = self.get_session(upload_id, owner=owner)
        remaining = self.expire_seconds - (time.time() - session.last_activity)
        return {
            "received_chunks": sorted(session.received_chunks),
            "total_chunks": session.total_chunks,
            "chunk_size": session.chunk_size,
            "expires_in": max(0, int(remaining)),
        }

    async def assemble(
        self,
        upload_id: str,
        dest: str | Path,
        *,
        owner: str | None = None,
    ) -> int:
        """合并全部分片到 dest，校验大小并清理会话。

        Returns:
            合并后文件的字节数。

        Raises:
            ChunkedUploadError: 分片缺失或合并大小与声明不符。
        """
        session = self.get_session(upload_id, owner=owner)
        if len(session.received_chunks) != session.total_chunks:
            missing = sorted(set(range(session.total_chunks)) - session.received_chunks)
            raise ChunkedUploadError(f"分片不完整，缺少: {missing[:10]}...")

        dest_path = Path(dest)
        try:
            with dest_path.open("wb") as outfile:
                for i in range(session.total_chunks):
                    with (session.chunk_dir / f"{i}.part").open("rb") as part:
                        shutil.copyfileobj(part, outfile)
            size = dest_path.stat().st_size
            if size != session.total_size:
                raise ChunkedUploadError(
                    f"合并后大小（{size} 字节）与声明大小（{session.total_size} 字节）不一致"
                )
        except BaseException:
            dest_path.unlink(missing_ok=True)
            raise

        await self.cleanup_session(upload_id)
        return size

    async def abort(self, upload_id: str, *, owner: str | None = None) -> bool:
        """中止并清理会话。会话不存在时静默成功。

        过期会话也可以中止（清理本来就是目的），但绝不动他人会话。

        Returns:
            会话存在且已移除时返回 True。

        Raises:
            ChunkedUploadError: 会话存在但属于其他用户。
        """
        session = self.sessions.get(upload_id)
        if session is None:
            return False
        if owner is not None and session.owner != owner:
            raise ChunkedUploadError("上传会话不存在或已过期")
        await self.cleanup_session(upload_id)
        return True

    async def cleanup_session(self, upload_id: str) -> None:
        session = self.sessions.get(upload_id)
        if session is None:
            return
        if session.chunk_dir.exists():
            try:
                shutil.rmtree(session.chunk_dir)
            except Exception as exc:
                logger.warning(f"移除分片目录失败 {session.chunk_dir}: {exc}")
                # 保留会话登记，让后台任务稍后重试清理残留目录，
                # 而不是永久遗忘。
                return
        self.sessions.pop(upload_id, None)

    def ensure_cleanup_task_started(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            coro = self._cleanup_expired_sessions()
            try:
                self._cleanup_task = asyncio.create_task(coro)
            except RuntimeError:
                # 无事件循环（如同步测试/导入期）：关闭协程避免未 await 警告
                coro.close()

    async def _cleanup_expired_sessions(self) -> None:
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                now = time.time()
                expired = [
                    upload_id
                    for upload_id, session in self.sessions.items()
                    if now - session.last_activity > self.expire_seconds
                ]
                for upload_id in expired:
                    await self.cleanup_session(upload_id)
                    logger.info(f"清理过期的上传会话: {upload_id}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"清理过期上传会话失败: {exc}")
