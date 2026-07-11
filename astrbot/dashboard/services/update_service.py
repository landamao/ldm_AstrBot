from __future__ import annotations

import asyncio
import inspect
import tempfile
import traceback
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.core import DEMO_MODE as _DEMO_MODE
from astrbot.core import logger
from astrbot.core import pip_installer as _pip_installer
from astrbot.core.config.default import VERSION
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.desktop_runtime import (
    DESKTOP_MANAGED_RESTART_MESSAGE,
    is_desktop_managed_backend,
)
from astrbot.core.updator import AstrBotUpdator
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.io import (
    download_dashboard as _download_dashboard,
)
from astrbot.core.utils.io import (
    extract_dashboard as _extract_dashboard,
)
from astrbot.core.utils.io import (
    get_dashboard_version as _get_dashboard_version,
)

DEMO_MODE = _DEMO_MODE
pip_installer = _pip_installer
download_dashboard = _download_dashboard
extract_dashboard = _extract_dashboard
get_dashboard_version = _get_dashboard_version


async def call_download_dashboard(*args, **kwargs):
    logger.warning(
        "已禁用 WebUI 自动下载/覆盖，跳过 download_dashboard 调用。"
    )
    return None


async def call_extract_dashboard(*args, **kwargs):
    if inspect.iscoroutinefunction(extract_dashboard):
        return await extract_dashboard(*args, **kwargs)
    result = await asyncio.to_thread(extract_dashboard, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def call_get_dashboard_version(*args, **kwargs):
    return await get_dashboard_version(*args, **kwargs)


async def call_pip_install(*args, **kwargs):
    logger.warning("已禁用 WebUI pip 安装/更新，跳过 pip install 调用。")
    return None


@dataclass
class UpdateServiceResult:
    data: Any = None
    message: str | None = None
    status: str = "ok"
    headers: dict | None = None


class UpdateServiceError(Exception):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class UpdateService:
    def __init__(
        self,
        astrbot_updator: AstrBotUpdator,
        core_lifecycle: AstrBotCoreLifecycle,
        *,
        download_dashboard_func: Callable[..., Awaitable[Any]],
        extract_dashboard_func: Callable[..., Any],
        get_dashboard_version_func: Callable[..., Awaitable[str | None]],
        pip_install_func: Callable[..., Awaitable[Any]],
        demo_mode: bool,
        clear_site_data_headers: dict,
    ) -> None:
        self.astrbot_updator = astrbot_updator
        self.core_lifecycle = core_lifecycle
        self.download_dashboard = download_dashboard_func
        self.extract_dashboard = extract_dashboard_func
        self.get_dashboard_version = get_dashboard_version_func
        self.pip_install = pip_install_func
        self.demo_mode = demo_mode
        self.clear_site_data_headers = clear_site_data_headers
        self.update_progress: dict[str, dict] = {}
        self._update_tasks: dict[str, asyncio.Task] = {}

    def get_update_progress(self, progress_id: str) -> UpdateServiceResult:
        if not progress_id:
            raise UpdateServiceError("缺少参数 id。")
        progress = self.update_progress.get(progress_id)
        if not progress:
            return UpdateServiceResult(
                data={"id": progress_id, "status": "idle"},
                message="没有正在进行的更新。",
            )
        return UpdateServiceResult(data=progress)

    async def check_update(self, update_type: str | None) -> UpdateServiceResult:
        try:
            dashboard_version = await self.get_dashboard_version()
            if update_type == "dashboard":
                return UpdateServiceResult(
                    data={
                        "has_new_version": dashboard_version != f"v{VERSION}",
                        "current_version": dashboard_version,
                    }
                )
            update_result = await self.astrbot_updator.check_update(None, None, False)
            return UpdateServiceResult(
                status="success",
                message=str(update_result)
                if update_result is not None
                else "已经是最新版本了。",
                data={
                    "version": f"v{VERSION}",
                    "has_new_version": update_result is not None,
                    "dashboard_version": dashboard_version,
                    "dashboard_has_new_version": bool(
                        dashboard_version and dashboard_version != f"v{VERSION}"
                    ),
                },
            )
        except Exception as exc:
            logger.warning(f"检查更新失败: {exc!s} (不影响除项目更新外的正常使用)")
            raise UpdateServiceError(exc.__str__()) from exc

    async def get_releases(self) -> UpdateServiceResult:
        try:
            releases = await self.astrbot_updator.get_releases()
            return UpdateServiceResult(data=releases)
        except Exception as exc:
            logger.error(f"/api/update/releases: {traceback.format_exc()}")
            raise UpdateServiceError(exc.__str__()) from exc

    async def update_project(self, data: object) -> UpdateServiceResult:
        """禁用 AstrBot 核心源码自动更新。"""
        logger.warning(
            "已禁用 ldm 核心源码自动更新，跳过 update_project 调用。"
        )
        progress_id = "update-disabled"
        self.update_progress[progress_id] = {
            "id": progress_id,
            "status": "error",
            "stage": "blocked",
            "message": "已禁用 ldm 核心源码自动更新；不会下载、解压、覆盖源码、安装依赖或重启。",
            "overall_percent": 100,
            "stages": {},
        }
        return UpdateServiceResult(
            data={"id": progress_id, "status": "blocked"},
            message="已禁用 ldm 核心源码自动更新；不会修改源码或 WebUI。",
            headers={},
        )

    async def _run_update_project(
        self,
        progress_id: str,
        version: str,
        latest: bool,
        reboot: bool,
        proxy: str | None,
    ) -> None:
        """核心源码自动更新已禁用；保留方法仅兼容旧任务调用。"""
        logger.warning(
            "已禁用 ldm 核心源码自动更新，跳过 _run_update_project 调用。"
        )
        self.update_progress[progress_id] = {
            "id": progress_id,
            "status": "error",
            "stage": "blocked",
            "message": "已禁用 ldm 核心源码自动更新；不会修改源码或 WebUI。",
            "overall_percent": 100,
            "stages": {},
        }
        return None

    async def update_dashboard(self) -> UpdateServiceResult:
        logger.warning(
            "已禁用 WebUI 自动下载/覆盖。当前 data/dist 自定义管理面板将保持不变。"
        )
        return UpdateServiceResult(
            message="已禁用 WebUI 自动下载/覆盖。请手动构建 dashboard 并复制到 data/dist。",
            headers={},
        )

    async def install_pip_package(self, data: object) -> UpdateServiceResult:
        """禁用 WebUI 触发的 pip 安装，避免更新类操作修改运行环境。"""
        logger.warning("已禁用 WebUI pip 安装/更新，跳过 install_pip_package 调用。")
        return UpdateServiceResult(
            message="已禁用 WebUI pip 安装/更新；不会修改运行环境或源码。",
            headers={},
        )

    def _init_update_progress(self, progress_id: str, version: str) -> None:
        self.update_progress[progress_id] = {
            "id": progress_id,
            "status": "running",
            "stage": "preparing",
            "version": version or "latest",
            "message": "正在准备更新...",
            "overall_percent": 0,
            "stages": {
                "dashboard": self._empty_stage("pending"),
                "core": self._empty_stage("pending"),
            },
        }

    @staticmethod
    def _empty_stage(status: str = "pending") -> dict:
        return {
            "status": status,
            "downloaded": 0,
            "total": 0,
            "percent": 0,
            "speed": 0,
        }

    def _set_update_stage(
        self,
        progress_id: str,
        stage: str,
        status: str,
        message: str,
        overall_percent: int | None = None,
    ) -> None:
        progress = self.update_progress.get(progress_id)
        if not progress:
            return
        progress["stage"] = stage
        progress["message"] = message
        progress["stages"].setdefault(stage, self._empty_stage())
        progress["stages"][stage]["status"] = status
        if overall_percent is not None:
            progress["overall_percent"] = overall_percent

    @staticmethod
    def _normalize_percent(value) -> int:
        try:
            percent = float(value or 0)
        except (TypeError, ValueError):
            return 0
        if percent <= 1:
            percent *= 100
        return max(0, min(100, int(percent)))

    def _make_progress_callback(
        self,
        progress_id: str,
        stage: str,
        stage_start: int,
        stage_weight: int,
    ):
        def _callback(payload: dict) -> None:
            progress = self.update_progress.get(progress_id)
            if not progress:
                return
            stage_percent = self._normalize_percent(payload.get("percent"))
            progress["stage"] = stage
            progress["stages"][stage] = {
                "status": "running" if stage_percent < 100 else "done",
                "downloaded": payload.get("downloaded", 0),
                "total": payload.get("total", 0),
                "percent": stage_percent,
                "speed": payload.get("speed", 0),
            }
            progress["overall_percent"] = min(
                99,
                stage_start + int(stage_percent * stage_weight / 100),
            )

        return _callback
