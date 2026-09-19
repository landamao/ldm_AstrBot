from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.core import logger
from astrbot.core.backup.exporter import AstrBotExporter
from astrbot.core.backup.importer import AstrBotImporter
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.db import BaseDatabase
from astrbot.core.utils.astrbot_path import (
    get_astrbot_backups_path,
    get_astrbot_data_path,
)
from astrbot.core.utils.upload import UploadTooLargeError
from astrbot.dashboard.services.chunked_upload_service import (
    ChunkedUploadError,
    ChunkedUploadService,
)

CHUNK_SIZE = 1024 * 1024
# 磁盘占用兜底上限：合法备份不会超过这个量级；直传端点只服务小备份，
# 大备份必须走分片上传。
MAX_BACKUP_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_DIRECT_UPLOAD_BYTES = 128 * 1024 * 1024
# 下载票据：短时可复用（非一次性）。
# 手机下载器常会 HEAD/重试/并发二次请求同一 URL；一次性 pop 会把第二次变成 61B 错误 JSON。
# URL 只带随机串，不带登录 JWT；真正的大文件仍走 FileResponse 流式。
DOWNLOAD_TICKET_TTL_SECONDS = 600  # 10 分钟，覆盖慢网/大文件启动与短时重试
DOWNLOAD_TICKET_MAX_ENTRIES = 256


class BackupServiceError(Exception):
    pass


@dataclass
class BackupDownload:
    path: str
    filename: str


def secure_filename(filename: str) -> str:
    filename = filename.replace("\\", "/")
    filename = os.path.basename(filename)
    filename = filename.replace("..", "_")
    filename = re.sub(r"[^\w\-.]", "_", filename)
    filename = filename.strip(".")
    if not filename or filename.replace("_", "") == "":
        filename = "backup"
    return filename


