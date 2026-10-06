from astrbot.api import sp, star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词


class SetUnsetCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def set_variable(self, event: AstrMessageEvent, key: str, value: str) -> None:
        """设置会话变量"""
        key = str(key or "").strip()
        value = str(value or "").strip()
        if not key:
            event.set_result(
                MessageEventResult().message(
                    f"请输入变量名。使用方法：{获取第一个唤醒词()}set <变量名> <值>"
                ),
            )
            return
        if not value:
            event.set_result(
                MessageEventResult().message(
                    f"请输入变量值。使用方法：{获取第一个唤醒词()}set {key} <值>"
                ),
            )
            return
        uid = event.unified_msg_origin
        session_var = await sp.session_get(uid, "session_variables", {})
        session_var[key] = value
        await sp.session_put(uid, "session_variables", session_var)

        event.set_result(
            MessageEventResult().message(
                f"会话 {uid} 变量 {key} 存储成功。使用 {获取第一个唤醒词()}unset 移除。",
            ),
        )

    async def unset_variable(self, event: AstrMessageEvent, key: str) -> None:
        """移除会话变量"""
        key = str(key or "").strip()
        if not key:
            event.set_result(
                MessageEventResult().message(
                    f"请输入变量名。使用方法：{获取第一个唤醒词()}unset <变量名>"
                ),
            )
            return
        uid = event.unified_msg_origin
        session_var = await sp.session_get(uid, "session_variables", {})

        if key not in session_var:
            event.set_result(
                MessageEventResult().message(f"没有那个变量名。格式 {获取第一个唤醒词()}unset 变量名。"),
            )
        else:
            del session_var[key]
            await sp.session_put(uid, "session_variables", session_var)
            event.set_result(
                MessageEventResult().message(f"会话 {uid} 变量 {key} 移除成功。"),
            )
