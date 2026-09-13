"""流式输出命令"""

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词


class FlowCommand:
    """会话级流式输出开关管理。

    三态设置：True=强制流式，False=强制非流式，未设置=跟随全局配置。
    """

    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def flow(self, event: AstrMessageEvent, arg: str = "") -> None:
        """开关当前会话的流式输出。on/off/unset；不带参数切换"""
        umo = event.unified_msg_origin
        参数 = (arg or "").strip().lower()

        # WebChat 平台的流式输出由前端控制，不走会话级覆盖
        if event.get_platform_name() == "webchat":
            await event.send(
                MessageChain().message(
                    "WebChat 的流式输出开关可在当前页面的设置里实时切换，无需使用指令。"
                ),
            )
            return

        if 参数 == "help":
            前缀 = 获取第一个唤醒词()
            帮助 = (
                "流式输出管理命令：\n"
                f"{前缀}flow              → 切换当前会话的流式输出\n"
                f"{前缀}flow on           → 开启当前会话的流式输出\n"
                f"{前缀}flow off          → 关闭当前会话的流式输出\n"
                f"{前缀}flow unset        → 取消设置，恢复跟随全局配置\n"
                f"{前缀}flow help         → 显示本帮助"
            )
            await event.send(MessageChain().message(帮助))
            return

        # 全局配置（跟随配置时实际生效的流式状态，仅无参切换取反时参考，不回显）
        cfg = self.context.get_config(umo=umo)
        全局流式 = bool(cfg["provider_settings"].get("streaming_response", False))

        # 当前会话的覆盖值
        当前覆盖 = await SessionServiceManager.get_streaming_override_for_session(umo)

        if 参数 in ("on", "开", "启用"):
            await SessionServiceManager.set_streaming_status_for_session(umo, True)
            await event.send(
                MessageChain().message("已开启当前会话的流式输出。"),
            )
            return

        if 参数 in ("off", "关", "停用"):
            await SessionServiceManager.set_streaming_status_for_session(umo, False)
            await event.send(
                MessageChain().message("已关闭当前会话的流式输出。"),
            )
            return

        if 参数 in ("unset", "清除", "恢复"):
            原本已设置 = await SessionServiceManager.unset_streaming_status_for_session(
                umo,
            )
            全局状态文案 = "开启" if 全局流式 else "关闭"
            if not 原本已设置:
                await event.send(
                    MessageChain().message(
                        "当前会话未设置流式输出，本就跟随全局配置。\n"
                        f"全局配置：{全局状态文案}",
                    ),
                )
            else:
                await event.send(
                    MessageChain().message(
                        "已取消当前会话的流式输出设置，恢复跟随全局配置。\n"
                        f"全局配置：{全局状态文案}",
                    ),
                )
            return

        # 不带参数 → 切换
        if 当前覆盖 is not None:
            新状态 = not 当前覆盖
        else:
            # 未设置时，把「当前实际生效状态」取反作为显式覆盖
            新状态 = not 全局流式
        await SessionServiceManager.set_streaming_status_for_session(umo, 新状态)
        新状态文案 = "开启" if 新状态 else "关闭"
        await event.send(
            MessageChain().message(f"已{新状态文案}当前会话的流式输出。"),
        )