def generate_unique_filename(original_filename: str) -> str:
    name, ext = os.path.splitext(original_filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{name}_{timestamp}{ext}"


class BackupService:
    def __init__(
        self,
        db: BaseDatabase,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        self.db = db
        self.core_lifecycle = core_lifecycle
        self.config = core_lifecycle.astrbot_config
        self.backup_dir = get_astrbot_backups_path()
        self.data_dir = get_astrbot_data_path()
        self.chunks_dir = os.path.join(self.backup_dir, ".chunks")
        self.backup_tasks: dict[str, dict] = {}
        self.backup_progress: dict[str, dict] = {}
        self.chunked_uploads = ChunkedUploadService(self.chunks_dir)
        self.download_tickets: dict[str, dict] = {}

    @staticmethod
    def _payload(data: object) -> dict[str, Any]:
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _validate_backup_filename(filename: str | None, *, missing: str) -> str:
        if not filename:
            raise BackupServiceError(missing)
        if ".." in filename or "/" in filename or "\\" in filename:
            raise BackupServiceError("无效的文件名")
        return filename

    def _init_task(self, task_id: str, task_type: str, status: str = "pending") -> None:
        self.backup_tasks[task_id] = {
            "type": task_type,
            "status": status,
            "result": None,
            "error": None,
        }
        self.backup_progress[task_id] = {
            "status": status,
            "stage": "waiting",
            "current": 0,
            "total": 100,
            "message": "",
        }

    def _set_task_result(
        self,
        task_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        if task_id in self.backup_tasks:
            self.backup_tasks[task_id]["status"] = status
            self.backup_tasks[task_id]["result"] = result
            self.backup_tasks[task_id]["error"] = error
        if task_id in self.backup_progress:
            self.backup_progress[task_id]["status"] = status

    def _update_progress(
        self,
        task_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        if task_id not in self.backup_progress:
            return
        progress = self.backup_progress[task_id]
        if status is not None:
            progress["status"] = status
        if stage is not None:
            progress["stage"] = stage
        if current is not None:
            progress["current"] = current
        if total is not None:
            progress["total"] = total
        if message is not None:
            progress["message"] = message

    def _make_progress_callback(self, task_id: str):
        async def _callback(
            stage: str,
            current: int,
            total: int,
            message: str = "",
        ) -> None:
            self._update_progress(
                task_id,
                status="processing",
                stage=stage,
                current=current,
                total=total,
                message=message,
            )

        return _callback

    def ensure_cleanup_task_started(self) -> None:
        self.chunked_uploads.ensure_cleanup_task_started()

    async def cleanup_upload_session(self, upload_id: str) -> None:
        await self.chunked_uploads.cleanup_session(upload_id)

    def get_backup_manifest(self, zip_path: str) -> dict | None:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" in zf.namelist():
                    manifest_data = zf.read("manifest.json")
                    return json.loads(manifest_data.decode("utf-8"))
                return None
        except Exception as exc:
            logger.debug(f"读取备份 manifest 失败: {exc}")
        return None

    def list_backups(self, *, page: int, page_size: int) -> dict:
        self.ensure_cleanup_task_started()
        Path(self.backup_dir).mkdir(parents=True, exist_ok=True)

        backup_files = []
        for filename in os.listdir(self.backup_dir):
            if not filename.endswith(".zip") or filename.startswith("."):
                continue

            file_path = os.path.join(self.backup_dir, filename)
            if not os.path.isfile(file_path):
                continue

            manifest = self.get_backup_manifest(file_path)
            if manifest is None:
                logger.debug(f"跳过无效备份文件: {filename}")
                continue

            stat = os.stat(file_path)
            backup_files.append(
                {
                    "filename": filename,
                    "size": stat.st_size,
                    "created_at": stat.st_mtime,
                    "type": manifest.get("origin", "exported"),
                    "astrbot_version": manifest.get("astrbot_version", "未知"),
                    "exported_at": manifest.get("exported_at"),
                }
            )

        backup_files.sort(key=lambda x: x["created_at"], reverse=True)
        start = (page - 1) * page_size
        end = start + page_size

        return {
            "items": backup_files[start:end],
            "total": len(backup_files),
            "page": page,
            "page_size": page_size,
            "backup_dir": self.backup_dir,
        }

    def export_backup(self, payload: object = None) -> dict:
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(exclude_none=True)
        else:
            data = self._payload(payload)
        filename_suffix = str(data.get("filename_suffix") or "").strip()
        if filename_suffix:
            filename_suffix = re.sub(r"[^\w\-.一-龥]", "_", filename_suffix).strip(".")
        # 导出耗时长且占用磁盘 CPU，不允许多个并发；此前导出卡顿期间连点
        # 会触发多个同名导出互相覆盖，留下截断的残废备份
        if any(
            info.get("type") == "export"
            and info.get("status") in ("pending", "processing")
            for info in self.backup_tasks.values()
        ):
            raise BackupServiceError("已有备份导出任务正在进行中，请等待其完成后再试")
        task_id = str(uuid.uuid4())
        self._init_task(task_id, "export", "pending")
        asyncio.create_task(self.background_export_task(task_id, filename_suffix))
        return {
            "task_id": task_id,
            "message": "export task created, processing in background",
        }

    async def background_export_task(self, task_id: str, filename_suffix: str = "") -> None:
        try:
            self._update_progress(task_id, status="processing", message="正在初始化...")
            kb_manager = getattr(self.core_lifecycle, "kb_manager", None)
            exporter = AstrBotExporter(
                main_db=self.db,
                kb_manager=kb_manager,
                config_path=os.path.join(self.data_dir, "cmd_config.json"),
            )
            zip_path = await exporter.export_all(
                output_dir=self.backup_dir,
                progress_callback=self._make_progress_callback(task_id),
                filename_suffix=filename_suffix,
            )
            export_result = {
                "filename": os.path.basename(zip_path),
                "path": zip_path,
                "size": os.path.getsize(zip_path),
            }
            # 导出过程中的文件级错误（附件缺失、单个文件读取失败等）记录在
            # manifest 中，这里透出到任务结果，避免用户拿到缺数据的备份而不自知
            manifest = self.get_backup_manifest(zip_path) or {}
            export_errors = manifest.get("export_errors") or []
            if export_errors:
                export_result["warnings"] = export_errors
                logger.warning(
                    f"备份 {export_result['filename']} 存在 {len(export_errors)} "
                    f"处文件级导出错误: {export_errors}"
                )
            self._set_task_result(task_id, "completed", result=export_result)
        except Exception as exc:
            logger.error(f"后台导出任务 {task_id} 失败: {exc}")
            logger.error(traceback.format_exc())
            self._set_task_result(task_id, "failed", error=str(exc))

    async def upload_backup(self, file: Any | None) -> dict:
        if not file:
            raise BackupServiceError("缺少备份文件")
        if not file.filename or not file.filename.endswith(".zip"):
            raise BackupServiceError("请上传 ZIP 格式的备份文件")

        safe_filename = secure_filename(file.filename)
        unique_filename = generate_unique_filename(safe_filename)

        Path(self.backup_dir).mkdir(parents=True, exist_ok=True)
        zip_path = os.path.join(self.backup_dir, unique_filename)
        try:
            await file.save(zip_path, max_bytes=MAX_DIRECT_UPLOAD_BYTES)
        except UploadTooLargeError as exc:
            raise BackupServiceError(
                f"备份文件超过大小上限（{MAX_DIRECT_UPLOAD_BYTES // (1024 * 1024)} MB），"
                "请使用分片上传"
            ) from exc

        logger.info(
            f"上传的备份文件已保存: {unique_filename} (原始名称: {file.filename})"
        )
        return {
            "filename": unique_filename,
            "original_filename": file.filename,
            "size": os.path.getsize(zip_path),
        }

    def upload_init(self, data: object, *, owner: str = "") -> dict:
        payload = self._payload(data)
        filename = payload.get("filename")
        total_size = payload.get("total_size", 0)

        if not filename:
            raise BackupServiceError("缺少 filename 参数")
        if not filename.endswith(".zip"):
            raise BackupServiceError("请上传 ZIP 格式的备份文件")
        if total_size <= 0:
            raise BackupServiceError("无效的文件大小")
        if total_size > MAX_BACKUP_TOTAL_BYTES:
            raise BackupServiceError(
                f"备份文件超过大小上限（{MAX_BACKUP_TOTAL_BYTES // (1024 ** 3)} GB）。"
                "可以先用 FTP/SFTP 把文件复制到数据目录的 backups 文件夹，"
                "再从备份列表恢复。"
            )

        unique_filename = generate_unique_filename(secure_filename(filename))
        self.chunked_uploads.ensure_cleanup_task_started()
        try:
            session = self.chunked_uploads.init_session(
                owner=owner,
                purpose="backup",
                filename=unique_filename,
                original_filename=filename,
                total_size=total_size,
            )
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

        return {
            "upload_id": session.id,
            "chunk_size": session.chunk_size,
            "total_chunks": session.total_chunks,
            "filename": unique_filename,
        }

    async def upload_chunk(
        self,
        *,
        upload_id: str | None,
        chunk_index_str: str | None,
        chunk_file: Any | None,
        owner: str = "",
    ) -> dict:
        if not upload_id or chunk_index_str is None:
            raise BackupServiceError("缺少必要参数")

        try:
            chunk_index = int(chunk_index_str)
        except ValueError as exc:
            raise BackupServiceError("无效的分片索引") from exc

        if not chunk_file:
            raise BackupServiceError("缺少分片数据")

        try:
            return await self.chunked_uploads.save_chunk(
                upload_id, chunk_index, chunk_file, owner=owner
            )
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

    def mark_backup_as_uploaded(self, zip_path: str) -> None:
        try:
            manifest = {"origin": "uploaded", "uploaded_at": datetime.now().isoformat()}
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" in zf.namelist():
                    manifest_data = zf.read("manifest.json")
                    manifest = json.loads(manifest_data.decode("utf-8"))
                    manifest["origin"] = "uploaded"
                    manifest["uploaded_at"] = datetime.now().isoformat()

            with zipfile.ZipFile(zip_path, "a") as zf:
                new_manifest = json.dumps(manifest, ensure_ascii=False, indent=2)
                zf.writestr("manifest.json", new_manifest)

            logger.debug(f"已标记备份为上传来源: {zip_path}")
        except Exception as exc:
            logger.warning(f"标记备份来源失败: {exc}")

    async def upload_complete(self, data: object, *, owner: str = "") -> dict:
        payload = self._payload(data)
        upload_id = payload.get("upload_id")

        if not upload_id:
            raise BackupServiceError("缺少 upload_id 参数")

        try:
            session = self.chunked_uploads.get_session(upload_id, owner=owner)
            Path(self.backup_dir).mkdir(parents=True, exist_ok=True)
            output_path = os.path.join(self.backup_dir, session.filename)
            file_size = await self.chunked_uploads.assemble(
                upload_id, output_path, owner=owner
            )
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

        self.mark_backup_as_uploaded(output_path)
        logger.info(
            f"分片上传完成: {session.filename}, size={file_size}, "
            f"chunks={session.total_chunks}"
        )

        return {
            "filename": session.filename,
            "original_filename": session.original_filename,
            "size": file_size,
        }

    async def upload_abort(
        self, data: object, *, owner: str = ""
    ) -> tuple[dict | None, str | None]:
        payload = self._payload(data)
        upload_id = payload.get("upload_id")
        if not upload_id:
            raise BackupServiceError("缺少 upload_id 参数")

        try:
            if await self.chunked_uploads.abort(upload_id, owner=owner):
                logger.info(f"取消分片上传: {upload_id}")
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

        return None, "上传已取消"

    def upload_status(self, data: object, *, owner: str = "") -> dict:
        payload = self._payload(data)
        upload_id = payload.get("upload_id")
        if not upload_id:
            raise BackupServiceError("缺少 upload_id 参数")

        try:
            return self.chunked_uploads.session_status(upload_id, owner=owner)
        except ChunkedUploadError as exc:
            raise BackupServiceError(str(exc)) from exc

    def check_backup(self, data: object) -> dict:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少 filename 参数",
        )
        zip_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(zip_path):
            raise BackupServiceError(f"备份文件不存在: {filename}")

        kb_manager = getattr(self.core_lifecycle, "kb_manager", None)
        importer = AstrBotImporter(
            main_db=self.db,
            kb_manager=kb_manager,
            config_path=os.path.join(self.data_dir, "cmd_config.json"),
        )
        return importer.pre_check(zip_path).to_dict()

    def import_backup(self, data: object) -> dict:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少 filename 参数",
        )
        confirmed = payload.get("confirmed", False)
        if not confirmed:
            raise BackupServiceError(
                "请先确认导入。导入将会清空并覆盖现有数据，此操作不可撤销。"
            )
        restore_webui_port = payload.get("restore_webui_port", False)
        restore_account_password = payload.get("restore_account_password", False)

        zip_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(zip_path):
            raise BackupServiceError(f"备份文件不存在: {filename}")

        task_id = str(uuid.uuid4())
        self._init_task(task_id, "import", "pending")
        asyncio.create_task(
            self.background_import_task(
                task_id,
                zip_path,
                restore_webui_port=restore_webui_port,
                restore_account_password=restore_account_password,
            )
        )

        return {
            "task_id": task_id,
            "message": "import task created, processing in background",
        }

    async def background_import_task(
        self,
        task_id: str,
        zip_path: str,
        *,
        restore_webui_port: bool = False,
        restore_account_password: bool = False,
    ) -> None:
        try:
            self._update_progress(task_id, status="processing", message="正在初始化...")
            kb_manager = getattr(self.core_lifecycle, "kb_manager", None)
            importer = AstrBotImporter(
                main_db=self.db,
                kb_manager=kb_manager,
                config_path=os.path.join(self.data_dir, "cmd_config.json"),
            )
            result = await importer.import_all(
                zip_path=zip_path,
                mode="replace",
                progress_callback=self._make_progress_callback(task_id),
                restore_webui_port=restore_webui_port,
                restore_account_password=restore_account_password,
            )

            if result.success:
                self._set_task_result(task_id, "completed", result=result.to_dict())
            else:
                self._set_task_result(
                    task_id,
                    "failed",
                    error="; ".join(result.errors),
                )
        except Exception as exc:
            logger.error(f"后台导入任务 {task_id} 失败: {exc}")
            logger.error(traceback.format_exc())
            self._set_task_result(task_id, "failed", error=str(exc))

    def get_progress(self, task_id: str | None) -> dict:
        if not task_id:
            raise BackupServiceError("缺少参数 task_id")
        if task_id not in self.backup_tasks:
            raise BackupServiceError("找不到该任务")

        task_info = self.backup_tasks[task_id]
        status = task_info["status"]
        response_data = {
            "task_id": task_id,
            "type": task_info["type"],
            "status": status,
        }

        if status == "processing" and task_id in self.backup_progress:
            response_data["progress"] = self.backup_progress[task_id]
        if status == "completed":
            response_data["result"] = task_info["result"]
        if status == "failed":
            response_data["error"] = task_info["error"]

        return response_data

    def prepare_download(
        self,
        *,
        filename: str | None,
    ) -> BackupDownload:
        """准备备份下载（调用方须已完成会话/票据鉴权）。"""
        filename = self._validate_backup_filename(filename, missing="缺少参数 filename")
        file_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(file_path):
            raise BackupServiceError("备份文件不存在")
        return BackupDownload(path=file_path, filename=filename)

    def _purge_expired_download_tickets(self) -> None:
        now = time.time()
        expired = [
            ticket
            for ticket, info in self.download_tickets.items()
            if float(info.get("exp", 0)) <= now
        ]
        for ticket in expired:
            self.download_tickets.pop(ticket, None)
        # 防止异常堆积
        if len(self.download_tickets) > DOWNLOAD_TICKET_MAX_ENTRIES:
            ordered = sorted(
                self.download_tickets.items(),
                key=lambda item: float(item[1].get("exp", 0)),
            )
            for ticket, _ in ordered[: len(ordered) - DOWNLOAD_TICKET_MAX_ENTRIES]:
                self.download_tickets.pop(ticket, None)

    def issue_download_ticket(
        self,
        filename: str | None,
        *,
        username: str | None = None,
    ) -> dict:
        """签发短时可复用下载票据。需已登录；票据本身不是登录 JWT。"""
        download = self.prepare_download(filename=filename)
        self._purge_expired_download_tickets()
        ticket = secrets.token_urlsafe(32)
        now = time.time()
        self.download_tickets[ticket] = {
            "filename": download.filename,
            "exp": now + DOWNLOAD_TICKET_TTL_SECONDS,
            "username": (username or "").strip(),
        }
        return {
            "ticket": ticket,
            "filename": download.filename,
            "expires_in": DOWNLOAD_TICKET_TTL_SECONDS,
        }

    def consume_download_ticket(
        self,
        *,
        filename: str | None,
        ticket: str | None,
    ) -> BackupDownload:
        """校验下载票据（有效期内可重复使用），返回可流式发送的文件信息。

        不做一次性 pop：手机浏览器/下载管理器常对同一 URL 发 HEAD + GET、
        或并发/重试第二次请求；一次性作废会导致第二次拿到错误 JSON 并被当成文件保存。
        """
        if not ticket or not str(ticket).strip():
            raise BackupServiceError("缺少下载凭证")
        self._purge_expired_download_tickets()
        key = str(ticket).strip()
        info = self.download_tickets.get(key)
        if not info:
            raise BackupServiceError("下载凭证无效或已过期")
        if time.time() > float(info.get("exp", 0)):
            self.download_tickets.pop(key, None)
            raise BackupServiceError("下载凭证已过期，请重试")

        download = self.prepare_download(filename=filename)
        expected = str(info.get("filename") or "")
        if download.filename != expected:
            raise BackupServiceError("下载凭证与文件不匹配")
        return download

    def delete_backup(self, data: object) -> tuple[dict | None, str | None]:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少参数 filename",
        )
        file_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(file_path):
            raise BackupServiceError("备份文件不存在")

        os.remove(file_path)
        return None, "删除备份成功"

    def rename_backup(self, data: object) -> dict:
        payload = self._payload(data)
        filename = self._validate_backup_filename(
            payload.get("filename"),
            missing="缺少参数 filename",
        )
        new_name = payload.get("new_name")
        if not new_name:
            raise BackupServiceError("缺少参数 new_name")

        new_name = secure_filename(new_name)
        if new_name.endswith(".zip"):
            new_name = new_name[:-4]
        if not new_name or new_name.replace("_", "") == "":
            raise BackupServiceError("新文件名无效")

        new_filename = f"{new_name}.zip"
        old_path = os.path.join(self.backup_dir, filename)
        if not os.path.exists(old_path):
            raise BackupServiceError("备份文件不存在")

        new_path = os.path.join(self.backup_dir, new_filename)
        if os.path.exists(new_path):
            raise BackupServiceError(f"文件名 '{new_filename}' 已存在")

        os.rename(old_path, new_path)
        logger.info(f"备份文件重命名: {filename} -> {new_filename}")
        return {
            "old_filename": filename,
            "new_filename": new_filename,
        }
