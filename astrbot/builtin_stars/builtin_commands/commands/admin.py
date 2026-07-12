from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult


class AdminCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def op(self, event: AstrMessageEvent, admin_id: str = "") -> None:
        """授权管理员。op <admin_id>"""
        if not admin_id:
            event.set_result(
                MessageEventResult().message(
                    "使用方法: /op <id> 授权管理员；/deop <id> 取消管理员。可通过 /sid 获取 ID。",
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
                    "使用方法: /deop <id> 取消管理员。可通过 /sid 获取 ID。",
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
                    "使用方法: /wl <id> 添加白名单；/dwl <id> 删除白名单。可通过 /sid 获取 ID。",
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
                    "使用方法: /dwl <id> 删除白名单。可通过 /sid 获取 ID。",
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

    async def update_dashboard(self, event: AstrMessageEvent) -> None:
        """从 landamao/ldm_AstrBot 同步管理面板到 data/dist。"""
        from astrbot.core.updator import AstrBotUpdator

        await event.send(
            MessageChain().message(
                "正在从 landamao/ldm_AstrBot 同步 WebUI，请稍候..."
            )
        )
        try:
            applied = await AstrBotUpdator().apply_webui_only_from_package(
                latest=True,
                version=None,
                proxy="",
            )
            if applied:
                await event.send(
                    MessageChain().message(
                        "WebUI 已从 landamao/ldm_AstrBot 同步完成。刷新面板即可。"
                    )
                )
            else:
                await event.send(
                    MessageChain().message(
                        "同步失败：更新包中未找到 dashboard/dist 或 data/dist。"
                    )
                )
        except Exception as exc:
            await event.send(
                MessageChain().message(f"同步 WebUI 失败: {exc}")
            )
