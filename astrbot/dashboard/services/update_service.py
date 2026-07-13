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
    """兼容旧调用：不再从官方源下载面板。

    实际 WebUI 同步由 AstrBotUpdator 从 landamao/ldm_AstrBot 源码包完成。
    """
    logger.info(
        "跳过官方 download_dashboard；WebUI 将从 ldm_AstrBot 源码包同步。"
    )
    return None


async def call_extract_dashboard(*args, **kwargs):
    """兼容旧调用：解压逻辑已并入 apply_update_package。"""
    logger.info("跳过官方 extract_dashboard；WebUI 解压由 ldm 更新器处理。")
    return None


async def call_get_dashboard_version(*args, **kwargs):
    return await get_dashboard_version(*args, **kwargs)


async def call_pip_install(*args, **kwargs):
    """保留依赖安装能力，供核心更新流程使用。"""
    return await pip_installer.install(*args, **kwargs)


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
            try:
                update_result = await self.astrbot_updator.check_update(
                    None, None, False
                )
            except Exception as exc:
                # 限流/网络问题时不把整个检查接口打成 error，方便前端继续工作
                logger.warning(
                    f"检查更新失败: {exc!s} (不影响除项目更新外的正常使用)"
                )
                return UpdateServiceResult(
                    status="success",
                    message=(
                        f"暂时无法检查更新：{exc}。"
                        "可稍后重试，或设置 GITHUB_TOKEN/LDM_GITHUB_TOKEN 提高限额。"
                    ),
                    data={
                        "version": f"v{VERSION}",
                        "has_new_version": False,
                        "dashboard_version": dashboard_version,
                        "dashboard_has_new_version": bool(
                            dashboard_version and dashboard_version != f"v{VERSION}"
                        ),
                        "update_source": "landamao/ldm_AstrBot",
                        "check_failed": True,
                    },
                )
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
                    "update_source": "landamao/ldm_AstrBot",
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
        """从 landamao/ldm_AstrBot 更新核心源码与 WebUI。"""
        if is_desktop_managed_backend():
            raise UpdateServiceError(
                DESKTOP_MANAGED_RESTART_MESSAGE,
                code="desktop_managed",
            )

        payload = data if isinstance(data, dict) else {}
        version = payload.get("version", "")
        reboot = payload.get("reboot", True)
        progress_id = payload.get("progress_id") or uuid.uuid4().hex
        if version == "" or version == "latest":
            latest = True
            version = ""
        else:
            latest = False

        proxy: str | None = payload.get("proxy", None)
        if proxy:
            proxy = proxy.removesuffix("/")

        existing_task = self._update_tasks.get(progress_id)
        if existing_task and not existing_task.done():
            return UpdateServiceResult(
                data={"id": progress_id, "status": "running"},
                message="更新任务正在进行中。",
                headers=self.clear_site_data_headers,
            )

        self._init_update_progress(progress_id, version)
        task = asyncio.create_task(
            self._run_update_project(progress_id, version, latest, reboot, proxy)
        )
        self._update_tasks[progress_id] = task
        task.add_done_callback(lambda _task: self._update_tasks.pop(progress_id, None))
        return UpdateServiceResult(
            data={"id": progress_id, "status": "running"},
            message="已开始从 landamao/ldm_AstrBot 更新。",
            headers=self.clear_site_data_headers,
        )

    async def _run_update_project(
        self,
        progress_id: str,
        version: str,
        latest: bool,
        reboot: bool,
        proxy: str | None,
    ) -> None:
        """下载并应用 ldm_AstrBot 源码包（核心 + WebUI）。"""
        update_temp_parent = Path(get_astrbot_temp_path()) / "updates"
        try:
            if update_temp_parent.is_symlink():
                update_temp_parent.unlink()
            update_temp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            update_temp_parent.chmod(0o700)
            with tempfile.TemporaryDirectory(
                prefix="project-update-",
                dir=update_temp_parent,
            ) as update_temp_dir_name:
                update_temp_dir = Path(update_temp_dir_name)
                update_token = uuid.uuid4().hex
                core_zip_path = update_temp_dir / f"{update_token}-core.zip"

                # 1) 下载源码包（核心+内嵌 WebUI）
                self._set_update_stage(
                    progress_id,
                    "core",
                    "running",
                    "正在从 landamao/ldm_AstrBot 下载更新包...",
                    5,
                )
                core_zip_path = Path(
                    await self.astrbot_updator.download_update_package(
                        latest=latest,
                        version=version,
                        proxy=proxy or "",
                        path=core_zip_path,
                        progress_callback=self._make_progress_callback(
                            progress_id,
                            "core",
                            5,
                            70,
                        ),
                    )
                )
                self._set_update_stage(
                    progress_id,
                    "core",
                    "done",
                    "更新包下载完成。",
                    75,
                )

                # 2) 校验
                self._set_update_stage(
                    progress_id,
                    "verify",
                    "running",
                    "正在校验更新包...",
                    76,
                )

                def _verify_update_package() -> None:
                    with zipfile.ZipFile(core_zip_path, "r") as archive:
                        corrupt_member = archive.testzip()
                    if corrupt_member:
                        raise UpdateServiceError(f"更新包校验失败: {corrupt_member}")

                await asyncio.to_thread(_verify_update_package)
                self._set_update_stage(
                    progress_id,
                    "verify",
                    "done",
                    "更新包校验完成。",
                    80,
                )

                # 3) 应用核心 + WebUI
                self._set_update_stage(
                    progress_id,
                    "apply",
                    "running",
                    "正在应用核心源码与 WebUI...",
                    82,
                )
                await asyncio.to_thread(
                    self.astrbot_updator.apply_update_package,
                    core_zip_path,
                )
                self._set_update_stage(
                    progress_id,
                    "apply",
                    "done",
                    "源码与 WebUI 应用完成。",
                    90,
                )
                # 前端仍展示 dashboard 阶段时给完成态，避免一直 pending
                self._set_update_stage(
                    progress_id,
                    "dashboard",
                    "done",
                    "WebUI 已从同一更新包同步。",
                    90,
                )

                # 4) 依赖
                self._set_update_stage(
                    progress_id,
                    "dependencies",
                    "running",
                    "正在更新依赖...",
                    92,
                )
                logger.info("更新依赖中...")
                try:
                    await self.pip_install(requirements_path="requirements.txt")
                except Exception as exc:
                    logger.error(f"更新依赖失败: {exc}")
                self._set_update_stage(
                    progress_id,
                    "dependencies",
                    "done",
                    "依赖更新完成。",
                    96,
                )

                if reboot:
                    self._set_update_stage(
                        progress_id,
                        "restart",
                        "running",
                        "更新成功，正在准备重启...",
                        98,
                    )
                    await self.core_lifecycle.restart()
                    message = "更新成功，ldm 将在 2 秒内全量重启以应用新的代码。"
                else:
                    message = "更新成功，ldm 将在下次启动时应用新的代码。"

                self.update_progress[progress_id].update(
                    {
                        "status": "success",
                        "stage": "done",
                        "message": message,
                        "overall_percent": 100,
                    },
                )
                logger.info(message)
        except asyncio.CancelledError:
            self.update_progress[progress_id].update(
                {
                    "status": "error",
                    "message": "更新任务已取消。",
                },
            )
            logger.warning(f"Update task was cancelled: {progress_id}")
            raise
        except Exception as exc:
            self.update_progress[progress_id].update(
                {
                    "status": "error",
                    "message": "更新失败，请查看服务端日志。",
                },
            )
            logger.error(f"/api/update_project: {traceback.format_exc()}")
            logger.debug(f"Update task failed: {exc!s}")

    async def update_dashboard(self) -> UpdateServiceResult:
        """仅从 landamao/ldm_AstrBot 同步 WebUI 到 data/dist。"""
        try:
            applied = await self.astrbot_updator.apply_webui_only_from_package(
                latest=True,
                version=None,
                proxy="",
            )
            if not applied:
                raise UpdateServiceError(
                    "ldm_AstrBot 更新包中未找到可用的 dashboard/dist 或 data/dist。"
                )
            return UpdateServiceResult(
                message="WebUI 已从 landamao/ldm_AstrBot 同步。刷新页面即可应用。",
                headers=self.clear_site_data_headers,
            )
        except UpdateServiceError:
            raise
        except Exception as exc:
            logger.error(f"/api/update_dashboard: {traceback.format_exc()}")
            raise UpdateServiceError(exc.__str__()) from exc

    async def install_pip_package(self, data: object) -> UpdateServiceResult:
        """禁用 WebUI 任意 pip 安装；核心更新时的 requirements 安装走独立路径。"""
        logger.warning("已禁用 WebUI 任意 pip 安装/更新，跳过 install_pip_package 调用。")
        return UpdateServiceResult(
            message="已禁用 WebUI 任意 pip 安装；请通过项目更新流程安装 requirements。",
            headers={},
        )

    def _init_update_progress(self, progress_id: str, version: str) -> None:
        self.update_progress[progress_id] = {
            "id": progress_id,
            "status": "running",
            "stage": "preparing",
            "version": version or "latest",
            "message": "正在准备从 landamao/ldm_AstrBot 更新...",
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
