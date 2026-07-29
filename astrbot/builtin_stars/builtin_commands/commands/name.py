from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core.umo_alias import get_event_auto_name, normalize_umo_name


class NameCommand:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def name(self, event: AstrMessageEvent, alias: str) -> None:
        umo = event.unified_msg_origin
        auto_name = get_event_auto_name(event)
        alias = normalize_umo_name(alias)

        # 无参数：查询当前设置
        if not alias:
            saved_alias = await self.context.get_db().get_umo_alias(umo)
            user_alias = normalize_umo_name(
                saved_alias.user_alias if saved_alias else ""
            )
            event.set_result(
                MessageEventResult()
                .message(
                    "\n".join(
                        [
                            "使用方法：/name <名称>",
                            "传入 unset 清除自定义名称",
                            f"会话 ID: {umo}",
                            f"自动名称: {auto_name or '（空）'}",
                            f"自定义名称: {user_alias or '（空）'}",
                        ]
                    )
                )
                .use_t2i(False)
            )
            return

        sender_id = str(event.get_sender_id() or "")

        # unset：清除自定义名称
        if alias.lower() == "unset":
            saved_alias = await self.context.get_db().get_umo_alias(umo)
            if not saved_alias:
                event.set_result(
                    MessageEventResult()
                    .message(f"该会话未设置自定义名称。\n会话 ID: {umo}")
                    .use_t2i(False)
                )
                return
            await self.context.get_db().upsert_umo_alias(
                umo=umo,
                creator_sender_id=sender_id,
                auto_name=auto_name,
                user_alias=None,
            )
            event.set_result(
                MessageEventResult()
                .message(f"已清除会话自定义名称。\n会话 ID: {umo}")
                .use_t2i(False)
            )
            return

        # 正常设置
        await self.context.get_db().upsert_umo_alias(
            umo=umo,
            creator_sender_id=sender_id,
            auto_name=auto_name,
            user_alias=alias,
        )

        event.set_result(
            MessageEventResult()
            .message(f"会话名称已设为：{alias}\n会话 ID: {umo}")
            .use_t2i(False)
        )
