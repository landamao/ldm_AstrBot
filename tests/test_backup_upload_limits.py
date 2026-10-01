"""备份上传链路的内存上限、owner 绑定与断点续传测试。

对照上游 AstrBot PR #10123 移植；错误文案为魔改版中文，匹配串相应调整。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrbot.core.utils.upload import UploadTooLargeError
from astrbot.dashboard.services.backup_service import (
    CHUNK_SIZE,
    MAX_BACKUP_TOTAL_BYTES,
    BackupService,
    BackupServiceError,
)


class _StubUploadFile:
    """模拟 adapter 风格 save() 契约的上传文件对象"""

    def __init__(self, data: bytes):
        self._data = data

    async def save(self, destination, *, max_bytes=None) -> int:
        if max_bytes is not None and len(self._data) > max_bytes:
            raise UploadTooLargeError(max_bytes)
        Path(destination).write_bytes(self._data)
        return len(self._data)


class TestBackupUploadLimits:
    """备份上传大小限制测试"""

    @pytest.fixture
    def backup_service(self, tmp_path):
        """创建使用临时目录的 BackupService"""
        service = BackupService(db=MagicMock(), core_lifecycle=MagicMock())
        service.backup_dir = str(tmp_path / "backups")
        service.chunks_dir = str(tmp_path / "backups" / ".chunks")
        service.chunked_uploads.chunks_root = Path(service.chunks_dir)
        return service

    def test_upload_init_rejects_oversized_total(self, backup_service):
        """声明总大小超过上限时拒绝初始化"""
        with pytest.raises(BackupServiceError, match="大小上限"):
            backup_service.upload_init(
                {"filename": "b.zip", "total_size": MAX_BACKUP_TOTAL_BYTES + 1},
                owner="tester",
            )

    @pytest.mark.asyncio
    async def test_upload_chunk_rejects_oversized_chunk(self, backup_service):
        """超过 CHUNK_SIZE 的分片被拒绝且不产生残留文件"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": CHUNK_SIZE}, owner="tester"
        )
        big_chunk = _StubUploadFile(b"x" * (CHUNK_SIZE + 1))

        with pytest.raises(BackupServiceError, match="分片大小超过上限"):
            await backup_service.upload_chunk(
                upload_id=session["upload_id"],
                chunk_index_str="0",
                chunk_file=big_chunk,
                owner="tester",
            )

        await backup_service.cleanup_upload_session(session["upload_id"])

    @pytest.mark.asyncio
    async def test_upload_chunk_accepts_small_chunk(self, backup_service):
        """正常大小的分片可以上传"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="tester"
        )
        result = await backup_service.upload_chunk(
            upload_id=session["upload_id"],
            chunk_index_str="0",
            chunk_file=_StubUploadFile(b"x" * 100),
            owner="tester",
        )
        assert result["received"] == 1
        assert result["total"] == 1

        await backup_service.cleanup_upload_session(session["upload_id"])

    @pytest.mark.asyncio
    async def test_upload_complete_rejects_size_mismatch(self, backup_service):
        """合并后大小与声明大小不一致时拒绝完成（分片带外损坏的兜底）"""
        declared = CHUNK_SIZE + 100
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": declared}, owner="tester"
        )
        await backup_service.upload_chunk(
            upload_id=session["upload_id"],
            chunk_index_str="0",
            chunk_file=_StubUploadFile(b"x" * CHUNK_SIZE),
            owner="tester",
        )
        await backup_service.upload_chunk(
            upload_id=session["upload_id"],
            chunk_index_str="1",
            chunk_file=_StubUploadFile(b"x" * 100),
            owner="tester",
        )
        # 传片时的字节数校验已拦截短写；此处绕过保存路径直接篡改磁盘上的
        # 分片（模拟带外损坏），验证合并时的大小复核仍然兜底
        chunk_path = (
            backup_service.chunked_uploads.chunks_root / session["upload_id"] / "1.part"
        )
        chunk_path.write_bytes(b"x" * 50)

        with pytest.raises(BackupServiceError, match="不一致"):
            await backup_service.upload_complete(
                {"upload_id": session["upload_id"]}, owner="tester"
            )

        await backup_service.cleanup_upload_session(session["upload_id"])

    @pytest.mark.asyncio
    async def test_session_rejects_other_owner(self, backup_service):
        """会话绑定创建者，其他用户无法传片、合并或取消"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )

        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            await backup_service.upload_chunk(
                upload_id=session["upload_id"],
                chunk_index_str="0",
                chunk_file=_StubUploadFile(b"x" * 100),
                owner="mallory",
            )
        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            await backup_service.upload_complete(
                {"upload_id": session["upload_id"]}, owner="mallory"
            )
        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            await backup_service.upload_abort(
                {"upload_id": session["upload_id"]}, owner="mallory"
            )

        # 会话在攻击后仍然存活，真正的主人可以正常使用
        result = await backup_service.upload_chunk(
            upload_id=session["upload_id"],
            chunk_index_str="0",
            chunk_file=_StubUploadFile(b"x" * 100),
            owner="alice",
        )
        assert result["received"] == 1

        await backup_service.cleanup_upload_session(session["upload_id"])

    @pytest.mark.asyncio
    async def test_upload_status_reports_progress(self, backup_service):
        """状态查询返回已收分片，支持乱序后的续传定位"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": CHUNK_SIZE * 2}, owner="tester"
        )
        # 故意乱序：只传第 1 片（索引 1）
        await backup_service.upload_chunk(
            upload_id=session["upload_id"],
            chunk_index_str="1",
            chunk_file=_StubUploadFile(b"x" * CHUNK_SIZE),
            owner="tester",
        )

        status = backup_service.upload_status(
            {"upload_id": session["upload_id"]}, owner="tester"
        )
        assert status["received_chunks"] == [1]
        assert status["total_chunks"] == 2
        assert status["chunk_size"] == CHUNK_SIZE
        assert 0 < status["expires_in"] <= 3600

        await backup_service.cleanup_upload_session(session["upload_id"])

    def test_upload_status_bound_to_owner(self, backup_service):
        """状态查询同样绑定 owner，且不能探测他人会话"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )

        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            backup_service.upload_status(
                {"upload_id": session["upload_id"]}, owner="mallory"
            )
        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            backup_service.upload_status({"upload_id": "no-such-id"}, owner="alice")

    def test_upload_status_does_not_extend_lifetime(self, backup_service):
        """查询状态不得刷新 last_activity，否则轮询会让会话永不过期"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )
        inner = backup_service.chunked_uploads.get_session(
            session["upload_id"], owner="alice"
        )
        inner.last_activity -= 100  # 模拟会话已经闲置了 100 秒

        status = backup_service.upload_status(
            {"upload_id": session["upload_id"]}, owner="alice"
        )
        assert status["expires_in"] <= 3600 - 100 + 1
        assert (
            backup_service.chunked_uploads.get_session(
                session["upload_id"], owner="alice"
            ).last_activity
            == inner.last_activity
        )

    @pytest.mark.asyncio
    async def test_failed_chunk_retry_preserves_received_chunk(self, backup_service):
        """同索引重传失败不得破坏已收到的分片"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )
        upload_id = session["upload_id"]
        good = b"x" * 100
        await backup_service.upload_chunk(
            upload_id=upload_id,
            chunk_index_str="0",
            chunk_file=_StubUploadFile(good),
            owner="alice",
        )

        # 同索引用超大分片重试：必须报错，且已收到的分片原样保留
        with pytest.raises(BackupServiceError, match="超过上限"):
            await backup_service.upload_chunk(
                upload_id=upload_id,
                chunk_index_str="0",
                chunk_file=_StubUploadFile(b"y" * (CHUNK_SIZE + 1)),
                owner="alice",
            )

        result = await backup_service.upload_complete(
            {"upload_id": upload_id}, owner="alice"
        )
        assert result["size"] == 100
        merged = Path(backup_service.backup_dir) / result["filename"]
        assert merged.read_bytes() == good

    @pytest.mark.asyncio
    async def test_chunk_publish_failure_cleans_temp(self, backup_service, monkeypatch):
        """原子改名失败时临时文件必须清理，已收分片不受影响"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": CHUNK_SIZE + 100}, owner="alice"
        )
        upload_id = session["upload_id"]
        await backup_service.upload_chunk(
            upload_id=upload_id,
            chunk_index_str="0",
            chunk_file=_StubUploadFile(b"x" * CHUNK_SIZE),
            owner="alice",
        )

        def _boom(*args, **kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(
            "astrbot.dashboard.services.chunked_upload_service.os.replace", _boom
        )
        with pytest.raises(OSError, match="simulated rename failure"):
            await backup_service.upload_chunk(
                upload_id=upload_id,
                chunk_index_str="1",
                chunk_file=_StubUploadFile(b"y" * 100),
                owner="alice",
            )

        chunk_dir = backup_service.chunked_uploads.chunks_root / upload_id
        assert not list(chunk_dir.glob("*.tmp"))
        # 改名失败的分片未登记，已收到的第 0 片完好
        status = backup_service.upload_status({"upload_id": upload_id}, owner="alice")
        assert status["received_chunks"] == [0]
        assert (chunk_dir / "0.part").read_bytes() == b"x" * CHUNK_SIZE

        await backup_service.cleanup_upload_session(upload_id)

    @pytest.mark.asyncio
    async def test_expired_session_is_rejected_and_abortable(self, backup_service):
        """过期会话不可用（get_session 强制过期），但 abort 清理仍有效"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )
        upload_id = session["upload_id"]
        inner = backup_service.chunked_uploads.get_session(upload_id, owner="alice")
        inner.last_activity -= 7200  # 闲置两小时，已过 1 小时过期线

        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            backup_service.upload_status({"upload_id": upload_id}, owner="alice")
        with pytest.raises(BackupServiceError, match="不存在或已过期"):
            await backup_service.upload_chunk(
                upload_id=upload_id,
                chunk_index_str="0",
                chunk_file=_StubUploadFile(b"x" * 100),
                owner="alice",
            )

        # abort 是清理路径，对过期会话仍然有效
        await backup_service.upload_abort({"upload_id": upload_id}, owner="alice")
        assert upload_id not in backup_service.chunked_uploads.sessions

    @pytest.mark.asyncio
    async def test_upload_init_starts_cleanup_task(self, backup_service):
        """备份上传初始化必须启动过期清理任务"""
        backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )
        task = backup_service.chunked_uploads._cleanup_task
        assert task is not None and not task.done()
        task.cancel()

    @pytest.mark.asyncio
    async def test_cleanup_failure_keeps_session_for_retry(
        self, backup_service, monkeypatch
    ):
        """目录删除失败时会话保持注册，看门狗可重试，成功后正常摘除"""
        import shutil

        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )
        upload_id = session["upload_id"]

        calls = {"n": 0}
        real_rmtree = shutil.rmtree

        def _flaky(path, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient fs error")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(
            "astrbot.dashboard.services.chunked_upload_service.shutil.rmtree", _flaky
        )
        await backup_service.chunked_uploads.cleanup_session(upload_id)
        assert upload_id in backup_service.chunked_uploads.sessions

        await backup_service.chunked_uploads.cleanup_session(upload_id)
        assert upload_id not in backup_service.chunked_uploads.sessions

    @pytest.mark.asyncio
    async def test_short_write_chunk_is_rejected(self, backup_service):
        """save() 落盘字节数不足时不得发布分片，且不留临时文件"""
        session = backup_service.upload_init(
            {"filename": "b.zip", "total_size": 100}, owner="alice"
        )
        upload_id = session["upload_id"]

        with pytest.raises(BackupServiceError, match="大小不匹配"):
            await backup_service.upload_chunk(
                upload_id=upload_id,
                chunk_index_str="0",
                chunk_file=_StubUploadFile(b"x" * 50),
                owner="alice",
            )

        # 分片未发布、无临时文件残留；补传完整数据后正常完成
        chunk_dir = backup_service.chunked_uploads.chunks_root / upload_id
        assert not list(chunk_dir.glob("*.tmp"))
        status = backup_service.upload_status({"upload_id": upload_id}, owner="alice")
        assert status["received_chunks"] == []

        await backup_service.upload_chunk(
            upload_id=upload_id,
            chunk_index_str="0",
            chunk_file=_StubUploadFile(b"y" * 100),
            owner="alice",
        )
        result = await backup_service.upload_complete(
            {"upload_id": upload_id}, owner="alice"
        )
        assert result["size"] == 100
