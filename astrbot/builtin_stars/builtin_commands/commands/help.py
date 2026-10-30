import aiohttp

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core.config.default import VERSION
from astrbot.core.notification import notification_service
from astrbot.core.star import command_management
from astrbot.core.utils.io import get_dashboard_version
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词


class HelpCommand:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def _query_astrbot_notice(self):
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(
                    "https://astrbot.app/notice.json",
                    timeout=2,
                ) as resp:
                    return (await resp.json())["notice"]
        except BaseException:
            return ""

    async def _build_reserved_command_lines(self) -> list[str]:
        """
        使用实时指令配置生成内置指令清单，确保重命名/禁用后与实际生效状态保持一致。
        """
        try:
            commands = await command_management.list_commands()
        except BaseException:
            return []

        lines: list[str] = []
        hidden_commands = {"websearch"}

        def walk(items: list[dict], indent: int = 0) -> None:
            for item in items:
                if not item.get("reserved") or not item.get("enabled"):
                    continue
                # 仅展示顶级指令或指令组
                if item.get("type") == "sub_command":
                    continue
                if item.get("parent_signature"):
                    continue

                effective = (
                    item.get("effective_command")
                    or item.get("original_command")
                    or item.get("handler_name")
                )
                if not effective:
                    continue
                if effective in hidden_commands:
                    continue

                description = item.get("description") or ""
                desc_text = f" - {description}" if description else ""
                indent_prefix = "  " * indent
                lines.append(f"{indent_prefix}{获取第一个唤醒词()}{effective}{desc_text}")

        walk(commands)
        return lines

    async def _build_important_notifications(self) -> list[str]:
        """管理员可见的未读重要通知列表（仅管理员展示）。"""
        try:
            items = await notification_service.list_notifications(
                include_read=False, limit=20
            )
        except Exception:
            logger.debug("读取重要通知失败", exc_info=True)
            return []
        if not items:
            return []

        lines = ["⚠️ 重要通知（请到 WebUI 通知中心查看详情并标记已读）："]
        for i, item in enumerate(items, 1):
            count_text = f"（已发生 {item.count} 次）" if item.count > 1 else ""
            detail = (item.detail or "").strip()
            line = f"{i}. {item.title}{count_text}"
            if detail:
                line += f"\n   {detail}"
            lines.append(line)
        return lines

    async def help(self, event: AstrMessageEvent) -> None:
        """查看帮助"""
        notice = ""
        try:
            notice = await self._query_astrbot_notice()
        except Exception:
            logger.debug("获取公告失败", exc_info=True)

        dashboard_version = await get_dashboard_version()
        command_lines = await self._build_reserved_command_lines()
        commands_section = (
            "\n".join(command_lines) if command_lines else "暂无启用的内置指令"
        )

        msg_parts: list[str] = []

        if event.is_admin():
            try:
                notification_lines = await self._build_important_notifications()
            except Exception:
                logger.debug("构建重要通知失败", exc_info=True)
                notification_lines = []
            msg_parts.extend(notification_lines)
            if notification_lines:
                msg_parts.append("")

        msg_parts.extend(
            [
                f"ldm v{VERSION}(WebUI: {dashboard_version})",
                "内置指令:",
                commands_section,
            ]
        )
        if notice:
            msg_parts.append(notice)
        msg = "\n".join(msg_parts)

        event.set_result(MessageEventResult().message(msg).use_t2i(False))
