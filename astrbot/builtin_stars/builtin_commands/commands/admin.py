import asyncio
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.github_proxy import normalize_ldm_mirror
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词


class AdminCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def op(self, event: AstrMessageEvent, admin_id: str = "") -> None:
        """授权管理员。op <admin_id>"""
        if not admin_id:
            event.set_result(
                MessageEventResult().message(
                    f"使用方法: {获取第一个唤醒词()}op <id> 授权管理员；{获取第一个唤醒词()}deop <id> 取消管理员。可通过 {获取第一个唤醒词()}sid 获取 ID。",
                ),
            )
            return
        self.context.get_config()["admins_id"].append(str(admin_id))
        self.context.get_config().save_config()
        event.set_result(MessageEventResult().message("授权成功。"))

    async def deop(self, event: AstrMessageEvent, admin_id: str = "") -> None:
        """取消授权管理员。deop <admin_id>"""
        if not admin_id:
            event.set_result(
                MessageEventResult().message(
                    f"使用方法: {获取第一个唤醒词()}deop <id> 取消管理员。可通过 {获取第一个唤醒词()}sid 获取 ID。",
                ),
            )
            return
        try:
            self.context.get_config()["admins_id"].remove(str(admin_id))
            self.context.get_config().save_config()
            event.set_result(MessageEventResult().message("取消授权成功。"))
        except ValueError:
            event.set_result(
                MessageEventResult().message("此用户 ID 不在管理员名单内。"),
            )

    async def wl(self, event: AstrMessageEvent, sid: str = "") -> None:
        """添加白名单。wl <sid>"""
        if not sid:
            event.set_result(
                MessageEventResult().message(
                    f"使用方法: {获取第一个唤醒词()}wl <id> 添加白名单；{获取第一个唤醒词()}dwl <id> 删除白名单。可通过 {获取第一个唤醒词()}sid 获取 ID。",
                ),
            )
            return
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        cfg["platform_settings"]["id_whitelist"].append(str(sid))
        cfg.save_config()
        event.set_result(MessageEventResult().message("添加白名单成功。"))

    async def dwl(self, event: AstrMessageEvent, sid: str = "") -> None:
        """删除白名单。dwl <sid>"""
        if not sid:
            event.set_result(
                MessageEventResult().message(
                    f"使用方法: {获取第一个唤醒词()}dwl <id> 删除白名单。可通过 {获取第一个唤醒词()}sid 获取 ID。",
                ),
            )
            return
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
            cfg["platform_settings"]["id_whitelist"].remove(str(sid))
            cfg.save_config()
            event.set_result(MessageEventResult().message("删除白名单成功。"))
        except ValueError:
            event.set_result(MessageEventResult().message("此 SID 不在白名单内。"))

    async def restart(self, event: AstrMessageEvent) -> None:
        """重启 ldm 框架"""
        from astrbot.core.desktop_runtime import (
            DESKTOP_MANAGED_RESTART_MESSAGE,
            is_desktop_managed_backend,
        )

        if is_desktop_managed_backend():
            event.set_result(
                MessageEventResult().message(DESKTOP_MANAGED_RESTART_MESSAGE),
            )
            return

        core_lifecycle = self.context._core_lifecycle

        await self._write_restart_record(event)

        await event.send(
            MessageChain().message("正在重启 ldm 框架，请稍候...")
        )

        # 调用 core_lifecycle.restart()：先终止各管理器并通知 WebUI 关闭，
        # 再由 _reboot() 起新进程。与 WebUI 重启按钮走同一条链路。
        await core_lifecycle.restart()

    async def _write_restart_record(self, event: AstrMessageEvent) -> None:
        """写入重启记录，供启动报告插件读取（restart / up_ldm 共用）。"""
        record = {
            "restart_time": datetime.now().isoformat(),
            "umo": event.unified_msg_origin,
        }
        group_id = event.get_group_id()
        if group_id:
            record["group_id"] = int(group_id)
        try:
            record_dir = os.path.join(
                get_astrbot_data_path(), "plugin_data", "startup-report",
            )
            os.makedirs(record_dir, exist_ok=True)
            record_path = os.path.join(record_dir, "restart_record.json")
            with open(record_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False)
            logger.info(f"已写入重启记录: {record_path}")
        except Exception as e:
            logger.warning(f"写入重启记录失败: {e}")

    async def up_ldm(self, event: AstrMessageEvent) -> None:
        """更新 ldm 到最新 Release 并自动重启。

        消息节奏：正在更新 → 更新成功 v旧 → v新，正在重启（或无更新/失败提示）。
        重启复用 restart() 的带写入重启记录链路（core_lifecycle.restart()）。
        """
        import importlib

        from astrbot.core import pip_installer
        from astrbot.core.config.default import VERSION
        from astrbot.core.updator import AstrBotUpdator

        old_version = f"v{VERSION}"

        await event.send(
            MessageChain().message("正在更新ldmbot...")
        )

        updator = AstrBotUpdator()
        try:
            # 1) 检查更新（Release-only，强制绕过短缓存）
            release = await updator.check_update(
                None, None, False, force_refresh=True
            )
            if release is None:
                await event.send(
                    MessageChain().message(
                        f"当前已是最新版本 {old_version}，无需更新。"
                    )
                )
                return

            # 2) 下载更新包（ldm 镜像源，与 WebUI 更新默认下载源一致）
            update_temp = Path(get_astrbot_temp_path()) / "updates"
            update_temp.mkdir(parents=True, exist_ok=True)
            zip_path = update_temp / f"upldm-{int(time.time())}.zip"
            await updator.download_update_package(
                latest=True,
                version=None,
                proxy="",
                path=zip_path,
                mirror_url=normalize_ldm_mirror("ldm_mirror"),
            )

            # 3) 应用更新包（核心 + WebUI，内部含更新前备份）
            await asyncio.to_thread(updator.apply_update_package, zip_path)

            # 4) 更新依赖（失败不阻断，与 WebUI 更新链路一致）
            try:
                await pip_installer.install(requirements_path="requirements.txt")
            except Exception as exc:
                logger.warning(f"更新依赖失败（继续重启）: {exc}")
        except Exception as exc:
            logger.error(f"upldm 更新失败: {traceback.format_exc()}")
            await event.send(
                MessageChain().message(f"更新ldmbot失败: {exc}")
            )
            return

        # 应用更新包是整进程覆盖，本进程内存里的 VERSION 还是启动时的旧值，
        # 必须从磁盘重读更新后的版本号。
        # 注意：importlib.import_module 会命中 sys.modules 缓存返回旧值，不可用；
        # 直接从运行目录的 astrbot/__init__.py 源文件解析 __version__
        new_version = release.version
        try:
            import re as _re

            init_file = Path(__file__).resolve().parents[4] / "astrbot" / "__init__.py"
            if not init_file.is_file():
                # 兜底：源码文件定位失败时按模块位置推算
                import astrbot as _pkg

                init_file = Path(_pkg.__file__).resolve()
            matched = _re.search(
                r"__version__\s*=\s*[\"']([^\"']+)[\"']",
                init_file.read_text(encoding="utf-8"),
            )
            if matched:
                new_version = f"v{matched.group(1)}"
        except Exception as exc:
            logger.warning(f"重读磁盘版本号失败，回退 Release tag: {exc}")
        await event.send(
            MessageChain().message(
                f"更新成功 {old_version} → {new_version}，正在重启"
            )
        )
        await self._write_restart_record(event)
        await self.context._core_lifecycle.restart()
