from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core import DEMO_MODE, logger
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import StarHandlerMetadata, star_handlers_registry
from astrbot.core.star.star_manager import PluginManager


class PluginCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

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
                "/plugin help 指令拦截",
                "/plugin on astrbot_plugin_stealer",
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
            return (0 if p.activated else 1, (p.name or "").casefold())

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
                MessageEventResult().message("/plugin off <插件名> 禁用插件。"),
            )
            return
        await self.context._star_manager.turn_off_plugin(plugin_name)  # type: ignore
        event.set_result(MessageEventResult().message(f"插件 {plugin_name} 已禁用。"))

    async def plugin_on(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """启用插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法启用插件。"))
            return
        if not plugin_name:
            event.set_result(
                MessageEventResult().message("/plugin on <插件名> 启用插件。"),
            )
            return
        await self.context._star_manager.turn_on_plugin(plugin_name)  # type: ignore
        event.set_result(MessageEventResult().message(f"插件 {plugin_name} 已启用。"))

    async def plugin_get(self, event: AstrMessageEvent, plugin_repo: str = "") -> None:
        """安装插件"""
        if DEMO_MODE:
            event.set_result(MessageEventResult().message("演示模式下无法安装插件。"))
            return
        if not plugin_repo:
            event.set_result(
                MessageEventResult().message("/plugin get <插件仓库地址> 安装插件"),
            )
            return
        logger.info(f"准备从 {plugin_repo} 安装插件。")
        if self.context._star_manager:
            star_mgr: PluginManager = self.context._star_manager
            try:
                await star_mgr.install_plugin(plugin_repo)  # type: ignore
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
                MessageEventResult().message("/plugin restart <插件名> 重启插件。"),
            )
            return
        plugin = self.context.get_registered_star(plugin_name)
        if plugin is None:
            event.set_result(MessageEventResult().message("未找到此插件。"))
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
                MessageEventResult().message("/plugin update <插件名> 更新插件。"),
            )
            return
        plugin = self.context.get_registered_star(plugin_name)
        if plugin is None:
            event.set_result(MessageEventResult().message("未找到此插件。"))
            return
        logger.info(f"准备更新插件 {plugin_name}。")
        try:
            await self.context._star_manager.update_plugin(plugin_name)  # type: ignore
            event.set_result(
                MessageEventResult().message(f"插件 {plugin_name} 更新成功。")
            )
        except Exception as e:
            logger.error(f"更新插件失败: {e}")
            event.set_result(MessageEventResult().message(f"更新插件失败: {e}"))

    async def plugin_help(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """获取插件帮助"""
        if not plugin_name:
            event.set_result(
                MessageEventResult().message("/plugin help <插件名> 查看插件信息。"),
            )
            return
        plugin = self.context.get_registered_star(plugin_name)
        if plugin is None:
            event.set_result(MessageEventResult().message("未找到此插件。"))
            return
        help_msg = ""
        help_msg += f"\n\n✨ 作者: {plugin.author}\n✨ 版本: {plugin.version}"
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

        if len(command_handlers) > 0:
            parts = ["\n\n🔧 指令列表：\n"]
            for i in range(len(command_handlers)):
                line = f"- {command_names[i]}"
                if command_handlers[i].desc:
                    line += f": {command_handlers[i].desc}"
                parts.append(line + "\n")
            parts.append("\nTip: 指令的触发需要添加唤醒前缀，默认为 /。")
            help_msg += "".join(parts)

        ret = f"🧩 插件 {plugin_name} 帮助信息：\n" + help_msg
        ret += "更多帮助信息请查看插件仓库 README。"
        event.set_result(MessageEventResult().message(ret).use_t2i(False))
