import os
from datetime import datetime

import yaml

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core import DEMO_MODE, logger
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import StarHandlerMetadata, star_handlers_registry
from astrbot.core.star.star_manager import PluginManager
from astrbot.core.star.updator import PLUGIN_METADATA_FILENAMES
from astrbot.core.utils.github_proxy import (
    get_configured_github_proxy,
    log_github_proxy_usage,
)


class PluginCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    @staticmethod
    def _plugin_not_found_message(plugin_name: str) -> str:
        """插件不存在时的统一提示。"""
        name = (plugin_name or "").strip() or "（空）"
        return f"插件名「{name}」不存在，使用'/plugin ls'查找插件名"

    def _github_proxy(self, *, action: str, target: str = "") -> str:
        """读取服务端配置的 GitHub 加速地址，并打使用日志。"""
        try:
            config = self.context.get_config()
        except Exception:
            config = None
        proxy = get_configured_github_proxy(config)
        return log_github_proxy_usage(
            proxy,
            action=action,
            target=target,
            source="服务端配置" if proxy else "无",
        )

    @staticmethod
    def build_group_help_message() -> str:
        """仅输入 /plugin 时展示的指令组帮助。"""
        return "\n".join(
            [
                "插件管理  /plugin",
                "",
                "用法：",
                "/plugin ls",
                "  查看已安装插件列表",
                "",
                "/plugin help <插件名>",
                "  查看指定插件帮助与指令",
                "",
                "/plugin on <插件名>",
                "  启用插件（管理员）",
                "",
                "/plugin off <插件名>",
                "  禁用插件（管理员）",
                "",
                "/plugin restart <插件名>",
                "  重启插件（管理员）",
                "",
                "/plugin update <插件名>",
                "  更新插件（管理员）",
                "",
                "/plugin get <插件仓库地址>",
                "  从仓库安装插件（管理员）",
                "",
                "示例：",
                "/plugin ls",
                "/plugin help ldm",
                "/plugin on ldm",
            ]
        )

    async def plugin_ls(self, event: AstrMessageEvent) -> None:
        """获取已经安装的插件列表。"""
        plugins = list(self.context.get_all_stars())
        if not plugins:
            event.set_result(
                MessageEventResult().message("没有加载任何插件。").use_t2i(False),
            )
            return

        # 启用在前、停用在后；同组内按名称排序
        def _sort_key(p) -> tuple:
            return 0 if p.activated else 1, (p.name or "").casefold()

        plugins_sorted = sorted(plugins, key=_sort_key)
        enabled = [p for p in plugins_sorted if p.activated]
        disabled = [p for p in plugins_sorted if not p.activated]

        def _format_plugin(plugin) -> list[str]:
            """按字段逐行输出，空值字段不显示。"""
            name = (plugin.name or "").strip()
            display = (getattr(plugin, "display_name", None) or "").strip()
            author = (plugin.author or "").strip()
            desc = (plugin.desc or plugin.short_desc or "").strip()

            lines: list[str] = []
            if name:
                lines.append(f"插件名：{name}")
            if display and display != name:
                lines.append(f"显示名：{display}")
            if author:
                lines.append(f"作者：{author}")
            if desc:
                lines.append(f"简介：{desc}")
            return lines

        def _format_group(title: str, items: list) -> list[str]:
            if not items:
                return []
            lines = [f"{title}（{len(items)}）", ""]
            for plugin in items:
                block = _format_plugin(plugin)
                if block:
                    lines.extend(block)
                    lines.append("")  # 插件之间空一行
            return lines

        parts: list[str] = [
            f"插件列表  共 {len(plugins)} 个（启用 {len(enabled)} / 停用 {len(disabled)}）",
            "",
        ]
        parts.extend(_format_group("✅ 已启用", enabled))
        parts.extend(_format_group("⏸ 未启用", disabled))
        while parts and parts[-1] == "":
            parts.pop()

        parts.extend(
            [
                "",
                "────────",
                "/plugin help <名>     查看帮助与指令",
                "/plugin on|off <名>   启用 / 禁用",
                "/plugin restart <名>  重启",
                "/plugin update <名>   更新",
            ]
        )

        event.set_result(
            MessageEventResult().message("\n".join(parts)).use_t2i(False),
        )

    async def plugin_off(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """禁用插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法禁用插件。"))
            return
        if not plugin_name:
            event.set_result(
                MessageEventResult().message(
                    "使用方法：/plugin off <插件名> 禁用插件。"
                ),
            )
            return
        if self.context.get_registered_star(plugin_name) is None:
            event.set_result(
                MessageEventResult().message(
                    self._plugin_not_found_message(plugin_name)
                ),
            )
            return
        try:
            await self.context._star_manager.turn_off_plugin(plugin_name)  # type: ignore
            event.set_result(
                MessageEventResult().message(f"插件 {plugin_name} 已禁用。")
            )
        except Exception as e:
            logger.error(f"禁用插件失败: {e}")
            event.set_result(MessageEventResult().message(f"禁用插件失败: {e}"))

    async def plugin_on(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """启用插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法启用插件。"))
            return
        if not plugin_name:
            event.set_result(
                MessageEventResult().message(
                    "使用方法：/plugin on <插件名> 启用插件。"
                ),
            )
            return
        if self.context.get_registered_star(plugin_name) is None:
            event.set_result(
                MessageEventResult().message(
                    self._plugin_not_found_message(plugin_name)
                ),
            )
            return
        try:
            await self.context._star_manager.turn_on_plugin(plugin_name)  # type: ignore
            event.set_result(
                MessageEventResult().message(f"插件 {plugin_name} 已启用。")
            )
        except Exception as e:
            logger.error(f"启用插件失败: {e}")
            event.set_result(MessageEventResult().message(f"启用插件失败: {e}"))

    async def plugin_get(self, event: AstrMessageEvent, plugin_repo: str = "") -> None:
        """安装插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法安装插件。"))
            return
        if not plugin_repo:
            event.set_result(
                MessageEventResult().message(
                    "使用方法：/plugin get <插件仓库地址> 安装插件。"
                ),
            )
            return
        logger.info(f"准备从 {plugin_repo} 安装插件。")
        if self.context._star_manager:
            star_mgr: PluginManager = self.context._star_manager
            try:
                proxy = self._github_proxy(action="指令安装插件", target=plugin_repo)
                await star_mgr.install_plugin(plugin_repo, proxy=proxy)  # type: ignore
                event.set_result(MessageEventResult().message("安装插件成功。"))
            except Exception as e:
                logger.error(f"安装插件失败: {e}")
                event.set_result(MessageEventResult().message(f"安装插件失败: {e}"))
                return

    async def plugin_restart(
        self, event: AstrMessageEvent, plugin_name: str = ""
    ) -> None:
        """重启插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法重启插件。"))
            return
        if not plugin_name:
            event.set_result(
                MessageEventResult().message(
                    "使用方法：/plugin restart <插件名> 重启插件。"
                ),
            )
            return
        plugin = self.context.get_registered_star(plugin_name)
        if plugin is None:
            event.set_result(
                MessageEventResult().message(
                    self._plugin_not_found_message(plugin_name)
                ),
            )
            return
        logger.info(f"准备重启插件 {plugin_name}。")
        try:
            success, error_message = await self.context._star_manager.reload(  # type: ignore
                plugin_name
            )
        except Exception as e:
            logger.error(f"重启插件失败: {e}")
            event.set_result(MessageEventResult().message(f"重启插件失败: {e}"))
            return
        if success:
            event.set_result(
                MessageEventResult().message(f"插件 {plugin_name} 已重启。")
            )
        else:
            event.set_result(
                MessageEventResult().message(
                    f"重启插件 {plugin_name} 失败: {error_message}"
                )
            )

    async def plugin_update(
        self, event: AstrMessageEvent, plugin_name: str = ""
    ) -> None:
        """更新插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法更新插件。"))
            return
        if not plugin_name:
            event.set_result(
                MessageEventResult().message(
                    "使用方法：/plugin update <插件名> 更新插件。"
                ),
            )
            return
        plugin = self.context.get_registered_star(plugin_name)
        if plugin is None:
            event.set_result(
                MessageEventResult().message(
                    self._plugin_not_found_message(plugin_name)
                ),
            )
            return
        logger.info(f"准备更新插件 {plugin_name}。")
        try:
            await event.send(
                MessageEventResult().message(f"正在更新「{plugin_name}」插件…")
            )
            proxy = self._github_proxy(action="指令更新插件", target=plugin_name)
            await self.context._star_manager.update_plugin(plugin_name, proxy=proxy)  # type: ignore
            event.set_result(
                MessageEventResult().message(f"插件 {plugin_name} 更新成功。")
            )
        except Exception as e:
            logger.error(f"更新插件失败: {e}")
            event.set_result(MessageEventResult().message(f"更新插件失败: {e}"))

    def _plugin_dir(self, plugin) -> str | None:
        """解析插件安装目录（绝对路径，跟随软链）。"""
        root = (getattr(plugin, "root_dir_name", None) or "").strip()
        if not root and plugin.module_path:
            parts = plugin.module_path.split(".")
            for idx, part in enumerate(parts):
                if part in ("builtin_stars", "plugins") and idx + 1 < len(parts):
                    root = parts[idx + 1]
                    break
        if not root:
            return None
        try:
            star_mgr = self.context._star_manager  # type: ignore
            base = (
                star_mgr.reserved_plugin_path
                if getattr(plugin, "reserved", False)
                else star_mgr.plugin_store_path
            )
            path = os.path.realpath(os.path.join(base, root))
        except Exception:
            return None
        return path if os.path.isdir(path) else None

    @staticmethod
    def _read_plugin_metadata(plugin_dir: str) -> dict:
        """读取插件目录下的 metadata 文件（metadata.yaml / metadata.yml）。"""
        for fname in PLUGIN_METADATA_FILENAMES:
            meta_path = os.path.join(plugin_dir, fname)
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}
        return {}

    @staticmethod
    def _metadata_mtime(plugin_dir: str) -> str | None:
        """metadata 文件的最后修改时间（以 metadata.yaml 的修改时间为准）。"""
        for fname in PLUGIN_METADATA_FILENAMES:
            meta_path = os.path.join(plugin_dir, fname)
            if os.path.isfile(meta_path):
                try:
                    t = datetime.fromtimestamp(os.path.getmtime(meta_path))
                except OSError:
                    return None
                return f"{t.year}-{t.month}-{t.day} {t.hour:02d}:{t.minute:02d}"
        return None

    @staticmethod
    def _plugin_files_line(plugin_dir: str) -> str | None:
        """插件目录关键文件状态。"""
        parts = []
        if os.path.isfile(os.path.join(plugin_dir, "main.py")):
            parts.append("main.py ✓")
        if os.path.isfile(os.path.join(plugin_dir, "_conf_schema.json")):
            parts.append("配置面板 ✓")
        if os.path.isfile(os.path.join(plugin_dir, "README.md")):
            parts.append("README ✓")
        if os.path.isfile(os.path.join(plugin_dir, "CHANGELOG.md")):
            parts.append("CHANGELOG ✓")
        return " | ".join(parts) if parts else None

    async def plugin_help(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """获取插件帮助"""
        if not plugin_name:
            event.set_result(
                MessageEventResult().message(
                    "使用方法：/plugin help <插件名> 查看插件信息。"
                ),
            )
            return
        plugin = self.context.get_registered_star(plugin_name)
        if plugin is None:
            event.set_result(
                MessageEventResult().message(
                    self._plugin_not_found_message(plugin_name)
                ),
            )
            return

        plugin_dir = self._plugin_dir(plugin)
        meta = self._read_plugin_metadata(plugin_dir) if plugin_dir else {}

        name = (plugin.name or "").strip()
        display = (getattr(plugin, "display_name", None) or "").strip()
        version = (plugin.version or "").strip()
        author = (plugin.author or "").strip()
        desc = (plugin.desc or plugin.short_desc or "").strip()
        repo = (plugin.repo or "").strip()

        title = display or name or (plugin_name or "").strip() or "插件"
        lines: list[str] = [f"✦ 插件详情: {title} ✦"]
        if name:
            lines.append(f"插件名: {name}")
        lines.append(f"显示名: {display or name}")
        if version:
            lines.append(f"版本: {version}")
        if author:
            lines.append(f"作者: {author}")
        if desc:
            lines.append(f"描述: {desc}")
        if repo:
            lines.append(f"仓库: {repo}")

        help_text = meta.get("help")
        if isinstance(help_text, str) and help_text.strip():
            lines.append(f"帮助: {help_text.strip()}")
        deps = meta.get("dependencies")
        if isinstance(deps, list) and deps:
            lines.append(f"依赖: {', '.join(str(d) for d in deps)}")
        if getattr(plugin, "support_platforms", None):
            lines.append(f"平台: {', '.join(plugin.support_platforms)}")

        if plugin_dir:
            files_line = self._plugin_files_line(plugin_dir)
            if files_line:
                lines.append(f"文件: {files_line}")
            lines.append(f"安装目录: {plugin_dir}")
        mtime = self._metadata_mtime(plugin_dir) if plugin_dir else None
        if mtime:
            lines.append(f"最后更新: {mtime}")

        command_handlers = []
        command_names = []
        for handler in star_handlers_registry:
            assert isinstance(handler, StarHandlerMetadata)
            if handler.handler_module_path != plugin.module_path:
                continue
            for filter_ in handler.event_filters:
                if isinstance(filter_, CommandFilter):
                    command_handlers.append(handler)
                    command_names.append(filter_.command_name)
                    break
                if isinstance(filter_, CommandGroupFilter):
                    command_handlers.append(handler)
                    command_names.append(filter_.group_name)

        if command_handlers:
            lines.append("")
            lines.append("🔧 指令列表：")
            for i in range(len(command_handlers)):
                cmd = (command_names[i] or "").strip()
                if cmd and not cmd.startswith("/"):
                    cmd = f"/{cmd}"
                line = cmd or "/"
                if command_handlers[i].desc:
                    line += f": {command_handlers[i].desc}"
                lines.append(line)
            lines.append("")
            lines.append("提示：指令的触发需要添加唤醒前缀，默认为 /。")

        event.set_result(
            MessageEventResult().message("\n".join(lines)).use_t2i(False),
        )
