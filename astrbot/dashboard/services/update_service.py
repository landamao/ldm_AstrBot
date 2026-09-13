from __future__ import annotations

import asyncio
import inspect
import os
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
from astrbot.core.utils.update_rollback import (
    clear_dir_contents,
    get_rollback_dir,
    list_backups,
    rollback,
)
from astrbot.core.utils.github_proxy import (
    log_github_proxy_usage,
    resolve_github_proxy,
    normalize_ldm_mirror,
)
from astrbot.core.utils.io import (
    get_dashboard_version as _get_dashboard_version,
)

DEMO_MODE = _DEMO_MODE
pip_installer = _pip_installer
get_dashboard_version = _get_dashboard_version


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
        get_dashboard_version_func: Callable[..., Awaitable[str | None]],
        pip_install_func: Callable[..., Awaitable[Any]],
        demo_mode: bool,
        clear_site_data_headers: dict,
    ) -> None:
        self.astrbot_updator = astrbot_updator
        self.core_lifecycle = core_lifecycle
        self.get_dashboard_version = get_dashboard_version_func
        self.pip_install = pip_install_func
        self.demo_mode = demo_mode
        self.clear_site_data_headers = clear_site_data_headers
        self.update_progress: dict[str, dict] = {}
        self._update_tasks: dict[str, asyncio.Task] = {}
        # 核心更新互斥标记：更新后台任务执行期间为 True，
        # 防止前端状态复位后重复请求产生多个并发更新任务（多标签页/双击竞态兜底）
        self._core_update_running = False

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

    async def check_update(
        self,
        update_type: str | None,
        force_refresh: bool = False,
        mirror_url: str = "",
    ) -> UpdateServiceResult:
        try:
            dashboard_version = await self.get_dashboard_version()
            try:
                update_result = await self.astrbot_updator.check_update(
                    None,
                    None,
                    False,
                    force_refresh=force_refresh,
                    mirror_url=mirror_url,
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
                        "可稍后重试，或设置 GITHUB_TOKEN/LDMBOT_GITHUB_TOKEN 提高限额。"
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

    async def get_releases(
        self, force_refresh: bool = False, mirror_url: str = ""
    ) -> UpdateServiceResult:
        try:
            releases = await self.astrbot_updator.get_releases(
                force_refresh=force_refresh,
                mirror_url=mirror_url,
            )
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

        # ldm 镜像服务器地址（前端硬编码传入，不需要服务端配置）
        mirror_url = normalize_ldm_mirror(payload.get("mirror_url", ""))

        proxy: str | None = payload.get("proxy", None)
        explicit = (proxy or "").strip() if proxy is not None else ""
        # 镜像模式忽略 proxy（直连国内服务器不需要加速）
        if mirror_url:
            proxy = None
            logger.info(
                f"使用 ldm 镜像服务器更新: {mirror_url}（忽略 GitHub 加速）"
            )
        else:
            proxy = resolve_github_proxy(
                proxy,
                getattr(self.core_lifecycle, "astrbot_config", None),
            ) or None
            log_github_proxy_usage(
                proxy,
                action="更新本体",
                target=version or "latest",
                source="请求参数" if explicit else ("服务端配置" if proxy else "无"),
            )

        # 全局互斥：已有核心更新在后台执行时拒绝新任务（progress_id 不同也拦截），
        # 报错给前端弹「已有更新任务正在进行」toast
        if self._core_update_running:
            running = [
                progress
                for progress in self.update_progress.values()
                if progress.get("status") == "running"
            ]
            target_desc = (
                running[0].get("version") or "latest"
                if running
                else "未知版本"
            )
            logger.warning(
                f"拒绝重复更新请求（目标: {version or 'latest'}），"
                f"已有更新任务正在执行（目标: {target_desc}）"
            )
            raise UpdateServiceError("已有更新任务正在进行中")

        existing_task = self._update_tasks.get(progress_id)
        if existing_task and not existing_task.done():
            return UpdateServiceResult(
                data={"id": progress_id, "status": "running"},
                message="更新任务正在进行中。",
                headers=self.clear_site_data_headers,
            )

        self._init_update_progress(progress_id, version)
        self._core_update_running = True
        task = asyncio.create_task(
            self._run_update_project(
                progress_id, version, latest, reboot, proxy, mirror_url
            )
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
        mirror_url: str = "",
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
                        mirror_url=mirror_url,
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
        finally:
            self._core_update_running = False

    async def install_pip_package(self, data: object) -> UpdateServiceResult:
        """禁用 WebUI 任意 pip 安装；核心更新时的 requirements 安装走独立路径。"""
        logger.warning("已禁用 WebUI 任意 pip 安装/更新，跳过 install_pip_package 调用。")
        return UpdateServiceResult(
            message="已禁用 WebUI 任意 pip 安装；请通过项目更新流程安装 requirements。",
            headers={},
        )

    async def list_rollback_backups(self) -> UpdateServiceResult:
        """列出回滚备份 zip（版本号、大小、备份时间，最新在前）。"""
        backups = await asyncio.to_thread(list_backups, str(get_astrbot_data_path()))
        items = []
        for zip_path in backups:
            stat = zip_path.stat()
            items.append(
                {
                    "version": zip_path.stem.removeprefix("ldmbot_"),
                    "filename": zip_path.name,
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                }
            )
        return UpdateServiceResult(
            data={
                "backups": items,
                "rollback_dir": str(
                    get_rollback_dir(str(get_astrbot_data_path()))
                ),
            }
        )

    async def rollback_to_version(self, data: object) -> UpdateServiceResult:
        """回滚到指定版本备份（默认最近一次），成功后自动全量重启。"""
        if is_desktop_managed_backend():
            raise UpdateServiceError(
                DESKTOP_MANAGED_RESTART_MESSAGE,
                code="desktop_managed",
            )
        if self._core_update_running:
            raise UpdateServiceError("已有更新任务正在进行中，请稍后再试。")
        if self.demo_mode:
            raise UpdateServiceError(DEMO_MODE)

        payload = data if isinstance(data, dict) else {}
        version = str(payload.get("version", "")).strip()

        logger.info(f"WebUI 发起版本回滚: {version or '最近一次备份'}")
        ok = await asyncio.to_thread(
            rollback,
            version or None,
            None,  # project_root 用模块默认（源码根）
            self.astrbot_updator._resolve_webui_dir(),
            str(get_astrbot_data_path()),
        )
        if not ok:
            raise UpdateServiceError(
                "回滚失败，请查看服务端日志（常见原因：找不到备份或备份文件损坏）。"
            )

        message = "回滚成功，ldm 将在 2 秒内全量重启以应用旧版本代码。"
        logger.info(f"版本回滚完成: {version or '最近一次备份'}，准备重启。")
        self._schedule_restart()
        return UpdateServiceResult(
            message=message,
            headers=self.clear_site_data_headers,
        )

    async def clear_rollback_backups(self, data: object) -> UpdateServiceResult:
        """清理回滚备份目录：只删 ldmbot_*.zip 备份，保留目录与说明.txt。"""
        if is_desktop_managed_backend():
            raise UpdateServiceError(
                DESKTOP_MANAGED_RESTART_MESSAGE,
                code="desktop_managed",
            )
        payload = data if isinstance(data, dict) else {}
        only_version = str(payload.get("version", "")).strip()

        rollback_dir = get_rollback_dir(str(get_astrbot_data_path()))
        if only_version:
            target = rollback_dir / f"ldmbot_{only_version}.zip"
            if not target.is_file():
                raise UpdateServiceError(f"找不到版本 {only_version} 的回滚备份。")
            await asyncio.to_thread(target.unlink)
            logger.info(f"已删除回滚备份: {target.name}")
            return UpdateServiceResult(message=f"已删除版本 {only_version} 的回滚备份。")

        # 全部清理：逐个删 zip（不整目录清空，保留说明.txt）
        backups = await asyncio.to_thread(list_backups, str(get_astrbot_data_path()))
        removed = 0
        for zip_path in backups:
            try:
                await asyncio.to_thread(zip_path.unlink)
                removed += 1
            except OSError as exc:
                logger.warning(f"删除回滚备份 {zip_path.name} 失败: {exc}")

        logger.info(f"已清理回滚备份 {removed} 个。")
        return UpdateServiceResult(message=f"已清理 {removed} 个回滚备份。")

    # 上传压缩包校验所需的一级文件/目录
    _上传校验_根文件 = {"main.py"}
    _上传校验_astrbot子项 = {
        "__init__.py", "api", "builtin_stars", "cli", "core", "dashboard", "utils",
    }
    _上传校验_webui文件 = {"index.html", "version"}
    _上传校验_webui目录 = {"assets"}

    async def update_from_upload(
        self,
        file_bytes: bytes,
        filename: str,
        reboot: bool = True,
    ) -> UpdateServiceResult:
        """从用户上传的 zip 压缩包应用更新（核心 + WebUI）。

        用户通过 WebUI 手动上传从 ldm 官方下载的更新包，
        后端校验 zip 完整性后复用 apply_update_package 应用。
        """
        if is_desktop_managed_backend():
            raise UpdateServiceError(
                DESKTOP_MANAGED_RESTART_MESSAGE,
                code="desktop_managed",
            )

        if not file_bytes:
            raise UpdateServiceError("上传文件为空。")

        if not filename.lower().endswith(".zip"):
            raise UpdateServiceError("请上传 .zip 格式的压缩包。")

        update_temp_parent = Path(get_astrbot_temp_path()) / "updates"
        try:
            if update_temp_parent.is_symlink():
                update_temp_parent.unlink()
            update_temp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            update_temp_parent.chmod(0o700)
        except Exception as exc:
            raise UpdateServiceError(f"创建临时目录失败: {exc}") from exc

        # 持久目录：校验通过后仍需保留文件供 confirm 步骤使用
        persist_dir = update_temp_parent / f"upload-{uuid.uuid4().hex}"
        persist_dir.mkdir(mode=0o700, exist_ok=True)
        token = uuid.uuid4().hex
        zip_path = persist_dir / f"{token}-{filename}"

        await asyncio.to_thread(lambda: zip_path.write_bytes(file_bytes))

        # zip 完整性校验
        def _verify_zip() -> None:
            if not zipfile.is_zipfile(zip_path):
                raise UpdateServiceError("文件不是有效的 zip 压缩包。")
            with zipfile.ZipFile(zip_path, "r") as archive:
                corrupt = archive.testzip()
            if corrupt:
                raise UpdateServiceError(f"压缩包校验失败: {corrupt}")

        await asyncio.to_thread(_verify_zip)

        # 文件结构完整性校验 + 版本提取
        missing_items: list[str] = []
        found_items: list[str] = []
        core_version: str = ""
        dashboard_version: str = ""

        def _check_structure() -> None:
            nonlocal core_version, dashboard_version, found_items
            with zipfile.ZipFile(zip_path, "r") as archive:
                entries = archive.namelist()
                normalized = [os.path.normpath(e).replace("\\", "/") for e in entries]

                # 判断根目录：所有路径的公共前缀
                root = self.astrbot_updator._resolve_archive_root_dir(entries)
                root_norm = os.path.normpath(root).replace("\\", "/") if root else ""

                def _full(path_parts: str) -> str:
                    return f"{root_norm}/{path_parts}" if root_norm else path_parts

                def _has(path_parts: str) -> bool:
                    full = _full(path_parts)
                    if full in normalized:
                        return True
                    prefix = full + "/"
                    return any(e.startswith(prefix) for e in normalized)

                # 根文件
                for f in self._上传校验_根文件:
                    if _has(f):
                        found_items.append(f"根文件 {f}")
                    else:
                        missing_items.append(f"根文件 {f}")

                # astrbot 子项
                for item in self._上传校验_astrbot子项:
                    label = f"astrbot/{item}"
                    if _has(label):
                        found_items.append(label)
                    else:
                        missing_items.append(label)

                # WebUI dist（data/dist 或 dashboard/dist）
                webui_found = False
                for dist_path in ("data/dist", "dashboard/dist"):
                    dist_prefix = _full(f"{dist_path}/")
                    has_dist = any(e.startswith(dist_prefix) for e in normalized)
                    if not has_dist:
                        continue
                    # 检查 dist 内的关键文件/目录
                    dist_items_ok = True
                    for f in self._上传校验_webui文件:
                        check = _full(f"{dist_path}/{f}")
                        if check not in normalized:
                            dist_items_ok = False
                            missing_items.append(f"{dist_path}/{f}")
                        else:
                            found_items.append(f"{dist_path}/{f}")
                    for d in self._上传校验_webui目录:
                        check_prefix = _full(f"{dist_path}/{d}/")
                        if not any(e.startswith(check_prefix) for e in normalized):
                            dist_items_ok = False
                            missing_items.append(f"{dist_path}/{d}")
                        else:
                            found_items.append(f"{dist_path}/{d}")
                    if dist_items_ok:
                        webui_found = True
                        found_items.append(f"{dist_path}/ (完整)")
                        # 读取 WebUI 版本
                        for vf in ("version", "assets/version"):
                            vf_path = _full(f"{dist_path}/{vf}")
                            if vf_path in normalized:
                                try:
                                    raw = archive.read(vf_path).decode("utf-8").strip()
                                    if raw:
                                        dashboard_version = raw
                                        break
                                except Exception:
                                    pass
                        break

                if not webui_found and not missing_items:
                    missing_items.append("data/dist 或 dashboard/dist (含 index.html/version/assets)")
                elif not webui_found:
                    missing_items.append("data/dist 或 dashboard/dist (含 index.html/version/assets)")

                # 读取程序代码版本：astrbot/__init__.py 里的 __version__
                init_path = _full("astrbot/__init__.py")
                if init_path in normalized:
                    try:
                        content = archive.read(init_path).decode("utf-8")
                        import re
                        m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
                        if m:
                            core_version = m.group(1)
                    except Exception:
                        pass

        await asyncio.to_thread(_check_structure)

        if missing_items:
            return UpdateServiceResult(
                status="warning",
                message="该更新包内文件不完整，缺失部分文件，如从 ldm 官方下载的压缩包，一定是完整的，该压缩包可能不是 ldm 官方的，或已损坏或已被修改。",
                data={
                    "missing": missing_items,
                    "found": found_items,
                    "core_version": core_version or "未知",
                    "dashboard_version": dashboard_version or "未知",
                    "zip_path": str(zip_path),
                    "persist_dir": str(persist_dir),
                },
            )

        return UpdateServiceResult(
            status="ok",
            message="校验通过，请确认更新包安全后继续。",
            data={
                "found": found_items,
                "core_version": core_version or "未知",
                "dashboard_version": dashboard_version or "未知",
                "zip_path": str(zip_path),
                "persist_dir": str(persist_dir),
            },
        )

    async def apply_uploaded_package(
        self,
        zip_path_str: str,
        reboot: bool = True,
    ) -> UpdateServiceResult:
        """应用已校验通过的上传压缩包。"""
        if is_desktop_managed_backend():
            raise UpdateServiceError(
                DESKTOP_MANAGED_RESTART_MESSAGE,
                code="desktop_managed",
            )

        zip_path = Path(zip_path_str)
        if not zip_path.is_file():
            raise UpdateServiceError("上传的压缩包文件已过期，请重新上传。")

        # 全局互斥：核心更新进行中不允许再应用上传包（同一份源码树，并发应用会互相覆盖）
        if self._core_update_running:
            raise UpdateServiceError(
                "已有核心更新任务正在进行中"
            )

        self._core_update_running = True
        try:
            # 应用更新包
            await asyncio.to_thread(
                self.astrbot_updator.apply_update_package,
                zip_path,
            )

            # 更新依赖
            logger.info("正在更新依赖（来自上传压缩包）...")
            try:
                await self.pip_install(requirements_path="requirements.txt")
            except Exception as exc:
                logger.error(f"更新依赖失败: {exc}")

            # 清理临时目录
            try:
                zip_path.parent.rmdir()
            except Exception:
                pass
        finally:
            self._core_update_running = False

        if reboot:
            message = "上传压缩包更新成功，ldm 将在重启后应用新的代码。"
            self._schedule_restart()
        else:
            message = "上传压缩包更新成功，ldm 将在下次启动时应用新的代码。"

        result = UpdateServiceResult(
            message=message,
            headers=self.clear_site_data_headers,
        )
        if reboot:
            result.data = {"reboot": True}
        return result

    def _schedule_restart(self) -> None:
        """在后台安排重启，避免 HTTP 请求阻塞。"""
        async def _do_restart():
            await self.core_lifecycle.restart()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_restart())
        except RuntimeError:
            pass

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
