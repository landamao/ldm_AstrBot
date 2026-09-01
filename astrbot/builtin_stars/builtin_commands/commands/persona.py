import builtins
from typing import TYPE_CHECKING

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core import sp
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词

if TYPE_CHECKING:
    from astrbot.core.db.po import Persona


class PersonaCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    def _build_tree_output(
        self,
        folder_tree: list[dict],
        all_personas: list["Persona"],
        depth: int = 0,
    ) -> list[str]:
        """递归构建树状输出，使用短线条表示层级"""
        lines: list[str] = []
        # 使用短线条作为缩进前缀，每层只用 "│" 加一个空格
        prefix = "│ " * depth

        for folder in folder_tree:
            # 输出文件夹
            lines.append(f"{prefix}├ 📁 {folder['name']}/")

            # 获取该文件夹下的人格
            folder_personas = [
                p for p in all_personas if p.folder_id == folder["folder_id"]
            ]
            child_prefix = "│ " * (depth + 1)

            # 输出该文件夹下的人格
            for persona in folder_personas:
                lines.append(f"{child_prefix}├ 👤 {persona.persona_id}")

            # 递归处理子文件夹
            children = folder.get("children", [])
            if children:
                lines.extend(
                    self._build_tree_output(
                        children,
                        all_personas,
                        depth + 1,
                    )
                )

        return lines

    async def persona(self, message: AstrMessageEvent) -> None:
        l = message.message_str.split(" ")  # noqa: E741
        umo = message.unified_msg_origin

        curr_persona_name = "无"
        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        default_persona = await self.context.persona_manager.get_default_persona_v3(
            umo=umo,
        )
        force_applied_persona_id = None

        # 会话服务配置（自定义规则），set/unset 时若有规则人格则直接改规则
        session_service_config = (
            await sp.get_async(
                scope="umo",
                scope_id=umo,
                key="session_service_config",
                default={},
            )
            or {}
        )
        force_applied_persona_id = session_service_config.get("persona_id") or None

        curr_cid_title = "无"
        if cid:
            conv = await self.context.conversation_manager.get_conversation(
                unified_msg_origin=umo,
                conversation_id=cid,
                create_if_not_exists=True,
            )
            if conv is None:
                message.set_result(
                    MessageEventResult().message(
                        f"当前对话不存在，请先使用 {获取第一个唤醒词()}new 新建一个对话。",
                    ),
                )
                return

            provider_settings = self.context.get_config(umo=umo).get(
                "provider_settings",
                {},
            )
            # 规则里的 force_applied_persona_id 已在方法开头读取，无需覆盖
            (
                persona_id,
                _,
                _,
                _,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conv.persona_id,
                platform_name=message.get_platform_name(),
                provider_settings=provider_settings,
            )

            if persona_id == "[%None]":
                curr_persona_name = "无"
            elif persona_id:
                curr_persona_name = persona_id

            if force_applied_persona_id:
                curr_persona_name = f"{curr_persona_name} (自定义规则)"

            curr_cid_title = conv.title if conv.title else "新对话"
            curr_cid_title += f"({cid[:4]})"

        if len(l) == 1:
            前缀 = 获取第一个唤醒词()
            message.set_result(
                MessageEventResult()
                .message(
                    f"""[Persona]

- 人格情景列表: `{前缀}persona list`
- 设置人格情景: `{前缀}persona 人格`
- 人格情景详细信息: `{前缀}persona view 人格`
- 取消人格: `{前缀}persona unset`

默认人格情景: {default_persona["name"]}
当前对话 {curr_cid_title} 的人格情景: {curr_persona_name}

配置人格情景请前往管理面板-配置页
""",
                )
                .use_t2i(False),
            )
        elif l[1] == "list":
            # 获取文件夹树和所有人格
            folder_tree = await self.context.persona_manager.get_folder_tree()
            all_personas = self.context.persona_manager.personas

            lines = ["📂 人格列表：\n"]

            # 构建树状输出
            tree_lines = self._build_tree_output(folder_tree, all_personas)
            lines.extend(tree_lines)

            # 输出根目录下的人格（没有文件夹的）
            root_personas = [p for p in all_personas if p.folder_id is None]
            if root_personas:
                if tree_lines:  # 如果有文件夹内容，加个空行
                    lines.append("")
                for persona in root_personas:
                    lines.append(f"👤 {persona.persona_id}")

            # 统计信息
            total_count = len(all_personas)
            lines.append(f"\n共 {total_count} 个人格")
            lines.append(f"\n*使用 `{获取第一个唤醒词()}persona <人格名>` 设置人格")
            lines.append(f"*使用 `{获取第一个唤醒词()}persona view <人格名>` 查看详细信息")

            msg = "\n".join(lines)
            message.set_result(MessageEventResult().message(msg).use_t2i(False))
        elif l[1] == "view":
            if len(l) == 2:
                message.set_result(MessageEventResult().message("请输入人格情景名"))
                return
            ps = l[2].strip()
            if persona := next(
                builtins.filter(
                    lambda persona: persona["name"] == ps,
                    self.context.provider_manager.personas,
                ),
                None,
            ):
                msg = f"人格{ps}的详细信息：\n"
                msg += f"{persona['prompt']}\n"
            else:
                msg = f"人格{ps}不存在"
            message.set_result(MessageEventResult().message(msg))
        elif l[1] == "unset":
            if force_applied_persona_id:
                # 自定义规则强制了人格 → 从规则里移除（其余字段保留）
                session_service_config.pop("persona_id", None)
                await sp.put_async(
                    "umo",
                    umo,
                    "session_service_config",
                    session_service_config,
                )
                message.set_result(
                    MessageEventResult()
                    .message("已从自定义规则移除人格强制，恢复按对话/默认人格生效。")
                    .use_t2i(False),
                )
                return
            if not cid:
                message.set_result(
                    MessageEventResult().message("当前没有对话，无法取消人格。"),
                )
                return
            await self.context.conversation_manager.update_conversation_persona_id(
                message.unified_msg_origin,
                "[%None]",
            )
            message.set_result(MessageEventResult().message("取消人格成功。"))
        else:
            ps = "".join(l[1:]).strip()
            if persona := next(
                builtins.filter(
                    lambda persona: persona["name"] == ps,
                    self.context.provider_manager.personas,
                ),
                None,
            ):
                if force_applied_persona_id:
                    # 自定义规则强制了人格 → 直接改规则值，实时生效
                    session_service_config["persona_id"] = ps
                    await sp.put_async(
                        "umo",
                        umo,
                        "session_service_config",
                        session_service_config,
                    )
                    message.set_result(
                        MessageEventResult()
                        .message(
                            f"已将自定义规则的人格设为「{ps}」，立即生效。如需清空上下文请注意使用 {获取第一个唤醒词()}reset。",
                        )
                        .use_t2i(False),
                    )
                    return
                if not cid:
                    message.set_result(
                        MessageEventResult().message(
                            f"当前没有对话，请先开始对话或使用 {获取第一个唤醒词()}new 创建一个对话。",
                        ),
                    )
                    return
                await self.context.conversation_manager.update_conversation_persona_id(
                    message.unified_msg_origin,
                    ps,
                )
                message.set_result(
                    MessageEventResult().message(
                        f"设置成功。如果您正在切换到不同的人格，请注意使用 {获取第一个唤醒词()}reset 来清空上下文，防止原人格对话影响现人格。",
                    ),
                )
            else:
                message.set_result(
                    MessageEventResult().message(
                        f"不存在该人格情景。使用 {获取第一个唤醒词()}persona list 查看所有。",
                    ),
                )
