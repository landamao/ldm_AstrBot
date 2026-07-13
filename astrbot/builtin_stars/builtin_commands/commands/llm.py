from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core import logger


class LLMCommands:
    """会话级 LLM 开关管理。

    行为对齐「关闭llm」插件：按群/私聊/全局维度开关 LLM，
    并通过 on_llm_request 钩子实际拦截被关闭会话的 LLM 请求。
    关闭列表与全局开关持久化在插件配置中。
    """

    def __init__(self, context: star.Context, config=None) -> None:
        self.context = context
        self.config = config
        self.关闭的私聊: set[str] = set()
        self.关闭的群组: set[str] = set()
        self.全局关闭 = False
        self._init_config()

    def _init_config(self) -> None:
        """清理并同步配置到内存状态。"""
        if self.config is None:
            return
        # 清理空白项并去重后回写
        self.config["关闭的群组"] = list(
            dict.fromkeys(
                [i for i in self.config.get("关闭的群组", []) if i.strip()]
            )
        )
        self.config["关闭的私聊"] = list(
            dict.fromkeys(
                [i for i in self.config.get("关闭的私聊", []) if i.strip()]
            )
        )
        if "全局关闭" not in self.config:
            self.config["全局关闭"] = False
        self.config.save_config()

        self.关闭的群组 = set(self.config["关闭的群组"])
        self.关闭的私聊 = set(self.config["关闭的私聊"])
        self.全局关闭 = self.config["全局关闭"]

    async def on_llm_request(self, event: AstrMessageEvent, _) -> None:
        """LLM 请求前拦截：全局关闭 > 群组 > 私聊。"""
        if self.config is None:
            return
        if self.全局关闭:
            logger.info("全局 LLM 已关闭，拦截请求")
            event.stop_event()
            return

        群号 = event.get_group_id()
        if 群号:
            if 群号 in self.关闭的群组:
                logger.info(f"群 {群号} 的 LLM 功能已关闭")
                event.stop_event()
                return
        else:
            if event.get_sender_id() in self.关闭的私聊:
                logger.info(f"私聊 {event.get_sender_id()} 的 LLM 功能已关闭")
                event.stop_event()
                return

    async def llm(
        self, event: AstrMessageEvent, sid: str = "", 操作: str = ""
    ) -> None:
        """代替框架 llm 指令，单独为某会话开关 LLM 功能。

        /llm           -> 开关当前会话
        /llm 123 -q    -> 开关群 123
        /llm 123 -s    -> 开关私聊 123
        /llm list [-q|-s|-a] -> 查看已关闭的会话
        /llm all [on|off]    -> 全局禁用/启用 LLM（不指定则切换）
        /llm help            -> 显示帮助
        """
        # 未注入配置时的兜底（理论上 reserved 插件会注入）
        if self.config is None:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
            enable = cfg["provider_settings"].get("enable", True)
            cfg["provider_settings"]["enable"] = not enable
            status = "关闭" if enable else "开启"
            cfg.save_config()
            await event.send(MessageChain().message(f"{status} LLM 聊天功能。"))
            return

        # -------- help ----------
        if sid and sid.lower() == "help":
            帮助 = (
                "LLM 管理命令：\n"
                "/llm              → 切换当前会话的 LLM 开关（群聊/私聊）\n"
                "/llm <id> -q      → 切换指定群组的 LLM 开关\n"
                "/llm <id> -s      → 切换指定私聊用户的 LLM 开关\n"
                "/llm list         → 查看已关闭 LLM 的群组\n"
                "/llm list -s      → 查看已关闭 LLM 的私聊用户\n"
                "/llm list -a      → 查看所有已关闭的会话\n"
                "/llm all [on|off] → 全局禁用/启用 LLM（不指定则切换状态）\n"
                "/llm help         → 显示本帮助"
            )
            await event.send(MessageChain().message(帮助))
            return

        # -------- 全局开关 ----------
        if sid and sid.lower() == "all":
            op = 操作.strip().lower()
            if op in ("on", "开", "启用"):
                self.全局关闭 = True
            elif op in ("off", "关", "停用"):
                self.全局关闭 = False
            else:
                self.全局关闭 = not self.全局关闭

            self.config["全局关闭"] = self.全局关闭
            self.config.save_config()
            self._init_config()

            state = "已关闭" if self.全局关闭 else "已开启"
            await event.send(MessageChain().message(f"全局 LLM 功能{state}"))
            return

        # -------- list 查看 ----------
        if sid.lower() == "list":
            if 操作 == "-s":
                私聊列表 = "\n".join(self.config["关闭的私聊"]) or "（无）"
                文本 = f"已关闭 LLM 的私聊用户：\n{私聊列表}"
            elif 操作 == "-a":
                群组列表 = "\n".join(self.config["关闭的群组"]) or "（无）"
                私聊列表 = "\n".join(self.config["关闭的私聊"]) or "（无）"
                文本 = (
                    f"已关闭 LLM 的群组：\n{群组列表}\n\n"
                    f"已关闭 LLM 的私聊用户：\n{私聊列表}"
                )
            else:  # 默认 / -q 视为查看群组
                群组列表 = "\n".join(self.config["关闭的群组"]) or "（无）"
                文本 = f"已关闭 LLM 的群组：\n{群组列表}"
            await event.send(MessageChain().message(文本))
            return

        # -------- 开关逻辑 ----------
        if not sid:
            群号 = event.get_group_id()
            if 群号:
                sid = 群号
                操作 = "-q"
            else:
                sid = event.get_sender_id()
                操作 = "-s"

        if 操作 == "-s":
            操作的列表: list = self.config["关闭的私聊"]
            类型 = "私聊"
        else:  # 默认 -q
            操作的列表 = self.config["关闭的群组"]
            类型 = "群"

        if sid in 操作的列表:
            操作的列表.remove(sid)
            状态 = "已开启"
        else:
            操作的列表.append(sid)
            状态 = "已关闭"

        self.config.save_config()
        self._init_config()

        await event.send(MessageChain().message(f"{状态} {类型} {sid} 的 LLM 功能"))
